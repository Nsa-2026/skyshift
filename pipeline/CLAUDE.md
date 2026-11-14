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
  fixed, crossids, refcat) tags each source `bright_fragment` (BLOOM on > 50% of its member dots, no Gaia/2MASS within
  3″ of its spectrum or link position: a piece of a saturated star's ring), `saturated_star` (the same with a match),
  `blend` (≥ 2 Gaia G < 18 within 8″), else "". Fragments carry their parent (brightest Gaia G < 12 within 60″, and that
  star's own source); blends both Gaia ids, separation and ΔG → results/<zone>/fragments.parquet + fragments.json.
  Consumers, each unchanged without the file: highpm (fragment → reason `fragment`, blend → status gaia), orphans
  (drops both), shape (drops fragments), candidates.py (fragments never listed, blend → reason `blend`), server
  measured.py. Rerun after it: highpm, orphans, shape, candidates. Hunt (14 Nov): 2,234 / 759 / 7,126 of 303,045
  sources; 2,226 fragments have a parent (747 stars), 2,031 the parent's own source; ~45 s locally.
- Candidates for the site (contracts/api.md, candidates.json): `uv run skyshift-candidates [--zones hunt,pilot]
  [--cache <dir>]` reads results/ of every finished zone (largest first) and writes ../web/public/data/candidates.json;
  then `cd ../server && uv run python ../scripts/candidates/frames.py` adds static frames for the top ones.
  Reads only some columns (range reads), the dot files' `flags` and the movers' dots: ~0.4 GB from the bucket for the hunt.
- Shape candidates (kind `shape`, "one object or two?"; shape.py, not a CLI step): `uv run python -m skyshift_pipeline.shape
  --zone hunt [--tables <dir>] [--out <dir>]` reads 38% of the zone's photos' member dots (8 shape columns, ~0.6 GB),
  writes results/<zone>/shape_candidates.parquet + shape_report.json (false alarms on held-out single Gaia stars,
  recall on Gaia pairs); the exporter lists every row. `--merge-into ../web/public/data/candidates.json --rows <parquet>`
  adds the rows to an existing export without re-running it (other kinds untouched, same order).
  Hunt (14 Nov): ≥ 20σ fires on 0.17% of isolated single Gaia stars (10/5,803) and 21% of Gaia pairs 3–10″ (axis
  within 2° of the pair's); at 6σ 4% vs 77%: faint uncatalogued neighbours are common, so the cut is high. A bright
  catalogued star at 15–35″ also stretches the moments (the annulus): first try's top 4 had one at 15.2″. 49 listed.
- crossid, colours, lightcurves, astrometry, highpm use only stored tables (members → dots, sources, refcat/<zone>.parquet
  or refcat/<zone>_forced.parquet, forced/), never photos; a missing refcat or forced/ gives a warning and empty matches.
- astrometry (14 Nov night, ASTRO_SAT_OFFSET): saturated dots sit ~0.01–0.06 px off the star along −x (fixed in pixels,
  the 180° roll made it a fake westward pm: −110 mas/yr at G 8–10). Per detector × depth bin (band mag − 50% saturation
  mag) the median pixel offset vs Gaia is removed via each photo's Jacobian; frame offsets use unsaturated dots only.
  Hunt: G 8–10 −112 → −33, G 10–11 −81 → −28 mas/yr (pm − Gaia − ϖ·k); the rest is bright unsaturated dots (~−0.001 px).
- highpm: CatWISE's pm of a saturated W1 < 8 source checks nothing. Before a vetted row counts as `new`, the deep check
  asks ESA Gaia TAP (no magnitude cut, along our motion back to 2016) and refcat's 2MASS (back to ~2000): a match makes it
  status `gaia` or `2mass`; a failed query keeps `new` (report deep_check.deep_gaia_checked false). Tests stub the query.
- Every step skips what exists: the photo list, dots/<obs_id>D<det>.parquet, cache/mpc/<zone>/run_<obs_id>.json.
  link, spectra, classify, photoz, tracklets, score and suspects are cheap and always recompute from what is there.
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
  cache/mpc/orbits/{mpcorb,cometels}_<date>.parquet. MPC is the only source of known objects (no flag, no env var).
  Comets have no V and no non-gravitational terms; C/2025 R2 still agreed with JPL Horizons to ~1″.
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
  Safe by design: a failure writes empty tables (score then counts movers only), each linking pass over a visit
  stops after TRACK_VISIT_MAX_S keeping the strongest seeds, and link never fails over nonfixed_dots (not suspects' freedots.parquet: that one
  has pixel x, y and no near-fixed dots, for vet's hot-pixel maps).
- suspects: no known object within 30″ (same session), no ghost/crosstalk/halo/bloom bit, SOURCE not at every dot's peak,
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
