"""Step 3, `known --known-source mpc`: the known asteroids and comets each photo saw, from the Minor
Planet Center's orbits propagated with kete (Caltech/IPAC, BSD-3) instead of the SkyBoT web service.

- Orbits: MPC mpcorb_extended.json.gz (~1.6 million asteroids) and cometels.json.gz (~1,000 comets;
  no interstellar objects, no non-gravitational terms). The first run keeps dated copies in
  cache/mpc/orbits/{mpcorb,cometels}_<YYYY-MM-DD>.parquet; every run uses the newest of each, so all
  machines and reruns use the same orbits. `skyshift-pipeline mpc-orbits` (refresh_orbits) adds newer
  snapshots, from a laptop: the GCP machines can't reach the MPC. Comets have no V (the MPC comet file's magnitudes aren't kept
  by kete): they count for the suspects' known-object cut and stay out of score's V cuts (SkyBoT gives some
  comets V = 0.0, which score then counts as bright asteroids: docs/research/2026-11-14-mpc-vs-skybot-score.md).
- Observer: SPHEREx itself. IMAGE header X_SC, Y_SC, Z_SC (km) and VX_SC, VY_SC, VZ_SC (km/s) are
  geocentric, equatorial J2000, at MJD-AVG (UTC). On one D2 photo this put the 8 known asteroids
  brighter than V 17 on their dots with a median offset of 1.0″ (1.7″ from the Earth's centre).
  One header per exposure, read over HTTPS (first MPC_HEADER_BLOCK bytes). A header without them
  falls back to the Earth's centre at the photo list's mid-time (counted in the summary).
- Each photo is a kete SpherexCmos field: its s_region corners pushed out by MPC_FOV_PAD_DEG (the exact
  footprint cut stays in load_predictions). One kete.fov_state_check per visit propagates every
  object once with n-body physics across all that visit's photos and corrects for light time.
- V from H and G (MPC_G_DEFAULT when missing) with kete's H-G model; fainter than MPC_V_MAX is dropped.

Cache per session, cache/mpc/<zone>/run_<first obs_id>.json, in the SkyBoT shape (known.py) so score
and suspects read it unchanged, plus two keys:
  per_photo: {obs_id, detector, name, ra, dec} (columns), each object at that photo's own time;
             load_predictions uses these instead of moving `objects` with the rates.
  observers: {obs_id: {mjd_avg, X_SC, ..., VZ_SC, observer: "spacecraft" | "geocentre"}}.
`objects` lists each object once, moved with its rates from the first photo that saw it to the
session mid-time. Rates are on-sky (dRA·cos dec), arcsec per hour, as SkyBoT's.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from . import config as C
from . import log
from .known import MJD_TO_JD, OBJ_KEYS, cache_dir, run_rel
from .sky import Footprint, offset_radec
from .store import Store, photolist_rel

L = log.get("known_mpc")
AU_KM = 149_597_870.7
SC_POS = ("X_SC", "Y_SC", "Z_SC")
SC_VEL = ("VX_SC", "VY_SC", "VZ_SC")
DEG_DAY_TO_ARCSEC_H = 3600.0 / 24.0


def _kete():
    try:
        import kete
    except ImportError as e:  # pragma: no cover
        raise ImportError("known (MPC) needs kete: `uv sync` in pipeline/") from e
    return kete


# ---------------------------------------------------------------- orbits

def _snapshot_name(prefix: str) -> str:
    return f"{prefix}_{time.strftime('%Y-%m-%d', time.gmtime())}.parquet"


def _snapshot(store: Store, prefix: str, fetch, refresh: bool = False) -> tuple[pd.DataFrame, str]:
    """The newest <prefix>_<date>.parquet in MPC_ORBITS_DIR; fetched and saved on first use.
    refresh=True fetches anyway and saves today's snapshot (replacing one already made today)."""
    names = sorted(n for n in store.listdir(C.MPC_ORBITS_DIR) if n.startswith(prefix + "_") and n.endswith(".parquet"))
    if names and not refresh:
        tab = store.read_parquet(f"{C.MPC_ORBITS_DIR}/{names[-1]}")
        L.info("MPC orbits: %s (%d objects)", names[-1], len(tab))
        return tab, names[-1]
    t0 = time.time()
    tab = fetch()
    tab["desig"] = tab["desig"].astype(str)  # numbered asteroids come as int, the rest as str
    name = _snapshot_name(prefix)
    store.write_parquet(f"{C.MPC_ORBITS_DIR}/{name}", tab)
    L.info("MPC orbits downloaded in %.0f s: %d objects, saved as %s", time.time() - t0, len(tab), name)
    return tab, name


