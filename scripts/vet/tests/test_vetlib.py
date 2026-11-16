"""Offline tests of the vetting harness' pure helpers. Run: cd pipeline && uv run pytest ../scripts/vet/tests"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import vetlib as V  # noqa: E402


def test_numbers_verdict_static_mover_unclear():
    assert V.numbers_verdict({"det": [18, 19], "dif": [1, 1], "ref": [20, 18, 19, 21]}) == "static"
    assert V.numbers_verdict({"det": [140, 250], "dif": [145, 262], "ref": [1, 0, 2, 0]}) == "mover"
    # an asteroid crossing a faint star keeps most of its signal in the difference: still a mover
    assert V.numbers_verdict({"det": [304, 153], "dif": [260, 129], "ref": [34, 20, 46, 31]}) == "mover"
    assert V.numbers_verdict({"det": [9], "dif": [4], "ref": [3, 4, 6, 4]}) == "unclear"
    assert V.numbers_verdict({"det": [9], "dif": [], "ref": []}) == "unclear"  # no reference exposures


def test_source_ids_and_zone():
    assert V.source_ids("hunt-sp-66757007063700") == [66757007063700]
    assert V.source_ids("ecl34-pm-53109020936200-53104020935700") == [53109020936200, 53104020935700]
    assert V.zone_of("ecl15n1-pm-63516008931800") == "ecl15n1"


def test_grid_is_north_up_east_left():
    ra, dec = V.grid(120.0, 10.0)
    assert ra.shape == (V.N, V.N)
    assert dec[V.N - 1, V.C0] > dec[0, V.C0]          # row index grows to the north
    assert ra[V.C0, 0] > ra[V.C0, V.N - 1]            # column 0 is east
    gx, gy = V.to_grid(120.0, 10.0, ra[5, 7], dec[5, 7])
    assert abs(gx - 7) < 1e-3 and abs(gy - 5) < 1e-3  # to_grid inverts grid


def test_ap_snr_finds_a_source_and_reports_masked():
    rng = np.random.default_rng(0)
    a = rng.normal(0, 1.0, (V.N, V.N))
    a[V.C0, V.C0] += 40.0
    assert V.ap_snr(a, 1.0, [(V.C0, V.C0)]) > 10
    assert abs(V.ap_snr(a, 1.0, [(5, 5)])) < 4
    a[V.C0 - 2:V.C0 + 3, V.C0 - 2:V.C0 + 3] = np.nan
    assert np.isnan(V.ap_snr(a, 1.0, [(V.C0, V.C0)]))


def test_fmt_dt_units():
    assert V.fmt_dt(0.035) == "+2.1 min" and V.fmt_dt(-11.3) == "-11.3 h" and V.fmt_dt(24 * 172) == "+172 d"


def test_numbers_verdict_hot_pixel_on_a_fixed_star_is_not_a_mover():
    # round 1, ecl05-679040028059: other times at +27..+32, a pixel hit kept the difference high
    assert V.numbers_verdict({"det": [37, 89], "dif": [20, 54], "ref": [27, 32, 30, 29]}) == "unclear"


def _dot(n, det, x, y, mjd, snr=20.0, size=3.5, chi2=1.0):
    return {"n": n, "det": det, "x": x, "y": y, "mjd": mjd, "snr": snr, "size": size, "chi2": chi2, "obs": f"o{n}", "flux": 1.0, "fpk": 0}


def test_warnings_name_the_round_1_failure_modes():
    dots = [_dot(1, 4, 1361.0, 66.0, 61017.0052, size=2.0, chi2=12.0), _dot(2, 4, 1361.0, 66.0, 61017.0067)]
    rows = [{"mjd": 61017.0052, "det": 1, "dot": None, "snr": 0.2, "px": (1359.1, 77.3)},
            {"mjd": 61017.0052, "det": 4, "dot": dots[0], "snr": 63.0, "px": (1361.0, 66.1)},
            {"mjd": 61017.0067, "det": 1, "dot": None, "snr": 1.0, "px": (1359.2, 77.2)},
            {"mjd": 61017.0067, "det": 4, "dot": dots[1], "snr": 59.0, "px": (1361.0, 65.9)}]
    W = " ".join(V.warnings_for(None, dots, rows, {"ref": [3, 1, 0, -2], "_motion_sigma": 1.2}, None))
    for key in ("WHOLE-PIXEL POSITION", "SAME DETECTOR PIXEL", "NOT STAR-LIKE", "PAIRED DETECTOR EMPTY", "ONE DETECTOR ONLY",
                "NO TELESCOPE STEP", "STATIONARY"):
        assert key in W, key
    assert "SOURCE AT OTHER TIMES" not in W
    assert V.session_steps(rows) and max(V.session_steps(rows)) < 1


def test_warnings_gap_between_detections():
    dots = [_dot(1, 6, 100.3, 500.2, 61000.0000), _dot(2, 6, 100.4, 155.1, 61000.0045)]
    rows = [{"mjd": 61000.0000, "det": 6, "dot": dots[0], "snr": 20.0, "px": (100.3, 500.2)},
            {"mjd": 61000.0015, "det": 6, "dot": None, "snr": 0.5, "px": (100.3, 385.0)},
            {"mjd": 61000.0030, "det": 6, "dot": None, "snr": 1.0, "px": (100.3, 270.0)},
            {"mjd": 61000.0045, "det": 6, "dot": dots[1], "snr": 18.0, "px": (100.4, 155.1)}]
    W = " ".join(V.warnings_for(None, dots, rows, {"ref": [0, 1]}, None))
    assert "GAP BETWEEN DETECTIONS" in W and "NO TELESCOPE STEP" not in W
