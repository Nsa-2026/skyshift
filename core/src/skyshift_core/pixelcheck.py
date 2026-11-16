"""Pixel checks of a candidate mover: what the dot tables can't tell, measured in NASA's photos.

Pure numpy, no I/O: the caller reads the pixels (scripts/candidates/pixel_check.py reads small row strips of
the public S3 copy) and passes cutouts in MJy/sr with 0-based pixel positions (x = column, y = row). The same
rules can be re-implemented on the browser's frames (raw × scale + zero; raw 0 = no data).

- forced_flux: aperture flux at given places, with an EMPIRICAL error (random apertures in the same cutout):
  VARIANCE underestimates the noise 2–3× in these fields (artefacts audit, 16 Nov: ecl04 D3 65 µJy from
  VARIANCE vs 145–200 µJy measured).
- moment_shape: the pipeline's dot `size` (√(a·b), second moments) and elongation, same window and annulus.
- size_ratio: a dot's size against the stars of similar S/N in the same cutout (cosmic rays are narrower).
- partner_colour: the simultaneous-partner test (D1+D4, D2+D5, D3+D6) against the known-asteroid colours.
- same_pixel: the hot-pixel rule of contracts/api.md (`flaw`): every dot within ±1 px on one detector while
  the sky moved.
- one_photo: is a dot circled in one photo a steady faint source, a cosmic ray, something that moved, or a flash?
- path_summary: was a mover seen below the cut where its path predicts it in other exposures?
- persistence: a bright source on the same detector pixels just before (a leftover image).
Numbers and their origin are next to each constant; the validation is in scripts/candidates/pixel_check.py
(--validate) and its report.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import sep
from numpy.typing import ArrayLike, NDArray

from .detect import APERTURE_R_PX
from .flags import MASK_BITS, SAT_BITS, TRANSIENT
from .photometry import APERTURE_CORRECTION

PIXEL_ARCSEC = 6.152
UJY_PER_MJYSR = 889.7  # µJy in one 6.152″ pixel per 1 MJy/sr (photometry.ujy_per_mjysr on real headers)
PAIR = {1: 4, 2: 5, 3: 6, 4: 1, 5: 2, 6: 3}  # detectors exposed at the same moment

# --- forced photometry ----------------------------------------------------------------------------------------
ANNULUS_PX = (4.0, 7.0)  # local background: median of this annulus
NOISE_N = 200  # random apertures for the empirical error
NOISE_MIN_SEP_PX = 8.0  # random apertures keep this far from every target
NOISE_EDGE_PX = 7.0  # and this far inside the cutout (the annulus must fit)


@dataclass
class Forced:
    """Forced aperture photometry at one place. err_ujy is empirical (1.4826 × MAD of random apertures)."""

    flux_ujy: float  # minus zero_ujy
    err_ujy: float
    snr: float
    masked: bool  # a mask bit (contracts/flags.md) or no data within 1 px of the centre
    n_noise: int  # random apertures behind err_ujy
    zero_ujy: float = 0.0  # median of the random apertures (the cutout's zero point), already subtracted
    dx: float = np.nan  # centroid of the light within SHAPE_R_PX, px from the place (nan without positive light)
    dy: float = np.nan

    @property
    def ok(self) -> bool:
        return bool(np.isfinite(self.flux_ujy) and np.isfinite(self.err_ujy) and self.err_ujy > 0 and not self.masked)


def _bad(data: NDArray, flags: ArrayLike | None) -> NDArray[np.bool_]:
    bad = ~np.isfinite(data)
    if flags is not None:
        bad |= (np.asarray(flags).astype(np.int64) & MASK_BITS) != 0
    return bad


def _ap_sums(d: NDArray, bad: NDArray, xs: NDArray, ys: NDArray, r: float, ann: tuple[float, float]) -> NDArray:
    """Background-subtracted r-px aperture sums (MJy/sr summed) at (xs, ys): sep's sub-pixel circle on the
    good pixels, minus the median of the good annulus pixels × the good aperture area. NaN without a background."""
    dd = np.ascontiguousarray(np.where(bad, 0.0, d), dtype=np.float64)
    m = np.ascontiguousarray(bad)
    ones = np.ascontiguousarray(np.where(bad, 0.0, 1.0))
    s, _, _ = sep.sum_circle(dd, xs, ys, r, mask=m, subpix=5)
    area, _, _ = sep.sum_circle(ones, xs, ys, r, mask=m, subpix=5)
    ny, nx = d.shape
    h = int(np.ceil(ann[1]))
    k = np.arange(-h, h + 1)
    out = np.full(xs.size, np.nan)
    for i, (x, y) in enumerate(zip(xs, ys)):
        ix, iy = int(round(x)), int(round(y))
        y0, y1, x0, x1 = max(0, iy - h), min(ny, iy + h + 1), max(0, ix - h), min(nx, ix + h + 1)
        if y1 <= y0 or x1 <= x0:
            continue
        yy, xx = np.mgrid[y0:y1, x0:x1]
        rr = (xx - x) ** 2 + (yy - y) ** 2
        sel = (rr >= ann[0] ** 2) & (rr <= ann[1] ** 2) & ~bad[y0:y1, x0:x1]
        if sel.sum() < 8:
            continue
        out[i] = s[i] - np.median(d[y0:y1, x0:x1][sel]) * area[i]
    del k
    return out


def forced_flux(data: ArrayLike, x: ArrayLike, y: ArrayLike, *, flags: ArrayLike | None = None,
                conv: float = UJY_PER_MJYSR, r: float = APERTURE_R_PX, annulus: tuple[float, float] = ANNULUS_PX,
                n_noise: int = NOISE_N, min_sep: float = NOISE_MIN_SEP_PX, seed: int = 0) -> list[Forced]:
    """Forced aperture photometry at each (x, y) of one cutout (MJy/sr), in µJy.

    flux: r = 2 px circle minus the annulus median, × 1.033 (aperture correction at r = 2 px) × conv.
    err: 1.4826 × MAD of the same measurement at n_noise random places of the cutout that keep min_sep px from
    every target and NOISE_EDGE_PX inside the edges (one estimate shared by the targets; nan if < 20 places).
    The median of those random apertures (the zero point: ±0.3σ from photo to photo) is subtracted from flux, so a
    source means light beyond a typical spot of that cutout. dx, dy: where the light near the place is centred.
    """
    d = np.asarray(data, dtype=np.float64)
    xs = np.atleast_1d(np.asarray(x, dtype=np.float64))
    ys = np.atleast_1d(np.asarray(y, dtype=np.float64))
    bad = _bad(d, flags)
    corr = APERTURE_CORRECTION if abs(r - APERTURE_R_PX) < 1e-9 else 1.0
    f = _ap_sums(d, bad, xs, ys, r, annulus) * corr * conv
    ny, nx = d.shape
    rng = np.random.default_rng(seed)
    e = NOISE_EDGE_PX
    n_ok = 0
    err = np.nan
    zero = 0.0
    if nx > 2 * e + 1 and ny > 2 * e:
        rx = rng.uniform(e, nx - 1 - e, 4 * n_noise)
        ry = rng.uniform(e, max(e, ny - 1 - e), 4 * n_noise) if ny - 1 - e > e else np.full(4 * n_noise, (ny - 1) / 2)
        far = np.ones(rx.size, bool)
        for tx, ty in zip(xs, ys):
            far &= np.hypot(rx - tx, ry - ty) >= min_sep
        rx, ry = rx[far][:n_noise], ry[far][:n_noise]
        cen = np.array([not bad[int(round(b)), int(round(a))] for a, b in zip(rx, ry)], bool) if rx.size else np.zeros(0, bool)
        rx, ry = rx[cen], ry[cen]
        if rx.size:
            rf = _ap_sums(d, bad, rx, ry, r, annulus) * corr * conv
            rf = rf[np.isfinite(rf)]
            n_ok = int(rf.size)
            if n_ok >= 20:
                zero = float(np.median(rf))
                err = float(1.4826 * np.median(np.abs(rf - zero)))
    f = f - zero
    cdx, cdy = light_centroid(d, xs, ys, flags=flags)
    out = []
    for xi, yi, fi, ox, oy in zip(xs, ys, f, cdx, cdy):
        ix, iy = int(round(xi)), int(round(yi))
        core = bad[max(0, iy - 1):iy + 2, max(0, ix - 1):ix + 2]
        masked = bool(core.size == 0 or core.any() or not (0 <= ix < nx and 0 <= iy < ny))
        snr = float(fi / err) if np.isfinite(fi) and np.isfinite(err) and err > 0 else np.nan
        out.append(Forced(float(fi), float(err), snr, masked, n_ok, zero, float(ox), float(oy)))
    return out


def combine(flux: ArrayLike, err: ArrayLike) -> tuple[float, float, int]:
    """Inverse-variance mean of fluxes: (flux, err, n used); finite, positive errors only. (nan, nan, 0) if none."""
    f = np.asarray(flux, np.float64)
    e = np.asarray(err, np.float64)
    ok = np.isfinite(f) & np.isfinite(e) & (e > 0)
    if not ok.any():
        return np.nan, np.nan, 0
    w = 1.0 / e[ok] ** 2
    return float((f[ok] * w).sum() / w.sum()), float(1.0 / np.sqrt(w.sum())), int(ok.sum())


# --- shape -----------------------------------------------------------------------------------------------------
SHAPE_R_PX = 2.5  # pipeline config ELONG_R_PX: second moments of the pixels within this radius
SHAPE_BKG_PX = (3.5, 5.5)  # pipeline config ELONG_BKG_PX: local background annulus


def moment_shape(data: ArrayLike, x: ArrayLike, y: ArrayLike, *, flags: ArrayLike | None = None,
                 r: float = SHAPE_R_PX, bkg: tuple[float, float] = SHAPE_BKG_PX
                 ) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    """(size_px, elong, theta_deg) of each place: size = √(a·b), a, b = √ of the second-moment matrix's eigenvalues
    of the good pixels within r px, minus the median of the bkg annulus, negatives clipped to 0 (the pipeline's
    dot `size` = size_px × 6.152″, dots.shape_moments); elong = a/b (≤ 99); theta = major axis from +x towards +y,
    (−90, 90]. NaN near the edge, with < 5 annulus pixels, or no positive signal. A star: size ≈ 0.6–0.8 px."""
    d = np.asarray(data, dtype=np.float64)
    bad = _bad(d, flags)
    xs = np.atleast_1d(np.asarray(x, np.float64))
    ys = np.atleast_1d(np.asarray(y, np.float64))
    ny, nx = d.shape
    h = int(np.ceil(bkg[1]))
    k = np.arange(-h, h + 1)
    ix, iy = np.rint(xs).astype(np.int64), np.rint(ys).astype(np.int64)
    inside = (ix - h >= 0) & (iy - h >= 0) & (ix + h + 1 <= nx) & (iy + h + 1 <= ny)
    yy = np.clip(iy[:, None, None] + k[None, :, None], 0, ny - 1)
    xx = np.clip(ix[:, None, None] + k[None, None, :], 0, nx - 1)
    st = d[yy, xx]
    good = ~bad[yy, xx] & np.isfinite(st)
    dx = k[None, None, :] - (xs - ix)[:, None, None]
    dy = k[None, :, None] - (ys - iy)[:, None, None]
    rr = dx * dx + dy * dy
    ann = good & (rr >= bkg[0] ** 2) & (rr <= bkg[1] ** 2)
    n_ann = ann.reshape(len(xs), -1).sum(axis=1)
    med = np.array([np.median(s[a]) if a.any() else np.nan for s, a in zip(st, ann)])
    w = np.where(good & (rr <= r * r), st - med[:, None, None], 0.0)
    w = np.clip(np.nan_to_num(w, nan=0.0, posinf=0.0, neginf=0.0), 0.0, None)
    tot = w.sum(axis=(1, 2))
    ok = inside & (n_ann >= 5) & (tot > 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        cxx = (w * dx * dx).sum(axis=(1, 2)) / tot
        cyy = (w * dy * dy).sum(axis=(1, 2)) / tot
        cxy = (w * dx * dy).sum(axis=(1, 2)) / tot
        tr, det = cxx + cyy, cxx * cyy - cxy * cxy
        disc = np.sqrt(np.maximum(tr * tr / 4.0 - det, 0.0))
        l1, l2 = tr / 2.0 + disc, tr / 2.0 - disc
        el = np.where(l2 > 1e-6, np.sqrt(l1 / l2), 99.0)
        th = np.degrees(0.5 * np.arctan2(2.0 * cxy, cxx - cyy))
        size = np.sqrt(np.sqrt(np.maximum(l1, 0.0)) * np.sqrt(np.maximum(l2, 0.0)))
    th = np.where(th <= -90.0, th + 180.0, th)
    return (np.where(ok, size, np.nan), np.where(ok, np.minimum(el, 99.0), np.nan), np.where(ok, th, np.nan))


def light_centroid(data: ArrayLike, x: ArrayLike, y: ArrayLike, *, flags: ArrayLike | None = None,
                   r: float = SHAPE_R_PX, bkg: tuple[float, float] = SHAPE_BKG_PX
                   ) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """(dx, dy) px from each place to the centroid of the light within r px (good pixels minus the bkg annulus
    median, negatives clipped): ~0 for a source at the place, ≥ 1 px when a neighbour's light spills in."""
    d = np.asarray(data, dtype=np.float64)
    bad = _bad(d, flags)
    xs = np.atleast_1d(np.asarray(x, np.float64))
    ys = np.atleast_1d(np.asarray(y, np.float64))
    ny, nx = d.shape
    h = int(np.ceil(bkg[1]))
    k = np.arange(-h, h + 1)
    ix, iy = np.rint(xs).astype(np.int64), np.rint(ys).astype(np.int64)
    yy = np.clip(iy[:, None, None] + k[None, :, None], 0, ny - 1)
    xx = np.clip(ix[:, None, None] + k[None, None, :], 0, nx - 1)
    st = d[yy, xx]
    good = ~bad[yy, xx] & np.isfinite(st)
    dx = k[None, None, :] - (xs - ix)[:, None, None]
    dy = k[None, :, None] - (ys - iy)[:, None, None]
    rr = dx * dx + dy * dy
    ann = good & (rr >= bkg[0] ** 2) & (rr <= bkg[1] ** 2)
    med = np.array([np.median(s_[a]) if a.any() else np.nan for s_, a in zip(st, ann)])
    w = np.where(good & (rr <= r * r), st - med[:, None, None], 0.0)
    w = np.clip(np.nan_to_num(w, nan=0.0, posinf=0.0, neginf=0.0), 0.0, None)
    tot = w.sum(axis=(1, 2))
    with np.errstate(divide="ignore", invalid="ignore"):
        ox = np.where(tot > 0, (w * dx).sum(axis=(1, 2)) / tot, np.nan)
        oy = np.where(tot > 0, (w * dy).sum(axis=(1, 2)) / tot, np.nan)
    return ox, oy


