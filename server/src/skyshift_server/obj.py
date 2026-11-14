"""GET /api/object: what is at a position, its spectrum and its motion (contracts/api.md).

Who: the nearest of (a) the built-in targets and (b) Gaia DR3 stars whose path over the SPHEREx
years passes within 6" of the position. Fast stars are far from their 2016.0 Gaia position
(Barnard's Star: 112"), so Gaia is asked for a 3.5' cone and every star is moved along its own
track before the match.

Spectrum: aperture photometry (core) on every cutout of every detector, at the object's position
at that exposure's time (proper motion + parallax when we have them, else the position asked).
Each point: wavelength of that pixel from WCS-WAVE, flux and error in uJy, detector, MJD-AVG,
visit, saturated (OVERFLOW or NONLINEAR in the core).

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
from scipy.optimize import least_squares
from skyshift_core import (
    GAIA_EPOCH_MJD,
    ShiftError,
    aperture_photometry,
    is_saturated_core,
    measure_shift,
    propagate,
    select_reference_stars,
    sky_to_pixel,
    tangent_offset_arcsec,
)

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


def spectrum_points(images: list[search.Image], l2s: list, model: dict[str, Any] | None,
                    ra: float, dec: float, positions: list[tuple[float, float]] | None = None) -> list[dict[str, Any]]:
    """One point per exposure where the aperture has good pixels. positions: the object's
    (ra, dec) per image, for a solar-system object. A deeply saturated star whose whole aperture
    is masked gives no point for that exposure."""
    out = []
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
        try:
            ph = aperture_photometry(l2.data, x, y, l2.wcs, flags=l2.flags, variance=l2.variance)
        except Exception as e:  # noqa: BLE001 - one bad cutout must not lose the spectrum
            log.warning("photometry failed on %s: %s", im.key, e)
            continue
        flux, err = float(ph.flux_ujy[0]), float(ph.err_ujy[0])
        um = float(l2.wavelength(x, y)) if l2.wave is not None else float("nan")
        if not (np.isfinite(flux) and np.isfinite(um)):
            continue
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
    out["source"] = {
        "det": det, "visits": [s1.visit, s2.visit],
        "mjd": [round(s1.mjd_median, 6), round(s2.mjd_median, 6)],
        "n_exposures": [s1.n_exposures, s2.n_exposures],
        "fov": MEASURE_FOV, "pixel_arcsec": grid.pixel_arcsec, "core_filled": filled,
    }
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
    spectrum = spectrum_points(images, l2s, model, ra, dec)

    result: dict[str, Any] | None = None
    pick = pick_detector(spectrum, images, MEASURE_DETECTORS.get((model or {}).get("slug", "")))
    if pick is not None:
        got = measure(ra, dec, model, images, *pick, cache)
        if got is not None:
            result, extra = got
            if details is not None:
                details.update(extra)
    return {"name": name, "gaia": entry, "known_ucd": None, "spectrum": spectrum, "measure": result}
