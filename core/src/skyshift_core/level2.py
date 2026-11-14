"""Open a SPHEREx Level 2 file (or a cutout of one) from a local path or an https URL.

Reads only IMAGE, FLAGS and WCS-WAVE (plus VARIANCE when asked); ZODI and EPSF are never read.
For URLs, astropy reads through fsspec with HTTP range requests, so only the HDUs we touch are
fetched (about 23 MB of a 60 MB file, far less with `region`).

Facts (docs/research/2026-11-14-first-photo-facts.md):
- Time: MJD-AVG in the IMAGE header (UTC midpoint). Never DATE.
- Detector: IMAGE header DETECTOR (1-6). Never parse the file name.
- IMAGE is MJy/sr, float32. FLAGS is a tile-compressed int32 HDU.
- WCS-WAVE is a FITS Paper III -TAB lookup table (alternate WCS key 'W' in the IMAGE header):
  a 9 x 9 grid of control points with (wavelength, bandwidth) in um, interpolated bilinearly.
"""

from __future__ import annotations

import os
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS, FITSFixedWarning
from numpy.typing import ArrayLike, NDArray

# Small blocks: we seek past headers and jump between HDUs; big reads are fetched as one range.
_FSSPEC_KWARGS: dict[str, Any] = {"block_size": 256 * 1024, "cache_type": "readahead"}

Region = tuple[int, int, int, int]  # (x0, x1, y0, y1), 0-based, half-open, numpy order data[y0:y1, x0:x1]


@dataclass
class WaveTable:
    """Per-pixel wavelength and bandwidth (um) from the WCS-WAVE lookup table.

    psi = CRVAL + CDELT * (p - CRPIX) maps the 1-based FITS pixel p of *this* array to the
    table's index coordinates (the raw detector pixel of the full 2040 x 2040 frame); a cutout
    keeps the table and shifts CRPIX{1,2}W. Values outside the control grid are clamped.
    """

    ctrl_x: NDArray[np.float64]  # control points along x (raw FITS pixels, 1-based)
    ctrl_y: NDArray[np.float64]
    values: NDArray[np.float64]  # shape (len(ctrl_y), len(ctrl_x), 2): [..., 0] um, [..., 1] bandwidth um
    crpix: tuple[float, float] = (1.0, 1.0)
    crval: tuple[float, float] = (1.0, 1.0)
    cdelt: tuple[float, float] = (1.0, 1.0)

    def _interp(self, x: ArrayLike, y: ArrayLike, k: int) -> NDArray[np.float64]:
        xa = np.asarray(x, dtype=np.float64)
        ya = np.asarray(y, dtype=np.float64)
        px = self.crval[0] + self.cdelt[0] * (xa + 1.0 - self.crpix[0])
        py = self.crval[1] + self.cdelt[1] * (ya + 1.0 - self.crpix[1])
        cx, cy, v = self.ctrl_x, self.ctrl_y, self.values[..., k]
        px = np.clip(px, cx[0], cx[-1])
        py = np.clip(py, cy[0], cy[-1])
        ix = np.clip(np.searchsorted(cx, px, side="right") - 1, 0, cx.size - 2)
        iy = np.clip(np.searchsorted(cy, py, side="right") - 1, 0, cy.size - 2)
        fx = (px - cx[ix]) / (cx[ix + 1] - cx[ix])
        fy = (py - cy[iy]) / (cy[iy + 1] - cy[iy])
        return (
            v[iy, ix] * (1 - fx) * (1 - fy)
            + v[iy, ix + 1] * fx * (1 - fy)
            + v[iy + 1, ix] * (1 - fx) * fy
            + v[iy + 1, ix + 1] * fx * fy
        )

    def wavelength(self, x: ArrayLike, y: ArrayLike) -> NDArray[np.float64]:
        """Central wavelength (um) at 0-based pixel (x, y) of this array."""
        return self._interp(x, y, 0)

    def bandwidth(self, x: ArrayLike, y: ArrayLike) -> NDArray[np.float64]:
        """Bandwidth (um) at 0-based pixel (x, y) of this array."""
        return self._interp(x, y, 1)

    def shifted(self, x0: int, y0: int) -> "WaveTable":
        """The same table for a sub-array starting at 0-based (x0, y0)."""
        return WaveTable(self.ctrl_x, self.ctrl_y, self.values,
                         (self.crpix[0] - x0, self.crpix[1] - y0), self.crval, self.cdelt)

    @classmethod
    def from_hdus(cls, image_header: fits.Header, table: fits.FITS_rec) -> "WaveTable":
        def g(key: str, default: float) -> float:
            return float(image_header.get(key, default))

        values = np.asarray(table["VALUES"][0], dtype=np.float64)  # numpy (ny, nx, 2)
        # PV1_3W / PV2_3W give the 1-based index of wavelength / bandwidth in VALUES' first FITS axis
        iw = int(image_header.get("PV1_3W", 1)) - 1
        ib = int(image_header.get("PV2_3W", 2)) - 1
        values = np.stack([values[..., iw], values[..., ib]], axis=-1)
        return cls(
            ctrl_x=np.asarray(table["X"][0], dtype=np.float64),
            ctrl_y=np.asarray(table["Y"][0], dtype=np.float64),
            values=values,
            crpix=(g("CRPIX1W", 1.0), g("CRPIX2W", 1.0)),
            crval=(g("CRVAL1W", 1.0), g("CRVAL2W", 1.0)),
            cdelt=(g("CDELT1W", 1.0), g("CDELT2W", 1.0)),
        )


