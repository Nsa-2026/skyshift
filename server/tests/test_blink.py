"""build_frames and build_object end to end, offline: the search, Gaia and known-object answers are
canned and every cutout comes from the cache (the real cutout in fixtures/fits/)."""

import io
import json
import re
from collections import Counter
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits
from skyshift_core import open_level2

from skyshift_server import blink, cutouts, gaia, known, obj, search, stack
from skyshift_server.cache import Cache
from skyshift_server.errors import ApiError, NotFoundError

from conftest import FIXTURE_CUTOUT, sia_row

REPO = Path(__file__).resolve().parents[2]
# a bright star near the middle of the fixture cutout (fixtures/fits/*.stars.json) and its Gaia entry
STAR_RA, STAR_DEC = 304.4799843, 41.8376316
STAR_GAIA_ID = "2068618363302306688"
# an empty patch of the fixture 2' from it: no dot and no Gaia star within 6 px (37")
EMPTY_RA, EMPTY_DEC = 304.4416228966218, 41.82004012070606
MJD1 = 60794.017655738  # the fixture's MJD-AVG
VISIT2 = 185.0  # days later
ZODI_LEVEL = 0.1
EPOCH_KEYS = {"id", "kind", "mjd_mid", "mjd_start", "mjd_end", "det", "um_min", "um_max", "n_exposures",
              "flag_frac", "sat_frac", "data_url", "scale", "zero"}


