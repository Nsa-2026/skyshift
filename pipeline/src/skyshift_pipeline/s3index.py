"""Tool `s3index`: a photo index built from NASA's public S3 copy of SPHEREx, without IRSA.

IRSA's TAP and SIA were down on 14 Nov evening (502), and every zone's photo list came from TAP.
This lists every Level 2 file on s3://nasa-irsa-spherex
(<dr1|qr3>/level2/<period>/<version>/<detector>/level2_*.fits; qr2 and *_deep are not used) and reads
its primary and IMAGE headers with one ranged HTTPS GET. Each row carries the columns photolist.clean
makes from TAP (photolist.py), with TAP's values:
    obs_id      IMAGE OBSID ('2025W42_2A_0111_1')
    collection  'spherex_dr1' | 'spherex_qr3' (from the S3 prefix)
    t_min/t_max MJD-BEG / MJD-END, rounded to 6 decimals as TAP stores them
    s_ra/s_dec  the WCS (TAN-SIP) at the image centre, rounded to 8 decimals
    s_region    'POLYGON ICRS ra dec ...': the WCS at the four outer pixel corners, closed (5 points)
    size_bytes  the object size (S3 listing)
    detector    IMAGE DETECTOR (int8), s3_key, mjd = (t_min + t_max) / 2, url = S3 HTTPS + key
Checked against photolists/zones/pilot.parquet (TAP, 990 photos): every value equal, except that a third of the
s_region strings differ in a last digit (TAP's vertices are 1 ulp off ours: <= 2.1e-14 deg).
S3 holds 1,628,230 Level 2 files (14 Nov: dr1 1,179,406 in 110 periods, qr3 448,824 in 41; ~10,800 per period).
A period's '_retry' version directory holds other exposures, never a second copy (checked on every period).

Output, at the store root (shards by collection and planning period):
    photolists/s3index/<dr1|qr3>/<period>.parquet  every listed file (a reprocessed version is a separate row)
    photolists/s3index/<dr1|qr3>/<period>.json     sidecar: keys listed, rows, failed keys + errors, seconds
    photolists/s3index/_complete.json              written last, only when every period of every collection has a shard
A period whose shard exists is skipped unless --force; its failed keys (after 3 tries) are tried again.

Work: periods are spread over --procs processes (the WCS of a header costs ~2 ms of Python: threads alone
are GIL-bound), each reading with --workers / --procs threads.

photolist.run reads the shards when TAP fails (or SKYSHIFT_PHOTOLIST_SOURCE=s3index): zone_photos().
"""

from __future__ import annotations

import fnmatch
import math
import multiprocessing as mp
import re
import threading
import time
import warnings
import xml.etree.ElementTree as ET
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

from . import config as C
from . import log
from .sky import Footprint, angsep, gnomonic

L = log.get("s3index")

INDEX_DIR = "photolists/s3index"
COMPLETE_REL = f"{INDEX_DIR}/_complete.json"
PREFIXES = {"dr1": "spherex_dr1", "qr3": "spherex_qr3"}  # S3 prefix -> collection; same order as C.COLLECTIONS
COLUMNS = ["obs_id", "collection", "t_min", "t_max", "s_ra", "s_dec", "s_region", "size_bytes", "detector", "s3_key",
           "mjd", "url"]
HEADER_BYTES = 2880 * 12       # primary (1 block) + IMAGE header (~9 blocks); read more if END isn't there
HEADER_MAX_BYTES = 2880 * 400
READ_TRIES = 3
_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
# the cards the celestial WCS needs: astropy builds it 5x faster from these alone, with the same numbers
_WCS_KEY = re.compile(r"^(NAXIS\d?|WCSAXES|CTYPE[12]|CUNIT[12]|CRVAL[12]|CRPIX[12]|CDELT[12]|CD[12]_[12]|PC[12]_[12]"
                      r"|LONPOLE|LATPOLE|RADESYS|EQUINOX|(A|B|AP|BP)_\w+)$")


