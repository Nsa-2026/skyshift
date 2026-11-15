"""Step `stack` (15 Nov night): detect on per-visit stacks, then fit every photo at all those positions at once.

Why: single photos (6.15″ pixels, ePSF FWHM 7.5″) miss sources below the single-photo limit, read pairs 8–15″
apart as one and merge stars on a bright star's glow into it. A visit has ~18 dithered photos per detector
(~110 over the six) of each spot; together they go ~1.5 mag deeper and sample the ePSF at 2″.

1. Read (once, cached locally): every photo of the zone's photo list that touches the zone box, only the block
   of pixels over the zone's grid (+ STACK_PAD_PX): IMAGE, FLAGS, VARIANCE and the EPSF table (once per detector
   and ePSF calibration file). Bad pixels: core's mask bits (contracts/flags.md). Background: core.detect's sep
   map (mesh STACK_BKG_BOX). Noise per pixel: max(√VARIANCE, background rms), as dots.
2. Stacks: each photo's background-subtracted pixels are splatted (bilinear) onto one tangent-plane grid of
   STACK_PIX_ARCSEC per zone with inverse-variance weights, one stack per visit × detector. Each stack's
   matched-filter S/N map uses its detector's ePSF (the zones' mean, evaluated at the grid offsets; the noise
   correlation of the resampling is taken out by normalising each map on its own lower half). Per visit an
   all-wavelength map (the detectors' S/N maps summed / √n: each detector scaled by its own noise), and one
   map over all visits. Peaks ≥ STACK_DETECT_SNR in any map, merged within STACK_MERGE_ARCSEC, are sources.
3. Joint ePSF fit in each photo (the photo's own ePSF evaluated at pixel centres, never integrated): all sources
   at once (sparse normal equations, conjugate gradients), fluxes with positions fixed (Gaia DR3 moved to the
   photo's MJD-AVG with pm and parallax where a source matches one, else the stack position), then a linearised
   free-position fit (flux, Δx, Δy) for sources with S/N ≥ STACK_FREE_MIN_SNR there, with the fixed position as a
   Gaussian prior (STACK_PRIOR_*). The first fit refines the stack positions (weighted mean of the photos'
   offsets) and its residual (photo − every source's model) is stacked again: peaks there (noise raised by the
   model's own misfit, STACK_RESID_PSF_ERR) are the neighbours of bright stars and the second star of a pair.
   The last fit gives the rows of stack_forced.parquet.
4. Sources are kept with forced S/N ≥ STACK_KEEP_SNR in at least one stack; matched to Gaia DR3, CatWISE2020 and
   2MASS (refcat/<zone>.parquet) and to the single-photo sources (results/<zone>/sources.parquet).

Writes results/<zone>/stack_sources.parquet, stack_forced.parquet (contracts/dots.md) and stack.json (what the
stacks found that the single-photo sources didn't). Never touches dots/ or any other table.
Run: `uv run skyshift-pipeline stack --zone t_barnard [--root …] [--workers N] [--cache <dir>]`.
"""

from __future__ import annotations

import json
import math
import multiprocessing as mp
import os
import shutil
import tempfile
import time
import uuid
import warnings
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass

import numpy as np
import pandas as pd
import sep
from astropy.io import fits
from astropy.wcs import WCS, FITSFixedWarning
from scipy import ndimage, sparse
from scipy.interpolate import RectBivariateSpline
from scipy.signal import fftconvolve
from scipy.sparse.linalg import LinearOperator, cg, spsolve
from scipy.spatial import cKDTree
from skyshift_core.astrometry import propagate
from skyshift_core.detect import background, build_mask
from skyshift_core.level2 import _FSSPEC_KWARGS, EPSF
from skyshift_core.photometry import ujy_per_mjysr

from . import config as C
from . import log
from .dots import bad_finast, epsf_calib_id
from .store import Store, photolist_rel, results_rel

L = log.get("stack")

GAIA_EPOCH_MJD = 57388.5
SOURCES_REL = "stack_sources.parquet"
FORCED_REL = "stack_forced.parquet"
REPORT_REL = "stack.json"


# --- the zone's grid -------------------------------------------------------------------------------------------


def _tan(ra, dec, ra0: float, dec0: float):
    """Gnomonic projection: (xi, eta) in degrees, xi towards the east."""
    a, d = np.radians(np.asarray(ra, float)), np.radians(np.asarray(dec, float))
    a0, d0 = math.radians(ra0), math.radians(dec0)
    cosc = math.sin(d0) * np.sin(d) + math.cos(d0) * np.cos(d) * np.cos(a - a0)
    xi = np.cos(d) * np.sin(a - a0) / cosc
    eta = (math.cos(d0) * np.sin(d) - math.sin(d0) * np.cos(d) * np.cos(a - a0)) / cosc
    return np.degrees(xi), np.degrees(eta)


def _untan(xi, eta, ra0: float, dec0: float):
    x, y = np.radians(np.asarray(xi, float)), np.radians(np.asarray(eta, float))
    a0, d0 = math.radians(ra0), math.radians(dec0)
    rho = np.hypot(x, y)
    c = np.arctan(rho)
    with np.errstate(invalid="ignore", divide="ignore"):
        dec = np.arcsin(np.cos(c) * math.sin(d0) + np.where(rho > 0, y * np.sin(c) * math.cos(d0) / rho, 0.0))
    ra = a0 + np.arctan2(x * np.sin(c), rho * math.cos(d0) * np.cos(c) - y * math.sin(d0) * np.sin(c))
    return np.degrees(ra) % 360.0, np.degrees(dec)


@dataclass(frozen=True)
class Grid:
    """Tangent-plane grid of a zone: x grows west (east is left, as on the sky), y north; 0-based pixel centres."""
    ra0: float
    dec0: float
    pix: float   # arcsec
    xoff: float
    yoff: float
    nx: int
    ny: int

    def xy(self, ra, dec):
        xi, eta = _tan(ra, dec, self.ra0, self.dec0)
        return -xi * 3600.0 / self.pix - self.xoff, eta * 3600.0 / self.pix - self.yoff

    def radec(self, x, y):
        xi = -(np.asarray(x, float) + self.xoff) * self.pix / 3600.0
        eta = (np.asarray(y, float) + self.yoff) * self.pix / 3600.0
        return _untan(xi, eta, self.ra0, self.dec0)


def zone_grid(zone: C.Zone, pix: float = C.STACK_PIX_ARCSEC, margin_arcsec: float = C.STACK_MARGIN_ARCSEC) -> Grid:
    ra0, dec0 = zone.centre
    t = np.linspace(0.0, 1.0, 64)
    ra = np.r_[zone.ra_min + t * (zone.ra_max - zone.ra_min), np.full(64, zone.ra_max),
               zone.ra_max - t * (zone.ra_max - zone.ra_min), np.full(64, zone.ra_min)]
    dec = np.r_[np.full(64, zone.dec_min), zone.dec_min + t * (zone.dec_max - zone.dec_min),
                np.full(64, zone.dec_max), zone.dec_max - t * (zone.dec_max - zone.dec_min)]
    xi, eta = _tan(ra, dec, ra0, dec0)
    gx, gy = -xi * 3600.0 / pix, eta * 3600.0 / pix
    m = margin_arcsec / pix
    xoff, yoff = math.floor(gx.min() - m), math.floor(gy.min() - m)
    nx, ny = int(math.ceil(gx.max() + m - xoff)) + 1, int(math.ceil(gy.max() + m - yoff)) + 1
    return Grid(ra0, dec0, pix, float(xoff), float(yoff), nx, ny)


def _celestial_wcs(header) -> WCS:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FITSFixedWarning)
        return WCS(header).celestial


# --- 1. read the photos once ----------------------------------------------------------------------------------


def _region(wcs: WCS, grid: Grid, nx_det: int, ny_det: int, pad: int) -> tuple[int, int, int, int] | None:
    """The photo's pixel block (x0, x1, y0, y1) over the grid (+ pad), or None when they don't meet."""
    step = 25
    gx = np.unique(np.r_[np.arange(0, grid.nx, step), grid.nx - 1])
    gy = np.unique(np.r_[np.arange(0, grid.ny, step), grid.ny - 1])
    X, Y = np.meshgrid(gx, gy)
    ra, dec = grid.radec(X.ravel(), Y.ravel())
    x, y = wcs.world_to_pixel_values(ra, dec)
    m = 2.0 * step * grid.pix / C.STACK_NATIVE_PIX_ARCSEC  # lattice spacing in native px
    ok = np.isfinite(x) & np.isfinite(y) & (x > -m) & (x < nx_det + m) & (y > -m) & (y < ny_det + m)
    if not ok.any():
        return None
    x0 = max(0, int(math.floor(x[ok].min())) - pad)
    x1 = min(nx_det, int(math.ceil(x[ok].max())) + pad + 1)
    y0 = max(0, int(math.floor(y[ok].min())) - pad)
    y1 = min(ny_det, int(math.ceil(y[ok].max())) + pad + 1)
    if x1 - x0 < 2 * C.STACK_BKG_BOX or y1 - y0 < 2 * C.STACK_BKG_BOX:
        return None
    return x0, x1, y0, y1


def _atomic_save(path: str, **arrays) -> None:
    tmp = f"{path}.tmp-{uuid.uuid4().hex[:8]}.npz"
    np.savez(tmp, **arrays)
    os.replace(tmp, path)


def epsf_path(cache: str, key: str) -> str:
    return os.path.join(cache, f"epsf_{key}.npz")


