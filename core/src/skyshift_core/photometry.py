"""Aperture photometry and MJy/sr -> uJy.

IMAGE is surface brightness in MJy/sr. A pixel's flux density is value x pixel solid angle:
1 MJy/sr x 8.897e-10 sr (6.152" x 6.152") = 889.7 uJy. We compute the solid angle from the WCS
rather than hard-coding it, locally at (x, y) when given (SIP distortion changes it slightly
across the frame).

Aperture correction (14 Nov, from NASA's ePSF in the EPSF HDU): a 2 px radius holds 96.8% (± 1.0%)
of a point source's light, so APERTURE_CORRECTION = 1 / 0.968 = 1.033 for the default radius.
It is only valid for r = 2 px (APERTURE_R_PX); other radii need their own value. The dot table's
`flux_ujy` (from detect) stays uncorrected; `flux_psf_ujy` is already a total flux.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import sep
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_area
from numpy.typing import ArrayLike, NDArray

from .detect import APERTURE_R_PX, _native, build_mask

DEG2_TO_SR = (np.pi / 180.0) ** 2
MJY_SR_TO_UJY_PER_SR = 1e12  # 1 MJy = 1e12 uJy
APERTURE_CORRECTION = 1.033  # r = 2 px only; see the module docstring


def pixel_solid_angle_sr(wcs: WCS, x: float | None = None, y: float | None = None) -> float:
    """Pixel solid angle in steradians.

    Without (x, y): from the linear part of the WCS (CD/PC x CDELT), i.e. at the reference pixel.
    With (x, y): local area from the full WCS (incl. SIP) by finite differences.
    """
    w = wcs.celestial
    if x is None or y is None:
        return float(proj_plane_pixel_area(w) * DEG2_TO_SR)
    pts = np.array([[x, y], [x + 0.5, y], [x - 0.5, y], [x, y + 0.5], [x, y - 0.5]], dtype=np.float64)
    ra, dec = w.all_pix2world(pts[:, 0], pts[:, 1], 0)
    cosd = np.cos(np.deg2rad(dec[0]))

    def offs(i: int) -> NDArray[np.float64]:
        dra = ((ra[i] - ra[0] + 180.0) % 360.0) - 180.0
        return np.array([dra * cosd, dec[i] - dec[0]])

    jx = offs(1) - offs(2)  # d(sky)/dx over 1 px, degrees
    jy = offs(3) - offs(4)
    return float(abs(jx[0] * jy[1] - jx[1] * jy[0]) * DEG2_TO_SR)


def ujy_per_mjysr(wcs: WCS, x: float | None = None, y: float | None = None) -> float:
    """uJy in one pixel per 1 MJy/sr (about 889.7 for a 6.152" pixel)."""
    return pixel_solid_angle_sr(wcs, x, y) * MJY_SR_TO_UJY_PER_SR


def to_ujy(mjysr_sum: ArrayLike, wcs: WCS, x: float | None = None, y: float | None = None) -> NDArray[np.float64]:
    """Convert a sum of pixel values (MJy/sr) to uJy."""
    return np.asarray(mjysr_sum, dtype=np.float64) * ujy_per_mjysr(wcs, x, y)


@dataclass
class Photometry:
    flux_mjysr: NDArray[np.float64]  # sum of pixel values in the aperture, background subtracted
    err_mjysr: NDArray[np.float64]
    flux_ujy: NDArray[np.float64]
    err_ujy: NDArray[np.float64]
    sep_flag: NDArray[np.int64]


def aperture_photometry(
    data: ArrayLike,
    x: ArrayLike,
    y: ArrayLike,
    wcs: WCS,
    *,
    r: float = APERTURE_R_PX,
    flags: ArrayLike | None = None,
    variance: ArrayLike | None = None,
    mask: ArrayLike | None = None,
    bkg_annulus: tuple[float, float] | None = (4.0, 7.0),
    local_area: bool = True,
) -> Photometry:
    """Circular-aperture photometry at 0-based (x, y), in MJy/sr summed and in uJy.

    The bad-pixel mask (contracts/flags.md) is applied. Masked pixels inside the aperture are
    simply left out (no interpolation), so a masked core under-estimates the flux; check
    flags.is_saturated_core() and report `saturated`.
    bkg_annulus: (r_in, r_out) in px for a local background, or None if data is already
    background subtracted. variance: VARIANCE plane for the error; else no error (0).
    """
    xs = np.atleast_1d(np.asarray(x, dtype=np.float64))
    ys = np.atleast_1d(np.asarray(y, dtype=np.float64))
    m = np.ascontiguousarray(build_mask(data, flags, mask))
    d = _native(data)
    d = np.ascontiguousarray(np.where(m, 0.0, d))
    var = None
    if variance is not None:
        v = _native(variance)
        var = np.ascontiguousarray(np.where(np.isfinite(v) & ~m, v, 0.0))
    flux, ferr, flag = sep.sum_circle(d, xs, ys, r, var=var, mask=m, bkgann=bkg_annulus, subpix=5)
    flux = np.asarray(flux, dtype=np.float64) * APERTURE_CORRECTION
    ferr = np.asarray(ferr, dtype=np.float64) * APERTURE_CORRECTION
    if local_area:
        conv = np.array([ujy_per_mjysr(wcs, xi, yi) for xi, yi in zip(xs, ys)])
    else:
        conv = np.full(xs.shape, ujy_per_mjysr(wcs))
    return Photometry(flux, ferr, flux * conv, ferr * conv, np.asarray(flag, dtype=np.int64))
