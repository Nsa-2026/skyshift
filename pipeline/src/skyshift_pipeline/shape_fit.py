"""Two-source fit of the shape candidates ("one object or two?"): one ePSF against two, on the pixels.

shape.py finds a fixed source whose dots are stretched along one axis; at 1–2 SPHEREx pixels (6.15″, PSF FWHM
7.5″) detection gives one dot per photo, so a second object merged into a star has no dot, position or flux of
its own. Here every photo the source was linked in (members.parquet) is read back from NASA's S3 copy (range
reads of the IMAGE, FLAGS and VARIANCE rows around it, anonymous HTTPS; the EPSF HDU once per detector: it is the
same calibration table in every photo of a detector) and its pixels are fitted twice:

  one:  D = B + F1 P(x − x1) + Σ_n F_n P(x − x1 − o_n)
  two:  D = B + F1 P(x − x1) + F2 P(x − x1 − A Δ) + Σ_n F_n P(x − x1 − o_n)

P is the photo's ePSF (evaluated at pixel centres: the pixel response is in it), x1 the primary's position (free
per photo: the WCS needs no trust), F1, F2, F_n, B linear per photo, o_n the catalogued neighbours in the stamp
(Gaia, CatWISE, at their catalogue offsets from the primary, fluxes free), A the photo's arcsec → pixel matrix
and Δ = (east, north) the companion's offset from the primary, ONE value for all photos: a real second object sits
at one place on the sky whatever the detector's roll and wavelength. Weights 1/(VARIANCE + (FIT_MODEL_FRAC ×
one-source model)²), the same for both fits, masked pixels (contracts/flags.md) out.

Δ comes from a grid over ±FIT_GRID_ARCSEC (profile χ²: x1 and the fluxes re-fitted at each point), refined on two
finer grids; its error from the χ² curvature (× √χ²_ν). Two against one: Δχ² = χ²_one − χ²_two (the two-source
fit has one flux per photo and Δ more, so pure noise gives about n_photos + 2; isolated single Gaia stars of the
same brightness calibrate it, `controls`). F2 is free per photo, so a bright star's photo-to-photo ePSF errors
also lower χ² (a few per photo): the decision rests on the companion's S/N per detector, from its flux ratio F2/F1
(the photos' weighted mean) with an error that is the larger of the formal one and the photos' scatter, and a real
object gives no negative light in any detector. Per visit: Δ fitted on that visit's photos alone; a companion that moves between visits (an
asteroid passing a star) lands at different offsets, a background star or a bound companion at the same one.
The companion's sky position = the primary's (median of x1 through each photo's WCS) + Δ; any Gaia, CatWISE or 2MASS
entry within FIT_CATALOG_ARCSEC of it (moved to the photos' epoch) is named in `companion_catalog`.
"""

from __future__ import annotations

import io
import math
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import config as C
from . import log

L = log.get("shape_fit")

FIT_HALF_PX = 6                # 13 × 13 pixel stamp around the source (±37″: the companion and the PSF wings)
FIT_NEIGHBOUR_PX = 9           # catalogued neighbours whose offset from the primary is within this box: own ePSF
FIT_NEIGHBOUR_DMAG = 3.0       # ... no more than this fainter than the primary (G for Gaia, W1 for CatWISE) ...
FIT_NEIGHBOUR_G0 = 19.0        # ... or, without a primary in that catalogue, brighter than these
FIT_NEIGHBOUR_W10 = 16.0
FIT_NEIGHBOUR_MAX = 6          # ... the nearest this many
FIT_MODEL_FRAC = C.PSF_MODEL_FRAC  # ePSF shape error added to the pixel noise (as psf.py)
FIT_GRID_ARCSEC = 21.0         # companion search: east and north offsets within ±this ...
FIT_GRID_STEP_ARCSEC = 2.5     # ... on this grid
FIT_MIN_SEP_ARCSEC = 4.0       # closer than this (half the PSF FWHM) a second ePSF fixes the PSF's shape errors
FIT_EDGE_ARCSEC = 0.5          # a best offset within this of FIT_MIN_SEP_ARCSEC sits on the search's edge: no split
FIT_GRID_ITER = 2              # Gauss–Newton steps per grid point (from the one-source positions) ...
FIT_GRID_PHOTOS = 8            # ... on at most this many photos per detector (the refinement uses all)
FIT_ITER = 5                   # ... in the refinement (5 × 5 points at 0.6″, then at 0.15″) and the one-source fit
FIT_PSF_OS = 20                # the ePSF tabulated at 1/this native pixel and read bilinearly (< 0.2% of its peak off)
FIT_MAX_STEP_PX = 0.5
FIT_MIN_GOOD_PIX = 100         # a photo's stamp needs this many unmasked pixels
FIT_DCHI2_MIN = 400.0          # two objects: Δχ² above this (and above the controls; see the report) ...
FIT_DCHI2_PER_PHOTO_MIN = 3.0  # ... and above this per photo (noise gives ~1 per photo)
FIT_COMPANION_SNR_MIN = 5.0    # ... and the companion seen at this S/N in at least one detector ...
FIT_COMPANION_NEG_SNR = -3.0   # ... and in no detector below this (a real object never gives negative light)
FIT_CATALOG_ARCSEC = 3.0      # a catalogue entry this close to the companion is the companion
FIT_MOVE_SIGMA = 3.0           # moves: the per-visit offsets differ by more than this many σ ...
FIT_MOVE_MIN_ARCSEC = 2.0      # ... and by more than this
FIT_VISIT_MIN_PHOTOS = 8       # a visit's own fit needs this many photos
FIT_MAX_PHOTOS = 400           # photos per source (spread over detectors and visits)
FIT_WORKERS = 32
UJY_PER_MJYSR = 889.7          # 1 MJy/sr in one 6.152″ pixel (core/photometry.py)
DET_UM = {1: 0.93, 2: 1.35, 3: 2.0, 4: 3.1, 5: 4.1, 6: 4.7}  # central wavelength of each detector (µm)
MASK_BITS = 0x0A02CEC7         # contracts/flags.md mask list (core.flags.MASK_BITS)
BLOCK = 2880


