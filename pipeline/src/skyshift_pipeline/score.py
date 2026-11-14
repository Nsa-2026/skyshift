"""Step 5, `score` (gate G2): do we re-find the known asteroids?

Known objects brighter than SCORE_V_MAX predicted inside a photo, at least SCORE_EDGE_PX from its
edge and inside the zone, are grouped per (object, session): one "visit of an asteroid" is one
unit however many photos and detectors saw it. For each unit:
- recall: a mover within MATCH_ARCSEC of the prediction moved (SkyBoT rates) to the mover's time,
  with the mover within SAME_SESSION_DAYS of the prediction;
- dot share: any dot within MATCH_ARCSEC in one of the unit's photos (detection vs linking);
- control: the same with every prediction moved CONTROL_OFFSET_ARCSEC in a random direction
  (fixed seed), which gives the chance-match rate.
Recall by V bins (V5's chart). G2 passes if recall at V < 16 is >= G2_RECALL_MIN.
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd
from skyshift_core import sessions

from . import config as C
from . import log
from .known import load_predictions
from .sky import SkyTree, angsep, offset_radec
from .store import Store, dots_rel, photolist_rel, results_rel

L = log.get("score")


def score_units(pred: pd.DataFrame, zone: C.Zone, v_max: float, edge_px: float = C.SCORE_EDGE_PX) -> pd.DataFrame:
    """Predictions eligible for scoring, with a `unit` id per (object, session)."""
    p = pred[(pred["v"] < v_max) & (pred["edge_px"] >= edge_px)
             & zone.contains(pred["ra"].to_numpy(float), pred["dec"].to_numpy(float))].copy()
    p = p.sort_values(["name", "mjd"]).reset_index(drop=True)
    unit = np.zeros(len(p), dtype=np.int64)
    nxt = 0
    for _, idx in p.groupby("name", sort=False).indices.items():
        s = sessions(p["mjd"].to_numpy()[idx])
        unit[idx] = nxt + s - 1
        nxt += int(s.max())
    p["unit"] = unit
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
    search = radius + rate * C.SAME_SESSION_DAYS * 24.0 + 1.0
    mm = movers["mjd"].to_numpy(float)
    mra, mdec = movers["ra"].to_numpy(float), movers["dec"].to_numpy(float)
    ra, dec, mjd = p["ra"].to_numpy(float), p["dec"].to_numpy(float), p["mjd"].to_numpy(float)
    dra, ddec = p["dra_arcsec_h"].to_numpy(float), p["ddec_arcsec_h"].to_numpy(float)
    for i in range(len(p)):
        cand = tree.query_radius(ra[i], dec[i], float(search[i]))[0]
        if not cand:
            continue
        cand = np.asarray(cand)
        dt = mm[cand] - mjd[i]
        ok = np.abs(dt) <= C.SAME_SESSION_DAYS
        if not ok.any():
            continue
        cand, dt = cand[ok], dt[ok]
        pra, pdec = offset_radec(ra[i], dec[i], dra[i] * dt * 24.0, ddec[i] * dt * 24.0)
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
    q = p[["unit", "name", "number", "v", "mjd"]].copy()
    q["mover"], q["dot"], q["ctl_mover"], q["ctl_dot"] = mover_hit, dot_hit, ctl_mover_hit, ctl_dot_hit
    return q.groupby("unit").agg(name=("name", "first"), number=("number", "first"), v=("v", "min"),
                                 mjd=("mjd", "mean"), n_photos=("mjd", "size"), mover=("mover", "any"),
                                 dot=("dot", "any"), ctl_mover=("ctl_mover", "any"), ctl_dot=("ctl_dot", "any")).reset_index()


def _share(x: pd.Series) -> float | None:
    return round(float(x.mean()), 4) if len(x) else None


def recall_report(units: pd.DataFrame, v_max: float = C.SCORE_V_MAX) -> dict:
    main = units[units["v"] < v_max]
    by_v = []
    for lo, hi in zip(C.V_BINS[:-1], C.V_BINS[1:]):
        u = units[(units["v"] >= lo) & (units["v"] < hi)]
        by_v.append({"v_min": None if lo < -50 else lo, "v_max": hi, "n": int(len(u)),
                     "recall": _share(u["mover"]), "dot_share": _share(u["dot"]),
                     "control_recall": _share(u["ctl_mover"]), "control_dot_share": _share(u["ctl_dot"])})
    recall = _share(main["mover"])
    return {
        "v_max": v_max, "n_known": int(len(main)), "n_objects": int(main["name"].nunique()),
        "recall": recall, "dot_share": _share(main["dot"]),
        "control_recall": _share(main["ctl_mover"]), "control_dot_share": _share(main["ctl_dot"]),
        "g2_threshold": C.G2_RECALL_MIN,
        "g2_pass": bool(recall is not None and recall >= C.G2_RECALL_MIN),
        "by_v": by_v,
    }


def run(store: Store, zone: C.Zone) -> dict:
    t0 = time.time()
    photos = store.read_parquet(photolist_rel(zone.name))
    movers = store.read_parquet(results_rel(zone.name, "movers.parquet"))
    pred = load_predictions(store, photos)
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
           "control_seed": C.CONTROL_SEED, "unit": "one known object in one session",
           "photos_with_predictions": int(len(trees)), **rep}
    store.write_json(results_rel(zone.name, "recall.json"), out)
    store.write_parquet(results_rel(zone.name, "recall_units.parquet"), units)
    L.info("score %s: recall %s at V<%.0f over %d units (dots %s, control %s) -> G2 %s",
           zone.name, rep["recall"], C.SCORE_V_MAX, rep["n_known"], rep["dot_share"], rep["control_recall"],
           "PASS" if rep["g2_pass"] else "FAIL")
    return {k: v for k, v in out.items() if k != "by_v"} | {"wall_s": round(time.time() - t0, 1)}
