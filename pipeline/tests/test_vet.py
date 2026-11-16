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
    y = [v + 0.37 if float(v).is_integer() else v for v in y]  # a measured centroid, not a whole pixel (whole_pixel)
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


def test_column_need_grows_with_the_photos_free_dots():
    """A sparse photo needs VET_COLUMN_MIN_DOTS (3) in the column; a crowded one more than chance gives there."""
    rng = np.random.default_rng(1)
    sparse = pd.DataFrame({"obs": "A", "det": 4, "x": rng.uniform(0, 2040, 50), "y": rng.uniform(0, 2040, 50)})
    crowd = pd.DataFrame({"obs": "B", "det": 4, "x": rng.uniform(0, 2040, 800), "y": rng.uniform(0, 2040, 800)})
    q = pd.DataFrame({"obs": ["A", "B", "C"], "det": [4, 4, 4], "x": [10.0, 10.0, 10.0], "y": [5.0, 5.0, 5.0]})
    need = V.column_need(q, pd.concat([sparse, crowd], ignore_index=True))
    assert need[0] == 3 and need[2] == 3  # 50 free dots: 0.02 others per column; photo C has none
    assert need[1] == 5  # 800: 0.39 others expected, P(>= 3) = 0.8%, P(>= 4) = 0.08%: 4 others + itself


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
        mover(11, [("E1", 4, 1200, 1000, 300.0, 20.0, 1.2, SOURCE), ("E2", 4, 1203, 885, 320.0, 20.0, 4.0, SOURCE)]),  # very sharp
        mover(12, [("E1", 4, 1300, 1000, 300.0, 12.0, 1.2, SOURCE), ("E2", 4, 1303, 885, 320.0, 12.0, 4.0, SOURCE)]),  # S/N < 15: B
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
    assert g[8] == ["red_photo"] and g[9] == [] and g[10] == ["2mass"] and g[11] == ["very_sharp"] and g[12] == []
    assert dict(zip(out["cell"], out["grade"])) == {k: ("B" if not g[k] else "C") for k in g}
    assert st["grades"] == {"A": 0, "B": 5, "C": 8} and st["gates"]["hot_pixel"] == 1 and st["first_reason"]["edge"] == 1
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


def _run_zone(tmp_path) -> tuple[Store, C.Zone, pd.DataFrame]:
    """A pilot store with 4 movers (cells 0-3) that all become suspects: 0 edge (C), 1 and 2 clean (B), 3 2MASS (C)."""
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
        dict(cat="2mass", ra=t3[0], dec=t3[1], epoch_mjd=51000.0, pmra=np.nan, pmdec=np.nan)]))  # refcat.py writes `2mass`
    store.write_parquet(results_rel(z.name, C.VET_QA_NAME), pd.DataFrame({"obs_id": ["E9"], "detector": [4], "grade": ["red"],
                                                                         "reasons": ["test"]}))
    return store, z, m


NO_PRED = pd.DataFrame(columns=["ra", "dec", "mjd", "session", "dra_arcsec_h", "ddec_arcsec_h"])


