"""Shared helpers for the validation data-gathering scripts (scripts/validate/).

Units follow contracts/api.md: degrees, arcsec, µm, MJD (UTC).
"""

from __future__ import annotations

import datetime as dt
import json
import math
import shutil
import subprocess
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
PHOTOLISTS = DATA / "photolists"
CACHE = HERE / ".cache"  # gitignored
BUCKET = "gs://nasa-glazing-data"

# Gaia DR3 reference epoch J2016.0 (TCB) = JD 2457389.0 = MJD 57388.5.
# contracts/api.md writes "MJD 57388.0"; the 0.5 d difference moves Barnard's Star by 0.014".
GAIA_EPOCH_YR = 2016.0
GAIA_EPOCH_MJD = 57388.5
DAYS_PER_YR = 365.25
PIXEL_ARCSEC = 6.15


def mjd_to_jyear(mjd: float) -> float:
    return GAIA_EPOCH_YR + (mjd - GAIA_EPOCH_MJD) / DAYS_PER_YR


def jyear_to_mjd(year: float) -> float:
    return GAIA_EPOCH_MJD + (year - GAIA_EPOCH_YR) * DAYS_PER_YR


def propagate(ra: float, dec: float, pmra: float, pmdec: float, from_mjd: float, to_mjd):
    """Linear proper-motion propagation (no parallax, no perspective acceleration).

    pmra is Gaia's mu_alpha* (already x cos dec), mas/yr. Good to well under 0.1" over 10 yr for
    every target here (Barnard's Star's secular acceleration adds ~0.06").
    """
    dt_yr = (np.asarray(to_mjd, dtype=float) - from_mjd) / DAYS_PER_YR
    dec_new = dec + pmdec * dt_yr / 3.6e6
    ra_new = ra + pmra * dt_yr / 3.6e6 / np.cos(np.radians(dec))
    return np.mod(ra_new, 360.0), dec_new


def detector_from_bandpass(name: str) -> int:
    """'SPHEREx-D2' -> 2 (contracts/api.md: band from energy_bandpassname, never file names)."""
    name = str(name).strip()
    if not name.startswith("SPHEREx-D"):
        raise ValueError(f"unexpected energy_bandpassname {name!r}")
    return int(name.removeprefix("SPHEREx-D"))


# --- visit rule -----------------------------------------------------------------------------
# The contracts/api.md visit and session rule, from the shared library (one copy of the rule).
from skyshift_core import sessions, visits  # noqa: E402,F401


# --- geometry -------------------------------------------------------------------------------
def parse_polygon(s_region: str) -> list[tuple[float, float]]:
    """'POLYGON ICRS ra1 dec1 ra2 dec2 ...' -> [(ra, dec), ...] (closing vertex dropped)."""
    parts = str(s_region).split()
    i = 1
    if parts[i].upper() in {"ICRS", "J2000", "FK5"}:
        i += 1
    nums = [float(x) for x in parts[i:]]
    pts = list(zip(nums[0::2], nums[1::2]))
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    return pts


def _gnomonic(ra, dec, ra0, dec0):
    ra, dec, ra0, dec0 = map(np.radians, (ra, dec, ra0, dec0))
    cosc = np.sin(dec0) * np.sin(dec) + np.cos(dec0) * np.cos(dec) * np.cos(ra - ra0)
    x = np.cos(dec) * np.sin(ra - ra0) / cosc
    y = (np.cos(dec0) * np.sin(dec) - np.sin(dec0) * np.cos(dec) * np.cos(ra - ra0)) / cosc
    return np.degrees(x), np.degrees(y)


def edge_distance_px(ra: float, dec: float, poly: list[tuple[float, float]]) -> float:
    """Signed distance in 6.15" pixels from (ra, dec) to the polygon edge: > 0 inside, < 0 outside."""
    ra0, dec0 = ra, dec
    xs, ys = _gnomonic(np.array([p[0] for p in poly]), np.array([p[1] for p in poly]), ra0, dec0)
    px, py = 0.0, 0.0  # the point is the tangent point
    inside = False
    n = len(xs)
    dmin = math.inf
    for k in range(n):
        x1, y1, x2, y2 = xs[k], ys[k], xs[(k + 1) % n], ys[(k + 1) % n]
        if (y1 > py) != (y2 > py):
            xc = x1 + (py - y1) * (x2 - x1) / (y2 - y1)
            if px < xc:
                inside = not inside
        dx, dy = x2 - x1, y2 - y1
        L2 = dx * dx + dy * dy
        u = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / L2))
        dmin = min(dmin, math.hypot(x1 + u * dx - px, y1 + u * dy - py))
    d_px = dmin * 3600.0 / PIXEL_ARCSEC
    return d_px if inside else -d_px


# --- output ---------------------------------------------------------------------------------
def today() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%d")


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def gcs_upload(local: Path, dest: str) -> bool:
    """Copy a file to the project bucket with `gcloud storage cp`. Returns False (and says so) on failure."""
    gcloud = shutil.which("gcloud") or str(Path.home() / "google-cloud-sdk/bin/gcloud")
    try:
        subprocess.run([gcloud, "storage", "cp", "--quiet", str(local), dest], check=True,
                       capture_output=True, text=True)
        print(f"  uploaded {local.name} -> {dest}")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"  upload FAILED for {local} -> {dest}: {getattr(e, 'stderr', e)}")
        return False
