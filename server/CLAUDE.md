# server/CLAUDE.md
Owner: P2. Also owns the root Dockerfile, scripts/precompute/ and web/public/data/<slug>/.
FastAPI on Cloud Run (us-east4, 0–2 instances). Implements contracts/api.md exactly.
- One function build_blink(ra, dec, fov, clock, det), used by server/ and scripts/precompute/. It uses core/ for the visit rule, masks and measurement.
- Search: astroquery Irsa.query_sia, collections spherex_dr1 and spherex_qr3 (skip *_deep). The TAP table name is unverified.
- Detector from energy_bandpassname. Time: middle of t_min and t_max, then MJD-AVG once a file is open.
- Pixels: IRSA cutouts (?center=RA,DEC&size=DEG) only. Never whole files on demand.
- Frames: 16-bit arrays with scale and zero, at most 300 px a side. Sampling per field as in contracts/api.md: months 5′ at 3×, 15′ at 2×; hours 20′ native, 60′ binned 2×. No PNGs, no stretch, no subtract or add on the server.
- Cache in gs://nasa-glazing-data/cache/. Static files first: scripts/precompute writes web/public/data/<slug>/.
- Tests: uv run pytest. Container: Dockerfile at the repo root, because the image needs core/ and server/; deploy with --source . from the root.
- Until an endpoint is real, return its example from fixtures/api/ (marked "synthetic": true).
