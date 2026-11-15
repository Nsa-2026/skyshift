# scripts/CLAUDE.md

Shared scripts. Run with uv run, except make_fixtures.py (stdlib only).
- validate/        P1. V1–V11, write web/public/data/validation.json (contracts/api.md).
- precompute/      P2. Calls build_blink() from server/ for the featured targets and writes
                   web/public/data/<slug>/ (frames.json, object.json, frame files).
- candidates/      P1. frames.py (run from server/, like precompute): static blink frames for the most
                   promising candidates, web/public/data/cand/<id>/, and their frames_url in candidates.json.
                   candidates.json itself comes from the pipeline: cd pipeline && uv run skyshift-candidates.
                   known[] is MPC only (server known.py); `--known-only` recomputes just known[] of cand/*.
                   The top of each kind, every grade A/B mover, never a grade C one. A mover's frames must
                   show its own exposures (a partial search is retried once, then skipped).
                   Tests: cd server && uv run pytest ../scripts/candidates/tests
- make_fixtures.py P1. Writes the synthetic examples in fixtures/api/: python3 -I scripts/make_fixtures.py
- demo_check.py    Opens every featured target from /data plus three random coordinates
                   through /api on the live site and prints the time to first frame. Used by /demo-check.
                   uv run scripts/demo_check.py [--base URL] [--api-base URL] [--random N] [--json PATH]
