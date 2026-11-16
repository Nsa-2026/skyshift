"""Compare the runs of one round (the agents' round and the API runs), and write the findings file.

Run from anywhere:  python3 scripts/vet/compare_runs.py <round dir> [--runs NAME ...] [--main NAME] [--doc OUT.md]

A run is "agents" (<round>/out/batch_*/records.json) or the name of a folder under <round>/api/.
Prints, per run: records, cost, the hidden controls' score, and how often it agrees with the main run
(same verdict; same side: keep / drop / unsure), by kind, with the findings where the sides differ.
--pairs: every pair of runs. --position FWD REV SINGLE: what the place in an agent's queue does (api_round --per-agent).
--doc writes one file with every finding: a real object, an open case, or an engine mistake and why.
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os

DROP = {"static_source", "artifact", "no_source", "engine_claim_wrong"}
KEEP = {"real_mover_known", "real_mover_unknown", "real_known", "real_other"}
SITE = "https://skyshift.web.app/"


def side(v) -> str:
    return "keep" if v in KEEP else "drop" if v in DROP else "unsure" if v == "unsure" else "none"


def load_run(round_dir: str, name: str) -> dict:
    paths = sorted(glob.glob(f"{round_dir}/out/batch_*/records.json")) if name == "agents" else [f"{round_dir}/api/{name}/records.json"]
    R = {}
    for p in paths:
        for r in json.load(open(p)):
            if isinstance(r, dict) and r.get("id") and r.get("verdict"):
                R[r["id"]] = r
    return R


def control_ok(f: dict, v) -> bool:
    """Is the verdict right for a finding with a known answer? (round 1 truths: known_asteroid, gaia_star; blind
    scenario rounds: real_mover = a known body with the known-body list hidden, junk)"""
    t = f["truth"]
    if t == "known_asteroid":
        return v == "real_mover_known"
    if t == "real_mover":
        return v in ("real_mover_unknown", "real_mover_known")
    if t == "junk":
        return v in DROP
    return v in ("static_source", "engine_claim_wrong")


def print_scenarios(c: dict) -> None:
    """Findings with a known answer, by scenario (why_picked): right / left open / wrong, per run."""
    F, runs = c["findings"], c["runs"]
    groups = collections.defaultdict(list)
    for i, f in F.items():
        if "truth" in f:
            groups[(f["truth"], f["why_picked"])].append(i)
    names = list(runs)
    print("\nKnown answers by scenario (right / open / wrong):")
    print(f"  {'scenario':46s} n  " + "  ".join(f"{n[:22]:>22s}" for n in names))
    tot = {n: [0, 0, 0] for n in names}
    for (truth, why), ids in sorted(groups.items()):
        cells = []
        for n in names:
            have = [i for i in ids if i in runs[n]]
            ok = sum(control_ok(F[i], runs[n][i]["verdict"]) for i in have)
            op = sum(runs[n][i]["verdict"] == "unsure" for i in have)
            cells.append(f"{ok} / {op} / {len(have) - ok - op}")
            for k, x in enumerate((ok, op, len(have) - ok - op)):
                tot[n][k] += x
        print(f"  {(truth + ': ' + why)[:46]:46s} {len(ids):2d} " + "  ".join(f"{x:>22s}" for x in cells))
    print(f"  {'all':46s}    " + "  ".join(f"{' / '.join(map(str, tot[n])):>22s}" for n in names))
    for n in names:
        bad = [i for i in F if "truth" in F[i] and i in runs[n] and not control_ok(F[i], runs[n][i]["verdict"]) and runs[n][i]["verdict"] != "unsure"]
        if bad:
            print(f"  {n} wrong: " + "; ".join(f"{i} ({F[i]['why_picked']}) -> {runs[n][i]['verdict']}" for i in bad))


def usage(round_dir: str, name: str) -> dict:
    p = f"{round_dir}/api/{name}/usage.json"
    return json.load(open(p))["total"] if os.path.exists(p) else {}


def compare(round_dir: str, names: list[str], main: str) -> dict:
    F = {f["id"]: f for f in json.load(open(f"{round_dir}/findings.json"))}
    runs = {n: load_run(round_dir, n) for n in names}
    ref = runs[main]
    out = {}
    for n, R in runs.items():
        ctl = [(i, control_ok(F[i], R[i]["verdict"])) for i in F if "truth" in F[i] and i in R]
        both = [i for i in R if i in ref and "truth" not in F[i]]
        same_v = sum(R[i]["verdict"] == ref[i]["verdict"] for i in both)
        same_s = sum(side(R[i]["verdict"]) == side(ref[i]["verdict"]) for i in both)
        by_kind = collections.defaultdict(lambda: [0, 0])
        for i in both:
            by_kind[F[i]["kind"]][0] += side(R[i]["verdict"]) == side(ref[i]["verdict"]); by_kind[F[i]["kind"]][1] += 1
        ref_keep = [i for i in both if side(ref[i]["verdict"]) == "keep"]
        ref_drop = [i for i in both if side(ref[i]["verdict"]) == "drop"]
        out[n] = {
            "records": len(R), "of": len(F), "usage": usage(round_dir, n),
            "controls_ok": sum(ok for _, ok in ctl), "controls": len(ctl), "controls_missed": [i for i, ok in ctl if not ok],
            "compared": len(both), "same_verdict": same_v, "same_side": same_s, "by_kind": {k: tuple(v) for k, v in by_kind.items()},
            "main_keeps_also_kept": (sum(side(R[i]["verdict"]) == "keep" for i in ref_keep), len(ref_keep)),
            "main_keeps_dropped": [i for i in ref_keep if side(R[i]["verdict"]) == "drop"],
            "main_drops_also_dropped": (sum(side(R[i]["verdict"]) == "drop" for i in ref_drop), len(ref_drop)),
            "main_drops_kept": [i for i in ref_drop if side(R[i]["verdict"]) == "keep"],
            "verdicts": dict(collections.Counter(R[i]["verdict"] for i in R if "truth" not in F[i])),
            "checks_median": sorted(len(r.get("checks") or []) for r in R.values())[len(R) // 2] if R else 0,
        }
    return {"findings": F, "runs": runs, "stats": out}


def print_stats(c: dict, main: str) -> None:
    for n, s in c["stats"].items():
        u = s["usage"]
        cost = f"${u['usd']:.2f}, {u['tool_calls']} tool calls, {u['seconds'] / max(1, s['records']):.0f} s per finding" if u else "run by agents"
        print(f"\n== {n}: {s['records']}/{s['of']} records; {cost}; checks per finding (median) {s['checks_median']}")
        print(f"  controls right {s['controls_ok']}/{s['controls']}" + (f"; missed: {', '.join(s['controls_missed'])}" if s["controls_missed"] else ""))
        print("  verdicts: " + ", ".join(f"{v} {k}" for v, k in sorted(s["verdicts"].items(), key=lambda x: -x[1])))
        if n == main:
            continue
        print(f"  against {main} ({s['compared']} findings): same verdict {s['same_verdict']}, same side {s['same_side']}; by kind: "
              + ", ".join(f"{k} {a}/{b}" for k, (a, b) in s["by_kind"].items()))
        a, b = s["main_keeps_also_kept"]; d, e = s["main_drops_also_dropped"]
        print(f"  real objects of {main} kept: {a}/{b}; dropped: {', '.join(s['main_keeps_dropped']) or 'none'}")
        print(f"  junk of {main} dropped: {d}/{e}; kept as real: {', '.join(s['main_drops_kept']) or 'none'}")


def print_pairs(c: dict, names: list[str]) -> None:
    """Every pair of runs: on how many shared findings they give the same verdict and the same side."""
    runs = c["runs"]
    print("\nPairs of runs (shared findings: same verdict / same side):")
    for x in range(len(names)):
        for y in range(x + 1, len(names)):
            A, B = runs[names[x]], runs[names[y]]
            both = [i for i in A if i in B]
            if both:
                v = sum(A[i]["verdict"] == B[i]["verdict"] for i in both); s = sum(side(A[i]["verdict"]) == side(B[i]["verdict"]) for i in both)
                print(f"  {names[x]:28s} {names[y]:28s} {len(both):4d}: {v} ({v / len(both):.0%}) / {s} ({s / len(both):.0%})")


def print_position(c: dict, fwd: str, rev: str, single: str, main: str) -> None:
    """Several findings per agent: what the place in the queue does. fwd and rev hold the same groups in opposite order."""
    F, runs = c["findings"], c["runs"]
    ref = runs[main]
    print(f"\nPlace in the agent's queue ({fwd} and {rev} together), against {main}:")
    print("  place  findings  tool calls  checks  same side as main  controls right  $ per finding")
    rows = collections.defaultdict(list)
    for n in (fwd, rev):
        for i, r in runs[n].items():
            rows[r.get("_position", 1)].append((i, r))
    for pos in sorted(rows):
        rs = rows[pos]
        cmpd = [(i, r) for i, r in rs if i in ref and "truth" not in F[i]]
        ctl = [(i, r) for i, r in rs if "truth" in F[i]]
        print(f"  {pos:5d}  {len(rs):8d}  {sum(len(r.get('_tool_calls') or []) for _, r in rs) / len(rs):10.1f}  "
              f"{sum(len(r.get('checks') or []) for _, r in rs) / len(rs):6.1f}  "
              f"{sum(side(r['verdict']) == side(ref[i]['verdict']) for i, r in cmpd):3d}/{len(cmpd):<3d}            "
              f"{sum(control_ok(F[i], r['verdict']) for i, r in ctl)}/{len(ctl)}             {sum(r['_usage']['usd'] for _, r in rs) / len(rs):.3f}")
    A, B = runs[fwd], runs[rev]
    both = [i for i in A if i in B]
    early_late = [(A[i], B[i]) if A[i]["_position"] < B[i]["_position"] else (B[i], A[i]) for i in both if A[i]["_position"] != B[i]["_position"]]
    if early_late:
        n = len(early_late)
        print(f"  the same finding seen early and late ({n} findings): same side {sum(side(e['verdict']) == side(l['verdict']) for e, l in early_late)}/{n}; "
              f"tool calls early {sum(len(e['_tool_calls']) for e, _ in early_late) / n:.1f}, late {sum(len(l['_tool_calls']) for _, l in early_late) / n:.1f}; "
              f"checks early {sum(len(e['checks']) for e, _ in early_late) / n:.1f}, late {sum(len(l['checks']) for _, l in early_late) / n:.1f}")
        for lab, pick in (("late keeps, early drops", lambda e, l: side(l["verdict"]) == "keep" and side(e["verdict"]) == "drop"),
                          ("late drops, early keeps", lambda e, l: side(l["verdict"]) == "drop" and side(e["verdict"]) == "keep")):
            ids = [e["id"] for e, l in early_late if pick(e, l)]
            print(f"  {lab}: {len(ids)}  {', '.join(ids[:8])}")
    if single in runs:
        S = runs[single]
        for n in (fwd, rev):
            sh = [i for i in runs[n] if i in S]
            print(f"  {n}: tool calls per finding {sum(len(runs[n][i]['_tool_calls']) for i in sh) / len(sh):.1f} against {sum(len(S[i]['_tool_calls']) for i in sh) / len(sh):.1f} alone; "
                  f"cost ${sum(runs[n][i]['_usage']['usd'] for i in sh):.2f} against ${sum(S[i]['_usage']['usd'] for i in sh):.2f} alone")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("round"); ap.add_argument("--runs", nargs="*"); ap.add_argument("--main")
    ap.add_argument("--doc"); ap.add_argument("--json"); ap.add_argument("--pairs", action="store_true")
    ap.add_argument("--scenarios", action="store_true", help="known answers by scenario, per run")
    ap.add_argument("--position", nargs=3, metavar=("FWD", "REV", "SINGLE"), help="two runs with the same groups in opposite order, and the one-per-agent run")
    a = ap.parse_args()
    names = a.runs or ((["agents"] if glob.glob(f"{a.round}/out/batch_*/records.json") else [])
                       + sorted(os.path.basename(os.path.dirname(p)) for p in glob.glob(f"{a.round}/api/*/records.json")))
    a.main = a.main or names[0]
    c = compare(a.round, names, a.main)
    print_stats(c, a.main)
    if a.scenarios:
        print_scenarios(c)
    if a.pairs:
        print_pairs(c, names)
    if a.position:
        print_position(c, *a.position, a.main)
    if a.json:
        json.dump(c["stats"], open(a.json, "w"), indent=1)
    if a.doc:
        import findings_doc
        findings_doc.write(c, names, a.main, a.doc)
        print(f"\nwrote {a.doc}")


if __name__ == "__main__":
    main()
