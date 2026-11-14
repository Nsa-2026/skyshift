---
name: demo-check
description: Run scripts/demo_check.py against the deployed site to check every featured target and three random coordinates load.
---
Run `uv run scripts/demo_check.py` from the repo root against the deployed site: the public Hosting
site https://skyshift.web.app (the default). If I give another URL, pass it as `--base URL`; if /api
lives on a different host than /data (local testing), add `--api-base URL`.
It opens every featured target from /data (Barnard's Star, Epsilon Indi B, WISE 0855−0714,
RW Aurigae, R Draconis, comet 3I/ATLAS and the two asteroids: frames.json, object.json and every
frame file) plus three random coordinates through /api (/api/frames on the months and hours clocks,
then /api/object), checks that frames, cards and spectra load and match contracts/api.md, and
prints the time to first frame.

Useful flags: `--random N` (default 3, 0 skips /api), `--seed S` (repeat the same coordinates),
`--clock months|hours|both`, `--all-sets` (also every frame set in /data/<slug>/index.json),
`--json PATH` (machine-readable summary; `median_first_frame_s` feeds V8, which uses `--random 10`).
`--help` lists the rest. Exit status 1 means something failed.

Report a table: target, source (/data or /api), frames OK, card OK, spectrum OK, time to first frame.
Flag any static target over 2 s and any live one over 15 s (the script marks them SLOW). For each
failure, show the URL, HTTP status and the first lines of the error (the script prints them under
"Failures"). Do not fix anything; this is a check.
P1 runs it on main at every milestone.
