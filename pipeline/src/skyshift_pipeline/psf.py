"""ePSF photometry for every dot at once: a weighted linear fit of flux + a local constant.

Model on a (2h+1)² stamp around each dot (Expsupp eq. 24):  D_j = F · P(x_j − x, y_j − y) + B,
with P the photo's ePSF for the dot's zone (skyshift_core.level2.EPSF) at the dot's measured
position, which stays fixed. F is the total flux (P sums to 1 over all pixels), so F / aperture flux
is the aperture correction. Masked pixels are left out; each pixel's weight is 1 / σ², where
σ² = noise² + (model_frac · F · P)²: `noise` is the per-pixel noise (VARIANCE, or as in core.detect
max(√VARIANCE, background rms)), and the model term stands for the ePSF's few-percent shape error
on bright stars (the Tractor photometry in Expsupp 3.2 adds 3% for the same reason). With a model
term the fit runs twice: the first F sets the weights of the second.

Everything is vectorised over dots: per dot the 2 × 2 normal equations are solved in closed form.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from . import config as C


@dataclass
class PSFPhot:
    flux: NDArray[np.float64]  # total flux, MJy/sr summed over pixels (× ujy_per_mjysr -> µJy)
    flux_err: NDArray[np.float64]
    sky: NDArray[np.float64]  # fitted local constant, MJy/sr
    chi2_nu: NDArray[np.float64]  # χ² / (n_pix − 2)
    npix: NDArray[np.int64]  # unmasked pixels used


def gather(a: np.ndarray, ix: np.ndarray, iy: np.ndarray, half: int, fill=np.nan) -> tuple[np.ndarray, np.ndarray]:
    """(N, 2h+1, 2h+1) stamps of `a` around pixels (ix, iy), and a mask of pixels inside the array."""
    ny, nx = a.shape
    k = np.arange(-half, half + 1)
    yy = iy[:, None, None] + k[None, :, None]
    xx = ix[:, None, None] + k[None, None, :]
    inside = (xx >= 0) & (xx < nx) & (yy >= 0) & (yy < ny)
    v = a[np.clip(yy, 0, ny - 1), np.clip(xx, 0, nx - 1)]
    if fill is not None:
        v = np.where(inside, v, fill)
    return v, inside


def psf_photometry(data: np.ndarray, x: np.ndarray, y: np.ndarray, epsf, *, noise: np.ndarray,
                   bad: np.ndarray | None = None, half: int = C.PSF_FIT_HALF_PX,
                   model_frac: float = C.PSF_MODEL_FRAC, min_pix: int = C.PSF_FIT_MIN_PIX,
                   chunk: int = 20000) -> PSFPhot:
    """Fit F and B for each dot at 0-based (x, y). `noise`: per-pixel 1σ map (MJy/sr), same shape
    as data; `bad`: True where a pixel must be left out. NaN where fewer than min_pix pixels remain."""
    x = np.asarray(x, np.float64)
    y = np.asarray(y, np.float64)
    out = PSFPhot(*(np.full(x.size, np.nan) for _ in range(4)), np.zeros(x.size, np.int64))
    for s in range(0, x.size, chunk):
        sl = slice(s, s + chunk)
        _fit_chunk(data, x[sl], y[sl], epsf, noise, bad, half, model_frac, min_pix, out, sl)
    return out


def _fit_chunk(data, x, y, epsf, noise, bad, half, model_frac, min_pix, out: PSFPhot, sl: slice) -> None:
    P, ix, iy = epsf.stamps(x, y, half)
    D, inside = gather(data, ix, iy, half)
    S, _ = gather(noise, ix, iy, half)
    good = inside & np.isfinite(D) & np.isfinite(S) & (S > 0)
    if bad is not None:
        good &= ~gather(bad, ix, iy, half, fill=None)[0]
    D = np.where(good, D, 0.0).astype(np.float64)
    var0 = np.where(good, S.astype(np.float64) ** 2, 1.0)
    n = good.sum(axis=(1, 2))

    def solve(w):
        Spp = (w * P * P).sum(axis=(1, 2))
        Sp = (w * P).sum(axis=(1, 2))
        S1 = w.sum(axis=(1, 2))
        Spd = (w * P * D).sum(axis=(1, 2))
        Sd = (w * D).sum(axis=(1, 2))
        det = Spp * S1 - Sp * Sp
        with np.errstate(divide="ignore", invalid="ignore"):
            F = (S1 * Spd - Sp * Sd) / det
            B = (Spp * Sd - Sp * Spd) / det
            varF = S1 / det
        return F, B, varF, det

    w = np.where(good, 1.0 / var0, 0.0)
    F, B, varF, det = solve(w)
    if model_frac > 0:
        Fp = np.where(np.isfinite(F), np.maximum(F, 0.0), 0.0)[:, None, None]
        w = np.where(good, 1.0 / (var0 + (model_frac * Fp * P) ** 2), 0.0)
        F, B, varF, det = solve(w)
    r = D - F[:, None, None] * P - B[:, None, None]
    chi2 = (w * r * r).sum(axis=(1, 2))
    ok = (n >= min_pix) & (det > 0) & np.isfinite(F)
    with np.errstate(invalid="ignore", divide="ignore"):
        out.flux[sl] = np.where(ok, F, np.nan)
        out.flux_err[sl] = np.where(ok, np.sqrt(varF), np.nan)
        out.sky[sl] = np.where(ok, B, np.nan)
        out.chi2_nu[sl] = np.where(ok, chi2 / np.maximum(n - 2, 1), np.nan)
    out.npix[sl] = n