@dataclass
class Level2:
    """One SPHEREx Level 2 exposure (or a region of it). Pixels are 0-based numpy [y, x]."""

    data: NDArray[np.float32]  # IMAGE, MJy/sr
    flags: NDArray[np.int32]  # FLAGS bits, contracts/flags.md
    variance: NDArray[np.float32] | None  # VARIANCE, (MJy/sr)^2, only if asked
    wcs: WCS  # celestial WCS (TAN-SIP) of this array
    mjd_avg: float  # MJD-AVG, UTC exposure midpoint
    mjd_beg: float | None
    mjd_end: float | None
    detector: int  # 1-6, from the IMAGE header DETECTOR
    obsid: str  # e.g. "2025W18_1B_0074_2"
    wave: WaveTable | None
    origin: tuple[int, int] = (0, 0)  # (x0, y0) of this array in the file's pixels
    header: fits.Header = field(default_factory=fits.Header, repr=False)  # IMAGE header

    def wavelength(self, x: ArrayLike, y: ArrayLike) -> NDArray[np.float64]:
        """Central wavelength (um) at 0-based pixel (x, y)."""
        if self.wave is None:
            raise ValueError("no WCS-WAVE table in this file")
        return self.wave.wavelength(x, y)

    def wavelength_map(self) -> NDArray[np.float64]:
        """Wavelength (um) of every pixel, same shape as data."""
        yy, xx = np.indices(self.data.shape)
        return self.wavelength(xx, yy)

    def wavelength_range(self) -> tuple[float, float]:
        """(um_min, um_max) seen by this array's pixels (frames.epochs[].um_min/um_max)."""
        m = self.wavelength_map()
        return float(m.min()), float(m.max())


def _celestial_wcs(header: fits.Header) -> WCS:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FITSFixedWarning)
        return WCS(header).celestial  # key ' ' only: the A and W alternates are ignored


def _read(hdu: Any, region: Region | None) -> NDArray[Any]:
    if region is None:
        return np.asarray(hdu.data)
    x0, x1, y0, y1 = region
    return np.asarray(hdu.section[y0:y1, x0:x1])


def _is_url(src: str | os.PathLike[str]) -> bool:
    return isinstance(src, str) and src.startswith(("http://", "https://", "s3://"))


def open_level2(
    src: str | os.PathLike[str],
    *,
    variance: bool = False,
    region: Region | None = None,
) -> Level2:
    """Read a Level 2 file or cutout.

    src: local path, or an http(s):// URL (fsspec + range reads; needs aiohttp).
    variance: also read VARIANCE (16 MB more for a full frame).
    region: (x0, x1, y0, y1) in 0-based pixels, half-open; reads only that block through
        astropy's `.section` (only the needed bytes or compressed tiles), and shifts the WCS
        and the wavelength table to match. None reads the whole array.
    """
    url = _is_url(src)
    open_kwargs: dict[str, Any] = {"lazy_load_hdus": True, "memmap": False}
    if url:
        open_kwargs.update(use_fsspec=True, fsspec_kwargs=_FSSPEC_KWARGS)
    path = src if url else str(Path(src))

    with fits.open(path, **open_kwargs) as hdul:
        img = hdul["IMAGE"]
        header = img.header.copy()
        data = _read(img, region).astype(np.float32, copy=False)
        flg = _read(hdul["FLAGS"], region).astype(np.int32, copy=False)
        var = _read(hdul["VARIANCE"], region).astype(np.float32, copy=False) if variance else None
        try:
            wave: WaveTable | None = WaveTable.from_hdus(header, hdul["WCS-WAVE"].data)
        except KeyError:
            wave = None

    wcs = _celestial_wcs(header)
    origin = (0, 0)
    if region is not None:
        x0, x1, y0, y1 = region
        wcs = wcs[y0:y1, x0:x1]  # astropy shifts CRPIX and the SIP reference point
        origin = (x0, y0)
        if wave is not None:
            wave = wave.shifted(x0, y0)

    if data.shape != flg.shape:
        raise ValueError(f"IMAGE {data.shape} and FLAGS {flg.shape} differ")

    def opt(key: str) -> float | None:
        v = header.get(key)
        return None if v is None else float(v)

    if "MJD-AVG" not in header:
        raise KeyError("IMAGE header has no MJD-AVG")
    if "DETECTOR" not in header:
        raise KeyError("IMAGE header has no DETECTOR")

    return Level2(
        data=data,
        flags=flg,
        variance=var,
        wcs=wcs,
        mjd_avg=float(header["MJD-AVG"]),
        mjd_beg=opt("MJD-BEG"),
        mjd_end=opt("MJD-END"),
        detector=int(header["DETECTOR"]),
        obsid=str(header.get("OBSID", "")).strip(),
        wave=wave,
        origin=origin,
        header=header,
    )


def detector_from_bandpass(name: str) -> int:
    """'SPHEREx-D2' (TAP energy_bandpassname) -> 2."""
    s = name.strip().upper()
    if not s.startswith("SPHEREX-D") or not s[9:].isdigit():
        raise ValueError(f"not a SPHEREx band name: {name!r}")
    d = int(s[9:])
    if not 1 <= d <= 6:
        raise ValueError(f"detector out of range: {name!r}")
    return d
