"""The forced-photometry target list for the whole sky, HEALPix-partitioned (contracts/dots.md, forced).

Layout (config FORCED_SKY_*):
    refcat/sky/forced_n32/p<pixel:05d>.parquet   one file per nested HEALPix pixel at nside 32 (12,288 of 3.36 deg²),
                                                 refcat.FORCED_COLUMNS, sorted by G
    refcat/sky/forced_n32/_complete.json         written last, once every partition exists: nside, limits, counts

Targets: Gaia DR3 G < FORCED_GAIA_G_MAX (the zone list's Gaia part; the CatWISE-only red objects are not in it
yet). A Gaia source belongs to the partition of its source_id (source_id // 2^35 is its level-12 nested HEALPix
pixel, so source_id // 2^49 is the nside-32 one): one ESA TAP sync query per partition on the primary key, no
spatial index needed. With FORCED_SKY_MAX_PER_DEG2 set, a partition keeps only its brightest
FORCED_SKY_MAX_PER_DEG2 × 3.357 deg² targets (TOP n ORDER BY G), which caps a photo near the 30,000 budget.

Build (resumable: finished partitions are skipped; parallel; any machine with internet):
    uv run python -m skyshift_pipeline.forced_sky --root gs://nasa-glazing-data --workers 16

Read (dots): `partitions_for_cone` gives the partitions a photo's cone overlaps; `SkyList.frame` returns
their rows (an LRU cache keeps recent partitions, photos come in time order so neighbours repeat).
"""

from __future__ import annotations

import argparse
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

from . import config as C
from . import log
from .refcat import ESA_TAP, FORCED_COLUMNS, GAIA_SELECT, IRSA_TAP, parse_gaia
from .store import Store

L = log.get("forced_sky")

SOURCE_ID_LEVEL12_SHIFT = 35  # Gaia: source_id // 2^35 = nested HEALPix index at level 12


def _level(nside: int) -> int:
    lev = int(round(np.log2(nside)))
    if 2 ** lev != nside:
        raise ValueError(f"nside {nside} is not a power of 2")
    return lev


def npix(nside: int = C.FORCED_SKY_NSIDE) -> int:
    return 12 * nside * nside


def area_deg2(nside: int = C.FORCED_SKY_NSIDE) -> float:
    return 4 * np.pi * (180.0 / np.pi) ** 2 / npix(nside)


def source_id_range(pix: int, nside: int = C.FORCED_SKY_NSIDE) -> tuple[int, int]:
    """[lo, hi) of Gaia source_ids whose level-12 HEALPix index falls in nested pixel `pix` at `nside`."""
    shift = SOURCE_ID_LEVEL12_SHIFT + 2 * (12 - _level(nside))
    return int(pix) << shift, (int(pix) + 1) << shift


def pixel_of_source_id(source_id, nside: int = C.FORCED_SKY_NSIDE) -> np.ndarray:
    shift = SOURCE_ID_LEVEL12_SHIFT + 2 * (12 - _level(nside))
    return np.asarray(source_id, dtype=np.int64) >> shift


def pixel_of(ra, dec, nside: int = C.FORCED_SKY_NSIDE) -> np.ndarray:
    import astropy.units as u
    from astropy_healpix import HEALPix

    h = HEALPix(nside=nside, order="nested")
    return np.asarray(h.lonlat_to_healpix(np.asarray(ra, float) * u.deg, np.asarray(dec, float) * u.deg), np.int64)


def partitions_for_cone(ra: float, dec: float, radius_deg: float, nside: int = C.FORCED_SKY_NSIDE,
                        pad_deg: float = C.FORCED_SKY_PAD_DEG) -> np.ndarray:
    """Sorted nested pixels overlapping the cone (radius + pad_deg, so that sources sitting a few arcsec
    outside their source_id pixel, or moved by proper motion, are still read)."""
    import astropy.units as u
    from astropy_healpix import HEALPix

    h = HEALPix(nside=nside, order="nested")
    pix = h.cone_search_lonlat(float(ra) * u.deg, float(dec) * u.deg, (float(radius_deg) + pad_deg) * u.deg)
    return np.unique(np.asarray(pix, np.int64))


