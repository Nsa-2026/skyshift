"""coadd.py (session coadds, simultaneous pairs, shift-and-stack) on synthetic photos. Offline."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from astropy.io import fits
from astropy.wcs import WCS
from skyshift_core.level2 import EPSF

from skyshift_pipeline import coadd as CO
from skyshift_pipeline import config as C
from skyshift_pipeline import stack as S

PIX = C.STACK_NATIVE_PIX_ARCSEC
SIGMA_PX = 0.55   # synthetic ePSF: Gaussian σ in native px (FWHM 8.0″, about SPHEREx's 7.5″)
RA0, DEC0 = 150.0, 10.0
N_PX = 90


def gauss_epsf() -> EPSF:
    ov = 5
    t = (np.arange(33) - 16) / ov
    X, Y = np.meshgrid(t, t)
    p = np.exp(-(X ** 2 + Y ** 2) / (2 * SIGMA_PX ** 2))
    return EPSF(psf=(p / p.sum())[None], xcenter=np.array([N_PX / 2]), ycenter=np.array([N_PX / 2]),
                oversample=(ov, ov))


def wcs_at(dx_px: float, dy_px: float) -> WCS:
    """A photo whose pointing is offset by (dx, dy) native px from the reference (the session's 11.8′ steps)."""
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [RA0, DEC0]
    w.wcs.crpix = [N_PX / 2 + 0.5 + dx_px, N_PX / 2 + 0.5 + dy_px]
    w.wcs.cdelt = [-PIX / 3600.0, PIX / 3600.0]
    return w


def photo(rng, dx_px, dy_px, stars, noise=1.0, mjd=61000.0) -> S.Cut:
    """A background-subtracted photo block with Gaussian noise and point sources (ra, dec, flux in pixel units)."""
    w = wcs_at(dx_px, dy_px)
    img = rng.normal(0.0, noise, (N_PX, N_PX))
    yy, xx = np.mgrid[0:N_PX, 0:N_PX]
    for ra, dec, f in stars:
        x, y = w.world_to_pixel_values(ra, dec)
        img += f * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * SIGMA_PX ** 2)) / (2 * math.pi * SIGMA_PX ** 2)
    meta = {"obs_id": f"syn{dx_px}_{dy_px}_{mjd}", "detector": 1, "mjd": mjd, "conv": 889.0}
    return S.Cut(meta, img.astype(np.float32), np.full((N_PX, N_PX), noise, np.float32), np.zeros((N_PX, N_PX), np.int32),
                 w, gauss_epsf())


def at(dx_arcsec: float, dy_arcsec: float) -> tuple[float, float]:
    return RA0 + dx_arcsec / 3600.0 / math.cos(math.radians(DEC0)), DEC0 + dy_arcsec / 3600.0


def kernel() -> np.ndarray:
    return S.psf_kernel(gauss_epsf())


def test_groups_and_pairs():
    p = pd.DataFrame({"obs_id": ["a", "a", "b", "b", "c"], "detector": [1, 4, 1, 4, 2], "session": [1, 1, 1, 1, 2],
                      "mjd": [1.0, 1.0, 1.0015, 1.0015, 2.0]})
    g = CO.session_groups(p)
    assert g[(1, 1)] == ["a", "b"] and g[(1, 4)] == ["a", "b"] and g[(2, 2)] == ["c"]
    assert CO.simultaneous_pairs(p) == [("a", 1, 4), ("b", 1, 4)]


def test_noise_map_is_normalised_and_negative_peaks_are_rare():
    rng = np.random.default_rng(1)
    grid = CO.small_grid(RA0, DEC0, 200.0)
    m = CO.detection_map("noise", CO.accumulate([photo(rng, 0, 0, [])], grid), kernel(), background=False)
    v = m.snr[m.ok]
    assert np.std(v) == pytest.approx(1.0, abs=0.15)
    assert len(CO.find_peaks(m, 5.0, negative=True)) == 0


