"""Vetting of the suspects: gates, grades and reasons (thresholds: config VET_*).

Runs in `suspects` on the movers that are left after the MPC, flag, bright-source, SOURCE-bit and Gaia/WISE cuts, never
before link's pairing, so known-asteroid recall is untouched. Each gate looks at the suspect's own dots (link's dot
lists in movers.parquet) or at zone-level maps:

- hot_pixel     a dot on a detector pixel (±1 px) where the zone's movers have dots in >= 3 exposures, or its free dots
                (freedots.parquet: every dot movers are paired from) in >= 5. Hot pixels NASA's HOT bit misses pair up
                with another such pixel one to three exposure steps (~115 px in y) away and make "movers".
- bad_column    a dot on a column (±0.5 px in x) holding >= 3 free dots of the same exposure and detector.
- edge          a dot within 6 px of the detector edge.
- masked_peak   a mask bit (contracts/flags.md, saturation aside) at a dot's peak: TRANSIENT, PERSIST, SUR_ERROR, ...
- sharp_outlier a dot sharper than a star (√(a·b) < 0.7 × the detector's stellar size at its S/N) with OUTLIER at the
                peak: cosmic-ray-like. OUTLIER alone is set on half the real asteroids' dots, so it never vetoes alone.
- flux_ratio    the brightest dot > 4× the faintest within one detector (a hit with a faint partner).
- red_photo     every dot from a photo that qa.py graded red (results/<zone>/qa_photos.parquet; skipped without it).
- 2mass         a 2MASS PSC source within 6″ (refcat): catalogued after all.

Grades: C = fails a gate or is catalogued (kept, with its reasons); B = passes everything, motion not shown (most suspects
are 2 dots of one session: at S/N 7–9 the centroid error exceeds the motion between exposures); A = B plus motion
>= 5σ over >= 2 sessions, which needs a multi-session linker the pipeline doesn't have yet, so no suspect is graded A.
`near_source_arcsec` (a refcat or fixed source 6–10″ away: a possible blend or PSF wing) is a warning, not a gate.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from . import config as C
from . import log
from .store import Store, results_rel

L = log.get("vet")

DOT_LISTS = ("dot_obs", "dot_det", "dot_x", "dot_y", "dot_mjd", "dot_flux", "dot_snr", "dot_size", "dot_chi2", "dot_fpk")


def has_dot_lists(m: pd.DataFrame) -> bool:
    return all(c in m.columns for c in DOT_LISTS)


def explode(m: pd.DataFrame) -> pd.DataFrame:
    """One row per dot of each mover: i (row in m), obs, det, x, y, mjd, flux, snr, size, chi2, fpk."""
    cols = {"obs": "dot_obs", "det": "dot_det", "x": "dot_x", "y": "dot_y", "mjd": "dot_mjd", "flux": "dot_flux",
            "snr": "dot_snr", "size": "dot_size", "chi2": "dot_chi2", "fpk": "dot_fpk"}
    if len(m) == 0 or not has_dot_lists(m):
        return pd.DataFrame({"i": pd.Series(dtype=np.int64), **{k: pd.Series(dtype=float) for k in cols}})
    n = np.array([len(v) for v in m["dot_det"]], np.int64)
    out = {"i": np.repeat(np.arange(len(m), dtype=np.int64), n)}
    for k, c in cols.items():
        vals = [np.asarray(v) for v in m[c]]
        out[k] = np.concatenate(vals) if len(vals) else np.zeros(0)
    d = pd.DataFrame(out)
    d["obs"] = d["obs"].astype(str)
    d["det"] = d["det"].astype(np.int64)
    d["fpk"] = d["fpk"].astype(np.int64) & 0xFFFFFFFF
    for c in ("x", "y", "mjd", "flux", "snr", "size", "chi2"):
        d[c] = d[c].astype(np.float64)
    return d


def star_size(det: np.ndarray, snr: np.ndarray) -> np.ndarray:
    """The detector's stellar √(a·b) (arcsec) at that S/N (config VET_STAR_SIZE_ARCSEC); NaN for an unknown detector."""
    det = np.asarray(det, np.int64)
    b = np.clip(np.searchsorted(np.asarray(C.VET_STAR_SNR_EDGES), np.asarray(snr, np.float64), side="right") - 1,
                0, len(C.VET_STAR_SNR_EDGES) - 1)
    out = np.full(det.size, np.nan)
    for d, row in C.VET_STAR_SIZE_ARCSEC.items():
        m = det == d
        out[m] = np.asarray(row)[b[m]]
    return out