def cut_path(cache: str, obs_id: str, det: int) -> str:
    return os.path.join(cache, f"{obs_id}D{int(det)}.npz")


def read_cutout(url: str, obs_id: str, det: int, visit: int, session: int, grid: Grid, cache: str) -> dict:
    """Read the photo's block over the grid, subtract the background, cache it (sub, noise, flags, header).
    Returns its metadata (status done / skipped / outside / finast / failed)."""
    out = {"obs_id": obs_id, "detector": int(det), "visit": int(visit), "session": int(session), "status": "",
           "read_s": 0.0, "error": ""}
    os.makedirs(cache, exist_ok=True)
    path = cut_path(cache, obs_id, det)
    if os.path.exists(path):
        with np.load(path) as z:
            out.update(json.loads(str(z["meta"])))
        out["status"] = "cached"
        return out
    t0 = time.time()
    last = None
    for attempt in range(1, C.NET_RETRIES + 1):
        try:
            with fits.open(url, use_fsspec=True, fsspec_kwargs=_FSSPEC_KWARGS, lazy_load_hdus=True,
                           memmap=False) as hdul:
                img = hdul["IMAGE"]
                hdr = img.header.copy()
                if bad_finast(hdr.get("FINAST")):
                    out["status"] = "finast"
                    return out
                wcs = _celestial_wcs(hdr)
                reg = _region(wcs, grid, int(hdr["NAXIS1"]), int(hdr["NAXIS2"]), C.STACK_PAD_PX)
                if reg is None:
                    out["status"] = "outside"
                    return out
                x0, x1, y0, y1 = reg
                data = np.asarray(img.section[y0:y1, x0:x1], dtype=np.float32)
                flags = np.asarray(hdul["FLAGS"].section[y0:y1, x0:x1]).astype(np.int32)
                var = np.asarray(hdul["VARIANCE"].section[y0:y1, x0:x1], dtype=np.float32)
                cid = epsf_calib_id(hdr) or f"photo_{obs_id}"
                ekey = f"{int(hdr['DETECTOR'])}_{cid}"
                if not os.path.exists(epsf_path(cache, ekey)):
                    e = EPSF.from_hdu(hdul["EPSF"])
                    _atomic_save(epsf_path(cache, ekey), psf=e.psf, xcenter=e.xcenter, ycenter=e.ycenter,
                                 oversample=np.asarray(e.oversample))
            break
        except Exception as e:  # noqa: BLE001 - network or decode errors: retry, then skip
            last = e
            time.sleep(3 * attempt)
    else:
        out["status"], out["error"] = "failed", f"{type(last).__name__}: {last}"[:300]
        return out
    m = build_mask(data, flags)
    d = np.where(m, 0.0, data.astype(np.float64))
    b = background(d, m, box=C.STACK_BKG_BOX)
    sub = (d - b.level).astype(np.float32)
    sub[m] = 0.0
    v = var.astype(np.float64)
    sd = np.sqrt(np.where(np.isfinite(v) & (v > 0), v, np.nan))
    noise = sd if C.PSF_NOISE == "variance" else np.fmax(sd, b.rms)
    noise = np.where(m | ~np.isfinite(noise) | (noise <= 0), np.inf, noise).astype(np.float32)
    wcut = wcs[y0:y1, x0:x1]
    meta = {"obs_id": obs_id, "detector": int(hdr["DETECTOR"]), "visit": int(visit), "session": int(session),
            "mjd": float(hdr["MJD-AVG"]), "x0": x0, "y0": y0, "shape": [int(y1 - y0), int(x1 - x0)],
            "epsf": ekey, "conv": float(ujy_per_mjysr(wcut)), "bad_frac": float(m.mean())}
    _atomic_save(path, sub=sub, noise=noise, flags=flags, header=np.asarray(hdr.tostring()),
                 meta=np.asarray(json.dumps(meta)))
    out.update(meta)
    out["status"], out["read_s"] = "done", round(time.time() - t0, 2)
    return out


# --- per photo: load, grid coordinates, splat, joint fit -----------------------------------------------------


_EPSF: dict[str, EPSF] = {}


def load_epsf(cache: str, key: str) -> EPSF:
    if key not in _EPSF:
        with np.load(epsf_path(cache, key)) as z:
            _EPSF[key] = EPSF(psf=z["psf"], xcenter=z["xcenter"], ycenter=z["ycenter"],
                              oversample=tuple(int(v) for v in z["oversample"]))
    return _EPSF[key]


@dataclass
class Cut:
    meta: dict
    sub: np.ndarray
    noise: np.ndarray
    flags: np.ndarray
    wcs: WCS
    epsf: EPSF

    @property
    def w(self) -> np.ndarray:
        return 1.0 / np.square(self.noise.astype(np.float64))  # 0 where bad (noise inf)


def load_cut(cache: str, obs_id: str, det: int) -> Cut:
    with np.load(cut_path(cache, obs_id, det)) as z:
        meta = json.loads(str(z["meta"]))
        hdr = fits.Header.fromstring(str(z["header"]))
        sub, noise, flags = z["sub"], z["noise"], z["flags"]
    (h, w), x0, y0 = meta["shape"], meta["x0"], meta["y0"]
    wcs = _celestial_wcs(hdr)[y0:y0 + h, x0:x0 + w]
    return Cut(meta, sub, noise, flags, wcs, load_epsf(cache, meta["epsf"]).shifted(x0, y0))


def grid_coords(cut: Cut, grid: Grid, step: int = 16) -> tuple[np.ndarray, np.ndarray]:
    """Grid (x, y) of every pixel centre of the block (WCS on a coarse lattice, splined: the WCS is smooth)."""
    h, w = cut.sub.shape
    xs = np.unique(np.r_[np.arange(0, w, step), w - 1])
    ys = np.unique(np.r_[np.arange(0, h, step), h - 1])
    X, Y = np.meshgrid(xs, ys)
    ra, dec = cut.wcs.pixel_to_world_values(X, Y)
    gx, gy = grid.xy(ra, dec)
    kx, ky = min(3, len(xs) - 1), min(3, len(ys) - 1)
    u = RectBivariateSpline(ys, xs, gx, kx=ky, ky=kx)(np.arange(h), np.arange(w))
    v = RectBivariateSpline(ys, xs, gy, kx=ky, ky=kx)(np.arange(h), np.arange(w))
    return u, v


@dataclass
class Acc:
    """One stack's sums on a block of the grid: Σ w·value, Σ w, photos covering."""
    s: np.ndarray
    w: np.ndarray
    n: np.ndarray

    @classmethod
    def zeros(cls, grid: Grid) -> "Acc":
        return cls(np.zeros((grid.ny, grid.nx)), np.zeros((grid.ny, grid.nx)), np.zeros((grid.ny, grid.nx), np.int32))

    def add(self, other: "Acc") -> None:
        self.s += other.s
        self.w += other.w
        self.n += other.n


def splat(acc: Acc, u: np.ndarray, v: np.ndarray, val: np.ndarray, w: np.ndarray) -> None:
    """Add one photo: each pixel's value to the 4 grid pixels around its centre, bilinear weights × w."""
    ny, nx = acc.s.shape
    ok = (w > 0) & np.isfinite(val) & (u > -1) & (u < nx) & (v > -1) & (v < ny)
    if not ok.any():
        return
    u, v, val, w = u[ok], v[ok], val[ok].astype(np.float64), w[ok]
    iu, iv = np.floor(u).astype(np.int64), np.floor(v).astype(np.int64)
    fu, fv = u - iu, v - iv
    hit = np.zeros(ny * nx, bool)
    for du, dv, k in ((0, 0, (1 - fu) * (1 - fv)), (1, 0, fu * (1 - fv)), (0, 1, (1 - fu) * fv), (1, 1, fu * fv)):
        jx, jy = iu + du, iv + dv
        inb = (jx >= 0) & (jx < nx) & (jy >= 0) & (jy < ny)
        idx = jy[inb] * nx + jx[inb]
        ww = (k * w)[inb]
        acc.s.ravel()[:] += np.bincount(idx, weights=ww * val[inb], minlength=nx * ny)
        acc.w.ravel()[:] += np.bincount(idx, weights=ww, minlength=nx * ny)
        hit[idx] = True
    acc.n.ravel()[hit] += 1


