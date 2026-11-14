"""Photo search without IRSA: SkyShift's own photo lists and saved searches (search.py's fallback).

IRSA's SIA and TAP answered "502 Proxy Error" on and off from 14 Nov 2026 16:05 UTC, and every
/api/frames or /api/object for a spot not searched before failed. Pixels never needed IRSA (cutouts
are range reads of NASA's S3 copy, cutouts.py), only the list of photos did. When the IRSA search
fails or does not answer in time, search.search asks find(), which tries in order:

1. A saved IRSA search at exactly this position from an earlier day (cache
   search/<ra>_<dec>/<day>.json): IRSA's own answer as of that day, complete. archive_checked is
   that day; no note.
2. The pipeline's photo lists, photolists/zones/<zone>.parquet (pipeline photolist.py: every DR1
   and QR3 photo whose footprint touches the zone's box, one row per (obs_id, detector), DR1 first,
   with its s_region, t_min, t_max, detector from energy_bandpassname, collection and S3 key). For
   every zone whose box contains the position, the rows whose footprint contains it. Inside a box
   the list has every photo there was when it was made: archive_checked is the list's date. Zone
   boxes come from skyrun/progress.json and results/<zone>/summary.json (measured.zones).
3. Outside every zone: the nearest saved IRSA search within NEAR_SAVED_DEG. A photo covering a
   point that close almost always covers this one; one that does not just gives no pixels.

Answers from 2 and 3 carry a note (NOTE_LISTS, or the saved search's distance): the engine marks
the answer partial with it (Budget.cut), so it is served with no-store and never cached as complete,
and the next request tries IRSA again. The rows go through search.rows_to_images like IRSA's, so
filters, the DR1 preference, the visit rule and the sort order are the same.

Memory: a zone's list is read once (only the columns used, about 6000 rows, 2-4 MB) and at most
ZONES_KEEP lists are kept per instance.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timezone
from typing import Any

import numpy as np

from .cache import Cache
from .search import Image, SearchResult, number_visits, pos_key, rows_to_images

log = logging.getLogger(__name__)

LISTS_PREFIX = "photolists/zones"
S3_BUCKET = "nasa-irsa-spherex"
S3_REGION = "us-east-1"
IRSA_DATA = "https://irsa.ipac.caltech.edu/ibe/data/spherex/"  # IRSA image URL = this + S3 key
COLUMNS = ["obs_id", "collection", "detector", "t_min", "t_max", "s_ra", "s_dec", "s_region", "s3_key"]
NEAR_CENTRE_DEG = 3.0  # a 3.5 deg photo reaches ~2.5 deg from its centre: rows tested for containment
NEAR_SAVED_DEG = 0.1  # outside the zones: a saved search this close stands in for this position
LISTS_TTL_S = 300.0  # the list of lists and of saved searches is read again after this long
ZONES_KEEP = 8
NOTE_LISTS = "search from SkyShift's photo lists; IRSA unavailable"
IRSA_DOWN = "IRSA (NASA's archive) is not answering right now; try a spot inside tonight's zones."


@dataclass(frozen=True)
class Box:
    """A zone's RA/Dec box (degrees, edges included)."""

    ra: tuple[float, float]
    dec: tuple[float, float]

    def contains(self, ra: float, dec: float) -> bool:
        if not self.dec[0] <= dec <= self.dec[1]:
            return False
        return any(self.ra[0] <= r <= self.ra[1] for r in (ra, ra + 360.0, ra - 360.0))


@dataclass
class ZoneList:
    name: str
    day: str  # YYYY-MM-DD the list was written: the date of the IRSA search it came from
    obs_id: np.ndarray
    collection: np.ndarray
    det: np.ndarray
    t_min: np.ndarray
    t_max: np.ndarray
    s_ra: np.ndarray
    s_dec: np.ndarray
    s_region: np.ndarray
    s3_key: np.ndarray

    def __len__(self) -> int:
        return len(self.obs_id)


def _store():
    from . import measured  # the data bucket (SKYSHIFT_DATA_ROOT) and its zone boxes

    return measured.store()


_lock = threading.Lock()
_lists: tuple[float, str, dict[str, str]] | None = None  # (read at, root, zone name -> YYYY-MM-DD)
_saved: tuple[float, str, list[tuple[float, float, str, str]]] | None = None  # (at, root, [(ra, dec, day, key)])
_boxes: tuple[float, str, dict[str, Any]] | None = None  # (read at, root, zone name -> box or None)
_tables: OrderedDict[tuple[str, str], ZoneList] = OrderedDict()
_table_locks: dict[tuple[str, str], threading.Lock] = {}


