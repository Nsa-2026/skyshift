"""Spectra on the fly: forced photometry of the target in every frame, each at its own wavelength."""
import numpy as np
from .detect import forced_flux, psf_fit_position

OMEGA_PIX_SR = (6.15 / 206265.0) ** 2   # solid angle of one pixel


def _annulus(img, x, y, r1=2.0, r2=5.0, rb1=8.0, rb2=11.0):
    n0, n1 = img.shape
    yy, xx = np.mgrid[0:n0, 0:n1]
    r = np.hypot(xx - x, yy - y)
    a, b = (r >= r1) & (r < r2), (r >= rb1) & (r < rb2)
    if np.isfinite(img[a]).mean() < 0.9 or np.isfinite(img[b]).sum() < 10:
        return np.nan
    return float(np.nansum(img[a]) - np.nanmedian(img[b]) * a.sum())


def _core(img, x, y, r=5.0, rb1=8.0, rb2=11.0):
    n0, n1 = img.shape
    yy, xx = np.mgrid[0:n0, 0:n1]
    rr = np.hypot(xx - x, yy - y)
    a, b = rr < r, (rr >= rb1) & (rr < rb2)
    if np.isfinite(img[a]).mean() < 0.98:
        return np.nan
    return float(np.nansum(img[a]) - np.nanmedian(img[b]) * a.sum())


def wing_ratio(img, var, x0, y0, fwhm, exclude_r=6, snr_min=30):
    """total/annulus flux ratio measured on unsaturated field stars of the same frame."""
    from .detect import snr_map, find_peaks
    s, _, _ = snr_map(img, var, fwhm)
    ratios = []
    for p in find_peaks(s, snr_min, border=11):
        if np.hypot(p["x"] - x0, p["y"] - y0) < exclude_r:
            continue
        tot, ann = _core(img, p["x"], p["y"]), _annulus(img, p["x"], p["y"])
        if np.isfinite(tot) and np.isfinite(ann) and ann > 0 and tot > 0:
            ratios.append(tot / ann)
    return (float(np.median(ratios)), len(ratios)) if ratios else (np.nan, 0)


def target_spectrum(cube, var, lam, meta, x, y, fwhms, bkg=None):
    """Return per-frame (lambda, flux_mJy, err_mJy, mjd, det).

    cube in MJy/sr; PSF-weighted integrated flux -> MJy/sr * pixel -> mJy.
    """
    out = []
    k = OMEGA_PIX_SR * 1e9        # MJy/sr * pixel -> mJy
    xi, yi = int(round(x)), int(round(y))
    for t in range(cube.shape[0]):
        img = cube[t] if bkg is None else cube[t] - bkg[t]
        core = img[yi - 1:yi + 2, xi - 1:xi + 2]
        saturated = np.isfinite(core).sum() < 7
        if saturated:
            # core masked (overflow / non-linear): measure the unsaturated PSF wings (annulus)
            # and scale by the total/annulus ratio of unsaturated stars in the same frame
            ann = _annulus(img, x, y)
            ratio, nref = wing_ratio(img, var[t], x, y, fwhms[t])
            if not np.isfinite(ann) or not np.isfinite(ratio) or nref < 2:
                continue
            f = ann * ratio
            e = abs(f) * 0.1
        else:
            f, e = forced_flux(img, var[t], x, y, fwhms[t])
        if not np.isfinite(f):
            continue
        out.append(dict(lam=float(lam[t]), flux=f * k, err=e * k, mjd=meta[t]["mjd"], det=meta[t]["det"],
                        quality=meta[t]["quality"]["grade"], method="wing-fit" if saturated else "psf"))
    out.sort(key=lambda r: r["lam"])
    return out


def epoch_split(spec):
    from skyshift_core.visits import visits
    if not spec:
        return []
    groups = visits([p['mjd'] for p in spec])
    return [dict(mjd=float(np.mean([p['mjd'] for p,g in zip(spec,groups) if g==v])),
                 points=sorted([p for p,g in zip(spec,groups) if g==v],key=lambda p:p['lam']))
            for v in np.unique(groups)]


def synth_band(spec_points, band="W1"):
    """Synthesise WISE W1/W2 from a SPHEREx spectrum (top-hat approximation of the RSR)."""
    lo, hi = {"W1": (2.8, 3.9), "W2": (4.0, 5.1)}[band]
    pts = [s for s in spec_points if lo <= s["lam"] <= hi and np.isfinite(s["flux"])]
    if len(pts) < 2:
        return None
    w = np.array([1 / max(s["err"], 1e-6) ** 2 for s in pts])
    f = np.array([s["flux"] for s in pts])
    fl = float((w * f).sum() / w.sum()); er = float(1 / np.sqrt(w.sum()))
    zp = {"W1": 309.540, "W2": 171.787}[band]  # Jy, Vega
    mag = -2.5 * np.log10(fl / 1000 / zp) if fl > 0 else None
    return dict(band=band, flux_mJy=fl, err_mJy=er, vega_mag=mag, n=len(pts))


def halo_lightcurve(cube, lam, mjd, x, y, r1=5.0, r2=9.0, rb1=11.0, rb2=15.0,
                    windows=((0.75, 1.1), (1.1, 1.6), (1.6, 2.4), (2.4, 3.8), (3.8, 4.4), (4.4, 5.05))):
    """Light curve of a star too bright for SPHEREx: the far PSF halo (5-9 px) is never
    saturated, and at a fixed wavelength its flux is a fixed fraction of the total. Each
    wavelength window is normalised to its own median, giving delta-magnitudes vs time."""
    n = cube.shape[1]
    yy, xx = np.mgrid[0:n, 0:n]
    rr = np.hypot(xx - x, yy - y)
    a, b = (rr >= r1) & (rr < r2), (rr >= rb1) & (rr < rb2)
    if b.sum() < 10:
        return []
    halo = np.full(len(cube), np.nan)
    for t in range(len(cube)):
        im = cube[t]
        if np.isfinite(im[a]).mean() > 0.95 and np.isfinite(im[b]).mean() > 0.5:
            halo[t] = np.nansum(im[a]) - np.nanmedian(im[b]) * a.sum()
    out = []
    for lo, hi in windows:
        sel = (lam >= lo) & (lam < hi) & np.isfinite(halo) & (halo > 0)
        if sel.sum() < 5:
            continue
        # remove the spectral slope inside the window with a linear fit in log(lambda)
        L, H = np.log(lam[sel]), np.log(halo[sel])
        A = np.c_[np.ones(sel.sum()), L - L.mean()]
        c, *_ = np.linalg.lstsq(A, H, rcond=None)
        dmag = -2.5 / np.log(10) * (H - A @ c)
        for m, d, l in zip(mjd[sel], dmag, lam[sel]):
            out.append(dict(mjd=float(m), dmag=float(d), lam=float(l), window=f"{lo}-{hi}um"))
    return sorted(out, key=lambda r: r["mjd"])
