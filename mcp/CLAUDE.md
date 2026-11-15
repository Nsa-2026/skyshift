# mcp/CLAUDE.md
Owner: P1. MCP server (stdio; `--http` for streamable HTTP) that exposes SkyShift to AI assistants.
- Reads only the public site (SKYSHIFT_BASE, default https://skyshift.web.app): /data/ static files
  first (candidates.json, cand/<id>/, <slug>/), then /api/. No cloud credentials, no writes, no cost.
- Shapes, names and units come from contracts/api.md; never invent fields. New API needs go to P1/P2.
- Modules: client.py (HTTP; a connection error or a 503 with Retry-After is asked once more),
  candidates.py (filter/rank, pure), frames.py
  (uint16 → one PNG/GIF: shared asinh stretch, last − first, Gaia/known/measured marks, pure),
  server.py (core tools: candidates, blink, measure, known, gaia, resolve). One module per stream,
  imported at the end of server.py: tools_reference.py (featured_targets, validation, resources,
  prompts), tools_data.py (measured_dots, batch_check, top_voted, votes_for: Firestore read-only,
  never voter ids), tools_spectrum.py (spectrum PNG; bands copied from pipeline INDEX_WINDOWS).
- data/ holds copies of web/src/data/targets.json and contracts/{api,flags}.md for an installed
  package; a checkout reads the repo files. A stale copy only warns in tests: refresh with cp.
- Pinned to the mcp v1 SDK (FastMCP); v2 renamed it MCPServer. Clients only see the protocol.
- Tests: `uv run pytest` (offline: web/public/data/ and fixtures/api/ served by an httpx mock);
  `SKYSHIFT_NETWORK=1` adds a live-site test.
