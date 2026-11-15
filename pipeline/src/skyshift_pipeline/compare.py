"""Compare two runs' results/<zone>/ table by table (e.g. tiled vs untiled).

    uv run python -m skyshift_pipeline.compare ROOT_A ROOT_B --zone pilot

Tables are matched on their keys (sorted first; row order is not part of the contract except for members); floats
report how many values differ and the largest difference (positions in arcsec), other columns must be equal. JSON
reports are compared value by value (relative 1e-6; timestamps ignored). Exit status 1 if anything differs.
"""

from __future__ import annotations

import argparse
import json
import sys

import numpy as np
import pandas as pd

from .store import Store, results_rel

KEYS = {"fixed": ["source_id"], "members": ["source_id", "obs_id", "detector", "dot_row"],
        "movers": ["session", "mjd", "ra", "dec"], "freedots": ["obs_id", "detector", "x", "y"],
        "sources": ["source_id"], "crossids": ["source_id"],
        "classes": ["source_id"], "photoz": ["source_id"], "colours": ["source_id", "pair"],
        "lightcurves": ["source_id", "ref_id", "origin", "detector", "mjd", "flux_ujy"],
        "lightcurves_summary": ["source_id", "ref_id", "detector", "origin"], "astrometry": ["source_id"],
        "highpm_candidates": ["method", "source_id", "source_id_b"], "suspects": ["id"], "recall_units": ["unit"],
        "calib_photos": ["obs_id", "detector"]}
REPORTS = ("calib.json", "lightcurves_report.json", "astrometry_report.json", "photoz_report.json", "highpm_report.json",
           "recall.json", "suspects.json", "channels.json")
ARCSEC = {"ra", "dec", "ra_b", "dec_b", "gaia_ra", "gaia_dec"}
IGNORE = {"created", "generated", "finished"}


def _norm(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for c in df.columns:
        v = df[c]
        if isinstance(v.dtype, pd.CategoricalDtype) or (v.dtype == object and len(v) and isinstance(v.iloc[0], str)) \
                or str(v.dtype) in ("str", "string"):
            df[c] = v.astype(str)
    return df


def _values(x: np.ndarray) -> np.ndarray:
    if len(x) and isinstance(x[0], (list, np.ndarray)):
        parts = [np.asarray(v).ravel() for v in x]
        if any(p.dtype.kind in "OUS" for p in parts):  # lists of strings (movers' dot_obs, suspects' reasons)
            return np.concatenate([p.astype(str) for p in parts])
        return np.concatenate([p.astype(np.float64) for p in parts])
    return x


def table(a: Store, b: Store, zone: str, name: str) -> tuple[bool, str]:
    rel = results_rel(zone, f"{name}.parquet")
    ea, eb = a.exists(rel), b.exists(rel)
    if not (ea and eb):
        return ea == eb, f"{name}: {'missing in both' if ea == eb else 'missing in ' + ('B' if ea else 'A')}"
    x, y = _norm(a.read_parquet(rel)), _norm(b.read_parquet(rel))
    keys = [k for k in KEYS.get(name, []) if k in x.columns and k in y.columns]
    if set(x.columns) != set(y.columns):
        return False, f"{name}: columns differ: {sorted(set(x.columns) ^ set(y.columns))}"
    if len(x) != len(y):
        if keys:
            ka = set(map(tuple, x[keys].astype(str).to_numpy()))
            kb = set(map(tuple, y[keys].astype(str).to_numpy()))
            return False, f"{name}: {len(x)} vs {len(y)} rows; only A {len(ka - kb)}, only B {len(kb - ka)}"
        return False, f"{name}: {len(x)} vs {len(y)} rows"
    if keys and len(x):
        x = x.sort_values(keys, kind="stable").reset_index(drop=True)
        y = y.sort_values(keys, kind="stable").reset_index(drop=True)
    notes, same = [], True
    for c in x.columns:
        u, v = x[c].to_numpy(), y[c].to_numpy()
        if len(u) and isinstance(u[0], (list, np.ndarray)) and [len(q) for q in u] != [len(q) for q in v]:
            notes.append(f"{c}: list lengths differ")
            same = False
            continue
        u, v = _values(u), _values(v)
        if u.dtype.kind in "fc" or v.dtype.kind in "fc":
            u, v = u.astype(np.float64), v.astype(np.float64)
            d = np.where(np.isnan(u) & np.isnan(v), 0.0, np.abs(u - v))
            d = np.where(np.isnan(d), np.inf, d)
            n = int((d > 0).sum())
            if n:
                same = False
                mx = float(d.max()) * (3600.0 if c in ARCSEC else 1.0)
                rel_ = float(np.max(np.where(d > 0, d / np.maximum(np.abs(u), 1e-300), 0.0)))
                notes.append(f"{c}: {n} differ, max {mx:.3g}{'″' if c in ARCSEC else ''} (relative {rel_:.2g})")
        else:
            ne = u.astype(str) != v.astype(str)
            if ne.any():
                same = False
                notes.append(f"{c}: {int(ne.sum())} differ, e.g. {u[ne][:2]} vs {v[ne][:2]}")
    return same, f"{name}: {len(x)} rows, " + ("identical" if same else "; ".join(notes))


def _flat(o, p=""):
    if isinstance(o, dict):
        for k, v in o.items():
            if k not in IGNORE:
                yield from _flat(v, f"{p}.{k}")
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from _flat(v, f"{p}[{i}]")
    else:
        yield p, o


def report(a: Store, b: Store, zone: str, name: str) -> tuple[bool, str]:
    rel = results_rel(zone, name)
    ea, eb = a.exists(rel), b.exists(rel)
    if not (ea and eb):
        return ea == eb, f"{name}: {'missing in both' if ea == eb else 'missing in ' + ('B' if ea else 'A')}"
    fa, fb = dict(_flat(a.read_json(rel))), dict(_flat(b.read_json(rel)))
    diffs = []
    for k in sorted(set(fa) | set(fb)):
        x, y = fa.get(k, "<none>"), fb.get(k, "<none>")
        if isinstance(x, (int, float)) and isinstance(y, (int, float)) and not isinstance(x, bool):
            if x != y and not (x != x and y != y) and abs(x - y) > 1e-6 * max(abs(x), abs(y), 1e-12):
                diffs.append(f"{k}: {x} vs {y}")
        elif x != y:
            diffs.append(f"{k}: {str(x)[:60]} vs {str(y)[:60]}")
    return not diffs, f"{name}: " + ("identical" if not diffs else f"{len(diffs)} differ: " + " | ".join(diffs[:6]))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m skyshift_pipeline.compare", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root_a")
    ap.add_argument("root_b")
    ap.add_argument("--zone", required=True)
    args = ap.parse_args(argv)
    a, b = Store(args.root_a), Store(args.root_b)
    ok = True
    for name in KEYS:
        same, line = table(a, b, args.zone, name)
        ok &= same
        print(line)
    for name in REPORTS:
        same, line = report(a, b, args.zone, name)
        ok &= same
        print(line)
    print("SAME" if ok else "DIFFERENT")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
