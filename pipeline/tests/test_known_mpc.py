"""known with KNOWN_SOURCE = "mpc": the switch, the cache layout and the exact per-photo positions.
The kete parts are network tests (orbit file and planet ephemerides are downloaded on first use)."""

import numpy as np
import pandas as pd
import pytest

from skyshift_pipeline import config as C
from skyshift_pipeline import known
from skyshift_pipeline.store import Store


ZONE = C.ZONES["pilot"]


def _square(ra0: float, dec0: float, half: float) -> str:
    v = [(ra0 - half, dec0 - half), (ra0 + half, dec0 - half), (ra0 + half, dec0 + half), (ra0 - half, dec0 + half)]
    return "POLYGON ICRS " + " ".join(f"{a} {b}" for a, b in v)


def test_default_source_is_mpc_and_skybot_paths_unchanged(monkeypatch):
    monkeypatch.delenv("SKYSHIFT_KNOWN_SOURCE", raising=False)
    import importlib
    assert importlib.reload(C).KNOWN_SOURCE == "mpc"
    g = pd.DataFrame({"obs_id": ["2025W43_1A_0679_2"]})
    assert known.run_rel(ZONE, g) == "cache/mpc/pilot/run_2025W43_1A_0679_2.json"
    monkeypatch.setattr(C, "KNOWN_SOURCE", "skybot")
    assert known.run_rel(ZONE, g) == "cache/skybot/pilot/run_2025W43_1A_0679_2.json"
    monkeypatch.setattr(C, "KNOWN_SOURCE", "nope")
    with pytest.raises(ValueError):
        known.run_rel(ZONE, g)


def _photos() -> pd.DataFrame:
    return pd.DataFrame({
        "obs_id": ["A_1", "A_1", "B_1"], "detector": [1, 4, 1], "mjd": [61000.0, 61000.0, 61000.002],
        "session": [5, 5, 5], "s_region": [_square(121.0, 15.0, 0.5)] * 3,
    })


def _cache(per_photo: list | None) -> dict:
    d = {"zone": "pilot", "session": 5, "obs_ids": ["A_1", "B_1"], "mjd": 61000.001, "jd": 61000.001 + 2400000.5,
         "query": {"source": "test"}, "n": 2, "fetched": "x",
         "objects": {"name": ["(1) Ceres", "2001 AB"], "number": [1, None], "ra": [121.0, 121.2], "dec": [15.0, 15.1],
                     "v": [8.0, 19.0], "dra_arcsec_h": [36.0, 0.0], "ddec_arcsec_h": [0.0, 0.0], "class": ["MB", "MB"]}}
    if per_photo is not None:
        d["per_photo"] = per_photo
    return d


def test_skybot_cache_moves_objects_with_rates(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "KNOWN_SOURCE", "skybot")
    store = Store(str(tmp_path))
    store.write_json("cache/skybot/pilot/run_A_1.json", _cache(None))
    p = known.load_predictions(store, _photos(), ZONE)
    ceres = p[(p["name"] == "(1) Ceres") & (p["obs_id"] == "B_1")]
    # 36″/h for 0.001 d = 0.864″ east of 121.0 at dec 15 (on-sky rate)
    assert ceres["ra"].iloc[0] == pytest.approx(121.0 + 0.864 / 3600 / np.cos(np.radians(15.0)), abs=1e-7)
    assert len(p) == 6  # 2 objects × 3 photos, all inside


def test_mpc_cache_uses_exact_per_photo_positions(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "KNOWN_SOURCE", "mpc")
    store = Store(str(tmp_path))
    per_photo = [
        {"obs_id": "A_1", "detector": 1, "name": "(1) Ceres", "ra": 121.01, "dec": 15.01},
        {"obs_id": "A_1", "detector": 4, "name": "(1) Ceres", "ra": 121.02, "dec": 15.02},
        {"obs_id": "B_1", "detector": 1, "name": "2001 AB", "ra": 121.3, "dec": 15.2},
    ]
    store.write_json("cache/mpc/pilot/run_A_1.json", _cache(per_photo))
    p = known.load_predictions(store, _photos(), ZONE).sort_values(["obs_id", "detector", "name"])
    got = [(r.obs_id, r.detector, r.name, round(r.ra, 6), round(r.dec, 6)) for r in p.itertuples()]
    assert got == [("A_1", 1, "(1) Ceres", 121.01, 15.01), ("A_1", 4, "(1) Ceres", 121.02, 15.02),
                   ("B_1", 1, "2001 AB", 121.3, 15.2)]  # an object not listed for a photo is left out
    assert set(p.columns) >= {"v", "dra_arcsec_h", "ddec_arcsec_h", "edge_px", "mjd", "session"}


