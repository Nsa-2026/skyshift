"""Offline site: /data/ from web/public/data/ (real static twins in the repo), /data/candidates.json
from fixtures/api/candidates_sample.json, /api/ from fixtures/api/."""
from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest

from skyshift_mcp import server
from skyshift_mcp.client import Client

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = ROOT / "web" / "public"
FIX = ROOT / "fixtures" / "api"
BASE = "https://skyshift.test"


def _file(path: Path) -> httpx.Response:
    if not path.is_file():
        return httpx.Response(404, json={"error": {"code": "not_found", "message": str(path.name)}})
    ctype = "application/json" if path.suffix == ".json" else "application/octet-stream"
    return httpx.Response(200, content=path.read_bytes(), headers={"content-type": ctype})


def handler(request: httpx.Request) -> httpx.Response:
    p = request.url.path
    q = dict(request.url.params)
    if p == "/data/candidates.json":
        return _file(FIX / "candidates_sample.json")
    if p.startswith("/data/"):
        return _file(PUBLIC / p.lstrip("/"))
    if p == "/api/resolve":
        name = "resolve_barnard.json" if "barnard" in q.get("q", "").lower() else "resolve_coords.json"
        return _file(FIX / name)
    if p == "/api/frames":
        return _file(FIX / "frames_barnard.json")
    if p.startswith("/api/frames/"):
        return _file(FIX / p[len("/api/"):])
    if p == "/api/object":
        return _file(FIX / "object_barnard.json")
    if p == "/api/gaia":
        return _file(FIX / "gaia_barnard.json")
    if p == "/api/known":
        if "mjd" not in q:
            return httpx.Response(400, json={"error": {"code": "bad_request", "message": "mjd is required"}})
        return _file(FIX / "known_comet.json")
    return httpx.Response(404, json={"error": {"code": "not_found", "message": p}})


@pytest.fixture
def offline(monkeypatch):
    c = Client(BASE, http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(server, "_client", c)
    return c


@pytest.fixture
def sample():
    return json.loads((FIX / "candidates_sample.json").read_text())["candidates"]


network = pytest.mark.skipif(os.environ.get("SKYSHIFT_NETWORK") != "1", reason="set SKYSHIFT_NETWORK=1")
