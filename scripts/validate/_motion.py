"""V6 and V7 helpers: star selection from Gaia DR3 rows, the predicted shift and the summary numbers.

numpy and skyshift_core only, so both the measurement script (v6_v7_motion.py, server environment) and
build_validation.py (this folder's environment) import it. Units: degrees, mas/yr, mas, arcsec, MJD.
"""

from __future__ import annotations

import math
from typing import Any, Iterable

import numpy as np
from skyshift_core import GAIA_EPOCH_MJD, propagate, tangent_offset_arcsec

# V6 (S2): docs/skyshift-hackathon-plan.html, "How we know it works": the 40 fastest stars with G > 12.
V6_COUNT = 40
V6_G_MIN = 12.0
# V7 (S3): brown dwarfs faster than 1"/yr. Gagne's list was never loaded (CDS is down), so the list is Gaia's own
# ultracool dwarfs: absolute G fainter than V7_MG_MIN and G - RP redder than V7_GRP_MIN (the L dwarfs and fainter:
# at the star/brown-dwarf boundary and below), a parallax measured to V7_PLX_SNR_MIN, plus the built-in brown dwarf
# Gaia doesn't have (WISE 0855-0714). Late-M stars (absolute G 15-16.5) are left out.
V7_PM_MIN = 1000.0
V7_MG_MIN = 16.5
V7_GRP_MIN = 1.5
V7_PLX_SNR_MIN = 10.0
# V7 "in the predicted direction": the measured shift is significant (>= 3 sigma) and within this angle of
# the prediction. Counted over the dwarfs whose predicted shift is >= 3 sigma (visible): with one year between
# visits most dwarfs move 1-2" against an error of 0.3-0.9", which no measurement can show at 3 sigma (rule set
# on 15 Nov after the first run, which counted every measured dwarf: 9 of 24).
V7_MAX_ANGLE_DEG = 30.0
V7_MIN_SIGMA = 3.0


