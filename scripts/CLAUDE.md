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
- vet/             P1. Vet one finding against the photos, catalogues and known bodies (vet.py tools), build agent
                   rounds (make_round.py), run a round through the Claude API (api_round.py, key from the environment),
                   compare runs (compare_runs.py) and check which known bodies we saw (known_check.py). Read only; local data
                   in ~/.cache/skyshift-vet. Checklist: .claude/skills/vet-finding. Run from pipeline/, see vet/README.md.
                   Tests: cd pipeline && uv run pytest ../scripts/vet/tests
- make_fixtures.py P1. Writes the synthetic examples in fixtures/api/: python3 -I scripts/make_fixtures.py
- demo_check.py    Opens every featured target from /data plus three random coordinates
                   through /api on the live site and prints the time to first frame. Used by /demo-check.
                   uv run scripts/demo_check.py [--base URL] [--api-base URL] [--random N] [--json PATH]
