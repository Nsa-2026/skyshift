---
name: demo-check
description: Run scripts/demo_check.py against the deployed site to check every featured target and three random coordinates load.
---
Run `uv run scripts/demo_check.py` against the deployed site: the public Hosting site
(e.g. https://skyshift-sky.web.app once P2 creates it; use the URL I give instead if there is one).
It opens every featured target from /data (Barnard's Star, Epsilon Indi B, WISE 0855−0714,
RW Aurigae, R Draconis, comet 3I/ATLAS and the two asteroids) plus three random coordinates
through /api, checks that frames, cards and spectra load, and prints the time to first frame.

Report a table: target, source (/data or /api), frames OK, card OK, spectrum OK, time to first frame.
Flag any static target over 2 s and any live one over 15 s. For each failure, show the URL,
HTTP status and the first lines of the error. Do not fix anything; this is a check.
P1 runs it on main at every milestone.
