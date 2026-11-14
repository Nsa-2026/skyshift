"""Step 3, `known`: SkyBoT cone search for the known asteroids and comets the zone's photos saw.

One cone per session (a run of exposures under 10 minutes apart): its centre and radius cover the
run's footprints inside the zone, at the run's mid-time (JD); every object is then moved to each
photo's mid-time with SkyBoT's rates. (Per photo would be ~1,000 cones of 13–23 s each.)
IMCCE SkyBoT conesearch, JSON, geocentre (-loc=500), -from=SkyShift, at most SKYBOT_MAX_IN_FLIGHT
requests at once. Cached per run in cache/skybot/<zone>/run_<first obs_id>.json; an existing file
is never asked again.

Cache file: {zone, session, obs_ids, mjd, jd, query: {ra, dec, radius_deg}, n, fetched,
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
from .sky import Footprint, angsep, offset_radec
from .store import Store, photolist_rel

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


def cache_dir(zone: C.Zone) -> str:
    """cache/skybot/<zone> (default) or cache/mpc/<zone> (config KNOWN_SOURCE = "mpc")."""
    if C.KNOWN_SOURCE not in C.KNOWN_SOURCES:
        raise ValueError(f"KNOWN_SOURCE {C.KNOWN_SOURCE!r} not in {C.KNOWN_SOURCES}")
    return f"cache/{C.KNOWN_SOURCE}/{zone.name}"


def run_rel(zone: C.Zone, g: pd.DataFrame) -> str:
    return f"{cache_dir(zone)}/run_{g['obs_id'].iloc[0]}.json"


def run_cone(g: pd.DataFrame, zone: C.Zone) -> tuple[float, float, float]:
    """(ra, dec, radius) of a cone covering the run's footprints, clipped to the zone box."""
    v = np.vstack([Footprint.from_s_region(s).vertices for s in g["s_region"]])
    pad = C.SKYBOT_PAD_DEG
    ra_lo = max(v[:, 0].min(), zone.ra_min) - pad
    ra_hi = min(v[:, 0].max(), zone.ra_max) + pad
    dec_lo = max(v[:, 1].min(), zone.dec_min) - pad
    dec_hi = min(v[:, 1].max(), zone.dec_max) + pad
    ra0, dec0 = (ra_lo + ra_hi) / 2.0, (dec_lo + dec_hi) / 2.0
    corners_ra = np.array([ra_lo, ra_hi, ra_lo, ra_hi])
    corners_dec = np.array([dec_lo, dec_lo, dec_hi, dec_hi])
    radius = float(angsep(ra0, dec0, corners_ra, corners_dec).max())
    return float(ra0), float(dec0), radius


def _do_run(store: Store, zone: C.Zone, g: pd.DataFrame, session: requests.Session) -> dict:
    ra0, dec0, radius = run_cone(g, zone)
    mjd = float((g["mjd"].min() + g["mjd"].max()) / 2.0)
    jd = mjd + MJD_TO_JD
    t0 = time.time()
    objs = skybot_conesearch(ra0, dec0, radius, jd, session)
    dt = time.time() - t0
    store.write_json(run_rel(zone, g), {
        "zone": zone.name, "session": int(g["session"].iloc[0]), "obs_ids": sorted(set(g["obs_id"])),
        "mjd": mjd, "jd": jd, "query": {"ra": ra0, "dec": dec0, "radius_deg": radius},
        "n": len(objs["name"]), "fetched": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "objects": objs,
    })
    return {"photos": len(g), "n": len(objs["name"]), "seconds": dt, "radius": radius}


