"""Gaia DR3 stars around a position, from the ESA Gaia Archive (TAP, synchronous), cached.

Entries have the contract's `gaia[]` shape: id (string), ra, dec (deg, epoch J2016.0),
pmra (x cos dec), pmdec (mas/yr), parallax (mas), g (mag); pmra, pmdec, parallax, g may be null.

A cone keeps at most MAX_STARS: every star with G <= G_LIMIT or a motion above FAST_PM mas/yr,
brightest first. SPHEREx reaches about AB 19 per exposure, and the blink needs the fast stars
whatever their brightness.
"""

from __future__ import annotations

import json
import logging
import math
import time
from typing import Any

import requests

from .cache import Cache
from .errors import UpstreamError
from .search import pos_key

log = logging.getLogger(__name__)

TAP_SYNC = "https://gea.esac.esa.int/tap-server/tap/sync"
G_LIMIT = 19.5
FAST_PM = 100.0  # mas/yr
MAX_STARS = 2500
COLUMNS = "source_id, ra, dec, pmra, pmdec, parallax, phot_g_mean_mag"


def _num(v: Any, digits: int) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f, digits) if math.isfinite(f) else None


def parse_tap_json(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Gaia TAP JSON ({metadata:[{name}], data:[[...]]}) -> contract entries."""
    names = [m["name"] for m in payload["metadata"]]
    out = []
    for row in payload["data"]:
        r = dict(zip(names, row))
        out.append({
            "id": str(r["source_id"]),
            "ra": float(r["ra"]),
            "dec": float(r["dec"]),
            "pmra": _num(r.get("pmra"), 3),
            "pmdec": _num(r.get("pmdec"), 3),
            "parallax": _num(r.get("parallax"), 3),
            "g": _num(r.get("phot_g_mean_mag"), 3),
        })
    return out


def _tap(adql: str) -> dict[str, Any]:
    last = ""
    for attempt in range(4):
        try:
            r = requests.post(TAP_SYNC, data={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "json",
                                              "QUERY": adql}, timeout=90,
                              headers={"User-Agent": "SkyShift/0.1"})
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}: {r.text[:200]}"
        except (requests.RequestException, ValueError) as e:
            last = repr(e)
        time.sleep(2 * (attempt + 1))
    raise UpstreamError(f"Gaia archive query failed: {last}")


def cone_adql(ra: float, dec: float, r_arcmin: float) -> str:
    return (
        f"SELECT TOP {MAX_STARS} {COLUMNS} FROM gaiadr3.gaia_source "
        f"WHERE 1=CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra:.7f}, {dec:.7f}, {r_arcmin / 60.0:.6f})) "
        f"AND (phot_g_mean_mag <= {G_LIMIT} OR pm > {FAST_PM}) "
        "ORDER BY phot_g_mean_mag ASC"
    )


def cone(ra: float, dec: float, r_arcmin: float, *, cache: Cache | None = None) -> list[dict[str, Any]]:
    """Gaia DR3 stars within r_arcmin of (ra, dec) at epoch J2016.0, brightest first."""
    cache = cache or Cache()
    key = f"gaia/{pos_key(ra, dec)}_{r_arcmin:g}.json"
    raw = cache.get(key)
    if raw is not None:
        return json.loads(raw)
    stars = parse_tap_json(_tap(cone_adql(ra, dec, r_arcmin)))
    cache.put(key, json.dumps(stars).encode(), "application/json")
    return stars
