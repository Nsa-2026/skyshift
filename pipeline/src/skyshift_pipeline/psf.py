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
the 4 × 4 normal equations); derivatives come from the ePSF spline (EPSF.stamps_at, one evaluation per
step). Steps are capped at max_step; a step that reverses the last one without shrinking (faint dots
oscillate) halves the following ones; the fit fails if it wanders more than max_shift from the start.
F, B, χ² and the errors are those of the last evaluation (within one step < tol of the returned x, y).
On 6 pilot photos: median 3 steps, 98–99% of unsaturated dots converge, ~27 µs per dot (Apple M-series).
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


# --- ePSF second pass (fine detection v2, contracts/dots.md) ---------------------------------------
@dataclass
class Companions:
    dots: np.ndarray          # new dots, core.detect.DETECTION_DTYPE, measured on the residual, brightest first
    model: np.ndarray         # the subtracted ePSF models (MJy/sr), same shape as the image
    info: dict


def _discs(shape: tuple[int, int], x: np.ndarray, y: np.ndarray, r: float) -> np.ndarray:
    """Boolean map: pixels within r of any (x, y) (rounded centres)."""
    out = np.zeros(shape, bool)
    if x.size == 0 or r <= 0:
        return out
    ny, nx = shape
    h = int(np.ceil(r))
    k = np.arange(-h, h + 1)
    disc = (k[:, None] ** 2 + k[None, :] ** 2) <= r * r
    yy = np.rint(y).astype(np.int64)[:, None, None] + k[None, :, None]
    xx = np.rint(x).astype(np.int64)[:, None, None] + k[None, None, :]
    yy, xx = np.broadcast_arrays(yy, xx)
    ins = (xx >= 0) & (xx < nx) & (yy >= 0) & (yy < ny) & disc[None]
    out[yy[ins], xx[ins]] = True
    return out


def residual_companions(sub: np.ndarray, noise: np.ndarray, bad: np.ndarray, x: np.ndarray, y: np.ndarray,
                        snr: np.ndarray, saturated: np.ndarray, epsf, *, thresh: float = C.DETECT_THRESH,
                        min_snr: float = C.DOT_MIN_SNR, primary_snr: float = C.PSF_PASS_PRIMARY_SNR,
                        r_px: float = C.PSF_PASS_R_PX, model_frac: float = C.PSF_PASS_MODEL_FRAC,
                        half: int = C.PSF_PASS_STAMP_HALF, minarea: int = C.PSF_PASS_MINAREA,
                        deblend_cont: float = C.PSF_PASS_DEBLEND_CONT, dedup_px: float = C.PSF_PASS_DEDUP_PX,
                        sat_r_px: float = C.PSF_PASS_SAT_R_PX, mirror_r_px: float = C.PSF_PASS_MIRROR_R_PX,
                        mirror_frac: float = C.PSF_PASS_MIRROR_FRAC,
                        mirror_sigma: float = C.PSF_PASS_MIRROR_SIGMA) -> Companions:
    """The ePSF second pass: faint sources sitting 1.5–6 px from a brighter dot, which sep merges into it (the PSF is
    undersampled, FWHM 1.22 px, and sep's deblending needs a saddle the pair often lacks).

    sub: the image core.detect measures on (IMAGE minus its background map, bad pixels 0); noise: per-pixel 1σ
    (max(√VARIANCE, background rms)); bad: the bad-pixel mask; (x, y, snr, saturated): the first pass's dots (S/N >=
    min_snr; saturated = OVERFLOW/NONLINEAR in the 3×3). The ePSF of every unsaturated dot with S/N >= primary_snr is
    fitted (position, then flux) and subtracted; sep runs on the residual inside discs of r_px around those dots, minus
    discs of sat_r_px around saturated dots; noise √(noise² + (model_frac · model)²). Kept: new dots with residual
    aperture S/N >= min_snr, > dedup_px from every first-pass dot, passing the mirror test (config PSF_PASS_*).
    The caller applies the saturation guard and sets the sep_flag bit."""
    import sep
    from scipy.spatial import cKDTree
    from skyshift_core.detect import DETECTION_DTYPE, measure_objects

    sub = np.ascontiguousarray(sub, dtype=np.float64)
    noise = np.ascontiguousarray(np.where(np.isfinite(noise), noise, 0.0), dtype=np.float64)
    bad = np.ascontiguousarray(bad, dtype=bool)
    x, y = np.asarray(x, np.float64), np.asarray(y, np.float64)
    snr, saturated = np.asarray(snr, np.float64), np.asarray(saturated, bool)
    shape = sub.shape
    empty = Companions(np.zeros(0, dtype=DETECTION_DTYPE), np.zeros(shape), {"n_primary": 0, "n_raw": 0, "n_kept": 0})
    use = (snr >= primary_snr) & ~saturated
    if not use.any():
        return empty
    xp, yp = x[use], y[use]
    p = psf_position(sub, xp, yp, epsf, noise=noise, bad=bad)
    xs, ys = np.where(p.ok, p.x, xp), np.where(p.ok, p.y, yp)
    ph = psf_photometry(sub, xs, ys, epsf, noise=noise, bad=bad)
    F = np.where(np.isfinite(ph.flux) & (ph.flux > 0), ph.flux, 0.0)
    P, ix, iy = epsf.stamps(xs, ys, half)
    ny, nx = shape
    k = np.arange(-half, half + 1)
    YY, XX = np.broadcast_arrays(iy[:, None, None] + k[None, :, None], ix[:, None, None] + k[None, None, :])
    inside = (XX >= 0) & (XX < nx) & (YY >= 0) & (YY < ny)
    model = np.zeros(shape)
    np.add.at(model, (YY[inside], XX[inside]), (F[:, None, None] * P)[inside])
    resid = np.ascontiguousarray(sub - model)
    resid[bad] = 0.0
    noise2 = np.ascontiguousarray(np.sqrt(noise ** 2 + (model_frac * model) ** 2))
    region = _discs(shape, xs, ys, r_px) & ~_discs(shape, x[saturated], y[saturated], sat_r_px)
    m2 = np.ascontiguousarray(bad | ~region)
    info = {"n_primary": int(use.sum()), "n_psf_ok": int(p.ok.sum())}
    if not region.any():
        return Companions(empty.dots, model, {**info, "n_raw": 0, "n_kept": 0})
    objs = sep.extract(resid, thresh, err=noise2, mask=m2, minarea=minarea, deblend_cont=deblend_cont,
                       filter_type="conv")
    new = measure_objects(resid, noise2, m2, objs)
    new = new[new["snr"] >= min_snr]
    if len(new) and x.size:
        d, _ = cKDTree(np.c_[x, y]).query(np.c_[new["x"], new["y"]], k=1)
        new = new[d > dedup_px]
    info["n_raw"] = int(len(new))
    if len(new):
        _, j = cKDTree(np.c_[xs, ys]).query(np.c_[new["x"], new["y"]], k=1)
        mx, my = 2 * xs[j] - new["x"], 2 * ys[j] - new["y"]
        nxs, nys = new["x"].astype(np.float64), new["y"].astype(np.float64)
        fn, en, _ = sep.sum_circle(resid, nxs, nys, mirror_r_px, err=noise2, mask=bad, subpix=5)
        fm, em, _ = sep.sum_circle(resid, mx.astype(np.float64), my.astype(np.float64), mirror_r_px, err=noise2,
                                   mask=bad, subpix=5)
        keep = (fm < mirror_frac * fn) & ((fn - fm) >= mirror_sigma * np.sqrt(en ** 2 + em ** 2))
        new = new[keep]
    info["n_kept"] = int(len(new))
    return Companions(new, model, info)