def reset() -> None:
    """For tests: forget every list read."""
    global _lists, _saved, _boxes
    with _lock:
        _lists, _saved, _boxes = None, None, None
        _tables.clear()
        _table_locks.clear()


def _day(mtime) -> str:
    if mtime is None:
        return time.strftime("%Y-%m-%d", time.gmtime())
    if mtime.tzinfo is not None:
        mtime = mtime.astimezone(timezone.utc)
    return mtime.strftime("%Y-%m-%d")


def photo_lists(st) -> dict[str, str]:
    """Zone name -> date of its photo list, for every photolists/zones/<zone>.parquet (cached)."""
    global _lists
    now = time.monotonic()
    with _lock:
        if _lists is not None and _lists[1] == st.root and now - _lists[0] < LISTS_TTL_S:
            return _lists[2]
    import pyarrow.fs as pafs

    try:
        infos = st.fs.get_file_info(pafs.FileSelector(st.path(LISTS_PREFIX), recursive=False))
    except FileNotFoundError:
        infos = []
    out = {i.base_name[: -len(".parquet")]: _day(i.mtime) for i in infos
           if i.type == pafs.FileType.File and i.base_name.endswith(".parquet")}
    with _lock:
        _lists = (now, st.root, out)
    return out


def zone_boxes(st, names) -> dict[str, Any]:
    """Zone name -> box (measured.Zone, with .contains) for the given names: skyrun/progress.json's
    zones (measured.zones), and for the others (pilot, t_<slug>) results/<zone>/summary.json, read
    here because a GCS listing does not show results/<zone>/ folders. Cached for LISTS_TTL_S."""
    global _boxes
    from . import measured

    now = time.monotonic()
    with _lock:
        if _boxes is not None and _boxes[1] == st.root and now - _boxes[0] < LISTS_TTL_S \
                and set(names) <= set(_boxes[2]):
            return _boxes[2]
    out: dict[str, Any] = {z.name: Box(z.ra, z.dec) for z in measured.zones(st) if z.name in names}

    def from_summary(n: str) -> Box | None:
        s = st.read_json(f"results/{n}/summary.json")
        z = s.get("zone") if isinstance(s, dict) else None
        try:
            return Box((float(z["ra"][0]), float(z["ra"][1])), (float(z["dec"][0]), float(z["dec"][1])))
        except (KeyError, TypeError, ValueError, IndexError):
            return None  # no box known: the list is not used

    rest = [n for n in names if n not in out]
    if rest:
        with ThreadPoolExecutor(min(8, len(rest))) as ex:
            out.update(zip(rest, ex.map(from_summary, rest)))
    with _lock:
        _boxes = (now, st.root, out)
    return out


def zones_with_lists(ra: float, dec: float, st) -> list[tuple[str, str]]:
    """(zone, list date) of every zone whose box contains (ra, dec) and that has a photo list."""
    lists = photo_lists(st)
    if not lists:
        return []
    boxes = zone_boxes(st, sorted(lists))
    return [(n, lists[n]) for n in sorted(lists) if boxes.get(n) is not None and boxes[n].contains(ra, dec)]


def zone_table(st, name: str, day: str) -> ZoneList:
    key = (st.root, name)
    with _lock:
        hit = _tables.get(key)
        if hit is not None and hit.day == day:
            _tables.move_to_end(key)
            return hit
        lock = _table_locks.setdefault(key, threading.Lock())
    with lock:  # one read per zone, even when several requests start together
        with _lock:
            hit = _tables.get(key)
        if hit is not None and hit.day == day:
            return hit
        t0 = time.perf_counter()
        t = st.read_table(f"{LISTS_PREFIX}/{name}.parquet", COLUMNS)
        if t is None:
            raise FileNotFoundError(f"{LISTS_PREFIX}/{name}.parquet")
        zl = ZoneList(name, day, t["obs_id"], t["collection"], np.asarray(t["detector"], np.int64),
                      np.asarray(t["t_min"], np.float64), np.asarray(t["t_max"], np.float64),
                      np.asarray(t["s_ra"], np.float64), np.asarray(t["s_dec"], np.float64),
                      t["s_region"], t["s3_key"])
        log.info("search fallback: photo list %s read in %.2f s (%d photos)", name,
                 time.perf_counter() - t0, len(zl))
        with _lock:
            _tables[key] = zl
            _tables.move_to_end(key)
            while len(_tables) > ZONES_KEEP:
                _tables.popitem(last=False)
        return zl


