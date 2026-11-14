"""Sky-wide forced list (forced_sky.py) and its use in dots: partition lookup, footprint selection,
fallback to the zone list, and the same forced rows either way."""

import json

import numpy as np
import pandas as pd
import pytest

from skyshift_pipeline import config as C
from skyshift_pipeline import dots, forced_sky
from skyshift_pipeline.refcat import FORCED_COLUMNS
from skyshift_pipeline.store import Store

from conftest import CUTOUT


@pytest.fixture(autouse=True)
def _fresh_caches():
    for c in (dots._SKY, dots._SKY_CW, dots._SKY_REF, dots._REFCAT):
        c.clear()
    yield
    for c in (dots._SKY, dots._SKY_CW, dots._SKY_REF, dots._REFCAT):
        c.clear()


# --- partitions ----------------------------------------------------------------------------------
def test_source_id_ranges_tile_the_id_space():
    lo0, hi0 = forced_sky.source_id_range(0)
    lo1, _ = forced_sky.source_id_range(1)
    assert lo0 == 0 and hi0 == lo1 == 2 ** 49
    assert forced_sky.source_id_range(12287)[1] == 12 * 4 ** 12 * 2 ** 35  # end of the level-12 index space
    # Barnard's Star: source_id 4472832130942575872 -> nside-32 pixel of its position (RA 269.45, Dec +4.74)
    sid = 4472832130942575872
    assert int(forced_sky.pixel_of_source_id(sid)) == int(forced_sky.pixel_of(269.4486, 4.7396))
    lo, hi = forced_sky.source_id_range(int(forced_sky.pixel_of_source_id(sid)))
    assert lo <= sid < hi
    assert forced_sky.npix() == 12288 and forced_sky.area_deg2() == pytest.approx(3.357, abs=1e-3)


@pytest.mark.parametrize("ra, dec", [(122.3, 16.1), (299.5, 35.7), (0.2, -0.1), (180.0, 89.0), (45.0, -88.5)])
def test_partitions_for_cone_cover_every_point_in_the_cone(ra, dec):
    r = 2.565  # a photo's cone (half diagonal + 0.1°)
    pix = set(forced_sky.partitions_for_cone(ra, dec, r).tolist())
    rng = np.random.default_rng(1)
    # random points uniformly inside the cone (+ the pad)
    rr = np.deg2rad(r + C.FORCED_SKY_PAD_DEG) * np.sqrt(rng.uniform(0, 1, 20000))
    th = rng.uniform(0, 2 * np.pi, 20000)
    d0, a0 = np.deg2rad(dec), np.deg2rad(ra)
    dd = np.arcsin(np.sin(d0) * np.cos(rr) + np.cos(d0) * np.sin(rr) * np.cos(th))
    aa = a0 + np.arctan2(np.sin(th) * np.sin(rr) * np.cos(d0), np.cos(rr) - np.sin(d0) * np.sin(dd))
    hit = set(forced_sky.pixel_of(np.rad2deg(aa) % 360, np.rad2deg(dd)).tolist())
    assert hit <= pix
    assert len(pix) <= 20  # a photo reads ~10-14 partitions, not the sky


# --- build ---------------------------------------------------------------------------------------
def _fake_gaia_tap(rows_per_pix=50, seed=0):
    """A TAP stand-in answering gaia_adql queries with sources inside the asked source_id range."""
    calls = []

    def tap(url, adql):
        import re
        calls.append(adql)
        lo, hi = (int(v) for v in re.search(r"source_id >= (\d+) AND source_id < (\d+)", adql).groups())
        rng = np.random.default_rng(lo % 1000 + seed)
        n = rows_per_pix
        sid = np.sort(rng.integers(lo, hi, n, dtype=np.int64))
        g = rng.uniform(8, 17.4, n)
        top = re.search(r"TOP (\d+)", adql)
        df = pd.DataFrame({"source_id": sid, "ra": rng.uniform(0, 360, n), "dec": rng.uniform(-90, 90, n),
                           "pmra": rng.normal(0, 5, n), "pmdec": rng.normal(0, 5, n), "parallax": rng.uniform(0, 3, n),
                           "phot_g_mean_mag": g})
        if top:
            df = df.sort_values(["phot_g_mean_mag", "source_id"]).head(int(top.group(1)))
        return df
    return tap, calls


