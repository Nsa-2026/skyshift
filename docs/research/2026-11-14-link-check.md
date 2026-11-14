# Link check of the plan's sources (14 Nov 2026)

Facts read from the pages linked in the two plans, plus what was set up in Google Cloud tonight.
"Not found" means the pages read did not say it, not that it's false.

## Google Cloud (done and verified on 14 Nov)
- Project `nasa-glazing`, region us-east4, zone us-east4-a. Billing on, €264 credit until 17 Jan 2027.
- Services enabled: compute, run, artifactregistry, cloudbuild, firestore, plus Firebase APIs.
- Bucket `gs://nasa-glazing-data` (US-EAST4, uniform access). Firestore native, us-east4, free tier.
- Firebase added; Hosting site `nasa-glazing` → https://nasa-glazing.web.app (nothing deployed yet).
- Quotas in us-east4: N2 CPUs 200, CPUs 200, Preemptible CPUs 96, E2 24, N2D 16 (increase denied: new project, wait 48 h). CPUs (all regions) 128.
- Worker machine type is therefore **n2-highcpu-64** (spot), not n2d-highcpu-64. A 64-core spot test machine started and was deleted successfully.
- Teammates have IAM access (P2 Editor, P3/P4 Viewer). GitHub repo `skyshift` is created tomorrow.

## SPHEREx data (IRSA pages)
Sources: https://irsa.ipac.caltech.edu/Missions/spherex.html · https://irsa.ipac.caltech.edu/cloud_access/ ·
https://irsa.ipac.caltech.edu/spherex-archive-documentation/spherex-data-access/ ·
https://caltech-ipac.github.io/irsa-tutorials/spherex-intro/ · https://caltech-ipac.github.io/irsa-tutorials/spherex-cutouts/

- Six bands, confirmed: 0.75–1.1, 1.1–1.6, 1.6–2.4, 2.4–3.8, 3.8–4.4, 4.4–5.0 µm (R = 39, 41, 41, 35, 112, 128). "17 channels each = 102" not stated on these pages.
- Detector labelling is inconsistent in NASA's own example (header DETECTOR=1 while text calls it detector 2). Map bands from `energy_bandpassname` (e.g. "SPHEREx-D2") or the header, not filenames. Verify on the first real file.
- Images 2040 × 2040 confirmed. Pixel scale not stated; the example WCS implies about 6″ per pixel.
- Level 2 file = one multi-extension FITS: IMAGE, FLAGS, VARIANCE, ZODI, EPSF, WCS-WAVE. File size not stated.
- Collections: spherex_dr1, spherex_qr3 (wide survey); spherex_dr1_deep, spherex_qr3_deep (skip); spherex_dr1_cube (all-sky cube).
  Releases: QR3 products September 2026; DR1 products November 2026. QR2 downloadable until Feb 2027 but not searchable via SIA.
  Ingestion is weekly; SIA lags about a day.
- Amazon bucket `nasa-irsa-spherex`, us-east-1, anonymous (`--no-sign-request` / `anonymous=True`), "without credentials or egress charges".
  Prefixes: dr1, qr3, qr2. Layout: `<prefix>/level2/<planning period>/l2b-v<version>-<processing date>/<detector>/<file>.fits`
  Example: `dr1/level2/2025W18_1B/l2b-v27-2026-196/1/level2_2025W18_1B_0074_2D1_spx_l2b-v27-2026-196.fits`
  All-sky cube as HiPS: `hips5_cube/hips_20261026/Norder5/...` (whether Aladin Lite can load it directly is unverified).
- Cutouts: append `?center=RA,DEC&size=DEG` (degrees) to the IRSA image URL (https://irsa.ipac.caltech.edu/ibe/data/spherex/...). Works on IRSA's servers only, not S3.
  Find images with astroquery `Irsa.query_sia` or `Irsa.query_tap` on `spherex.artifact` JOIN `spherex.plane` (filter `energy_bandpassname = 'SPHEREx-D2'`).
  Tutorial: 84 images for one position at 0.1° in about 13 s with 10 threads; use 7–12 workers; retry transient failures; `astropy.utils.data.conf.remote_timeout = 120`.
- Time: `MJD-AVG` not found. Header has `DATE`; TAP has `time_bounds_lower`. Check the real time keyword on the first file before fixing the pass rule.
- FLAGS bits: 0 TRANSIENT, 1 OVERFLOW, 2 SUR_ERROR, 6 NONFUNC, 7 DICHROIC, 9 MISSING_DATA, 10 HOT, 11 COLD, 12 FULLSAMPLE, 14 PHANMISS, 15 NONLINEAR, 17 PERSIST, 19 OUTLIER, 20 CROSSTALK, 21 SOURCE, 22 GHOST, 23 GHOST_FPA, 24 GHOST_EXT, 25 STREAK, 26 BLOOM, 27 SNOWBALL, 28 HALO.
  **SOURCE (bit 21) is set on ~99% of pixels** in NASA's example (4,135,535 of 4,161,600). Never mask SOURCE; mask an explicit list of bad bits only.
- Not a "first catalog": IRSA lists SPLICES (SPHEREx target list of ice sources) and an on-demand Source Discovery Tool. Neither is a general dot list or moving-object search.
- MOST (Moving Object Search Tool): https://irsa.ipac.caltech.edu/applications/MOST — finds SPHEREx images covering a known solar-system object's predicted positions. Useful to validate "we re-found N known asteroids".
- Comet 3I/ATLAS SPHEREx page: https://irsa.ipac.caltech.edu/data/SPHEREx/docs/comet3iatlas/index.html (not yet read).
- Required acknowledgement and citations (Bock et al. 2026 mission paper; Akeson et al. 2026 pipeline paper) are on the mission page.
- Tools: astroquery.ipac.irsa (recommended), pyvo, firefly_client. Explanatory Supplement: https://irsa.ipac.caltech.edu/data/SPHEREx/docs/SPHEREx_Expsupp.pdf

## Other links
- Aladin Lite docs link https://aladin.cds.unistra.fr/AladinLite/doc returned 404. Needs a working source.
- WiseView http://byw.tools/wiseview refused the connection (site may be down).
- Backyard Worlds http://backyardworlds.org/ redirects to https://www.zooniverse.org/projects/marckuchner/backyard-worlds-planet-9 (not read).
- Gaia: the STScI/MAST page points to ESA's Gaia Archive (https://gea.esac.esa.int/archive/) for DR3; use ESA's TAP or astroquery.gaia. Gaia observations ended in 2024/2025; no DR4 date on that page.

## Claude Code (code.claude.com docs)
- Install `curl -fsSL https://claude.ai/install.sh | bash` is correct (macOS/Linux/WSL). Windows: `irm https://claude.ai/install.ps1 | iex`.
- Needs Pro, Max, Team, Enterprise or Console; the free plan has no Claude Code.
- Login is per machine; over SSH press `c` to copy the login URL and paste the code back. `claude auth status` checks it.
- `claude -w <name>` starts in a worktree at `<repo>/.claude/worktrees/<name>` (add `.claude/worktrees/` to .gitignore).
- `claude doctor` validates settings files after editing the shared `.claude/settings.json`.
