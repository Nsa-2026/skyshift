import numpy as np
import pandas as pd
import pytest

from skyshift_pipeline import config as C
from skyshift_pipeline.score import (control_shift, fixed_member_rows, match_dot_kinds, match_dots, match_movers,
                                     recall_report, score_units, summarize_units)
from skyshift_pipeline.sky import SkyTree, offset_radec
from skyshift_pipeline.store import Store, results_rel

from conftest import t

OBJS = [("A", 14.5, 121.0, 15.0), ("B", 15.5, 121.5, 15.5), ("C", 15.0, 122.0, 16.0), ("D", 15.9, 122.5, 16.5),
        ("E", 17.5, 123.0, 17.0), ("F", 11.0, 123.5, 17.5)]


def predictions(n_exp=2):
    """Each object predicted in n_exp exposures of session 2, moving 36″/h east."""
    rows = []
    for name, v, ra, dec in OBJS:
        for e in range(n_exp):
            ra_e, dec_e = offset_radec(ra, dec, 36.0 * e * 2.1 / 60, 0.0)
            rows.append(dict(name=name, number=1, v=v, ra=ra_e, dec=dec_e, dra_arcsec_h=36.0, ddec_arcsec_h=0.0,
                             edge_px=500.0, obs_id=f"{name}{e}", detector=1, mjd=t(1, e), session=2, **{"class": "MB"}))
    return pd.DataFrame(rows)


def mover_at(pred, name, off=0.0, session=2):
    r = pred[pred["name"] == name].iloc[0]
    ra, dec = offset_radec(r["ra"], r["dec"], 36.0 * 1.05 / 60 + off, 0.0)
    return dict(ra=ra, dec=dec, mjd=r["mjd"] + 1.05 / 1440, session=session)


def test_recall_control_and_bins():
    pred = predictions()
    p = score_units(pred, C.ZONES["hunt"], 18.0)
    assert p["unit"].nunique() == 6
    movers = pd.DataFrame([mover_at(pred, "A", 2.0), mover_at(pred, "B"), mover_at(pred, "E"), mover_at(pred, "F")])
    c = pred[pred["name"] == "C"].iloc[0]
    trees = {("C0", 1): SkyTree([c["ra"]], [c["dec"]]), ("A0", 1): SkyTree([], [])}
    ctl = control_shift(p)
    units = summarize_units(p, match_movers(p, movers), match_dots(p, trees), match_movers(ctl, movers), match_dots(ctl, trees))
    rep = recall_report(units)
    # V < 16: A, B, C, D, F -> found A, B, F
    assert rep["n_known"] == 5 and rep["n_found"] == 3
    assert rep["recall"] == pytest.approx(0.6)
    assert rep["dot_share"] == pytest.approx(0.2)
    assert rep["control_recall"] == 0.0 and rep["control_dot_share"] == 0.0
    assert rep["g2_pass"] is True
    assert rep["per_object"]["n"] == 5 and rep["per_object"]["found"] == 3
    assert rep["v_12_to_16"]["n"] == 4 and rep["v_12_to_16"]["found"] == 2
    assert rep["v_lt_12_saturated"]["n"] == 1 and rep["v_lt_12_saturated"]["found"] == 1
    bins = {b["v_max"]: b for b in rep["by_v"]}
    assert bins[15.0]["n"] == 1 and bins[18.0]["n"] == 1 and bins[18.0]["recall"] == 1.0


def test_units_need_two_exposures_and_edge():
    assert score_units(predictions(n_exp=1), C.ZONES["hunt"], C.SCORE_V_MAX).empty
    pred = predictions()
    pred.loc[pred["name"] == "A", "edge_px"] = 5.0
    p = score_units(pred, C.ZONES["hunt"], C.SCORE_V_MAX)
    assert "A" not in set(p["name"])
    units = summarize_units(p, *(np.zeros(len(p), bool),) * 4)
    assert recall_report(units)["g2_pass"] is False