def test_coadd_gains_sqrt_n_on_a_faint_star():
    """Four exposures stepped 7 px apart (each lands the star on other pixels): S/N × ~2 and the flux error / 2."""
    ra, dec = at(10.0, -6.0)
    snr1, snr4, err1, err4 = [], [], [], []
    grid = CO.small_grid(RA0, DEC0, 200.0)
    K = kernel()
    for seed in range(6):
        rng = np.random.default_rng(10 + seed)
        cuts = [photo(rng, 7.0 * k, 0.3 * k, [(ra, dec, 6.0)], mjd=61000 + k * 0.0015) for k in range(4)]
        gx, gy = grid.xy(ra, dec)
        m1 = CO.detection_map("one", CO.accumulate(cuts[:1], grid), K, background=False)
        m4 = CO.detection_map("four", CO.accumulate(cuts, grid), K, background=False)
        snr1.append(CO.local_peak(m1, float(gx), float(gy), 2.0)[0])
        snr4.append(CO.local_peak(m4, float(gx), float(gy), 2.0)[0])
        inner = np.zeros(m1.ok.shape, bool)
        inner[60:-60, 60:-60] = True
        err1.append(np.nanmedian(CO.flux_err(m1)[inner & m1.ok]))
        err4.append(np.nanmedian(CO.flux_err(m4)[inner & m4.ok]))
    assert np.mean(snr4) / np.mean(snr1) == pytest.approx(2.0, rel=0.25)
    assert np.mean(err1) / np.mean(err4) == pytest.approx(2.0, rel=0.2)


def test_limit_ab_from_the_flux_error():
    rng = np.random.default_rng(4)
    grid = CO.small_grid(RA0, DEC0, 200.0)
    m = CO.detection_map("one", CO.accumulate([photo(rng, 0, 0, [], noise=0.5)], grid), kernel(), background=False)
    e = np.nanmedian(CO.flux_err(m)[m.ok])
    assert CO.limit_ab(m, 889.0) == pytest.approx(23.9 - 2.5 * math.log10(5 * e * 889.0), abs=1e-9)
    # noise 0.5 MJy/sr per pixel, PSF fit over ~4πσ² pixels: σ_F ≈ 0.5 × √(4π σ²) ≈ 0.98 pixel units
    assert e == pytest.approx(0.5 * math.sqrt(4 * math.pi * SIGMA_PX ** 2), rel=0.3)


def test_positive_chi2_matches_monte_carlo():
    rng = np.random.default_rng(5)
    z = rng.normal(size=(400_000, 2))
    x = np.square(np.clip(z, 0, None)).sum(1)
    for t in (1.0, 4.0, 9.0):
        assert CO.positive_chi2_sf(np.array([t]), 2)[0] == pytest.approx((x > t).mean(), rel=0.05)
    # one map: P(max(z,0)² > t²) = P(z > t)
    assert CO.z_of_p(CO.positive_chi2_sf(np.array([25.0]), 1))[0] == pytest.approx(5.0, abs=1e-6)


def test_pair_sum_and_chi2():
    """Two maps of the same sky with a source at S/N 4 in each: sum → ~5.7, χ² → ~5.2 (it pays for any colour)."""
    ok = np.ones((40, 40), bool)
    rng = np.random.default_rng(6)
    a, b = rng.normal(size=(40, 40)), rng.normal(size=(40, 40))
    a[20, 20] = b[20, 20] = 4.0
    ma, mb = S.Map("a", a, np.ones_like(a), ok, 1.0), S.Map("b", b, np.ones_like(b), ok, 1.0)
    s = CO.combine_maps("sum", [ma, mb], "sum")
    c = CO.combine_maps("chi2", [ma, mb], "chi2")
    assert s.snr[20, 20] == pytest.approx(8 / math.sqrt(2) / s.sigma, rel=1e-6)
    assert 5.0 < c.snr[20, 20] < 5.5
    # one side only (an extreme colour): the sum halves the S/N, χ² keeps most of it
    a[20, 20], b[20, 20] = 6.0, 0.0
    s = CO.combine_maps("sum", [ma, mb], "sum")
    c = CO.combine_maps("chi2", [ma, mb], "chi2")
    assert s.snr[20, 20] < 4.7 < 5.5 < c.snr[20, 20]


