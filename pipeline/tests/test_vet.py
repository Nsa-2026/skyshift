"""Vetting gates, grades and reasons on the suspects (vet.py, suspects.grade / run)."""

import numpy as np
import pandas as pd

from skyshift_pipeline import config as C
from skyshift_pipeline import suspects as S
from skyshift_pipeline import vet as V
from skyshift_pipeline.sky import offset_radec
from skyshift_pipeline.store import Store, photolist_rel, results_rel

OUTLIER = 1 << 19
TRANSIENT = 1 << 0
SOURCE = 1 << 21


def mover(k, dots, ra=None, dec=15.0, session=3, snr=10.0):
    """A mover row with dot lists. dots: list of (obs, det, x, y, flux, snr, size, fpk)."""
    obs, det, x, y, flux, dsnr, size, fpk = (list(c) for c in zip(*dots))
    return dict(ra=121.0 + 0.05 * k if ra is None else ra, dec=dec, mjd=61000.3 + k / 1440, det=det[0], session=session,
                visit=1, evidence="two_exposures", n_dots=len(dots), snr=snr, flux_ujy=float(np.median(flux)), flags=0,
                flags_peak_all=0, cell=k, dot_obs=obs, dot_det=np.array(det, np.int8), dot_x=np.array(x, np.float32),
                dot_y=np.array(y, np.float32), dot_mjd=np.array([61000.3 + k / 1440 + j / 1440 for j in range(len(dots))]),
                dot_flux=np.array(flux, np.float32), dot_snr=np.array(dsnr, np.float32), dot_size=np.array(size, np.float32),
                dot_chi2=np.full(len(dots), 2.0, np.float32), dot_fpk=np.array(fpk, np.int64).astype(np.int32))


def clean(k, x=None, y=1000.0, obs=("E1", "E2"), det=4, flux=(300.0, 320.0), fpk=(SOURCE, SOURCE), size=(4.8, 4.9)):
    """Two dots of one detector, one exposure step (115 px in y) apart; x = 1000 + 10 k unless given."""
    x = 1000.0 + 10 * k if x is None else x
    return mover(k, [(obs[0], det, x, y, flux[0], 8.0, size[0], fpk[0]),
                     (obs[1], det, x + 3, y - 115, flux[1], 8.0, size[1], fpk[1])])


def test_star_size_lookup():
    s = V.star_size(np.array([1, 4, 6, 9]), np.array([5.0, 12.0, 1000.0, 10.0]))
    assert s[0] == C.VET_STAR_SIZE_ARCSEC[1][0] and s[1] == C.VET_STAR_SIZE_ARCSEC[4][2]
    assert s[2] == C.VET_STAR_SIZE_ARCSEC[6][-1] and np.isnan(s[3])


def test_pixel_exposures_and_columns():
    ref = pd.DataFrame({"obs": ["A", "B", "C", "C", "D"], "det": [4, 4, 4, 4, 5],
                        "x": [100.0, 100.8, 99.2, 100.5, 100.0], "y": [200.0, 199.1, 200.9, 200.0, 200.0]})
    q = pd.DataFrame({"obs": ["A", "A", "Z"], "det": [4, 5, 4], "x": [100.0, 100.0, 103.0], "y": [200.0, 200.0, 200.0]})
    assert list(V.pixel_exposures(q, ref)) == [3, 1, 0]  # A, B, C (C twice) on detector 4; D on detector 5
    free = pd.DataFrame({"obs": ["A"] * 4 + ["B"], "det": [4] * 5, "x": [100.0, 100.3, 99.6, 140.0, 100.0], "y": [1.0, 500, 900, 4, 5]})
    assert list(V.column_dots(q, free)) == [3, 0, 0]