def shard_rel(prefix: str, period: str, ext: str = "parquet") -> str:
    return f"{INDEX_DIR}/{prefix}/{period}.{ext}"


# --- S3 over HTTPS (anonymous) ---------------------------------------------------------------
_tls = threading.local()


def _http():
    import requests
    from requests.adapters import HTTPAdapter

    s = getattr(_tls, "s", None)
    if s is None:
        s = requests.Session()
        s.mount("https://", HTTPAdapter(pool_connections=4, pool_maxsize=4))
        _tls.s = s
    return s


def _get(url: str, tries: int = READ_TRIES, **kw):
    last = None
    for attempt in range(1, tries + 1):
        try:
            r = _http().get(url, timeout=60, **kw)
            r.raise_for_status()
            return r
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < tries:
                time.sleep(attempt)
    raise last  # type: ignore[misc]


def s3_list(prefix: str, delimiter: str | None = None) -> tuple[list[tuple[str, int]], list[str]]:
    """(key, size) of every object under prefix, and the common prefixes (with a delimiter)."""
    keys: list[tuple[str, int]] = []
    prefixes: list[str] = []
    token = None
    while True:
        params = {"list-type": "2", "prefix": prefix}
        if delimiter:
            params["delimiter"] = delimiter
        if token:
            params["continuation-token"] = token
        root = ET.fromstring(_get(C.S3_HTTPS + "/", params=params).content)
        keys += [(c.findtext(f"{_NS}Key"), int(c.findtext(f"{_NS}Size"))) for c in root.iter(f"{_NS}Contents")]
        prefixes += [p.findtext(f"{_NS}Prefix") for p in root.iter(f"{_NS}CommonPrefixes")]
        if root.findtext(f"{_NS}IsTruncated") != "true":
            return keys, prefixes
        token = root.findtext(f"{_NS}NextContinuationToken")


def list_periods(prefix: str) -> list[str]:
    """Planning periods under <prefix>/level2/ ('2025W42_2A', ...)."""
    _, ps = s3_list(f"{prefix}/level2/", "/")
    out = [p.rstrip("/").rsplit("/", 1)[-1] for p in ps]
    return sorted(p for p in out if p and "deep" not in p.lower())


def list_period_keys(prefix: str, period: str) -> list[tuple[str, int]]:
    keys, _ = s3_list(f"{prefix}/level2/{period}/")
    return sorted((k, n) for k, n in keys if k.endswith(".fits") and k.rsplit("/", 1)[-1].startswith("level2_"))


# --- headers ---------------------------------------------------------------------------------
class _Short(Exception):
    """The ranged read ended before the IMAGE header did."""


def _card_value(card: str) -> str:
    """A numeric card's value text."""
    return card[10:].split("/", 1)[0].strip()


def image_header_text(data: bytes) -> str:
    """The IMAGE HDU's header (80-char cards up to END) from the first bytes of a FITS file."""
    pos = 0
    while True:
        end = None
        for i in range(pos, len(data) - 79, 80):
            if data[i:i + 8] == b"END     ":
                end = i
                break
        if end is None:
            raise _Short()
        text = data[pos:end + 80].decode("ascii", "replace")
        cards = [text[i:i + 80] for i in range(0, len(text), 80)]
        kv = {c[:8].strip(): c for c in cards if c[8:10] == "= "}
        ext = kv.get("EXTNAME", "")[10:].split("'")
        if len(ext) > 1 and ext[1].strip() == "IMAGE":
            return text
        # skip this HDU's data: |BITPIX|/8 * GCOUNT * (PCOUNT + NAXIS1 * ... * NAXISn), in 2880-byte blocks
        val = lambda k, d=0: int(float(_card_value(kv[k]))) if k in kv else d  # noqa: E731
        naxis = val("NAXIS")
        n = 0
        if naxis:
            n = int(np.prod([val(f"NAXIS{k}") for k in range(1, naxis + 1)], dtype=np.int64))
            n = abs(val("BITPIX", 8)) // 8 * val("GCOUNT", 1) * (val("PCOUNT") + n)
        hdr_end = pos + math.ceil((end + 80 - pos) / 2880) * 2880
        pos = hdr_end + math.ceil(n / 2880) * 2880
        if pos >= len(data):
            raise _Short()


