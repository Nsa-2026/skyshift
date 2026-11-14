"""Step 1, `photolist`: every Level 2 photo touching the zone, and the 0.25° tile map.

Source: IRSA TAP. `spherex.obscore` gives the footprint, time and band; its `cloud_access` column
is empty over TAP, so the S3 key comes from `spherex.artifact.uri` ('ibe/data/spherex/' + key),
joined through `spherex.plane.obs_publisher_did`. DR1 and QR3 only (no *_deep); one row per
(obs_id, detector), DR1 preferred. Time = (t_min + t_max) / 2. Detector from energy_bandpassname.

Outputs (under photolists/zones/):
- <zone>.parquet          one row per photo
- <zone>_tilemap.parquet  (tile, obs_id, detector) -> mjd, session, visit, covers_centre
- <zone>_tiles.parquet    one row per tile: centre, photos and sessions covering the centre
Sessions and visits are numbered per tile from every photo touching the tile (core.sessions,
core.visits); coverage counts only photos that contain the tile centre.
"""

from __future__ import annotations

import time
from collections import Counter

import numpy as np
import pandas as pd
from skyshift_core import detector_from_bandpass, sessions, visits

from . import config as C
from . import log
from .sky import Footprint, zone_tiles
from .store import Store, photolist_rel

L = log.get("photolist")

TILE_HALF_DIAG_DEG = C.TILE_DEG / np.sqrt(2.0)


def tap_query(zone: C.Zone) -> pd.DataFrame:
    from astroquery.ipac.irsa import Irsa

    m = C.PHOTO_HALF_DIAG_DEG
    dec_lo, dec_hi = zone.dec_min - m, zone.dec_max + m
    cosd = np.cos(np.deg2rad(max(abs(dec_lo), abs(dec_hi))))
    ra_lo, ra_hi = zone.ra_min - m / cosd, zone.ra_max + m / cosd
    cols = ", ".join(f"'{c}'" for c in C.COLLECTIONS)
    q = f"""SELECT o.obs_id, o.obs_collection, o.energy_bandpassname, o.t_min, o.t_max,
       o.s_ra, o.s_dec, o.s_region, a.uri, a.contentlength
FROM spherex.obscore AS o
JOIN spherex.plane AS p ON p.obs_publisher_did = o.obs_publisher_did
LEFT OUTER JOIN spherex.artifact AS a ON a.planeid = p.planeid AND a.producttype = 'science'
WHERE o.obs_collection IN ({cols}) AND o.dataproduct_type = 'image'
  AND o.s_dec BETWEEN {dec_lo:.4f} AND {dec_hi:.4f} AND o.s_ra BETWEEN {ra_lo:.4f} AND {ra_hi:.4f}"""
    for attempt in range(1, C.NET_RETRIES + 1):
        try:
            t0 = time.time()
            tab = Irsa.query_tap(q).to_table()
            L.info("TAP: %d rows in %.1f s", len(tab), time.time() - t0)
            break
        except Exception as e:  # noqa: BLE001
            L.warning("TAP failed (%s), attempt %d", e, attempt)
            if attempt == C.NET_RETRIES:
                raise
            time.sleep(10 * attempt)
    df = pd.DataFrame({
        "obs_id": [str(x) for x in tab["obs_id"]],
        "collection": [str(x) for x in tab["obs_collection"]],
        "bandpass": [str(x) for x in tab["energy_bandpassname"]],
        "t_min": np.asarray(tab["t_min"], np.float64),
        "t_max": np.asarray(tab["t_max"], np.float64),
        "s_ra": np.asarray(tab["s_ra"], np.float64),
        "s_dec": np.asarray(tab["s_dec"], np.float64),
        "s_region": [str(x) for x in tab["s_region"]],
        "uri": ["" if np.ma.is_masked(x) else str(x) for x in tab["uri"]],
        "size_bytes": [0 if np.ma.is_masked(x) else int(x) for x in tab["contentlength"]],
    })
    return df


def s3_key_from_uri(uri: str) -> str:
    """'ibe/data/spherex/dr1/level2/...fits' -> 'dr1/level2/...fits' ('' if not a DR1/QR3 key)."""
    if not uri.startswith(C.IRSA_URI_PREFIX):
        return ""
    key = uri[len(C.IRSA_URI_PREFIX):]
    return key if key.split("/", 1)[0] in ("dr1", "qr3") else ""