def _cutout_at(mjd: float, noise: float = 0.0, seed: int = 0) -> bytes:
    """The fixture cutout with its times moved (so we can fake several exposures and visits),
    optionally with fresh noise (MJy/sr) added to IMAGE so that exposures differ."""
    with fits.open(FIXTURE_CUTOUT) as h:
        hdr = h["IMAGE"].header
        shift = mjd - hdr["MJD-AVG"]
        for k in ("MJD-AVG", "MJD-BEG", "MJD-END", "MJD-OBS"):
            if k in hdr:
                hdr[k] = hdr[k] + shift
        if noise:
            data = h["IMAGE"].data
            h["IMAGE"].data = (data + np.random.default_rng(seed).normal(0, noise, data.shape)).astype(data.dtype)
        b = io.BytesIO()
        h.writeto(b)
        return b.getvalue()


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A cache holding 3 + 3 exposures of detector 1 in two visits (and detector 4 rows with no
    cutouts), canned Gaia and known objects, and no network."""
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
    known_calls = []

    def fake_near(ra, dec, r, mjds, *, epoch_ids=None, cache=None):
        known_calls.append({"ra": ra, "dec": dec, "r": r, "mjds": list(mjds), "ids": list(epoch_ids)})
        return [{"name": "Vesta", "epoch_id": i, "ra": 304.48, "dec": 41.84, "v": 7.1, "source": "mpc"}
                for i in epoch_ids]

    monkeypatch.setattr(known, "near", fake_near)

    def no_network(*a, **k):
        raise AssertionError("the network was used")

    monkeypatch.setattr(cutouts, "_get", no_network)
    zodi_calls = []

    def flat_zodi(im, l2, cache):  # NASA's model, here a flat 0.1 MJy/sr
        zodi_calls.append(im.key)
        return np.full(l2.data.shape, ZODI_LEVEL, dtype=np.float32)

    monkeypatch.setattr(cutouts, "zodi_model", flat_zodi)
    monkeypatch.setenv("SKYSHIFT_PIXELS", "irsa")
    return {"cache": cache, "stars": stars, "known_calls": known_calls, "times": times, "zodi_calls": zodi_calls}


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

    # the zodiacal-light model was taken off every exposure: the sky is 0.1 MJy/sr lower
    raw_sky = float(np.nanmedian(open_level2(FIXTURE_CUTOUT).data[30:70, 20:60]))
    assert len(world["zodi_calls"]) == 6
    assert float(np.nanmedian(img)) == pytest.approx(raw_sky - ZODI_LEVEL, abs=0.03)

    # Gaia and known objects ride along; one MPC call for the frame centre, at every epoch's mjd_mid
    assert m["gaia"] == world["stars"]
    (call,) = world["known_calls"]
    assert (call["ra"], call["dec"]) == (STAR_RA, STAR_DEC) and call["r"] == pytest.approx(5 / 2 * 2**0.5)
    assert call["mjds"] == pytest.approx([e["mjd_mid"] for e in m["epochs"]])
    assert call["ids"] == ["d1-v1", "d1-v2"]
    assert {k["epoch_id"] for k in m["known"]} == {"d1-v1", "d1-v2"}
    assert m["known"][0]["name"] == "Vesta" and m["known"][0]["source"] == "mpc"
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


def _visit_rows(sizes, start=60900.0, step=1.5, det=2):
    """One visit: session s has sizes[s] exposures 2.1 min apart, sessions `step` days apart."""
    rows = []
    for s, n in enumerate(sizes):
        rows += [sia_row(f"S{s:02d}_{k}", det, start + s * step + k * 2.1 / 1440) for k in range(n)]
    return rows


def test_pick_hours_live_spreads_whole_runs_over_the_visit():
    # 21 sessions over 30 days (52 exposures); sessions of 1 exposure in between
    sizes = [4, 1, 3, 1, 2, 5, 1, 3, 2, 1, 4, 3, 1, 2, 5, 1, 3, 2, 1, 4, 3]
    images = search.rows_to_images(_visit_rows(sizes))
    assert len(images) == 52 and len(blink.pick_hours_images(images)) == 45  # sessions 0-17
    picked = blink.pick_hours_live(images, 6, 2)
    assert len(picked) == 6 and [im.mjd for im in picked] == sorted(im.mjd for im in picked)
    by_session: dict[int, list] = {}
    for im in picked:
        by_session.setdefault(im.session, []).append(im.obs_id)
    # three pairs: each the first two exposures of one session, spread over the visit's sessions
    # with two or more exposures, the first and last included (past the 48 precompute shows)
    assert list(by_session.values()) == [["S00_0", "S00_1"], ["S10_0", "S10_1"], ["S20_0", "S20_1"]]
    # the default is the live cap, and it keeps at least two runs of two
    live = blink.pick_hours_live(images)
    assert len(live) <= blink.LIVE_HOURS_FRAMES and len({im.session for im in live}) >= 2
    assert all(n >= 2 for n in Counter(im.session for im in live).values())
    # no cap (precompute) and small visits: what pick_hours_images shows
    assert blink.pick_hours_live(images, 0) == blink.pick_hours_images(images)
    small = search.rows_to_images(_visit_rows([2, 1, 2]))
    assert blink.pick_hours_live(small, 6) == blink.pick_hours_images(small)


def test_pick_hours_live_edge_cases():
    one = search.rows_to_images(_visit_rows([9]))  # a single long session: its first exposures
    assert [im.obs_id for im in blink.pick_hours_live(one, 6, 2)] == [f"S00_{k}" for k in range(6)]
    singles = search.rows_to_images(_visit_rows([1] * 12))  # no session has two: spread singles
    got = blink.pick_hours_live(singles, 6, 2)
    assert [im.obs_id for im in got] == ["S00_0", "S02_0", "S04_0", "S07_0", "S09_0", "S11_0"]


def test_hours_live_cap_and_full_set(world, monkeypatch):
    rows = _visit_rows([3, 1, 3, 3], start=MJD1, det=1)
    monkeypatch.setattr(search, "_query_sia", lambda ra, dec, c: rows if c == "spherex_dr1" else [])
    images = search.search(STAR_RA, STAR_DEC, cache=world["cache"], refresh=True).of_detector(1)
    for im in images:
        world["cache"].put(cutouts.cache_key(im, STAR_RA, STAR_DEC, cutouts.SIZE_TIERS_DEG[-1]), _cutout_at(im.mjd))
    full = blink.build_frames(STAR_RA, STAR_DEC, 20, "hours", 1, cache=world["cache"], hours_frames=None)
    live = blink.build_frames(STAR_RA, STAR_DEC, 20, "hours", 1, cache=world["cache"], hours_frames=4)
    assert len(full.meta["epochs"]) == 10
    ids = [e["id"] for e in live.meta["epochs"]]
    assert ids == ["d1-S00_0", "d1-S00_1", "d1-S03_0", "d1-S03_1"]
    assert all(e["n_exposures"] == 1 for e in live.meta["epochs"])  # each frame is one real exposure
    assert set(live.blobs) == {e["data_url"] for e in live.meta["epochs"]}
    assert {k["epoch_id"] for k in live.meta["known"]} == set(ids)  # known objects for the shown frames only


def test_object_spectrum_and_zero_shift(world):
    details = {}
    o = obj.build_object(STAR_RA, STAR_DEC, cache=world["cache"], details=details)
    assert set(o) == {"name", "gaia", "known_ucd", "spectrum", "spectrum_reason", "measure",
                      "measure_reason", "measure_hint"}
    assert o["spectrum_reason"] is None
    assert o["measure_reason"] is None and o["measure_hint"] is None  # there is a measure
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
    assert m["dt_yr"] == pytest.approx((VISIT2 + 0.0015 - 0.0015) / 365.25, abs=1e-4)
    assert m["n_ref_stars"] >= 3 and m["sigma_arcsec"] < 0.05
    assert m["predicted_arcsec"] is not None and m["predicted_arcsec"] < 0.05  # a slow star
    assert details["det"] == 1 and details["visits"] == [1, 2]
    # photometry is done on IMAGE as it is: the zodi model is only taken off the stacks
    assert len(world["zodi_calls"]) == 6


def test_object_on_an_empty_spot_has_no_shift(world, monkeypatch):
    """A spot with nothing on it (RA 125.5, Dec 19.4 on the live site: "30.15" between visits 1
    and 2"). Without the detection guard the centroid takes the brightest noise peak near the
    spot in each stack, two different ones, and reports their distance as motion. With it, no
    shift: measure is null and details say why."""
    ra, dec = EMPTY_RA, EMPTY_DEC
    images = search.search(ra, dec, cache=world["cache"]).of_detector(1)
    for i, (im, t) in enumerate(zip(images, world["times"])):
        world["cache"].put(cutouts.cache_key(im, ra, dec, cutouts.SIZE_TIERS_DEG[-1]), _cutout_at(t, 0.05, seed=i))
    unguarded = obj.measure_shift

    def old_rule(*a, **kw):
        return unguarded(*a, **kw, min_snr=None, edge_r=None, max_pm_arcsec_yr=None)

    monkeypatch.setattr(obj, "measure_shift", old_rule)
    before = obj.build_object(ra, dec, cache=world["cache"])
    assert before["name"] is None and before["gaia"] is None
    assert before["measure"] is not None and before["measure"]["shift_arcsec"] > 3 * before["measure"]["sigma_arcsec"]

    monkeypatch.setattr(obj, "measure_shift", unguarded)
    details = {}
    o = obj.build_object(ra, dec, cache=world["cache"], details=details)
    assert o["measure"] is None
    assert o["measure_reason"] == "not_detected" and o["measure_hint"] is None
    assert details["measure_reason"] == "not_detected" and details["shift_error"] in ("not_detected", "not_found")
    assert o["spectrum"]  # the spectrum is still measured there


def _empty_spot_cutouts(world):
    images = search.search(EMPTY_RA, EMPTY_DEC, cache=world["cache"]).of_detector(1)
    for i, (im, t) in enumerate(zip(images, world["times"])):
        world["cache"].put(cutouts.cache_key(im, EMPTY_RA, EMPTY_DEC, cutouts.SIZE_TIERS_DEG[-1]),
                           _cutout_at(t, 0.05, seed=i))


def test_object_at_a_fast_stars_old_position_says_where_it_went(world, monkeypatch):
    """Someone pastes a fast star's Gaia 2016 position (as Barnard's Star at 269.4485, 4.7394): by
    SPHEREx's visits the star is elsewhere, nothing is measured, and the answer says where it went."""
    _empty_spot_cutouts(world)
    fast = {"id": "999", "ra": EMPTY_RA, "dec": EMPTY_DEC, "pmra": 0.0, "pmdec": 3000.0, "parallax": 10.0, "g": 12.0}
    monkeypatch.setattr(gaia, "cone", lambda ra, dec, r, cache=None: [*world["stars"], fast])
    details = {}
    o = obj.build_object(EMPTY_RA, EMPTY_DEC, cache=world["cache"], details=details)
    assert o["name"] is None and o["measure"] is None
    assert o["measure_reason"] == "moved_away" and details["measure_reason"] == "moved_away"
    hint = o["measure_hint"]
    assert set(hint) == {"name", "ra", "dec", "mjd"} and hint["name"] == "Gaia DR3 999"
    t = world["times"]
    assert hint["mjd"] == pytest.approx((t[0] + t[-1]) / 2, abs=0.01)
    north = (hint["dec"] - EMPTY_DEC) * 3600
    assert north == pytest.approx(3.0 * (hint["mjd"] - obj.GAIA_EPOCH_MJD) / 365.25, abs=0.2)  # ~28"
    # at the hint the star is found again: no hint there
    assert obj.moved_away(hint["ra"], hint["dec"], t[0], t[-1], [fast], []) is None


