"""A learned real-or-bogus score for movers (`rb_score`, 0-1): it ORDERS movers inside their tier, it never removes,
demotes or changes a status.

    from skyshift_pipeline import rb_score as RB
    rb = RB.score_rows(suspects_rows)          # one value per row, NaN where the model can't speak (no dot lists)
    RB.reorder(candidates)                     # the exporter's last step: rb order inside each tier (see reorder)

The model is a small list of boosted trees in rb_model.json (next to this file), rebuilt by scripts/vet/rb_train.py
from local tables (every mover of the labelled zones, as if no body were known; real = an MPC body within 5″, rest =
none within 60″; the blind-round ids left out). It reads only the suspects row the exporter already has: link's
columns and the mover's own dot lists, never the known-body columns, the rate or the motion's significance (those
would teach it what main-belt asteroids look like and bury slow, unusual bodies). Features (FEATURES): S/N and flux,
dots, exposures and detectors, the dots' size against a star of the same S/N (trail-corrected), PSF chi2, the weakest
dot, whole-pixel dots, two exposures on one pixel, a detector seeing only one exposure, flux_ratio, dup_dots, the
coverage counts, still_free, paired_exposures, the peak and 3×3 flag bits, and the nearest catalogued or fixed source.

Exempt (rb_score null, they keep their place by the existing score): slow movers (in-session rate under
SLOW_ARCSEC_H, a motion under SLOW_SIGMA σ, or no rate from >= 2 exposures), wide ones (the `wide` warning or
reason: a comet's haze or a blend), and known comets (position, rate and direction match an MPC comet via
known_comets, or a `known_body` comet check). The training set holds few of these, so a learned score would bury
what we are hunting.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path

import numpy as np
import pandas as pd
from skyshift_core.flags import BLOOM, CROSSTALK, GHOST, GHOST_EXT, GHOST_FPA, HALO, MASK_BITS, OUTLIER, SAT_BITS, SOURCE

from . import vet

MODEL_PATH = Path(__file__).with_name("rb_model.json")
SLOW_ARCSEC_H = 10.0   # exempt: a distant body drifts under this within a session (40 AU at quadrature: ~4″/h)
SLOW_SIGMA = 3.0       # ... or its rate is under this many σ (candidates.RATE_SIGMA_MIN: the motion is not significant)
NEAR_SOURCE_CAP = 60.0  # near_source_arcsec: none within this (or unknown) counts as this far
WIDE_CODES = ("wide", "wide_blend")

FEATURES = ("log_snr", "log_flux", "n_dots", "n_exposures", "n_det", "only_long", "has_pair", "two_detectors",
            "size_ratio_min", "size_ratio_med", "size_ratio_max", "wide_ratio_med", "wide_ratio_min", "chi2_med",
            "chi2_max", "log_snr_min", "whole_share", "same_pixel_px", "cross_detector", "flux_ratio", "dup_dots",
            "cover_exposures", "missed_exposures", "edge_exposures", "still_free", "paired_exposures",
            "fpk_source_all", "fpk_outlier_share", "fpk_mask_any", "fpk_sat_any", "flag_halo", "flag_bloom",
            "flag_ghost", "flag_crosstalk", "near_source")


def _col(s: pd.DataFrame, name: str) -> np.ndarray:
    """A numeric column, NaN where missing (older tables lack still_free, paired_exposures, near_source_arcsec)."""
    if name not in s:
        return np.full(len(s), np.nan)
    return pd.to_numeric(s[name], errors="coerce").to_numpy(np.float64)


def has_dots(s: pd.DataFrame) -> np.ndarray:
    """Rows that carry link's dot lists (every list column present and non-empty)."""
    if not vet.has_dot_lists(s):
        return np.zeros(len(s), bool)
    return np.array([v is not None and not isinstance(v, float) and len(v) > 0 for v in s["dot_det"]], bool)


