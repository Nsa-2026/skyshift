"""The glow model in the pipeline (16 Nov; core.glow, contracts/dots.md "Glow model"): the dots step detects on IMAGE
minus the bright stars' glow with noise x k, drops the star's pieces and measures its own dot on IMAGE; the stack
scales its S/N maps by the local scatter around bright stars and replaces a saturated star's hole rim by the star."""

import numpy as np
import pandas as pd
import pytest
from scipy import ndimage

import skyshift_pipeline.config as C
from skyshift_core.flags import OVERFLOW, SUR_ERROR
from skyshift_pipeline import dots as D
from skyshift_pipeline import stack as S

from test_fine_v2 import FINE, FINE_NO_GLOW, _photo
from test_dots_extras import synthetic_mef  # noqa: F401

X0, Y0, R_CORE = 50.3, 50.6, 3.6


def _with_saturated_star(l2, lumps=0.35, amp=25.0, seed=3):
    """A saturated star at (X0, Y0) on the real cutout: masked core (SUR_ERROR, OVERFLOW at the centre) and a lopsided
    power-law glow (~0.4 MJy/sr at 10 px, 15x the sky noise, as Barnard's Star in D1) broken into PSF-sized lumps."""
    ny, nx = np.shape(l2.data)
    yy, xx = np.mgrid[:ny, :nx]
    r, th = np.hypot(xx - X0, yy - Y0), np.arctan2(yy - Y0, xx - X0)
    s = ndimage.gaussian_filter(np.random.default_rng(seed).normal(0, 1, (ny, nx)), 1.0)
    glow = amp / (1 + (r / 2) ** 2) ** 1.25 * (1 + 0.3 * np.cos(th - 0.7)) * (1 + lumps * s / s.std())
    glow[r < R_CORE] = 0
    data = np.asarray(l2.data, np.float64) + glow
    data[r < R_CORE] = 3000.0
    l2.data = data.astype(np.float32)
    l2.variance = (np.asarray(l2.variance, np.float64) + 5e-4 * glow).astype(np.float32)
    fl = np.asarray(l2.flags).copy()
    fl[r < R_CORE] |= SUR_ERROR
    fl[r < 1.2] |= OVERFLOW
    l2.flags = fl
    return r


def _near(df, xy, tol):
    if len(df) == 0 or len(xy) == 0:
        return np.zeros(len(df), bool)
    d = np.hypot(df.x.to_numpy()[:, None] - xy[None, :, 0], df.y.to_numpy()[:, None] - xy[None, :, 1])
    return d.min(axis=1) <= tol


