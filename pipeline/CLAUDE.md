# pipeline/CLAUDE.md
Owner: P1. Imports core/ for the visit rule, masks and measurement. Design: docs/cloud-architecture.md,
after review (PR #11); dot columns and derived-table rules: contracts/dots.md.
Reads IMAGE, FLAGS, VARIANCE, ZODI, EPSF, WCS-WAVE (~56 MB per photo) from s3://nasa-irsa-spherex over HTTPS
(anonymous), once: dots, forced photometry at refcat positions and photo metadata come out of that one read.
Pilot (12 deg², 990 photos = 352 exposures × detectors) on skyshift-test; hunt zone (3,692 photos) on skyshift-worker after G2.

## Run
`uv run skyshift-pipeline <step> --zone pilot|hunt [--root gs://nasa-glazing-data | <dir>] [--workers N] [--limit N]`
Steps in order: photolist, refcat, dots, known, link, spectra, crossid, classify, photoz, colours, lightcurves, astrometry,
highpm, score, suspects; `all` runs them in order.
- crossid, colours, lightcurves, astrometry, highpm use only stored tables (members → dots, sources, refcat/<zone>.parquet
  or refcat/<zone>_forced.parquet, forced/), never photos; a missing refcat or forced/ gives a warning and empty matches.
- Every step skips what exists: the photo list, dots/<obs_id>D<det>.parquet, cache/<mpc|skybot>/<zone>/run_<obs_id>.json.
  link, spectra, classify, photoz, score and suspects are cheap and always recompute from what is there.
- classify and photoz read only sources.parquet (+ optional dot shape columns, crossids, refcat); never photos.
  photoz queries SDSS DR17 once per zone (cached in cache/sdss/<zone>.parquet); a network failure only skips the report.
- refcat: Gaia DR3 (G<20), CatWISE2020, 2MASS PSC over zone + 0.1° in 1° tiles (cache/refcat/<zone>/), writes
  refcat/<zone>.parquet and the forced list refcat/<zone>_forced.parquet (contracts/dots.md). Skips if both exist.
- `known` doesn't need `dots`: run it at the same time in a second process.
- `known` uses MPC asteroid + comet orbits propagated with kete from SPHEREx's own position (IMAGE header X_SC..,
  geocentric equatorial km, at MJD-AVG); cache/mpc/<zone>/ with exact per-photo positions, orbit snapshots
  cache/mpc/orbits/{mpcorb,cometels}_<date>.parquet. `--known-source skybot` (or SKYSHIFT_KNOWN_SOURCE=skybot) uses the
  SkyBoT service instead (cache/skybot/<zone>/). Comets have no V and no non-gravitational terms: C/2025 R2 was 4.5′ off SkyBoT a month after perihelion.
- Every threshold lives in src/skyshift_pipeline/config.py.

## Rules (summary)
- Session = run of the zone's exposure times with gaps < 10 min (core.sessions over all photos), one per photo; visit = core.visits.
- dots: core.detect thresh=2 (per pixel), keep snr >= 5; saturated dots re-centroided on the wings; flags = 3×3 OR,
  flags_peak = bits at the peak pixel; FINAST != 0 → empty dot file.
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
- Tests: `uv run pytest` (offline); `SKYSHIFT_NETWORK=1 uv run pytest` adds the live SkyBoT test and kete vs SkyBoT.

## Spectral science: what the outputs mean (for the web)
- `classes.parquet` → `class`: star, galaxy or unsure. SPHEREx's 6″ pixels and 7.5″ PSF leave most galaxies
  fainter than ~AB 17 looking point-like, so "unsure" is common and honest: it means "point-like, no Gaia
  star there" (compact galaxy, quasar, or a very red star such as a brown dwarf). A Gaia parallax makes it a star;
  a profile clearly broader than the stars' around it (size, curve of growth, PSF misfit) makes it a galaxy.
- Indices (`<name>_depth`/`_excess` and `<name>_sig`): how deep a known band is against the spectrum around it,
  and how sure (σ). Show a feature only when sig ≥ 5. Depth 0.2 = the band is 20% below the continuum.
  Ice (H2O 3.05, CO2 4.27, CO 4.67 µm): frozen molecules in a dark cloud in front of a background star.
  CH4 3.3 / 1.6 µm: methane, only in the coldest brown dwarfs (T and Y types; the "Planet X"-like cold bodies).
  H2O 1.4 / 1.9 µm: water vapour in M/L dwarfs (and pulsating giants such as R Draconis).
  CO 2.3 µm: the bandhead of cool giants. PAH 3.3 µm emission: star-forming galaxies (at z ≈ 0; photoz
  repeats it at the galaxy's redshift as `pah_33_sig_z`).
- `brown_dwarf_like`: methane or water seen, red (J − [4.6] AB > −0.5), and missing or very faint in Gaia.
  A lead for a human to check, not a discovery; `bd_evidence` says which bands fired.
- `mag_ab_*`: brightness from our own spectrum in J, H, K, 3.5 and 4.6 µm windows (AB).
- `photoz.parquet` → `z_best` with a 68% range `z_lo`–`z_hi`: a rough distance for galaxies from the shape of their
  102-channel spectrum (the 1.6 µm stellar bump, PAH and line features shifting red). Trust it when `good` is true.
  `photoz_report.json` says how close it came to SDSS spectroscopic redshifts in the zone (σ_NMAD of
  Δz/(1+z); 0.02 is good for photo-z, outliers are > 0.15). Expect useful values only for bright galaxies (AB ≲ 17).