def features(s: pd.DataFrame) -> pd.DataFrame:
    """One row of numbers per suspects (or movers) row, columns FEATURES, index = s's. NaN where a row lacks the
    input (the trees send it the way the training data's majority went)."""
    s = s.reset_index(drop=True)
    n = len(s)
    out = pd.DataFrame(index=pd.RangeIndex(n), columns=list(FEATURES), dtype=np.float64)
    if n == 0:
        return out
    out["log_snr"] = np.log10(np.clip(_col(s, "snr"), 1.0, None))
    out["log_flux"] = np.log10(np.clip(_col(s, "flux_ujy"), 1.0, None))
    out["n_dots"] = _col(s, "n_dots")
    out["n_exposures"] = _col(s, "n_exposures")
    out["two_detectors"] = (s["evidence"].astype(str) == "two_detectors").to_numpy(np.float64) if "evidence" in s else np.nan
    for c in ("flux_ratio", "dup_dots", "cover_exposures", "missed_exposures", "edge_exposures", "still_free",
              "paired_exposures"):
        out[c] = _col(s, c)
    ns = _col(s, "near_source_arcsec")
    out["near_source"] = np.where(np.isfinite(ns), np.minimum(ns, NEAR_SOURCE_CAP), NEAR_SOURCE_CAP)
    f3 = _col(s, "flags")
    f3 = np.where(np.isfinite(f3), f3, 0).astype(np.int64) & 0xFFFFFFFF
    out["flag_halo"] = (f3 & HALO) != 0
    out["flag_bloom"] = (f3 & BLOOM) != 0
    out["flag_ghost"] = (f3 & (GHOST | GHOST_FPA | GHOST_EXT)) != 0
    out["flag_crosstalk"] = (f3 & CROSSTALK) != 0
    has = has_dots(s)
    if not has.any():
        return out.astype(np.float64)
    rows = np.flatnonzero(has)
    s = s.iloc[rows].reset_index(drop=True)  # the dot features: rows with dot lists only
    n = len(s)
    d = vet.explode(s)
    i = d["i"].to_numpy()
    idx = pd.RangeIndex(n)
    dot = pd.DataFrame(index=idx)

    def per(values, how):
        return getattr(pd.Series(values).groupby(i), how)().reindex(idx).to_numpy(np.float64)

    det = d["det"].to_numpy(np.int64)
    dets = pd.Series(det).groupby(i)
    dot["n_det"] = dets.nunique().reindex(idx).to_numpy(np.float64)
    dot["only_long"] = per(det >= 4, "min")
    pairs = pd.DataFrame({"i": i, "det": det}).drop_duplicates()
    pk = set(zip(pairs["i"], pairs["det"]))
    dot["has_pair"] = [float(any((k, dd) in pk and (k, dd + 3) in pk for dd in (1, 2, 3))) for k in range(n)]
    ratio = d["size"].to_numpy(np.float64) / vet.star_size(det, d["snr"].to_numpy(np.float64))
    dot["size_ratio_min"] = per(ratio, "min")
    dot["size_ratio_med"] = per(ratio, "median")
    dot["size_ratio_max"] = per(ratio, "max")
    chi = d["chi2"].to_numpy(np.float64)
    dot["chi2_med"] = per(chi, "median")
    dot["chi2_max"] = per(chi, "max")
    dot["log_snr_min"] = np.log10(np.clip(per(d["snr"].to_numpy(np.float64), "min"), 1.0, None))
    fpk = d["fpk"].to_numpy(np.int64)
    dot["fpk_source_all"] = per((fpk & SOURCE) != 0, "min")
    dot["fpk_outlier_share"] = per((fpk & OUTLIER) != 0, "mean")
    dot["fpk_mask_any"] = per((fpk & MASK_BITS & ~SAT_BITS) != 0, "max")
    dot["fpk_sat_any"] = per((fpk & SAT_BITS) != 0, "max")
    fl = vet.flaw_numbers(s, None)
    nd = _col(s, "n_dots")
    dot["whole_share"] = fl["whole_dots"].to_numpy(np.float64) / np.where(nd > 0, nd, np.nan)
    dot["wide_ratio_med"] = fl["wide_ratio_med"].to_numpy(np.float64)
    dot["wide_ratio_min"] = fl["wide_ratio_min"].to_numpy(np.float64)
    dot["same_pixel_px"] = np.minimum(fl["same_pixel_px"].to_numpy(np.float64), 100.0)
    dot["cross_detector"] = fl["cross_detector"].to_numpy(np.float64)
    for c in dot.columns:
        out.loc[rows, c] = dot[c].to_numpy(np.float64)
    return out.astype(np.float64)


# --- the model ------------------------------------------------------------------------------------
@functools.lru_cache(maxsize=4)
def load_model(path: str | None = None) -> dict:
    """rb_model.json: {"version", "features", "baseline", "trees": [{"feature", "threshold", "left", "right",
    "missing_left", "leaf", "value"}], "trained": {...}}; arrays per tree, node 0 the root."""
    m = json.loads(Path(path or MODEL_PATH).read_text())
    if tuple(m["features"]) != FEATURES:
        raise ValueError(f"rb model {path or MODEL_PATH} was trained on other features: rebuild it (scripts/vet/rb_train.py)")
    return m