def angsep(ra1, dec1, ra2, dec2) -> np.ndarray:
    """Great-circle separation in degrees."""
    a1, d1, a2, d2 = (np.deg2rad(np.asarray(v, np.float64)) for v in (ra1, dec1, ra2, dec2))
    h = np.sin((d2 - d1) / 2) ** 2 + np.cos(d1) * np.cos(d2) * np.sin((a2 - a1) / 2) ** 2
    return np.rad2deg(2 * np.arcsin(np.sqrt(np.clip(h, 0, 1))))


def parse_polygon(s_region: str) -> np.ndarray:
    """'POLYGON ICRS ra dec ra dec ...' -> (n, 2) vertices (degrees)."""
    nums = []
    for p in str(s_region).replace(",", " ").split():
        try:
            nums.append(float(p))
        except ValueError:
            continue
    if len(nums) < 6 or len(nums) % 2:
        return np.zeros((0, 2))
    return np.array(nums, np.float64).reshape(-1, 2)


def contains(s_regions, ra: float, dec: float) -> np.ndarray:
    """Which footprints (s_region polygons) contain (ra, dec): a crossing test on the tangent plane
    at the position, so RA wrap and the poles need no care. Footprints must lie within 90 deg."""
    polys = [parse_polygon(s) for s in s_regions]
    out = np.zeros(len(polys), dtype=bool)
    if not polys:
        return out
    k = max(len(p) for p in polys)
    if k < 3:
        return out
    # pad with the last vertex: a zero-length edge never crosses
    v = np.stack([np.vstack([p, np.repeat(p[-1:], k - len(p), axis=0)]) if len(p) else np.full((k, 2), np.nan)
                  for p in polys])
    a0, d0 = math.radians(ra), math.radians(dec)
    a, d = np.deg2rad(v[..., 0]), np.deg2rad(v[..., 1])
    cosc = math.sin(d0) * np.sin(d) + math.cos(d0) * np.cos(d) * np.cos(a - a0)
    with np.errstate(divide="ignore", invalid="ignore"):
        x = np.cos(d) * np.sin(a - a0) / cosc
        y = (math.cos(d0) * np.sin(d) - math.sin(d0) * np.cos(d) * np.cos(a - a0)) / cosc
        x2, y2 = np.roll(x, -1, axis=1), np.roll(y, -1, axis=1)
        cross = (y > 0) != (y2 > 0)
        xc = x + (0.0 - y) * (x2 - x) / (y2 - y)
        inside = np.logical_xor.reduce(cross & (xc > 0), axis=1)
    ok = np.all(cosc > 0, axis=1) & np.all(np.isfinite(x), axis=1)
    return inside & ok


def sia_row(obs_id: str, collection: str, det: int, t_min: float, t_max: float, s3_key: str) -> dict[str, Any]:
    """A photo-list row in IRSA SIA's shape (search._query_sia), for search.rows_to_images.
    The detector was read from energy_bandpassname when the list was made (photolist.clean)."""
    return {
        "obs_collection": collection, "obs_id": obs_id, "energy_bandpassname": f"SPHEREx-D{int(det)}",
        "t_min": float(t_min), "t_max": float(t_max), "access_url": IRSA_DATA + s3_key,
        "cloud_access": json.dumps({"aws": {"bucket_name": S3_BUCKET, "region": S3_REGION, "key": s3_key}}),
        "dataproduct_type": "image", "dataproduct_subtype": "science",
    }


def from_lists(ra: float, dec: float, st) -> tuple[list[dict[str, Any]], list[str], str] | None:
    """(SIA-shaped rows, zones used, newest list date) from every photo list whose zone box contains
    the position; None when no zone covers it."""
    zones = zones_with_lists(ra, dec, st)
    if not zones:
        return None
    rows: list[dict[str, Any]] = []
    for name, day in zones:
        zl = zone_table(st, name, day)
        near = np.flatnonzero(angsep(zl.s_ra, zl.s_dec, ra, dec) < NEAR_CENTRE_DEG)
        inside = near[contains(zl.s_region[near], ra, dec)]
        rows += [sia_row(str(zl.obs_id[i]), str(zl.collection[i]), int(zl.det[i]), float(zl.t_min[i]),
                         float(zl.t_max[i]), str(zl.s3_key[i])) for i in inside if zl.s3_key[i]]
    return rows, [n for n, _ in zones], max(d for _, d in zones)