def clean(raw: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """One row per (obs_id, detector), DR1 first; photos without an S3 key are dropped and counted."""
    df = raw.copy()
    df["detector"] = [detector_from_bandpass(b) for b in df["bandpass"]]
    df["s3_key"] = [s3_key_from_uri(u) for u in df["uri"]]
    pref = {c: i for i, c in enumerate(C.COLLECTIONS)}
    df["_pref"] = df["collection"].map(pref)
    df = df[df["_pref"].notna()]
    df = df.sort_values(["obs_id", "detector", "_pref", "s3_key"], ascending=[True, True, True, False])
    n_rows = len(df)
    df = df.drop_duplicates(["obs_id", "detector"], keep="first")
    no_key = df["s3_key"] == ""
    stats = {"tap_rows": int(len(raw)), "duplicates_removed": int(n_rows - len(df)),
             "no_s3_key": int(no_key.sum()),
             "no_s3_key_examples": df.loc[no_key, "obs_id"].head(5).tolist()}
    df = df[~no_key].drop(columns=["_pref", "uri", "bandpass"])
    df["detector"] = df["detector"].astype("int8")
    df["mjd"] = (df["t_min"] + df["t_max"]) / 2.0
    df["url"] = C.S3_HTTPS + "/" + df["s3_key"]
    return df.reset_index(drop=True), stats


def tile_map(photos: pd.DataFrame, zone: C.Zone) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Keep photos touching the zone, then map (tile, photo) -> session, visit."""
    tid, tra, tdec = zone_tiles(zone)
    # zone sample points for the "touches the zone" test: tile centres and corners
    h = C.TILE_DEG / 2.0
    sra = np.concatenate([tra, tra - h, tra + h, tra - h, tra + h])
    sdec = np.concatenate([tdec, tdec - h, tdec - h, tdec + h, tdec + h])
    rows = []
    keep = np.zeros(len(photos), dtype=bool)
    for k, (s_region, obs_id, det, mjd) in enumerate(zip(photos["s_region"], photos["obs_id"], photos["detector"], photos["mjd"])):
        fp = Footprint.from_s_region(s_region)
        v = fp.vertices
        touches = bool(fp.contains(sra, sdec).any() or zone.contains(v[:, 0], v[:, 1]).any())
        if not touches:
            continue
        keep[k] = True
        d = fp.edge_distance_deg(tra, tdec)
        near = d >= -TILE_HALF_DIAG_DEG
        for t, dd in zip(tid[near], d[near]):
            rows.append((int(t), obs_id, int(det), float(mjd), bool(dd >= 0)))
    kept = photos[keep].reset_index(drop=True)
    tm = pd.DataFrame(rows, columns=["tile", "obs_id", "detector", "mjd", "covers_centre"])
    tm["session"] = np.int32(0)
    tm["visit"] = np.int16(0)
    tiles = []
    for t, g in tm.groupby("tile", sort=True):
        s = sessions(g["mjd"].to_numpy())
        v = visits(g["mjd"].to_numpy())
        tm.loc[g.index, "session"] = s.astype(np.int32)
        tm.loc[g.index, "visit"] = v.astype(np.int16)
        c = g["covers_centre"].to_numpy()
        tiles.append((t, int(c.sum()), int(np.unique(s[c]).size), int(np.unique(v[c]).size),
                      int(np.unique(s).size), int(np.unique(v).size)))
    tiles_df = pd.DataFrame(tiles, columns=["tile", "n_photos", "n_sessions", "n_visits",
                                            "n_sessions_touching", "n_visits_touching"])
    centres = pd.DataFrame({"tile": tid, "ra": tra, "dec": tdec})
    tiles_df = centres.merge(tiles_df, on="tile", how="left").fillna(0)
    for c in ("n_photos", "n_sessions", "n_visits", "n_sessions_touching", "n_visits_touching"):
        tiles_df[c] = tiles_df[c].astype("int32")
    tm["session"] = tm["session"].astype("int32")
    tm["visit"] = tm["visit"].astype("int16")
    tm["detector"] = tm["detector"].astype("int8")
    return kept, tm, tiles_df


def describe(photos: pd.DataFrame, tiles: pd.DataFrame) -> dict:
    return {
        "photos": int(len(photos)),
        "exposures": int(photos["obs_id"].nunique()),
        "per_detector": {str(k): int(v) for k, v in sorted(Counter(photos["detector"]).items())},
        "per_collection": {str(k): int(v) for k, v in sorted(Counter(photos["collection"]).items())},
        "mjd_range": [float(photos["mjd"].min()), float(photos["mjd"].max())] if len(photos) else None,
        "gigabytes_full_files": round(float(photos["size_bytes"].sum()) / 1e9, 1),
        "tiles": int(len(tiles)),
        "sessions_per_tile": _quantiles(tiles["n_sessions"]),
        "visits_per_tile": _quantiles(tiles["n_visits"]),
        "photos_per_tile": _quantiles(tiles["n_photos"]),
        "tiles_with_3plus_sessions": int((tiles["n_sessions"] >= C.MOVER_MIN_COVER_SESSIONS).sum()),
    }


def _quantiles(s: pd.Series) -> dict:
    if len(s) == 0:
        return {}
    q = np.percentile(s.to_numpy(), [0, 10, 50, 90, 100])
    return dict(zip(["min", "p10", "median", "p90", "max"], [float(x) for x in q]))


def run(store: Store, zone: C.Zone, force: bool = False) -> dict:
    rel = photolist_rel(zone.name)
    rel_tm, rel_tiles = photolist_rel(zone.name, "tilemap"), photolist_rel(zone.name, "tiles")
    if not force and all(store.exists(r) for r in (rel, rel_tm, rel_tiles)):
        L.info("photolist for %s exists, skipping", zone.name)
        photos, tiles = store.read_parquet(rel), store.read_parquet(rel_tiles)
        return {"skipped": True, **describe(photos, tiles)}
    t0 = time.time()
    raw = tap_query(zone)
    photos, stats = clean(raw)
    photos, tm, tiles = tile_map(photos, zone)
    # tilemap and tiles first: the photo list's existence marks the step done
    store.write_parquet(rel_tm, tm)
    store.write_parquet(rel_tiles, tiles)
    store.write_parquet(rel, photos)
    out = {**stats, **describe(photos, tiles), "tilemap_rows": int(len(tm)), "seconds": round(time.time() - t0, 1)}
    L.info("photolist %s: %s", zone.name, out)
    return out