def _fetch_asteroids() -> pd.DataFrame:
    return _kete().mpc.fetch_known_orbit_data(url=C.MPC_ORBITS_URL)


def _fetch_comets() -> pd.DataFrame:
    return _kete().mpc.fetch_known_comet_orbit_data()


def orbit_table(store: Store) -> tuple[pd.DataFrame, str]:
    """Asteroids then comets, one row per object, and the snapshot names ("mpcorb_… + cometels_…")."""
    ast, a = _snapshot(store, "mpcorb", _fetch_asteroids)
    com, c = _snapshot(store, "cometels", _fetch_comets)
    return pd.concat([ast, com], ignore_index=True), f"{a} + {c}"


def refresh_orbits(store: Store, force: bool = False, fetchers: dict | None = None) -> dict:
    """`skyshift-pipeline mpc-orbits`: new dated snapshots of both orbit files, which every later `known`
    run then uses (the newest wins). Run it from a laptop: minorplanetcenter.net timed out from the GCP
    machines (14 Nov). A file whose snapshot for today exists is kept unless force. Sessions already in
    cache/mpc/<zone>/ keep their old positions; delete those files to recompute them."""
    fetchers = fetchers or {"mpcorb": _fetch_asteroids, "cometels": _fetch_comets}
    out: dict = {}
    for prefix, fetch in fetchers.items():
        today = _snapshot_name(prefix)
        if not force and store.exists(f"{C.MPC_ORBITS_DIR}/{today}"):
            L.info("MPC orbits: %s exists, kept (--force fetches it again)", today)
            out[prefix] = {"snapshot": today, "fetched": False}
            continue
        tab, name = _snapshot(store, prefix, fetch, refresh=True)
        out[prefix] = {"snapshot": name, "fetched": True, "objects": int(len(tab))}
    return out


def object_table(orb: pd.DataFrame) -> pd.DataFrame:
    """name, number, h, g, class for each orbit row (row k is kete state desig str(k)).
    name: the MPC name ("Sapientia"), else "(number)" for numbered objects, else the designation;
    a name used twice gets its designation appended so per_photo rows stay unique."""
    desig = orb["desig"].astype(str).str.strip().reset_index(drop=True)  # comet designations end in a space
    numbered = desig.str.fullmatch(r"\d+").fillna(False).to_numpy(bool)
    nan = pd.Series(np.nan, index=orb.index)
    raw = orb.get("name", nan).reset_index(drop=True)
    named = raw.notna().to_numpy() & (raw.fillna("").astype(str).str.strip() != "").to_numpy()
    name = pd.Series(np.where(named, raw.fillna("").astype(str).str.strip(),
                              np.where(numbered, "(" + desig + ")", desig)), dtype=object)
    dup = name.duplicated(keep=False).to_numpy()
    name[dup] = name[dup] + " [" + desig[dup] + "]"
    return pd.DataFrame({
        "name": name.to_numpy(object),
        "number": pd.Series([int(d) if n else None for d, n in zip(desig, numbered)], dtype=object).to_numpy(),
        "h": pd.to_numeric(orb.get("h_mag", nan), errors="coerce").to_numpy(float),
        "g": pd.to_numeric(orb.get("g_phase", nan), errors="coerce").fillna(C.MPC_G_DEFAULT).to_numpy(float),
        "class": orb["group_name"].fillna("").astype(str).to_numpy(object),
    })