def test_object_reason_few_visits_and_no_time(world, monkeypatch):
    real_pick = obj.pick_detector
    monkeypatch.setattr(obj, "pick_detector", lambda *a, **k: None)  # one visit only
    o = obj.build_object(STAR_RA, STAR_DEC, cache=world["cache"])
    assert o["measure"] is None and o["measure_reason"] == "few_visits" and o["measure_hint"] is None

    monkeypatch.setattr(obj, "pick_detector", real_pick)

    def late(*a, budget=None, **k):  # what measure() does when too few exposures arrived in time
        budget.cut("too few exposures in time for the measurement")
        return None

    monkeypatch.setattr(obj, "measure", late)
    budget = obj.Budget(60.0)
    o = obj.build_object(STAR_RA, STAR_DEC, cache=world["cache"], budget=budget)
    assert budget.partial and o["spectrum"]
    assert o["measure"] is None and o["measure_reason"] == "no_time" and o["measure_hint"] is None


BARNARD_GAIA = {"id": "4472832130942575872", "ra": 269.44850252543836, "dec": 4.739420051112412,
                "pmra": -801.551, "pmdec": 10362.394, "parallax": 546.976, "g": 8.194}
BARNARD_BUILTIN = {"name": "Barnard's Star", "slug": "barnard", "id": BARNARD_GAIA["id"],
                   "ra": BARNARD_GAIA["ra"], "dec": BARNARD_GAIA["dec"], "pmra": -801.5509783684709,
                   "pmdec": 10362.394206546573, "parallax": 546.975939730948, "g": 8.194, "epoch_mjd": 57388.5}
