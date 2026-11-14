# SkyShift

Web tool for NASA Space Apps 2026 "Planet X and SPHEREx". Paste coordinates and blink any spot of
NASA's SPHEREx sky on two clocks: visits six months apart, and exposures inside a visit.
Measure what moved and check it against Gaia and known asteroid positions.
Plans: docs/skyshift-hackathon-plan.html (what and why) · docs/skyshift-dev-plan.html (how).

## Areas and owners
- core/                Shared science: visit rule, flag masks, detect, centroid, shift, photometry. P1.
- pipeline/            Pilot and hunt zone on the cloud machines. P1.
- scripts/validate/    V1–V11, writes web/public/data/validation.json. P1.
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
  60 days; cut any visit longer than 75 days (near the ecliptic poles) into equal parts of at most
  30 days. Mid-latitude seasons of 45–55 days stay one visit.
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
- A file is one FITS: IMAGE, FLAGS, VARIANCE, ZODI, EPSF, WCS-WAVE, 60–70 MB. The pipeline reads all
  six (about 56 MB) so each photo is read once; cutouts read IMAGE, FLAGS, VARIANCE, WCS-WAVE.
  FLAGS is a tile-compressed HDU: let astropy decode it.
- ZODI (NASA's zodiacal-light model, MJy/sr) is NOT subtracted from IMAGE (Expsupp 2.2, 3.2.11).
  EPSF is an effective PSF (pixel response included): evaluate it at pixel centres, don't integrate.
  An r = 2 px aperture holds 96.8% of a star's light: aperture correction 1.033 (core photometry).
- Units: IMAGE is in MJy/sr (BUNIT 'MJy / sr', float32). 1 MJy/sr = 889 µJy in a 6.15″ pixel.
  TELAPSE 118 s from start to stop.
- Flags (contracts/flags.md). Mask for detection and photometry: TRANSIENT, OVERFLOW, SUR_ERROR,
  NONFUNC, DICHROIC, MISSING_DATA, HOT, COLD, PHANMISS, NONLINEAR, PERSIST, STREAK, SNOWBALL.
  Record on the dot, don't mask: CROSSTALK, GHOST, GHOST_FPA, GHOST_EXT, BLOOM, HALO, OUTLIER.
  Never mask SOURCE (set on about 99% of pixels) or FULLSAMPLE.
  OVERFLOW or NONLINEAR in a source's core means saturated.
  Bit names and numbers confirmed on the first file (14 Nov, FLAGS header MSKNnnnn keys).
  SOURCE was on 99.37% of pixels (Cygnus, crowded); about 50% in the hunt zone. The mask list removed about 2.1%.
- Saturation (pilot, 50% of stars with OVERFLOW/NONLINEAR at the peak): D1 J_AB 10.5, D2 J_AB 10.8,
  D3 K_AB 11.0, D4 W1_AB 11.1, D5 W2_AB 9.6, D6 W2_AB 9.0. Units checked: our fluxes / 2MASS = 0.995 (J),
  1.037 (H), 1.022 (Ks), so 889 µJy per MJy/sr and the 1.033 aperture correction hold to 2–4%.
- pos_err_arcsec is 1.4–1.8× too small against Gaia (single-dot floor ~0.43″); scale it before use. Barnard's Star, RW Aurigae and
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
- Bucket gs://nasa-glazing-data: photolists/, refcat/, dots/, forced/, results/, cache/, logs/. What runs where and what we store: docs/cloud-architecture.md.
- Firestore (us-east4): candidates, votes. Create-only rules. Site on Firebase Hosting, API on Cloud Run.
- Machines: skyshift-test (e2-standard-8) for most work; skyshift-worker (n2-highcpu-64 spot)
  only for the hunt zone after gate G2. Both stop themselves after each job; use infra/worker/vm.sh.
- Schedule: docs/plan-one-day.md (everything built Saturday, freeze 20:00).

## Not yet verified (check on real data, then fix here and in contracts/)
Facts measured on the first file: docs/research/2026-11-14-first-photo-facts.md.
- Nothing open from the first list: the pilot (14 Nov) checked units and saturation (see Domain facts).
