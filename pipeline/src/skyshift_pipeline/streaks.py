"""Streak search (extraction v2): fast movers' trails on the matched-filter pass's residual.

A body moving 900″/h draws a 28″ (4.6 px) trail in one 113.6 s exposure (header XPOSURE), 3,000″/h 15 px, 12,000″/h
61 px; the PSF is 1.22 px (FWHM 7.5″). sep and the point-source matched filter find such trails poorly
(docs/extraction-v2.md §3.3). This module searches the residual the matched-filter pass works on (IMAGE minus its
background, the bright stars' glow and the ePSF models of the S/N >= 10 dots; bad pixels 0; noise with the model
term) for trails, and writes what it finds to its own file; the dots are not changed.

Kernels. A trail's kernel is the photo's ePSF (its centre zone) along a line segment of length L at angle θ, uniform
light (STREAK_PROFILE). Like psf.matched_filter the filter is inverse-variance weighted: S/N = Σ w·d·K / √(Σ w·K²),
flux = Σ w·d·K / Σ w·K², w = 1/noise² (0 on bad pixels); d is clipped at ±STREAK_CLIP_SIGMA per pixel for the search
(one residual star core cannot fake a trail; fluxes use d).

Short trails (STREAK_LENGTHS_PX, 4–9 px, angles so that a segment's end moves STREAK_END_STEP_PX between two): an S/N
map per kernel, by FFT, on native pixels (binning loses up to half the S/N: across a 1.5 px wide trail it adds the
noise of empty pixels). Σ w·K² is approximated per length by ΣK² × the good pixels' mean weight around the pixel
(exact on clean sky, too large where the kernel crosses masked pixels, so S/N there comes out low, never high).
Crowded fields: faint stars that are not subtracted add up in a trail kernel, so each length's S/N map has its own
scatter and offset; each length is normalised by its first map's lower-half sigma and median (psf.mf_sigma_map's
estimator) in STREAK_NORM_GRID² blocks: snr_local = (S/N − max(median, 0)) / max(sigma, 1).

Pieces = local maxima (3 × 3) of the best snr_local over the bank >= STREAK_PIECE_SNR, placed with their kernel's exact
S/N and dropped when one point source on the segment explains them as well (snr² − snr_point² < STREAK_MIN_DCHI2:
a star, a dot, a matched-filter peak). A piece >= STREAK_SNR is a candidate. Long trails are chains: pieces in line
(angles within STREAK_CHAIN_DTH_DEG, each centre within STREAK_CHAIN_PERP_PX of the other's line, gaps up to
STREAK_CHAIN_GAP_PX) are fitted as one segment on native pixels, any length, and normalised by the same kernel's S/N at
STREAK_NULL_N places around it (their lower-half sigma and median). Candidates on a better one's segment are merged.

Each streak is measured on native pixels with its own kernel (length and angle refined, then a parabola): flux, S/N,
the best point source on it (snr_point), the perpendicular kernel (snr_perp: a round blob excites every angle), the
trail cut into parts (a uniform trail has equal parts; one star under a long kernel doesn't), the dots on it, the
flags under it, the share of its weight on masked pixels, the nearest saturated pixel. Every streak is kept with
`why` bits saying what makes it doubtful (WHY_*); cuts happen downstream.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

from . import config as C

# `why` bits of a streak (0 = nothing against it)
WHY_POINT = 1          # snr² − snr_point² < STREAK_MIN_DCHI2 at the end: one point source on it explains it as well
WHY_LUMPY = 2          # its parts disagree (χ² probability < STREAK_PARTS_P, with STREAK_PARTS_FRAC of the flux slack)
WHY_ROUND = 4          # snr_perp > STREAK_MAX_PERP × snr: as bright across as along (a blob, a residual ring)
WHY_DOT = 8            # a dot on it with S/N >= STREAK_DOT_RATIO × its own: a star's light or its residual
WHY_MASKED = 16        # >= STREAK_MAX_MASKED of its kernel weight on bad pixels
WHY_SATURATED = 32     # within STREAK_SAT_PX of an OVERFLOW/NONLINEAR pixel
WHY_EDGE = 64          # an end within STREAK_EDGE_PX of the photo's edge
WHY_STREAK_FLAG = 128  # NASA's STREAK bit (a satellite trail) under it
WHY_AXIS = 256         # within STREAK_AXIS_DEG of a pixel row or column (bad rows and columns, D2's ramp rows)


# --- kernels ------------------------------------------------------------------------------------------------------

def psf_table(epsf, zone: int | None = None) -> tuple[np.ndarray, int]:
    """(oversampled ePSF table of one zone, sum 1; samples per pixel): the photo's ePSF at its centre zone, or a
    Gaussian of the PSF's FWHM without an ePSF (tests)."""
    if epsf is not None:
        if zone is None:
            cx = 0.5 * float(np.max(epsf.xcenter) + np.min(epsf.xcenter)) - epsf.origin[0]
            cy = 0.5 * float(np.max(epsf.ycenter) + np.min(epsf.ycenter)) - epsf.origin[1]
            zone = int(epsf.zone(np.array([cx]), np.array([cy]))[0])
        return np.asarray(epsf.psf[int(zone)], np.float64), int(epsf.oversample[0])
    from skyshift_core.detect import PSF_SIGMA_PX
    os_ = 5
    k = np.arange(-16, 17) / os_
    g = np.exp(-0.5 * (k[None, :] ** 2 + k[:, None] ** 2) / PSF_SIGMA_PX ** 2)
    return g / g.sum(), os_


def trail_weights(n: int, profile: str = C.STREAK_PROFILE) -> np.ndarray:
    """Weights of n equally spaced points along a trail (sum 1): `uniform` (the light spread evenly over the
    exposure) or `ramp` (6τ(1 − τ): what a least-squares slope of equally spaced up-the-ramp reads makes of light that
    arrives at time τ, so the trail's ends are fainter than its middle)."""
    if n <= 1:
        return np.ones(1)
    w = np.ones(n)
    if profile == "ramp":
        t = (np.arange(n) + 0.5) / n
        w = t * (1.0 - t)
    return w / w.sum()


