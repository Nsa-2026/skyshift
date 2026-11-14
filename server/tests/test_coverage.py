"""Frames prefer photos that cover the whole field (coverage.py and blink's selection). Offline:
footprints are synthetic s_region squares, the search and every cutout are canned."""

import json

import numpy as np
import pytest
from astropy.table import Table

from skyshift_server import blink, coverage, cutouts, search, search_fallback
from skyshift_server.cache import Cache
from skyshift_server.errors import UpstreamError

from conftest import sia_row
from test_blink import world  # noqa: F401 - the fixture: canned Gaia, known objects, no network

REAL_QUERY_SIA = search._query_sia
RA, DEC = 125.0, 20.0
HALF = 1.7  # a detector is 3.49 deg a side


def square(ra0: float, dec0: float, half: float = HALF) -> str:
    """A footprint 2*half deg on a side around (ra0, dec0), its east and west edges meridians (closed)."""
    c = np.cos(np.deg2rad(dec0))
    v = [(ra0 + half / c, dec0 - half), (ra0 - half / c, dec0 - half), (ra0 - half / c, dec0 + half),
         (ra0 + half / c, dec0 + half)]
    v.append(v[0])
    return "POLYGON ICRS " + " ".join(f"{r} {d}" for r, d in v)


def edge_at(ra_edge: float, dec0: float = DEC, east: bool = True) -> str:
    """A footprint whose west (east=True: it lies east of the edge) or east edge is the meridian ra_edge."""
    c = np.cos(np.deg2rad(dec0))
    return square(ra_edge + (HALF / c if east else -HALF / c), dec0)


FULL = square(RA, DEC)
HALF_E = edge_at(RA, east=True)  # the field's centre on the detector's west edge: east half covered
HALF_W = edge_at(RA, east=False)


def part(f: float) -> str:
    """A footprint covering share f of the 20' hours field at (RA, DEC): its west edge a meridian."""
    side = coverage.field_side_deg("hours", 20)
    return edge_at(RA + side * (0.5 - f) / np.cos(np.deg2rad(DEC)), east=True)


def im(obs_id: str, mjd: float, s_region: str | None, det: int = 2, visit: int = 1, session: int = 1):
    return search.Image("spherex_dr1", obs_id, det, mjd, mjd, mjd, "", None, visit=visit, session=session,
                        s_region=s_region)


def cover_of(images, clock="hours", fov=20):
    return coverage.for_frames(images, RA, DEC, clock, fov)


# --- coverage of one field -------------------------------------------------------------------

def test_field_side_matches_the_frame_grid():
    assert coverage.field_side_deg("months", 5) == pytest.approx(146 * 6.15 / 3 / 3600)
    assert coverage.field_side_deg("hours", 60) == pytest.approx(293 * 12.3 / 3600, rel=1e-3)


def test_fraction_of_the_field_inside_a_footprint():
    c = np.cos(np.deg2rad(DEC))
    ims = [im("full", 0, FULL), im("east", 0, HALF_E), im("west", 0, HALF_W),
           im("quarter", 0, part(0.25)),
           im("off", 0, square(RA + 5.0, DEC)), im("none", 0, None), im("junk", 0, "POLYGON ICRS 1 2")]
    cov = cover_of(ims)
    got = {i.obs_id: cov.frac(i) for i in ims}
    assert got["full"] == 1.0 and got["off"] == 0.0
    assert got["east"] == pytest.approx(0.5, abs=0.01) and got["west"] == pytest.approx(0.5, abs=0.01)
    assert got["quarter"] == pytest.approx(0.25, abs=0.02)
    assert got["none"] == 1.0 and not cov.known(ims[5]) and not cov.known(ims[6])  # unknown: as before
    assert cov.union([ims[1], ims[2]]) == pytest.approx(1.0) and cov.union([ims[1], ims[3]]) == pytest.approx(0.5, abs=0.01)
    assert cov.gain([ims[1]], ims[2]) == pytest.approx(0.5, abs=0.01) and cov.gain([ims[1]], ims[3]) == 0.0
    # the same footprint is nearly whole in a 5' field 2' inside its edge, and half in a 60' one
    near = im("near", 0, edge_at(RA - 2.0 / 60 / c, east=True))
    assert coverage.for_frames([near], RA, DEC, "months", 5).frac(near) > 0.85
    assert coverage.for_frames([near], RA, DEC, "hours", 60).frac(near) < 0.6


