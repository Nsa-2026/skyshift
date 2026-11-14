"""Overlays on blink's frames: projection, proper motion, crop, drawing, legend, GIF; then blink
end to end against the offline site (conftest)."""
import asyncio
import base64
import io
import json
import math

import httpx
import numpy as np
import pytest
from PIL import Image

from skyshift_mcp import frames as fr
from skyshift_mcp import server

from conftest import PUBLIC, make_offline

BARNARD = PUBLIC / "data" / "barnard" / "frames_months_5.json"
CAND_ID = "hunt-648380072434-87805628"
CAND = PUBLIC / "data" / "cand" / CAND_ID / "frames.json"


def run(coro):
    return asyncio.run(coro)


def _flat(n: int) -> np.ma.MaskedArray:
    rng = np.random.default_rng(3)
    return np.ma.masked_array(rng.normal(1.0, 0.01, (n, n)), mask=np.zeros((n, n), bool))


def _rgb(png: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(png)).convert("RGB"))


# ---------------------------------------------------------------- geometry

def test_project_north_up_east_left():
    ra0, dec0, p, size = 269.4462, 4.76883, 2.05, [146, 146]
    cx = cy = 72.5
    x, y = fr.project(ra0, dec0, ra0, dec0, p, size)
    assert (float(x), float(y)) == pytest.approx((cx, cy))
    # 60″ north at 2.05″/px: 29.3 px above the centre (smaller y), same column
    x, y = fr.project(ra0, dec0 + 60 / 3600, ra0, dec0, p, size)
    assert float(cy - y) == pytest.approx(60 / 2.05, abs=0.02) and float(x) == pytest.approx(cx, abs=1e-6)
    # 30″ east (RA grows): LEFT of the centre
    x, y = fr.project(ra0 + 30 / 3600 / math.cos(math.radians(dec0)), dec0, ra0, dec0, p, size)
    assert float(cx - x) == pytest.approx(30 / 2.05, abs=0.02) and float(y) == pytest.approx(cy, abs=0.01)
    # the far side of the sky does not project
    x, y = fr.project(ra0 + 180, -dec0, ra0, dec0, p, size)
    assert np.isnan(x) and np.isnan(y)


def test_propagate_barnard_ten_years():
    ra, dec = 269.44850252543836, 4.739420051112412
    ra2, dec2 = fr.propagate(ra, dec, -801.551, 10362.394, fr.GAIA_EPOCH_MJD + 3652.5)
    assert (dec2 - dec) * 3600 == pytest.approx(103.62, abs=0.01)  # ≈ 104″ north in 10 yr
    assert (ra2 - ra) * 3600 * math.cos(math.radians(dec)) == pytest.approx(-8.016, abs=0.01)  # west
    same = fr.propagate(ra, dec, np.nan, None, 61000.0)  # no motion known: stays
    assert float(same[0]) == pytest.approx(ra) and float(same[1]) == pytest.approx(dec)


def test_barnard_circle_follows_the_star_and_respects_crop():
    data = json.loads(BARNARD.read_text())
    eps = sorted((e for e in data["epochs"] if e["det"] == 2), key=lambda e: e["mjd_mid"])
    first, last = eps[0], eps[-1]
    shape = (data["size"][1], data["size"][0])
    m1, m3 = fr.epoch_marks(data, first, shape), fr.epoch_marks(data, last, shape)
    (x1, y1, g1), (x3, y3, _) = m1.gaia[0], m3.gaia[0]  # brightest first: Barnard's Star, G 8.2
    assert g1 == pytest.approx(8.194)
    # the frames are centred on the star: its circle stays within a few pixels of the centre
    for x, y in ((x1, y1), (x3, y3)):
        assert abs(x - 72.5) < 5 and abs(y - 72.5) < 5
    # and moves north (up) by 10.36″/yr × the time between the visits
    dt_yr = (last["mjd_mid"] - first["mjd_mid"]) / 365.25
    assert (y1 - y3) * data["pixel_arcsec"] == pytest.approx(10.362 * dt_yr, rel=0.01)
    assert abs(x3 - x1) * data["pixel_arcsec"] == pytest.approx(0.802 * dt_yr, abs=0.05)
    # cropped: the same marks shifted by the crop origin, only those inside the crop
    zoom = 1.2
    origin = fr.crop_origin(shape, data["pixel_arcsec"], zoom)
    crop_shape = fr.crop(np.ma.zeros(shape), data["pixel_arcsec"], zoom).shape
    mc = fr.epoch_marks(data, last, crop_shape, origin)
    assert mc.gaia[0][:2] == pytest.approx((x3 - origin[0], y3 - origin[1]))
    assert 0 < len(mc.gaia) < len(m3.gaia)
    assert all(-1 <= x <= crop_shape[1] and -1 <= y <= crop_shape[0] for x, y, _ in mc.gaia)