def build_states(orb: pd.DataFrame) -> list:
    """kete states, one per orbit row, named by row number (kete would rename designations)."""
    t = orb.reset_index(drop=True).copy()
    t["desig"] = [str(k) for k in range(len(t))]
    return _kete().conversion.table_to_states(t)


# ---------------------------------------------------------------- SPHEREx's position

def read_observer(url: str) -> dict:
    """mjd_avg and X_SC..VZ_SC from a photo's IMAGE header (None where missing)."""
    import fsspec
    from astropy.io import fits

    last: Exception | None = None
    for attempt in range(1, C.NET_RETRIES + 1):
        try:
            with fsspec.open(url, "rb", block_size=C.MPC_HEADER_BLOCK, cache_type="readahead") as f, \
                    fits.open(f, lazy_load_hdus=True) as hd:
                h = hd["IMAGE"].header
                return {"mjd_avg": _num(h.get("MJD-AVG")), **{k: _num(h.get(k)) for k in (*SC_POS, *SC_VEL)}}
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * attempt)
    raise RuntimeError(f"IMAGE header read failed after {C.NET_RETRIES} tries: {last}")


def _num(x) -> float | None:
    try:
        v = float(x)
        return v if np.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def observer_state(kete, ob: dict, mjd_fallback: float):
    """kete State of SPHEREx (Sun-centred, ecliptic) from header values; the Earth's centre at
    mjd_fallback when they are missing. Returns (state, "spacecraft" | "geocentre")."""
    have = ob.get("mjd_avg") is not None and all(ob.get(k) is not None for k in (*SC_POS, *SC_VEL))
    jd = kete.Time.from_mjd(float(ob["mjd_avg"] if have else mjd_fallback), scaling="utc").jd
    earth = kete.spice.get_state("Earth", jd)
    if not have:
        return earth, "geocentre"
    e_pos, e_vel = earth.pos.as_ecliptic, earth.vel.as_ecliptic
    sp = kete.Vector([ob[k] / AU_KM for k in SC_POS], kete.Frames.Equatorial).as_ecliptic
    sv = kete.Vector([ob[k] * 86400.0 / AU_KM for k in SC_VEL], kete.Frames.Equatorial).as_ecliptic
    pos = kete.Vector([e_pos.x + sp.x, e_pos.y + sp.y, e_pos.z + sp.z], kete.Frames.Ecliptic)
    vel = kete.Vector([e_vel.x + sv.x, e_vel.y + sv.y, e_vel.z + sv.z], kete.Frames.Ecliptic)
    return kete.State("SPHEREx", jd, pos, vel, kete.Frames.Ecliptic, 10), "spacecraft"


# ---------------------------------------------------------------- fields and the check

def _unit(ra, dec) -> np.ndarray:
    ra, dec = np.radians(ra), np.radians(dec)
    return np.stack([np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)], axis=-1)


def padded_corners(s_region: str, pad_deg: float) -> list[tuple[float, float]]:
    """The footprint's corners, each moved pad_deg further from the footprint centre."""
    fp = Footprint.from_s_region(s_region)
    c = _unit(fp.ra0, fp.dec0)
    out = []
    for p in _unit(fp.vertices[:, 0], fp.vertices[:, 1]):
        cosang = float(np.clip(p @ c, -1.0, 1.0))
        t = p - cosang * c
        t /= np.linalg.norm(t)
        a = np.arccos(cosang) + np.radians(pad_deg)
        q = np.cos(a) * c + np.sin(a) * t
        out.append((float(np.degrees(np.arctan2(q[1], q[0])) % 360.0), float(np.degrees(np.arcsin(q[2])))))
    return out


