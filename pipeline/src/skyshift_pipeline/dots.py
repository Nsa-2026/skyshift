"""Step 2, `dots`: every 5σ point in every photo, one Parquet per photo (contracts/dots.md).

Per photo: stream IMAGE, FLAGS, VARIANCE and WCS-WAVE from the public S3 copy (never saved),
core.detect with thresh=DETECT_THRESH per pixel (bad-pixel mask of contracts/flags.md, SOURCE never
masked), keep aperture snr >= DOT_MIN_SNR; saturated dots (OVERFLOW/NONLINEAR under them) are
re-centroided on their wings (core.centroid method="fit"); then sky position, µJy, wavelength,
bandwidth, elongation, the flag bits in the 3×3 pixels under the dot (`flags`) and at its peak pixel
(`flags_peak`), the photo's session and visit, and the 6″ cell. Photos with FINAST != 0 (bad
astrometry) get an empty dot file.
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
from skyshift_core import SAT_BITS, build_mask, centroid, detect, open_level2, pixel_to_sky, ujy_per_mjysr

from . import config as C
from . import log
from .sky import cell_of
from .store import Store, dots_rel, photolist_rel, results_rel

L = log.get("dots")

DOT_COLUMNS = {
    "obs_id": "string", "detector": "int8", "mjd": "float64", "session": "int32", "visit": "int16",
    "ra": "float64", "dec": "float64", "pos_err_arcsec": "float32", "x": "float32", "y": "float32",
    "flux_ujy": "float32", "flux_err_ujy": "float32", "wavelength_um": "float32", "bandwidth_um": "float32",
    "elong": "float32", "snr": "float32", "flags": "int32", "flags_peak": "int32", "cell": "int64",
}


def empty_dots() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype=t) for c, t in DOT_COLUMNS.items()})


def or3x3(flags: np.ndarray) -> np.ndarray:
    """OR of each pixel's 3×3 neighbourhood (the r = 1.5 px disk of core.flags_under), whole frame."""
    f = np.asarray(flags).astype(np.int32, copy=False)
    ny, nx = f.shape
    p = np.pad(f, 1)
    out = np.zeros_like(f)
    for dy in range(3):
        for dx in range(3):
            out |= p[dy:dy + ny, dx:dx + nx]
    return out