def run(store: Store, zone: C.Zone, limit: int | None = None) -> dict:
    if C.KNOWN_SOURCE == "mpc":
        from . import known_mpc  # kete is an optional extra: `uv sync --extra mpc`
        return known_mpc.run(store, zone, limit=limit)
    t_start = time.time()
    photos = store.read_parquet(photolist_rel(zone.name))
    have = set(store.listdir(cache_dir(zone)))
    runs = [g for _, g in photos.groupby("session", sort=True)]
    todo = [g for g in runs if run_rel(zone, g).rsplit("/", 1)[1] not in have]
    if limit is not None:
        todo = todo[:limit]  # --limit counts sessions here
    L.info("known %s: %d photos in %d sessions, %d cached, %d cones to ask (%d in flight)", zone.name, len(photos),
           len(runs), len(runs) - len([g for g in runs if run_rel(zone, g).rsplit("/", 1)[1] not in have]), len(todo),
           C.SKYBOT_MAX_IN_FLIGHT)
    res, failed = [], []
    with requests.Session() as sess, ThreadPoolExecutor(max_workers=C.SKYBOT_MAX_IN_FLIGHT) as ex:
        futs = {ex.submit(_do_run, store, zone, g, sess): g for g in todo}
        for k, f in enumerate(as_completed(futs), 1):
            g = futs[f]
            name = f"session {int(g['session'].iloc[0])} ({len(g)} photos)"
            try:
                r = f.result()
                res.append(r)
                L.info("[%d/%d] %s: %d objects in %.1f s (r = %.2f°)", k, len(todo), name, r["n"], r["seconds"], r["radius"])
            except Exception as e:  # noqa: BLE001
                failed.append(name)
                L.warning("[%d/%d] %s FAILED: %s", k, len(todo), name, e)
    out = {"photos": int(len(photos)), "sessions": len(runs), "requests": len(res),
           "failed": len(failed), "failed_sessions": failed[:50], "wall_s": round(time.time() - t_start, 1)}
    if res:
        out["seconds_per_request_median"] = float(np.median([r["seconds"] for r in res]))
        out["objects_per_request_median"] = float(np.median([r["n"] for r in res]))
    L.info("known %s: %s", zone.name, out)
    return out


def propagate_objects(objs: pd.DataFrame, mjd_from: float, mjd_to: float) -> tuple[np.ndarray, np.ndarray]:
    h = (mjd_to - mjd_from) * 24.0
    return offset_radec(objs["ra"].to_numpy(float), objs["dec"].to_numpy(float),
                        objs["dra_arcsec_h"].to_numpy(float) * h, objs["ddec_arcsec_h"].to_numpy(float) * h)


def per_photo_positions(d: dict):
    """For a cache file with a `per_photo` table (known_mpc), a function (objs, obs_id, detector) ->
    (ra, dec) arrays aligned with objs: the exact position in that photo, NaN where the object isn't
    listed for it. None for SkyBoT caches (positions are then moved with the rates)."""
    if "per_photo" not in d:
        return None
    t = pd.DataFrame(d["per_photo"], columns=["obs_id", "detector", "name", "ra", "dec"])
    if t.empty:
        return lambda objs, obs_id, det: (np.full(len(objs), np.nan), np.full(len(objs), np.nan))
    t["detector"] = t["detector"].astype(int)
    idx = {k: g.set_index("name")[["ra", "dec"]] for k, g in t.groupby(["obs_id", "detector"])}

    def at(objs: pd.DataFrame, obs_id: str, det: int):
        g = idx.get((obs_id, det))
        if g is None:
            return np.full(len(objs), np.nan), np.full(len(objs), np.nan)
        m = g.reindex(objs["name"].to_numpy())
        return m["ra"].to_numpy(float), m["dec"].to_numpy(float)

    return at


def load_predictions(store: Store, photos: pd.DataFrame, zone: C.Zone) -> pd.DataFrame:
    """Every cached object moved to each photo's mid-time, kept where it falls inside that photo,
    with its distance to the edge (px), the photo's obs_id, detector, mjd and session."""
    parts = []
    for _, g in photos.groupby("session", sort=True):
        try:
            d = store.read_json(run_rel(zone, g))
        except FileNotFoundError:
            continue
        o = d["objects"]
        if not o["name"]:
            continue
        objs = pd.DataFrame({k: o[k] for k in OBJ_KEYS})
        for c in ("v", "dra_arcsec_h", "ddec_arcsec_h"):
            objs[c] = pd.to_numeric(objs[c], errors="coerce")
        objs[["dra_arcsec_h", "ddec_arcsec_h"]] = objs[["dra_arcsec_h", "ddec_arcsec_h"]].fillna(0.0)
        exact = per_photo_positions(d)  # MPC caches: positions computed at each photo's own time
        for r in g.itertuples():
            if exact is not None:
                ra, dec = exact(objs, r.obs_id, int(r.detector))
            else:
                ra, dec = propagate_objects(objs, float(d["mjd"]), float(r.mjd))
            fp = Footprint.from_s_region(r.s_region)
            edge = fp.edge_distance_deg(ra, dec) * 3600.0 / C.PIXEL_ARCSEC
            ok = edge >= 0
            if not ok.any():
                continue
            df = objs[ok].copy()
            df["ra"], df["dec"], df["edge_px"] = ra[ok], dec[ok], edge[ok]
            df["obs_id"], df["detector"], df["mjd"], df["session"] = r.obs_id, int(r.detector), float(r.mjd), int(r.session)
            parts.append(df)
    cols = [*OBJ_KEYS, "edge_px", "obs_id", "detector", "mjd", "session"]
    if not parts:
        return pd.DataFrame(columns=cols)
    return pd.concat(parts, ignore_index=True)[cols]
