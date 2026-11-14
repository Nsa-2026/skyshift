"""Find every SPHEREx Level 2 image covering a position (IRSA SIA) and group them into visits.

One row per (obs_id, detector). Collections spherex_dr1 and spherex_qr3 (never *_deep); an image
present in both keeps the DR1 row. Detector from energy_bandpassname, time = middle of
t_min/t_max, visits and sessions from skyshift_core (computed over every exposure at the position,
all detectors together, so a visit number means the same thing in every detector).

Results are cached per position and per day ("search/<ra>_<dec>/<YYYY-MM-DD>.json"): IRSA ingests
weekly, and `archive_checked` in the frames response is the date of the search that was used.
Each image keeps its footprint (s_region), so the engine can tell how much of a field it covers
before downloading anything (coverage.py). A search saved before footprints were kept (no
"footprints" key) is asked again; if IRSA does not answer, it is used as it is.

When IRSA fails (an error or a 5xx after the retries) or does not answer within SIA_WAIT_S, the
photos come from SkyShift's own photo lists and saved searches (search_fallback.py). Such a result
carries a `note`; the engine marks the answer partial with it, so it is never cached as complete,
and it is not saved as the day's search. After a failure IRSA is asked once, without retries and
for at most SIA_WAIT_DOWN_S, for the next SIA_DOWN_S seconds.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
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
SIA_TRIES = 3
SIA_WAIT_S = float(os.environ.get("SKYSHIFT_SIA_WAIT_S", "20"))  # longest wait for IRSA's answer
SIA_WAIT_DOWN_S = 6.0  # ... while IRSA is down (it failed less than SIA_DOWN_S ago)
SIA_DOWN_S = 120.0
FALLBACK_SHARE_S = 60.0  # a fallback result is reused this long (frames and object at one spot)


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
    s_region: str | None = None  # footprint, 'POLYGON ICRS ra dec ...' (None: unknown)

    @property
    def key(self) -> str:
        return f"{self.obs_id}D{self.det}"


@dataclass
class SearchResult:
    ra: float
    dec: float
    archive_checked: str  # YYYY-MM-DD
    images: list[Image]
    note: str | None = None  # set when the photos did not come from IRSA at this position: the answer is partial

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


def region_text(v: Any) -> str | None:
    """An s_region value as text: a string as it is, a flat array of numbers (a DALI polygon) as
    'POLYGON ICRS ra dec ...'; None when missing or empty."""
    if v is None or np.ma.is_masked(v):
        return None
    if isinstance(v, bytes):
        v = v.decode("ascii", "replace")
    if isinstance(v, str):
        return v.strip() or None
    try:
        nums = [float(x) for x in np.asarray(v, dtype=np.float64).ravel()]
    except (TypeError, ValueError):
        return str(v).strip() or None
    return ("POLYGON ICRS " + " ".join(repr(x) for x in nums)) if nums else None


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
                   str(r["access_url"]), s3_https(r.get("cloud_access")),
                   s_region=region_text(r.get("s_region")))
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
    tries = 1 if _sia.down() else SIA_TRIES
    for attempt in range(tries):
        try:
            t = Irsa.query_sia(pos=(pos, SEARCH_RADIUS_ARCSEC * u.arcsec), collection=collection)
            break
        except Exception as e:  # noqa: BLE001 - network: retry, then give up loudly
            last = e
            log.warning("SIA %s failed (%s), attempt %d", collection, str(e)[:200], attempt + 1)
            if attempt < tries - 1:  # no sleep after the last try
                time.sleep(3 * (attempt + 1))
    else:
        raise UpstreamError(f"IRSA SIA search failed for {collection}: {last}")
    cols = ("obs_collection", "obs_id", "energy_bandpassname", "t_min", "t_max", "access_url",
            "cloud_access", "dataproduct_type", "dataproduct_subtype")
    has_region = "s_region" in t.colnames
    out = []
    for r in t:
        d = {}
        for c in cols:
            v = r[c]
            d[c] = None if np.ma.is_masked(v) else (float(v) if c in ("t_min", "t_max") else str(v))
        d["s_region"] = region_text(r["s_region"]) if has_region else None
        out.append(d)
    return out


class _Health:
    """Whether IRSA's search failed in the last SIA_DOWN_S seconds."""

    def __init__(self) -> None:
        self.until = 0.0

    def down(self) -> bool:
        return time.monotonic() < self.until

    def record(self, ok: bool) -> None:
        self.until = 0.0 if ok else time.monotonic() + SIA_DOWN_S