def seen_in_photos(kete, states: list, objs: pd.DataFrame, photos: pd.DataFrame, observers: dict) -> pd.DataFrame:
    """One fov_state_check over the photos' fields (states first moved to one epoch, the first field's
    time, as kete requires). Rows: obs_id, detector, row (orbit row), ra, dec, dra_arcsec_h,
    ddec_arcsec_h, v; objects fainter than MPC_V_MAX dropped."""
    t0 = time.time()
    states = kete.propagate_n_body(states, min(observers[o][0].jd for o in set(photos["obs_id"])))
    L.info("orbits moved to the visit start in %.0f s", time.time() - t0)
    fovs, where = [], {}
    for r in photos.itertuples():
        uri = f"{r.obs_id}D{int(r.detector)}"
        corners = [kete.Vector.from_ra_dec(a, d) for a, d in padded_corners(r.s_region, C.MPC_FOV_PAD_DEG)]
        fovs.append(kete.fov.SpherexCmos(corners, observers[r.obs_id][0], uri, str(int(r.detector))))
        where[uri] = (r.obs_id, int(r.detector))
    parts = []
    for ss in kete.fov_state_check(states, fovs):
        if not len(ss.states):
            continue
        obs_id, det = where[ss.fov.uri]
        sun2obs = ss.fov.observer.pos
        rows = np.array([int(st.desig) for st in ss.states])
        rd = np.asarray(ss.ra_dec_with_rates, float).reshape(-1, 4)  # deg, deg, deg/day (RA × cos dec), deg/day
        h, g = objs["h"].to_numpy()[rows], objs["g"].to_numpy()[rows]
        v = np.array([kete.flux.hg_apparent_mag(st.pos, sun2obs, float(hh), float(gg)) if np.isfinite(hh) else np.nan
                      for st, hh, gg in zip(ss.states, h, g)], float)
        keep = ~(v >= C.MPC_V_MAX)  # unknown V is kept: better a known object too many than a false suspect
        parts.append(pd.DataFrame({
            "obs_id": obs_id, "detector": det, "row": rows[keep], "ra": rd[keep, 0] % 360.0, "dec": rd[keep, 1],
            "dra_arcsec_h": rd[keep, 2] * DEG_DAY_TO_ARCSEC_H, "ddec_arcsec_h": rd[keep, 3] * DEG_DAY_TO_ARCSEC_H,
            "v": v[keep],
        }))
    cols = ["obs_id", "detector", "row", "ra", "dec", "dra_arcsec_h", "ddec_arcsec_h", "v"]
    return pd.concat(parts, ignore_index=True)[cols] if parts else pd.DataFrame(columns=cols)


# ---------------------------------------------------------------- cache files

def _f(x, nd: int) -> float | None:
    return None if x is None or not np.isfinite(x) else round(float(x), nd)


def session_record(zone: C.Zone, g: pd.DataFrame, seen: pd.DataFrame, objs: pd.DataFrame,
                   observers: dict, query: dict) -> dict:
    """The cache file of one session from its rows of `seen`."""
    mjd = float((g["mjd"].min() + g["mjd"].max()) / 2.0)
    s = seen.astype({"detector": int, "row": int}).merge(g[["obs_id", "detector", "mjd"]].astype({"detector": int}),
                                                         on=["obs_id", "detector"]).sort_values(["mjd", "obs_id", "detector", "row"])
    first = s.drop_duplicates("row", keep="first")
    hours = (mjd - first["mjd"].to_numpy(float)) * 24.0
    ra, dec = offset_radec(first["ra"].to_numpy(float), first["dec"].to_numpy(float),
                           first["dra_arcsec_h"].to_numpy(float) * hours, first["ddec_arcsec_h"].to_numpy(float) * hours)
    o = objs.iloc[first["row"].to_numpy(int)]
    objects = {
        "name": o["name"].tolist(), "number": o["number"].tolist(),
        "ra": [_f(x, 7) for x in ra], "dec": [_f(x, 7) for x in dec],
        "v": [_f(x, 2) for x in first["v"]],
        "dra_arcsec_h": [_f(x, 4) for x in first["dra_arcsec_h"]],
        "ddec_arcsec_h": [_f(x, 4) for x in first["ddec_arcsec_h"]],
        "class": o["class"].tolist(),
    }
    assert tuple(objects) == OBJ_KEYS
    used = sorted(set(g["obs_id"]))
    return {
        "zone": zone.name, "session": int(g["session"].iloc[0]), "obs_ids": used, "mjd": mjd, "jd": mjd + MJD_TO_JD,
        "query": query, "n": len(first), "fetched": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "objects": objects,
        "per_photo": {
            "obs_id": s["obs_id"].tolist(), "detector": [int(x) for x in s["detector"]],
            "name": objs["name"].to_numpy()[s["row"].to_numpy(int)].tolist(),
            "ra": [_f(x, 7) for x in s["ra"]], "dec": [_f(x, 7) for x in s["dec"]],
        },
        "observers": {k: {**observers[k][1], "observer": observers[k][2]} for k in used},
    }


