"""core.glow: a saturated star's glow modelled and taken out before detection; the noise factor; a bright star's
own pixels left alone. Synthetic images: a saturated star (masked core) with a lopsided power-law halo, optionally
broken into PSF-sized lumps (what real glows are cut into), faint real stars inside the halo and in the field."""

from __future__ import annotations

import numpy as np
import pytest
from scipy import ndimage

from skyshift_core import detect
from skyshift_core.flags import OVERFLOW, STREAK, SUR_ERROR
from skyshift_core.glow import STAR_DTYPE, Glow, aperture_z, bright_stars, fit_glow, noise_factor
from skyshift_core.level2 import EPSF

from conftest import gaussian_image

SIGMA_PSF = 0.6
N = 180
X0, Y0 = 90.3, 88.6
NOISE = 0.05
SKY = 0.3
AMP = 120.0
R_CORE = 4.2


def gauss_epsf(sigma: float = SIGMA_PSF) -> EPSF:
    """A Gaussian ePSF in the EPSF HDU's convention (33 x 33 samples at 1/5 px, sum 1)."""
    t = (np.arange(33) - 16) / 5
    X, Y = np.meshgrid(t, t)
    p = np.exp(-(X ** 2 + Y ** 2) / (2 * sigma ** 2))
    return EPSF(psf=(p / p.sum())[None], xcenter=np.array([N / 2]), ycenter=np.array([N / 2]), oversample=(5, 5))


def polar():
    yy, xx = np.mgrid[:N, :N]
    return np.hypot(xx - X0, yy - Y0), np.arctan2(yy - Y0, xx - X0)


def halo(r: np.ndarray, th: np.ndarray, amp: float = AMP) -> np.ndarray:
    """A bright star's wings beyond the ePSF: a power law out to ~30 px, 30 % brighter on one side."""
    return amp / (1.0 + (r / 2.0) ** 2) ** 1.25 * (1.0 + 0.3 * np.cos(th - 0.7))


def scene(seed: int = 0, with_halo: bool = True, lumps: float = 0.0):
    """(data, flags, variance, faint star positions inside the halo, field star positions). lumps: the halo's
    PSF-sized structure, as a fraction of the halo (not in VARIANCE: it is not noise the pipeline knows of)."""
    rng = np.random.default_rng(seed)
    r, th = polar()
    img = np.full((N, N), SKY)
    if with_halo:
        h = halo(r, th)
        if lumps:
            s = ndimage.gaussian_filter(np.random.default_rng(100 + seed).normal(0, 1, (N, N)), 1.0)
            h = h * (1.0 + lumps * s / s.std() * (r >= R_CORE))
        img += h
    rs, a = rng.uniform(9.0, 26.0, 14), rng.uniform(0, 2 * np.pi, 14)
    inner = np.c_[X0 + rs * np.cos(a), Y0 + rs * np.sin(a)]
    keep = [0]
    for i in range(1, len(inner)):  # keep them apart from each other
        if np.min(np.hypot(*(inner[keep] - inner[i]).T)) > 5:
            keep.append(i)
    inner = inner[keep]
    field = np.c_[rng.uniform(10, 40, 6), rng.uniform(10, N - 10, 6)]
    img += gaussian_image((N, N), inner[:, 0], inner[:, 1], np.full(len(inner), 4.0), SIGMA_PSF)  # S/N ~25
    img += gaussian_image((N, N), field[:, 0], field[:, 1], np.full(len(field), 4.0), SIGMA_PSF)
    var = np.full((N, N), NOISE ** 2)
    if with_halo:
        var += np.maximum(img - SKY, 0) * 0.002  # the glow's own Poisson noise (VARIANCE holds it)
    img += rng.normal(0, 1, (N, N)) * np.sqrt(var)
    flags = np.zeros((N, N), np.int32)
    if with_halo:
        flags[r < R_CORE] |= SUR_ERROR   # the masked core (Barnard's Star: SUR_ERROR / TRANSIENT around OVERFLOW)
        flags[r < 1.5] |= OVERFLOW
        img[r < R_CORE] = 5000.0
    return img.astype(np.float32), flags, var.astype(np.float32), inner, field


def old_and_new(img, flags, var):
    """Detection as the dots step does it (2 sigma, fine deblending, S/N >= 5) on the image, and on image - glow with
    noise x k, pieces of the bright star dropped. Returns (old, new, own, glow)."""
    kw = dict(thresh=2.0, deblend_cont=1e-4, clean_param=3.0)
    old = detect(img, flags, variance=var, **kw)
    old = old[old["snr"] >= 5]
    g = fit_glow(img, flags, np.sqrt(var))
    model, k = g.outside()
    new = detect(img - model, flags, variance=np.where(k > 1, var * k ** 2, var), **kw)
    new = new[new["snr"] >= 5]
    own, piece = g.own(new["x"], new["y"], 2.0)
    return old, new[~piece], own[~piece], g


