"""refcat: tiles, parsing of recorded TAP samples, unified schema, Gaia/CatWISE de-duplication, forced list."""

from pathlib import Path

import numpy as np
import pandas as pd
from astropy.table import Table

from skyshift_pipeline import config as C
from skyshift_pipeline import refcat as R
from skyshift_pipeline.store import Store

DATA = Path(__file__).parent / "data"


def sample(name: str) -> Table:
    return Table.read(DATA / f"refcat_{name}.ecsv")


# --- tiles ---------------------------------------------------------------------------------------
def test_tiles_cover_box_without_overlap():
    ts = R.tiles(120.0, 122.5, 14.0, 15.3, 1.0)
    assert len(ts) == 3 * 2
    assert {t.ra0 for t in ts} == {120.0, 121.0, 122.0} and max(t.ra1 for t in ts) == 122.5
    assert max(t.dec1 for t in ts) == 15.3
    area = sum((t.ra1 - t.ra0) * (t.dec1 - t.dec0) for t in ts)
    assert np.isclose(area, 2.5 * 1.3)
    assert len({t.key for t in ts}) == len(ts)


def test_query_box_adds_margin():
    z = C.ZONES["pilot"]
    ra0, ra1, dec0, dec1 = R.query_box(z, 0.1)
    assert np.isclose(dec0, z.dec_min - 0.1) and np.isclose(dec1, z.dec_max + 0.1)
    assert ra0 < z.ra_min - 0.1 and ra1 > z.ra_max + 0.1  # widened by 1/cos dec
    assert len(R.tiles(*R.query_box(z))) == 4 * 4


def test_tile_where_is_half_open():
    w = R.Tile(120.5, 121.5, 14.3, 15.3).where()
    assert "ra >= 120.5" in w and "ra < 121.5" in w and "dec < 15.3" in w


# --- parsing -------------------------------------------------------------------------------------
def test_parse_recorded_samples_unified_schema():
    g, w, m = R.parse_gaia(sample("gaia")), R.parse_catwise(sample("catwise")), R.parse_2mass(sample("2mass"))
    for df in (g, w, m):
        assert list(df.columns) == list(R.REFCAT_COLUMNS)
        assert dict(df.dtypes.astype(str)) == R.REFCAT_COLUMNS
        assert len(df) > 0 and df["ra"].notna().all() and df["dec"].notna().all()
    assert g["ref_id"].str.match(r"^gaia:\d+$").all() and (g["epoch_mjd"] == R.GAIA_EPOCH_MJD).all()
    assert g["g_mag"].lt(20).all() and g["w1_mag"].isna().all()
    assert w["ref_id"].str.startswith("catwise:J").all() and w["w1_mag"].notna().all()
    assert w["epoch_mjd"].between(55000, 59000).all()
    assert m["ref_id"].str.startswith("2mass:").all() and m["pmra"].isna().all()
    assert m["epoch_mjd"].between(50500, 51700).all() and (m["ph_qual"].str.len() == 3).all()


def test_catwise_motion_in_mas_per_year():
    t = Table({"source_name": ["J000000.00+000000.0"], "ra": [10.0], "dec": [1.0], "ra_pm": [10.0001],
               "dec_pm": [1.0001], "meanobsmjd": [57000.0], "w1mpro": [12.0], "w1sigmpro": [0.01],
               "w2mpro": [11.0], "w2sigmpro": [0.01], "pmra": [0.5], "pmdec": [-0.25], "sigpmra": [0.01], "sigpmdec": [0.02]})
    r = R.parse_catwise(t).iloc[0]
    assert r.pmra == 500.0 and r.pmdec == -250.0 and r.epoch_mjd == R.CATWISE_PM_EPOCH_MJD and r.ra == 10.0001


# --- de-duplication and forced list ---------------------------------------------------------------
def frame(cat, ra, dec, **kw):
    n = len(ra)
    out = {c: R._default(c, n) for c in R.REFCAT_COLUMNS}
    out.update({"ref_id": [f"{cat}:{i}" for i in range(n)], "cat": [cat] * n, "ra": np.asarray(ra, float),
                "dec": np.asarray(dec, float),
                "epoch_mjd": np.full(n, R.GAIA_EPOCH_MJD if cat == "gaia" else R.CATWISE_PM_EPOCH_MJD)})
    out.update({k: np.asarray(v, float) for k, v in kw.items()})
    return pd.DataFrame(out).astype(R.REFCAT_COLUMNS)


def test_catwise_dedup_within_2_arcsec():
    a = 1 / 3600
    gaia = frame("gaia", [122.0, 123.0], [16.0, 16.0], g_mag=[15, 19.5])
    cw = frame("catwise", [122.0 + 1.5 * a, 122.0 + 2.5 * a / np.cos(np.deg2rad(16)), 123.0, 124.0],
               [16.0, 16.0, 16.0 + 1.0 * a, 16.0], w1_mag=[12, 12, 12, 12])
    assert R.catwise_without_gaia(cw, gaia).tolist() == [False, True, False, True]