def test_build_is_resumable_and_writes_the_manifest_last(tmp_path):
    store = Store(str(tmp_path))
    tap, calls = _fake_gaia_tap()
    out = forced_sky.build(store, workers=4, nside=1, max_per_deg2=None, tap=tap, limit=5)
    assert out["fetched"] == 5 and "manifest" not in out
    assert not store.exists(forced_sky.manifest_rel())
    out = forced_sky.build(store, workers=4, nside=1, max_per_deg2=None, tap=tap)
    assert out["fetched"] == 7 and len(calls) == 12  # only the missing ones
    man = store.read_json(forced_sky.manifest_rel())
    assert man["nside"] == 1 and man["rows"] == 12 * 50 and man["rows_per_partition"] == [50] * 12
    df = store.read_parquet(forced_sky.partition_rel(3))
    assert list(df.columns) == list(FORCED_COLUMNS) and df["ref_id"].str.startswith("gaia:").all()
    assert forced_sky.build(store, workers=4, nside=1, max_per_deg2=None, tap=tap)["fetched"] == 0


def test_cap_keeps_the_brightest(tmp_path):
    tap, calls = _fake_gaia_tap(rows_per_pix=200)
    df, st = forced_sky.fetch_partition(7, cap=30, tap=tap)
    assert "TOP 30" in calls[-1] and "ORDER BY phot_g_mean_mag" in calls[-1]
    assert len(df) == 30 and st["capped"] and st["g_lim"] < 17.5
    df2, st2 = forced_sky.fetch_partition(7, cap=None, tap=tap)
    assert len(df2) == 200 and not st2["capped"] and "TOP" not in calls[-1]
    assert set(df["ref_id"]) <= set(df2["ref_id"])
    assert forced_sky.cap_per_partition(C.FORCED_MAX_PER_PHOTO / C.PHOTO_SIDE_DEG ** 2) == 8221


# --- dots: footprint selection, fallback, same rows ------------------------------------------------
def _targets_in(l2, n=400, seed=3):
    """Gaia-like targets scattered over (and a bit beyond) the photo."""
    from skyshift_core import pixel_to_sky
    ny, nx = l2.data.shape
    rng = np.random.default_rng(seed)
    x, y = rng.uniform(-30, nx + 30, n), rng.uniform(-30, ny + 30, n)
    ra, dec = pixel_to_sky(l2.wcs, x, y)
    return pd.DataFrame({"ref_id": [f"gaia:{i}" for i in range(n)], "ra": ra, "dec": dec,
                         "epoch_mjd": 57388.5, "pmra": rng.normal(0, 20, n), "pmdec": rng.normal(0, 20, n),
                         "parallax": np.nan}).astype(FORCED_COLUMNS)


def _write_sky(store: Store, targets: pd.DataFrame):
    pix = forced_sky.pixel_of(targets["ra"], targets["dec"])
    for p in np.unique(pix):
        store.write_parquet(forced_sky.partition_rel(p), targets[pix == p].reset_index(drop=True))
    # neighbouring partitions the photo's cone touches exist too (empty): the list is complete
    return set(int(p) for p in np.unique(pix))


def _complete(store: Store, l2):
    ra, dec, r = dots.photo_cone(l2)
    for p in forced_sky.partitions_for_cone(ra, dec, r):
        if not store.exists(forced_sky.partition_rel(p)):
            store.write_parquet(forced_sky.partition_rel(p), pd.DataFrame(
                {c: pd.Series(dtype=t) for c, t in FORCED_COLUMNS.items()}))
    store.write_json(forced_sky.manifest_rel(), {"nside": C.FORCED_SKY_NSIDE})


