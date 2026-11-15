"""known, second source: planets and their moons from JPL Horizons (stubbed; offline)."""

import numpy as np
import pandas as pd

from skyshift_pipeline import config as C
from skyshift_pipeline import known, known_planets
from skyshift_pipeline.store import Store

ZONE = C.ZONES["pilot"]  # RA 120.5–124.1, Dec 14.4–17.9
JD = 2400000.5

SAMPLE = """Table format    : Comma Separated Values (spreadsheet)
****
Date_________JDUT, , , R.A.___(ICRF), DEC____(ICRF),  dRA*cosD, d(DEC)/dt,    APmag,  S-brt,
****
$$SOE
2461022.500000000, , , 356.402660238,  -4.127150861,  4.874007,  2.446905,   11.270,  7.698,
2461022.506944444, , , 356.402886501,  -4.127037615,  4.875119,  2.447645,     n.a.,  7.698,
$$EOE
"""


def _square(ra0: float, dec0: float, half: float) -> str:
    v = [(ra0 - half, dec0 - half), (ra0 + half, dec0 - half), (ra0 + half, dec0 + half), (ra0 - half, dec0 + half)]
    return "POLYGON ICRS " + " ".join(f"{a} {b}" for a, b in v)


def test_parse_table_reads_horizons_csv():
    t = known_planets.parse_table(SAMPLE)
    assert list(t.columns) == list(known_planets.EPH_COLUMNS)
    assert t["ra"].tolist() == [356.402660238, 356.402886501]
    assert t["dra_arcsec_h"].iloc[0] == 4.874007 and t["ddec_arcsec_h"].iloc[1] == 2.447645
    assert t["v"].iloc[0] == 11.27 and np.isnan(t["v"].iloc[1])  # "n.a." -> NaN


def test_interp_unwraps_ra_across_zero():
    eph = pd.DataFrame({"jd": [JD + 61000.0, JD + 61001.0], "ra": [359.9, 0.1], "dec": [0.0, 0.2],
                        "dra_arcsec_h": [30.0, 30.0], "ddec_arcsec_h": [0.0, 0.0], "v": [10.0, 10.0]})
    p = known_planets.interp(eph, np.array([61000.5]))
    assert abs(p["ra"][0] - 0.0) < 1e-9 and abs(p["dec"][0] - 0.1) < 1e-12


def test_near_box_widens_ra_and_wraps():
    z = C.Zone("w", 350.0, 360.0, -5.0, 5.0)
    assert known_planets.near_box(z, np.array([2.0, 340.0, 10.0]), np.array([0.0, 0.0, 0.0]), 4.0).tolist() == [True, False, False]
    assert known_planets.near_box(ZONE, np.array([122.0]), np.array([22.5]), 4.0).tolist() == [False]


def _photos() -> pd.DataFrame:
    s = _square(122.0, 16.0, 1.75)
    return pd.DataFrame({"obs_id": ["A_1", "A_1", "B_1", "C_1"], "detector": [1, 4, 1, 1],
                         "mjd": [61000.0, 61000.0, 61000.0015, 61100.0], "session": [1, 1, 1, 2], "visit": [1, 1, 1, 2],
                         "s_region": [s, s, s, s]})


def _fake_fetch(calls: list):
    """Saturn at (122, 16) during visit 1 and far away in visit 2; each moon 0.01° further east per NAIF step."""
    def fetch(naif: int, jd0: float, jd1: float, step: str) -> pd.DataFrame:
        calls.append((naif, step))
        t = np.arange(jd0, jd1 + 1e-9, 0.25 if step.endswith("h") else 10 / 1440)
        far = (t - JD) > 61050  # visit 2: the planet has moved away
        ra = np.where(far, 200.0, 122.0 + (0.01 * (naif - 600) if 600 < naif < 700 else 0.0))
        if naif not in (699, *[n for n, _ in C.PLANET_SYSTEMS["Saturn"]["moons"]]):
            ra = np.full_like(t, 250.0)  # every other planet far from the zone
        return pd.DataFrame({"jd": t, "ra": ra, "dec": np.full_like(t, 16.0), "dra_arcsec_h": np.full_like(t, 5.0),
                             "ddec_arcsec_h": np.zeros_like(t), "v": np.full_like(t, 11.0)})
    return fetch


