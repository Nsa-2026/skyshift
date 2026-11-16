"""A bright star's glow, modelled and taken out before detection ("glow model").

Why: around a saturated or very bright star the wings and halo stay above the sky out to ~25-30 px (150-180")
(Barnard's Star, D1: 0.5 MJy/sr at 10 px, 0.16 at 15-20 px, against a sky noise of 0.04). sep's background mesh
(32 px) follows little of it, so detection at 2 sigma per pixel cuts the glow's lumps and asymmetries into many
"sources" with no star behind them, and fine deblending splits them further.

What it does, per photo (or block):
1. bright_stars: the stars to model. A saturated star = a connected bad-pixel blob holding OVERFLOW/NONLINEAR
   pixels (its masked core; Barnard's is ~9 px wide: SUR_ERROR/TRANSIENT around 3 OVERFLOW pixels); an unsaturated
   one = a local maximum whose peak is >= GLOW_MIN_PEAK_SNR x the pixel noise. Brightest first, at most GLOW_MAX_STARS.
2. Per star, on the background-subtracted image minus the brighter stars' models:
   a. ePSF (the photo's EPSF HDU, evaluated at pixel centres, never integrated): flux and centre from the unmasked
      wing pixels (the saturated core and 1 px around it left out). The ePSF stops at +-3.2 px, so for a saturated
      star whose masked core reaches ~4 px it is not constrained and is skipped (`epsf_ok` false).
   b. the smooth residual term = a radial profile x an angular modulation. The profile: medians of what is left
      in narrow rings (each >= GLOW_BIN_MIN_PX good pixels; ~1 px wide beyond the core), linear in radius, out to
      the last ring whose median is >= GLOW_STOP_SIGMA x the pixel noise (then 0). The modulation (a halo is
      rarely round): in wide rings (>= GLOW_ANG_REL_WIDTH x r, >= GLOW_ANG_MIN_WIDTH_PX) cut into as many sectors
      as hold >= GLOW_BIN_MIN_PX pixels (up to GLOW_MAX_SECTORS), median(left) / median(profile) per sector,
      linear in angle and radius; 1 where the profile is below GLOW_ANG_MIN_LEVEL x the pixel noise (too faint to
      measure a shape). A median of >= 40 pixels ignores a real star (~9 pixels) in the bin.
   The background is then measured again on the image minus every model, and the models fitted again (twice).
3. Local significance: the residual summed in r = 2 px apertures (the dots' aperture), over its expected noise
   from the noise map, z. Its robust scatter (1.4826 MAD) in each polar bin over the frame's own (the field
   level, crowding included) is the noise factor k >= 1: the glow's own Poisson noise (in VARIANCE already) and
   whatever structure the model leaves. Detection noise near the star = noise x k.

Returns the model (MJy/sr, add-subtract) and k; the caller detects on image - model with noise x k and keeps the
star's own detection from the unmodified image (the model holds the star's wings; inside r_self it is the star).
Pure numpy/scipy, no I/O. Parameters are module constants; the pipeline passes its config.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy import ndimage

from .detect import background, build_mask
from .flags import SAT_BITS

GLOW_R_MAX_PX = 40.0        # the farthest ring measured (246"); Barnard's glow fades into the sky at ~25-30 px
GLOW_FAR_PX = 8.0           # the field level: median of the ring r_max .. r_max + this
GLOW_R_KEEP_PX = 3.5        # Glow.outside: the star's own pixels (beyond the ePSF's +-3.2 px) are left as observed
GLOW_CORE_PAD_PX = 1.5      # ... and a saturated star's unmasked rim: its core radius + this
GLOW_MIN_PEAK_SNR = 300.0   # an unsaturated star is modelled from this peak S/N (pixel; ~10 mag in D1)
GLOW_SAT_MIN_SNR = 50.0     # a saturated core is a star when the pixels just outside it reach this S/N: MWIR photos
                            # hold ~1,700-2,600 lone OVERFLOW/NONLINEAR pixels with nothing bright around (S/N < 50)
GLOW_MAX_STARS = 400        # per photo, brightest first
GLOW_BIN_MIN_PX = 40        # good pixels per polar bin (a real star covers ~9: the median ignores it)
GLOW_MAX_SECTORS = 12
GLOW_RING_MIN_WIDTH_PX = 1.0
GLOW_PROFILE_SECTORS = 1     # sectors per profile ring (narrow rings: 1, the shape comes from the modulation)
GLOW_ANG_REL_WIDTH = 0.4    # the modulation's rings: at least this x their inner radius ...
GLOW_ANG_MIN_WIDTH_PX = 3.0  # ... and this wide
GLOW_ANG_MIN_LEVEL = 0.5    # the modulation is measured where the profile is >= this x the pixel noise (else 1)
GLOW_ANG_MAX = 3.0          # and clipped to 0 .. this
GLOW_STOP_SIGMA = 0.15      # the profile ends at the first ring whose median is below this x the pixel noise
GLOW_K_STOP = 1.05          # the noise factor ends at the first ring whose k is below this
GLOW_AP_R_PX = 2.0          # the dots' aperture: z is measured in it
GLOW_ITER = 2               # model, background again, model again
GLOW_SAT_GROW_PX = 1        # pixels around saturated ones left out of the ePSF fit
GLOW_EPSF_HALF = 3          # ePSF stamp half size (the ePSF is 0 beyond +-3.2 px)
GLOW_EPSF_MIN_PX = 6        # unmasked pixels with ePSF >= 1e-3 needed to fit the ePSF part

STAR_DTYPE = np.dtype([
    ("x", "f8"), ("y", "f8"),     # centre, 0-based pixels (saturated: the masked core's centre / wings fit)
    ("saturated", "?"),
    ("r_core", "f4"),             # radius of the masked core (0 if none), px
    ("peak_snr", "f4"),           # brightest unmasked pixel / noise within 2 px of the core edge
    ("flux_epsf", "f8"),          # ePSF flux, image units x px (NaN when not constrained)
    ("epsf_ok", "?"),
    ("r_glow", "f4"),             # the model's extent, px (0: nothing above GLOW_STOP_SIGMA)
    ("r_k", "f4"),                # the noise factor's extent, px
    ("k_max", "f4"),
])


@dataclass
class Glow:
    model: NDArray[np.float64]   # what the bright stars add, image units (0 elsewhere)
    k: NDArray[np.float64]       # noise factor >= 1 (1 elsewhere)
    stars: NDArray[np.void]      # STAR_DTYPE, brightest first
    bkg_level: NDArray[np.float64] | None = None  # the background measured on image - model (None: not measured)

    @property
    def active(self) -> bool:
        return bool(len(self.stars)) and bool(np.any(self.model != 0) or np.any(self.k > 1))

    def keep_radius(self, r_keep: float = GLOW_R_KEEP_PX,
                    core_pad: float | None = GLOW_CORE_PAD_PX) -> NDArray[np.float64]:
        """Per star, the radius left as observed by outside(): r_keep, or a saturated core's radius + core_pad
        (core_pad None: r_keep for every star, so a saturated star's unmasked rim beyond r_keep is modelled too)."""
        s = self.stars
        if core_pad is None:
            return np.full(len(s), r_keep, np.float64)
        return np.where(s["saturated"], np.maximum(r_keep, s["r_core"] + core_pad), r_keep).astype(np.float64)

    def outside(self, r_keep: float = GLOW_R_KEEP_PX,
                core_pad: float | None = GLOW_CORE_PAD_PX) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        """(model, k) with each star's own pixels left alone: 0 and 1 within keep_radius() of it (r_keep beyond the
        ePSF's +-3.2 px, so the star's own detection, aperture and ePSF fit see it as observed; a saturated star's
        unmasked rim too). What to subtract before detection."""
        model, k = self.model.copy(), self.k.copy()
        ny, nx = model.shape
        for s, rk in zip(self.stars, self.keep_radius(r_keep, core_pad)):
            h = int(np.ceil(rk))
            ix, iy = int(round(float(s["x"]))), int(round(float(s["y"])))
            y0, y1, x0, x1 = max(iy - h, 0), min(iy + h + 1, ny), max(ix - h, 0), min(ix + h + 1, nx)
            if y0 >= y1 or x0 >= x1:
                continue
            yy, xx = np.mgrid[y0:y1, x0:x1]
            inside = np.hypot(xx - float(s["x"]), yy - float(s["y"])) <= rk
            model[y0:y1, x0:x1][inside] = 0.0
            k[y0:y1, x0:x1][inside] = 1.0
        return model, k

    def near(self, x: ArrayLike, y: ArrayLike, r_self: float, core_pad: float = GLOW_CORE_PAD_PX) -> NDArray[np.int64]:
        """Index of the modelled star within max(r_self, its core radius + core_pad) of each (x, y) (the nearest), else
        -1: a detection there is the star itself or a piece of it."""
        x, y = np.atleast_1d(np.asarray(x, float)), np.atleast_1d(np.asarray(y, float))
        out = np.full(x.size, -1, np.int64)
        best = np.full(x.size, np.inf)
        for i, s in enumerate(self.stars):
            d = np.hypot(x - s["x"], y - s["y"])
            r = max(r_self, float(s["r_core"]) + core_pad) if s["saturated"] else r_self
            hit = (d <= r) & (d < best)
            out[hit], best[hit] = i, d[hit]
        return out

    def own(self, x: ArrayLike, y: ArrayLike, r_self: float, core_pad: float = GLOW_CORE_PAD_PX) -> tuple[NDArray, NDArray]:
        """(own, piece) masks over detections (x, y): `own` = the one nearest each modelled star within near()'s
        radius (the star's own detection), `piece` = the others there (pieces of it)."""
        x, y = np.atleast_1d(np.asarray(x, float)), np.atleast_1d(np.asarray(y, float))
        idx = self.near(x, y, r_self, core_pad)
        own = np.zeros(x.size, bool)
        for si in np.unique(idx[idx >= 0]):
            j = np.flatnonzero(idx == si)
            own[j[np.argmin(np.hypot(x[j] - self.stars[si]["x"], y[j] - self.stars[si]["y"]))]] = True
        return own, (idx >= 0) & ~own


def _noise_map(noise: ArrayLike | None, shape: tuple[int, int], fallback: float) -> NDArray[np.float64]:
    if noise is None:
        return np.full(shape, fallback)
    n = np.asarray(noise, np.float64)
    return np.where(np.isfinite(n) & (n > 0), n, np.inf)


def bright_stars(
    data: ArrayLike,
    flags: ArrayLike | None,
    noise: ArrayLike,
    *,
    mask: ArrayLike | None = None,
    sub: ArrayLike | None = None,
    min_peak_snr: float = GLOW_MIN_PEAK_SNR,
    max_stars: int = GLOW_MAX_STARS,
    max_core_px: int = 2000,
    sat_min_snr: float = GLOW_SAT_MIN_SNR,
) -> NDArray[np.void]:
    """The stars whose glow is modelled: saturated cores (bad-pixel blobs with OVERFLOW/NONLINEAR, at most
    max_core_px pixels: a bigger blob is missing data; the pixels within 3 px around reach S/N sat_min_snr: a lone
    flagged pixel is no star) and unsaturated local maxima with peak / noise >= min_peak_snr. `sub`: the background-subtracted image (default: data minus its sep background).
    Returns STAR_DTYPE rows, saturated ones first (largest core first), then by peak S/N."""
    d = np.asarray(data, np.float64)
    m = build_mask(d, flags, mask)
    if sub is None:
        dd = np.where(m, 0.0, d)
        sub = dd - background(dd, m).level
    s = np.where(m, np.nan, np.asarray(sub, np.float64))
    nz = _noise_map(noise, d.shape, 1.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        snr = np.where(np.isfinite(s) & np.isfinite(nz), s / nz, -np.inf)
    rows = []
    taken = np.zeros(d.shape, bool)
    if flags is not None:
        sat = (np.asarray(flags).astype(np.int64) & SAT_BITS) != 0
        if sat.any():
            blob = m | sat
            lab, n = ndimage.label(blob, structure=np.ones((3, 3), bool))
            ids = np.unique(lab[sat])
            ids = ids[ids > 0]
            sizes = np.bincount(lab.ravel(), minlength=n + 1)
            objs = ndimage.find_objects(lab)
            for i in ids:
                if sizes[i] > max_core_px:
                    continue
                sl = objs[i - 1]
                yy, xx = np.nonzero(lab[sl] == i)
                yy, xx = yy + sl[0].start, xx + sl[1].start
                cx, cy = float(xx.mean()), float(yy.mean())
                r_core = float(np.max(np.hypot(xx - cx, yy - cy))) + 0.5
                y0, y1 = max(int(cy - r_core - 3), 0), min(int(cy + r_core + 4), d.shape[0])
                x0, x1 = max(int(cx - r_core - 3), 0), min(int(cx + r_core + 4), d.shape[1])
                pk = snr[y0:y1, x0:x1]
                peak = float(np.max(pk)) if np.isfinite(pk).any() and pk.size else 0.0
                if peak < sat_min_snr:
                    continue
                rows.append((cx, cy, True, r_core, max(peak, 0.0), np.nan, False, 0.0, 0.0, 1.0, sizes[i]))
                taken[y0:y1, x0:x1] = True
    loc = ndimage.maximum_filter(np.where(np.isfinite(snr), snr, -np.inf), size=5, mode="nearest")
    pk = (snr == loc) & (snr >= min_peak_snr) & ~taken
    iy, ix = np.nonzero(pk)
    order = np.argsort(-snr[iy, ix])
    for j in order:
        rows.append((float(ix[j]), float(iy[j]), False, 0.0, float(snr[iy[j], ix[j]]), np.nan, False, 0.0, 0.0, 1.0, 0))
    sat_rows = sorted([r for r in rows if r[2]], key=lambda r: (-r[-1], -r[4]))
    uns = [r for r in rows if not r[2]]
    out = np.array([r[:-1] for r in (sat_rows + uns)[:max_stars]], dtype=STAR_DTYPE)
    return out


@dataclass
class _Ring:
    a: float                 # inner and outer radius
    b: float
    rc: float                # median radius of its pixels
    ang: NDArray[np.float64]  # sector centre angles
    vals: NDArray[np.float64]  # stat of each sector
    whole: float             # stat of the whole ring
    aux: float               # median of `aux` over the ring (NaN without)


def _polar(val: NDArray[np.float64], r: NDArray[np.float64], th: NDArray[np.float64], good: NDArray[np.bool_],
           r_min: float, r_max: float, stat, *, aux: NDArray[np.float64] | None = None,
           bin_min_px: int = GLOW_BIN_MIN_PX, max_sectors: int = GLOW_MAX_SECTORS,
           min_width: float = GLOW_RING_MIN_WIDTH_PX, rel_width: float = 0.0) -> list[_Ring]:
    """`stat` of `val` in polar bins around a centre (r, th: each pixel's radius and angle) from r_min to r_max. Rings
    are at least min_width (and rel_width x their inner radius) wide and hold at least bin_min_px good pixels (narrow
    where pixels are many, wider next to a masked core); each ring is cut into as many sectors as hold bin_min_px
    pixels (1..max_sectors). The rings depend only on r, th, good and the settings, never on val."""
    sel = good & (r >= r_min) & (r < r_max)
    rv, tv, vv = r[sel], th[sel], val[sel]
    av = aux[sel] if aux is not None else None
    o = np.argsort(rv, kind="stable")
    rv, tv, vv = rv[o], tv[o], vv[o]
    if av is not None:
        av = av[o]
    n = rv.size
    out: list[_Ring] = []
    a, i0 = r_min, 0
    while i0 < n:
        j = i0 + bin_min_px - 1
        if j >= n - bin_min_px // 2:  # the rest (too few for two rings) is the last ring
            i1, b = n, r_max
        else:
            b = min(max(float(rv[j]) + 1e-9, a + min_width, a * (1.0 + rel_width)), r_max)
            i1 = int(np.searchsorted(rv, b))
        if i1 <= i0:
            break
        v, t = vv[i0:i1], tv[i0:i1]
        ns = int(np.clip((i1 - i0) // bin_min_px, 1, max_sectors))
        whole = stat(v)
        if ns == 1:
            vals = np.array([whole])
        else:
            sec = np.minimum((t / (2 * np.pi) * ns).astype(int), ns - 1)
            vals = np.array([stat(v[sec == k]) if np.count_nonzero(sec == k) >= max(5, bin_min_px // 4) else np.nan
                             for k in range(ns)])
            vals = np.where(np.isfinite(vals), vals, whole)
        if np.isfinite(whole):
            out.append(_Ring(a, b, float(np.median(rv[i0:i1])), (np.arange(ns) + 0.5) * 2 * np.pi / ns, vals, whole,
                             float(np.median(av[i0:i1])) if av is not None else np.nan))
        a, i0 = b, i1
        if b >= r_max:
            break
    return out


def _polar_eval(rings: list[_Ring], r: NDArray[np.float64], th: NDArray[np.float64], r_end: float) -> NDArray[np.float64]:
    """The rings' sector values, linear in angle (periodic) and in radius between ring centres; inside the first
    ring centre its value, beyond the last its value, 0 beyond r_end."""
    out = np.zeros(r.shape)
    if not rings:
        return out
    rc = np.array([q.rc for q in rings])

    def ring_at(q: _Ring, sel: NDArray[np.bool_]) -> NDArray[np.float64]:
        if q.vals.size == 1:
            return np.full(int(sel.sum()), q.vals[0])
        a2 = np.r_[q.ang - 2 * np.pi, q.ang, q.ang + 2 * np.pi]
        return np.interp(th[sel], a2, np.r_[q.vals, q.vals, q.vals])

    inside = r <= r_end
    j = np.searchsorted(rc, r)  # r in (rc[j-1], rc[j]]
    for i in range(len(rings) - 1):
        sel = inside & (j == i + 1)
        if sel.any():
            t = (r[sel] - rc[i]) / max(rc[i + 1] - rc[i], 1e-9)
            out[sel] = (1 - t) * ring_at(rings[i], sel) + t * ring_at(rings[i + 1], sel)
    sel = inside & (j == 0)
    if sel.any():
        out[sel] = ring_at(rings[0], sel)
    sel = inside & (j >= len(rings))
    if sel.any():
        out[sel] = ring_at(rings[-1], sel)
    return out


def _epsf_part(res: NDArray[np.float64], w: NDArray[np.float64], sat_near: NDArray[np.bool_], epsf, x: float, y: float,
               fit_pos: bool) -> tuple[float, float, float, NDArray[np.float64] | None, tuple[int, int]]:
    """ePSF flux (and centre if fit_pos) from the unmasked, unsaturated pixels of a (2h+1)^2 stamp.
    Returns (x, y, flux, stamp model or None, (ix, iy))."""
    h = GLOW_EPSF_HALF
    ny, nx = res.shape
    for _ in range(3 if fit_pos else 1):
        ix, iy = int(round(x)), int(round(y))
        P, Gx, Gy = epsf.stamps_at([x], [y], [ix], [iy], h, grad=True)
        P, Gx, Gy = P[0], Gx[0], Gy[0]
        k = np.arange(-h, h + 1)
        PX, PY = ix + k[None, :], iy + k[:, None]
        inb = (PX >= 0) & (PX < nx) & (PY >= 0) & (PY < ny)
        PXc, PYc = np.clip(PX, 0, nx - 1), np.clip(PY, 0, ny - 1)
        PXc, PYc = np.broadcast_to(PXc, P.shape), np.broadcast_to(PYc, P.shape)
        ww = np.where(inb, w[PYc, PXc], 0.0) * ~sat_near[PYc, PXc]
        v = np.where(ww > 0, res[PYc, PXc], 0.0)
        use = (ww > 0) & (P >= 1e-3)
        if use.sum() < GLOW_EPSF_MIN_PX:
            return x, y, np.nan, None, (ix, iy)
        if fit_pos:
            A = np.stack([P[use], -Gx[use], -Gy[use]], axis=1)
        else:
            A = P[use][:, None]
        sw = np.sqrt(ww[use])
        try:
            sol, *_ = np.linalg.lstsq(A * sw[:, None], v[use] * sw, rcond=None)
        except np.linalg.LinAlgError:
            return x, y, np.nan, None, (ix, iy)
        f = float(sol[0])
        if not np.isfinite(f) or f <= 0:
            return x, y, np.nan, None, (ix, iy)
        if fit_pos:
            dx, dy = float(sol[1]) / f, float(sol[2]) / f
            if not (np.isfinite(dx) and np.isfinite(dy)) or np.hypot(dx, dy) > 1.0:
                fit_pos = False
                continue
            x, y = x + dx, y + dy
    ix, iy = int(round(x)), int(round(y))
    P = epsf.stamps_at([x], [y], [ix], [iy], h)[0][0]
    return x, y, f, P * f, (ix, iy)


def _add_stamp(img: NDArray[np.float64], st: NDArray[np.float64], ix: int, iy: int, sign: float = 1.0) -> None:
    h = st.shape[0] // 2
    ny, nx = img.shape
    y0, y1, x0, x1 = iy - h, iy + h + 1, ix - h, ix + h + 1
    cy0, cy1, cx0, cx1 = max(y0, 0), min(y1, ny), max(x0, 0), min(x1, nx)
    if cy0 >= cy1 or cx0 >= cx1:
        return
    img[cy0:cy1, cx0:cx1] += sign * st[cy0 - y0:cy1 - y0, cx0 - x0:cx1 - x0]


def _star_model(res: NDArray[np.float64], good: NDArray[np.bool_], nz: NDArray[np.float64], w: NDArray[np.float64],
                sat_near: NDArray[np.bool_], star: np.void, epsf, r_max: float) -> tuple[NDArray[np.float64], tuple, dict]:
    """One star's model on its stamp: ePSF part + polar residual term. Returns (stamp, (y0, y1, x0, x1), info)."""
    ny, nx = res.shape
    x, y = float(star["x"]), float(star["y"])
    R = int(np.ceil(r_max + GLOW_FAR_PX))
    y0, y1 = max(int(round(y)) - R, 0), min(int(round(y)) + R + 1, ny)
    x0, x1 = max(int(round(x)) - R, 0), min(int(round(x)) + R + 1, nx)
    info = {"x": x, "y": y, "flux_epsf": np.nan, "epsf_ok": False, "r_glow": 0.0}
    if y0 >= y1 or x0 >= x1:
        return np.zeros((0, 0)), (y0, y1, x0, x1), info
    model = np.zeros((y1 - y0, x1 - x0))
    rr = res[y0:y1, x0:x1]
    if epsf is not None:
        xe, ye, f, st, (ix, iy) = _epsf_part(res, w, sat_near, epsf, x, y, fit_pos=not bool(star["saturated"]))
        if st is not None:
            info.update(x=xe, y=ye, flux_epsf=f, epsf_ok=True)
            x, y = xe, ye
            _add_stamp(model, st, ix - x0, iy - y0)
    yy, xx = np.mgrid[y0:y1, x0:x1]
    r = np.hypot(xx - x, yy - y)
    th = np.mod(np.arctan2(yy - y, xx - x), 2 * np.pi)
    g = good[y0:y1, x0:x1] & np.isfinite(rr)
    left = rr - model
    # the field level around the star: the median of the ring just outside r_max (in a crowded field the median
    # of the sky sits above sep's mode-like background; the glow is what rises above this level)
    far_px = g & (r >= r_max) & (r < r_max + GLOW_FAR_PX)
    if far_px.sum() < GLOW_BIN_MIN_PX:
        return model, (y0, y1, x0, x1), info
    far = float(np.median(left[far_px]))
    left = left - far
    r_min = float(star["r_core"]) if star["saturated"] else 0.0
    nzs = nz[y0:y1, x0:x1]
    rings = _polar(left, r, th, g, r_min, r_max, np.median, aux=nzs, max_sectors=GLOW_PROFILE_SECTORS)
    # the extent: the first ring (outside the core) whose median falls below GLOW_STOP_SIGMA x its pixel noise
    r_end, keep = 0.0, []
    for q in rings:
        if q.whole < GLOW_STOP_SIGMA * q.aux:
            break
        r_end = q.b
        keep.append(q)
    if r_end <= 0 or not keep:
        return model, (y0, y1, x0, x1), info
    prof = _polar_eval(keep, r, th, r_end)
    # taper the last ring to 0 at r_end (no step at the edge)
    rc_last = keep[-1].rc
    taper = np.clip((r_end - r) / max(r_end - rc_last, 1.0), 0.0, 1.0)
    prof = np.where(r > rc_last, prof * taper, prof)
    model += prof * _modulation(left, prof, r, th, g, nzs, r_min, r_end)
    info["r_glow"] = r_end
    return model, (y0, y1, x0, x1), info


def _modulation(left: NDArray[np.float64], prof: NDArray[np.float64], r: NDArray[np.float64],
                th: NDArray[np.float64], g: NDArray[np.bool_], nz: NDArray[np.float64], r_min: float,
                r_end: float) -> NDArray[np.float64]:
    """The glow's shape around the profile: median(left) / median(prof) per sector of wide rings (the same bins for
    both), linear in angle and radius; 1 where the profile is too faint to measure it (module docstring, 2b)."""
    kw = dict(min_width=GLOW_ANG_MIN_WIDTH_PX, rel_width=GLOW_ANG_REL_WIDTH)
    rl = _polar(left, r, th, g, r_min, r_end, np.median, aux=nz, **kw)
    rp = _polar(prof, r, th, g, r_min, r_end, np.median, **kw)
    mods: list[_Ring] = []
    for ql, qp in zip(rl, rp):
        ok = qp.vals >= GLOW_ANG_MIN_LEVEL * ql.aux
        with np.errstate(invalid="ignore", divide="ignore"):
            m = np.where(ok, np.clip(ql.vals / qp.vals, 0.0, GLOW_ANG_MAX), 1.0)
        mods.append(_Ring(ql.a, ql.b, ql.rc, ql.ang, m, 1.0, ql.aux))
    if not mods:
        return np.ones(r.shape)
    out = _polar_eval(mods, r, th, np.inf)
    return np.where(r <= r_end, out, 1.0)


def aperture_z(res: ArrayLike, noise: ArrayLike, bad: ArrayLike, r: float = GLOW_AP_R_PX,
               min_good: float = 0.7) -> NDArray[np.float64]:
    """Each pixel's r-px aperture sum of `res` over its expected noise sqrt(sum noise^2): ~N(0, 1) on pure noise.
    NaN where less than min_good of the aperture is good."""
    res = np.asarray(res, np.float64)
    bad = np.asarray(bad, bool) | ~np.isfinite(res) | ~np.isfinite(np.asarray(noise, np.float64))
    h = int(np.ceil(r))
    k = np.arange(-h, h + 1)
    K = (np.hypot(*np.meshgrid(k, k)) <= r).astype(np.float64)
    r0 = np.where(bad, 0.0, res)
    v0 = np.where(bad, 0.0, np.square(np.asarray(noise, np.float64)))
    s = ndimage.convolve(r0, K, mode="constant")
    v = ndimage.convolve(v0, K, mode="constant")
    n = ndimage.convolve((~bad).astype(np.float64), K, mode="constant")
    with np.errstate(invalid="ignore", divide="ignore"):
        z = np.where((n >= min_good * K.sum()) & (v > 0), s / np.sqrt(v), np.nan)
    return z


def _mad(v: NDArray[np.float64]) -> float:
    v = v[np.isfinite(v)]
    if v.size < 5:
        return np.nan
    return 1.4826 * float(np.median(np.abs(v - np.median(v))))


def fit_glow(
    data: ArrayLike,
    flags: ArrayLike | None,
    noise: ArrayLike,
    *,
    epsf=None,
    mask: ArrayLike | None = None,
    stars: NDArray[np.void] | None = None,
    subtract_background: bool = True,
    box: int = 32,
    r_max: float = GLOW_R_MAX_PX,
    min_peak_snr: float = GLOW_MIN_PEAK_SNR,
    max_stars: int = GLOW_MAX_STARS,
    n_iter: int = GLOW_ITER,
) -> Glow:
    """The glow model and noise factor of every bright star in an image (module docstring).

    data: IMAGE (MJy/sr), or an already background-subtracted block with subtract_background=False.
    noise: per-pixel 1 sigma (max(sqrt(VARIANCE), background rms) as core.detect uses; inf/NaN where bad).
    epsf: the photo's EPSF (positions in this array's pixels), or None for the polar term only.
    stars: STAR_DTYPE rows to model (default bright_stars()).
    """
    d = np.asarray(data, np.float64)
    m = build_mask(d, flags, mask)
    nz = _noise_map(noise, d.shape, 1.0)
    good = ~m & np.isfinite(nz)
    dd = np.where(m, 0.0, d)
    level = background(dd, m, box=box).level if subtract_background else np.zeros(d.shape)
    if stars is None:
        stars = bright_stars(d, flags, nz, mask=mask, sub=dd - level, min_peak_snr=min_peak_snr, max_stars=max_stars)
    stars = np.array(stars, dtype=STAR_DTYPE, copy=True)
    model = np.zeros(d.shape)
    k = np.ones(d.shape)
    if len(stars) == 0:
        return Glow(model, k, stars, level if subtract_background else None)
    w = np.where(good, 1.0 / np.square(nz), 0.0)
    if flags is not None:
        sat = (np.asarray(flags).astype(np.int64) & SAT_BITS) != 0
        sat_near = ndimage.binary_dilation(sat, iterations=GLOW_SAT_GROW_PX) if GLOW_SAT_GROW_PX > 0 else sat
    else:
        sat_near = np.zeros(d.shape, bool)
    for it in range(max(1, n_iter)):
        if it > 0 and subtract_background:
            level = background(np.where(m, 0.0, d - model), m, box=box).level
        res = np.where(good, dd - level, np.nan)
        model = np.zeros(d.shape)
        for i in range(len(stars)):
            st, (y0, y1, x0, x1), info = _star_model(res, good, nz, w, sat_near, stars[i], epsf, r_max)
            if st.size == 0:
                continue
            res[y0:y1, x0:x1] -= st
            model[y0:y1, x0:x1] += st
            if it == max(1, n_iter) - 1:
                stars[i]["flux_epsf"], stars[i]["epsf_ok"] = info["flux_epsf"], info["epsf_ok"]
                stars[i]["r_glow"] = info["r_glow"]
                if info["epsf_ok"]:
                    stars[i]["x"], stars[i]["y"] = info["x"], info["y"]
    # local significance: the residual's aperture z, its scatter around each star over the field's
    z = aperture_z(res, nz, ~good)
    k, r_k, k_max = noise_factor(z, stars["x"], stars["y"], np.where(stars["saturated"], stars["r_core"], 0.0),
                                 r_max, use=stars["r_glow"] > 0)
    stars["r_k"], stars["k_max"] = r_k, k_max
    return Glow(model, k, stars, level if subtract_background else None)


def noise_factor(z: ArrayLike, x: ArrayLike, y: ArrayLike, r_min: ArrayLike, r_max: float, *,
                 use: ArrayLike | None = None, z_field: float | None = None, bin_min_px: int = GLOW_BIN_MIN_PX,
                 max_sectors: int = GLOW_MAX_SECTORS, min_width: float = GLOW_RING_MIN_WIDTH_PX,
                 k_stop: float = GLOW_K_STOP, r_keep: ArrayLike = 0.0) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    """How much noisier than the field a significance map z is around each centre (x, y): the robust scatter
    (1.4826 MAD) of z in polar bins from r_min to r_max over z_field (default: z's own over the whole map, the
    field level with crowding), at least 1, linear between bins, ending at the first ring where it falls below
    k_stop. z: ~N(0, 1) on noise (aperture_z, or a matched-filter S/N map); NaN where unknown. Within r_keep of a
    centre k stays 1 (the star itself).
    Returns (k map, r_k, k_max per centre); where centres overlap the larger k wins."""
    z = np.asarray(z, np.float64)
    x, y = np.atleast_1d(np.asarray(x, float)), np.atleast_1d(np.asarray(y, float))
    r_min = np.broadcast_to(np.asarray(r_min, float), x.shape)
    r_keep = np.broadcast_to(np.asarray(r_keep, float), x.shape)
    use = np.ones(x.size, bool) if use is None else np.broadcast_to(np.asarray(use, bool), x.shape)
    k = np.ones(z.shape)
    r_k, k_max = np.zeros(x.size), np.ones(x.size)
    fin = np.isfinite(z)
    if z_field is None:
        zs = z[fin]
        z_field = _mad(zs[:: max(1, zs.size // 2_000_000)])
    if not np.isfinite(z_field) or z_field <= 0:
        z_field = 1.0
    ny, nx = z.shape
    R = int(np.ceil(r_max))
    for i in range(x.size):
        if not use[i]:
            continue
        y0, y1 = max(int(round(y[i])) - R, 0), min(int(round(y[i])) + R + 1, ny)
        x0, x1 = max(int(round(x[i])) - R, 0), min(int(round(x[i])) + R + 1, nx)
        if y0 >= y1 or x0 >= x1:
            continue
        yy, xx = np.mgrid[y0:y1, x0:x1]
        r = np.hypot(xx - x[i], yy - y[i])
        th = np.mod(np.arctan2(yy - y[i], xx - x[i]), 2 * np.pi)
        zz = z[y0:y1, x0:x1]
        g = np.isfinite(zz)
        rings = _polar(zz, r, th, g, float(r_min[i]), r_max, _mad, bin_min_px=bin_min_px, max_sectors=max_sectors,
                       min_width=min_width)
        if not rings:
            continue
        for q in rings:
            q.vals = np.maximum(np.nan_to_num(q.vals, nan=z_field) / z_field, 1.0) - 1.0
        end, keep = 0.0, []
        for q in rings:
            if float(np.median(q.vals)) + 1.0 < k_stop and float(np.max(q.vals)) + 1.0 < 1.5 * k_stop:
                break
            end = q.b
            keep.append(q)
        r_k[i] = end
        if end <= 0:
            continue
        kk = _polar_eval(keep, r, th, end) + 1.0
        kk[r < r_keep[i]] = 1.0
        k_max[i] = float(np.max(kk))
        k[y0:y1, x0:x1] = np.maximum(k[y0:y1, x0:x1], kk)
    return k, r_k, k_max