def meta_from_header(text: str) -> dict:
    """Index columns (without collection, size, key) from the IMAGE header text."""
    from astropy.io import fits
    from astropy.wcs import WCS, FITSFixedWarning

    h = fits.Header.fromstring(text)
    wh = fits.Header([c for c in h.cards if _WCS_KEY.match(c.keyword)])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FITSFixedWarning)
        w = WCS(wh)
    nx, ny = int(h["NAXIS1"]), int(h["NAXIS2"])
    pix = np.array([[0.5, 0.5], [nx + 0.5, 0.5], [nx + 0.5, ny + 0.5], [0.5, ny + 0.5], [(nx + 1) / 2.0, (ny + 1) / 2.0]])
    rd = w.all_pix2world(pix, 1)
    pts = list(rd[:4]) + [rd[0]]
    return {
        "obs_id": str(h["OBSID"]).strip(),
        "t_min": round(float(h["MJD-BEG"]), 6), "t_max": round(float(h["MJD-END"]), 6),
        "s_ra": round(float(rd[4, 0]), 8), "s_dec": round(float(rd[4, 1]), 8),
        "s_region": "POLYGON ICRS " + " ".join(f"{float(a)!r} {float(b)!r}" for a, b in pts),
        "detector": int(h["DETECTOR"]),
    }


def read_meta(key: str) -> dict:
    """One ranged GET (more only for an unusually long header), then the header values."""
    n = HEADER_BYTES
    while True:
        r = _get(f"{C.S3_HTTPS}/{key}", headers={"Range": f"bytes=0-{n - 1}"})
        try:
            return meta_from_header(image_header_text(r.content))
        except _Short:
            if n >= HEADER_MAX_BYTES or len(r.content) < n:
                raise ValueError(f"no IMAGE header in the first {len(r.content)} bytes")
            n *= 4


def _one(key: str) -> tuple[str, dict | None, str | None]:
    err = None
    for attempt in range(1, READ_TRIES + 1):
        try:
            return key, read_meta(key), None
        except Exception as e:  # noqa: BLE001
            err = repr(e)[:300]
            if attempt < READ_TRIES:
                time.sleep(2 * attempt)
    return key, None, err


def rows_frame(prefix: str, metas: list[dict], sizes: dict[str, int]) -> pd.DataFrame:
    """Index rows in the photo-list column order and types (photolist.clean)."""
    if not metas:
        return empty_frame()
    m = pd.DataFrame(metas)
    df = pd.DataFrame({
        "obs_id": m["obs_id"].astype(str), "collection": PREFIXES[prefix],
        "t_min": m["t_min"].astype("float64"), "t_max": m["t_max"].astype("float64"),
        "s_ra": m["s_ra"].astype("float64"), "s_dec": m["s_dec"].astype("float64"),
        "s_region": m["s_region"].astype(str), "size_bytes": m["key"].map(sizes).astype("int64"),
        "detector": m["detector"].astype("int8"), "s3_key": m["key"].astype(str),
    })
    df["collection"] = df["collection"].astype(str)
    df["mjd"] = (df["t_min"] + df["t_max"]) / 2.0
    df["url"] = C.S3_HTTPS + "/" + df["s3_key"]
    return df.sort_values(["s3_key"]).reset_index(drop=True)[COLUMNS]


def empty_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "obs_id": pd.Series([], dtype=str), "collection": pd.Series([], dtype=str),
        "t_min": pd.Series([], dtype="float64"), "t_max": pd.Series([], dtype="float64"),
        "s_ra": pd.Series([], dtype="float64"), "s_dec": pd.Series([], dtype="float64"),
        "s_region": pd.Series([], dtype=str), "size_bytes": pd.Series([], dtype="int64"),
        "detector": pd.Series([], dtype="int8"), "s3_key": pd.Series([], dtype=str),
        "mjd": pd.Series([], dtype="float64"), "url": pd.Series([], dtype=str)})