def test_dedup_moves_gaia_to_catwise_epoch():
    # a fast star: 5″/yr east; 0.6 yr earlier it sat 3″ west of its 2016.0 position
    gaia = frame("gaia", [122.0], [0.0], g_mag=[12], pmra=[5000.0], pmdec=[0.0])
    dt = (R.GAIA_EPOCH_MJD - R.CATWISE_PM_EPOCH_MJD) / 365.25
    cw = frame("catwise", [122.0 - 5.0 * dt / 3600], [0.0], w1_mag=[10])
    assert R.catwise_without_gaia(cw, gaia).tolist() == [False]
    assert R.catwise_without_gaia(cw, gaia.assign(pmra=0.0)).tolist() == [True]


def test_forced_list_limits_and_columns():
    a = 1 / 3600
    gaia = frame("gaia", [122.0, 122.1, 122.2], [16.0, 16.0, 16.0], g_mag=[18.9, 19.0, 19.8], parallax=[1.0, 2.0, np.nan])
    cw = frame("catwise", [122.2 + a / 2, 122.3, 122.4, 122.5], [16.0] * 4, w1_mag=[12.0, 15.9, 16.1, 14.0],
               w2_mag=[11.0, 15.0, 15.0, 13.9])
    f = R.forced_list(gaia, cw, g_max=19.0, w1_max=16.0, w1_w2_min=0.4)
    assert list(f.columns) == list(R.FORCED_COLUMNS) and dict(f.dtypes.astype(str)) == R.FORCED_COLUMNS
    # gaia:0 only (G<19); catwise:0 matched by the G=19.8 star (any G); catwise:1 kept; catwise:2 too faint; catwise:3 too blue (W1-W2 = 0.1)
    assert f["ref_id"].tolist() == ["gaia:0", "catwise:1"]
    assert f["parallax"].iloc[0] == 1.0 and np.isnan(f["parallax"].iloc[1])


def test_per_photo_counts_uniform():
    z = C.ZONES["hunt"]
    rng = np.random.default_rng(0)
    n = 100_000
    ra = rng.uniform(z.ra_min, z.ra_max, n)
    dec = np.rad2deg(np.arcsin(rng.uniform(np.sin(np.deg2rad(z.dec_min)), np.sin(np.deg2rad(z.dec_max)), n)))
    p = R.per_photo_counts(ra, dec, z)
    assert p["boxes"] > 1
    assert abs(p["mean_per_photo"] - p["max_per_photo"]) / p["mean_per_photo"] < 0.1


# --- run, offline, with a fake TAP -----------------------------------------------------------------
def test_run_writes_both_files_and_skips_cached_tiles(tmp_path):
    calls = []

    def fake_tap(url, adql):
        calls.append(adql)
        if "gaia" in adql:
            return sample("gaia")
        if "catwise" in adql:
            return sample("catwise")
        return sample("2mass")

    store = Store(str(tmp_path))
    z = C.ZONES["pilot"]
    st = R.run(store, z, workers=2, tap=fake_tap)
    n_tiles = len(R.tiles(*R.query_box(z)))
    assert len(calls) == 3 * n_tiles and st["fetched"] == 3 * n_tiles
    assert calls[0].startswith("SELECT") and "gaiadr3.gaia_source" in "".join(calls)
    full = store.read_parquet(R.refcat_rel("pilot"))
    forced = store.read_parquet(R.refcat_rel("pilot", forced=True))
    assert set(full["cat"]) == {"gaia", "catwise", "2mass"} and full["ref_id"].is_unique
    assert list(forced.columns) == list(R.FORCED_COLUMNS)
    st2 = R.run(store, z, workers=2, tap=fake_tap)
    assert st2.get("skipped") and len(calls) == 3 * n_tiles
    st3 = R.run(store, z, workers=2, tap=fake_tap, force=True)
    assert st3["fetched"] == 0 and len(calls) == 3 * n_tiles


def test_esa_failure_falls_back_to_irsa(monkeypatch):
    monkeypatch.setattr(C, "NET_RETRIES", 1)

    def tap(url, adql):
        if url == R.ESA_TAP:
            raise OSError("ESA down")
        return sample("gaia")

    df, name = R.fetch_tile("gaia", R.Tile(120.5, 121.5, 14.3, 15.3), tap)
    assert name == "IRSA Gaia" and len(df) > 0