# --- extraction v2: the ePSF-matched filter (docs/extraction-v2.md) --------------------------------------------------

def _gauss_kernel(half: int, phase: tuple[float, float] = (0.0, 0.0)) -> np.ndarray:
    """Fallback kernel without an ePSF: a Gaussian of the PSF's FWHM (1.22 px), sum 1, source at `phase` px from the
    central pixel's centre."""
    from skyshift_core.detect import PSF_SIGMA_PX
    k = np.arange(-half, half + 1, dtype=np.float64)
    g = np.exp(-0.5 * ((k[None, :] - phase[0]) ** 2 + (k[:, None] - phase[1]) ** 2) / PSF_SIGMA_PX ** 2)
    return g / g.sum()


def mf_phases(n: int = C.MF_PHASES) -> list[tuple[float, float]]:
    """Sub-pixel source positions the matched filter tries: n per axis (1: the pixel centre; 2: also half a pixel
    right / up / both). The PSF is undersampled (FWHM 1.22 px): a star on a pixel corner loses part of its S/N to a
    pixel-centred kernel."""
    o = [k / n for k in range(n)]
    return [(a, b) for b in o for a in o]


def mf_kernel(epsf, zone: int, half: int = C.MF_KERNEL_HALF, phase: tuple[float, float] = (0.0, 0.0)) -> np.ndarray:
    """(2h+1)² ePSF (flux fractions per native pixel) of a source at `phase` (dx, dy) px from the central pixel's
    centre, for one ePSF zone."""
    if epsf is None or zone < 0:
        return _gauss_kernel(half, phase)
    k = np.arange(-half, half + 1, dtype=np.float64)
    dx, dy = np.meshgrid(k - phase[0], k - phase[1])
    return np.asarray(epsf.evaluate(dx, dy, np.full(dx.shape, int(zone))), np.float64)


