import numpy as np
import pandas as pd
import pytest

from skyshift_pipeline import config as C
from skyshift_pipeline.score import control_shift, match_dots, match_movers, recall_report, score_units, summarize_units
from skyshift_pipeline.sky import SkyTree, offset_radec

from conftest import t


def predictions():
    """4 bright asteroids (V<16) each seen in 2 photos of one session, plus a faint one."""
    rows = []
    objs = [("A", 14.5, 121.0, 15.0), ("B", 15.5, 121.5, 15.5), ("C", 15.0, 122.0, 16.0), ("D", 15.9, 122.5, 16.5),
            ("E", 17.5, 123.0, 17.0)]
    for name, v, ra, dec in objs:
        for e in range(2):
            ra_e, dec_e = offset_radec(ra, dec, 36.0 * e * 2.1 / 60, 0.0)  # 36″/h east
            rows.append(dict(name=name, number=1, v=v, ra=ra_e, dec=dec_e, dra_arcsec_h=36.0, ddec_arcsec_h=0.0,
                             edge_px=500.0, obs_id=f"{name}{e}", detector=1, mjd=t(1, e), **{"class": "MB"}))
    return pd.DataFrame(rows)


def test_recall_and_control():
    pred = predictions()
    p = score_units(pred, C.ZONES["hunt"], 18.0)
    assert p["unit"].nunique() == 5
    # movers: found A (2″ off) and B (at the session mean); C has only a dot; D nothing; E found
    mv = []
    for name, off in (("A", 2.0), ("B", 0.0), ("E", 0.0)):
        r = pred[pred["name"] == name].iloc[0]
        ra, dec = offset_radec(r["ra"], r["dec"], 36.0 * 1.05 / 60 + off, 0.0)
        mv.append(dict(ra=ra, dec=dec, mjd=r["mjd"] + 1.05 / 1440))
    movers = pd.DataFrame(mv)
    c = pred[pred["name"] == "C"].iloc[0]
    trees = {("C0", 1): SkyTree([c["ra"]], [c["dec"]]), ("A0", 1): SkyTree([], [])}
    ctl = control_shift(p)
    units = summarize_units(p, match_movers(p, movers), match_dots(p, trees), match_movers(ctl, movers), match_dots(ctl, trees))
    rep = recall_report(units)
    assert rep["n_known"] == 4
    assert rep["recall"] == pytest.approx(0.5)
    assert rep["dot_share"] == pytest.approx(0.25)
    assert rep["control_recall"] == 0.0 and rep["control_dot_share"] == 0.0
    assert rep["g2_pass"] is True
    bins = {b["v_max"]: b for b in rep["by_v"]}
    assert bins[15.0]["n"] == 1 and bins[18.0]["n"] == 1 and bins[18.0]["recall"] == 1.0


def test_g2_fails_below_half_and_edge_cut():
    pred = predictions()
    p = score_units(pred, C.ZONES["hunt"], C.SCORE_V_MAX)
    units = summarize_units(p, *(np.zeros(len(p), bool),) * 4)
    assert recall_report(units)["g2_pass"] is False
    pred.loc[pred["name"] == "A", "edge_px"] = 5.0
    assert "A" not in set(score_units(pred, C.ZONES["hunt"], C.SCORE_V_MAX)["name"])


def test_mover_in_another_session_does_not_count():
    pred = predictions()
    p = score_units(pred, C.ZONES["hunt"], C.SCORE_V_MAX)
    r = p.iloc[0]
    movers = pd.DataFrame([dict(ra=r["ra"], dec=r["dec"], mjd=r["mjd"] + 1.0)])
    assert not match_movers(p, movers).any()
