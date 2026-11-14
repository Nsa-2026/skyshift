"""GET /api/object: what is at a position, its spectrum and its motion (contracts/api.md).

Who: the nearest of (a) the built-in targets and (b) Gaia DR3 stars whose path over the SPHEREx
years passes within 6" of the position. Fast stars are far from their 2016.0 Gaia position
(Barnard's Star: 112"), so Gaia is asked for a 3.5' cone and every star is moved along its own
track before the match.

Spectrum: photometry on every cutout of every detector, at the object's position at that
exposure's time (proper motion + parallax when we have them, else the position asked).
Each point: wavelength of that pixel from WCS-WAVE, flux and error in uJy, detector, MJD-AVG,
visit, saturated (OVERFLOW or NONLINEAR in the core). Three ways to measure, per point
(docs/research/2026-11-14-target-data-check.md has the numbers):
- the r = 2 px aperture of core.aperture_photometry, when its core is clean (most points);
- a saturated or masked core: the flux from the PSF wings (wing_photometry), with a 50%
  systematic error. The aperture there leaves the core out and read 4-10x low (Barnard's Star);
  R Draconis's aperture is masked in every exposure, so it had no spectrum at all;
- a Gaia star 5-15" away at that time: a joint fit of both at their known positions
  (blended_photometry). WISE 0855-0714 passes a G = 16.3 star (11" -> 4" over the visits),
  which put ~1 mJy into D1-D3, where a 250 K brown dwarf is dark.
A point that can't be measured honestly (masked core on a faint source, a neighbour closer than
5" to a source Gaia doesn't see) is left out rather than shown wrong.

Measure: core.measure_shift between the first and last visit stacks of one detector, on a
native-scale (6.15"/px) 15' grid, with quiet Gaia stars as the error floor. The detector is the
one with the best signal that is not saturated; if every detector saturates, the least saturated
(a built-in target seen only in some detectors is measured in those: MEASURE_DETECTORS).
The two times are the MEDIAN exposure times of the two stacks: a per-pixel median of a slowly
moving star sits where the star was at the median time, not at mjd_mid (the middle of the first
and last exposure). Barnard's Star, visit 3: 24 exposures in 9 days and one 35 days in, so
mjd_mid is 14.6 days (0.41") after the median. dt_yr is therefore the difference of the median
times (contracts/api.md says mjd_mid: raised with P1). predicted_arcsec includes parallax.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import numpy as np
from scipy.ndimage import binary_dilation
from scipy.optimize import least_squares
from scipy.special import erf
from skyshift_core import (
    GAIA_EPOCH_MJD,
    MASK_BITS,
    SAT_BITS,
    ShiftError,
    aperture_photometry,
    bad_pixel_mask,
    is_saturated_core,
    measure_shift,
    propagate,
    select_reference_stars,
    sky_to_pixel,
    tangent_offset_arcsec,
    ujy_per_mjysr,
)
from skyshift_core.flags import SUR_ERROR, TRANSIENT
from skyshift_core.photometry import APERTURE_CORRECTION

from . import blink, cutouts, gaia, resolve, search, stack
from .cache import Cache
from .errors import ApiError, NotFoundError

log = logging.getLogger(__name__)

MATCH_ARCSEC = 6.0
GAIA_CONE_ARCMIN = 3.5  # reaches a 19"/yr star 11 years from its 2016.0 position
EDGE_PX = 8  # the aperture's sky annulus reaches 7 px
MEASURE_FOV = 15
MEASURE_PIXEL = stack.NATIVE_ARCSEC
MIN_VISIT_EXPOSURES = 3
DETECTOR_PREFERENCE = (2, 1, 3, 4, 5, 6)  # on ties
# Built-in targets seen only in some detectors. WISE 0855-0714 (a 250 K brown dwarf) is invisible
# short of 3 um; the aperture at its position in D1-D3 holds a blend of background sources, which
# win on signal and measure 0.7" instead of its 8"/yr. In D5 it measures 7.05" +- 0.90" (Gaia-free
# prediction 8.06"), in D6 8.57" +- 3.3". (targets.json says "Expected only in D5-D6" in a note.)
MEASURE_DETECTORS = {"wise0855": (5, 6)}

# --- Photometry of saturated cores: the wings ----------------------------------------------------
# Pixel value per unit total flux (total = the r = 2 px aperture x 1.033) at distance r px from a
# star, x 1e-4: medians over 30-55 isolated stars of 50-150 mJy per detector in the cached
# cutouts of the five targets (14 Nov). Beyond 4.75 px the field stars are lost in the noise.
WING_PROFILE_R = (1.75, 2.25, 2.75, 3.25, 3.75, 4.5)
WING_PROFILE = {
    1: (46.0, 13.0, 5.6, 3.9, 3.3, 2.9),
    2: (51.0, 15.8, 7.4, 5.1, 4.4, 2.6),
    3: (56.0, 16.7, 8.6, 4.9, 3.6, 2.6),
    4: (92.0, 28.3, 11.8, 6.9, 4.0, 2.0),
    5: (97.0, 36.3, 16.6, 8.8, 5.9, 2.9),
    6: (125.0, 45.6, 17.2, 7.7, 4.0, 2.0),
}
WING_R_PX = (2.0, 4.75)  # pixels used
WING_SKY_R_PX = (7.0, 11.0)  # local sky (median)
WING_MIN_PIXELS = 6
# Bright stars set TRANSIENT and SUR_ERROR on their own wings (ramp fits near saturation); those
# pixels follow the profile, so the wing fit keeps them. Every other mask bit is left out, and so
# is the ring of pixels touching the saturated (OVERFLOW, NONLINEAR or NaN) hole.
WING_BAD_BITS = MASK_BITS & ~(TRANSIENT | SUR_ERROR)
# Checked on Barnard's Star against 2MASS J and AllWISE W1/W2: D2 +23%, D5 +9%, D6 -9%, but
# D3 +70-80% and D4 about 2x, and the result moves 25% with the outer radius. 1 sigma = 50%.
WING_SYSTEMATIC = 0.5
WING_MIN_SNR = 5.0  # an unsaturated source with a masked core: below this, no point

# --- Photometry next to a Gaia star: a joint fit --------------------------------------------------
BLEND_ARCSEC = 15.0  # a Gaia star closer than this puts light into the r = 2 px (12.3") aperture
BLEND_MIN_ARCSEC = 5.0  # closer than this, two sources can't be told apart at 6.15" pixels
BLEND_IGNORE_DMAG = 4.0  # a neighbour this much fainter in G than a Gaia target is ignored
BLEND_FIT_R_PX = 3.5
# Pixel-integrated Gaussian per detector (sigma px, aperture flux / Gaussian amplitude), fitted to
# 120-450 isolated stars at S/N > 50 per detector in the targets' cutouts (14 Nov).
BLEND_PSF = {1: (0.38, 1.104), 2: (0.38, 1.086), 3: (0.38, 1.114), 4: (0.46, 1.111), 5: (0.46, 1.089), 6: (0.50, 1.098)}


def _builtin_targets() -> list[dict[str, Any]]:
    """The five targets' astrometry (scripts/validate/data/targets.json), as Gaia-like models."""
    try:
        raw = json.loads(resolve._targets_path().read_text())["targets"]
    except (OSError, ValueError, KeyError) as e:
        log.warning("built-in targets not readable: %s", e)
        return []
    out = []
    for t in raw:
        out.append({
            "name": t["name"], "slug": t["slug"],
            "id": t.get("gaia_source_id"), "ra": t["ra"], "dec": t["dec"],
            "pmra": t.get("pmra"), "pmdec": t.get("pmdec"), "parallax": t.get("parallax"),
            "g": None if t.get("phot_g_mean_mag") is None else round(t["phot_g_mean_mag"], 3),
            "epoch_mjd": t.get("ref_mjd", GAIA_EPOCH_MJD),
        })
    return out