SPHEREX_BARNARD = (60902.87, 61305.63)  # first and last exposure at Barnard's Star (static frames)


def test_moved_away_barnards_catalogue_positions():
    slow = {"id": "1", "ra": 269.4462, "dec": 4.7705, "pmra": 1.0, "pmdec": -2.0, "parallax": 0.5, "g": 15.0}
    a, b = SPHEREX_BARNARD
    # its Gaia DR3 position as people paste it (4 decimals): the built-in name, where SPHEREx sees it
    hint = obj.moved_away(269.4485, 4.7394, a, b, [BARNARD_GAIA, slow], [BARNARD_BUILTIN])
    assert hint["name"] == "Barnard's Star" and hint["mjd"] == pytest.approx((a + b) / 2, abs=0.01)
    assert (hint["dec"] - 4.7394) * 3600 == pytest.approx(105.4, abs=1.5)  # 100-111" north over the visits
    assert hint["ra"] == pytest.approx(269.44620, abs=2e-4)  # the featured target's frame centre
    # without the built-ins: the Gaia star
    assert obj.moved_away(269.4485, 4.7394, a, b, [BARNARD_GAIA, slow], [])["name"] == f"Gaia DR3 {BARNARD_GAIA['id']}"
    # SIMBAD's J2000.0 position (17 57 48.498 +04 41 36.11) and the Hipparcos epoch 1991.25 position
    assert obj.moved_away(269.452075, 4.693364, a, b, [BARNARD_GAIA], [])["name"].startswith("Gaia DR3")
    r91, d91 = obj.position_at(BARNARD_GAIA, 51544.5 - 8.75 * 365.25, parallax=False)
    assert obj.moved_away(r91, d91, a, b, [BARNARD_GAIA], []) is not None
    # not: where SPHEREx sees it (identify follows it there), with or without skip
    now = obj.position_at(BARNARD_BUILTIN, (a + b) / 2)
    assert obj.moved_away(*now, a, b, [BARNARD_GAIA], [BARNARD_BUILTIN]) is None
    assert obj.moved_away(269.4485, 4.7394, a, b, [BARNARD_GAIA], [BARNARD_BUILTIN], skip=BARNARD_BUILTIN) is None
    # not: a slow star at its own position, a spot 20" from the old position, a star without a motion
    assert obj.moved_away(slow["ra"], slow["dec"], a, b, [slow], []) is None
    assert obj.moved_away(269.4485 + 20 / 3600, 4.7394, a, b, [BARNARD_GAIA], []) is None
    still = {**BARNARD_GAIA, "pmra": None, "pmdec": None}
    assert obj.moved_away(269.4485, 4.7394, a, b, [still], []) is None


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