def test_coverage_across_ra_zero_and_near_the_pole():
    a = im("wrap", 0, edge_at(0.0, 10.0, east=True))  # west edge on RA 0, field centred there
    assert coverage.for_frames([a], 0.0, 10.0, "hours", 20).frac(a) == pytest.approx(0.5, abs=0.01)
    assert coverage.for_frames([a], 359.99, 10.0, "hours", 20).frac(a) < 0.5
    polar = im("pole", 0, "POLYGON ICRS 0 88 90 88 180 88 270 88 0 88")
    assert coverage.for_frames([polar], 123.0, 89.5, "hours", 60).frac(polar) == 1.0
    c88, s88 = np.cos(np.deg2rad(88.0)), np.sin(np.deg2rad(88.0))
    edge_dec = np.rad2deg(np.arcsin(s88 / np.sqrt(c88**2 / 2 + s88**2)))  # the edge's middle, at RA 45
    assert coverage.for_frames([polar], 45.0, edge_dec, "hours", 20).frac(polar) == pytest.approx(0.5, abs=0.02)
    assert coverage.for_frames([polar], 45.0, 88.0, "hours", 20).frac(polar) == 0.0


def test_region_text_reads_strings_and_dali_polygons():
    assert search.region_text(FULL) == FULL
    assert search.region_text(np.array([1.0, 2.0, 3.0, 2.0, 2.0, 3.0])) == "POLYGON ICRS 1.0 2.0 3.0 2.0 2.0 3.0"
    assert search.region_text(None) is None and search.region_text("  ") is None
    assert search.region_text(np.ma.masked) is None


# --- months: each live stack -------------------------------------------------------------------

def test_months_prefers_exposures_that_cover_the_field():
    # 10 exposures; the first two and the last two have the centre near the detector's edge
    regions = [HALF_E, HALF_W] + [FULL] * 6 + [HALF_W, HALF_E]
    ims = [im(f"A{k}", 60900.0 + k, r) for k, r in enumerate(regions)]
    old = blink.pick_months_images(ims, 4)
    assert [i.obs_id for i in old] == ["A0", "A3", "A6", "A9"]  # by time alone: two half frames
    new = blink.pick_months_images(ims, 4, cover_of(ims, "months", 5))
    assert [i.obs_id for i in new] == ["A2", "A4", "A5", "A7"]  # spread over the full ones only
    assert blink.pick_months_images(ims, None, cover_of(ims, "months", 5)) == ims  # precompute: all


def test_months_fills_the_field_when_too_few_cover_it():
    # no exposure covers the field; time alone would stack three east halves
    regions = [HALF_E, HALF_W, HALF_W, HALF_E, HALF_W, HALF_W, HALF_E]
    ims = [im(f"B{k}", 60900.0 + k, r) for k, r in enumerate(regions)]
    cov = cover_of(ims, "months", 5)
    assert cov.union(blink.pick_months_images(ims, 3)) == pytest.approx(0.5, abs=0.01)
    got = blink.pick_months_images(ims, 3, cov)
    assert len(got) == 3 and cov.union(got) == pytest.approx(1.0)
    assert [i.mjd for i in got] == sorted(i.mjd for i in got)
    # one full exposure and halves: the full one, then halves spread in time
    ims[3] = im("B3", 60903.0, FULL)
    got = blink.pick_months_images(ims, 3, cover_of(ims, "months", 5))
    assert "B3" in [i.obs_id for i in got] and [i.obs_id for i in got][0] == "B0" and got[-1].obs_id == "B6"


def test_months_unknown_footprints_keep_the_old_choice():
    ims = [im(f"C{k}", 60900.0 + k, None) for k in range(9)]
    assert blink.pick_months_images(ims, 4, cover_of(ims, "months", 5)) == blink.pick_months_images(ims, 4)


# --- hours: visit, sessions, exposures --------------------------------------------------------

def test_hours_visit_counts_exposures_that_cover_the_field():
    v1 = [im(f"V1_{k}", 60900.0 + k, part(0.6) if k % 3 else FULL, visit=1) for k in range(12)]  # 4 full
    v2 = [im(f"V2_{k}", 61080.0 + k, FULL, visit=2) for k in range(6)]
    v3 = [im(f"V3_{k}", 61260.0 + k, part(0.25), visit=3) for k in range(20)]
    ims = v1 + v2 + v3
    assert blink.best_hours_visit(ims) == 3  # most exposures
    assert blink.best_hours_visit(ims, cover_of(ims)) == 1  # most covering half: 12
    ims = v1[:6] + v2 + v3
    assert blink.best_hours_visit(ims, cover_of(ims)) == 2  # 6 and 6 covering half: more of them full


