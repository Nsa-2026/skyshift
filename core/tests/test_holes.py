"""core.holes: unflagged pixels far below the sky (MWIR cold pixels) masked; real sources and noise never."""

from __future__ import annotations

import numpy as np

from skyshift_core.flags import OUTLIER
from skyshift_core.holes import dark_holes


def _frame(seed=0, n=200):
    rng = np.random.default_rng(seed)
    sub = rng.normal(0, 1, (n, n))
    yy, xx = np.mgrid[:n, :n]
    for x, y, f in ((50, 60, 400.0), (120, 40, 30.0), (150, 150, 2000.0)):  # stars (sigma 0.8 px)
        sub += f / (2 * np.pi * 0.64) * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * 0.64))
    return sub, np.ones((n, n)), np.zeros((n, n), bool)


def test_single_and_small_holes_masked_stars_and_big_dips_not():
    sub, noise, bad = _frame()
    cold = [(10, 10), (52, 61), (100, 30), (180, 20)]  # one right next to a star's core
    for x, y in cold:
        sub[y, x] = -40.0
    sub[90, 90] = sub[90, 91] = -15.0   # a 2-pixel hole
    sub[30:40, 150:160] = -12.0         # a 100-pixel dip: background structure, not a hole
    sub[170, 170] = -4.0                # above the 5 sigma line
    bad[5, 5] = True
    sub[5, 5] = -100.0                  # already bad: not counted again
    m, info = dark_holes(sub, noise, bad)
    for x, y in cold:
        assert m[y, x]
    assert m[90, 90] and m[90, 91]
    assert not m[30:40, 150:160].any() and not m[170, 170] and not m[5, 5]
    assert info["n_holes"] == 5 and info["n_px"] == 6 and info["depth_min_sigma"] == -40.0
    # no growth: no pixel of the stars is taken, the cold pixel beside one star is its only masked neighbour
    assert m.sum() == 6
    assert not m[59:62, 49:52].any() or m[61, 52]


def test_pure_noise_masks_nothing():
    rng = np.random.default_rng(3)
    sub = rng.normal(0, 1, (1000, 1000))
    m, info = dark_holes(sub, np.ones_like(sub), np.zeros(sub.shape, bool))
    assert m.sum() == (sub < -5).sum() <= 5 and info["n_holes"] == m.sum()  # only its own few pixels below -5 sigma
    assert not dark_holes(sub, np.ones_like(sub), np.zeros(sub.shape, bool), sigma=7.0)[0].any()


def test_near_bits_and_growth_options():
    sub, noise, bad = _frame(1)
    sub[10, 10] = sub[100, 100] = -50.0
    flags = np.zeros(sub.shape, np.int64)
    flags[101, 101] = OUTLIER
    m, info = dark_holes(sub, noise, bad, flags, near_bits=OUTLIER, near_px=2)
    assert m[100, 100] and not m[10, 10] and info["n_holes"] == 1
    m2, _ = dark_holes(sub, noise, bad, None, near_bits=OUTLIER)  # no flags: nothing can be near
    assert not m2.any()
    m3, info3 = dark_holes(sub, noise, bad, grow_px=1)
    assert m3[9:12, 9:12].all() and info3["n_px"] == 18