def test_run_writes_grades_vetoes_and_ranks(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "VET_ALLWISE", False)  # offline; the AllWISE reason has its own test
    store, z, m = _run_zone(tmp_path)
    st = S.run(store, z, pred=NO_PRED)
    assert st["suspects"] == 4 and st["vetting"]["grades"] == {"A": 0, "B": 2, "C": 2}
    j = store.read_json(results_rel(z.name, "suspects.json"))
    assert j["vetoes"] == ["mpc", "planets"] and j["catalogues"] == ["gaia", "catwise", "2mass"] and j["provisional"] is False
    assert [x["grade"] for x in j["suspects"]] == ["B", "B", "C", "C"]
    assert [x["snr"] for x in j["suspects"]] == [20.0, 9.0, 50.0, 30.0]  # by grade, then S/N
    assert [x["reasons"] for x in j["suspects"]] == [[], [], ["edge"], ["2mass"]]
    # cell 1 has link's fixed source 8″ away (no flux in the table): a fixed_source warning, not a reason
    assert [x["warn_codes"] for x in j["suspects"]] == [[], ["fixed_source"], ["edge"], ["catalogue_star"]]
    assert j["suspects"][1]["warn_values"] == {"fixed_source": {"sep_arcsec": 8.0, "fixed_snr": 20}}
    assert all(x["warn_levels"] == {} for x in j["suspects"])
    assert j["suspects"][2]["warn_values"] == {"edge": {"edge_px": 3.0}}
    assert j["suspects"][3]["warn_values"]["catalogue_star"]["tmass_sep_arcsec"] == 4.0
    assert j["warnings"] == {"catalogue_star": 1, "edge": 1, "fixed_source": 1}
    near = {x["cell"]: x["near_source_arcsec"] for x in j["suspects"]}
    assert near[1] == 8.0 and near[3] == 4.0 and near[2] is None
    assert j["grades"] == {"A": 0, "B": 2, "C": 2} and j["gates"]["edge"] == 1
    p = store.read_parquet(results_rel(z.name, "suspects.parquet"))
    assert list(p["grade"]) == ["B", "B", "C", "C"] and "dot_x" in p and "gate_2mass" in p
    # no tracklets.parquet: no track fields, nothing graded A
    assert "tracklet_id" not in p and all("rate_arcsec_h" not in x for x in j["suspects"])
    assert st["vetting"]["tracklets"] == {"source": None, "on_track": 0, "graded_a": 0, "fast_held": 0, "track_overruled": 0}


def test_run_catalogue_reason_only_for_a_source_as_bright_as_the_mover(tmp_path, monkeypatch):
    """A Gaia star within 6″ is a reason (gaia, grade C) when at most 2 mag fainter than the mover; a much fainter one is
    a chance neighbour: catalogue_star only. The movers are AB 17.67 (310 µJy)."""
    monkeypatch.setattr(C, "VET_ALLWISE", False)
    store, z, m = _run_zone(tmp_path)
    g1 = offset_radec(m.loc[1, "ra"], m.loc[1, "dec"], 2.0, 0.0)
    g2 = offset_radec(m.loc[2, "ra"], m.loc[2, "dec"], 0.0, 3.0)
    store.write_parquet("refcat/pilot.parquet", pd.DataFrame([
        dict(cat="gaia", ra=g1[0], dec=g1[1], epoch_mjd=57388.5, pmra=0.0, pmdec=0.0, g_mag=17.0),
        dict(cat="gaia", ra=g2[0], dec=g2[1], epoch_mjd=57388.5, pmra=0.0, pmdec=0.0, g_mag=19.9),
        dict(cat="catwise", ra=130.1, dec=20.0, epoch_mjd=57170.0, pmra=np.nan, pmdec=np.nan, g_mag=np.nan)]))
    S.run(store, z, pred=NO_PRED)
    j = {x["cell"]: x for x in store.read_json(results_rel(z.name, "suspects.json"))["suspects"]}
    assert j[1]["grade"] == "C" and j[1]["reasons"] == ["gaia"]
    assert j[1]["warn_values"]["catalogue_star"]["fainter_mag"] == -0.7
    assert j[2]["grade"] == "B" and j[2]["reasons"] == [] and j[2]["warn_codes"] == ["catalogue_star"]
    assert j[2]["warn_values"]["catalogue_star"] == {"sep_arcsec": 3.0, "gaia_sep_arcsec": 3.0, "gaia_g_mag": 19.9,
                                                     "mover_ab_mag": 17.67, "fainter_mag": 2.2}


def _sid(z, m, k) -> str:
    return f"{z.name}-{int(m.loc[k, 'cell'])}-{int(round(m.loc[k, 'mjd'] * 1440))}"