def _matches(det, pts, tol=1.0):
    if len(det) == 0 or len(pts) == 0:
        return np.zeros(len(pts), bool)
    d = np.hypot(det["x"][None, :] - pts[:, 0:1], det["y"][None, :] - pts[:, 1:2])
    return d.min(axis=1) <= tol


def _fakes(det, truth, r_lo=6, r_hi=40):
    """Detections between r_lo and r_hi px of the bright star with no real star within 1.5 px."""
    rd = np.hypot(det["x"] - X0, det["y"] - Y0)
    sel = (rd >= r_lo) & (rd < r_hi)
    if not sel.any():
        return 0
    d = np.hypot(det["x"][sel][:, None] - truth[None, :, 0], det["y"][sel][:, None] - truth[None, :, 1]).min(axis=1)
    return int((d > 1.5).sum())


def test_bright_stars_finds_the_saturated_core_and_a_bright_peak():
    img, flags, var, _, _ = scene()
    img = img + gaussian_image((N, N), [150.2], [150.7], [400.0], SIGMA_PSF).astype(np.float32)  # unsaturated, bright
    st = bright_stars(img, flags, np.sqrt(var), min_peak_snr=300)
    assert st[0]["saturated"] and np.hypot(st[0]["x"] - X0, st[0]["y"] - Y0) < 0.5
    assert 3.5 < st[0]["r_core"] < 5.0
    un = st[~st["saturated"]]
    assert len(un) >= 1 and np.min(np.hypot(un["x"] - 150.2, un["y"] - 150.7)) <= 1.0
    # nothing bright: nothing to model; a lone flagged pixel with nothing bright around is no star
    calm = np.random.default_rng(1).normal(SKY, NOISE, (60, 60))
    assert len(bright_stars(calm, np.zeros((60, 60), np.int32), np.full((60, 60), NOISE))) == 0
    fl = np.zeros((60, 60), np.int32)
    fl[30, 30] = OVERFLOW
    assert len(bright_stars(calm, fl, np.full((60, 60), NOISE))) == 0


def test_a_masked_streak_through_a_saturated_pixel_is_not_a_huge_core():
    """Real case (2025W43_2A_0389_2D6): a STREAK-masked satellite trail with one OVERFLOW pixel was one 1,085 px blob,
    r_core 135 px, and Galatea (S/N 137) 43 px away became a 'piece' of it and was dropped."""
    img, flags, var, _, _ = scene(with_halo=False)
    img = img + gaussian_image((N, N), [120.2], [60.4], [300.0], SIGMA_PSF).astype(np.float32)  # the bright asteroid
    flags[:, 88:91] |= STREAK                       # a trail along the whole frame ...
    img[:, 88:91] += 50.0
    flags[100, 89] |= OVERFLOW                      # ... saturated in one pixel
    st = bright_stars(img, flags, np.sqrt(var), min_peak_snr=1e9)
    sat = st[st["saturated"]]
    assert len(sat) <= 1 and (len(sat) == 0 or sat[0]["r_core"] <= 8)
    if len(sat):
        g = Glow(model=np.zeros((N, N)), k=np.ones((N, N)), stars=sat)
        assert g.near([120.2], [60.4], 2.0)[0] == -1   # 50 px away: not a piece of it


def test_glow_model_follows_the_lopsided_halo_and_leaves_the_field_alone():
    img, flags, var, inner, field = scene()
    g = fit_glow(img, flags, np.sqrt(var))
    r, th = polar()
    s = g.stars[0]
    assert s["saturated"] and s["r_glow"] >= 25
    true = halo(r, th)
    for a, b in ((6, 9), (9, 14), (14, 20)):
        for side in (np.cos(th - 0.7) > 0.7, np.cos(th - 0.7) < -0.7):  # the bright side and the faint side
            ring = (r >= a) & (r < b) & side
            assert np.median(g.model[ring]) == pytest.approx(np.median(true[ring]), rel=0.15)
    # what is left beyond 10 px is at the noise (robust scatter: the faint stars in the ring are not glow)
    ring = (r >= 10) & (r < 16)
    left = (img - SKY - g.model)[ring]
    assert 1.4826 * np.median(np.abs(left - np.median(left))) < 1.3 * np.median(np.sqrt(var[ring]))
    # far from the star: no model, k = 1
    assert np.all(g.model[r > 45] == 0) and np.all(g.k[r > 45] == 1)


def test_a_lumpy_glow_is_no_longer_cut_into_stars():
    img, flags, var, inner, field = scene(seed=1, lumps=0.3)
    old, new, own, g = old_and_new(img, flags, var)
    truth = np.r_[inner, field, [[X0, Y0]]]
    f_old, f_new = _fakes(old, truth), _fakes(new, truth)
    assert f_old >= 5             # the glow's lumps pass as stars
    assert f_new <= 1
    assert _matches(new, field).all()  # the field is untouched
    assert own.sum() == 1 and np.hypot(new["x"][own] - X0, new["y"][own] - Y0)[0] < 6  # the star itself, once
    assert g.stars[0]["k_max"] > 2   # the lumps are noise the variance did not hold