SIZE_SNR_FACTOR = 2.0  # stars "of similar S/N": within ×/÷ 2 of the dot's
SIZE_MIN_STARS = 8


def size_ratio(size: float, snr: float, star_size: ArrayLike, star_snr: ArrayLike, *,
               factor: float = SIZE_SNR_FACTOR, min_stars: int = SIZE_MIN_STARS) -> tuple[float, int]:
    """(size ÷ the median size of the stars whose S/N is within ×/÷ factor of snr, number of those stars);
    (nan, n) with fewer than min_stars. Sizes in any one unit."""
    s = np.asarray(star_size, np.float64)
    q = np.asarray(star_snr, np.float64)
    ok = np.isfinite(s) & np.isfinite(q) & (q >= snr / factor) & (q <= snr * factor)
    n = int(ok.sum())
    if n < min_stars or not np.isfinite(size):
        return np.nan, n
    return float(size / np.median(s[ok])), n


# --- partner detector -----------------------------------------------------------------------------------------
# Flux ratio long-wavelength / short-wavelength (D4/D1, D5/D2, D6/D3) of real asteroids, measured 16 Nov (artefacts
# audit §3) on 40,711 movers that tracklets put on tracks (known asteroids) and 132 one-side sightings with forced
# photometry in the partner. Seen on both sides: D4/D1 q01 0.24 – q99 1.07; D5/D2 0.21 – 5.3; D6/D3 0.27 – 20.2.
# One side only: D4/D1 at most 1.08, D5/D2 at most 11.3, D6/D3 at most 17.4 (lower limits to 20.6).
# Physical limits (reflected sunlight at the blue end, a dark near-Earth asteroid ≥ 1 AU from the Sun at the red
# end): ~0.12 and D4/D1 3, D5/D2 15, D6/D3 50. Comet 306P (ecl04) is D6/D3 ≥ 8: red is no reason to drop anything.
KNOWN_RATIO = {4: (0.24, 1.1), 5: (0.21, 11.3), 6: (0.27, 21.0)}  # keyed by the MWIR detector of the pair
PHYSICAL_RATIO = {4: (0.12, 3.0), 5: (0.12, 15.0), 6: (0.12, 50.0)}
PARTNER_SEEN_SNR = 3.0