def kernel_half(length: float, shift: float = 0.0) -> int:
    """Half size (px) of a kernel holding the ePSF (±3.2 px) along a segment of `length` shifted by `shift` px."""
    return int(np.ceil(length / 2.0 + abs(shift) + 3.3))


def trail_kernel(table: np.ndarray, os_: int, length: float, theta: float, phase: tuple[float, float] = (0.0, 0.0),
                 half: int | None = None, profile: str = C.STREAK_PROFILE) -> np.ndarray:
    """A trail's pixel kernel (flux fractions, sum ~1): the ePSF (`table`, os_ samples per pixel) along a segment of
    `length` px at angle `theta` (radians, from +x towards +y), its centre `phase` (dx, dy) px from the centre of the
    kernel's central pixel. Shape (2·half + 1)², half default kernel_half. length 0: the ePSF itself."""
    from scipy.signal import fftconvolve
    c = table.shape[0] // 2
    n = max(1, int(round(length * os_)) + 1)
    s = np.linspace(-length / 2.0, length / 2.0, n) if n > 1 else np.zeros(1)
    rx = np.rint((s * np.cos(theta) + phase[0]) * os_).astype(np.int64)
    ry = np.rint((s * np.sin(theta) + phase[1]) * os_).astype(np.int64)
    R = int(max(np.abs(rx).max(), np.abs(ry).max()))
    canvas = np.zeros((2 * R + 1, 2 * R + 1))
    np.add.at(canvas, (ry + R, rx + R), trail_weights(n, profile))
    full = fftconvolve(canvas, table, mode="full") * float(os_ * os_)
    full[full < 0] = 0.0
    cc = R + c                                         # the index of offset 0
    H = kernel_half(length, float(np.hypot(*phase))) if half is None else int(half)
    idx = cc + os_ * np.arange(-H, H + 1)
    ok = (idx >= 0) & (idx < full.shape[0])
    out = np.zeros((2 * H + 1, 2 * H + 1))
    ii = np.flatnonzero(ok)
    out[np.ix_(ii, ii)] = full[np.ix_(idx[ok], idx[ok])]
    return out


def make_bank(lengths=C.STREAK_LENGTHS_PX, end_step_px: float = C.STREAK_END_STEP_PX) -> list[tuple[float, float]]:
    """(length px, angle rad) per kernel. Angles per length: the segment's end moves end_step_px from one angle to the
    next, so a trail between two is at most half of it off at its ends."""
    out = []
    for L in lengths:
        n = max(4, int(np.ceil(np.pi * (L / 2.0) / end_step_px)))
        out.extend((float(L), float(t)) for t in np.arange(n) * np.pi / n)
    return out


_BANK_CACHE: dict = {}


def _bank_kernels(table: np.ndarray, os_: int, bank: list) -> list[np.ndarray]:
    """The bank's kernels (cached per ePSF table: one per detector and calibration file per worker)."""
    key = (hash(table.tobytes()), os_, tuple(bank), C.STREAK_PROFILE)
    if key not in _BANK_CACHE:
        if len(_BANK_CACHE) > 8:
            _BANK_CACHE.clear()
        _BANK_CACHE[key] = [trail_kernel(table, os_, L, th) for L, th in bank]
    return _BANK_CACHE[key]


# --- maps -----------------------------------------------------------------------------------------------------------