def test_empty_per_photo_means_nothing_seen(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "KNOWN_SOURCE", "mpc")
    store = Store(str(tmp_path))
    store.write_json("cache/mpc/pilot/run_A_1.json", _cache([]))
    # per_photo present but empty: no object is in any photo (no fallback to the rates path)
    assert known.load_predictions(store, _photos(), ZONE).empty


# ---------------------------------------------------------------- known_mpc helpers (no kete needed)

from conftest import SKYBOT_SAMPLE  # noqa: E402

from skyshift_pipeline import known_mpc  # noqa: E402
from skyshift_pipeline.sky import Footprint, angsep  # noqa: E402


def test_object_table_names_numbers_and_defaults():
    orb = pd.DataFrame({
        "desig": ["1", "275", "600001", "2021 AU1", "5", "6"],
        "name": ["Ceres", "Sapientia", None, None, "Twin", "Twin"],
        "h_mag": [3.34, 9.03, 15.9, 17.9, np.nan, 10.0],
        "g_phase": [0.12, np.nan, 0.15, 0.15, 0.15, 0.15],
        "group_name": ["MBA", "MBA", "MBA", "Apollo", None, "MBA"],
    })
    o = known_mpc.object_table(orb)
    assert o["name"].tolist() == ["Ceres", "Sapientia", "(600001)", "2021 AU1", "Twin [5]", "Twin [6]"]
    assert o["number"].tolist() == [1, 275, 600001, None, 5, 6]
    assert o["g"].tolist() == [0.12, C.MPC_G_DEFAULT, 0.15, 0.15, 0.15, 0.15]
    assert np.isnan(o["h"][4]) and o["class"].tolist()[3:5] == ["Apollo", ""]


def test_object_table_comets_have_no_v_and_no_number():
    orb = pd.DataFrame({"desig": ["1", "C/2025 N1 ", "427P/ATLAS"], "name": ["Ceres", np.nan, np.nan],
                        "h_mag": [3.34, np.nan, np.nan], "g_phase": [0.12, np.nan, np.nan],
                        "group_name": ["MBA", "Comet C", "Comet P"]})
    o = known_mpc.object_table(orb)
    assert o["name"].tolist() == ["Ceres", "C/2025 N1", "427P/ATLAS"] and o["number"].tolist() == [1, None, None]
    assert np.isnan(o["h"][1:]).all() and o["class"].tolist()[1:] == ["Comet C", "Comet P"]
    # the comet file alone has no name, h_mag or g_phase columns
    only = known_mpc.object_table(pd.DataFrame({"desig": ["C/2025 N1 "], "group_name": ["Comet C"]}))
    assert only["name"].tolist() == ["C/2025 N1"] and np.isnan(only["h"][0])


def test_orbit_snapshots_are_saved_once_and_reused(tmp_path, monkeypatch):
    store = Store(str(tmp_path))
    calls = []

    def fetch():
        calls.append(1)
        return pd.DataFrame({"desig": pd.Series([1, "2021 AU1"], dtype=object), "epoch": [2461200.5, 2459220.5]})

    tab, name = known_mpc._snapshot(store, "mpcorb", fetch)
    assert name.startswith("mpcorb_") and name.endswith(".parquet") and tab["desig"].tolist() == ["1", "2021 AU1"]
    again, name2 = known_mpc._snapshot(store, "mpcorb", fetch)
    assert name2 == name and len(calls) == 1 and again["desig"].tolist() == ["1", "2021 AU1"]
    assert known_mpc._snapshot(store, "cometels", lambda: pd.DataFrame({"desig": ["C/2025 N1 "]}))[1].startswith("cometels_")


def test_padded_corners_move_each_corner_out_by_the_pad():
    s = _square(359.8, 15.0, 0.5)  # crosses RA 0
    fp = Footprint.from_s_region(s)
    padded = np.array(known_mpc.padded_corners(s, 0.05))
    before = angsep(fp.ra0, fp.dec0, fp.vertices[:, 0], fp.vertices[:, 1])
    after = angsep(fp.ra0, fp.dec0, padded[:, 0], padded[:, 1])
    assert after - before == pytest.approx(np.full(4, 0.05), abs=1e-9)
    assert angsep(fp.vertices[:, 0], fp.vertices[:, 1], padded[:, 0], padded[:, 1]) == pytest.approx(np.full(4, 0.05), abs=1e-9)
    assert ((padded[:, 0] >= 0) & (padded[:, 0] < 360)).all()