def partner_colour(det: int, own_flux: float, partner_flux: float, partner_err: float) -> dict:
    """The simultaneous partner of `det` measured partner_flux ± partner_err (µJy, combined over the exposures) at the
    place where `det` saw own_flux. Returns ratio = MWIR/SWIR (or its 3σ bound), the known-asteroid range and where it
    falls: verdict `seen` (partner ≥ 3σ), `red`/`blue` (not seen and the bound lies past every known asteroid), or
    `normal` (not seen, bound inside the range). bound_kind `lower` when `det` is MWIR (redder than), `upper` when SWIR."""
    mw = det >= 4
    mdet = det if mw else PAIR[det]
    lo, hi = KNOWN_RATIO[mdet]
    out = dict(det=int(det), partner=int(PAIR[det]), own_flux=float(own_flux), partner_flux=float(partner_flux),
               partner_err=float(partner_err), snr=float(partner_flux / partner_err) if partner_err > 0 else np.nan,
               known_lo=lo, known_hi=hi, ratio=np.nan, bound=np.nan, bound_kind="lower" if mw else "upper")
    if not (np.isfinite(partner_flux) and np.isfinite(partner_err) and partner_err > 0 and own_flux > 0):
        out["verdict"] = "untested"
        return out
    if partner_flux >= PARTNER_SEEN_SNR * partner_err:
        out["ratio"] = own_flux / partner_flux if mw else partner_flux / own_flux
        out["verdict"] = "seen"
        out["in_range"] = bool(lo <= out["ratio"] <= hi)
        return out
    lim = max(partner_flux, 0.0) + 3.0 * partner_err
    out["bound"] = own_flux / lim if mw else lim / own_flux
    out["verdict"] = ("red" if out["bound"] > hi else "normal") if mw else ("blue" if out["bound"] < lo else "normal")
    return out