_sia = _Health()
_sia_pool = ThreadPoolExecutor(16, thread_name_prefix="sia")  # a search left behind finishes into the cache
_recent: dict[str, tuple[float, SearchResult]] = {}  # fallback results by cache key, FALLBACK_SHARE_S
_recent_lock = threading.Lock()


def fallback_enabled() -> bool:
    return os.environ.get("SKYSHIFT_SEARCH_FALLBACK", "1") != "0"


_key_locks: dict[str, threading.Lock] = {}
_key_locks_guard = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    with _key_locks_guard:
        if len(_key_locks) > 10_000:
            _key_locks.clear()
        return _key_locks.setdefault(key, threading.Lock())


def search(ra: float, dec: float, *, cache: Cache | None = None, refresh: bool = False) -> SearchResult:
    """Every DR1 + QR3 image covering (ra, dec), with visit and session numbers.
    Builds asking for one position at once (frames and object) share one search."""
    cache = cache or Cache()
    day = today()
    key = f"search/{pos_key(ra, dec)}/{day}.json"
    with _lock_for(key):
        return _search(ra, dec, cache, day, key, refresh)


def _search(ra: float, dec: float, cache: Cache, day: str, key: str, refresh: bool) -> SearchResult:
    raw = None if refresh else cache.get(key)
    legacy = None  # today's search, saved without footprints: asked again, used if IRSA fails
    if raw is not None:
        d = json.loads(raw)
        res = SearchResult(d["ra"], d["dec"], d["archive_checked"], [Image(**im) for im in d["images"]])
        if d.get("footprints") or not res.images:
            return res
        legacy = res
    if not fallback_enabled():
        try:
            return _irsa(ra, dec, cache, day, key)
        except Exception:  # noqa: BLE001 - IRSA's own earlier answer beats an error
            if legacy is None:
                raise
            log.warning("IRSA search failed at (%.5f, %.5f): today's saved search, without footprints", ra, dec)
            return legacy
    with _recent_lock:
        hit = _recent.get(key)
    if hit is not None and not refresh and time.monotonic() - hit[0] < FALLBACK_SHARE_S:
        return hit[1]
    if legacy is not None and _sia.down():  # no wait for an archive that just failed
        return legacy
    wait = SIA_WAIT_DOWN_S if _sia.down() else SIA_WAIT_S
    fut = _sia_pool.submit(_irsa, ra, dec, cache, day, key)
    fut.add_done_callback(lambda f: _sia.record(not f.cancelled() and f.exception() is None))
    try:
        return fut.result(timeout=wait)
    except FutureTimeout:
        why = f"no answer in {wait:.0f} s"
        _sia.record(False)
    except Exception as e:  # noqa: BLE001 - UpstreamError after the retries, or a malformed answer
        why = str(e)[:300]
    if legacy is not None:  # IRSA's own answer of today, without footprints: complete
        log.warning("IRSA search failed at (%.5f, %.5f): %s; today's saved search, without footprints",
                    ra, dec, why)
        return legacy
    log.warning("IRSA search failed at (%.5f, %.5f): %s; trying SkyShift's photo lists", ra, dec, why)
    from . import search_fallback

    got = search_fallback.find(ra, dec, cache)
    if got is None:
        raise UpstreamError(search_fallback.IRSA_DOWN)
    with _recent_lock:
        if len(_recent) > 1000:
            _recent.clear()
        _recent[key] = (time.monotonic(), got)
    return got


def _irsa(ra: float, dec: float, cache: Cache, day: str, key: str) -> SearchResult:
    """IRSA's answer for both collections, saved as the day's search."""
    with ThreadPoolExecutor(len(COLLECTIONS)) as ex:
        parts = list(ex.map(lambda c: _query_sia(ra, dec, c), COLLECTIONS))
    images = rows_to_images([r for p in parts for r in p])
    res = SearchResult(ra, dec, day, images)
    cache.put(key, json.dumps({"ra": ra, "dec": dec, "archive_checked": day, "footprints": True,
                               "images": [asdict(im) for im in images]}).encode(), "application/json")
    return res
