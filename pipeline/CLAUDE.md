# pipeline/CLAUDE.md
Owner: P1. Imports core/ for the visit rule, masks and measurement. Design: docs/cloud-architecture.md,
after review (PR #11); dot columns and derived-table rules: contracts/dots.md.
Reads IMAGE, FLAGS, VARIANCE, ZODI, EPSF, WCS-WAVE (~56 MB per photo) from s3://nasa-irsa-spherex over HTTPS
(anonymous), once: dots, forced photometry at refcat positions and photo metadata come out of that one read.
Pilot (12 deg², 990 photos = 352 exposures × detectors) on skyshift-test; hunt zone (3,692 photos) on skyshift-worker after G2.

## Run
`uv run skyshift-pipeline <step> --zone pilot|hunt [--root gs://nasa-glazing-data | <dir>] [--workers N] [--limit N]`
Steps in order: photolist, refcat, dots, known, link, spectra, crossid, calib, classify, photoz, colours, lightcurves, astrometry,
highpm, score, suspects; `all` runs them in order.
- Candidates for the site (contracts/api.md, candidates.json): `uv run skyshift-candidates [--zones hunt,pilot]
  [--cache <dir>]` reads results/ of every finished zone (largest first) and writes ../web/public/data/candidates.json;
  then `cd ../server && uv run python ../scripts/candidates/frames.py` adds static frames for the top ones.
  Reads only some columns (range reads), the dot files' `flags` and the movers' dots: ~0.4 GB from the bucket for the hunt.
- crossid, colours, lightcurves, astrometry, highpm use only stored tables (members → dots, sources, refcat/<zone>.parquet
  or refcat/<zone>_forced.parquet, forced/), never photos; a missing refcat or forced/ gives a warning and empty matches.
- Every step skips what exists: the photo list, dots/<obs_id>D<det>.parquet, cache/mpc/<zone>/run_<obs_id>.json.
  link, spectra, classify, photoz, score and suspects are cheap and always recompute from what is there.
- classify and photoz read only sources.parquet (+ optional dot shape columns, crossids, refcat); never photos.
  photoz queries SDSS DR17 once per zone (cached in cache/sdss/<zone>.parquet); a network failure only skips the report.
- refcat: Gaia DR3 (G<20), CatWISE2020, 2MASS PSC over zone + 0.1° in 1° tiles (cache/refcat/<zone>/), writes
  refcat/<zone>.parquet and the forced list refcat/<zone>_forced.parquet (contracts/dots.md). Skips if both exist.
- forced_sky (not a CLI step): `uv run python -m skyshift_pipeline.forced_sky --root gs://nasa-glazing-data --workers 16`
  builds the sky-wide forced list refcat/sky/forced_n32/p<pixel>.parquet (Gaia DR3 G<17.5, HEALPix nside 32 by source_id,
  ESA TAP sync, brightest 8,221 per partition) and _complete.json last; resumable. dots uses it for every photo's whole
  footprint once _complete.json exists, else refcat/<zone>_forced.parquet (SKYSHIFT_FORCED_SKY=off forces the zone list).
- `known` doesn't need `dots`: run it at the same time in a second process.
- calib (after crossid; `all` reruns spectra after it): from the raw member dots, per detector the pixel-phase centroid
  bias (Fourier in φ = x − round(x)), per photo a distortion polynomial vs Gaia (order by cross-validation), pos_err =
  √((a/snr)² + floor²), a flux pixel-phase term (ap and PSF) and a per-detector total-flux scale (2MASS/CatWISE anchors +
  detector-edge stitching) → results/<zone>/calib.json + calib_photos.parquet. motion_io.member_dots and spectra apply it
  when present (stats `calib_applied`); dot files stay raw; link stays raw (4″ FoF ≫ 0.6″ bias). Rerun after crossid.
  Dots v4: calib fits the ePSF positions' own distortion and pos_err (calib.json `psf`, schema 3, no phase term);
  the loaders use ra_psf/dec_psf where psf_pos_ok (column `pos_psf`, stats `pos_psf_dots`); with a calib.json only
  if it has `psf` for the detector and the ePSF's calibrated pos_err beats the centroid's at that S/N (ePSF floor
  0.12–0.15″ vs centroid 0.08–0.21″: bright dots keep the centroid). Schema-2 calib.json: all centroid. v3: unchanged.
  Also (14 Nov): a fixed Δy(y) per detector along the wavelength axis (`ymap`; D3 centroid ±0.05 px, D2 ±0.015 px in
  both position kinds); `floor_rel_arcsec` for motion fits (member_dots `pos_err_rel_arcsec`); Gaia GSPC z, y anchors
  for D1 (refcat ESA query joins gaiadr3.synthetic_photometry_gspc); `forced`: forced PSF fluxes' own level and phase
  share, applied in motion_io.load_forced (aperture fluxes take the dots' factors).
  refcat: Gaia tiles cached before the GSPC join (15 Nov) are refetched by the next full (not partial) refcat run, best
  effort (a failed refetch keeps the old tile; marker refcat/<zone>_catalogs.json `gspc`). Without GSPC, D1 keeps the stitch.
- `known` uses MPC asteroid + comet orbits propagated with kete from SPHEREx's own position (IMAGE header X_SC..,
  geocentric equatorial km, at MJD-AVG); cache/mpc/<zone>/ with exact per-photo positions, orbit snapshots
  cache/mpc/orbits/{mpcorb,cometels}_<date>.parquet. MPC is the only source of known objects (no flag, no env var).
  Comets have no V and no non-gravitational terms; C/2025 R2 still agreed with JPL Horizons to ~1″.
- minorplanetcenter.net times out from GCP machines (14 Nov). The newest snapshot in cache/mpc/orbits/ is always reused,
  so refresh it from a laptop: `uv run skyshift-pipeline mpc-orbits --root gs://nasa-glazing-data [--force]` (~1 min, gcloud
  login; not part of `all`, no --zone). It writes today's mpcorb_/cometels_<date>.parquet, which then win; old ones stay.
  Without --force a snapshot already made today is kept. Sessions already in cache/mpc/<zone>/ keep their old positions:
  delete those run_*.json files to recompute them with the new orbits.
- History: SkyBoT was removed after #64 (docs/research/2026-11-14-mpc-vs-skybot-score.md): on the pilot MPC gave
  G2 recall 92.2% with predictions 0.27″ from the matched movers vs SkyBoT's 1.43″.
- Every threshold lives in src/skyshift_pipeline/config.py.

## Tiles (tiles.py): large zones combined per sky tile
- `all` on a zone > TILE_AUTO_MIN_DEG2 (30 deg²; hunt yes, pilot no) runs link … suspects per tile in spawned processes
  (`--workers`), then merges into the same results/<zone>/ files. `--tiles off|N`, `--tile-deg D` override; `--tile-dir`
  is the local work dir (default the temp dir, removed after; `--keep-tiles` keeps it). Single steps stay untiled.
- Grid: nx × ny equal cores minimising rounds × per-tile cost (rounds = ceil(tiles / workers); cost ∝ (side + 2°)²: a
  tile reads every photo it overlaps), sides ≤ TILE_MAX_DEG (2.5°); hunt on 16 workers → 4 × 4.
- Tile = core box (cores partition the zone box) + TILE_MARGIN_ARCSEC border (2′: link's 4″ FoF chains reach 30–60″ at
  pilot depth). Ownership: fixed source by link's position, mover by its position, sourceless forced target by the
  position of its earliest forced row (with the sky-wide forced list a photo's forced file holds targets far outside
  the zone: `--zone-edge border` leaves those to the zone they're in; `clip` keeps them, as untiled). source_id is
  sky-wide (6″ cell), so no renumbering; the merge checks uniqueness. A fixed group near the core touching the border's
  outer edge → the tile is redone with a doubled border.
- Zone edges are tile edges (TILE_ZONE_EDGE_MARGIN): edge tiles load the border outside the zone box from the zone's
  photos, so stars on the zone edge aren't cut (untiled clips at the box). Photos touching only that 2′ strip are missing.
- Fitted once on the merged data and given to every tile: calib (calib.run, unchanged, fed the tiles' raw member dots of
  Gaia-matched sources + S/N ≥ 30 and the merged raw spectra), classify's stellar locus, the light-curve error model
  and bad photos, astrometry's per-photo frame offsets; photoz/lightcurves/astrometry reports on the merged tables;
  highpm, score and suspects run once on the merged tables (their 15′/2′ controls need no border).
- Dot files are mirrored once into the work dir without flux_ap_ujy/flux_ap_err_ujy (TILE_SLIM_DOTS; unread by the
  combining steps, 3/4 of a full read); each tile reads every photo it overlaps.
- Equality (tests/test_tiles.py; pilot photos with 0.3° tiles): tiled == untiled table by table and report by report.

## Rules (summary)
- Session = run of the zone's exposure times with gaps < 10 min (core.sessions over all photos), one per photo; visit = core.visits.
- dots: core.detect thresh=2 (per pixel), keep snr >= 5; saturated dots re-centroided on the wings; flags = 3×3 OR,
  flags_peak = bits at the peak pixel; FINAST != 0 → empty dot file. Dots v4 (14 Nov) add an ePSF position fit
  (x_psf, y_psf, ra_psf, dec_psf, pos_err_psf_arcsec, psf_pos_ok; no pixel-phase pull, ~2× smaller scatter vs Gaia):
  use them where psf_pos_ok, never phase-correct them (contracts/dots.md, Versions).
- link: fixed = FoF 4″ groups in >= 3 sessions; drop dots within 6″ of fixed; pair dots of one session
  (≤ 3″ + 1″/min × Δt, or a simultaneous D1+D4/D2+D5/D3+D6 pair ≤ 3″, elong ≤ 3); coverage ≥ 3 sessions from footprints.
- link also writes members.parquet (source_id, obs_id, detector, dot_row = row in the dot file as written; dot
  files are never rewritten). spectra: 102 channels (ours, 17 log-spaced per detector), MASK bits out, clipped
  weighted mean per channel, chi2_nu and last/first visit ratio for variability; vectorised, no loop per source.
- suspects: no known object within 30″ (same session), no ghost/crosstalk/halo/bloom bit, SOURCE not at every dot's peak,
  no bright fixed source within 30″, no Gaia DR3 within 6″, no CatWISE2020 within 6″ (from refcat/<zone>.parquet;
  the services only if that file is missing: 50-circle ESA batches took 30+ min for 30k movers).

## Facts
- S3 key: TAP spherex.obscore has no usable cloud_access; spherex.artifact.uri = 'ibe/data/spherex/' + S3 key.
- IRSA TAP doesn't accept OR'd CONTAINS; use a TAP_UPLOAD crossmatch (pyvo) for IRSA catalogues.
- macOS: uv may mark the editable .pth files hidden (Python then skips them); pytest adds ../core/src itself.
- ESA Gaia TAP: async jobs stalled for minutes (14 Nov), sync answers a 1° tile in ~2 s. IRSA: ~35 s (Gaia, 2MASS) to
  ~60 s (CatWISE, ~35k rows/deg²) per 1° tile, sync or async alike.
- Tests: `uv run pytest` (offline); `SKYSHIFT_NETWORK=1 uv run pytest` adds the live kete test (MPC orbits, rates
  vs positions an hour apart) and the SDSS query.

## Spectral science: what the outputs mean (for the web)
- `classes.parquet` → `class`: star, galaxy or unsure. SPHEREx's 6″ pixels and 7.5″ PSF leave most galaxies
  fainter than ~AB 17 looking point-like, so "unsure" is common and honest: it means "point-like, no Gaia
  star there" (compact galaxy, quasar, or a very red star such as a brown dwarf). A Gaia parallax makes it a star;
  a profile clearly broader than the stars' around it (size, curve of growth, PSF misfit) makes it a galaxy.
- Indices (`<name>_depth`/`_excess` and `<name>_sig`): how deep a known band is against the spectrum around it,
  and how sure (σ). Depth 0.2 = the band is 20% below the continuum. **Show the `label_*` booleans (or the
  `labels` list), not the raw sig**: sig alone fires on the wrong objects (pilot: "water ice" on 372 sources,
  mostly M dwarfs and galaxies; "methane" on 50, 15 of them Gaia stars). Each label needs sig ≥ 5, a minimum
  depth, no saturation, and physical guards (classify.band_labels, config LABEL_*):
  Ice (H2O 3.05, CO2 4.27, CO 4.67 µm, `label_h2o_ice`/`co2_ice`/`co_ice`): frozen molecules in a dark cloud in
  front of a background star, so: not a galaxy, point-like, reddened (J − [4.6] AB ≥ 0), not a cool Gaia star
  (BP − RP ≥ 2: M dwarfs' own water vapour absorbs at 2.7–3.2 µm) and no water vapour at 1.4/1.9 µm.
  CH4 3.3 / 1.6 µm (`label_ch4`): methane, only in the coldest brown dwarfs (T and Y types; the "Planet X"-like
  cold bodies), invisible to Gaia: no Gaia (or G > 19.5), red, not a galaxy, and a second band agreeing.
  H2O 1.4 / 1.9 µm (`label_h2o_vapour`): water *vapour* in M/L dwarfs and cool giants (R Draconis), not ice.
  CO 2.3 µm (`label_co_23`): the bandhead of cool giants. PAH 3.3 µm emission (`label_pah_33`, never on a star):
  star-forming galaxies (at z ≈ 0; photoz repeats it at the galaxy's redshift as `pah_33_sig_z`).
  Pilot before → after: ice 372 → 2, CO2 ice 3 → 0, CO ice 3 → 0, methane 52 → 0, water vapour 121 → 84,
  CO 2.3 µm 85 → 60, PAH 21 → 4.
- `brown_dwarf_like`: methane or water seen, red (J − [4.6] AB > −0.5), and missing or very faint in Gaia.
  A lead for a human to check, not a discovery; `bd_evidence` says which bands fired.
- `mag_ab_*`: brightness from our own spectrum in J, H, K, 3.5 and 4.6 µm windows (AB).
- `photoz.parquet` → `z_best` with a 68% range `z_lo`–`z_hi`: a rough distance for galaxies from the shape of their
  102-channel spectrum (the 1.6 µm stellar bump, PAH and line features shifting red). Trust it when `good` is true.
  `photoz_report.json` says how close it came to SDSS spectroscopic redshifts in the zone (σ_NMAD of
  Δz/(1+z); 0.02 is good for photo-z, outliers are > 0.15). Expect useful values only for bright galaxies (AB ≲ 17).
- `lightcurves_summary.parquet` → `variable`: chi2_nu (without the single worst point) > 5 and robust scatter > 5%,
  with errors from the zone's own error model (`flux_err_cal_ujy`: quoted error × k plus a fractional floor, per
  detector and origin, so the median Gaia star has chi2_nu 1; values in lightcurves_report.json). Pilot: quiet
  Gaia stars flagged 7.2% (dots) / 4.3% (forced) of light curves before, 0.9% / 1.5% after.
- `highpm_candidates.parquet` → show only `vetted` rows as leads; `reason` says why the others were cut
  (pilot: 241 candidates, 1 vetted, the Gaia star at 501 mas/yr; the 10 "new" were all artefacts).