def test_run_writes_planet_files_that_load_predictions_reads(tmp_path):
    store = Store(str(tmp_path))
    store.write_parquet("photolists/zones/pilot.parquet", _photos())
    calls: list = []
    out = known_planets.run(store, ZONE, fetch=_fake_fetch(calls))
    assert out["computed"] == 2 and out["near"] == ["Saturn visit 1"] and out["sessions_with_bodies"] == 1
    coarse = [c for c in calls if c[1].endswith("h")]
    assert len(coarse) == len(C.PLANET_SYSTEMS)  # one coarse table per planet
    fine = {c[0] for c in calls if c[1].endswith("m")}
    assert fine == {699, *[n for n, _ in C.PLANET_SYSTEMS["Saturn"]["moons"]]}  # only the planet that came near
    assert store.exists("cache/planets/pilot/run_A_1.json") and store.exists("cache/planets/pilot/run_C_1.json")
    assert store.read_json("cache/planets/pilot/run_C_1.json")["n"] == 0  # visit 2: nothing near, file still written
    p = known.load_predictions(store, _photos(), ZONE, sources=("planets",))
    iap = p[(p["name"] == "Iapetus") & (p["obs_id"] == "A_1") & (p["detector"] == 1)]
    assert len(iap) == 1 and abs(iap["ra"].iloc[0] - 122.08) < 1e-6 and iap["class"].iloc[0] == "moon"
    assert set(p.loc[p["name"] == "Saturn", "class"]) == {"planet"}
    assert known_planets.run(store, ZONE, fetch=_fake_fetch([]))["computed"] == 0  # cached: nothing again


def test_failed_horizons_writes_nothing_and_known_still_succeeds(tmp_path, monkeypatch):
    store = Store(str(tmp_path))
    store.write_parquet("photolists/zones/pilot.parquet", _photos())
    from skyshift_pipeline import known_mpc

    def boom(*a):
        raise RuntimeError("Horizons down")
    monkeypatch.setattr(known_mpc, "run", lambda *a, **k: {"source": "mpc", "computed": 0})
    monkeypatch.setattr(known_planets, "horizons", boom)
    monkeypatch.setattr(known_planets.run, "__defaults__", (None, boom))
    out = known.run(store, ZONE)
    assert out["source"] == "mpc" and "Horizons down" in out["planets"]["error"]
    assert not store.listdir("cache/planets/pilot")  # rerun `known` to try again


def test_score_leaves_planets_and_moons_out():
    from skyshift_pipeline.score import asteroids_only
    pred = pd.DataFrame({"class": ["MB", "moon", "planet", "JFc"], "v": [12.0, 11.0, 0.5, 14.0]})
    assert asteroids_only(pred)["class"].tolist() == ["MB", "JFc"]
    assert asteroids_only(pd.DataFrame()).empty


def test_both_sources_are_read_by_default(tmp_path):
    store = Store(str(tmp_path))
    ph = _photos()
    rec = {"objects": {"name": ["X"], "number": [None], "ra": [122.0], "dec": [16.0], "v": [10.0], "dra_arcsec_h": [0.0],
                       "ddec_arcsec_h": [0.0], "class": ["?"]},
           "per_photo": {"obs_id": ["A_1"], "detector": [1], "name": ["X"], "ra": [122.0], "dec": [16.0]}}
    store.write_json("cache/mpc/pilot/run_A_1.json", {**rec, "objects": {**rec["objects"], "name": ["(1) Ceres"], "class": ["MB"]},
                                                     "per_photo": {**rec["per_photo"], "name": ["(1) Ceres"]}})
    store.write_json("cache/planets/pilot/run_A_1.json", {**rec, "objects": {**rec["objects"], "name": ["Titan"], "class": ["moon"]},
                                                         "per_photo": {**rec["per_photo"], "name": ["Titan"]}})
    assert sorted(known.load_predictions(store, ph, ZONE)["name"]) == ["(1) Ceres", "Titan"]
    assert known.load_predictions(store, ph, ZONE, sources=("mpc",))["name"].tolist() == ["(1) Ceres"]