def test_pick_months_images_spreads_over_the_visit():
    rows = [sia_row(f"A{s:02d}", 2, 60900.0 + s * 0.5) for s in range(20)]  # visit 1: 20 exposures
    rows += [sia_row(f"B{s}", 2, 61100.0 + s) for s in range(3)]  # visit 2: 3 exposures
    images = search.rows_to_images(rows)
    picked = blink.pick_months_images(images, 5)
    v1 = [im for im in picked if im.visit == 1]
    assert [im.obs_id for im in v1] == ["A00", "A05", "A10", "A14", "A19"]  # first, last, evenly between
    assert [im.obs_id for im in picked if im.visit == 2] == ["B0", "B1", "B2"]  # small visits stay whole
    assert blink.pick_months_images(images, None) == images
    assert blink.pick_months_images(images, 0) == images


def test_months_live_cap_and_full_stacks(world):
    full = blink.build_frames(STAR_RA, STAR_DEC, 5, "months", 1, cache=world["cache"], per_visit=None)
    live = blink.build_frames(STAR_RA, STAR_DEC, 5, "months", 1, cache=world["cache"], per_visit=2)
    assert [e["n_exposures"] for e in full.meta["epochs"]] == [3, 3]
    assert [e["n_exposures"] for e in live.meta["epochs"]] == [2, 2]
    # the cap keeps the visit's first and last exposure, so the visit's time span is unchanged
    for a, b in zip(full.meta["epochs"], live.meta["epochs"]):
        assert a["mjd_start"] == b["mjd_start"] and a["mjd_end"] == b["mjd_end"]
    assert blink.LIVE_PER_VISIT >= 3  # the default (the API) still stacks enough to drop asteroids


def test_pick_detector_best_signal_unless_the_target_says_otherwise():
    rows = [sia_row(f"A{k}", d, 60900.0 + k * 0.01) for k in range(3) for d in (1, 2, 5, 6)]
    rows += [sia_row(f"B{k}", d, 61100.0 + k * 0.01) for k in range(3) for d in (1, 2, 5, 6)]
    images = search.rows_to_images(rows)
    snr = {1: 13.0, 2: 11.0, 5: 3.6, 6: 2.9}  # WISE 0855's field: a blend wins short of 3 um
    spectrum = [{"det": d, "flux_ujy": s * 10.0, "err_ujy": 10.0, "saturated": False} for d, s in snr.items()]
    assert obj.pick_detector(spectrum, images) == (1, 1, 2)
    assert obj.pick_detector(spectrum, images, obj.MEASURE_DETECTORS["wise0855"]) == (5, 1, 2)
    saturated = [dict(p, saturated=p["det"] in (1, 2)) for p in spectrum]
    assert obj.pick_detector(saturated, images)[0] == 5  # Barnard's Star: not where it saturates