def position_at(model: dict[str, Any], mjd: float, parallax: bool = True) -> tuple[float, float]:
    """The model's (ra, dec) at mjd: proper motion, and parallax if it has one."""
    if model.get("pmra") is None or model.get("pmdec") is None:
        return float(model["ra"]), float(model["dec"])
    plx = model.get("parallax") if parallax else None
    r, d = propagate(model["ra"], model["dec"], model["pmra"], model["pmdec"], mjd,
                     epoch_mjd=model.get("epoch_mjd", GAIA_EPOCH_MJD),
                     parallax=None if plx is None else plx)
    return float(r), float(d)


def track_distance_arcsec(model: dict[str, Any], ra: float, dec: float, mjd_a: float, mjd_b: float) -> float:
    """Smallest distance from (ra, dec) to the model's track between two times (a straight
    segment on the tangent plane; parallax ignored)."""
    a = tangent_offset_arcsec(ra, dec, *position_at(model, mjd_a, parallax=False))
    b = tangent_offset_arcsec(ra, dec, *position_at(model, mjd_b, parallax=False))
    p, q = np.array([float(a[0]), float(a[1])]), np.array([float(b[0]), float(b[1])])
    d = q - p
    t = 0.0 if not d.any() else float(np.clip(-(p @ d) / (d @ d), 0.0, 1.0))
    return float(np.hypot(*(p + t * d)))


