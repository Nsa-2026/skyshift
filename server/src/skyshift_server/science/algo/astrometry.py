# TODO(handoff): parallax comes out ~1.5-2x too high (suspected per-visit centroid systematic); PM is fine.
"""Measure the motion of a known object: per-frame PSF centroids -> proper motion + parallax."""
import numpy as np
from astropy.time import Time
from astropy.coordinates import get_body_barycentric
import astropy.units as u
from .detect import psf_fit_position


def parallax_factors(ra, dec, mjd):
    from skyshift_core.astrometry import parallax_offset_arcsec
    return parallax_offset_arcsec(ra, dec, 1000.0, mjd)


def frame_offsets(frames_img, frames_var, fwhms, exclude_xy, n_ref=12, min_snr=10, exclude_r=5):
    """Differential astrometry: per-frame (dx, dy) of the local reference-star grid.

    Reference stars are the brightest point sources of the deep stack (excluding the target).
    Each is PSF-fitted in each frame; the weighted median offset from its mean position is the
    frame's registration error, which is removed from the target's position.
    """
    from .detect import snr_map, find_peaks
    T = len(frames_img)
    ok = np.isfinite(frames_img) & np.isfinite(frames_var) & (frames_var > 0)
    iv = np.where(ok, 1 / np.where(ok, frames_var, 1), 0)
    st = (np.where(ok, frames_img, 0) * iv).sum(0) / np.maximum(iv.sum(0), 1e-30)
    sv = 1 / np.maximum(iv.sum(0), 1e-30)
    s, _, _ = snr_map(st, sv, float(np.median(fwhms)))
    pk = [p for p in find_peaks(s, min_snr, border=5) if np.hypot(p["x"] - exclude_xy[0], p["y"] - exclude_xy[1]) > exclude_r]
    pk = sorted(pk, key=lambda p: -p["snr"])[:n_ref]
    if len(pk) < 3:
        return np.zeros(T), np.zeros(T), 0
    P = np.full((T, len(pk), 3), np.nan)
    for t in range(T):
        for j, p in enumerate(pk):
            f = psf_fit_position(frames_img[t], frames_var[t], p["x"], p["y"], fwhms[t], box=4)
            if f and f["eamp"] > 0 and f["amp"] / f["eamp"] > 5 and f["ex"] < 0.3 and f["ey"] < 0.3 \
                    and np.hypot(f["x"] - p["x"], f["y"] - p["y"]) < 1.5:
                P[t, j] = f["x"], f["y"], np.hypot(f["ex"], f["ey"])
    mx, my = np.nanmedian(P[..., 0], 0), np.nanmedian(P[..., 1], 0)
    dx, dy = P[..., 0] - mx, P[..., 1] - my
    ox = np.nanmedian(dx, 1); oy = np.nanmedian(dy, 1)
    n = np.isfinite(dx).sum(1)
    ox[n < 3] = 0; oy[n < 3] = 0
    return np.nan_to_num(ox), np.nan_to_num(oy), len(pk)


