# scripts/CLAUDE.md

Shared scripts. Run with uv run, except make_fixtures.py (stdlib only).
- validate/        P1. V1–V8, write web/public/data/validation.json (contracts/api.md).
- precompute/      P2. Calls build_blink() from server/ for the featured targets and writes
                   web/public/data/<slug>/ (frames.json, object.json, frame files).
- make_fixtures.py P1. Writes the synthetic examples in fixtures/api/: python3 -I scripts/make_fixtures.py
- demo_check.py    To write. Opens every featured target from /data plus three random coordinates
                   through /api on the live site and prints the time to first frame. Used by /demo-check.
