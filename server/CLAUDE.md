# server/CLAUDE.md
Owner: P2. Also owns the root Dockerfile, scripts/precompute/ and web/public/data/<slug>/.
FastAPI on Cloud Run (us-east4, 0–4 instances). Implements contracts/api.md exactly.
- The engine is blink.build_frames(ra, dec, fov, clock, det) and blink.build_object(ra, dec), used by app.py and scripts/precompute/. Modules: search, cutouts, stack, blink, coverage, obj, gaia, known. It uses core/ for the visit rule, masks and measurement.
- Coverage: each search image keeps its footprint (s_region). coverage.py gives the share of the field it covers before any download, and the selection prefers full frames (months: >= 90% first; hours: the visit with the most exposures covering >= 50%, sessions covering >= 90% first, exposures under 50% dropped when their session has better). No footprint (a search saved before 14 Nov evening): time alone, as before.
- Hours at a time (contracts v3.19): /api/frames?clock=hours&mjd= → blink.pick_hours_at: the visit of the exposure nearest mjd, its session whole (any coverage) and asked for first, the rest by pick_hours_live; at most 6, or the session + 2 when it has 5. mjd is in the cache key only when sent. The review pages send a mover's mjd.
- Hours pages (contracts v3.23): `page=k` (k ≥ 1, clock=hours, raw view, live only) → blink.hours_pages: page 0 is the choice above (its cache key unchanged); each later page holds up to 6 more exposures of the same visit and detector, whole sessions not shown yet (full-field first, spread over the visit), then the rest of partly shown sessions; past the last page 404. Every live hours answer says `more` and `visit_exposures`; an old cached page 0 gains them from its saved search (blink.page_info, no IRSA wait). The Viewer joins pages behind page 0 (web/src/blink/pages.ts).
- Search: astroquery Irsa.query_sia, collections spherex_dr1 and spherex_qr3 (skip *_deep). The TAP table name is unverified.
  When SIA fails or takes over 20 s (502 Proxy Error from 14 Nov 16:05 UTC), search_fallback.py answers from a saved
  search at this spot, else the pipeline's photolists/zones/<zone>.parquet of every zone whose box holds it, else a saved
  search within 6′. Those answers are partial (no-store, header X-SkyShift-Partial). Outside: 502 "IRSA ... not answering".
- Detector from energy_bandpassname. Time: middle of t_min and t_max, then MJD-AVG once a file is open.
- Pixels: IRSA cutouts (?center=RA,DEC&size=DEG), streamed and cut after VARIANCE. When IRSA answers 503 "server busy" (most of 14 Nov), the same region is read from the S3 copy with range requests (SKYSHIFT_PIXELS=auto|irsa|s3). Never whole files on demand.
- Frames: 16-bit arrays with scale and zero, at most 300 px a side. Sampling per field as in contracts/api.md: months 5′ at 3×, 15′ at 2×; hours 20′ native, 60′ binned 2×. No PNGs, no stretch, no subtract or add on the server.
- Known objects: MPC orbits only (no SkyBoT). The measured layer also calls a mover near a planet `known` ("Saturn" or
  "a moon of Saturn", skyshift_core.planets, contracts v3.18), even when an old suspects table lists it. known.py reads the index cache/mpc/known_index/known_index_<date>.npz (default key in known.py INDEX_KEY, now 2026-11-16: all 2,215 comets; SKYSHIFT_KNOWN_INDEX overrides it for one run, deploy.sh sets none) once per instance and calls skyshift_core.known_index.known_near (kete); one call per frame centre. The image bakes kete's kernels (KETE_CACHE_DIR). New index (scripts/rebuild/rebuild_known_index.py): change the default key in known.py, upload the .npz before the deploy, and `precompute.py --known-only` refreshes the static known[].
- Months shifts (contracts v3.21): measured.mark_steady checks each shift against its own visits (the reported pm as a straight line, a zero point per detector, Gaia parallax out, per-visit errors × POS_ERR_SCALE 1.6): `steady` when χ²_ν ≤ 3 or the motion stays ≥ 5σ with errors × √χ²_ν; else a wobble (`wobble_arcsec`), no arrow. `precompute.py --steady-only` adds the fields to the static months files from their own detections, no bucket.
- Cache in gs://nasa-glazing-data/cache/. Static files first: scripts/precompute writes web/public/data/<slug>/.
- Live builds answer inside Firebase Hosting's 60 s: a 40 s Budget (budget.py), most useful cutouts first, partial answers served with no-store and not cached, 503 "upstream" past 48 s, identical requests share one build. Cloud Run reads S3 first (SKYSHIFT_PIXELS=s3first) with one range request per HDU (cutouts._region_fast). One "timing ..." log line per build.
- Tests: uv run pytest (offline: every upstream call is replaced; cutouts come from fixtures/fits/). Container: Dockerfile at the repo root, because the image needs core/ and server/; deploy with --source . from the root.
- Optional API keys (apikeys.py, contracts/api.md v3.14): POST /api/keys, GET/DELETE /api/keys/me. ApiKeyMiddleware checks
  X-API-Key (or Bearer) on /api/ except /api/health: no key = anonymous, unchanged; unknown key 401; per-key limit
  SKYSHIFT_KEY_LIMIT/h (default 1000), anonymous limit only if SKYSHIFT_ANON_LIMIT is set. Counts are per instance.
  Store: gs://nasa-glazing-data/apikeys/<sha256>.json (SKYSHIFT_KEYS); a key-store outage lets key holders through.
- A missing engine answers 404. Fixture examples ("synthetic": true) only with SKYSHIFT_ALLOW_SYNTHETIC=1 (tests).
- Science workspace (supplemental, not the scored pipeline): /api/analysis, /api/science, /api/science/capabilities,
  /api/enrichment, /api/export and /api/frames?view=model|resid|refdiff read the five featured fields' products in
  web/public/science-data/ (published_science.py; copied into the image). Background jobs (POST /api/jobs,
  POST /api/reference off a published centre) only with SKYSHIFT_ENABLE_SCIENCE_JOBS=1 on a local single-process
  server (scripts/run-local.sh); Cloud Run leaves it unset: 503 / 404. A failed catalogue (IRSA, ESA Gaia) is a 502
  that names it, never cached. Enrichment `asteroids` uses known.py (MPC orbits with kete) like /api/known.
