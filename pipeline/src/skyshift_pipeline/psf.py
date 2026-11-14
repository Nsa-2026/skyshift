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

psf_position also fits the position (x, y, F, B), the standard method for undersampled data (Anderson &
King 2000): the windowed centroid is pulled toward pixel centres (an S-curve in pixel phase, up to
±0.09 px on the pilot), the ePSF already holds the pixel response, so its best-fit position is not.
Gauss–Newton from the windowed centroid on the same stamp, weights, mask and model term; F and B are
re-solved in closed form at each position, so each step only moves (x, y) (the Schur complement of
the 4 × 4 normal equations); derivatives come from the ePSF spline (EPSF.evaluate_grad). Steps are
capped at max_step and the fit fails if it wanders more than max_shift from the start.
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


# --- position fit ---------------------------------------------------------------------------------
@dataclass
class PSFPos:
    x: NDArray[np.float64]  # fitted position, 0-based pixels (NaN where not fitted)
    y: NDArray[np.float64]
    x_err: NDArray[np.float64]  # 1σ from the covariance of the (x, y, F, B) fit, pixels
    y_err: NDArray[np.float64]
    flux: NDArray[np.float64]  # F at the fitted position, MJy/sr summed over pixels
    flux_err: NDArray[np.float64]
    sky: NDArray[np.float64]
    chi2_nu: NDArray[np.float64]  # χ² / (n_pix − 4)
    npix: NDArray[np.int64]
    n_iter: NDArray[np.int64]  # Gauss–Newton steps taken (0 = not fitted)
    ok: NDArray[np.bool_]  # converged within max_iter, F > 0, moved <= max_shift, finite errors


def _sums(w, *a):
    return [(w * v).sum(axis=(1, 2)) for v in a]


def psf_position(data: np.ndarray, x: np.ndarray, y: np.ndarray, epsf, *, noise: np.ndarray,
                 bad: np.ndarray | None = None, fit: np.ndarray | None = None, half: int = C.PSF_FIT_HALF_PX,
                 model_frac: float = C.PSF_MODEL_FRAC, min_pix: int = C.PSF_FIT_MIN_PIX,
                 max_iter: int = C.PSF_POS_MAX_ITER, max_step: float = C.PSF_POS_MAX_STEP_PX,
                 max_shift: float = C.PSF_POS_MAX_SHIFT_PX, tol: float = C.PSF_POS_TOL_PX,
                 tol_sigma: float = C.PSF_POS_TOL_SIGMA, chunk: int = 10000) -> PSFPos:
    """Fit x, y, F, B for each dot, starting at 0-based (x, y) (the windowed centroid). Same stamp
    ((2h+1)² around the starting pixel), noise, mask and model term as psf_photometry. `fit`: boolean
    mask of the dots to fit (e.g. not saturated); the others get NaN, n_iter 0, ok False."""
    x = np.asarray(x, np.float64)
    y = np.asarray(y, np.float64)
    n = x.size
    out = PSFPos(*(np.full(n, np.nan) for _ in range(8)), np.zeros(n, np.int64), np.zeros(n, np.int64),
                 np.zeros(n, bool))
    idx = np.arange(n) if fit is None else np.flatnonzero(np.asarray(fit, bool))
    for s in range(0, idx.size, chunk):
        k = idx[s:s + chunk]
        _pos_chunk(data, x[k], y[k], epsf, noise, bad, half, model_frac, min_pix, max_iter, max_step,
                   max_shift, tol, tol_sigma, out, k)
    return out


