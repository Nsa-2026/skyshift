# pipeline/CLAUDE.md
Owner: P1. Imports core/ for the visit rule, masks and measurement. Design: docs/cloud-architecture.md.
Pilot and hunt zone. Reads IMAGE, FLAGS, VARIANCE, WCS-WAVE from s3://nasa-irsa-spherex over HTTPS (anonymous).

## Run
`uv run skyshift-pipeline <step> --zone pilot|hunt [--root gs://nasa-glazing-data | <dir>] [--workers N] [--limit N]`
Steps in order: photolist, dots, known, link, score, suspects; `all` runs them in order.
- Every step skips what exists (dots/<obs_id>D<det>.parquet, cache/skybot/<obs_id>D<det>.json, the photo list).
  link, score and suspects are cheap and always recompute from what is there.
- `known` doesn't need `dots`: run it at the same time in a second process (SkyBoT is the slow part).
- Thresholds (sessions for fixed, coverage, match radii, elongation, Gaia radius) live in src/skyshift_pipeline/config.py.

## Layout under --root
photolists/zones/<zone>.parquet (+ _tilemap, _tiles) · dots/<obs_id>D<det>.parquet ·
cache/skybot/<obs_id>D<det>.json · results/<zone>/{fixed,movers,suspects}.parquet, recall.json,
recall_units.parquet, suspects.json, summary.json, dots_runs/<time>.json

## Facts
- S3 key: TAP spherex.obscore has no usable cloud_access; spherex.artifact.uri = 'ibe/data/spherex/' + S3 key.
- Tiles: one 0.25° grid anchored at RA 120.5, Dec 14.4 for every zone, so pilot tiles are hunt tiles.
- SkyBoT: ssp.imcce.fr, geocentre (500), -from=SkyShift, 4 in flight, one request per simultaneous detector pair.
- Tests: `uv run pytest` (offline); `SKYSHIFT_NETWORK=1 uv run pytest` adds the live SkyBoT test.
