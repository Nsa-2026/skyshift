"""More of SkyShift's data: the measured layer (detections, tracks, shifts), batch checks of many
positions, and community votes (Firestore, read-only).

Register with `@app.tool()`, `@app.resource()`, `@app.prompt()` on the shared app."""
from __future__ import annotations

from .server import app, client  # noqa: F401
