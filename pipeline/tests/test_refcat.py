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


# --- 16 Nov: the combine always gets every catalogue (the sky run's ecliptic zones were Gaia-only) -----------------
def _fake_tap(calls):
    def tap(url, adql):
        calls.append(adql)
        if "gaia" in adql:
            return sample("gaia")
        return sample("catwise") if "catwise" in adql else sample("2mass")
    return tap


def test_all_builds_every_catalogue_whatever_the_env_says(tmp_path, monkeypatch):
    """skyrun.sh exported SKYSHIFT_REFCAT_CATALOGS=gaia for prep AND combine: the combine's refcat skipped and the zone
    stayed Gaia-only. Now `all` (and `all --from link`) adds CatWISE + 2MASS to a Gaia-only prep, env or not."""
    import argparse

    from skyshift_pipeline import cli
    calls = []
    monkeypatch.setattr(R, "_tap", _fake_tap(calls))
    monkeypatch.setattr(C, "REFCAT_CATALOGS", ("gaia",))  # the sky run's environment, for prep and combine alike
    store = Store(str(tmp_path))
    z = C.ZONES["pilot"]
    n_tiles = len(R.tiles(*R.query_box(z)))
    R.run(store, z, workers=2)  # extraction prep: Gaia only
    assert R.catalogs_in(store, "pilot") == ["gaia"] and R.built_partial(store, "pilot")
    # a standalone refcat with the same env still skips (the prep's job is done) ...
    assert cli._run_step("refcat", store, z, argparse.Namespace(step="refcat", force=False)).get("skipped")
    # ... but the combine's refcat step adds the rest from the cached Gaia tiles
    st = cli._run_step("refcat", store, z, argparse.Namespace(step="all", force=False))
    assert st["fetched"] == 2 * n_tiles and st["catalogs"] == ["gaia", "catwise", "2mass"]
    assert set(store.read_parquet(R.refcat_rel("pilot"))["cat"]) == {"gaia", "catwise", "2mass"}
    assert not R.built_partial(store, "pilot") and len(calls) == 3 * n_tiles
    assert store.read_json(R.catalogs_rel("pilot"))["services"]["catwise"] == ["IRSA CatWISE2020"]
    # `all --from link` on a Gaia-only zone: ensure_full_refcat completes it first; on a complete one it does nothing
    store2 = Store(str(tmp_path / "b"))
    R.run(store2, z, workers=2)
    assert R.built_partial(store2, "pilot")
    out = cli.ensure_full_refcat(store2, z)
    assert out["fetched"] == 2 * n_tiles and not R.built_partial(store2, "pilot")
    assert cli.ensure_full_refcat(store2, z) is None
    # a root without any refcat (fixtures, local tests): no network, nothing done
    assert cli.ensure_full_refcat(Store(str(tmp_path / "c")), z) is None


def _allwise_partition(tile):
    """A fake AllWISE partition: 3 rows inside the tile (one with a 2MASS association 2″ due north) and 1 outside."""
    ra = [tile.ra0 + 0.2, tile.ra0 + 0.5, tile.ra0 + 0.7, tile.ra1 + 0.5]
    dec = [tile.dec0 + 0.2, tile.dec0 + 0.5, tile.dec0 + 0.7, tile.dec0 + 0.5]
    nan = np.nan
    return pd.DataFrame({
        "designation": [f"J{i:06d}" for i in range(4)], "ra": ra, "dec": dec,
        "w1mpro": [14.0, 15.0, 16.0, 13.0], "w1sigmpro": 0.05, "w2mpro": [13.5, 14.8, 15.9, 12.9], "w2sigmpro": 0.07,
        "tmass_key": [1234.0, nan, nan, 99.0], "r_2mass": [2.0, nan, nan, 1.0], "pa_2mass": [0.0, nan, nan, 0.0],
        "j_m_2mass": [15.1, nan, nan, 14.0], "j_msig_2mass": [0.05, nan, nan, 0.03],
        "h_m_2mass": [14.6, nan, nan, 13.5], "h_msig_2mass": [0.06, nan, nan, 0.03],
        "k_m_2mass": [14.3, nan, nan, 13.3], "k_msig_2mass": [0.07, nan, nan, 0.03],
    })


