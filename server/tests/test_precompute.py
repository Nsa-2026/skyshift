"""scripts/precompute helpers (no network)."""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "precompute"))
import precompute  # noqa: E402


def _row(det: int, mjd: float, ra: float = 10.0, dec: float = 5.0):
    return {"image": precompute._image(f"X_{mjd}", det, mjd - 0.0007, mjd + 0.0007, "https://irsa.example/a.fits",
                                       "dr1/a.fits", "spherex_dr1"), "ra": ra, "dec": dec, "v": 11.0}


def test_clean_makes_strict_json():
    out = precompute.clean({"a": float("nan"), "b": [np.float32(1.5), np.int64(3), float("inf")], "c": None})
    assert out == {"a": None, "b": [1.5, 3, None], "c": None}


def test_star_targets_are_centred_near_the_middle_of_the_data():
    t = {x["slug"]: x for x in precompute.star_targets()}
    assert set(t) == {"barnard", "epsindib", "wise0855", "rwaur", "rdra"}
    assert (t["barnard"]["ra"], t["barnard"]["dec"]) == (269.4462, 4.76883)
    # Epsilon Indi B moves 4.7"/yr: about 47" from its 2016.0 Gaia position
    assert 40 < abs(t["epsindib"]["dec"] - -56.79381206688221) * 3600 * 1.9 < 55
    assert t["rdra"]["dec"] == pytest.approx(66.755, abs=1e-3)


def test_densest_window_and_image():
    rows = [_row(2, 60900.0 + i * 0.07) for i in range(5)] + [_row(2, 60950.0 + i * 0.07) for i in range(9)]
    rows += [_row(5, 60900.0 + i * 0.01) for i in range(20)]
    win = precompute.densest_window(rows, 2, 1.0)
    assert len(win) == 9 and win[0]["image"].mjd == pytest.approx(60950.0)
    im = win[0]["image"]
    assert im.det == 2 and im.s3_url == "https://nasa-irsa-spherex.s3.us-east-1.amazonaws.com/dr1/a.fits"
    assert precompute.densest_window(rows, 3, 1.0) == []


def test_comet_rows_come_from_the_irsa_table():
    rows = precompute.comet_rows()
    assert len(rows) == 258  # 259 images, one in neither DR1 nor QR3
    assert {r["image"].det for r in rows} == {1, 2, 3, 4, 5, 6}
    seq = [r for r in rows if precompute.COMET_MJD[0] < r["image"].mjd < precompute.COMET_MJD[1]]
    assert 30 < len(seq) < 80 and all(r["image"].visit == 2 for r in seq)
