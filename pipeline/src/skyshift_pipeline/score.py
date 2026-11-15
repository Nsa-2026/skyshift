"""Step 5, `score` (gate G2): do we re-find the known asteroids?

Units: one known object in one session (the zone's time runs) where it was predicted inside
>= SCORE_MIN_EXPOSURES exposures, at least SCORE_EDGE_PX from the edge and inside the zone.
- recall: a mover of that session, or a dot `tracklets` recovered on a track's predicted path in that session
  (tracklet_dots.parquet), within MATCH_ARCSEC of the prediction moved (the object's rates) to its time;
  recall_movers: movers alone (the measure before tracklets);
- dot share: any dot within MATCH_ARCSEC in one of the unit's photos (detection vs linking); free dot share: such a dot
  in no fixed group of link (members.parquet), the only kind link can pair into a mover. A dot inside a fixed group is
  a star's (the object merged into it, or its own dot absorbed by a fixed source): it says nothing about linking.
- miss: why a unit wasn't found, the first that applies: no_dot (no dot within MATCH_ARCSEC), fixed_dot (only dots
  inside fixed groups), free_dot (a free dot, but no mover or recovered dot);
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
from .known import PLANET_CLASSES, load_predictions
from .sky import SkyTree, angsep, offset_radec
from .store import Store, dots_rel, photolist_rel, results_rel

L = log.get("score")


def asteroids_only(pred: pd.DataFrame) -> pd.DataFrame:
    """The predictions without known_planets' planets and moons: recall is about asteroids and comets."""
    if len(pred) and "class" in pred:
        return pred[~pred["class"].isin(PLANET_CLASSES)].reset_index(drop=True)
    return pred


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


def match_dot_kinds(p: pd.DataFrame, dots_by_photo: dict[tuple[str, int], SkyTree],
                    in_fixed: dict[tuple[str, int], np.ndarray], radius: float = C.MATCH_ARCSEC) -> tuple[np.ndarray, np.ndarray]:
    """Per prediction row: (free, fixed) — a dot of the same photo within `radius` that is in no fixed group / one that is
    a fixed source's member. in_fixed: per photo, per dot row of its tree, True for a member (fixed_member_rows); a photo
    missing from it has free dots only."""
    free, fixed = np.zeros(len(p), dtype=bool), np.zeros(len(p), dtype=bool)
    for i, (o, d, ra, dec) in enumerate(zip(p["obs_id"], p["detector"], p["ra"], p["dec"])):
        t = dots_by_photo.get((o, int(d)))
        if t is None:
            continue
        hits = t.query_radius(ra, dec, radius)[0]
        if not hits:
            continue
        f = in_fixed.get((o, int(d)))
        member = f[np.asarray(hits, np.int64)] if f is not None else np.zeros(len(hits), bool)
        fixed[i], free[i] = bool(member.any()), bool((~member).any())
    return free, fixed