def _field(**extra):
    """A 41×41 field at 2″/px centred on (100, 0), one epoch."""
    epoch = {"id": "e1", "kind": "exposure", "mjd_mid": fr.GAIA_EPOCH_MJD, "det": 2}
    return {"center": {"ra": 100.0, "dec": 0.0}, "pixel_arcsec": 2.0, "size": [41, 41],
            "epochs": [epoch], "gaia": [], "known": [], **extra}, epoch


def test_known_v_limit_null_v_and_epoch_match():
    data, e = _field(known=[
        {"name": "(1) bright", "epoch_id": "e1", "ra": 100.0, "dec": 0.001, "v": 15.0, "source": "mpc"},
        {"name": "(2) faint", "epoch_id": "e1", "ra": 100.0, "dec": -0.001, "v": 20.5, "source": "mpc"},
        {"name": "C/2025 N1 (ATLAS) long name", "epoch_id": "e1", "ra": 100.002, "dec": 0.0, "v": None,
         "source": "mpc"},
        {"name": "(3) other epoch", "epoch_id": "e2", "ra": 100.0, "dec": 0.0, "v": 10.0, "source": "mpc"},
        {"name": "(4) off the frame", "epoch_id": "e1", "ra": 101.0, "dec": 0.0, "v": 10.0, "source": "mpc"},
    ])
    m = fr.epoch_marks(data, e, (41, 41))
    names = [k[2] for k in m.known]
    assert names[0] == "(1) bright" and len(names) == 2 and names[1].endswith("…")
    assert m.known_fainter == 1
    assert len(fr.epoch_marks(data, e, (41, 41), max_known_v=None).known) == 3