def test_irsa_down_falls_back_to_allwise_on_s3(monkeypatch):
    """CatWISE and 2MASS TAP fail: the WISE role comes from AllWISE (allwise:…, no motion) and 2MASS from AllWISE's
    association (position moved r_2mass along pa_2mass); Gaia is unaffected; the fallback can be turned off."""
    import pytest
    from skyshift_core import tangent_offset_arcsec
    monkeypatch.setattr(C, "NET_RETRIES", 1)
    tile = R.Tile(120.5, 121.5, 14.3, 15.3)

    def tap(url, adql):
        if "catwise" in adql or "fp_psc" in adql:
            raise OSError("IRSA TAP 502")
        return sample("gaia")

    reads = []

    def s3(pix):
        reads.append(pix)
        return _allwise_partition(tile)

    w, name = R.fetch_tile("catwise", tile, tap, s3)
    assert name == R.S3_NAMES["catwise"] and len(w) == 3 * len(R.tile_pixels(tile))  # rows outside the tile dropped
    assert w["ref_id"].str.startswith("allwise:J").all() and (w["cat"] == "catwise").all()
    assert w["pmra"].isna().all() and (w["epoch_mjd"] == C.ALLWISE_EPOCH_MJD).all() and w["w1_mag"].notna().all()
    assert list(w.columns) == list(R.REFCAT_COLUMNS) and len(reads) == len(R.tile_pixels(tile))
    m, name = R.fetch_tile("2mass", tile, tap, s3)
    assert name == R.S3_NAMES["2mass"] and set(m["ref_id"]) == {"2mass:aw1234"}
    e, n = tangent_offset_arcsec(tile.ra0 + 0.2, tile.dec0 + 0.2, m["ra"].to_numpy()[:1], m["dec"].to_numpy()[:1])
    assert abs(e[0]) < 1e-3 and abs(n[0] - 2.0) < 1e-3  # 2″ due north of the WISE source
    assert np.isclose(m["j_mag"].iloc[0], 15.1) and (m["epoch_mjd"] == R.TMASS_EPOCH_MJD).all()
    g, name = R.fetch_tile("gaia", tile, tap, s3)
    assert name == "ESA Gaia" and len(g) > 0
    monkeypatch.setattr(C, "REFCAT_S3_FALLBACK", False)
    with pytest.raises(RuntimeError, match="every service failed"):
        R.fetch_tile("catwise", tile, tap, s3)


def test_s3_first_skips_irsa_tap_and_falls_back_to_it(monkeypatch):
    """REFCAT_S3_FIRST: CatWISE's and 2MASS's roles from AllWISE on S3 without asking IRSA; S3 down: IRSA's TAP."""
    monkeypatch.setattr(C, "NET_RETRIES", 1)
    monkeypatch.setattr(C, "REFCAT_S3_FIRST", True)
    tile = R.Tile(120.5, 121.5, 14.3, 15.3)
    asked = []

    def tap(url, adql):
        asked.append(adql)
        return sample("catwise" if "catwise" in adql else "2mass" if "fp_psc" in adql else "gaia")

    w, name = R.fetch_tile("catwise", tile, tap, lambda pix: _allwise_partition(tile))
    assert name == R.S3_NAMES["catwise"] and not asked and w["ref_id"].str.startswith("allwise:").all()
    g, name = R.fetch_tile("gaia", tile, tap, lambda pix: _allwise_partition(tile))
    assert name == "ESA Gaia" and len(asked) == 1  # Gaia is never from S3

    def down(pix):
        raise OSError("S3 timeout")

    w, name = R.fetch_tile("catwise", tile, tap, down)
    assert name.startswith("IRSA") and not w["ref_id"].str.startswith("allwise:").any()


def test_tile_pixels_cover_the_tile():
    """Every point of the tile falls in one of the partitions read (the 0.02° sampling misses no pixel)."""
    import astropy.units as u
    from astropy_healpix import HEALPix
    tile = R.Tile(40.0, 41.0, 11.9, 12.9)
    pix = set(R.tile_pixels(tile).tolist())
    rng = np.random.default_rng(1)
    ra, dec = rng.uniform(40.0, 41.0, 20000), rng.uniform(11.9, 12.9, 20000)
    hp = HEALPix(nside=C.ALLWISE_NSIDE, order="nested")
    assert set(np.asarray(hp.lonlat_to_healpix(ra * u.deg, dec * u.deg)).tolist()) <= pix