def test_known_is_asked_at_the_planned_times_and_again_when_an_epoch_moved(tmp_path, monkeypatch):
    asked = []
    monkeypatch.setattr(known, "near", lambda ra, dec, r, mjds, epoch_ids=None, cache=None:
                        asked.append([round(m, 4) for m in mjds]) or
                        [{"name": "Vesta", "epoch_id": i, "ra": 1.0, "dec": 2.0, "v": 7.1, "source": "mpc"}
                         for i in epoch_ids])
    rows = [sia_row("A1", 2, 60900.0), sia_row("A2", 2, 60910.0), sia_row("B1", 2, 61100.0), sia_row("B2", 2, 61104.0)]
    images = search.rows_to_images(rows)
    planned = blink.planned_epochs(images, 2, "months")
    assert planned == [{"id": "d2-v1", "mjd_mid": pytest.approx(60905.0)}, {"id": "d2-v2", "mjd_mid": pytest.approx(61102.0)}]
    assert [p["id"] for p in blink.planned_epochs(images, 2, "hours")] == ["d2-A1", "d2-A2", "d2-B1", "d2-B2"]
    c = Cache(str(tmp_path))
    found = blink.known_for_epochs(planned, 1.0, 2.0, 5, c)
    # v1 as planned; v2 lost its last exposure (no pixels), so its mjd_mid moved to 61100
    epochs = [{"id": "d2-v1", "mjd_mid": 60905.0}, {"id": "d2-v2", "mjd_mid": 61100.0}]
    asked.clear()
    got = blink.reconcile_known(found, planned, epochs, 1.0, 2.0, 5, c)
    assert asked == [[61100.0]]  # only the moved epoch is asked again
    assert sorted(k["epoch_id"] for k in got) == ["d2-v1", "d2-v2"]
    # an epoch that was planned but not built drops its known objects
    assert [k["epoch_id"] for k in blink.reconcile_known(found, planned, epochs[:1], 1.0, 2.0, 5, c)] == ["d2-v1"]


# ---------------------------------------------------------------- live time budget

def test_by_usefulness_gives_every_visit_its_ends_first():
    rows = [sia_row(f"A{s}", 2, 60900.0 + s) for s in range(5)] + [sia_row(f"B{s}", 2, 61100.0 + s) for s in range(3)]
    order = [im.obs_id for im in blink.by_usefulness(search.rows_to_images(rows))]
    assert order == ["A0", "B0", "A4", "B2", "A2", "B1", "A1", "A3"]


def test_spectrum_images_take_detectors_in_turn_and_cap_each():
    rows = [sia_row(f"E{k}", d, 60900.0 + k * 0.01) for k in range(4) for d in (1, 2, 5)]
    order = obj.spectrum_images(search.rows_to_images(rows), 2)
    assert [im.det for im in order] == [1, 2, 5, 1, 2, 5]
    assert len(obj.spectrum_images(search.rows_to_images(rows), None)) == 12


def test_months_frames_partial_when_cutouts_are_late(world, monkeypatch):
    import time

    from skyshift_server.budget import Budget

    real = cutouts.fetch

    def fetch(im, ra, dec, size, cache, variance=False, **kw):
        if im.obs_id.endswith(("0001_1", "0004_1")):  # the middle exposure of each visit
            time.sleep(1.5)
        return real(im, ra, dec, size, cache, variance, **kw)

    monkeypatch.setattr(cutouts, "fetch", fetch)
    b = Budget(blink.FRAMES_RESERVE_S + 0.7)
    t = time.monotonic()
    fr = blink.build_frames(STAR_RA, STAR_DEC, 5, "months", 1, cache=world["cache"], budget=b)
    assert time.monotonic() - t < 1.4
    assert [e["n_exposures"] for e in fr.meta["epochs"]] == [2, 2]  # each visit's first and last
    assert fr.partial == ["2 of 6 cutouts not in time"]
    assert len(fr.meta["gaia"]) > 0 and fr.meta["known"]  # Gaia and known objects answered in time
    full = blink.build_frames(STAR_RA, STAR_DEC, 5, "months", 1, cache=world["cache"], budget=Budget(30))
    assert full.partial == [] and [e["n_exposures"] for e in full.meta["epochs"]] == [3, 3]


