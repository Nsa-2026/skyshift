"""Session coadds, simultaneous-pair detection maps and shift-and-stack (digital tracking): a prototype (16 Nov).

Not a CLI step and not used by any step. The measurements on real hunt photos, what each gains and costs, and the
recommendation for the one combined rerun are in docs/session-stacks.md.

Three ways to go deeper than one photo, all on the same matched-filter S/N maps as `stack` (stack.py: each photo's
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

import math
from dataclasses import dataclass

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
