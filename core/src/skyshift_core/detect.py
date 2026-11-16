"""Source detection and windowed centroids with sep, on a cutout with the bad-pixel mask applied.

Positions are 0-based numpy pixels (x = column, y = row), as in the dot table.
Fluxes here stay in MJy/sr summed over the aperture; photometry.to_ujy() converts to uJy.

SPHEREx PSF FWHM is 7.53" = 1.22 px, so the PSF is undersampled. A windowed centroid with the
"natural" window (sigma = PSF sigma, 0.52 px) is biased by pixel phase (~0.2 px); a 1.0 px
window brings that to ~0.02 px on a pixel-integrated Gaussian, so WINDOW_SIGMA_PX = 1.0.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import sep
from numpy.typing import ArrayLike, NDArray
from scipy.ndimage import binary_dilation, gaussian_filter, label
from scipy.optimize import least_squares
from scipy.special import erf

from .flags import bad_pixel_mask

WINDOW_SIGMA_PX = 1.0  # windowed-centroid Gaussian sigma

sep.set_extract_pixstack(2_000_000)
_WINPOS_OK_FLAGS = 0x0010 | 0x0020  # sep APER_TRUNC, APER_HASMASKED: informational  # full 2040 x 2040 frames in crowded fields
APERTURE_R_PX = 2.0  # default photometry radius (1.6 x FWHM)
PSF_SIGMA_PX = 1.22 / 2.3548  # SPHEREx PSF FWHM 7.53" = 1.22 px
# The largest hole a saturated core leaves in a stack (masked in every exposure): Barnard's Star's
# is 5-7 px wide at 6.15"/px, under 40 px. A bigger no-data area is missing sky (no_data_near).
MAX_HOLE_PX = 100
# sep_flag bit (ours; sep's extract flags use 0x1-0x8, the pipeline 0x200 and 0x400): the windowed centroid failed or
# moved > 1 px from sep's peak pixel, so x, y are its start point (sep's barycentre when within 1 px of the peak, else
# the peak pixel itself, a whole pixel). Vet's whole-pixel guess (|x - round(x)| < 0.02) misses the barycentre case.
SEP_FLAG_CENTROID_FALLBACK = 0x800

DETECTION_DTYPE = np.dtype([
    ("x", "f8"), ("y", "f8"),  # windowed centroid, 0-based pixels
    ("flux", "f8"),  # MJy/sr summed in an r = APERTURE_R_PX aperture, background subtracted
    ("flux_err", "f8"),
    ("snr", "f8"),  # flux / flux_err
    ("peak", "f8"),  # peak above background, MJy/sr
    ("npix", "i4"),  # pixels above threshold
    ("sep_flag", "i4"),  # sep extraction flags (merged, truncated, ...) | SEP_FLAG_CENTROID_FALLBACK
])


def _native(a: ArrayLike, dtype: type = np.float64) -> NDArray[np.float64]:
    """sep wants native byte order and C-contiguous arrays (FITS data is big-endian)."""
    return np.ascontiguousarray(np.asarray(a), dtype=dtype)


def build_mask(data: ArrayLike, flags: ArrayLike | None = None, mask: ArrayLike | None = None) -> NDArray[np.bool_]:
    """Bad pixels: mask bits of contracts/flags.md, non-finite values, and any extra mask."""
    d = np.asarray(data)
    m = ~np.isfinite(d)
    if flags is not None:
        m |= bad_pixel_mask(flags)
    if mask is not None:
        m |= np.asarray(mask, dtype=bool)
    return m


@dataclass
class Background:
    level: NDArray[np.float64]  # background map, MJy/sr
    rms: NDArray[np.float64]  # background noise map, MJy/sr
    global_rms: float


def background(data: ArrayLike, mask: ArrayLike | None = None, box: int = 32, filter_size: int = 3) -> Background:
    """sep background map. On a ~100 px cutout box=32 gives a 3 x 3 mesh."""
    d = _native(data)
    m = None if mask is None else _native(mask, np.bool_)
    d = np.where(np.isfinite(d), d, 0.0)
    b = sep.Background(d, mask=m, bw=box, bh=box, fw=filter_size, fh=filter_size)
    return Background(np.asarray(b.back()), np.asarray(b.rms()), float(b.globalrms))


def detect(
    data: ArrayLike,
    flags: ArrayLike | None = None,
    *,
    variance: ArrayLike | None = None,
    mask: ArrayLike | None = None,
    thresh: float = 5.0,
    minarea: int = 3,
    box: int = 32,
    aperture_r: float = APERTURE_R_PX,
    window_sigma: float = WINDOW_SIGMA_PX,
    deblend_cont: float = 0.005,
    clean_param: float = 1.0,
    bkg: Background | None = None,
    near: tuple[ArrayLike, ArrayLike, float] | None = None,
) -> NDArray[np.void]:
    """Detect sources on a cutout.

    data: IMAGE (MJy/sr). flags: FLAGS; the bad-pixel mask is applied (SOURCE is never masked).
    variance: VARIANCE; the noise is max(sqrt(VARIANCE), background rms map).
    thresh: detection threshold in sigma per pixel.
    deblend_cont, clean_param: sep.extract's (clean_param 1.0 is sep's default; larger cleans fewer faint
    neighbours into a brighter source's wings).
    bkg: optional precomputed background(d, m, box=box) of this same data and mask (d = data with
    the bad pixels zeroed), to skip computing it twice; the result is identical.
    near: (x, y, r): measure only the objects whose peak pixel lies within r px of one of these points (the others
    are left out). Each object is measured on its own and its position always ends within 1 px of its peak, so
    with r >= d + 1 this returns exactly the rows of the full call that lie within d px of a point (the
    pipeline's saturation guard, 16 Nov: ~2x cheaper than measuring every object).
    Returns a structured array with DETECTION_DTYPE, sorted by flux, brightest first.
    """
    m = build_mask(data, flags, mask)
    d = _native(data)
    d = np.where(m, 0.0, d)
    if bkg is None:
        bkg = background(d, m, box=box)
    sub = np.ascontiguousarray(d - bkg.level)
    sub[m] = 0.0
    # Noise per pixel = max(sqrt(VARIANCE), background rms). VARIANCE is the instrumental noise;
    # in crowded fields the measured background scatter is larger (first photo, Cygnus: 0.088
    # vs 0.026 MJy/sr) because of confusion, and thresholds on VARIANCE alone find junk.
    err = np.array(bkg.rms, dtype=np.float64)
    if variance is not None:
        var = _native(variance)
        sd = np.sqrt(np.where(np.isfinite(var) & (var > 0), var, 0.0))
        err = np.maximum(err, sd)
    err = np.ascontiguousarray(err)

    mm = np.ascontiguousarray(m)
    # filter_type "conv": sep's default "matched" filter with a noise map merged stars 4 px apart
    # on the first photo; plain convolution keeps them apart.
    objs = sep.extract(sub, thresh, err=err, mask=mm, minarea=minarea, deblend_cont=deblend_cont,
                       clean_param=clean_param, filter_type="conv")
    if near is not None:
        qx, qy = np.atleast_1d(np.asarray(near[0], np.float64)), np.atleast_1d(np.asarray(near[1], np.float64))
        if len(objs) and qx.size:
            from scipy.spatial import cKDTree
            dist, _ = cKDTree(np.c_[qx, qy]).query(np.c_[objs["xpeak"], objs["ypeak"]].astype(np.float64), k=1)
            objs = objs[dist <= float(near[2])]
        else:
            objs = objs[:0]
    return measure_objects(sub, err, mm, objs, aperture_r=aperture_r, window_sigma=window_sigma)


def measure_objects(sub: NDArray[np.float64], err: NDArray[np.float64], mm: NDArray[np.bool_], objs: NDArray[np.void], *,
                    aperture_r: float = APERTURE_R_PX, window_sigma: float = WINDOW_SIGMA_PX) -> NDArray[np.void]:
    """detect's measurement of sep.extract objects on a background-subtracted image `sub` (C-contiguous float64, bad
    pixels 0) with noise `err` and mask `mm`: windowed centroid from the peak, r = aperture_r aperture.
    Returns DETECTION_DTYPE sorted by flux, brightest first."""
    out = np.zeros(len(objs), dtype=DETECTION_DTYPE)
    if len(objs) == 0:
        return out

    # Start the windowed centroid from the peak pixel, not the isophotal barycentre: in nebulous
    # fields a star can sit in a big connected blob whose barycentre is pixels away.
    px, py = objs["xpeak"].astype(np.float64), objs["ypeak"].astype(np.float64)
    bx, by = objs["x"].astype(np.float64), objs["y"].astype(np.float64)
    start_x = np.where(np.hypot(bx - px, by - py) < 1.0, bx, px)
    start_y = np.where(np.hypot(bx - px, by - py) < 1.0, by, py)
    sig = np.full(len(objs), window_sigma)
    xw, yw, wflag = sep.winpos(sub, start_x, start_y, sig, mask=mm)
    ok = ((wflag & ~_WINPOS_OK_FLAGS) == 0) & np.isfinite(xw) & np.isfinite(yw) & (np.hypot(xw - px, yw - py) < 1.0)
    xw = np.where(ok, xw, start_x)
    yw = np.where(ok, yw, start_y)

    flux, ferr, _ = sep.sum_circle(sub, xw, yw, aperture_r, err=err, mask=mm, subpix=5)
    out["x"], out["y"] = xw, yw
    out["flux"], out["flux_err"] = flux, ferr
    with np.errstate(divide="ignore", invalid="ignore"):
        out["snr"] = np.where(ferr > 0, flux / ferr, 0.0)
    out["peak"] = objs["peak"]
    out["npix"] = objs["npix"]
    out["sep_flag"] = objs["flag"].astype(np.int32) | np.where(ok, 0, SEP_FLAG_CENTROID_FALLBACK)
    return np.sort(out, order="flux")[::-1]


def detection_snr(
    data: ArrayLike,
    x: float,
    y: float,
    *,
    flags: ArrayLike | None = None,
    mask: ArrayLike | None = None,
    r: float = 2.0,
    box: int = 32,
) -> float:
    """How clearly a point source stands out at (x, y): S/N of the PSF-matched filter.

    The frame (bad pixels masked, sep background subtracted as in centroid) is smoothed with the
    PSF; the signal is the highest smoothed value within r px of (x, y), the noise the robust
    scatter (1.4826 MAD) of the same smoothed frame over all its good pixels. Measuring the noise
    on the smoothed frame itself keeps it honest on resampled stacks, whose neighbouring pixels
    are correlated, and in crowded fields, where confusion adds to it. A saturated core masked in
    every exposure (a hole at the centre) is read on the hole's rim, its bright wings.
    Returns 0 where nothing stands out, inf on a noise-free frame (synthetic tests).
    """
    m = build_mask(data, flags, mask)
    d = _native(data)
    d = np.where(m, 0.0, d)
    sub = d - background(d, m, box=box).level
    sub[m] = 0.0
    sm = gaussian_filter(sub, PSF_SIGMA_PX, mode="constant")
    good = ~m
    if not good.any():
        return 0.0
    v = sm[good]
    noise = 1.4826 * float(np.median(np.abs(v - np.median(v))))
    ny, nx = sm.shape
    yy, xx = np.ogrid[:ny, :nx]
    r2 = (xx - x) ** 2 + (yy - y) ** 2
    near = r2 <= r**2
    core = m & (r2 <= 1.5**2)
    if core.any():  # a masked core (a small hole, not the edge of the data): its rim joins the search
        lab, _ = label(m, structure=np.ones((3, 3), bool))
        sizes = np.bincount(lab.ravel())
        ids = np.unique(lab[core])
        ids = ids[(ids > 0) & (sizes[ids] <= MAX_HOLE_PX)]
        if ids.size:
            near |= binary_dilation(np.isin(lab, ids), iterations=2) & good
    if not near.any():
        return 0.0
    peak = float(np.max(sm[near]))
    if peak <= 0:
        return 0.0
    return peak / noise if noise > 0 else float("inf")


def no_data_near(data: ArrayLike, x: float, y: float, r: float, *, max_hole: int = MAX_HOLE_PX) -> bool:
    """Is part of the frame within r px of (x, y) outside the data: the edge of the area the
    exposures covered (no value: NaN)?

    A small hole (at most max_hole px) that does not reach the frame's border is a star's core
    masked in every exposure (saturated), not missing sky, and doesn't count.
    """
    d = np.asarray(data)
    nan = ~np.isfinite(d)
    if not nan.any():
        return False
    ny, nx = d.shape
    x0, x1 = max(int(np.floor(x - r)), 0), min(int(np.ceil(x + r)) + 1, nx)
    y0, y1 = max(int(np.floor(y - r)), 0), min(int(np.ceil(y + r)) + 1, ny)
    if x0 >= x1 or y0 >= y1:
        return True  # (x, y) is off the frame
    yy, xx = np.mgrid[y0:y1, x0:x1]
    disc = (xx - x) ** 2 + (yy - y) ** 2 <= r**2
    if not (nan[y0:y1, x0:x1] & disc).any():
        return False
    lab, n = label(nan, structure=np.ones((3, 3), bool))
    sizes = np.bincount(lab.ravel(), minlength=n + 1)
    edge = np.zeros(n + 1, bool)
    edge[np.unique(np.r_[lab[0], lab[-1], lab[:, 0], lab[:, -1]])] = True
    big = (sizes > max_hole) | edge
    big[0] = False
    return bool(big[lab[y0:y1, x0:x1][disc]].any())


def _gauss_model(p: NDArray[np.float64], ex: NDArray[np.float64], ey: NDArray[np.float64]) -> NDArray[np.float64]:
    """Pixel-integrated circular Gaussian + constant; p = (x, y, flux, sigma, sky)."""
    x0, y0, f, sg, sky = p
    s2 = np.sqrt(2.0) * abs(sg)
    cx = np.diff(0.5 * erf((ex - x0) / s2))
    cy = np.diff(0.5 * erf((ey - y0) / s2))
    return f * np.outer(cy, cx) + sky


def fit_centroid(
    data: NDArray[np.float64],
    mask: NDArray[np.bool_],
    x: float,
    y: float,
    *,
    half: int = 4,
    sigma0: float = PSF_SIGMA_PX,
) -> tuple[float, float] | None:
    """Least-squares fit of a pixel-integrated Gaussian to the unmasked pixels of a stamp.

    Masked pixels (e.g. a saturated core) are left out, so the fit uses the wings, and an
    asymmetric mask does not bias the centre the way a windowed centroid does.
    """
    ny, nx = data.shape
    ix, iy = int(round(x)), int(round(y))
    x0, x1 = max(ix - half, 0), min(ix + half + 1, nx)
    y0, y1 = max(iy - half, 0), min(iy + half + 1, ny)
    stamp = data[y0:y1, x0:x1]
    good = ~mask[y0:y1, x0:x1] & np.isfinite(stamp)
    if good.sum() < 8:
        return None
    ex = np.arange(x0, x1 + 1) - 0.5
    ey = np.arange(y0, y1 + 1) - 0.5
    sky0 = float(np.median(stamp[good]))
    f0 = max(float(np.sum(stamp[good] - sky0)), 1e-6)
    if mask[y0:y1, x0:x1].any():
        f0 *= 2.0  # the masked core hides much of the flux
    p0 = np.array([x, y, f0, sigma0, sky0])

    def resid(p: NDArray[np.float64]) -> NDArray[np.float64]:
        return (_gauss_model(p, ex, ey) - stamp)[good]

    lo = [x - 2.0, y - 2.0, 0.0, 0.2, -np.inf]
    hi = [x + 2.0, y + 2.0, np.inf, 5.0, np.inf]
    try:
        r = least_squares(resid, p0, bounds=(lo, hi), x_scale="jac")
    except (ValueError, np.linalg.LinAlgError):
        return None
    if not r.success or not np.all(np.isfinite(r.x[:2])):
        return None
    return float(r.x[0]), float(r.x[1])


def centroid(
    data: ArrayLike,
    x: float,
    y: float,
    *,
    flags: ArrayLike | None = None,
    mask: ArrayLike | None = None,
    search_r: float = 2.0,
    method: str = "auto",
    window_sigma: float = WINDOW_SIGMA_PX,
    subtract_background: bool = True,
    box: int = 32,
) -> tuple[float, float] | None:
    """Centroid of the source near (x, y), 0-based.

    Jumps to the brightest unmasked pixel within search_r of the guess, then:
    - method "window": sep windowed centroid (fast; ~0.02 px on an undersampled Gaussian);
    - method "fit": pixel-integrated Gaussian fit to the unmasked pixels;
    - method "auto" (default): "fit" if a masked pixel lies within 2 px of the peak (a saturated
      core: OVERFLOW and NONLINEAR are mask bits, so the fit uses the wings), else "window".
    Returns None if nothing usable is found.
    """
    if method not in ("auto", "window", "fit"):
        raise ValueError(f"unknown method {method!r}")
    m = build_mask(data, flags, mask)
    d = _native(data)
    d = np.where(m, 0.0, d)
    if subtract_background:
        d = d - background(d, m, box=box).level
    d = np.ascontiguousarray(d)
    d[m] = 0.0
    ny, nx = d.shape
    if not (0 <= x < nx and 0 <= y < ny):
        return None

    yy, xx = np.ogrid[:ny, :nx]
    near = ((xx - x) ** 2 + (yy - y) ** 2 <= search_r**2) & ~m
    if not near.any():
        return None
    iy, ix = np.unravel_index(np.argmax(np.where(near, d, -np.inf)), d.shape)

    use_fit = method == "fit"
    if method == "auto":
        core = ((xx - ix) ** 2 + (yy - iy) ** 2 <= 4.0)
        use_fit = bool(m[core].any())

    if use_fit:
        c = fit_centroid(d, m, float(ix), float(iy))
    else:
        mm = np.ascontiguousarray(m)
        xw, yw, flag = sep.winpos(d, np.array([float(ix)]), np.array([float(iy)]), np.array([window_sigma]), mask=mm)
        ok = (int(flag[0]) & ~_WINPOS_OK_FLAGS) == 0 and np.isfinite(xw[0]) and np.isfinite(yw[0])
        c = (float(xw[0]), float(yw[0])) if ok else None
    if c is None or np.hypot(c[0] - x, c[1] - y) > search_r + 1.0:
        return None
    return c
