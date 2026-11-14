"""Reference for the model: featured targets, validation results (V1–V11), MCP resources (contracts,
flags, domain facts) and MCP prompts (e.g. explain_candidate).

Register with `@app.tool()`, `@app.resource()`, `@app.prompt()` on the shared app."""
from __future__ import annotations

from .server import app, client  # noqa: F401
