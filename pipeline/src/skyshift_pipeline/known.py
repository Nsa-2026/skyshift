"""Step 3, `known`: the known bodies the zone's photos saw, from two sources: asteroids and comets from MPC
orbits moved with kete (known_mpc.py), and the planets with their large moons from JPL Horizons
(known_planets.py). This module holds the cache paths and the reader that score and suspects use.

Cached per session (a run of exposures under 10 minutes apart) in cache/mpc/<zone>/run_<first obs_id>.json
and cache/planets/<zone>/run_<first obs_id>.json; an existing file is never computed again.

Cache file: {zone, session, obs_ids, mjd, jd, query, n, fetched,
             objects: {name, number, ra, dec, v, dra_arcsec_h, ddec_arcsec_h, class} (columns),
             per_photo: {obs_id, detector, name, ra, dec} (columns), observers}
dRA is the on-sky rate (dRA·cos dec), arcsec per hour. per_photo holds each object at each photo's own time.
Planet files: class "planet" or "moon", number null, observers {}.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C
from .sky import Footprint, SkyTree, angsep, offset_radec
from .store import Store

MJD_TO_JD = 2400000.5
OBJ_KEYS = ("name", "number", "ra", "dec", "v", "dra_arcsec_h", "ddec_arcsec_h", "class")
SOURCES = ("mpc", "planets")  # cache/<source>/<zone>/
PLANET_CLASSES = ("planet", "moon")  # known_planets' classes; score leaves them out


def cache_dir(zone: C.Zone, source: str = "mpc") -> str:
    """cache/mpc/<zone> or cache/planets/<zone>"""
    return f"cache/{source}/{zone.name}"


def run_rel(zone: C.Zone, g: pd.DataFrame, source: str = "mpc") -> str:
    return f"{cache_dir(zone, source)}/run_{g['obs_id'].iloc[0]}.json"


def run(store: Store, zone: C.Zone, limit: int | None = None) -> dict:
    """MPC first, then the planets. A failed planet query is logged and leaves no file: run `known` again."""
    from . import known_mpc, known_planets  # both import this module
    out = known_mpc.run(store, zone, limit=limit)
    try:
        out["planets"] = known_planets.run(store, zone, limit=limit)
    except Exception as e:  # noqa: BLE001
        out["planets"] = {"source": "jpl-horizons", "error": str(e)[:300]}
    return out


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


def load_predictions(store: Store, photos: pd.DataFrame, zone: C.Zone, sources: tuple[str, ...] = SOURCES) -> pd.DataFrame:
    """Every cached object at each photo's mid-time (its per_photo position), kept where it falls inside
    that photo, with its distance to the edge (px), the photo's obs_id, detector, mjd and session.
    sources: which cache folders (default both; a missing file is skipped)."""
    parts = []
    for (_, g), source in ((x, s) for x in photos.groupby("session", sort=True) for s in sources):
        try:
            d = store.read_json(run_rel(zone, g, source))
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


# ---------------------------------------------------------------- matching a mover to a known body

def brighter(pred: pd.DataFrame, v_max: float = C.MPC_V_MAX) -> pd.DataFrame:
    """The predictions a position-only match may use: V < v_max, or no V (comets, planets, moons). The cache keeps
    objects to MPC_V_STORE (23) since 16 Nov; before, to MPC_V_MAX (21), so old caches pass unchanged."""
    if len(pred) == 0 or "v" not in pred:
        return pred
    v = pd.to_numeric(pred["v"], errors="coerce").to_numpy(np.float64)
    return pred[~(v >= v_max)].reset_index(drop=True)


def rate_pa(dra_arcsec_h, ddec_arcsec_h) -> tuple[np.ndarray, np.ndarray]:
    """On-sky rate (″/h) and direction (deg east of north, 0–360) from dRA·cos dec and dDec rates."""
    e, n = np.asarray(dra_arcsec_h, np.float64), np.asarray(ddec_arcsec_h, np.float64)
    return np.hypot(e, n), np.degrees(np.arctan2(e, n)) % 360.0


def pa_diff(a, b) -> np.ndarray:
    """Smallest angle between two directions (deg, 0–180)."""
    d = np.abs((np.asarray(a, np.float64) - np.asarray(b, np.float64)) % 360.0)
    return np.minimum(d, 360.0 - d)


def judge(seps_arcsec, our_rate, our_pa, k_rate, k_pa) -> dict:
    """The contract's rule (api.md v3.23 `status` known) for one known body against one mover. seps_arcsec: the body's
    predicted distance from the mover at each dot's time (NaN = not predicted there). Certain when every dot is within
    KNOWN_CERTAIN_ARCSEC and, when both have a motion (our rate finite and > 0, the body's > 0), the body's rate is
    within KNOWN_RATE_TOL of ours and its direction within KNOWN_PA_TOL_DEG. Returns {certain, sep_max, sep_min,
    rate_ratio (body / ours, or None), dpa (deg, or None)}."""
    s = np.asarray(seps_arcsec, np.float64)
    sep_max = float(np.max(s)) if s.size and np.isfinite(s).all() else float("inf")
    sep_min = float(np.nanmin(s)) if s.size and np.isfinite(s).any() else float("inf")
    moving = our_rate is not None and np.isfinite(our_rate) and our_rate > 0 and k_rate is not None and k_rate > 0
    ratio = float(k_rate / our_rate) if moving else None
    dpa = float(pa_diff(our_pa, k_pa)) if moving and our_pa is not None and np.isfinite(our_pa) else None
    ok_motion = (not moving) or (abs(ratio - 1.0) <= C.KNOWN_RATE_TOL and dpa is not None and dpa <= C.KNOWN_PA_TOL_DEG)
    return {"certain": bool(sep_max <= C.KNOWN_CERTAIN_ARCSEC and ok_motion), "sep_max": sep_max, "sep_min": sep_min,
            "rate_ratio": ratio, "dpa": dpa}


def dot_places(ra: float, dec: float, mjd: float, rate, pa, times) -> tuple[np.ndarray, np.ndarray]:
    """A mover's place at each of `times` (MJD) on its straight line: the mean position (ra, dec at mjd) moved by its
    on-sky rate (″/h) along pa (deg E of N); the mean position itself without a rate."""
    t = np.atleast_1d(np.asarray(times, np.float64))
    if rate is None or not np.isfinite(rate) or pa is None or not np.isfinite(pa):
        return np.full(t.shape, float(ra)), np.full(t.shape, float(dec))
    h = (t - float(mjd)) * 24.0
    p = np.radians(float(pa))
    return offset_radec(np.full(t.shape, float(ra)), np.full(t.shape, float(dec)),
                        float(rate) * np.sin(p) * h, float(rate) * np.cos(p) * h)


def certain_known(movers: pd.DataFrame, pred: pd.DataFrame, search_arcsec: float = C.KNOWN_CERTAIN_ARCSEC + 2.0) -> pd.DataFrame:
    """Per mover, the known body (any brightness) that certainly is it by `judge`, from the cached predictions of
    its session (load_predictions rows). The mover's places are its straight line at its dots' times (`dot_mjd` when
    present, else its mean time); a body's place at a dot's time is its prediction for the photo nearest in time
    moved by its rate. Columns (aligned with movers): known_name ("" when none), known_sep_max (″), known_v."""
    n = len(movers)
    out = pd.DataFrame({"known_name": np.full(n, "", dtype=object), "known_sep_max": np.full(n, np.nan),
                        "known_v": np.full(n, np.nan)})
    if n == 0 or len(pred) == 0:
        return out
    tree = SkyTree(pred["ra"].to_numpy(float), pred["dec"].to_numpy(float))
    ps, pm = pred["session"].to_numpy(np.int64), pred["mjd"].to_numpy(np.float64)
    pra, pdec = pred["ra"].to_numpy(np.float64), pred["dec"].to_numpy(np.float64)
    pe = pd.to_numeric(pred["dra_arcsec_h"], errors="coerce").fillna(0.0).to_numpy(np.float64)
    pn = pd.to_numeric(pred["ddec_arcsec_h"], errors="coerce").fillna(0.0).to_numpy(np.float64)
    pv = pd.to_numeric(pred["v"], errors="coerce").to_numpy(np.float64)
    names = pred["name"].to_numpy(object)
    has_dots = "dot_mjd" in movers
    rates = pd.to_numeric(movers.get("rate_arcsec_h", pd.Series(np.nan, index=movers.index)), errors="coerce").to_numpy(float)
    pas = pd.to_numeric(movers.get("pa_deg", pd.Series(np.nan, index=movers.index)), errors="coerce").to_numpy(float)
    for i, r in enumerate(movers.itertuples()):
        cand = tree.query_radius(r.ra, r.dec, search_arcsec + 900.0)[0]  # + room for the bodies' motion within the session
        cand = np.asarray(cand, np.int64)
        cand = cand[ps[cand] == int(r.session)] if cand.size else cand
        if cand.size == 0:
            continue
        # both move on straight lines, so a body within the limit at every dot is within it at the mean time too
        h0 = (r.mjd - pm[cand]) * 24.0
        r0, d0 = offset_radec(pra[cand], pdec[cand], pe[cand] * h0, pn[cand] * h0)
        cand = cand[angsep(r.ra, r.dec, r0, d0) * 3600.0 <= search_arcsec]
        if cand.size == 0:
            continue
        times = np.asarray(r.dot_mjd, np.float64) if has_dots and r.dot_mjd is not None and len(r.dot_mjd) else np.array([r.mjd])
        ora, odec = dot_places(r.ra, r.dec, r.mjd, rates[i], pas[i], times)
        best = None
        for name in pd.unique(names[cand]):
            rows = cand[names[cand] == name]
            seps = np.empty(len(times))
            for k, t in enumerate(times):
                j = rows[np.argmin(np.abs(pm[rows] - t))]
                h = (t - pm[j]) * 24.0
                kra, kdec = offset_radec(pra[j], pdec[j], pe[j] * h, pn[j] * h)
                seps[k] = float(angsep(ora[k], odec[k], kra, kdec)) * 3600.0
            kr, kp = rate_pa(pe[rows].mean(), pn[rows].mean())
            j = judge(seps, rates[i], pas[i], float(kr), float(kp))
            if j["certain"] and (best is None or j["sep_max"] < best[1]):
                best = (name, j["sep_max"], float(np.nanmin(pv[rows])) if np.isfinite(pv[rows]).any() else np.nan)
        if best is not None:
            out.iloc[i] = list(best)
    return out
