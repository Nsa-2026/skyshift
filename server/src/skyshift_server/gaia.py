"""Gaia DR3 stars around a position, from the ESA Gaia Archive (TAP, synchronous), cached.
When ESA fails, from IRSA's copy of Gaia DR3 (gaia_dr3_source), as the pipeline's refcat does: since
25 Nov 2026 ESA's TAP URLs answer with an HTML page, and every live answer waited ~28 s for it and
came back partial (not cached). A failed ESA is skipped for ESA_PAUSE_S.

Entries have the contract's `gaia[]` shape: id (string), ra, dec (deg, epoch J2016.0),
pmra (x cos dec), pmdec (mas/yr), parallax (mas), g (mag); pmra, pmdec, parallax, g may be null.

A cone keeps at most MAX_STARS: every star with G <= G_LIMIT or a motion above FAST_PM mas/yr,
brightest first. SPHEREx reaches about AB 19 per exposure, and the blink needs the fast stars
whatever their brightness.
"""

from __future__ import annotations

import csv
import io
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
IRSA_TAP_SYNC = "https://irsa.ipac.caltech.edu/TAP/sync"
ESA_PAUSE_S = 600.0  # after ESA fails, ask IRSA first for this long
TIMEOUT = (6, 25)  # connect, read (s): a live answer's whole budget is 40 s
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


def _entry(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(r["source_id"]),
        "ra": float(r["ra"]),
        "dec": float(r["dec"]),
        "pmra": _num(r.get("pmra"), 3),
        "pmdec": _num(r.get("pmdec"), 3),
        "parallax": _num(r.get("parallax"), 3),
        "g": _num(r.get("phot_g_mean_mag"), 3),
    }


def parse_tap_json(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Gaia TAP JSON ({metadata:[{name}], data:[[...]]}) -> contract entries."""
    names = [m["name"] for m in payload["metadata"]]
    return [_entry(dict(zip(names, row))) for row in payload["data"]]


def parse_tap_csv(text: str) -> list[dict[str, Any]]:
    """IRSA TAP CSV (a header row; an empty field is null) -> contract entries."""
    return [_entry(r) for r in csv.DictReader(io.StringIO(text))]


_esa_down_until = 0.0


def _tap(adql: str) -> dict[str, Any]:
    """ESA, JSON. Network errors and 5xx are asked once more; an answer that is not JSON (ESA's HTML
    page) fails at once: asking again gives the same page."""
    last = ""
    for attempt in range(2):
        try:
            r = requests.post(TAP_SYNC, data={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "json",
                                              "QUERY": adql}, timeout=TIMEOUT,
                              headers={"User-Agent": "SkyShift/0.1"})
            if r.status_code == 200:
                if "json" not in r.headers.get("content-type", "json"):
                    raise UpstreamError(f"Gaia archive answered {r.headers.get('content-type')}, not JSON")
                return r.json()
            last = f"HTTP {r.status_code}: {r.text[:200]}"
            if r.status_code < 500:
                break
        except (requests.RequestException, ValueError) as e:
            last = repr(e)
        if attempt == 0:
            time.sleep(1.0)
    raise UpstreamError(f"Gaia archive query failed: {last}")


def _irsa(adql: str) -> str:
    """IRSA's Gaia DR3 (gaia_dr3_source), CSV."""
    last = ""
    for attempt in range(2):
        try:
            r = requests.post(IRSA_TAP_SYNC, data={"LANG": "ADQL", "FORMAT": "csv", "QUERY": adql},
                              timeout=TIMEOUT, headers={"User-Agent": "SkyShift/0.1"})
            if r.status_code == 200 and r.text.startswith("source_id"):
                return r.text
            last = f"HTTP {r.status_code}: {r.text[:200]}"
        except requests.RequestException as e:
            last = repr(e)
        if attempt == 0:
            time.sleep(1.0)
    raise UpstreamError(f"IRSA Gaia query failed: {last}")


def stars_in(ra: float, dec: float, r_arcmin: float) -> list[dict[str, Any]]:
    """ESA first, IRSA when ESA fails (or failed less than ESA_PAUSE_S ago)."""
    global _esa_down_until
    esa_error = None
    if time.monotonic() >= _esa_down_until:
        try:
            return parse_tap_json(_tap(cone_adql(ra, dec, r_arcmin)))
        except UpstreamError as e:
            _esa_down_until = time.monotonic() + ESA_PAUSE_S
            esa_error = e
            log.warning("ESA Gaia failed, asking IRSA: %s", e)
    try:
        return parse_tap_csv(_irsa(cone_adql(ra, dec, r_arcmin, table="gaia_dr3_source")))
    except UpstreamError as e:
        raise UpstreamError(f"{esa_error or 'ESA Gaia paused'}; {e}") from e


def cone_adql(ra: float, dec: float, r_arcmin: float, table: str = "gaiadr3.gaia_source") -> str:
    return (
        f"SELECT TOP {MAX_STARS} {COLUMNS} FROM {table} "
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
    stars = stars_in(ra, dec, r_arcmin)
    cache.put(key, json.dumps(stars).encode(), "application/json")
    return stars
