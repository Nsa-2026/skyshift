"""Common sky grid, resampling of cutouts onto it, median stacks, and the 16-bit frame encoding.

Grid (contracts/api.md): a tangent plane centred on the requested position, north up, east left.
Row 0 of every array is the NORTH edge and column 0 the EAST edge, exactly as the frame bytes are
written, so the grid's WCS has both pixel axes pointing "backwards" (x to the west, y to the
south) and the centre sits at pixel ((nx-1)/2, (ny-1)/2).

    months  5'  3x oversampled  2.05"/px   146 px
    months 15'  2x oversampled  3.075"/px  293 px   (the contract table prints 3.08)
    hours  20'  native          6.15"/px   195 px
    hours  60'  binned 2x       12.3"/px   293 px   (resampled at 6.15", then 2x2 mean)

Resampling: bilinear, weighted by the good-pixel mask (bad pixels get weight 0, so a masked pixel
leaves a hole only where it holds most of the weight). Values stay in MJy/sr (surface brightness
is not changed by resampling).

Stack: each exposure's sky level (median of its good pixels in the frame) is taken off, the
NaN-median over exposures is taken per pixel, and the median sky level is put back. Without the
levelling, exposures with different zodiacal levels (each one sees the field at a different
wavelength) would print their footprints into the median. The median also removes asteroids and
cosmic rays.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np
from astropy.wcs import WCS
from numpy.typing import NDArray
from scipy.ndimage import map_coordinates
from skyshift_core import Level2, bad_pixel_mask, saturated_mask

NATIVE_ARCSEC = 6.15

# (clock, fov arcmin) -> (pixel arcsec, pixels a side, bin factor applied after resampling)
SAMPLING: dict[tuple[str, int], tuple[float, int, int]] = {
    ("months", 5): (NATIVE_ARCSEC / 3, 146, 1),
    ("months", 15): (NATIVE_ARCSEC / 2, 293, 1),
    ("hours", 20): (NATIVE_ARCSEC, 195, 1),
    ("hours", 60): (NATIVE_ARCSEC * 2, 293, 2),
}
DEFAULT_FOV = {"months": 5, "hours": 20}


@dataclass
class Grid:
    ra: float
    dec: float
    pixel_arcsec: float
    n: int

    @property
    def wcs(self) -> WCS:
        w = WCS(naxis=2)
        w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
        w.wcs.crval = [self.ra, self.dec]
        w.wcs.crpix = [(self.n + 1) / 2.0, (self.n + 1) / 2.0]  # 1-based: centre at (n-1)/2 0-based
        # column 0 = east: RA grows to the left, so +x is west (dRA*cos(dec) < 0 on the sky, and
        # the FITS CDELT1 is in "RA increasing" units, hence positive is east: use negative).
        # row 0 = north: +y is south, so CDELT2 is negative too.
        w.wcs.cdelt = [-self.pixel_arcsec / 3600.0, -self.pixel_arcsec / 3600.0]
        w.wcs.set()
        return w

    def sky(self) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        """(ra, dec) of every pixel centre, arrays [row, col]."""
        yy, xx = np.indices((self.n, self.n), dtype=np.float64)
        ra, dec = self.wcs.wcs_pix2world(xx, yy, 0)
        return ra, dec

    def binned(self, factor: int) -> "Grid":
        return Grid(self.ra, self.dec, self.pixel_arcsec * factor, self.n // factor)


def grid_for(ra: float, dec: float, clock: str, fov: float) -> tuple[Grid, int]:
    """(resampling grid, bin factor). After binning the grid has the contract's size and scale."""
    try:
        pix, n, b = SAMPLING[(clock, int(round(fov)))]
    except KeyError:
        raise ValueError(f"fov {fov} is not allowed for the {clock} clock "
                         f"(months: 5 or 15; hours: 20 or 60)") from None
    return Grid(ra, dec, pix / b, n * b), b


@dataclass
class Resampled:
    """One exposure on the grid."""

    image: NDArray[np.float32]  # MJy/sr, NaN = no good data
    covered: NDArray[np.bool_]  # grid pixels inside the exposure
    bad: NDArray[np.bool_]  # covered pixels whose nearest source pixel has a mask bit
    sat: NDArray[np.bool_]  # ... has OVERFLOW or NONLINEAR
    um_min: float
    um_max: float