# --- hot pixel --------------------------------------------------------------------------------------------------
SAME_PIXEL_PX = 1.0  # contracts/api.md v3.23 `flaw`: every dot within ±1 px on one detector
SKY_MOVED_ARCSEC = 6.0  # ... although the sky moved under it (a dither step is ~115 px = 700″)


def same_pixel(det: ArrayLike, x: ArrayLike, y: ArrayLike, ra: ArrayLike, dec: ArrayLike,
               tol: float = SAME_PIXEL_PX) -> dict:
    """The hot-pixel rule. For each detector with ≥ 2 dots: the largest |Δx|, |Δy| between its dots and the largest
    sky separation (″). `flaw` is True when every detector with ≥ 2 dots keeps them within ±tol px in x and y while the
    sky under them moved ≥ SKY_MOVED_ARCSEC, and at least one detector has ≥ 2 dots."""
    det = np.asarray(det, np.int64)
    x, y = np.asarray(x, np.float64), np.asarray(y, np.float64)
    ra, dec = np.asarray(ra, np.float64), np.asarray(dec, np.float64)
    per = {}
    for d in np.unique(det):
        m = det == d
        if m.sum() < 2:
            continue
        dra = (ra[m][:, None] - ra[m][None, :] + 180.0) % 360.0 - 180.0
        sky = np.hypot(dra * np.cos(np.radians(dec[m].mean())), dec[m][:, None] - dec[m][None, :]) * 3600.0
        per[int(d)] = dict(dx=float(np.ptp(x[m])), dy=float(np.ptp(y[m])), sky_arcsec=float(np.nanmax(sky)), n=int(m.sum()))
    flaw = bool(per) and all(p["dx"] <= 2 * tol and p["dy"] <= 2 * tol and p["sky_arcsec"] >= SKY_MOVED_ARCSEC
                             and _within(x[det == d], y[det == d], tol) for d, p in per.items())
    return dict(flaw=flaw, per_det=per)


