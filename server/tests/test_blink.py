"""build_frames and build_object end to end, offline: the search, Gaia and SkyBoT answers are
canned and every cutout comes from the cache (the real cutout in fixtures/fits/)."""

import io
import json
import re
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from skyshift_server import blink, cutouts, gaia, known, obj, search, stack
from skyshift_server.cache import Cache
from skyshift_server.errors import ApiError, NotFoundError

from conftest import FIXTURE_CUTOUT, sia_row

REPO = Path(__file__).resolve().parents[2]
# a bright star near the middle of the fixture cutout (fixtures/fits/*.stars.json) and its Gaia entry
STAR_RA, STAR_DEC = 304.4799843, 41.8376316
STAR_GAIA_ID = "2068618363302306688"
MJD1 = 60794.017655738  # the fixture's MJD-AVG
VISIT2 = 185.0  # days later
EPOCH_KEYS = {"id", "kind", "mjd_mid", "mjd_start", "mjd_end", "det", "um_min", "um_max", "n_exposures",
              "flag_frac", "sat_frac", "data_url", "scale", "zero"}


def _cutout_at(mjd: float) -> bytes:
    """The fixture cutout with its times moved (so we can fake several exposures and visits)."""
    with fits.open(FIXTURE_CUTOUT) as h:
        hdr = h["IMAGE"].header
        shift = mjd - hdr["MJD-AVG"]
        for k in ("MJD-AVG", "MJD-BEG", "MJD-END", "MJD-OBS"):
            if k in hdr:
                hdr[k] = hdr[k] + shift
        b = io.BytesIO()
        h.writeto(b)
        return b.getvalue()


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A cache holding 3 + 3 exposures of detector 1 in two visits (and detector 4 rows with no
    cutouts), canned Gaia and SkyBoT, and no network."""
    cache = Cache(str(tmp_path))
    times = [MJD1, MJD1 + 0.0015, MJD1 + 3.0, MJD1 + VISIT2, MJD1 + VISIT2 + 0.0015, MJD1 + VISIT2 + 0.003]
    rows = [sia_row(f"2025W18_1B_{i:04d}_1", 1, t) for i, t in enumerate(times)]
    rows += [sia_row(f"2025W18_1B_{i:04d}_1", 4, t) for i, t in enumerate(times)]
    monkeypatch.setattr(search, "_query_sia", lambda ra, dec, c: rows if c == "spherex_dr1" else [])
    images = search.search(STAR_RA, STAR_DEC, cache=cache).of_detector(1)
    for im, t in zip(images, times):
        cache.put(cutouts.cache_key(im, STAR_RA, STAR_DEC, cutouts.SIZE_TIERS_DEG[-1]), _cutout_at(t))
    stars = json.loads((REPO / "fixtures/fits/cutout_2025W18_1B_0074_2D1.gaia.json").read_text())["gaia"]
    monkeypatch.setattr(gaia, "cone", lambda ra, dec, r, cache=None: stars)
    skybot_calls = []
    monkeypatch.setattr(known, "_skybot", lambda ra, dec, rs, mjd: skybot_calls.append(mjd) or
                        " 4 | Vesta | 20 17 55.1962 | +41 50 15.474 | MB>Inner | 7.1 | 0.01 | 1.0 \n")

    def no_network(*a, **k):
        raise AssertionError("the network was used")

    monkeypatch.setattr(cutouts, "_get", no_network)
    monkeypatch.setenv("SKYSHIFT_PIXELS", "irsa")
    return {"cache": cache, "stars": stars, "skybot_calls": skybot_calls, "times": times}


def _decode(fr: blink.Frames, ep: dict) -> np.ndarray:
    nx, ny = fr.meta["size"]
    return stack.decode(fr.blobs[ep["data_url"]], ep["scale"], ep["zero"], (ny, nx))


def test_months_frames_contract_shape_and_pixels(world):
    fr = blink.build_frames(STAR_RA, STAR_DEC, 5, "months", 1, cache=world["cache"])
    m = fr.meta
    assert set(m) == {"clock", "center", "pixel_arcsec", "size", "epochs", "gaia", "known",
                      "data_release", "archive_checked"}
    assert m["clock"] == "months" and m["center"] == {"ra": STAR_RA, "dec": STAR_DEC}
    assert m["pixel_arcsec"] == pytest.approx(2.05) and m["size"] == [146, 146]
    assert m["data_release"] == "DR1+QR3" and re.fullmatch(r"\d{4}-\d\d-\d\d", m["archive_checked"])
    assert [e["id"] for e in m["epochs"]] == ["d1-v1", "d1-v2"]
    for e in m["epochs"]:
        assert set(e) == EPOCH_KEYS
        assert e["kind"] == "visit" and e["det"] == 1 and e["n_exposures"] == 3
        assert e["mjd_start"] <= e["mjd_mid"] <= e["mjd_end"]
        assert e["mjd_mid"] == pytest.approx((e["mjd_start"] + e["mjd_end"]) / 2, abs=1e-5)
        assert 0.74 < e["um_min"] < e["um_max"] < 1.11
        assert 0 <= e["sat_frac"] <= e["flag_frac"] < 0.2
        assert re.fullmatch(r"[A-Za-z0-9_.\-/]+", e["data_url"]) and ".." not in e["data_url"]
        assert len(fr.blobs[e["data_url"]]) == 2 * 146 * 146
    assert set(fr.blobs) == {e["data_url"] for e in m["epochs"]}
    assert m["epochs"][0]["mjd_start"] == pytest.approx(MJD1, abs=1e-5)
    assert m["epochs"][0]["mjd_end"] == pytest.approx(MJD1 + 3.0, abs=1e-5)
    assert m["epochs"][1]["mjd_mid"] == pytest.approx(MJD1 + VISIT2 + 0.0015, abs=1e-5)

    # the star we centred on is at the centre pixel ((n-1)/2), and it is the frame's star
    img = _decode(fr, m["epochs"][0])
    c = (146 - 1) / 2
    y, x = np.unravel_index(np.nanargmax(img[53:93, 53:93]), (40, 40))
    assert abs(x + 53 - c) <= 2 and abs(y + 53 - c) <= 2

    # another real star lands where the grid says: north up, east left, right scale
    g = stack.Grid(STAR_RA, STAR_DEC, 2.05, 146)
    ra2, dec2 = 304.4932277, 41.829853  # stars.json: 36" east, 28" south of the centre
    px, py = (float(v) for v in g.wcs.wcs_world2pix(ra2, dec2, 0))
    assert px < c and py > c  # east = left, south = down
    sub = img[int(py) - 4:int(py) + 6, int(px) - 4:int(px) + 6]
    yy, xx = np.unravel_index(np.nanargmax(sub), sub.shape)
    assert abs(xx + int(px) - 4 - px) <= 2 and abs(yy + int(py) - 4 - py) <= 2
    assert np.nanmax(sub) > np.nanmedian(img) + 20 * stack.robust_sigma(img)

    # Gaia and known objects ride along; one SkyBoT question per epoch, at its mjd_mid
    assert m["gaia"] == world["stars"]
    assert sorted(world["skybot_calls"]) == pytest.approx(sorted(e["mjd_mid"] for e in m["epochs"]))
    assert {k["epoch_id"] for k in m["known"]} == {"d1-v1", "d1-v2"}
    assert m["known"][0]["name"] == "(4) Vesta" and m["known"][0]["source"] == "skybot"
    assert set(m["known"][0]) == {"name", "epoch_id", "ra", "dec", "v", "source"}


def test_hours_frames_one_visit_in_time_order(world):
    fr = blink.build_frames(STAR_RA, STAR_DEC, 20, "hours", 1, cache=world["cache"])
    m = fr.meta
    assert m["clock"] == "hours" and m["size"] == [195, 195] and m["pixel_arcsec"] == pytest.approx(6.15)
    eps = m["epochs"]
    assert len(eps) == 3  # one visit only: the latest of the two equal ones
    assert [e["mjd_mid"] for e in eps] == sorted(e["mjd_mid"] for e in eps)
    assert eps[0]["mjd_mid"] == pytest.approx(MJD1 + VISIT2, abs=1e-5)
    for e in eps:
        assert set(e) == EPOCH_KEYS and e["kind"] == "exposure" and e["n_exposures"] == 1
        assert e["id"].startswith("d1-2025W18_1B_") and e["mjd_start"] < e["mjd_mid"] < e["mjd_end"]
        assert len(fr.blobs[e["data_url"]]) == 2 * 195 * 195
    img = _decode(fr, eps[0])
    assert np.isnan(img[0, 0])  # the 10' fixture does not fill a 20' frame: raw 0 = no data
    c = 97
    y, x = np.unravel_index(np.nanargmax(img[c - 3:c + 4, c - 3:c + 4]), (7, 7))
    assert abs(x - 3) <= 1 and abs(y - 3) <= 1  # the star is at the centre


def test_hours_60_is_binned(world):
    fr = blink.build_frames(STAR_RA, STAR_DEC, 60, "hours", 1, cache=world["cache"])
    assert fr.meta["size"] == [293, 293] and fr.meta["pixel_arcsec"] == pytest.approx(12.3)
    img = _decode(fr, fr.meta["epochs"][0])
    y, x = np.unravel_index(np.nanargmax(img[143:150, 143:150]), (7, 7))
    assert abs(x - 3) <= 1 and abs(y - 3) <= 1


def test_bad_arguments_and_no_data(world):
    for args in ((5, "weeks", 1), (20, "months", 1), (5, "hours", 1), (5, "months", 7)):
        with pytest.raises(ApiError) as e:
            blink.build_frames(STAR_RA, STAR_DEC, args[0], args[1], args[2], cache=world["cache"])
        assert e.value.code == "bad_request"
    with pytest.raises(NotFoundError):
        blink.build_frames(STAR_RA, STAR_DEC, 5, "months", 2, cache=world["cache"])  # no D2 images


def test_pick_hours_images_caps_whole_sessions():
    rows = []
    for s in range(30):  # 30 sessions of 2 exposures in one visit
        rows += [sia_row(f"S{s:02d}_{k}", 2, 60900.0 + s * 0.5 + k * 0.0015) for k in (1, 2)]
    rows += [sia_row(f"L{s}", 2, 61200.0 + s) for s in range(5)]  # a smaller, later visit
    picked = blink.pick_hours_images(search.rows_to_images(rows))
    assert len(picked) == blink.HOURS_MAX_FRAMES == 48
    assert {im.visit for im in picked} == {1}
    assert [im.mjd for im in picked] == sorted(im.mjd for im in picked)
    assert picked[-1].obs_id == "S23_2"  # sessions are kept whole


def test_object_spectrum_and_zero_shift(world):
    details = {}
    o = obj.build_object(STAR_RA, STAR_DEC, cache=world["cache"], details=details)
    assert set(o) == {"name", "gaia", "known_ucd", "spectrum", "measure"}
    assert o["name"] == f"Gaia DR3 {STAR_GAIA_ID}" and o["gaia"]["id"] == STAR_GAIA_ID
    assert o["known_ucd"] is None
    sp = o["spectrum"]
    assert len(sp) == 6 and [p["um"] for p in sp] == sorted(p["um"] for p in sp)
    for p in sp:
        assert set(p) == {"um", "flux_ujy", "err_ujy", "det", "mjd", "visit", "saturated"}
        assert p["det"] == 1 and 0.74 < p["um"] < 1.11 and p["flux_ujy"] > 1e4 and p["err_ujy"] > 0
    assert sorted({p["visit"] for p in sp}) == [1, 2]
    m = o["measure"]
    assert set(m) == {"shift_arcsec", "pa_deg", "dt_yr", "rate_arcsec_yr", "sigma_arcsec",
                      "n_ref_stars", "predicted_arcsec"}
    assert m["shift_arcsec"] < 0.05  # the same pixels in both visits
    assert m["dt_yr"] == pytest.approx((VISIT2 + 0.0015 - 1.0005) / 365.25, abs=1e-3)
    assert m["n_ref_stars"] >= 3 and m["sigma_arcsec"] < 0.05
    assert m["predicted_arcsec"] is not None and m["predicted_arcsec"] < 0.05  # a slow star
    assert details["det"] == 1 and details["visits"] == [1, 2]


def test_identify_follows_fast_stars():
    barnard = {"id": "4472832130942575872", "ra": 269.44850252543836, "dec": 4.739420051112412,
               "pmra": -801.551, "pmdec": 10362.394, "parallax": 546.976, "g": 8.194}
    slow = {"id": "1", "ra": 269.4462, "dec": 4.7705, "pmra": 1.0, "pmdec": -2.0, "parallax": 0.5, "g": 15.0}
    builtin = [{"name": "Barnard's Star", "id": barnard["id"], "ra": barnard["ra"], "dec": barnard["dec"],
                "pmra": -801.5509783684709, "pmdec": 10362.394206546573, "parallax": 546.975939730948,
                "g": 8.194, "epoch_mjd": 57388.5}]
    # Barnard's Star in March 2026 is 106" north of its Gaia position
    name, entry, model = obj.identify(269.44620, 4.76883, 60900.0, 61300.0, [barnard, slow], builtin)
    assert name == "Barnard's Star" and entry == barnard and model["pmdec"] > 10000
    name, entry, _ = obj.identify(269.44620, 4.76883, 60900.0, 61300.0, [barnard, slow], [])
    assert name == "Gaia DR3 4472832130942575872"
    # 6" away from the slow star only
    name, entry, _ = obj.identify(269.4462, 4.7705 + 5.0 / 3600, 60900.0, 61300.0, [slow], [])
    assert entry == slow
    assert obj.identify(269.5, 4.9, 60900.0, 61300.0, [barnard, slow], builtin) == (None, None, None)