def _zone_grid(epsf, shape: tuple[int, int], group: int = C.MF_ZONE_GROUP) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(row edges, column edges, zone per rectangle) of the matched filter's kernel tiles: the ePSF zones (they tile
    the detector in rows and columns, 21 × 21 on a full photo) merged `group` × `group`, each rectangle using the zone
    at its centre (neighbouring zones' ePSFs differ by ~1%; one kernel per zone costs ~2× more). No ePSF: one tile,
    zone -1."""
    ny, nx = shape
    if epsf is None:
        return np.array([0, ny]), np.array([0, nx]), np.full((1, 1), -1)
    xs, ys = np.arange(nx, dtype=np.float64), np.arange(ny, dtype=np.float64)
    zc = epsf.zone(xs, np.full(nx, ny / 2.0))  # changes only at column boundaries
    zr = epsf.zone(np.full(ny, nx / 2.0), ys)
    g = max(1, int(group))
    cb = np.r_[0, (np.flatnonzero(np.diff(zc)) + 1)[g - 1::g], nx]
    rb = np.r_[0, (np.flatnonzero(np.diff(zr)) + 1)[g - 1::g], ny]
    cb, rb = np.unique(cb), np.unique(rb)
    cx, cy = (cb[:-1] + cb[1:] - 1) / 2.0, (rb[:-1] + rb[1:] - 1) / 2.0
    X, Y = np.meshgrid(cx, cy)
    return rb, cb, epsf.zone(X.ravel(), Y.ravel()).reshape(X.shape)


def _zone_tiles(epsf, shape: tuple[int, int]) -> list[tuple[int, int, int, int, int]]:
    """(y0, y1, x0, x1, zone) rectangles of _zone_grid."""
    rb, cb, zg = _zone_grid(epsf, shape)
    return [(int(rb[i]), int(rb[i + 1]), int(cb[j]), int(cb[j + 1]), int(zg[i, j]))
            for i in range(len(rb) - 1) for j in range(len(cb) - 1)]


def _weights(sub, noise, bad) -> tuple[np.ndarray, np.ndarray]:
    nz = np.where(np.isfinite(noise), noise, 0.0)
    w = np.where(np.asarray(bad, bool) | ~(nz > 0), 0.0, 1.0 / np.maximum(nz, 1e-30) ** 2)
    return w, np.where(w > 0, np.asarray(sub, np.float64) * w, 0.0)


def matched_filter(sub: np.ndarray, noise: np.ndarray, bad: np.ndarray, epsf, half: int = C.MF_KERNEL_HALF,
                   phases: int = C.MF_PHASES) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Inverse-variance ePSF-matched filter for a point source on each pixel: (S/N, flux, Σ w·K², phase) maps. For
    each sub-pixel phase (mf_phases), S/N = Σ w·d·K / √(Σ w·K²) and flux = Σ w·d·K / Σ w·K² over the (2h+1)² pixels
    around the pixel, w = 1/noise² (0 on bad pixels), K = the ePSF of that pixel's zone with the source at that phase
    (each zone's rectangle filtered with its own kernels); the maps keep the phase with the highest S/N (`phase` = its
    index in mf_phases). Flux in the image's units summed over pixels (MJy/sr × px; × ujy_per_mjysr -> µJy). The
    third map is Σ w·K² of the centred phase: 1/√ of it is a point source's flux 1σ there (the depth).
    `sub`: background-subtracted image, bad pixels 0."""
    from scipy import ndimage
    w, sw = _weights(sub, noise, bad)
    ny, nx = w.shape
    snr = np.full((ny, nx), -np.inf)
    flux, den0 = np.zeros((ny, nx)), np.zeros((ny, nx))
    phase = np.zeros((ny, nx), np.int8)
    tiles = _zone_tiles(epsf, (ny, nx))
    for ip, ph in enumerate(mf_phases(phases)):
        num, den = np.zeros((ny, nx)), np.zeros((ny, nx))
        for y0, y1, x0, x1, z in tiles:
            K = mf_kernel(epsf, z, half, ph)
            ya, yb, xa, xb = max(0, y0 - half), min(ny, y1 + half), max(0, x0 - half), min(nx, x1 + half)
            tn = ndimage.correlate(sw[ya:yb, xa:xb], K, mode="constant")
            td = ndimage.correlate(w[ya:yb, xa:xb], K * K, mode="constant")
            num[y0:y1, x0:x1] = tn[y0 - ya:y1 - ya, x0 - xa:x1 - xa]
            den[y0:y1, x0:x1] = td[y0 - ya:y1 - ya, x0 - xa:x1 - xa]
        with np.errstate(divide="ignore", invalid="ignore"):
            s = np.where(den > 0, num / np.sqrt(den), 0.0)
            better = s > snr
            snr = np.where(better, s, snr)
            flux = np.where(better, np.where(den > 0, num / den, 0.0), flux)
        phase[better] = ip
        if ip == 0:
            den0 = den
    return snr, flux, den0, phase


def mf_at(sub: np.ndarray, noise: np.ndarray, bad: np.ndarray, x: np.ndarray, y: np.ndarray, epsf,
          half: int = C.MF_KERNEL_HALF, phases: int = C.MF_PHASES) -> np.ndarray:
    """matched_filter's S/N at given positions without the whole map: the largest value over the 3 × 3 pixels around
    each rounded (x, y) (the dots' column snr_mf). Same kernels and weights as matched_filter."""
    x, y = np.asarray(x, np.float64), np.asarray(y, np.float64)
    if x.size == 0:
        return np.zeros(0)
    w, sw = _weights(sub, noise, bad)
    ny, nx = w.shape
    ix = np.clip(np.rint(x).astype(np.int64), 0, nx - 1)
    iy = np.clip(np.rint(y).astype(np.int64), 0, ny - 1)
    rb, cb, zg = _zone_grid(epsf, (ny, nx))  # the same kernels as matched_filter
    zone = zg[np.searchsorted(rb, iy, side="right") - 1, np.searchsorted(cb, ix, side="right") - 1]
    m = 2 * half + 1
    S, _ = gather(sw, ix, iy, half + 1, fill=0.0)
    W, _ = gather(w, ix, iy, half + 1, fill=0.0)
    best = np.full(x.size, -np.inf)
    for ph in mf_phases(phases):
        Ks = {int(z): mf_kernel(epsf, int(z), half, ph) for z in np.unique(zone)}
        K = np.stack([Ks[int(z)] for z in zone])  # (N, m, m)
        for oy in range(3):
            for ox in range(3):
                n = np.einsum("nij,nij->n", S[:, oy:oy + m, ox:ox + m], K)
                d = np.einsum("nij,nij->n", W[:, oy:oy + m, ox:ox + m], K * K)
                with np.errstate(divide="ignore", invalid="ignore"):
                    best = np.maximum(best, np.where(d > 0, n / np.sqrt(d), 0.0))
    return np.where(np.isfinite(best), best, 0.0)


def mf_peaks(snr: np.ndarray, thresh: float, phase: np.ndarray | None = None,
             phases: int = C.MF_PHASES) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Local maxima (3 × 3) of an S/N map at or above thresh: (x, y), the peak pixel (ix, iy) and its S/N. Position:
    with a phase map (matched_filter, phases > 1) the pixel plus its best phase's offset; else refined by a parabola
    through the peak and its neighbours along each axis (≤ 0.5 px)."""
    from scipy import ndimage
    mx = ndimage.maximum_filter(snr, size=3, mode="constant", cval=-np.inf)
    iy, ix = np.nonzero((snr >= mx) & (snr >= thresh))
    ny, nx = snr.shape
    s0 = snr[iy, ix]

    def _off(sm, sp):
        d = sm - 2.0 * s0 + sp
        with np.errstate(divide="ignore", invalid="ignore"):
            o = np.where(d < 0, 0.5 * (sm - sp) / d, 0.0)
        return np.clip(np.nan_to_num(o), -0.5, 0.5)

    xm, xp = snr[iy, np.clip(ix - 1, 0, nx - 1)], snr[iy, np.clip(ix + 1, 0, nx - 1)]
    ym, yp = snr[np.clip(iy - 1, 0, ny - 1), ix], snr[np.clip(iy + 1, 0, ny - 1), ix]
    if phase is not None and phases > 1:
        off = np.asarray(mf_phases(phases))[phase[iy, ix]]
        return ix + off[:, 0], iy + off[:, 1], ix, iy, s0
    x = ix + np.where((ix > 0) & (ix < nx - 1), _off(xm, xp), 0.0)
    y = iy + np.where((iy > 0) & (iy < ny - 1), _off(ym, yp), 0.0)
    return x, y, ix, iy, s0


@dataclass
class MFPass:
    dots: np.ndarray        # new dots (core.detect's DETECTION_DTYPE), measured on the residual, sep_flag 0 or the
                            # refused_flags / low_flag bits
    snr_mf: np.ndarray      # their matched-filter S/N
    peaks: dict             # the other peaks >= min_peak_snr: x, y, ix, iy, snr_mf, flux (image units × px), why
    den: np.ndarray         # Σ w·K² map (matched_filter): 1/√den = a point source's flux 1σ (image units × px)
    info: dict


# `why` codes of the peaks file
PEAK_BELOW = 0      # S/N between min_peak_snr and min_snr
PEAK_GUARD = 1      # >= min_snr but within the saturation guard
PEAK_MIRROR = 2     # >= min_snr but failed the mirror test beside a subtracted dot
PEAK_EXCLUDED = 3   # >= min_snr but inside an excluded region (a glow star's own pixels)


P_LO = 15.865525393145707  # the 1-sigma lower percentile of a normal distribution


def _sigma_lo(v: np.ndarray) -> tuple[float, float]:
    """(median - 15.87th percentile, median) of finite values: a noise sigma from the lower half (sources are
    positive); (nan, nan) for fewer than 100."""
    v = v[np.isfinite(v)]
    if v.size < 100:
        return float("nan"), float("nan")
    med = float(np.median(v))
    return med - float(np.percentile(v, P_LO)), med


def mf_sigma_map(snr: np.ndarray, den: np.ndarray, n: int | None = None) -> np.ndarray:
    """The S/N map's own sigma (_sigma_lo) in n x n blocks (n None: MF_SIGMA_GRID; edges round(linspace(0, size,
    n + 1)), as the footer grids), each block's pixels with data (den > 0); a block with < 100 such pixels takes the
    whole map's."""
    n = int(C.MF_SIGMA_GRID if n is None else n)
    ny, nx = snr.shape
    ey = np.round(np.linspace(0, ny, n + 1)).astype(int)
    ex = np.round(np.linspace(0, nx, n + 1)).astype(int)
    ok = (den > 0) & np.isfinite(snr)
    whole, _ = _sigma_lo(snr[ok][::7])
    out = np.full((n, n), whole)
    for i in range(n):
        for j in range(n):
            b = snr[ey[i]:ey[i + 1], ex[j]:ex[j + 1]][ok[ey[i]:ey[i + 1], ex[j]:ex[j + 1]]]
            sg, _ = _sigma_lo(b)
            if np.isfinite(sg):
                out[i, j] = sg
    return out