def _pos_chunk(data, x0, y0, epsf, noise, bad, half, model_frac, min_pix, max_iter, max_step, max_shift, tol,
               tol_sigma, out: PSFPos, k: np.ndarray) -> None:
    ix, iy = np.rint(x0).astype(np.int64), np.rint(y0).astype(np.int64)  # the stamp stays put
    D, inside = gather(data, ix, iy, half)
    S, _ = gather(noise, ix, iy, half)
    good = inside & np.isfinite(D) & np.isfinite(S) & (S > 0)
    if bad is not None:
        good &= ~gather(bad, ix, iy, half, fill=None)[0]
    D = np.where(good, D, 0.0).astype(np.float64)
    var0 = np.where(good, S.astype(np.float64) ** 2, np.inf)  # weight 0 on left-out pixels
    npix = good.sum(axis=(1, 2))
    zone = epsf.zone(x0, y0)
    n = x0.size
    xc, yc = x0.copy(), y0.copy()
    F = np.full(n, np.nan)
    n_iter = np.zeros(n, np.int64)
    conv = np.zeros(n, bool)
    prev = np.zeros((n, 2))  # last step, to damp oscillations
    damp = np.ones(n)
    res = {c: np.full(n, np.nan) for c in ("F", "B", "chi2", "vx", "vy", "vF")}
    active = npix >= min_pix

    def weights(a, P, Fa):
        if model_frac <= 0:
            return 1.0 / var0[a]
        Fp = np.where(np.isfinite(Fa), np.maximum(Fa, 0.0), 0.0)[:, None, None]
        return 1.0 / (var0[a] + (model_frac * Fp * P) ** 2)

    def linear(w, P, Da):
        Spp, Sp, S1, Spd, Sd = _sums(w, P * P, P, 1.0, P * Da, Da)
        det = Spp * S1 - Sp * Sp
        with np.errstate(divide="ignore", invalid="ignore"):
            return (S1 * Spd - Sp * Sd) / det, (Spp * Sd - Sp * Spd) / det

    for it in range(max_iter):
        a = np.flatnonzero(active)
        if a.size == 0:
            break
        P, Gx, Gy = epsf.stamps_at(xc[a], yc[a], ix[a], iy[a], half, zone=zone[a], grad=True)
        Da = D[a]
        Fa = F[a]
        if it == 0:  # weights without the model term set the first F
            Fa = linear(weights(a, P, np.zeros(a.size)), P, Da)[0]
        w = weights(a, P, Fa)
        Fa, Ba = linear(w, P, Da)
        # (x, y) normal equations with F and B profiled out: the Schur complement of the 4 × 4
        Jx, Jy = -Fa[:, None, None] * Gx, -Fa[:, None, None] * Gy  # ∂model/∂x_source = −F ∂P/∂dx
        r = Da - Fa[:, None, None] * P - Ba[:, None, None]
        Spp, Sp, S1, Spx, Spy, S1x, S1y, Sxx, Sxy, Syy, bx, by, chi2 = _sums(
            w, P * P, P, 1.0, P * Jx, P * Jy, Jx, Jy, Jx * Jx, Jx * Jy, Jy * Jy, Jx * r, Jy * r, r * r)
        with np.errstate(divide="ignore", invalid="ignore"):
            det = Spp * S1 - Sp * Sp

            def q(ax, bx_, ay, by_):  # u A_ll⁻¹ v for A_ll = [[Spp, Sp], [Sp, S1]]
                return (ax * (S1 * ay - Sp * by_) + bx_ * (Spp * by_ - Sp * ay)) / det
            mxx = Sxx - q(Spx, S1x, Spx, S1x)
            mxy = Sxy - q(Spx, S1x, Spy, S1y)
            myy = Syy - q(Spy, S1y, Spy, S1y)
            dm = mxx * myy - mxy * mxy
            sx = (myy * bx - mxy * by) / dm
            sy = (mxx * by - mxy * bx) / dm
            step = np.hypot(sx, sy)
            sigma = np.sqrt(0.5 * (mxx + myy) / dm)  # 1σ per axis here
            # a step that reverses the last one without shrinking much: the fit oscillates (faint dots);
            # halve this one and the next ones
            osc = (sx * prev[a, 0] + sy * prev[a, 1] < 0) & (step * damp[a] > 0.5 * np.hypot(prev[a, 0], prev[a, 1]))
            damp[a] = np.where(osc, 0.5 * damp[a], damp[a])
            scale = np.where(step > max_step, max_step / step, 1.0) * damp[a]
            # F, B, χ² and the covariance at this point (the last step of a converged fit is < tol)
            res["F"][a], res["B"][a], res["chi2"][a] = Fa, Ba, chi2
            res["vx"][a], res["vy"][a] = myy / dm, mxx / dm  # the (x, y) block of the 4 × 4 inverse
            # var F = 1 / (A_FF − A_F,rest A_rest⁻¹ A_rest,F): F with B, x, y profiled out
            A3 = np.stack([np.stack([S1, S1x, S1y], -1), np.stack([S1x, Sxx, Sxy], -1),
                           np.stack([S1y, Sxy, Syy], -1)], -2)
            c3 = np.stack([Sp, Spx, Spy], -1)
            ok3 = np.isfinite(A3).all(axis=(1, 2)) & (np.linalg.det(np.where(np.isfinite(A3), A3, 0.0)) > 0)
            sol = np.full((a.size, 3), np.nan)
            if ok3.any():
                sol[ok3] = np.linalg.solve(A3[ok3], c3[ok3][:, :, None])[:, :, 0]
            res["vF"][a] = 1.0 / (Spp - (c3 * sol).sum(axis=1))
        bad_fit = ~(np.isfinite(sx) & np.isfinite(sy) & np.isfinite(Fa)) | (Fa <= 0) | ~(det > 0) | ~(dm > 0)
        sx, sy = np.where(bad_fit, 0.0, sx * scale), np.where(bad_fit, 0.0, sy * scale)
        xc[a] += sx
        yc[a] += sy
        prev[a, 0], prev[a, 1] = sx, sy
        F[a] = Fa
        n_iter[a] += 1
        # converged: the step is below tol, or below tol_sigma × its own 1σ (faint dots)
        done = ~bad_fit & ((step < tol) | (step * damp[a] < tol_sigma * sigma))
        far = np.hypot(xc[a] - x0[a], yc[a] - y0[a]) > max_shift
        conv[a[done & ~far]] = True
        active[a[done | bad_fit | far]] = False

    fitted = n_iter > 0
    with np.errstate(invalid="ignore"):
        ok = conv & fitted & (res["F"] > 0) & (res["vx"] > 0) & (res["vy"] > 0) & (res["vF"] > 0) \
            & (np.hypot(xc - x0, yc - y0) <= max_shift)
        out.x[k] = np.where(fitted, xc, np.nan)
        out.y[k] = np.where(fitted, yc, np.nan)
        out.x_err[k], out.y_err[k] = np.sqrt(res["vx"]), np.sqrt(res["vy"])
        out.flux[k], out.flux_err[k], out.sky[k] = res["F"], np.sqrt(res["vF"]), res["B"]
        out.chi2_nu[k] = res["chi2"] / np.maximum(npix - 4, 1)
    out.ok[k] = ok
    out.npix[k] = npix
    out.n_iter[k] = n_iter


def dot_positions(data: np.ndarray, x: np.ndarray, y: np.ndarray, epsf, *, noise: np.ndarray, bad: np.ndarray,
                  fit: np.ndarray, pos_err: np.ndarray,
                  pixel_arcsec: float = C.PIXEL_ARCSEC) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """The dot table's x_psf, y_psf, pos_err_psf_arcsec, psf_pos_ok (contracts/dots.md): the ePSF
    position fit where it ran (`fit`) and converged, else the given (x, y) and pos_err (windowed or,
    for saturated dots, wings centroid). The error is the fit's 1σ per axis, √((σx² + σy²) / 2),
    in quadrature with PSF_POS_ERR_FLOOR_ARCSEC."""
    p = psf_position(data, x, y, epsf, noise=noise, bad=bad, fit=fit)
    ok = p.ok
    err = np.sqrt(0.5 * (p.x_err ** 2 + p.y_err ** 2)) * pixel_arcsec
    err = np.sqrt(err ** 2 + C.PSF_POS_ERR_FLOOR_ARCSEC ** 2)
    return (np.where(ok, p.x, x), np.where(ok, p.y, y), np.where(ok, err, pos_err), ok)