def _zone_around(l2, half=False):
    from skyshift_core import pixel_to_sky
    ny, nx = l2.data.shape
    ra, dec = pixel_to_sky(l2.wcs, np.array([0, nx - 1, 0, nx - 1.0]), np.array([0, 0, ny - 1, ny - 1.0]))
    w = ra.max() - ra.min()
    ra0 = ra.min() + w / 2 if half else ra.min() - 1  # half: the box starts at the photo's middle
    return C.Zone("t_forced", ra0, ra.max() + 1, dec.min() - 1, dec.max() + 1)


def _forced_rows(root, zone):
    st = dots.process_photo(str(root), "2025W18_1B_0074_2", 1, str(CUTOUT), 7, 2, zone=zone)
    assert st["status"] == "done", st
    p = root / dots.forced_rel("2025W18_1B_0074_2", 1)
    return st, (pd.read_parquet(p) if p.exists() else None)


def test_fallback_to_the_zone_list_without_a_complete_sky_list(tmp_path, monkeypatch):
    from skyshift_core import open_level2
    l2 = open_level2(CUTOUT)
    t = _targets_in(l2)
    monkeypatch.setitem(C.ZONES, "t_forced", _zone_around(l2))
    store = Store(str(tmp_path))
    store.write_parquet(dots.refcat_rel("t_forced"), t)
    _write_sky(store, t.head(10))  # partitions but no manifest: not complete, not used
    st, f = _forced_rows(tmp_path, "t_forced")
    assert st["forced_from"] == "zone" and len(f) > 100


def test_sky_list_gives_the_whole_footprint_and_the_same_rows(tmp_path, monkeypatch):
    from skyshift_core import open_level2
    l2 = open_level2(CUTOUT)
    t = _targets_in(l2)
    monkeypatch.setitem(C.ZONES, "t_forced", _zone_around(l2))
    monkeypatch.setitem(C.ZONES, "t_half", _zone_around(l2, half=True))  # cuts the photo in two
    zone_root, sky_root = tmp_path / "zone", tmp_path / "sky"
    Store(str(zone_root)).write_parquet(dots.refcat_rel("t_forced"), t)
    Store(str(zone_root)).write_parquet(dots.refcat_rel("t_half"), t)
    sky = Store(str(sky_root))
    _write_sky(sky, t)
    _complete(sky, l2)
    st_z, fz = _forced_rows(zone_root, "t_forced")
    st_s, fs = _forced_rows(sky_root, "t_half")  # the zone box no longer limits the targets
    assert st_z["forced_from"] == "zone" and st_s["forced_from"] == "sky"
    assert list(fs.columns) == list(dots.FORCED_COLUMNS)
    for c, typ in dots.FORCED_COLUMNS.items():
        assert str(fs[c].dtype) == typ, c
    assert sorted(fs["ref_id"]) == sorted(fz["ref_id"]) and len(fs) > 100
    a = fz.set_index("ref_id").sort_index()
    b = fs.set_index("ref_id").sort_index()
    for c in a.columns:
        if a[c].dtype.kind == "f":
            np.testing.assert_allclose(a[c].to_numpy(np.float64), b[c].to_numpy(np.float64), rtol=1e-5, atol=1e-6,
                                       equal_nan=True, err_msg=c)
        else:
            assert (a[c].to_numpy() == b[c].to_numpy()).all(), c
    # with the half zone list the zone path keeps only the zone part
    dots._REFCAT.clear()
    monkeypatch.setattr(C, "FORCED_SKY_MODE", "off")
    half_root = tmp_path / "half"
    Store(str(half_root)).write_parquet(dots.refcat_rel("t_half"), t)
    _, fh = _forced_rows(half_root, "t_half")
    assert 0 < len(fh) < len(fs)