def pixel_of(x: np.ndarray, y: np.ndarray, shape: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    ix = np.clip(np.rint(x).astype(np.int64), 0, shape[1] - 1)
    iy = np.clip(np.rint(y).astype(np.int64), 0, shape[0] - 1)
    return ix, iy


def peak_pixel(data: np.ndarray, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Brightest pixel in the 3×3 around each dot's pixel."""
    ny, nx = data.shape
    ix, iy = pixel_of(x, y, data.shape)
    best = np.full(ix.size, -np.inf)
    bx, by = ix.copy(), iy.copy()
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            jx, jy = np.clip(ix + dx, 0, nx - 1), np.clip(iy + dy, 0, ny - 1)
            v = np.nan_to_num(data[jy, jx].astype(np.float64), nan=-np.inf)
            better = v > best
            best = np.where(better, v, best)
            bx, by = np.where(better, jx, bx), np.where(better, jy, by)
    return bx, by


def recentroid_saturated(data: np.ndarray, flags: np.ndarray, x: np.ndarray, y: np.ndarray,
                         sat: np.ndarray, half: int = C.SAT_RECENTROID_HALF_PX) -> tuple[np.ndarray, np.ndarray, int]:
    """Wings fit (core.centroid method="fit") on a stamp for the dots flagged saturated."""
    x, y = x.copy(), y.copy()
    ny, nx = data.shape
    n = 0
    for k in np.flatnonzero(sat):
        ix, iy = int(round(x[k])), int(round(y[k]))
        x0, y0 = max(ix - half, 0), max(iy - half, 0)
        x1, y1 = min(ix + half + 1, nx), min(iy + half + 1, ny)
        if x1 - x0 < 9 or y1 - y0 < 9:
            continue
        try:
            c = centroid(data[y0:y1, x0:x1], x[k] - x0, y[k] - y0, flags=flags[y0:y1, x0:x1],
                         method="fit", box=min(x1 - x0, y1 - y0) // 2)
        except Exception:  # noqa: BLE001 - keep the original centroid
            c = None
        if c is not None:
            x[k], y[k] = c[0] + x0, c[1] + y0
            n += 1
    return x, y, n


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


def dots_table(l2, obs_id: str, detector: int, session: int = 0, visit: int = 0,
               thresh: float = C.DETECT_THRESH, min_snr: float = C.DOT_MIN_SNR) -> pd.DataFrame:
    """The dot table of one opened Level 2 photo (or cutout)."""
    det = detect(l2.data, l2.flags, variance=l2.variance, thresh=thresh)
    det = det[det["snr"] >= min_snr]
    if len(det) == 0:
        return empty_dots()
    data = np.asarray(l2.data)
    f3 = or3x3(l2.flags)
    x, y = det["x"].astype(np.float64), det["y"].astype(np.float64)
    ix, iy = pixel_of(x, y, data.shape)
    sat = (f3[iy, ix] & SAT_BITS) != 0
    x, y, _ = recentroid_saturated(data, np.asarray(l2.flags), x, y, sat)
    ix, iy = pixel_of(x, y, data.shape)
    px, py = peak_pixel(data, x, y)
    ra, dec = pixel_to_sky(l2.wcs, x, y)
    ra = ra % 360.0
    conv = ujy_per_mjysr(l2.wcs)
    if l2.wave is not None:
        wl, bw = l2.wave.wavelength(x, y), l2.wave.bandwidth(x, y)
    else:
        wl = bw = np.full(x.size, np.nan)
    elong = elongation(data, build_mask(l2.data, l2.flags), x, y)
    df = pd.DataFrame({
        "obs_id": obs_id,
        "detector": detector,
        "mjd": l2.mjd_avg,
        "session": session,
        "visit": visit,
        "ra": ra,
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
        "flags": f3[iy, ix],
        "flags_peak": np.asarray(l2.flags).astype(np.int32)[py, px],
        "cell": cell_of(ra, dec),
    })
    return df.astype(DOT_COLUMNS)


def bad_finast(value) -> bool:
    """IMAGE header FINAST != 0 means the astrometric solution failed (missing key = fine)."""
    if value is None or str(value).strip() == "":
        return False
    try:
        return int(value) != C.FINAST_OK
    except (TypeError, ValueError):
        return True


# --- worker ------------------------------------------------------------------------------------
_STORE: Store | None = None


def _store(root: str) -> Store:
    global _STORE
    if _STORE is None or _STORE.root != root.rstrip("/"):
        _STORE = Store(root)
    return _STORE


def process_photo(root: str, obs_id: str, detector: int, url: str, session: int = 0, visit: int = 0) -> dict:
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
        finast = l2.header.get("FINAST")
        if bad_finast(finast):
            df = empty_dots()  # bad astrometry: an empty file marks the photo done
            st["finast"] = str(finast)
        else:
            df = dots_table(l2, obs_id, detector, session, visit)
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
        st["status"] = "done_finast" if "finast" in st else "done"
        st["error"] = ""
    except Exception as e:  # noqa: BLE001
        st["status"] = "failed"
        st["error"] = f"{type(e).__name__}: {e}"[:300]
    return st


def run(store: Store, zone: C.Zone, workers: int, limit: int | None = None) -> dict:
    t_start = time.time()
    photos = store.read_parquet(photolist_rel(zone.name)).sort_values(["mjd", "detector"]).reset_index(drop=True)
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
                              int(r.session), int(r.visit)) for r in todo]
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
    finast = [s for s in stats if s["status"] == "done_finast"]
    failed = [s for s in stats if s["status"] == "failed"]
    wall = time.time() - t_start
    out = {
        "photos": int(len(photos)), "done_before": n_done_before, "done_now": len(done_now),
        "skipped_finast": len(finast),
        "failed": len(failed), "failed_photos": [f"{s['obs_id']}D{s['detector']}" for s in failed][:50],
        "remaining": int(len(photos) - n_done_before - len(done_now) - len(finast)),
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
