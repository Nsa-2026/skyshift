"""Tools end to end against the offline site (conftest), and once through the MCP protocol."""
import asyncio
import base64
import io
import json

import pytest
from PIL import Image

from skyshift_mcp import server
from skyshift_mcp.client import SkyShiftError

from conftest import network


def run(coro):
    return asyncio.run(coro)


def test_top_candidates_filters_and_ranks(offline):
    out = json.loads(run(server.top_candidates(kind="mover", limit=3)))
    assert out["shown"] <= 3 and out["matching"] >= out["shown"]
    scores = [c["score"] for c in out["candidates"]]
    assert scores == sorted(scores, reverse=True)
    assert all(c["kind"] == "mover" for c in out["candidates"])


def test_candidates_near_and_get(offline, sample):
    c0 = sample[0]
    near = json.loads(run(server.candidates_near(ra=c0["ra"], dec=c0["dec"], radius_arcmin=5)))
    assert near["candidates"][0]["id"] == c0["id"]
    full = json.loads(run(server.get_candidate(c0["id"])))
    assert full["id"] == c0["id"] and full["blink_page"].endswith("#/explore")
    with pytest.raises(SkyShiftError):
        run(server.get_candidate("nope"))


def _png(result):
    meta, img = result
    data = img.to_image_content()
    assert data.mimeType == "image/png"
    return json.loads(meta), Image.open(io.BytesIO(base64.b64decode(data.data)))


def test_blink_featured_target_reads_static(offline):
    meta, img = _png(run(server.blink(target="Barnard's Star")))
    assert meta["source"].startswith("static ") and "/data/barnard/" in meta["source"]
    assert meta["clock"] == "months" and meta["det"] == 2
    assert 2 <= len(meta["epochs_shown"]) <= 4
    assert img.width > img.height


def test_blink_zoom(offline):
    meta, img = _png(run(server.blink(target="Barnard's Star", zoom_arcmin=1.0)))
    assert meta["image_field_arcmin"] < meta["field_arcmin"]


def test_blink_coordinates_go_live(offline):
    meta, _ = _png(run(server.blink(ra=10.0, dec=20.0)))
    assert meta["source"] == "live /api/frames"


def test_blink_missing_detector(offline):
    with pytest.raises(SkyShiftError, match="detector 4"):
        run(server.blink(ra=10.0, dec=20.0, det=4))


def test_blink_needs_position(offline):
    with pytest.raises(SkyShiftError):
        run(server.blink())


def test_measure_static_and_live(offline):
    s = json.loads(run(server.measure(target="Barnard's Star")))
    assert s["source"] == "static /data/barnard/object.json"
    assert s["measure"]["rate_arcsec_yr"] > 5 and "spectrum" not in s
    live = json.loads(run(server.measure(ra=1.0, dec=2.0, spectrum=True)))
    assert live["source"] == "live /api/object" and "spectrum" in live


def test_known_and_gaia(offline):
    k = json.loads(run(server.known_objects(ra=1.0, dec=2.0, mjd=60900.0)))
    assert "known" in k
    g = json.loads(run(server.gaia_stars(ra=1.0, dec=2.0, limit=3)))
    assert g["shown"] <= 3
    mags = [s["g"] for s in g["gaia"] if s["g"] is not None]
    assert mags == sorted(mags)


def test_protocol_lists_tools_and_returns_image(offline):
    from mcp.shared.memory import create_connected_server_and_client_session

    async def go():
        async with create_connected_server_and_client_session(server.app._mcp_server) as s:
            tools = {t.name for t in (await s.list_tools()).tools}
            res = await s.call_tool("blink", {"target": "Barnard's Star"})
            bad = await s.call_tool("get_candidate", {"candidate_id": "nope"})
            return tools, res, bad

    tools, res, bad = run(go())
    assert {"top_candidates", "candidates_near", "get_candidate", "blink", "measure",
            "known_objects", "gaia_stars", "resolve"} <= tools
    assert [c.type for c in res.content] == ["text", "image"]
    assert bad.isError and "nope" in bad.content[0].text


@network
def test_live_site_top_mover():
    server._client = None
    out = json.loads(run(server.top_candidates(kind="mover", limit=1)))
    assert out["candidates"] and out["candidates"][0]["kind"] == "mover"