def test_nothing_in_time_is_a_503(world, monkeypatch):
    from skyshift_server.budget import Budget, BudgetError

    with pytest.raises(BudgetError) as e:
        blink.build_frames(STAR_RA, STAR_DEC, 5, "months", 1, cache=world["cache"], budget=Budget(0))
    assert e.value.status == 503 and e.value.code == "upstream"
    with pytest.raises(BudgetError):
        obj.build_object(STAR_RA, STAR_DEC, cache=world["cache"], budget=Budget(0))


def test_object_with_a_budget_is_the_same_when_there_is_time(world):
    from skyshift_server.budget import Budget

    b = Budget(60)
    live = obj.build_object(STAR_RA, STAR_DEC, cache=world["cache"], budget=b)
    full = obj.build_object(STAR_RA, STAR_DEC, cache=world["cache"])
    assert b.partial == [] and live == full


def test_known_one_call_per_centre_and_failures_leave_epochs_empty(tmp_path, monkeypatch):
    calls = []

    def fake_near(ra, dec, r, mjds, *, epoch_ids=None, cache=None):
        calls.append((ra, dec, list(mjds), list(epoch_ids)))
        if ra == 9.0:
            raise ValueError("MJD outside the known index's range")
        return [{"name": "Vesta", "epoch_id": i, "ra": ra, "dec": dec, "v": 7.1, "source": "mpc"} for i in epoch_ids]

    monkeypatch.setattr(known, "near", fake_near)
    epochs = [{"id": "a", "mjd_mid": 61000.0}, {"id": "b", "mjd_mid": 61000.1},
              {"id": "c", "mjd_mid": 61000.2, "center": {"ra": 5.0, "dec": 6.0}},
              {"id": "d", "mjd_mid": 61000.3, "center": {"ra": 9.0, "dec": 6.0}}]
    got = blink.known_for_epochs(epochs, 1.0, 2.0, 20, Cache(str(tmp_path)))
    assert calls == [(1.0, 2.0, [61000.0, 61000.1], ["a", "b"]), (5.0, 6.0, [61000.2], ["c"]),
                     (9.0, 6.0, [61000.3], ["d"])]
    assert [(k["epoch_id"], k["ra"]) for k in got] == [("a", 1.0), ("b", 1.0), ("c", 5.0)]  # d failed: empty
    assert blink.known_for_epochs([], 1.0, 2.0, 20, Cache(str(tmp_path))) == []

    # a live build: a time outside the index is final; a missing index makes the answer partial (not cached)
    from skyshift_server.budget import Budget
    from skyshift_server.errors import UpstreamError

    b = Budget(30)
    blink.known_for_epochs(epochs[3:], 1.0, 2.0, 20, Cache(str(tmp_path)), b)
    assert b.partial == []

    def missing(*a, **k):
        raise UpstreamError("known-object index missing")

    monkeypatch.setattr(known, "near", missing)
    assert blink.known_for_epochs(epochs, 1.0, 2.0, 20, Cache(str(tmp_path)), b) == []
    assert b.partial == ["known objects failed"]


def test_spectrum_reason():
    assert obj.spectrum_reason([{"um": 1.0}], Counter(saturated=3)) is None
    assert obj.spectrum_reason([], Counter(saturated=2, other=1)) == "saturated"  # R Draconis
    assert obj.spectrum_reason([], Counter(other=1)) == "no_data"
    assert obj.spectrum_reason([], Counter()) == "no_data"


