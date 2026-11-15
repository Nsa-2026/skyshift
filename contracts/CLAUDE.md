# contracts/CLAUDE.md

Owner: P1. Source of truth for every interface between areas (api.md v3.18, 15 Nov 2026).
- api.md    Endpoints, static twins under web/public/data/, frame data layout, the visit rule, Firestore.
- flags.md  What we do with each FLAGS bit: mask, record, never mask. Bit meanings confirmed 14 Nov.
- dots.md   The dot table and the derived tables (fixed, movers, suspects).

- Contract changes are their own PR and wait for P1. Never edit contracts/ in a feature PR.
- If a contract looks wrong, write a short proposal for P1 instead of coding around it.
- When a contract changes, update fixtures/api/ in the same PR (scripts/make_fixtures.py).
- Lines marked "proposal" are open: P1 confirms or changes them, then removes the mark.
