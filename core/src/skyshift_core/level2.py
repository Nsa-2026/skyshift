"""Open a SPHEREx Level 2 file (or a cutout of one) from a local path or an https URL.

Reads IMAGE, FLAGS and WCS-WAVE, plus VARIANCE, ZODI and EPSF only when asked.
For URLs, astropy reads through fsspec with HTTP range requests, so only the HDUs we touch are
fetched (about 23 MB of a 60 MB file, far less with `region`; ZODI adds 16.6 MB, EPSF 3.9 MB).

Facts (docs/research/2026-11-14-first-photo-facts.md):
- Time: MJD-AVG in the IMAGE header (UTC midpoint). Never DATE.
- Detector: IMAGE header DETECTOR (1-6). Never parse the file name.
- IMAGE is MJy/sr, float32. FLAGS is a tile-compressed int32 HDU.
- WCS-WAVE is a FITS Paper III -TAB lookup table (alternate WCS key 'W' in the IMAGE header):
  a 9 x 9 grid of control points with (wavelength, bandwidth) in um, interpolated bilinearly.
- ZODI (Explanatory Supplement 2.2 and 3.2.11): the zodiacal-light model in MJy/sr, 2040 x 2040.
  It is NOT subtracted from IMAGE ("no zodiacal light subtraction is applied"); IRSA's mosaics do
  IMAGE - ZODI. Our detection fits its own local background, so zodi is recorded, not subtracted.
- EPSF (Expsupp 4.1.2 and 4.2.3): a table of effective PSFs (Anderson & King 2000; photutils
  EPSFBuilder), one row per detector zone (21 x 21; 11 x 41 on D3), each 33 x 33 at 5x
  oversampling, summing to 1. An ePSF already includes the pixel response: evaluate it at a
  pixel's centre, never integrate it over the pixel. See EPSF below.
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
from scipy import ndimage

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
class EPSF:
    """Effective PSFs of one photo's detector zones (the EPSF HDU), evaluated on native pixels.

    Convention (Expsupp 4.2.3, eq. 24; photutils EPSFModel; checked on real stars): each zone's
    33 x 33 array (numpy [y, x]) is sampled at 1/OVSMP native pixels, its centre index 16 is the
    source position, and the samples sum to 1. The fraction of a point source's flux recorded in a
    native pixel whose centre is offset (dx, dy) from the source is
        P(dx, dy) = OVSMPX * OVSMPY * epsf[16 + 5 dy, 16 + 5 dx]
    (cubic-spline interpolation between samples, each zone on its own; 0 beyond +-3.2 px;
    evaluate_grad also gives the spline's derivatives, for position fits). Do not integrate over the
    pixel: the pixel response is already in the ePSF.
    Zone choice: the zone whose centre (XCENTER, YCENTER: 0-based detector pixels) is nearest,
    i.e. the zone containing the position (zones tile the detector uniformly). No interpolation
    between zones: on the first photo, fitting a neighbouring zone's ePSF changes a flux by 0.7%
    (median; 2% at the 95th percentile).
    Positions passed in are 0-based pixels of *this* array; `origin` maps them to the detector.
    QR1/QR2 files before pipeline 6.5.6 had a mislabelled row order; DR1 and QR3 are unaffected.
    """

    psf: NDArray[np.float64]  # (n_zones, ny, nx), oversampled, each sums to 1
    xcenter: NDArray[np.float64]  # (n_zones,) zone centres, 0-based detector pixels
    ycenter: NDArray[np.float64]
    oversample: tuple[int, int] = (5, 5)  # (OVSMPX, OVSMPY)
    origin: tuple[int, int] = (0, 0)  # (x0, y0) of this array in the detector
    table: Any = field(default=None, repr=False)  # the full EPSF table (NSTAR, NEFF_MEAN, CWAVE, ...)
    _coef: NDArray[np.float64] | None = field(default=None, repr=False)
    _grid: tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.int64]] | None = field(default=None, repr=False)

    @classmethod
    def from_hdu(cls, hdu: Any) -> "EPSF":
        t = hdu.data
        hdr = hdu.header
        n = len(t)
        raw = np.asarray(t["EPSF"], dtype=np.float64)
        if raw.ndim == 2:  # no TDIM: 'x-fast then y'
            side = int(round(np.sqrt(raw.shape[1])))
            raw = raw.reshape(n, side, side)
        return cls(
            psf=np.ascontiguousarray(raw),
            xcenter=np.asarray(t["XCENTER"], dtype=np.float64),
            ycenter=np.asarray(t["YCENTER"], dtype=np.float64),
            oversample=(int(hdr.get("OVSMPX", 5)), int(hdr.get("OVSMPY", 5))),
            table=t,
        )

    def shifted(self, x0: int, y0: int) -> "EPSF":
        """The same ePSFs for a sub-array starting at 0-based (x0, y0) of this array."""
        return EPSF(self.psf, self.xcenter, self.ycenter, self.oversample,
                    (self.origin[0] + x0, self.origin[1] + y0), self.table, self._coef, self._grid)

    def zone(self, x: ArrayLike, y: ArrayLike) -> NDArray[np.int64]:
        """Row of the zone containing 0-based pixel (x, y) of this array."""
        if self._grid is None:
            ux, uy = np.unique(self.xcenter), np.unique(self.ycenter)
            grid = np.full((uy.size, ux.size), -1, dtype=np.int64)
            grid[np.searchsorted(uy, self.ycenter), np.searchsorted(ux, self.xcenter)] = np.arange(self.psf.shape[0])
            self._grid = (ux, uy, grid)
        ux, uy, grid = self._grid
        dx = np.asarray(x, dtype=np.float64) + self.origin[0]
        dy = np.asarray(y, dtype=np.float64) + self.origin[1]
        return grid[_nearest(uy, dy), _nearest(ux, dx)]

    def evaluate(self, dx: ArrayLike, dy: ArrayLike, zone: ArrayLike) -> NDArray[np.float64]:
        """Fraction of the flux in a native pixel whose centre is (dx, dy) px from the source."""
        return self._spline(dx, dy, zone, grad=False)[0]

    def evaluate_grad(self, dx: ArrayLike, dy: ArrayLike,
                      zone: ArrayLike) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
        """evaluate() and its derivatives (∂P/∂dx, ∂P/∂dy, per native pixel) from the same cubic spline.
        A source moving by +δ in x changes a pixel's value by −δ ∂P/∂dx (dx = pixel − source)."""
        return self._spline(dx, dy, zone, grad=True)

    def _coefficients(self) -> NDArray[np.float64]:
        """Cubic B-spline coefficients of each zone on its own (2-D, x and y only), zero-padded by
        _SPLINE_PAD samples. (Until 14 Nov one 3-D spline also ran across the zone axis: exact inside,
        but the first and last zone rows came out up to 17% of the peak wrong at the boundary.)"""
        if self._coef is None:
            p = np.pad(self.psf, ((0, 0), (_SPLINE_PAD, _SPLINE_PAD), (_SPLINE_PAD, _SPLINE_PAD)))
            c = ndimage.spline_filter1d(p, 3, axis=1, mode="grid-constant")
            self._coef = np.ascontiguousarray(ndimage.spline_filter1d(c, 3, axis=2, mode="grid-constant"))
        return self._coef

    def _spline(self, dx: ArrayLike, dy: ArrayLike, zone: ArrayLike, grad: bool) -> tuple[NDArray[np.float64], ...]:
        c = self._coefficients()
        nz, sy, sx = c.shape
        ny, nx = self.psf.shape[1:]
        ox, oy = self.oversample
        dx, dy, zone = np.broadcast_arrays(np.asarray(dx, np.float64), np.asarray(dy, np.float64), np.asarray(zone))
        u = (nx - 1) / 2.0 + _SPLINE_PAD + ox * dx
        v = (ny - 1) / 2.0 + _SPLINE_PAD + oy * dy
        iu, iv = np.floor(u), np.floor(v)
        inside = (iu >= 1) & (iu <= sx - 3) & (iv >= 1) & (iv <= sy - 3)  # 0 beyond the padded grid
        iu = np.clip(iu, 1, sx - 3).astype(np.int64)
        iv = np.clip(iv, 1, sy - 3).astype(np.int64)
        wu, du = _bspline3(u - iu, grad)
        wv, dv = _bspline3(v - iv, grad)
        base = zone.astype(np.int64) * (sy * sx) + (iv - 1) * sx + (iu - 1)
        flat = c.ravel()
        val = np.zeros(dx.shape)
        gx = np.zeros(dx.shape) if grad else None
        gy = np.zeros(dx.shape) if grad else None
        for j in range(4):
            row = np.zeros(dx.shape)
            drow = np.zeros(dx.shape) if grad else None
            for i in range(4):
                cc = flat.take(base + (j * sx + i))
                row += wu[i] * cc
                if grad:
                    drow += du[i] * cc
            val += wv[j] * row
            if grad:
                gx += wv[j] * drow
                gy += dv[j] * row
        s = float(ox * oy)
        val = np.where(inside, val * s, 0.0)
        if not grad:
            return (val,)
        return val, np.where(inside, gx * (s * ox), 0.0), np.where(inside, gy * (s * oy), 0.0)

    def stamps(self, x: ArrayLike, y: ArrayLike, half: int = 3) -> tuple[NDArray[np.float64], NDArray[np.int64], NDArray[np.int64]]:
        """Native-pixel model stamps for sources at 0-based (x, y): (N, 2h+1, 2h+1) flux fractions
        on pixels [iy-h..iy+h, ix-h..ix+h] around (ix, iy) = rounded (x, y); returns (stamps, ix, iy)."""
        xs = np.atleast_1d(np.asarray(x, dtype=np.float64))
        ys = np.atleast_1d(np.asarray(y, dtype=np.float64))
        ix, iy = np.rint(xs).astype(np.int64), np.rint(ys).astype(np.int64)
        k = np.arange(-half, half + 1)
        dx = (ix - xs)[:, None, None] + k[None, None, :]
        dy = (iy - ys)[:, None, None] + k[None, :, None]
        z = self.zone(xs, ys)[:, None, None]
        return self.evaluate(dx, dy, z), ix, iy

    def stamp(self, x: float, y: float, half: int = 3) -> NDArray[np.float64]:
        """One (2h+1, 2h+1) stamp, centred on the pixel nearest (x, y)."""
        return self.stamps([x], [y], half)[0][0]


_SPLINE_PAD = 4  # zero samples around each zone's ePSF before the spline (its edge values are ~0)


def _bspline3(t: NDArray[np.float64], grad: bool) -> tuple[tuple, tuple | None]:
    """Cubic B-spline weights (and their t-derivatives) of the 4 coefficients at floor − 1 .. floor + 2,
    for t = position − floor in [0, 1)."""
    s = 1.0 - t
    t2 = t * t
    t3 = t2 * t
    w = (s * s * s / 6.0, (3.0 * t3 - 6.0 * t2 + 4.0) / 6.0, (-3.0 * t3 + 3.0 * t2 + 3.0 * t + 1.0) / 6.0, t3 / 6.0)
    if not grad:
        return w, None
    return w, (-0.5 * s * s, 1.5 * t2 - 2.0 * t, -1.5 * t2 + t + 0.5, 0.5 * t2)


def _nearest(centres: NDArray[np.float64], v: NDArray[np.float64]) -> NDArray[np.int64]:
    """Index of the nearest of sorted centres for each value."""
    i = np.clip(np.searchsorted(centres, v), 1, max(centres.size - 1, 1))
    if centres.size == 1:
        return np.zeros(np.shape(v), dtype=np.int64)
    left = np.abs(v - centres[i - 1]) <= np.abs(centres[i] - v)
    return np.where(left, i - 1, i)


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
    zodi: NDArray[np.float32] | None = None  # ZODI model, MJy/sr (not subtracted from data), only if asked
    epsf: EPSF | None = None  # EPSF zones, only if asked
    primary_header: fits.Header = field(default_factory=fits.Header, repr=False)  # PRIMARY (VERSION)

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
    zodi: bool = False,
    epsf: bool = False,
) -> Level2:
    """Read a Level 2 file or cutout.

    src: local path, or an http(s):// URL (fsspec + range reads; needs aiohttp).
    variance: also read VARIANCE (16 MB more for a full frame).
    region: (x0, x1, y0, y1) in 0-based pixels, half-open; reads only that block through
        astropy's `.section` (only the needed bytes or compressed tiles), and shifts the WCS
        and the wavelength table to match. None reads the whole array.
    zodi: also read ZODI (the zodiacal-light model, MJy/sr; same region). None if the file has none.
    epsf: also read the EPSF table (3.9 MB) as an EPSF helper. None if the file has none.
    """
    url = _is_url(src)
    open_kwargs: dict[str, Any] = {"lazy_load_hdus": True, "memmap": False}
    if url:
        open_kwargs.update(use_fsspec=True, fsspec_kwargs=_FSSPEC_KWARGS)
    path = src if url else str(Path(src))

    with fits.open(path, **open_kwargs) as hdul:
        img = hdul["IMAGE"]
        header = img.header.copy()
        primary = hdul[0].header.copy()
        data = _read(img, region).astype(np.float32, copy=False)
        flg = _read(hdul["FLAGS"], region).astype(np.int32, copy=False)
        var = _read(hdul["VARIANCE"], region).astype(np.float32, copy=False) if variance else None
        zod = None
        if zodi:
            try:
                zod = _read(hdul["ZODI"], region).astype(np.float32, copy=False)
            except KeyError:
                zod = None
        psf: EPSF | None = None
        if epsf:
            try:
                psf = EPSF.from_hdu(hdul["EPSF"])
            except KeyError:
                psf = None
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
        if psf is not None:
            psf = psf.shifted(x0, y0)

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
        zodi=zod,
        epsf=psf,
        primary_header=primary,
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