def _f(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def gaia_model(row: dict[str, Any], name: str | None = None) -> dict[str, Any]:
    """A Gaia DR3 row (source_id, ra, dec, pmra, pmdec, parallax, phot_g_mean_mag) as an astrometric
    model in the server's shape (obj.position_at, obj.measure)."""
    sid = str(row["source_id"])
    return {"id": sid, "name": name or f"Gaia DR3 {sid}", "ra": float(row["ra"]), "dec": float(row["dec"]),
            "pmra": _f(row.get("pmra")), "pmdec": _f(row.get("pmdec")), "parallax": _f(row.get("parallax")),
            "g": _f(row.get("phot_g_mean_mag")), "epoch_mjd": GAIA_EPOCH_MJD}


def abs_g(g: float | None, parallax_mas: float | None) -> float | None:
    if g is None or parallax_mas is None or parallax_mas <= 0:
        return None
    return g + 5.0 * math.log10(parallax_mas / 100.0)


def select_v6(rows: Iterable[dict[str, Any]], n: int = V6_COUNT, g_min: float = V6_G_MIN) -> list[dict[str, Any]]:
    """The n fastest rows with G > g_min (and a proper motion), fastest first."""
    ok = [r for r in rows if _f(r.get("pm")) is not None and (_f(r.get("phot_g_mean_mag")) or -99) > g_min]
    return sorted(ok, key=lambda r: -float(r["pm"]))[:n]


def is_ultracool(row: dict[str, Any], mg_min: float = V7_MG_MIN, grp_min: float = V7_GRP_MIN,
                 plx_snr_min: float = V7_PLX_SNR_MIN) -> bool:
    g, rp = _f(row.get("phot_g_mean_mag")), _f(row.get("phot_rp_mean_mag"))
    plx, plx_err = _f(row.get("parallax")), _f(row.get("parallax_error"))
    if None in (g, rp, plx, plx_err) or plx_err <= 0 or plx / plx_err < plx_snr_min:
        return False
    mg = abs_g(g, plx)
    return mg is not None and mg > mg_min and g - rp > grp_min


def select_v7(rows: Iterable[dict[str, Any]], pm_min: float = V7_PM_MIN) -> list[dict[str, Any]]:
    """Gaia's ultracool dwarfs faster than pm_min, fastest first."""
    ok = [r for r in rows if (_f(r.get("pm")) or 0.0) > pm_min and is_ultracool(r)]
    return sorted(ok, key=lambda r: -float(r["pm"]))


def predicted_shift(model: dict[str, Any], mjd1: float, mjd2: float) -> tuple[float, float]:
    """(east, north) arcsec the model moves from mjd1 to mjd2: proper motion and, when it has one, parallax."""
    plx = model.get("parallax")
    kw = dict(epoch_mjd=model.get("epoch_mjd", GAIA_EPOCH_MJD), parallax=None if plx is None else plx)
    r1, d1 = propagate(model["ra"], model["dec"], model["pmra"], model["pmdec"], mjd1, **kw)
    r2, d2 = propagate(model["ra"], model["dec"], model["pmra"], model["pmdec"], mjd2, **kw)
    e, n = tangent_offset_arcsec(float(r1), float(d1), float(r2), float(d2))
    return float(e), float(n)


def angle_deg(e1: float, n1: float, e2: float, n2: float) -> float:
    """Angle between two sky vectors, 0-180 degrees."""
    a = math.degrees(math.atan2(e1, n1)) - math.degrees(math.atan2(e2, n2))
    return abs((a + 180.0) % 360.0 - 180.0)


def pa_deg(e: float, n: float) -> float:
    return math.degrees(math.atan2(e, n)) % 360.0


def slope_fit(meas: list[tuple[float, float]], pred: list[tuple[float, float]],
              sigma: list[float] | None = None) -> dict[str, float | int | None]:
    """Measured against predicted shift, both components of every star: the slope of a line through the
    origin (weights 1/sigma^2 when given), its error from the weighted residuals, and the RMS of the
    residual vectors |measured - predicted| (arcsec)."""
    if not meas:
        return {"n": 0, "slope": None, "slope_err": None, "rms_arcsec": None, "median_resid_arcsec": None}
    m = np.asarray(meas, float).reshape(-1, 2)
    p = np.asarray(pred, float).reshape(-1, 2)
    w = np.ones(len(m)) if sigma is None else 1.0 / np.square(np.asarray(sigma, float))
    w2 = np.repeat(w, 2)
    x, y = p.ravel(), m.ravel()
    sxx = float(np.sum(w2 * x * x))
    slope = float(np.sum(w2 * x * y) / sxx) if sxx > 0 else float("nan")
    res = y - slope * x
    dof = max(len(y) - 1, 1)
    slope_err = float(math.sqrt(np.sum(w2 * res * res) / dof / sxx)) if sxx > 0 else float("nan")
    d = np.hypot(*(m - p).T)
    return {"n": int(len(m)), "slope": slope, "slope_err": slope_err, "rms_arcsec": float(np.sqrt(np.mean(d * d))),
            "median_resid_arcsec": float(np.median(d))}


def visible(row: dict[str, Any], min_sigma: float = V7_MIN_SIGMA) -> bool:
    """V7's sample: the predicted shift is at least min_sigma of the measurement's error, so SPHEREx could show
    its direction at all. Decided from Gaia's prediction and the error bar, never from the measured shift."""
    p, sig = row.get("predicted_arcsec"), row.get("sigma_arcsec")
    return p is not None and sig is not None and sig > 0 and p / sig >= min_sigma - 1e-9  # 1.2 / 0.4 is 3


def in_predicted_direction(row: dict[str, Any], max_angle: float = V7_MAX_ANGLE_DEG,
                           min_sigma: float = V7_MIN_SIGMA) -> bool:
    """V7: the measured shift is at least min_sigma and within max_angle of the predicted one."""
    s, sig, ang = row.get("shift_arcsec"), row.get("sigma_arcsec"), row.get("angle_deg")
    if s is None or sig is None or ang is None or sig <= 0:
        return False
    return s / sig >= min_sigma - 1e-9 and ang <= max_angle