def test_stars_inside_a_smooth_glow_are_kept():
    hit_old = hit_new = n = fakes = 0
    for seed in range(4):
        img, flags, var, inner, field = scene(seed=seed)
        old, new, own, g = old_and_new(img, flags, var)
        far = np.hypot(inner[:, 0] - X0, inner[:, 1] - Y0) >= 12   # inside 12 px the glow is ~1 MJy/sr: confused
        hit_old += _matches(old, inner[far]).sum()
        hit_new += _matches(new, inner[far]).sum()
        n += far.sum()
        fakes += _fakes(new, np.r_[inner, field, [[X0, Y0]]])
        assert _matches(new, field).all()
    assert hit_new >= hit_old and hit_new >= 0.85 * n
    assert fakes == 0


def test_without_a_bright_star_nothing_changes():
    img, flags, var, inner, field = scene(with_halo=False)
    g = fit_glow(img, flags, np.sqrt(var))
    assert len(g.stars) == 0 and not g.active
    assert np.all(g.model == 0) and np.all(g.k == 1)


def test_epsf_part_of_an_unsaturated_bright_star():
    E = gauss_epsf()
    img = np.full((N, N), SKY)
    xs, ys, F = 70.3, 101.6, 900.0
    P, ix, iy = E.stamps([xs], [ys], half=4)
    img[iy[0] - 4:iy[0] + 5, ix[0] - 4:ix[0] + 5] += F * P[0]
    img += np.random.default_rng(2).normal(0, NOISE, (N, N))
    st = np.zeros(1, STAR_DTYPE)
    st["x"], st["y"] = 70.0, 102.0
    g = fit_glow(img, None, np.full((N, N), NOISE), epsf=E, stars=st)
    s = g.stars[0]
    assert s["epsf_ok"] and s["flux_epsf"] == pytest.approx(F, rel=0.03)
    assert np.hypot(s["x"] - xs, s["y"] - ys) < 0.05
    # its own pixels stay as observed for detection
    model, k = g.outside(3.5)
    yy, xx = np.mgrid[:N, :N]
    assert np.all(model[np.hypot(xx - s["x"], yy - s["y"]) <= 3.4] == 0)


def test_noise_factor_is_one_on_noise_and_follows_local_scatter():
    rng = np.random.default_rng(4)
    z = rng.normal(0, 1, (200, 200))
    k, r_k, kmax = noise_factor(z, [100.0], [100.0], [0.0], 40.0)
    assert kmax[0] < 1.15
    yy, xx = np.mgrid[:200, :200]
    r = np.hypot(xx - 100, yy - 100)
    z2 = np.where(r < 15, 3.0 * z, z)
    k2, r_k2, kmax2 = noise_factor(z2, [100.0], [100.0], [0.0], 40.0, r_keep=3.0)
    assert np.median(k2[(r > 5) & (r < 12)]) == pytest.approx(3.0, rel=0.2)
    assert np.all(k2[r < 3.0] == 1)  # the star's own pixels
    assert np.all(k2[r > 25] == 1) and 14 <= r_k2[0] <= 24


def test_aperture_z_is_unit_on_noise():
    rng = np.random.default_rng(5)
    noise = np.full((150, 150), 0.2)
    z = aperture_z(rng.normal(0, 0.2, (150, 150)), noise, np.zeros((150, 150), bool))
    v = z[np.isfinite(z)]
    assert 1.4826 * np.median(np.abs(v - np.median(v))) == pytest.approx(1.0, rel=0.1)


def test_own_and_pieces():
    st = np.zeros(2, STAR_DTYPE)
    st[0]["x"], st[0]["y"], st[0]["saturated"], st[0]["r_core"] = 50.0, 50.0, True, 4.0
    st[1]["x"], st[1]["y"] = 20.0, 20.0
    g = Glow(np.zeros((100, 100)), np.ones((100, 100)), st)
    x = np.array([50.4, 53.0, 47.5, 60.0, 20.5, 21.9, 23.0])
    y = np.array([50.2, 50.0, 49.0, 50.0, 20.0, 20.0, 20.0])
    own, piece = g.own(x, y, 2.0)
    assert list(own) == [True, False, False, False, True, False, False]
    assert list(piece) == [False, True, True, False, False, True, False]  # within 4 + 1.5 px / 2 px
    np.testing.assert_allclose(g.keep_radius(3.5), [5.5, 3.5])
    np.testing.assert_allclose(g.keep_radius(3.5, None), [3.5, 3.5])  # the stack: a saturated rim is modelled too
