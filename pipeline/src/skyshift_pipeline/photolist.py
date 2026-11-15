"""Step 1, `photolist`: every Level 2 photo touching the zone, with its session and visit.

Source: IRSA TAP. Photos whose footprint (spherex.plane.poly) INTERSECTS the zone box, joined to
spherex.obscore (s_region, time, band) and spherex.artifact (S3 key: uri = 'ibe/data/spherex/' +
key; obscore's cloud_access is empty over TAP). DR1 and QR3 only (no *_deep); one row per
(obs_id, detector), DR1 preferred. Time = (t_min + t_max) / 2. Detector from energy_bandpassname.
If IRSA refuses the INTERSECTS query, a box on the photo centres plus a local footprint test is used.
If TAP fails altogether (IRSA down, 14 Nov evening), or env SKYSHIFT_PHOTOLIST_SOURCE=s3index, the list comes from
the S3 photo index (s3index.py, photolists/s3index/): same columns and values, footprints intersecting the zone box
(great-circle edges, as INTERSECTS), same dedupe. Used only when every period shard exists; stats say whether the
index is complete (`index_complete`, `index_failed_keys`).

Sessions: a run of the zone's exposure times with gaps under 10 minutes (core.sessions on every
photo of the zone), one value per photo. Visits: core.visits on the same times. Coverage of a
position is counted later from the footprints (sky.sessions_covering).

Output: photolists/zones/<zone>.parquet, one row per photo.
"""

from __future__ import annotations

import os
import time
from collections import Counter

import numpy as np
import pandas as pd
from skyshift_core import detector_from_bandpass, sessions, visits

from . import config as C
from . import log
from .sky import Footprint
from .store import Store, photolist_rel

L = log.get("photolist")

_SELECT = """SELECT o.obs_id, o.obs_collection, o.energy_bandpassname, o.t_min, o.t_max,
       o.s_ra, o.s_dec, o.s_region, a.uri, a.contentlength"""


def _poly(zone: C.Zone) -> str:
    return (f"POLYGON('ICRS',{zone.ra_min},{zone.dec_min},{zone.ra_max},{zone.dec_min},"
            f"{zone.ra_max},{zone.dec_max},{zone.ra_min},{zone.dec_max})")


def _cols() -> str:
    return ", ".join(f"'{c}'" for c in C.COLLECTIONS)


def query_intersects(zone: C.Zone) -> str:
    return f"""{_SELECT}
FROM spherex.plane AS p JOIN spherex.artifact AS a ON a.planeid = p.planeid
JOIN spherex.obscore AS o ON o.obs_publisher_did = p.obs_publisher_did
WHERE INTERSECTS(p.poly, {_poly(zone)}) = 1 AND o.obs_collection IN ({_cols()})"""


def query_box(zone: C.Zone) -> str:
    m = C.PHOTO_HALF_DIAG_DEG
    dec_lo, dec_hi = zone.dec_min - m, zone.dec_max + m
    cosd = np.cos(np.deg2rad(max(abs(dec_lo), abs(dec_hi))))
    ra_lo, ra_hi = zone.ra_min - m / cosd, zone.ra_max + m / cosd
    return f"""{_SELECT}
FROM spherex.obscore AS o
JOIN spherex.plane AS p ON p.obs_publisher_did = o.obs_publisher_did
LEFT OUTER JOIN spherex.artifact AS a ON a.planeid = p.planeid AND a.producttype = 'science'
WHERE o.obs_collection IN ({_cols()}) AND o.dataproduct_type = 'image'
  AND o.s_dec BETWEEN {dec_lo:.4f} AND {dec_hi:.4f} AND o.s_ra BETWEEN {ra_lo:.4f} AND {ra_hi:.4f}"""


def _tap(q: str):
    from astroquery.ipac.irsa import Irsa

    for attempt in range(1, C.NET_RETRIES + 1):
        try:
            t0 = time.time()
            tab = Irsa.query_tap(q).to_table()
            L.info("TAP: %d rows in %.1f s", len(tab), time.time() - t0)
            return tab
        except Exception as e:  # noqa: BLE001
            L.warning("TAP failed (%s), attempt %d", str(e)[:200], attempt)
            if attempt == C.NET_RETRIES:
                raise
            time.sleep(10 * attempt)


def tap_query(zone: C.Zone) -> tuple[pd.DataFrame, str]:
    try:
        tab, how = _tap(query_intersects(zone)), "intersects"
    except Exception:  # noqa: BLE001
        L.warning("INTERSECTS query failed; falling back to a box on photo centres + local footprint test")
        tab, how = _tap(query_box(zone)), "box"
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
    return df, how


def s3_key_from_uri(uri: str) -> str:
    """'ibe/data/spherex/dr1/level2/...fits' -> 'dr1/level2/...fits' ('' if not a DR1/QR3 key)."""
    if not uri.startswith(C.IRSA_URI_PREFIX):
        return ""
    key = uri[len(C.IRSA_URI_PREFIX):]
    return key if key.split("/", 1)[0] in ("dr1", "qr3") else ""