def test_sky_list_without_a_zone_and_mode_off(tmp_path, monkeypatch):
    from skyshift_core import open_level2
    l2 = open_level2(CUTOUT)
    t = _targets_in(l2)
    store = Store(str(tmp_path))
    _write_sky(store, t)
    _complete(store, l2)
    st, f = _forced_rows(tmp_path, None)  # no zone at all: the sky list still applies
    assert st["forced_from"] == "sky" and len(f) > 100
    dots._SKY.clear()
    monkeypatch.setattr(C, "FORCED_SKY_MODE", "off")
    off = tmp_path / "off"
    _write_sky(Store(str(off)), t)
    _complete(Store(str(off)), l2)
    st2, f2 = _forced_rows(off, None)
    assert f2 is None and "forced_from" not in st2


def test_manifest_records_counts(tmp_path):
    store = Store(str(tmp_path))
    tap, _ = _fake_gaia_tap(rows_per_pix=40)
    forced_sky.build(store, workers=2, nside=1, max_per_deg2=None, tap=tap)
    man = json.loads((tmp_path / forced_sky.manifest_rel()).read_text())
    assert man["catalogues"] == ["gaia_dr3"] and man["gaia_g_max"] == C.FORCED_GAIA_G_MAX


# --- CatWISE-only red list ------------------------------------------------------------------------
@pytest.mark.parametrize("where", [(0, 90), (0, -90), (0.01, 10), (359.9, -40), (122, 16), (300, 35), (45, 70), (200, -60)])
def test_cw_boxes_cover_the_pixel(where):
    pix8 = int(forced_sky.pixel_of(*where, forced_sky.CW_QUERY_NSIDE))
    import astropy.units as u
    from astropy_healpix import HEALPix
    h = HEALPix(nside=forced_sky.CW_QUERY_NSIDE, order="nested")
    # points inside pix8: centres of its nside-256 descendants
    k = (256 // forced_sky.CW_QUERY_NSIDE) ** 2
    lon, lat = HEALPix(nside=256, order="nested").healpix_to_lonlat(np.arange(pix8 * k, (pix8 + 1) * k))
    ra, dec = lon.to_value(u.deg), lat.to_value(u.deg)
    assert (forced_sky.pixel_of(ra, dec, forced_sky.CW_QUERY_NSIDE) == pix8).all()
    inside = np.zeros(ra.size, bool)
    for r0, r1, d0, d1 in forced_sky.cw_boxes(pix8):
        assert 0 <= r0 < r1 <= 360 and -90 <= d0 < d1 <= 90
        inside |= (ra >= r0) & (ra < r1) & (dec >= d0) & (dec < d1)
    assert inside.all()
    assert h.npix == 12 * forced_sky.CW_QUERY_NSIDE ** 2


def _fake_catwise_tap(per_deg2=2.0, seed=0):
    import re

    def tap(url, adql):
        r0, r1, d0, d1 = (float(v) for v in re.search(
            r"ra >= ([\d.\-]+) AND ra < ([\d.\-]+) AND dec >= ([\d.\-]+) AND dec < ([\d.\-]+)", adql).groups())
        area = (r1 - r0) * (np.sin(np.deg2rad(d1)) - np.sin(np.deg2rad(d0))) * 180 / np.pi
        n = int(per_deg2 * area)
        rng = np.random.default_rng(int(r0 * 100 + d0 * 10) % 2 ** 31 + seed)
        ra = rng.uniform(r0, r1, n)
        dec = np.rad2deg(np.arcsin(rng.uniform(np.sin(np.deg2rad(d0)), np.sin(np.deg2rad(d1)), n)))
        w1 = rng.uniform(10, 15.9, n)
        return pd.DataFrame({"source_name": [f"J{a:09.5f}{d:+09.5f}" for a, d in zip(ra, dec)], "ra": ra, "dec": dec,
                             "ra_pm": ra, "dec_pm": dec, "meanobsmjd": 57170.0, "w1mpro": w1, "w1sigmpro": 0.02,
                             "w2mpro": w1 - 0.6, "w2sigmpro": 0.03, "pmra": 0.0, "pmdec": 0.0, "sigpmra": 0.01,
                             "sigpmdec": 0.01})
    return tap


def test_cw_build_dedups_against_gaia_and_caps(tmp_path):
    store = Store(str(tmp_path))
    tap = _fake_catwise_tap(per_deg2=3.0)
    pix8 = int(forced_sky.pixel_of(122.0, 16.0, forced_sky.CW_QUERY_NSIDE))
    # the raw rows of pix8, and a Gaia list with a star on top of a third of them (and an empty rest)
    raw = forced_sky.cw_fetch(pix8, tap)
    assert len(raw) > 20 and (forced_sky.pixel_of(raw["ra"], raw["dec"], forced_sky.CW_QUERY_NSIDE) == pix8).all()
    on = raw.iloc[::3]
    gaia = pd.DataFrame({"ref_id": [f"gaia:{i}" for i in range(len(on))], "ra": on["ra"].to_numpy() + 0.5 / 3600,
                         "dec": on["dec"].to_numpy(), "epoch_mjd": 57388.5, "pmra": 0.0, "pmdec": 0.0,
                         "parallax": np.nan}).astype(FORCED_COLUMNS)
    gp = forced_sky.pixel_of(gaia["ra"], gaia["dec"])
    nb = set()
    for c in forced_sky.cw_children(pix8):
        nb |= set(forced_sky.cw_neighbourhood(c))
    for p in nb:
        store.write_parquet(forced_sky.partition_rel(p), gaia[gp == p].reset_index(drop=True))
    out = forced_sky.cw_build(store, workers=2, pixels8=[pix8], max_per_deg2=None, tap=tap)
    got = pd.concat([store.read_parquet(forced_sky.partition_rel(c, C.FORCED_SKY_CW_DIR))
                     for c in forced_sky.cw_children(pix8)], ignore_index=True)
    assert out["rows"] == len(got) == len(raw) - len(on)
    assert not set(got["ref_id"]) & {"catwise:" + n for n in on["ref_id"].str[8:]}
    assert got["ref_id"].str.startswith("catwise:").all() and list(got.columns) == list(FORCED_COLUMNS)
    # rerun skips; a cap keeps the brightest in W1 per partition
    assert forced_sky.cw_build(store, workers=2, pixels8=[pix8], tap=tap)["fetched"] == 0
    capped = tmp_path / "capped"
    cs = Store(str(capped))
    forced_sky.cw_build(cs, workers=2, pixels8=[pix8], max_per_deg2=1.0, gaia_dir=C.FORCED_SKY_DIR, tap=tap)
    for c in forced_sky.cw_children(pix8):
        assert len(cs.read_parquet(forced_sky.partition_rel(c, C.FORCED_SKY_CW_DIR))) <= forced_sky.cap_per_partition(1.0)


def test_dots_reads_the_catwise_list_with_the_gaia_one(tmp_path):
    from skyshift_core import open_level2
    l2 = open_level2(CUTOUT)
    t = _targets_in(l2)
    store = Store(str(tmp_path))
    _write_sky(store, t.iloc[:200])
    _complete(store, l2)
    cw = t.iloc[200:].assign(ref_id=lambda d: "catwise:" + d["ref_id"].str[5:])
    pix = forced_sky.pixel_of(cw["ra"], cw["dec"])
    ra, dec, r = dots.photo_cone(l2)
    for p in forced_sky.partitions_for_cone(ra, dec, r):
        store.write_parquet(forced_sky.partition_rel(p, C.FORCED_SKY_CW_DIR), cw[pix == p].reset_index(drop=True))
    store.write_json(forced_sky.manifest_rel(C.FORCED_SKY_CW_DIR), {"nside": 32})
    st, f = _forced_rows(tmp_path, None)
    assert f["ref_id"].str.startswith("catwise:").sum() > 50 and f["ref_id"].str.startswith("gaia:").sum() > 50
