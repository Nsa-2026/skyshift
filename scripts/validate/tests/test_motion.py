"""Offline tests for _motion.py: the V6 and V7 lists, the predicted shift and the summary numbers."""

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import _motion as M  # noqa: E402


def _row(sid, pm, g, rp=None, plx=100.0, plx_err=0.5):
    return {"source_id": sid, "ra": 10.0, "dec": 5.0, "pmra": -pm, "pmdec": 0.0, "pm": pm, "parallax": plx,
            "parallax_error": plx_err, "phot_g_mean_mag": g, "phot_rp_mean_mag": rp}


def test_select_v6_fastest_fainter_than_g12():
    rows = [_row(str(i), 1000.0 + 100 * i, 12.5) for i in range(50)]
    rows += [_row("bright", 99999.0, 11.9), _row("nog", 99998.0, None)]
    got = M.select_v6(rows)
    assert len(got) == 40
    assert [r["source_id"] for r in got[:2]] == ["49", "48"]  # fastest first; G <= 12 and no G left out
    assert all(r["phot_g_mean_mag"] > 12 for r in got)


def test_select_v7_ultracool_and_fast():
    # absolute G = G + 5 log10(plx / 100): plx 100 mas -> M_G = G
    ok = _row("ucd", 1500.0, 18.0, rp=16.2)                     # M_G 18, G-RP 1.8
    slow = _row("slow", 900.0, 18.0, rp=16.2)                   # under 1"/yr
    star = _row("mdwarf", 2000.0, 15.0, rp=13.6)                # M_G 15: a late-M star, left out
    blue = _row("blue", 2000.0, 18.0, rp=17.0)                  # G-RP 1.0: a white dwarf, left out
    weak = _row("weakplx", 2000.0, 18.0, rp=16.2, plx_err=20.0)  # parallax at 5 sigma
    assert [r["source_id"] for r in M.select_v7([ok, slow, star, blue, weak])] == ["ucd"]


def test_abs_g():
    assert M.abs_g(15.0, 100.0) == pytest.approx(15.0)
    assert M.abs_g(15.0, 10.0) == pytest.approx(10.0)
    assert M.abs_g(15.0, -1.0) is None and M.abs_g(None, 10.0) is None


def test_predicted_shift_is_the_proper_motion_over_whole_years():
    m = M.gaia_model({"source_id": 1, "ra": 30.0, "dec": 20.0, "pmra": -3000.0, "pmdec": 1000.0, "parallax": 200.0})
    e, n = M.predicted_shift(m, 60900.0, 60900.0 + 365.25)
    # one year apart the parallax returns (almost) to the same place
    assert e == pytest.approx(-3.0, abs=0.02) and n == pytest.approx(1.0, abs=0.02)
    e2, n2 = M.predicted_shift(m, 60900.0, 60900.0 + 182.6)
    assert math.hypot(e2 + 1.5, n2 - 0.5) > 0.05  # half a year apart the parallax shows


def test_angle_and_pa():
    assert M.pa_deg(1.0, 0.0) == pytest.approx(90.0)
    assert M.pa_deg(-1.0, 0.0) == pytest.approx(270.0)
    assert M.angle_deg(1.0, 0.0, 0.0, 1.0) == pytest.approx(90.0)
    assert M.angle_deg(-1.0, 0.01, -1.0, -0.01) == pytest.approx(1.15, abs=0.01)  # across PA 270
    assert M.angle_deg(0.0, 1.0, 0.0, -1.0) == pytest.approx(180.0)


def test_slope_fit():
    pred = [(-5.0, 1.0), (3.0, 4.0), (0.5, -6.0)]
    f = M.slope_fit([(0.9 * e, 0.9 * n) for e, n in pred], pred, [0.5, 0.5, 0.5])
    assert f["n"] == 3 and f["slope"] == pytest.approx(0.9) and f["slope_err"] == pytest.approx(0.0, abs=1e-9)
    assert f["rms_arcsec"] == pytest.approx(math.sqrt(sum(0.01 * (e * e + n * n) for e, n in pred) / 3))
    # a precise star weighs more than a noisy one
    w = M.slope_fit([(5.0, 0.0), (10.0, 0.0)], [(5.0, 0.0), (5.0, 0.0)], [0.1, 10.0])
    assert w["slope"] == pytest.approx(1.0, abs=0.01)
    assert M.slope_fit([], [])["slope"] is None


def test_in_predicted_direction():
    assert M.in_predicted_direction({"shift_arcsec": 2.0, "sigma_arcsec": 0.5, "angle_deg": 20.0})
    assert not M.in_predicted_direction({"shift_arcsec": 2.0, "sigma_arcsec": 0.5, "angle_deg": 31.0})
    assert not M.in_predicted_direction({"shift_arcsec": 1.0, "sigma_arcsec": 0.5, "angle_deg": 5.0})  # 2 sigma
    assert not M.in_predicted_direction({"shift_arcsec": None, "sigma_arcsec": 0.5, "angle_deg": 5.0})