def mf_noise_stats(snr: np.ndarray, den: np.ndarray, min_snr: float, step: int = 7, grid: bool = False) -> dict:
    """The matched-filter S/N map's own noise level (mf_pass): sigma_lo = median - 15.87th percentile over every
    step-th pixel with data (den > 0), the median, n_neg = local minima (3 x 3) <= -min_snr, and with grid=True
    `sigma_grid` (mf_sigma_map, MF_SIGMA_GRID x MF_SIGMA_GRID)."""
    from scipy import ndimage
    ok = den > 0
    sg, med = _sigma_lo(snr[ok][::step])
    if not np.isfinite(sg):    # too few data pixels (< ~700: a photo mostly masked or off the chip)
        out = {"sigma_lo": float("nan"), "median": float("nan"), "n_neg": 0}
        if grid:               # every block NaN: mf_pass's block mode then keeps min_snr (scale 1)
            out["sigma_grid"] = np.full((int(C.MF_SIGMA_GRID),) * 2, np.nan)
        return out
    neg = np.where(ok & np.isfinite(snr), -snr, -np.inf)
    mx = ndimage.maximum_filter(neg, size=3, mode="constant", cval=-np.inf)
    n_neg = int(((neg >= mx) & (neg >= min_snr)).sum())
    out = {"sigma_lo": round(sg, 4), "median": round(med, 4), "n_neg": n_neg}
    if grid:
        out["sigma_grid"] = mf_sigma_map(snr, den)
    return out