def test_gates_grades_and_reasons():
    rows = [
        clean(0),                                                         # B
        clean(1, x=3.0),                                                  # edge (x < 6)
        clean(2, fpk=(SOURCE | TRANSIENT, SOURCE)),                       # masked bit at the peak
        clean(3, size=(2.0, 4.9), fpk=(OUTLIER, SOURCE)),                 # sharp + OUTLIER
        clean(4, fpk=(OUTLIER, OUTLIER)),                                 # OUTLIER alone: B
        clean(5, size=(2.0, 4.9)),                                        # sharp alone: B
        clean(6, flux=(100.0, 500.0)),                                    # 5× within one detector
        mover(7, [("E1", 1, 900, 900, 100.0, 8.0, 4.6, 0), ("E1", 4, 900, 900, 500.0, 8.0, 4.9, 0)]),  # 5× across D1/D4: B
        clean(8, obs=("R1", "R2")),                                       # both dots from red photos
        clean(9, obs=("R1", "E2")),                                       # one red photo: B
        clean(10),                                                        # 2MASS within 6″
    ]
    s = pd.DataFrame(rows)
    # a hot pixel: three other movers' dots on the pixel of mover 0's first dot... in 3 exposures
    hot = [mover(20 + j, [(f"H{j}", 4, 1000.4, 999.6, 200.0, 8.0, 4.8, 0), (f"H{j}b", 4, 500, 500, 200.0, 8.0, 4.8, 0)]) for j in range(2)]
    movers = pd.concat([s, pd.DataFrame(hot)], ignore_index=True)
    red = {("R1", 4), ("R2", 4)}
    cat = np.zeros(len(s), bool)
    cat[10] = True
    out, st = V.vet(s, movers, None, red, {"2mass": cat})
    g = dict(zip(out["cell"], out["reasons"]))
    assert g[0] == ["hot_pixel"]  # E1 + H0 + H1 on (1000, 1000) of D4
    assert g[1] == ["edge"] and g[2] == ["masked_peak"] and g[3] == ["sharp_outlier"]
    assert g[4] == [] and g[5] == [] and g[6] == ["flux_ratio"] and g[7] == []
    assert g[8] == ["red_photo"] and g[9] == [] and g[10] == ["2mass"]
    assert dict(zip(out["cell"], out["grade"])) == {k: ("B" if not g[k] else "C") for k in g}
    assert st["grades"] == {"A": 0, "B": 4, "C": 7} and st["gates"]["hot_pixel"] == 1 and st["first_reason"]["edge"] == 1
    assert out["gate_edge"].sum() == 1


def test_free_dot_maps():
    s = pd.DataFrame([clean(0), clean(1)])
    # mover 0's first dot (E1, D4, x 1000, y 1000): free dots on that pixel in 5 exposures; mover 1's first dot
    # (E1, D4, x 1010): a column with 3 free dots in E1
    free = pd.DataFrame({"obs": [f"F{j}" for j in range(4)] + ["E1", "E1", "E1", "E1"], "det": 4,
                         "x": [1000.5] * 4 + [1000.0, 1010.2, 1009.8, 1010.0], "y": [999.5] * 4 + [1000.0, 50.0, 1500.0, 1000.0]})
    out, _ = V.vet(s, s, free, None, None)
    assert list(out["reasons"]) == [["hot_pixel"], ["bad_column"]]
    out4, _ = V.vet(s, s, free[free["obs"] != "F3"], None, None)  # 4 exposures: not enough for free dots alone
    assert list(out4["reasons"]) == [[], ["bad_column"]]


def test_movers_without_dot_lists_are_not_vetted():
    s = pd.DataFrame([clean(0)]).drop(columns=list(V.DOT_LISTS))
    out, st = V.vet(s, s, None, None, None)
    assert list(out["grade"]) == ["C"] and list(out["reasons"]) == [["not_vetted"]] and st["dot_lists"] is False