def raw_predict(model: dict, X: np.ndarray) -> np.ndarray:
    """Sum of the trees' leaf values + the baseline (log-odds) for each row of X (columns in model['features'] order).
    A node sends x <= threshold left; NaN goes where missing_left says (sklearn HistGradientBoosting's rule)."""
    X = np.asarray(X, np.float64)
    raw = np.full(X.shape[0], float(model["baseline"]))
    rows = np.arange(X.shape[0])
    for t in model["trees"]:
        feat, thr = np.asarray(t["feature"], np.int64), np.asarray(t["threshold"], np.float64)
        left, right = np.asarray(t["left"], np.int64), np.asarray(t["right"], np.int64)
        mleft, leaf, val = np.asarray(t["missing_left"], bool), np.asarray(t["leaf"], bool), np.asarray(t["value"], np.float64)
        node = np.zeros(X.shape[0], np.int64)
        for _ in range(64):  # depth bound; a tiny tree ends long before
            act = ~leaf[node]
            if not act.any():
                break
            nd = node[act]
            x = X[rows[act], feat[nd]]
            go_left = np.where(np.isnan(x), mleft[nd], x <= thr[nd])
            node[act] = np.where(go_left, left[nd], right[nd])
        raw += val[node]
    return raw


def predict(model: dict, X: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-raw_predict(model, X)))


def score_rows(s: pd.DataFrame, model: dict | None = None) -> np.ndarray:
    """rb_score (0-1) per row of a suspects table; NaN for a row without dot lists (nothing the model was trained on)."""
    if len(s) == 0:
        return np.zeros(0)
    model = model or load_model()
    F = features(s)
    p = predict(model, F[list(FEATURES)].to_numpy(np.float64))
    return np.where(has_dots(s), p, np.nan)


# --- exemption and order --------------------------------------------------------------------------
def is_slow(rate, rate_err=None, n_exposures: int = 1) -> bool:
    """Slow as far as we can tell: a rate under SLOW_ARCSEC_H, or a motion not significant (rate under SLOW_SIGMA σ:
    the exporter's motion_not_significant rule, a distant body's in-session drift reads as noise), or no rate at all
    while seen in >= 2 exposures (an older table without link's rate). One exposure in two detectors has no motion to
    measure: nothing says it is slow."""
    if rate is None or not np.isfinite(float(rate)):
        return n_exposures >= 2
    r = float(rate)
    if r < SLOW_ARCSEC_H:
        return True
    return rate_err is not None and np.isfinite(float(rate_err)) and r < SLOW_SIGMA * float(rate_err)


def is_wide(c: dict) -> bool:
    """The `wide` warning or reason on the candidate (a comet's haze or a blend), from any check producer."""
    return any(x in WIDE_CODES for x in c.get("reasons") or []) or \
        any((k.get("code") if isinstance(k, dict) else None) in WIDE_CODES for k in c.get("checks") or [])


def is_comet(c: dict) -> bool:
    """A `known_body` check from a check producer matching a known comet."""
    return any(isinstance(k, dict) and k.get("code") == "known_body"
               and (k.get("values") or {}).get("body") == "comet"
               for k in c.get("checks") or [])


def exempt(c: dict) -> bool:
    """Not ordered by rb_score: not an open mover (a known body or a flaw is listed apart as it is), no rb_score
    (exempt at export, or an older file), wide or a known comet."""
    return c.get("kind") != "mover" or c.get("status", "open") != "open" or c.get("rb_score") is None \
        or is_wide(c) or is_comet(c)


def tier(c: dict) -> tuple:
    """The groups the list shows apart (contracts/api.md, the To check order): status, grade, a strong check, and the
    reason motion_not_significant (the site sets those aside). rb_score orders only inside one tier of open movers."""
    strong = any(isinstance(k, dict) and k.get("level") == "strong" for k in c.get("checks") or [])
    return (c.get("status", "open"), c.get("grade"), strong, "motion_not_significant" in (c.get("reasons") or []))


def reorder(cands: list[dict]) -> dict[str, int]:
    """Inside each tier of open movers (all zones together, as the site lists them): the exempt movers keep their score
    and so their rank; the others take the tier's other scores (the same values) in rb_score order, highest first
    (ties by the old score, then id). No score leaves its tier, no mover changes grade or status. A wide or comet one
    found since (a side file's `wide` or `known_body` comet check) gets rb_score null. Idempotent. Returns counts."""
    for c in cands:
        if c.get("kind") == "mover" and c.get("rb_score") is not None and (is_wide(c) or is_comet(c)):
            c["rb_score"] = None
    groups: dict[tuple, list[dict]] = {}
    for c in cands:
        if c.get("kind") == "mover":
            groups.setdefault(tier(c), []).append(c)
    moved = ranked = 0
    for g in groups.values():
        free = [c for c in g if not exempt(c)]
        ranked += len(free)
        if len(free) < 2:
            continue
        slots = sorted((float(c.get("score") or 0.0) for c in free), reverse=True)
        order = sorted(free, key=lambda c: (-float(c["rb_score"]), -float(c.get("score") or 0.0), c["id"]))
        for c, sc in zip(order, slots):
            if float(c.get("score") or 0.0) != sc:
                moved += 1
            c["score"] = sc
    return {"tiers": len(groups), "ranked": ranked, "exempt": sum(c.get("kind") == "mover" for c in cands) - ranked,
            "score_changed": moved}