def saved_searches(cache: Cache) -> list[tuple[float, float, str, str]]:
    """Every saved IRSA search in the cache: (ra, dec, day, key), cached for LISTS_TTL_S."""
    global _saved
    now = time.monotonic()
    with _lock:
        if _saved is not None and _saved[1] == cache.root and now - _saved[0] < LISTS_TTL_S:
            return _saved[2]
    out = []
    for key in cache.keys("search/"):
        parts = key.split("/")
        if len(parts) != 3 or not parts[2].endswith(".json"):
            continue
        try:
            r, d = parts[1].split("_")
            out.append((float(r), float(d), parts[2][:-5], key))
        except ValueError:
            continue
    with _lock:
        _saved = (now, cache.root, out)
    return out


def _load_saved(cache: Cache, key: str) -> dict | None:
    raw = cache.get(key)
    return None if raw is None else json.loads(raw)


def from_saved(ra: float, dec: float, cache: Cache, exact_only: bool) -> SearchResult | None:
    """The latest saved IRSA search at this position (no note), or, unless exact_only, the
    nearest within NEAR_SAVED_DEG (noted with its distance)."""
    saved = saved_searches(cache)
    if not saved:
        return None
    here = pos_key(ra, dec)
    exact = sorted((s for s in saved if pos_key(s[0], s[1]) == here), key=lambda s: s[2])
    if exact:
        d = _load_saved(cache, exact[-1][3])
        if d is not None:
            return SearchResult(ra, dec, d["archive_checked"], [Image(**im) for im in d["images"]])
    if exact_only:
        return None
    sep = angsep(np.array([s[0] for s in saved]), np.array([s[1] for s in saved]), ra, dec)
    order = [i for i in np.argsort(sep) if sep[i] <= NEAR_SAVED_DEG]
    if not order:
        return None
    best = saved[order[0]]
    same = sorted((s for s in saved if s[0] == best[0] and s[1] == best[1]), key=lambda s: s[2])
    d = _load_saved(cache, same[-1][3])
    if d is None:
        return None
    images = [Image(**{**im, "visit": 0, "session": 0}) for im in d["images"]]
    images = number_visits(sorted(images, key=lambda im: (im.mjd, im.det)))
    arcmin = float(sep[order[0]]) * 60.0
    note = f"search from a saved IRSA search {arcmin:.1f} arcmin away; IRSA unavailable"
    return SearchResult(ra, dec, d["archive_checked"], images, note=note)


def warm_up_in_background(cache: Cache) -> None:
    """Read the list of photo lists, the zone boxes and the saved searches in a thread at startup, so
    the first fallback only reads its zone's list (about 1 s instead of 5-10 s)."""
    def run() -> None:
        try:
            st = _store()
            zone_boxes(st, sorted(photo_lists(st)))
            saved_searches(cache)
        except Exception:  # noqa: BLE001 - never matters for startup
            log.exception("search fallback warm-up failed")

    threading.Thread(target=run, name="fallback-warm-up", daemon=True).start()


def find(ra: float, dec: float, cache: Cache, *, st=None) -> SearchResult | None:
    """Every DR1 + QR3 image covering (ra, dec) without asking IRSA, or None if we cannot tell
    (outside every zone, no saved search here or near). Never raises."""
    t0 = time.perf_counter()
    try:
        got = from_saved(ra, dec, cache, exact_only=True)
        if got is not None:
            log.warning("search fallback (%.5f, %.5f): saved IRSA search of %s, %d images, %.2f s",
                        ra, dec, got.archive_checked, len(got.images), time.perf_counter() - t0)
            return got
    except Exception:  # noqa: BLE001 - the cache listing failed: try the lists
        log.exception("search fallback: saved searches unreadable")
    try:
        st = st or _store()
        lists = from_lists(ra, dec, st)
    except Exception:  # noqa: BLE001 - the bucket failed: try a nearby saved search
        log.exception("search fallback: photo lists unreadable")
        lists = None
    if lists is not None:
        rows, zones, day = lists
        images = rows_to_images(rows)
        log.warning("search fallback (%.5f, %.5f): photo lists %s, %d images, %.2f s",
                    ra, dec, ",".join(zones), len(images), time.perf_counter() - t0)
        return SearchResult(ra, dec, day, images, note=NOTE_LISTS)
    try:
        got = from_saved(ra, dec, cache, exact_only=False)
    except Exception:  # noqa: BLE001
        log.exception("search fallback: saved searches unreadable")
        got = None
    if got is not None:
        log.warning("search fallback (%.5f, %.5f): %s, %d images", ra, dec, got.note, len(got.images))
    return got
