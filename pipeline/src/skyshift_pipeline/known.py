"""Step 3, `known`: SkyBoT cone search for the known asteroids and comets in each photo.

IMCCE SkyBoT conesearch, JSON, epoch = photo mid-time as JD, geocentre (-loc=500), -from=SkyShift.
The two detectors exposed at the same moment (D1+D4, D2+D5, D3+D6) share one footprint, so one
request serves both photos. At most SKYBOT_MAX_IN_FLIGHT requests run at once. One cache file per
photo, cache/skybot/<obs_id>D<det>.json; an existing file is never asked again.

Cache file: {obs_id, detector, mjd, jd, query: {ra, dec, radius_deg}, n, fetched,
             objects: {name, number, ra, dec, v, dra_arcsec_h, ddec_arcsec_h, class}} (columns)
SkyBoT's dRA is the on-sky rate (dRA·cos dec), arcsec per hour.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import requests

from . import config as C
from . import log
from .sky import Footprint
from .store import Store, photolist_rel, skybot_rel

L = log.get("known")
MJD_TO_JD = 2400000.5
OBJ_KEYS = ("name", "number", "ra", "dec", "v", "dra_arcsec_h", "ddec_arcsec_h", "class")


def hms_to_deg(s: str) -> float:
    h, m, sec = (float(p) for p in s.split())
    return 15.0 * (h + m / 60.0 + sec / 3600.0)


def dms_to_deg(s: str) -> float:
    s = s.strip()
    sign = -1.0 if s.startswith("-") else 1.0
    d, m, sec = (abs(float(p)) for p in s.lstrip("+-").split())
    return sign * (d + m / 60.0 + sec / 3600.0)


def _num(x) -> float | None:
    try:
        v = float(x)
        return v if np.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def parse_skybot_json(text: str) -> dict[str, list]:
    """SkyBoT conesearch JSON -> columns. An empty body means no object in the cone."""
    cols: dict[str, list] = {k: [] for k in OBJ_KEYS}
    text = text.strip()
    if not text:
        return cols
    data = json.loads(text)
    if isinstance(data, dict):
        # error or "no object" answers come back as an object; a list of rows is the normal case
        rows = data.get("data") or data.get("result") or []
        if not rows and any(k.lower().startswith(("error", "flag")) for k in data):
            msg = json.dumps(data)[:200]
            if "error" in msg.lower():
                raise ValueError(f"SkyBoT error: {msg}")
    else:
        rows = data
    for r in rows:
        num = r.get("Num")
        cols["name"].append(str(r.get("Name", "")).strip())
        cols["number"].append(int(num) if str(num).strip().isdigit() else None)
        cols["ra"].append(round(hms_to_deg(r["RA (hms)"]), 7))
        cols["dec"].append(round(dms_to_deg(r["DEC (dms)"]), 7))
        cols["v"].append(_num(r.get("VMag (mag)")))
        cols["dra_arcsec_h"].append(_num(r.get("dRA (arcsec/h)")))
        cols["ddec_arcsec_h"].append(_num(r.get("dDEC (arcsec/h)")))
        cols["class"].append(str(r.get("Class", "")))
    return cols


def skybot_conesearch(ra: float, dec: float, radius_deg: float, jd: float, session: requests.Session | None = None) -> dict[str, list]:
    params = {
        "-ep": f"{jd:.6f}", "-ra": f"{ra:.6f}", "-dec": f"{dec:.6f}", "-rd": f"{radius_deg:.4f}",
        "-mime": "json", "-output": "basic", "-loc": "500", "-filter": "0", "-objFilter": "111",
        "-refsys": "EQJ2000", "-from": "SkyShift",
    }
    get = (session or requests).get
    last: Exception | None = None
    for attempt in range(1, C.NET_RETRIES + 1):
        try:
            r = get(C.SKYBOT_URL, params=params, timeout=C.SKYBOT_TIMEOUT_S)
            r.raise_for_status()
            return parse_skybot_json(r.text)
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(10 * attempt)
    raise RuntimeError(f"SkyBoT failed after {C.NET_RETRIES} tries: {last}")


def query_groups(photos: pd.DataFrame) -> list[pd.DataFrame]:
    """Photos sharing a request: same obs_id, simultaneous detector pair."""
    p = photos.copy()
    p["_pair"] = (p["detector"].astype(int) - 1) % 3
    return [g for _, g in p.groupby(["obs_id", "_pair"], sort=False)]


def _do_group(store: Store, g: pd.DataFrame, session: requests.Session) -> dict:
    fps = [Footprint.from_s_region(s) for s in g["s_region"]]
    ra0, dec0 = fps[0].ra0, fps[0].dec0
    radius = max(f.radius_deg() + float(np.hypot((f.ra0 - ra0) * np.cos(np.deg2rad(dec0)), f.dec0 - dec0)) for f in fps)
    radius += C.SKYBOT_RADIUS_PAD_DEG
    mjd = float(g["mjd"].iloc[0])
    jd = mjd + MJD_TO_JD
    t0 = time.time()
    objs = skybot_conesearch(ra0, dec0, radius, jd, session)
    dt = time.time() - t0
    for r in g.itertuples():
        store.write_json(skybot_rel(r.obs_id, int(r.detector)), {
            "obs_id": r.obs_id, "detector": int(r.detector), "mjd": float(r.mjd), "jd": float(r.mjd) + MJD_TO_JD,
            "query": {"ra": ra0, "dec": dec0, "radius_deg": radius, "jd": jd},
            "n": len(objs["name"]), "fetched": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "objects": objs,
        })
    return {"photos": len(g), "n": len(objs["name"]), "seconds": dt}


def run(store: Store, zone: C.Zone, limit: int | None = None) -> dict:
    t_start = time.time()
    photos = store.read_parquet(photolist_rel(zone.name)).sort_values(["mjd", "detector"]).reset_index(drop=True)
    have = {n for n in store.listdir("cache/skybot") if n.endswith(".json")}
    photos["_done"] = [f"{o}D{int(d)}.json" in have for o, d in zip(photos["obs_id"], photos["detector"])]
    groups = [g for g in query_groups(photos) if not g["_done"].all()]
    if limit is not None:
        # --limit counts photos, as for dots
        out_groups, n = [], 0
        for g in groups:
            if n >= limit:
                break
            out_groups.append(g)
            n += len(g)
        groups = out_groups
    L.info("known %s: %d photos, %d cached, %d requests to make (%d in flight)", zone.name, len(photos),
           int(photos["_done"].sum()), len(groups), C.SKYBOT_MAX_IN_FLIGHT)
    res, failed = [], []
    with requests.Session() as sess, ThreadPoolExecutor(max_workers=C.SKYBOT_MAX_IN_FLIGHT) as ex:
        futs = {ex.submit(_do_group, store, g, sess): g for g in groups}
        for k, f in enumerate(as_completed(futs), 1):
            g = futs[f]
            name = f"{g['obs_id'].iloc[0]}D{'+'.join(str(int(d)) for d in g['detector'])}"
            try:
                r = f.result()
                res.append(r)
                L.info("[%d/%d] %s: %d objects in %.1f s", k, len(groups), name, r["n"], r["seconds"])
            except Exception as e:  # noqa: BLE001
                failed.append(name)
                L.warning("[%d/%d] %s FAILED: %s", k, len(groups), name, e)
    out = {"photos": int(len(photos)), "cached_before": int(photos["_done"].sum()), "requests": len(res),
           "failed": len(failed), "failed_photos": failed[:50], "wall_s": round(time.time() - t_start, 1)}
    if res:
        out["seconds_per_request_median"] = float(np.median([r["seconds"] for r in res]))
        out["objects_per_request_median"] = float(np.median([r["n"] for r in res]))
    L.info("known %s: %s", zone.name, out)
    return out


def load_predictions(store: Store, photos: pd.DataFrame, workers: int = 16) -> pd.DataFrame:
    """Every cached prediction that falls inside its photo, with its distance to the edge (px)."""
    def one(r) -> pd.DataFrame | None:
        rel = skybot_rel(r.obs_id, int(r.detector))
        try:
            d = store.read_json(rel)
        except FileNotFoundError:
            return None
        o = d["objects"]
        if not o["name"]:
            return None
        df = pd.DataFrame({k: o[k] for k in OBJ_KEYS})
        fp = Footprint.from_s_region(r.s_region)
        edge = fp.edge_distance_deg(df["ra"].to_numpy(float), df["dec"].to_numpy(float)) * 3600.0 / C.PIXEL_ARCSEC
        df["edge_px"] = edge
        df = df[edge >= 0]
        df["obs_id"] = r.obs_id
        df["detector"] = int(r.detector)
        df["mjd"] = float(d["mjd"])
        return df

    with ThreadPoolExecutor(max_workers=workers) as ex:
        parts = [p for p in ex.map(one, photos.itertuples()) if p is not None and len(p)]
    if not parts:
        return pd.DataFrame(columns=[*OBJ_KEYS, "edge_px", "obs_id", "detector", "mjd"])
    out = pd.concat(parts, ignore_index=True)
    for c in ("v", "dra_arcsec_h", "ddec_arcsec_h"):
        out[c] = pd.to_numeric(out[c], errors="coerce")
    out[["dra_arcsec_h", "ddec_arcsec_h"]] = out[["dra_arcsec_h", "ddec_arcsec_h"]].fillna(0.0)
    return out
