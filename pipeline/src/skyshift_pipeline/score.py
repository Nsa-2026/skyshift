"""Step 5, `score` (gate G2): do we re-find the known asteroids?

Units: one known object in one session (the zone's time runs) where it was predicted inside
>= SCORE_MIN_EXPOSURES exposures, at least SCORE_EDGE_PX from the edge and inside the zone.
- recall: a mover of that session within MATCH_ARCSEC of the prediction moved (SkyBoT rates) to
  the mover's time;
- dot share: any dot within MATCH_ARCSEC in one of the unit's photos (detection vs linking);
- control: the same with each unit moved CONTROL_OFFSET_ARCSEC in a random direction (fixed seed),
  the chance-match rate.
Also per object (found in any of its sessions), recall by V bins with counts, and V < 12
(saturated; centroid on the wings) apart from 12 <= V < 16. G2 passes if the per-(object, session)
recall at V < 16 is >= G2_RECALL_MIN.
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd

from . import config as C
from . import log
from .known import load_predictions
from .sky import SkyTree, angsep, offset_radec
from .store import Store, dots_rel, photolist_rel, results_rel

L = log.get("score")


def score_units(pred: pd.DataFrame, zone: C.Zone, v_max: float, edge_px: float = C.SCORE_EDGE_PX,
                min_exposures: int = C.SCORE_MIN_EXPOSURES) -> pd.DataFrame:
    """Predictions eligible for scoring, with a `unit` id per (object, session)."""
    p = pred[(pred["v"] < v_max) & (pred["edge_px"] >= edge_px)
             & zone.contains(pred["ra"].to_numpy(float), pred["dec"].to_numpy(float))].copy()
    n_exp = p.groupby(["name", "session"])["obs_id"].transform("nunique")
    p = p[n_exp >= min_exposures].sort_values(["name", "session", "mjd"]).reset_index(drop=True)
    p["unit"] = p.groupby(["name", "session"], sort=False).ngroup().astype(np.int64)
    return p


def _moved(p: pd.DataFrame, dt_days: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    h = dt_days * 24.0
    return offset_radec(p["ra"].to_numpy(float), p["dec"].to_numpy(float),
                        p["dra_arcsec_h"].to_numpy(float) * h, p["ddec_arcsec_h"].to_numpy(float) * h)


def match_movers(p: pd.DataFrame, movers: pd.DataFrame, radius: float = C.MATCH_ARCSEC) -> np.ndarray:
    """True per prediction row if a mover of the same session lies within `radius` of where the
    object was at the mover's time."""
    hit = np.zeros(len(p), dtype=bool)
    if len(p) == 0 or len(movers) == 0:
        return hit
    tree = SkyTree(movers["ra"].to_numpy(float), movers["dec"].to_numpy(float))
    rate = np.hypot(p["dra_arcsec_h"].to_numpy(float), p["ddec_arcsec_h"].to_numpy(float))
    ms, mm = movers["session"].to_numpy(np.int64), movers["mjd"].to_numpy(float)
    mra, mdec = movers["ra"].to_numpy(float), movers["dec"].to_numpy(float)
    span_h = float(movers.groupby("session")["mjd"].agg(lambda x: x.max() - x.min()).max()) * 24.0 + 0.5
    ra, dec, mjd = p["ra"].to_numpy(float), p["dec"].to_numpy(float), p["mjd"].to_numpy(float)
    ps = p["session"].to_numpy(np.int64)
    dra, ddec = p["dra_arcsec_h"].to_numpy(float), p["ddec_arcsec_h"].to_numpy(float)
    for i in range(len(p)):
        cand = tree.query_radius(ra[i], dec[i], float(radius + rate[i] * span_h + 1.0))[0]
        if not cand:
            continue
        cand = np.asarray(cand)
        cand = cand[ms[cand] == ps[i]]
        if cand.size == 0:
            continue
        dt_h = (mm[cand] - mjd[i]) * 24.0
        pra, pdec = offset_radec(ra[i], dec[i], dra[i] * dt_h, ddec[i] * dt_h)
        if (angsep(pra, pdec, mra[cand], mdec[cand]) * 3600.0 <= radius).any():
            hit[i] = True
    return hit


def match_dots(p: pd.DataFrame, dots_by_photo: dict[tuple[str, int], SkyTree], radius: float = C.MATCH_ARCSEC) -> np.ndarray:
    """True per prediction row if a dot of the same photo lies within `radius`."""
    hit = np.zeros(len(p), dtype=bool)
    for i, (o, d, ra, dec) in enumerate(zip(p["obs_id"], p["detector"], p["ra"], p["dec"])):
        t = dots_by_photo.get((o, int(d)))
        if t is not None and t.query_radius(ra, dec, radius)[0]:
            hit[i] = True
    return hit


def control_shift(p: pd.DataFrame, seed: int = C.CONTROL_SEED, offset: float = C.CONTROL_OFFSET_ARCSEC) -> pd.DataFrame:
    """Every unit moved `offset` arcsec in one random direction (the same for all its photos)."""
    rng = np.random.default_rng(seed)
    units = p["unit"].to_numpy()
    uniq, inv = np.unique(units, return_inverse=True)
    ang = rng.uniform(0, 2 * np.pi, uniq.size)[inv]
    q = p.copy()
    q["ra"], q["dec"] = offset_radec(p["ra"].to_numpy(float), p["dec"].to_numpy(float),
                                     offset * np.sin(ang), offset * np.cos(ang))
    return q


