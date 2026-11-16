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
    dots._SKY.clear(), dots._SKY_RED.clear(), dots._SKY_REF.clear(), dots._REFCAT.clear()
    yield
    dots._SKY.clear(), dots._SKY_RED.clear(), dots._SKY_REF.clear(), dots._REFCAT.clear()


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


# --- the red layer (AllWISE red sources with no Gaia target) ---------------------------------------
def _allwise(n=60, seed=5, ra0=10.0, dec0=5.0):
    rng = np.random.default_rng(seed)
    w1 = rng.uniform(10, 17, n)
    return pd.DataFrame({"designation": [f"J{i:06d}" for i in range(n)], "ra": ra0 + rng.uniform(0, 0.5, n),
                         "dec": dec0 + rng.uniform(0, 0.5, n), "w1mpro": w1, "w2mpro": w1 - rng.uniform(-0.5, 2.5, n),
                         "w2snr": rng.uniform(1, 30, n)})


def test_red_targets_rule_dedup_and_order():
    aw = _allwise()
    aw.loc[0, ["w1mpro", "w2mpro", "w2snr"]] = [12.0, 10.0, 20.0]  # red, bright, has a Gaia target 1" away
    aw.loc[1, ["w1mpro", "w2mpro", "w2snr"]] = [12.0, 10.0, 2.0]   # red but W2 S/N < 3
    aw.loc[2, ["w1mpro", "w2mpro", "w2snr"]] = [12.0, 11.7, 20.0]  # W1 - W2 = 0.3: not red
    aw.loc[3, ["w1mpro", "w2mpro", "w2snr"]] = [16.2, 14.0, 20.0]  # W1 >= 16
    aw.loc[4, ["w1mpro", "w2mpro", "w2snr"]] = [14.0, 10.0, 20.0]  # the reddest: first
    gaia = pd.DataFrame({"ref_id": ["gaia:1", "gaia:2"],
                         "ra": [aw.loc[0, "ra"] + 1.0 / 3600 / np.cos(np.deg2rad(aw.loc[0, "dec"])), 50.0],
                         "dec": [aw.loc[0, "dec"], 50.0], "epoch_mjd": C.ALLWISE_EPOCH_MJD, "pmra": 0.0,
                         "pmdec": 0.0, "parallax": np.nan}).astype(FORCED_COLUMNS)
    df, st = forced_sky.red_targets(aw, gaia, cap=None)
    ids = set(df["ref_id"])
    assert "allwise:J000000" not in ids and "allwise:J000001" not in ids and "allwise:J000002" not in ids
    assert "allwise:J000003" not in ids and df["ref_id"].iloc[0] == "allwise:J000004"
    assert list(df.columns) == list(FORCED_COLUMNS) and (df["epoch_mjd"] == C.ALLWISE_EPOCH_MJD).all()
    c = aw.set_index("designation").loc[[r.split(":")[1] for r in df["ref_id"]]]
    assert ((c["w1mpro"] < 16) & (c["w1mpro"] - c["w2mpro"] >= 0.4) & (c["w2snr"] >= 3)).all()
    assert np.all(np.diff((c["w1mpro"] - c["w2mpro"]).to_numpy()) <= 1e-12)  # reddest first
    assert st["red"] == st["no_gaia_target"] + 1 and st["rows"] == len(df)
    capped, st2 = forced_sky.red_targets(aw, gaia, cap=3)
    assert len(capped) == 3 and st2["capped"] and list(capped["ref_id"]) == list(df["ref_id"][:3])


def test_build_red_needs_the_gaia_layer_and_writes_its_manifest(tmp_path):
    store = Store(str(tmp_path))
    for p in range(12):  # nside 1: 12 partitions; the Gaia layer first
        store.write_parquet(forced_sky.partition_rel(p), pd.DataFrame(
            {c: pd.Series(dtype=t) for c, t in FORCED_COLUMNS.items()}))
    reads = []

    def reader(p):
        reads.append(p)
        return _allwise(seed=p)
    out = forced_sky.build_red(store, workers=3, nside=1, reader=reader, limit=4)
    assert out["built"] == 4 and "manifest" not in out
    out = forced_sky.build_red(store, workers=3, nside=1, reader=reader)
    assert out["built"] == 8 and len(reads) == 12 and out["manifest"]["catalogues"] == ["allwise"]
    man = store.read_json(forced_sky.manifest_rel(C.FORCED_SKY_RED_DIR))
    assert man["rows"] == sum(man["rows_per_partition"]) > 0
    df = store.read_parquet(forced_sky.partition_rel(5, C.FORCED_SKY_RED_DIR))
    assert df["ref_id"].str.startswith("allwise:").all()


def test_dots_reads_the_red_layer_next_to_the_gaia_one(tmp_path, monkeypatch):
    from skyshift_core import open_level2, pixel_to_sky
    l2 = open_level2(CUTOUT)
    t = _targets_in(l2)
    store = Store(str(tmp_path))
    _write_sky(store, t)
    _complete(store, l2)
    ny, nx = l2.data.shape
    rng = np.random.default_rng(9)
    ra, dec = pixel_to_sky(l2.wcs, rng.uniform(10, nx - 10, 40), rng.uniform(10, ny - 10, 40))
    red = pd.DataFrame({"ref_id": [f"allwise:J{i}" for i in range(40)], "ra": ra, "dec": dec,
                        "epoch_mjd": C.ALLWISE_EPOCH_MJD, "pmra": np.nan, "pmdec": np.nan,
                        "parallax": np.nan}).astype(FORCED_COLUMNS)
    pix = forced_sky.pixel_of(red["ra"], red["dec"])
    for p in np.unique(pix):
        store.write_parquet(forced_sky.partition_rel(p, C.FORCED_SKY_RED_DIR), red[pix == p].reset_index(drop=True))
    ra0, dec0, r = dots.photo_cone(l2)
    for p in forced_sky.partitions_for_cone(ra0, dec0, r):
        if not store.exists(forced_sky.partition_rel(p, C.FORCED_SKY_RED_DIR)):
            store.write_parquet(forced_sky.partition_rel(p, C.FORCED_SKY_RED_DIR), red.head(0))
    # no red manifest yet: Gaia layer only
    st, f = _forced_rows(tmp_path, None)
    assert st["forced_from"] == "sky" and not f["ref_id"].str.startswith("allwise:").any()
    store.write_json(forced_sky.manifest_rel(C.FORCED_SKY_RED_DIR), {"nside": C.FORCED_SKY_NSIDE})
    dots._SKY_RED.clear(), dots._SKY_REF.clear()
    (tmp_path / dots.dots_rel("2025W18_1B_0074_2", 1, C.zone_dots(None)[0])).unlink()
    st2, f2 = _forced_rows(tmp_path, None)
    assert st2["forced_from"] == "sky+red"
    assert f2["ref_id"].str.startswith("allwise:").sum() == 40
    assert set(f["ref_id"]) <= set(f2["ref_id"])
    dots._SKY_RED.clear(), dots._SKY_REF.clear()
    monkeypatch.setattr(C, "FORCED_SKY_RED_MODE", "off")
    (tmp_path / dots.dots_rel("2025W18_1B_0074_2", 1, C.zone_dots(None)[0])).unlink()
    st3, _ = _forced_rows(tmp_path, None)
    assert st3["forced_from"] == "sky"