def test_spectrum_points_counts_saturated_drops(world, monkeypatch):
    images = search.search(STAR_RA, STAR_DEC, cache=world["cache"]).of_detector(1)
    l2s = cutouts.fetch_many(images, STAR_RA, STAR_DEC, cutouts.tier_for_field(5), world["cache"], variance=True)
    nan = obj.aperture_photometry(l2s[0].data, 50.0, 50.0, l2s[0].wcs)
    nan.flux_ujy[:] = np.nan
    monkeypatch.setattr(obj, "aperture_photometry", lambda *a, **k: nan)  # no good pixels anywhere
    monkeypatch.setattr(obj, "is_saturated_core", lambda flags, x, y: True)
    monkeypatch.setattr(obj, "wing_photometry", lambda *a, **k: None)  # and no usable wings either
    dropped = Counter()
    assert obj.spectrum_points(images, l2s, None, STAR_RA, STAR_DEC, dropped=dropped) == []
    assert dropped == Counter(saturated=6)
    assert obj.spectrum_reason([], dropped) == "saturated"


def test_measure_detectors_prefer_targets_json(tmp_path, monkeypatch):
    targets = json.loads((REPO / "scripts/validate/data/targets.json").read_text())
    for t in targets["targets"]:
        if t["slug"] == "rdra":
            t["dets"] = [3, 4]
    path = tmp_path / "targets.json"
    path.write_text(json.dumps(targets))
    monkeypatch.setenv("SKYSHIFT_TARGETS", str(path))
    by_slug = {m["slug"]: m for m in obj._builtin_targets()}
    assert by_slug["rdra"]["dets"] == (3, 4) and obj.measure_detectors(by_slug["rdra"]) == (3, 4)
    # no `dets`: obj.MEASURE_DETECTORS, else any detector
    assert by_slug["wise0855"]["dets"] is None and obj.measure_detectors(by_slug["wise0855"]) == (5, 6)
    assert obj.measure_detectors(by_slug["barnard"]) is None and obj.measure_detectors(None) is None


def test_measured_layer_gets_the_photos_of_the_frames_and_never_fails_them(world, monkeypatch):
    from skyshift_server import measured
    from skyshift_server.budget import Budget

    monkeypatch.setenv("SKYSHIFT_MEASURED", "1")
    seen = []

    def fake_load(ra, dec, r_deg, keys, **kw):
        seen.append((ra, dec, r_deg, list(keys)))
        return measured.Loaded(reason="not_processed")

    monkeypatch.setattr(measured, "load", fake_load)
    fr = blink.build_frames(STAR_RA, STAR_DEC, 5, "months", 1, cache=world["cache"], per_visit=None)
    assert fr.meta["measured"] is None and fr.meta["measured_reason"] == "not_processed"
    assert seen[0][3] == [f"2025W18_1B_{i:04d}_1D1" for i in range(6)]
    assert fr.epoch_keys == {"d1-v1": [f"2025W18_1B_{i:04d}_1D1" for i in range(3)],
                             "d1-v2": [f"2025W18_1B_{i:04d}_1D1" for i in range(3, 6)]}
    hours = blink.build_frames(STAR_RA, STAR_DEC, 20, "hours", 1, cache=world["cache"])
    assert all(hours.epoch_keys[e["id"]] == [e["id"][3:] + "D1"] for e in hours.meta["epochs"])
    # a failure (or a timeout) gives null + "unavailable" and a partial answer (not cached)
    monkeypatch.setattr(measured, "load", lambda *a, **k: measured.Loaded(reason="unavailable", error=True))
    fr = blink.build_frames(STAR_RA, STAR_DEC, 5, "months", 1, cache=world["cache"], budget=Budget(30))
    assert fr.meta["measured"] is None and fr.meta["measured_reason"] == "unavailable"
    assert fr.partial == ["measured layer unavailable"]

    def boom(*a, **k):
        raise RuntimeError("bucket on fire")

    monkeypatch.setattr(measured, "load", boom)
    fr = blink.build_frames(STAR_RA, STAR_DEC, 5, "months", 1, cache=world["cache"], budget=Budget(30))
    assert fr.meta["measured_reason"] == "unavailable" and fr.meta["epochs"]
    # off: no fields at all (read as "not computed")
    monkeypatch.setenv("SKYSHIFT_MEASURED", "0")
    fr = blink.build_frames(STAR_RA, STAR_DEC, 5, "months", 1, cache=world["cache"])
    assert "measured" not in fr.meta