def summarize_units(p: pd.DataFrame, mover_hit: np.ndarray, dot_hit: np.ndarray,
                    ctl_mover_hit: np.ndarray, ctl_dot_hit: np.ndarray) -> pd.DataFrame:
    q = p[["unit", "name", "number", "v", "mjd", "session"]].copy()
    q["mover"], q["dot"], q["ctl_mover"], q["ctl_dot"] = mover_hit, dot_hit, ctl_mover_hit, ctl_dot_hit
    return q.groupby("unit").agg(name=("name", "first"), number=("number", "first"), v=("v", "min"),
                                 session=("session", "first"), mjd=("mjd", "mean"), n_photos=("mjd", "size"), mover=("mover", "any"),
                                 dot=("dot", "any"), ctl_mover=("ctl_mover", "any"), ctl_dot=("ctl_dot", "any")).reset_index()


def _share(x) -> float | None:
    return round(float(np.mean(x)), 4) if len(x) else None


def _rates(u: pd.DataFrame) -> dict:
    return {"n": int(len(u)), "found": int(u["mover"].sum()), "recall": _share(u["mover"]),
            "dot_share": _share(u["dot"]), "control_recall": _share(u["ctl_mover"]),
            "control_dot_share": _share(u["ctl_dot"])}


def recall_report(units: pd.DataFrame, v_max: float = C.SCORE_V_MAX) -> dict:
    main = units[units["v"] < v_max]
    by_v = []
    for lo, hi in zip(C.V_BINS[:-1], C.V_BINS[1:]):
        u = units[(units["v"] >= lo) & (units["v"] < hi)]
        by_v.append({"v_min": None if lo < -50 else lo, "v_max": hi, **_rates(u)})
    obj = main.groupby("name").agg(mover=("mover", "any"), dot=("dot", "any"), ctl_mover=("ctl_mover", "any"),
                                   ctl_dot=("ctl_dot", "any")) if len(main) else main
    unsat = main[main["v"] >= C.SCORE_V_SATURATED]
    sat = main[main["v"] < C.SCORE_V_SATURATED]
    r = _rates(main)
    return {
        "v_max": v_max, "n_known": r["n"], "n_found": r["found"], "recall": r["recall"],
        "dot_share": r["dot_share"], "control_recall": r["control_recall"], "control_dot_share": r["control_dot_share"],
        "per_object": _rates(obj) if len(obj) else {"n": 0},
        f"v_{C.SCORE_V_SATURATED:g}_to_{v_max:g}": _rates(unsat),
        f"v_lt_{C.SCORE_V_SATURATED:g}_saturated": _rates(sat),
        "g2_threshold": C.G2_RECALL_MIN,
        "g2_pass": bool(r["recall"] is not None and r["recall"] >= C.G2_RECALL_MIN),
        "by_v": by_v,
    }


def run(store: Store, zone: C.Zone) -> dict:
    t0 = time.time()
    photos = store.read_parquet(photolist_rel(zone.name))
    movers = store.read_parquet(results_rel(zone.name, "movers.parquet"))
    pred = load_predictions(store, photos, zone)
    v_top = max(C.V_BINS[-1], C.SCORE_V_MAX)
    p = score_units(pred, zone, v_top)
    L.info("score %s: %d predictions inside photos, %d with V < %.0f in %d units", zone.name, len(pred), len(p), v_top, p["unit"].nunique() if len(p) else 0)
    # dots of just the photos we need
    trees: dict[tuple[str, int], SkyTree] = {}
    for o, d in set(zip(p["obs_id"], p["detector"].astype(int))):
        try:
            df = store.read_parquet(dots_rel(o, d))
            trees[(o, d)] = SkyTree(df["ra"].to_numpy(float), df["dec"].to_numpy(float))
        except FileNotFoundError:
            pass
    have = np.array([(o, int(d)) in trees for o, d in zip(p["obs_id"], p["detector"])], dtype=bool)
    p = p[have].reset_index(drop=True)  # only photos that have been through `dots`
    ctl = control_shift(p)
    units = summarize_units(p, match_movers(p, movers), match_dots(p, trees),
                            match_movers(ctl, movers), match_dots(ctl, trees))
    rep = recall_report(units)
    out = {"zone": zone.name, "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "match_arcsec": C.MATCH_ARCSEC, "edge_px": C.SCORE_EDGE_PX, "control_offset_arcsec": C.CONTROL_OFFSET_ARCSEC,
           "control_seed": C.CONTROL_SEED, "unit": f"one known object in one session with >= {C.SCORE_MIN_EXPOSURES} exposures covering it",
           "photos_with_predictions": int(len(trees)), **rep}
    store.write_json(results_rel(zone.name, "recall.json"), out)
    store.write_parquet(results_rel(zone.name, "recall_units.parquet"), units)
    L.info("score %s: recall %s (%d of %d object-sessions) at V<%.0f; per object %s; dots %s; control %s -> G2 %s",
           zone.name, rep["recall"], rep["n_found"], rep["n_known"], C.SCORE_V_MAX, rep["per_object"],
           rep["dot_share"], rep["control_recall"], "PASS" if rep["g2_pass"] else "FAIL")
    for b in rep["by_v"]:
        L.info("score %s:   V %s-%s: %s", zone.name, b["v_min"], b["v_max"], b)
    return {k: v for k, v in out.items() if k != "by_v"} | {"wall_s": round(time.time() - t0, 1)}