def run(store: Store, zone: C.Zone, limit: int | None = None) -> dict:
    kete = _kete()
    t_start = time.time()
    photos = store.read_parquet(photolist_rel(zone.name))
    have = set(store.listdir(cache_dir(zone)))
    runs = [g for _, g in photos.groupby("session", sort=True)]
    todo = [g for g in runs if run_rel(zone, g).rsplit("/", 1)[1] not in have]
    n_cached = len(runs) - len(todo)
    if limit is not None:
        todo = todo[:limit]  # --limit counts sessions here
    L.info("known (MPC) %s: %d photos in %d sessions, %d cached, %d to compute", zone.name, len(photos), len(runs),
           n_cached, len(todo))
    out = {"source": "mpc", "photos": int(len(photos)), "sessions": len(runs), "cached": n_cached, "computed": 0}
    if not todo:
        return {**out, "wall_s": round(time.time() - t_start, 1)}

    orb, snapshot = orbit_table(store)
    objs = object_table(orb)
    t0 = time.time()
    states = build_states(orb)
    L.info("%d kete states in %.0f s", len(states), time.time() - t0)

    need = pd.concat(todo).drop_duplicates("obs_id")
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=C.MPC_HEADER_WORKERS) as ex:
        heads = dict(zip(need["obs_id"], ex.map(_try_read_observer, need["url"])))
    observers = {}
    for r in need.itertuples():
        state, how = observer_state(kete, heads[r.obs_id], float(r.mjd))
        observers[r.obs_id] = (state, heads[r.obs_id], how)
    n_geo = sum(1 for v in observers.values() if v[2] == "geocentre")
    L.info("SPHEREx position for %d exposures in %.0f s (%d fell back to the Earth's centre)", len(need),
           time.time() - t0, n_geo)

    query = {"source": "mpc", "orbits": snapshot, "kete": getattr(kete, "__version__", "?"), "v_max": C.MPC_V_MAX,
             "fov_pad_deg": C.MPC_FOV_PAD_DEG, "observer": "SPHEREx (IMAGE header X_SC.., MJD-AVG)"}
    chunks = pd.concat(todo).groupby("visit", sort=True) if "visit" in photos.columns else [(0, pd.concat(todo))]
    n_obj, n_rows = [], 0
    for visit, chunk in chunks:
        t0 = time.time()
        seen = seen_in_photos(kete, states, objs, chunk, observers)
        L.info("visit %s: %d photos, %d object-photo rows in %.0f s", visit, len(chunk), len(seen), time.time() - t0)
        for _, g in chunk.groupby("session", sort=True):
            rec = session_record(zone, g, seen[seen["obs_id"].isin(set(g["obs_id"]))], objs, observers, query)
            store.write_json(run_rel(zone, g), rec)
            n_obj.append(rec["n"])
            n_rows += len(rec["per_photo"]["obs_id"])
            out["computed"] += 1
    out.update({"exposures_geocentre": n_geo, "orbits": snapshot, "objects_per_session_median": float(np.median(n_obj)),
                "object_photo_rows": n_rows, "wall_s": round(time.time() - t_start, 1)})
    L.info("known (MPC) %s: %s", zone.name, out)
    return out


def _try_read_observer(url: str) -> dict:
    try:
        return read_observer(url)
    except Exception as e:  # noqa: BLE001
        L.warning("no SPHEREx position for %s: %s", url.rsplit("/", 1)[-1], e)
        return {}