def _session(s: int, regions: list[str | None], start: float = 60900.0, visit: int = 1):
    return [im(f"S{s:02d}_{k}", start + s * 1.5 + k * 2.1 / 1440, r, visit=visit, session=s)
            for k, r in enumerate(regions)]


def test_hours_live_picks_sessions_that_cover_the_field():
    # six sessions of two; 0, 2 and 5 have the centre near the edge
    ims = []
    for s in range(6):
        ims += _session(s, [HALF_E, HALF_E] if s in (0, 2, 5) else [FULL, FULL])
    old = blink.pick_hours_live(ims, 6, 2)
    assert {i.session for i in old} == {0, 2, 5}  # first, middle, last: every frame half empty
    new = blink.pick_hours_live(ims, 6, 2, cover=cover_of(ims))
    assert [i.obs_id for i in new] == ["S01_0", "S01_1", "S03_0", "S03_1", "S04_0", "S04_1"]


def test_hours_drops_poor_exposures_when_the_session_has_better_ones():
    poor = part(0.25)
    ims = (_session(0, [poor, FULL, FULL, poor]) + _session(1, [poor, poor]) + _session(2, [part(0.6), FULL])
           + _session(3, [FULL]) + _session(4, [FULL, FULL]))
    cov = cover_of(ims)
    assert [[i.obs_id for i in r] for r in blink.hours_runs(ims, cov)] == [
        ["S00_1", "S00_2"], ["S01_0", "S01_1"], ["S02_0", "S02_1"], ["S03_0"], ["S04_0", "S04_1"]]
    # precompute: every session that has a frame covering half, without the poor exposures
    assert [i.obs_id for i in blink.pick_hours_images(ims, cover=cov)] == [
        "S00_1", "S00_2", "S02_0", "S02_1", "S03_0", "S04_0", "S04_1"]
    assert len(blink.pick_hours_images(ims)) == len(ims)  # without coverage: as before
    # live, 4 frames: runs of two whose exposures cover >= 90%, the first and the last
    assert [i.obs_id for i in blink.pick_hours_live(ims, 4, 2)] == ["S00_0", "S00_1", "S04_0", "S04_1"]
    assert [i.obs_id for i in blink.pick_hours_live(ims, 4, 2, cover=cov)] == ["S00_1", "S00_2", "S04_0", "S04_1"]


def test_hours_keeps_partial_frames_when_nothing_is_better():
    ims = []
    for s in range(5):
        ims += _session(s, [part(0.6), part(0.7)])
    cov = cover_of(ims)
    assert blink.pick_hours_live(ims, 6, 2, cover=cov) == blink.pick_hours_live(ims, 6, 2)
    poor = part(0.25)
    ims = []
    for s in range(5):
        ims += _session(s, [poor, poor])
    cov = cover_of(ims)
    assert len(blink.pick_hours_live(ims, 6, 2, cover=cov)) == 6
    assert blink.pick_hours_images(ims, cover=cov) == blink.pick_hours_images(ims)


def test_hours_unknown_footprints_keep_the_old_choice():
    ims = []
    for s, n in enumerate([4, 1, 3, 1, 2, 5, 1, 3, 2, 1, 4, 3]):
        ims += _session(s, [None] * n)
    cov = cover_of(ims)
    assert blink.pick_hours_live(ims, 6, 2, cover=cov) == blink.pick_hours_live(ims, 6, 2)
    assert blink.pick_hours_images(ims, cover=cov) == blink.pick_hours_images(ims)
    assert blink.best_hours_visit(ims, cov) == blink.best_hours_visit(ims)


# --- search keeps the footprints ----------------------------------------------------------------

def test_sia_rows_keep_s_region(tmp_path, monkeypatch):
    from astroquery.ipac.irsa import Irsa

    row = sia_row("A_1", 2, 60900.0)
    t = Table(rows=[[row[k] for k in row] + [FULL]], names=list(row) + ["s_region"])
    monkeypatch.setattr(search, "_query_sia", REAL_QUERY_SIA)
    monkeypatch.setattr(Irsa, "query_sia", lambda pos, collection: t if collection == "spherex_dr1" else t[:0])
    res = search.search(RA, DEC, cache=Cache(str(tmp_path)))
    assert res.images[0].s_region == FULL
    saved = json.loads(Cache(str(tmp_path)).get(f"search/{search.pos_key(RA, DEC)}/{search.today()}.json"))
    assert saved["footprints"] is True and saved["images"][0]["s_region"] == FULL