def identify(ra: float, dec: float, mjd_a: float, mjd_b: float, stars: list[dict[str, Any]],
             builtins: list[dict[str, Any]]) -> tuple[str | None, dict[str, Any] | None, dict[str, Any] | None]:
    """(name, gaia entry, astrometric model) of the nearest object within 6" of the position at
    some time between mjd_a and mjd_b. A built-in wins over the same star from Gaia."""
    best: tuple[float, int, dict[str, Any]] | None = None
    for rank, group in enumerate((builtins, stars)):
        for m in group:
            d = track_distance_arcsec(m, ra, dec, mjd_a, mjd_b)
            if d <= MATCH_ARCSEC and (best is None or (d, rank) < (best[0] - 0.01, best[1])):
                best = (d, rank, m)
    if best is None:
        return None, None, None
    m = best[2]
    entry = None
    if m.get("id"):
        from_gaia = next((s for s in stars if s["id"] == m["id"]), None)
        entry = from_gaia or {k: m.get(k) for k in ("id", "ra", "dec", "pmra", "pmdec", "parallax", "g")}
    name = m.get("name") or f"Gaia DR3 {m['id']}"
    return name, entry, m


def wing_photometry(data: np.ndarray, flags: np.ndarray, x: float, y: float, det: int,
                    conv: float) -> tuple[float, float, float] | None:
    """(flux, error with the systematic, statistical error) in uJy of a star whose core can't be
    used, from its wings: the median over pixels 2-4.75 px out of (pixel - local sky) / profile(r).
    conv: uJy per MJy/sr pixel. None with fewer than WING_MIN_PIXELS usable pixels."""
    ny, nx = data.shape
    yy, xx = np.mgrid[:ny, :nx]
    r = np.hypot(xx - x, yy - y)
    f = np.asarray(flags).astype(np.int64)
    finite = np.isfinite(data)
    hole = ~finite | ((f & SAT_BITS) != 0)
    use = finite & ((f & WING_BAD_BITS) == 0) & ~binary_dilation(hole, iterations=1)
    use &= (r >= WING_R_PX[0]) & (r <= WING_R_PX[1])
    sky = finite & ((f & MASK_BITS) == 0) & (r >= WING_SKY_R_PX[0]) & (r <= WING_SKY_R_PX[1])
    if use.sum() < WING_MIN_PIXELS or sky.sum() < 20:
        return None
    prof = np.exp(np.interp(r[use], WING_PROFILE_R, np.log(np.asarray(WING_PROFILE[det]) * 1e-4)))
    est = (data[use].astype(np.float64) - float(np.median(data[sky]))) / prof
    total = float(np.median(est))
    stat = 1.4826 * float(np.median(np.abs(est - total))) / np.sqrt(est.size)
    flux, stat = total * conv, stat * conv
    return flux, float(np.hypot(stat, WING_SYSTEMATIC * flux)), stat


def _gauss_psf(nx: int, ny: int, x0: float, y0: float, sigma: float) -> np.ndarray:
    """Pixel-integrated circular Gaussian of unit flux at 0-based (x0, y0)."""
    s = np.sqrt(2.0) * sigma
    cx = np.diff(0.5 * erf((np.arange(nx + 1) - 0.5 - x0) / s))
    cy = np.diff(0.5 * erf((np.arange(ny + 1) - 0.5 - y0) / s))
    return np.outer(cy, cx)


