"""Search fallback (search_fallback.py): when IRSA's SIA fails, photos come from SkyShift's photo lists
and saved searches. Offline: a fake failing SIA, a small synthetic photo list in a local data root."""

import json
import time

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from skyshift_server import app as app_mod
from skyshift_server import blink, measured, search, search_fallback
from skyshift_server.budget import Budget
from skyshift_server.cache import Cache
from skyshift_server.errors import UpstreamError

from conftest import sia_row

S3 = "https://nasa-irsa-spherex.s3.us-east-1.amazonaws.com/"
IRSA = "https://irsa.ipac.caltech.edu/ibe/data/spherex/"
HALF = 113.0 / 86400.0 / 2.0
REAL_QUERY_SIA = search._query_sia


def square(ra0: float, dec0: float, half: float) -> str:
    """A footprint 2*half deg on a side around (ra0, dec0), as IRSA's s_region writes it (closed)."""
    c = np.cos(np.deg2rad(dec0))
    v = [(ra0 + half / c, dec0 - half), (ra0 - half / c, dec0 - half), (ra0 - half / c, dec0 + half),
         (ra0 + half / c, dec0 + half)]
    v.append(v[0])
    return "POLYGON ICRS " + " ".join(f"{r} {d}" for r, d in v)


def photo(obs_id, det, mjd, ra0, dec0, collection="spherex_dr1", half=1.7):
    key = f"{collection[8:]}/level2/{obs_id[:10]}/l2b-v27/{det}/level2_{obs_id}D{det}_spx.fits"
    return {"obs_id": obs_id, "collection": collection, "t_min": mjd - HALF, "t_max": mjd + HALF,
            "s_ra": ra0, "s_dec": dec0, "s_region": square(ra0, dec0, half), "size_bytes": 65_000_000,
            "detector": np.int8(det), "s3_key": key, "mjd": mjd, "url": S3 + key,
            "session": np.int32(1), "visit": np.int16(1)}


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Data root with one zone (box 120-130, 15-25) and its photo list; a cache; SIA that fails."""
    root = tmp_path / "data"
    (root / "photolists" / "zones").mkdir(parents=True)
    (root / "skyrun").mkdir()
    (root / "skyrun" / "progress.json").write_text(json.dumps({"zones": [
        {"name": "zz", "ra": [120.0, 130.0], "dec": [15.0, 25.0], "state": "extracted"}]}))
    (root / "results" / "t_star").mkdir(parents=True)  # a target zone, box from its summary.json only
    (root / "results" / "t_star" / "summary.json").write_text(json.dumps(
        {"zone": {"name": "t_star", "ra": [269.0, 270.0], "dec": [4.0, 5.0]}, "steps": {"photolist": {}}}))
    rows = [
        photo("2025W17_1A_0001_1", 2, 60800.0, 125.0, 20.0),
        photo("2025W17_1A_0001_1", 5, 60800.0, 125.0, 20.0),
        photo("2025W17_1A_0002_1", 2, 60800.0015, 125.0, 20.0),
        photo("2026W05_1A_0003_1", 2, 60980.0, 125.2, 20.1, collection="spherex_qr3"),
        photo("2025W17_1A_0009_1", 2, 60800.01, 129.5, 24.5),  # in the zone, misses (125, 20)
    ]
    pd.DataFrame(rows).to_parquet(root / "photolists" / "zones" / "zz.parquet")
    pd.DataFrame([photo("2025W30_2B_0001_1", 3, 60900.0, 269.5, 4.5)]).to_parquet(
        root / "photolists" / "zones" / "t_star.parquet")
    pd.DataFrame([photo("2025W30_2B_0002_1", 3, 60900.0, 10.0, 10.0)]).to_parquet(
        root / "photolists" / "zones" / "nobox.parquet")  # no box anywhere: never used
    monkeypatch.setattr(measured, "DATA_ROOT", str(root))
    monkeypatch.setenv("SKYSHIFT_SEARCH_FALLBACK", "1")
    measured.reset()
    search_fallback.reset()
    search._sia.record(True)
    search._recent.clear()
    calls = []

    def failing_sia(ra, dec, collection):
        calls.append(collection)
        raise UpstreamError(f"IRSA SIA search failed for {collection}: HTTP Code: 502 Proxy Error")

    monkeypatch.setattr(search, "_query_sia", failing_sia)
    yield {"root": root, "cache": Cache(str(tmp_path / "cache")), "calls": calls}
    measured.reset()
    search_fallback.reset()
    search._sia.record(True)
    search._recent.clear()


def test_footprint_test_handles_ra_wrap_and_poles():
    polar = "POLYGON ICRS 0 88 90 88 180 88 270 88 0 88"  # a photo on the north pole
    regions = [square(0.2, 10.0, 1.0), polar, square(50.0, -30.0, 1.0)]
    assert search_fallback.contains(regions, 359.5, 10.0).tolist() == [True, False, False]
    assert search_fallback.contains(regions, 123.0, 89.5).tolist() == [False, True, False]
    assert search_fallback.contains(regions, 45.0, 87.5).tolist() == [False, False, False]
    assert search_fallback.contains(regions, 51.5, -30.0).tolist() == [False, False, False]
    assert search_fallback.contains(["nonsense"], 1.0, 1.0).tolist() == [False]


def test_failing_sia_falls_back_to_the_photo_list(world):
    res = search.search(125.0, 20.0, cache=world["cache"])
    assert "spherex_dr1" in world["calls"]  # IRSA was asked first
    assert res.note == search_fallback.NOTE_LISTS
    assert res.archive_checked == time.strftime("%Y-%m-%d", time.gmtime())  # the list's date
    # the same rows search.search makes from IRSA: footprint test, detector, times, URLs, visits
    assert [im.key for im in res.images] == ["2025W17_1A_0001_1D2", "2025W17_1A_0001_1D5",
                                             "2025W17_1A_0002_1D2", "2026W05_1A_0003_1D2"]
    im = res.images[0]
    assert im.det == 2 and im.collection == "spherex_dr1"
    assert im.mjd == pytest.approx(60800.0) and im.t_min == pytest.approx(60800.0 - HALF)
    key = "dr1/level2/2025W17_1A/l2b-v27/2/level2_2025W17_1A_0001_1D2_spx.fits"
    assert im.s3_url == S3 + key and im.access_url == IRSA + key
    assert [i.visit for i in res.images] == [1, 1, 1, 2] and [i.session for i in res.images] == [1, 1, 1, 2]
    expected = search.rows_to_images([sia_row(i.obs_id, i.det, i.mjd, collection=i.collection)
                                      for i in res.images])
    assert [(i.visit, i.session, i.det, i.mjd) for i in res.images] == \
        [(i.visit, i.session, i.det, i.mjd) for i in expected]
    # not saved as the day's IRSA search
    assert world["cache"].get(f"search/{search.pos_key(125.0, 20.0)}/{search.today()}.json") is None


def test_after_a_failure_irsa_is_asked_once_and_results_are_shared(world, monkeypatch):
    from astroquery.ipac.irsa import Irsa

    calls = []

    def proxy_error(pos, collection):  # the real _query_sia and its retries, IRSA answering 502
        calls.append(collection)
        raise RuntimeError("HTTP Code: 502 Proxy Error")

    monkeypatch.setattr(search, "_query_sia", REAL_QUERY_SIA)
    monkeypatch.setattr(Irsa, "query_sia", proxy_error)
    monkeypatch.setattr(search.time, "sleep", lambda s: None)
    res = search.search(125.0, 20.0, cache=world["cache"])
    assert res.note == search_fallback.NOTE_LISTS and search._sia.down()
    assert calls.count("spherex_dr1") == search.SIA_TRIES  # the usual retries the first time
    calls.clear()
    search.search(125.0, 20.0, cache=world["cache"])  # frames then object at one spot: shared
    assert calls == []
    search.search(125.1, 20.0, cache=world["cache"])  # another spot while IRSA is down: one try
    assert calls.count("spherex_dr1") == 1 and calls.count("spherex_qr3") <= 1


def test_slow_sia_falls_back_and_its_late_answer_is_cached(world, monkeypatch):
    import threading

    gate = threading.Event()

    def slow(ra, dec, collection):
        gate.wait(5)
        return [sia_row("A_1", 2, 60900.0)] if collection == "spherex_dr1" else []

    monkeypatch.setattr(search, "_query_sia", slow)
    monkeypatch.setattr(search, "SIA_WAIT_S", 0.2)
    res = search.search(125.0, 20.0, cache=world["cache"])
    assert res.note == search_fallback.NOTE_LISTS and search._sia.down()
    gate.set()
    key = f"search/{search.pos_key(125.0, 20.0)}/{search.today()}.json"
    for _ in range(50):
        if world["cache"].get(key) is not None:
            break
        time.sleep(0.05)
    assert json.loads(world["cache"].get(key))["images"][0]["obs_id"] == "A_1"
    for _ in range(50):  # the late answer closes the breaker
        if not search._sia.down():
            break
        time.sleep(0.05)
    assert not search._sia.down()


def test_target_zone_box_from_its_summary(world):
    res = search.search(269.5, 4.5, cache=world["cache"])
    assert [im.key for im in res.images] == ["2025W30_2B_0001_1D3"] and res.note


def test_outside_every_zone_the_error_says_so(world):
    with pytest.raises(UpstreamError) as e:
        search.search(10.0, 10.0, cache=world["cache"])  # "nobox" has a list but no known box
    assert e.value.status == 502 and e.value.message == search_fallback.IRSA_DOWN
    assert "tonight's zones" in e.value.message


def _save_search(cache, ra, dec, day, images):
    cache.put(f"search/{search.pos_key(ra, dec)}/{day}.json",
              json.dumps({"ra": ra, "dec": dec, "archive_checked": day, "images": images}).encode())


def _img(obs_id, det, mjd, visit=1, session=1):
    return {"collection": "spherex_dr1", "obs_id": obs_id, "det": det, "t_min": mjd - HALF, "t_max": mjd + HALF,
            "mjd": mjd, "access_url": IRSA + obs_id, "s3_url": S3 + obs_id, "visit": visit, "session": session}


def test_saved_search_here_from_an_earlier_day_is_used_whole(world):
    _save_search(world["cache"], 125.0, 20.0, "2026-11-13", [_img("OLD_1", 2, 60800.0)])
    res = search.search(125.0, 20.0, cache=world["cache"])
    assert res.note is None and res.archive_checked == "2026-11-13"  # IRSA's own answer: complete
    assert [im.key for im in res.images] == ["OLD_1D2"]


def test_nearby_saved_search_outside_the_zones(world):
    _save_search(world["cache"], 10.03, 10.0, "2026-11-14",
                 [_img("N_2", 2, 61000.0, 9, 9), _img("N_1", 2, 60800.0, 9, 9)])
    res = search.search(10.0, 10.0, cache=world["cache"])
    assert [im.key for im in res.images] == ["N_1D2", "N_2D2"]
    assert [im.visit for im in res.images] == [1, 2]  # visit rule again, at this position
    assert res.note.startswith("search from a saved IRSA search 1.8 arcmin away")
    search_fallback.reset()
    search._recent.clear()
    with pytest.raises(UpstreamError):
        search.search(10.5, 10.0, cache=world["cache"])  # 30' away: too far


def test_frames_from_the_fallback_are_partial_and_not_cached(world, monkeypatch, tmp_path):
    """The whole route: SIA fails, frames are built from the photo list, served with no-store and a
    note in X-SkyShift-Partial, and built again next time."""
    seen = []

    def build_frames(ra, dec, fov, clock, det, *, cache=None, budget=None, **kw):
        found = search.search(ra, dec, cache=cache)
        seen.append(found)
        fr = blink.Frames({"clock": clock, "center": {"ra": ra, "dec": dec}, "pixel_arcsec": 2.05,
                           "size": [1, 1], "epochs": [], "gaia": [], "known": [],
                           "data_release": search.DATA_RELEASE, "archive_checked": found.archive_checked})
        if found.note and budget is not None:
            budget.cut(found.note)
        fr.partial = list(budget.partial)
        return fr

    monkeypatch.setattr(blink, "build_frames", build_frames)
    c = Cache(str(tmp_path / "appcache"))
    app_mod.reset_cache(c)
    try:
        client = TestClient(app_mod.app, raise_server_exceptions=False)
        for _ in range(2):
            r = client.get("/api/frames", params={"ra": 125.0, "dec": 20.0})
            assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
            assert r.headers["x-skyshift-partial"] == search_fallback.NOTE_LISTS
        assert len(seen) == 2 and seen[0].note == search_fallback.NOTE_LISTS
        r = client.get("/api/frames", params={"ra": 300.0, "dec": 60.0})
        assert r.status_code == 502 and r.json()["error"] == {"code": "upstream", "message": search_fallback.IRSA_DOWN}
    finally:
        app_mod.reset_cache(None)


def test_engine_builds_frames_and_object_from_the_fallback(world, monkeypatch):
    """The real build_frames and build_object on a photo list at the fixture cutout's star (cutouts
    from the cache, as test_blink.py): frames and a spectrum come out, and the budget carries the note."""
    from test_blink import STAR_DEC, STAR_RA, _cutout_at, MJD1, VISIT2

    from skyshift_server import cutouts, gaia, known, obj

    times = [MJD1, MJD1 + 0.0015, MJD1 + 3.0, MJD1 + VISIT2, MJD1 + VISIT2 + 0.0015, MJD1 + VISIT2 + 0.003]
    rows = [photo(f"2025W18_1B_{i:04d}_1", 1, t, STAR_RA + 0.5, STAR_DEC - 0.3) for i, t in enumerate(times)]
    world["root"].joinpath("skyrun", "progress.json").write_text(json.dumps({"zones": [
        {"name": "cyg", "ra": [304.0, 305.0], "dec": [41.0, 42.0], "state": "extracted"}]}))
    pd.DataFrame(rows).to_parquet(world["root"] / "photolists" / "zones" / "cyg.parquet")
    cache = world["cache"]
    images = search.search(STAR_RA, STAR_DEC, cache=cache).of_detector(1)
    assert len(images) == 6
    for im, t in zip(images, times):
        cache.put(cutouts.cache_key(im, STAR_RA, STAR_DEC, cutouts.SIZE_TIERS_DEG[-1]), _cutout_at(t))
    stars = json.loads((app_mod._repo_root() / "fixtures/fits/cutout_2025W18_1B_0074_2D1.gaia.json")
                       .read_text())["gaia"]
    monkeypatch.setattr(gaia, "cone", lambda ra, dec, r, cache=None: stars)
    monkeypatch.setattr(known, "near", lambda ra, dec, r, mjds, *, epoch_ids=None, cache=None: [])
    monkeypatch.setattr(cutouts, "_get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    monkeypatch.setattr(cutouts, "zodi_model", lambda im, l2, cache: np.full(l2.data.shape, 0.1, np.float32))
    monkeypatch.setenv("SKYSHIFT_PIXELS", "irsa")

    b = Budget(60, "test")
    fr = blink.build_frames(STAR_RA, STAR_DEC, 5, "months", 1, cache=cache, budget=b, measured=False)
    assert [e["id"] for e in fr.meta["epochs"]] == ["d1-v1", "d1-v2"]
    assert fr.meta["archive_checked"] == time.strftime("%Y-%m-%d", time.gmtime())
    assert fr.partial == [search_fallback.NOTE_LISTS]  # served with no-store, never cached
    b = Budget(60, "test")
    o = obj.build_object(STAR_RA, STAR_DEC, cache=cache, budget=b)
    assert o["spectrum"] and search_fallback.NOTE_LISTS in b.partial
