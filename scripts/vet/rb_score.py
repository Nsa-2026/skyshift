"""A real-or-bogus score for movers from table numbers alone (prototype; it ranks, it never removes).

Run from pipeline/:  uv run --with scikit-learn python ../scripts/vet/rb_score.py [--out scores.parquet] [--round DIR ...]

Training set: every mover of the prepared mover zones. Real = within 2" of a known body (asteroid, comet, planet,
moon); rest = no known body within 60" (mostly junk, with some unknown real ones: the labels are noisy on that
side). No known-body column is a feature. Boosted trees on the columns below; each zone is scored by a model that
never saw it. Prints the junk removed at several "share of real movers kept" levels, and the scores of findings with
a certain answer in the given rounds (blind rounds: findings.json with a `truth`).

Measured 16 Nov 2026 (hunt, ecl17, ecl05, ecl10; 6,546 real, 3,265 rest; without rate and motion significance):
AUC 0.990-0.998 per held-out zone; keeping 99% of real movers removes 85% of the rest, 99.9% removes 62%. Of the 51
real movers with a certain answer in rounds 1 and 2, one falls below the 99% line (a slow body, score 0.03; next a
comet at 0.21); of 23 certain junk, 20 fall below it. The model learns what main-belt asteroids look like, so slow
and unusual bodies must be exempt or ranked within rate bins before anything is hidden by it.

Features (all from movers.parquet and freedots.parquet): snr, log flux, n_dots, n_exposures, number of detectors,
only long-wave detectors, a detector pair seen, two_detectors evidence, dot size (min, median, max), PSF chi2 (median,
max), weakest dot S/N, a whole-pixel centroid, free dots on the same detector pixel (max, min over dots), two dots
on one pixel, distance to the nearest fixed source and Gaia star, that star's G, flux_ratio, dup_dots, cover / missed
/ edge exposures, still_free; and, in the "all numbers" variant only, rate, motion_snr, line residual.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vetlib as V  # noqa: E402

ZONES = ("hunt", "ecl17", "ecl05", "ecl10")
MOTION = ("rate", "motion_snr", "line_resid")
OPTIONAL = ("motion_snr", "line_resid_arcsec", "flux_ratio", "dup_dots", "still_free", "cover_exposures", "missed_exposures", "edge_exposures")


def _pixel_key(det, x, y):
    return np.asarray(det).astype(np.int64) * 10**8 + np.round(x).astype(np.int64) * 10**4 + np.round(y).astype(np.int64)


def mover_features(mv: pd.DataFrame, freedots: pd.DataFrame) -> pd.DataFrame:
    """One row of numbers per mover (index = mover id). Older tables lack some columns: those become -1."""
    for c in OPTIONAL:
        if c not in mv.columns:
            mv = mv.assign(**{c: np.nan})
    hist = pd.Series(_pixel_key(freedots.detector.to_numpy(), freedots.x.to_numpy(float), freedots.y.to_numpy(float))).value_counts()
    num = lambda v: float(v) if v is not None and np.isfinite(v) else -1.0  # noqa: E731
    rows = []
    for r in mv.itertuples():
        sz, ch, sn = np.asarray(r.dot_size, float), np.asarray(r.dot_chi2, float), np.asarray(r.dot_snr, float)
        x, y, dd = np.asarray(r.dot_x, float), np.asarray(r.dot_y, float), np.asarray(r.dot_det)
        h = np.array([hist.get(int(q), 0) for q in _pixel_key(dd, x, y)])
        dets = sorted({int(d) for d in dd})
        close = [np.hypot(x[i] - x[j], y[i] - y[j]) for i in range(len(x)) for j in range(i) if dd[i] == dd[j]]
        rows.append({
            "snr": r.snr, "flux": np.log10(max(r.flux_ujy, 1)), "n_dots": r.n_dots, "n_exposures": r.n_exposures, "n_det": len(dets),
            "only_long": int(all(d >= 4 for d in dets)), "has_pair": int(any((d + 3) in dets for d in dets if d <= 3)),
            "two_det_only": int(r.evidence == "two_detectors"),
            "size_min": sz.min(), "size_med": np.median(sz), "size_max": sz.max(), "chi2_med": np.median(ch), "chi2_max": ch.max(),
            "snr_min": sn.min(), "whole_px": int(np.any((np.abs(x - np.round(x)) < 0.02) & (np.abs(y - np.round(y)) < 0.02))),
            "pix_hist_max": h.max(), "pix_hist_min": h.min(), "same_pixel": int(bool(close) and min(close) < 0.6),
            "fixed_sep": min(num(r.fixed_sep) if num(r.fixed_sep) >= 0 else 60, 60), "gaia_sep": min(num(r.gaia_sep) if num(r.gaia_sep) >= 0 else 60, 60),
            "gaia_g": r.gaia_g if np.isfinite(r.gaia_g) else 25,
            "flux_ratio": num(r.flux_ratio), "dup_dots": max(num(r.dup_dots), 0), "cover_exposures": num(r.cover_exposures),
            "missed_exposures": num(r.missed_exposures), "edge_exposures": num(r.edge_exposures), "still_free": num(r.still_free),
            "rate": min(num(r.rate_arcsec_h), 1000), "motion_snr": num(r.motion_snr), "line_resid": num(r.line_resid_arcsec)})
    return pd.DataFrame(rows, index=mv.id)


def main():
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import roc_auc_score

    ap = argparse.ArgumentParser(); ap.add_argument("--out"); ap.add_argument("--round", nargs="*", default=[])
    ap.add_argument("--all-numbers", action="store_true", help="also use rate and motion significance (penalises slow bodies more)")
    a = ap.parse_args()
    X, y, zone = [], [], []
    for z in ZONES:
        mv = V.table(z, "movers")
        X.append(mover_features(mv, V.table(z, "freedots")))
        y += list(np.where(mv.known_sep <= 2, 1, np.where(~(mv.known_sep <= 60), 0, -1)))
        zone += [z] * len(mv)
    X, y, zone = pd.concat(X), np.array(y), np.array(zone)
    truths = {}
    for rd in a.round:
        truths.update({f["id"]: (f.get("truth"), f.get("why_picked", "")) for f in json.load(open(f"{rd}/findings.json")) if f.get("truth")})
    cols = [c for c in X.columns if a.all_numbers or c not in MOTION]
    use = (y >= 0) & ~X.index.isin(truths)  # findings with a hidden answer never train the model
    P = pd.Series(np.nan, index=X.index, name="score")
    for z in ZONES:  # each zone is scored by a model trained on the other zones
        m = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.06, max_leaf_nodes=15, l2_regularization=1.0, random_state=0)
        m.fit(X[cols][use & (zone != z)], y[use & (zone != z)])
        P[zone == z] = m.predict_proba(X[cols][zone == z])[:, 1]
        t = (zone == z) & (y >= 0)
        print(f"zone {z} held out: AUC {roc_auc_score(y[t], P[t]):.3f} ({int((y[t] == 1).sum())} real, {int((y[t] == 0).sum())} rest)")
    real, rest = P[y == 1], P[y == 0]
    for keep in (0.999, 0.995, 0.99, 0.98, 0.95):
        thr = float(np.quantile(real, 1 - keep))
        print(f"keep {keep:.1%} of real movers (score >= {thr:.3f}): removes {np.mean(rest < thr):.1%} of the rest")
    thr = float(np.quantile(real, 0.01))
    for want, lab in ((("real_mover", "known_asteroid"), "real, certain"), (("junk", "gaia_star"), "junk, certain")):
        ids = [i for i, (t, _) in truths.items() if t in want and i in P.index]
        if ids:
            s = P[ids].sort_values()
            print(f"{lab} ({len(ids)}): {int((s < thr).sum())} below the 99% line; lowest: " + "; ".join(f"{v:.2f} {truths[i][1]}" for i, v in s.head(4).items()))
    if a.out:
        P.to_frame().to_parquet(a.out)


if __name__ == "__main__":
    main()