def blended_photometry(data: np.ndarray, flags: np.ndarray, variance: np.ndarray | None, x: float, y: float,
                       others: list[tuple[float, float]], det: int, conv: float) -> tuple[float, float] | None:
    """(flux, error) in uJy of the source at (x, y) fitted jointly with neighbours at `others`
    (pixel positions), all at fixed positions: one Gaussian amplitude each and a flat sky, by
    weighted linear least squares over the good pixels within BLEND_FIT_R_PX of any of them."""
    ny, nx = data.shape
    sigma, k = BLEND_PSF[det]
    yy, xx = np.mgrid[:ny, :nx]
    near = np.zeros(data.shape, bool)
    for px, py in [(x, y), *others]:
        near |= np.hypot(xx - px, yy - py) <= BLEND_FIT_R_PX
    good = near & np.isfinite(data) & ~bad_pixel_mask(flags)
    if variance is not None:
        good &= np.isfinite(variance) & (variance > 0)
    n_par = 2 + len(others)
    if good.sum() < 3 * n_par:
        return None
    cols = [_gauss_psf(nx, ny, px, py, sigma)[good] for px, py in [(x, y), *others]]
    a = np.vstack([*cols, np.ones(int(good.sum()))]).T
    v = data[good].astype(np.float64)
    w = 1.0 / variance[good].astype(np.float64) if variance is not None else np.ones_like(v)
    sw = np.sqrt(w)
    try:
        sol, *_ = np.linalg.lstsq(a * sw[:, None], v * sw, rcond=None)
        cov = np.linalg.inv((a * w[:, None]).T @ a)
    except np.linalg.LinAlgError:
        return None
    chi2 = float(np.sum(w * (a @ sol - v) ** 2)) / max(len(v) - n_par, 1)
    err = float(np.sqrt(cov[0, 0] * max(chi2, 1.0)))
    scale = k * APERTURE_CORRECTION * conv
    return float(sol[0]) * scale, err * scale


def neighbours_at(stars: list[dict[str, Any]], model: dict[str, Any] | None, mjd: float,
                  ra: float, dec: float) -> list[tuple[float, float, float]]:
    """Gaia stars within BLEND_ARCSEC of (ra, dec) at mjd, other than the target itself:
    [(separation arcsec, ra, dec)]. Stars BLEND_IGNORE_DMAG fainter than a Gaia target are left out."""
    if not stars or model is None:
        return []
    g_target = model.get("g")
    out = []
    for s in stars:
        if model.get("id") and s.get("id") == model.get("id"):
            continue
        if g_target is not None and s.get("g") is not None and s["g"] > g_target + BLEND_IGNORE_DMAG:
            continue
        sr, sd = position_at(s, mjd)
        dx, dy = tangent_offset_arcsec(ra, dec, sr, sd)
        sep = float(np.hypot(dx, dy))
        if sep < BLEND_ARCSEC:
            out.append((sep, sr, sd))
    return out


def blend_candidates(stars: list[dict[str, Any]], model: dict[str, Any] | None, mjd_a: float,
                     mjd_b: float) -> list[dict[str, Any]]:
    """The Gaia stars that come within BLEND_ARCSEC (+ 3" for parallax) of the target at some time
    between mjd_a and mjd_b (straight tracks relative to the target), so neighbours_at, which
    moves each star to every exposure's time (0.4 ms a star), only sees a few."""
    if not stars or model is None:
        return []
    ta, tb = position_at(model, mjd_a, parallax=False), position_at(model, mjd_b, parallax=False)
    out = []
    for s in stars:
        if model.get("id") and s.get("id") == model.get("id"):
            continue
        a = tangent_offset_arcsec(*ta, *position_at(s, mjd_a, parallax=False))
        b = tangent_offset_arcsec(*tb, *position_at(s, mjd_b, parallax=False))
        p, q = np.array([float(a[0]), float(a[1])]), np.array([float(b[0]), float(b[1])])
        d = q - p
        t = 0.0 if not d.any() else float(np.clip(-(p @ d) / (d @ d), 0.0, 1.0))
        if np.hypot(*(p + t * d)) < BLEND_ARCSEC + 3.0:
            out.append(s)
    return out