def _tracklets(z, m) -> pd.DataFrame:
    """tracklets.parquet rows (tracklets.TRACK_COLUMNS subset) for the _run_zone movers: 1 on a 3-session track at 50σ
    (→ A), 2 on a track at 2σ (stays B), 0 on a track but gated (stays C), 3 on no track; plus another zone mover."""
    rows = [
        dict(mover_id=_sid(z, m, 1), tracklet_id=0, n_sessions=3, rate_arcsec_h=25.0, pa_deg=80.0, rate_sigma=0.5, rate_snr=50.0),
        dict(mover_id=_sid(z, m, 2), tracklet_id=1, n_sessions=4, rate_arcsec_h=1.0, pa_deg=10.0, rate_sigma=0.5, rate_snr=2.0),
        dict(mover_id=_sid(z, m, 0), tracklet_id=2, n_sessions=5, rate_arcsec_h=30.0, pa_deg=270.0, rate_sigma=0.3, rate_snr=100.0),
        dict(mover_id=_sid(z, m, 3), tracklet_id=-1, n_sessions=1, rate_arcsec_h=np.nan, pa_deg=np.nan, rate_sigma=np.nan,
             rate_snr=np.nan),
        dict(mover_id="pilot-99-1", tracklet_id=0, n_sessions=3, rate_arcsec_h=25.0, pa_deg=80.0, rate_sigma=0.5, rate_snr=50.0),
    ]
    t = pd.DataFrame(rows)
    return t.astype({"tracklet_id": "int64", "n_sessions": "int16", "rate_arcsec_h": "float32", "pa_deg": "float32",
                     "rate_sigma": "float32", "rate_snr": "float32"})


def test_run_grades_a_from_tracklets(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "VET_ALLWISE", False)
    store, z, m = _run_zone(tmp_path)
    store.write_parquet(results_rel(z.name, "tracklets.parquet"), _tracklets(z, m))
    st = S.run(store, z, pred=NO_PRED)
    assert st["vetting"]["grades"] == {"A": 1, "B": 1, "C": 2}
    assert st["vetting"]["tracklets"] == {"source": "tracklets.parquet", "on_track": 3, "graded_a": 1, "fast_held": 0,
                                          "track_overruled": 0}
    j = store.read_json(results_rel(z.name, "suspects.json"))
    assert j["grades"] == {"A": 1, "B": 1, "C": 2}
    assert [(x["cell"], x["grade"]) for x in j["suspects"]] == [(1, "A"), (2, "B"), (0, "C"), (3, "C")]  # A leads
    tr = {x["cell"]: {k: x[k] for k in ("n_sessions", "rate_arcsec_h", "pa_deg", "rate_sigma")} for x in j["suspects"]}
    assert tr[1] == {"n_sessions": 3, "rate_arcsec_h": 25.0, "pa_deg": 80.0, "rate_sigma": 0.5}
    assert tr[2] == {"n_sessions": 4, "rate_arcsec_h": 1.0, "pa_deg": 10.0, "rate_sigma": 0.5}  # on a track, < 5σ: B
    assert tr[0]["n_sessions"] == 5 and tr[3] == dict.fromkeys(tr[3])  # gated C keeps its track; no track: all null
    assert j["suspects"][0]["id"] == _sid(z, m, 1) and j["suspects"][0]["reasons"] == []
    p = store.read_parquet(results_rel(z.name, "suspects.parquet"))
    assert list(p["grade"]) == ["A", "B", "C", "C"] and list(p["tracklet_id"]) == [0, 1, 2, -1]
    assert p["rate_snr"].iloc[0] == 50.0 and np.isnan(p["rate_snr"].iloc[3]) and pd.isna(p["n_sessions"].iloc[3])


def test_unreadable_tracklets_change_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "VET_ALLWISE", False)
    store, z, m = _run_zone(tmp_path)
    store.write_parquet(results_rel(z.name, "tracklets.parquet"), pd.DataFrame({"mover_id": [_sid(z, m, 1)], "other": [1]}))
    st = S.run(store, z, pred=NO_PRED)
    assert st["vetting"]["grades"] == {"A": 0, "B": 2, "C": 2} and st["vetting"]["tracklets"]["source"] is None
    j = store.read_json(results_rel(z.name, "suspects.json"))
    assert [x["grade"] for x in j["suspects"]] == ["B", "B", "C", "C"] and all("n_sessions" not in x for x in j["suspects"])
    # tracklets' error table (no rows, float columns): read, nothing on a track, fields null
    store.write_parquet(results_rel(z.name, "tracklets.parquet"),
                        pd.DataFrame({c: pd.Series(dtype="float64") for c in ("mover_id", *S.TRACK_COLUMNS)}))
    st = S.run(store, z, pred=NO_PRED)
    assert st["vetting"]["grades"] == {"A": 0, "B": 2, "C": 2} and st["vetting"]["tracklets"]["on_track"] == 0
    j = store.read_json(results_rel(z.name, "suspects.json"))
    assert all(x["n_sessions"] is None and x["rate_arcsec_h"] is None for x in j["suspects"])


