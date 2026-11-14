"""spectrum tool against the offline site (conftest): static object.json for featured targets,
fixtures/api/object_barnard.json for live /api/object."""
import asyncio
import base64
import io
import json

import httpx
import pytest
from PIL import Image

from skyshift_mcp import server, tools_spectrum as ts
from skyshift_mcp.client import SkyShiftError

from conftest import PUBLIC, make_offline, network


def run(coro):
    return asyncio.run(coro)


def _png(result):
    assert len(result) == 2
    meta, img = result
    data = img.to_image_content()
    assert data.mimeType == "image/png"
    return json.loads(meta), Image.open(io.BytesIO(base64.b64decode(data.data)))


def _object(slug):
    return json.loads((PUBLIC / "data" / slug / "object.json").read_text())


def _resolve_wise(request):
    if request.url.path == "/api/resolve" and "0855" in request.url.params.get("q", ""):
        return httpx.Response(200, json={"q": "WISE 0855-0714", "name": "WISE 0855−0714", "ra": 133.786,
                                         "dec": -7.244, "slug": "wise0855", "source": "builtin"})
    return None


def test_featured_target_reads_static_and_draws(offline):
    meta, img = _png(run(ts.spectrum(target="Barnard's Star")))
    assert meta["source"] == "static /data/barnard/object.json"
    assert meta["name"] == "Barnard's Star"
    assert img.size == (ts.W, ts.H) and img.mode == "RGB"
    spec = _object("barnard")["spectrum"]
    assert meta["n_points"] == len(spec)
    assert set(meta["per_detector"]) == {f"D{d}" for d in range(1, 7)}
    assert sum(d["n"] for d in meta["per_detector"].values()) == len(spec)
    assert sum(v["n"] for v in meta["per_visit"].values()) == len(spec)
    assert meta["n_saturated"] == sum(p["saturated"] for p in spec)
    assert 0.7 < meta["um_range"][0] < meta["um_range"][1] < 5.1
    assert meta["median_snr"] is not None and meta["y_scale"] in ("linear", "log")
    assert meta["bands_um"]["ch4_33"] == [3.20, 3.45]


def test_brown_dwarf_static(monkeypatch):
    make_offline(monkeypatch, _resolve_wise)
    meta, img = _png(run(ts.spectrum(target="WISE 0855-0714")))
    assert meta["source"] == "static /data/wise0855/object.json"
    assert meta["n_points"] == len(_object("wise0855")["spectrum"])
    assert meta["y_scale"] == "linear"          # D1–D4 scatter around zero
    assert meta["per_detector"]["D5"]["median_flux_ujy"] > 10 * abs(meta["per_detector"]["D2"]["median_flux_ujy"])
    assert img.size == (ts.W, ts.H)


def test_scale_option_and_bands_off(offline):
    meta, _ = _png(run(ts.spectrum(target="Barnard's Star", scale="log", bands=False)))
    assert meta["y_scale"] == "log" and "bands_um" not in meta


def test_choose_scale():
    assert ts.choose_scale(p["flux_ujy"] for p in _object("wise0855")["spectrum"]) == "linear"
    assert ts.choose_scale(p["flux_ujy"] for p in _object("epsindib")["spectrum"]) == "log"
    assert ts.choose_scale([1.0, 2.0]) == "linear"


def test_visit_filter(offline):
    spec = _object("barnard")["spectrum"]
    meta, _ = _png(run(ts.spectrum(target="Barnard's Star", visit=2)))
    assert meta["visit"] == 2 and list(meta["per_visit"]) == ["2"]
    assert meta["n_points"] == sum(p["visit"] == 2 for p in spec)
    assert meta["visits_with_points"] == sorted({p["visit"] for p in spec})
    out = run(ts.spectrum(target="Barnard's Star", visit=9))
    assert len(out) == 1
    gone = json.loads(out[0])
    assert gone["spectrum_points"] == 0 and gone["spectrum_reason"] == "no points in visit 9"


def test_empty_spectrum_is_text_with_reason(monkeypatch):
    def empty(request):
        if request.url.path == "/api/object":
            return httpx.Response(200, json={"name": "R Draconis", "gaia": None, "known_ucd": None,
                                             "spectrum": [], "spectrum_reason": "saturated",
                                             "measure": None, "measure_reason": "few_visits"})
        return None
    make_offline(monkeypatch, empty)
    out = run(ts.spectrum(ra=245.45, dec=66.75))
    assert len(out) == 1 and isinstance(out[0], str)
    meta = json.loads(out[0])
    assert meta["spectrum_points"] == 0 and meta["spectrum_reason"] == "saturated"
    assert meta["source"] == "live /api/object" and "saturated" in meta["why"]


def test_empty_spectrum_without_reason_reads_no_data(monkeypatch):
    make_offline(monkeypatch, lambda r: httpx.Response(200, json={"name": None, "spectrum": []})
                 if r.url.path == "/api/object" else None)
    meta = json.loads(run(ts.spectrum(ra=1.0, dec=2.0))[0])
    assert meta["spectrum_reason"] == "no_data" and meta["name"].startswith("RA 1.00000")


def test_candidate_id_uses_its_position(monkeypatch, sample):
    seen = []

    def spy(request):
        if request.url.path == "/api/object":
            seen.append(dict(request.url.params))
        return None
    make_offline(monkeypatch, spy)
    c0 = sample[0]
    meta, img = _png(run(ts.spectrum(candidate_id=c0["id"])))
    assert meta["source"] == "live /api/object" and meta["candidate"]["id"] == c0["id"]
    assert float(seen[0]["ra"]) == pytest.approx(c0["ra"]) and float(seen[0]["dec"]) == pytest.approx(c0["dec"])
    assert meta["position"]["ra"] == c0["ra"] and img.size == (ts.W, ts.H)
    if c0["kind"] == "mover":
        assert "mover" in meta["note"]
    with pytest.raises(SkyShiftError, match="nope"):
        run(ts.spectrum(candidate_id="nope"))


def test_needs_a_position(offline):
    with pytest.raises(SkyShiftError):
        run(ts.spectrum())


def test_render_marks_off_scale_points():
    pts = [{"um": 1.0 + i * 0.03, "flux_ujy": 100.0 + i, "err_ujy": 5.0, "det": 2, "mjd": 61000.0,
            "visit": 1, "saturated": i % 7 == 0} for i in range(40)]
    pts.append({"um": 4.5, "flux_ujy": 1e6, "err_ujy": 5.0, "det": 6, "mjd": 61000.0, "visit": 1,
                "saturated": False})
    png, info = ts.render_png(pts, "test · SPHEREx spectrum", "a subtitle", "linear", True)
    assert info["off_scale"]["above"] == 1 and info["plot_flux_unit"] == "µJy"
    assert Image.open(io.BytesIO(png)).size == (ts.W, ts.H)


def test_protocol_returns_text_and_image(offline):
    from mcp.shared.memory import create_connected_server_and_client_session

    async def go():
        async with create_connected_server_and_client_session(server.app._mcp_server) as s:
            tools = {t.name for t in (await s.list_tools()).tools}
            res = await s.call_tool("spectrum", {"target": "Barnard's Star", "visit": 1})
            return tools, res

    tools, res = run(go())
    assert "spectrum" in tools
    assert [c.type for c in res.content] == ["text", "image"] and not res.isError


@network
def test_live_site_brown_dwarf():
    server._client = None
    meta, img = _png(run(ts.spectrum(target="WISE 0855-0714")))
    assert meta["n_points"] > 50 and img.size == (ts.W, ts.H)