def mf_pass(resid: np.ndarray, noise: np.ndarray, bad: np.ndarray, epsf, dot_x: np.ndarray, dot_y: np.ndarray, *,
            primary_x: np.ndarray | None = None, primary_y: np.ndarray | None = None,
            sat_dist: np.ndarray | None = None, sat_guard_px: float = 0.0, exclude: np.ndarray | None = None,
            min_snr: float = C.MF_PASS_SNR, min_peak_snr: float = C.MF_PEAK_SNR, normalise: str | bool = "off",
            dedup_px: float = C.MF_PASS_DEDUP_PX, r_px: float = C.PSF_PASS_R_PX,
            mirror_r_px: float = C.PSF_PASS_MIRROR_R_PX, mirror_frac: float = C.PSF_PASS_MIRROR_FRAC,
            mirror_sigma: float = C.PSF_PASS_MIRROR_SIGMA, refused_flags: dict[int, int] | None = None,
            low_snr: float | None = None, low_flag: int = 0) -> MFPass:
    """The matched-filter pass (extraction v2): faint point sources the aperture cut (S/N >= 5 in r = 2 px) leaves out.
    resid: the image detection measured on minus its background (and minus the second pass's ePSF models), bad pixels
    0; noise: its per-pixel 1σ (with the model term); (dot_x, dot_y): every dot already found. Peaks of the matched
    filter's S/N map >= min_snr more than dedup_px from every dot become dots unless within sat_guard_px of a saturated
    pixel (sat_dist), inside `exclude`, or, within r_px of a subtracted dot (primary_x, primary_y), failing the second
    pass's mirror test. They are measured like core.detect's dots on resid (windowed centroid from the peak, r = 2 px
    aperture) and kept when still > dedup_px from every dot. The other peaks >= min_peak_snr with no dot within
    dedup_px go to `peaks` with the reason they are not dots.
    The map's own scatter (16 Nov, second pass) goes to info: sigma_lo = median - 15.87th percentile of the S/N map
    (its lower half: noise only, sources are positive; 1 for pure noise), n_neg = local minima <= -min_snr (the same
    threshold's false alarms on the map's negative side), sigma_grid (the same per block, MF_SIGMA_GRID^2).
    normalise "photo" multiplies min_snr by max(1, sigma_lo); "block" each peak's by its block's sigma where that is
    > MF_SIGMA_MIN_SCALE (else 1): a structured patch (D2's top rows, a crowded field) needs more to make a dot, the
    rest of the photo keeps min_snr (its sigma is 1.00-1.05: faint sources, not noise).
    refused_flags {why: bit OR'd into the dot's int32 sep_flag; dots passes dot_flag bits in their in-memory form,
    config.dot_flag_mem} (18 Nov, rejected peaks): a peak >= min_snr that the guard (PEAK_GUARD), the
    excluded region (PEAK_EXCLUDED) or the mirror test (PEAK_MIRROR) refuses becomes a dot too, measured the same way,
    with that why's bit (every test that refuses it, not just the first); a why not in the dict keeps the peak in
    `peaks`. low_snr (< min_snr): peaks from low_snr up to min_snr (both x the same block / photo scale) become dots
    with low_flag (and the refusal bits they would get at >= min_snr). Both None: dots only from peaks passing every
    test. When two measured dots converge within dedup_px, a dot without these bits wins, then the brighter."""
    import sep
    from scipy.spatial import cKDTree
    from skyshift_core.detect import DETECTION_DTYPE, measure_objects

    resid = np.ascontiguousarray(resid, dtype=np.float64)
    noise = np.ascontiguousarray(np.where(np.isfinite(noise), noise, 0.0), dtype=np.float64)
    bad = np.ascontiguousarray(bad, dtype=bool)
    snr, flux, den, phase = matched_filter(resid, noise, bad, epsf)
    stats = mf_noise_stats(snr, den, min_snr, grid=True)
    normalise = {True: "photo", False: "off"}.get(normalise, normalise)
    scale_grid = None
    if normalise == "photo" and np.isfinite(stats["sigma_lo"]):
        min_snr = min_snr * max(1.0, stats["sigma_lo"])
    elif normalise == "block" and stats.get("sigma_grid") is not None:
        g = np.nan_to_num(stats["sigma_grid"], nan=1.0)
        scale_grid = np.where(g > C.MF_SIGMA_MIN_SCALE, g, 1.0)
    stats["min_snr_used"] = float(min_snr)
    x, y, ix, iy, s = mf_peaks(snr, min(min_snr, min_peak_snr), phase)
    f = flux[iy, ix]
    if len(x) and np.size(dot_x):
        d, _ = cKDTree(np.c_[dot_x, dot_y]).query(np.c_[x, y], k=1)
        far = d > dedup_px
        x, y, ix, iy, s, f = x[far], y[far], ix[far], iy[far], s[far], f[far]
    why = np.full(len(x), PEAK_BELOW, np.int8)
    scale = np.ones(len(x))
    if scale_grid is not None and len(x):
        n = scale_grid.shape[0]
        by = np.searchsorted(np.round(np.linspace(0, snr.shape[0], n + 1)).astype(int), iy, side="right") - 1
        bx = np.searchsorted(np.round(np.linspace(0, snr.shape[1], n + 1)).astype(int), ix, side="right") - 1
        scale = scale_grid[np.clip(by, 0, n - 1), np.clip(bx, 0, n - 1)]
    cand = s >= min_snr * scale
    refused_flags = dict(refused_flags or {})
    low = np.zeros(len(x), bool)
    if low_snr is not None and low_snr < min_snr:
        low = ~cand & (s >= low_snr * scale)
    test = cand | low                      # every peak that may become a dot
    bits = np.where(low, int(low_flag), 0).astype(np.int32)
    refused = np.zeros(len(x), bool)       # refused and kept as a peak (a why without a bit)

    def refuse(g: np.ndarray, code: int) -> None:
        nonlocal refused
        if code in refused_flags:
            bits[g] |= int(refused_flags[code])
        else:
            why[g & ~refused & ~low] = code
            refused = refused | g
    if sat_guard_px > 0 and sat_dist is not None and len(x):
        refuse(test & (sat_dist[iy, ix] < sat_guard_px), PEAK_GUARD)
    if exclude is not None and len(x):
        refuse(test & ~refused & np.asarray(exclude, bool)[iy, ix], PEAK_EXCLUDED)
    if primary_x is not None and np.size(primary_x) and (test & ~refused).any():
        px_, py_ = np.asarray(primary_x, np.float64), np.asarray(primary_y, np.float64)
        dd, j = cKDTree(np.c_[px_, py_]).query(np.c_[x, y], k=1)
        near = test & ~refused & (dd <= r_px)
        if near.any():
            k = np.flatnonzero(near)
            mx, my = 2 * px_[j[k]] - x[k], 2 * py_[j[k]] - y[k]
            fn, en, _ = sep.sum_circle(resid, x[k], y[k], mirror_r_px, err=noise, mask=bad, subpix=5)
            fm, em, _ = sep.sum_circle(resid, mx, my, mirror_r_px, err=noise, mask=bad, subpix=5)
            ok = (fm < mirror_frac * fn) & ((fn - fm) >= mirror_sigma * np.sqrt(en ** 2 + em ** 2))
            g = np.zeros(len(x), bool)
            g[k[~ok]] = True
            refuse(g, PEAK_MIRROR)
    cand = test & ~refused
    info = {"n_mf_raw": int((cand & (bits == 0)).sum())}
    dots = np.zeros(0, dtype=DETECTION_DTYPE)
    dsnr = np.zeros(0)
    if cand.any():
        objs = np.zeros(int(cand.sum()), dtype=[("x", "f8"), ("y", "f8"), ("xpeak", "i4"), ("ypeak", "i4"),
                                                ("peak", "f8"), ("npix", "i4"), ("flag", "i4")])
        objs["x"], objs["y"] = x[cand], y[cand]
        objs["xpeak"], objs["ypeak"] = ix[cand], iy[cand]
        objs["peak"] = resid[iy[cand], ix[cand]]
        objs["flag"] = bits[cand]  # measure_objects ORs its sep_flag with these
        dots = measure_objects(resid, noise, bad, objs)
        if len(dots) and np.size(dot_x):
            d, _ = cKDTree(np.c_[dot_x, dot_y]).query(np.c_[dots["x"], dots["y"]], k=1)
            dots = dots[d > dedup_px]
        if len(dots):  # two peaks whose centroids converged on one source: keep the unflagged, then the brighter (first)
            mine = int(low_flag)
            for v in refused_flags.values():
                mine |= int(v)
            fl = (dots["sep_flag"] & mine) != 0
            drop = np.zeros(len(dots), bool)
            for a, b in sorted(cKDTree(np.c_[dots["x"], dots["y"]]).query_pairs(dedup_px)):
                if not (drop[a] or drop[b]):
                    drop[a if fl[a] and not fl[b] else b] = True
            dots = dots[~drop]
        dsnr = mf_at(resid, noise, bad, dots["x"], dots["y"], epsf)
    keep = ~cand
    peaks = {"x": x[keep], "y": y[keep], "ix": ix[keep], "iy": iy[keep], "snr_mf": s[keep], "flux": f[keep],
             "why": why[keep]}
    info["stats"] = stats
    info.update({"n_mf_pass": int(len(dots)), "n_peaks": int(keep.sum()),
                 "n_peaks_guard": int((why == PEAK_GUARD).sum()), "n_peaks_mirror": int((why == PEAK_MIRROR).sum()),
                 "n_peaks_excluded": int((why == PEAK_EXCLUDED).sum())})
    for code, name in ((PEAK_GUARD, "guard"), (PEAK_MIRROR, "mirror"), (PEAK_EXCLUDED, "excluded")):
        if code in refused_flags:  # dots with that refusal bit
            info[f"n_mf_{name}"] = int(((dots["sep_flag"] & int(refused_flags[code])) != 0).sum())
    if low_snr is not None and low_flag:
        info["n_mf_low_snr"] = int(((dots["sep_flag"] & int(low_flag)) != 0).sum())
    return MFPass(dots, dsnr, peaks, den, info)