def partition_rel(pix: int, root_dir: str = C.FORCED_SKY_DIR) -> str:
    return f"{root_dir}/p{int(pix):05d}.parquet"


def manifest_rel(root_dir: str = C.FORCED_SKY_DIR) -> str:
    return f"{root_dir}/{C.FORCED_SKY_MANIFEST}"


# --- build ---------------------------------------------------------------------------------------
def cap_per_partition(max_per_deg2: float | None = C.FORCED_SKY_MAX_PER_DEG2, nside: int = C.FORCED_SKY_NSIDE) -> int | None:
    return None if max_per_deg2 is None else int(np.floor(max_per_deg2 * area_deg2(nside)))


def gaia_adql(pix: int, table: str, g_max: float = C.FORCED_GAIA_G_MAX, cap: int | None = None,
              nside: int = C.FORCED_SKY_NSIDE) -> str:
    lo, hi = source_id_range(pix, nside)
    top = f"TOP {cap} " if cap else ""
    order = " ORDER BY phot_g_mean_mag, source_id" if cap else ""
    return (f"SELECT {top}{GAIA_SELECT} FROM {table} WHERE source_id >= {lo} AND source_id < {hi} "
            f"AND phot_g_mean_mag < {g_max}{order}")


def _tap(url: str, adql: str):
    """refcat._tap with a per-request timeout (C.FORCED_SKY_TIMEOUT_S), so one hung query of 12,288 is
    retried instead of blocking a worker forever: ESA sync, IRSA async."""
    import pyvo
    import requests

    class _Session(requests.Session):
        def request(self, *a, **k):
            k.setdefault("timeout", C.FORCED_SKY_TIMEOUT_S)
            return super().request(*a, **k)

    svc = pyvo.dal.TAPService(url, session=_Session())
    run = svc.run_sync if url == ESA_TAP else svc.run_async
    return run(adql, maxrec=C.REFCAT_MAXREC).to_table()


GAIA_TABLES = [("ESA Gaia", ESA_TAP, "gaiadr3.gaia_source"), ("IRSA Gaia", IRSA_TAP, "gaia_dr3_source")]


def fetch_partition(pix: int, g_max: float = C.FORCED_GAIA_G_MAX, cap: int | None = None,
                    nside: int = C.FORCED_SKY_NSIDE, tap=_tap) -> tuple[pd.DataFrame, dict]:
    """One partition's targets (FORCED_COLUMNS, sorted by G then source_id) and its stats. ESA first, then
    IRSA, NET_RETRIES tries each; raises if all fail."""
    last = None
    for name, url, table in GAIA_TABLES:
        adql = gaia_adql(pix, table, g_max, cap, nside)
        for attempt in range(1, C.NET_RETRIES + 1):
            try:
                t = tap(url, adql)
                if len(t) >= C.REFCAT_MAXREC:
                    raise RuntimeError(f"{len(t)} rows hit maxrec")
                g = parse_gaia(t)
                g = g.sort_values(["g_mag", "ref_id"], kind="stable").reset_index(drop=True)
                capped = cap is not None and len(g) >= cap
                st = {"pix": int(pix), "rows": int(len(g)), "capped": bool(capped),
                      "g_lim": float(g["g_mag"].max()) if capped else float(g_max), "service": name}
                return g[list(FORCED_COLUMNS)].astype(FORCED_COLUMNS).reset_index(drop=True), st
            except Exception as e:  # noqa: BLE001
                last = e
                L.warning("%s partition %d try %d: %s", name, pix, attempt, str(e)[:200])
                if attempt < C.NET_RETRIES:
                    time.sleep(5 * attempt)
    raise RuntimeError(f"partition {pix}: every service failed: {last}")


