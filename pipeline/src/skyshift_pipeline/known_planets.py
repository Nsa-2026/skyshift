"""Step 3, `known`, second source: the planets and their large moons each photo saw, from JPL Horizons.

MPC orbits (known_mpc.py) hold asteroids and comets only, so Saturn's moons, Uranus and its moons, Jupiter's
moons and Triton went through the suspects as unknown movers (15 Nov: 11 of the 22 movers with static frames
were Iapetus, Titan, Rhea, Hyperion, Titania or Uranus, each within 0.4″ of Horizons).

- Bodies: config PLANET_SYSTEMS, the planet and its moons bright enough for SPHEREx (NAIF ids).
- Which photos: a coarse ephemeris of each planet (PLANETS_COARSE_STEP_H) over the zone's time range; a
  visit counts when the planet comes within PLANETS_MARGIN_DEG + the system's radius of the zone box.
- Positions: one Horizons table per body and visit (PLANETS_STEP_MIN steps, astrometric ICRF RA/Dec, on-sky
  rates in ″/h, apparent V), interpolated to each photo's mid-time. Observer: the Earth's centre. SPHEREx is
  at most ~7,100 km from it: ≤ 2.3″ at Jupiter, ≤ 1″ at Saturn, far inside suspects' KNOWN_ARCSEC (30″).
- Cache per session, cache/planets/<zone>/run_<first obs_id>.json, the same shape as the MPC files (known.py),
  with class "planet" or "moon" and number null; known.load_predictions reads both folders. A session with
  no planet near gets a file with no objects. Nothing is written when Horizons fails (rerun `known`).
- score leaves these classes out (its recall is about asteroids); suspects and orphans use them.
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd

from . import config as C
from . import log
from .known import MJD_TO_JD, OBJ_KEYS, cache_dir, run_rel
from .sky import Footprint
from .store import Store, photolist_rel

L = log.get("known_planets")
HORIZONS_URL = "https://ssd.jpl.nasa.gov/api/horizons.api"
EPH_COLUMNS = ("jd", "ra", "dec", "dra_arcsec_h", "ddec_arcsec_h", "v")


# ---------------------------------------------------------------- Horizons

def parse_table(result: str) -> pd.DataFrame:
    """Horizons' CSV observer table (QUANTITIES 1,3,9, CAL_FORMAT JD, ANG_FORMAT DEG) -> EPH_COLUMNS."""
    if "$$SOE" not in result:
        raise ValueError(f"Horizons gave no table: {result.strip()[:300]}")
    head, body = result.split("$$SOE", 1)
    body = body.split("$$EOE", 1)[0]
    names = [h.strip() for h in [ln for ln in head.splitlines() if ln.startswith("Date")][-1].split(",")]
    col = {k: names.index(next(n for n in names if n.startswith(p)))
           for k, p in (("jd", "Date"), ("ra", "R.A."), ("dec", "DEC"), ("dra_arcsec_h", "dRA*cosD"),
                        ("ddec_arcsec_h", "d(DEC)/dt"), ("v", "APmag"))}
    rows = []
    for ln in body.strip().splitlines():
        f = [x.strip() for x in ln.split(",")]
        rows.append([_num(f[col[k]]) for k in EPH_COLUMNS])
    return pd.DataFrame(rows, columns=list(EPH_COLUMNS))


def _num(s: str) -> float:
    try:
        return float(s)
    except ValueError:
        return np.nan  # "n.a."


def horizons(naif: int, jd0: float, jd1: float, step: str) -> pd.DataFrame:
    """One body's geocentric table from jd0 to jd1 (UT) at `step` (e.g. "10m", "6h")."""
    import requests

    params = {"format": "json", "COMMAND": f"'{naif}'", "OBJ_DATA": "NO", "MAKE_EPHEM": "YES", "EPHEM_TYPE": "OBSERVER",
              "CENTER": "'500@399'", "START_TIME": f"'JD {jd0:.6f}'", "STOP_TIME": f"'JD {jd1:.6f}'",
              "STEP_SIZE": f"'{step}'", "QUANTITIES": "'1,3,9'", "CSV_FORMAT": "YES", "ANG_FORMAT": "DEG",
              "CAL_FORMAT": "JD", "EXTRA_PREC": "YES"}
    last: Exception | None = None
    for attempt in range(1, C.NET_RETRIES + 1):
        try:
            r = requests.get(HORIZONS_URL, params=params, timeout=120)
            r.raise_for_status()
            return parse_table(r.json()["result"])
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * attempt)
    raise RuntimeError(f"Horizons {naif} failed after {C.NET_RETRIES} tries: {last}")


