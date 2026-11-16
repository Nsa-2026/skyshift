"""Dark holes: unflagged pixels far below the sky, masked before detection (extraction v2, 16 Nov, second pass).

What they are (measured on 36 full photos, docs/extraction-v2.md "16 Nov, second pass"): MWIR photos (D4-D6) carry
~80-650 single pixels per photo more than 10 sigma below the sky (the deepest -400 to -1,800 sigma) with no FLAGS bit;
groups of 2+ such pixels are almost absent. They are mostly the same detector pixels in every photo: two D4 photos of
other weeks and fields share 100-250 of them, consecutive exposures 80% (cold pixels missing from NASA's COLD mask, plus
a session's own share). They are not tied to PERSIST or OUTLIER pixels (20% lie within 2 px of one, about the share of
the field). SWIR photos have 0-10. Each one pulls a nearby source's aperture and ePSF flux down and makes a deep
negative peak in the matched filter, whose negative side extraction v2 uses as its false-alarm control.

Threshold: at 7 sigma two thirds of the negative-image matched-filter dots stayed (their deepest pixel lay at -5 to
-7 sigma, half of them a single pixel); at 5 sigma 71-94% of them go in every detector, with 300-1,270 holes per MWIR
photo and 2-140 per SWIR one (pure noise gives ~1 pixel below -5 sigma per photo), no known asteroid and no Gaia star
lost, and the fluxes of S/N > 10 dots with a hole in their aperture rise 11-14% (the hole had pulled them down).

Rule: on IMAGE minus its background, a pixel is low when it lies more than HOLE_SIGMA x its noise below the sky;
8-connected groups of HOLE_MIN_PX to HOLE_MAX_PX low pixels are holes (larger low areas are background structure, left
alone); optionally only groups within HOLE_NEAR_PX of a pixel with one of HOLE_NEAR_BITS; each hole grown by
HOLE_GROW_PX. Real sources are positive, so with no growth the mask never takes a source's light, only a broken pixel
next to it. Pure numpy/scipy, no I/O; the pipeline passes its config.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy import ndimage

from .flags import OUTLIER, PERSIST

HOLE_SIGMA = 5.0                    # a pixel this many sigma below the sky is low
HOLE_MIN_PX = 1                     # a hole is this many to ...
HOLE_MAX_PX = 4                     # ... this many 8-connected low pixels
HOLE_NEAR_BITS = 0                  # 0: anywhere; else within HOLE_NEAR_PX of a pixel with one of these bits
HOLE_NEAR_PX = 2                    #   (PERSIST | OUTLIER was the first guess; the holes don't follow them)
HOLE_GROW_PX = 0                    # the mask covers each hole grown by this many pixels (8-connected steps)
NEAR_PERSIST_OUTLIER = PERSIST | OUTLIER


def dark_holes(sub: ArrayLike, noise: ArrayLike, bad: ArrayLike, flags: ArrayLike | None = None, *,
               sigma: float = HOLE_SIGMA, min_px: int = HOLE_MIN_PX, max_px: int = HOLE_MAX_PX,
               near_bits: int = HOLE_NEAR_BITS, near_px: int = HOLE_NEAR_PX,
               grow_px: int = HOLE_GROW_PX) -> tuple[NDArray[np.bool_], dict]:
    """The dark-hole mask of one photo (module docstring) and its counts.

    sub: IMAGE minus its background (MJy/sr); noise: per-pixel 1 sigma (max(sqrt(VARIANCE), background rms));
    bad: the bad-pixel mask (contracts/flags.md); flags: FLAGS (needed when near_bits != 0).
    Returns (mask: True on hole pixels not already bad, info: n_holes, n_px, depth_min_sigma)."""
    s = np.asarray(sub, np.float64)
    nz = np.asarray(noise, np.float64)
    b = np.asarray(bad, bool)
    with np.errstate(invalid="ignore", divide="ignore"):
        low = ~b & np.isfinite(s) & np.isfinite(nz) & (nz > 0) & (s < -sigma * nz)
    info = {"n_holes": 0, "n_px": 0, "depth_min_sigma": None}
    if not low.any():
        return np.zeros(s.shape, bool), info
    st8 = np.ones((3, 3), bool)
    lab, n = ndimage.label(low, structure=st8)
    size = np.bincount(lab.ravel(), minlength=n + 1)
    keep = (size >= min_px) & ((size <= max_px) if max_px and max_px > 0 else True)
    keep[0] = False
    if near_bits and keep.any():
        if flags is None:
            keep[:] = False
        else:
            near = (np.asarray(flags).astype(np.int64) & int(near_bits)) != 0
            if near_px > 0:
                near = ndimage.binary_dilation(near, structure=st8, iterations=int(near_px))
            touch = np.zeros(n + 1, bool)
            touch[np.unique(lab[near & (lab > 0)])] = True
            keep &= touch
    if not keep.any():
        return np.zeros(s.shape, bool), info
    core = keep[lab]
    with np.errstate(invalid="ignore", divide="ignore"):
        depth = float(np.nanmin(np.where(core, s / nz, np.nan)))
    m = ndimage.binary_dilation(core, structure=st8, iterations=int(grow_px)) if grow_px > 0 else core
    m &= ~b
    info.update(n_holes=int(keep.sum()), n_px=int(m.sum()), depth_min_sigma=round(depth, 1))
    return m, info