def test_session_record_round_trips_through_load_predictions(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "KNOWN_SOURCE", "mpc")
    store = Store(str(tmp_path))
    photos = _photos()
    objs = known_mpc.object_table(pd.DataFrame({
        "desig": ["1", "2001 AB"], "name": ["Ceres", None], "h_mag": [3.3, 18.0], "g_phase": [0.12, 0.15],
        "group_name": ["MBA", "MBA"]}))
    seen = pd.DataFrame({  # Ceres in all three photos (own positions), 2001 AB only in B_1 D1
        "obs_id": ["A_1", "A_1", "B_1", "B_1"], "detector": [1, 4, 1, 1], "row": [0, 0, 0, 1],
        "ra": [121.0, 121.00001, 121.0002, 121.3], "dec": [15.0, 15.0, 15.0, 15.2],
        "dra_arcsec_h": [36.0, 36.0, 36.0, 0.0], "ddec_arcsec_h": [0.0, 0.0, 0.0, 0.0], "v": [7.5, 7.5, 7.5, 18.2]})
    ob = {"mjd_avg": 61000.0, "X_SC": 1.0, "Y_SC": 2.0, "Z_SC": 3.0, "VX_SC": 0.1, "VY_SC": 0.2, "VZ_SC": 0.3}
    observers = {"A_1": (None, ob, "spacecraft"), "B_1": (None, {}, "geocentre")}
    rec = known_mpc.session_record(ZONE, photos, seen, objs, observers, {"source": "mpc"})
    assert rec["n"] == 2 and rec["objects"]["name"] == ["Ceres", "2001 AB"] and rec["objects"]["number"] == [1, None]
    # Ceres moved from its first photo (A_1, mjd 61000.0) to the session mid-time (61000.001) with its rate
    assert rec["objects"]["ra"][0] == pytest.approx(121.0 + 0.864 / 3600 / np.cos(np.radians(15.0)), abs=1e-7)
    assert rec["observers"]["A_1"]["observer"] == "spacecraft" and rec["observers"]["B_1"]["observer"] == "geocentre"
    store.write_json(known.run_rel(ZONE, photos), rec)
    p = known.load_predictions(store, photos, ZONE).sort_values(["obs_id", "detector", "name"])
    got = [(r.obs_id, r.detector, r.name, round(r.ra, 5)) for r in p.itertuples()]
    assert got == [("A_1", 1, "Ceres", 121.0), ("A_1", 4, "Ceres", 121.00001), ("B_1", 1, "2001 AB", 121.3),
                   ("B_1", 1, "Ceres", 121.0002)]


def test_session_record_with_nothing_seen():
    objs = known_mpc.object_table(pd.DataFrame({"desig": ["1"], "name": ["Ceres"], "h_mag": [3.3], "g_phase": [0.1],
                                                "group_name": ["MBA"]}))
    empty = pd.DataFrame(columns=["obs_id", "detector", "row", "ra", "dec", "dra_arcsec_h", "ddec_arcsec_h", "v"])
    rec = known_mpc.session_record(ZONE, _photos(), empty, objs, {"A_1": (None, {}, "geocentre"), "B_1": (None, {}, "geocentre")}, {})
    assert rec["n"] == 0 and rec["objects"]["name"] == [] and rec["per_photo"]["obs_id"] == []


# ---------------------------------------------------------------- kete against SkyBoT (network, needs the mpc extra)

@pytest.mark.network
def test_kete_from_the_earths_centre_matches_skybot():
    """The fixture is SkyBoT's geocentric answer at JD 2460965.236 (UTC). Through our own code path
    (orbit rows -> states -> geocentre observer -> a field around them), kete should agree to ~1″."""
    kete = pytest.importorskip("kete")
    sky = pd.DataFrame(known.parse_skybot_json(SKYBOT_SAMPLE.read_text()))
    sky = sky[sky["v"] < 17]  # 275 Sapientia, 289 Nenetta, 643 Scheherezade
    orb = kete.mpc.fetch_known_orbit_data(url=C.MPC_ORBITS_URL)
    orb = orb[orb["desig"].astype(str).isin([str(int(n)) for n in sky["number"]])].copy()
    orb["desig"] = orb["desig"].astype(str)
    objs = known_mpc.object_table(orb)
    states = known_mpc.build_states(orb)
    mjd = 2460965.236 - 2400000.5
    obs, how = known_mpc.observer_state(kete, {}, mjd)
    assert how == "geocentre"
    photos = pd.DataFrame({"obs_id": ["X"], "detector": [2], "s_region": [_square(120.5, 15.5, 2.5)]})
    seen = known_mpc.seen_in_photos(kete, states, objs, photos, {"X": (obs, {}, how)})
    got = seen.assign(name=objs["name"].to_numpy()[seen["row"].to_numpy()]).set_index("name")
    for r in sky.itertuples():
        g = got.loc[r.name]
        assert angsep(g.ra, g.dec, r.ra, r.dec) * 3600 < 2.0, r.name
        assert g.v == pytest.approx(r.v, abs=0.3)
        assert g.dra_arcsec_h == pytest.approx(r.dra_arcsec_h, abs=0.5)