def resample(l2: Level2, grid: Grid, sky: tuple[NDArray, NDArray] | None = None) -> Resampled:
    """Bilinear resampling of one cutout onto the grid, bad pixels masked (never SOURCE)."""
    ra, dec = sky if sky is not None else grid.sky()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        x, y = l2.wcs.all_world2pix(ra, dec, 0, quiet=True)
    ny, nx = l2.data.shape
    covered = (x > -0.5) & (x < nx - 0.5) & (y > -0.5) & (y < ny - 0.5) & np.isfinite(x) & np.isfinite(y)
    bad_src = bad_pixel_mask(l2.flags) | ~np.isfinite(l2.data)
    good = (~bad_src).astype(np.float64)
    data = np.where(bad_src, 0.0, l2.data).astype(np.float64)
    coords = np.array([y, x])
    num = map_coordinates(data, coords, order=1, mode="nearest")
    den = map_coordinates(good, coords, order=1, mode="nearest")
    ok = covered & (den > 0.5)
    image = np.full(ra.shape, np.nan, dtype=np.float32)
    image[ok] = (num[ok] / den[ok]).astype(np.float32)

    xi = np.clip(np.rint(x), 0, nx - 1).astype(np.intp)
    yi = np.clip(np.rint(y), 0, ny - 1).astype(np.intp)
    xi[~covered], yi[~covered] = 0, 0
    bad = covered & bad_src[yi, xi]
    sat = covered & saturated_mask(l2.flags)[yi, xi]
    if covered.any() and l2.wave is not None:
        um = l2.wavelength(x[covered], y[covered])
        um_min, um_max = float(um.min()), float(um.max())
    else:
        um_min = um_max = float("nan")
    return Resampled(image, covered, bad, sat, um_min, um_max)


def bin_mean(a: NDArray, factor: int) -> NDArray[np.float32]:
    """factor x factor NaN-mean (NaN only where every input pixel is NaN)."""
    if factor == 1:
        return a.astype(np.float32, copy=False)
    n = a.shape[0] // factor
    blocks = a[: n * factor, : n * factor].reshape(n, factor, n, factor)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmean(blocks, axis=(1, 3)).astype(np.float32)


def sky_level(image: NDArray) -> float:
    """Median of the finite pixels (the background dominates a SPHEREx cutout), NaN if none."""
    v = image[np.isfinite(image)]
    return float(np.median(v)) if v.size else float("nan")


def median_stack(images: list[NDArray], level: bool = True) -> NDArray[np.float32]:
    """Per-pixel NaN-median of exposures on one grid. NaN where no exposure has good data.
    level: take each exposure's sky level off first and put the median level back."""
    if not images:
        raise ValueError("nothing to stack")
    cube = np.stack(images).astype(np.float32)
    back = 0.0
    if level:
        levels = np.array([sky_level(im) for im in cube], dtype=np.float64)
        okl = np.isfinite(levels)
        if okl.any():
            back = float(np.median(levels[okl]))
            cube = cube - np.where(okl, levels, 0.0).astype(np.float32)[:, None, None]
            cube[~okl] = np.nan
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        out = np.nanmedian(cube, axis=0)
    return (out + back).astype(np.float32)


def fractions(parts: list[Resampled]) -> tuple[float, float]:
    """(flag_frac, sat_frac): share of the frame's covered pixels, over all exposures, with a
    mask bit / with OVERFLOW or NONLINEAR."""
    n = sum(int(p.covered.sum()) for p in parts)
    if n == 0:
        return 0.0, 0.0
    return (sum(int(p.bad.sum()) for p in parts) / n, sum(int(p.sat.sum()) for p in parts) / n)


# --- 16-bit encoding (contracts/api.md, "Frame data") -----------------------------------------

def robust_sigma(image: NDArray) -> float:
    v = image[np.isfinite(image)]
    if v.size < 2:
        return 0.0
    med = np.median(v)
    return float(1.4826 * np.median(np.abs(v - med)))


def encode(image: NDArray, *, max_step_sigma: float = 0.5) -> tuple[bytes, float, float]:
    """(bytes, scale, zero): raw little-endian uint16, row by row; value = raw * scale + zero;
    raw 0 = no data, real values use 1..65535.

    The step is the full range / 65534, unless that is coarser than max_step_sigma x the frame's
    noise (a saturated star next to a faint sky): then the step is capped there and the brightest
    pixels are clipped to raw 65535 (the top value is a lower limit). Faint things stay visible.
    """
    a = np.asarray(image, dtype=np.float64)
    ok = np.isfinite(a)
    if not ok.any():
        return np.zeros(a.shape, dtype="<u2").tobytes(), 1.0, 0.0
    vmin, vmax = float(a[ok].min()), float(a[ok].max())
    scale = (vmax - vmin) / 65534.0 if vmax > vmin else 1.0
    sig = robust_sigma(a)
    if sig > 0 and scale > max_step_sigma * sig:
        scale = max_step_sigma * sig
    zero = vmin - scale
    raw = np.zeros(a.shape, dtype="<u2")
    raw[ok] = np.clip(np.rint((a[ok] - zero) / scale), 1, 65535).astype("<u2")
    return raw.tobytes(), scale, zero


def decode(data: bytes, scale: float, zero: float, shape: tuple[int, int]) -> NDArray[np.float64]:
    """Inverse of encode: NaN where raw is 0."""
    raw = np.frombuffer(data, dtype="<u2").reshape(shape)
    return np.where(raw == 0, np.nan, raw * scale + zero)
