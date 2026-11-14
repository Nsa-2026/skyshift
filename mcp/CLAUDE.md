# mcp/CLAUDE.md
Owner: P1. MCP server (stdio; `--http` for streamable HTTP) that exposes SkyShift to AI assistants.
- Reads only the public site (SKYSHIFT_BASE, default https://skyshift.web.app): /data/ static files
  first (candidates.json, cand/<id>/, <slug>/), then /api/. No cloud credentials, no writes, no cost.
- Shapes, names and units come from contracts/api.md; never invent fields. New API needs go to P1/P2.
- Modules: client.py (HTTP, 503 Retry-After once), candidates.py (filter/rank, pure), frames.py
  (uint16 → one PNG: shared asinh stretch + last − first, pure), server.py (FastMCP tools).
- Pinned to the mcp v1 SDK (FastMCP); v2 renamed it MCPServer. Clients only see the protocol.
- Tests: `uv run pytest` (offline: web/public/data/ and fixtures/api/ served by an httpx mock);
  `SKYSHIFT_NETWORK=1` adds a live-site test.