# --- extraction v2: extended (diffuse) sources: comets' comae, galaxies (agent "comets", 18 Nov) --------------------

def ext_kernel(epsf, zone: int, scale: float, half: int | None = None) -> np.ndarray:
    """A source `scale` × the PSF's FWHM wide: the zone's ePSF (mf_kernel, MF_KERNEL_HALF) convolved with a round
    Gaussian of sigma PSF_SIGMA_PX · √(scale² − 1) (Gaussians add in quadrature), on (2h+1)² native pixels (h None:
    ext_half(scale)), sum 1. scale <= 1 returns the ePSF itself (mf_kernel's pixels, not renormalised)."""
    from scipy.signal import fftconvolve
    from skyshift_core.detect import PSF_SIGMA_PX
    h = ext_half(scale) if half is None else int(half)
    base = mf_kernel(epsf, zone, C.MF_KERNEL_HALF)
    if scale <= 1.0:
        k = np.zeros((2 * h + 1, 2 * h + 1))
        b = min(h, C.MF_KERNEL_HALF)
        c = C.MF_KERNEL_HALF
        k[h - b:h + b + 1, h - b:h + b + 1] = base[c - b:c + b + 1, c - b:c + b + 1]
        return k  # = mf_kernel (its 7 × 7 pixels hold ~99% of the ePSF)
    sg = PSF_SIGMA_PX * np.sqrt(scale * scale - 1.0)
    g = _gauss_sigma(h + C.MF_KERNEL_HALF, sg)
    k = fftconvolve(g, base, mode="same")
    n = g.shape[0] // 2
    k = np.clip(k[n - h:n + h + 1, n - h:n + h + 1], 0.0, None)
    return k / k.sum()


def ext_half(scale: float) -> int:
    """Half-size of ext_kernel: 3 sigma of the widened PSF, at least MF_KERNEL_HALF (2 × FWHM: 4 px; 4 ×: 7 px)."""
    from skyshift_core.detect import PSF_SIGMA_PX
    return max(C.MF_KERNEL_HALF, int(np.ceil(3.0 * PSF_SIGMA_PX * max(1.0, float(scale)) + 0.5)))


def _gauss_sigma(half: int, sigma: float) -> np.ndarray:
    k = np.arange(-half, half + 1, dtype=np.float64)
    g = np.exp(-0.5 * (k[None, :] ** 2 + k[:, None] ** 2) / sigma ** 2)
    return g / g.sum()


