"""Known comets at the movers, for rb_score's comet exemption (rb_score.py): which suspects row's place, rate and
direction match a known comet.

    names, st = KC.zone_comets(store, zone, suspects)   # {id: comet name} for the rows that match; st: counts

- Orbits: MPC comets only, the newest cometels_<date>.parquet under MPC_ORBITS_DIR. A snapshot made before 16 Nov holds
  cometels.json only (no ORBIT_FILE column); it is completed with an allcometels_<date>.parquet found next to it or in
  known_check's cache (ALLCOMETS_DIRS), exactly as known_mpc.merge_comets does. Nothing is fetched or written here.
- Positions: kete (n-body, light time), one cone per mover. First from the Earth's centre at the mover's mean time and
  place (COARSE_ARCSEC: room for SPHEREx's parallax); then, for the movers with a comet in that cone, at each dot's time
  and place on the mover's straight line (known.dot_places), seen from SPHEREx (X_SC..VZ_SC of the dot's photo from the
  known step's session files cache/mpc/<zone>/run_<first obs_id>.json or cache/known_check/observers.parquet) or the
  Earth's centre where those are missing.
- Matching: the known step's own rule (suspects.select), on these comet predictions: suspects.known_certain on the
  nearest comet (within SUSPECT_KNOWN_CERTAIN_ARCSEC at the mover's time, or within KNOWN_ARCSEC with our motion measured
  at its rate and in its direction), or known.certain_known (known.judge at every dot's time: the rule known_check.py
  uses for `known_body`).
A failure (no snapshot, no kete, an orbit kete can't move) leaves the rows unmatched, with a warning: the list then keeps
the order it had before this exemption.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from . import config as C
from . import log

L = log.get("known_comets")

COARSE_ARCSEC = C.KNOWN_NEARBY_ARCSEC  # first pass, from the Earth's centre: SPHEREx is ≤ ~10″/Δ(AU) off that
FINE_ARCSEC = C.KNOWN_NEARBY_ARCSEC    # second pass, from SPHEREx: the bodies near each dot (the rule then needs ≤ 30″)
CHUNK_DAYS = 2.0                       # movers within this many days share one n-body step
ALLCOMETS_DIRS = (C.MPC_ORBITS_DIR, "cache/known_check")  # where an allcometels_<date>.parquet may already be
_SNAP = re.compile(r"^(cometels|allcometels)_\d{4}-\d{2}-\d{2}\.parquet$")


def _newest(store, prefix: str, dirs) -> str | None:
    best = None
    for d in dirs:
        try:
            names = store.listdir(d)
        except Exception:  # noqa: BLE001 (a missing folder)
            continue
        for n in names:
            n = str(n).rstrip("/").rsplit("/", 1)[-1]
            m = _SNAP.match(n)
            if m and m.group(1) == prefix and (best is None or n > best[1]):
                best = (d, n)
    return f"{best[0]}/{best[1]}" if best else None


def comet_orbits(store) -> tuple[pd.DataFrame | None, str]:
    """(kete's comet table, label): the newest cometels snapshot, completed with allcometels when it predates 16 Nov.
    (None, reason) without one."""
    from . import known_mpc as KM

    cur = _newest(store, "cometels", (C.MPC_ORBITS_DIR,))
    if cur is None:
        return None, f"no {C.MPC_ORBITS_DIR}/cometels_<date>.parquet"
    com = store.read_parquet(cur)
    label = cur.rsplit("/", 1)[-1]
    if KM.ORBIT_FILE not in com.columns:
        every = _newest(store, "allcometels", ALLCOMETS_DIRS)
        if every is None:
            L.warning("known comets: %s has cometels.json only and no allcometels snapshot is here: comets last seen "
                      "before the MPC's current epoch (306P among them) are missing", label)
        else:
            com = KM.merge_comets(com, store.read_parquet(every))
            label += " + " + every.rsplit("/", 1)[-1]
    com = com.reset_index(drop=True)
    com["desig"] = com["desig"].astype(str)
    return com, label


def kete_comets(com: pd.DataFrame) -> tuple[list, np.ndarray]:
    """(kete states named by row number, display names) for a comet table (known_mpc.build_states, object_table)."""
    from . import known_mpc as KM

    return KM.build_states(com), KM.object_table(com)["name"].to_numpy(object)


def _cone_hits(kete, states: list, items: list[tuple], radius_arcsec: float) -> tuple[list, list]:
    """items: (mjd, ra, dec, sc or None). Bodies inside a cone of radius_arcsec around each item, seen from its observer
    at its time: [(item index, state row, ra, dec, dra_arcsec_h, ddec_arcsec_h)]. Returns (hits, states moved to the
    last chunk) so a caller can go on from there."""
    from skyshift_core import known_index as KI

    hits: list[tuple] = []
    order = sorted(range(len(items)), key=lambda i: items[i][0])
    i = 0
    while i < len(order):
        j = i
        while j < len(order) and items[order[j]][0] - items[order[i]][0] <= CHUNK_DAYS:
            j += 1
        ch = order[i:j]
        i = j
        obs = [KI.observer_state(kete, items[k][0], items[k][3]) for k in ch]
        states = kete.propagate_n_body(states, min(o.jd for o in obs), suppress_errors=True)
        states = [s for s in states if s.is_finite]
        keys: dict[tuple, list[int]] = {}
        fovs = []
        for k, o in zip(ch, obs):
            fov = kete.ConeFOV(kete.Vector.from_ra_dec(float(items[k][1]), float(items[k][2])), radius_arcsec / 3600.0, o)
            fovs.append(fov)
            keys.setdefault((round(o.jd, 9), round(float(items[k][1]), 7), round(float(items[k][2]), 7)), []).append(k)
        for ss in kete.fov_state_check(states, fovs):
            if not len(ss.states):
                continue
            p = ss.fov.pointing
            jd, pra, pdec = round(ss.fov.observer.jd, 9), float(p.ra), float(p.dec)
            ks = keys.get((jd, round(pra, 7), round(pdec, 7)))
            if ks is None:  # the pointing's round trip is off in the last digit: the nearest centre at that time
                near = [(abs(kk[1] - pra) + abs(kk[2] - pdec), v) for kk, v in keys.items() if kk[0] == jd]
                if not near:
                    continue
                ks = min(near, key=lambda x: x[0])[1]
            rd = np.asarray(ss.ra_dec_with_rates, float).reshape(-1, 4)  # deg, deg, deg/day (RA × cos dec), deg/day
            for st, (sra, sdec, dra, ddec) in zip(ss.states, rd):
                for k in ks:
                    hits.append((k, int(st.desig), float(sra) % 360.0, float(sdec), float(dra) * 150.0, float(ddec) * 150.0))
    return hits, states


KNOWN_OBS_PARQUET = "cache/known_check/observers.parquet"


def session_observers(store, zone: str, obs_ids) -> dict[str, dict]:
    """obs_id -> X_SC..VZ_SC from the known step's session files of these exposures (cache/mpc/<zone>/run_<first
    obs_id of the session>.json `observers`) and, for any exposure still missing, cache/known_check/observers.parquet."""
    from . import known_mpc as KM
    from .store import photolist_rel

    want = {str(o) for o in obs_ids if o is not None}
    if not want:
        return {}
    out: dict[str, dict] = {}
    keys = (*KM.SC_POS, *KM.SC_VEL)
    try:
        p = store.read_parquet(photolist_rel(zone), columns=["obs_id", "session", "mjd"])
    except Exception:  # noqa: BLE001
        p = None
    if p is not None and len(p):
        p = p.sort_values(["session", "mjd", "obs_id"])
        first = p.groupby("session")["obs_id"].first()
        sess = dict(zip(p["obs_id"].astype(str), p["session"]))
        for s in sorted({sess[o] for o in want if o in sess}):
            rel = f"cache/mpc/{zone}/run_{first[s]}.json"
            try:
                if not store.exists(rel):
                    continue
                obs = store.read_json(rel).get("observers") or {}
            except Exception:  # noqa: BLE001
                continue
            for o, v in obs.items():
                if o in want and v.get("observer") == "spacecraft" and all(v.get(k) is not None for k in keys):
                    out[o] = {k: float(v[k]) for k in keys}
    miss = want - out.keys()
    if miss:
        try:
            if store.exists(KNOWN_OBS_PARQUET):
                pq = store.read_parquet(KNOWN_OBS_PARQUET, columns=["obs_id", *keys])
                sub = pq[pq["obs_id"].astype(str).isin(miss)]
                for r in sub.itertuples(index=False):
                    vals = [getattr(r, k) for k in keys]
                    if all(v is not None and np.isfinite(float(v)) for v in vals):
                        out[str(r.obs_id)] = {k: float(v) for k, v in zip(keys, vals)}
        except Exception:  # noqa: BLE001
            pass
    return out


def _list(v) -> list:
    if v is None or isinstance(v, float):
        return []
    return list(v)


def session_motion(m: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Per row of a suspects or movers table, its own session's (rate ″/h, PA °), never a track's chord:
    session_rate_arcsec_h / session_pa_deg where the table has them (track_grades writes the chord into rate_arcsec_h /
    pa_deg on linked rows), else rate_arcsec_h / pa_deg off a track; NaN on a track without them."""
    n = len(m)
    rate = pd.to_numeric(m["rate_arcsec_h"], errors="coerce").to_numpy(float, copy=True) if "rate_arcsec_h" in m \
        else np.full(n, np.nan)
    pa = pd.to_numeric(m["pa_deg"], errors="coerce").to_numpy(float, copy=True) if "pa_deg" in m else np.full(n, np.nan)
    on_trk = (pd.to_numeric(m["tracklet_id"], errors="coerce").to_numpy(float) >= 0) if "tracklet_id" in m \
        else np.zeros(n, bool)
    if "session_rate_arcsec_h" in m:
        srate = pd.to_numeric(m["session_rate_arcsec_h"], errors="coerce").to_numpy(float)
        spa = pd.to_numeric(m["session_pa_deg"], errors="coerce").to_numpy(float) if "session_pa_deg" in m \
            else np.full(n, np.nan)
        use_s = on_trk | np.isfinite(srate)
        rate[use_s] = srate[use_s]
        pa[use_s] = spa[use_s]
    else:
        rate[on_trk] = np.nan
        pa[on_trk] = np.nan
    return rate, pa