@dataclass
class Sources:
    """What every photo's fit needs: positions (stack epoch), Gaia for its own epoch, the prior σ, a flux guess."""
    ra: np.ndarray
    dec: np.ndarray
    g_ra: np.ndarray      # Gaia DR3 (epoch 2016.0) of the matched star, NaN = none
    g_dec: np.ndarray
    g_pmra: np.ndarray
    g_pmdec: np.ndarray
    g_plx: np.ndarray

    @classmethod
    def plain(cls, ra, dec) -> "Sources":
        n = len(ra)
        nan = np.full(n, np.nan)
        return cls(np.asarray(ra, float), np.asarray(dec, float), nan, nan.copy(), nan.copy(), nan.copy(), nan.copy())

    def at(self, mjd: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(ra, dec, is_gaia) at this time: Gaia moved with pm and parallax, else the stack position."""
        g = np.isfinite(self.g_ra)
        ra, dec = self.ra.copy(), self.dec.copy()
        if g.any():
            r, d = propagate(self.g_ra[g], self.g_dec[g], self.g_pmra[g], self.g_pmdec[g], np.full(g.sum(), mjd),
                             parallax=np.nan_to_num(self.g_plx[g]))
            ra[g], dec[g] = r, d
        return ra, dec, g


def _solve(N: sparse.spmatrix, rhs: np.ndarray) -> np.ndarray:
    """The normal equations N x = rhs (sparse, symmetric positive): direct (SuperLU), else conjugate gradients.
    Unknowns without data (zero diagonal) come out 0."""
    n = rhs.size
    if n == 0:
        return rhs.copy()
    diag = N.diagonal()
    empty = diag <= 0
    # 1 on empty unknowns; a relative ridge keeps two sources at one spot (identical columns) solvable
    N = N + sparse.diags(np.where(empty, 1.0, C.STACK_RIDGE_REL * diag))
    d = np.where(empty, 1.0, diag)
    try:
        x = spsolve(N.tocsc(), rhs)
        if np.all(np.isfinite(x)):
            return x
    except Exception:  # noqa: BLE001 - singular or too large: iterate instead
        pass
    M = LinearOperator((n, n), matvec=lambda v: v / d, dtype=np.float64)
    x, _ = cg(N, rhs, x0=rhs / d, rtol=C.STACK_CG_RTOL, maxiter=C.STACK_CG_ITER, M=M)
    return x


def fit_photo(cut: Cut, src: Sources, free: bool = True, half: int = C.STACK_STAMP_HALF) -> dict:
    """Joint ePSF fit of every source in this photo. Fixed positions → flux (MJy/sr·px summed, like the ePSF's
    normalisation); then, where the fixed S/N ≥ STACK_FREE_MIN_SNR, flux + Δx + Δy with a Gaussian position prior.
    Large blocks are fitted in tiles of STACK_FIT_TILE_PX (sources within STACK_FIT_TILE_MARGIN_PX outside a tile are
    fitted with it as neighbours; each source's result comes from the tile holding it).
    Returns per-source arrays (idx = rows of src inside the block) and the model image of the last fit."""
    h, wd = cut.sub.shape
    mjd = float(cut.meta["mjd"])
    ra, dec, is_g = src.at(mjd)
    x, y = cut.wcs.world_to_pixel_values(ra, dec)
    x, y = np.asarray(x, float), np.asarray(y, float)
    inside = np.isfinite(x) & np.isfinite(y) & (x > -half) & (x < wd - 1 + half) & (y > -half) & (y < h - 1 + half)
    idx = np.nonzero(inside)[0]
    x, y, is_g = x[idx], y[idx], is_g[idx]
    n = idx.size
    out = {"idx": idx, "x": x, "y": y, "gaia_pos": is_g, "model": np.zeros((h, wd))}
    if n == 0:
        return out
    T, M = C.STACK_FIT_TILE_PX, C.STACK_FIT_TILE_MARGIN_PX
    w = cut.w
    if h <= T + 2 * M and wd <= T + 2 * M:
        r = _fit_block(cut.sub, w, cut.flags, cut.epsf, x, y, is_g, np.ones(n, bool), free, half)
        out.update(r)
        return out
    cols = {}
    xr, yr = np.rint(x), np.rint(y)
    for ty0 in range(0, h, T):
        for tx0 in range(0, wd, T):
            ty1, tx1 = min(h, ty0 + T), min(wd, tx0 + T)
            own = (xr >= (tx0 if tx0 else -half)) & (xr < (tx1 if tx1 < wd else wd + half)) \
                & (yr >= (ty0 if ty0 else -half)) & (yr < (ty1 if ty1 < h else h + half))
            if not own.any():
                continue
            fit = (x >= tx0 - M) & (x < tx1 + M) & (y >= ty0 - M) & (y < ty1 + M)
            bx0, by0 = max(0, tx0 - M - half - 1), max(0, ty0 - M - half - 1)
            bx1, by1 = min(wd, tx1 + M + half + 1), min(h, ty1 + M + half + 1)
            j = np.nonzero(fit | own)[0]
            sl = (slice(by0, by1), slice(bx0, bx1))
            r = _fit_block(cut.sub[sl], w[sl], cut.flags[sl], cut.epsf.shifted(bx0, by0), x[j] - bx0, y[j] - by0,
                           is_g[j], own[j], free, half)
            out["model"][sl] += r.pop("model")
            keep = own[j]
            for k, v in r.items():
                if k not in cols:
                    cols[k] = np.full(n, np.nan) if v.dtype.kind == "f" else np.zeros(n, v.dtype)
                cols[k][j[keep]] = v[keep]
    out.update(cols)
    return out


def _fit_block(sub: np.ndarray, wmap: np.ndarray, flags: np.ndarray, epsf: EPSF, x: np.ndarray, y: np.ndarray,
               is_g: np.ndarray, core_src: np.ndarray, free: bool, half: int) -> dict:
    """fit_photo on one block (positions in its pixels); the model image holds only the core_src sources."""
    h, wd = sub.shape
    n = x.size
    res: dict = {}
    ix, iy = np.rint(x).astype(np.int64), np.rint(y).astype(np.int64)
    P, Gx, Gy = epsf.stamps_at(x, y, ix, iy, half, grad=True)
    k = np.arange(-half, half + 1)
    PX = np.broadcast_to(ix[:, None, None] + k[None, None, :], P.shape)
    PY = np.broadcast_to(iy[:, None, None] + k[None, :, None], P.shape)
    inb = (PX >= 0) & (PX < wd) & (PY >= 0) & (PY < h)
    flat = np.where(inb, PY * wd + PX, 0)
    wpix = np.ascontiguousarray(wmap).ravel()
    wv = np.where(inb, wpix[flat], 0.0)
    sw = np.sqrt(wv)
    col = np.broadcast_to(np.arange(n)[:, None, None], P.shape)
    use = wv > 0
    rows, cols = flat[use], col[use]
    npix = h * wd
    A = sparse.csr_matrix(((P * sw)[use], (rows, cols)), shape=(npix, n))
    At = A.T.tocsr()
    subv = np.ascontiguousarray(sub, dtype=np.float64).ravel()
    b = subv * np.sqrt(wpix)
    b[~np.isfinite(b)] = 0.0
    dP = (P * P * wv).sum(axis=(1, 2))
    f = _solve((At @ A).tocsr(), At @ b)
    with np.errstate(divide="ignore", invalid="ignore"):
        err = np.where(dP > 0, 1.0 / np.sqrt(dP), np.inf)
    f = np.where(dP > 0, f, np.nan)
    model = np.bincount(flat[use], weights=(P * np.nan_to_num(f)[:, None, None])[use], minlength=npix)
    # χ² of the fixed fit in the central 5 × 5 (the source's own pixels)
    r = subv - model
    core = use & (np.abs(k)[None, :, None] <= 2) & (np.abs(k)[None, None, :] <= 2)
    chi = np.where(core, wv * r[flat] ** 2, 0.0).sum(axis=(1, 2))
    ncore = core.sum(axis=(1, 2))
    fl3 = np.zeros(n, np.int32)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            jx, jy = np.clip(ix + dx, 0, wd - 1), np.clip(iy + dy, 0, h - 1)
            fl3 |= flags[jy, jx]
    cs = core_src[:, None, None] & use
    res.update(flux=f, err=err, chi2=np.where(ncore > 1, chi / np.maximum(ncore - 1, 1), np.nan),
               npix=ncore.astype(np.int16), flags=fl3,
               model=np.bincount(flat[cs], weights=(P * np.nan_to_num(f)[:, None, None])[cs],
                                 minlength=npix).reshape(h, wd))
    if not free:
        return res
    snr = f / err
    fr = np.nonzero(np.isfinite(snr) & (snr >= C.STACK_FREE_MIN_SNR))[0]
    res.update(dx=np.full(n, np.nan), dy=np.full(n, np.nan), sx=np.full(n, np.nan), sy=np.full(n, np.nan),
               free_ok=np.zeros(n, bool))
    if fr.size == 0:
        return res
    m = fr.size
    usef = use[fr]
    colf = np.broadcast_to(np.arange(m)[:, None, None], usef.shape)[usef]
    Ax = sparse.csr_matrix(((-Gx[fr] * sw[fr])[usef], (flat[fr][usef], colf)), shape=(npix, m))
    Ay = sparse.csr_matrix(((-Gy[fr] * sw[fr])[usef], (flat[fr][usef], colf)), shape=(npix, m))
    B = sparse.hstack([A, Ax, Ay], format="csr")
    Bt = B.T.tocsr()
    prior_px = np.where(is_g[fr], C.STACK_PRIOR_GAIA_ARCSEC, C.STACK_PRIOR_STACK_ARCSEC) / C.STACK_NATIVE_PIX_ARCSEC
    lam = 1.0 / np.square(f[fr] * prior_px)
    ridge = np.r_[np.zeros(n), lam, lam]
    dxx = (Gx[fr] ** 2 * wv[fr]).sum(axis=(1, 2)) + lam
    dyy = (Gy[fr] ** 2 * wv[fr]).sum(axis=(1, 2)) + lam
    sol = _solve((Bt @ B + sparse.diags(ridge)).tocsr(), Bt @ b)
    ff, a, c = sol[:n], sol[n:n + m], sol[n + m:]
    # the source's own 3 × 3 block for the errors (neighbours' covariance left out)
    pxy = -(P[fr] * Gx[fr] * wv[fr]).sum(axis=(1, 2))
    pyy = -(P[fr] * Gy[fr] * wv[fr]).sum(axis=(1, 2))
    xy = (Gx[fr] * Gy[fr] * wv[fr]).sum(axis=(1, 2))
    blk = np.stack([np.stack([dP[fr], pxy, pyy], -1), np.stack([pxy, dxx, xy], -1), np.stack([pyy, xy, dyy], -1)], -2)
    try:
        cov = np.linalg.inv(blk)
    except np.linalg.LinAlgError:
        cov = np.full_like(blk, np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        ddx, ddy = a / ff[fr], c / ff[fr]
        sx, sy = np.sqrt(cov[:, 1, 1]) / np.abs(ff[fr]), np.sqrt(cov[:, 2, 2]) / np.abs(ff[fr])
    ok = (ff[fr] > 0) & (np.hypot(ddx, ddy) <= C.STACK_FREE_MAX_SHIFT_PX) & np.isfinite(sx) & np.isfinite(sy)
    res["dx"][fr], res["dy"][fr], res["sx"][fr], res["sy"][fr], res["free_ok"][fr] = ddx, ddy, sx, sy, ok
    amp = np.zeros((n, 3))
    amp[:, 0] = np.nan_to_num(ff)
    amp[fr, 1], amp[fr, 2] = a, c
    stamp = P * amp[:, 0, None, None] - Gx * amp[:, 1, None, None] - Gy * amp[:, 2, None, None]
    res["model"] = np.bincount(flat[cs], weights=stamp[cs], minlength=npix).reshape(h, wd)
    return res


def _task(cache: str, grid: Grid, photos: list[tuple[str, int]], src: Sources | None, mode: str) -> dict:
    """One group of photos of one stack. mode 'stack': splat the photos; 'refine': fit, splat the residual;
    'final': fit only. Returns the stack's sums (or None) and each photo's fit."""
    acc = Acc.zeros(grid) if mode in ("stack", "refine") else None
    fits_out = []
    t_fit = t_splat = 0.0
    for obs_id, det in photos:
        cut = load_cut(cache, obs_id, det)
        t0 = time.time()
        if mode == "stack":
            u, v = grid_coords(cut, grid)
            splat(acc, u, v, cut.sub, cut.w)
            t_splat += time.time() - t0
            continue
        r = fit_photo(cut, src, free=True)
        t_fit += time.time() - t0
        t0 = time.time()
        model = r.pop("model")
        if mode == "refine":
            u, v = grid_coords(cut, grid)
            wr = 1.0 / (np.square(cut.noise.astype(np.float64)) + np.square(C.STACK_RESID_PSF_ERR * model))
            splat(acc, u, v, cut.sub - model, np.where(np.isfinite(cut.noise), wr, 0.0))
        if r["idx"].size:
            ra, dec = cut.wcs.pixel_to_world_values(r["x"], r["y"])
            r["ra"], r["dec"] = np.asarray(ra), np.asarray(dec)
            if "dx" in r:
                rf, df = cut.wcs.pixel_to_world_values(r["x"] + np.nan_to_num(r["dx"]), r["y"] + np.nan_to_num(r["dy"]))
                r["ra_free"], r["dec_free"] = np.asarray(rf), np.asarray(df)
        r["meta"] = cut.meta
        fits_out.append(r)
        t_splat += time.time() - t0
    return {"acc": acc, "fits": fits_out, "fit_s": t_fit, "splat_s": t_splat, "n": len(photos)}


# --- 2. stacks → S/N maps → peaks -----------------------------------------------------------------------------


def psf_kernel(epsf: EPSF, pix: float = C.STACK_PIX_ARCSEC) -> np.ndarray:
    """The detector's ePSF (mean of its zones) on the grid: K[j, i] = flux fraction per native pixel at grid offset
    (i, j) from the source (x west, as the grid). A stack value ≈ flux × K."""
    r = int(math.ceil(3.3 * C.STACK_NATIVE_PIX_ARCSEC / pix))
    o = np.arange(-r, r + 1) * pix / C.STACK_NATIVE_PIX_ARCSEC
    DX, DY = np.meshgrid(-o, o)  # grid x grows west = −detector x roughly; symmetric enough for a matched filter
    nz = epsf.psf.shape[0]
    K = np.zeros_like(DX)
    for z in range(nz):
        K += epsf.evaluate(DX, DY, np.full(DX.shape, z))
    return K / nz


@dataclass
class Map:
    name: str
    snr: np.ndarray
    flux: np.ndarray   # MJy/sr·px (the ePSF's normalisation): × conv for µJy
    ok: np.ndarray     # searched: enough photos
    sigma: float       # the raw map's noise (the resampling's correlation)


def snr_map(name: str, acc: Acc, K: np.ndarray) -> Map:
    with np.errstate(divide="ignore", invalid="ignore"):
        D = np.where(acc.w > 0, acc.s / acc.w, 0.0)
    bad = acc.w <= 0
    if (~bad).sum() > 100:
        try:
            bk = sep.Background(np.ascontiguousarray(D), mask=np.ascontiguousarray(bad), bw=C.STACK_GRID_BKG_BOX,
                                bh=C.STACK_GRID_BKG_BOX, fw=3, fh=3)
            D = D - bk.back()
        except Exception:  # noqa: BLE001 - a tiny or empty stack: no background
            pass
    D[bad] = 0.0
    num = fftconvolve(acc.w * D, K[::-1, ::-1], mode="same")
    den = fftconvolve(acc.w, (K * K)[::-1, ::-1], mode="same")
    ok = (acc.n >= C.STACK_MIN_PHOTOS) & (den > 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        raw = np.where(ok, num / np.sqrt(den), 0.0)
        flux = np.where(ok, num / den, np.nan)
    sigma = 1.0
    if ok.sum() > 100:
        v = raw[ok]
        med = float(np.median(v))
        sigma = max(med - float(np.percentile(v, 15.865)), 1e-12)
    return Map(name, raw / sigma, flux, ok, sigma)


def wing_profile(resid: Map, x: np.ndarray, y: np.ndarray, F: np.ndarray, Ferr: np.ndarray,
                 rmax_px: int) -> np.ndarray:
    """How much of a source's flux its residual leaves at each distance (grid px 0..rmax): per bright source the
    ring's 90th percentile of |residual matched-filter flux| less what the noise alone gives (1.64 σ), / F; the
    STACK_WING_PCT percentile over the sources; made non-increasing outwards. The ePSF stops at ±3.2 native px
    (20″): beyond it, and in a misfit core, the residual is the source's own."""
    E = np.zeros(rmax_px + 1)
    good = np.isfinite(F) & np.isfinite(Ferr) & (Ferr > 0) & (F / Ferr >= C.STACK_WING_MIN_SNR)
    idx = np.nonzero(good)[0]
    idx = idx[np.argsort(-F[idx])][:C.STACK_WING_MAX_STARS]
    if idx.size < 3:
        return np.full(rmax_px + 1, C.STACK_RESID_PSF_ERR)
    ny, nx = resid.flux.shape
    with np.errstate(divide="ignore", invalid="ignore"):
        err = np.abs(resid.flux / resid.snr)
    k = np.arange(-rmax_px, rmax_px + 1)
    R = np.rint(np.hypot(*np.meshgrid(k, k))).astype(int)
    rings = [R == r for r in range(rmax_px + 1)]
    vals = [[] for _ in range(rmax_px + 1)]
    for i in idx:
        ix, iy = int(round(x[i])), int(round(y[i]))
        if ix - rmax_px < 0 or iy - rmax_px < 0 or ix + rmax_px >= nx or iy + rmax_px >= ny:
            continue
        sl = (slice(iy - rmax_px, iy + rmax_px + 1), slice(ix - rmax_px, ix + rmax_px + 1))
        a, e = np.abs(resid.flux[sl]), err[sl]
        for r in range(rmax_px + 1):
            v, ev = a[rings[r]], e[rings[r]]
            ok = np.isfinite(v) & np.isfinite(ev)
            if ok.sum() >= 1:
                vals[r].append(max(0.0, float(np.percentile(v[ok], 90)) - 1.64 * float(np.median(ev[ok]))) / F[i])
    for r in range(rmax_px + 1):
        v = np.asarray(vals[r], float)
        E[r] = np.percentile(v, C.STACK_WING_PCT) if v.size >= 3 else C.STACK_RESID_PSF_ERR
    return np.maximum.accumulate(E[::-1])[::-1]


def inflate(resid: Map, x: np.ndarray, y: np.ndarray, F: np.ndarray, Ferr: np.ndarray) -> Map:
    """The residual map's S/N with the sources' own leftovers as noise: flux / √(err² + (Σ_j F_j E(r_j))²)."""
    rmax = int(round(C.STACK_WING_RADIUS_ARCSEC / C.STACK_PIX_ARCSEC))
    E = wing_profile(resid, x, y, F, Ferr, rmax)
    ny, nx = resid.flux.shape
    img = np.zeros((ny, nx))
    ok = np.isfinite(F) & (F > 0) & np.isfinite(x) & np.isfinite(y)
    ix, iy = np.rint(x[ok]).astype(int), np.rint(y[ok]).astype(int)
    inb = (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny)
    np.add.at(img, (iy[inb], ix[inb]), F[ok][inb])
    k = np.arange(-rmax, rmax + 1)
    KE = E[np.minimum(np.rint(np.hypot(*np.meshgrid(k, k))).astype(int), rmax)]
    KE[np.hypot(*np.meshgrid(k, k)) > rmax] = 0.0
    M = np.clip(fftconvolve(img, KE, mode="same"), 0, None)
    with np.errstate(divide="ignore", invalid="ignore"):
        err = np.where(resid.snr != 0, np.abs(resid.flux / resid.snr), np.nan)
        snr = np.where(resid.ok, resid.flux / np.sqrt(err ** 2 + M ** 2), 0.0)
    return Map(resid.name, np.nan_to_num(snr), resid.flux, resid.ok, resid.sigma)


def combine(name: str, maps: list[Map]) -> Map | None:
    maps = [m for m in maps if m is not None]
    if not maps:
        return None
    s = sum(np.where(m.ok, m.snr, 0.0) for m in maps)
    n = sum(m.ok.astype(np.int32) for m in maps)
    with np.errstate(divide="ignore", invalid="ignore"):
        snr = np.where(n > 0, s / np.sqrt(n), 0.0)
    return Map(name, snr, np.full(snr.shape, np.nan), n > 0, 1.0)


def peaks(m: Map, thresh: float = C.STACK_DETECT_SNR, edge: int = C.STACK_EDGE_PX) -> pd.DataFrame:
    s = m.snr
    mx = ndimage.maximum_filter(np.where(m.ok, s, -np.inf), size=3, mode="constant", cval=-np.inf)
    pk = (s == mx) & (s >= thresh) & m.ok
    pk[:edge, :] = pk[-edge:, :] = False
    pk[:, :edge] = pk[:, -edge:] = False
    iy, ix = np.nonzero(pk)
    if ix.size == 0:
        return pd.DataFrame({"x": [], "y": [], "snr": [], "map": []})
    l, c, r = s[iy, ix - 1], s[iy, ix], s[iy, ix + 1]
    dn, up = s[iy - 1, ix], s[iy + 1, ix]
    with np.errstate(divide="ignore", invalid="ignore"):
        ddx = np.clip(np.nan_to_num(0.5 * (l - r) / (l - 2 * c + r)), -0.5, 0.5)
        ddy = np.clip(np.nan_to_num(0.5 * (dn - up) / (dn - 2 * c + up)), -0.5, 0.5)
    return pd.DataFrame({"x": ix + ddx, "y": iy + ddy, "snr": c, "map": m.name})


def merge_peaks(pk: pd.DataFrame, r_px: float) -> pd.DataFrame:
    """Peaks of all maps → sources: strongest first, every peak within r_px of a source joins it."""
    if pk.empty:
        return pd.DataFrame({"x": [], "y": [], "snr_peak": [], "found_in": []})
    pk = pk.sort_values("snr", ascending=False).reset_index(drop=True)
    xy = pk[["x", "y"]].to_numpy()
    tree = cKDTree(xy)
    owner = np.full(len(pk), -1)
    rows = []
    for i in range(len(pk)):
        if owner[i] >= 0:
            continue
        j = np.asarray(tree.query_ball_point(xy[i], r_px), int)
        j = j[owner[j] < 0]
        owner[j] = len(rows)
        rows.append((xy[i, 0], xy[i, 1], pk["snr"].iat[i], ",".join(sorted(set(pk["map"].iloc[j])))))
    return pd.DataFrame(rows, columns=["x", "y", "snr_peak", "found_in"])


# --- catalogues -----------------------------------------------------------------------------------------------


def _refcat(store: Store, zone: C.Zone) -> pd.DataFrame:
    try:
        cols = ["ref_id", "cat", "ra", "dec", "epoch_mjd", "pmra", "pmdec", "parallax", "g_mag", "w1_mag", "w2_mag",
                "j_mag"]
        rc = store.read_parquet(f"refcat/{zone.name}.parquet", columns=cols)
    except Exception as e:  # noqa: BLE001
        L.warning("stack %s: no refcat (%s): no catalogue match", zone.name, e)
        return pd.DataFrame()
    pad = 0.05
    cosd = math.cos(math.radians(zone.centre[1]))
    keep = ((rc["dec"] > zone.dec_min - pad) & (rc["dec"] < zone.dec_max + pad)
            & (rc["ra"] > zone.ra_min - pad / max(cosd, 0.05)) & (rc["ra"] < zone.ra_max + pad / max(cosd, 0.05)))
    return rc[keep].reset_index(drop=True)


def _at_epoch(rc: pd.DataFrame, mjd: float) -> tuple[np.ndarray, np.ndarray]:
    """Catalogue rows moved to mjd with their own pm (NaN pm = 0; parallax ignored) from their own epoch."""
    ra, dec = rc["ra"].to_numpy(float), rc["dec"].to_numpy(float)
    ep = rc["epoch_mjd"].to_numpy(float)
    ep = np.where(np.isfinite(ep), ep, GAIA_EPOCH_MJD)
    dt = (mjd - ep) / 365.25
    pmra, pmdec = np.nan_to_num(rc["pmra"].to_numpy(float)), np.nan_to_num(rc["pmdec"].to_numpy(float))
    dec2 = dec + pmdec * dt / 3.6e6
    ra2 = ra + pmra * dt / 3.6e6 / np.maximum(np.cos(np.radians(dec)), 1e-6)
    return ra2, dec2


def nearest(ra, dec, ref_ra, ref_dec, r_arcsec: float, ra0: float, dec0: float) -> tuple[np.ndarray, np.ndarray]:
    """Index of the nearest reference within r_arcsec (−1) and its separation (arcsec, NaN)."""
    n = len(ra)
    if n == 0 or len(ref_ra) == 0:
        return np.full(n, -1), np.full(n, np.nan)
    xi, eta = _tan(ref_ra, ref_dec, ra0, dec0)
    tree = cKDTree(np.column_stack([xi, eta]) * 3600.0)
    a, b = _tan(ra, dec, ra0, dec0)
    d, i = tree.query(np.column_stack([a, b]) * 3600.0, distance_upper_bound=r_arcsec)
    ok = np.isfinite(d)
    return np.where(ok, i, -1), np.where(ok, d, np.nan)


# --- the run --------------------------------------------------------------------------------------------------


def _photos(store: Store, zone: C.Zone, limit: int | None) -> pd.DataFrame:
    from .photolist import touches_zone

    ph = store.read_parquet(photolist_rel(zone.name))
    ph = ph[touches_zone(ph, zone)].sort_values(["visit", "detector", "mjd"]).reset_index(drop=True)
    if limit:
        ph = ph.groupby(["visit", "detector"], group_keys=False).head(max(1, limit)).reset_index(drop=True)
    return ph


def _pool(workers: int):
    return ProcessPoolExecutor(max_workers=max(1, workers), mp_context=mp.get_context("spawn"), initializer=log.setup)


def _tasks(metas: list[dict], n: int) -> list[tuple[tuple[int, int], list[tuple[str, int]]]]:
    by: dict[tuple[int, int], list] = {}
    for m in metas:
        by.setdefault((m["visit"], m["detector"]), []).append((m["obs_id"], m["detector"]))
    out = []
    for key, ph in sorted(by.items()):
        for i in range(0, len(ph), n):
            out.append((key, ph[i:i + n]))
    return out


def run_pass(ex, cache: str, grid: Grid, metas: list[dict], src: Sources | None, mode: str) -> tuple[dict, list]:
    """One pass over every photo; returns the stacks {(visit, det): Acc} and every photo's fit."""
    tasks = _tasks(metas, C.STACK_TASK_PHOTOS)
    futs = [(key, ex.submit(_task, cache, grid, ph, src, mode)) for key, ph in tasks]
    accs: dict[tuple[int, int], Acc] = {}
    fits_all: list = []
    tf = ts = 0.0
    for key, f in futs:
        r = f.result()
        tf += r["fit_s"]
        ts += r["splat_s"]
        if r["acc"] is not None:
            if key in accs:
                accs[key].add(r["acc"])
            else:
                accs[key] = r["acc"]
        fits_all += r["fits"]
    L.info("stack pass %s: %d photos, fit %.1f s, splat %.1f s (cpu, summed)", mode, len(metas), tf, ts)
    return accs, fits_all


def detect(accs: dict[tuple[int, int], Acc], kernels: dict[int, np.ndarray], grid: Grid,
           first: dict | None = None, src_xy: tuple[np.ndarray, np.ndarray] | None = None) -> tuple[pd.DataFrame, dict]:
    """Peaks of every stack's S/N map, its visit's all-wavelength map and the all-visit map. For residual stacks
    pass `first` (the first pass's maps) and the sources' grid positions: their fluxes there scale the leftovers
    (inflate) that count as noise."""
    maps: dict[str, Map] = {}
    for (v, d), acc in sorted(accs.items()):
        m = snr_map(f"v{v}d{d}", acc, kernels[d])
        if first is not None and src_xy is not None and m.name in first:
            f0 = first[m.name]
            x, y = src_xy
            ix = np.clip(np.rint(x).astype(int), 0, grid.nx - 1)
            iy = np.clip(np.rint(y).astype(int), 0, grid.ny - 1)
            F = f0.flux[iy, ix]
            with np.errstate(divide="ignore", invalid="ignore"):
                Ferr = np.abs(F / f0.snr[iy, ix])
            m = inflate(m, x, y, F, Ferr)
        maps[m.name] = m
    visits = sorted({v for v, _ in accs})
    for v in visits:
        m = combine(f"v{v}", [maps[f"v{v}d{d}"] for vv, d in accs if vv == v])
        if m is not None:
            maps[m.name] = m
    if len(visits) > 1:
        m = combine("all", [maps[f"v{v}d{d}"] for v, d in accs])
        if m is not None:
            maps["all"] = m
    pk = pd.concat([peaks(m) for m in maps.values()], ignore_index=True)
    return merge_peaks(pk, C.STACK_MERGE_ARCSEC / grid.pix), maps


def _gaia_sources(src_ra, src_dec, mjd: float, gaia: pd.DataFrame, ra0: float, dec0: float):
    """Nearest Gaia DR3 star (moved to mjd) within STACK_GAIA_MATCH_ARCSEC: (index, sep)."""
    if gaia.empty:
        return np.full(len(src_ra), -1), np.full(len(src_ra), np.nan)
    gra, gdec = _at_epoch(gaia, mjd)
    return nearest(src_ra, src_dec, gra, gdec, C.STACK_GAIA_MATCH_ARCSEC, ra0, dec0)


def _with_gaia(ra, dec, gi, gaia: pd.DataFrame) -> Sources:
    s = Sources.plain(ra, dec)
    g = gi >= 0
    if g.any():
        rows = gaia.iloc[gi[g]]
        s.g_ra[g], s.g_dec[g] = rows["ra"].to_numpy(float), rows["dec"].to_numpy(float)
        s.g_pmra[g], s.g_pmdec[g] = np.nan_to_num(rows["pmra"].to_numpy(float)), np.nan_to_num(rows["pmdec"].to_numpy(float))
        s.g_plx[g] = np.nan_to_num(rows["parallax"].to_numpy(float))
    return s


def _refine(src: Sources, fits_all: list) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Weighted mean of each source's free-fit offsets over all photos → new stack positions (non-Gaia sources)."""
    n = len(src.ra)
    se, sn, sw = np.zeros(n), np.zeros(n), np.zeros(n)
    for r in fits_all:
        if "free_ok" not in r or not r["idx"].size:
            continue
        ok = r["free_ok"]
        i = r["idx"][ok]
        cosd = np.cos(np.radians(r["dec"][ok]))
        de = ((r["ra_free"][ok] - r["ra"][ok] + 180.0) % 360.0 - 180.0) * cosd * 3600.0
        dn = (r["dec_free"][ok] - r["dec"][ok]) * 3600.0
        s2 = (np.square(r["sx"][ok]) + np.square(r["sy"][ok])) / 2.0 * C.STACK_NATIVE_PIX_ARCSEC ** 2
        w = 1.0 / np.maximum(s2, 1e-4)
        np.add.at(se, i, w * de)
        np.add.at(sn, i, w * dn)
        np.add.at(sw, i, w)
    with np.errstate(invalid="ignore", divide="ignore"):
        de, dn = se / sw, sn / sw
    de, dn = np.nan_to_num(de), np.nan_to_num(dn)
    ra = src.ra + de / 3600.0 / np.cos(np.radians(src.dec))
    dec = src.dec + dn / 3600.0
    err = np.where(sw > 0, 1.0 / np.sqrt(np.maximum(sw, 1e-12)), np.nan)
    return ra, dec, err


def forced_table(fits_all: list, ids: np.ndarray) -> pd.DataFrame:
    parts = []
    for r in fits_all:
        if not r["idx"].size:
            continue
        m = r["meta"]
        conv = float(m["conv"])
        n = r["idx"].size
        sx = r.get("sx", np.full(n, np.nan))
        sy = r.get("sy", np.full(n, np.nan))
        parts.append(pd.DataFrame({
            "stack_id": ids[r["idx"]], "obs_id": m["obs_id"], "detector": np.int8(m["detector"]),
            "visit": np.int16(m["visit"]), "mjd": m["mjd"],
            "x": (r["x"] + m["x0"]).astype(np.float32), "y": (r["y"] + m["y0"]).astype(np.float32),
            "pos_gaia": r["gaia_pos"],
            "flux_ujy": (r["flux"] * conv).astype(np.float32), "flux_err_ujy": (r["err"] * conv).astype(np.float32),
            "snr": (r["flux"] / r["err"]).astype(np.float32), "chi2": r["chi2"].astype(np.float32),
            "npix": r["npix"], "flags": r["flags"],
            "ra_free": np.where(r.get("free_ok", np.zeros(n, bool)), r.get("ra_free", np.nan), np.nan),
            "dec_free": np.where(r.get("free_ok", np.zeros(n, bool)), r.get("dec_free", np.nan), np.nan),
            "pos_err_free_arcsec": (np.sqrt((np.square(sx) + np.square(sy)) / 2.0)
                                    * C.STACK_NATIVE_PIX_ARCSEC).astype(np.float32),
            "free_ok": r.get("free_ok", np.zeros(n, bool)),
        }))
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True)


def _wmean(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    ok = np.isfinite(df["flux_ujy"]) & np.isfinite(df["flux_err_ujy"]) & (df["flux_err_ujy"] > 0)
    d = df.loc[ok, keys + ["flux_ujy", "flux_err_ujy"]].copy()
    d["w"] = 1.0 / np.square(d["flux_err_ujy"].astype(float))
    d["fw"] = d["flux_ujy"] * d["w"]
    g = d.groupby(keys, sort=False)[["fw", "w"]].sum()
    g["flux"] = g["fw"] / g["w"]
    g["err"] = 1.0 / np.sqrt(g["w"])
    g["snr"] = g["flux"] / g["err"]
    return g[["flux", "err", "snr"]].reset_index()


def summarise(forced: pd.DataFrame, n_src: int) -> pd.DataFrame:
    """Per source: flux, error and S/N per detector (all visits), per visit (all detectors), all; transient share;
    per-visit free-fit positions."""
    out = pd.DataFrame({"stack_id_idx": np.arange(n_src)})
    f = forced.copy()
    for d in range(1, 7):
        g = _wmean(f[f["detector"] == d], ["i"]).set_index("i")
        out[f"flux_ujy_d{d}"] = g["flux"].reindex(out.index).to_numpy(np.float32)
        out[f"flux_err_ujy_d{d}"] = g["err"].reindex(out.index).to_numpy(np.float32)
        out[f"snr_d{d}"] = g["snr"].reindex(out.index).to_numpy(np.float32)
    vd = _wmean(f, ["i", "visit", "detector"])
    best = vd.groupby("i")["snr"].max()
    v = _wmean(f, ["i", "visit"])
    a = _wmean(f, ["i"]).set_index("i")
    out["snr_all"] = a["snr"].reindex(out.index).to_numpy(np.float32)
    out["snr_best"] = np.fmax(np.fmax(best.reindex(out.index).to_numpy(float),
                                      v.groupby("i")["snr"].max().reindex(out.index).to_numpy(float)),
                              out["snr_all"].to_numpy(float)).astype(np.float32)
    vis = sorted(f["visit"].unique())
    snr_v = v.pivot(index="i", columns="visit", values="snr").reindex(index=out.index, columns=vis)
    flux_v = v.pivot(index="i", columns="visit", values="flux").reindex(index=out.index, columns=vis)
    out["visits"] = [np.asarray(vis, np.int16)] * n_src
    out["snr_visit"] = [np.asarray(r, np.float32) for r in snr_v.to_numpy()]
    out["flux_visit_ujy"] = [np.asarray(r, np.float32) for r in flux_v.to_numpy()]
    s2 = np.square(np.clip(f["snr"].to_numpy(float), 0, None))
    f["s2"] = np.nan_to_num(s2)
    g = f.groupby("i")["s2"].agg(["max", "sum"])
    out["transient_share"] = (g["max"] / g["sum"].where(g["sum"] > 0)).reindex(out.index).to_numpy(np.float32)
    out["n_photos"] = f.groupby("i").size().reindex(out.index).fillna(0).to_numpy(np.int32)
    sat = (f["flags"].to_numpy() & 0x00008002) != 0
    f["sat"] = sat
    out["saturated"] = (f.groupby("i")["sat"].mean().reindex(out.index).fillna(0) > 0.5).to_numpy()
    # per-visit positions from the free fits
    fr = f[f["free_ok"]].copy()
    rows_ra = [[] for _ in range(n_src)]
    rows_dec, rows_err = [[] for _ in range(n_src)], [[] for _ in range(n_src)]
    if not fr.empty:
        fr["w"] = 1.0 / np.square(np.maximum(fr["pos_err_free_arcsec"].astype(float), 0.01))
        fr["wra"], fr["wdec"] = fr["ra_free"] * fr["w"], fr["dec_free"] * fr["w"]
        g = fr.groupby(["i", "visit"])[["wra", "wdec", "w"]].sum()
        for (i, vv), r in g.iterrows():
            rows_ra[i].append(r["wra"] / r["w"])
            rows_dec[i].append(r["wdec"] / r["w"])
            rows_err[i].append(1.0 / math.sqrt(r["w"]))
        vis_of = g.reset_index().groupby("i")["visit"].apply(list)
    else:
        vis_of = pd.Series(dtype=object)
    out["visits_free"] = [np.asarray(vis_of.get(i, []), np.int16) for i in range(n_src)]
    out["ra_visit"] = [np.asarray(x, float) for x in rows_ra]
    out["dec_visit"] = [np.asarray(x, float) for x in rows_dec]
    out["pos_err_visit_arcsec"] = [np.asarray(x, np.float32) for x in rows_err]
    return out


def run(store: Store, zone: C.Zone, workers: int, limit: int | None = None, cache: str | None = None,
        keep_cache: bool = True) -> dict:
    t_start = time.time()
    grid = zone_grid(zone)
    own_cache = cache is None
    cache = cache or os.path.join(tempfile.gettempdir(), f"skyshift-stack-{zone.name}")
    os.makedirs(cache, exist_ok=True)
    ph = _photos(store, zone, limit)
    L.info("stack %s: %d photos touch the zone, grid %d × %d at %.1f″, cache %s", zone.name, len(ph), grid.nx, grid.ny,
           grid.pix, cache)
    timing: dict[str, float] = {}
    with _pool(workers) as ex:
        t0 = time.time()
        futs = [ex.submit(read_cutout, r.url, r.obs_id, int(r.detector), int(r.visit), int(r.session), grid, cache)
                for r in ph.itertuples()]
        reads = [f.result() for f in futs]
        timing["read_s"] = time.time() - t0
        metas = [r for r in reads if r["status"] in ("done", "cached")]
        st = pd.Series([r["status"] for r in reads]).value_counts().to_dict()
        L.info("stack %s: read %s in %.0f s", zone.name, st, timing["read_s"])
        if not metas:
            return {"photos": len(ph), "read": st, "sources": 0}
        kernels = {}
        for m in metas:
            if m["detector"] not in kernels:
                kernels[m["detector"]] = psf_kernel(load_epsf(cache, m["epsf"]))
        mjds = np.array([m["mjd"] for m in metas])
        visit_mjd = {v: float(np.median([m["mjd"] for m in metas if m["visit"] == v])) for v in {m["visit"] for m in metas}}
        mean_mjd = float(np.median(mjds))
        rc = _refcat(store, zone)
        gaia = rc[rc["cat"] == "gaia"].reset_index(drop=True) if not rc.empty else pd.DataFrame()

        # pass 1: stacks → peaks
        t0 = time.time()
        accs, _ = run_pass(ex, cache, grid, metas, None, "stack")
        cand, maps = detect(accs, kernels, grid)
        cand["pass"] = 1
        timing["pass1_s"] = time.time() - t0
        if keep_cache:
            save_maps(os.path.join(cache, "maps_pass1.npz"), accs, maps, grid)
        L.info("stack %s: pass 1 %d sources from %d maps (%.0f s)", zone.name, len(cand), len(maps), timing["pass1_s"])
        src_ra, src_dec = grid.radec(cand["x"].to_numpy(), cand["y"].to_numpy())
        src_ra, src_dec, cand = _dedupe_gaia(src_ra, src_dec, cand, gaia, visit_mjd, mean_mjd, grid)
        resid_maps = {}
        for p in range(C.STACK_RESID_PASSES):
            t0 = time.time()
            gi, _ = _gaia_sources(src_ra, src_dec, mean_mjd, gaia, grid.ra0, grid.dec0)
            src = _with_gaia(src_ra, src_dec, gi, gaia)
            accs_r, fits_r = run_pass(ex, cache, grid, metas, src, "refine")
            ra2, dec2, _ = _refine(src, fits_r)
            g = gi >= 0
            src_ra, src_dec = np.where(g, src_ra, ra2), np.where(g, src_dec, dec2)
            keep = dedupe_close(src_ra, src_dec, cand["snr_peak"].to_numpy(float), grid, C.STACK_DEDUPE_ARCSEC)
            src_ra, src_dec, cand = src_ra[keep], src_dec[keep], cand[keep].reset_index(drop=True)
            new, resid_maps = detect(accs_r, kernels, grid, first=maps, src_xy=grid.xy(src_ra, src_dec))
            if keep_cache:
                save_maps(os.path.join(cache, f"maps_pass{p + 2}.npz"), accs_r, resid_maps, grid)
            if not new.empty:
                nra, ndec = grid.radec(new["x"].to_numpy(), new["y"].to_numpy())
                _, sep_ = nearest(nra, ndec, src_ra, src_dec, C.STACK_RESID_MIN_SEP_ARCSEC, grid.ra0, grid.dec0)
                keep = ~np.isfinite(sep_)
                new = new[keep].assign(**{"pass": p + 2})
                src_ra, src_dec = np.r_[src_ra, nra[keep]], np.r_[src_dec, ndec[keep]]
                cand = pd.concat([cand, new], ignore_index=True)
            timing[f"pass{p + 2}_s"] = time.time() - t0
            L.info("stack %s: residual pass %d: %d new sources (%.0f s)", zone.name, p + 2, int((cand["pass"] == p + 2).sum()),
                   timing[f"pass{p + 2}_s"])
        # final fit
        t0 = time.time()
        gi, _ = _gaia_sources(src_ra, src_dec, mean_mjd, gaia, grid.ra0, grid.dec0)
        src = _with_gaia(src_ra, src_dec, gi, gaia)
        _, fits_f = run_pass(ex, cache, grid, metas, src, "final")
        timing["final_s"] = time.time() - t0
    n = len(src_ra)
    forced = forced_table(fits_f, np.arange(n))
    if n == 0 or forced.empty:
        L.warning("stack %s: no sources", zone.name)
        return {"photos": len(ph), "photos_used": len(metas), "sources": 0}
    forced["i"] = forced["stack_id"]
    summ = summarise(forced, n)
    cand = cand.reset_index(drop=True)
    # positions: Gaia-matched sources at Gaia DR3 moved (pm only) to the epoch, the others the refined stack position
    g = np.isfinite(src.g_ra)
    pra, pdec = src_ra.copy(), src_dec.copy()
    if g.any():
        pra[g], pdec[g] = propagate(src.g_ra[g], src.g_dec[g], src.g_pmra[g], src.g_pmdec[g], np.full(g.sum(), mean_mjd))
    _, _, perr = _refine(src, fits_f)
    sources = pd.DataFrame({"i": np.arange(n), "ra": pra, "dec": pdec, "epoch_mjd": mean_mjd, "pos_gaia": g,
                            "pos_err_arcsec": perr.astype(np.float32),
                            "snr_peak": cand["snr_peak"].to_numpy(np.float32), "found_in": cand["found_in"].to_numpy(),
                            "found_pass": cand["pass"].to_numpy(np.int8)})
    sources = pd.concat([sources, summ.drop(columns=["stack_id_idx"])], axis=1)
    inz = zone.contains(sources["ra"].to_numpy(), sources["dec"].to_numpy())
    neg = sources[[f"snr_d{d}" for d in range(1, 7)]].min(axis=1).fillna(0).to_numpy() < -C.STACK_NEG_SNR
    keep = (sources["snr_best"] >= C.STACK_KEEP_SNR).to_numpy() & inz & ~neg
    dropped = {"below_keep_snr": int((inz & (sources["snr_best"] < C.STACK_KEEP_SNR)).sum()),
               "negative_detector": int((inz & neg & (sources["snr_best"] >= C.STACK_KEEP_SNR)).sum())}
    sources = sources[keep].copy()
    sources = match_catalogues(store, zone, sources, rc, grid)
    sources = sources.sort_values("dec").reset_index(drop=True)
    sources["stack_id"] = np.arange(len(sources), dtype=np.int64)
    remap = pd.Series(sources["stack_id"].to_numpy(), index=sources["i"].to_numpy())
    forced = forced[forced["i"].isin(remap.index)].copy()
    forced["stack_id"] = remap.reindex(forced["i"].to_numpy()).to_numpy(np.int64)
    forced = forced.drop(columns=["i"]).sort_values(["stack_id", "mjd", "detector"]).reset_index(drop=True)
    sources = sources.drop(columns=["i"])
    sources = sources[["stack_id"] + [c for c in sources.columns if c != "stack_id"]]
    report = stack_report(sources, rc, zone, grid, mean_mjd)
    report.update({"photos": len(ph), "photos_used": len(metas), "read": st, "dropped": dropped,
                   "grid": {"nx": grid.nx, "ny": grid.ny, "pix_arcsec": grid.pix},
                   "maps": {k: round(float(m.sigma), 4) for k, m in maps.items()},
                   "timing_s": {k: round(v, 1) for k, v in timing.items()},
                   "wall_s": round(time.time() - t_start, 1),
                   "s_per_photo": round((time.time() - t_start) / max(1, len(metas)), 2)})
    nb = store.write_parquet(results_rel(zone.name, SOURCES_REL), sources)
    nf = store.write_parquet(results_rel(zone.name, FORCED_REL), forced)
    report["bytes"] = {"stack_sources": nb, "stack_forced": nf}
    store.write_json(results_rel(zone.name, REPORT_REL), report)
    if own_cache and not keep_cache:
        shutil.rmtree(cache, ignore_errors=True)
    L.info("stack %s: %s", zone.name, {k: v for k, v in report.items() if k not in ("maps",)})
    return {k: v for k, v in report.items() if k not in ("maps", "read")}


def dedupe_close(ra, dec, snr, grid: Grid, r_arcsec: float) -> np.ndarray:
    """Keep mask: of sources closer than r_arcsec, the strongest stays."""
    keep = np.ones(len(ra), bool)
    if len(ra) < 2:
        return keep
    x, y = grid.xy(ra, dec)
    xy = np.column_stack([x, y]) * grid.pix
    tree = cKDTree(xy)
    for i in np.argsort(-np.nan_to_num(snr)):
        if not keep[i]:
            continue
        for j in tree.query_ball_point(xy[i], r_arcsec):
            if j != i:
                keep[j] = False
    return keep


def save_maps(path: str, accs: dict, maps: dict, grid: Grid) -> None:
    """The stacks (weighted mean image per visit × detector, MJy/sr) and the S/N maps, for inspection."""
    out = {"grid": np.asarray([grid.ra0, grid.dec0, grid.pix, grid.xoff, grid.yoff, grid.nx, grid.ny])}
    for (v, d), a in accs.items():
        with np.errstate(divide="ignore", invalid="ignore"):
            out[f"img_v{v}d{d}"] = np.where(a.w > 0, a.s / a.w, np.nan).astype(np.float32)
    for k, m in maps.items():
        out[f"snr_{k}"] = m.snr.astype(np.float32)
    _atomic_save(path, **out)


def _dedupe_gaia(ra, dec, cand: pd.DataFrame, gaia: pd.DataFrame, visit_mjd: dict, mean_mjd: float, grid: Grid):
    """A fast Gaia star found in several visits' maps at its several positions is one source (the strongest)."""
    if gaia.empty or not len(ra):
        return ra, dec, cand.reset_index(drop=True)
    best = np.full(len(ra), -1)
    for v, mjd in list(visit_mjd.items()) + [(None, mean_mjd)]:
        gi, _ = _gaia_sources(ra, dec, mjd, gaia, grid.ra0, grid.dec0)
        best = np.where(best < 0, gi, best)
    cand = cand.reset_index(drop=True).assign(_g=best, _ra=ra, _dec=dec)
    g = cand[cand["_g"] >= 0].sort_values("snr_peak", ascending=False)
    drop = g.index[g.duplicated("_g")]
    cand = cand.drop(index=drop).reset_index(drop=True)
    return cand.pop("_ra").to_numpy(), cand.pop("_dec").to_numpy(), cand.drop(columns=["_g"])


def match_catalogues(store: Store, zone: C.Zone, s: pd.DataFrame, rc: pd.DataFrame, grid: Grid) -> pd.DataFrame:
    ra, dec, mjd = s["ra"].to_numpy(), s["dec"].to_numpy(), float(s["epoch_mjd"].iloc[0]) if len(s) else 0.0
    s = s.copy()
    for cat, pre, col, r in (("gaia", "gaia", "g_mag", C.STACK_GAIA_MATCH_ARCSEC),
                             ("catwise", "catwise", "w1_mag", C.STACK_CAT_MATCH_ARCSEC),
                             ("2mass", "tmass", "j_mag", C.STACK_CAT_MATCH_ARCSEC)):
        sub = rc[rc["cat"] == cat].reset_index(drop=True) if not rc.empty else pd.DataFrame()
        if sub.empty:
            s[f"{pre}_ref_id"], s[f"{pre}_sep_arcsec"], s[f"{pre}_mag"] = "", np.float32(np.nan), np.float32(np.nan)
            continue
        cra, cdec = _at_epoch(sub, mjd)
        i, d = nearest(ra, dec, cra, cdec, r, grid.ra0, grid.dec0)
        s[f"{pre}_ref_id"] = np.where(i >= 0, sub["ref_id"].to_numpy()[np.maximum(i, 0)], "")
        s[f"{pre}_sep_arcsec"] = d.astype(np.float32)
        s[f"{pre}_mag"] = np.where(i >= 0, sub[col].to_numpy(float)[np.maximum(i, 0)], np.nan).astype(np.float32)
    s["cat"] = np.select([s["gaia_ref_id"] != "", s["catwise_ref_id"] != "", s["tmass_ref_id"] != ""],
                         ["gaia", "catwise", "2mass"], "")
    # single-photo sources
    try:
        sp = store.read_parquet(results_rel(zone.name, "sources.parquet"), columns=["source_id", "ra", "dec"])
    except Exception as e:  # noqa: BLE001
        L.warning("stack %s: no sources.parquet (%s): every source counts as new", zone.name, e)
        sp = pd.DataFrame({"source_id": [], "ra": [], "dec": []})
    i, d = nearest(ra, dec, sp["ra"].to_numpy(float), sp["dec"].to_numpy(float), C.STACK_SINGLE_MATCH_ARCSEC,
                   grid.ra0, grid.dec0)
    s["source_id"] = np.where(i >= 0, sp["source_id"].to_numpy(np.int64)[np.maximum(i, 0)] if len(sp) else -1, -1).astype(np.int64)
    s["single_sep_arcsec"] = d.astype(np.float32)
    s["transient"] = (s["transient_share"] > C.STACK_TRANSIENT_SHARE).to_numpy()
    s["new"] = (s["source_id"] < 0) & ~s["transient"]
    # bright star context
    gaia = rc[(rc["cat"] == "gaia") & (rc["g_mag"] < C.STACK_BRIGHT_G)].reset_index(drop=True) if not rc.empty else pd.DataFrame()
    if gaia.empty:
        s["bright_sep_arcsec"], s["bright_g"] = np.float32(np.nan), np.float32(np.nan)
    else:
        bra, bdec = _at_epoch(gaia, mjd)
        i, d = nearest(ra, dec, bra, bdec, C.STACK_BRIGHT_RADIUS_ARCSEC, grid.ra0, grid.dec0)
        s["bright_sep_arcsec"] = d.astype(np.float32)
        s["bright_g"] = np.where(i >= 0, gaia["g_mag"].to_numpy(float)[np.maximum(i, 0)], np.nan).astype(np.float32)
    s["brighter_sep_arcsec"] = brighter_sep(s["ra"].to_numpy(), s["dec"].to_numpy(), s["snr_all"].to_numpy(float),
                                            grid).astype(np.float32)
    d = s["brighter_sep_arcsec"].to_numpy(float)
    lo, hi = C.STACK_ARTIFACT_RING_ARCSEC
    s["artifact_zone"] = (s["cat"] == "").to_numpy() & ((d < C.STACK_ARTIFACT_CORE_ARCSEC) | ((d >= lo) & (d <= hi)))
    return s


def brighter_sep(ra, dec, snr, grid: Grid) -> np.ndarray:
    """Distance (arcsec) to the nearest source with S/N ≥ STACK_ARTIFACT_MIN_SNR and ≥ STACK_ARTIFACT_RATIO × this
    one's, within STACK_ARTIFACT_RADIUS_ARCSEC; NaN none."""
    n = len(ra)
    out = np.full(n, np.nan)
    snr = np.nan_to_num(np.asarray(snr, float), nan=0.0)
    b = np.nonzero(snr >= C.STACK_ARTIFACT_MIN_SNR)[0]
    if n == 0 or b.size == 0:
        return out
    x, y = grid.xy(ra, dec)
    xy = np.column_stack([x, y]) * grid.pix
    tree = cKDTree(xy[b])
    for i, js in enumerate(tree.query_ball_point(xy, C.STACK_ARTIFACT_RADIUS_ARCSEC)):
        js = [b[j] for j in js if b[j] != i and snr[b[j]] >= C.STACK_ARTIFACT_RATIO * max(snr[i], 1.0)]
        if js:
            out[i] = float(np.min(np.hypot(*(xy[js] - xy[i]).T)))
    return out


def stack_report(s: pd.DataFrame, rc: pd.DataFrame, zone: C.Zone, grid: Grid, mjd: float) -> dict:
    """What the stacks found that the single-photo sources didn't."""
    new = s[s["new"]]
    out = {"sources": int(len(s)), "single_matched": int((s["source_id"] >= 0).sum()), "new": int(len(new)),
           "transient": int(s["transient"].sum()),
           "new_by_catalogue": {"gaia": int((new["cat"] == "gaia").sum()), "catwise_only": int((new["cat"] == "catwise").sum()),
                                "2mass_only": int((new["cat"] == "2mass").sum()), "none": int((new["cat"] == "").sum())},
           "found_pass2": int((s["found_pass"] >= 2).sum()),
           "new_near_bright": int((new["bright_sep_arcsec"].notna()).sum()),
           "uncatalogued": int((s["cat"] == "").sum()), "artifact_zone": int(s["artifact_zone"].sum()),
           "new_stack_class": int((new["cat"].notna() & ~s.loc[new.index, "artifact_zone"]).sum())}
    # pairs: Gaia pairs 6–15″ (both G < 19) inside the zone; split = each star has its own source within 2.5″
    if not rc.empty:
        g = rc[(rc["cat"] == "gaia") & (rc["g_mag"] < 19)].reset_index(drop=True)
        gra, gdec = _at_epoch(g, mjd)
        inz = zone.contains(gra, gdec)
        xi, eta = _tan(gra, gdec, grid.ra0, grid.dec0)
        tree = cKDTree(np.column_stack([xi, eta]) * 3600.0)
        pairs = np.array(sorted(tree.query_pairs(15.0)), int).reshape(-1, 2)
        if len(pairs):
            dd = np.hypot(*(np.column_stack([xi, eta])[pairs[:, 0]] - np.column_stack([xi, eta])[pairs[:, 1]]).T) * 3600
            pairs = pairs[(dd >= 6.0) & inz[pairs[:, 0]] & inz[pairs[:, 1]]]
            # isolated pairs only: no third G < 19 star within 15″ of either
            cnt = np.bincount(np.array(sorted(tree.query_pairs(15.0)), int).ravel(), minlength=len(g))
            pairs = pairs[(cnt[pairs[:, 0]] == 1) & (cnt[pairs[:, 1]] == 1)]
        i, _ = nearest(gra, gdec, s["ra"].to_numpy(), s["dec"].to_numpy(), 2.5, grid.ra0, grid.dec0)
        split = int(sum(1 for a, b in pairs if i[a] >= 0 and i[b] >= 0 and i[a] != i[b]))
        out["gaia_pairs_6_15"] = {"pairs": int(len(pairs)), "split_stack": split}
    return out