def pixel_exposures(q: pd.DataFrame, ref: pd.DataFrame, r_px: float = C.VET_HOT_PX) -> np.ndarray:
    """For each query dot (det, x, y): the number of distinct exposures (ref `obs`) with a ref dot on the same detector
    within r_px in both x and y (the query's own dot counts when it is in ref)."""
    out = np.zeros(len(q), np.int64)
    if len(q) == 0 or len(ref) == 0:
        return out
    ref = ref[np.isfinite(ref["x"].to_numpy(np.float64)) & np.isfinite(ref["y"].to_numpy(np.float64))]
    qd = q["det"].to_numpy(np.int64)
    qxy = np.c_[q["x"].to_numpy(np.float64), q["y"].to_numpy(np.float64)]
    rdet = ref["det"].to_numpy(np.int64)
    rcode = pd.factorize(ref["obs"].astype(str))[0]
    rxy = np.c_[ref["x"].to_numpy(np.float64), ref["y"].to_numpy(np.float64)]
    for d in np.unique(qd):
        qi = np.flatnonzero((qd == d) & np.isfinite(qxy).all(axis=1))
        ri = np.flatnonzero(rdet == d)
        if qi.size == 0 or ri.size == 0:
            continue
        tree = cKDTree(rxy[ri])
        for k, nb in zip(qi, tree.query_ball_point(qxy[qi], r_px + 1e-6, p=np.inf)):
            out[k] = np.unique(rcode[ri[nb]]).size if nb else 0
    return out


def column_dots(q: pd.DataFrame, free: pd.DataFrame, r_px: float = C.VET_COLUMN_PX) -> np.ndarray:
    """For each query dot (obs, det, x): free dots of the same exposure and detector within r_px in x."""
    out = np.zeros(len(q), np.int64)
    if len(q) == 0 or len(free) == 0:
        return out
    groups = {k: np.sort(g["x"].to_numpy(np.float64)) for k, g in free.groupby([free["obs"].astype(str), free["det"].astype(np.int64)])}
    for k, (o, d, x) in enumerate(zip(q["obs"].astype(str), q["det"].to_numpy(np.int64), q["x"].to_numpy(np.float64))):
        xs = groups.get((o, int(d)))
        if xs is None or not np.isfinite(x):
            continue
        out[k] = int(np.searchsorted(xs, x + r_px + 1e-6, side="right") - np.searchsorted(xs, x - r_px - 1e-6, side="left"))
    return out


def load_free(store: Store, zone: C.Zone) -> pd.DataFrame | None:
    """freedots.parquet as obs, det, x, y (None if link didn't write it)."""
    from .link import FREEDOTS_NAME
    rel = results_rel(zone.name, FREEDOTS_NAME)
    if not store.exists(rel):
        return None
    f = store.read_parquet(rel, columns=["obs_id", "detector", "x", "y"])
    return pd.DataFrame({"obs": f["obs_id"].astype(str).to_numpy(), "det": f["detector"].to_numpy(np.int64),
                         "x": f["x"].to_numpy(np.float64), "y": f["y"].to_numpy(np.float64)})


def load_red(store: Store, zone: C.Zone) -> set | None:
    """{(obs_id, detector)} of the photos qa.py graded red; None if results/<zone>/qa_photos.parquet doesn't exist."""
    rel = results_rel(zone.name, C.VET_QA_NAME)
    if not store.exists(rel):
        return None
    try:
        q = store.read_parquet(rel, columns=["obs_id", "detector", "grade"])
    except Exception as e:  # noqa: BLE001  (a malformed QA file must not stop the suspects)
        L.warning("vet %s: %s unreadable, red_photo gate skipped: %s", zone.name, rel, e)
        return None
    r = q[q["grade"].astype(str) == "red"]
    return set(zip(r["obs_id"].astype(str), r["detector"].astype(int)))