def point_photometry(l2: Any, x: float, y: float, neighbours: list[tuple[float, float, float]],
                     target_in_gaia: bool) -> tuple[float, float] | None:
    """(flux, error) in uJy for one spectrum point, or None when it can't be measured honestly.
    See the module docstring for the three ways."""
    conv = ujy_per_mjysr(l2.wcs, x, y)
    det = int(l2.detector)
    ny, nx = l2.data.shape
    yy, xx = np.ogrid[:ny, :nx]
    core = (xx - x) ** 2 + (yy - y) ** 2 <= 1.5**2
    core_bad = bool((~np.isfinite(l2.data[core])).any() or bad_pixel_mask(l2.flags[core]).any())
    saturated = is_saturated_core(l2.flags, x, y)
    if saturated or core_bad:
        got = wing_photometry(l2.data, l2.flags, x, y, det, conv)
        if got is None:
            return None
        flux, err, stat = got
        if not saturated and not (flux > WING_MIN_SNR * stat):
            return None  # a faint source with a masked core (a cosmic ray, a bad pixel)
        return flux, err
    close = [n for n in neighbours if n[0] < BLEND_MIN_ARCSEC]
    if close and not target_in_gaia:
        return None  # e.g. WISE 0855 4" from a G = 16 star: the blend is mostly the star
    fit = [n for n in neighbours if n[0] >= BLEND_MIN_ARCSEC]
    if fit:  # (a Gaia target's companion closer than 5" stays in, as one blend: RW Aur A+B)
        others = [tuple(float(v) for v in sky_to_pixel(l2.wcs, n[1], n[2])) for n in fit]
        return blended_photometry(l2.data, l2.flags, l2.variance, x, y, others, det, conv)
    try:
        ph = aperture_photometry(l2.data, x, y, l2.wcs, flags=l2.flags, variance=l2.variance)
    except ValueError:  # sep: the sky annulus is fully masked
        got = wing_photometry(l2.data, l2.flags, x, y, det, conv)
        return None if got is None or not (got[0] > WING_MIN_SNR * got[2]) else got[:2]
    return float(ph.flux_ujy[0]), float(ph.err_ujy[0])