def index_keys(prefix: str, keys: list[tuple[str, int]], threads: int) -> tuple[pd.DataFrame, list[dict]]:
    """Read the headers of these keys: (rows, failures [{key, error}])."""
    sizes = dict(keys)
    metas, failed = [], []
    with ThreadPoolExecutor(max(1, threads)) as ex:
        for key, m, err in ex.map(_one, [k for k, _ in keys]):
            if m is None:
                failed.append({"key": key, "error": err})
            else:
                metas.append({**m, "key": key})
    return rows_frame(prefix, metas, sizes), failed


def index_period(prefix: str, period: str, threads: int, only_keys: list[str] | None = None) -> tuple[pd.DataFrame, dict]:
    """One shard (in a worker process): list the period, read every header. only_keys: just these (a retry)."""
    t0 = time.time()
    keys = list_period_keys(prefix, period)
    if only_keys is not None:
        want = set(only_keys)
        keys = [(k, n) for k, n in keys if k in want]
    t_list = time.time() - t0
    df, failed = index_keys(prefix, keys, threads)
    info = {"collection": PREFIXES[prefix], "period": period, "keys": len(keys), "rows": int(len(df)),
            "failed": failed, "list_seconds": round(t_list, 1), "seconds": round(time.time() - t0, 1),
            "finished": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    return df, info


# --- the tool --------------------------------------------------------------------------------
def _match(period: str, patterns: list[str] | None) -> bool:
    return not patterns or any(fnmatch.fnmatchcase(period, p) for p in patterns)


def run(store, collections: tuple[str, ...] = ("dr1", "qr3"), workers: int = 64, procs: int | None = None,
        periods: list[str] | None = None, force: bool = False) -> dict:
    import os

    t0 = time.time()
    procs = max(1, min(procs or os.cpu_count() or 1, workers))
    threads = max(1, math.ceil(workers / procs))
    for c in collections:
        if c not in PREFIXES:
            raise ValueError(f"unknown collection {c!r}: use {', '.join(PREFIXES)}")
    listed = {c: list_periods(c) for c in collections}
    L.info("S3INDEX periods: %s; %d processes x %d threads", {c: len(v) for c, v in listed.items()}, procs, threads)
    tasks, skipped = [], 0
    for c in collections:
        for p in listed[c]:
            if not _match(p, periods):
                continue
            if not force and store.exists(shard_rel(c, p)) and store.exists(shard_rel(c, p, "json")):
                bad = [f["key"] for f in store.read_json(shard_rel(c, p, "json")).get("failed", [])]
                if bad:
                    tasks.append((c, p, bad))
                else:
                    skipped += 1
                continue
            tasks.append((c, p, None))
    L.info("S3INDEX %d shards to build (%d retries of failed keys), %d already done", len(tasks),
           sum(t[2] is not None for t in tasks), skipped)
    done_rows = done_keys = done_failed = 0
    for k, ((c, p, bad), res) in enumerate(_results(tasks, procs, threads), 1):
        if isinstance(res, Exception):  # listing failed: the shard stays missing, a rerun redoes it
            L.error("S3INDEX shard %s/%s failed: %r", c, p, res)
            continue
        df, info = res
        if bad is not None:  # retry: merge into the shard
            old = store.read_parquet(shard_rel(c, p))
            side = store.read_json(shard_rel(c, p, "json"))
            df = (pd.concat([old, df], ignore_index=True).drop_duplicates("s3_key", keep="last")
                  .sort_values("s3_key").reset_index(drop=True)[COLUMNS])
            info = {**side, "rows": int(len(df)), "failed": info["failed"], "retried": len(bad),
                    "retry_seconds": info["seconds"], "finished": info["finished"]}
        store.write_parquet(shard_rel(c, p), df)
        store.write_json(shard_rel(c, p, "json"), info)
        if bad is None:
            done_rows += len(df)
            done_keys += info["keys"]
        done_failed += len(info["failed"])
        el = time.time() - t0
        L.info("S3INDEX shard %s/%s keys=%d rows=%d failed=%d %.0fs | %d/%d shards, %d rows, %.0f files/s, %.0f s",
               c, p, info["keys"], info["rows"], len(info["failed"]), info.get("retry_seconds", info["seconds"]),
               k, len(tasks), done_rows, done_keys / max(el, 1e-9), el)
    status = write_complete(store, collections, listed)
    out = {"shards_built": len(tasks), "shards_skipped": skipped, "rows_built": done_rows, "failed_keys_left": done_failed,
           "complete": status["complete"], "seconds": round(time.time() - t0, 1)}
    L.info("S3INDEX end %s", out)
    return out


def _results(tasks, procs: int, threads: int):
    """((collection, period, retry keys), (rows, info) or the exception) as each shard finishes."""
    if procs == 1 or len(tasks) <= 1:  # in this process (tests, one period)
        for t in tasks:
            try:
                yield t, index_period(t[0], t[1], threads, t[2])
            except Exception as e:  # noqa: BLE001
                yield t, e
        return
    ctx = mp.get_context("spawn")
    with ProcessPoolExecutor(max_workers=min(procs, len(tasks)), mp_context=ctx, initializer=log.setup) as ex:
        futs = {ex.submit(index_period, c, p, threads, bad): (c, p, bad) for c, p, bad in tasks}
        for f in as_completed(futs):
            try:
                yield futs[f], f.result()
            except Exception as e:  # noqa: BLE001
                yield futs[f], e


def write_complete(store, collections, listed: dict[str, list[str]]) -> dict:
    """_complete.json when every listed period of every collection has a shard; totals from the sidecars."""
    missing = [f"{c}/{p}" for c in collections for p in listed[c]
               if not (store.exists(shard_rel(c, p)) and store.exists(shard_rel(c, p, "json")))]
    if missing:
        L.warning("S3INDEX incomplete: %d shards missing (%s ...); no _complete.json", len(missing), missing[:5])
        return {"complete": False, "missing": missing}
    have = store.read_json(COMPLETE_REL) if store.exists(COMPLETE_REL) else {}
    periods = {**have.get("periods", {}), **{c: listed[c] for c in collections}}
    rows = keys = 0
    failed = []
    for c, ps in periods.items():
        for p in ps:
            side = store.read_json(shard_rel(c, p, "json"))
            rows += side["rows"]
            keys += side["keys"]
            failed += [f["key"] for f in side.get("failed", [])]
    status = {"complete": True, "periods": periods, "shards": sum(len(v) for v in periods.values()), "keys": keys,
              "rows": rows, "failed_keys": len(failed), "failed_examples": failed[:20],
              "finished": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    store.write_json(COMPLETE_REL, status)
    L.info("S3INDEX complete: %d shards, %d keys, %d rows, %d failed keys", status["shards"], keys, rows, len(failed))
    return status


# --- zone photo lists from the index (photolist's fallback) ----------------------------------
def zone_box_intersects(fp: Footprint, zone: C.Zone) -> bool:
    """Do the footprint and the zone box (great-circle edges, as TAP's ADQL POLYGON) overlap?

    Both are convex; in the gnomonic projection about the footprint centre great circles are straight
    lines, so a separating-axis test on the projected vertices is exact."""
    zr = np.array([zone.ra_min, zone.ra_max, zone.ra_max, zone.ra_min])
    zd = np.array([zone.dec_min, zone.dec_min, zone.dec_max, zone.dec_max])
    zx, zy = gnomonic(fp.ra0, fp.dec0, zr, zd)
    if not (np.isfinite(zx).all() and np.isfinite(zy).all()):
        return bool(zone.contains(fp.vertices[:, 0], fp.vertices[:, 1]).any())  # zone reaches past 90° away
    polys = [np.column_stack([fp.px, fp.py]), np.column_stack([zx, zy])]
    for poly in polys:
        edges = np.roll(poly, -1, axis=0) - poly
        for ex_, ey_ in edges:
            n = np.array([-ey_, ex_])
            a, b = polys[0] @ n, polys[1] @ n
            if a.max() < b.min() or b.max() < a.min():
                return False
    return True


def _zone_radius_deg(zone: C.Zone) -> float:
    ra0, dec0 = zone.centre
    zr = [zone.ra_min, zone.ra_max, zone.ra_max, zone.ra_min]
    zd = [zone.dec_min, zone.dec_min, zone.dec_max, zone.dec_max]
    return float(np.max(angsep(ra0, dec0, zr, zd)))


def near_zone(df: pd.DataFrame, zone: C.Zone) -> np.ndarray:
    """Cheap cut on the photo centres: could the footprint reach the zone?"""
    ra0, dec0 = zone.centre
    return angsep(ra0, dec0, df["s_ra"].to_numpy(), df["s_dec"].to_numpy()) <= _zone_radius_deg(zone) + C.PHOTO_HALF_DIAG_DEG


def select_zone(df: pd.DataFrame, zone: C.Zone) -> pd.DataFrame:
    """Rows whose s_region footprint intersects the zone box."""
    df = df[near_zone(df, zone)]
    keep = np.array([zone_box_intersects(Footprint.from_s_region(s), zone) for s in df["s_region"]], dtype=bool)
    return df[keep].reset_index(drop=True)


def index_status(store) -> dict:
    """complete: _complete.json exists. Otherwise the shards present vs the periods on S3 now."""
    if store.exists(COMPLETE_REL):
        st = store.read_json(COMPLETE_REL)
        return {**st, "complete": True, "all_shards": True}
    shards = {c: sorted(x[:-len(".parquet")] for x in store.listdir(f"{INDEX_DIR}/{c}") if x.endswith(".parquet"))
              for c in PREFIXES}
    try:
        listed = {c: list_periods(c) for c in PREFIXES}
    except Exception as e:  # noqa: BLE001
        L.warning("S3 listing failed (%r): can't tell whether the index has every period", e)
        return {"complete": False, "all_shards": False, "periods": shards}
    missing = [f"{c}/{p}" for c in PREFIXES for p in listed[c] if p not in set(shards[c])]
    return {"complete": False, "all_shards": not missing, "missing": missing, "periods": listed}


def zone_photos(store, zone: C.Zone, workers: int = 16) -> tuple[pd.DataFrame, dict]:
    """The zone's photos from the index, as photolist.clean makes them from TAP (before add_sessions):
    footprint intersects the zone box, DR1 and QR3, one row per (obs_id, detector) with clean's preference.
    Raises if a period shard is missing."""
    from .photolist import dedupe

    t0 = time.time()
    st = index_status(store)
    if not st["all_shards"]:
        miss = st.get("missing")
        raise RuntimeError(f"s3index incomplete: {len(miss) if miss is not None else '?'} period shards missing "
                           f"({(miss or [])[:5]}); run `skyshift-pipeline s3index`")
    if not st["complete"]:
        L.warning("s3index has every period shard but no _complete.json (index job still running?): using it")
    rels = [shard_rel(c, p) for c in PREFIXES for p in st["periods"].get(c, [])]
    parts, failed = [], 0

    def load(rel: str) -> pd.DataFrame:
        return select_zone(store.read_parquet(rel), zone)

    with ThreadPoolExecutor(max(1, workers)) as ex:
        parts = list(ex.map(load, rels))
    if not st["complete"]:
        for rel in rels:
            j = rel[:-len(".parquet")] + ".json"
            if store.exists(j):
                failed += len(store.read_json(j).get("failed", []))
    else:
        failed = int(st.get("failed_keys", 0))
    parts = [p for p in parts if len(p)]
    raw = pd.concat(parts, ignore_index=True) if parts else empty_frame()
    photos, dd = dedupe(raw)
    stats = {"index_complete": bool(st["complete"]), "index_shards": len(rels), "index_failed_keys": failed,
             "index_rows_in_zone": int(len(raw)), **dd, "index_seconds": round(time.time() - t0, 1)}
    if failed:
        L.warning("s3index: %d files across the index could not be read; they are missing from every zone", failed)
    return photos, stats