def test_gaia_only_refcat_then_full_run_adds_the_rest(tmp_path, monkeypatch):
    calls = []

    def fake_tap(url, adql):
        calls.append(adql)
        if "gaia" in adql:
            return sample("gaia")
        if "catwise" in adql:
            return sample("catwise")
        return sample("2mass")

    store = Store(str(tmp_path))
    z = C.ZONES["pilot"]
    n_tiles = len(R.tiles(*R.query_box(z)))
    monkeypatch.setattr(C, "REFCAT_CATALOGS", ("gaia",))
    R.run(store, z, workers=2, tap=fake_tap)
    assert len(calls) == n_tiles and all("gaia" in a for a in calls)
    assert store.exists(R.refcat_rel("pilot", forced=True)) and store.exists(R.refcat_rel("pilot"))
    assert set(store.read_parquet(R.refcat_rel("pilot"))["cat"]) == {"gaia"} and R.catalogs_in(store, "pilot") == ["gaia"]
    forced = store.read_parquet(R.refcat_rel("pilot", forced=True))
    assert len(forced) > 0 and forced["ref_id"].str.startswith("gaia:").all()
    assert R.run(store, z, workers=2, tap=fake_tap).get("skipped") and len(calls) == n_tiles
    monkeypatch.setattr(C, "REFCAT_CATALOGS", ("gaia", "catwise", "2mass"))
    st = R.run(store, z, workers=2, tap=fake_tap)  # full refcat missing: reuses the Gaia tiles, adds the rest
    assert st["fetched"] == 2 * n_tiles and len(calls) == 3 * n_tiles
    assert set(store.read_parquet(R.refcat_rel("pilot"))["cat"]) == {"gaia", "catwise", "2mass"}
    assert R.catalogs_in(store, "pilot") == ["gaia", "catwise", "2mass"]
    assert R.run(store, z, workers=2, tap=fake_tap).get("skipped") and len(calls) == 3 * n_tiles


def test_ecliptic_zones_align_on_the_hunt_zone():
    z = C.ecliptic_zones()
    assert "ecl12" not in z and C.ZONES["hunt"] == C.Zone("hunt", 120.5, 130.5, 14.4, 24.4)
    assert z["ecl13"].ra_min == 130.5 and z["ecl11"].ra_max == 120.5
    assert z["ecl12n1"].dec_min == 24.4 and z["ecl12s1"].dec_max == 14.4  # rows stack on the hunt zone
    assert z["ecl35"].ra_max == 360.0 and z["ecl36"].ra_min == 0.0 and z["ecl36"].ra_max == 0.5
    assert all(-90 <= v.dec_min < v.dec_max <= 90 for v in z.values())


def test_gaia_tiles_cached_without_gspc_are_upgraded_best_effort(tmp_path, monkeypatch):
    """A zone built before the GSPC join (tiles without z_ab, y_ab; marker without `gspc`): a partial run still skips;
    a full run refetches only those Gaia tiles; a tile whose refetch fails keeps its old copy and the run succeeds."""
    calls = []
    fail = {"on": False}

    def fake_tap(url, adql):
        calls.append(adql)
        if "gaia" in adql:
            if fail["on"]:
                raise OSError("ESA and IRSA down")
            return sample("gaia")
        return sample("catwise") if "catwise" in adql else sample("2mass")

    monkeypatch.setattr(C, "NET_RETRIES", 1)
    store = Store(str(tmp_path))
    z = C.ZONES["pilot"]
    ts = R.tiles(*R.query_box(z))
    R.run(store, z, workers=2, tap=fake_tap)
    # make it look like tonight's (pre-GSPC) zone: tiles without z_ab/y_ab, marker without gspc
    for t in ts:
        rel = R.tile_rel("pilot", "gaia", t)
        store.write_parquet(rel, store.read_parquet(rel).drop(columns=["z_ab", "y_ab"]))
    store.write_json(R.catalogs_rel("pilot"), {"catalogs": ["gaia", "catwise", "2mass"]})
    assert not R.gspc_in(store, "pilot") and not R.tile_has_gspc(store, R.tile_rel("pilot", "gaia", ts[0]))
    n0 = len(calls)
    monkeypatch.setattr(C, "REFCAT_CATALOGS", ("gaia",))
    assert R.run(store, z, workers=2, tap=fake_tap).get("skipped") and len(calls) == n0  # partial: skips
    monkeypatch.setattr(C, "REFCAT_CATALOGS", ("gaia", "catwise", "2mass"))
    fail["on"] = True  # upgrade fails: old tiles kept, refcat rebuilt from them, gspc stays false
    st = R.run(store, z, workers=2, tap=fake_tap)
    assert st["fetched"] == 0 and st["gspc_kept_old"] == len(ts) and not st["gspc"] and not R.gspc_in(store, "pilot")
    full = store.read_parquet(R.refcat_rel("pilot"))
    assert {"z_ab", "y_ab"} <= set(full.columns) and full["z_ab"].isna().all()
    fail["on"] = False
    n1 = len(calls)
    st = R.run(store, z, workers=2, tap=fake_tap)  # next full run: only the Gaia tiles, once each
    assert st["gspc_upgraded"] == len(ts) and st["fetched"] == 0 and st["gspc"] and len(calls) == n1 + len(ts)
    assert all("gaia" in a for a in calls[n1:]) and R.gspc_in(store, "pilot")
    assert R.run(store, z, workers=2, tap=fake_tap).get("skipped") and len(calls) == n1 + len(ts)
