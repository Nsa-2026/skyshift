# pipeline/CLAUDE.md
Owner: P1. Imports core/ for the visit rule, masks and measurement. Design: docs/cloud-architecture.md,
after review (PR #11); dot columns and derived-table rules: contracts/dots.md.
Reads IMAGE, FLAGS, VARIANCE, ZODI, EPSF, WCS-WAVE (~56 MB per photo) from s3://nasa-irsa-spherex over HTTPS
(anonymous), once: dots, forced photometry at refcat positions and photo metadata come out of that one read.
Pilot (12 deg², 990 photos = 352 exposures × detectors) on skyshift-test; hunt zone (3,692 photos) on skyshift-worker after G2.

## Run
`uv run skyshift-pipeline <step> --zone pilot|hunt [--root gs://nasa-glazing-data | <dir>] [--workers N] [--limit N]`
Steps in order: photolist, refcat, dots, known, link, spectra, crossid, calib, qa, classify, photoz, colours, lightcurves,
fragments, astrometry, highpm, orphans, tracklets, score, completeness, suspects; `all` runs them in order.
- fragments (fragments.py, after crossid; tiled `all`: once on the merged tables after merge 2, before highpm, the dot
  files' flags from the slim mirror): from stored tables only (members → the dot files' `flags`/`flags_peak`, sources,
  fixed, crossids, refcat) tags each source, first that applies: `bright_fragment` (BLOOM on > 50% of its member dots,
  no Gaia/2MASS within 3″ of its spectrum or link position and not a Gaia star's kept source: a piece of a saturated
  star's ring), `duplicate` (a second source of one Gaia star, `duplicate_of` its kept source), `saturated_star` (BLOOM
  majority and a match or a star's kept source), `blend` (≥ 2 Gaia G < 19 within 8″), else "". Which source is which
  star: each source's home is its nearest Gaia star within 6″ (G < 11), 4″ (G < 18) or 3″; a star keeps crossid's match,
  else the most member dots (within 6″ / 3″), else the nearest (beyond 3″ at G ≥ 11 only with ≥ 10 member dots); its
  other home sources within 6″ / 3″ are duplicates unless the star moved > 1″ between them, they have their own
  catalogue entry, or another Gaia star there has no source. Fragments and duplicates carry their parent (a fragment's
  brightest Gaia G < 12 within 60″; a duplicate's star) and that star's own source; blends both Gaia ids, separation and
  ΔG → results/<zone>/fragments.parquet + fragments.json. Consumers, each unchanged without the file: highpm (fragment or
  duplicate → reason `fragment`, blend → status gaia), orphans (drops all three), shape (drops fragments and
  duplicates), candidates.py (fragments and duplicates never listed, blend → reason `blend`), server measured.py
  (duplicate drawn as its star's bright_fragment). Rerun after it: highpm, orphans, shape, candidates. Hunt (15 Nov,
  14 Nov tables): 3,537 bright_fragment / 223 duplicate / 363 saturated_star / 13,224 blend of 314,467 sources; Gaia
  stars with two kept sources within 3″ 200 → 7; ~50 s locally (the star matching adds ~5 s). Every distance from a
  source (catalogue, bright neighbour, blend count per Gaia star, a fragment's parent) takes the nearer of its spectrum
  and link positions (FRAGMENTS_LINK_MIN_OFFSET_ARCSEC). Hunt (15 Nov 00:01Z tables): parent_dist of 491 fragments
  nearer (median 25.0 → 21.8″), bright_* of 894 sources, 9 more blends, no other tag changes.
- stack (stack.py, 15 Nov; not in `all`; zones of config STACK_ZONES, the five featured targets): `uv run
  skyshift-pipeline stack --zone t_barnard --root gs://nasa-glazing-data --workers 8 --cache /tmp/stack-t_barnard`;
  all five on skyshift-test: `infra/worker/vm.sh run test 'infra/worker/stack_featured.sh'` (marker
  skyrun/final/stack_featured.txt; ~1–1.5 h, Barnard ~15 min, 4 GB in the main process).
  Reads each photo's IMAGE/FLAGS/VARIANCE block over the zone once (S3 section reads, cached as npz in --cache),
  stacks each visit × detector on a 2″ grid, detects on matched-filter S/N maps, fits every photo at all sources at once
  (ePSF at pixel centres, Gaia positions moved to the photo time), detects again on the residual stacks, fits again →
  results/<zone>/stack_sources.parquet, stack_forced.parquet, stack.json (contracts/dots.md). Reads no dot files:
  `new` compares with results/<zone>/sources.parquet, so run it after the zone's link/crossid on its dots folder. Two hunt
  boxes (0.24 deg²): CatWISE W1 16–17 found 45 → 1,387 of 1,613, Gaia pairs 6–15″ split 4 → 34 of 38; 14–16% of the
  sources have no catalogue entry, a quarter of those in `artifact_zone`. Every threshold is a STACK_* in config.py.
- Candidates for the site (contracts/api.md, candidates.json): `uv run skyshift-candidates [--zones hunt,pilot]
  [--cache <dir>]` reads results/ of every finished zone (largest first) and writes ../web/public/data/candidates.json;
  then `cd ../server && uv run python ../scripts/candidates/frames.py` adds static frames for the top ones.
  Reads only some columns (range reads), the dot files' `flags` and the movers' dots: ~0.4 GB from the bucket for the hunt.
  It drops movers near a planet itself (suspects tables written before the `planets` veto); `uv run skyshift-candidates
  --drop-planets ../web/public/data/candidates.json` applies only that to an existing export (reads no zone).
- Shape candidates (kind `shape`, "one object or two?"; shape.py, not a CLI step): `uv run python -m skyshift_pipeline.shape
  --zone hunt [--tables <dir>] [--out <dir>]` reads 38% of the zone's photos' member dots (8 shape columns, ~0.6 GB),
  writes results/<zone>/shape_candidates.parquet + shape_report.json (false alarms on held-out single Gaia stars,
  recall on Gaia pairs); the exporter lists every row. `--merge-into ../web/public/data/candidates.json --rows <parquet>`
  adds the rows to an existing export without re-running it (other kinds untouched, same order).
  Hunt (14 Nov): ≥ 20σ fires on 0.17% of isolated single Gaia stars (10/5,803) and 21% of Gaia pairs 3–10″ (axis
  within 2° of the pair's); at 6σ 4% vs 77%: faint uncatalogued neighbours are common, so the cut is high. A bright
  catalogued star at 15–35″ also stretches the moments (the annulus): first try's top 4 had one at 15.2″. 49 listed.
  Review fixes (15 Nov): steady_pa = the SWIR (D1–D3) and MWIR (D4–D6) arms each show it (≥ 2σ) at one PA (≤ 30°);
  any 2MASS entry within 15″ that isn't the object's own (moved to 2MASS's epoch) explains it; catalogued neighbours to
  20″ weighted by flux ratio × share of light in the window, summed as a vector, explain it when the projection on the
  measured PA ≥ 0.25 × excess; galaxy veto (classify galaxy, a good photo-z, or no Gaia star and ext > 1 / size > 1.12).
  The 49 listed → 5 (#2, #13, #25, #26, #29: the review's keepers): 28 galaxy-like, 17 neighbours (13 of them 2MASS).
  Two-source fit (15 Nov, shape_fit.py; `shape` runs it on the listed rows, `--no-fit` skips it; `python -m
  skyshift_pipeline.shape_fit --zone hunt [--rows <parquet>] [--tables <dir>] [--out <dir>]` refits an existing
  file): each candidate's member photos (≤ 400) read back from S3 (13 × 13 px of IMAGE, FLAGS, VARIANCE; the EPSF
  table once per detector: the same in every photo), one ePSF vs two, the companion at ONE sky offset for all photos
  (grid ±21″ then refined, the brighter one is the primary), Gaia/CatWISE neighbours ≤ 3 mag fainter as their own
  ePSFs. F2 is free per photo, so a bright star's ePSF errors lower χ² too (15 isolated Gaia stars of the
  candidates' G: Δχ² 300–2,700, one 39,000): the split rests on the companion's S/N (error = max(formal, the photos'
  scatter)) ≥ 5 in a detector and > −3 in all, 4.5–21″ out. Controls: 5 of 15 split, all onto CatWISE W1 16.5–17.1
  entries ~4 mag fainter (real, named via `companion_catalog`). Hunt: 4 of 5 split (5–12″, 0.8–1.9 mag fainter,
  Δχ² 12k–38k, none moves; #5's companion is a 2MASS entry); ~4 min per candidate on a loaded laptop. Columns
  `companion_*` (contracts/dots.md); server measured.py draws a split companion (reason `split_by_fit`).
- crossid, colours, lightcurves, astrometry, highpm use only stored tables (members → dots, sources, refcat/<zone>.parquet
  or refcat/<zone>_forced.parquet, forced/), never photos; a missing refcat or forced/ gives a warning and empty matches.
- astrometry (14 Nov night, ASTRO_SAT_OFFSET): saturated dots sit ~0.01–0.06 px off the star along −x (fixed in pixels,
  the 180° roll made it a fake westward pm: −110 mas/yr at G 8–10). Per detector × depth bin (band mag − 50% saturation
  mag) the median pixel offset vs Gaia is removed via each photo's Jacobian; frame offsets use unsaturated dots only.
  Hunt: G 8–10 −112 → −33, G 10–11 −81 → −28 mas/yr (pm − Gaia − ϖ·k); the rest is bright unsaturated dots (~−0.001 px).
- astrometry (15 Nov, ASTRO_PIXEL_MAP, ASTRO_BRIGHT_OFFSET): unsaturated dots also sit off in fixed pixels: a static map
  per detector (128 px cells, rms 3–9 mpx, up to 0.04 px) and a brightness term on the dot's own level (AB from flux_ujy −
  the detector's 50% saturation AB): −0.3 to −4.5 mpx along x just below saturation, +0.3 to +1.4 at the faint end.
  Fitted once per zone on quiet Gaia stars' unsaturated dots after the frame offsets (tiled: from the tiles' frame
  residuals, which now carry x, y, snr, level, kind and the Jacobian: ~27 M rows for the hunt), removed via the Jacobian.
  Hunt, fitted on half the stars, the other half, RA: G 9–10 −34 → −8, G 10–11 −29 → −11, G 11–12 −19 → −3,
  G 12–13 −7 → +2, G > 18 +15 → +2 mas/yr; G < 8 (saturated) −76 → −47. highpm: fast Gaia stars 77 → 78 of 106,
  fp_control 1 → 1, Gaia stars G 9–13 with a fake pm 18 → 14. Open: 6 of those 14 sit in the zone's SW corner
  (RA < 121, Dec < 15: 43 such stars; elsewhere 0.07%). Not the calib quadratics of photos that cover only a patch of the
  zone (604 of 3,552 photos have a coefficient > 2″; leaving their dots out: 6 → 4).
- highpm: CatWISE's pm of a saturated W1 < 8 source checks nothing. Before a vetted row counts as `new`, the deep check
  asks ESA Gaia TAP (no magnitude cut, along our motion back to 2016) and refcat's 2MASS (back to ~2000): a match makes it
  status `gaia` or `2mass`; a failed query keeps `new` (report deep_check.deep_gaia_checked false). Tests stub the query.
- Fit cut (14 Nov night): astrometry writes a fit only with >= ASTRO_MIN_MOTION_DOF (8) degrees of freedom left for the
  motion after the detector zero points (detectors seen in both visits; astrometry.motion_dof): hunt 287,695 → 277,276
  fits, Gaia stars with a fake pm 214 → 149, no fast Gaia star lost. highpm: unconfirmed fits from 4.5σ (fp_control
  0.30 expected new fakes; 4.25σ 0.91, 4.0σ 2.1), a fast motion Gaia confirms from 3σ, CatWISE (its own pm fast and
  ≥ 3σ) from 3.5σ: fast Gaia stars counted 63 → 77 of 106 detected, raw-table fakes 211 → 146, fp_control 1 → 1.
- Every step skips what exists: the photo list, dots/<obs_id>D<det>.parquet, cache/mpc/<zone>/run_<obs_id>.json.
  link, spectra, classify, photoz, tracklets, score and suspects are cheap and always recompute from what is there.
- Deblending (15 Nov): sep `deblend_cont` is config `DOTS_DEBLEND_CONT` (0.005, the sky run) except for the zones of
  `DOTS_FINE_ZONES` (the five featured targets): `DOTS_FINE_DEBLEND_CONT` (1e-4) finds stars in a bright star's glow
  (R Dra D4: 3 of 4 Gaia neighbours at 92–162″, none at 0.005). Their photos also lie in ecliptic zones and dot files are
  never rewritten, so those zones write `dots_deblend1e-4/<obs_id>D<det>.parquet` (config.zone_dots; a new value gets a
  new folder) and keep any existing forced/ file. `dots` and `link` use the zone's folder; link records it in
  summary.json `steps.link.dots_dir`, and every later step, skyshift-candidates, shape and server measured.py read that
  folder (store.dots_dir, set per step by the CLI), so members.dot_row always points into the files it was made from.
  Each dot file's metadata says which setting made it (`detect`: thresh, min_snr, deblend_cont, dots_dir).
  Fine detection v2 (15 Nov night, config.zone_detect → DetectSettings): the fine zones also use sep clean_param 3
  (DOTS_FINE_CLEAN_PARAM), the saturation guard (a dot the sky setting doesn't also find within 1 px is dropped within
  DETECT_SAT_GUARD_PX = 6 px of an OVERFLOW/NONLINEAR pixel: wing pieces of saturated stars) and the ePSF second pass
  (psf.residual_companions: subtract the ePSF of unsaturated S/N ≥ 10 dots, sep on the residual within 6 px, mirror test,
  guard; config PSF_PASS_*). Its dots have sep_flag 0x200 and are measured on the residual (dots._measure_dots on IMAGE
  minus the models). Folder from every setting (config.dots_dir_for): dots_deblend1e-4_cp3_g6_psf/; the plain 1e-4
  folder of 15 Nov stays as written. Cost: a full photo's dots ~2× the sky run's time (a second sep run for the guard,
  the residual pass). Deblend test (43 hunt photos + R Dra): pairs 2–3 px 9% → 81%, known asteroids next to a brighter
  star 4 → 14 of 17, R Dra D4 glow neighbours 0 → 3 of 4; ~12–50 fake dots per 21,900, mostly near G < 10 stars.
  Glow model (16 Nov, contracts/dots.md "Glow model"; DetectSettings.glow, config GLOW_*, DOTS_FINE_GLOW true,
  DOTS_SKY_GLOW false): dots.glow_dots fits skyshift_core.glow on the photo, detects (guard and second pass too) on
  IMAGE minus the glow with noise × k, keeps each modelled star's nearest detection as its own dot (aperture flux on
  IMAGE), drops the other detections within max(GLOW_SELF_PX, core + 1.5 px) as pieces; folder suffix `_glow`
  (fine zones: dots_deblend1e-4_cp3_g6_psf_glow/); metadata `detect.glow`, `glow_config`, n_glow_*. stack: load_cut
  subtracts each block's glow (glow_cut; STACK_GLOW), every S/N map gets a local noise factor around Gaia-confirmed
  saturated holes and S/N ≥ STACK_GLOW_MIN_SNR peaks (local_significance; peaks found on snr, kept on snr / k), and a
  hole's rim peaks become one peak at the star (rim_peaks). Cost: ~3–5 s per photo for the fit (400 stars).
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
- qa (after calib, zone-wide; tiled: once, before suspects starts): a grade per photo, green/amber/red + reasons, because
  NASA's header QA passes every photo. Each photo against the zone's photos of its detector: dots per photo (vs detector
  and visit), FWHM and PSF/aperture flux of S/N > 50 dots, raw offset and scatter vs forced Gaia, SAA distance, SATSTRKS
  (all from the dot files' footers: one ranged read each), calib_photos.parquet's per-photo scatter after the distortion
  fit, and, where the dot files are local (tiled mirror, local root), the share of bright dots much sharper than the
  stars with OUTLIER at the peak (never OUTLIER alone). → results/<zone>/qa_photos.parquet (obs_id, detector, grade,
  reasons, metrics) + qa.json; lightcurves gives red photos photo_ok false; suspects reads the grades. Thresholds:
  config QA_*. ecl15 sample (1,209 photos): 14 red (1.2%: 4 SAA passages, 9 visit-3 D6 artefact photos, 1 bad WCS);
  hunt: the 2 bad-WCS photos (0.99″, 0.74″ after calib). MWIR sharp artefacts grow with time (D6: 1% → 24% → 44% of
  bright dots in visits 1–3): every visit-3 D6 photo is at least amber. In `all` a failing qa is logged and skipped.
- `known` uses MPC asteroid + comet orbits propagated with kete from SPHEREx's own position (IMAGE header X_SC..,
  geocentric equatorial km, at MJD-AVG); cache/mpc/<zone>/ with exact per-photo positions, orbit snapshots
  cache/mpc/orbits/{mpcorb,cometels}_<date>.parquet. MPC is the only source of asteroids and comets (no flag, no env var).
  Comets have no V and no non-gravitational terms; C/2025 R2 still agreed with JPL Horizons to ~1″.
- `known` then adds the planets and their large moons (known_planets.py, 15 Nov; config PLANET_SYSTEMS), which MPC
  orbits don't hold: on 15 Nov 11 of the 22 movers with static frames were Iapetus (×6), Titan, Rhea, Hyperion,
  Titania or Uranus, each within 0.4″ of JPL Horizons. Horizons (geocentric: ≤ 2.3″ off at Jupiter, far inside
  KNOWN_ARCSEC) only for the visits where a planet comes within PLANETS_MARGIN_DEG + its system's radius of the zone
  box; 10-minute tables interpolated to each photo → cache/planets/<zone>/run_<obs_id>.json, the MPC files' shape,
  class `planet`/`moon`. load_predictions reads both folders (suspects, orphans); score leaves planets and moons out.
  A failed query writes nothing and `known` still succeeds (summary `planets.error`): run `known` again. ecl35 from a
  laptop: Saturn and Neptune in 55 of 491 sessions, 16 of its 91 movers within 1.1″ of a moon, 2.5 min.
  Changing the bodies means deleting cache/planets/<zone>/, then `known`, `suspects` and the exporter again.
  On top of that, offline: suspects and skyshift-candidates drop every mover near a planet (skyshift_core.planets,
  astropy's built-in ephemeris, contracts/api.md v3.18), which also catches moons outside PLANET_SYSTEMS (Carme),
  the planet's glare, and zones whose `known` ran before the Horizons step. Never SkyBoT.
- minorplanetcenter.net times out from GCP machines (14 Nov). The newest snapshot in cache/mpc/orbits/ is always reused,
  so refresh it from a laptop: `uv run skyshift-pipeline mpc-orbits --root gs://nasa-glazing-data [--force]` (~1 min, gcloud
  login; not part of `all`, no --zone). It writes today's mpcorb_/cometels_<date>.parquet, which then win; old ones stay.
  Without --force a snapshot already made today is kept. Sessions already in cache/mpc/<zone>/ keep their old positions:
  delete those run_*.json files to recompute them with the new orbits.
- IRSA down (14 Nov evening, TAP and SIA 502): `uv run skyshift-pipeline s3index --root gs://nasa-glazing-data
  [--workers 128] [--procs N] [--periods ...] [--force]` (a tool like mpc-orbits, no --zone, not in `all`) lists all
  1.63 M Level 2 files on S3 (dr1 + qr3, 151 periods) and range-reads each IMAGE header (~34 KB): photolists/s3index/
  <dr1|qr3>/<period>.parquet + .json sidecar (failed keys), _complete.json last; resumable. Rows = TAP's photo-list
  columns and values (pilot: all 990 equal; s_region within 1 ulp). photolist uses it when TAP fails after its retries,
  or always with SKYSHIFT_PHOTOLIST_SOURCE=s3index: footprints intersecting the zone box with great-circle edges (as
  INTERSECTS), clean's dedupe, add_sessions; only if every period shard exists (stats `index_complete`, `index_failed_keys`).
- History: SkyBoT was removed after #64 (docs/research/2026-11-14-mpc-vs-skybot-score.md): on the pilot MPC gave
  G2 recall 92.2% with predictions 0.27″ from the matched movers vs SkyBoT's 1.43″.
- Every threshold lives in src/skyshift_pipeline/config.py.

## Tiles (tiles.py): large zones combined per sky tile
- `all` on a zone > TILE_AUTO_MIN_DEG2 (30 deg²; hunt yes, pilot no) runs link … suspects per tile in spawned processes
  (`--workers`), then merges into the same results/<zone>/ files. `--tiles off|N` (env SKYSHIFT_TILES), `--tile-deg D`
  override; `--tile-dir` (env SKYSHIFT_TILE_DIR) is the local work dir (default the temp dir, removed after;
  `--keep-tiles` keeps it). Single steps stay untiled. Each run has a fresh work dir; dead runs' dirs are removed, so a
  pre-empted zone simply runs `all` again.
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
  Gaia-matched sources + S/N ≥ 30 and the merged raw spectra), qa (on the mirror), classify's stellar locus, the
  light-curve error model and bad photos (+ qa's red ones), astrometry's per-photo frame offsets and saturated-dot offsets; photoz/lightcurves/astrometry reports on the merged tables;
  tracklets (right after merge 1, next to calib), fragments (after merge 2), highpm, orphans, score and suspects run once on the merged tables
  (tracks cross tiles; their 15′/2′ controls and orphans' 300–7200″ pairs need no border). link's nonfixed_dots.parquet is
  merged like movers (owner: the dot's position).
- Dot files are mirrored once into the work dir without flux_ap_ujy/flux_ap_err_ujy (TILE_SLIM_DOTS; unread by the
  combining steps, 3/4 of a full read); each tile reads every photo it overlaps. No mirror when the disk lacks room
  (TILE_DISK_RESERVE_GB; hunt: ~6 GB slim dots + 1.9 GB forced): tiles then read the root, slower, same results.
- Equality (tests/test_tiles.py; pilot photos with 0.3° tiles): tiled == untiled table by table and report by report.

## Distant planets (orphans.py): what the route can and cannot find
SPHEREx looks ~90° from the Sun, so a planet at 70–1000 AU drifts 0.5–0.002″/h inside a visit and link chains it into a
fixed source seen in ONE visit; six months later it is 4,800″ (70 AU) to 390″ (1000 AU) away along ecliptic longitude.
`orphans` (after highpm, once on the merged tables) lists single-visit sources with no Gaia/CatWISE/2MASS entry whose
spot the other visit covered (>= 6 photos), minus bright-star fragments and the zone's 3′ edge; fits each one's drift
from its member dots (projected column reads, ~900 dot files for the hunt); pairs orphans of the two visits 300–7,200″
apart when one heliocentric distance r explains both positions with a bound orbit AND both measured drifts (Earth's
reflex at that r) AND the same-detector fluxes; and repeats the pairing 50× with the later visit shifted cyclically in
its box: the mean is the chance expectation. results/<zone>/orphans.parquet (every candidate, `reason`, drift, `in_pair`,
`track_in_zone`) and distant.json (passing pairs with distance, fluxes, positions, times; control counts; the depth
and reach searched; `open` orphans). Can find: a planet detected (S/N >= 5) in >= 3 sessions of BOTH visits, with no
catalogue entry within crossid's radius at either spot (~8% of the ecliptic sky is lost to CatWISE alone). Brightness:
single-exposure depth AB ~17.4–17.7 (D1–D4), no stacking: a Neptune-size planet (albedo 0.4) to ~290 AU, an Earth
much nearer (distant.json `reach_au_at_d2_depth`). Cannot find: anything fainter; movers > ~1″/h (score/suspects do
those); a planet whose 70–100 AU fragments have < 3 sessions each; one blended with a star; a partner beyond the zone
edge. Zone edge: the partner can be up to 2° away, so each drift-fitted orphan gets `track_in_zone` (share of its
70–1000 AU parallax track inside the zone box less the 3′ margin); those < 1 are listed in distant.json `open` with the
track's 70 and 1000 AU ends. No cross-zone pass yet: it would read every zone's orphans.parquet and pair `open` orphans
with the neighbours' orphans using the same model (orphans.pair_model). Known bodies: an MPC object (the `known`
predictions; tiles pass theirs) moving like an orphan (rate within 0.3″/h + 3σ) and within 30″ of it is tagged
`known_name` (distant.json `known`, `known_a/b`): a recovered TNO validates the route, it is not dropped. Hunt (14 Nov
tables): 447 orphans, 892 pairs tested, 1 passing vs 1.5 expected by chance (p 0.78), no known slow body bright enough.
Table-level injection at the hunt's cadence (70–1000 AU, 3–20 dots per visit): 83–100 % recovered, r within ~2 %.
The step never raises (errors go to its stats).

## Completeness (inject.py, step `completeness`): what "found nothing" means
- Once per zone after score (a tiled `all` runs it after the tiles; env SKYSHIFT_COMPLETENESS=off skips it). A 1° × 1°
  sub-area at the zone's centre (INJECT_*; smaller when it holds > 2 M real dots): its real dots from every photo that
  overlaps it, plus synthetic dots of 8 × 400 objects on straight tracks (0.01–300″/h log-uniform, any direction, S/N 5–100
  per dot in a median-noise photo of each detector) at the real exposure times, detectors and footprints. Flux from the
  photo's own noise; kept with the zone's own detection probability (dot S/N counts against a power law: hunt 0.3 at
  S/N 5.25, 0.7 at 6.75, ~1 from 9); position scatter 1.6 × pos_err; elong drawn from real dots, stretched by the trail.
  Real + synthetic dots go through link.fixed_groups/classify unchanged; nothing is written but
  results/<zone>/completeness.json (grid rate × S/N, by_rate, by_snr, limits, headline, vs_recall) and a summary entry.
- Unit = score's (one object in one session, ≥ 2 exposures, ≥ 10 px inside); found = a mover within 12″ of the object at
  the mover's time. Other outcomes: fixed (own fixed source: too slow), merged (joined a real star), undetected, vetoed,
  unlinked; `fixed_single_visit` per object and visit (its own fixed source seen in that visit only: the slow route,
  orphans). Hunt centre (1.0 deg², 445 photos, 329k real dots; 12 s, ~0.5 GB on a laptop from local files; a run on
  the bucket adds the read of ~445 dot files): ≥ 50% found per session at 0.46–300″/h, ≥ 90% at 2.2–300″/h (S/N ≥ 20);
  0.1–0.2″/h 7%, 0.2–0.5″/h 30% (these become single-visit fixed sources, 87–99%); 10–90″/h S/N ≥ 20 94% vs the
  measured known-asteroid recall 90.4% (`vs_recall` ratio 0.96: missing dots, masks and artefacts aren't modelled).
  Same S/N in every detector by default (INJECT_MWIR_SNR_FACTOR; asteroids are fainter in D4–D6: 0.3 → S/N 5–7 49%).

## Rules (summary)
- Session = run of the zone's exposure times with gaps < 10 min (core.sessions over all photos), one per photo; visit = core.visits.
- dots: core.detect thresh=2 (per pixel), keep snr >= 5; saturated dots re-centroided on the wings; flags = 3×3 OR,
  flags_peak = bits at the peak pixel; FINAST != 0 → empty dot file. Dots v4 (14 Nov) add an ePSF position fit
  (x_psf, y_psf, ra_psf, dec_psf, pos_err_psf_arcsec, psf_pos_ok; no pixel-phase pull, ~2× smaller scatter vs Gaia):
  use them where psf_pos_ok, never phase-correct them (contracts/dots.md, Versions).
- link: fixed = FoF 4″ groups in >= 3 sessions; drop dots within 6″ of fixed; pair dots of one session
  (≤ 3″ + 1″/min × Δt, or a simultaneous D1+D4/D2+D5/D3+D6 pair ≤ 3″, elong ≤ 3); coverage ≥ 3 sessions from footprints.
- link release (review of #176, 14 Nov; config FIXED_RELEASE_*, FIXED_VETO_BRIGHT_RATIO): out of a would-be fixed group
  come dots > 6″ from its median, dots ≥ 3× its median flux in their detector (unsaturated), and the off dots of a
  session 2″ + 3σ off the visit's other sessions (and > 5× their median offset: slow movers stay one group for orphans).
  A released group of ≥ 3 sessions stays fixed only if catalogued (refcat ≤ 2.5″, proper motion applied; link reads
  refcat/<zone>.parquet) or in ≥ half its parent's sessions; else its far dots rejoin the parent. A free dot ≥ 3× every
  fixed source within 6″ escapes the veto. Coverage (missed rule): an exposure covers only ≥ 25 px inside a photo
  (edge veto stays 10 px), ≥ 10″ inside the zone box, and not where that photo has another dot within 15″ (or S/N ≥ 100
  within 30″); 2 exposures both with a detector pair may miss 1. `still`: a free dot at the mover's place (2″) in 2 other
  sessions, or in 1 in its detector within 2× its flux. Boxes b1–b3 (4.1 deg², hunt zone): V < 16 movers 51 → 55
  of 64, uncatalogued fixed fragments 176 → 55, movers 108 → 118. Changing these means rerunning link onward.
- link also writes members.parquet (source_id, obs_id, detector, dot_row = row in the dot file as written; dot
  files are never rewritten) and nonfixed_dots.parquet (every dot in no fixed group with elong ≤ 3, `near_fixed` within 6″
  of one; ~0.8 M rows, ~20 MB for the hunt) for tracklets' recovery. spectra: 102 channels (ours, 17 log-spaced per detector), MASK bits out, clipped
  weighted mean per channel, chi2_nu and last/first visit ratio for variability; vectorised, no loop per source.
- tracklets (tracklets.py, zone level after link, before score; ~5 s on the hunt): links movers of different sessions of
  one visit into tracks: seed pairs ≤ 36 h apart at 0.5–150″/h, tested in nearby sessions, grown session by session
  with local fits (linear + the visit's acceleration, quadratic with ≥ 4 members over ≥ 24 h), leave-one-out pruned;
  a track needs movers in ≥ 3 sessions (exactly 3: span ≤ 36 h). Main-belt asteroids share one apparent acceleration
  per visit (hunt: 0.028″/h², 99% within 0.016 of the visit's vector), measured from the first pass's long tracks.
  Then, in every other session of the visit within 60 h, link's non-fixed dots at the predicted position (3–8″) are
  recovered detections. Writes tracklets.parquet (one row per mover: mover_id = suspects' id
  `<zone>-<cell>-<round(mjd×1440)>`, tracklet_id (−1 = none), n_sessions, rate_arcsec_h, pa_deg, rate_sigma, rate_snr,
  fit chi2…) and tracklet_dots.parquet (recovered dots: obs_id, detector, dot_row, sep_arcsec…). score counts a known
  sighting found when a mover OR a recovered dot matches it (`recall`; movers alone: `recall_movers`).
  Hunt: 124 tracks (all known asteroids, none mixed), 1245 of 6143 movers; control (each session's movers moved
  0.5–1.5°) 0 tracks in 4 runs; recall at V < 16 90.4% → 94.7% (433/457), control 0; 0 of 228 suspects on a track.
  The rest of the misses: no dot (12), or a dot inside a star's fixed group (≤ 4″, not a free dot).
  score tells the two kinds of dot apart (members.parquet: a dot in a fixed source's group vs a free dot, the only kind
  link pairs): recall_units `dot_free`/`dot_fixed`/`miss`, recall.json `free_dot_share` and `misses`. Hunt (15 Nov
  tables, V < 16): dot_share 97.4% but free_dot_share 95.0%; misses no_dot 11, fixed_dot 11, free_dot 1; control
  dot_share 13.6% → free 2.2% (most chance dots within 12″ are stars').
  Safe by design: a failure writes empty tables (score then counts movers only), each linking pass over a visit
  stops after TRACK_VISIT_MAX_S keeping the strongest seeds, and link never fails over nonfixed_dots (not suspects' freedots.parquet: that one
  has pixel x, y and no near-fixed dots, for vet's hot-pixel maps).
- suspects: no known object within 30″ (same session), not near a planet at its time (skyshift_core.planets: Mars 60′,
  Jupiter 80′, Saturn 45′, Uranus 5′, Neptune 3′: the planet, its moons, its glare; summary `cut_planet`, veto
  `planets`; 15 Nov: 131 of the 1,885 movers listed in candidates.json, 54 of the 56 grade A, e.g. Iapetus ×17, Oberon
  ×17, Himalia ×13), no ghost/crosstalk/halo/bloom bit, SOURCE not at every dot's peak,
  no bright fixed source within 30″, no Gaia DR3 within 6″, no CatWISE2020 within 6″ (from refcat/<zone>.parquet;
  the services only if that file is missing: 50-circle ESA batches took 30+ min for 30k movers).
- Vetting (vet.py, 14 Nov; config VET_*): after those cuts every suspect gets `grade` and `reasons`. Gates on its own dots
  (link carries each mover's dots as lists dot_obs, dot_det, dot_x, dot_y, dot_flux, dot_snr, dot_size, dot_fpk, …) and on
  zone maps from freedots.parquet (link: every free dot's obs_id, detector, x, y; tiles merge it): hot_pixel (±1 px with
  movers' dots in ≥ 3 exposures or free dots in ≥ 5), bad_column (≥ 3 free dots within ±0.5 px in x, one exposure), edge
  (6 px), masked_peak, sharp_outlier (√(a·b) < 0.7 × stellar at that S/N with OUTLIER; OUTLIER alone never: 52% of real
  asteroids), flux_ratio (> 4× within a detector), red_photo (qa_photos.parquet, if present), 2mass (refcat, 6″).
  C = any reason (kept in the file), B = none, A = candidate mover: on a track over ≥2 sessions with motion ≥5σ, no
  catalogue or known match (suspects.track_grades after vet: B, and its tracklets.parquet row, joined on suspect id =
  mover_id, has tracklet_id ≥ 0, n_sessions ≥ VET_A_MIN_SESSIONS, rate_snr ≥ VET_MOTION_SIGMA). Suspects on a track
  carry its n_sessions, rate_arcsec_h, pa_deg, rate_sigma (suspects.json; null off a track); a missing or unreadable
  tracklets.parquet changes nothing (stats vetting.tracklets). candidates.py lists A first and names the track in `why`.
  Gates run on the suspects only, never before pairing: recall is unchanged. completeness' vs_recall compares the
  synthetic movers with recall.json `recall_movers` (movers alone) when present.

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
- Review fixes (15 Nov; config SPECTRA_*, LABEL_*, CLASSIFY_*, PHOTOZ_*): spectra errors per dot √((kσ)² + (floor ×
  channel median)²), floors 1.7–2.5% per detector (k = 1 measured on pairs); single bad dots out (aperture/PSF > 2, or
  √(a·b) < 0.7 × the stars' at that S/N); a channel > 5σ and > 50% off its ±2 neighbours' median is dropped unless its
  deviating dots span ≥ 2 visits; `visit_bits` per channel, and a band label needs its window seen in ≥ 2 visits.
  classify: stellar locus per S/N bin (S/N ≥ 8; stars are 1.23× wider at S/N 8–12 than above 80), parallax ≥ 3σ = star;
  ice needs catalogue (else synthetic) Vega colours H − K ≥ 0.6, J − H ≥ 1.0 near the reddening line, W1 − W2 < 0.4,
  no PAH label with it. photoz: `good` needs z > 0.01; `label_pah_33_z` (PAH at the galaxy's z, seen in ≥ 2 visits).
  Export (clean_labels): PAH only from photoz (or z < 0.02), no ice on a good-photo-z galaxy, no h2o_vapour/co_23 on a
  Gaia M dwarf (BP − RP ≥ 1.8: expected there); a Gaia G < 20 within 6″ at the epoch counts as a Gaia match (blend);
  ≤ 20 PAH-only rows. crossid matches 2MASS at its own epoch (pm from Gaia, else CatWISE). The 97 listed (main, 19:07
  tables) → 9: the two L dwarfs (brown_dwarf_like kept), 6 water vapour, 1 PAH; ice 5 → 0, CO 25 → 1.
  Rerun per zone: spectra, classify, photoz, then shape and candidates (no re-extraction, no recombine).
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
