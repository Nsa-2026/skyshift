"""Step 2, `dots`: every 5σ point in every photo, one Parquet per photo (contracts/dots.md).

Per photo: stream IMAGE, FLAGS, VARIANCE and WCS-WAVE from the public S3 copy (never saved),
core.detect at 5σ (bad-pixel mask of contracts/flags.md, SOURCE never masked), then sky position,
µJy, wavelength, flag bits under the dot, session and visit from its tile, and the 6″ cell.
One process per core; a photo whose file exists is skipped; a photo that keeps failing is logged
and skipped.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
import pandas as pd
from skyshift_core import build_mask, detect, flags_under, open_level2, pixel_to_sky, ujy_per_mjysr

from . import config as C
from . import log
from .sky import cell_of, tile_of
from .store import Store, dots_rel, photolist_rel, results_rel

L = log.get("dots")

DOT_COLUMNS = {
    "obs_id": "string", "detector": "int8", "mjd": "float64", "session": "int32", "visit": "int16",
    "ra": "float64", "dec": "float64", "pos_err_arcsec": "float32", "x": "float32", "y": "float32",
    "flux_ujy": "float32", "flux_err_ujy": "float32", "wavelength_um": "float32", "bandwidth_um": "float32",
    "elong": "float32", "snr": "float32", "flags": "int32", "cell": "int64",
}


def empty_dots() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype=t) for c, t in DOT_COLUMNS.items()})


def _flags_per_dot(flags: np.ndarray, x: np.ndarray, y: np.ndarray, r: float) -> np.ndarray:
    """core.flags_under on a small stamp around each dot (the full-frame version is O(frame))."""
    ny, nx = flags.shape
    h = int(np.ceil(r)) + 1
    out = np.zeros(x.size, dtype=np.int64)
    for k, (xi, yi) in enumerate(zip(x, y)):
        ix, iy = int(round(xi)), int(round(yi))
        x0, y0 = max(ix - h, 0), max(iy - h, 0)
        x1, y1 = min(ix + h + 1, nx), min(iy + h + 1, ny)
        if x1 <= x0 or y1 <= y0:
            continue
        out[k] = flags_under(flags[y0:y1, x0:x1], xi - x0, yi - y0, r)
    return out


def pos_err_arcsec(snr: np.ndarray) -> np.ndarray:
    """contracts/dots.md: sqrt((1.4 × 3.2″ / snr)² + 0.25″²); NaN where snr <= 0."""
    snr = np.asarray(snr, np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        e = np.sqrt((C.POS_ERR_K * C.PSF_SIGMA_ARCSEC / snr) ** 2 + C.POS_ERR_FLOOR_ARCSEC**2)
    return np.where(snr > 0, e, np.nan)


def elongation(data: np.ndarray, bad: np.ndarray, x: np.ndarray, y: np.ndarray,
               r: float = C.ELONG_R_PX, bkg: tuple[float, float] = C.ELONG_BKG_PX) -> np.ndarray:
    """a/b of each dot from intensity-weighted second moments (good pixels within r px, minus the
    median of a local annulus). core.detect does not return sep's a and b, so this stands in for
    them: ~1 for a point source, large for trails and streaks. NaN if there is too little signal."""
    ny, nx = data.shape
    h = int(np.ceil(bkg[1]))
    yy, xx = np.mgrid[-h:h + 1, -h:h + 1]
    out = np.full(x.size, np.nan, dtype=np.float64)
    for k, (xi, yi) in enumerate(zip(x, y)):
        ix, iy = int(round(xi)), int(round(yi))
        if ix - h < 0 or iy - h < 0 or ix + h + 1 > nx or iy + h + 1 > ny:
            continue
        st = data[iy - h:iy + h + 1, ix - h:ix + h + 1].astype(np.float64)
        good = ~bad[iy - h:iy + h + 1, ix - h:ix + h + 1] & np.isfinite(st)
        dx, dy = xx - (xi - ix), yy - (yi - iy)
        rr = dx * dx + dy * dy
        ann = good & (rr >= bkg[0] ** 2) & (rr <= bkg[1] ** 2)
        if ann.sum() < 5:
            continue
        w = np.where(good & (rr <= r * r), st - np.median(st[ann]), 0.0)
        w = np.clip(w, 0.0, None)
        tot = w.sum()
        if tot <= 0:
            continue
        cxx = (w * dx * dx).sum() / tot
        cyy = (w * dy * dy).sum() / tot
        cxy = (w * dx * dy).sum() / tot
        tr, det = cxx + cyy, cxx * cyy - cxy * cxy
        disc = np.sqrt(max(tr * tr / 4.0 - det, 0.0))
        l1, l2 = tr / 2.0 + disc, tr / 2.0 - disc
        out[k] = np.sqrt(l1 / l2) if l2 > 1e-6 else 99.0
    return np.minimum(out, 99.0)


def session_visit(tiles: np.ndarray, tile_sv: dict[int, tuple[int, int]]) -> np.ndarray:
    """(n, 2) session, visit per tile id from a {tile: (session, visit)} map; (0, 0) if absent."""
    ut, inv = np.unique(np.asarray(tiles, np.int64), return_inverse=True)
    m = np.array([tile_sv.get(int(t), (0, 0)) for t in ut], dtype=np.int64).reshape(-1, 2)
    return m[inv.ravel()] if ut.size else np.zeros((0, 2), dtype=np.int64)


def dots_table(l2, obs_id: str, detector: int, tile_sv: dict[int, tuple[int, int]],
               thresh: float = C.DETECT_SIGMA) -> pd.DataFrame:
    """The dot table of one opened Level 2 photo (or cutout). tile_sv: tile -> (session, visit)."""
    det = detect(l2.data, l2.flags, variance=l2.variance, thresh=thresh)
    if len(det) == 0:
        return empty_dots()
    x, y = det["x"], det["y"]
    ra, dec = pixel_to_sky(l2.wcs, x, y)
    conv = ujy_per_mjysr(l2.wcs)
    tiles = tile_of(ra, dec)
    sv = session_visit(tiles, tile_sv)
    if l2.wave is not None:
        wl, bw = l2.wave.wavelength(x, y), l2.wave.bandwidth(x, y)
    else:
        wl = bw = np.full(x.size, np.nan)
    flg = _flags_per_dot(l2.flags, x, y, C.FLAGS_UNDER_R_PX)
    elong = elongation(np.asarray(l2.data), build_mask(l2.data, l2.flags), x, y)
    df = pd.DataFrame({
        "obs_id": obs_id,
        "detector": detector,
        "mjd": l2.mjd_avg,
        "session": sv[:, 0],
        "visit": sv[:, 1],
        "ra": ra % 360.0,
        "dec": dec,
        "pos_err_arcsec": pos_err_arcsec(det["snr"]),
        "x": x,
        "y": y,
        "flux_ujy": det["flux"] * conv,
        "flux_err_ujy": det["flux_err"] * conv,
        "wavelength_um": wl,
        "bandwidth_um": bw,
        "elong": elong,
        "snr": det["snr"],
        "flags": (flg & 0xFFFFFFFF).astype(np.uint32).view(np.int32),
        "cell": cell_of(ra % 360.0, dec),
    })
    return df.astype(DOT_COLUMNS)


# --- worker ------------------------------------------------------------------------------------
_STORE: Store | None = None


def _store(root: str) -> Store:
    global _STORE
    if _STORE is None or _STORE.root != root.rstrip("/"):
        _STORE = Store(root)
    return _STORE


def process_photo(root: str, obs_id: str, detector: int, url: str, tile_sv: dict[int, tuple[int, int]]) -> dict:
    """Do one photo. Returns a stats dict; never raises."""
    store = _store(root)
    rel = dots_rel(obs_id, detector)
    st = {"obs_id": obs_id, "detector": int(detector), "status": "", "n_dots": 0, "read_s": 0.0,
          "detect_s": 0.0, "write_s": 0.0, "bytes": 0, "error": ""}
    try:
        if store.exists(rel):
            st["status"] = "skipped"
            return st
    except Exception as e:  # noqa: BLE001
        st["error"] = f"exists check: {e}"
    for attempt in range(1, C.NET_RETRIES + 1):
        try:
            t0 = time.time()
            l2 = open_level2(url, variance=True)
            st["read_s"] = round(time.time() - t0, 2)
            break
        except Exception as e:  # noqa: BLE001 - network or decode errors: retry, then skip
            st["error"] = f"{type(e).__name__}: {e}"[:300]
            if attempt == C.NET_RETRIES:
                st["status"] = "failed"
                return st
            time.sleep(5 * attempt)
    try:
        if int(l2.detector) != int(detector):
            raise ValueError(f"header DETECTOR {l2.detector} != photo list {detector}")
        t0 = time.time()
        df = dots_table(l2, obs_id, detector, tile_sv)
        st["detect_s"] = round(time.time() - t0, 2)
        st["n_dots"] = int(len(df))
        t0 = time.time()
        for attempt in range(1, C.NET_RETRIES + 1):
            try:
                st["bytes"] = store.write_parquet(rel, df)
                break
            except Exception:  # noqa: BLE001
                if attempt == C.NET_RETRIES:
                    raise
                time.sleep(5 * attempt)
        st["write_s"] = round(time.time() - t0, 2)
        st["status"] = "done"
        st["error"] = ""
    except Exception as e:  # noqa: BLE001
        st["status"] = "failed"
        st["error"] = f"{type(e).__name__}: {e}"[:300]
    return st


def tile_lookup(tilemap: pd.DataFrame) -> dict[tuple[str, int], dict[int, tuple[int, int]]]:
    """(obs_id, detector) -> {tile: (session, visit)}."""
    out: dict[tuple[str, int], dict[int, tuple[int, int]]] = {}
    for (o, d), g in tilemap.groupby(["obs_id", "detector"], sort=False):
        out[(str(o), int(d))] = dict(zip(g["tile"].astype(int), zip(g["session"].astype(int), g["visit"].astype(int))))
    return out


def run(store: Store, zone: C.Zone, workers: int, limit: int | None = None) -> dict:
    t_start = time.time()
    photos = store.read_parquet(photolist_rel(zone.name)).sort_values(["mjd", "detector"]).reset_index(drop=True)
    tilemap = store.read_parquet(photolist_rel(zone.name, "tilemap"))
    lookup = tile_lookup(tilemap)
    done = {n for n in store.listdir("dots") if n.endswith(".parquet")}
    todo = [r for r in photos.itertuples() if f"{r.obs_id}D{int(r.detector)}.parquet" not in done]
    n_done_before = len(photos) - len(todo)
    if limit is not None:
        todo = todo[:limit]
    L.info("dots %s: %d photos, %d already done, %d to do now, %d workers", zone.name, len(photos), n_done_before, len(todo), workers)
    stats: list[dict] = []
    if todo:
        ctx = mp.get_context("spawn")
        with ProcessPoolExecutor(max_workers=max(1, workers), mp_context=ctx, initializer=log.setup) as ex:
            futs = [ex.submit(process_photo, store.root, r.obs_id, int(r.detector), r.url,
                              lookup.get((r.obs_id, int(r.detector)), {})) for r in todo]
            for k, f in enumerate(as_completed(futs), 1):
                st = f.result()
                stats.append(st)
                if st["status"] == "failed":
                    L.warning("[%d/%d] %sD%d FAILED: %s", k, len(todo), st["obs_id"], st["detector"], st["error"])
                else:
                    L.info("[%d/%d] %sD%d %s: %d dots, read %.1f s, detect %.1f s, write %.1f s, %.0f KB",
                           k, len(todo), st["obs_id"], st["detector"], st["status"], st["n_dots"],
                           st["read_s"], st["detect_s"], st["write_s"], st["bytes"] / 1e3)
    done_now = [s for s in stats if s["status"] == "done"]
    failed = [s for s in stats if s["status"] == "failed"]
    wall = time.time() - t_start
    out = {
        "photos": int(len(photos)), "done_before": n_done_before, "done_now": len(done_now),
        "failed": len(failed), "failed_photos": [f"{s['obs_id']}D{s['detector']}" for s in failed][:50],
        "remaining": int(len(photos) - n_done_before - len(done_now)),
        "wall_s": round(wall, 1), "workers": workers,
    }
    if done_now:
        a = pd.DataFrame(done_now)
        out.update({
            "dots_per_photo_median": float(a["n_dots"].median()),
            "dots_total_now": int(a["n_dots"].sum()),
            "read_s_median": float(a["read_s"].median()),
            "detect_s_median": float(a["detect_s"].median()),
            "kb_per_photo_median": round(float(a["bytes"].median()) / 1e3, 1),
            "photos_per_minute": round(len(done_now) / wall * 60.0, 1),
        })
    if stats:
        store.write_json(results_rel(zone.name, f"dots_runs/{time.strftime('%Y%m%dT%H%M%S')}.json"),
                         {"summary": out, "photos": stats})
    L.info("dots %s: %s", zone.name, {k: v for k, v in out.items() if k != "failed_photos"})
    return out


def cpu_count() -> int:
    return os.cpu_count() or 1
