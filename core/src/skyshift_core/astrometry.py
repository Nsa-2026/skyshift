"""Pixel <-> sky, Gaia proper-motion propagation, and the V2/V3 shift measurement.

Conventions (contracts/api.md): ICRS degrees; shifts and errors in arcsec; Gaia pmra already
multiplied by cos(dec), pmra/pmdec in mas/yr, parallax in mas; time MJD (UTC);
position angle east of north.

Gaia propagation is linear motion on the tangent plane from J2016.0 (exact gnomonic
deprojection, so it is fine near the poles). Parallax is ignored by default. That is up to
+-parallax around the mean position: 0.55" for Barnard's Star, i.e. up to ~1" between visits six
months apart (~10% of its 5.2" shift). Pass `parallax` to `propagate()` to include it (Earth's
barycentric position from astropy's built-in ephemeris; SPHEREx's low orbit adds < 1e-4 AU).
Radial velocity and light-time are ignored (perspective acceleration of Barnard's Star is
~1.3 mas/yr^2: negligible here).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from astropy.wcs import WCS
from numpy.typing import ArrayLike, NDArray

from .detect import centroid, detection_snr, no_data_near

GAIA_EPOCH_JYEAR = 2016.0
GAIA_EPOCH_MJD = 57388.5  # J2016.0 = JD 2457389.0
MAS_TO_RAD = np.pi / (180.0 * 3600.0 * 1000.0)
ARCSEC_PER_RAD = 180.0 * 3600.0 / np.pi
DAYS_PER_JYEAR = 365.25

FloatArr = NDArray[np.float64]

# The detection guard of measure_shift: a shift is reported only for one source clearly seen in
# both stacks. A random spot used to "move" 30" (RA 125.5, Dec 19.4, D6): with nothing there,
# the centroid took the brightest noise pixel within search_r in each stack, two different ones.
PSF_FWHM_ARCSEC = 7.53
MIN_SNR = 5.0  # PSF-matched S/N of the target in each stack (detect.detection_snr)
EDGE_R_PX = 4.0  # no missing sky this close to either centroid: the centroid fit's 9 x 9 stamp
MAX_PM_ARCSEC_YR = 12.0  # contracts/api.md `implausible_pm`: no star moves faster (Barnard's 10.4)


# --- pixel <-> sky ---------------------------------------------------------------------------

def pixel_to_sky(wcs: WCS, x: ArrayLike, y: ArrayLike) -> tuple[FloatArr, FloatArr]:
    """0-based pixel (x, y) -> (ra, dec) degrees, through the full WCS incl. SIP."""
    ra, dec = wcs.celestial.all_pix2world(np.asarray(x, float), np.asarray(y, float), 0)
    return np.asarray(ra, dtype=np.float64), np.asarray(dec, dtype=np.float64)


def sky_to_pixel(wcs: WCS, ra: ArrayLike, dec: ArrayLike) -> tuple[FloatArr, FloatArr]:
    """(ra, dec) degrees -> 0-based pixel (x, y), through the full WCS incl. SIP."""
    x, y = wcs.celestial.all_world2pix(np.asarray(ra, float), np.asarray(dec, float), 0)
    return np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)


# --- sky geometry ----------------------------------------------------------------------------

def tangent_offset_arcsec(ra0: ArrayLike, dec0: ArrayLike, ra: ArrayLike, dec: ArrayLike) -> tuple[FloatArr, FloatArr]:
    """Gnomonic offsets (east, north) in arcsec of (ra, dec) from (ra0, dec0)."""
    a0, d0 = np.deg2rad(np.asarray(ra0, float)), np.deg2rad(np.asarray(dec0, float))
    a, d = np.deg2rad(np.asarray(ra, float)), np.deg2rad(np.asarray(dec, float))
    cosc = np.sin(d0) * np.sin(d) + np.cos(d0) * np.cos(d) * np.cos(a - a0)
    xi = np.cos(d) * np.sin(a - a0) / cosc
    eta = (np.cos(d0) * np.sin(d) - np.sin(d0) * np.cos(d) * np.cos(a - a0)) / cosc
    return xi * ARCSEC_PER_RAD, eta * ARCSEC_PER_RAD


def tangent_deproject(ra0: ArrayLike, dec0: ArrayLike, east_arcsec: ArrayLike, north_arcsec: ArrayLike) -> tuple[FloatArr, FloatArr]:
    """Inverse of tangent_offset_arcsec: move (ra0, dec0) by (east, north) arcsec on the tangent plane."""
    a0, d0 = np.deg2rad(np.asarray(ra0, float)), np.deg2rad(np.asarray(dec0, float))
    xi = np.asarray(east_arcsec, float) / ARCSEC_PER_RAD
    eta = np.asarray(north_arcsec, float) / ARCSEC_PER_RAD
    den = np.cos(d0) - eta * np.sin(d0)
    a = a0 + np.arctan2(xi, den)
    d = np.arctan2(np.sin(d0) + eta * np.cos(d0), np.hypot(xi, den))
    return np.rad2deg(a) % 360.0, np.rad2deg(d)


def position_angle_deg(d_east: ArrayLike, d_north: ArrayLike) -> FloatArr:
    """Position angle east of north, 0-360 degrees."""
    return np.rad2deg(np.arctan2(np.asarray(d_east, float), np.asarray(d_north, float))) % 360.0


def mjd_to_jyear(mjd: ArrayLike) -> FloatArr:
    """MJD -> Julian epoch (J2000.0 = MJD 51544.5). UTC/TT/TCB differences (< 2 min) are ignored."""
    return 2000.0 + (np.asarray(mjd, float) - 51544.5) / DAYS_PER_JYEAR


# --- Gaia propagation ------------------------------------------------------------------------

def parallax_offset_arcsec(ra: ArrayLike, dec: ArrayLike, parallax_mas: ArrayLike, mjd: ArrayLike) -> tuple[FloatArr, FloatArr]:
    """Parallactic displacement (east, north) in arcsec seen from the Earth at `mjd`."""
    from astropy.coordinates import get_body_barycentric
    from astropy.time import Time

    pos = get_body_barycentric("earth", Time(np.asarray(mjd, float), format="mjd", scale="utc"))
    X, Y, Z = (np.asarray(c.to_value("AU")) for c in (pos.x, pos.y, pos.z))
    a, d = np.deg2rad(np.asarray(ra, float)), np.deg2rad(np.asarray(dec, float))
    p = np.asarray(parallax_mas, float) / 1000.0
    de = p * (X * np.sin(a) - Y * np.cos(a))
    dn = p * (X * np.cos(a) * np.sin(d) + Y * np.sin(a) * np.sin(d) - Z * np.cos(d))
    return de, dn


def propagate(
    ra: ArrayLike,
    dec: ArrayLike,
    pmra: ArrayLike,
    pmdec: ArrayLike,
    mjd: ArrayLike,
    *,
    epoch_mjd: float = GAIA_EPOCH_MJD,
    parallax: ArrayLike | None = None,
) -> tuple[FloatArr, FloatArr]:
    """Move Gaia positions from `epoch_mjd` (J2016.0) to `mjd`. pmra includes cos(dec), mas/yr.

    Linear motion on the tangent plane. NaN proper motions are treated as 0.
    parallax (mas): if given, adds the parallactic displacement at `mjd` (default: ignored).
    """
    dt = (np.asarray(mjd, float) - epoch_mjd) / DAYS_PER_JYEAR
    pmra_ = np.nan_to_num(np.asarray(pmra, float))
    pmdec_ = np.nan_to_num(np.asarray(pmdec, float))
    de = pmra_ * dt / 1000.0
    dn = pmdec_ * dt / 1000.0
    if parallax is not None:
        pe, pn = parallax_offset_arcsec(ra, dec, np.nan_to_num(np.asarray(parallax, float)), mjd)
        de, dn = de + pe, dn + pn
    return tangent_deproject(ra, dec, de, dn)


def select_reference_stars(
    g: ArrayLike, pmra: ArrayLike, pmdec: ArrayLike, *, g_min: float = 13.0, g_max: float = 17.0, pm_max: float = 20.0
) -> NDArray[np.bool_]:
    """Quiet Gaia stars for V3: G 13-17 and total motion under 20 mas/yr (contracts/api.md)."""
    g_ = np.asarray(g, float)
    pm = np.hypot(np.asarray(pmra, float), np.asarray(pmdec, float))
    return np.isfinite(g_) & (g_ >= g_min) & (g_ <= g_max) & np.isfinite(pm) & (pm < pm_max)


# --- V2 / V3: the shift between two visit stacks ------------------------------------------------

@dataclass
class ShiftResult:
    shift_arcsec: float  # |target shift|, after the reference-star correction if applied
    pa_deg: float  # east of north
    dt_yr: float
    rate_arcsec_yr: float
    sigma_arcsec: float  # 2-D RMS of the reference stars' residual shifts (V3); NaN if none
    n_ref_stars: int
    predicted_arcsec: float | None  # Gaia's shift over dt_yr (proper motion only), or None
    d_east_arcsec: float
    d_north_arcsec: float
    raw_d_east_arcsec: float  # before the reference-star correction
    raw_d_north_arcsec: float
    frame_offset_arcsec: tuple[float, float]  # median reference residual (east, north) removed
    target_xy1: tuple[float, float]
    target_xy2: tuple[float, float]
    ref_residuals_arcsec: FloatArr = field(repr=False)  # (n, 2) east, north, of the refs kept
    target_snr: tuple[float, float] = (float("nan"), float("nan"))  # detection S/N in each stack
    match_r_arcsec: float = float("nan")  # the largest distance from the prediction allowed

    def to_measure(self) -> dict[str, Any]:
        """The `measure` object of GET /api/object (contracts/api.md)."""
        def f(v: float) -> float | None:
            return None if v is None or not np.isfinite(v) else float(v)

        return {
            "shift_arcsec": f(self.shift_arcsec),
            "pa_deg": f(self.pa_deg),
            "dt_yr": f(self.dt_yr),
            "rate_arcsec_yr": f(self.rate_arcsec_yr),
            "sigma_arcsec": f(self.sigma_arcsec),
            "n_ref_stars": int(self.n_ref_stars),
            "predicted_arcsec": None if self.predicted_arcsec is None else f(self.predicted_arcsec),
        }


class ShiftError(ValueError):
    """No honest shift: `reason` says why, as a code.

    not_found          the target could not be centroided in a stack
    not_detected       it does not stand out clearly (S/N < min_snr) in a stack
    edge               missing sky (the edge of the covered area) within edge_r px of a centroid
    different_sources  the two centroids are too far apart to be one source (see measure_shift)
    """

    def __init__(self, message: str, reason: str = "not_found") -> None:
        super().__init__(message)
        self.reason = reason


def _centroid_sky(image: ArrayLike, wcs: WCS, ra: float, dec: float, flags: ArrayLike | None,
                  search_r: float, method: str) -> tuple[float, float, float, float] | None:
    gx, gy = sky_to_pixel(wcs, ra, dec)
    c = centroid(image, float(gx), float(gy), flags=flags, search_r=search_r, method=method)
    if c is None:
        return None
    r, d = pixel_to_sky(wcs, c[0], c[1])
    return c[0], c[1], float(r), float(d)


def measure_shift(
    image1: ArrayLike,
    image2: ArrayLike,
    wcs1: WCS,
    wcs2: WCS,
    mjd1: float,
    mjd2: float,
    target_ra: float,
    target_dec: float,
    *,
    target_pmra: float | None = None,
    target_pmdec: float | None = None,
    target_parallax: float | None = None,
    target_epoch_mjd: float = GAIA_EPOCH_MJD,
    ref_ra: ArrayLike = (),
    ref_dec: ArrayLike = (),
    ref_pmra: ArrayLike | None = None,
    ref_pmdec: ArrayLike | None = None,
    ref_epoch_mjd: float = GAIA_EPOCH_MJD,
    flags1: ArrayLike | None = None,
    flags2: ArrayLike | None = None,
    search_r: float = 2.0,
    ref_search_r: float = 1.5,
    exclude_r_arcsec: float = 20.0,
    correct_with_refs: bool = True,
    min_ref_stars: int = 3,
    clip_sigma: float = 3.0,
    method: str = "fit",
    min_snr: float | None = MIN_SNR,
    edge_r: float | None = EDGE_R_PX,
    max_pm_arcsec_yr: float | None = MAX_PM_ARCSEC_YR,
    match_floor_arcsec: float = PSF_FWHM_ARCSEC,
) -> ShiftResult:
    """Target's shift between two visit stacks, with the reference-star error floor (V2 + V3).

    image1/2, wcs1/2, mjd1/2: the two stacks (e.g. first and last visit), their WCS, and mjd_mid.
    target_ra/dec (+ optional pm, parallax, at target_epoch_mjd): guesses the target's pixel in
        each stack. Without pm the same position is used for both; widen search_r for fast movers.
    ref_*: reference stars (pick them with select_reference_stars), propagated to each epoch.
    Each ref's residual = measured shift - its Gaia-predicted shift. If correct_with_refs and at
    least min_ref_stars survive, the median residual (a frame-to-frame WCS offset) is removed
    from the target's shift. sigma_arcsec is the 2-D RMS of the residuals after that (one pass of
    clip_sigma clipping); it is the 1-sigma error on the shift, and 3 sigma is the slowest motion
    we can see. Saturated cores are masked through flags, so centroids use the wings.
    method: centroid method for target and refs (detect.centroid); "fit" (pixel-integrated
        Gaussian) avoids the pixel-phase bias of windowed centroids on the undersampled PSF.

    The detection guard: a shift is one source, clearly seen in both stacks. Each check can be
    turned off with None. ShiftError (with its `reason`) when
    - edge_r: missing sky (detect.no_data_near) lies within edge_r px of either centroid;
    - min_snr: the target's PSF-matched S/N (detect.detection_snr) is below min_snr in either
      stack: the centroid then sits on whatever noise peak is brightest near the guess;
    - max_pm_arcsec_yr: the shift strays from the prediction by more than the match radius,
      max(match_floor_arcsec, the motion allowed) + 3 sigma (sigma: 0 without reference stars).
      The motion allowed is 0 with a predicted motion (target_pmra/pmdec: the guesses already
      follow it) and max_pm_arcsec_yr x dt without one, since no star moves faster. The floor
      is the PSF's FWHM: two dots closer than that can't be told apart anyway.
    """
    dt_yr = (mjd2 - mjd1) / DAYS_PER_JYEAR

    if target_pmra is not None and target_pmdec is not None:
        p1 = propagate(target_ra, target_dec, target_pmra, target_pmdec, mjd1, epoch_mjd=target_epoch_mjd, parallax=target_parallax)
        p2 = propagate(target_ra, target_dec, target_pmra, target_pmdec, mjd2, epoch_mjd=target_epoch_mjd, parallax=target_parallax)
        g1 = (float(p1[0]), float(p1[1]))
        g2 = (float(p2[0]), float(p2[1]))
        pe, pn = tangent_offset_arcsec(g1[0], g1[1], g2[0], g2[1])
        predicted: float | None = float(np.hypot(pe, pn))
    else:
        g1 = g2 = (float(target_ra), float(target_dec))
        predicted = None

    t1 = _centroid_sky(image1, wcs1, g1[0], g1[1], flags1, search_r, method)
    t2 = _centroid_sky(image2, wcs2, g2[0], g2[1], flags2, search_r, method)
    if t1 is None or t2 is None:
        raise ShiftError("target not found in " + ("first" if t1 is None else "second") + " stack")
    stacks = (("first", image1, flags1, t1), ("second", image2, flags2, t2))
    if edge_r is not None:
        for which, img, _, t in stacks:
            if no_data_near(img, t[0], t[1], edge_r):
                raise ShiftError(f"missing sky within {edge_r:g} px of the target in the {which} stack", "edge")
    snr = [float("nan"), float("nan")]
    if min_snr is not None:
        for i, (which, img, fl, t) in enumerate(stacks):
            snr[i] = detection_snr(img, t[0], t[1], flags=fl)
            if not snr[i] >= min_snr:
                raise ShiftError(f"target not detected in the {which} stack (S/N {snr[i]:.1f} < {min_snr:g})",
                                 "not_detected")
    re_, rn_ = tangent_offset_arcsec(t1[2], t1[3], t2[2], t2[3])
    raw_e, raw_n = float(re_), float(rn_)

    # reference stars
    rra = np.atleast_1d(np.asarray(ref_ra, float))
    rde = np.atleast_1d(np.asarray(ref_dec, float))
    rpa = np.zeros_like(rra) if ref_pmra is None else np.atleast_1d(np.asarray(ref_pmra, float))
    rpd = np.zeros_like(rra) if ref_pmdec is None else np.atleast_1d(np.asarray(ref_pmdec, float))
    res: list[tuple[float, float]] = []
    for a, d, pa, pd in zip(rra, rde, rpa, rpd):
        if float(np.hypot(*tangent_offset_arcsec(t1[2], t1[3], a, d))) < exclude_r_arcsec:
            continue  # too close to the target (blends, or it *is* the target)
        q1 = propagate(a, d, pa, pd, mjd1, epoch_mjd=ref_epoch_mjd)
        q2 = propagate(a, d, pa, pd, mjd2, epoch_mjd=ref_epoch_mjd)
        c1 = _centroid_sky(image1, wcs1, float(q1[0]), float(q1[1]), flags1, ref_search_r, method)
        c2 = _centroid_sky(image2, wcs2, float(q2[0]), float(q2[1]), flags2, ref_search_r, method)
        if c1 is None or c2 is None:
            continue
        me, mn = tangent_offset_arcsec(c1[2], c1[3], c2[2], c2[3])
        pe_, pn_ = tangent_offset_arcsec(q1[0], q1[1], q2[0], q2[1])
        res.append((float(me - pe_), float(mn - pn_)))
    r = np.array(res, dtype=np.float64).reshape(-1, 2)

    offset = np.zeros(2)
    if len(r) >= 5:
        # one robust clipping pass: |dev| is Rayleigh with median sigma * sqrt(2 ln 2)
        med = np.median(r, axis=0)
        dev = np.hypot(*(r - med).T)
        sigma_axis = float(np.median(dev)) / np.sqrt(2.0 * np.log(2.0))
        if sigma_axis > 0:
            r = r[dev <= clip_sigma * np.sqrt(2.0) * sigma_axis]
    n_ref = len(r)
    if correct_with_refs and n_ref >= min_ref_stars:
        offset = np.median(r, axis=0)
    resid = r - offset
    sigma = float(np.sqrt(np.mean(np.sum(resid**2, axis=1)))) if n_ref else float("nan")

    de, dn = raw_e - offset[0], raw_n - offset[1]
    shift = float(np.hypot(de, dn))
    match_r = float("nan")
    if max_pm_arcsec_yr is not None:
        allowed = 0.0 if predicted is not None else max_pm_arcsec_yr * abs(dt_yr)
        match_r = max(match_floor_arcsec, allowed) + 3.0 * (sigma if np.isfinite(sigma) else 0.0)
        stray = float(np.hypot(de - pe, dn - pn)) if predicted is not None else shift
        if stray > match_r:
            what = "from the predicted motion" if predicted is not None else "between the stacks"
            raise ShiftError(f"the centroids are {stray:.1f}\" apart {what}, over the {match_r:.1f}\" one "
                             "source can account for: two different sources", "different_sources")
    return ShiftResult(
        shift_arcsec=shift,
        pa_deg=float(position_angle_deg(de, dn)),
        dt_yr=float(dt_yr),
        rate_arcsec_yr=shift / dt_yr if dt_yr != 0 else float("nan"),
        sigma_arcsec=sigma,
        n_ref_stars=n_ref,
        predicted_arcsec=predicted,
        d_east_arcsec=float(de),
        d_north_arcsec=float(dn),
        raw_d_east_arcsec=raw_e,
        raw_d_north_arcsec=raw_n,
        frame_offset_arcsec=(float(offset[0]), float(offset[1])),
        target_xy1=(t1[0], t1[1]),
        target_xy2=(t2[0], t2[1]),
        ref_residuals_arcsec=resid,
        target_snr=(snr[0], snr[1]),
        match_r_arcsec=match_r,
    )
