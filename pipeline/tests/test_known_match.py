"""known.py's mover ↔ known body rule (contracts/api.md v3.23 `status` known) and the suspects' known cut that uses it:
a known body of ANY brightness removes a mover only when it is certain (place at every dot, rate, direction);
by position alone (30″) only bodies brighter than MPC_V_MAX, as before the cache kept V ≤ 25."""

import numpy as np
import pandas as pd
import pytest

from skyshift_pipeline import config as C
from skyshift_pipeline import known
from skyshift_pipeline.sky import angsep, offset_radec


def test_judge_applies_the_contract_rule():
    ok = known.judge([0.5, 1.1, 0.2, 1.0], 104.9, 76.4, 100.2, 83.6)  # 306P vs the ecl04 grade A mover
    assert ok["certain"] and ok["sep_max"] == pytest.approx(1.1) and ok["dpa"] == pytest.approx(7.2)
    assert not known.judge([0.5, 10.5], 30.0, 90.0, 30.0, 90.0)["certain"]          # one dot 10.5″ off
    assert not known.judge([0.5, np.nan], 30.0, 90.0, 30.0, 90.0)["certain"]       # not predicted at one dot
    assert not known.judge([1.0, 1.0], 30.0, 90.0, 37.0, 90.0)["certain"]          # rate 23% off
    assert known.judge([1.0, 1.0], 30.0, 90.0, 35.9, 90.0)["certain"]              # 19.7%
    assert not known.judge([1.0, 1.0], 30.0, 355.0, 30.0, 11.0)["certain"]         # 16° apart across north
    assert known.judge([1.0, 1.0], 30.0, 355.0, 30.0, 9.0)["certain"]              # 14°
    one = known.judge([3.0], None, None, 30.0, 90.0)  # a two-detector mover (one moment, no rate): place alone
    assert one["certain"] and one["rate_ratio"] is None and one["dpa"] is None


def test_brighter_keeps_old_behaviour_for_position_only_matching():
    pred = pd.DataFrame({"name": ["a", "b", "c", "d"], "v": [20.9, 21.0, 24.0, np.nan]})
    assert known.brighter(pred)["name"].tolist() == ["a", "d"]  # comets/planets (no V) stay


def test_dot_places_follow_the_line():
    ra, dec = known.dot_places(44.93, 21.75, 60906.9, 100.0, 90.0, [60906.9 - 1 / 24, 60906.9, 60906.9 + 1 / 24])
    assert angsep(ra[0], dec[0], 44.93, 21.75) * 3600 == pytest.approx(100.0, rel=1e-4)
    assert ra[2] > 44.93 > ra[0] and dec[1] == pytest.approx(21.75)
    r2, d2 = known.dot_places(1.0, 2.0, 0.0, None, None, [0.0, 1.0])
    assert r2.tolist() == [1.0, 1.0] and d2.tolist() == [2.0, 2.0]


def _mover(ra=44.93, dec=21.75, mjd=60906.9, rate=105.0, pa=76.0, session=124, n=4):
    t = mjd + (np.arange(n) - (n - 1) / 2) * 2.1 / 1440
    return {"ra": ra, "dec": dec, "mjd": mjd, "session": session, "rate_arcsec_h": rate, "pa_deg": pa, "dot_mjd": t}


def _pred(name, ra, dec, mjd, rate, pa, v, session=124, photos=4):
    """load_predictions rows: the body in each photo of the session at that photo's time, with its rates."""
    e, n = rate * np.sin(np.radians(pa)), rate * np.cos(np.radians(pa))
    t = mjd + (np.arange(photos) - (photos - 1) / 2) * 2.1 / 1440
    h = (t - mjd) * 24
    r, d = offset_radec(np.full(photos, ra), np.full(photos, dec), e * h, n * h)
    return pd.DataFrame({"name": name, "number": None, "ra": r, "dec": d, "v": v, "dra_arcsec_h": e, "ddec_arcsec_h": n,
                         "class": "MBA", "edge_px": 100.0, "obs_id": [f"x_{k}" for k in range(photos)], "detector": 6,
                         "mjd": t, "session": session})


def test_certain_known_finds_a_faint_body_moving_like_the_mover():
    mv = pd.DataFrame([_mover()])
    # the body 3″ east of the mover, same motion within the tolerances, V 23 (the old cache dropped it)
    ra3, dec3 = offset_radec(44.93, 21.75, 3.0, 0.0)
    pred = pd.concat([_pred("2019 XY1", float(ra3), float(dec3), 60906.9, 110.0, 80.0, 23.0),
                      _pred("(161055)", 44.93 + 0.1, 21.75, 60906.9, 34.0, 92.0, 20.1)], ignore_index=True)
    got = known.certain_known(mv, pred)
    assert got["known_name"].tolist() == ["2019 XY1"] and got["known_sep_max"][0] == pytest.approx(3.0, abs=0.5)  # the motions differ a little
    assert got["known_v"][0] == 23.0


def test_certain_known_needs_the_motion_and_the_session():
    mv = pd.DataFrame([_mover()])
    ra3, dec3 = offset_radec(44.93, 21.75, 3.0, 0.0)
    wrong_dir = _pred("A", float(ra3), float(dec3), 60906.9, 105.0, 120.0, 22.0)    # 44° off: may be a neighbour, not it
    other_session = _pred("B", float(ra3), float(dec3), 60906.9, 105.0, 76.0, 22.0, session=125)
    assert known.certain_known(mv, pd.concat([wrong_dir, other_session], ignore_index=True))["known_name"].tolist() == [""]
    assert known.certain_known(mv.iloc[:0], wrong_dir).empty


def test_suspects_cut_faint_known_only_when_certain(monkeypatch):
    from skyshift_pipeline import suspects

    monkeypatch.setattr(suspects, "near_planet_names", lambda m: np.full(len(m), "", dtype=object))
    m1 = _mover()                                  # a faint known body moves with it: cut
    m2 = _mover(ra=45.5, dec=21.0, pa=200.0)       # a faint known body 20″ away moving elsewhere: kept
    movers = pd.DataFrame([m1, m2]).assign(flags=0, flags_peak_all=0)
    ra3, dec3 = offset_radec(44.93, 21.75, 3.0, 0.0)
    ra20, dec20 = offset_radec(45.5, 21.0, 20.0, 0.0)
    pred = pd.concat([_pred("2019 XY1", float(ra3), float(dec3), 60906.9, 108.0, 78.0, 23.5),
                      _pred("2020 AB", float(ra20), float(dec20), 60906.9, 30.0, 90.0, 23.5)], ignore_index=True)
    fixed = pd.DataFrame({"ra": [10.0], "dec": [10.0], "snr": [5.0]})
    s, st = suspects.select(movers, fixed, pred)
    assert len(s) == 1 and s["ra"].tolist() == [45.5]
    assert st["cut_known"] == 1 and st["cut_known_certain_only"] == 1
    assert np.isnan(s["nearest_known_arcsec"][0])  # the V 23.5 body is not reported as the nearest (V < MPC_V_MAX only)
    # a bright body 20″ away still cuts by position alone (KNOWN_ARCSEC 30″), as before
    pred2 = pd.concat([pred, _pred("(5) Astraea", float(ra20), float(dec20), 60906.9, 30.0, 90.0, 12.0)], ignore_index=True)
    s2, st2 = suspects.select(movers, fixed, pred2)
    assert len(s2) == 0 and st2["cut_known"] == 2 and C.KNOWN_ARCSEC == 30.0
