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
                   max_per_deg2: float | None = C.FORCED_SKY_MAX_PER_DEG2, root_dir: str = C.FORCED_SKY_DIR) -> dict:
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
    man = {"nside": nside, "order": "nested", "partition": "gaia source_id >> 49 (level-12 HEALPix in source_id)",
           "gaia_g_max": g_max, "max_per_deg2": max_per_deg2, "cap_per_partition": cap,
           "catalogues": ["gaia_dr3"], "rows": int(counts.sum()),
           "capped_partitions": int((counts >= cap).sum()) if cap else 0,
           "rows_per_partition": counts.tolist(), "built": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    store.write_json(manifest_rel(root_dir), man)
    return {k: v for k, v in man.items() if k != "rows_per_partition"}


# --- read ----------------------------------------------------------------------------------------
class SkyList:
    """Read side for one store: is the list complete, and the rows of a set of partitions (LRU-cached)."""

    def __init__(self, store: Store, root_dir: str = C.FORCED_SKY_DIR, cache: int = C.FORCED_SKY_CACHE_PARTITIONS):
        self.store = store
        self.root_dir = root_dir
        self.cache_size = cache
        self._parts: OrderedDict[int, pd.DataFrame] = OrderedDict()
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
        if pix in self._parts:
            self._parts.move_to_end(pix)
            return self._parts[pix]
        df = self.store.read_parquet(partition_rel(pix, self.root_dir))
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
    ap.add_argument("--pixels", default=None, help="comma list of nside-32 nested pixels (a test run; no manifest)")
    ap.add_argument("--no-cap", action="store_true", help="every G < limit target (no per-partition cap)")
    ap.add_argument("--manifest-only", action="store_true", help="only (re)write the manifest")
    a = ap.parse_args(argv)
    log.setup()
    store = Store(a.root)
    cap = None if a.no_cap else C.FORCED_SKY_MAX_PER_DEG2
    if a.manifest_only:
        print(write_manifest(store, max_per_deg2=cap))
        return
    pixels = [int(p) for p in a.pixels.split(",")] if a.pixels else None
    out = build(store, a.workers, pixels=pixels, max_per_deg2=cap, limit=a.limit)
    print({k: v for k, v in out.items()})


if __name__ == "__main__":
    main()
