"""Photometry on one cutout: centroid near a predicted position, robust background, apertures.

Built on skyshift_core: centroid (wings when the core is masked), aperture_photometry (uJy, the
contracts/flags.md mask, aperture correction 1.033 at r = 2 px only), is_saturated_core.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from astropy.stats import sigma_clipped_stats

from skyshift_core import (
    Level2,
    aperture_photometry,
    bad_pixel_mask,
    centroid,
    flags_under,
    is_saturated_core,
    sky_to_pixel,
)
from skyshift_core.flags import RECORD_BITS


@dataclass
class Point:
    ok: bool
    why: str = ""
    x: float = np.nan
    y: float = np.nan
    offset_px: float = np.nan
    um: float = np.nan
    bw_um: float = np.nan
    flux: dict[float, float] = field(default_factory=dict)  # aperture radius px -> uJy
    err: dict[float, float] = field(default_factory=dict)
    bkg_mjysr: float = np.nan
    saturated: bool = False
    record_flags: int = 0


def robust_background(data: np.ndarray, mask: np.ndarray, x: float, y: float, r_in: float, r_out: float
                      ) -> tuple[float, float, int]:
    """Sigma-clipped median and std of the unmasked pixels in an annulus (MJy/sr)."""
    yy, xx = np.indices(data.shape)
    rr = np.hypot(xx - x, yy - y)
    sel = (rr >= r_in) & (rr <= r_out) & ~mask & np.isfinite(data)
    if sel.sum() < 10:
        return np.nan, np.nan, int(sel.sum())
    _, med, std = sigma_clipped_stats(data[sel], sigma=3.0, maxiters=5)
    return float(med), float(std), int(sel.sum())


def measure(l2: Level2, ra: float, dec: float, *, radii: tuple[float, ...] = (2.0,),
            annulus: tuple[float, float] = (4.0, 9.0), recentre: bool = True, max_offset_px: float = 1.5,
            mask_r: float = 2.0) -> Point:
    """Photometry of a (moving) source predicted at ra, dec.

    recentre: centroid within 2 px of the prediction (dropped if > max_offset_px away); else forced
    at the prediction. A point is rejected when a mask bit falls within `mask_r` px (masked pixels
    would be left out of the sum) or the core is saturated.
    """
    px, py = sky_to_pixel(l2.wcs, ra, dec)
    px, py = float(np.atleast_1d(px)[0]), float(np.atleast_1d(py)[0])
    ny, nx = l2.data.shape
    rmax = max(max(radii), annulus[1])
    if not (0 <= px < nx and 0 <= py < ny):
        return Point(False, "off cutout")
    x, y = px, py
    if recentre:
        c = centroid(l2.data, px, py, flags=l2.flags, search_r=2.0, subtract_background=False)
        if c is None:
            return Point(False, "no centroid")
        x, y = c
    off = float(np.hypot(x - px, y - py))
    if off > max_offset_px:
        return Point(False, f"centroid {off:.1f} px from prediction", x=x, y=y, offset_px=off)
    if x - rmax < -0.5 or y - rmax < -0.5 or x + rmax > nx - 0.5 or y + rmax > ny - 0.5:
        # the annulus may be clipped by the cutout edge; still usable if enough pixels remain
        pass
    sat = is_saturated_core(l2.flags, x, y)
    rec = int(flags_under(l2.flags, x, y, mask_r) & RECORD_BITS)
    m = bad_pixel_mask(l2.flags)
    yy, xx = np.indices(m.shape)
    if (m & (np.hypot(xx - x, yy - y) <= mask_r)).any():
        return Point(False, "saturated" if sat else "masked pixel in aperture", x=x, y=y, offset_px=off,
                     saturated=sat, record_flags=rec)
    bkg, _, nb = robust_background(l2.data, m, x, y, *annulus)
    if not np.isfinite(bkg):
        return Point(False, f"background: {nb} px", x=x, y=y, offset_px=off)
    sub = l2.data.astype(np.float64) - bkg
    pt = Point(True, "", x=x, y=y, offset_px=off, bkg_mjysr=bkg, saturated=sat, record_flags=rec)
    pt.um = float(l2.wavelength(x, y))
    pt.bw_um = float(l2.wave.bandwidth(x, y)) if l2.wave is not None else np.nan
    for r in radii:
        ph = aperture_photometry(sub, x, y, l2.wcs, r=r, flags=l2.flags, variance=l2.variance, bkg_annulus=None)
        pt.flux[r] = float(ph.flux_ujy[0])
        pt.err[r] = float(ph.err_ujy[0])
    return pt