def dot_gates(d: pd.DataFrame, movers_dots: pd.DataFrame, free: pd.DataFrame | None, red: set | None) -> pd.DataFrame:
    """Per-dot gate booleans for the suspects' dots d (explode's rows)."""
    g = pd.DataFrame(index=d.index)
    x, y = d["x"].to_numpy(np.float64), d["y"].to_numpy(np.float64)
    hi = C.VET_DETECTOR_PX - 1 - C.VET_EDGE_PX
    g["edge"] = (x < C.VET_EDGE_PX) | (x > hi) | (y < C.VET_EDGE_PX) | (y > hi)
    hot = pixel_exposures(d, movers_dots) >= C.VET_HOT_MIN_EXP_MOVERS
    if free is not None:
        hot |= pixel_exposures(d, free) >= C.VET_HOT_MIN_EXP_FREE
        g["bad_column"] = column_dots(d, free) >= C.VET_COLUMN_MIN_DOTS
    else:
        g["bad_column"] = False
    g["hot_pixel"] = hot
    fpk = d["fpk"].to_numpy(np.int64)
    g["masked_peak"] = (fpk & C.VET_PEAK_BITS) != 0
    sharp = d["size"].to_numpy(np.float64) < C.VET_SHARP_FRAC * star_size(d["det"].to_numpy(), d["snr"].to_numpy())
    g["sharp_outlier"] = sharp & ((fpk & C.VET_OUTLIER_BIT) != 0)
    g["red"] = (np.array([(o, int(k)) in red for o, k in zip(d["obs"], d["det"])], bool)
                if red is not None and len(d) else np.zeros(len(d), bool))
    return g


def flux_ratio_fail(d: pd.DataFrame) -> pd.Series:
    """Per suspect (index i): brightest > VET_FLUX_RATIO_MAX × faintest dot within one detector."""
    if len(d) == 0:
        return pd.Series(dtype=bool)
    f = d.groupby(["i", "det"])["flux"].agg(["max", "min", "size"])
    bad = (f["size"] >= 2) & ((f["min"] <= 0) | (f["max"] > C.VET_FLUX_RATIO_MAX * f["min"]))
    return bad.groupby(level="i").any()


def vet(s: pd.DataFrame, movers: pd.DataFrame, free: pd.DataFrame | None, red: set | None,
        catalogued: np.ndarray | None = None) -> tuple[pd.DataFrame, dict]:
    """Gates on the suspects s (rows of movers with the dot lists). Returns s with grade, reasons (list of gate names in
    VET_GATES order) and one boolean column per gate (gate_<name>), and stats: per gate the suspects failing it, the
    suspects whose first reason it is, and the grade counts. catalogued: per suspect, a 2MASS source within 6″."""
    s = s.copy().reset_index(drop=True)
    n = len(s)
    fail = {k: np.zeros(n, bool) for k in C.VET_GATES}
    st: dict = {"suspects_in": int(n), "dot_lists": bool(has_dot_lists(s)), "free_dots": None if free is None else int(len(free)),
                "qa_photos": red is not None}
    if n and not has_dot_lists(s):
        fail["not_vetted"][:] = True
    elif n:
        d = explode(s)
        nan_xy = ~(np.isfinite(d["x"].to_numpy()) & np.isfinite(d["y"].to_numpy()))
        md = explode(movers) if has_dot_lists(movers) else d
        g = dot_gates(d, md, free, red)
        i = d["i"].to_numpy()
        for name, col in (("hot_pixel", "hot_pixel"), ("bad_column", "bad_column"), ("edge", "edge"),
                          ("masked_peak", "masked_peak"), ("sharp_outlier", "sharp_outlier")):
            fail[name] = np.bincount(i, weights=g[col].to_numpy(float), minlength=n) > 0
        fail["not_vetted"] = np.bincount(i, weights=nan_xy.astype(float), minlength=n) > 0  # no pixel position for a dot
        fr = flux_ratio_fail(d)
        fail["flux_ratio"][fr.index[fr.to_numpy(bool)].to_numpy(np.int64)] = True
        if red is not None:
            nd = np.bincount(i, minlength=n)
            fail["red_photo"] = (np.bincount(i, weights=g["red"].to_numpy(float), minlength=n) == nd) & (nd > 0)
        st["dots"] = int(len(d))
        st["movers_dots"] = int(len(md))
    if catalogued is not None and n:
        fail["2mass"] = np.asarray(catalogued, bool)
    reasons = [[k for k in C.VET_GATES if fail[k][j]] for j in range(n)]
    s["grade"] = ["C" if r else "B" for r in reasons]
    s["reasons"] = reasons
    for k in C.VET_GATES:
        s[f"gate_{k}"] = fail[k]
    st["gates"] = {k: int(fail[k].sum()) for k in C.VET_GATES}
    st["first_reason"] = {k: int(sum(1 for r in reasons if r and r[0] == k)) for k in C.VET_GATES}
    st["grades"] = {g_: int(sum(1 for x in s["grade"] if x == g_)) for g_ in ("A", "B", "C")}
    return s, st
