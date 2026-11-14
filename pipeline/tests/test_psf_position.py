"""ePSF position fit (psf.psf_position, psf.dot_positions) and the dot table's v4 columns."""

import numpy as np
import pandas as pd
import pytest

import skyshift_pipeline.config as C
from skyshift_core import detect
from skyshift_core.level2 import EPSF
from skyshift_pipeline import dots as D
from skyshift_pipeline.psf import dot_positions, psf_position

from test_dots_extras import EPSF_FILE, epsf_hdu


@pytest.fixture(scope="module")
def E():
    return EPSF.from_hdu(epsf_hdu(EPSF_FILE)).shifted(1000, 1000)  # image (0, 0) = detector (1000, 1000)


def field(E, x, y, flux, sky=0.5, sigma=0.05, shape=(400, 400), seed=0):
    """Stars rendered from the ePSF at pixel centres (no pixel integration: it is in the ePSF) + noise."""
    rng = np.random.default_rng(seed)
    img = np.full(shape, sky)
    P, ix, iy = E.stamps(x, y, half=5)
    for k in range(len(x)):
        img[iy[k] - 5:iy[k] + 6, ix[k] - 5:ix[k] + 6] += flux * P[k]
    return img + rng.normal(0, sigma, shape)


def grid_stars(n_side=24, step=15.0, seed=0):
    """Stars on a grid with sub-pixel phases spread evenly over (-0.5, 0.5) in x and y."""
    rng = np.random.default_rng(seed)
    gx, gy = np.meshgrid(np.arange(12, 12 + step * n_side, step), np.arange(12, 12 + step * n_side, step))
    n = gx.size
    px = (np.arange(n) % 20 + 0.5) / 20 - 0.5
    py = rng.permutation(px)
    return gx.ravel() + px, gy.ravel() + py


def detected(img, tx, ty, min_snr=5.0):
    """Windowed centroids from core.detect matched to the true positions."""
    det = detect(img.astype(np.float32), None, thresh=2.0)
    det = det[det["snr"] >= min_snr]
    d = np.hypot(det["x"][:, None] - tx[None, :], det["y"][:, None] - ty[None, :])
    j = d.argmin(axis=1)
    m = d[np.arange(len(det)), j] < 1.5
    return det[m], j[m]


def phase_bias(err, phase):
    b = np.digitize(phase, np.linspace(-0.5, 0.5, 11)) - 1
    return np.array([np.median(err[b == k]) for k in range(10)])


def test_position_fit_has_no_pixel_phase_bias(E):
    tx, ty = grid_stars()
    img = field(E, tx, ty, flux=30.0)
    det, j = detected(img, tx, ty)
    assert len(det) > 500
    fit = psf_position(img, det["x"], det["y"], E, noise=np.full(img.shape, 0.05), model_frac=0.0)
    assert fit.ok.all() and (fit.n_iter <= 6).all()
    ex, ey = fit.x - tx[j], fit.y - ty[j]
    phx, phy = tx[j] - np.round(tx[j]), ty[j] - np.round(ty[j])
    # the windowed centroid is pulled towards pixel centres (an S-curve in phase); the fit is not
    cen = phase_bias(det["x"] - tx[j], phx)
    assert np.ptp(cen) > 0.05
    assert np.abs(phase_bias(ex, phx)).max() < 0.005 and np.abs(phase_bias(ey, phy)).max() < 0.005
    assert np.sqrt(np.mean(ex**2)) < 0.3 * np.sqrt(np.mean((det["x"] - tx[j]) ** 2))
    # errors from the covariance are right (no model term here: pure pixel noise)
    for e, s in ((ex, fit.x_err), (ey, fit.y_err)):
        assert 0.8 < np.std(e / s) < 1.25
    # flux and sky are recovered too
    assert abs(np.median(fit.flux) / 30.0 - 1) < 0.01 and abs(np.median(fit.sky) - 0.5) < 0.005