def _within(x: NDArray, y: NDArray, tol: float) -> bool:
    """Every dot within ±tol px of the first one in x and y."""
    return bool((np.abs(x - x[0]) <= tol).all() and (np.abs(y - y[0]) <= tol).all())


# --- one photo --------------------------------------------------------------------------------------------------
STEADY_SNR = 3.0  # the other exposures together see a source at the spot at ≥ 3σ ...
STEADY_RATIO = 0.3  # ... at ≥ 0.3 × the circled flux (a source just under the cut reads lower than its lucky photo)
UNCLEAR_LIMIT = 0.5  # nothing seen, but a source half as bright as the circled one could hide in the others' noise
SHARP_RATIO = 0.7  # narrower than 0.7 × the stars of similar S/N (vet's sharp gate; no real asteroid below 0.69 there)
MIN_OTHERS = 2
TRANSIENT_R_PX = 3.0
NEIGHBOUR_PX = 0.8  # steady light whose centroid sits ≥ 0.8 px (5″) from the place: a neighbour's light spilling in


def transient_pixels(flags: ArrayLike, x: float, y: float, r: float = TRANSIENT_R_PX) -> int:
    """Pixels with NASA's TRANSIENT bit (a cosmic-ray hit found in the ramp) within r px of (x, y)."""
    f = np.asarray(flags).astype(np.int64)
    ny, nx = f.shape
    yy, xx = np.ogrid[:ny, :nx]
    sel = (xx - x) ** 2 + (yy - y) ** 2 <= r * r
    return int(((f & TRANSIENT) != 0)[sel].sum())