def test_dots_on_a_saturated_stars_glow(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    base = D.dots_table(_photo(tmp_path / "a"), "X", 1, settings=FINE_NO_GLOW)  # the real sources, no star
    real = base[base.snr >= 8][["x", "y"]].to_numpy()
    l2 = _photo(tmp_path / "b")
    _with_saturated_star(l2, lumps=0.6, amp=100.0)
    old = D.dots_table(l2, "X", 1, settings=FINE_NO_GLOW)
    counts: dict = {}
    new = D.dots_table(l2, "X", 1, settings=FINE, counts=counts)
    assert counts["n_glow_stars"] >= 1 and counts["n_glow_saturated"] >= 1 and counts["n_glow_extended"] >= 1

    def ring(df, a, b):
        rr = np.hypot(df.x - X0, df.y - Y0).to_numpy()
        return df[(rr >= a) & (rr < b)]

    # dots in the glow with no dot of the star-free photo within 1.5 px (a crowded real cutout: 245 dots in 101 px)
    every = base[["x", "y"]].to_numpy()
    fake_old = int((~_near(ring(old, 6, 35), every, 1.5)).sum())
    fake_new = int((~_near(ring(new, 6, 35), every, 1.5)).sum())
    assert fake_old >= 6 and fake_new <= fake_old - 3
    # the real sources inside the glow (S/N >= 8 without it) stay
    inside = real[(np.hypot(real[:, 0] - X0, real[:, 1] - Y0) >= 12) & (np.hypot(real[:, 0] - X0, real[:, 1] - Y0) < 35)]
    rec_old = _near(pd.DataFrame(inside, columns=["x", "y"]), old[["x", "y"]].to_numpy(), 1.0).sum()
    rec_new = _near(pd.DataFrame(inside, columns=["x", "y"]), new[["x", "y"]].to_numpy(), 1.0).sum()
    assert len(inside) >= 50 and rec_new >= rec_old - 1
    # the star itself: one dot (without the model its masked core's rim gives several)
    assert len(ring(old, 0, 8)) >= 2 and len(ring(new, 0, 8)) == 1
    assert len(ring(new, 0, R_CORE + 1.5)) == 1
    meta = D.detect_meta(settings=FINE, counts=counts)
    assert meta["glow"] is True and meta["glow_config"]["r_max_px"] == C.GLOW_R_MAX_PX and meta["n_glow_pieces"] >= 0
    assert FINE.folder == "dots_deblend1e-4_cp3_g6_psf_glow"


def test_no_bright_star_means_the_same_dots(tmp_path):
    """A photo with nothing to model gives the v2 dots exactly (the glow step changes nothing there)."""
    l2 = _photo(tmp_path)
    old = D.dots_table(l2, "X", 1, settings=FINE_NO_GLOW)
    counts: dict = {}
    new = D.dots_table(l2, "X", 1, settings=FINE, counts=counts)
    if counts.get("n_glow_stars", 0) == 0:
        pd.testing.assert_frame_equal(old.reset_index(drop=True), new.reset_index(drop=True))
    else:  # the cutout's brightest star is modelled: only dots near it may change
        assert abs(len(new) - len(old)) <= 3


def _glow_map(n=240, hole=7.0, seed=0, lumps=4.0):
    """An S/N map (2" grid) around a saturated star: a hole (not searched) of radius `hole` px, a bright rim, glow
    leftovers that are coherent over the photos (smooth lumps of S/N ~lumps out to ~60 px) and real sources."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[:n, :n]
    c = n / 2 + 0.3
    r = np.hypot(xx - c, yy - c)
    s = ndimage.gaussian_filter(rng.normal(0, 1, (n, n)), 1.5)
    snr = rng.normal(0, 1, (n, n)) + lumps * s / s.std() * np.exp(-r / 30.0)
    snr += 300.0 * np.exp(-((r - hole - 1.5) / 1.5) ** 2) * (1 + 0.3 * np.cos(3 * np.arctan2(yy - c, xx - c)))
    a = np.linspace(0, 2 * np.pi, 8, endpoint=False)
    src = np.c_[c + 45 * np.cos(a), c + 45 * np.sin(a)]
    for x, y in src:  # real sources at 90": S/N 40
        snr += 40.0 * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * 1.6 ** 2))
    ok = r > hole
    m = S.Map("v1_d1", np.where(ok, snr, 0.0), np.full((n, n), np.nan), ok, 1.0)
    return m, c, src


def test_stack_local_significance_and_the_hole_rim():
    m, c, src = _glow_map()
    raw = S.peaks(m)
    m2, centres = S.local_significance(m, gaia_xy=(np.array([c]), np.array([c])))
    assert len(centres[0]) == 1 and abs(centres[0][0] - c) < 1e-6 and centres[2][0] >= 6  # the hole, at the Gaia star
    assert m2.k is not None and m2.k.max() > 1.5 and np.all(m2.k[~m.ok | (np.hypot(*np.mgrid[:240, :240] - c) > 100)] == 1)
    pk = S.rim_peaks(S.peaks(m2), m2, centres)
    rr = np.hypot(pk.x - c, pk.y - c).to_numpy()
    rraw = np.hypot(raw.x - c, raw.y - c).to_numpy()
    real = lambda p: _near(p, src, 2.0)  # noqa: E731
    fake_raw = int(((rraw > 15) & (rraw < 80) & ~real(raw)).sum())
    fake_new = int(((rr > 15) & (rr < 80) & ~real(pk)).sum())
    assert fake_raw >= 10 and fake_new <= fake_raw // 5
    assert real(pk).sum() >= 7                                     # the S/N 40 sources at 90" stay
    centre = pk[rr < 1e-6]
    assert len(centre) == 1 and not ((rr > 1e-6) & (rr <= centres[2][0] + C.STACK_GLOW_RIM_ARCSEC / 2)).any()


def test_stack_without_bright_stars_is_unchanged():
    rng = np.random.default_rng(2)
    m = S.Map("v1_d1", rng.normal(0, 1, (120, 120)), np.full((120, 120), np.nan), np.ones((120, 120), bool), 1.0)
    m2, centres = S.local_significance(m)
    assert m2 is m and len(centres[0]) == 0
    pk = S.peaks(m)
    pd.testing.assert_frame_equal(S.rim_peaks(pk, m, centres), pk)