def test_track_grades_rule():
    """A needs B, a track, n_sessions >= VET_A_MIN_SESSIONS and rate_snr >= VET_MOTION_SIGMA (5σ exactly passes)."""
    s = pd.DataFrame({"grade": ["B", "B", "B", "B", "C", "B"]})
    ids = [f"m{i}" for i in range(6)]
    tr = pd.DataFrame({"mover_id": ids[:5], "tracklet_id": [0, 1, 2, -1, 3], "n_sessions": [3, 1, 2, 1, 3],
                       "rate_arcsec_h": 10.0, "pa_deg": 0.0, "rate_sigma": 2.0, "rate_snr": [5.0, 9.0, 4.99, np.nan, 9.0]})
    out, st = S.track_grades(s, ids, tr)
    assert list(out["grade"]) == ["A", "B", "B", "B", "C", "B"]
    assert st == {"source": "tracklets.parquet", "on_track": 4, "graded_a": 1, "fast_held": 0, "track_overruled": 0}
    assert list(out["tracklet_id"]) == [0, 1, 2, -1, 3, -1] and out["rate_arcsec_h"].isna().tolist() == [False] * 3 + [True, False, True]
    same, st0 = S.track_grades(s, ids, None)
    assert same is s and st0["graded_a"] == 0


def test_fast_track_needs_a_recovered_dot_or_a_detector_partner_for_a():
    """Above VET_A_FAST_ARCSEC_H a track's movers alone don't make A (a single-detector artefact links too): it needs a
    recovered dot or a suspect of the track seen by both detectors of a simultaneous pair; slower tracks are unchanged."""
    ids = [f"m{i}" for i in range(6)]
    # tracks: 0 fast (400 < 600″/h) no evidence, 1 fast with a D2+D5 partner on its second mover, 2 fast with a
    # recovered dot, 3 slow (exactly the limit counts as slow)
    tr = pd.DataFrame({"mover_id": ids, "tracklet_id": [0, 0, 1, 1, 2, 3], "n_sessions": 2,
                       "rate_arcsec_h": [600.0, 600.0, 600.0, 600.0, 600.0, C.VET_A_FAST_ARCSEC_H], "pa_deg": 0.0,
                       "rate_sigma": 10.0, "rate_snr": 60.0})
    s = pd.DataFrame({"grade": ["B"] * 6, "paired_exposures": [0, 0, 0, 1, 0, 0]})
    out, st = S.track_grades(s, ids, tr, recovered={2: 1, 0: 0})
    assert list(out["grade"]) == ["B", "B", "A", "A", "A", "A"] and st["fast_held"] == 2 and st["graded_a"] == 4
    # without paired_exposures: the dot lists tell (D3 and D6 in one exposure)
    s2 = pd.DataFrame({"grade": ["B"] * 6, "dot_obs": [["e1", "e2"]] * 3 + [["e1", "e1"]] + [["e1"]] * 2,
                       "dot_det": [[6, 6]] * 3 + [[3, 6]] + [[6]] * 2})
    for c in ("dot_x", "dot_y", "dot_mjd", "dot_flux", "dot_snr", "dot_size", "dot_chi2", "dot_fpk"):
        s2[c] = [[0.0] * len(v) for v in s2["dot_obs"]]
    assert list(S.detector_partner(s2)) == [False, False, False, True, False, False]
    out2, st2 = S.track_grades(s2, ids, tr)
    assert list(out2["grade"]) == ["B", "B", "A", "A", "B", "A"] and st2["fast_held"] == 3


def test_track_grades_keep_the_in_session_motion():
    """rate_arcsec_h / pa_deg become the track's (NaN off a track); link's own motion stays in session_*."""
    s = pd.DataFrame({"grade": ["B", "B"], "rate_arcsec_h": [100.0, 5.0], "pa_deg": [80.0, 10.0]})
    tr = pd.DataFrame({"mover_id": ["m0"], "tracklet_id": [0], "n_sessions": [3], "rate_arcsec_h": [107.0], "pa_deg": [54.0],
                       "rate_sigma": [1.0], "rate_snr": [50.0]})
    out, _ = S.track_grades(s, ["m0", "m1"], tr)
    assert out["rate_arcsec_h"].tolist()[0] == 107.0 and np.isnan(out["rate_arcsec_h"][1])
    assert out["session_rate_arcsec_h"].tolist() == [100.0, 5.0] and out["session_pa_deg"].tolist() == [80.0, 10.0]
    same, _ = S.track_grades(s, ["m0", "m1"], None)
    assert same["session_rate_arcsec_h"].tolist() == [100.0, 5.0] and same["rate_arcsec_h"].tolist() == [100.0, 5.0]