def one_photo(own_flux: float, own_err: float, other_flux: ArrayLike, other_err: ArrayLike, *,
              size_ratio: float = np.nan, transient_px: int | None = None, moved: bool | None = None,
              other_offset: ArrayLike | None = None) -> dict:
    """What a dot circled in one photo is, from forced photometry at the same sky place in the other exposures of the
    same detector (core-api-onephoto.md). Verdicts, first match: `too_few` (< MIN_OTHERS usable others), `steady`
    (others together ≥ STEADY_SNR σ at ≥ STEADY_RATIO × own: a faint fixed source) or `neighbour` (the same, but the
    light in the others is centred ≥ NEIGHBOUR_PX away: other_offset = per other exposure the (east, north) offset of
    its light centroid from the place in one sky frame, in pixels of 6.152″; their flux-weighted mean vector: a star
    next to it spills into the spot), `unclear` (their 3σ limit is not
    below UNCLEAR_LIMIT × own), `cosmic_ray` (nothing there and sharper than SHARP_RATIO × stars), `moved` (nothing
    there, a dot nearby in the next/previous exposure), else `flash`. transient_px (NASA's TRANSIENT pixels next to it)
    is reported but decides nothing: it sits beside 12% of real asteroids' dots (a moving source changes a pixel's
    ramp during the exposure) and 21% of junk dots (validation, 16 Nov)."""
    f, e, n = combine(other_flux, other_err)
    fo = np.asarray(other_flux, np.float64)
    eo = np.asarray(other_err, np.float64)
    okm = np.isfinite(fo) & np.isfinite(eo) & (eo > 0)
    n_seen = int((fo[okm] >= 3.0 * eo[okm]).sum())
    res = dict(n_other=n, n_seen=n_seen, other_flux_ujy=f, other_err_ujy=e,
               other_snr=float(f / e) if n else np.nan, own_flux_ujy=float(own_flux),
               own_snr=float(own_flux / own_err) if own_err and own_err > 0 else np.nan,
               ratio=float(f / own_flux) if n and own_flux > 0 else np.nan,
               limit_ratio=float((max(f, 0.0) + 3.0 * e) / own_flux) if n and own_flux > 0 else np.nan,
               size_ratio=float(size_ratio) if size_ratio is not None else np.nan,
               transient_px=transient_px, moved=moved, offset_px=np.nan)
    if other_offset is not None and len(other_offset):
        oo = np.asarray(other_offset, np.float64).reshape(-1, 2)
        sel = okm & np.isfinite(oo).all(axis=1) & (fo > 0)
        if sel.any():
            v = np.average(oo[sel], axis=0, weights=fo[sel])
            res["offset_px"] = float(np.hypot(v[0], v[1]))
    if n < MIN_OTHERS or not own_flux > 0:
        res["verdict"] = "too_few"
    elif res["other_snr"] >= STEADY_SNR and res["ratio"] >= STEADY_RATIO:
        res["verdict"] = "neighbour" if res["offset_px"] >= NEIGHBOUR_PX else "steady"
    elif res["limit_ratio"] >= UNCLEAR_LIMIT:
        res["verdict"] = "unclear"
    elif np.isfinite(res["size_ratio"]) and res["size_ratio"] < SHARP_RATIO:
        res["verdict"] = "cosmic_ray"
    elif moved:
        res["verdict"] = "moved"
    else:
        res["verdict"] = "flash"
    return res