def build(store: Store, workers: int = C.FORCED_SKY_WORKERS, pixels=None, g_max: float = C.FORCED_GAIA_G_MAX,
          max_per_deg2: float | None = C.FORCED_SKY_MAX_PER_DEG2, nside: int = C.FORCED_SKY_NSIDE,
          root_dir: str = C.FORCED_SKY_DIR, tap=_tap, limit: int | None = None) -> dict:
    """Fetch every missing partition (or only `pixels`), then write the manifest if all npix exist.
    Partition stats are kept next to the files (cache-free: a rerun rereads the finished ones' row counts)."""
    t0 = time.time()
    cap = cap_per_partition(max_per_deg2, nside)
    allpix = np.arange(npix(nside)) if pixels is None else np.unique(np.asarray(pixels, np.int64))
    have = {n for n in store.listdir(root_dir) if n.startswith("p") and n.endswith(".parquet")}
    todo = [int(p) for p in allpix if f"p{int(p):05d}.parquet" not in have]
    if limit is not None:
        todo = todo[:limit]
    L.info("forced_sky: nside %d, G < %s, cap %s per partition, %d partitions asked, %d to fetch, %d workers",
           nside, g_max, cap, len(allpix), len(todo), workers)
    failed, rows, done = [], 0, 0

    def job(p):
        df, st = fetch_partition(p, g_max, cap, nside, tap)
        st["bytes"] = store.write_parquet(partition_rel(p, root_dir), df)
        return st

    with ThreadPoolExecutor(max(1, workers)) as ex:
        futs = {ex.submit(job, p): p for p in todo}
        for i, f in enumerate(as_completed(futs), 1):
            p = futs[f]
            try:
                st = f.result()
                rows += st["rows"]
                done += 1
                if i % 100 == 0 or i == len(todo):
                    el = time.time() - t0
                    L.info("forced_sky [%d/%d] %d rows so far, %.0f s, %.0f partitions/h", i, len(todo), rows, el,
                           i / max(el, 1e-9) * 3600)
            except Exception as e:  # noqa: BLE001
                failed.append(p)
                L.error("forced_sky partition %d failed: %s", p, str(e)[:300])
    out = {"fetched": done, "rows_fetched": rows, "failed": failed, "wall_s": round(time.time() - t0, 1)}
    have = set(store.listdir(root_dir))
    out["missing"] = sum(f"p{p:05d}.parquet" not in have for p in range(npix(nside)))
    if pixels is None and not failed and out["missing"] == 0:
        out["manifest"] = write_manifest(store, nside, g_max, max_per_deg2, root_dir)
    L.info("forced_sky: %s", {k: v for k, v in out.items() if k != "manifest"})
    if failed:
        raise RuntimeError(f"forced_sky: {len(failed)} partitions failed ({failed[:5]}...); rerun to retry them")
    return out