def test_position_fit_at_low_snr(E):
    """S/N 5–10: the fit still converges for nearly every dot, beats the centroid, and its errors hold."""
    tx, ty = grid_stars(seed=1)
    ex, ec, pulls, ok = [], [], [], []
    for seed in range(3):
        img = field(E, tx, ty, flux=1.4, seed=seed + 10)
        det, j = detected(img, tx, ty)
        det, j = det[det["snr"] < 10], j[det["snr"] < 10]
        fit = psf_position(img, det["x"], det["y"], E, noise=np.full(img.shape, 0.05))
        ok.append(fit.ok)
        ex.append(np.hypot(fit.x - tx[j], fit.y - ty[j])[fit.ok])
        ec.append(np.hypot(det["x"] - tx[j], det["y"] - ty[j])[fit.ok])
        pulls.append(((fit.x - tx[j]) / fit.x_err)[fit.ok])
    ok, ex, ec, pulls = map(np.concatenate, (ok, ex, ec, pulls))
    assert len(ok) > 500 and ok.mean() > 0.97
    assert np.sqrt(np.mean(ex**2)) < 0.85 * np.sqrt(np.mean(ec**2))
    assert 0.8 < 1.4826 * np.median(np.abs(pulls)) < 1.25


def test_masked_pixels_and_skipped_dots(E):
    tx, ty = np.array([20.3, 50.0, 80.0]), np.array([20.2, 50.0, 80.0])
    img = field(E, tx, ty, flux=50.0, sigma=0.02, shape=(100, 100))
    noise = np.full(img.shape, 0.02)
    bad = np.zeros(img.shape, bool)
    bad[20, 20] = True  # the core pixel of star 0
    bad[50, 51] = True  # a pixel beside star 1: an asymmetric mask must not pull the position
    bad[76:84, 77:84] = True  # star 2: its whole stamp but one column masked, fewer than 12 pixels left
    start_x, start_y = tx + 0.15, ty - 0.12
    fit = psf_position(img, start_x, start_y, E, noise=noise, bad=bad, model_frac=0.0)
    assert fit.npix[0] == 48 and fit.npix[1] == 48 and fit.npix[2] < C.PSF_FIT_MIN_PIX
    assert fit.ok[:2].all() and not fit.ok[2] and fit.n_iter[2] == 0 and np.isnan(fit.x[2])
    for k in range(2):
        assert abs(fit.x[k] - tx[k]) < 4 * fit.x_err[k] and abs(fit.y[k] - ty[k]) < 4 * fit.y_err[k]
        assert abs(fit.x[k] - tx[k]) < 0.02 and abs(fit.y[k] - ty[k]) < 0.02
    # `fit` false (e.g. saturated): not fitted; dot_positions then keeps the given position and error
    sat = np.array([False, True, False])
    xp, yp, err, ok = dot_positions(img, start_x, start_y, E, noise=noise, bad=bad, fit=~sat,
                                    pos_err=np.full(3, 0.7))
    assert ok[0] and not ok[1] and not ok[2]
    assert xp[1] == start_x[1] and yp[1] == start_y[1] and err[1] == 0.7 and err[2] == 0.7
    assert abs(xp[0] - tx[0]) < 0.02
    assert C.PSF_POS_ERR_FLOOR_ARCSEC <= err[0] < C.PSF_POS_ERR_FLOOR_ARCSEC + 0.05  # bright: the floor dominates


def test_fit_fails_cleanly_on_a_blank_spot(E):
    rng = np.random.default_rng(7)
    img = 0.5 + rng.normal(0, 0.05, (60, 60))
    fit = psf_position(img, [30.0, 20.0], [30.0, 40.0], E, noise=np.full(img.shape, 0.05))
    assert not fit.ok.any()  # no star: F <= 0 or wandering off, not a made-up position


def test_as_dots_fills_v4_columns_from_v3_frames():
    df = pd.DataFrame({"obs_id": ["a"], "detector": [1], "mjd": [61000.0], "session": [1], "visit": [1],
                       "ra": [10.0], "dec": [-5.0], "pos_err_arcsec": [0.4], "x": [3.5], "y": [7.25],
                       "snr": [12.0], "flags": [0], "flags_peak": [0], "cell": [1]})
    out = D.as_dots(df)
    assert out["x_psf"].iloc[0] == 3.5 and out["y_psf"].iloc[0] == 7.25
    assert out["ra_psf"].iloc[0] == 10.0 and out["dec_psf"].iloc[0] == -5.0
    assert out["pos_err_psf_arcsec"].iloc[0] == pytest.approx(0.4)
    assert not out["psf_pos_ok"].iloc[0] and out["sep_flag"].iloc[0] == 0
    assert str(out["psf_pos_ok"].dtype) == "bool" and str(out["sep_flag"].dtype) == "int16"
