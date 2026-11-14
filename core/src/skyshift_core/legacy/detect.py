"""Matched-filter source detection and PSF-weighted forced photometry."""
import numpy as np
from scipy.ndimage import convolve, maximum_filter, gaussian_filter


def psf_kernel(fwhm_pix=1.8, size=7):
    s = fwhm_pix / 2.3548
    y, x = np.mgrid[:size, :size] - (size - 1) / 2
    k = np.exp(-(x ** 2 + y ** 2) / (2 * s ** 2))
    return k / k.sum()


def psf_fwhm_pix(lam):
    """Rough SPHEREx PSF FWHM in 6.15" pixels vs wavelength (pixel-limited at short lambda)."""
    lam = np.asarray(lam, float)
    return np.sqrt(1.5 ** 2 + (lam / 5.0 * 1.4) ** 2)


def snr_map(img, var, fwhm=1.8):
    """Optimal (matched-filter) S/N map for point sources."""
    k = psf_kernel(fwhm)
    ok = np.isfinite(img) & np.isfinite(var) & (var > 0)
    iv = np.where(ok, 1 / np.where(ok, var, 1), 0)
    num = convolve(np.where(ok, img, 0) * iv, k[::-1, ::-1], mode="constant")
    den = convolve(iv, (k ** 2)[::-1, ::-1], mode="constant")
    flux = np.where(den > 0, num / np.where(den > 0, den, 1), np.nan)
    err = np.where(den > 0, 1 / np.sqrt(np.where(den > 0, den, 1)), np.nan)
    return flux / err, flux, err


def find_peaks(snr, thresh=5.0, border=3, sign=+1):
    s = sign * np.nan_to_num(snr, nan=-1e9 if sign > 0 else 1e9)
    mx = maximum_filter(s, size=5)
    pk = (s == mx) & (s > thresh)
    pk[:border] = pk[-border:] = False
    pk[:, :border] = pk[:, -border:] = False
    ys, xs = np.nonzero(pk)
    out = []
    for y, x in zip(ys, xs):
        cx, cy = centroid(sign * np.nan_to_num(snr), x, y)
        out.append(dict(x=float(cx), y=float(cy), snr=float(sign * snr[y, x])))
    return out


def centroid(img, x, y, r=2):
    y0, y1 = max(0, y - r), min(img.shape[0], y + r + 1)
    x0, x1 = max(0, x - r), min(img.shape[1], x + r + 1)
    sub = np.clip(img[y0:y1, x0:x1], 0, None)
    if sub.sum() <= 0:
        return float(x), float(y)
    yy, xx = np.mgrid[y0:y1, x0:x1]
    return float((sub * xx).sum() / sub.sum()), float((sub * yy).sum() / sub.sum())


def psf_fit_position(img, var, x0, y0, fwhm=1.8, box=5, n_iter=3):
    """Weighted least-squares fit of a Gaussian PSF + constant. Returns x, y, flux, errors."""
    from scipy.optimize import least_squares
    xi, yi = int(round(x0)), int(round(y0))
    ys = slice(max(0, yi - box), min(img.shape[0], yi + box + 1)); xs = slice(max(0, xi - box), min(img.shape[1], xi + box + 1))
    sub, sv = img[ys, xs], var[ys, xs]
    yy, xx = np.mgrid[ys, xs]
    ok = np.isfinite(sub) & np.isfinite(sv) & (sv > 0)
    if ok.sum() < 10:
        return None
    s = fwhm / 2.3548

    def model(p):
        a, x, y, b, ss = p
        return a * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * ss ** 2)) + b

    def res(p):
        return ((model(p) - sub) / np.sqrt(np.where(ok, sv, 1)))[ok]

    a0 = np.nanmax(sub) - np.nanmedian(sub)
    p0 = [a0, x0, y0, np.nanmedian(sub), s]
    try:
        r = least_squares(res, p0, bounds=([-np.inf, x0 - box, y0 - box, -np.inf, 0.4], [np.inf, x0 + box, y0 + box, np.inf, 5]))
        J = r.jac
        cov = np.linalg.pinv(J.T @ J) * max(1.0, (r.fun ** 2).sum() / max(1, ok.sum() - 5))
        e = np.sqrt(np.diag(cov))
        return dict(x=r.x[1], y=r.x[2], amp=r.x[0], bkg=r.x[3], sigma=r.x[4], ex=e[1], ey=e[2], eamp=e[0],
                    chi2r=float((r.fun ** 2).sum() / max(1, ok.sum() - 5)))
    except Exception:
        return None


def forced_flux(img, var, x, y, fwhm=1.8, size=9):
    """PSF-weighted flux (in image units x pixel, i.e. integrated) at a fixed position."""
    s = fwhm / 2.3548
    xi, yi = int(round(x)), int(round(y))
    h = size // 2
    ys = slice(max(0, yi - h), min(img.shape[0], yi + h + 1)); xs = slice(max(0, xi - h), min(img.shape[1], xi + h + 1))
    if ys.stop <= ys.start or xs.stop <= xs.start:
        return np.nan, np.nan
    yy, xx = np.mgrid[ys, xs]
    P = np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * s ** 2))
    P /= 2 * np.pi * s ** 2
    d, v = img[ys, xs], var[ys, xs]
    ok = np.isfinite(d) & np.isfinite(v) & (v > 0)
    if ok.sum() < 5 or P[ok].sum() < 0.2:
        return np.nan, np.nan
    w = P / np.where(ok, v, np.inf)
    w[~ok] = 0
    f = (w * np.where(ok, d, 0)).sum() / (w * P).sum()
    e = 1 / np.sqrt((w * P).sum())
    return float(f), float(e)
