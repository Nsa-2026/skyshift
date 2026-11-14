"""SkyShift server: the blink engine (build_frames, build_object) and the FastAPI app.

Module owners while the two P2 builds run in parallel:
- engine: search, cutouts, stack, blink, obj, gaia, known  (build_frames, build_object)
- shell:  app, resolve, Dockerfile, infra/run
- shared: cache (written once, here)
"""