def match(m: pd.DataFrame, states: list, names: np.ndarray, observers: dict[str, dict] | None = None,
          kete=None) -> tuple[np.ndarray, dict]:
    """Per row of m (suspects or movers rows: ra, dec, mjd, rate_arcsec_h, pa_deg, n_exposures; session, motion_snr,
    dot_mjd, dot_obs when present), the name of the known comet its place, rate and direction match ("" none), by the
    known step's rule (module doc), with the row's in-session motion (session_motion). observers: obs_id ->
    X_SC..VZ_SC (missing: the Earth's centre)."""
    from . import known as K
    from . import suspects as SU

    n = len(m)
    out = np.full(n, "", dtype=object)
    st = {"movers": n, "near_coarse": 0, "matched": 0}
    if n == 0 or not len(states):
        return out, st
    if kete is None:
        import kete
    m = m.reset_index(drop=True)
    observers = observers or {}
    ra, dec, mjd = (m[c].to_numpy(np.float64) for c in ("ra", "dec", "mjd"))
    coarse, _ = _cone_hits(kete, list(states), [(mjd[i], ra[i], dec[i], None) for i in range(n)], COARSE_ARCSEC)
    rows_near: dict[int, set] = {}
    for k, row, *_ in coarse:
        rows_near.setdefault(k, set()).add(row)
    st["near_coarse"] = len(rows_near)
    if not rows_near:
        return out, st
    sub = sorted(rows_near)
    ms = m.iloc[sub].reset_index(drop=True)
    if "session" not in ms:
        ms["session"] = 0
    rate, pa = session_motion(ms)
    ms["rate_arcsec_h"] = rate
    ms["pa_deg"] = pa
    items, owner = [], []
    for i, r in enumerate(ms.itertuples()):
        times = np.asarray(_list(getattr(r, "dot_mjd", None)), np.float64)
        obs = _list(getattr(r, "dot_obs", None))
        if times.size == 0 or not np.isfinite(times).all():
            times, obs = np.array([float(r.mjd)]), [None]
        if len(obs) != times.size:
            obs = [None] * times.size
        t_u, first = np.unique(np.round(times, 7), return_index=True)
        times, obs = times[first], [obs[j] for j in first]
        pra, pdec = K.dot_places(float(r.ra), float(r.dec), float(r.mjd), rate[i], pa[i], times)
        for t, o, a, d in zip(times, obs, pra, pdec):
            items.append((float(t), float(a), float(d), observers.get(str(o)) if o is not None else None))
            owner.append(i)
    want = sorted({row for s in rows_near.values() for row in s})
    fine, _ = _cone_hits(kete, [states[r] for r in want], items, FINE_ARCSEC)
    rec = []
    for k, row, kra, kdec, dra, ddec in fine:  # row = int(st.desig), already the index in states / names
        i = owner[k]
        rec.append({"name": str(names[row]), "ra": kra, "dec": kdec, "mjd": items[k][0], "dra_arcsec_h": dra,
                    "ddec_arcsec_h": ddec, "v": np.nan, "session": int(ms["session"].iloc[i])})
    if not rec:
        return out, st
    pred = pd.DataFrame.from_records(rec)
    near = SU.nearest_known(ms, pred, search_arcsec=FINE_ARCSEC)
    place = SU.known_certain(ms, near)
    sure = K.certain_known(ms, pred)["known_name"].to_numpy(object)
    got = np.where(sure != "", sure, np.where(place, near["name"].to_numpy(object), ""))
    out[np.asarray(sub)] = got
    st["matched"] = int((out != "").sum())
    return out, st