def measure_track(frames_img, frames_var, mjd, wcs, ra, dec, fwhms, search_radius=3.0, guess_pm=(0, 0),
                  fit_parallax=True, min_snr=5, differential=True, use=None, dets=None, det_offsets=True):
    """frames_* (T,Y,X) on the common grid; returns per-frame positions and the motion fit."""
    T, Y, X = frames_img.shape
    cx, cy = wcs.all_world2pix([[ra, dec]], 0)[0]
    ox = oy = np.zeros(T); nref = 0
    if differential:
        selected = np.arange(T) if use is None else np.flatnonzero(use)
        ox = np.zeros(T); oy = np.zeros(T)
        if len(selected):
            dx, dy, nref = frame_offsets(frames_img[selected], frames_var[selected], fwhms[selected], (cx, cy))
            ox[selected], oy[selected] = dx, dy
    pts = []
    for t in range(T):
        if use is not None and not use[t]:
            continue
        img, var = frames_img[t], frames_var[t]
        yc, xc = int(round(cy)), int(round(cx))
        if np.isfinite(img[yc - 3:yc + 4, xc - 3:xc + 4]).sum() < 15:
            continue
        # start at the brightest pixel near the expected position
        r = int(np.ceil(search_radius))
        yi, xi = int(round(cy)), int(round(cx))
        sub = img[yi - r:yi + r + 1, xi - r:xi + r + 1]
        if sub.size == 0 or not np.isfinite(sub).any():
            continue
        j = np.nanargmax(sub)
        y0, x0 = yi - r + j // sub.shape[1], xi - r + j % sub.shape[1]
        fit = psf_fit_position(img, var, float(x0), float(y0), fwhms[t])
        if not fit or fit["eamp"] <= 0 or fit["amp"] / fit["eamp"] < min_snr:
            continue
        if fit["ex"] > 0.5 or fit["ey"] > 0.5 or not (0.4 < fit["sigma"] < 5):
            continue
        fit["x"] -= ox[t]; fit["y"] -= oy[t]
        sky = wcs.all_pix2world([[fit["x"], fit["y"]]], 0)[0]
        pts.append(dict(t=t, mjd=float(mjd[t]), x=fit["x"], y=fit["y"], ra=float(sky[0]), dec=float(sky[1]),
                        ex=fit["ex"], ey=fit["ey"], snr=fit["amp"] / fit["eamp"]))
    if len(pts) < 3:
        return dict(points=pts, fit=None)
    P = np.array([[p["mjd"], p["ra"], p["dec"], p["ex"], p["ey"]] for p in pts])
    pix = 6.15 / 3600
    t_yr = (P[:, 0] - P[:, 0].mean()) / 365.25
    # tangent-plane offsets in arcsec relative to the mean
    ra0, dec0 = P[:, 1].mean(), P[:, 2].mean()
    dx = (P[:, 1] - ra0) * np.cos(np.radians(dec0)) * 3600
    dy = (P[:, 2] - dec0) * 3600
    sx = np.hypot(P[:, 3] * 6.15, 0.3); sy = np.hypot(P[:, 4] * 6.15, 0.3)   # 0.3" systematic floor
    fr, fd = parallax_factors(ra0, dec0, P[:, 0])
    cols = [np.ones_like(t_yr), t_yr]
    pdet = np.array([dets[p["t"]] for p in pts]) if dets is not None else None

    def solve(with_plx):
        n = len(t_yr)
        # unknowns: x0, y0, pmra, pmdec, [plx], [per-detector x/y offsets: wavelength-dependent
        # PSF centroid shifts differ between detectors and would otherwise bias the parallax]
        extra = []
        if det_offsets and pdet is not None:
            ud = [d for d in sorted(set(pdet.tolist()))]
            ref = max(ud, key=lambda d: (pdet == d).sum())
            extra = [d for d in ud if d != ref and (pdet == d).sum() >= 3]
        A = np.zeros((2 * n, (5 if with_plx else 4) + 2 * len(extra)))
        A[:n, 0] = 1; A[:n, 2] = t_yr
        A[n:, 1] = 1; A[n:, 3] = t_yr
        if with_plx:
            A[:n, 4] = fr; A[n:, 4] = fd
        c0 = 5 if with_plx else 4
        for j, d in enumerate(extra):
            A[:n, c0 + 2 * j] = (pdet == d); A[n:, c0 + 2 * j + 1] = (pdet == d)
        b = np.r_[dx, dy]; s = np.r_[sx, sy]
        # robust: iterate with clipping
        keep = np.ones(2 * n, bool)
        for _ in range(3):
            Aw, bw = A[keep] / s[keep, None], b[keep] / s[keep]
            p, *_ = np.linalg.lstsq(Aw, bw, rcond=None)
            r = (b - A @ p) / s
            keep = np.abs(r) < max(3.0, 3 * np.std(r[keep]))
        Aw = A[keep] / s[keep, None]
        cov = np.linalg.pinv(Aw.T @ Aw)
        chi2 = float((((b - A @ p) / s)[keep] ** 2).sum())
        dof = max(1, keep.sum() - A.shape[1])
        cov *= max(1.0, chi2 / dof)
        return p, np.sqrt(np.diag(cov)), chi2 / dof, int(keep.sum() // 2)
    span = P[:, 0].max() - P[:, 0].min()
    use_plx = fit_parallax and span > 150
    p, e, chi2r, nk = solve(use_plx)
    fit = dict(pmra=p[2], pmdec=p[3], epmra=e[2], epmdec=e[3], chi2r=chi2r, n=len(pts), span_days=float(span),
               plx=(p[4] if use_plx else None), eplx=(e[4] if use_plx else None),
               units="arcsec/yr, arcsec", n_reference_stars=nref)
    fit["pm_total"] = float(np.hypot(p[2], p[3]))
    return dict(points=pts, fit=fit)
