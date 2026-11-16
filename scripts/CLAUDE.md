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
                   catalogue_check.py (check producer `catalogue`, run from pipeline/): IRSA catalogues at every
                   candidate → ~/skyshift-data/checks/catalogue.json; answers cached per position (--cache).
                   Tests: cd server && uv run pytest ../scripts/candidates/tests
                   pixel_check.py (P1, 16 Nov): check producer for movers (contracts v3.23 `checks`), measured in
                   NASA's photos by small S3 range reads (s3pixels.py): partner detector, faint on the path, hot
                   pixel, persistence, sharp, wide, one-photo. Writes ~/skyshift-data/checks/pixel.json; --validate
                   gives the hit rates on known asteroids, junk and faint stars (pixel_validate.py, into
                   ~/skyshift-data/cache/pixel/validation.json). Cache: ~/skyshift-data/cache/pixel. Run from server/.
- vet/             P1. Vet one finding against the photos, catalogues and known bodies (vet.py tools), build agent
                   rounds (make_round.py) and check which known bodies we saw (known_check.py). Read only; local data
                   in ~/.cache/skyshift-vet. Checklist: .claude/skills/vet-finding. Run from pipeline/, see vet/README.md.
                   Tests: cd pipeline && uv run pytest ../scripts/vet/tests
- make_fixtures.py P1. Writes the synthetic examples in fixtures/api/: python3 -I scripts/make_fixtures.py
- demo_check.py    Opens every featured target from /data plus three random coordinates
                   through /api on the live site and prints the time to first frame. Used by /demo-check.
                   uv run scripts/demo_check.py [--base URL] [--api-base URL] [--random N] [--json PATH]