def write_manifest(store: Store, nside: int = C.FORCED_SKY_NSIDE, g_max: float = C.FORCED_GAIA_G_MAX,
                   max_per_deg2: float | None = C.FORCED_SKY_MAX_PER_DEG2, root_dir: str = C.FORCED_SKY_DIR,
                   catalogues: list[str] | None = None) -> dict:
    """Check every partition exists, count rows (Parquet footers only) and write the manifest."""
    import pyarrow.parquet as pq

    cap = cap_per_partition(max_per_deg2, nside)
    have = set(store.listdir(root_dir))
    missing = [p for p in range(npix(nside)) if f"p{p:05d}.parquet" not in have]
    if missing:
        raise RuntimeError(f"forced_sky: {len(missing)} partitions missing (e.g. {missing[:5]}), no manifest written")

    def nrows(p):
        with store.fs.open(store.path(partition_rel(p, root_dir)), "rb") as fh:
            return pq.ParquetFile(fh).metadata.num_rows

    with ThreadPoolExecutor(32) as ex:
        counts = np.array(list(ex.map(nrows, range(npix(nside)))), np.int64)
    man = {"nside": nside, "order": "nested", "partition": ("position (ra, dec)" if catalogues and "gaia_dr3" not in catalogues
                         else "gaia source_id >> 49 (level-12 HEALPix in source_id)"),
           "gaia_g_max": g_max, "max_per_deg2": max_per_deg2, "cap_per_partition": cap,
           "catalogues": catalogues or ["gaia_dr3"], "rows": int(counts.sum()),
           "capped_partitions": int((counts >= cap).sum()) if cap else 0,
           "rows_per_partition": counts.tolist(), "built": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    store.write_json(manifest_rel(root_dir), man)
    return {k: v for k, v in man.items() if k != "rows_per_partition"}


# --- CatWISE-only red objects (second list, refcat/sky/forced_cw_n32) ---------------------------
# CatWISE2020 W1 < FORCED_CATWISE_W1_MAX and W1 - W2 >= FORCED_CATWISE_W1_W2_MIN with no Gaia target of the Gaia sky
# list within FORCED_DEDUP_ARCSEC (the zone list dedups against every Gaia G < 20; measured in 6 zones, the G < 17.5
# dedup keeps 4-12% more: objects with a faint Gaia counterpart, which are not forced targets either way).
# One IRSA TAP query per nside-FORCED_SKY_CW_QUERY_NSIDE pixel (its RA/Dec bounding box, the colour cut done by IRSA),
# split into that pixel's nside-32 children. Needs the Gaia list complete (for the dedup).
CW_QUERY_NSIDE = C.FORCED_SKY_CW_QUERY_NSIDE


def cw_boxes(pix8: int, pad_deg: float = 0.01) -> list[tuple[float, float, float, float]]:
    """RA/Dec boxes (ra0, ra1, dec0, dec1), degrees, covering nested pixel `pix8` at CW_QUERY_NSIDE (1 or 2 boxes)."""
    import astropy.units as u
    from astropy_healpix import HEALPix

    h = HEALPix(nside=CW_QUERY_NSIDE, order="nested")
    lon, lat = h.boundaries_lonlat([pix8], step=32)
    lon, lat = lon.to_value(u.deg)[0], lat.to_value(u.deg)[0]
    clon = h.healpix_to_lonlat([pix8])[0].to_value(u.deg)[0]
    d0, d1 = max(lat.min() - pad_deg, -90.0), min(lat.max() + pad_deg, 90.0)
    for pole in (90.0, -90.0):
        if int(pixel_of(0.0, pole, CW_QUERY_NSIDE)) == pix8:
            return [(0.0, 360.0, min(d0, pole), max(d1, pole))]
    rel = (lon - clon + 180.0) % 360.0 - 180.0
    cosd = np.cos(np.deg2rad(min(max(abs(d0), abs(d1)), 89.0)))
    r0, r1 = clon + rel.min() - pad_deg / cosd, clon + rel.max() + pad_deg / cosd
    if r1 - r0 >= 360.0:
        return [(0.0, 360.0, d0, d1)]
    if r0 < 0:
        return [(r0 + 360.0, 360.0, d0, d1), (0.0, r1, d0, d1)]
    if r1 > 360:
        return [(r0, 360.0, d0, d1), (0.0, r1 - 360.0, d0, d1)]
    return [(r0, r1, d0, d1)]


def cw_adql(box) -> str:
    from .refcat import CATWISE_SELECT
    r0, r1, d0, d1 = box
    return (f"SELECT {CATWISE_SELECT} FROM catwise_2020 WHERE ra >= {r0:.6f} AND ra < {r1:.6f} AND dec >= {d0:.6f} "
            f"AND dec < {d1:.6f} AND w1mpro < {C.FORCED_CATWISE_W1_MAX} AND (w1mpro - w2mpro) >= {C.FORCED_CATWISE_W1_W2_MIN}")


def cw_children(pix8: int, nside: int = C.FORCED_SKY_NSIDE) -> list[int]:
    k = (nside // CW_QUERY_NSIDE) ** 2
    return list(range(pix8 * k, (pix8 + 1) * k))


def cw_neighbourhood(pix: int, nside: int = C.FORCED_SKY_NSIDE) -> list[int]:
    from astropy_healpix import HEALPix

    nb = HEALPix(nside=nside, order="nested").neighbours([pix])[:, 0]
    return sorted({int(pix)} | {int(p) for p in nb if p >= 0})


def cw_fetch(pix8: int, tap=_tap) -> pd.DataFrame:
    """CatWISE red objects in nested pixel pix8 at CW_QUERY_NSIDE as refcat rows; NET_RETRIES tries per box."""
    from .refcat import IRSA_TAP, parse_catwise

    parts = []
    for box in cw_boxes(pix8):
        for attempt in range(1, C.NET_RETRIES + 1):
            try:
                t = tap(IRSA_TAP, cw_adql(box))
                if len(t) >= C.REFCAT_MAXREC:
                    raise RuntimeError(f"{len(t)} rows hit maxrec")
                parts.append(parse_catwise(t))
                break
            except Exception as e:  # noqa: BLE001
                L.warning("CatWISE pix8 %d box %s try %d: %s", pix8, box, attempt, str(e)[:200])
                if attempt == C.NET_RETRIES:
                    raise
                time.sleep(5 * attempt)
    df = pd.concat(parts, ignore_index=True).drop_duplicates("ref_id")
    keep = pixel_of(df["ra"], df["dec"], CW_QUERY_NSIDE) == pix8
    return df[keep].reset_index(drop=True)


def cw_build(store: Store, workers: int = C.FORCED_SKY_CW_WORKERS, pixels8=None,
             max_per_deg2: float | None = C.FORCED_SKY_CW_MAX_PER_DEG2, nside: int = C.FORCED_SKY_NSIDE,
             gaia_dir: str = C.FORCED_SKY_DIR, root_dir: str = C.FORCED_SKY_CW_DIR, tap=_tap,
             limit: int | None = None) -> dict:
    """Fetch every query pixel (CW_QUERY_NSIDE) whose nside-32 children are not all written; dedup each child against the Gaia
    list (its partition and 8 neighbours); keep the brightest-in-W1 cap per child; manifest when complete."""
    from .refcat import catwise_without_gaia

    t0 = time.time()
    gaia = SkyList(store, gaia_dir, cache=64)
    if pixels8 is None and not gaia.available:
        raise RuntimeError(f"forced_sky catwise: the Gaia list {gaia_dir} is not complete (needed for the dedup)")
    cap = cap_per_partition(max_per_deg2, nside)
    all8 = range(12 * CW_QUERY_NSIDE ** 2) if pixels8 is None else [int(p) for p in pixels8]
    have = set(store.listdir(root_dir))
    todo = [p for p in all8 if not all(f"p{c:05d}.parquet" in have for c in cw_children(p, nside))]
    if limit is not None:
        todo = todo[:limit]
    L.info("forced_sky catwise: %d nside-%d pixels to fetch, cap %s per partition, %d workers",
           len(todo), CW_QUERY_NSIDE, cap, workers)

    def job(p8):
        df = cw_fetch(p8, tap)
        pix = pixel_of(df["ra"], df["dec"], nside)
        n = 0
        for c in cw_children(p8, nside):
            d = df[pix == c]
            if len(d):
                g = gaia.frame([q for q in cw_neighbourhood(c, nside) if store.exists(partition_rel(q, gaia_dir))])
                d = d[catwise_without_gaia(d, g)]
            d = d.sort_values(["w1_mag", "ref_id"], kind="stable")
            if cap is not None:
                d = d.head(cap)
            store.write_parquet(partition_rel(c, root_dir), d[list(FORCED_COLUMNS)].astype(FORCED_COLUMNS).reset_index(drop=True))
            n += len(d)
        return n

    failed, rows = [], 0
    with ThreadPoolExecutor(max(1, workers)) as ex:
        futs = {ex.submit(job, p): p for p in todo}
        for i, f in enumerate(as_completed(futs), 1):
            try:
                rows += f.result()
                if i % 20 == 0 or i == len(todo):
                    L.info("forced_sky catwise [%d/%d] %d rows, %.0f s", i, len(todo), rows, time.time() - t0)
            except Exception as e:  # noqa: BLE001
                failed.append(futs[f])
                L.error("forced_sky catwise pix8 %d failed: %s", futs[f], str(e)[:300])
    have = set(store.listdir(root_dir))
    out = {"fetched": len(todo) - len(failed), "rows": rows, "failed": failed, "wall_s": round(time.time() - t0, 1),
           "missing": sum(f"p{p:05d}.parquet" not in have for p in range(npix(nside)))}
    if pixels8 is None and not failed and out["missing"] == 0:
        out["manifest"] = write_manifest(store, nside, C.FORCED_GAIA_G_MAX, max_per_deg2, root_dir,
                                         catalogues=["catwise2020"])
    if failed:
        raise RuntimeError(f"forced_sky catwise: {len(failed)} pixels failed ({failed[:5]}...); rerun to retry them")
    return out


# --- read ----------------------------------------------------------------------------------------
class SkyList:
    """Read side for one store: is the list complete, and the rows of a set of partitions (LRU-cached)."""

    def __init__(self, store: Store, root_dir: str = C.FORCED_SKY_DIR, cache: int = C.FORCED_SKY_CACHE_PARTITIONS):
        self.store = store
        self.root_dir = root_dir
        self.cache_size = cache
        self._parts: OrderedDict[int, pd.DataFrame] = OrderedDict()
        self._lock = threading.Lock()
        self.manifest: dict | None = None
        if store.exists(manifest_rel(root_dir)):
            self.manifest = store.read_json(manifest_rel(root_dir))

    @property
    def available(self) -> bool:
        return self.manifest is not None

    @property
    def nside(self) -> int:
        return int(self.manifest["nside"]) if self.manifest else C.FORCED_SKY_NSIDE

    def partition(self, pix: int) -> pd.DataFrame:
        pix = int(pix)
        with self._lock:
            if pix in self._parts:
                self._parts.move_to_end(pix)
                return self._parts[pix]
        df = self.store.read_parquet(partition_rel(pix, self.root_dir))
        with self._lock:
            self._parts[pix] = df
            while len(self._parts) > self.cache_size:
                self._parts.popitem(last=False)
        return df

    def frame(self, pixels) -> pd.DataFrame:
        """Rows of these partitions, in pixel order (each partition sorted by G)."""
        parts = [self.partition(p) for p in sorted(int(p) for p in pixels)]
        if not parts:
            return pd.DataFrame({c: pd.Series(dtype=t) for c, t in FORCED_COLUMNS.items()})
        return pd.concat(parts, ignore_index=True)

    def frame_for_cone(self, ra: float, dec: float, radius_deg: float) -> tuple[pd.DataFrame, tuple[int, ...]]:
        pix = tuple(int(p) for p in partitions_for_cone(ra, dec, radius_deg, self.nside))
        return self.frame(pix), pix


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Build the HEALPix-partitioned forced-photometry list (Gaia DR3).")
    ap.add_argument("--root", default="gs://nasa-glazing-data")
    ap.add_argument("--workers", type=int, default=C.FORCED_SKY_WORKERS)
    ap.add_argument("--limit", type=int, default=None, help="fetch at most this many partitions (a test run)")
    ap.add_argument("--pixels", default=None,
                    help="comma list of nested pixels, nside 32 (FORCED_SKY_CW_QUERY_NSIDE with --catwise): a test run, no manifest")
    ap.add_argument("--no-cap", action="store_true", help="every G < limit target (no per-partition cap)")
    ap.add_argument("--manifest-only", action="store_true", help="only (re)write the manifest")
    ap.add_argument("--catwise", action="store_true",
                    help="build the CatWISE-only red list (refcat/sky/forced_cw_n32) instead; needs the Gaia list complete")
    a = ap.parse_args(argv)
    log.setup()
    store = Store(a.root)
    cap = None if a.no_cap else C.FORCED_SKY_MAX_PER_DEG2
    if a.catwise:
        cap = None if a.no_cap else C.FORCED_SKY_CW_MAX_PER_DEG2
        if a.manifest_only:
            print(write_manifest(store, max_per_deg2=cap, root_dir=C.FORCED_SKY_CW_DIR, catalogues=["catwise2020"]))
            return
        px = [int(p) for p in a.pixels.split(",")] if a.pixels else None
        print(cw_build(store, a.workers if a.workers != C.FORCED_SKY_WORKERS else C.FORCED_SKY_CW_WORKERS,
                       pixels8=px, max_per_deg2=cap, limit=a.limit))
        return
    if a.manifest_only:
        print(write_manifest(store, max_per_deg2=cap))
        return
    pixels = [int(p) for p in a.pixels.split(",")] if a.pixels else None
    out = build(store, a.workers, pixels=pixels, max_per_deg2=cap, limit=a.limit)
    print({k: v for k, v in out.items()})


if __name__ == "__main__":
    main()