def _wrapped(K: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    """K (odd square, centre in its middle) in an array of `shape` with its centre at [0, 0] (FFT correlation)."""
    out = np.zeros(shape, np.float32)
    h = K.shape[0] // 2
    k = np.arange(-h, h + 1)
    out[np.ix_(k % shape[0], k % shape[1])] = K
    return out


class _Correlator:
    """FFT correlations of one image with many kernels (float32)."""

    def __init__(self, a: np.ndarray, hmax: int):
        import scipy.fft as F
        self.F = F
        self.my, self.mx = a.shape
        self.shape = (F.next_fast_len(self.my + hmax, real=True), F.next_fast_len(self.mx + hmax, real=True))
        self.Fa = F.rfft2(a.astype(np.float32), s=self.shape, workers=1)

    def corr(self, K: np.ndarray) -> np.ndarray:
        """Σ a·K around every pixel."""
        F = self.F
        FK = np.conj(F.rfft2(_wrapped(K, self.shape), workers=1))
        FK *= self.Fa
        return F.irfft2(FK, s=self.shape, workers=1)[:self.my, :self.mx]


def _box_mean(a: np.ndarray, size: int) -> np.ndarray:
    from scipy.ndimage import uniform_filter
    return uniform_filter(np.asarray(a, np.float32), size=int(size), mode="constant")


P_LO = 15.865525393145707  # the 1-sigma lower percentile of a normal distribution


def _sigma_lo(v: np.ndarray) -> tuple[float, float]:
    v = v[np.isfinite(v)]
    if v.size < 20:
        return float("nan"), float("nan")
    m = float(np.median(v))
    return m - float(np.percentile(v, P_LO)), m


def norm_grid(s: np.ndarray, ok: np.ndarray, n: int = C.STREAK_NORM_GRID, step: int = 3) -> tuple[np.ndarray, np.ndarray]:
    """(lower-half sigma, median) of an S/N map in n × n blocks (every step-th pixel where `ok`); a block with < 100
    such pixels takes the whole map's (NaN if that has < 100 too)."""
    sub = np.where(ok[::step, ::step], s[::step, ::step], np.nan).astype(np.float64)
    v = sub[np.isfinite(sub)]
    if v.size < 100:
        return np.full((n, n), np.nan), np.full((n, n), np.nan)
    whole = _sigma_lo(v)
    sy, sx = sub.shape
    ey = np.round(np.linspace(0, sy, n + 1)).astype(int)
    ex = np.round(np.linspace(0, sx, n + 1)).astype(int)
    sig, med = np.full((n, n), whole[0]), np.full((n, n), whole[1])
    for i in range(n):
        for j in range(n):
            b = sub[ey[i]:ey[i + 1], ex[j]:ex[j + 1]]
            b = b[np.isfinite(b)]
            if b.size >= 100:
                sig[i, j], med[i, j] = _sigma_lo(b)
    return sig, med


def _expand(g: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    n = g.shape[0]
    ry = np.minimum((np.arange(shape[0]) * n) // shape[0], n - 1)
    rx = np.minimum((np.arange(shape[1]) * n) // shape[1], n - 1)
    return g[np.ix_(ry, rx)].astype(np.float32)


def _local_max(snr: np.ndarray, thresh: float) -> tuple[np.ndarray, np.ndarray]:
    """(iy, ix) of pixels >= thresh that are the maximum of their 3 × 3."""
    iy, ix = np.nonzero(snr >= thresh)
    if iy.size == 0:
        return iy, ix
    ny, nx = snr.shape
    v = snr[iy, ix]
    keep = np.ones(iy.size, bool)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dy or dx:
                keep &= v >= snr[np.clip(iy + dy, 0, ny - 1), np.clip(ix + dx, 0, nx - 1)]
    return iy[keep], ix[keep]


def _segment_samples(x, y, L, th, step: float = 1.0):
    """Points every `step` px along each segment (centre x, y, length L, angle th): (row index, sx, sy)."""
    x, y, L, th = (np.asarray(a, np.float64) for a in (x, y, L, th))
    n = np.maximum(2, np.ceil(L / step).astype(int) + 1)
    rows = np.repeat(np.arange(x.size), n)
    t = np.concatenate([np.linspace(-0.5, 0.5, k) for k in n]) if x.size else np.zeros(0)
    return rows, x[rows] + t * L[rows] * np.cos(th[rows]), y[rows] + t * L[rows] * np.sin(th[rows])


def _along(a: np.ndarray, rows, sx, sy, n: int, how: str):
    """max / min / bitwise or of map `a` at the samples (nearest pixel; off the map ignored), per segment."""
    ny, nx = a.shape
    ix, iy = np.rint(sx).astype(np.int64), np.rint(sy).astype(np.int64)
    inside = (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny)
    v = a[np.clip(iy, 0, ny - 1), np.clip(ix, 0, nx - 1)]
    if how == "or":
        out = np.zeros(n, np.int64)
        np.bitwise_or.at(out, rows[inside], v[inside].astype(np.int64))
        return out
    out = np.full(n, -np.inf if how == "max" else np.inf)
    (np.maximum if how == "max" else np.minimum).at(out, rows[inside], v[inside].astype(np.float64))
    return out


def _stamp_snr(S: np.ndarray, W: np.ndarray, K: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(Σ S·K, Σ W·K²) over stamps (N, m, m) for one kernel (m, m) or per-stamp kernels (N, m, m)."""
    if K.ndim == 2:
        return np.einsum("nij,ij->n", S, K), np.einsum("nij,ij->n", W, K * K)
    return np.einsum("nij,nij->n", S, K), np.einsum("nij,nij->n", W, K * K)


def _ratio(num, den):
    num, den = np.asarray(num, np.float64), np.asarray(den, np.float64)
    return np.where(den > 0, num / np.sqrt(np.where(den > 0, den, 1.0)), -np.inf)


# --- the search -----------------------------------------------------------------------------------------------------

@dataclass
class StreakResult:
    streaks: dict            # column arrays (native pixels): STREAK_KEYS
    info: dict


STREAK_KEYS = ("x", "y", "x1", "y1", "x2", "y2", "length_px", "theta_deg", "flux", "flux_err", "snr", "snr_local",
               "sigma_local", "snr_point", "snr_perp", "parts_chi2", "min_part_snr", "n_parts", "n_dots",
               "dot_snr_max", "flags", "masked_frac", "sat_px", "n_pieces", "why")


def _empty() -> dict:
    return {k: np.zeros(0) for k in STREAK_KEYS}


class _Photo:
    """The arrays every stage reads: weights, clipped and raw weighted data, the point-source S/N map."""

    def __init__(self, resid, noise, bad, epsf):
        nz = np.where(np.isfinite(noise), noise, 0.0)
        self.bad = np.asarray(bad, bool) | ~(nz > 0)
        self.w = np.where(self.bad, 0.0, 1.0 / np.maximum(nz, 1e-30) ** 2)
        d = np.where(self.bad, 0.0, np.asarray(resid, np.float64))
        clip = C.STREAK_CLIP_SIGMA * nz
        self.sw = self.w * np.clip(d, -clip, clip)
        self.swr = self.w * d
        self.table, self.os = psf_table(epsf)
        self.shape = self.sw.shape
        self.point = None

    def kernel(self, L, th, half=None, phase=(0.0, 0.0)):
        return trail_kernel(self.table, self.os, L, th, phase=phase, half=half)

    def stamps(self, a, x, y, half):
        from .psf import gather
        v, _ = gather(a, np.asarray(x, np.int64), np.asarray(y, np.int64), half, fill=0.0)
        return v

    def snr_at(self, x, y, L, th, search: int = 0):
        """Exact S/N of the (L, th) kernel at integer pixels (x, y), the best within ±search px: (snr, x, y)."""
        K = self.kernel(L, th)
        h = K.shape[0] // 2
        S = self.stamps(self.sw, x, y, h + search)
        W = self.stamps(self.w, x, y, h + search)
        m = 2 * h + 1
        best = np.full(len(x), -np.inf)
        bx, by = np.asarray(x, np.int64).copy(), np.asarray(y, np.int64).copy()
        for oy in range(2 * search + 1):
            for ox in range(2 * search + 1):
                s = _ratio(*_stamp_snr(S[:, oy:oy + m, ox:ox + m], W[:, oy:oy + m, ox:ox + m], K))
                u = s > best
                best[u] = s[u]
                bx[u], by[u] = np.asarray(x)[u] + ox - search, np.asarray(y)[u] + oy - search
        return best, bx, by

    def point_along(self, x, y, L, th):
        rows, sx, sy = _segment_samples(x, y, L, th)
        return _along(self.point, rows, sx, sy, len(np.atleast_1d(x)), "max")


def _maps(ph: _Photo, bank: list, norm: dict | None, thresh: float, info: dict, timing: dict) -> np.ndarray:
    """Pieces from the bank's S/N maps: rows (x, y, kernel index, snr_local, sigma)."""
    t0 = time.perf_counter()
    kern = _bank_kernels(ph.table, ph.os, bank)
    cor = _Correlator(ph.sw, max(K.shape[0] // 2 for K in kern))
    ok = ph.w > 0
    best = np.full(ph.shape, -np.inf, np.float32)
    arg = np.zeros(ph.shape, np.int16)
    bsig = np.ones(ph.shape, np.float32)
    cur, A, Bm, sig_map = None, None, None, None
    for i, K in enumerate(kern):
        num = cor.corr(K)
        L = bank[i][0]
        if L != cur:
            cur = L
            h = K.shape[0] // 2
            box = min(2 * h + 1, C.STREAK_DEN_BOX)
            wbar = _box_mean(ph.w, box) / np.maximum(_box_mean(ok, box), 1e-6)
            den = float(np.sum(K ** 2)) * wbar
            a = np.where(den > 0, 1.0 / np.sqrt(np.maximum(den, 1e-30)), 0.0).astype(np.float32)
            if norm is not None and L in norm:
                sg, md = norm[L]
            else:
                sg, md = norm_grid(num * a, ok)
            info["norm"][L] = (sg, md)
            sg = np.where(np.isfinite(sg), np.maximum(sg, 1.0), 1.0)
            md = np.where(np.isfinite(md), np.maximum(md, 0.0), 0.0)
            sig_map = _expand(sg, ph.shape)
            A = a / sig_map
            Bm = -_expand(md, ph.shape) / sig_map
        num *= A
        num += Bm
        better = num > best
        np.copyto(best, num, where=better)
        arg[better] = i
        np.copyto(bsig, sig_map, where=better)
    best[~ok] = -np.inf
    timing["streak_maps_s"] = time.perf_counter() - t0
    iy, ix = _local_max(best, thresh)
    return np.c_[ix, iy, arg[iy, ix], best[iy, ix], bsig[iy, ix]]


def _chains(px, py, pth, pL, psnr) -> list[np.ndarray]:
    """Groups (index arrays) of >= 2 pieces in line: angles within STREAK_CHAIN_DTH_DEG, each centre within
    STREAK_CHAIN_PERP_PX of the other's line, the gap between their ends <= STREAK_CHAIN_GAP_PX (union-find)."""
    from scipy.spatial import cKDTree
    n = len(px)
    if n < 2:
        return []
    parent = np.arange(n)

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    dth = np.radians(C.STREAK_CHAIN_DTH_DEG)
    reach = float(np.max(pL)) + C.STREAK_CHAIN_GAP_PX
    tree = cKDTree(np.c_[px, py])
    for i, j in tree.query_pairs(reach):
        d = abs((pth[i] - pth[j] + np.pi / 2) % np.pi - np.pi / 2)
        if d > dth:
            continue
        dx, dy = px[j] - px[i], py[j] - py[i]
        sep = np.hypot(dx, dy)
        if sep < 0.5 * (pL[i] + pL[j]) / 2 or sep > (pL[i] + pL[j]) / 2 + C.STREAK_CHAIN_GAP_PX:
            continue
        if abs(-dx * np.sin(pth[i]) + dy * np.cos(pth[i])) > C.STREAK_CHAIN_PERP_PX:
            continue
        if abs(-dx * np.sin(pth[j]) + dy * np.cos(pth[j])) > C.STREAK_CHAIN_PERP_PX:
            continue
        a, b = find(i), find(j)
        if a != b:
            parent[a] = b
    roots = np.array([find(i) for i in range(n)])
    return [np.flatnonzero(roots == r) for r in np.unique(roots) if (roots == r).sum() >= 2]


def _null(ph: _Photo, x: float, y: float, L: float, th: float) -> tuple[float, float]:
    """(lower-half sigma, median) of the (L, th) kernel's S/N at STREAK_NULL_N places 2–6 lengths around (x, y): this
    kernel's own scatter here (confusion grows with length)."""
    rng = np.random.default_rng(abs(int(x)) * 7919 + abs(int(y)))  # the same places on every run
    n = C.STREAK_NULL_N
    r = rng.uniform(1.0, 3.0, n) * (L + 10.0)
    a = rng.uniform(0, 2 * np.pi, n)
    ny, nx = ph.shape
    qx = np.clip(np.rint(x + r * np.cos(a)), 0, nx - 1).astype(np.int64)
    qy = np.clip(np.rint(y + r * np.sin(a)), 0, ny - 1).astype(np.int64)
    s, _, _ = ph.snr_at(qx, qy, L, th)
    return _sigma_lo(s[np.isfinite(s)])


def max_point_ratio(L):
    """The most of a candidate's S/N one point source on it may explain (snr_point / snr), by length: a trail longer
    than the PSF spreads its light, a star or a faint blend doesn't (STREAK_POINT_RATIO, interpolated in length)."""
    Ls, r = zip(*C.STREAK_POINT_RATIO)
    return np.interp(np.asarray(L, np.float64), Ls, r)


def streak_search(resid: np.ndarray, noise: np.ndarray, bad: np.ndarray, epsf, dot_x=None, dot_y=None, dot_snr=None,
                  *, f3: np.ndarray | None = None, sat_dist: np.ndarray | None = None, norm: dict | None = None,
                  thresh: float = C.STREAK_SNR, bank: list | None = None, timing: dict | None = None) -> StreakResult:
    """Trails on `resid` (background- and model-subtracted image, bad pixels 0) with per-pixel 1σ `noise` (module
    docstring). dot_x/dot_y/dot_snr: every dot (n_dots, dot_snr_max, WHY_DOT); f3: the 3 × 3 OR of FLAGS (`flags`,
    WHY_STREAK_FLAG); sat_dist: px to the nearest OVERFLOW/NONLINEAR pixel (sat_px, WHY_SATURATED); norm: the bank's
    per-length (sigma, median) grids to use instead of measuring them (a negative-image control uses the positive
    photo's). Returns the streaks (native pixel coordinates; ends x1, y1 – x2, y2) and info (counts, the norm grids)."""
    from scipy.spatial import cKDTree
    from scipy.stats import chi2 as chi2_dist

    t_all = time.perf_counter()
    timing = {} if timing is None else timing
    bank = make_bank() if bank is None else bank
    ph = _Photo(resid, noise, bad, epsf)
    info: dict = {"n_kernels": len(bank), "norm": {}}
    pieces = _maps(ph, bank, norm, min(C.STREAK_PIECE_SNR, thresh), info, timing)
    info["n_pieces_raw"] = int(len(pieces))

    # the point-source matched filter (the ePSF of the centre zone): snr_point
    t0 = time.perf_counter()
    pt = ph.kernel(0.0, 0.0)
    pnum = _Correlator(ph.sw, pt.shape[0] // 2).corr(pt)
    pden = _Correlator(ph.w, pt.shape[0] // 2).corr(pt * pt)
    ph.point = np.where(pden > 1e-12 * max(float(pden.max()), 1e-30), pnum / np.sqrt(np.maximum(pden, 1e-30)), 0.0)
    timing["streak_point_s"] = time.perf_counter() - t0

    # pieces: exact S/N with their kernel, minus the point-like ones
    t0 = time.perf_counter()
    n = len(pieces)
    px, py = pieces[:, 0].astype(np.int64), pieces[:, 1].astype(np.int64)
    kidx = pieces[:, 2].astype(int)
    pL = np.array([bank[k][0] for k in kidx]) if n else np.zeros(0)
    pth = np.array([bank[k][1] for k in kidx]) if n else np.zeros(0)
    psnr = np.full(n, -np.inf)
    for k in np.unique(kidx):
        sel = np.flatnonzero(kidx == k)
        psnr[sel], _, _ = ph.snr_at(px[sel], py[sel], *bank[k])
    prpt = np.maximum(ph.point_along(px, py, pL, pth), 0.0) / np.maximum(psnr, 1e-9)
    trail = psnr ** 2 * (1.0 - prpt ** 2) >= C.STREAK_MIN_DCHI2
    info["n_pieces_point_like"] = int((~trail).sum())
    px, py, pL, pth, psnr, plocal, psig, prpt, kidx = (a[trail] for a in (px, py, pL, pth, psnr, pieces[:, 3],
                                                                         pieces[:, 4], prpt, kidx))
    info["n_pieces"] = int(len(px))

    # candidates: strong pieces, and chains of pieces fitted as one segment
    cands = []   # (x, y, L, th, dth, snr_local, sigma, n_pieces, group: bank kernel or -1 - chain)
    strong = (plocal >= thresh) & (prpt <= max_point_ratio(pL))
    info["n_pieces_strong"] = int(strong.sum())
    for i in np.flatnonzero(strong):
        nth = sum(1 for b in bank if b[0] == pL[i])
        cands.append((px[i], py[i], pL[i], pth[i], np.pi / nth, plocal[i], psig[i], 1, kidx[i]))
    chains = _chains(px.astype(float), py.astype(float), pth, pL, psnr)
    info["n_chains"] = len(chains)
    for g in chains:
        wts = np.maximum(psnr[g], 0.1)
        cx, cy = np.average(px[g], weights=wts), np.average(py[g], weights=wts)
        # the chain's direction: its pieces' mean angle (doubled-angle mean), its ends: the extreme projections
        th = 0.5 * np.arctan2(np.average(np.sin(2 * pth[g]), weights=wts), np.average(np.cos(2 * pth[g]), weights=wts))
        t = (px[g] - cx) * np.cos(th) + (py[g] - cy) * np.sin(th)
        lo, hi = np.min(t - pL[g] / 2), np.max(t + pL[g] / 2)
        L = float(hi - lo)
        mx, my = cx + 0.5 * (lo + hi) * np.cos(th), cy + 0.5 * (lo + hi) * np.sin(th)
        s, bx, by = ph.snr_at(np.array([int(round(mx))]), np.array([int(round(my))]), L, th, search=1)
        sg, md = _null(ph, float(bx[0]), float(by[0]), L, th)
        sg = max(sg, 1.0) if np.isfinite(sg) else 1.0
        md = max(md, 0.0) if np.isfinite(md) else 0.0
        local = (float(s[0]) - md) / sg
        rpt = max(float(ph.point_along(bx, by, [L], [th])[0]), 0.0) / max(float(s[0]), 1e-9)
        if local >= thresh and rpt <= float(max_point_ratio(L)):
            cands.append((int(bx[0]), int(by[0]), L, th, 1.0 / max(L, 1.0), local, sg, len(g), -1 - len(cands)))
    timing["streak_pieces_s"] = time.perf_counter() - t0
    if not cands:
        info["n_streaks"] = 0
        timing["streaks_s"] = info["time_s"] = time.perf_counter() - t_all
        return StreakResult(_empty(), info)
    cands = np.array(cands, np.float64)

    # merge: a candidate whose centre lies on a better one's segment, or that holds a better one's centre on its own
    # (perpendicular <= STREAK_MERGE_PX, along within half its length + STREAK_MERGE_PX, angles within
    # STREAK_MERGE_DEG), or whose centre is within 1 px of a better one's, is the same trail
    t0 = time.perf_counter()
    xs, ys, Ls, ths = cands[:, 0], cands[:, 1], cands[:, 2], cands[:, 3]
    order = np.argsort(-cands[:, 5], kind="stable")
    tree = cKDTree(np.c_[xs, ys])
    gone = np.zeros(len(cands), bool)
    acc = np.zeros(len(cands), bool)
    rmax = float(np.max(Ls)) / 2 + C.STREAK_MERGE_PX

    def on(a, b) -> bool:
        """a's centre on b's segment at b's angle (within STREAK_MERGE_DEG), or on b's centre: a trail crossing
        another at a clear angle is its own."""
        dx, dy = xs[a] - xs[b], ys[a] - ys[b]
        if np.hypot(dx, dy) <= 1.0:
            return True
        if abs((ths[a] - ths[b] + np.pi / 2) % np.pi - np.pi / 2) > np.radians(C.STREAK_MERGE_DEG):
            return False
        return (abs(-dx * np.sin(ths[b]) + dy * np.cos(ths[b])) <= C.STREAK_MERGE_PX
                and abs(dx * np.cos(ths[b]) + dy * np.sin(ths[b])) <= Ls[b] / 2 + C.STREAK_MERGE_PX)

    for i in order:
        if gone[i]:
            continue
        acc[i] = True
        for j in tree.query_ball_point([xs[i], ys[i]], rmax):
            if not (acc[j] or gone[j]) and (on(j, i) or on(i, j)):
                gone[j] = True
    cands = cands[~gone]
    info["n_merged"] = int(gone.sum())
    timing["streak_merge_s"] = time.perf_counter() - t0

    # measure each streak on native pixels with its own kernel
    t0 = time.perf_counter()
    out = _measure(ph, cands)
    n = len(cands)
    th = np.radians(out["theta_deg"])
    rows, sx, sy = _segment_samples(out["x"], out["y"], out["length_px"], th)
    out["snr_point"] = _along(ph.point, rows, sx, sy, n, "max")
    out["flags"] = _along(np.asarray(f3, np.int64), rows, sx, sy, n, "or") if f3 is not None else np.zeros(n, np.int64)
    out["sat_px"] = (np.minimum(_along(np.asarray(sat_dist, np.float64), rows, sx, sy, n, "min"), 999.0)
                     if sat_dist is not None else np.full(n, 999.0))
    out["n_dots"], out["dot_snr_max"] = np.zeros(n), np.full(n, np.nan)
    if dot_x is not None and np.size(dot_x):
        dx_, dy_ = np.asarray(dot_x, np.float64), np.asarray(dot_y, np.float64)
        ds = np.asarray(dot_snr, np.float64) if dot_snr is not None else np.full(dx_.size, np.nan)
        dtree = cKDTree(np.c_[dx_, dy_])
        for i in range(n):
            near = np.asarray(dtree.query_ball_point([out["x"][i], out["y"][i]], out["length_px"][i] / 2 + C.STREAK_DOT_PX),
                              np.int64)
            if not near.size:
                continue
            ex, ey = dx_[near] - out["x"][i], dy_[near] - out["y"][i]
            al = ex * np.cos(th[i]) + ey * np.sin(th[i])
            pp = -ex * np.sin(th[i]) + ey * np.cos(th[i])
            onseg = (np.abs(pp) <= C.STREAK_DOT_PX) & (np.abs(al) <= out["length_px"][i] / 2 + C.STREAK_DOT_PX)
            out["n_dots"][i] = int(onseg.sum())
            if onseg.any() and np.isfinite(ds[near][onseg]).any():
                out["dot_snr_max"][i] = float(np.nanmax(ds[near][onseg]))
    out["why"] = _why(out, ph.shape, chi2_dist)
    o = np.argsort(-out["snr_local"], kind="stable")
    out = {k: np.asarray(v)[o] for k, v in out.items()}
    timing["streak_measure_s"] = time.perf_counter() - t0
    info["n_streaks"] = int(n)
    info["n_clean"] = int((out["why"] == 0).sum())
    timing["streaks_s"] = info["time_s"] = time.perf_counter() - t_all
    return StreakResult(out, info)


def _measure(ph: _Photo, cands: np.ndarray) -> dict:
    """Per group of candidates sharing a starting kernel (a bank kernel; each chain its own): length and angle on a
    3 × 3 grid around it (STREAK_REFINE_LEN × ±half the angle step), each at the candidate's pixel ±1; at the best:
    flux, S/N, the perpendicular kernel, the parts; the reported length and angle then go through a parabola in log L
    and θ around the best."""
    n = len(cands)
    out = {k: np.full(n, np.nan) for k in STREAK_KEYS}
    out["snr_local"], out["sigma_local"], out["n_pieces"] = cands[:, 5], cands[:, 6], cands[:, 7]
    fl = np.array(C.STREAK_REFINE_LEN)
    ft = np.array([-0.5, 0.0, 0.5])
    grp = cands[:, 8].astype(np.int64)
    for g in np.unique(grp):
        sel = np.flatnonzero(grp == g)
        L0, th0, dth = cands[sel[0], 2], cands[sel[0], 3], cands[sel[0], 4]
        h = kernel_half(L0 * fl[-1])
        m = 2 * h + 1
        x0, y0 = cands[sel, 0].astype(np.int64), cands[sel, 1].astype(np.int64)
        S, W = ph.stamps(ph.sw, x0, y0, h + 1), ph.stamps(ph.w, x0, y0, h + 1)
        grid = np.full((fl.size, ft.size, sel.size), -np.inf)
        gpos = np.zeros((fl.size, ft.size, sel.size), np.int64)
        kern = {}
        for a, f in enumerate(fl):
            for b, t in enumerate(ft):
                K = kern[(a, b)] = ph.kernel(L0 * f, th0 + t * dth, half=h)
                for o in range(9):
                    oy, ox = divmod(o, 3)
                    s = _ratio(*_stamp_snr(S[:, oy:oy + m, ox:ox + m], W[:, oy:oy + m, ox:ox + m], K))
                    u = s > grid[a, b]
                    grid[a, b][u], gpos[a, b][u] = s[u], o
        flat = grid.reshape(-1, sel.size)
        best = np.argmax(flat, axis=0)
        ba, bb = np.unravel_index(best, grid.shape[:2])
        bo = gpos.reshape(-1, sel.size)[best, np.arange(sel.size)]
        oy, ox = np.divmod(bo, 3)
        xs, ys = x0 + ox - 1, y0 + oy - 1
        out["x"][sel], out["y"][sel] = xs, ys
        # measure at the best grid kernel, on stamps centred on the best pixel
        S, W = ph.stamps(ph.sw, xs, ys, h), ph.stamps(ph.w, xs, ys, h)
        SR, B = ph.stamps(ph.swr, xs, ys, h), ph.stamps(ph.bad.astype(np.float64), xs, ys, h)
        for a in range(fl.size):
            for b in range(ft.size):
                j = np.flatnonzero((ba == a) & (bb == b))
                if not j.size:
                    continue
                L, th = L0 * fl[a], th0 + ft[b] * dth
                K = kern[(a, b)]
                num, den = _stamp_snr(S[j], W[j], K)
                numr = np.einsum("nij,ij->n", SR[j], K)
                ok = den > 0
                rows = sel[j]
                out["snr"][rows] = np.where(ok, num / np.sqrt(np.where(ok, den, 1.0)), np.nan)
                out["flux"][rows] = np.where(ok, numr / np.where(ok, den, 1.0), np.nan)
                out["flux_err"][rows] = np.where(ok, 1.0 / np.sqrt(np.where(ok, den, 1.0)), np.nan)
                out["masked_frac"][rows] = np.einsum("nij,ij->n", B[j], K) / max(float(K.sum()), 1e-12)
                Kp = ph.kernel(L, th + np.pi / 2, half=h)
                out["snr_perp"][rows] = _ratio(*_stamp_snr(S[j], W[j], Kp))
                npart = int(min(C.STREAK_MAX_PARTS, L // C.STREAK_PART_MIN_PX))
                out["n_parts"][rows] = npart
                if npart >= 2:
                    f, e = [], []
                    for q in range(npart):
                        c = -L / 2 + (q + 0.5) * L / npart
                        Kq = ph.kernel(L / npart, th, half=h, phase=(c * np.cos(th), c * np.sin(th)))
                        nq, dq = _stamp_snr(S[j], W[j], Kq)
                        dq = np.where(dq > 0, dq, np.nan)
                        f.append(nq / dq)
                        e.append(1.0 / np.sqrt(dq))
                    f, e = np.array(f), np.array(e)                 # (npart, n)
                    ref = np.where(ok, num / np.where(ok, den, 1.0), np.nan) / npart  # a uniform trail: F / npart each
                    e = np.hypot(e, C.STREAK_PARTS_FRAC * np.abs(ref))
                    out["parts_chi2"][rows] = np.nansum(((f - ref) / e) ** 2, axis=0)
                    out["min_part_snr"][rows] = np.nanmin(f / e, axis=0)
        # reported length and angle: a parabola through the grid around the best
        Lr, tr = L0 * fl[ba], th0 + ft[bb] * dth
        u = np.log(fl)
        for jj in range(sel.size):
            a, b = ba[jj], bb[jj]
            if 0 < a < fl.size - 1:
                p, q, r = grid[a - 1, b, jj], grid[a, b, jj], grid[a + 1, b, jj]
                if np.isfinite([p, q, r]).all() and p - 2 * q + r < 0:
                    off = float(np.clip(0.5 * (p - r) / (p - 2 * q + r), -1, 1))
                    Lr[jj] = L0 * float(np.exp(u[a] + off * (u[a + 1] - u[a - 1]) / 2))
            if b == 1:
                p, q, r = grid[a, 0, jj], grid[a, 1, jj], grid[a, 2, jj]
                if np.isfinite([p, q, r]).all() and p - 2 * q + r < 0:
                    tr[jj] = th0 + 0.5 * dth * float(np.clip(0.5 * (p - r) / (p - 2 * q + r), -1, 1))
        out["length_px"][sel], out["theta_deg"][sel] = Lr, np.degrees(tr) % 180.0
    th = np.radians(out["theta_deg"])
    hx, hy = out["length_px"] / 2 * np.cos(th), out["length_px"] / 2 * np.sin(th)
    out["x1"], out["y1"], out["x2"], out["y2"] = out["x"] - hx, out["y"] - hy, out["x"] + hx, out["y"] + hy
    return out


def _why(out: dict, shape: tuple[int, int], chi2_dist) -> np.ndarray:
    from skyshift_core.flags import STREAK as STREAK_BIT
    n = len(out["x"])
    snr = np.nan_to_num(out["snr"], nan=-np.inf)
    why = np.zeros(n, np.int64)
    why |= np.where(snr ** 2 - np.maximum(np.nan_to_num(out["snr_point"], nan=0.0), 0) ** 2 < C.STREAK_MIN_DCHI2, WHY_POINT, 0)
    okp = np.isfinite(out["parts_chi2"]) & (out["n_parts"] >= 2)
    p = np.ones(n)
    p[okp] = chi2_dist.sf(out["parts_chi2"][okp], out["n_parts"][okp] - 1)
    why |= np.where(okp & (p < C.STREAK_PARTS_P), WHY_LUMPY, 0)
    why |= np.where(np.nan_to_num(out["snr_perp"], nan=-np.inf) > C.STREAK_MAX_PERP * snr, WHY_ROUND, 0)
    why |= np.where(np.nan_to_num(out["dot_snr_max"], nan=-np.inf) >= C.STREAK_DOT_RATIO * snr, WHY_DOT, 0)
    why |= np.where(np.nan_to_num(out["masked_frac"], nan=1.0) >= C.STREAK_MAX_MASKED, WHY_MASKED, 0)
    why |= np.where(out["sat_px"] < C.STREAK_SAT_PX, WHY_SATURATED, 0)
    ny, nx = shape
    e = C.STREAK_EDGE_PX
    edge = np.minimum.reduce([out["x1"], out["x2"], out["y1"], out["y2"], nx - 1 - out["x1"], nx - 1 - out["x2"],
                              ny - 1 - out["y1"], ny - 1 - out["y2"]]) < e
    why |= np.where(edge, WHY_EDGE, 0)
    why |= np.where((np.asarray(out["flags"], np.int64) & STREAK_BIT) != 0, WHY_STREAK_FLAG, 0)
    ax = np.minimum(out["theta_deg"] % 90.0, 90.0 - out["theta_deg"] % 90.0)
    why |= np.where(ax <= C.STREAK_AXIS_DEG, WHY_AXIS, 0)
    return why


# --- the streak file --------------------------------------------------------------------------------------------------

STREAK_COLUMNS = {
    "ra": "float64", "dec": "float64",            # centre (deg)
    "ra1": "float64", "dec1": "float64",          # the two ends (a trail has no direction: 1 and 2 are arbitrary)
    "ra2": "float64", "dec2": "float64",
    "x": "float32", "y": "float32",                # pixels, 0-based
    "x1": "float32", "y1": "float32", "x2": "float32", "y2": "float32",
    "length_px": "float32", "length_arcsec": "float32",
    "theta_deg": "float32",                        # pixel frame, from +x towards +y, 0–180
    "pa_deg": "float32",                           # sky position angle of the segment, east of north, 0–180
    "rate_arcsec_h": "float32",                    # length_arcsec over the exposure (header XPOSURE)
    "flux_ujy": "float32", "flux_err_ujy": "float32",
    "snr": "float32", "snr_local": "float32", "sigma_local": "float32",
    "snr_point": "float32", "snr_perp": "float32",
    "parts_chi2": "float32", "min_part_snr": "float32", "n_parts": "int8",
    "n_pieces": "int16", "n_dots": "int16", "dot_snr_max": "float32",
    "flags": "int32", "masked_frac": "float32", "sat_px": "float32",
    "why": "int16",
}


def streaks_rel(obs_id: str, det: int, folder: str) -> str:
    """The streak file of a v2 dot file: <folder>_streaks/<obs_id>D<det>.parquet."""
    return f"{folder}_streaks/{obs_id}D{int(det)}.parquet"


def empty_streaks():
    import pandas as pd
    return pd.DataFrame({k: np.zeros(0, t) for k, t in STREAK_COLUMNS.items()})


def streaks_table(l2, result: StreakResult | None, conv: float):
    """The streak file's rows (STREAK_COLUMNS) from streak_search's result on photo l2; conv = µJy per MJy/sr in one
    pixel (the dots' flux scale; no aperture correction)."""
    import pandas as pd
    from skyshift_core import pixel_to_sky
    if result is None or not len(result.streaks["x"]):
        return empty_streaks()
    s = result.streaks
    ra, dec = pixel_to_sky(l2.wcs, s["x"], s["y"])
    ra1, dec1 = pixel_to_sky(l2.wcs, s["x1"], s["y1"])
    ra2, dec2 = pixel_to_sky(l2.wcs, s["x2"], s["y2"])
    d1, d2 = np.deg2rad(dec1), np.deg2rad(dec2)
    da = np.deg2rad(np.asarray(ra2) - np.asarray(ra1))
    hav = np.sin((d2 - d1) / 2) ** 2 + np.cos(d1) * np.cos(d2) * np.sin(da / 2) ** 2
    sep = np.degrees(2 * np.arcsin(np.sqrt(np.clip(hav, 0, 1)))) * 3600
    pa = np.degrees(np.arctan2(np.sin(da) * np.cos(d2), np.cos(d1) * np.sin(d2) - np.sin(d1) * np.cos(d2) * np.cos(da)))
    try:
        xposure = float(l2.header.get("XPOSURE") or C.STREAK_EXPOSURE_S)
    except (AttributeError, TypeError, ValueError):
        xposure = C.STREAK_EXPOSURE_S
    df = pd.DataFrame({
        "ra": np.asarray(ra) % 360.0, "dec": dec, "ra1": np.asarray(ra1) % 360.0, "dec1": dec1,
        "ra2": np.asarray(ra2) % 360.0, "dec2": dec2,
        "x": s["x"], "y": s["y"], "x1": s["x1"], "y1": s["y1"], "x2": s["x2"], "y2": s["y2"],
        "length_px": s["length_px"], "length_arcsec": sep, "theta_deg": s["theta_deg"], "pa_deg": pa % 180.0,
        "rate_arcsec_h": sep / xposure * 3600.0,
        "flux_ujy": np.asarray(s["flux"]) * conv, "flux_err_ujy": np.asarray(s["flux_err"]) * conv,
        "snr": s["snr"], "snr_local": s["snr_local"], "sigma_local": s["sigma_local"],
        "snr_point": s["snr_point"], "snr_perp": s["snr_perp"], "parts_chi2": s["parts_chi2"],
        "min_part_snr": s["min_part_snr"], "n_parts": np.nan_to_num(s["n_parts"]).astype(np.int64),
        "n_pieces": np.nan_to_num(s["n_pieces"]).astype(np.int64), "n_dots": np.nan_to_num(s["n_dots"]).astype(np.int64),
        "dot_snr_max": s["dot_snr_max"], "flags": np.asarray(s["flags"], np.int64),
        "masked_frac": s["masked_frac"], "sat_px": s["sat_px"], "why": np.asarray(s["why"], np.int64),
    })
    return df.astype(STREAK_COLUMNS)


def streak_meta(result: StreakResult | None) -> dict:
    """The dot file's footer key `streaks`: counts and the settings that made the streak file."""
    info = {} if result is None else result.info
    return {"n_streaks": int(info.get("n_streaks", 0)), "n_clean": int(info.get("n_clean", 0)),
            "n_pieces": int(info.get("n_pieces", 0)), "n_chains": int(info.get("n_chains", 0)),
            "config": {"lengths_px": list(C.STREAK_LENGTHS_PX), "end_step_px": C.STREAK_END_STEP_PX,
                       "snr": C.STREAK_SNR, "piece_snr": C.STREAK_PIECE_SNR, "profile": C.STREAK_PROFILE,
                       "point_ratio": [list(p) for p in C.STREAK_POINT_RATIO], "min_dchi2": C.STREAK_MIN_DCHI2}}
