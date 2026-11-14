"""Find every SPHEREx Level 2 image covering a position (IRSA SIA) and group them into visits.

One row per (obs_id, detector). Collections spherex_dr1 and spherex_qr3 (never *_deep); an image
present in both keeps the DR1 row. Detector from energy_bandpassname, time = middle of
t_min/t_max, visits and sessions from skyshift_core (computed over every exposure at the position,
all detectors together, so a visit number means the same thing in every detector).

Results are cached per position and per day ("search/<ra>_<dec>/<YYYY-MM-DD>.json"): IRSA ingests
weekly, and `archive_checked` in the frames response is the date of the search that was used.
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

import numpy as np
from skyshift_core import detector_from_bandpass, sessions, visits

from .cache import Cache
from .errors import UpstreamError

log = logging.getLogger(__name__)

COLLECTIONS = ("spherex_dr1", "spherex_qr3")  # DR1 first: it wins duplicates
DATA_RELEASE = "DR1+QR3"
SEARCH_RADIUS_ARCSEC = 1.0


@dataclass
class Image:
    """One Level 2 image: an exposure seen by one detector."""

    collection: str
    obs_id: str
    det: int  # 1-6, from energy_bandpassname
    t_min: float
    t_max: float
    mjd: float  # (t_min + t_max) / 2; replaced by MJD-AVG once the file is open
    access_url: str  # IRSA image URL (cutouts are served from it)
    s3_url: str | None  # https URL of NASA's public S3 copy, from cloud_access
    visit: int = 0
    session: int = 0

    @property
    def key(self) -> str:
        return f"{self.obs_id}D{self.det}"


@dataclass
class SearchResult:
    ra: float
    dec: float
    archive_checked: str  # YYYY-MM-DD
    images: list[Image]

    def of_detector(self, det: int) -> list[Image]:
        return [im for im in self.images if im.det == det]


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def pos_key(ra: float, dec: float) -> str:
    """Cache-key part for a position: 1e-5 deg = 0.036 arcsec."""
    return f"{ra:.5f}_{dec:+.5f}"


def s3_https(cloud_access: str | None) -> str | None:
    """cloud_access JSON -> anonymous https URL of the S3 object, or None."""
    try:
        aws = json.loads(cloud_access or "")["aws"]
        return f"https://{aws['bucket_name']}.s3.{aws['region']}.amazonaws.com/{aws['key']}"
    except (ValueError, KeyError, TypeError):
        return None


def rows_to_images(rows: list[dict[str, Any]]) -> list[Image]:
    """Plain SIA rows -> images: science images only, no *_deep, one per (obs_id, detector)
    (the first collection in COLLECTIONS wins), with visit and session numbers, sorted by time."""
    seen: dict[tuple[str, int], Image] = {}
    order = {c: i for i, c in enumerate(COLLECTIONS)}
    rows = sorted((r for r in rows if str(r.get("obs_collection")) in order),
                  key=lambda r: order[str(r["obs_collection"])])
    for r in rows:
        if str(r.get("dataproduct_type", "image")) != "image":
            continue
        if str(r.get("dataproduct_subtype", "science")) != "science":
            continue
        det = detector_from_bandpass(str(r["energy_bandpassname"]))
        t0, t1 = float(r["t_min"]), float(r["t_max"])
        im = Image(str(r["obs_collection"]), str(r["obs_id"]), det, t0, t1, (t0 + t1) / 2.0,
                   str(r["access_url"]), s3_https(r.get("cloud_access")))
        seen.setdefault((im.obs_id, im.det), im)
    images = sorted(seen.values(), key=lambda im: (im.mjd, im.det))
    return number_visits(images)


def number_visits(images: list[Image]) -> list[Image]:
    """Set visit and session on every image from the times at this position (core's rule)."""
    if images:
        t = np.array([im.mjd for im in images])
        for im, v, s in zip(images, visits(t), sessions(t)):
            im.visit, im.session = int(v), int(s)
    return images


def _query_sia(ra: float, dec: float, collection: str) -> list[dict[str, Any]]:
    import astropy.units as u
    from astropy.coordinates import SkyCoord
    from astroquery.ipac.irsa import Irsa

    pos = SkyCoord(ra * u.deg, dec * u.deg, frame="icrs")
    last: Exception | None = None
    for attempt in range(3):
        try:
            t = Irsa.query_sia(pos=(pos, SEARCH_RADIUS_ARCSEC * u.arcsec), collection=collection)
            break
        except Exception as e:  # noqa: BLE001 - network: retry, then give up loudly
            last = e
            log.warning("SIA %s failed (%s), attempt %d", collection, e, attempt + 1)
            time.sleep(3 * (attempt + 1))
    else:
        raise UpstreamError(f"IRSA SIA search failed for {collection}: {last}")
    cols = ("obs_collection", "obs_id", "energy_bandpassname", "t_min", "t_max", "access_url",
            "cloud_access", "dataproduct_type", "dataproduct_subtype")
    out = []
    for r in t:
        d = {}
        for c in cols:
            v = r[c]
            d[c] = None if np.ma.is_masked(v) else (float(v) if c in ("t_min", "t_max") else str(v))
        out.append(d)
    return out


def search(ra: float, dec: float, *, cache: Cache | None = None, refresh: bool = False) -> SearchResult:
    """Every DR1 + QR3 image covering (ra, dec), with visit and session numbers."""
    cache = cache or Cache()
    day = today()
    key = f"search/{pos_key(ra, dec)}/{day}.json"
    raw = None if refresh else cache.get(key)
    if raw is not None:
        d = json.loads(raw)
        return SearchResult(d["ra"], d["dec"], d["archive_checked"], [Image(**im) for im in d["images"]])
    with ThreadPoolExecutor(len(COLLECTIONS)) as ex:
        parts = list(ex.map(lambda c: _query_sia(ra, dec, c), COLLECTIONS))
    images = rows_to_images([r for p in parts for r in p])
    res = SearchResult(ra, dec, day, images)
    cache.put(key, json.dumps({"ra": ra, "dec": dec, "archive_checked": day,
                               "images": [asdict(im) for im in images]}).encode(), "application/json")
    return res