def zone_comets(store, zone: str, susp: pd.DataFrame, orbits: tuple | None = None) -> tuple[dict[str, str], dict]:
    """{suspects id: comet name} for the zone's rows that match a known comet (match), and counts. orbits: (states,
    names, label) already built (kete_comets), else from the store's snapshot. Any failure: {} and the reason."""
    st: dict = {"movers": int(len(susp))}
    if len(susp) == 0:
        return {}, st
    try:
        if orbits is None:
            orbits = load_orbits(store)
        if orbits is None:
            st["error"] = "no comet orbits"
            return {}, st
        states, names, label = orbits
        st["orbits"] = label
        obs_ids = {o for v in susp.get("dot_obs", pd.Series([], dtype=object)) for o in _list(v)}
        observers = session_observers(store, zone, obs_ids) if obs_ids else {}
        st["observers_spherex"] = len(observers)
        got, mst = match(susp, states, names, observers)
        st.update(mst)
    except Exception as e:  # noqa: BLE001
        L.warning("known comets %s: not matched (%s): movers keep the existing rb_score rule", zone, str(e)[:200])
        st["error"] = str(e)[:200]
        return {}, st
    ids = susp["id"].astype(str).to_numpy()
    return {ids[i]: str(got[i]) for i in np.flatnonzero(got != "")}, st


def load_orbits(store) -> tuple | None:
    """(kete states, names, label) of the store's newest comet snapshot, or None with a warning."""
    com, label = comet_orbits(store)
    if com is None or not len(com):
        L.warning("known comets: %s: no comet exempt from rb_score", label)
        return None
    states, names = kete_comets(com)
    L.info("known comets: %s, %d comets", label, len(com))
    return states, names, label