# ---------------------------------------------------------------- geometry

def interp(eph: pd.DataFrame, mjd: np.ndarray) -> dict[str, np.ndarray]:
    """The table at each MJD: linear in time, RA unwrapped across 0/360."""
    jd = np.asarray(mjd, float) + MJD_TO_JD
    t = eph["jd"].to_numpy(float)
    ra = np.degrees(np.unwrap(np.radians(eph["ra"].to_numpy(float))))
    out = {"ra": np.interp(jd, t, ra) % 360.0}
    for k in ("dec", "dra_arcsec_h", "ddec_arcsec_h", "v"):
        out[k] = np.interp(jd, t, eph[k].to_numpy(float))
    return out


def near_box(zone: C.Zone, ra: np.ndarray, dec: np.ndarray, margin_deg: float) -> np.ndarray:
    """True where (ra, dec) is within margin_deg of the zone box (RA margin widened by 1/cos dec)."""
    ra, dec = np.asarray(ra, float), np.asarray(dec, float)
    cosd = np.maximum(np.cos(np.radians(np.clip(np.abs(dec) + margin_deg, 0, 89.0))), 0.02)
    dra = margin_deg / cosd
    lo, hi = zone.ra_min - dra, zone.ra_max + dra
    in_ra = ((ra - lo) % 360.0) <= (hi - lo)
    return in_ra & (dec >= zone.dec_min - margin_deg) & (dec <= zone.dec_max + margin_deg)


def relevant_visits(zone: C.Zone, photos: pd.DataFrame, coarse: dict[str, pd.DataFrame]) -> list[tuple[str, int]]:
    """(planet, visit) pairs whose planet comes near the zone box during that visit."""
    out = []
    vis = photos["visit"] if "visit" in photos.columns else pd.Series(0, index=photos.index)
    for planet, eph in coarse.items():
        radius = C.PLANET_SYSTEMS[planet]["radius_deg"]
        for v, g in photos.groupby(vis, sort=True):
            m0, m1 = float(g["mjd"].min()) - 0.5, float(g["mjd"].max()) + 0.5
            t = np.arange(m0, m1 + 0.25, 0.25)
            p = interp(eph, t)
            if near_box(zone, p["ra"], p["dec"], C.PLANETS_MARGIN_DEG + radius).any():
                out.append((planet, int(v)))
    return out


# ---------------------------------------------------------------- cache files

def session_record(zone: C.Zone, g: pd.DataFrame, bodies: list[tuple[str, str]], tables: dict[str, pd.DataFrame],
                   query: dict) -> dict:
    """The cache file of one session: each body at each photo's own time (photos whose padded footprint it is in),
    and once in `objects` at the session mid-time. bodies: (name, class) with a table in `tables`."""
    mjd = float((g["mjd"].min() + g["mjd"].max()) / 2.0)
    per = {"obs_id": [], "detector": [], "name": [], "ra": [], "dec": []}
    objects = {k: [] for k in OBJ_KEYS}
    fps = [(r.obs_id, int(r.detector), float(r.mjd), Footprint.from_s_region(r.s_region)) for r in g.itertuples()]
    for name, cls in bodies:
        eph = tables[name]
        seen = False
        for obs_id, det, m, fp in fps:
            p = interp(eph, np.array([m]))
            if not np.isfinite(p["ra"][0]) or fp.edge_distance_deg(p["ra"], p["dec"])[0] < -C.MPC_FOV_PAD_DEG:
                continue
            per["obs_id"].append(obs_id); per["detector"].append(det); per["name"].append(name)
            per["ra"].append(_f(p["ra"][0], 7)); per["dec"].append(_f(p["dec"][0], 7))
            seen = True
        if not seen:
            continue
        p = interp(eph, np.array([mjd]))
        for k, v in (("name", name), ("number", None), ("ra", _f(p["ra"][0], 7)), ("dec", _f(p["dec"][0], 7)),
                     ("v", _f(p["v"][0], 2)), ("dra_arcsec_h", _f(p["dra_arcsec_h"][0], 4)),
                     ("ddec_arcsec_h", _f(p["ddec_arcsec_h"][0], 4)), ("class", cls)):
            objects[k].append(v)
    return {"zone": zone.name, "session": int(g["session"].iloc[0]), "obs_ids": sorted(set(g["obs_id"])), "mjd": mjd,
            "jd": mjd + MJD_TO_JD, "query": query, "n": len(objects["name"]),
            "fetched": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "objects": objects, "per_photo": per,
            "observers": {}}


