"""Sort findings into easy and hard BEFORE any model runs, so each goes to the cheapest checker that is safe for it.

Run from pipeline/:  uv run python ../scripts/vet/route.py <round dir> --scores scores.parquet [--out routes.json]

Free inputs only: the learned real-or-bogus score (rb_score.py, or the engine's own when it exports one) and the
packet warnings that need no image. For movers:
  easy junk   score below LOW and a warning that explains it (hot pixel, same detector pixel, whole-pixel position,
              a fixed source within 9.5", between two stars, broad)
  easy real   score at or above HIGH, none of those warnings, 3+ exposures, a short-wave detector, not "two detectors
              at one moment" only, and moving 10"/h or more
  hard        everything else: score and warnings disagree, a mover beside a fixed source, one long-wave detector,
              slow bodies, two-exposure pairs, anything without a score
Fast stars, spectral leads and shapes have no score: they are "standard" (the easy checker; measured 94-96% on the
same side as Opus).

Measured 16 Nov 2026 on 119 movers with a known answer (rounds 1 and 2): easy junk 59 junk + 1 real, easy real 14
real, hard 17 junk + 36 real. Sonnet medium made no wrong drop in the easy groups (it kept the one real mover there);
both real movers it wrongly dropped were hard, and Sonnet high dropped none of the hard ones it saw.
api_round.py --routes sends "hard" to the hard model and effort and the rest to the easy one; an "easy real" that the
easy checker drops is checked again by the hard one.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

LOW, HIGH = 0.05, 0.9
PROOF = ("HOT PIXEL", "SAME DETECTOR PIXEL", "WHOLE-PIXEL POSITION")
AMBIGUOUS = ("FIXED SOURCE", "BETWEEN TWO STARS", "BROAD")
SLOW_ARCSEC_H = 10.0


def tier(score, warnings: list[str], n_exposures: int, detectors: list[int], evidence: str, rate) -> str:
    """easy junk | easy real | hard, from numbers that cost nothing. No score means hard."""
    if score is None or score != score:
        return "hard"
    flagged = any(w.startswith(PROOF + AMBIGUOUS) for w in warnings)
    if score < LOW and flagged:
        return "easy junk"
    plain = (n_exposures >= 3 and any(d <= 3 for d in detectors) and evidence != "two_detectors"
             and rate is not None and rate == rate and rate >= SLOW_ARCSEC_H)
    if score >= HIGH and not flagged and plain:
        return "easy real"
    return "hard"


def route_round(round_dir: str, scores: dict) -> dict:
    """{finding id: tier} for a round's findings.json."""
    import vetlib as V
    ctx, out = {}, {}
    for f in json.load(open(os.path.join(round_dir, "findings.json"))):
        i = f["id"]
        mv = V.mover_row(i) if f["kind"] == "mover" else None
        if mv is None:
            out[i] = "standard" if f["kind"] != "mover" else "hard"
            continue
        z = V.zone_of(i)
        if z not in ctx:
            ctx[z] = {"fixed": V.table(z, "fixed"), "refcat": V.table(z, "refcat"), "freedots": V.table(z, "freedots")}
        W = V.warnings_for(mv, V.mover_dots(mv), [], {}, ctx[z])
        out[i] = tier(scores.get(i), W, int(mv.n_exposures), [int(d) for d in mv.dot_det], str(mv.evidence), float(V._g(mv, "rate_arcsec_h")))
    return out


def main():
    import pandas as pd
    ap = argparse.ArgumentParser(); ap.add_argument("round"); ap.add_argument("--scores", required=True); ap.add_argument("--out")
    a = ap.parse_args()
    scores = pd.read_parquet(a.scores).iloc[:, 0].to_dict()
    routes = route_round(a.round, scores)
    c = collections.Counter(routes.values())
    print(f"{len(routes)} findings: " + ", ".join(f"{k} {v}" for k, v in c.most_common()))
    out = a.out or os.path.join(a.round, "routes.json")
    json.dump(routes, open(out, "w"), indent=1)
    print("wrote", out)


if __name__ == "__main__":
    main()
