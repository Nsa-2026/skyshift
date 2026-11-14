# pipeline/CLAUDE.md
Owner: P1. Imports core/ for the visit rule, masks and measurement.
Pilot and hunt zone. Reads IMAGE, FLAGS, WCS-WAVE from s3://nasa-irsa-spherex (anonymous).
- One Parquet file per photo in gs://nasa-glazing-data/dots/ (contracts/dots.md). Its existence marks the photo done; skip it on restart. No job list, no locks.
- SkyBoT: ssp.imcce.fr, geocentre (500), -from=SkyShift, cached, a few requests at a time.
- Read only the parts needed: IMAGE + FLAGS + WCS-WAVE are 22.9 MB of a 60.2 MB file; the parts are contiguous, so range reads skip ZODI and EPSF.
- Derived tables fixed, movers and suspects: contracts/dots.md. Suspects are exported to web/public/data/suspects.json.
- The pilot (12 deg², 990 photos = 352 exposures × detectors) runs on skyshift-test; the hunt zone runs on skyshift-worker only after gate G2.
- Tests: uv run pytest.