def test_run_writes_grades_vetoes_and_ranks(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "VET_ALLWISE", False)  # offline; the AllWISE reason has its own test
    z = C.ZONES["pilot"]
    store = Store(str(tmp_path))
    rows = [clean(0, x=3.0) | {"snr": 50.0}, clean(1) | {"snr": 9.0}, clean(2) | {"snr": 20.0}, clean(3) | {"snr": 30.0}]
    m = pd.DataFrame(rows)
    m["obs_ids"] = "E1,E2"
    m["detectors"] = "4"
    m["n_exposures"] = 2
    m["cover_sessions"] = 5
    photos = pd.DataFrame({"obs_id": ["E1", "E2"], "detector": [4, 4], "collection": ["spherex_dr1"] * 2,
                           "session": [3, 3], "mjd": [61000.3, 61000.31]})
    store.write_parquet(photolist_rel(z.name), photos)
    store.write_parquet(results_rel(z.name, "movers.parquet"), m)
    store.write_parquet(results_rel(z.name, "fixed.parquet"), pd.DataFrame({"ra": [m.loc[1, "ra"] + 8 / 3600 / np.cos(np.radians(15))],
                                                                          "dec": [15.0], "snr": [20.0]}))
    t3 = offset_radec(m.loc[3, "ra"], m.loc[3, "dec"], 0.0, 4.0)
    store.write_parquet("refcat/pilot.parquet", pd.DataFrame([
        dict(cat="gaia", ra=130.0, dec=20.0, epoch_mjd=57388.5, pmra=0.0, pmdec=0.0),
        dict(cat="catwise", ra=130.1, dec=20.0, epoch_mjd=57170.0, pmra=np.nan, pmdec=np.nan),
        dict(cat="tmass", ra=t3[0], dec=t3[1], epoch_mjd=51000.0, pmra=np.nan, pmdec=np.nan)]))
    store.write_parquet(results_rel(z.name, C.VET_QA_NAME), pd.DataFrame({"obs_id": ["E9"], "detector": [4], "grade": ["red"],
                                                                         "reasons": ["test"]}))
    st = S.run(store, z, pred=pd.DataFrame(columns=["ra", "dec", "mjd", "session", "dra_arcsec_h", "ddec_arcsec_h"]))
    assert st["suspects"] == 4 and st["vetting"]["grades"] == {"A": 0, "B": 2, "C": 2}
    j = store.read_json(results_rel(z.name, "suspects.json"))
    assert j["vetoes"] == ["mpc", "gaia", "catwise"] and j["provisional"] is False
    assert [x["grade"] for x in j["suspects"]] == ["B", "B", "C", "C"]
    assert [x["snr"] for x in j["suspects"]] == [20.0, 9.0, 50.0, 30.0]  # by grade, then S/N
    assert [x["reasons"] for x in j["suspects"]] == [[], [], ["edge"], ["2mass"]]
    near = {x["cell"]: x["near_source_arcsec"] for x in j["suspects"]}
    assert near[1] == 8.0 and near[3] == 4.0 and near[2] is None
    assert j["grades"] == {"A": 0, "B": 2, "C": 2} and j["gates"]["edge"] == 1
    p = store.read_parquet(results_rel(z.name, "suspects.parquet"))
    assert list(p["grade"]) == ["B", "B", "C", "C"] and "dot_x" in p and "gate_2mass" in p


def test_allwise_reason_where_catwise_was_the_veto(tmp_path, monkeypatch):
    from skyshift_pipeline.forced_sky import pixel_of
    monkeypatch.setattr(C, "VET_ALLWISE", True)
    monkeypatch.setattr(C, "ALLWISE_CACHE", False)
    z = C.ZONES["pilot"]
    store = Store(str(tmp_path))
    s = pd.DataFrame([clean(0), clean(1)])
    a = offset_radec(s.loc[1, "ra"], s.loc[1, "dec"], 3.0, 0.0)
    cat = pd.DataFrame({"ra": [a[0]], "dec": [a[1]]})
    cpix = pixel_of(cat["ra"], cat["dec"], C.ALLWISE_NSIDE)

    def reader(pix):
        return cat[cpix == pix]

    empty = pd.DataFrame(columns=["ra", "dec"])
    out, st = S.grade(store, z, s, s, empty, None, "refcat/pilot.parquet (CatWISE2020)", allwise_reader=reader)
    assert list(out["reasons"]) == [[], ["allwise"]] and st["catalogs_checked"] == ["allwise"]
    out2, st2 = S.grade(store, z, s, s, empty, None, "AllWISE on S3", allwise_reader=reader)  # AllWISE was the veto
    assert list(out2["reasons"]) == [[], []] and st2["catalogs_checked"] == []
