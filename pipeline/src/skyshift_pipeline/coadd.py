"""Session stacks: step `sessionstack` (16 Nov evening), plus the study's prototype (pairs, shift-and-stack trials).

The step (behind config SESSION_STACK, env SKYSHIFT_SESSION_STACK=1; off: nothing reads or writes its files and every
other step's output is unchanged). For each session of the zone and each detector with >= SESSION_STACK_MIN_EXP photos:
1. Each photo as extraction v2 prepares it: core's mask and background (dots.photo_context), noise max(√VARIANCE,
   background rms), MWIR-style dark holes masked (dark_holes), the bright stars' glow out (core.glow via
   stack.glow_cut).
2. One grid per session: the middle photo's own pixel frame (its WCS with SIP), extended to every photo of the session.
   Each photo's ePSF-matched filter (psf.mf_kernel, the v2 pass's kernel and weights) is evaluated AT the grid's pixel
   centres: per tile of the grid the photo is a pure shift (an integer pixel step + a sub-pixel phase that goes into
   the kernel; tiles split until the shift varies by < SESSION_STACK_TILE_MAX_DEV_PX over the tile), so no pixel is
   ever interpolated. num = Σ w·d·K and den = Σ w·K² add over the photos: S/N = num/√den is the inverse-variance
   matched filter of all exposures together, with no correlated resampling noise; its scale is checked on the map's
   own lower half (`sigma_norm`, ~1).
3. Detection: local maxima >= the threshold where >= SESSION_STACK_MIN_COVER photos cover, on the local-significance
   map (bright stars' local noise and saturated holes as `stack`: local_significance, rim_peaks); threshold per session
   × detector = max(MF_PASS_SNR, the S/N where that map's negative peaks, holes' rims excluded, reach
   SESSION_STACK_FA_PER_DEG2), capped at SESSION_STACK_SNR_MAX; `near_sat` within DETECT_SAT_GUARD_PX of a saturated
   pixel.
4. Each stacked source measured in every single photo of the session (psf.psf_photometry: ePSF flux + local sky at its
   position; psf.psf_position where that photo's S/N >= SESSION_STACK_POS_MIN_SNR): per-photo flux, position, error.
   From those: n_exp, the session-mean mjd, flux (inverse-variance mean), glitch_share (share of S/N² in the strongest
   photo), snr_rest (S/N without that photo: < SESSION_STACK_GLITCH_REST_SNR = a one-photo glitch), flux_chi2_nu, and
   the in-session motion (a weighted line through the per-photo positions: rate, PA, motion_snr) so movers stay
   linkable and a fast mover is never hidden in a static row; the line's mid-session point is the source's ePSF
   position (ra_psf, dec_psf, pos_err_arcsec: 0.12–0.15″ rms vs Gaia; the map peak's ra, dec are a parabola on native
   pixels, ~0.8″).
Writes session_stacks/<key>.parquet (one row per stacked source; footer metadata: threshold, false alarms, depth,
timing), session_stacks/<key>_photos.parquet (one row per source per photo), session_stacks/<key>_moving.parquet (the
per-photo sky positions of its moving sources: link's pseudo-dots), session_stacks/index/<zone>.parquet (key ↔ obs_id:
link finds a photo list's stacks, also from tiles and other zones), for SESSION_STACK_MAP_ZONES
session_stacks_maps/<key>.npz (matched-filter flux and err float16, coverage int8: shift-and-stack later without
re-reading photos), and results/<zone>/session_stack.json. key = <first obs_id>_n<N>_<hash>D<det>.
link and orphans read them only with the switch on (link.with_stack_rows, load_stack_rows below; orphans' stack-only
candidates). Measurements and the place in the combined rerun: docs/session-stacks.md §7.

The prototype (16 Nov afternoon, used by the study only): three ways to go deeper than one photo, all on the same
matched-filter S/N maps as `stack` (stack.py: each photo's
background-subtracted pixels splatted onto a tangent-plane grid with inverse-variance weights, convolved with the
detector's ePSF, the noise normalised on the map's own lower half):
- session coadd: the 1–5 exposures of one session in one detector (2.15 min apart, the pointing stepped 11.8′ so the
  same star lands ~115 px further along the LVF: the next wavelength channel). Static over 6.5 min for anything
  slower than ~1″/h; a main-belt asteroid (20–75″/h) trails 2–8″ over a 4-exposure session.
- simultaneous pair: D1+D4, D2+D5, D3+D6 of one exposure see the same sky at the same moment (the dichroic): two
  detection maps combined as a noise-weighted sum (S/N_a + S/N_b)/√2 (best when both see the source equally) or as
  the positive χ², Σ max(S/N, 0)², turned into a Gaussian-equivalent significance (any colour, one side may be 0).
- shift-and-stack: every photo shifted by a trial motion before splatting (track_shifts); the trial grids for a
  visit (linear rates, or the parallax path of a body at distance d plus a small own motion) are in trial grids below.

Positions: grid pixels of stack.Grid (x grows west, y north, STACK_PIX_ARCSEC) or of PixGrid (a reference photo's own
pixel frame, f grid px per native px: what the measurements used). Fluxes: Map.flux × the photo's µJy per MJy/sr
(stack's convention). Nothing here reads the network: callers pass stack.Cut photos.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import time
import warnings
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import ndimage
from scipy.signal import fftconvolve
from scipy.spatial import cKDTree
from scipy.stats import chi2, norm

from . import config as C
from . import stack as S

PARTNER = {1: 4, 2: 5, 3: 6, 4: 1, 5: 2, 6: 3}  # detectors exposed at the same moment (same obs_id)
AB_ZERO_UJY = 3.631e9                           # AB magnitude 0 in µJy
HOURS_PER_DAY = 24.0


# --- grouping --------------------------------------------------------------------------------------------------------


def session_groups(photos: pd.DataFrame) -> dict[tuple[int, int], list[str]]:
    """(session, detector) → the session's obs_ids in that detector, in time order (a photo list's rows: obs_id,
    detector, session, mjd)."""
    out: dict[tuple[int, int], list[str]] = {}
    for (s, d), g in photos.sort_values("mjd").groupby(["session", "detector"], sort=True):
        out[(int(s), int(d))] = list(dict.fromkeys(g["obs_id"]))
    return out


def simultaneous_pairs(photos: pd.DataFrame) -> list[tuple[str, int, int]]:
    """(obs_id, SWIR detector, MWIR detector) for every exposure where both halves of a pair are in the list."""
    have = set(zip(photos["obs_id"], photos["detector"].astype(int)))
    return sorted((o, d, PARTNER[d]) for o, d in have if d <= 3 and (o, PARTNER[d]) in have)


def small_grid(ra0: float, dec0: float, half_arcsec: float, pix: float = C.STACK_PIX_ARCSEC) -> S.Grid:
    """A square stack.Grid of ±half_arcsec around (ra0, dec0)."""
    n = int(math.ceil(2 * half_arcsec / pix)) + 1
    return S.Grid(float(ra0), float(dec0), float(pix), -(n - 1) / 2.0, -(n - 1) / 2.0, n, n)


@dataclass
class PixGrid:
    """A grid in a reference photo's own pixel frame, `f` grid px per native px (f = 3: 2.05″): grid (0, 0) is the
    native pixel (x0, y0). A session's exposures are stepped along the detector's y (the LVF axis, 114.6 px), so a
    strip of full rows of the reference covers the same sky in every exposure, and reading full rows is what range
    reads do cheaply. Same interface as stack.Grid for grid_coords (xy, radec, nx, ny, pix); the kernel for it is
    pix_kernel (detector orientation, not stack's x-west)."""
    wcs: object
    f: int
    x0: float
    y0: float
    nx: int
    ny: int

    @property
    def pix(self) -> float:
        return C.STACK_NATIVE_PIX_ARCSEC / self.f

    def xy(self, ra, dec):
        x, y = self.wcs.world_to_pixel_values(np.asarray(ra, float), np.asarray(dec, float))
        return (np.asarray(x) - self.x0) * self.f, (np.asarray(y) - self.y0) * self.f

    def radec(self, gx, gy):
        ra, dec = self.wcs.pixel_to_world_values(np.asarray(gx, float) / self.f + self.x0,
                                                 np.asarray(gy, float) / self.f + self.y0)
        return np.asarray(ra) % 360.0, np.asarray(dec)


def pix_kernel(epsf, f: int, half_native: float = 3.3) -> np.ndarray:
    """The detector's ePSF (mean of its zones) on a PixGrid of f grid px per native px: K[j, i] = flux fraction per
    native pixel at offset (i, j)/f native px (detector orientation)."""
    r = int(math.ceil(half_native * f))
    o = np.arange(-r, r + 1) / f
    DX, DY = np.meshgrid(o, o)
    nz = epsf.psf.shape[0]
    K = np.zeros_like(DX)
    for z in range(nz):
        K += epsf.evaluate(DX, DY, np.full(DX.shape, z))
    return K / nz


# --- accumulate and detect -------------------------------------------------------------------------------------------


def accumulate(cuts: list[S.Cut], grid: S.Grid, shifts: list[tuple[float, float]] | None = None,
               coords: list[tuple[np.ndarray, np.ndarray]] | None = None) -> S.Acc:
    """One stack of the photos `cuts` on `grid`. shifts (grid px per photo, optional): each photo's pixels are moved
    by −shift before splatting, so a source at (x + shift) in that photo lands on x (shift-and-stack). coords: the
    photos' precomputed stack.grid_coords (reused across trials). acc.n is the number of photos whose footprint
    covers each grid pixel (footprint below), not stack's count of splat hits: one photo's 6.15″ pixels hit only ~40%
    of a 2″ grid, so with hits N photos would rarely all land on one grid pixel."""
    acc = S.Acc.zeros(grid)
    cov = np.zeros((grid.ny, grid.nx), np.int32)
    for i, cut in enumerate(cuts):
        u, v = coords[i] if coords is not None else S.grid_coords(cut, grid)
        if shifts is not None:
            u, v = u - shifts[i][0], v - shifts[i][1]
        S.splat(acc, u, v, cut.sub, cut.w)
        cov += footprint(u, v, cut.w, grid.nx, grid.ny)
    acc.n = cov
    return acc


def footprint(u: np.ndarray, v: np.ndarray, w: np.ndarray, nx: int, ny: int, r: int = 2) -> np.ndarray:
    """Grid pixels within r grid px of one of the photo's good pixel centres (its footprint without the holes)."""
    ok = (w > 0) & (u > -0.5) & (u < nx - 0.5) & (v > -0.5) & (v < ny - 0.5)
    hit = np.zeros(ny * nx, bool)
    hit[np.rint(v[ok]).astype(np.int64) * nx + np.rint(u[ok]).astype(np.int64)] = True
    return ndimage.maximum_filter(hit.reshape(ny, nx), size=2 * r + 1, mode="constant").astype(np.int32)


def detection_map(name: str, acc: S.Acc, K: np.ndarray, min_photos: int = 1, background: bool = True) -> S.Map:
    """stack.snr_map with its own minimum photo count (stack's is STACK_MIN_PHOTOS = 3, so a single photo or a pair
    would be empty there) and coverage that bridges one photo's holes between its pixel centres. The raw map num/√den
    is the PSF-fit S/N for independent pixels; it is divided by its own noise, measured on the lower half of its
    values (median − 15.87th percentile)."""
    with np.errstate(divide="ignore", invalid="ignore"):
        D = np.where(acc.w > 0, acc.s / acc.w, 0.0)
    bad = acc.w <= 0
    if background and (~bad).sum() > 100:
        try:
            import sep
            bk = sep.Background(np.ascontiguousarray(D), mask=np.ascontiguousarray(bad), bw=C.STACK_GRID_BKG_BOX,
                                bh=C.STACK_GRID_BKG_BOX, fw=3, fh=3)
            D = D - bk.back()
        except Exception:  # noqa: BLE001 - a tiny or empty stack: no background
            pass
    D[bad] = 0.0
    num = fftconvolve(acc.w * D, K[::-1, ::-1], mode="same")
    den = fftconvolve(acc.w, (K * K)[::-1, ::-1], mode="same")
    # coverage: acc.n from accumulate counts photos whose footprint covers the pixel (a native pixel, 3.1 grid px at
    # 2″, splats onto 2 × 2 grid px, so one photo leaves holes between its pixel centres); the weight must also be at
    # least a quarter of the map's typical (no thin edges)
    pos = den[den > 0]
    dmin = 0.25 * float(np.median(pos)) if pos.size else np.inf
    ok = (acc.n >= min_photos) & (den > dmin)
    with np.errstate(divide="ignore", invalid="ignore"):
        raw = np.where(ok, num / np.sqrt(np.where(ok, den, 1.0)), 0.0)
        flux = np.where(ok, num / np.where(ok, den, 1.0), np.nan)
    sigma = robust_sigma(raw[ok]) if ok.sum() > 100 else 1.0
    return S.Map(name, raw / sigma, flux, ok, sigma)


def robust_sigma(v: np.ndarray) -> float:
    """Noise of a map from the lower half of its values (sources only add to the upper half)."""
    v = np.asarray(v, np.float64)
    v = v[np.isfinite(v)]
    if v.size < 10:
        return 1.0
    return max(float(np.median(v)) - float(np.percentile(v, 15.865)), 1e-12)


def positive_chi2_sf(x: np.ndarray, n: int) -> np.ndarray:
    """P(Σ_{i<n} max(z_i, 0)² > x) for n independent N(0, 1): Σ_k C(n, k)/2ⁿ · P(χ²_k > x) (k = how many are > 0)."""
    x = np.asarray(x, np.float64)
    p = np.zeros_like(x)
    for k in range(1, n + 1):
        p += math.comb(n, k) / 2.0 ** n * chi2.sf(x, k)
    return np.where(x > 0, p, 1.0)


def _chi2_logsf(x: np.ndarray, k: int) -> np.ndarray:
    """log P(χ²_k > x); beyond x = 1000 (where scipy underflows) the leading term of the upper incomplete gamma
    function, log Γ(s, y) ≈ (s − 1) log y − y (s = k/2, y = x/2), less log Γ(s)."""
    x = np.asarray(x, np.float64)
    out = chi2.logsf(np.minimum(x, 1000.0), k)
    far = x > 1000.0
    if far.any():
        s, y = k / 2.0, x[far] / 2.0
        out = np.where(far, 0.0, out)
        out[far] = (s - 1.0) * np.log(y) - y - math.lgamma(s)
    return out


def positive_chi2_logsf(x: np.ndarray, n: int) -> np.ndarray:
    """log of positive_chi2_sf, finite far into the tail (bright stars reach χ² ~ 10⁶)."""
    x = np.asarray(x, np.float64)
    terms = [math.log(math.comb(n, k) / 2.0 ** n) + _chi2_logsf(x, k) for k in range(1, n + 1)]
    lp = np.logaddexp.reduce(np.stack(terms), axis=0)
    return np.where(x > 0, lp, 0.0)


def z_of_logp(lp: np.ndarray) -> np.ndarray:
    """One-sided Gaussian-equivalent significance of a log tail probability: norm.isf where it is representable,
    else the asymptote log Q(z) = −z²/2 − log z − ½ log 2π solved by iteration (monotone, no plateau)."""
    lp = np.minimum(np.asarray(lp, np.float64), 0.0)
    z = np.empty_like(lp)
    near = lp > -700.0
    z[near] = norm.isf(np.exp(lp[near]))
    far = ~near
    if far.any():
        a = -2.0 * lp[far] - math.log(2 * math.pi)
        zz = np.sqrt(a)
        for _ in range(8):
            zz = np.sqrt(np.maximum(a - 2.0 * np.log(zz), 1e-12))
        z[far] = zz
    return z


def z_of_p(p: np.ndarray) -> np.ndarray:
    """One-sided Gaussian-equivalent significance of a tail probability (logs keep it finite far out)."""
    p = np.clip(np.asarray(p, np.float64), 1e-300, 1.0)
    return norm.isf(p)


def combine_maps(name: str, maps: list[S.Map], mode: str = "sum") -> S.Map:
    """Detection map of several S/N maps of the same sky (the simultaneous pair, or a session's detectors).
    sum: Σ S/N / √n over the maps covering a pixel (optimal for equal S/N in each), re-normalised on its lower half.
    chi2: the positive χ² Σ max(S/N, 0)² as a Gaussian-equivalent significance (positive_chi2_sf); only where all
    maps cover. flux: the inverse-variance mean of the maps' fluxes where all have one (same units only)."""
    ok = np.logical_and.reduce([m.ok for m in maps]) if mode == "chi2" else np.logical_or.reduce([m.ok for m in maps])
    if mode == "sum":
        s = sum(np.where(m.ok, m.sig, 0.0) for m in maps)
        n = sum(m.ok.astype(np.int32) for m in maps)
        with np.errstate(divide="ignore", invalid="ignore"):
            snr = np.where(n > 0, s / np.sqrt(np.maximum(n, 1)), 0.0)
        sigma = robust_sigma(snr[ok]) if ok.sum() > 100 else 1.0
        snr = snr / sigma
    elif mode == "chi2":
        x = sum(np.square(np.clip(np.where(m.ok, m.sig, 0.0), 0.0, None)) for m in maps)
        snr = np.where(ok, z_of_logp(positive_chi2_logsf(x, len(maps))), 0.0)
        sigma = 1.0
    else:
        raise ValueError(f"mode {mode!r}")
    return S.Map(name, snr, np.full(snr.shape, np.nan), ok, sigma)


def flux_err(m: S.Map) -> np.ndarray:
    """1σ flux error per grid pixel in the map's flux units (× µJy per MJy/sr for µJy): flux / S/N."""
    with np.errstate(divide="ignore", invalid="ignore"):
        e = np.where(m.ok & (m.snr != 0), m.flux / m.snr, np.nan)
    return np.where(np.isfinite(e) & (e > 0), e, np.nan)


def limit_ab(m: S.Map, conv: float, nsigma: float = 5.0, inner: np.ndarray | None = None) -> float:
    """The map's nsigma point-source limit (AB): the median flux error over the searched pixels (or `inner`)."""
    e = flux_err(m)
    sel = m.ok if inner is None else (m.ok & inner)
    v = e[sel]
    v = v[np.isfinite(v)]
    if not v.size:
        return float("nan")
    return float(23.9 - 2.5 * math.log10(nsigma * float(np.median(v)) * conv))


def ab_of_ujy(f) -> np.ndarray:
    f = np.asarray(f, np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(f > 0, 23.9 - 2.5 * np.log10(f), np.nan)


def find_peaks(m: S.Map, thresh: float = 5.0, edge: int = C.STACK_EDGE_PX, negative: bool = False) -> pd.DataFrame:
    """stack.peaks (local maxima ≥ thresh). negative: the same on −S/N: noise peaks only (a false-alarm count)."""
    if negative:
        m = S.Map(m.name, -m.snr, m.flux, m.ok, m.sigma, None if m.k is None else m.k)
    return S.peaks(m, thresh=thresh, edge=edge)


def nearest(x: np.ndarray, y: np.ndarray, cx: np.ndarray, cy: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """For each (x, y): distance to the nearest (cx, cy) and its index (inf, -1 when there is none)."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    if not len(cx) or not len(x):
        return np.full(len(x), np.inf), np.full(len(x), -1)
    d, i = cKDTree(np.c_[cx, cy]).query(np.c_[x, y])
    return d, i


def local_peak(m: S.Map, x: float, y: float, r_px: float) -> tuple[float, float, float]:
    """The highest S/N within r_px of (x, y): (snr, x, y); (nan, nan, nan) where the map doesn't cover."""
    ny, nx = m.snr.shape
    x0, x1 = max(0, int(math.floor(x - r_px))), min(nx, int(math.ceil(x + r_px)) + 1)
    y0, y1 = max(0, int(math.floor(y - r_px))), min(ny, int(math.ceil(y + r_px)) + 1)
    if x1 <= x0 or y1 <= y0:
        return float("nan"), float("nan"), float("nan")
    yy, xx = np.mgrid[y0:y1, x0:x1]
    inside = (np.hypot(xx - x, yy - y) <= r_px) & m.ok[y0:y1, x0:x1]
    if not inside.any():
        return float("nan"), float("nan"), float("nan")
    s = np.where(inside, m.sig[y0:y1, x0:x1], -np.inf)
    j = int(np.argmax(s))
    return float(s.ravel()[j]), float(xx.ravel()[j]), float(yy.ravel()[j])


# --- shift-and-stack -------------------------------------------------------------------------------------------------


def track_shifts(mjd: np.ndarray, t_ref: float, rate_x: float, rate_y: float) -> list[tuple[float, float]]:
    """Per photo, the grid-px shift of a source moving (rate_x, rate_y) grid px per hour relative to t_ref (MJD)."""
    dt = (np.asarray(mjd, float) - t_ref) * HOURS_PER_DAY
    return [(float(rate_x * t), float(rate_y * t)) for t in dt]


def linear_trials(rate_max: float, span_h: float, tol_arcsec: float, rate_min: float = 0.0) -> np.ndarray:
    """Trial velocities (″/h, (n, 2): east, north) for a straight-line shift-and-stack over span_h hours, referred to
    the middle time: a square lattice of step 2√2 · tol / span, so any velocity within [rate_min, rate_max] has a trial
    whose track stays within tol arcsec of it at every time of the span."""
    step = 2.0 * math.sqrt(2.0) * tol_arcsec / span_h
    n = int(math.ceil(rate_max / step))
    t = np.arange(-n, n + 1) * step
    X, Y = np.meshgrid(t, t)
    r = np.hypot(X, Y)
    keep = (r <= rate_max + step / math.sqrt(2.0)) & (r >= max(0.0, rate_min - step / math.sqrt(2.0)))
    return np.c_[X[keep], Y[keep]]


def n_linear_trials(rate_max: float, span_h: float, tol_arcsec: float, rate_min: float = 0.0) -> float:
    """len(linear_trials(...)) from the areas (no array): (annulus area) / step²."""
    step = 2.0 * math.sqrt(2.0) * tol_arcsec / span_h
    return math.pi * (rate_max ** 2 - rate_min ** 2) / step ** 2


@dataclass(frozen=True)
class DistanceTrials:
    """Shift-and-stack along physical paths: a body at heliocentric distance d is displaced from its mid-visit
    position by ≈ (1 AU / d) × P(t) (Earth's barycentric offset from the mid-time, across the line of sight; exact to
    first order in 1/d) plus its own motion (≤ escape speed, √2 · 2π/√d AU/yr). The trial grid steps 1/d so that
    neighbouring paths differ by ≤ 2·tol at every photo time, and the own motion on a lattice of the same tolerance."""
    d_au: np.ndarray        # trial distances
    own_per_d: np.ndarray   # own-motion lattice points per distance
    p_max_arcsec: float     # max |P(t)| over the photos at 1 AU (arcsec per 1/AU)

    @property
    def n(self) -> int:
        return int(self.own_per_d.sum())


def parallax_offsets(mjd: np.ndarray, ra: float, dec: float, t_ref: float | None = None) -> np.ndarray:
    """(n, 2) east/north offsets (arcsec, per unit 1/d in 1/AU) of a distant body's direction at the photo times
    relative to t_ref (default: the middle of the times): −(E(t) − E(t_ref)) projected across the line of sight."""
    from skyshift_core.motion import observer_xyz_au

    mjd = np.asarray(mjd, float)
    if t_ref is None:
        t_ref = 0.5 * (mjd.min() + mjd.max())
    E = observer_xyz_au(np.r_[t_ref, mjd])
    dE = E[1:] - E[0]
    a, d = math.radians(ra), math.radians(dec)
    east = np.array([-math.sin(a), math.cos(a), 0.0])
    north = np.array([-math.sin(d) * math.cos(a), -math.sin(d) * math.sin(a), math.cos(d)])
    rad = 180.0 / math.pi * 3600.0
    return np.c_[-(dE @ east), -(dE @ north)] * rad


def distance_trials(mjd: np.ndarray, ra: float, dec: float, d_min: float, d_max: float, tol_arcsec: float
                    ) -> DistanceTrials:
    """The trial grid of DistanceTrials for photos at `mjd` of one spot (ra, dec), distances d_min..d_max AU."""
    mjd = np.asarray(mjd, float)
    P = parallax_offsets(mjd, ra, dec)
    pmax = float(np.max(np.hypot(P[:, 0], P[:, 1])))
    step_inv = 2.0 * tol_arcsec / max(pmax, 1e-9)
    inv = np.arange(1.0 / d_max, 1.0 / d_min + step_inv, step_inv)
    d = 1.0 / inv
    span_h = (mjd.max() - mjd.min()) * HOURS_PER_DAY
    # own motion: ≤ escape speed at d, as an angular rate (″/h) = v / d
    v_esc = math.sqrt(2.0) * 2.0 * math.pi / np.sqrt(d)   # AU/yr
    rate = v_esc / d * (180 / math.pi * 3600) / (365.25 * 24)
    own = np.array([max(1.0, n_linear_trials(r, span_h, tol_arcsec)) for r in rate])
    return DistanceTrials(d, np.ceil(own), pmax)



# =====================================================================================================================
# The session-stack step (module doc, top). Everything below is used by `sessionstack` and by link's stack rows.
# =====================================================================================================================

SOURCE_COLUMNS = {
    "stack_row": "int32", "ra": "float64", "dec": "float64", "x": "float32", "y": "float32", "detector": "int8",
    "session": "int32", "visit": "int16", "mjd": "float64", "n_exp": "int8", "n_exp_session": "int8",
    "snr_mf": "float32", "snr_mf_raw": "float32", "flux_mf_ujy": "float32", "flux_ujy": "float32",
    "flux_err_ujy": "float32", "snr": "float32", "glitch_share": "float32", "snr_rest": "float32", "glitch": "bool",
    "flux_chi2_nu": "float32", "n_pos": "int8", "rate_arcsec_h": "float32", "rate_err_arcsec_h": "float32",
    "pa_deg": "float32", "motion_snr": "float32", "pos_chi2_nu": "float32", "flags": "int32", "near_sat": "bool",
    "noise_k": "float32", "ra_psf": "float64", "dec_psf": "float64", "pos_err_arcsec": "float32",
}
PHOTO_COLUMNS = {
    "stack_row": "int32", "photo": "int8", "obs_id": "str", "mjd": "float64", "x": "float32", "y": "float32",
    "flux_ujy": "float32", "flux_err_ujy": "float32", "sky_mjysr": "float32", "chi2_nu": "float32", "flags": "int32",
    "pos_ok": "bool", "de_arcsec": "float32", "dn_arcsec": "float32", "pos_err_arcsec": "float32",
}


def session_key(obs_ids: list[str], detector: int) -> str:
    """The files' name for one session in one detector: its first obs_id, the photo count and a hash of all of them
    (two zones may hold different photos of one session)."""
    ids = sorted(str(o) for o in obs_ids)
    h = hashlib.sha1(",".join(ids).encode()).hexdigest()[:6]
    return f"{ids[0]}_n{len(ids)}_{h}D{int(detector)}"


def session_rel(key: str, photos: bool = False) -> str:
    return f"{C.SESSION_STACK_DIR}/{key}{'_photos' if photos else ''}.parquet"


def session_map_rel(key: str) -> str:
    return f"{C.SESSION_STACK_MAP_DIR}/{key}.npz"


def session_moving_rel(key: str) -> str:
    """Per-photo rows (with ra, dec) of the session's stacked sources that move (moving_rows): what link reads."""
    return f"{C.SESSION_STACK_DIR}/{key}_moving.parquet"


def index_rel(zone: str) -> str:
    """One row per (session key, obs_id) the zone's step wrote: link finds a photo list's stacks through these."""
    return f"{C.SESSION_STACK_DIR}/index/{zone}.parquet"


def session_tasks(photos: pd.DataFrame, min_exp: int = C.SESSION_STACK_MIN_EXP) -> list[dict]:
    """The zone's (session, detector) groups with >= min_exp photos: key, obs_ids (time order), urls, mjd, visit."""
    out = []
    p = photos.sort_values("mjd")
    for (s, d), g in p.groupby(["session", "detector"], sort=True):
        g = g.drop_duplicates("obs_id")
        if len(g) < min_exp:
            continue
        out.append({"key": session_key(list(g["obs_id"]), int(d)), "session": int(s), "detector": int(d),
                    "visit": int(g["visit"].iloc[0]) if "visit" in g else -1, "obs_ids": list(g["obs_id"].astype(str)),
                    "urls": list(g["url"].astype(str)) if "url" in g else [""] * len(g),
                    "mjd": [float(v) for v in g["mjd"]]})
    return out


# --- 1. one photo as v2 prepares it -----------------------------------------------------------------------------------


def dark_holes(sub: np.ndarray, noise: np.ndarray, bad: np.ndarray, nsigma: float = C.SESSION_STACK_HOLE_SIGMA,
               min_px: int = C.SESSION_STACK_HOLE_MIN_PX, grow: int = C.SESSION_STACK_HOLE_GROW_PX) -> np.ndarray:
    """Unflagged dark holes (docs/session-stacks.md §1: MWIR, next to PERSIST/OUTLIER pixels): clumps of >= min_px
    pixels (8-connected) below −nsigma × noise, grown by `grow` px. Returns the mask (bad pixels excluded)."""
    with np.errstate(invalid="ignore"):
        low = ~bad & np.isfinite(noise) & (sub < -nsigma * noise)
    if not low.any():
        return low
    lab, n = ndimage.label(low, structure=np.ones((3, 3), bool))
    sizes = np.bincount(lab.ravel(), minlength=n + 1)
    keep = sizes >= min_px
    keep[0] = False
    m = keep[lab]
    if grow > 0 and m.any():
        m = ndimage.binary_dilation(m, structure=np.ones((3, 3), bool), iterations=int(grow))
    return m & ~bad


def prepare(l2, obs_id: str, glow: bool | None = None, holes: bool | None = None,
            shared: "tuple | None" = None) -> tuple[S.Cut, dict]:
    """A Level 2 photo (core.level2) → stack.Cut: IMAGE − v2's background (dots.photo_context), bad pixels and dark
    holes 0 with noise inf, the glow subtracted (noise × its factor). meta: obs_id, detector, mjd, conv.
    shared: (detection background of the raw photo, its dark-hole mask) as dots.raw_background_holes made them for the
    dots of the same photo (the fused dots worker: computed once, same values)."""
    from skyshift_core.photometry import ujy_per_mjysr

    from .dots import photo_context
    glow = C.SESSION_STACK_GLOW if glow is None else glow
    holes = C.SESSION_STACK_HOLES if holes is None else holes
    t0 = time.process_time()
    ctx = photo_context(l2, pre=None if shared is None else shared[0])
    bad = np.asarray(ctx.bad, bool).copy()
    sub = np.asarray(ctx.sub, np.float64)
    noise = np.asarray(ctx.noise, np.float64)
    info = {"obs_id": obs_id, "hole_px": 0, "holes": 0}
    if holes:
        if shared is not None and shared[1] is not None:
            hm = shared[1]
        else:
            from skyshift_core.holes import dark_holes as core_dark_holes  # extraction v2's rule (#392), as dots.py
            hm, _ = core_dark_holes(sub, noise, bad, l2.flags, sigma=C.HOLE_SIGMA, min_px=C.HOLE_MIN_PX,
                                    max_px=C.HOLE_MAX_PX, near_bits=C.HOLE_NEAR_BITS, near_px=C.HOLE_NEAR_PX,
                                    grow_px=C.HOLE_GROW_PX)
        if hm.any():
            info["hole_px"] = int(hm.sum())
            info["holes"] = int(ndimage.label(hm)[1])
            bad |= hm
            sub = np.where(hm, 0.0, sub)
    noise = np.where(bad | ~np.isfinite(noise) | (noise <= 0), np.inf, noise)
    flags = np.asarray(l2.flags, np.int32)
    sub32, noise32 = sub.astype(np.float32), noise.astype(np.float32)
    del ctx, sub, noise
    info["prep_s"] = round(time.process_time() - t0, 2)
    t1 = time.process_time()
    if glow and getattr(l2, "epsf", None) is not None:
        try:
            sub32, noise32 = S.glow_cut(sub32, noise32, flags, l2.epsf)
            info["glow"] = True
        except Exception as e:  # noqa: BLE001 - no glow model: the photo as it is
            info["glow_error"] = repr(e)[:200]
    info["glow_s"] = round(time.process_time() - t1, 2)
    meta = {"obs_id": obs_id, "detector": int(l2.detector), "mjd": float(l2.mjd_avg), "conv": float(ujy_per_mjysr(l2.wcs)),
            "x0": 0, "y0": 0, "shape": list(sub32.shape)}
    return S.Cut(meta, sub32, noise32, flags, l2.wcs, l2.epsf), info


# --- 2. the session's grid and the matched filter at its pixel centres -----------------------------------------------


@dataclass
class Frame:
    """The session's grid: the reference photo's own pixel frame (its WCS, SIP included; within one pointing the other
    exposures are then pure shifts to 0.05 px over 64 px), grid (0, 0) = reference pixel (x0, y0), native pixels."""
    wcs: object
    x0: int
    y0: int
    nx: int
    ny: int

    def radec(self, x, y):
        ra, dec = self.wcs.pixel_to_world_values(np.asarray(x, float) + self.x0, np.asarray(y, float) + self.y0)
        return np.asarray(ra) % 360.0, np.asarray(dec)

    def xy(self, ra, dec):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            x, y = self.wcs.all_world2pix(np.asarray(ra, float), np.asarray(dec, float), 0, quiet=True)
        return np.asarray(x) - self.x0, np.asarray(y) - self.y0

    @property
    def pix_arcsec(self) -> float:
        ra, dec = self.radec([self.nx / 2, self.nx / 2 + 1], [self.ny / 2, self.ny / 2])
        return float(np.hypot((ra[1] - ra[0]) * math.cos(math.radians(dec[0])), dec[1] - dec[0]) * 3600.0)


def _edge_points(shape: tuple[int, int], step: int = 32) -> tuple[np.ndarray, np.ndarray]:
    ny, nx = shape
    xs = np.r_[np.arange(0, nx, step), nx - 1]
    ys = np.r_[np.arange(0, ny, step), ny - 1]
    x = np.r_[xs, xs, np.zeros(len(ys)), np.full(len(ys), nx - 1)]
    y = np.r_[np.zeros(len(xs)), np.full(len(xs), ny - 1), ys, ys]
    return x.astype(float), y.astype(float)


def session_frame(cuts: list[S.Cut], ref: int | None = None, max_extend: int = C.SESSION_STACK_MAX_EXTEND_PX) -> Frame:
    """The grid holding every photo of the session: the middle photo's pixel frame, extended to the others' edges
    (at most max_extend px beyond the reference)."""
    ref = len(cuts) // 2 if ref is None else ref
    rc = cuts[ref]
    ny0, nx0 = rc.sub.shape
    lo_x, hi_x, lo_y, hi_y = 0.0, nx0 - 1.0, 0.0, ny0 - 1.0
    for c in cuts:
        ex, ey = _edge_points(c.sub.shape)
        ra, dec = c.wcs.pixel_to_world_values(ex, ey)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            x, y = rc.wcs.all_world2pix(np.asarray(ra), np.asarray(dec), 0, quiet=True)
        ok = np.isfinite(x) & np.isfinite(y)
        if ok.any():
            lo_x, hi_x = min(lo_x, float(x[ok].min())), max(hi_x, float(x[ok].max()))
            lo_y, hi_y = min(lo_y, float(y[ok].min())), max(hi_y, float(y[ok].max()))
    lo_x, lo_y = max(lo_x, -max_extend), max(lo_y, -max_extend)
    hi_x, hi_y = min(hi_x, nx0 - 1 + max_extend), min(hi_y, ny0 - 1 + max_extend)
    x0, y0 = int(math.floor(lo_x)), int(math.floor(lo_y))
    return Frame(rc.wcs, x0, y0, int(math.ceil(hi_x)) - x0 + 1, int(math.ceil(hi_y)) - y0 + 1)


def _tile_offsets(frame: Frame, cut: S.Cut, tiles: np.ndarray, sub: int = 3) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per grid tile (gx0, gx1, gy0, gy1): the photo's mean pixel offset (x − u, y − v) and the largest deviation from
    it over a sub × sub lattice of the tile (px; inf where the photo's WCS gives nothing)."""
    f = (np.arange(sub) + 0.5) / sub
    gx = tiles[:, 0, None] + (tiles[:, 1] - tiles[:, 0])[:, None] * f[None, :] - 0.5
    gy = tiles[:, 2, None] + (tiles[:, 3] - tiles[:, 2])[:, None] * f[None, :] - 0.5
    U = np.repeat(gx[:, None, :], sub, axis=1)
    V = np.repeat(gy[:, :, None], sub, axis=2)
    ra, dec = frame.radec(U.ravel(), V.ravel())
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        x, y = cut.wcs.all_world2pix(ra, dec, 0, quiet=True)
    ox = (np.asarray(x) - U.ravel()).reshape(U.shape)
    oy = (np.asarray(y) - V.ravel()).reshape(V.shape)
    mx, my = ox.mean(axis=(1, 2)), oy.mean(axis=(1, 2))
    dev = np.maximum(np.abs(ox - mx[:, None, None]).max(axis=(1, 2)), np.abs(oy - my[:, None, None]).max(axis=(1, 2)))
    return mx, my, np.where(np.isfinite(dev), dev, np.inf)


def _photo_tiles(frame: Frame, cut: S.Cut, tile: int, tile_min: int, max_dev: float) -> tuple[np.ndarray, np.ndarray, dict]:
    """The grid tiles over this photo, split until the photo is a pure shift on each: (tiles (n, 4) as gx0, gx1, gy0,
    gy1, offsets (n, 2), stats). Tiles that don't reach the photo are dropped; tiles still beyond max_dev at tile_min
    are kept and counted (`dev_kept`)."""
    ny, nx = cut.sub.shape
    ex, ey = _edge_points(cut.sub.shape, 64)
    ra, dec = cut.wcs.pixel_to_world_values(ex, ey)
    gx, gy = frame.xy(ra, dec)
    ok = np.isfinite(gx) & np.isfinite(gy)
    st = {"tiles": 0, "split": 0, "dev_max": 0.0, "dev_kept": 0}
    if not ok.any():
        return np.zeros((0, 4), np.int64), np.zeros((0, 2)), st
    xa = max(0, int(math.floor(gx[ok].min())) - 1)
    xb = min(frame.nx, int(math.ceil(gx[ok].max())) + 2)
    ya = max(0, int(math.floor(gy[ok].min())) - 1)
    yb = min(frame.ny, int(math.ceil(gy[ok].max())) + 2)
    if xb <= xa or yb <= ya:
        return np.zeros((0, 4), np.int64), np.zeros((0, 2)), st
    xs, ys = np.arange(xa, xb, tile), np.arange(ya, yb, tile)
    t = np.array([(x, min(x + tile, xb), y, min(y + tile, yb)) for y in ys for x in xs], np.int64).reshape(-1, 4)
    out_t, out_o = [], []
    size = tile
    while len(t):
        mx, my, dev = _tile_offsets(frame, cut, t)
        fin = np.isfinite(mx) & np.isfinite(my)
        on = fin & (t[:, 0] + mx < nx + 3) & (t[:, 1] + mx > -3) & (t[:, 2] + my < ny + 3) & (t[:, 3] + my > -3)
        good = on & (dev <= max_dev)
        out_t.append(t[good])
        out_o.append(np.c_[mx[good], my[good]])
        if good.any():
            st["dev_max"] = max(st["dev_max"], float(dev[good].max()))
        rest = on & ~good
        if not rest.any():
            break
        if size // 2 < tile_min:  # can't split further: keep them as they are (counted)
            st["dev_kept"] += int(rest.sum())
            out_t.append(t[rest])
            out_o.append(np.c_[mx[rest], my[rest]])
            st["dev_max"] = max(st["dev_max"], float(np.nanmax(np.where(np.isfinite(dev[rest]), dev[rest], 0.0))))
            break
        size //= 2
        st["split"] += int(rest.sum())
        parts = []
        for x0, x1, y0, y1 in t[rest]:
            xm, ym = min(x0 + size, x1), min(y0 + size, y1)
            for a, b in ((x0, xm), (xm, x1)):
                for c, d in ((y0, ym), (ym, y1)):
                    if b > a and d > c:
                        parts.append((a, b, c, d))
        t = np.array(parts, np.int64).reshape(-1, 4)
    tiles = np.concatenate(out_t) if out_t else np.zeros((0, 4), np.int64)
    offs = np.concatenate(out_o) if out_o else np.zeros((0, 2))
    st["tiles"] = int(len(tiles))
    st["dev_max"] = round(st["dev_max"], 4)
    return tiles, offs, st


@dataclass
class SessionMaps:
    frame: Frame
    num: np.ndarray               # Σ over photos of Σ w·d·K at each grid pixel centre
    den: np.ndarray               # Σ w·K²
    ncov: np.ndarray              # photos that measure each grid pixel (good pixels hold >= half the kernel's weight)
    info: dict = field(default_factory=dict)


def accumulate_session(cuts: list[S.Cut], frame: Frame, half: int = C.MF_KERNEL_HALF, tile: int = C.SESSION_STACK_TILE_PX,
                       tile_min: int = C.SESSION_STACK_TILE_MIN_PX,
                       max_dev: float = C.SESSION_STACK_TILE_MAX_DEV_PX) -> SessionMaps:
    """The session's matched filter at the grid's pixel centres (module doc, 2): per photo and grid tile, the photo's
    pixels around (u + sx, v + sy) correlated with the ePSF of a source at the tile's sub-pixel phase (psf.mf_kernel,
    the zone at the tile's centre), so photo pixels are only ever weighted, never interpolated."""
    from . import psf
    ny, nx = frame.ny, frame.nx
    num, den, ncov = np.zeros((ny, nx)), np.zeros((ny, nx)), np.zeros((ny, nx), np.int16)
    info = {"photos": []}
    for cut in cuts:
        t0 = time.process_time()
        w, sw = psf._weights(cut.sub, cut.noise, ~np.isfinite(cut.noise))
        ph, pw = cut.sub.shape
        tiles, offs, st = _photo_tiles(frame, cut, tile, tile_min, max_dev)
        kcache: dict = {}
        epsf = cut.epsf
        for (gx0, gx1, gy0, gy1), (ox, oy) in zip(tiles, offs):
            sx, sy = int(round(ox)), int(round(oy))
            phx, phy = ox - sx, oy - sy
            cx = min(max(0.5 * (gx0 + gx1 - 1) + ox, 0.0), pw - 1.0)
            cy = min(max(0.5 * (gy0 + gy1 - 1) + oy, 0.0), ph - 1.0)
            zone = int(np.asarray(epsf.zone(np.array([cx]), np.array([cy]))).ravel()[0]) if epsf is not None else -1
            kk = (zone, round(phx, 3), round(phy, 3))
            if kk not in kcache:
                K = psf.mf_kernel(epsf, zone, half, (phx, phy))
                kcache[kk] = (K, K * K, float((K * K).sum()))
            K, K2, k2sum = kcache[kk]
            px0, px1 = gx0 + sx - half, gx1 + sx + half
            py0, py1 = gy0 + sy - half, gy1 + sy + half
            a0, a1, b0, b1 = max(px0, 0), min(px1, pw), max(py0, 0), min(py1, ph)
            if a1 <= a0 or b1 <= b0:
                continue
            bw = np.zeros((py1 - py0, px1 - px0))
            bs = np.zeros_like(bw)
            bw[b0 - py0:b1 - py0, a0 - px0:a1 - px0] = w[b0:b1, a0:a1]
            bs[b0 - py0:b1 - py0, a0 - px0:a1 - px0] = sw[b0:b1, a0:a1]
            tn = ndimage.correlate(bs, K, mode="constant")[half:-half, half:-half]
            td = ndimage.correlate(bw, K2, mode="constant")[half:-half, half:-half]
            # measured: good pixels hold >= half the kernel's weight (a bright star's own variance lowers den, not this)
            tc = ndimage.correlate((bw > 0).astype(np.float64), K2, mode="constant")[half:-half, half:-half]
            num[gy0:gy1, gx0:gx1] += tn
            den[gy0:gy1, gx0:gx1] += td
            ncov[gy0:gy1, gx0:gx1] += (tc >= 0.5 * k2sum).astype(np.int16)
        info["photos"].append({"obs_id": cut.meta.get("obs_id"), **st, "kernels": len(kcache),
                               "mf_s": round(time.process_time() - t0, 2)})
    return SessionMaps(frame, num, den, ncov, info)


def session_map(sm: SessionMaps, min_cover: int = C.SESSION_STACK_MIN_COVER, name: str = "session") -> S.Map:
    """S/N = num/√den where >= min_cover photos measure the pixel, divided by the map's own noise (lower half)."""
    ok = (sm.ncov >= min_cover) & (sm.den > 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        raw = np.where(ok, sm.num / np.sqrt(np.where(ok, sm.den, 1.0)), 0.0)
        flux = np.where(ok, sm.num / np.where(ok, sm.den, 1.0), np.nan)
    sigma = robust_sigma(raw[ok]) if ok.sum() > 100 else 1.0
    return S.Map(name, raw / sigma, flux, ok, sigma)


# --- 3. detection ------------------------------------------------------------------------------------------------------


def local_map(m: S.Map) -> tuple[S.Map, tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """The S/N map over its own local scatter around bright stars (stack.local_significance on native pixels)."""
    return S.local_significance(m, pix=C.STACK_NATIVE_PIX_ARCSEC)


def _off_rims(pk: pd.DataFrame, centres: tuple[np.ndarray, np.ndarray, np.ndarray]) -> pd.DataFrame:
    """Peaks outside every saturated hole's radius + STACK_GLOW_RIM_ARCSEC (where rim_peaks keeps only the star)."""
    x, y, r0 = centres
    hole = r0 > 0
    if not len(pk) or not hole.any():
        return pk
    rim = C.STACK_GLOW_RIM_ARCSEC / C.STACK_NATIVE_PIX_ARCSEC
    d, i = cKDTree(np.c_[x[hole], y[hole]]).query(np.c_[pk["x"], pk["y"]], k=1)
    return pk[~(d <= r0[hole][i] + rim)]


def fa_threshold(ml: S.Map, area_deg2: float, centres: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None,
                 base: float = C.SESSION_STACK_SNR, fa: float = C.SESSION_STACK_FA_PER_DEG2,
                 cap: float = C.SESSION_STACK_SNR_MAX, edge: int = C.SESSION_STACK_EDGE_PX) -> tuple[float, dict]:
    """The detection threshold: base, raised to just above the k-th highest peak of the negated local map (k = fa ×
    area; the same local noise as detection, saturated holes' rims left out as rim_peaks does), capped at `cap`.
    Returns (threshold, negative peaks per deg² at base and at the threshold: the chance level of the positives)."""
    neg = find_peaks(ml, base, edge=edge, negative=True)
    if centres is not None:
        neg = _off_rims(neg, centres)
    v = np.sort(neg["snr"].to_numpy(np.float64))[::-1] if len(neg) else np.zeros(0)
    k = int(math.floor(fa * area_deg2))
    thr = base
    if v.size > k:
        thr = min(cap, max(base, float(v[k]) + 1e-3))
    n_at = int((v >= thr).sum())
    return thr, {"neg_per_deg2_at_base": round(v.size / max(area_deg2, 1e-9), 2),
                 "neg_per_deg2": round(n_at / max(area_deg2, 1e-9), 2), "neg_n": n_at}


def detect_session(m: S.Map, frame: Frame, thr: float, sat_xy: tuple[np.ndarray, np.ndarray] | None = None,
                   edge: int = C.SESSION_STACK_EDGE_PX, local=None) -> pd.DataFrame:
    """Peaks of the session map >= thr against the local noise around bright stars (stack.local_significance, rim_peaks
    for saturated holes), on native pixels; near_sat within DETECT_SAT_GUARD_PX of a saturated pixel. local: local_map's
    result when already computed."""
    ml, centres = local if local is not None else local_map(m)
    pk = S.peaks(ml, thresh=thr, edge=edge)
    pk = S.rim_peaks(pk, ml, centres, pix=C.STACK_NATIVE_PIX_ARCSEC)
    cols = ["x", "y", "snr_mf", "snr_mf_raw", "noise_k", "near_sat"]
    if not len(pk):
        return pd.DataFrame({c: pd.Series(dtype=bool if c == "near_sat" else "float64") for c in cols})
    ix = np.clip(np.rint(pk["x"].to_numpy(float)).astype(int), 0, frame.nx - 1)
    iy = np.clip(np.rint(pk["y"].to_numpy(float)).astype(int), 0, frame.ny - 1)
    k = ml.k[iy, ix] if ml.k is not None else np.ones(len(pk))
    near = np.zeros(len(pk), bool)
    if sat_xy is not None and len(sat_xy[0]):
        d, _ = cKDTree(np.c_[sat_xy[0], sat_xy[1]]).query(np.c_[pk["x"], pk["y"]], k=1)
        near = d < C.DETECT_SAT_GUARD_PX
    return pd.DataFrame({"x": pk["x"].to_numpy(float), "y": pk["y"].to_numpy(float), "snr_mf": pk["snr"].to_numpy(float),
                         "snr_mf_raw": m.snr[iy, ix], "noise_k": k, "near_sat": near})


def saturated_xy(cuts: list[S.Cut], frame: Frame) -> tuple[np.ndarray, np.ndarray]:
    """Grid positions of every photo's OVERFLOW/NONLINEAR pixels."""
    from skyshift_core import SAT_BITS
    xs, ys = [], []
    for c in cuts:
        iy, ix = np.nonzero((c.flags & SAT_BITS) != 0)
        if not ix.size:
            continue
        ra, dec = c.wcs.pixel_to_world_values(ix.astype(float), iy.astype(float))
        gx, gy = frame.xy(ra, dec)
        ok = np.isfinite(gx) & np.isfinite(gy)
        xs.append(gx[ok])
        ys.append(gy[ok])
    return (np.concatenate(xs), np.concatenate(ys)) if xs else (np.zeros(0), np.zeros(0))


# --- 4. every stacked source in every photo ----------------------------------------------------------------------------


def measure_photos(cuts: list[S.Cut], ra: np.ndarray, dec: np.ndarray,
                   pos_min_snr: float = C.SESSION_STACK_POS_MIN_SNR) -> pd.DataFrame:
    """Per photo and stacked source inside it: ePSF flux + local sky at the stacked position (psf.psf_photometry), and
    where that S/N >= pos_min_snr a free ePSF position (psf.psf_position) as east/north offsets from the stacked
    position (arcsec) with its error (+ SESSION_STACK_MOVE_FLOOR_ARCSEC). Columns PHOTO_COLUMNS (stack_row = index)."""
    from skyshift_core.astrometry import tangent_offset_arcsec

    from . import psf
    from .dots import or3x3
    parts = []
    ra, dec = np.asarray(ra, float), np.asarray(dec, float)
    for i, c in enumerate(cuts):
        ny, nx = c.sub.shape
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            x, y = c.wcs.all_world2pix(ra, dec, 0, quiet=True)
        x, y = np.asarray(x, float), np.asarray(y, float)
        inside = np.isfinite(x) & np.isfinite(y) & (x > -0.5) & (x < nx - 0.5) & (y > -0.5) & (y < ny - 0.5)
        j = np.flatnonzero(inside)
        if not j.size:
            continue
        bad = ~np.isfinite(c.noise)
        noise = c.noise.astype(np.float64)
        ph = psf.psf_photometry(c.sub, x[j], y[j], c.epsf, noise=noise, bad=bad)
        conv = float(c.meta.get("conv", 1.0))
        with np.errstate(divide="ignore", invalid="ignore"):
            snr = ph.flux / ph.flux_err
        fit = np.isfinite(snr) & (snr >= pos_min_snr)
        de = np.full(j.size, np.nan)
        dn = np.full(j.size, np.nan)
        perr = np.full(j.size, np.nan)
        ok = np.zeros(j.size, bool)
        if fit.any():
            pp = psf.psf_position(c.sub, x[j], y[j], c.epsf, noise=noise, bad=bad, fit=fit)
            ok = pp.ok & fit
            if ok.any():
                r2, d2 = c.wcs.pixel_to_world_values(pp.x[ok], pp.y[ok])
                e, n = tangent_offset_arcsec(ra[j[ok]], dec[j[ok]], np.asarray(r2), np.asarray(d2))
                de[ok], dn[ok] = e, n
                perr[ok] = np.hypot(np.sqrt(0.5 * (pp.x_err[ok] ** 2 + pp.y_err[ok] ** 2)) * C.STACK_NATIVE_PIX_ARCSEC,
                                    C.SESSION_STACK_MOVE_FLOOR_ARCSEC)
        ixp = np.clip(np.rint(x[j]).astype(int), 0, nx - 1)
        iyp = np.clip(np.rint(y[j]).astype(int), 0, ny - 1)
        f3 = or3x3(c.flags)[iyp, ixp]
        parts.append(pd.DataFrame({
            "stack_row": j.astype(np.int32), "photo": np.int8(i), "obs_id": str(c.meta.get("obs_id", "")),
            "mjd": float(c.meta["mjd"]), "x": x[j].astype(np.float32), "y": y[j].astype(np.float32),
            "flux_ujy": (ph.flux * conv).astype(np.float32), "flux_err_ujy": (ph.flux_err * conv).astype(np.float32),
            "sky_mjysr": ph.sky.astype(np.float32), "chi2_nu": ph.chi2_nu.astype(np.float32),
            "flags": f3.astype(np.int32), "pos_ok": ok, "de_arcsec": de.astype(np.float32),
            "dn_arcsec": dn.astype(np.float32), "pos_err_arcsec": perr.astype(np.float32)}))
    if not parts:
        return pd.DataFrame({k: pd.Series(dtype=v if v != "str" else object) for k, v in PHOTO_COLUMNS.items()})
    return pd.concat(parts, ignore_index=True)


def summarise_sources(src: pd.DataFrame, ph: pd.DataFrame, n_session: int) -> pd.DataFrame:
    """Per stacked source from its per-photo rows: n_exp, mjd (mean of the photos that measured it), flux (inverse-
    variance mean) and snr, glitch_share (max S/N² / Σ S/N², positive parts), snr_rest (the mean without the strongest
    photo), glitch, flux_chi2_nu, flags (OR), and the in-session motion: a weighted line through the per-photo positions
    (rate, its error, PA east of north, motion_snr = rate / error; errors × √χ²_ν when above 1)."""
    n = len(src)
    out = src.copy()
    r = ph["stack_row"].to_numpy(np.int64) if len(ph) else np.zeros(0, np.int64)
    f = ph["flux_ujy"].to_numpy(np.float64) if len(ph) else np.zeros(0)
    e = ph["flux_err_ujy"].to_numpy(np.float64) if len(ph) else np.zeros(0)
    good = np.isfinite(f) & np.isfinite(e) & (e > 0)
    w = np.where(good, 1.0 / np.where(good, e, 1.0) ** 2, 0.0)
    fz = np.where(good, f, 0.0)
    nexp = np.bincount(r, good, n).astype(np.int64)
    sw = np.bincount(r, w, n)
    sfw = np.bincount(r, w * fz, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        mean = sfw / sw
        err = 1.0 / np.sqrt(sw)
        snr_i = np.where(good, fz / np.where(good, e, 1.0), 0.0)
    pos2 = np.clip(snr_i, 0.0, None) ** 2
    tot2 = np.bincount(r, pos2, n)
    mx2 = np.zeros(n)
    np.maximum.at(mx2, r, pos2)
    # the strongest photo (highest S/N) per source, then the S/N of the rest
    is_top = np.zeros(len(r), bool)
    if len(r):
        order = np.lexsort((-snr_i, r))
        first = np.r_[True, r[order][1:] != r[order][:-1]]
        is_top[order[first]] = True
    sw_rest = np.bincount(r, np.where(is_top, 0.0, w), n)
    sfw_rest = np.bincount(r, np.where(is_top, 0.0, w * fz), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        share = np.where(tot2 > 0, mx2 / tot2, np.nan)
        snr_rest = np.where(sw_rest > 0, sfw_rest / np.sqrt(sw_rest), 0.0)
        chi = np.bincount(r, np.where(good, w * (fz - mean[r]) ** 2, 0.0), n) if n else np.zeros(0)
        chi_nu = np.where(nexp > 1, chi / np.maximum(nexp - 1, 1), np.nan)
    mjd = np.bincount(r, np.where(good, ph["mjd"].to_numpy(np.float64), 0.0) if len(ph) else np.zeros(0), n) \
        / np.maximum(nexp, 1)
    flags = np.zeros(n, np.int64)
    if len(r):
        np.bitwise_or.at(flags, r, ph["flags"].to_numpy(np.int64) & 0xFFFFFFFF)
    # motion: e(t) = a + b (t − t̄), weighted, per axis
    if len(ph):
        po = ph["pos_ok"].to_numpy(bool) & np.isfinite(ph["de_arcsec"].to_numpy(np.float64))
        pe = ph["pos_err_arcsec"].to_numpy(np.float64)
        t = ph["mjd"].to_numpy(np.float64) * 24.0
        de = np.where(po, ph["de_arcsec"].to_numpy(np.float64), 0.0)
        dn = np.where(po, ph["dn_arcsec"].to_numpy(np.float64), 0.0)
    else:
        po, pe, t, de, dn = (np.zeros(0, bool), np.zeros(0), np.zeros(0), np.zeros(0), np.zeros(0))
    wp = np.where(po, 1.0 / np.where(po, pe, 1.0) ** 2, 0.0)
    npos = np.bincount(r, po, n).astype(np.int64)
    S0 = np.bincount(r, wp, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        tm = np.bincount(r, wp * t, n) / S0
        dt = np.where(po, t - tm[r], 0.0) if n else np.zeros(0)
        Stt = np.bincount(r, wp * dt * dt, n)
        ae, an = np.bincount(r, wp * de, n) / S0, np.bincount(r, wp * dn, n) / S0
        be, bn = np.bincount(r, wp * dt * de, n) / Stt, np.bincount(r, wp * dt * dn, n) / Stt
        re = de - (ae[r] + be[r] * dt) if n else np.zeros(0)
        rn = dn - (an[r] + bn[r] * dt) if n else np.zeros(0)
        dof = 2 * npos - 4
        chi_p = np.bincount(r, np.where(po, wp * (re * re + rn * rn), 0.0), n)
        pchi = np.where(dof > 0, chi_p / np.maximum(dof, 1), np.nan)
        infl = np.sqrt(np.maximum(1.0, np.nan_to_num(pchi, nan=1.0)))
        sb = infl / np.sqrt(Stt)
        rate = np.hypot(be, bn)
        msnr = rate / sb
    fit = (npos >= 2) & (Stt > 0) & np.isfinite(rate)
    out["mjd"] = np.where(nexp > 0, mjd, np.nan)
    out["n_exp"] = nexp.astype(np.int8)
    out["n_exp_session"] = np.int8(n_session)
    out["flux_ujy"] = np.where(nexp > 0, mean, np.nan).astype(np.float32)
    out["flux_err_ujy"] = np.where(nexp > 0, err, np.nan).astype(np.float32)
    with np.errstate(divide="ignore", invalid="ignore"):
        out["snr"] = (out["flux_ujy"].to_numpy(np.float64) / out["flux_err_ujy"].to_numpy(np.float64)).astype(np.float32)
    out["glitch_share"] = share.astype(np.float32)
    out["snr_rest"] = snr_rest.astype(np.float32)
    out["glitch"] = (nexp >= 2) & (snr_rest < C.SESSION_STACK_GLITCH_REST_SNR)
    out["flux_chi2_nu"] = chi_nu.astype(np.float32)
    out["n_pos"] = npos.astype(np.int8)
    out["rate_arcsec_h"] = np.where(fit, rate, np.nan).astype(np.float32)
    out["rate_err_arcsec_h"] = np.where(fit, sb, np.nan).astype(np.float32)
    out["pa_deg"] = np.where(fit, np.rad2deg(np.arctan2(be, bn)) % 360.0, np.nan).astype(np.float32)
    out["motion_snr"] = np.where(fit, msnr, np.nan).astype(np.float32)
    out["pos_chi2_nu"] = pchi.astype(np.float32)
    # the position from the photos' own ePSF fits (the line's value at its weighted mean time): the map peak is a
    # parabola on native pixels, the fits are not; error 1/√Σw (× √χ²_ν when above 1)
    from skyshift_core import tangent_deproject
    has = (npos >= 1) & np.isfinite(ae) & np.isfinite(an)
    rp, dp = tangent_deproject(src["ra"].to_numpy(np.float64), src["dec"].to_numpy(np.float64),
                               np.where(has, ae, 0.0), np.where(has, an, 0.0))
    with np.errstate(divide="ignore", invalid="ignore"):
        perr = infl / np.sqrt(S0)
    out["ra_psf"] = np.where(has, np.asarray(rp) % 360.0, np.nan)
    out["dec_psf"] = np.where(has, dp, np.nan)
    out["pos_err_arcsec"] = np.where(has, perr, np.nan).astype(np.float32)
    out["flags"] = (flags & 0xFFFFFFFF).astype(np.uint32).view(np.int32)
    return out


# --- the whole session -------------------------------------------------------------------------------------------------


def stack_session(cuts: list[S.Cut], task: dict, maps: bool = False) -> tuple[pd.DataFrame, pd.DataFrame, dict, dict | None]:
    """One session in one detector from its prepared photos: (sources, per-photo rows, metadata, maps or None)."""
    t0 = time.process_time()
    order = np.argsort([c.meta["mjd"] for c in cuts], kind="stable")
    cuts = [cuts[i] for i in order]
    frame = session_frame(cuts)
    sm = accumulate_session(cuts, frame)
    t_mf = time.process_time() - t0
    m = session_map(sm)
    pix = frame.pix_arcsec
    area = float(m.ok.sum()) * (pix / 3600.0) ** 2
    t1 = time.process_time()
    local = local_map(m)
    thr, fa = fa_threshold(local[0], area, local[1])
    sat = saturated_xy(cuts, frame)
    pk = detect_session(m, frame, thr, sat, local=local)
    ra, dec = frame.radec(pk["x"].to_numpy(float), pk["y"].to_numpy(float))
    ix = np.clip(np.rint(pk["x"].to_numpy(float)).astype(int), 0, frame.nx - 1)
    iy = np.clip(np.rint(pk["y"].to_numpy(float)).astype(int), 0, frame.ny - 1)
    conv = float(np.median([c.meta.get("conv", 1.0) for c in cuts]))
    src = pd.DataFrame({"stack_row": np.arange(len(pk), dtype=np.int32), "ra": np.asarray(ra, float),
                        "dec": np.asarray(dec, float), "x": pk["x"].to_numpy(np.float32), "y": pk["y"].to_numpy(np.float32),
                        "detector": np.int8(task["detector"]), "session": np.int32(task.get("session", -1)),
                        "visit": np.int16(task.get("visit", -1)), "snr_mf": pk["snr_mf"].to_numpy(np.float32),
                        "snr_mf_raw": pk["snr_mf_raw"].to_numpy(np.float32),
                        "flux_mf_ujy": (m.flux[iy, ix] * conv).astype(np.float32),
                        "near_sat": pk["near_sat"].to_numpy(bool), "noise_k": pk["noise_k"].to_numpy(np.float32)})
    t_det = time.process_time() - t1
    t2 = time.process_time()
    ph = measure_photos(cuts, src["ra"].to_numpy(), src["dec"].to_numpy())
    src = summarise_sources(src, ph, len(cuts))
    src = src[list(SOURCE_COLUMNS)].astype(SOURCE_COLUMNS)
    t_meas = time.process_time() - t2
    lim = depth_ab(sm, m, conv)
    meta = {"key": task.get("key"), "session": task.get("session"), "visit": task.get("visit"),
            "detector": int(task["detector"]), "obs_ids": [c.meta.get("obs_id") for c in cuts],
            "mjd": [float(c.meta["mjd"]) for c in cuts],
            "frame": {"x0": frame.x0, "y0": frame.y0, "nx": frame.nx, "ny": frame.ny, "pix_arcsec": round(pix, 4)},
            "sigma_norm": round(float(m.sigma), 4), "threshold": round(thr, 3), "area_deg2": round(area, 4),
            "false_alarms": fa, "n_sources": int(len(src)), "per_deg2": round(len(src) / max(area, 1e-9), 1),
            "n_glitch": int(src["glitch"].sum()), "n_near_sat": int(src["near_sat"].sum()),
            "n_moving_5sigma": int((src["motion_snr"].fillna(0) >= 5).sum()), "depth_ab_5sigma": lim,
            "tiles": sm.info["photos"],
            "cpu_s": {"mf": round(t_mf, 1), "detect": round(t_det, 1), "measure": round(t_meas, 1)},
            "settings": {"tile_px": C.SESSION_STACK_TILE_PX, "max_dev_px": C.SESSION_STACK_TILE_MAX_DEV_PX,
                         "fa_per_deg2": C.SESSION_STACK_FA_PER_DEG2, "base_snr": C.SESSION_STACK_SNR,
                         "glow": C.SESSION_STACK_GLOW, "holes": C.SESSION_STACK_HOLES, "kernel_half": C.MF_KERNEL_HALF}}
    mp = None
    if maps:
        # flux (MJy/sr, the ePSF amplitude; saturated stars clipped to float16's range) and its error 1/√den: the
        # shift-and-stack sums num = flux/err², den = 1/err²; err is 0 where no photo measures the pixel
        with np.errstate(divide="ignore", invalid="ignore"):
            pos = sm.den > 0
            fl = np.where(pos, sm.num / np.where(pos, sm.den, 1.0), 0.0)
            er = np.where(pos, 1.0 / np.sqrt(np.where(pos, sm.den, 1.0)), 0.0)
        mp = {"flux": np.clip(fl, -6.0e4, 6.0e4).astype(np.float16), "err": er.astype(np.float16),
              "ncov": sm.ncov.astype(np.int8),
              "header": np.asarray(frame.wcs.to_header(relax=True).tostring()), "x0": frame.x0, "y0": frame.y0,
              "conv": conv, "sigma_norm": float(m.sigma)}
    return src, ph, meta, mp


def depth_ab(sm: SessionMaps, m: S.Map, conv: float) -> dict:
    """5σ point-source limit (AB) of the session map: median over the searched pixels, and where every photo covers."""
    with np.errstate(divide="ignore", invalid="ignore"):
        e = np.where(m.ok & (sm.den > 0), m.sigma / np.sqrt(np.where(sm.den > 0, sm.den, 1.0)), np.nan)
    out = {}
    full = m.ok & (sm.ncov >= (int(sm.ncov.max()) if sm.ncov.size else 0))
    for name, sel in (("searched", m.ok), ("full", full)):
        v = e[sel]
        v = v[np.isfinite(v) & (v > 0)]
        if v.size:
            out[name] = round(float(23.9 - 2.5 * math.log10(5.0 * float(np.median(v)) * conv)), 3)
    return out


# --- the step ----------------------------------------------------------------------------------------------------------

_EPSF_CACHE: dict = {}


def load_photo(url: str):
    """IMAGE, FLAGS, VARIANCE and (once per worker, detector and ePSF calibration file) the EPSF table of one photo."""
    from skyshift_core.level2 import open_level2

    from .dots import epsf_calib_id
    l2 = open_level2(url, variance=True, epsf=not _EPSF_CACHE)
    cid = epsf_calib_id(l2.header)
    key = (int(l2.detector), cid) if cid else None
    if l2.epsf is not None:
        if key:
            _EPSF_CACHE[key] = l2.epsf
    elif key and key in _EPSF_CACHE:
        l2.epsf = _EPSF_CACHE[key]
    else:
        l2.epsf = open_level2(url, region=(0, 1, 0, 1), epsf=True).epsf
        if key and l2.epsf is not None:
            _EPSF_CACHE[key] = l2.epsf
    return l2


def _parquet(df: pd.DataFrame, meta: dict | None = None) -> bytes:
    import pyarrow as pa
    import pyarrow.parquet as pq
    t = pa.Table.from_pandas(df, preserve_index=False)
    if meta is not None:
        t = t.replace_schema_metadata({**(t.schema.metadata or {}), b"skyshift": json.dumps(meta, default=str).encode()})
    if C.PARQUET_SLIM:  # the same table in fewer bytes (config PARQUET_SLIM: today's stack files are ~40% larger)
        from .dots import parquet_bytes
        return parquet_bytes(t, slim=True)
    buf = io.BytesIO()
    pq.write_table(t, buf, compression="zstd")
    return buf.getvalue()


def npz_bytes(arrays: dict, slim: bool | None = None) -> bytes:
    """A map file: np.savez_compressed (zip deflate); slim (default config PARQUET_SLIM) the same .npy members as bzip2
    zip entries, which np.load reads unchanged (bench maps 3.82 → 3.40 MB per photo; ~0.3 s more CPU to write)."""
    buf = io.BytesIO()
    if not (C.PARQUET_SLIM if slim is None else slim):
        np.savez_compressed(buf, **arrays)
        return buf.getvalue()
    import zipfile
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_BZIP2, compresslevel=9) as zf:
        for k, v in arrays.items():
            m = io.BytesIO()
            np.lib.format.write_array(m, np.asanyarray(v), allow_pickle=False)
            zf.writestr(f"{k}.npy", m.getvalue())
    return buf.getvalue()


def read_meta(raw: bytes) -> dict:
    """The `skyshift` metadata of a session-stack file."""
    import pyarrow.parquet as pq
    md = pq.ParquetFile(io.BytesIO(raw)).schema_arrow.metadata or {}
    return json.loads(md.get(b"skyshift", b"{}"))


def run_task(root: str, task: dict, maps: bool = False, loader=None) -> dict:
    """One (session, detector): read and prepare its photos, stack, measure, write. Skips a session already written
    (the sources file is written last)."""
    from .store import Store
    store = Store(root)
    rel = session_rel(task["key"])
    st = _task_stats(task)
    if store.complete_parquet(rel):  # a 0-byte or cut-short file (a crash) is built again
        st["status"] = "exists"
        return st
    loader = loader or load_photo
    t0, c0 = time.time(), time.process_time()
    cuts, prep = [], []
    try:
        for obs_id, url in zip(task["obs_ids"], task["urls"]):
            tr = time.time()
            l2 = loader(url)
            read_s = time.time() - tr
            cut, info = prepare(l2, obs_id)
            info["read_wall_s"] = round(read_s, 1)
            cuts.append(cut)
            prep.append(info)
            del l2
    except Exception as e:  # noqa: BLE001 - one session failing never stops the zone
        st.update(status="failed", error=repr(e)[:300])
        return st
    return write_task(store, task, cuts, prep, maps, t0, c0)


def _task_stats(task: dict) -> dict:
    return {"key": task["key"], "session": task["session"], "detector": task["detector"], "n_photos": len(task["obs_ids"]),
            "status": ""}


def write_task(store, task: dict, cuts: list[S.Cut], prep: list[dict], maps: bool = False, t0: float | None = None,
               c0: float | None = None, extra_cpu_s: float = 0.0, fused: bool = False) -> dict:
    """Stack one (session, detector) from its prepared photos and write its files (the sources file last). t0 / c0:
    wall and CPU clocks at the task's start (run_task: before the first read); extra_cpu_s: CPU spent before c0 that
    belongs to the task (the fused dots worker: the photos' preparation). Never raises."""
    st = _task_stats(task)
    t0 = time.time() if t0 is None else t0
    c0 = time.process_time() if c0 is None else c0
    try:
        src, ph, meta, mp = stack_session(cuts, task, maps=maps)
        meta["prepare"] = prep
        meta["wall_s"] = round(time.time() - t0, 1)
        meta["cpu_s"]["total"] = round(time.process_time() - c0 + extra_cpu_s, 1)
        if fused:
            meta["fused_in_dots"] = True
        store.write_bytes(session_rel(task["key"], photos=True), _parquet(ph))
        store.write_bytes(session_moving_rel(task["key"]), _parquet(moving_rows(src, ph)))
        if mp is not None:
            store.write_bytes(session_map_rel(task["key"]), npz_bytes(mp))
        store.write_bytes(session_rel(task["key"]), _parquet(src, meta))  # last: its existence means the session is done
    except Exception as e:  # noqa: BLE001 - one session failing never stops the zone
        st.update(status="failed", error=repr(e)[:300])
        return st
    st.update(status="done", n_sources=int(len(src)), threshold=meta["threshold"], wall_s=meta["wall_s"],
              cpu_s=meta["cpu_s"]["total"], depth=meta["depth_ab_5sigma"], per_deg2=meta["per_deg2"],
              neg_per_deg2=meta["false_alarms"]["neg_per_deg2"], n_glitch=meta["n_glitch"])
    return st


class Stacker:
    """Session stacks built inside the dots worker (fused: extraction v2 with SESSION_STACK on; docs/session-stacks.md
    §8). The worker hands over every photo it holds, raw (before its dots run), with the detection background and
    dark-hole mask it computed once for both (dots.raw_background_holes); a (session, detector) is stacked and written
    as soon as its last photo is prepared, and its cuts are freed. Only tasks whose every photo is in the worker's job
    list are attempted; a photo that could not be read drops its task (the `sessionstack` step after dots does it
    later, reading the photos again). results: one stats dict per task (done / failed / incomplete), as run_task's."""

    def __init__(self, store, tasks: list[dict], have: set, maps: bool = False):
        self.store, self.maps = store, maps
        self.tasks: dict[str, dict] = {}
        self.by_photo: dict[tuple, str] = {}
        self.results: list[dict] = []
        for t in tasks:
            need = {(str(o), int(t["detector"])) for o in t["obs_ids"]}
            if not need <= have:
                self.results.append({**_task_stats(t), "status": "incomplete", "fused": True})
                continue
            self.tasks[t["key"]] = t
            for k in need:
                self.by_photo[k] = t["key"]
        self.cuts: dict[str, dict] = {k: {} for k in self.tasks}
        self.prep: dict[str, dict] = {k: {} for k in self.tasks}
        self.cpu: dict[str, float] = {k: 0.0 for k in self.tasks}
        self.t0: dict[str, float] = {}

    def wants(self, obs_id: str, detector: int) -> bool:
        return (str(obs_id), int(detector)) in self.by_photo

    def held(self) -> int:
        """Prepared photos held right now (memory: ~50 MB each for a full photo)."""
        return sum(len(v) for v in self.cuts.values())

    def add(self, obs_id: str, detector: int, l2, shared: tuple | None = None, read_s: float | None = None) -> dict | None:
        """Prepare one photo of a wanted task; stack and write the task when it is complete. Returns that task's
        stats when it finished (done or failed), else None. Never raises."""
        key = self.by_photo.pop((str(obs_id), int(detector)), None)
        if key is None or key not in self.tasks:
            return None
        self.t0.setdefault(key, time.time())
        c0 = time.process_time()
        try:
            cut, info = prepare(l2, str(obs_id), shared=shared)
        except Exception as e:  # noqa: BLE001
            return self._drop(key, f"prepare {obs_id}: {e!r}"[:300])
        info["read_wall_s"] = None if read_s is None else round(float(read_s), 1)
        self.cpu[key] += time.process_time() - c0
        self.cuts[key][str(obs_id)] = cut
        self.prep[key][str(obs_id)] = info
        t = self.tasks[key]
        if len(self.cuts[key]) < len(t["obs_ids"]):
            return None
        cuts = [self.cuts[key][str(o)] for o in t["obs_ids"]]
        prep = [self.prep[key][str(o)] for o in t["obs_ids"]]
        cpu = self.cpu[key]
        self._forget(key)
        st = write_task(self.store, t, cuts, prep, self.maps, t0=self.t0[key], extra_cpu_s=cpu, fused=True)
        st["fused"] = True
        st["prepare_cpu_s"] = round(cpu, 2)
        self.results.append(st)
        return st

    def fail(self, obs_id: str, detector: int, why: str) -> dict | None:
        """A photo of a wanted task could not be read: the task is dropped (its cuts freed)."""
        key = self.by_photo.pop((str(obs_id), int(detector)), None)
        if key is None or key not in self.tasks:
            return None
        return self._drop(key, why)

    def _drop(self, key: str, why: str) -> dict:
        st = {**_task_stats(self.tasks[key]), "status": "failed", "error": why, "fused": True}
        self._forget(key)
        self.results.append(st)
        return st

    def _forget(self, key: str) -> None:
        self.tasks.pop(key, None)
        self.cuts.pop(key, None)
        self.prep.pop(key, None)
        self.cpu.pop(key, None)
        for k in [k for k, v in self.by_photo.items() if v == key]:
            del self.by_photo[k]

    def close(self) -> list[dict]:
        """Tasks never completed (a photo never came) are reported incomplete; returns every task's stats."""
        for key in list(self.tasks):
            self.results.append({**_task_stats(self.tasks[key]), "status": "incomplete", "fused": True})
            self._forget(key)
        return self.results


def _run_task_star(a):
    return run_task(*a)


def run(store, zone: C.Zone, workers: int = 2, limit: int | None = None) -> dict:
    """Step `sessionstack` for a zone: every session × detector with >= SESSION_STACK_MIN_EXP photos (limit: the
    first N), in at most SESSION_STACK_WORKERS_MAX processes; results/<zone>/session_stack.json sums them up. With
    extraction v2 the dots worker builds them while it holds the photos (Stacker; docs/session-stacks.md §8): this
    step then finds them written ("exists", nothing read) and does only those the worker could not."""
    from concurrent.futures import ProcessPoolExecutor

    from .store import photolist_rel
    t0 = time.time()
    photos = store.read_parquet(photolist_rel(zone.name))
    tasks = session_tasks(photos)
    if limit:
        tasks = tasks[: int(limit)]
    maps = zone.name in C.SESSION_STACK_MAP_ZONES
    n = max(1, min(int(workers), C.SESSION_STACK_WORKERS_MAX, len(tasks) or 1))
    args = [(store.root, t, maps) for t in tasks]
    if n == 1:
        res = [run_task(*a) for a in args]
    else:
        import multiprocessing as mp
        with ProcessPoolExecutor(n, mp_context=mp.get_context("spawn")) as ex:
            res = list(ex.map(_run_task_star, args))
    return write_index_report(store, zone, tasks, res, t0)


def write_index_report(store, zone: C.Zone, tasks: list[dict], res: list[dict], t0: float,
                       fused: bool = False) -> dict:
    """session_stacks/index/<zone>.parquet (every task done or already there) and results/<zone>/session_stack.json
    from the tasks' stats. A run that stacked nothing new keeps the previous report's per-detector figures (the
    `sessionstack` step after a fused dots run finds every stack written)."""
    from .store import results_rel
    done = [r for r in res if r.get("status") == "done"]
    ok_keys = {r["key"] for r in res if r.get("status") in ("done", "exists")}
    idx = [{"key": t["key"], "session": t["session"], "visit": t["visit"], "detector": t["detector"], "obs_id": o,
            "n_exp_session": len(t["obs_ids"])} for t in tasks if t["key"] in ok_keys for o in t["obs_ids"]]
    store.write_parquet(index_rel(zone.name), pd.DataFrame(idx, columns=["key", "session", "visit", "detector", "obs_id",
                                                                         "n_exp_session"]))
    by_det: dict = {}
    for r in done:
        by_det.setdefault(int(r["detector"]), []).append(r)
    rep_rel = results_rel(zone.name, "session_stack.json")
    old: dict = {}
    if not done:
        try:
            old = (store.read_json(rep_rel) if store.exists(rep_rel) else None) or {}
        except Exception:  # noqa: BLE001 - an unreadable old report is simply replaced
            old = {}
    per_det = {f"D{d}": {"sessions": len(v),
                         "depth_ab_5sigma_median": _median([x["depth"].get("searched") for x in v]),
                         "sources_per_deg2_median": _median([x["per_deg2"] for x in v]),
                         "neg_per_deg2_median": _median([x["neg_per_deg2"] for x in v]),
                         "threshold_median": _median([x["threshold"] for x in v]),
                         "cpu_s_median": _median([x["cpu_s"] for x in v])}
               for d, v in sorted(by_det.items())}
    rep = {"zone": zone.name, "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "tasks": len(tasks),
           "done": len(done), "exists": sum(r.get("status") == "exists" for r in res),
           "incomplete": sum(r.get("status") == "incomplete" for r in res),
           "failed": [r for r in res if r.get("status") == "failed"][:50],
           "per_detector": per_det or old.get("per_detector", {}),
           "fused_in_dots": bool(fused) or bool(old.get("fused_in_dots", False)),
           "wall_s": round(time.time() - t0, 1)}
    if fused and done:
        rep["prepare_cpu_s_median"] = _median([r.get("prepare_cpu_s") for r in done])
    store.write_json(rep_rel, rep)
    return {k: v for k, v in rep.items() if k != "failed"} | {"failed": len(rep["failed"])}


def _median(v):
    v = [float(x) for x in v if x is not None and np.isfinite(x)]
    return round(float(np.median(v)), 3) if v else None


# --- link's use of the stacks (link.run when SESSION_STACK is on) -----------------------------------------------------

MOVING_COLUMNS = [*PHOTO_COLUMNS, "ra", "dec"]
STACK_LINK_COLUMNS = ["stack_row", "ra", "dec", "ra_psf", "dec_psf", "detector", "mjd", "n_exp", "flux_ujy", "snr",
                      "snr_mf", "flags", "glitch", "near_sat", "motion_snr", "rate_arcsec_h", "pos_err_arcsec"]
STACK_ROW_COLUMNS = ["detector", "mjd", "ra", "dec", "flux_ujy", "snr", "flags", "flags_peak", "elong", "session", "visit",
                     "photo", "exposure", "dot_row", "key", "stack_row", "n_exp", "snr_mf", "pos_err_arcsec"]


def moving_rows(src: pd.DataFrame, ph: pd.DataFrame) -> pd.DataFrame:
    """The per-photo rows, with their fitted sky position, of every stacked source that moves inside its session
    (motion_snr >= SESSION_STACK_MOVER_SIGMA, rate >= SESSION_STACK_MOVER_MIN_RATE, not a glitch): only photos where
    the position was fitted (pos_ok)."""
    from .sky import offset_radec
    empty = pd.DataFrame({c: pd.Series(dtype="float64") for c in MOVING_COLUMNS})
    if not len(src) or not len(ph):
        return empty
    mv = src[(src["motion_snr"].fillna(0).to_numpy() >= C.SESSION_STACK_MOVER_SIGMA)
             & (src["rate_arcsec_h"].fillna(0).to_numpy() >= C.SESSION_STACK_MOVER_MIN_RATE) & ~src["glitch"].to_numpy(bool)]
    q = ph[ph["stack_row"].isin(mv["stack_row"]) & ph["pos_ok"].to_numpy(bool)]
    if not len(q):
        return empty
    pos = src.set_index("stack_row").reindex(q["stack_row"].to_numpy())
    ra, dec = offset_radec(pos["ra"].to_numpy(np.float64), pos["dec"].to_numpy(np.float64),
                           q["de_arcsec"].to_numpy(np.float64), q["dn_arcsec"].to_numpy(np.float64))
    out = q.reset_index(drop=True).copy()
    out["ra"], out["dec"] = np.asarray(ra, np.float64), np.asarray(dec, np.float64)
    return out[MOVING_COLUMNS]


def stack_index(store) -> pd.DataFrame:
    """Every zone's index rows (session_stacks/index/*.parquet): a tile or another zone finds stacks by obs_id."""
    cols = ["key", "session", "visit", "detector", "obs_id", "n_exp_session"]
    try:
        names = [n.rstrip("/").rsplit("/", 1)[-1] for n in store.listdir(f"{C.SESSION_STACK_DIR}/index")]
    except (FileNotFoundError, OSError):
        names = []
    parts = []
    for n in names:
        if not n.endswith(".parquet"):
            continue
        try:
            parts.append(store.read_parquet(f"{C.SESSION_STACK_DIR}/index/{n}"))
        except Exception:  # noqa: BLE001  one unreadable index never stops link
            continue
    parts = [p for p in parts if len(p)]
    return pd.concat(parts, ignore_index=True)[cols] if parts else pd.DataFrame(columns=cols)


def stack_keys(index: pd.DataFrame, photos: pd.DataFrame) -> list[str]:
    """The stacks that cover this photo list: keys with a photo in it, the largest first, each (obs_id, detector) in
    one stack only (two zones may have stacked different subsets of one session)."""
    if not len(index) or not len(photos):
        return []
    want = set(zip(photos["obs_id"].astype(str), photos["detector"].astype(int)))
    pairs = list(zip(index["obs_id"].astype(str), index["detector"].astype(int)))
    ix = index[[p in want for p in pairs]]
    if not len(ix):
        return []
    size = ix.groupby("key")["n_exp_session"].max()
    members: dict = {}
    for k, o, d in zip(ix["key"], ix["obs_id"].astype(str), ix["detector"].astype(int)):
        members.setdefault(k, set()).add((o, d))
    taken: set = set()
    keys = []
    for k in sorted(members, key=lambda k: (-int(size[k]), k)):
        if members[k] & taken:
            continue
        taken |= members[k]
        keys.append(k)
    return keys


def load_stack_rows(store, photos: pd.DataFrame, zone: C.Zone,
                    dots: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame | None, dict]:
    """The zone's stacked sources as link rows, and the moving ones' per-photo positions as dots.

    rows: one per stacked source inside the zone box that is not a glitch, not near a saturated pixel and has no real dot
    of its session and detector within SESSION_STACK_DEDUP_ARCSEC (that dot already is it): link's dot columns with
    the session-mean mjd, the photos' ePSF position (else the map peak), snr = the forced S/N over the session's photos,
    flags = the photos' 3×3 OR, elong 1, photo, exposure and dot_row −1 (no dot file row), plus key, stack_row, n_exp,
    snr_mf, pos_err_arcsec. Session and visit come from this photo list.
    pseudo: the per-photo rows (moving_rows) of the kept rows that move, as dots of their photo (exposure, photo index,
    dot_row −1), less those with a real dot of the same photo within the dedup radius. `dots` = link.load_dots' table."""
    from .sky import SkyTree
    st = {"stack_files": 0, "stack_rows_read": 0, "stack_rows": 0, "stack_rows_glitch": 0, "stack_rows_near_sat": 0,
          "stack_rows_dedup": 0, "stack_pseudo_dots": 0, "stack_pseudo_dedup": 0}
    keys = stack_keys(stack_index(store), photos)
    exp_code = {o: i for i, o in enumerate(sorted(photos["obs_id"].unique()))}
    pkey = {(str(o), int(d)): (i, int(s), int(v)) for i, o, d, s, v in
            zip(photos.index, photos["obs_id"], photos["detector"], photos["session"], photos["visit"])}
    rows, pseudo = [], []
    for k in keys:
        try:
            raw = store.read_bytes(session_rel(k))
        except FileNotFoundError:
            continue
        meta = read_meta(raw)
        st["stack_files"] += 1
        src = pd.read_parquet(io.BytesIO(raw), columns=STACK_LINK_COLUMNS)
        st["stack_rows_read"] += len(src)
        det = int(meta.get("detector", src["detector"].iloc[0] if len(src) else 0))
        here = [pkey[(str(o), det)] for o in meta.get("obs_ids", []) if (str(o), det) in pkey]
        if not here:
            continue
        ses, vis = here[0][1], here[0][2]
        src = src[zone.contains(src["ra"].to_numpy(), src["dec"].to_numpy())]
        gl, ns = src["glitch"].to_numpy(bool), src["near_sat"].to_numpy(bool)
        st["stack_rows_glitch"] += int(gl.sum())
        st["stack_rows_near_sat"] += int((ns & ~gl).sum())
        src = src[~gl & ~ns]
        psf = np.isfinite(src["ra_psf"].to_numpy(np.float64))
        ra = np.where(psf, src["ra_psf"].to_numpy(np.float64), src["ra"].to_numpy(np.float64))
        dec = np.where(psf, src["dec_psf"].to_numpy(np.float64), src["dec"].to_numpy(np.float64))
        same = dots[(dots["session"].to_numpy() == ses) & (dots["detector"].to_numpy() == det)] if len(dots) else dots
        dup = np.zeros(len(src), bool)
        if len(same) and len(src):
            sep, _ = SkyTree(same["ra"].to_numpy(), same["dec"].to_numpy()).nearest(ra, dec)
            dup = sep <= C.SESSION_STACK_DEDUP_ARCSEC
        st["stack_rows_dedup"] += int(dup.sum())
        keep = ~dup
        n = int(keep.sum())
        r = pd.DataFrame({"detector": np.full(n, det, np.int8), "mjd": src["mjd"].to_numpy(np.float64)[keep],
                          "ra": ra[keep], "dec": dec[keep], "flux_ujy": src["flux_ujy"].to_numpy(np.float32)[keep],
                          "snr": src["snr"].to_numpy(np.float32)[keep], "flags": src["flags"].to_numpy(np.int32)[keep],
                          "flags_peak": np.zeros(n, np.int32), "elong": np.ones(n, np.float32),
                          "session": np.full(n, ses, np.int32), "visit": np.full(n, vis, np.int16),
                          "photo": np.full(n, -1, np.int64), "exposure": np.full(n, -1, np.int32),
                          "dot_row": np.full(n, -1, np.int32), "key": np.full(n, k, object),
                          "stack_row": src["stack_row"].to_numpy(np.int32)[keep], "n_exp": src["n_exp"].to_numpy(np.int8)[keep],
                          "snr_mf": src["snr_mf"].to_numpy(np.float32)[keep],
                          "pos_err_arcsec": src["pos_err_arcsec"].to_numpy(np.float32)[keep]})
        rows.append(r)
        try:
            mv = store.read_parquet(session_moving_rel(k))
        except FileNotFoundError:
            mv = None
        if mv is None or not len(mv):
            continue
        mv = mv[mv["stack_row"].isin(r["stack_row"])]
        if not len(mv):
            continue
        info = [pkey.get((str(o), det), (-1, ses, vis)) for o in mv["obs_id"]]
        m = len(mv)
        with np.errstate(divide="ignore", invalid="ignore"):
            snr = mv["flux_ujy"].to_numpy(np.float64) / mv["flux_err_ujy"].to_numpy(np.float64)
        pseudo.append(pd.DataFrame({
            "detector": np.full(m, det, np.int8), "mjd": mv["mjd"].to_numpy(np.float64), "ra": mv["ra"].to_numpy(np.float64),
            "dec": mv["dec"].to_numpy(np.float64), "flux_ujy": mv["flux_ujy"].to_numpy(np.float32),
            "snr": snr.astype(np.float32), "flags": mv["flags"].to_numpy(np.int32), "flags_peak": np.zeros(m, np.int32),
            "elong": np.ones(m, np.float32), "session": np.full(m, ses, np.int32), "visit": np.full(m, vis, np.int16),
            "photo": np.array([i[0] for i in info], np.int64),
            "exposure": np.array([exp_code.get(str(o), -1) for o in mv["obs_id"]], np.int32),
            "dot_row": np.full(m, -1, np.int32), "x": mv["x"].to_numpy(np.float32), "y": mv["y"].to_numpy(np.float32),
            "size": np.full(m, np.nan, np.float32), "psf_chi2_nu": np.full(m, np.nan, np.float32),
            "key": np.full(m, k, object), "stack_row": mv["stack_row"].to_numpy(np.int32)}))
    out = (pd.concat(rows, ignore_index=True) if rows
           else pd.DataFrame({c: pd.Series(dtype="float64") for c in STACK_ROW_COLUMNS}))
    ps = pd.concat(pseudo, ignore_index=True) if pseudo else None
    if ps is not None and len(ps):
        ps = ps[(ps["photo"].to_numpy() >= 0) & (ps["exposure"].to_numpy() >= 0)].reset_index(drop=True)
        dup = np.zeros(len(ps), bool)
        if len(dots) and len(ps):
            dphoto = dots["photo"].to_numpy()
            for ph_i, idx in ps.groupby("photo").indices.items():
                d = dots[dphoto == ph_i]
                if len(d):
                    sep, _ = SkyTree(d["ra"].to_numpy(), d["dec"].to_numpy()).nearest(ps["ra"].to_numpy()[idx],
                                                                                    ps["dec"].to_numpy()[idx])
                    dup[idx] = sep <= C.SESSION_STACK_DEDUP_ARCSEC
        st["stack_pseudo_dedup"] = int(dup.sum())
        ps = ps[~dup].reset_index(drop=True)
    st["stack_rows"] = int(len(out))
    st["stack_pseudo_dots"] = 0 if ps is None else int(len(ps))
    return out, ps, st
