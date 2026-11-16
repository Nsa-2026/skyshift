"""Collect a round's check records and print the report.

Run from anywhere:  python3 scripts/vet/round_report.py <round dir> [--json OUT]

Reads <round>/findings.json (the picks and the hidden controls) and <round>/out/batch_*/records.json.
Prints: records received, verdicts by kind, the controls' score, the survivors (interest medium/high or a real
unknown), the engine problems named most often, what could not be checked, and the tools used.
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import re

DROP = {"static_source", "artifact", "no_source", "engine_claim_wrong"}


def load(round_dir: str):
    F = {f["id"]: f for f in json.load(open(f"{round_dir}/findings.json"))}
    R, bad = {}, []
    for p in sorted(glob.glob(f"{round_dir}/out/batch_*/records.json")):
        try:
            for r in json.load(open(p)):
                if isinstance(r, dict) and r.get("id") in F:
                    R[r["id"]] = {**r, "_batch": os.path.basename(os.path.dirname(p))}
        except Exception as e:  # noqa: BLE001
            bad.append((p, repr(e)[:120]))
    return F, R, bad


def theme(text: str) -> str:
    """A coarse label for an engine problem or a missing check, for counting."""
    t = text.lower()
    for key, pats in (
        ("fixed star taken for a mover", ("static", "fixed star", "fixed source", "same spot", "other times", "star at")),
        ("blend of neighbours", ("blend", "between two", "neighbour", "neighbor", "midpoint")),
        ("masked / persistence pixels bias the position", ("persist", "masked", "mask")),
        ("hot pixel / detector defect / cosmic ray", ("hot pixel", "same detector pixel", "cosmic", "defect", "spike", "bad column")),
        ("bright-star halo, ghost or bleed", ("halo", "ghost", "bleed", "bloom", "diffraction", "glare")),
        ("motion not significant / rate wrong", ("not significant", "rate", "motion", "direction", "pa ")),
        ("unrelated sources paired (fast star)", ("pair", "unrelated", "two different", "39", "mismatch")),
        ("grade too high", ("grade",)),
        ("known body not recognised", ("known", "asteroid", "moon", "planet")),
        ("catalogue match missed", ("gaia", "catwise", "2mass", "catalog")),
        ("spectrum / classification wrong", ("spectr", "class", "label", "brown dwarf", "pah", "ice")),
    ):
        if any(p in t for p in pats):
            return key
    return "other"


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("round"); ap.add_argument("--json")
    a = ap.parse_args()
    F, R, bad = load(a.round)
    print(f"records {len(R)} of {len(F)} findings ({sum('truth' in f for f in F.values())} of them controls); unreadable files: {bad or 'none'}")
    missing = [i for i in F if i not in R]
    if missing:
        print("  no record for:", ", ".join(missing[:12]), "..." if len(missing) > 12 else "")
    print("\nVerdicts by kind (controls left out):")
    tab = collections.defaultdict(collections.Counter)
    for i, r in R.items():
        if "truth" not in F[i]:
            tab[F[i]["kind"]][r.get("verdict", "?")] += 1
    for k, c in tab.items():
        print(f"  {k:9s} n={sum(c.values()):3d}  " + ", ".join(f"{v} {n}" for v, n in c.most_common()))
    print("\nControls (hidden answers):")
    ok = n = 0
    for i, f in F.items():
        if "truth" in f and i in R:
            v = R[i].get("verdict")
            good = (v == "real_mover_known") if f["truth"] == "known_asteroid" else (v in ("static_source", "engine_claim_wrong"))
            n += 1; ok += good
            print(f"  {'OK  ' if good else 'MISS'} {i}  truth {f['truth']} ({f['truth_detail']})  ->  {v} ({R[i].get('confidence')})")
    print(f"  controls right: {ok}/{n}")
    print("\nMovers: the agents against the numeric rule (controls left out):")
    cross = collections.Counter()
    for i, r in R.items():
        f = F[i]
        if f["kind"] == "mover" and "truth" not in f:
            rule = f["why_picked"].split("numeric rule: ")[-1]
            cross[(rule, "dropped" if r.get("verdict") in DROP else r.get("verdict", "?"))] += 1
    for (rule, v), c in sorted(cross.items()):
        print(f"  rule {rule:8s} -> agent {v:20s} {c}")
    print("\nSurvivors (a real unknown mover, a real other object, or interest medium/high):")
    surv = [r for i, r in R.items() if "truth" not in F[i] and (r.get("verdict") in ("real_mover_unknown", "real_other", "real_known") or r.get("interest") in ("medium", "high"))]
    for r in sorted(surv, key=lambda r: ({"high": 0, "medium": 1}.get(r.get("interest"), 2), -float(r.get("confidence") or 0))):
        print(f"  {r['id']}  {r.get('kind')}  {r.get('verdict')}  conf {r.get('confidence')}  interest {r.get('interest')}\n      {str(r.get('what_it_is', ''))[:230]}")
    print("\nEngine problems named (count of findings, by theme):")
    th = collections.Counter(); ex = collections.defaultdict(list)
    for i, r in R.items():
        seen = set()
        for p in r.get("engine_problems") or []:
            t = theme(str(p))
            if t not in seen:
                seen.add(t); th[t] += 1; ex[t].append((i, str(p)))
    for t, c in th.most_common():
        print(f"  {c:3d}  {t}\n         e.g. {ex[t][0][0]}: {ex[t][0][1][:200]}")
    print("\nCould not check (count, by what):")
    cc = collections.Counter(); cex = {}
    for i, r in R.items():
        for c in r.get("could_not_check") or []:
            w = re.sub(r"\s+", " ", str(c.get("what", c) if isinstance(c, dict) else c))[:70].lower()
            cc[w] += 1; cex.setdefault(w, (i, c))
    for w, c in cc.most_common(25):
        print(f"  {c:3d}  {w}")
    tu = collections.Counter(t for r in R.values() for t in (r.get("tools_used") or []))
    nchecks = [len(r.get("checks") or []) for r in R.values()]
    print("\nTools used (findings):", dict(tu.most_common()))
    print(f"Checks per finding: median {sorted(nchecks)[len(nchecks) // 2] if nchecks else 0}, total {sum(nchecks)}")
    if a.json:
        json.dump({"records": R, "findings": F}, open(a.json, "w"), indent=1)


if __name__ == "__main__":
    main()
