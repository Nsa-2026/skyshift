"""FLAGS bits and the mask policy of contracts/flags.md.

Bit numbers were confirmed on the first real file (FLAGS header MSKNnnnn keys, 14 Nov 2026).
Policy: mask the "mask" bits for detection and photometry, record the "record" bits on a dot,
never mask SOURCE (set on ~99% of pixels) or FULLSAMPLE.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import ArrayLike, NDArray

# --- bit values (1 << bit number) ---------------------------------------------------------
TRANSIENT = 1 << 0
OVERFLOW = 1 << 1
SUR_ERROR = 1 << 2
NONFUNC = 1 << 6
DICHROIC = 1 << 7
MISSING_DATA = 1 << 9
HOT = 1 << 10
COLD = 1 << 11
FULLSAMPLE = 1 << 12
PHANMISS = 1 << 14
NONLINEAR = 1 << 15
PERSIST = 1 << 17
OUTLIER = 1 << 19
CROSSTALK = 1 << 20
SOURCE = 1 << 21
GHOST = 1 << 22
GHOST_FPA = 1 << 23
GHOST_EXT = 1 << 24
STREAK = 1 << 25
BLOOM = 1 << 26
SNOWBALL = 1 << 27
HALO = 1 << 28

BIT_NAMES: dict[int, str] = {
    0: "TRANSIENT", 1: "OVERFLOW", 2: "SUR_ERROR", 6: "NONFUNC", 7: "DICHROIC",
    9: "MISSING_DATA", 10: "HOT", 11: "COLD", 12: "FULLSAMPLE", 14: "PHANMISS",
    15: "NONLINEAR", 17: "PERSIST", 19: "OUTLIER", 20: "CROSSTALK", 21: "SOURCE",
    22: "GHOST", 23: "GHOST_FPA", 24: "GHOST_EXT", 25: "STREAK", 26: "BLOOM",
    27: "SNOWBALL", 28: "HALO",
}

# --- policy masks (values copied from contracts/flags.md; tests check they match the bits) ---
MASK_BITS = 0x0A02CEC7    # bad_pixel_mask(flags) = (flags & MASK_BITS) != 0
RECORD_BITS = 0x15D80000  # kept in the dot's flags column, never used to mask
NEVER_BITS = 0x00201000   # FULLSAMPLE | SOURCE
SAT_BITS = 0x00008002     # OVERFLOW | NONLINEAR


def bad_pixel_mask(flags: ArrayLike) -> NDArray[np.bool_]:
    """True where a pixel has any 'mask' bit. SOURCE and FULLSAMPLE are never masked."""
    f = np.asarray(flags).astype(np.int64, copy=False)
    return (f & MASK_BITS) != 0


def record_bits(flags: ArrayLike) -> NDArray[np.int32]:
    """Only the 'record' bits (OUTLIER, CROSSTALK, GHOST*, BLOOM, HALO) of each pixel."""
    f = np.asarray(flags).astype(np.int64, copy=False)
    return (f & RECORD_BITS).astype(np.int32)


def saturated_mask(flags: ArrayLike) -> NDArray[np.bool_]:
    """True where OVERFLOW or NONLINEAR is set."""
    f = np.asarray(flags).astype(np.int64, copy=False)
    return (f & SAT_BITS) != 0


def _disk(shape: tuple[int, ...], x: float, y: float, r: float) -> NDArray[np.bool_]:
    yy, xx = np.ogrid[: shape[0], : shape[1]]
    return (xx - x) ** 2 + (yy - y) ** 2 <= r * r


def flags_under(flags: ArrayLike, x: float, y: float, r: float = 1.5) -> int:
    """OR of every flag bit within r pixels of (x, y), 0-based. The dot table's `flags` column."""
    f = np.asarray(flags)
    sel = _disk(f.shape, x, y, r)
    if not sel.any():
        return 0
    return int(np.bitwise_or.reduce(f[sel].astype(np.int64)) & 0xFFFFFFFF)


def is_saturated_core(flags: ArrayLike, x: float, y: float, r: float = 1.5) -> bool:
    """True if OVERFLOW or NONLINEAR is set on any pixel within r pixels of (x, y), 0-based.

    This is the `saturated` field of the spectrum (contracts/api.md). Default r = 1.5 px covers
    the PSF core (FWHM 7.53" = 1.22 px).
    """
    return bool(flags_under(flags, x, y, r) & SAT_BITS)


def flag_frac(flags: ArrayLike) -> float:
    """Share of pixels hit by a mask bit (frames.epochs[].flag_frac), 0-1."""
    m = bad_pixel_mask(flags)
    return float(m.mean()) if m.size else 0.0


def sat_frac(flags: ArrayLike) -> float:
    """Share of pixels with OVERFLOW or NONLINEAR (frames.epochs[].sat_frac), 0-1."""
    m = saturated_mask(flags)
    return float(m.mean()) if m.size else 0.0


def bit_names(value: int) -> list[str]:
    """Names of the defined bits set in one flag value (undefined bits are ignored)."""
    return [name for bit, name in BIT_NAMES.items() if value & (1 << bit)]
