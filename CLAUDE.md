# SkyShift

Web tool for NASA Space Apps 2026 "Planet X and SPHEREx". Paste coordinates and blink any spot of
NASA's SPHEREx sky on two clocks: visits six months apart, and exposures inside a visit.
Measure what moved and check it against Gaia and known asteroid positions.
Plans: docs/skyshift-hackathon-plan.html (what and why) · docs/skyshift-dev-plan.html (how).

## Areas and owners
- core/                Shared science: visit rule, flag masks, detect, centroid, shift, photometry. P1.
- pipeline/            Pilot and hunt zone on the cloud machines. P1.
- scripts/validate/    V1–V8, writes web/public/data/validation.json. P1.
- server/              FastAPI on Cloud Run. build_blink() for any coordinates. P2.
- scripts/precompute/  Featured targets → web/public/data/<slug>/. P2.
- web/                 Vite + TypeScript. src/app sky card api data score → P3. src/blink vote → P4.
- contracts/           Source of truth for every interface: api.md, flags.md, dots.md. P1.
- fixtures/            Small real samples. Use them in tests and until real endpoints exist.
- infra/               worker/ → P1. run/ and firebase/ → P2.

## Rules
- Stay inside your area. If something outside must change, say so in the PR description.
- Contract changes are their own PR and wait for P1. Owners merge their own area when tests pass.
- Static first: the site reads /data/<slug>/ before it calls /api/.
- Match the contracts exactly: names, types, units (degrees, arcsec, µm, MJD, µJy), the visit rule.
- Tests before commit: `uv run pytest` in core/, pipeline/, server/; `npm test` in web/.
- No secrets in the repo or in chat. Google access comes from gcloud. Anything else goes in .env.
- All product code is written after Sat 14 Nov 09:00. Keep the Co-Authored-By line on commits.

## Domain facts
- Exposure = one SPHEREx photo (113 s). Session = 1–5 exposures 2.1 min apart.
  Visit = the 2–4 weeks in which SPHEREx sees one spot, every six months: about 21 sessions,
  55–60 exposures. Dates depend on the position. There are no fixed "passes".
- Visit rule: sort the exposure times at a position; start a new visit where the gap exceeds
  60 days; cut any visit longer than 45 days (near the ecliptic poles) into 30-day bins.
  A session is a run with gaps under 10 minutes.
- Time: MJD-AVG from the IMAGE header (UTC, exposure midpoint). In search results, the middle
  of t_min and t_max. MJD-BEG and MJD-END also exist. DATE is the file creation time: never use it.
- Band: detector from energy_bandpassname ("SPHEREx-D2") or the header, never the filename.
  IMAGE header DETECTOR: 1–3 SWIR, 4–6 MWIR. The name suffix _<exp>D<det> (e.g. _2D1) agrees,
  but never parse names.
- 6 detectors: D1 0.75–1.1, D2 1.1–1.6, D3 1.6–2.4, D4 2.4–3.8, D5 3.8–4.4, D6 4.4–5.0 µm.
  102 channels in all. Pixel 6.15″ (6.152″ measured). PSF FWHM 7.53″. Images 2040 × 2040.
  DETCOORD = 'sky': MWIR pixels are already flipped on X to match the SWIR view.
- D1+D4, D2+D5, D3+D6 are exposed at the same moment: a D1→D4 flip is colour, not motion.
  Compare brightness only within one detector.
- A file is one FITS: IMAGE, FLAGS, VARIANCE, ZODI, EPSF, WCS-WAVE, 60.2 MB (measured). We read
  IMAGE, FLAGS and WCS-WAVE: 22.9 MB (+ VARIANCE for error bars: 39.6 MB). The parts are contiguous,
  so range reads can skip ZODI and EPSF. FLAGS is a tile-compressed HDU: let astropy decode it.
- Units: IMAGE is in MJy/sr (BUNIT 'MJy / sr', float32). 1 MJy/sr = 889 µJy in a 6.15″ pixel.
  TELAPSE 118 s from start to stop.
- Flags (contracts/flags.md). Mask for detection and photometry: TRANSIENT, OVERFLOW, SUR_ERROR,
  NONFUNC, DICHROIC, MISSING_DATA, HOT, COLD, PHANMISS, NONLINEAR, PERSIST, STREAK, SNOWBALL.
  Record on the dot, don't mask: CROSSTALK, GHOST, GHOST_FPA, GHOST_EXT, BLOOM, HALO, OUTLIER.
  Never mask SOURCE (set on about 99% of pixels) or FULLSAMPLE.
  OVERFLOW or NONLINEAR in a source's core means saturated.
  Bit names and numbers confirmed on the first file (14 Nov, FLAGS header MSKNnnnn keys).
  SOURCE was on 99.37% of pixels; the mask list removed about 2.1%.
- Saturation starts near AB 10–11 (to check on real data). Barnard's Star, RW Aurigae and
  R Draconis are expected to saturate: centroid on the wings and mark the core.
- Collections: spherex_dr1 and spherex_qr3. Skip *_deep. Search with astroquery Irsa.query_sia;
  the TAP table name is unverified.
- S3: s3://nasa-irsa-spherex (us-east-1, anonymous). Layout:
  <dr1|qr3>/level2/<planning period>/l2b-v<version>-<date>/<detector>/<file>.fits
  Cutouts (?center=RA,DEC&size=DEG, degrees) work on IRSA's servers only, not on S3.
- No CDS: SIMBAD, Aladin's CDN and CDS tiles are down. Aladin Lite 3.8.2 from jsDelivr,
  IRSA layer URLs only, SIMBAD features off. Names from our /api/resolve list.
- Targets (web/src/data/targets.json): Barnard's Star, Epsilon Indi B, WISE 0855−0714,
  RW Aurigae, R Draconis; plus comet 3I/ATLAS and two numbered asteroids.
  Move Gaia DR3 positions from epoch 2016.0 to each visit's mid-time.
- Docs: https://caltech-ipac.github.io/irsa-tutorials/ ·
  https://irsa.ipac.caltech.edu/data/SPHEREx/docs/SPHEREx_Expsupp.pdf

## Cloud
- Google Cloud project nasa-glazing, region us-east4, zone us-east4-a.
- Bucket gs://nasa-glazing-data: photolists/, dots/, results/, cache/.
- Firestore (us-east4): candidates, votes. Create-only rules. Site on Firebase Hosting, API on Cloud Run.
- Machines: skyshift-test (e2-standard-8) for most work; skyshift-worker (n2-highcpu-64 spot)
  only for the hunt zone after gate G2. Stop machines when idle.

## Not yet verified (check on real data, then fix here and in contracts/)
Facts measured on the first file: docs/research/2026-11-14-first-photo-facts.md.
- Units to µJy: the 889 µJy per MJy/sr per pixel conversion, against a catalogue star.
- Saturation level: where saturation starts (AB 10–11 expected).
