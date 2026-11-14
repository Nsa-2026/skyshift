"""Known solar-system objects near a position at a time: IMCCE SkyBoT cone search, cached.

Entries have the contract's `known[]` shape: name, ra, dec (deg), v (V magnitude or null), source.
`epoch_id` is added by the caller (frames: the epoch's id; /api/known: null).

SkyBoT is asked for the geocentre (-loc=500): SPHEREx is in low Earth orbit, so the parallax
against a main-belt asteroid is under 10" (under 2 native pixels).
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import requests

from .cache import Cache
from .errors import UpstreamError
from .search import pos_key

log = logging.getLogger(__name__)

SKYBOT = "https://ssp.imcce.fr/webservices/skybot/api/conesearch.php"
MJD_TO_JD = 2400000.5


def _sexa(text: str, hours: bool) -> float:
    s = text.strip()
    sign = -1.0 if s.startswith("-") else 1.0
    a, b, c = (float(p) for p in s.lstrip("+-").split())
    v = sign * (a + b / 60.0 + c / 3600.0)
    return v * 15.0 if hours else v


def parse_skybot_text(text: str) -> list[dict[str, Any]]:
    """SkyBoT '-mime=text -output=object' answer -> entries (nearest first, as SkyBoT sorts them).

    # Num | Name | RA(h) | DE(deg) | Class | Mv | Err(arcsec) | d(arcsec)
    """
    out = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        p = [x.strip() for x in line.split("|")]
        if len(p) < 6:
            continue
        num, name, cls = p[0], p[1], p[4]
        try:
            ra, dec = _sexa(p[2], True), _sexa(p[3], False)
            v: float | None = float(p[5])
        except ValueError:
            continue
        if cls.startswith("Comet") and not v:
            v = None  # SkyBoT prints 0.0 for comets: no magnitude
        if cls.startswith("Comet"):
            label = num if num not in ("", "-") else name
        else:
            label = f"({num}) {name}" if num not in ("", "-") else name
        out.append({"name": label, "ra": round(ra, 6), "dec": round(dec, 6), "v": v, "source": "skybot"})
    return out


def _skybot(ra: float, dec: float, r_arcsec: float, mjd: float) -> str:
    params = {"-ep": f"{mjd + MJD_TO_JD:.6f}", "-ra": f"{ra:.6f}", "-dec": f"{dec:.6f}",
              "-rs": f"{r_arcsec:.0f}", "-mime": "text", "-output": "object", "-loc": "500",
              "-filter": "0", "-objFilter": "111", "-from": "SkyShift"}
    last = ""
    for attempt in range(3):
        try:
            r = requests.get(SKYBOT, params=params, timeout=60, headers={"User-Agent": "SkyShift/0.1"})
            if r.status_code == 200:
                return r.text
            if r.status_code == 204:  # no content: nothing known in the cone
                return ""
            last = f"HTTP {r.status_code}: {r.text[:200]}"
        except requests.RequestException as e:
            last = repr(e)
        time.sleep(2 * (attempt + 1))
    raise UpstreamError(f"SkyBoT query failed: {last}")


def cone(ra: float, dec: float, r_arcmin: float, mjd: float, *, cache: Cache | None = None) -> list[dict[str, Any]]:
    """Known objects within r_arcmin of (ra, dec) at `mjd` (UTC), nearest first."""
    cache = cache or Cache()
    key = f"skybot/{pos_key(ra, dec)}_{r_arcmin:g}_{mjd:.5f}.json"
    raw = cache.get(key)
    if raw is not None:
        return json.loads(raw)
    found = parse_skybot_text(_skybot(ra, dec, r_arcmin * 60.0, mjd))
    cache.put(key, json.dumps(found).encode(), "application/json")
    return found
