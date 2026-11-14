"""Step 3, `known`: the known asteroids and comets the zone's photos saw, from MPC orbits moved with
kete (known_mpc.py computes them). This module holds the cache paths and the reader that score and
suspects use.

Cached per session (a run of exposures under 10 minutes apart) in cache/mpc/<zone>/run_<first obs_id>.json;
an existing file is never computed again.

Cache file: {zone, session, obs_ids, mjd, jd, query, n, fetched,
             objects: {name, number, ra, dec, v, dra_arcsec_h, ddec_arcsec_h, class} (columns),
             per_photo: {obs_id, detector, name, ra, dec} (columns), observers}
dRA is the on-sky rate (dRA·cos dec), arcsec per hour. per_photo holds each object at each photo's own time.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C
from .sky import Footprint
from .store import Store

MJD_TO_JD = 2400000.5
OBJ_KEYS = ("name", "number", "ra", "dec", "v", "dra_arcsec_h", "ddec_arcsec_h", "class")


def cache_dir(zone: C.Zone) -> str:
    """cache/mpc/<zone>"""
    return f"cache/mpc/{zone.name}"


def run_rel(zone: C.Zone, g: pd.DataFrame) -> str:
    return f"{cache_dir(zone)}/run_{g['obs_id'].iloc[0]}.json"


def run(store: Store, zone: C.Zone, limit: int | None = None) -> dict:
    from . import known_mpc  # known_mpc imports this module
    return known_mpc.run(store, zone, limit=limit)


def per_photo_positions(d: dict):
    """For a cache file, a function (objs, obs_id, detector) -> (ra, dec) arrays aligned with objs:
    the exact position in that photo from the `per_photo` table, NaN where the object isn't listed for it."""
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
    """Every cached object at each photo's mid-time (its per_photo position), kept where it falls inside
    that photo, with its distance to the edge (px), the photo's obs_id, detector, mjd and session."""
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
        exact = per_photo_positions(d)
        for r in g.itertuples():
            ra, dec = exact(objs, r.obs_id, int(r.detector))
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