# --- reading the photos ---------------------------------------------------------------------------
class Reader:
    """Range reads of NASA's public S3 copy (anonymous HTTPS): HDU layout per file, rows of IMAGE, FLAGS and
    VARIANCE around a position, the EPSF table once per (detector, collection)."""

    def __init__(self) -> None:
        import requests

        self._local = threading.local()
        self._requests = requests
        self._epsf: dict[tuple, object] = {}
        self._lock = threading.Lock()

    def _session(self):
        s = getattr(self._local, "s", None)
        if s is None:
            s = self._requests.Session()
            a = self._requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=4, max_retries=2)
            s.mount("https://", a)
            self._local.s = s
        return s

    def get(self, url: str, start: int, n: int) -> bytes:
        last = None
        for attempt in range(4):
            try:
                r = self._session().get(url, headers={"Range": f"bytes={start}-{start + n - 1}"}, timeout=60)
                r.raise_for_status()
                return r.content
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(0.5 * (attempt + 1))
        raise OSError(f"range read of {url} failed: {last!r}")

    def headers(self, url: str, until: str = "VARIANCE") -> dict[str, tuple[int, object, int]]:
        """EXTNAME -> (first data byte, header, padded data size), walking the file's headers up to `until`."""
        from astropy.io import fits

        out: dict[str, tuple[int, object, int]] = {}
        pos = 0
        for _ in range(8):
            want = 12 * BLOCK
            raw = self.get(url, pos, want)
            end = _end_of_header(raw)
            while end is None and len(raw) == want and want < 200 * BLOCK:
                want *= 3
                raw = self.get(url, pos, want)
                end = _end_of_header(raw)
            if end is None:
                raise ValueError(f"no END in a header of {url}")
            h = fits.Header.fromstring(raw[:end].decode("ascii", "replace"))
            name = str(h.get("EXTNAME", "PRIMARY")).strip()
            naxis = int(h.get("NAXIS", 0))
            size = (abs(int(h["BITPIX"])) // 8) * int(np.prod([int(h[f"NAXIS{i}"]) for i in range(1, naxis + 1)])) if naxis else 0
            size += int(h.get("PCOUNT", 0))
            size = -(-size // BLOCK) * BLOCK
            out[name] = (pos + end, h, size)
            if name == until:
                break
            pos += end + size
        return out

    def float_rows(self, url: str, start: int, nx: int, y0: int, y1: int) -> np.ndarray:
        n = (y1 - y0) * nx * 4
        raw = self.get(url, start + y0 * nx * 4, n)
        if len(raw) != n:
            raise OSError(f"short read of {url}")
        return np.frombuffer(raw, dtype=">f4").reshape(y1 - y0, nx).astype(np.float32)

    def flag_rows(self, url: str, start: int, h, nx: int, y0: int, y1: int) -> np.ndarray:
        """Rows of the tile-compressed FLAGS (RICE_1, one tile per row; astropy's own codec decodes them)."""
        from astropy.io.fits.hdu.compressed._codecs import Rice1

        if (str(h.get("ZCMPTYPE", "")).strip() != "RICE_1" or int(h.get("ZTILE2", 0)) != 1
                or int(h.get("ZTILE1", 0)) != nx or int(h.get("ZBITPIX", 0)) != 32):
            raise ValueError("FLAGS is not RICE_1 row tiles of int32")
        params = {str(h.get(f"ZNAME{i}", "")).strip(): int(h[f"ZVAL{i}"]) for i in (1, 2, 3) if f"ZVAL{i}" in h}
        row_bytes = int(h["NAXIS1"])
        heap = start + int(h.get("THEAP", row_bytes * int(h["NAXIS2"])))
        raw = self.get(url, start + y0 * row_bytes, (y1 - y0) * row_bytes)
        desc = np.frombuffer(raw, dtype=">i4").reshape(y1 - y0, 2).astype(np.int64)
        lo, hi = int(desc[:, 1].min()), int((desc[:, 1] + desc[:, 0]).max())
        blob = self.get(url, heap + lo, hi - lo)
        codec = Rice1(blocksize=params.get("BLOCKSIZE", 32), bytepix=params.get("BYTEPIX", 4), tilesize=nx)
        out = np.empty((y1 - y0, nx), dtype=np.int32)
        for i, (n, off) in enumerate(desc):
            out[i] = codec.decode(np.frombuffer(blob, dtype=np.uint8, count=n, offset=off - lo))
        return out

    def epsf(self, url: str, det: int, collection: str):
        """The detector's ePSF table (skyshift_core.level2.EPSF), read once per (detector, collection)."""
        from astropy.io import fits

        from skyshift_core.level2 import EPSF

        key = (int(det), str(collection))
        with self._lock:
            got = self._epsf.get(key)
        if got is not None:
            return got
        lay = self.headers(url, until="EPSF")
        start, h, size = lay["EPSF"]
        raw = self.get(url, start, size)
        prim = fits.PrimaryHDU().header.tostring().encode()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            hd = fits.open(io.BytesIO(prim + h.tostring().encode() + raw))
            e = EPSF.from_hdu(hd[1])
        with self._lock:
            self._epsf[key] = e
        return e


def _end_of_header(raw: bytes) -> int | None:
    for i in range(0, len(raw) - BLOCK + 1, BLOCK):
        blk = raw[i:i + BLOCK]
        if any(blk[j:j + 8] == b"END     " for j in range(0, BLOCK, 80)):
            return i + BLOCK
    return None


@dataclass
class Stamp:
    """One photo's pixels around a source."""

    key: str
    det: int
    visit: int
    mjd: float
    collection: str
    data: np.ndarray        # (2h+1)², MJy/sr
    var: np.ndarray
    flags: np.ndarray
    ix0: int                # detector pixel of the stamp's corner (0-based)
    iy0: int
    x: float                # the source's position from the WCS (0-based detector pixels)
    y: float
    A: np.ndarray           # 2 × 2: pixel offset per arcsec (east, north)
    wcs: object = field(repr=False, default=None)


def read_stamp(reader: Reader, url: str, ra: float, dec: float, half: int = FIT_HALF_PX, **meta) -> Stamp | None:
    """The stamp around (ra, dec) in one photo, or None when it misses the detector."""
    from astropy.wcs import FITSFixedWarning, WCS

    lay = reader.headers(url)
    istart, ih, _ = lay["IMAGE"]
    fstart, fh, _ = lay["FLAGS"]
    vstart, vh, _ = lay["VARIANCE"]
    nx, ny = int(ih["NAXIS1"]), int(ih["NAXIS2"])
    if int(ih.get("BITPIX", 0)) != -32 or int(vh.get("BITPIX", 0)) != -32:
        raise ValueError("IMAGE/VARIANCE not float32")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FITSFixedWarning)
        w = WCS(ih).celestial
    x, y = (float(v) for v in w.world_to_pixel_values(ra, dec))
    cosd = math.cos(math.radians(dec))
    xe, ye = (float(v) for v in w.world_to_pixel_values(ra + 1.0 / 3600.0 / cosd, dec))
    xn, yn = (float(v) for v in w.world_to_pixel_values(ra, dec + 1.0 / 3600.0))
    A = np.array([[xe - x, xn - x], [ye - y, yn - y]])
    cx, cy = int(round(x)), int(round(y))
    if cx - half < 0 or cy - half < 0 or cx + half >= nx or cy + half >= ny:
        return None
    y0, y1 = cy - half, cy + half + 1
    x0, x1 = cx - half, cx + half + 1
    data = reader.float_rows(url, istart, nx, y0, y1)[:, x0:x1]
    var = reader.float_rows(url, vstart, nx, y0, y1)[:, x0:x1]
    flags = reader.flag_rows(url, fstart, fh, nx, y0, y1)[:, x0:x1]
    return Stamp(data=data, var=var, flags=flags, ix0=x0, iy0=y0, x=x, y=y, A=A, wcs=w, **meta)


# --- the fit ----------------------------------------------------------------------------------------
@dataclass
class Group:
    """The stamps of one detector, stacked for vectorised fits."""

    det: int
    epsf: object
    idx: np.ndarray          # rows of the source's stamp list
    D: np.ndarray            # (K, N)
    var: np.ndarray
    good: np.ndarray
    px: np.ndarray           # (K, N) detector pixel centres
    py: np.ndarray
    zone: np.ndarray         # (K,) ePSF zone at the source
    A: np.ndarray            # (K, 2, 2)
    nb: np.ndarray           # (K, M, 2) neighbour pixel offsets from the primary (NaN = none)
    W: np.ndarray | None = None
    x1: np.ndarray | None = None  # one-source positions
    y1: np.ndarray | None = None


class TabPSF:
    """Some zones of an ePSF (skyshift_core.level2.EPSF) tabulated once on a 1/FIT_PSF_OS px grid, value and
    gradient from core's spline, read back bilinearly: the fit's inner loop evaluates it millions of times. `zone`
    arguments are rows of `zones` (the table), not the EPSF's zone numbers."""

    def __init__(self, epsf, zones, os: int = FIT_PSF_OS, reach: float = 3.4) -> None:
        self.os, self.reach = os, reach
        t = np.arange(-reach, reach + 1e-9, 1.0 / os)
        self.n = len(t)
        dx, dy = np.meshgrid(t, t)  # [iy, ix]
        tabs = []
        for z in zones:
            v, gx, gy = epsf.evaluate_grad(dx, dy, np.full(dx.shape, int(z)))
            tabs.append(np.stack([v, gx, gy]))
        self.tab = np.ascontiguousarray(np.stack(tabs)).ravel()  # (Z, 3, n, n)

    def evaluate_grad(self, dx, dy, zone):
        n, os = self.n, self.os
        u = (np.asarray(dx, np.float64) + self.reach) * os
        v = (np.asarray(dy, np.float64) + self.reach) * os
        inside = (u >= 0) & (u <= n - 1) & (v >= 0) & (v <= n - 1)
        iu = np.clip(np.floor(u), 0, n - 2).astype(np.int64)
        iv = np.clip(np.floor(v), 0, n - 2).astype(np.int64)
        fu, fv = np.clip(u - iu, 0, 1), np.clip(v - iv, 0, 1)
        w00, w01, w10, w11 = (1 - fu) * (1 - fv), fu * (1 - fv), (1 - fu) * fv, fu * fv
        base = np.asarray(zone, np.int64) * (3 * n * n) + iv * n + iu
        out = []
        for k in range(3):
            b = base + k * n * n
            t = self.tab
            val = w00 * t.take(b) + w01 * t.take(b + 1) + w10 * t.take(b + n) + w11 * t.take(b + n + 1)
            out.append(np.where(inside, val, 0.0))
        return tuple(out)

    def evaluate(self, dx, dy, zone):
        return self.evaluate_grad(dx, dy, zone)[0]


def _psf(g: Group, dx: np.ndarray, dy: np.ndarray, grad: bool):
    z = np.broadcast_to(g.zone[:, None], dx.shape)
    if grad:
        return g.epsf.evaluate_grad(dx, dy, z)
    return (g.epsf.evaluate(dx, dy, z),)


def _fit_group(g: Group, u: np.ndarray | None, W: np.ndarray, x1: np.ndarray, y1: np.ndarray, n_iter: int,
               want_cov: bool = False):
    """Gauss–Newton on (x1, y1) with the fluxes solved linearly, per photo. u: (K, 2) companion pixel offset from
    the primary or None (one source). Returns chi2 (K,), F (K, L), x1, y1 and, with want_cov, the fluxes' variances."""
    K, N = g.D.shape
    M = g.nb.shape[1]
    has_nb = np.isfinite(g.nb[:, :, 0])
    x1, y1 = x1.copy(), y1.copy()
    comps = 1 + (u is not None) + M
    for it in range(n_iter + 1):
        cols, gx, gy = [], [], []
        offs = [np.zeros((K, 2))]
        if u is not None:
            offs.append(u)
        for m in range(M):
            offs.append(np.where(has_nb[:, m, None], np.nan_to_num(g.nb[:, m]), 1e3))
        for o in offs:
            dx = g.px - (x1 + o[:, 0])[:, None]
            dy = g.py - (y1 + o[:, 1])[:, None]
            P, Gx, Gy = _psf(g, dx, dy, True)
            cols.append(P)
            gx.append(Gx)
            gy.append(Gy)
        X = np.stack(cols + [np.ones((K, N))], axis=2)  # (K, N, L)
        Lc = X.shape[2]
        XtW = X * W[:, :, None]
        NM = np.einsum("knl,knm->klm", XtW, X)
        # a component with no light in the stamp (an absent neighbour, one beyond the edge): a zero column, kept
        # solvable with a unit ridge (its flux comes out 0)
        dg = np.diagonal(NM, axis1=1, axis2=2)
        ridge = (dg <= 1e-9 * dg.max(axis=1, keepdims=True)).astype(np.float64)
        NM = NM + ridge[:, :, None] * np.eye(Lc)[None]
        b = np.einsum("knl,kn->kl", XtW, g.D)
        F = np.linalg.solve(NM, b[:, :, None])[:, :, 0]
        r = g.D - np.einsum("knl,kl->kn", X, F)
        if it == n_iter:
            break
        # position step: the whole scene moves with the primary (the derivative of a pixel value w.r.t. a source
        # moving +δ is −δ ∂P/∂dx)
        jx = -sum(F[:, c, None] * gx[c] for c in range(comps))
        jy = -sum(F[:, c, None] * gy[c] for c in range(comps))
        Xf = np.concatenate([jx[:, :, None], jy[:, :, None], X], axis=2)
        XfW = Xf * W[:, :, None]
        NF = np.einsum("knl,knm->klm", XfW, Xf)
        NF[:, 2:, 2:] += ridge[:, :, None] * np.eye(Lc)[None]
        bf = np.einsum("knl,kn->kl", XfW, r)
        try:
            step = np.linalg.solve(NF, bf[:, :, None])[:, :, 0]
        except np.linalg.LinAlgError:
            step = np.zeros((K, Lc + 2))
        step = np.nan_to_num(step)
        x1 += np.clip(step[:, 0], -FIT_MAX_STEP_PX, FIT_MAX_STEP_PX)
        y1 += np.clip(step[:, 1], -FIT_MAX_STEP_PX, FIT_MAX_STEP_PX)
    chi2 = np.sum(W * r * r, axis=1)
    out = {"chi2": chi2, "F": F, "x1": x1, "y1": y1, "X": X}
    if want_cov:
        cov = np.linalg.inv(NM)
        out["varF"] = np.diagonal(cov, axis1=1, axis2=2)
    return out


def _chi2_many(groups: list[Group], deltas: np.ndarray, n_iter: int, sel: list[np.ndarray] | None = None,
               max_points: int = 600_000, with_snr: bool = False):
    """Profile χ² of the two-source model with the companion at each sky offset of deltas ((B, 2), east, north ″):
    the photos are tiled over a block of offsets so one vectorised fit does them all. with_snr: also the companion's
    combined S/N per offset, Σ_det z / √n_det with z = ΣF2 / √ΣVar(F2) per detector (≤ 0: no light there, a
    PSF-shape correction rather than an object)."""
    deltas = np.atleast_2d(np.asarray(deltas, np.float64))
    tot = np.zeros(len(deltas))
    zsum, zn = np.zeros(len(deltas)), np.zeros(len(deltas))
    for gi, g in enumerate(groups):
        k = np.arange(len(g.idx)) if sel is None else sel[gi]
        if len(k) == 0:
            continue
        sub = _subset(g, k)
        K, N = sub.D.shape
        bs = max(1, max_points // (K * N))
        for s in range(0, len(deltas), bs):
            d = deltas[s:s + bs]
            b = len(d)
            t = Group(sub.det, sub.epsf, np.tile(sub.idx, b), np.tile(sub.D, (b, 1)), np.tile(sub.var, (b, 1)),
                      np.tile(sub.good, (b, 1)), np.tile(sub.px, (b, 1)), np.tile(sub.py, (b, 1)), np.tile(sub.zone, b),
                      np.tile(sub.A, (b, 1, 1)), np.tile(sub.nb, (b, 1, 1)))
            u = np.einsum("kij,bj->bki", sub.A, d).reshape(b * K, 2)
            r = _fit_group(t, u, np.tile(sub.W, (b, 1)), np.tile(sub.x1, b), np.tile(sub.y1, b), n_iter,
                           want_cov=with_snr)
            tot[s:s + b] += r["chi2"].reshape(b, K).sum(axis=1)
            if with_snr:
                f2 = r["F"][:, 1].reshape(b, K).sum(axis=1)
                v2 = r["varF"][:, 1].reshape(b, K).sum(axis=1)
                zsum[s:s + b] += f2 / np.sqrt(np.maximum(v2, 1e-30))
                zn[s:s + b] += 1
    if with_snr:
        return tot, np.where(zn > 0, zsum / np.sqrt(np.maximum(zn, 1)), np.nan)
    return tot


def _chi2_at(groups: list[Group], delta: np.ndarray, n_iter: int, sel: list[np.ndarray] | None = None) -> float:
    """Profile χ² of the two-source model with the companion at sky offset delta (east, north ″)."""
    return float(_chi2_many(groups, np.asarray(delta, np.float64)[None], n_iter, sel)[0])


def _subset(g: Group, k: np.ndarray) -> Group:
    if len(k) == len(g.idx) and np.all(k == np.arange(len(g.idx))):
        return g
    return Group(g.det, g.epsf, g.idx[k], g.D[k], g.var[k], g.good[k], g.px[k], g.py[k], g.zone[k], g.A[k], g.nb[k],
                 g.W[k], g.x1[k], g.y1[k])


def _search(groups: list[Group], sel=None, grid: float = FIT_GRID_ARCSEC, step: float = FIT_GRID_STEP_ARCSEC):
    """Grid search over the companion's offset (FIT_GRID_ITER steps per point, on at most FIT_GRID_PHOTOS photos per
    detector spread over time), then two finer grids with every photo (0.6″, then 0.15″; FIT_ITER steps) around the
    best point, and around the best one more than 4″ from it when that one is nearly as good (within a tenth of the
    best's gain over the median). Two sources at x1 and x1 + Δ fit as well as at x1 + Δ and x1 with the fluxes
    swapped: when the companion comes out brighter (geometric mean of its per-detector flux ratios > 1), −Δ is
    refined too and kept if it fits as well (the brighter one is the primary). Returns (best delta, chi2 at it,
    coarse grid table: east, north, chi2, snr). Only offsets of at least FIT_MIN_SEP_ARCSEC where the companion
    gives light (combined S/N > 0) count: a negative "companion" only reshapes the PSF of a bright star."""
    v = np.arange(-grid, grid + 1e-9, step)
    E, Nn = np.meshgrid(v, v)
    r = np.hypot(E, Nn)
    pts = np.column_stack([E[(r >= FIT_MIN_SEP_ARCSEC) & (r <= grid)], Nn[(r >= FIT_MIN_SEP_ARCSEC) & (r <= grid)]])
    coarse = []
    for gi, g in enumerate(groups):
        k = np.arange(len(g.idx)) if sel is None else np.asarray(sel[gi])
        if len(k) > FIT_GRID_PHOTOS:
            k = k[np.unique(np.linspace(0, len(k) - 1, FIT_GRID_PHOTOS).round().astype(int))]
        coarse.append(k)
    chi, snr = _chi2_many(groups, pts, FIT_GRID_ITER, coarse, with_snr=True)
    eff = np.where(snr > 0, chi, np.inf)  # a companion gives light: a negative one only reshapes the PSF
    if not np.isfinite(eff).any():
        eff = chi
    order = np.argsort(eff)
    starts = [pts[order[0]]]
    far = [i for i in order[1:] if np.hypot(*(pts[i] - starts[0])) > 4.0]
    if far and eff[far[0]] - eff[order[0]] < 0.1 * (np.median(chi) - eff[order[0]]):
        starts.append(pts[far[0]])
    f, x = min((_refine(groups, b, sel) for b in starts), key=lambda t: t[0])
    if not np.isfinite(f):  # no offset with a positive companion: the best grid point, as it is (no split)
        x = np.asarray(starts[0], np.float64)
        f = _chi2_at(groups, x, FIT_ITER, sel)
    elif _companion_brighter(groups, x, sel):
        f2, x2 = _refine(groups, -x, sel)
        if f2 <= f + max(10.0, 2e-4 * abs(f)):
            f, x = f2, x2
    return np.asarray(x), f, pd.DataFrame({"east": pts[:, 0], "north": pts[:, 1], "chi2": chi, "snr": snr})


def _refine(groups: list[Group], b: np.ndarray, sel=None) -> tuple[float, np.ndarray]:
    """5 × 5 grids at 0.6″, then 0.15″, around b (FIT_ITER steps, every selected photo), offsets of at least
    FIT_MIN_SEP_ARCSEC where the companion gives light (combined S/N > 0): (χ², best offset); (inf, b) if no
    point qualifies."""
    b = np.asarray(b, np.float64)
    best = float("inf")
    for h in (0.6, 0.15):
        o = np.arange(-2, 3) * h
        oe, on = np.meshgrid(o, o)
        fine = b + np.column_stack([oe.ravel(), on.ravel()])
        fine = fine[np.hypot(fine[:, 0], fine[:, 1]) >= FIT_MIN_SEP_ARCSEC]
        if not len(fine):
            break
        fc, snr = _chi2_many(groups, fine, FIT_ITER, sel, with_snr=True)
        fc = np.where(snr > 0, fc, np.inf)
        if not np.isfinite(fc).any():
            break
        i = int(np.argmin(fc))
        b, best = fine[i], float(fc[i])
    return best, b


def _companion_brighter(groups: list[Group], delta: np.ndarray, sel=None) -> bool:
    """At this offset, is the companion brighter than the primary (geometric mean of ΣF2 / ΣF1 over detectors > 1)?"""
    logs = []
    for gi, g in enumerate(groups):
        k = np.arange(len(g.idx)) if sel is None else np.asarray(sel[gi])
        if not len(k):
            continue
        sub = _subset(g, k)
        u = np.einsum("kij,j->ki", sub.A, np.asarray(delta, np.float64))
        F = _fit_group(sub, u, sub.W, sub.x1, sub.y1, FIT_ITER)["F"]
        f1, f2 = float(F[:, 0].sum()), float(F[:, 1].sum())
        if f1 > 0 and f2 > 0:
            logs.append(math.log(f2 / f1))
    return bool(logs) and float(np.mean(logs)) > 0


def _curvature_err(groups: list[Group], delta: np.ndarray, chi0: float, sel=None, h: float = 0.4) -> np.ndarray:
    """2 × 2 covariance of delta from the χ² curvature (finite differences, h arcsec)."""
    e1, e2 = np.array([h, 0.0]), np.array([0.0, h])
    fpp = _chi2_many(groups, np.stack([delta + e1, delta - e1, delta + e2, delta - e2, delta + e1 + e2, delta - e1 - e2]),
                     FIT_ITER, sel)
    H11 = (fpp[0] - 2 * chi0 + fpp[1]) / h ** 2
    H22 = (fpp[2] - 2 * chi0 + fpp[3]) / h ** 2
    H12 = (fpp[4] + fpp[5] - fpp[0] - fpp[1] - fpp[2] - fpp[3] + 2 * chi0) / (2 * h ** 2)
    H = 0.5 * np.array([[H11, H12], [H12, H22]])
    try:
        cov = np.linalg.inv(H)
        if not np.all(np.isfinite(cov)) or np.any(np.diag(cov) <= 0):
            raise np.linalg.LinAlgError
        return cov
    except np.linalg.LinAlgError:
        return np.full((2, 2), np.nan)


def _neighbours(refcat: pd.DataFrame | None, ra: float, dec: float, mjd: float, primary: tuple[float, float],
                radius: float) -> np.ndarray:
    """(M, 2) catalogue neighbours' (east, north) ″ offsets from the primary at mjd, within a ±radius box: Gaia (moved
    by its pm) no more than FIT_NEIGHBOUR_DMAG fainter than the primary in G, CatWISE entries more than 3″ from every
    Gaia star likewise in W1 (no primary in that catalogue: G ≤ 19, W1 ≤ 16); the primary's own entries (within 3″)
    left out; at most FIT_NEIGHBOUR_MAX, nearest first."""
    if refcat is None or not len(refcat):
        return np.zeros((0, 2))
    from skyshift_core import tangent_offset_arcsec

    d = (radius + 10) / 3600.0
    cosd = math.cos(math.radians(dec))
    r = refcat[(np.abs(refcat["dec"] - dec) < d) & (np.abs((refcat["ra"] - ra + 180) % 360 - 180) * cosd < d)]
    r = r[r["cat"].astype(str).isin(["gaia", "catwise"])]
    if not len(r):
        return np.zeros((0, 2))
    ep = pd.to_numeric(r["epoch_mjd"], errors="coerce").fillna(57388.5).to_numpy(np.float64)
    dt = (mjd - ep) / 365.25
    pmra = np.nan_to_num(pd.to_numeric(r["pmra"], errors="coerce").to_numpy(np.float64))
    pmdec = np.nan_to_num(pd.to_numeric(r["pmdec"], errors="coerce").to_numpy(np.float64))
    rra = r["ra"].to_numpy(np.float64) + pmra * dt / 3.6e6 / np.cos(np.radians(r["dec"].to_numpy(np.float64)))
    rdec = r["dec"].to_numpy(np.float64) + pmdec * dt / 3.6e6
    e, n = tangent_offset_arcsec(primary[0], primary[1], rra, rdec)
    gaia = (r["cat"].astype(str) == "gaia").to_numpy()
    sep = np.hypot(e, n)
    own = sep <= 3.0  # the primary's own entries
    keep = ~own
    if (~gaia).any() and gaia.any():  # a CatWISE entry of a Gaia star is the same object
        ge, gn = e[gaia], n[gaia]
        dmin = np.min(np.hypot(e[~gaia, None] - ge[None], n[~gaia, None] - gn[None]), axis=1)
        keep[np.flatnonzero(~gaia)[dmin <= 3.0]] = False
    G = pd.to_numeric(r["g_mag"], errors="coerce").to_numpy(np.float64) if "g_mag" in r else np.full(len(r), np.nan)
    W1 = pd.to_numeric(r["w1_mag"], errors="coerce").to_numpy(np.float64) if "w1_mag" in r else np.full(len(r), np.nan)
    gp = np.nanmin(np.where(own & gaia, G, np.nan)) if (own & gaia & np.isfinite(G)).any() else FIT_NEIGHBOUR_G0 - FIT_NEIGHBOUR_DMAG
    wp = np.nanmin(np.where(own & ~gaia, W1, np.nan)) if (own & ~gaia & np.isfinite(W1)).any() else FIT_NEIGHBOUR_W10 - FIT_NEIGHBOUR_DMAG
    with np.errstate(invalid="ignore"):
        bright = np.where(gaia, ~(G > gp + FIT_NEIGHBOUR_DMAG), ~(W1 > wp + FIT_NEIGHBOUR_DMAG))
    keep &= bright & (np.abs(e) <= radius) & (np.abs(n) <= radius)
    k = np.flatnonzero(keep)
    k = k[np.argsort(sep[k])][:FIT_NEIGHBOUR_MAX]
    return np.column_stack([e[k], n[k]])


@dataclass
class SourceFit:
    source_id: int
    n_photos: int = 0
    n_dets: int = 0
    chi2_one: float = float("nan")
    chi2_two: float = float("nan")
    dchi2: float = float("nan")
    npix: int = 0
    east: float = float("nan")
    north: float = float("nan")
    cov: np.ndarray = field(default_factory=lambda: np.full((2, 2), np.nan))
    ra: float = float("nan")          # companion
    dec: float = float("nan")
    primary_ra: float = float("nan")
    primary_dec: float = float("nan")
    ratio: dict = field(default_factory=dict)      # det -> F2/F1
    ratio_err: dict = field(default_factory=dict)
    snr: dict = field(default_factory=dict)        # det -> companion S/N
    flux_ujy: dict = field(default_factory=dict)   # det -> companion mean flux
    visits: list = field(default_factory=list)     # per visit: {visit, n, mjd, east, north, err, dchi2}
    moves: bool | None = None
    move_arcsec: float = float("nan")
    move_sigma: float = float("nan")
    grid: pd.DataFrame | None = field(default=None, repr=False)
    groups: list = field(default_factory=list, repr=False)
    stamps: list = field(default_factory=list, repr=False)
    catalog: str = ""                # Gaia / CatWISE / 2MASS entry at the companion ("" = none)
    error: str | None = None

    @property
    def sep(self) -> float:
        return float(math.hypot(self.east, self.north))

    @property
    def pa(self) -> float:
        return float(math.degrees(math.atan2(self.east, self.north)) % 360.0)

    @property
    def sep_err(self) -> float:
        if not np.all(np.isfinite(self.cov)) or self.sep == 0:
            return float("nan")
        u = np.array([self.east, self.north]) / self.sep
        return float(math.sqrt(max(u @ self.cov @ u, 0.0)))

    @property
    def split(self) -> bool:
        """The fit separates a second object: Δχ² (FIT_DCHI2_MIN, FIT_DCHI2_PER_PHOTO_MIN), its S/N in some detector
        (FIT_COMPANION_SNR_MIN) and in none negative (FIT_COMPANION_NEG_SNR), inside the search (not on its inner edge
        or beyond its radius) with a finite position error."""
        if not (np.isfinite(self.dchi2) and self.n_photos and np.isfinite(self.east)):
            return False
        return _passes(self.dchi2, self.n_photos, self.snr, self.sep, self.cov)


def build_groups(stamps: list[Stamp], epsfs: dict, nb_offsets: np.ndarray, half: int = FIT_HALF_PX) -> list[Group]:
    """Stack the stamps per detector; nb_offsets (M, 2): neighbours' (east, north) ″ from the primary."""
    groups = []
    k = np.arange(2 * half + 1)  # pixel index inside the stamp (ix0, iy0 = its corner)
    for det in sorted({s.det for s in stamps}):
        idx = np.array([i for i, s in enumerate(stamps) if s.det == det])
        ss = [stamps[i] for i in idx]
        D = np.stack([s.data.ravel() for s in ss]).astype(np.float64)
        var = np.stack([s.var.ravel() for s in ss]).astype(np.float64)
        fl = np.stack([s.flags.ravel() for s in ss]).astype(np.int64) & 0xFFFFFFFF
        good = np.isfinite(D) & np.isfinite(var) & (var > 0) & ((fl & MASK_BITS) == 0)
        D = np.where(good, D, 0.0)
        var = np.where(good, var, 1.0)
        px = np.stack([(s.ix0 + k[None, :] + 0 * k[:, None]).ravel() for s in ss]).astype(np.float64)
        py = np.stack([(s.iy0 + k[:, None] + 0 * k[None, :]).ravel() for s in ss]).astype(np.float64)
        zones = np.array([int(epsfs[det].zone([s.x], [s.y])[0]) for s in ss])
        uz, zone = np.unique(zones, return_inverse=True)
        e = TabPSF(epsfs[det], uz)
        A = np.stack([s.A for s in ss])
        if len(nb_offsets):
            nb = np.einsum("kij,mj->kmi", A, nb_offsets)
            far = (np.abs(nb[:, :, 0]) > FIT_NEIGHBOUR_PX) | (np.abs(nb[:, :, 1]) > FIT_NEIGHBOUR_PX)
            nb[far] = np.nan
            keep = np.isfinite(nb[:, :, 0]).any(axis=0)
            nb = nb[:, keep]
        else:
            nb = np.zeros((len(ss), 0, 2))
        groups.append(Group(det, e, idx, D, var, good, px, py, zone, A, nb))
    return groups


def fit_source(stamps: list[Stamp], epsfs: dict, nb_offsets: np.ndarray, source_id: int = -1,
               primary_radec: tuple[float, float] | None = None, visits: bool = True,
               min_good: int = FIT_MIN_GOOD_PIX) -> SourceFit:
    """One against two sources on the stamps of one source (see the module doc)."""
    from skyshift_core.astrometry import tangent_deproject

    out = SourceFit(int(source_id))
    stamps = [s for s in stamps if s is not None]
    groups = build_groups(stamps, epsfs, nb_offsets)
    # photos with too few good pixels out
    keep_groups = []
    for g in groups:
        ok = np.flatnonzero(g.good.sum(axis=1) >= min_good)
        if len(ok):
            g2 = Group(g.det, g.epsf, g.idx[ok], g.D[ok], g.var[ok], g.good[ok], g.px[ok], g.py[ok], g.zone[ok],
                       g.A[ok], g.nb[ok])
            keep_groups.append(g2)
    groups = keep_groups
    if not groups:
        out.error = "no usable photos"
        return out
    # one source: positions from the WCS, weights 1/VARIANCE, then with the model term, refitted
    for g in groups:
        x0 = np.array([stamps[i].x for i in g.idx])
        y0 = np.array([stamps[i].y for i in g.idx])
        W0 = np.where(g.good, 1.0 / g.var, 0.0)
        r = _fit_group(g, None, W0, x0, y0, FIT_ITER)
        Fp = np.maximum(r["F"][:, 0], 0.0)
        P = r["X"][:, :, 0]
        g.W = np.where(g.good, 1.0 / (g.var + (FIT_MODEL_FRAC * Fp[:, None] * P) ** 2), 0.0)
        r = _fit_group(g, None, g.W, r["x1"], r["y1"], FIT_ITER)
        g.x1, g.y1 = r["x1"], r["y1"]
        g.one = r
    out.groups, out.stamps = groups, stamps
    out.n_photos = int(sum(len(g.idx) for g in groups))
    out.n_dets = len(groups)
    out.npix = int(sum(g.good.sum() for g in groups))
    out.chi2_one = float(sum(g.one["chi2"].sum() for g in groups))
    delta, chi2, grid = _search(groups)
    out.grid = grid
    m = _measure(groups, delta, chi2, out.chi2_one, keep=True)
    if not m["valid"]:  # e.g. a bright star's ePSF error near the core won: the best other offset, if it passes
        alt = _second_offset(groups, grid, delta)
        if alt is not None:
            m2 = _measure(groups, alt[0], alt[1], out.chi2_one, keep=True)
            if m2["valid"]:
                m = m2
            else:
                _det_ratios(groups, m["delta"], keep=True)  # the fits at the best offset again
    out.east, out.north = float(m["delta"][0]), float(m["delta"][1])
    out.chi2_two = m["chi2"]
    out.dchi2 = out.chi2_one - m["chi2"]
    out.cov = m["cov"]
    for det, (ratio, err, flux) in m["ratio"].items():
        out.ratio[det], out.ratio_err[det] = ratio, err
        out.snr[det] = ratio / err if err > 0 else float("nan")
        out.flux_ujy[det] = flux
    # the primary's sky position: its fitted pixel position through each photo's WCS (two-source fit)
    pr, pd_ = [], []
    for g in groups:
        for j, i in enumerate(g.idx):
            a, b = stamps[i].wcs.pixel_to_world_values(g.two["x1"][j], g.two["y1"][j])
            pr.append(float(a))
            pd_.append(float(b))
    pr, pd_ = np.asarray(pr), np.asarray(pd_)
    ra0 = float(np.median(pr))
    out.primary_ra = float((ra0 + np.median((pr - ra0 + 180) % 360 - 180)) % 360)
    out.primary_dec = float(np.median(pd_))
    cra, cdec = tangent_deproject(out.primary_ra, out.primary_dec, out.east, out.north)
    out.ra, out.dec = float(cra) % 360.0, float(cdec)
    if visits:
        _visit_fits(out, groups, stamps)
    return out


def _measure(groups: list[Group], delta: np.ndarray, chi2: float, chi2_one: float, sel=None,
             keep: bool = False) -> dict:
    """The companion at sky offset delta (χ² chi2 there) on the selected photos: its covariance (curvature × χ²_ν),
    per-detector flux ratio, error and mean flux (µJy), and whether it passes the split rule (`valid`, as
    SourceFit.split)."""
    ks = [np.arange(len(g.idx)) if sel is None else np.asarray(sel[gi]) for gi, g in enumerate(groups)]
    n = int(sum(len(k) for k in ks))
    npix = int(sum(g.good[k].sum() for g, k in zip(groups, ks)))
    nu = max(1.0, chi2 / max(npix - n * 5, 1))
    cov = _curvature_err(groups, delta, chi2, sel) * nu
    ratio = {det: (r, e, float(fit["F"][:, 1].mean() * UJY_PER_MJYSR))
             for det, (r, e, fit) in _det_ratios(groups, delta, sel, keep).items()}
    snr = {det: r / e if e > 0 else float("nan") for det, (r, e, _) in ratio.items()}
    return {"delta": np.asarray(delta, np.float64), "chi2": float(chi2), "cov": cov, "ratio": ratio,
            "valid": _passes(chi2_one - chi2, n, snr, float(np.hypot(*delta)), cov)}


def _passes(dchi2: float, n_photos: int, snr: dict, sep: float, cov: np.ndarray) -> bool:
    """The split rule: FIT_DCHI2_MIN and FIT_DCHI2_PER_PHOTO_MIN, S/N FIT_COMPANION_SNR_MIN in some detector and
    above FIT_COMPANION_NEG_SNR in every one, inside the search (FIT_MIN_SEP_ARCSEC + FIT_EDGE_ARCSEC to
    FIT_GRID_ARCSEC) with a finite position error."""
    v = [x for x in snr.values() if np.isfinite(x)]
    return bool(np.isfinite(dchi2) and n_photos and dchi2 >= FIT_DCHI2_MIN
                and dchi2 / n_photos >= FIT_DCHI2_PER_PHOTO_MIN
                and max(v, default=0.0) >= FIT_COMPANION_SNR_MIN and min(v, default=0.0) >= FIT_COMPANION_NEG_SNR
                and FIT_MIN_SEP_ARCSEC + FIT_EDGE_ARCSEC <= sep <= FIT_GRID_ARCSEC and np.all(np.isfinite(cov)))


def _second_offset(groups: list[Group], grid: pd.DataFrame, delta: np.ndarray, sel=None):
    """The best coarse-grid offset with light (snr > 0) more than 4″ from delta and clear of the inner edge,
    refined: (offset, χ²), or None."""
    if grid is None or not len(grid):
        return None
    e, n = grid["east"].to_numpy(), grid["north"].to_numpy()
    ok = ((grid["snr"].to_numpy() > 0) & (np.hypot(e - delta[0], n - delta[1]) > 4.0)
          & (np.hypot(e, n) >= FIT_MIN_SEP_ARCSEC + FIT_EDGE_ARCSEC))
    if not ok.any():
        return None
    i = np.flatnonzero(ok)[int(np.argmin(grid["chi2"].to_numpy()[ok]))]
    f, x = _refine(groups, np.array([e[i], n[i]]), sel)
    return (x, f) if np.isfinite(f) else None


def _ratio(F1: np.ndarray, F2: np.ndarray, var2: np.ndarray) -> tuple[float, float]:
    """A detector's companion / primary flux ratio: the per-photo F2 / F1 averaged with weights F1² / Var(F2), and
    its error, the larger of the formal one and the photos' scatter about the mean (a photo-to-photo ePSF error
    that a free F2 soaks up scatters; a real companion's ratio holds still). (nan, nan) without photos."""
    ok = (F1 > 0) & np.isfinite(F2) & (var2 > 0)
    if not ok.any():
        return float("nan"), float("nan")
    rho = F2[ok] / F1[ok]
    w = F1[ok] ** 2 / var2[ok]
    m = float(np.sum(w * rho) / np.sum(w))
    formal = float(1.0 / math.sqrt(np.sum(w)))
    n = int(ok.sum())
    if n >= 3:
        scatter = float(math.sqrt(np.sum(w * (rho - m) ** 2) / np.sum(w) / (n - 1)))
    else:
        scatter = 0.0
    return m, max(formal, scatter)


def _det_ratios(groups: list[Group], delta: np.ndarray, sel=None, keep: bool = False) -> dict:
    """det -> (flux ratio, error) of the companion at sky offset delta on the selected photos (_ratio); keep: store
    the fit on each group as g.two (every photo)."""
    out = {}
    for gi, g in enumerate(groups):
        k = np.arange(len(g.idx)) if sel is None else np.asarray(sel[gi])
        if not len(k):
            continue
        sub = _subset(g, k)
        u = np.einsum("kij,j->ki", sub.A, np.asarray(delta, np.float64))
        r = _fit_group(sub, u, sub.W, sub.x1, sub.y1, FIT_ITER, want_cov=True)
        if keep:
            g.two = r
        dof = max(int(sub.good.sum()) - len(k) * (r["F"].shape[1] + 2), 1)
        s2 = max(1.0, float(r["chi2"].sum()) / dof)
        out[g.det] = (*_ratio(r["F"][:, 0], r["F"][:, 1], r["varF"][:, 1] * s2), r)
    return out


def _visit_fits(out: SourceFit, groups: list[Group], stamps: list[Stamp]) -> None:
    """Δ, its error, Δχ² and the per-detector flux ratios on each visit's photos alone (≥ FIT_VISIT_MIN_PHOTOS);
    `moves` from the first and last such visit. A companion that moves is fitted at each visit's own offset: its
    flux ratios and S/N become the visits' inverse-variance means and Δχ² the visits' sum."""
    vis = sorted({stamps[i].visit for g in groups for i in g.idx})
    rows = []
    for v in vis:
        sel = [np.flatnonzero(np.array([stamps[i].visit == v for i in g.idx])) for g in groups]
        n = int(sum(len(s) for s in sel))
        mjd = float(np.mean([stamps[i].mjd for g, s in zip(groups, sel) for i in g.idx[s]])) if n else float("nan")
        if n < FIT_VISIT_MIN_PHOTOS:
            rows.append({"visit": int(v), "n": n, "mjd": mjd, "east": float("nan"), "north": float("nan"),
                         "err": float("nan"), "dchi2": float("nan")})
            continue
        one = float(sum(g.one["chi2"][s].sum() for g, s in zip(groups, sel)))
        d, chi2, _ = _search(groups, sel)
        cands = [_measure(groups, d, chi2, one, sel)]
        if np.isfinite(out.east):  # the overall offset, refined on this visit: a fixed companion is there
            f, x = _refine(groups, np.array([out.east, out.north]), sel)
            if np.isfinite(f) and np.hypot(*(x - d)) > 1.0:
                cands.append(_measure(groups, x, f, one, sel))
        good = [c for c in cands if c["valid"]]
        m = min(good, key=lambda c: c["chi2"]) if good else cands[-1]
        cov = m["cov"]
        err = float(math.sqrt(max(np.trace(cov) / 2, 0))) if np.all(np.isfinite(cov)) else float("nan")
        rows.append({"visit": int(v), "n": n, "mjd": mjd, "east": float(m["delta"][0]),
                     "north": float(m["delta"][1]), "err": err, "dchi2": one - m["chi2"], "cov": cov,
                     "valid": m["valid"], "ratio": {det: (r, e) for det, (r, e, _) in m["ratio"].items()}})
    out.visits = rows
    ok = [r for r in rows if np.isfinite(r["east"]) and np.isfinite(r["err"])]
    if len(ok) >= 2:
        a, b = ok[0], ok[-1]
        dd = math.hypot(a["east"] - b["east"], a["north"] - b["north"])
        s = math.hypot(a["err"], b["err"])
        out.move_arcsec = dd
        out.move_sigma = dd / s if s > 0 else float("nan")
        out.moves = bool(dd >= FIT_MOVE_MIN_ARCSEC and (not np.isfinite(out.move_sigma) or out.move_sigma >= FIT_MOVE_SIGMA))
    if out.moves:
        fitted = [r for r in rows if np.isfinite(r["east"])]
        out.dchi2 = float(sum(r["dchi2"] for r in fitted))
        out.chi2_two = out.chi2_one - out.dchi2
        for det in out.ratio:
            pairs = [r["ratio"][det] for r in fitted if det in r["ratio"] and r["ratio"][det][1] > 0]
            if pairs:
                w = np.array([1.0 / e ** 2 for _, e in pairs])
                m = float(np.sum(w * np.array([x for x, _ in pairs])) / w.sum())
                out.ratio[det], out.ratio_err[det] = m, float(1.0 / math.sqrt(w.sum()))
                out.snr[det] = m / out.ratio_err[det]


# --- candidates ---------------------------------------------------------------------------------------
def photos_of(members: pd.DataFrame, photolist: pd.DataFrame, source_id: int, max_photos: int = FIT_MAX_PHOTOS) -> pd.DataFrame:
    """The photos a source was linked in, with url, mjd, visit, detector, collection; at most max_photos (every
    detector and visit kept, spread evenly)."""
    m = members[members["source_id"] == source_id][["obs_id", "detector"]].drop_duplicates()
    p = m.astype({"obs_id": str, "detector": int}).merge(
        photolist.astype({"obs_id": str, "detector": int}), on=["obs_id", "detector"], how="inner")
    p = p.sort_values(["detector", "mjd"]).reset_index(drop=True)
    if len(p) > max_photos:
        p = p.iloc[np.unique(np.linspace(0, len(p) - 1, max_photos).round().astype(int))].reset_index(drop=True)
    return p


def read_source(reader: Reader, photos: pd.DataFrame, ra: float, dec: float, workers: int = FIT_WORKERS) -> tuple[list[Stamp], dict]:
    """Stamps around (ra, dec) in the photos, and the ePSF of every detector among them."""
    def one(r):
        try:
            return read_stamp(reader, r["url"], ra, dec, key=f"{r['obs_id']}D{int(r['detector'])}", det=int(r["detector"]),
                              visit=int(r["visit"]) if pd.notna(r.get("visit")) else 0, mjd=float(r["mjd"]),
                              collection=str(r.get("collection", "")))
        except Exception as e:  # noqa: BLE001
            L.warning("shape_fit: %s: %r", r["obs_id"], e)
            return None

    recs = photos.to_dict("records")
    with ThreadPoolExecutor(max(1, workers)) as ex:
        stamps = [s for s in ex.map(one, recs) if s is not None]
    epsfs = {}
    for det in sorted({s.det for s in stamps}):
        r = next(x for x in recs if int(x["detector"]) == det)
        epsfs[det] = reader.epsf(r["url"], det, str(r.get("collection", "")))
    return stamps, epsfs


def catalogued_at(refcat: pd.DataFrame | None, ra: float, dec: float, mjd: float,
                  radius: float = FIT_CATALOG_ARCSEC) -> str:
    """The nearest Gaia, CatWISE or 2MASS entry within radius ″ of (ra, dec) at mjd (proper motion applied where
    known), as words ("Gaia G 19.8"); "" if none."""
    if refcat is None or not len(refcat):
        return ""
    from skyshift_core import tangent_offset_arcsec

    d = (radius + 30) / 3600.0
    cosd = max(math.cos(math.radians(dec)), 1e-6)
    r = refcat[(np.abs(refcat["dec"] - dec) < d) & (np.abs((refcat["ra"] - ra + 180) % 360 - 180) * cosd < d)]
    if not len(r):
        return ""
    ep = pd.to_numeric(r["epoch_mjd"], errors="coerce").fillna(57388.5).to_numpy(np.float64)
    dt = (mjd - ep) / 365.25
    pmra = np.nan_to_num(pd.to_numeric(r["pmra"], errors="coerce").to_numpy(np.float64))
    pmdec = np.nan_to_num(pd.to_numeric(r["pmdec"], errors="coerce").to_numpy(np.float64))
    rdec0 = r["dec"].to_numpy(np.float64)
    rra = r["ra"].to_numpy(np.float64) + pmra * dt / 3.6e6 / np.cos(np.radians(rdec0))
    rdec = rdec0 + pmdec * dt / 3.6e6
    e, n = tangent_offset_arcsec(ra, dec, rra, rdec)
    sep = np.hypot(e, n)
    if not (sep <= radius).any():
        return ""
    i = int(np.argmin(sep))
    x = r.iloc[i]
    cat = str(x["cat"])
    name, band, col = {"gaia": ("Gaia", "G", "g_mag"), "catwise": ("CatWISE", "W1", "w1_mag")}.get(
        cat, ("2MASS", "K", "k_mag"))
    m = pd.to_numeric(pd.Series([x.get(col)]), errors="coerce").iloc[0]
    return f"{name} {band} {m:.1f}" if np.isfinite(m) else name


def mag_diff(ratio: float) -> float:
    return float(-2.5 * math.log10(ratio)) if ratio and ratio > 0 and np.isfinite(ratio) else float("nan")


def compass(pa: float) -> str:
    names = ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"]
    return names[int(((pa % 360) + 22.5) // 45) % 8]


RESULT_COLUMNS = ["companion_split", "companion_sep_arcsec", "companion_sep_err_arcsec", "companion_pa_deg",
                  "companion_ra", "companion_dec", "companion_flux_ratio", "companion_det",
                  *[f"companion_flux_ratio_d{d}" for d in range(1, 7)], *[f"companion_snr_d{d}" for d in range(1, 7)],
                  "two_vs_one_dchi2", "fit_n_photos", "companion_moves", "companion_move_arcsec",
                  "companion_move_sigma", "companion_visit_mjd", "companion_visit_ra", "companion_visit_dec",
                  "companion_catalog"]


def result_row(f: SourceFit) -> dict:
    """The new shape_candidates.parquet columns of one fit (contracts/dots.md, shape_candidates)."""
    from skyshift_core.astrometry import tangent_deproject

    row = {c: np.nan for c in RESULT_COLUMNS}
    row.update({"companion_split": bool(f.split), "two_vs_one_dchi2": f.dchi2, "fit_n_photos": int(f.n_photos),
                "companion_moves": f.moves if f.moves is not None else None, "companion_catalog": f.catalog,
                "companion_visit_mjd": [], "companion_visit_ra": [], "companion_visit_dec": []})
    if not np.isfinite(f.east):
        return row
    snr = {d: v for d, v in f.snr.items() if np.isfinite(v)}
    best = max(snr, key=snr.get) if snr else None
    row.update({"companion_sep_arcsec": f.sep, "companion_sep_err_arcsec": f.sep_err, "companion_pa_deg": f.pa,
                "companion_ra": f.ra, "companion_dec": f.dec, "companion_det": best if best is not None else np.nan,
                "companion_flux_ratio": f.ratio.get(best, np.nan) if best is not None else np.nan,
                "companion_move_arcsec": f.move_arcsec, "companion_move_sigma": f.move_sigma})
    for d in range(1, 7):
        row[f"companion_flux_ratio_d{d}"] = f.ratio.get(d, np.nan)
        row[f"companion_snr_d{d}"] = f.snr.get(d, np.nan)
    for v in f.visits:
        if np.isfinite(v.get("east", np.nan)):
            a, b = tangent_deproject(f.primary_ra, f.primary_dec, v["east"], v["north"])
            row["companion_visit_mjd"].append(float(v["mjd"]))
            row["companion_visit_ra"].append(float(a) % 360.0)
            row["companion_visit_dec"].append(float(b))
    return row


def companion_words(row) -> str | None:
    """The `why` sentence about the fitted companion, plain words; None when the fit doesn't split it."""
    if not row.get("companion_split"):
        return None
    sep, pa = float(row["companion_sep_arcsec"]), float(row["companion_pa_deg"])
    parts = []
    for d in range(1, 7):
        r, s = row.get(f"companion_flux_ratio_d{d}"), row.get(f"companion_snr_d{d}")
        if r is not None and np.isfinite(r) and s is not None and np.isfinite(s) and s >= 3 and r > 0:
            parts.append((d, mag_diff(r)))
    if parts:
        lo, hi = parts[0], parts[-1]
        bright = (f"about {_fainter(lo[1])} at {DET_UM[lo[0]]:g} µm" if len(parts) == 1 else
                  f"{_fainter(lo[1])} at {DET_UM[lo[0]]:g} µm and {_fainter(hi[1])} at {DET_UM[hi[0]]:g} µm")
    else:
        bright = "faint"
    dchi2 = f"(two objects fit better than one by Δχ² {float(row['two_vs_one_dchi2']):,.0f})"
    if row.get("companion_moves") is True:  # its place changes: one offset says little, the motion says it
        move = float(row.get("companion_move_arcsec", float("nan")))
        return (f"a two-object fit finds a second object next to it that moved {move:.0f}″ between visits "
                f"(something passing by?), {bright} {dchi2}")
    cat = row.get("companion_catalog")
    known = f"catalogued there as {cat}" if isinstance(cat, str) and cat else "not in Gaia, CatWISE or 2MASS"
    motion = ", and it stays put between visits" if row.get("companion_moves") is False else ""
    return (f"a two-object fit puts a second object {sep:.0f}″ {compass(pa)} (PA {pa:.0f}°) of it, {bright}, "
            f"{known}{motion} {dchi2}")


def _fainter(dm: float) -> str:
    if dm < -0.05:
        return f"{-dm:.1f} mag brighter"
    if abs(dm) <= 0.05:
        return "as bright"
    return f"{dm:.1f} mag fainter"


def fit_rows(rows: pd.DataFrame, members: pd.DataFrame, photolist: pd.DataFrame, refcat: pd.DataFrame | None,
             reader: Reader | None = None, workers: int = FIT_WORKERS, keep: dict | None = None) -> tuple[pd.DataFrame, dict]:
    """The two-source fit of every shape_candidates row (source_id, ra, dec, mjd): the rows with RESULT_COLUMNS
    added and `why` reworded where the fit splits the source. keep: a dict that receives each SourceFit."""
    reader = reader or Reader()
    res, info = [], {"fits": []}
    for r in rows.to_dict("records"):
        sid = int(r["source_id"])
        t0 = time.time()
        try:
            photos = photos_of(members, photolist, sid)
            stamps, epsfs = read_source(reader, photos, float(r["ra"]), float(r["dec"]), workers)
            mjd = float(r["mjd"]) if pd.notna(r.get("mjd")) else float(np.mean([s.mjd for s in stamps]))
            nb = _neighbours(refcat, float(r["ra"]), float(r["dec"]), mjd, (float(r["ra"]), float(r["dec"])),
                             FIT_NEIGHBOUR_PX * 6.2)
            f = fit_source(stamps, epsfs, nb, sid)
            if np.isfinite(f.east):
                f.catalog = catalogued_at(refcat, f.ra, f.dec, mjd)
        except Exception as e:  # noqa: BLE001  a failed fit leaves the row as it was
            L.warning("shape_fit: source %d failed: %r", sid, e)
            f = SourceFit(sid, error=repr(e))
        if keep is not None:
            keep[sid] = f
        row = result_row(f)
        res.append(row)
        info["fits"].append({"source_id": sid, "photos": f.n_photos, "error": f.error, "split": bool(f.split),
                             "dchi2": f.dchi2, "sep_arcsec": f.sep if np.isfinite(f.east) else None,
                             "pa_deg": f.pa if np.isfinite(f.east) else None, "moves": f.moves,
                             "seconds": round(time.time() - t0, 1)})
        L.info("shape_fit: %d: %d photos, Δχ² %.0f, sep %.1f″ PA %.0f°, split %s (%.0f s)", sid, f.n_photos, f.dchi2,
               f.sep if np.isfinite(f.east) else float("nan"), f.pa if np.isfinite(f.east) else float("nan"),
               f.split, time.time() - t0)
    add = pd.DataFrame(res, index=rows.index)
    out = rows.drop(columns=[c for c in RESULT_COLUMNS if c in rows.columns]).join(add)
    out["why"] = [_why_with(w, row) for w, row in zip(out["why"], add.to_dict("records"))]
    info["rule"] = {k: v for k, v in globals().items() if k.startswith("FIT_") and isinstance(v, (int, float))}
    return out, info


def _why_with(why: str, row: dict) -> str:
    base = str(why).split(" Fit: ")[0]
    words = companion_words(row)
    if words is None:
        if np.isfinite(row.get("two_vs_one_dchi2", np.nan)):
            return base + " Fit: two objects don't fit clearly better than one here."
        return base
    return base + " Fit: " + words + "."


def empty_columns(rows: pd.DataFrame) -> pd.DataFrame:
    """rows with RESULT_COLUMNS added, empty (a file with no candidates keeps one schema)."""
    out = rows.copy()
    for c in RESULT_COLUMNS:
        if c not in out:
            obj = c.startswith("companion_visit") or c in ("companion_split", "companion_moves", "companion_catalog")
            out[c] = pd.Series(dtype=object if obj else float, index=out.index)
    return out


def load_inputs(store, zone: str, source_ids, tables: str | None = None, refcat: pd.DataFrame | None = None):
    """members (only these sources: row groups read by source_id), the photo list with url and collection, refcat."""
    from pathlib import Path

    import pyarrow.parquet as pq

    from .store import photolist_rel, results_rel

    ids = [int(x) for x in source_ids]
    if tables:
        members = pq.read_table(Path(tables) / "members.parquet", filters=[("source_id", "in", ids)]).to_pandas()
    else:
        with store.fs.open(store.path(results_rel(zone, "members.parquet")), "rb") as f:
            members = pq.read_table(f, filters=[("source_id", "in", ids)]).to_pandas()
    pl = store.read_parquet(photolist_rel(zone), ["obs_id", "detector", "mjd", "visit", "url", "collection"])
    if refcat is None:
        refcat = (pd.read_parquet(Path(tables) / f"refcat_{zone}.parquet") if tables
                  else store.read_parquet(f"refcat/{zone}.parquet"))
    refcat = refcat[refcat["cat"].astype(str).isin(["gaia", "catwise", "2mass", "tmass"])].assign(
        cat=lambda r: r["cat"].astype(str))
    return members, pl, refcat


def run(store, zone: str, rows: pd.DataFrame, tables: str | None = None, refcat: pd.DataFrame | None = None,
        workers: int = FIT_WORKERS) -> tuple[pd.DataFrame, dict]:
    """fit_rows on a zone's shape_candidates rows (inputs from load_inputs). Never raises: on failure the rows come back
    with empty fit columns and the error in the report."""
    if not len(rows):
        return empty_columns(rows), {"fits": [], "rows": 0}
    try:
        members, pl, refcat = load_inputs(store, zone, rows["source_id"], tables, refcat)
        return fit_rows(rows, members, pl, refcat, workers=workers)
    except Exception as e:  # noqa: BLE001
        L.warning("shape_fit: %s not fitted: %r", zone, e)
        return empty_columns(rows), {"fits": [], "error": repr(e)}


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    from pathlib import Path

    from .store import Store, results_rel

    ap = argparse.ArgumentParser(prog="python -m skyshift_pipeline.shape_fit", description=__doc__.split("\n")[0])
    ap.add_argument("--zone", default="hunt")
    ap.add_argument("--root", default="gs://nasa-glazing-data")
    ap.add_argument("--rows", help="shape_candidates.parquet to fit (default results/<zone>/ in --root)")
    ap.add_argument("--tables", help="local folder with members.parquet and refcat_<zone>.parquet")
    ap.add_argument("--out", help="folder for shape_candidates.parquet and shape_fit_report.json (default results/<zone>/)")
    ap.add_argument("--workers", type=int, default=FIT_WORKERS)
    a = ap.parse_args(argv)
    log.setup()
    store = Store(a.root)
    rows = pd.read_parquet(a.rows) if a.rows else store.read_parquet(results_rel(a.zone, "shape_candidates.parquet"))
    out, info = run(store, a.zone, rows, a.tables, workers=a.workers)
    if a.out:
        Path(a.out).mkdir(parents=True, exist_ok=True)
        out.to_parquet(Path(a.out) / "shape_candidates.parquet", index=False)
        (Path(a.out) / "shape_fit_report.json").write_text(json.dumps(info, indent=1, default=str))
    else:
        store.write_parquet(results_rel(a.zone, "shape_candidates.parquet"), out)
        store.write_json(results_rel(a.zone, "shape_fit_report.json"), info)
    print(json.dumps(info, indent=1, default=str))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