def ext_filter(sub: np.ndarray, noise: np.ndarray, bad: np.ndarray, epsf, scale: float,
               half: int | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """matched_filter for a source `scale` × the PSF wide (ext_kernel): (S/N, flux, Σ w·K²) maps, S/N =
    Σ w·d·K / √(Σ w·K²), flux = Σ w·d·K / Σ w·K² (image units × px: the source's total flux if it has the kernel's
    shape), w = 1/noise² (0 on bad pixels), one kernel per matched-filter tile (_zone_tiles, the zone at its centre).
    By FFT per tile (the kernel is up to 15 × 15): ~0.3 s per scale on a full photo."""
    from scipy.signal import oaconvolve
    w, sw = _weights(sub, noise, bad)
    ny, nx = w.shape
    h = ext_half(scale) if half is None else int(half)
    num, den = np.zeros((ny, nx)), np.zeros((ny, nx))
    for y0, y1, x0, x1, z in _zone_tiles(epsf, (ny, nx)):
        K = ext_kernel(epsf, z, scale, h)
        Kf = K[::-1, ::-1]  # correlation = convolution with the flipped kernel
        ya, yb, xa, xb = max(0, y0 - h), min(ny, y1 + h), max(0, x0 - h), min(nx, x1 + h)
        tn = oaconvolve(sw[ya:yb, xa:xb], Kf, mode="same")
        td = oaconvolve(w[ya:yb, xa:xb], Kf * Kf, mode="same")
        num[y0:y1, x0:x1] = tn[y0 - ya:y1 - ya, x0 - xa:x1 - xa]
        den[y0:y1, x0:x1] = td[y0 - ya:y1 - ya, x0 - xa:x1 - xa]
    den = np.where(den > 1e-12 * max(float(den.max()), 1e-300), den, 0.0)  # FFT round-off where w = 0
    with np.errstate(divide="ignore", invalid="ignore"):
        snr = np.where(den > 0, num / np.sqrt(den), 0.0)
        flux = np.where(den > 0, num / den, 0.0)
    return snr, flux, den


def point_model(shape: tuple[int, int], epsf, x: np.ndarray, y: np.ndarray, flux: np.ndarray,
                half: int = C.PSF_PASS_STAMP_HALF) -> np.ndarray:
    """Σ flux · ePSF at (x, y) on a map of `shape` (image units; non-finite fluxes skipped)."""
    model = np.zeros(shape)
    x, y, flux = (np.asarray(v, np.float64) for v in (x, y, flux))
    ok = np.isfinite(flux) & np.isfinite(x) & np.isfinite(y)
    if epsf is None or not ok.any():
        return model
    P, ix, iy = epsf.stamps(x[ok], y[ok], half)
    vals = P * flux[ok][:, None, None]
    k = np.arange(-half, half + 1)
    yy = np.broadcast_to(iy[:, None, None] + k[None, :, None], vals.shape)
    xx = np.broadcast_to(ix[:, None, None] + k[None, None, :], vals.shape)
    inside = (yy >= 0) & (yy < shape[0]) & (xx >= 0) & (xx < shape[1])
    np.add.at(model, (yy[inside], xx[inside]), vals[inside])
    return model


@dataclass
class ExtPass:
    dots: np.ndarray        # new dots (core.detect's DETECTION_DTYPE), measured on the point-free residual
    snr_ext: np.ndarray     # their extended-filter S/N (the winning scale's)
    scale: np.ndarray       # the winning kernel's width in PSF FWHMs (int8)
    info: dict
    snr_point: np.ndarray | None = None  # the point filter's S/N there (largest over the peak's 3 × 3), same residual
    flux_ext: np.ndarray | None = None   # the winning filter's flux there (image units × px: the total, if that wide)
    resid: np.ndarray | None = None      # the point-free residual the filters ran on (stamps.py keeps a patch of it)


def ext_pass(resid: np.ndarray, noise: np.ndarray, bad: np.ndarray, epsf, dot_x: np.ndarray, dot_y: np.ndarray, *,
             sub_x: np.ndarray | None = None, sub_y: np.ndarray | None = None,
             sat_dist: np.ndarray | None = None, sat_guard_px: float = 0.0, exclude: np.ndarray | None = None,
             scales: tuple = C.EXT_SCALES, min_snr: float = C.EXT_PASS_SNR, ratio: float = C.EXT_RATIO,
             dedup_px: float = C.EXT_DEDUP_PX, normalise: str = C.EXT_NORMALISE) -> ExtPass:
    """The extended-source pass (agent "comets", 18 Nov): diffuse sources (comet comae, galaxies) a point-source
    filter misses. resid: the image the matched-filter pass searched (IMAGE minus background minus the second pass's
    ePSF models, bad pixels 0); (sub_x, sub_y): the dots not yet in that model, whose ePSF (flux fitted here,
    psf_photometry at fixed position; negative fits skipped) is subtracted first, so that no known point source is
    left; (dot_x, dot_y): every dot. On that point-free residual ext_filter runs at each scale (× the PSF FWHM) and at
    1 (the point filter). A local maximum (3 × 3) of a scale's S/N >= min_snr (× the map's block sigma where it is
    > MF_SIGMA_MIN_SCALE, normalise "block") whose S/N is >= ratio × the point filter's there (a point source gives
    0.76 at 2×, 0.42 at 4×; a source as wide as the kernel 1.3 and 2.4), more than dedup_px from every dot, outside
    the saturation guard and `exclude`, is a candidate; candidates closer than the wider one's FWHM are one source
    (highest S/N over its threshold wins). Each is measured like core.detect's dots on the point-free residual
    (windowed centroid from the peak, r = 2 px aperture). info: counts per scale and each map's own noise
    (mf_noise_stats: sigma_lo, n_neg)."""
    from scipy.spatial import cKDTree
    from skyshift_core.detect import DETECTION_DTYPE, PSF_SIGMA_PX, measure_objects

    resid = np.ascontiguousarray(resid, dtype=np.float64)
    noise = np.ascontiguousarray(np.where(np.isfinite(noise), noise, 0.0), dtype=np.float64)
    bad = np.ascontiguousarray(bad, dtype=bool)
    info: dict = {"n_subtracted": 0}
    if sub_x is not None and np.size(sub_x) and epsf is not None:
        ph = psf_photometry(resid, sub_x, sub_y, epsf, noise=noise, bad=bad)
        f = np.where(ph.flux > 0, ph.flux, np.nan)
        resid = np.ascontiguousarray(np.where(bad, 0.0, resid - point_model(resid.shape, epsf, sub_x, sub_y, f)))
        info["n_subtracted"] = int(np.isfinite(f).sum())
    point = ext_filter(resid, noise, bad, epsf, 1.0, C.MF_KERNEL_HALF)[0]
    fwhm = 2.3548 * PSF_SIGMA_PX
    cand = []  # columns: x, y, ix, iy, snr, snr / threshold, scale, point S/N, flux
    for sc in scales:
        snr, flx, den = ext_filter(resid, noise, bad, epsf, sc)
        st = mf_noise_stats(snr, den, min_snr, grid=normalise == "block")
        info[f"s{sc:g}"] = {k: v for k, v in st.items() if k != "sigma_grid"}
        x, y, ix, iy, s = mf_peaks(snr, min_snr)
        scl = np.ones(len(x))
        if normalise == "block" and st.get("sigma_grid") is not None and len(x):
            g = np.nan_to_num(st["sigma_grid"], nan=1.0)
            g = np.where(g > C.MF_SIGMA_MIN_SCALE, g, 1.0)
            n = g.shape[0]
            by = np.searchsorted(np.round(np.linspace(0, snr.shape[0], n + 1)).astype(int), iy, side="right") - 1
            bx = np.searchsorted(np.round(np.linspace(0, snr.shape[1], n + 1)).astype(int), ix, side="right") - 1
            scl = g[np.clip(by, 0, n - 1), np.clip(bx, 0, n - 1)]
        pt = gather(point, ix, iy, 1, fill=-np.inf)[0].reshape(len(x), -1).max(axis=1) if len(x) else np.zeros(0)
        keep = (s >= min_snr * scl) & (s >= ratio * pt)
        info[f"n_peaks_s{sc:g}"] = int(len(x))
        info[f"n_wide_s{sc:g}"] = int(keep.sum())
        cand.append(np.c_[x[keep], y[keep], ix[keep], iy[keep], s[keep], s[keep] / (min_snr * scl[keep]),
                          np.full(int(keep.sum()), float(sc)), pt[keep], flx[iy[keep], ix[keep]]])
    c = np.concatenate(cand) if cand else np.zeros((0, 9))
    if len(c) and np.size(dot_x):
        d, _ = cKDTree(np.c_[dot_x, dot_y]).query(c[:, :2], k=1)
        c = c[d > dedup_px]
    if len(c) and sat_guard_px > 0 and sat_dist is not None:
        c = c[sat_dist[c[:, 3].astype(np.int64), c[:, 2].astype(np.int64)] >= sat_guard_px]
    if len(c) and exclude is not None:
        c = c[~np.asarray(exclude, bool)[c[:, 3].astype(np.int64), c[:, 2].astype(np.int64)]]
    if len(c):  # one source per blob: the highest S/N over its threshold first, drop others within the wider FWHM
        c = c[np.argsort(-c[:, 5], kind="stable")]
        drop = np.zeros(len(c), bool)
        tree = cKDTree(c[:, :2])
        for i in range(len(c)):
            if drop[i]:
                continue
            for j in tree.query_ball_point(c[i, :2], fwhm * max(scales)):
                if j > i and np.hypot(*(c[j, :2] - c[i, :2])) <= fwhm * max(c[i, 6], c[j, 6]):
                    drop[j] = True
        c = c[~drop]
    dots = np.zeros(0, dtype=DETECTION_DTYPE)
    snr_ext, scale, snr_pt, fx = np.zeros(0), np.zeros(0, np.int8), np.zeros(0), np.zeros(0)
    if len(c):
        objs = np.zeros(len(c), dtype=[("x", "f8"), ("y", "f8"), ("xpeak", "i4"), ("ypeak", "i4"),
                                       ("peak", "f8"), ("npix", "i4"), ("flag", "i4")])
        objs["x"], objs["y"] = c[:, 0], c[:, 1]
        objs["xpeak"], objs["ypeak"] = c[:, 2], c[:, 3]
        objs["peak"] = resid[c[:, 3].astype(np.int64), c[:, 2].astype(np.int64)]
        dots = measure_objects(resid, noise, bad, objs)  # sorted by flux: map back to the candidates
        _, j = cKDTree(c[:, :2]).query(np.c_[dots["x"], dots["y"]], k=1)
        snr_ext, scale, snr_pt, fx = c[j, 4], np.rint(c[j, 6]).astype(np.int8), c[j, 7], c[j, 8]
        if np.size(dot_x):  # a centroid that slid onto a dot: that dot is the source
            d, _ = cKDTree(np.c_[dot_x, dot_y]).query(np.c_[dots["x"], dots["y"]], k=1)
            ok = d > dedup_px
            dots, snr_ext, scale, snr_pt, fx = dots[ok], snr_ext[ok], scale[ok], snr_pt[ok], fx[ok]
    info["n_ext_pass"] = int(len(dots))
    for sc in scales:
        info[f"n_ext_s{sc:g}"] = int((scale == np.rint(sc)).sum())
    return ExtPass(dots, snr_ext, scale, info, snr_pt, fx, resid)
