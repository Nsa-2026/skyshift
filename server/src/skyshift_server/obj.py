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
- the r = 2 px aperture of core.aperture_photometry, when its core is clean (most points), with
  a clipped-median sky 4-7 px out (local_sky; not the annulus mean, which a bright neighbour's
  halo pulls up: Barnard's Star's old position read 5-12 sigma below zero);
- a saturated core: the flux from the PSF wings (wing_photometry), with a 50%
  systematic error. The aperture there leaves the core out and read 4-10x low (Barnard's Star);
  R Draconis's aperture is masked in every exposure, so it had no spectrum at all;
- a Gaia star 5-15" away at that time: a joint fit of both at their known positions
  (blended_photometry). WISE 0855-0714 passes a G = 16.3 star (11" -> 4" over the visits),
  which put ~1 mJy into D1-D3, where a 250 K brown dwarf is dark. For a built-in target Gaia
  doesn't see, with its own proper motion and parallax, the fit goes down to 3.5" (blend_min_arcsec):
  that brings back WISE 0855's visit 3, when the star is 3.9-4.3" away.
A point that can't be measured honestly (a masked but unsaturated core, a neighbour closer than
5" to a source Gaia doesn't see, 3.5" for such a built-in target) is left out rather than shown wrong.

Measure: core.measure_shift between the first and last visit stacks of one detector, on a
native-scale (6.15"/px) 15' grid, with quiet Gaia stars as the error floor. Its detection guard
gives a shift only for one source clearly seen in both stacks (PSF-matched S/N >= 5 in each, no
missing sky within 4 px, the two centroids within max(FWHM, 12"/yr x dt) + 3 sigma of each other,
or of the predicted motion); otherwise `measure` is null and `measure_reason` says why
(contracts/api.md v3.2: few_visits, not_detected, edge, different_sources, no_time, moved_away).
moved_away: someone pasted an old catalogue position of a fast star (Barnard's Star at its Gaia
2016.0 position is 100-111" from where SPHEREx sees it, so nothing is there); `measure_hint` then
gives the star's name and its position in the middle of SPHEREx's visits, from the Gaia cone and
the built-in targets already at hand (no extra request). Before the guard, a random spot with
nothing on it "moved" 30" (RA 125.5, Dec 19.4: two noise peaks). The detector is the one with the
best signal that is not saturated; if every detector saturates, the least saturated
(a built-in target seen only in some detectors is measured in those: its `dets` in targets.json,
else MEASURE_DETECTORS).
The two times are the MEDIAN exposure times of the two stacks: a per-pixel median of a slowly
moving star sits where the star was at the median time, not at mjd_mid (the middle of the first
and last exposure). Barnard's Star, visit 3: 24 exposures in 9 days and one 35 days in, so
mjd_mid is 14.6 days (0.41") after the median. dt_yr is therefore the difference of the median
times (contracts/api.md, accepted by P1). predicted_arcsec includes parallax; it is null without a
Gaia star (contracts/api.md), also for a built-in target with published astrometry (WISE 0855).

spectrum_reason: why the spectrum is empty, or null. "saturated" when exposures were left out for
a saturated core (OVERFLOW or NONLINEAR) and none remain, e.g. a star so bright that even its
wings are masked; "no_data" otherwise. R Draconis now has wing points, so it is null. We give no
lower limits.

Live requests (a Budget) do the same work in a fixed time: the spectrum from at most
SPECTRUM_LIVE_PER_DET exposures of each detector, downloaded most useful first (detectors in
turn, each visit's exposures spread over its time), until OBJECT_MEASURE_S before the deadline;
then the measurement from at most blink.LIVE_PER_VISIT exposures of each of the two visits. What
did not arrive is left out (never filled in); budget.partial says so and the app does not cache
it. The 0.12 deg cutouts are the same files GET /api/frames (months 5') uses, so the two share
work through the cache. scripts/precompute passes no budget and gets every exposure.
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeout
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
from skyshift_core.astrometry import PSF_FWHM_ARCSEC
from skyshift_core.flags import SUR_ERROR, TRANSIENT
from skyshift_core.detect import APERTURE_R_PX
from skyshift_core.photometry import APERTURE_CORRECTION

from . import blink, cutouts, gaia, resolve, search, stack
from .budget import Budget, BudgetError
from .cache import Cache
from .errors import ApiError, NotFoundError

log = logging.getLogger(__name__)

MATCH_ARCSEC = 6.0
GAIA_CONE_ARCMIN = 3.5  # reaches a 19"/yr star 11 years from its 2016.0 position
EDGE_PX = 8  # the aperture's sky annulus reaches 7 px
# Sky for the r = 2 px aperture (local_sky): clipped median of the good pixels 4-7 px out
SKY_ANNULUS_PX = (4.0, 7.0)
SKY_CLIP_SIGMA = 3.0
SKY_MIN_PIXELS = 10
MEASURE_FOV = 15
MEASURE_PIXEL = stack.NATIVE_ARCSEC
MIN_VISIT_EXPOSURES = 3
DETECTOR_PREFERENCE = (2, 1, 3, 4, 5, 6)  # on ties
# Built-in targets seen only in some detectors. WISE 0855-0714 (a 250 K brown dwarf) is invisible
# short of 3 um; the aperture at its position in D1-D3 holds a blend of background sources, which
# win on signal and measure 0.7" instead of its 8"/yr. In D5 it measures 7.05" +- 0.90" (Gaia-free
# prediction 8.06"), in D6 8.57" +- 3.3". (targets.json says "Expected only in D5-D6" in a note.)
MEASURE_DETECTORS = {"wise0855": (5, 6)}
SPECTRUM_LIVE_PER_DET = 12  # live: spectrum points asked per detector (precompute: every exposure)
OBJECT_MEASURE_S = 16.0  # live: seconds of the budget kept for the measurement after the spectrum
OBJECT_RESERVE_S = 3.0  # live: seconds kept after the measurement's downloads
MAX_REF_LIVE = 150  # live: reference stars fitted, the nearest to the position (precompute: all)
# contracts/api.md `measure_reason` for core's ShiftError codes ("not_found": no centroid at all)
SHIFT_REASONS = {"not_found": "not_detected", "not_detected": "not_detected", "edge": "edge",
                 "different_sources": "different_sources"}
# `moved_away` looks for catalogue positions back to J1990.0: Hipparcos (1991.25), 2MASS (1997-2001),
# SIMBAD's J2000.0 positions, Gaia DR2 (2015.5) and DR3 (2016.0)
OLDEST_CATALOGUE_MJD = 47892.0
MOVED_ARCSEC = PSF_FWHM_ARCSEC  # `moved_away`: the star stays further than this through the visits
_measure_pool = ThreadPoolExecutor(8, thread_name_prefix="measure")


def spectrum_images(images: list[search.Image], per_det: int | None) -> list[search.Image]:
    """Download order for the spectrum: the detectors in turn (D1..D6), and within a detector its
    visits in turn, each visit's exposures spread over its time (blink.by_usefulness), at most
    per_det of each detector (None: all). Whatever arrives by a deadline covers every detector."""
    queues = []
    for det in range(1, 7):
        q = blink.by_usefulness([im for im in images if im.det == det])
        queues.append(q[:per_det] if per_det else q)
    out: list[search.Image] = []
    for i in range(max((len(q) for q in queues), default=0)):
        out += [q[i] for q in queues if i < len(q)]
    return out


def _result(f: Future, budget: Budget | None, reserve: float, what: str) -> list[dict[str, Any]]:
    """A Gaia cone's result, waited for until `reserve` s before the deadline; [] if it failed or
    is late (budget marked partial)."""
    try:
        return f.result(timeout=budget.until(reserve) if budget is not None else None)
    except FutureTimeout:
        if budget is not None:
            budget.cut(f"{what} not in time")
    except ApiError as e:
        log.warning("%s failed: %s", what, e)
        if budget is not None:
            budget.cut(f"{what} failed")
    return []

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

# --- Photometry next to a Gaia star: a joint fit --------------------------------------------------
BLEND_ARCSEC = 15.0  # a Gaia star closer than this puts light into the r = 2 px (12.3") aperture
BLEND_MIN_ARCSEC = 5.0  # closer than this, two sources can't be told apart at 6.15" pixels
# ... unless both positions are known to well under a pixel: a built-in target Gaia doesn't see,
# with its own proper motion and parallax (blend_min_arcsec). WISE 0855 in visit 3, its Gaia
# neighbour 3.9-4.3" away: the fit agrees with visits 1-2, errors grow 1.3-1.4x against 8" (#143).
BLEND_MIN_ARCSEC_BUILTIN = 3.5
BLEND_IGNORE_DMAG = 4.0  # a neighbour this much fainter in G than a Gaia target is ignored
BLEND_FIT_R_PX = 3.5
# Pixel-integrated Gaussian per detector (sigma px, aperture flux / Gaussian amplitude), fitted to
# 120-450 isolated stars at S/N > 50 per detector in the targets' cutouts (14 Nov).
BLEND_PSF = {1: (0.38, 1.104), 2: (0.38, 1.086), 3: (0.38, 1.114), 4: (0.46, 1.111), 5: (0.46, 1.089), 6: (0.50, 1.098)}


def measure_detectors(model: dict[str, Any] | None) -> tuple[int, ...] | None:
    """Detectors a built-in target is measured in: its `dets` in targets.json, else
    MEASURE_DETECTORS by slug, else None (any detector)."""
    m = model or {}
    return m.get("dets") or MEASURE_DETECTORS.get(m.get("slug") or "")


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
            "dets": tuple(int(d) for d in t["dets"]) if t.get("dets") else None,
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


def moved_away(ra: float, dec: float, mjd_a: float, mjd_b: float, stars: list[dict[str, Any]],
               builtins: list[dict[str, Any]], skip: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """contracts/api.md `measure_hint`: the fast star catalogued at (ra, dec) that SPHEREx sees
    elsewhere, as {name, ra, dec, mjd} at the middle of mjd_a..mjd_b, or None.

    A star qualifies when its track from J1990.0 to its catalogue epoch (Gaia: 2016.0) passes within
    MATCH_ARCSEC of the position, and its track from mjd_a to mjd_b stays more than MOVED_ARCSEC from
    it. Slow stars never do. The nearest catalogue track wins; a built-in over the same Gaia star.
    skip: the star identify() follows at this position (its measure is about it already)."""
    cosd = np.cos(np.radians(dec))
    best: tuple[float, dict[str, Any]] | None = None
    for m in [*builtins, *stars]:
        if m.get("pmra") is None or m.get("pmdec") is None:
            continue
        if skip is not None and (m is skip or (skip.get("id") and m.get("id") == skip.get("id"))):
            continue
        epoch = float(m.get("epoch_mjd", GAIA_EPOCH_MJD))
        # cheap cut first: the catalogue position can be no further than 6" + the motion since J1990
        sep = 3600.0 * float(np.hypot(((m["ra"] - ra + 180.0) % 360.0 - 180.0) * cosd, m["dec"] - dec))
        span_yr = max(epoch - OLDEST_CATALOGUE_MJD, 0.0) / 365.25
        if sep > MATCH_ARCSEC + 1.0 + float(np.hypot(m["pmra"], m["pmdec"])) / 1000.0 * span_yr:
            continue
        d = track_distance_arcsec(m, ra, dec, min(OLDEST_CATALOGUE_MJD, epoch), epoch)
        if d > MATCH_ARCSEC or track_distance_arcsec(m, ra, dec, mjd_a, mjd_b) <= MOVED_ARCSEC:
            continue
        if best is None or d < best[0] - 0.01:
            best = (d, m)
    if best is None:
        return None
    m = best[1]
    name = m.get("name") or (f"Gaia DR3 {m['id']}" if m.get("id") else None)
    mid = (mjd_a + mjd_b) / 2.0
    r, d = position_at(m, mid)
    return {"name": name, "ra": round(r, 6), "dec": round(d, 6), "mjd": round(mid, 2)}


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


def blend_min_arcsec(model: dict[str, Any] | None) -> float:
    """Closest Gaia neighbour the joint fit is trusted at, for this target.

    BLEND_MIN_ARCSEC_BUILTIN for a built-in target (a `slug` from targets.json) that Gaia doesn't
    see (no Gaia id) and has its own proper motion and parallax: its position at each exposure
    comes from its own astrometry (WISE 0855: 8.1"/yr, parallax 449 mas, from the literature), as
    the neighbour's comes from Gaia, so a fit with both positions fixed can split them at 0.6 px.
    BLEND_MIN_ARCSEC for everything else: a typed position
    or a Gaia-less source found by chance can be off by an arcsec, and with a fixed position that
    far off the fit moves flux between the two sources. (A Gaia target keeps its closer companions
    inside its own aperture, as one blend, whatever this says: point_photometry.)"""
    m = model or {}
    if (m.get("slug") and not m.get("id")
            and all(m.get(k) is not None for k in ("pmra", "pmdec", "parallax"))):
        return BLEND_MIN_ARCSEC_BUILTIN
    return BLEND_MIN_ARCSEC


def local_sky(data: np.ndarray, flags: np.ndarray, x: float, y: float) -> tuple[float, float] | None:
    """(sky, its 1 sigma error) in MJy/sr for the r = 2 px aperture at (x, y): the median of the good
    pixels SKY_ANNULUS_PX out, after SKY_CLIP_SIGMA clipping around the median (scale from the MAD),
    and 1.2533 x scatter / sqrt(n) for the error. None with fewer than SKY_MIN_PIXELS good pixels.

    Not the annulus mean (sep's bkgann, core.aperture_photometry's default): a bright star's halo
    or a neighbour in part of the annulus pulls the mean up and the aperture then reads below zero.
    Barnard's Star's 2016.0 position (`moved_away`): the star is 16-18 px away and 58 of 61 live
    points were 5-12 sigma negative (-200 to -1000 uJy); with this sky the median point is +47 uJy
    (+0.3 sigma). On blank spots of three target fields (12" from any Gaia star) the mean put the
    median point at -1.2 to -11.5 sigma; this sky at -0.1 to +0.03 sigma (14 Nov)."""
    ny, nx = data.shape
    yy, xx = np.ogrid[:ny, :nx]
    r2 = (xx - x) ** 2 + (yy - y) ** 2
    ann = (r2 >= SKY_ANNULUS_PX[0] ** 2) & (r2 <= SKY_ANNULUS_PX[1] ** 2) & np.isfinite(data) & ~bad_pixel_mask(flags)
    v = data[ann].astype(np.float64)
    if v.size < SKY_MIN_PIXELS:
        return None
    keep = v
    for _ in range(10):
        med = float(np.median(keep))
        mad = 1.4826 * float(np.median(np.abs(keep - med)))
        new = v[np.abs(v - med) <= SKY_CLIP_SIGMA * mad] if mad > 0 else keep
        if new.size == keep.size or new.size < SKY_MIN_PIXELS:
            break
        keep = new
    med = float(np.median(keep))
    scatter = 1.4826 * float(np.median(np.abs(keep - med)))
    return med, 1.2533 * scatter / np.sqrt(keep.size)


def point_photometry(l2: Any, x: float, y: float, neighbours: list[tuple[float, float, float]],
                     target_in_gaia: bool, min_sep: float = BLEND_MIN_ARCSEC) -> tuple[float, float] | None:
    """(flux, error) in uJy for one spectrum point, or None when it can't be measured honestly.
    See the module docstring for the three ways. min_sep: neighbours at least this far are fitted
    jointly (blend_min_arcsec); a closer one next to a target Gaia doesn't see drops the point."""
    conv = ujy_per_mjysr(l2.wcs, x, y)
    det = int(l2.detector)
    ny, nx = l2.data.shape
    yy, xx = np.ogrid[:ny, :nx]
    core = (xx - x) ** 2 + (yy - y) ** 2 <= 1.5**2
    core_bad = bool((~np.isfinite(l2.data[core])).any() or bad_pixel_mask(l2.flags[core]).any())
    saturated = is_saturated_core(l2.flags, x, y)
    if saturated:
        got = wing_photometry(l2.data, l2.flags, x, y, det, conv)
        return None if got is None else got[:2]
    if core_bad:
        # A masked core that isn't saturated (a cosmic ray, a bad pixel): the aperture would read
        # low, and a wing estimate's 50% error would draw a detected source as an upper limit.
        return None
    close = [n for n in neighbours if n[0] < min_sep]
    if close and not target_in_gaia:
        return None  # e.g. a typed position 4" from a G = 16 star: the blend is mostly the star
    fit = [n for n in neighbours if n[0] >= min_sep]
    if fit:  # (a Gaia target's companion closer than 5" stays in, as one blend: RW Aur A+B)
        others = [tuple(float(v) for v in sky_to_pixel(l2.wcs, n[1], n[2])) for n in fit]
        return blended_photometry(l2.data, l2.flags, l2.variance, x, y, others, det, conv)
    sky = local_sky(l2.data, l2.flags, x, y)
    if sky is None:  # the sky annulus is (almost) fully masked
        return None
    ph = aperture_photometry(l2.data - np.float32(sky[0]), x, y, l2.wcs, flags=l2.flags, variance=l2.variance,
                             bkg_annulus=None)
    sky_err = np.pi * APERTURE_R_PX**2 * sky[1] * conv * APERTURE_CORRECTION  # the sky's error over the aperture
    return float(ph.flux_ujy[0]), float(np.hypot(ph.err_ujy[0], sky_err))


def spectrum_points(images: list[search.Image], l2s: list, model: dict[str, Any] | None,
                    ra: float, dec: float, positions: list[tuple[float, float]] | None = None,
                    stars: list[dict[str, Any]] | None = None,
                    dropped: Counter | None = None) -> list[dict[str, Any]]:
    """One point per exposure that can be measured (point_photometry). positions: the object's
    (ra, dec) per image, for a solar-system object. stars: Gaia stars around the position, for
    neighbours of the target (model) that share its aperture. dropped: counts the exposures that
    gave no point, as "saturated" (OVERFLOW or NONLINEAR in the core) or "other"."""
    out = []
    target_in_gaia = bool(model and model.get("g") is not None)
    min_sep = blend_min_arcsec(model)
    mjds =[l2.mjd_avg for l2 in l2s if l2 is not None]
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
        sat = bool(is_saturated_core(l2.flags, x, y))
        um = float(l2.wavelength(x, y)) if l2.wave is not None else float("nan")
        if not np.isfinite(um):
            if dropped is not None:
                dropped["other"] += 1
            continue
        nb = neighbours_at(near, model, l2.mjd_avg, r, d) if near else []
        try:
            got = point_photometry(l2, x, y, nb, target_in_gaia, min_sep)
        except Exception as e:  # noqa: BLE001 - one bad cutout must not lose the spectrum
            log.warning("photometry failed on %s: %s", im.key, e)
            if dropped is not None:
                dropped["saturated" if sat else "other"] += 1
            continue
        if got is None or not np.isfinite(got[0]):
            if dropped is not None:
                dropped["saturated" if sat else "other"] += 1
            continue
        flux, err = got
        out.append({
            "um": round(um, 4), "flux_ujy": round(flux, 1),
            "err_ujy": round(err, 1) if np.isfinite(err) else None,
            "det": int(l2.detector), "mjd": round(float(l2.mjd_avg), 6), "visit": int(im.visit),
            "saturated": sat,
        })
    out.sort(key=lambda p: (p["um"], p["mjd"]))
    return out


def spectrum_reason(spectrum: list[dict[str, Any]], dropped: Counter) -> str | None:
    """Why the spectrum is empty (contracts/api.md `spectrum_reason`): "saturated" when exposures
    were dropped for a saturated core, "no_data" otherwise; None when there are points."""
    if spectrum:
        return None
    return "saturated" if dropped.get("saturated") else "no_data"


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
            det: int, v1: int, v2: int, cache: Cache, *, budget: Budget | None = None,
            per_visit: int | None = None, ref_stars: Future | None = None,
            max_ref: int | None = None, reasons: list[str] | None = None) -> dict[str, Any] | None:
    """per_visit: at most this many exposures of each visit (blink.pick_months_images; None: all).
    ref_stars: the 15' Gaia cone, already asked (else asked here).
    max_ref: at most this many reference stars, the nearest to the position (None: all).
    reasons: receives the guard's code (core ShiftError.reason) when the shift is refused."""
    grid = stack.Grid(ra, dec, MEASURE_PIXEL, int(round(MEASURE_FOV * 60 / MEASURE_PIXEL)) | 1)
    mine = blink.pick_months_images([im for im in images if im.det == det and im.visit in (v1, v2)], per_visit)
    stacks = blink.visit_stacks(mine, grid, cutouts.tier_for_field(MEASURE_FOV), cache,
                                visits_wanted={v1, v2}, budget=budget, reserve=OBJECT_RESERVE_S)
    if len(stacks) < 2:
        return None
    s1, s2 = stacks[0], stacks[-1]
    if budget is not None and budget.partial and min(s1.n_exposures, s2.n_exposures) < MIN_VISIT_EXPOSURES:
        budget.cut("too few exposures in time for the measurement")  # a thin stack keeps asteroids
        return None
    if ref_stars is None:
        ref_stars = blink._side.submit(gaia.cone, ra, dec, MEASURE_FOV / 2.0 * 2**0.5, cache=cache)
    stars = _result(ref_stars, budget, 1.0, "Gaia reference stars")

    def col(k: str) -> np.ndarray:
        return np.array([np.nan if s.get(k) is None else s[k] for s in stars], dtype=float)

    ref = select_reference_stars(col("g"), col("pmra"), col("pmdec")) if stars else np.zeros(0, bool)
    if max_ref and int(ref.sum()) > max_ref:
        # core fits every reference star in both stacks (a crowded field near the Galactic centre
        # has 1159: 45 s on Cloud Run); the nearest max_ref give the same error floor
        idx = np.flatnonzero(ref)
        d_ra = (col("ra")[idx] - ra + 180.0) % 360.0 - 180.0
        d = np.hypot(d_ra * np.cos(np.radians(dec)), col("dec")[idx] - dec)
        ref = np.zeros_like(ref)
        ref[idx[np.argsort(d)[:max_ref]]] = True
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
    except ShiftError as e:  # the detection guard (core.measure_shift): no honest shift here
        log.info("no measurement (%s): %s", e.reason, e)
        if reasons is not None:
            reasons.append(e.reason)
        return None
    out = res.to_measure()
    if not (model and model.get("id")):
        # contracts/api.md: Gaia's predicted shift, or null without Gaia. A built-in target Gaia
        # doesn't see (WISE 0855: published astrometry) still guides the search and the guard above,
        # but that prediction is not Gaia's.
        out["predicted_arcsec"] = None
    for k in ("shift_arcsec", "pa_deg", "dt_yr", "rate_arcsec_yr", "sigma_arcsec", "predicted_arcsec"):
        if out.get(k) is not None:
            out[k] = round(out[k], 4)
    # contracts/api.md v3.9: what the shift was measured on (the player may show another detector)
    out["source"] = {"det": det, "visits": [s1.visit, s2.visit],
                     "mjd": [round(s1.mjd_median, 6), round(s2.mjd_median, 6)],
                     "n_exposures": [s1.n_exposures, s2.n_exposures],
                     "fov": MEASURE_FOV, "pixel_arcsec": MEASURE_PIXEL, "core_filled": filled}
    return out, {"det": det, "visits": [v1, v2], "d_east_arcsec": res.d_east_arcsec, "d_north_arcsec": res.d_north_arcsec,
                 "frame_offset_arcsec": res.frame_offset_arcsec, "n_exposures": [s1.n_exposures, s2.n_exposures],
                 "mjd": [s1.mjd_median, s2.mjd_median], "core_filled": filled,
                 "target_snr": [round(float(v), 1) for v in res.target_snr],
                 "match_r_arcsec": round(float(res.match_r_arcsec), 2)}


def build_object(ra: float, dec: float, *, cache: Cache | None = None, details: dict | None = None,
                 budget: Budget | None = None) -> dict[str, Any]:
    """GET /api/object. `details` (optional dict) receives what the contract has no field for:
    the detector and visits measured, the shift's east/north parts (used by scripts/precompute),
    and with no measure, core's own reason code (details["shift_error"]).
    budget: a live request's time budget (None: every exposure, no limit). BudgetError (503) if
    neither a spectrum point nor a measurement arrived in time."""
    cache = cache or Cache()
    live = budget is not None and budget.limited
    t = time.monotonic()
    found = search.search(ra, dec, cache=cache)
    if budget is not None:
        budget.add("search", time.monotonic() - t)
    images = found.images
    if not images:
        raise NotFoundError("no SPHEREx images at this position")
    mjd_a, mjd_b = min(im.mjd for im in images), max(im.mjd for im in images)
    # both Gaia cones run while the cutouts download
    near_f = blink._side.submit(blink._timed, budget, "gaia", gaia.cone, ra, dec, GAIA_CONE_ARCMIN, cache=cache)
    ref_f = blink._side.submit(blink._timed, budget, "gaia_ref", gaia.cone, ra, dec,
                               MEASURE_FOV / 2.0 * 2**0.5, cache=cache)

    spec_images = spectrum_images(images, SPECTRUM_LIVE_PER_DET if live else None)
    l2s = cutouts.fetch_many(spec_images, ra, dec, cutouts.tier_for_field(5), cache, variance=True,
                             budget=budget, reserve=OBJECT_MEASURE_S)
    stars = _result(near_f, budget, OBJECT_MEASURE_S, "Gaia")
    builtins = _builtin_targets()
    name, entry, model = identify(ra, dec, mjd_a, mjd_b, stars, builtins)
    dropped: Counter = Counter()
    spectrum = spectrum_points(spec_images, l2s, model, ra, dec, stars=stars, dropped=dropped)

    result: dict[str, Any] | None = None
    reason: str | None = None  # contracts/api.md `measure_reason`
    pick = pick_detector(spectrum, images, measure_detectors(model))
    if pick is not None and not (budget is not None and budget.expired(OBJECT_RESERVE_S)):
        t = time.monotonic()
        reasons: list[str] = []
        kw = dict(budget=budget, per_visit=blink.LIVE_PER_VISIT if live else None, ref_stars=ref_f,
                  max_ref=MAX_REF_LIVE if live else None, reasons=reasons)
        if live:  # the fits are CPU work the budget cannot stop half-way: wait only until the deadline
            fut = _measure_pool.submit(measure, ra, dec, model, images, *pick, cache, **kw)
            try:
                got = fut.result(timeout=budget.until(OBJECT_RESERVE_S))
            except FutureTimeout:
                got = None
                budget.cut("measurement not in time")
        else:
            got = measure(ra, dec, model, images, *pick, cache, **kw)
        if budget is not None:
            budget.add("measure", time.monotonic() - t)
        if got is not None:
            result, extra = got
            if details is not None:
                details.update(extra)
        elif reasons:  # the detection guard refused the shift
            reason = SHIFT_REASONS.get(reasons[0], "not_detected")
            if details is not None:
                details["shift_error"] = reasons[0]
        elif budget is not None and budget.partial:
            reason = "no_time"
        else:  # fewer than two visit stacks could be made from the cutouts
            reason = "few_visits"
    elif pick is not None and budget is not None:
        budget.cut("no time left for the measurement")
        reason = "no_time"
    else:
        reason = "few_visits"
    hint = None
    if result is None:
        hint = moved_away(ra, dec, mjd_a, mjd_b, stars, builtins, skip=model)
        if hint is not None:
            reason = "moved_away"
    if details is not None and reason is not None:
        details["measure_reason"] = reason
    if budget is not None and budget.partial and not spectrum and result is None:
        raise BudgetError("the SPHEREx archive did not deliver in time "
                          f"({budget.elapsed():.0f} s); try again in a minute: what arrived is kept")
    if found.note and budget is not None:  # photos not from IRSA (search_fallback.py): never cached
        budget.cut(found.note)
    return {"name": name, "gaia": entry, "known_ucd": None, "spectrum": spectrum,
            "spectrum_reason": spectrum_reason(spectrum, dropped), "measure": result,
            "measure_reason": reason, "measure_hint": hint}