def test_load_recovered_leaves_out_dots_inside_a_star(tmp_path):
    store = Store(str(tmp_path))
    z = C.ZONES["pilot"]
    assert S.load_recovered(store, z) is None
    store.write_parquet(results_rel(z.name, "tracklet_dots.parquet"),
                        pd.DataFrame({"tracklet_id": [0, 0, 1, 2], "in_fixed": [True, False, True, False]}))
    assert S.load_recovered(store, z) == {0: 1, 2: 1}
    store.write_parquet(results_rel(z.name, "tracklet_dots.parquet"), pd.DataFrame({"tracklet_id": [0, 1, 1]}))
    assert S.load_recovered(store, z) == {0: 1, 1: 2}


def test_allwise_reason_where_catwise_was_the_veto(tmp_path, monkeypatch):
    from skyshift_pipeline.forced_sky import pixel_of
    monkeypatch.setattr(C, "VET_ALLWISE", True)
    monkeypatch.setattr(C, "SUSPECTS_ALLWISE", "auto")  # conftest turns both off
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
    ra, dec, mjd = (s[c].to_numpy(np.float64) for c in ("ra", "dec", "mjd"))
    # CatWISE2020 in the refcat (nothing near): AllWISE is a second WISE catalogue, its 3″ source is a reason
    ref = pd.DataFrame([dict(cat="gaia", ra=130.0, dec=20.0, epoch_mjd=57388.5, pmra=0.0, pmdec=0.0),
                        dict(cat="catwise", ra=130.1, dec=20.0, epoch_mjd=57170.0, pmra=np.nan, pmdec=np.nan)])
    cats, info = S.catalogue_matches(store, z, ra, dec, mjd, ref, allwise_reader=reader)
    assert sorted(cats) == ["allwise", "catwise", "gaia"] and info["wise"] == "catwise"
    w = S.Warnings(len(s))
    S.catalogue_warnings(cats, s["flux_ujy"].to_numpy(), w)
    out, st = S.grade(store, z, s, s, empty, ref, cats, w)
    assert list(out["reasons"]) == [[], ["allwise"]] and "allwise" in st["catalogs_checked"]
    assert w.codes[1] == ["catalogue_star"] and w.values[1]["catalogue_star"]["allwise_sep_arcsec"] == 3.0
    # a Gaia-only refcat: AllWISE is the WISE catalogue; its source is the same reason (and no longer a cut)
    cats2, info2 = S.catalogue_matches(store, z, ra, dec, mjd, ref[ref["cat"] == "gaia"], allwise_reader=reader)
    assert sorted(cats2) == ["allwise", "gaia"] and info2["wise"] == "allwise" and info2["catwise_source"].startswith("AllWISE")
    out2, _ = S.grade(store, z, s, s, empty, ref, cats2, S.Warnings(len(s)))
    assert list(out2["reasons"]) == [[], ["allwise"]]


def test_allwise_cache_without_magnitudes(tmp_path, monkeypatch):
    """A partition cached before 16 Nov (ra, dec only) is read again from S3 for the magnitudes, then cached with them;
    with ALLWISE_REREAD_MAGS off it is used as it is, magnitudes NaN."""
    monkeypatch.setattr(C, "ALLWISE_CACHE", True)
    monkeypatch.setattr(C, "ALLWISE_REREAD_MAGS", False)
    store = Store(str(tmp_path))
    rel = f"{C.ALLWISE_CACHE_DIR}/p00007.parquet"
    store.write_parquet(rel, pd.DataFrame({"ra": [1.0], "dec": [2.0]}))
    calls = []

    def reader(pix):
        calls.append(pix)
        return pd.DataFrame({"ra": [1.0], "dec": [2.0], "w1mpro": [11.5], "w2mpro": [11.0]})

    df = S.allwise_partition(store, 7, reader)
    assert calls == [] and list(df.columns) == list(C.ALLWISE_COLUMNS) and np.isnan(df["w1mpro"][0])
    monkeypatch.setattr(C, "ALLWISE_REREAD_MAGS", True)
    df = S.allwise_partition(store, 7, reader)
    assert calls == [7] and df["w1mpro"][0] == 11.5
    df2 = S.allwise_partition(store, 7, lambda p: (_ for _ in ()).throw(AssertionError()))
    assert df2["w2mpro"][0] == 11.0


