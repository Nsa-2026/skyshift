"""Tools end to end against the offline site (conftest), and once through the MCP protocol."""
import asyncio
import base64
import io
import json

import httpx
import pytest
from PIL import Image

from skyshift_mcp import client as client_mod
from skyshift_mcp import server
from skyshift_mcp.client import SkyShiftError

from conftest import FIX, make_offline, network


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


def test_blink_clock_without_fov_uses_default_field(offline):
    meta, _ = _png(run(server.blink(target="Barnard's Star", clock="months")))
    assert meta["source"].endswith("/data/barnard/frames_months_5.json")
    meta, _ = _png(run(server.blink(target="Barnard's Star", clock="hours")))
    assert meta["source"].endswith("/data/barnard/frames_hours_20.json")


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


def test_top_candidates_says_why_grade_finds_nothing_and_how_rate_sorts(offline):
    out = json.loads(run(server.top_candidates(kind="mover", grade="A")))
    assert out["matching"] == 0 and out["notes"][0].startswith("this file has no grades")
    out = json.loads(run(server.top_candidates(sort="rate", limit=5)))
    assert any("mas/yr" in n for n in out["notes"])
    rates = [c["rate_arcsec_h"] for c in out["candidates"]]
    assert all(r is not None for r in rates) and rates == sorted(rates, reverse=True)
    assert "notes" not in json.loads(run(server.top_candidates(limit=1)))


def _names_unknown_to_api(request):
    """/api/resolve as the live site: a 404 for names it doesn't know (the featured asteroids)."""
    q = request.url.params.get("q", "").lower()
    if request.url.path == "/api/resolve" and any(n in q for n in ("elpis", "euphrosyne", "wise", "thing")):
        return httpx.Response(404, json={"error": {"code": "not_found", "message": f"unknown name {q!r}"}})
    return None


def test_resolve_falls_back_to_featured_names(monkeypatch):
    make_offline(monkeypatch, _names_unknown_to_api)
    for q in ("Elpis", "(59) Elpis", "59 elpis", "ELPIS", "elpis"):
        r = json.loads(run(server.resolve(q)))
        assert r["slug"] == "elpis" and r["name"] == "(59) Elpis" and r["source"] == "builtin", q
    assert json.loads(run(server.resolve("euphrosyne")))["slug"] == "euphrosyne"
    with pytest.raises(SkyShiftError, match="not_found"):
        run(server.resolve("not a thing"))
    meta, _ = _png(run(server.blink(target="Elpis")))
    assert "/data/elpis/" in meta["source"] and meta["clock"] == "hours"


def test_blink_featured_target_uses_its_own_detector(monkeypatch):
    make_offline(monkeypatch, _names_unknown_to_api)
    meta, _ = _png(run(server.blink(target="WISE 0855-0714")))
    assert meta["det"] == 6 and meta["source"].endswith("/data/wise0855/frames_months_5.json")
    # the hours set has D2 and D5 only: the nearest to D6
    meta, _ = _png(run(server.blink(target="WISE 0855-0714", clock="hours")))
    assert meta["det"] == 5
    meta, _ = _png(run(server.blink(target="WISE 0855-0714", det=2)))
    assert meta["det"] == 2


def _hours_frames(mjds):
    f = json.loads((FIX / "frames_barnard.json").read_text())
    base = f["epochs"]
    f["clock"] = "hours"
    f["epochs"] = [{**base[i % len(base)], "id": f"x{i}", "kind": "exposure", "mjd_mid": m,
                    "mjd_start": m - 0.0007, "mjd_end": m + 0.0007} for i, m in enumerate(mjds)]
    return f


def _live_mover(monkeypatch, sample, offsets_days):
    c = next(x for x in sample if x["kind"] == "mover" and not x["frames_url"])
    frames = _hours_frames([c["mjd"] + d for d in offsets_days])
    make_offline(monkeypatch, lambda r: httpx.Response(200, json=frames) if r.url.path == "/api/frames" else None)
    meta, _ = _png(run(server.blink(candidate_id=c["id"], det=2)))
    assert meta["source"] == "live /api/frames"
    return meta


def test_blink_live_mover_says_the_find_is_not_in_the_frames(monkeypatch, sample):
    meta = _live_mover(monkeypatch, sample, [0.4, 0.401, 0.402, 3.0])
    s = meta["find_session"]
    assert "NOT in these frames" in s["warning"] and "9.6 hours after" in s["warning"]
    assert s["nearest_frame_minutes"] == pytest.approx(576.0, abs=0.1)


def test_blink_live_mover_warns_and_shows_the_frames_nearest_the_find(monkeypatch, sample):
    m = 1.0 / 1440.0
    meta = _live_mover(monkeypatch, sample, [-2.0, -1.07 * m, 1.07 * m, 3.2 * m, 0.5, 1.0])
    s = meta["find_session"]
    assert "no time parameter" in s["warning"] and "1.1 min" in s["warning"]
    assert [e["id"] for e in meta["epochs_shown"]] == ["x1", "x2", "x3", "x4"]


def test_blink_static_mover_reports_the_nearest_frame(offline, sample):
    c = next(x for x in sample if x["kind"] == "mover" and x["frames_url"])
    meta, _ = _png(run(server.blink(candidate_id=c["id"])))
    assert meta["source"].startswith("static ") and "/data/cand/" in meta["source"]
    assert abs(meta["find_session"]["nearest_frame_minutes"]) < 1.5 and "warning" not in meta["find_session"]


def test_connection_errors_are_asked_once_more(monkeypatch):
    monkeypatch.setattr(client_mod, "RETRY_S", 0.0)
    calls = []

    def flaky(request, fail=1):
        if request.url.path == "/data/candidates.json":
            calls.append(1)
            if len(calls) <= fail:
                raise httpx.ConnectError("connection refused", request=request)
        return None

    make_offline(monkeypatch, flaky)
    assert json.loads(run(server.top_candidates(limit=1)))["shown"] == 1 and len(calls) == 2
    calls.clear()
    make_offline(monkeypatch, lambda r: flaky(r, fail=2))
    with pytest.raises(SkyShiftError, match="tried twice"):
        run(server.top_candidates(limit=1))
    assert len(calls) == 2


@network
def test_live_site_top_mover():
    server._client = None
    out = json.loads(run(server.top_candidates(kind="mover", limit=1)))
    assert out["candidates"] and out["candidates"][0]["kind"] == "mover"