# --- faint on the path ------------------------------------------------------------------------------------------
PATH_SEEN_SNR = 3.0  # a forced flux ≥ 3σ at the predicted place ...
PATH_SEEN_RATIO = (0.25, 4.0)  # ... between 0.25 and 4 × the mover's own flux (real asteroids: ratio ≤ 2.6 in a session)
PATH_SHOULD_SNR = 5.0  # an exposure "should show it" when its own flux would read ≥ 5σ there
PATH_MIN_FOUND = 2  # ok when seen in at least this many exposures


def path_summary(own_flux: float, flux: ArrayLike, err: ArrayLike, chance: ArrayLike | None = None) -> dict:
    """Forced fluxes at a mover's predicted places in other exposures of its detector (best place within the
    prediction's error). chance: per exposure, the S/N that random places reach with the same search (the
    look-elsewhere level; ≤ 3 for a point). Counts: n (measured), n_should (own_flux would be ≥ 5σ there), n_found
    (≥ max(3, chance) σ at 0.25–4 × own), and the combined flux of the exposures that should show it."""
    f = np.asarray(flux, np.float64)
    e = np.asarray(err, np.float64)
    c = np.full(f.shape, PATH_SEEN_SNR) if chance is None else np.maximum(np.asarray(chance, np.float64), PATH_SEEN_SNR)
    ok = np.isfinite(f) & np.isfinite(e) & (e > 0)
    snr = np.where(ok, f / np.where(e > 0, e, 1.0), np.nan)
    found = ok & (snr >= c) & (f >= PATH_SEEN_RATIO[0] * own_flux) & (f <= PATH_SEEN_RATIO[1] * own_flux)
    should = ok & (own_flux / np.where(e > 0, e, np.inf) >= PATH_SHOULD_SNR)
    cf, ce, cn = combine(f[should], e[should])
    return dict(n=int(ok.sum()), n_should=int(should.sum()), n_found=int(found.sum()),
                n_found_should=int((found & should).sum()), max_snr=float(np.nanmax(snr)) if ok.any() else np.nan,
                should_flux_ujy=cf, should_err_ujy=ce,
                should_limit_ratio=float((max(cf, 0.0) + 3.0 * ce) / own_flux) if cn and own_flux > 0 else np.nan,
                verdict=("found" if found.sum() >= PATH_MIN_FOUND else
                         "absent" if should.sum() >= PATH_MIN_FOUND and found.sum() == 0
                         and (max(cf, 0.0) + 3.0 * ce) < 0.5 * own_flux else "unclear"))


# --- persistence ------------------------------------------------------------------------------------------------
PERSIST_BRIGHT_FACTOR = 30.0  # a source ≥ 30 × the dot's flux on the same pixels just before can leave it behind
PERSIST_WINDOW_MIN = 30.0  # previous exposures of the same detector up to this many minutes earlier
PERSIST_R_PX = 1.5


def persistence(own_flux: float, prev_peak_flux: ArrayLike, prev_sat: ArrayLike, prev_persist: ArrayLike | None = None
                ) -> dict:
    """Was a dot's detector pixel bright just before? prev_*: per earlier exposure of the same detector (within
    PERSIST_WINDOW_MIN), the forced flux there (µJy), whether OVERFLOW/NONLINEAR was set within PERSIST_R_PX, and
    (optional) whether NASA's PERSIST bit is set there now or then. `bright` when any earlier exposure was saturated
    there or ≥ PERSIST_BRIGHT_FACTOR × own_flux; `saturated` when one was saturated."""
    pf = np.asarray(prev_peak_flux, np.float64)
    ps = np.asarray(prev_sat, bool)
    pp = np.zeros(pf.shape, bool) if prev_persist is None else np.asarray(prev_persist, bool)
    bright = ps | (np.isfinite(pf) & (pf >= PERSIST_BRIGHT_FACTOR * max(own_flux, 0.0)))
    return dict(n_prev=int(pf.size), bright=bool(bright.any()), saturated=bool(ps.any()), persist_flag=bool(pp.any()),
                max_prev_flux=float(np.nanmax(pf)) if np.isfinite(pf).any() else np.nan)


def saturated_near(flags: ArrayLike, x: float, y: float, r: float = PERSIST_R_PX) -> bool:
    """OVERFLOW or NONLINEAR within r px of (x, y)."""
    f = np.asarray(flags).astype(np.int64)
    ny, nx = f.shape
    yy, xx = np.ogrid[:ny, :nx]
    sel = (xx - x) ** 2 + (yy - y) ** 2 <= r * r
    return bool(((f & SAT_BITS) != 0)[sel].any())