def test_dot_numbers_and_gate_warnings():
    """edge, persistence, sharp and flux_jump warnings carry the numbers behind the gates."""
    persist = 1 << 17
    rows = [clean(0), clean(1, x=3.0), clean(2, fpk=(SOURCE | persist, SOURCE)), clean(3, size=(2.0, 4.9), fpk=(OUTLIER, SOURCE)),
            clean(4, flux=(100.0, 500.0))]
    s = pd.DataFrame(rows)
    num = V.dot_numbers(s)
    assert num["edge_px"][1] == 3.0 and round(num["edge_px"][0], 2) == 885.37
    assert list(num["persist_dots"]) == [0, 0, 1, 0, 0]
    ratio = round(2.0 / C.VET_STAR_SIZE_ARCSEC[4][1], 2)
    assert round(num["size_ratio"][3], 2) == ratio
    assert num["flux_ratio"][4] == 5.0 and round(num["flux_ratio"][0], 3) == round(320 / 300, 3)
    out, _ = V.vet(s, s, None, None, None)
    w = S.Warnings(len(out))
    S.gate_warnings(out, w)
    assert w.codes == [[], ["edge"], ["persistence"], ["sharp"], ["flux_jump"]]
    assert w.values[1]["edge"] == {"edge_px": 3.0} and w.values[2]["persistence"] == {"dots_peak": 1, "n_dots": 2}
    assert w.values[3]["sharp"] == {"size_ratio": ratio, "outlier_at_peak": 1}
    assert w.values[4]["flux_jump"] == {"flux_ratio_max": 5.0}
    assert V.dot_numbers(s.drop(columns=list(V.DOT_LISTS)))["edge_px"].isna().all()


def test_d2_ramp_rows_give_an_edge_warning_only_when_the_motion_rests_on_them():
    """D2's last rows (y >= VET_D2_RAMP_Y, a background ramp): a warning when the mover's other dots span < 2 exposures
    and < 2 detectors; never a reason, never for another detector or a mover held by other exposures."""
    x = 1500.0
    rows = [clean(0, det=2, y=2010.0),                                                  # D2 two exposures, one in the band
            mover(1, [("E1", 2, x, 2010.0, 300.0, 8.0, 4.8, SOURCE), ("E2", 2, x + 3, 1895.0, 310.0, 8.0, 4.8, SOURCE),
                      ("E3", 2, x + 6, 1780.0, 320.0, 8.0, 4.8, SOURCE)]),              # two more exposures hold it
            mover(2, [("E1", 2, x, 2001.0, 300.0, 8.0, 4.8, SOURCE), ("E1", 5, x, 2001.0, 280.0, 8.0, 4.8, SOURCE)]),
            clean(3, det=4, y=2010.0),                                                  # D4 has no ramp
            clean(4, det=2, y=1970.0)]                                                  # above the band
    s = pd.DataFrame(rows)
    s.loc[2, "evidence"] = "two_detectors"
    num = V.dot_numbers(s)
    assert list(num["ramp_dots"]) == [1, 1, 1, 0, 0]
    assert list(num["ramp_rests"]) == [True, False, True, False, False]
    assert round(num["ramp_y"][0], 2) == 2010.37 and np.isnan(num["ramp_y"][3])
    out, _ = V.vet(s, s, None, None, None)
    assert "edge" not in ";".join(map(str, out["reasons"]))
    w = S.Warnings(len(out))
    S.gate_warnings(out, w)
    assert [("edge" in c) for c in w.codes] == [True, False, True, False, False]
    assert w.values[0]["edge"] == {"ramp_dots": 1, "ramp_y": 2010, "ramp_from_y": C.VET_D2_RAMP_Y, "ramp_detector": 2}
    assert w.levels[0] == {} and w.values[2]["edge"]["ramp_y"] == 2001