def spectrum_points(images: list[search.Image], l2s: list, model: dict[str, Any] | None,
                    ra: float, dec: float, positions: list[tuple[float, float]] | None = None,
                    stars: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """One point per exposure that can be measured (point_photometry). positions: the object's
    (ra, dec) per image, for a solar-system object. stars: Gaia stars around the position, for
    neighbours of the target (model) that share its aperture."""
    out = []
    target_in_gaia = bool(model and model.get("g") is not None)
    mjds = [l2.mjd_avg for l2 in l2s if l2 is not None]
    near = blend_candidates(stars or [], model, min(mjds), max(mjds)) if mjds and positions is None else []
    for i, (im, l2) in enumerate(zip(images, l2s)):
        if l2 is None:
            continue
        if positions is not None:
            r, d = positions[i]
        else:
            r, d = position_at(model, l2.mjd_avg) if model else (ra, dec)
        x, y = (float(v) for v in sky_to_pixel(l2.wcs, r, d))
        ny, nx = l2.data.shape
        if not (EDGE_PX <= x < nx - EDGE_PX and EDGE_PX <= y < ny - EDGE_PX):
            continue
        um = float(l2.wavelength(x, y)) if l2.wave is not None else float("nan")
        if not np.isfinite(um):
            continue
        nb = neighbours_at(near, model, l2.mjd_avg, r, d) if near else []
        try:
            got = point_photometry(l2, x, y, nb, target_in_gaia)
        except Exception as e:  # noqa: BLE001 - one bad cutout must not lose the spectrum
            log.warning("photometry failed on %s: %s", im.key, e)
            continue
        if got is None or not np.isfinite(got[0]):
            continue
        flux, err = got
        out.append({
            "um": round(um, 4), "flux_ujy": round(flux, 1),
            "err_ujy": round(err, 1) if np.isfinite(err) else None,
            "det": int(l2.detector), "mjd": round(float(l2.mjd_avg), 6), "visit": int(im.visit),
            "saturated": bool(is_saturated_core(l2.flags, x, y)),
        })
    out.sort(key=lambda p: (p["um"], p["mjd"]))
    return out


def pick_detector(spectrum: list[dict[str, Any]], images: list[search.Image],
                  allowed: tuple[int, ...] | None = None) -> tuple[int, int, int] | None:
    """(detector, first visit, last visit) for the measurement, or None with fewer than two visits.
    allowed: only these detectors (a built-in target seen only in some)."""
    best = None
    for det in DETECTOR_PREFERENCE:
        if allowed and det not in allowed:
            continue
        per_visit: dict[int, int] = {}
        for im in images:
            if im.det == det:
                per_visit[im.visit] = per_visit.get(im.visit, 0) + 1
        good = sorted(v for v, n in per_visit.items() if n >= MIN_VISIT_EXPOSURES)
        if len(good) < 2:
            continue
        pts = [p for p in spectrum if p["det"] == det]
        sat = float(np.mean([p["saturated"] for p in pts])) if pts else 1.0
        snr = [p["flux_ujy"] / p["err_ujy"] for p in pts if p["err_ujy"]]
        med_snr = float(np.median(snr)) if snr else 0.0
        score = (sat < 0.5, -round(sat, 1) if sat >= 0.5 else 0.0, med_snr if sat < 0.5 else 0.0)
        if best is None or score > best[0]:
            best = (score, det, good[0], good[-1])
    return None if best is None else (best[1], best[2], best[3])


def fill_saturated_core(image: np.ndarray, x: float, y: float, *, radius: float = 8.0,
                        max_move: float = 3.0) -> tuple[np.ndarray, tuple[float, float] | None]:
    """A stack whose star has a hole (saturated in every exposure) -> (the stack with the hole
    filled by a round profile fitted to the wings, the fitted centre).

    core.centroid needs a peak to start from, and a deeply saturated star (Barnard's Star: a
    5-7 px hole at native scale) has none. We fit a circular Moffat profile + sky to the good
    pixels within `radius` px of the predicted position (centre free within max_move px) and
    write the model into the hole only. Real pixels are never changed. Returns (image, None)
    when there is no hole near (x, y) or the fit fails.
    """
    ny, nx = image.shape
    yy, xx = np.mgrid[:ny, :nx]
    r2 = (xx - x) ** 2 + (yy - y) ** 2
    hole = ~np.isfinite(image) & (r2 <= (radius - 2.0) ** 2)
    if not (hole & (r2 <= 4.0)).any():
        return image, None
    use = np.isfinite(image) & (r2 <= radius**2)
    if use.sum() < 30:
        return image, None
    v = image[use].astype(np.float64)
    px, py = xx[use].astype(np.float64), yy[use].astype(np.float64)
    sky0 = float(np.percentile(v, 10))
    amp0 = max(float(v.max() - sky0), 1e-6) * 20.0

    def model(p: np.ndarray, qx: np.ndarray, qy: np.ndarray) -> np.ndarray:
        cx, cy, amp, alpha, beta, sky = p
        return amp * (1.0 + ((qx - cx) ** 2 + (qy - cy) ** 2) / alpha**2) ** (-beta) + sky

    def resid(p: np.ndarray) -> np.ndarray:
        # relative residuals: the bright ring next to the hole must not outvote the wings
        return (model(p, px, py) - v) / np.sqrt(np.abs(v - sky0) + 0.05 * amp0 / 20.0)

    p0 = np.array([x, y, amp0, 1.5, 2.0, sky0])
    lo = [x - max_move, y - max_move, 0.0, 0.3, 0.8, -np.inf]
    hi = [x + max_move, y + max_move, np.inf, 10.0, 8.0, np.inf]
    try:
        fit = least_squares(resid, p0, bounds=(lo, hi), x_scale="jac", loss="soft_l1")
    except (ValueError, np.linalg.LinAlgError):
        return image, None
    if not fit.success or not np.all(np.isfinite(fit.x)):
        return image, None
    out = image.copy()
    out[hole] = model(fit.x, xx[hole].astype(np.float64), yy[hole].astype(np.float64))
    return out, (float(fit.x[0]), float(fit.x[1]))


def measure(ra: float, dec: float, model: dict[str, Any] | None, images: list[search.Image],
            det: int, v1: int, v2: int, cache: Cache) -> dict[str, Any] | None:
    grid = stack.Grid(ra, dec, MEASURE_PIXEL, int(round(MEASURE_FOV * 60 / MEASURE_PIXEL)) | 1)
    stacks = blink.visit_stacks([im for im in images if im.det == det], grid,
                                cutouts.tier_for_field(MEASURE_FOV), cache, visits_wanted={v1, v2})
    if len(stacks) < 2:
        return None
    s1, s2 = stacks[0], stacks[-1]
    try:
        stars = gaia.cone(ra, dec, MEASURE_FOV / 2.0 * 2**0.5, cache=cache)
    except ApiError as e:
        log.warning("Gaia failed, no reference stars: %s", e)
        stars = []

    def col(k: str) -> np.ndarray:
        return np.array([np.nan if s.get(k) is None else s[k] for s in stars], dtype=float)

    ref = select_reference_stars(col("g"), col("pmra"), col("pmdec")) if stars else np.zeros(0, bool)
    kw: dict[str, Any] = {}
    if model and model.get("pmra") is not None:
        kw = dict(target_pmra=model["pmra"], target_pmdec=model["pmdec"], target_parallax=model.get("parallax"),
                  target_epoch_mjd=model.get("epoch_mjd", GAIA_EPOCH_MJD))
        t_ra, t_dec, search_r = model["ra"], model["dec"], 2.0
    else:
        t_ra, t_dec, search_r = ra, dec, 3.0
    w = grid.wcs
    img1, img2 = s1.image.astype(np.float64), s2.image.astype(np.float64)
    filled = []
    for i, (img, s) in enumerate(((img1, s1), (img2, s2))):
        gx, gy = (float(v) for v in w.wcs_world2pix(*(position_at(model, s.mjd_median) if model else (ra, dec)), 0))
        new, centre = fill_saturated_core(img, gx, gy)
        filled.append(centre is not None)
        if i == 0:
            img1 = new
        else:
            img2 = new
    try:
        res = measure_shift(
            img1, img2, w, w, s1.mjd_median, s2.mjd_median, t_ra, t_dec,
            ref_ra=col("ra")[ref] if stars else (), ref_dec=col("dec")[ref] if stars else (),
            ref_pmra=col("pmra")[ref] if stars else None, ref_pmdec=col("pmdec")[ref] if stars else None,
            search_r=search_r, method="fit", **kw)
    except ShiftError as e:
        log.info("no measurement: %s", e)
        return None
    out = res.to_measure()
    for k in ("shift_arcsec", "pa_deg", "dt_yr", "rate_arcsec_yr", "sigma_arcsec", "predicted_arcsec"):
        if out.get(k) is not None:
            out[k] = round(out[k], 4)
    return out, {"det": det, "visits": [v1, v2], "d_east_arcsec": res.d_east_arcsec, "d_north_arcsec": res.d_north_arcsec,
                 "frame_offset_arcsec": res.frame_offset_arcsec, "n_exposures": [s1.n_exposures, s2.n_exposures],
                 "mjd": [s1.mjd_median, s2.mjd_median], "core_filled": filled}


def build_object(ra: float, dec: float, *, cache: Cache | None = None, details: dict | None = None) -> dict[str, Any]:
    """GET /api/object. `details` (optional dict) receives what the contract has no field for:
    the detector and visits measured, the shift's east/north parts (used by scripts/precompute)."""
    cache = cache or Cache()
    found = search.search(ra, dec, cache=cache)
    images = found.images
    if not images:
        raise NotFoundError("no SPHEREx images at this position")
    mjd_a, mjd_b = min(im.mjd for im in images), max(im.mjd for im in images)
    try:
        stars = gaia.cone(ra, dec, GAIA_CONE_ARCMIN, cache=cache)
    except ApiError as e:
        log.warning("Gaia failed: %s", e)
        stars = []
    name, entry, model = identify(ra, dec, mjd_a, mjd_b, stars, _builtin_targets())

    l2s = cutouts.fetch_many(images, ra, dec, cutouts.tier_for_field(5), cache, variance=True)
    spectrum = spectrum_points(images, l2s, model, ra, dec, stars=stars)

    result: dict[str, Any] | None = None
    pick = pick_detector(spectrum, images, MEASURE_DETECTORS.get((model or {}).get("slug", "")))
    if pick is not None:
        got = measure(ra, dec, model, images, *pick, cache)
        if got is not None:
            result, extra = got
            if details is not None:
                details.update(extra)
    return {"name": name, "gaia": entry, "known_ucd": None, "spectrum": spectrum, "measure": result}