def test_gaia_limit_and_cap():
    stars = [{"id": str(i), "ra": 100.0 + (i % 10 - 5) * 0.0005, "dec": (i // 10 - 5) * 0.0005,
              "pmra": 0.0, "pmdec": 0.0, "parallax": None, "g": 10.0 + i * 0.1} for i in range(100)]
    stars.append({"id": "nog", "ra": 100.0, "dec": 0.0, "pmra": None, "pmdec": None, "parallax": None, "g": None})
    data, e = _field(gaia=stars)
    m = fr.epoch_marks(data, e, (41, 41), max_gaia_g=15.0, max_gaia=20)
    assert len(m.gaia) == 20 and m.gaia_capped == 31 and m.gaia_fainter == 50  # 51 with G ≤ 15; 49 + no G
    assert [g for _, _, g in m.gaia] == sorted(g for _, _, g in m.gaia) and m.gaia[0][2] == 10.0


def test_measured_matched_by_epoch_with_labels():
    det = {"snr": 10.0, "flux_ujy": 100.0, "n_dots": 1, "source_id": None, "gaia_id": None,
           "known_name": None, "track_id": None, "grade": None, "reasons": []}
    data, e = _field(measured={"detections": [
        {**det, "epoch_id": "e1", "ra": 100.0, "dec": 0.0, "class": "suspect", "grade": "B"},
        {**det, "epoch_id": "e1", "ra": 100.001, "dec": 0.0, "class": "mover"},
        {**det, "epoch_id": "e1", "ra": 100.0, "dec": 0.001, "class": "star"},
        {**det, "epoch_id": "e2", "ra": 100.0, "dec": 0.0, "class": "suspect"},
        {**det, "epoch_id": "e1", "ra": 100.0, "dec": 1.0, "class": "artifact"},  # off the frame
    ]})
    m = fr.epoch_marks(data, e, (41, 41))
    assert m.counts()["measured"] == {"suspect": 1, "mover": 1, "known": 0, "artifact": 0, "star": 1, "other": 0}
    assert [(c, t) for _, _, c, t in m.measured] == [("suspect", "suspect B"), ("mover", "mover"), ("star", "")]
    assert fr.epoch_marks({**data, "measured": None}, e, (41, 41)).measured == []


# ---------------------------------------------------------------- drawing

def test_circle_drawn_where_projected_after_crop():
    star = {"id": "1", "ra": 100.0 + 6 / 3600, "dec": 10 / 3600, "pmra": 0, "pmdec": 0, "parallax": None, "g": 12.0}
    data, e = _field(gaia=[star])
    full = _flat(41)
    zoom = 0.5  # 30″ → half 8 px → 17×17 crop from pixel 12
    a = fr.crop(full, 2.0, zoom)
    origin = fr.crop_origin(full.shape, 2.0, zoom)
    assert a.shape == (17, 17) and origin == (12, 12)
    m = fr.epoch_marks(data, e, a.shape, origin)
    assert m.gaia[0][:2] == pytest.approx((20 - 3 - 12, 20 - 5 - 12), abs=1e-3)  # 6″ east = 3 px left
    png = fr.contact_sheet([a, a], ["1", "2"], marks=[m, m])
    rgb = _rgb(png)
    factor = -(-fr.PANEL_MIN // 17)
    ys, xs = np.nonzero(np.all(rgb == fr.GAIA_COLOR, axis=2))
    panel1 = xs < 17 * factor
    assert panel1.sum() > 20
    assert xs[panel1].mean() == pytest.approx((5 + 0.5) * factor, abs=1.5)
    assert ys[panel1].mean() == pytest.approx((3 + 0.5) * factor + fr.LABEL_H, abs=1.5)
    # no marks on the difference panel (the third)
    assert not (xs >= 2 * (17 * factor + fr.GAP)).any()


def test_legend_strip_and_reason():
    a = _flat(30)
    items = fr.legend(measured=False, measured_reason="not_processed")
    assert any("not_processed" in t for _, _, t in items)
    assert any(t.startswith("suspect") for _, _, t in fr.legend(measured=True))
    assert any("brightest" in t for _, _, t in fr.legend(gaia_capped=True))
    plain = Image.open(io.BytesIO(fr.contact_sheet([a, a], ["1", "2"])))
    with_legend = Image.open(io.BytesIO(fr.contact_sheet([a, a], ["1", "2"], legend_items=items)))
    assert with_legend.width == plain.width and with_legend.height > plain.height


def test_animation_has_one_frame_per_epoch_and_loops():
    frames = [_flat(30) + 0.1 * i for i in range(3)]
    data, e = _field()
    marks = [fr.epoch_marks(data, e, (30, 30)) for _ in frames]
    gif = fr.animation(frames, ["a", "b", "c"], marks, fr.legend())
    im = Image.open(io.BytesIO(gif))
    assert im.format == "GIF" and im.n_frames == 3
    assert im.info.get("loop") == 0 and im.info.get("duration") == 700


# ---------------------------------------------------------------- blink end to end

def _image(content):
    c = content.to_image_content()
    return c.mimeType, base64.b64decode(c.data)


def test_blink_overlays_counts_and_gif(offline):
    res = run(server.blink(target="Barnard's Star", animate=True))
    assert len(res) == 3
    meta = json.loads(res[0])
    (png_type, _), (gif_type, gif) = _image(res[1]), _image(res[2])
    assert png_type == "image/png" and gif_type == "image/gif"
    n = len(meta["epochs_shown"])
    assert Image.open(io.BytesIO(gif)).n_frames == n
    ov = meta["overlays"]
    assert ov["max_gaia_g"] == 19.0 and ov["max_known_v"] == 18.0
    assert [p["epoch_id"] for p in ov["per_frame"]] == [e["id"] for e in meta["epochs_shown"]]
    assert all(p["gaia"] > 0 for p in ov["per_frame"]) and ov["drawn"]["gaia"] == sum(p["gaia"] for p in ov["per_frame"])


def test_blink_overlays_off(offline):
    on = run(server.blink(target="Barnard's Star"))
    off = run(server.blink(target="Barnard's Star", overlays=False))
    assert len(off) == 2 and json.loads(off[0])["overlays"] is None
    h_on = Image.open(io.BytesIO(_image(on[1])[1])).height
    h_off = Image.open(io.BytesIO(_image(off[1])[1])).height
    assert h_on > h_off  # the legend strip


def _cand_with_measured() -> dict:
    """The candidate's real static frames with a measured layer added: a suspect at the frame
    centre in the first exposure and one far away; data_url made absolute so /api/ serves it."""
    data = json.loads(CAND.read_text())
    for e in data["epochs"]:
        e["data_url"] = f"/data/cand/{CAND_ID}/{e['data_url']}"
    eps = sorted(data["epochs"], key=lambda e: e["mjd_mid"])
    det = {"snr": 12.0, "flux_ujy": 500.0, "n_dots": 1, "source_id": None, "gaia_id": None,
           "known_name": None, "track_id": "t1", "grade": "A", "reasons": []}
    c = data["center"]
    data["measured"] = {
        "zone": "hunt", "stage": "final", "zone_box": {"ra": [120.5, 130.5], "dec": [14.4, 24.4]},
        "clock": "hours", "photos": len(eps), "photos_missing": 0, "truncated": False,
        "counts": {"star": 0, "known": 0, "mover": 0, "suspect": 2, "artifact": 0, "other": 0},
        "detections": [{**det, "epoch_id": eps[0]["id"], "ra": c["ra"], "dec": c["dec"], "class": "suspect"},
                       {**det, "epoch_id": eps[0]["id"], "ra": c["ra"], "dec": c["dec"] + 0.15, "class": "suspect"}],
        "tracks": [], "shifts": []}
    data["measured_reason"] = None
    return data


def test_blink_measured_layer_known_limit_and_crop(monkeypatch):
    body = json.dumps(_cand_with_measured()).encode()

    def extra(request: httpx.Request):
        if request.url.path == "/api/frames":
            return httpx.Response(200, content=body, headers={"content-type": "application/json"})
        return None

    make_offline(monkeypatch, extra)
    c = json.loads(CAND.read_text())["center"]
    meta = json.loads(run(server.blink(ra=c["ra"], dec=c["dec"], clock="hours"))[0])
    ov = meta["overlays"]
    assert ov["drawn"]["measured"]["suspect"] == 2  # both inside the 20′ frame
    # this field's known objects are V 19–21: hidden at the default V 18, drawn when asked
    assert ov["drawn"]["known"] == 0 and sum(p["known_fainter"] for p in ov["per_frame"]) > 0
    deep = json.loads(run(server.blink(ra=c["ra"], dec=c["dec"], clock="hours", max_known_v=25))[0])
    assert deep["overlays"]["drawn"]["known"] > 0
    # zoomed to 2′: the suspect 9′ away is outside the crop
    z = json.loads(run(server.blink(ra=c["ra"], dec=c["dec"], clock="hours", zoom_arcmin=2))[0])
    assert z["overlays"]["drawn"]["measured"]["suspect"] == 1
    assert z["overlays"]["drawn"]["gaia"] < ov["drawn"]["gaia"]


def test_protocol_blink_animate_returns_png_then_gif(offline):
    from mcp.shared.memory import create_connected_server_and_client_session

    async def go():
        async with create_connected_server_and_client_session(server.app._mcp_server) as s:
            return await s.call_tool("blink", {"target": "Barnard's Star", "animate": True})

    res = run(go())
    assert [c.type for c in res.content] == ["text", "image", "image"]
    assert [c.mimeType for c in res.content[1:]] == ["image/png", "image/gif"]
