# server/CLAUDE.md
Owner: P2. Also owns the root Dockerfile, scripts/precompute/ and web/public/data/<slug>/.
FastAPI on Cloud Run (us-east4, 0–4 instances). Implements contracts/api.md exactly.
- The engine is blink.build_frames(ra, dec, fov, clock, det) and blink.build_object(ra, dec), used by app.py and scripts/precompute/. Modules: search, cutouts, stack, blink, obj, gaia, known. It uses core/ for the visit rule, masks and measurement.
- Search: astroquery Irsa.query_sia, collections spherex_dr1 and spherex_qr3 (skip *_deep). The TAP table name is unverified.
  When SIA fails or takes over 20 s (502 Proxy Error from 14 Nov 16:05 UTC), search_fallback.py answers from a saved
  search at this spot, else the pipeline's photolists/zones/<zone>.parquet of every zone whose box holds it, else a saved
  search within 6′. Those answers are partial (no-store, header X-SkyShift-Partial). Outside: 502 "IRSA ... not answering".
- Detector from energy_bandpassname. Time: middle of t_min and t_max, then MJD-AVG once a file is open.
- Pixels: IRSA cutouts (?center=RA,DEC&size=DEG), streamed and cut after VARIANCE. When IRSA answers 503 "server busy" (most of 14 Nov), the same region is read from the S3 copy with range requests (SKYSHIFT_PIXELS=auto|irsa|s3). Never whole files on demand.
- Frames: 16-bit arrays with scale and zero, at most 300 px a side. Sampling per field as in contracts/api.md: months 5′ at 3×, 15′ at 2×; hours 20′ native, 60′ binned 2×. No PNGs, no stretch, no subtract or add on the server.
- Known objects: MPC orbits only (no SkyBoT). known.py reads the index cache/mpc/known_index/known_index_<date>.npz once per instance (SKYSHIFT_KNOWN_INDEX overrides the key) and calls skyshift_core.known_index.known_near (kete); one call per frame centre. The image bakes kete's kernels (KETE_CACHE_DIR). New index: `precompute.py --known-only` refreshes the static known[].
- Cache in gs://nasa-glazing-data/cache/. Static files first: scripts/precompute writes web/public/data/<slug>/.
- Live builds answer inside Firebase Hosting's 60 s: a 40 s Budget (budget.py), most useful cutouts first, partial answers served with no-store and not cached, 503 "upstream" past 48 s, identical requests share one build. Cloud Run reads S3 first (SKYSHIFT_PIXELS=s3first) with one range request per HDU (cutouts._region_fast). One "timing ..." log line per build.
- Tests: uv run pytest (offline: every upstream call is replaced; cutouts come from fixtures/fits/). Container: Dockerfile at the repo root, because the image needs core/ and server/; deploy with --source . from the root.
- A missing engine answers 404. Fixture examples ("synthetic": true) only with SKYSHIFT_ALLOW_SYNTHETIC=1 (tests).
- Science workspace (supplemental, not the scored pipeline): /api/analysis, /api/science, /api/science/capabilities,
  /api/enrichment, /api/export and /api/frames?view=model|resid|refdiff read the five featured fields' products in
  web/public/science-data/ (published_science.py; copied into the image). Background jobs (POST /api/jobs,
  POST /api/reference off a published centre) only with SKYSHIFT_ENABLE_SCIENCE_JOBS=1 on a local single-process
  server (scripts/run-local.sh); Cloud Run leaves it unset: 503 / 404. A failed catalogue (IRSA, ESA Gaia) is a 502
  that names it, never cached. Enrichment `asteroids` uses known.py (MPC orbits with kete) like /api/known.