def fixed_member_rows(store: Store, zone: C.Zone, photos: set[tuple[str, int]]) -> dict[tuple[str, int], np.ndarray] | None:
    """Per photo (obs_id, detector) of `photos`: the dot rows (as in the dot file) that are members of a fixed source
    (link's members.parquet). None when the file is missing or unreadable: dots then have no kind."""
    rel = results_rel(zone.name, "members.parquet")
    try:
        if not store.exists(rel):
            return None
        m = store.read_parquet(rel, columns=["obs_id", "detector", "dot_row"])
    except Exception:  # noqa: BLE001  (an old or broken members table: recall without the dot kinds)
        L.warning("score %s: %s unreadable; dots within the match radius are not split into free and fixed", zone.name, rel,
                  exc_info=True)
        return None
    ob = m["obs_id"] if isinstance(m["obs_id"].dtype, pd.CategoricalDtype) else m["obs_id"].astype("category")
    names = pd.Index(ob.cat.categories.astype(str))
    key = ob.cat.codes.to_numpy(np.int64) * 8 + m["detector"].to_numpy(np.int64)
    ph = sorted(photos)
    code = names.get_indexer([o for o, _ in ph])
    want = np.array([c * 8 + int(d) for c, (_, d) in zip(code, ph) if c >= 0], np.int64)
    sel = np.isin(key, want)
    k, r = key[sel], m["dot_row"].to_numpy(np.int64)[sel]
    order = np.argsort(k, kind="stable")
    k, r = k[order], r[order]
    out: dict[tuple[str, int], np.ndarray] = {}
    if len(k):
        cuts = np.flatnonzero(np.diff(k)) + 1
        for kk, rr in zip(k[np.r_[0, cuts]], np.split(r, cuts)):
            out[(str(names[kk // 8]), int(kk % 8))] = rr
    return out


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


MISSES = ("no_dot", "fixed_dot", "free_dot")  # recall_units `miss`, the first that applies ("" = found)


def summarize_units(p: pd.DataFrame, mover_hit: np.ndarray, dot_hit: np.ndarray,
                    ctl_mover_hit: np.ndarray, ctl_dot_hit: np.ndarray,
                    rec_hit: np.ndarray | None = None, ctl_rec_hit: np.ndarray | None = None,
                    kinds: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None = None) -> pd.DataFrame:
    """Per unit: mover (a mover matched), recovered (a tracklets-recovered dot matched), found = either; ctl_* the same
    for the control; dot = any dot. kinds: (free, fixed, ctl_free, ctl_fixed) per prediction row (match_dot_kinds): then
    also dot_free / dot_fixed (a dot in no fixed group / one inside a fixed group, in any of its photos), their ctl_*
    twins and `miss` (MISSES, "" when found)."""
    q = p[["unit", "name", "number", "v", "mjd", "session"]].copy()
    q["mover"], q["dot"], q["ctl_mover"], q["ctl_dot"] = mover_hit, dot_hit, ctl_mover_hit, ctl_dot_hit
    q["recovered"] = rec_hit if rec_hit is not None else False
    q["ctl_recovered"] = ctl_rec_hit if ctl_rec_hit is not None else False
    extra = {}
    if kinds is not None:
        for c, v in zip(("dot_free", "dot_fixed", "ctl_dot_free", "ctl_dot_fixed"), kinds):
            q[c] = np.asarray(v, bool)
            extra[c] = (c, "any")
    u = q.groupby("unit").agg(name=("name", "first"), number=("number", "first"), v=("v", "min"),
                              session=("session", "first"), mjd=("mjd", "mean"), n_photos=("mjd", "size"), mover=("mover", "any"),
                              dot=("dot", "any"), ctl_mover=("ctl_mover", "any"), ctl_dot=("ctl_dot", "any"),
                              recovered=("recovered", "any"), ctl_recovered=("ctl_recovered", "any"), **extra).reset_index()
    u["found"] = u["mover"] | u["recovered"]
    u["ctl_found"] = u["ctl_mover"] | u["ctl_recovered"]
    if kinds is not None:
        u["miss"] = np.select([u["found"], ~u["dot"], ~u["dot_free"]], ["", "no_dot", "fixed_dot"], "free_dot").astype(object)
    return u


def _share(x) -> float | None:
    return round(float(np.mean(x)), 4) if len(x) else None


def _rates(u: pd.DataFrame) -> dict:
    """found / recall: a mover or a tracklets-recovered dot; *_movers: movers alone (the measure before tracklets)."""
    f = u["found"] if "found" in u else u["mover"]
    cf = u["ctl_found"] if "ctl_found" in u else u["ctl_mover"]
    out = {"n": int(len(u)), "found": int(f.sum()), "recall": _share(f),
           "found_movers": int(u["mover"].sum()), "recall_movers": _share(u["mover"]),
           "dot_share": _share(u["dot"]), "control_recall": _share(cf), "control_recall_movers": _share(u["ctl_mover"]),
           "control_dot_share": _share(u["ctl_dot"])}
    if "dot_free" in u:  # dots split into free ones and fixed sources' members (members.parquet)
        out.update(free_dot_share=_share(u["dot_free"]), control_free_dot_share=_share(u["ctl_dot_free"]))
    if "miss" in u:
        out["misses"] = {k: int((u["miss"] == k).sum()) for k in MISSES}
    return out


def recall_report(units: pd.DataFrame, v_max: float = C.SCORE_V_MAX) -> dict:
    main = units[units["v"] < v_max]
    by_v = []
    for lo, hi in zip(C.V_BINS[:-1], C.V_BINS[1:]):
        u = units[(units["v"] >= lo) & (units["v"] < hi)]
        by_v.append({"v_min": None if lo < -50 else lo, "v_max": hi, **_rates(u)})
    cols = [c for c in ("mover", "dot", "ctl_mover", "ctl_dot", "found", "ctl_found", "dot_free", "ctl_dot_free") if c in main]
    obj = main.groupby("name").agg(**{c: (c, "any") for c in cols}) if len(main) else main
    unsat = main[main["v"] >= C.SCORE_V_SATURATED]
    sat = main[main["v"] < C.SCORE_V_SATURATED]
    r = _rates(main)
    return {
        "v_max": v_max, "n_known": r["n"], "n_found": r["found"], "recall": r["recall"],
        "n_found_movers": r["found_movers"], "recall_movers": r["recall_movers"],
        "dot_share": r["dot_share"], "control_recall": r["control_recall"], "control_recall_movers": r["control_recall_movers"],
        "control_dot_share": r["control_dot_share"],
        **{k: r[k] for k in ("free_dot_share", "control_free_dot_share", "misses") if k in r},
        "per_object": _rates(obj) if len(obj) else {"n": 0},
        f"v_{C.SCORE_V_SATURATED:g}_to_{v_max:g}": _rates(unsat),
        f"v_lt_{C.SCORE_V_SATURATED:g}_saturated": _rates(sat),
        "g2_threshold": C.G2_RECALL_MIN,
        "g2_pass": bool(r["recall"] is not None and r["recall"] >= C.G2_RECALL_MIN),
        "by_v": by_v,
    }


def recovered_dots(store: Store, zone: C.Zone) -> pd.DataFrame:
    """tracklets' recovered dots (ra, dec, mjd, session); empty when the step hasn't run."""
    rel = results_rel(zone.name, "tracklet_dots.parquet")
    cols = ["ra", "dec", "mjd", "session"]
    if not store.exists(rel):
        return pd.DataFrame({c: pd.Series(dtype="float64") for c in cols})
    return store.read_parquet(rel, columns=cols)


def run(store: Store, zone: C.Zone, pred: pd.DataFrame | None = None) -> dict:
    """pred: known.load_predictions' result when already loaded (tiles.py loads it once, in parallel)."""
    t0 = time.time()
    photos = store.read_parquet(photolist_rel(zone.name))
    movers = store.read_parquet(results_rel(zone.name, "movers.parquet"))
    if pred is None:
        pred = load_predictions(store, photos, zone)
    pred = asteroids_only(pred)
    v_top = max(C.V_BINS[-1], C.SCORE_V_MAX)
    p = score_units(pred, zone, v_top)
    L.info("score %s: %d predictions inside photos, %d with V < %.0f in %d units", zone.name, len(pred), len(p), v_top, p["unit"].nunique() if len(p) else 0)
    # dots of just the photos we need
    trees: dict[tuple[str, int], SkyTree] = {}
    for o, d in set(zip(p["obs_id"], p["detector"].astype(int))):
        try:
            df = store.read_parquet(dots_rel(o, d), columns=["ra", "dec"])
            trees[(o, d)] = SkyTree(df["ra"].to_numpy(float), df["dec"].to_numpy(float))
        except FileNotFoundError:
            pass
    have = np.array([(o, int(d)) in trees for o, d in zip(p["obs_id"], p["detector"])], dtype=bool)
    p = p[have].reset_index(drop=True)  # only photos that have been through `dots`
    ctl = control_shift(p)
    rec = recovered_dots(store, zone)
    rows = fixed_member_rows(store, zone, set(trees))
    if rows is None:  # no members table: "a dot" only, as before
        dot, ctl_dot, kinds = match_dots(p, trees), match_dots(ctl, trees), None
    else:
        in_fixed = {}
        for k, t in trees.items():
            m = np.zeros(t.n, bool)
            r = rows.get(k)
            if r is not None:
                m[r[(r >= 0) & (r < t.n)]] = True
            in_fixed[k] = m
        kinds = (*match_dot_kinds(p, trees, in_fixed), *match_dot_kinds(ctl, trees, in_fixed))
        dot, ctl_dot = kinds[0] | kinds[1], kinds[2] | kinds[3]
    units = summarize_units(p, match_movers(p, movers), dot, match_movers(ctl, movers), ctl_dot,
                            match_movers(p, rec), match_movers(ctl, rec), kinds)
    rep = recall_report(units)
    out = {"zone": zone.name, "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "match_arcsec": C.MATCH_ARCSEC, "edge_px": C.SCORE_EDGE_PX, "control_offset_arcsec": C.CONTROL_OFFSET_ARCSEC,
           "control_seed": C.CONTROL_SEED, "unit": f"one known object in one session with >= {C.SCORE_MIN_EXPOSURES} exposures covering it",
           "found_by": "a mover, or a dot tracklets recovered on a track's predicted path (recall_movers: movers alone)",
           "dot_kinds": ("free: a dot in no fixed group of link (members.parquet), the kind link pairs into movers; fixed: a "
                         "fixed source's member (a star's dot, or the object's own dot absorbed by one)") if kinds else None,
           "recovered_dots": int(len(rec)), "photos_with_predictions": int(len(trees)), **rep}
    store.write_json(results_rel(zone.name, "recall.json"), out)
    store.write_parquet(results_rel(zone.name, "recall_units.parquet"), units)
    L.info("score %s: recall %s (%d of %d object-sessions; movers alone %s) at V<%.0f; per object %s; dots %s (free %s); "
           "misses %s; control %s -> G2 %s", zone.name, rep["recall"], rep["n_found"], rep["n_known"], rep["recall_movers"],
           C.SCORE_V_MAX, rep["per_object"], rep["dot_share"], rep.get("free_dot_share"), rep.get("misses"),
           rep["control_recall"], "PASS" if rep["g2_pass"] else "FAIL")
    for b in rep["by_v"]:
        L.info("score %s:   V %s-%s: %s", zone.name, b["v_min"], b["v_max"], b)
    return {k: v for k, v in out.items() if k != "by_v"} | {"wall_s": round(time.time() - t0, 1)}