def test_mover_in_another_session_does_not_count():
    pred = predictions()
    p = score_units(pred, C.ZONES["hunt"], C.SCORE_V_MAX)
    movers = pd.DataFrame([mover_at(pred, "A", session=3)])
    assert not match_movers(p, movers).any()


def test_dots_inside_a_fixed_group_are_told_apart_from_free_dots():
    """A dot within 12″ that belongs to a fixed source's group (a star's) is not a free dot: recall.json's free_dot_share
    and the misses (no_dot, fixed_dot, free_dot) say which kind a missed unit had."""
    pred = predictions()
    p = score_units(pred, C.ZONES["hunt"], 18.0)
    movers = pd.DataFrame([mover_at(pred, "B")])
    a, c, d = (pred[pred["name"] == n].iloc[0] for n in ("A", "C", "D"))
    star = offset_radec(c["ra"], c["dec"], 0.0, 5.0)    # C: a star's dot 5″ off, nothing else
    free = offset_radec(d["ra"], d["dec"], 3.0, 0.0)     # D: a free dot 3″ off, and a star's dot 8″ off
    dstar = offset_radec(d["ra"], d["dec"], 0.0, -8.0)
    trees = {("C0", 1): SkyTree([star[0]], [star[1]]), ("D0", 1): SkyTree([free[0], dstar[0]], [free[1], dstar[1]]),
             ("A0", 1): SkyTree([], [])}
    in_fixed = {("C0", 1): np.array([True]), ("D0", 1): np.array([False, True]), ("A0", 1): np.zeros(0, bool)}
    kf, kx = match_dot_kinds(p, trees, in_fixed)
    ctl = control_shift(p)
    cf, cx = match_dot_kinds(ctl, trees, in_fixed)
    assert (kf | kx).tolist() == match_dots(p, trees).tolist()
    units = summarize_units(p, match_movers(p, movers), kf | kx, match_movers(ctl, movers), cf | cx, kinds=(kf, kx, cf, cx))
    u = units.set_index("name")
    assert u.loc["C", ["dot", "dot_free", "dot_fixed", "miss"]].tolist() == [True, False, True, "fixed_dot"]
    assert u.loc["D", ["dot", "dot_free", "dot_fixed", "miss"]].tolist() == [True, True, True, "free_dot"]
    assert u.loc["A", "miss"] == "no_dot" and u.loc["B", "miss"] == ""
    rep = recall_report(units)
    # V < 16: A, B, C, D, F: B found; C a star's dot only, D a free dot, A and F nothing
    assert rep["dot_share"] == pytest.approx(0.4) and rep["free_dot_share"] == pytest.approx(0.2)
    assert rep["misses"] == {"no_dot": 2, "fixed_dot": 1, "free_dot": 1}
    assert rep["control_free_dot_share"] == 0.0 and rep["per_object"]["free_dot_share"] == pytest.approx(0.2)
    assert "misses" in rep["by_v"][0] and "miss" not in summarize_units(p, *(np.zeros(len(p), bool),) * 4)


def test_fixed_member_rows_from_members(tmp_path):
    store = Store(str(tmp_path))
    zone = C.ZONES["hunt"]
    assert fixed_member_rows(store, zone, {("X", 1)}) is None  # no members table: dots have no kind
    m = pd.DataFrame({"source_id": [1, 1, 2, 3], "obs_id": pd.Categorical(["X", "X", "Y", "X"]),
                      "detector": np.array([1, 4, 1, 1], np.int8), "dot_row": np.array([5, 2, 7, 0], np.int32)})
    store.write_parquet(results_rel(zone.name, "members.parquet"), m)
    rows = fixed_member_rows(store, zone, {("X", 1), ("Y", 1), ("Z", 2)})
    assert sorted(rows) == [("X", 1), ("Y", 1)]
    assert sorted(rows[("X", 1)].tolist()) == [0, 5] and rows[("Y", 1)].tolist() == [7]