def _f(x, nd: int) -> float | None:
    return None if x is None or not np.isfinite(x) else round(float(x), nd)


def run(store: Store, zone: C.Zone, limit: int | None = None, fetch=horizons) -> dict:
    """Cache files for every session without one. fetch(naif, jd0, jd1, step) -> table (tests stub it)."""
    t_start = time.time()
    photos = store.read_parquet(photolist_rel(zone.name))
    if "visit" not in photos.columns:
        photos = photos.assign(visit=0)
    have = set(store.listdir(cache_dir(zone, "planets")))
    runs = [g for _, g in photos.groupby("session", sort=True)]
    todo = [g for g in runs if run_rel(zone, g, "planets").rsplit("/", 1)[1] not in have]
    if limit is not None:
        todo = todo[:limit]
    out = {"source": "jpl-horizons", "sessions": len(runs), "cached": len(runs) - len(todo), "computed": 0}
    if not todo:
        return {**out, "wall_s": round(time.time() - t_start, 1)}
    m0, m1 = float(photos["mjd"].min()) - 1.0, float(photos["mjd"].max()) + 1.0
    coarse = {p: fetch(s["naif"], m0 + MJD_TO_JD, m1 + MJD_TO_JD, f"{C.PLANETS_COARSE_STEP_H}h")
              for p, s in C.PLANET_SYSTEMS.items()}
    near = relevant_visits(zone, photos, coarse)
    L.info("planets %s: %d sessions to do; near the zone: %s", zone.name, len(todo), near or "none")
    tables: dict[int, dict[str, pd.DataFrame]] = {}
    bodies: dict[int, list[tuple[str, str]]] = {}
    pad = C.PLANETS_STEP_MIN / 1440.0
    for planet, v in near:
        g = photos[photos["visit"] == v]
        j0, j1 = float(g["mjd"].min()) - pad + MJD_TO_JD, float(g["mjd"].max()) + pad + MJD_TO_JD
        sysd = C.PLANET_SYSTEMS[planet]
        for naif, name, cls in [(sysd["naif"], planet, "planet"), *[(n, nm, "moon") for n, nm in sysd["moons"]]]:
            tables.setdefault(v, {})[name] = fetch(naif, j0, j1, f"{C.PLANETS_STEP_MIN}m")
            bodies.setdefault(v, []).append((name, cls))
    query = {"source": "jpl-horizons", "observer": "500@399 (the Earth's centre)", "step_min": C.PLANETS_STEP_MIN,
             "systems": {p: [p, *[nm for _, nm in s["moons"]]] for p, s in C.PLANET_SYSTEMS.items()}}
    n_obj, n_with = 0, 0
    for g in todo:
        v = int(g["visit"].iloc[0])
        rec = session_record(zone, g, bodies.get(v, []), tables.get(v, {}), query)
        store.write_json(run_rel(zone, g, "planets"), rec)
        out["computed"] += 1
        n_obj += rec["n"]
        n_with += rec["n"] > 0
    out.update({"near": [f"{p} visit {v}" for p, v in near], "sessions_with_bodies": n_with, "objects": n_obj,
                "wall_s": round(time.time() - t_start, 1)})
    L.info("known (planets) %s: %s", zone.name, out)
    return out
