"""Sort a round's movers into three piles from several checkers' records. Nothing is deleted.

Run from anywhere:  python3 scripts/vet/triage.py <round dir> --runs RUN [RUN ...] [--min-confidence 0.8] [--out FILE.json]

  likely real   any checker gives real_mover_*
  open          everything else: a checker is unsure, the checkers disagree, a drop has no named proof or low confidence
  likely junk   EVERY checker drops it, each with confidence >= the minimum and a named proof (junk_proof)

A mover leaves the "to check" list only into "likely junk", and stays one click away there with its reason.
With hidden answers in findings.json (blind rounds) it prints how many real movers each pile holds.
"""
from __future__ import annotations

import argparse
import collections
import json

from compare_runs import DROP, load_run

NO_PROOF = (None, "", "none", "not_applicable")


def pile(recs: list[dict], min_conf: float) -> tuple[str, str]:
    if any(str(r["verdict"]).startswith("real_mover") for r in recs):
        return "likely real", "a checker calls it a real mover"
    drops = [r for r in recs if r["verdict"] in DROP]
    if len(drops) < len(recs):
        return "open", "a checker left it open"
    weak = [r for r in drops if float(r.get("confidence") or 0) < min_conf]
    if weak:
        return "open", f"a drop has confidence below {min_conf:g}"
    if any(r.get("junk_proof") in NO_PROOF for r in drops):
        return "open", "a drop names no proof"
    return "likely junk", "; ".join(sorted({str(r["junk_proof"]).replace("_", " ") for r in drops}))


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("round"); ap.add_argument("--runs", nargs="+", required=True)
    ap.add_argument("--min-confidence", type=float, default=0.8); ap.add_argument("--out")
    a = ap.parse_args()
    F = {f["id"]: f for f in json.load(open(f"{a.round}/findings.json"))}
    runs = [load_run(a.round, n) for n in a.runs]
    out, tab = {}, collections.defaultdict(collections.Counter)
    for i, f in F.items():
        recs = [R[i] for R in runs if i in R]
        if f["kind"] != "mover" or len(recs) < len(runs):
            continue
        p, why = pile(recs, a.min_confidence)
        out[i] = {"pile": p, "why": why, "verdicts": [r["verdict"] for r in recs], "confidence": [r.get("confidence") for r in recs]}
        tab[f.get("truth", "no known answer")][p] += 1
    print(f"{len(out)} movers checked by all of: {', '.join(a.runs)}  (a drop needs confidence >= {a.min_confidence:g} and a named proof from every checker)")
    for truth, c in sorted(tab.items()):
        print(f"  {truth:22s} likely real {c['likely real']:3d}   open {c['open']:3d}   likely junk {c['likely junk']:3d}")
    lost = [i for i, o in out.items() if o["pile"] == "likely junk" and F[i].get("truth") in ("real_mover", "known_asteroid")]
    for i in lost:
        print(f"  REAL MOVER IN THE JUNK PILE: {i} ({F[i]['why_picked']}; {F[i].get('truth_detail', '')[:70]}): {out[i]['why']}")
    if a.out:
        json.dump(out, open(a.out, "w"), indent=1)


if __name__ == "__main__":
    main()