def dedupe(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """One row per (obs_id, detector): DR1 first, then the larger s3_key; other collections dropped."""
    pref = {c: i for i, c in enumerate(C.COLLECTIONS)}
    df = df.assign(_pref=df["collection"].map(pref))
    df = df[df["_pref"].notna()]
    df = df.sort_values(["obs_id", "detector", "_pref", "s3_key"], ascending=[True, True, True, False])
    n_rows = len(df)
    df = df.drop_duplicates(["obs_id", "detector"], keep="first").drop(columns=["_pref"])
    return df.reset_index(drop=True), {"duplicates_removed": int(n_rows - len(df))}


def clean(raw: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """One row per (obs_id, detector), DR1 first; photos without an S3 key are dropped and counted."""
    df = raw.copy()
    df["detector"] = [detector_from_bandpass(b) for b in df["bandpass"]]
    df["s3_key"] = [s3_key_from_uri(u) for u in df["uri"]]
    df, dd = dedupe(df)
    no_key = df["s3_key"] == ""
    stats = {"tap_rows": int(len(raw)), **dd,
             "no_s3_key": int(no_key.sum()),
             "no_s3_key_examples": df.loc[no_key, "obs_id"].head(5).tolist()}
    df = df[~no_key].drop(columns=["uri", "bandpass"])
    df["detector"] = df["detector"].astype("int8")
    df["mjd"] = (df["t_min"] + df["t_max"]) / 2.0
    df["url"] = C.S3_HTTPS + "/" + df["s3_key"]
    return df.reset_index(drop=True), stats


def touches_zone(photos: pd.DataFrame, zone: C.Zone, step_deg: float = 0.1) -> np.ndarray:
    """Local footprint test (used after the box fallback): a vertex in the zone, or a zone grid
    point inside the footprint."""
    gr, gd = np.meshgrid(np.arange(zone.ra_min, zone.ra_max + 1e-9, step_deg),
                         np.arange(zone.dec_min, zone.dec_max + 1e-9, step_deg))
    gr, gd = gr.ravel(), gd.ravel()
    keep = np.zeros(len(photos), dtype=bool)
    for k, s in enumerate(photos["s_region"]):
        fp = Footprint.from_s_region(s)
        v = fp.vertices
        keep[k] = bool(zone.contains(v[:, 0], v[:, 1]).any() or fp.contains(gr, gd).any())
    return keep


def add_sessions(photos: pd.DataFrame) -> pd.DataFrame:
    """session and visit per photo from the zone's exposure times (core.sessions, core.visits)."""
    p = photos.copy()
    t = p["mjd"].to_numpy(np.float64)
    p["session"] = sessions(t).astype(np.int32) if len(p) else np.zeros(0, np.int32)
    p["visit"] = visits(t).astype(np.int16) if len(p) else np.zeros(0, np.int16)
    return p.sort_values(["mjd", "detector"]).reset_index(drop=True)


def from_s3index(store: Store, zone: C.Zone) -> tuple[pd.DataFrame, dict]:
    """The zone's photos from photolists/s3index/ (s3index.py): same rows and columns as clean() makes from TAP."""
    from . import s3index

    photos, stats = s3index.zone_photos(store, zone)
    if not stats["index_complete"]:
        L.warning("photo list of %s from an index without _complete.json (every period shard is there)", zone.name)
    return photos, stats


def describe(photos: pd.DataFrame) -> dict:
    if len(photos) == 0:
        return {"photos": 0}
    per_s = photos.groupby("session")["obs_id"].nunique()
    span = photos.groupby("session")["mjd"].agg(lambda x: (x.max() - x.min()) * 1440.0)
    return {
        "photos": int(len(photos)),
        "exposures": int(photos["obs_id"].nunique()),
        "per_detector": {str(k): int(v) for k, v in sorted(Counter(photos["detector"]).items())},
        "per_collection": {str(k): int(v) for k, v in sorted(Counter(photos["collection"]).items())},
        "mjd_range": [float(photos["mjd"].min()), float(photos["mjd"].max())],
        "sessions": int(photos["session"].nunique()),
        "visits": int(photos["visit"].nunique()),
        "exposures_per_session": {"min": int(per_s.min()), "median": float(per_s.median()), "max": int(per_s.max())},
        "session_minutes": {"median": round(float(span.median()), 1), "max": round(float(span.max()), 1)},
        "file_mb": {"min": round(float(photos["size_bytes"].min()) / 1e6, 1), "max": round(float(photos["size_bytes"].max()) / 1e6, 1)},
        "gigabytes_full_files": round(float(photos["size_bytes"].sum()) / 1e9, 1),
    }


def run(store: Store, zone: C.Zone, force: bool = False) -> dict:
    rel = photolist_rel(zone.name)
    if not force and store.exists(rel):
        L.info("photolist for %s exists, skipping", zone.name)
        return {"skipped": True, **describe(store.read_parquet(rel))}
    t0 = time.time()
    if os.environ.get("SKYSHIFT_PHOTOLIST_SOURCE", "tap").lower() == "s3index":
        L.info("SKYSHIFT_PHOTOLIST_SOURCE=s3index: photo list from the S3 index, no TAP")
        photos, stats, how = *from_s3index(store, zone), "s3index"
    else:
        try:
            raw, how = tap_query(zone)
        except Exception as e:  # noqa: BLE001  IRSA down: the S3 index, if it has every period
            L.warning("TAP failed after its retries (%s): trying the S3 index", str(e)[:200])
            try:
                photos, stats = from_s3index(store, zone)
            except Exception as e2:  # noqa: BLE001
                L.error("no photo list from the S3 index either: %s", e2)
                raise e from e2
            how, stats["tap_error"] = "s3index", str(e)[:200]
        else:
            photos, stats = clean(raw)
            if how == "box":
                photos = photos[touches_zone(photos, zone)].reset_index(drop=True)
    photos = add_sessions(photos)
    store.write_parquet(rel, photos)
    out = {"query": how, **stats, **describe(photos), "seconds": round(time.time() - t0, 1)}
    L.info("photolist %s: %s", zone.name, out)
    return out