def test_shift_and_stack_recovers_a_mover():
    """A source moving 9″ per exposure (3 grid px at 2″): the static coadd smears it, the tracked coadd doesn't."""
    rng = np.random.default_rng(7)
    grid = CO.small_grid(RA0, DEC0, 200.0)
    K = kernel()
    mjd = 61000 + np.arange(4) * 2.15 / 1440
    step = 9.0  # arcsec per exposure, westwards (grid x grows west)
    cuts = [photo(rng, 7.0 * k, 0, [at(-step * k, 0.0) + (8.0,)], mjd=mjd[k]) for k in range(4)]
    rate_px_h = (step / C.STACK_PIX_ARCSEC) / (2.15 / 60)
    static = CO.detection_map("static", CO.accumulate(cuts, grid), K, background=False)
    tracked = CO.detection_map("tracked", CO.accumulate(cuts, grid, CO.track_shifts(mjd, mjd[0], rate_px_h, 0.0)), K,
                               background=False)
    gx, gy = grid.xy(*at(0.0, 0.0))
    s_static = CO.local_peak(static, float(gx) + 4.5, float(gy), 8.0)[0]
    s_track = CO.local_peak(tracked, float(gx), float(gy), 2.0)[0]
    assert s_track > 1.3 * s_static
    assert s_track > 6.0


def test_linear_trials_cover_every_rate_within_tolerance():
    span, tol = 48.0, 1.5
    tr = CO.linear_trials(2.0, span, tol, rate_min=0.1)
    assert len(tr) == pytest.approx(CO.n_linear_trials(2.0, span, tol, 0.1), rel=0.15)
    rng = np.random.default_rng(8)
    r = rng.uniform(0.1, 2.0, 500)
    a = rng.uniform(0, 2 * math.pi, 500)
    v = np.c_[r * np.cos(a), r * np.sin(a)]
    d, _ = CO.nearest(v[:, 0], v[:, 1], tr[:, 0], tr[:, 1])
    assert np.max(d * span / 2) <= tol + 1e-9


def test_distance_trials_step_in_inverse_distance():
    mjd = 61000 + np.linspace(0, 20, 60)
    t = CO.distance_trials(mjd, 125.6, 19.4, 70.0, 1000.0, 2.0)
    assert t.d_au.max() == pytest.approx(1000.0) and t.d_au.min() <= 72.0
    inv = 1.0 / t.d_au
    assert np.allclose(np.diff(inv), 2 * 2.0 / t.p_max_arcsec)
    assert t.n >= len(t.d_au)
    P = CO.parallax_offsets(mjd, 125.6, 19.4)
    assert P.shape == (60, 2) and np.isfinite(P).all()
    # Earth moves ~0.34 AU in 20 days: up to ~0.17 AU from the middle time → ≤ ~36,000″ at 1 AU, a few × 10⁴
    assert 5_000 < t.p_max_arcsec < 40_000


def test_coverage_counts_photos_not_splat_hits():
    """Four photos of one sky: every grid pixel well inside all four is counted 4 (one photo's pixels hit only ~40%
    of a 2″ grid, so counting hits would leave most of the coadd unsearched)."""
    rng = np.random.default_rng(9)
    grid = CO.small_grid(RA0, DEC0, 200.0)
    cuts = [photo(rng, 0.37 * k, 0.21 * k, []) for k in range(4)]
    acc = CO.accumulate(cuts, grid)
    inner = acc.n[40:-40, 40:-40]
    assert (inner == 4).mean() > 0.99
    m = CO.detection_map("four", acc, kernel(), min_photos=4, background=False)
    assert m.ok[40:-40, 40:-40].mean() > 0.99


def test_chi2_significance_is_finite_and_monotone_far_out():
    x = np.array([25.0, 100.0, 1e3, 1e4, 1e6])
    z = CO.z_of_logp(CO.positive_chi2_logsf(x, 2))
    assert np.all(np.isfinite(z)) and np.all(np.diff(z) > 0)
    assert z[0] == pytest.approx(CO.z_of_p(CO.positive_chi2_sf(x[:1], 2))[0], abs=1e-6)
    assert z[-1] == pytest.approx(1000.0, rel=0.01)  # ~√x far out