def _legacy(cache: Cache) -> None:
    img = {"collection": "spherex_dr1", "obs_id": "OLD_1", "det": 2, "t_min": 60900.0, "t_max": 60900.001,
           "mjd": 60900.0005, "access_url": "", "s3_url": None, "visit": 1, "session": 1}
    cache.put(f"search/{search.pos_key(RA, DEC)}/{search.today()}.json",
              json.dumps({"ra": RA, "dec": DEC, "archive_checked": search.today(), "images": [img]}).encode())


@pytest.mark.parametrize("fallback", ["0", "1"])
def test_todays_search_without_footprints_is_asked_again(tmp_path, monkeypatch, fallback):
    monkeypatch.setenv("SKYSHIFT_SEARCH_FALLBACK", fallback)
    monkeypatch.setattr(search, "_recent", {})
    monkeypatch.setattr(search, "_sia", search._Health())
    cache = Cache(str(tmp_path))
    calls = []

    def fake(ra, dec, collection):
        calls.append(collection)
        return [sia_row("NEW_1", 2, 60900.0, s_region=FULL)] if collection == "spherex_dr1" else []

    monkeypatch.setattr(search, "_query_sia", fake)
    _legacy(cache)
    assert [i.obs_id for i in search.search(RA, DEC, cache=cache).images] == ["NEW_1"]
    calls.clear()
    assert search.search(RA, DEC, cache=cache).images[0].s_region == FULL and calls == []  # saved, with footprints

    def failing(ra, dec, collection):
        calls.append(collection)
        raise UpstreamError("IRSA SIA search failed: HTTP Code: 502 Proxy Error")

    monkeypatch.setattr(search, "_query_sia", failing)
    monkeypatch.setattr(search_fallback, "find", lambda *a, **k: pytest.fail("IRSA's own answer of today first"))
    _legacy(cache)
    res = search.search(RA, DEC, cache=cache)
    assert [i.obs_id for i in res.images] == ["OLD_1"] and res.note is None  # IRSA's own answer: complete
    assert "spherex_dr1" in calls
    if fallback == "1":  # IRSA just failed: today's saved search at once, no wait
        calls.clear()
        assert [i.obs_id for i in search.search(RA, DEC, cache=cache).images] == ["OLD_1"] and calls == []


def test_photo_list_rows_keep_s_region():
    row = search_fallback.sia_row("A_1", "spherex_dr1", 2, 60900.0, 60900.001, "dr1/x.fits", FULL)
    assert search.rows_to_images([row])[0].s_region == FULL


# --- end to end ----------------------------------------------------------------------------------

def test_build_frames_shows_the_exposures_that_cover_the_field(world, monkeypatch):
    """build_frames on canned rows with footprints (the cutouts are all the fixture's): the live
    hours clock takes the runs that cover the field, the months stack drops the edge exposures."""
    from test_blink import MJD1, STAR_DEC, STAR_RA, _cutout_at

    c = np.cos(np.deg2rad(STAR_DEC))
    full, half = square(STAR_RA, STAR_DEC), square(STAR_RA + HALF / c, STAR_DEC)
    plan = [(0, [half] * 3), (1, [full]), (2, [full] * 3), (3, [full] * 3)]
    rows = [sia_row(f"S{s:02d}_{k}", 1, MJD1 + s * 1.5 + k * 2.1 / 1440, s_region=r)
            for s, regions in plan for k, r in enumerate(regions)]
    monkeypatch.setattr(search, "_query_sia", lambda ra, dec, coll: rows if coll == "spherex_dr1" else [])
    cache = world["cache"]
    images = search.search(STAR_RA, STAR_DEC, cache=cache, refresh=True).of_detector(1)
    for i in images:
        cache.put(cutouts.cache_key(i, STAR_RA, STAR_DEC, cutouts.SIZE_TIERS_DEG[-1]), _cutout_at(i.mjd))
    live = blink.build_frames(STAR_RA, STAR_DEC, 20, "hours", 1, cache=cache, hours_frames=4, measured=False)
    assert [e["id"] for e in live.meta["epochs"]] == ["d1-S02_0", "d1-S02_1", "d1-S03_0", "d1-S03_1"]
    months = blink.build_frames(STAR_RA, STAR_DEC, 5, "months", 1, cache=cache, per_visit=4, measured=False)
    (ep,) = months.meta["epochs"]
    assert ep["n_exposures"] == 4 and ep["mjd_start"] > MJD1 + 1.4  # the half frames of session 0 are left out
    full_stack = blink.build_frames(STAR_RA, STAR_DEC, 5, "months", 1, cache=cache, per_visit=None, measured=False)
    assert full_stack.meta["epochs"][0]["n_exposures"] == 10  # precompute: every exposure
