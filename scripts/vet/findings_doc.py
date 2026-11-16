"""Write the findings file of a round: every finding as a real object, an open case, or an engine mistake and why.

Called by compare_runs.py --doc. The main run decides the group; the other runs are shown beside it.
"""
from __future__ import annotations

import json
import os
from urllib.parse import urlencode

from compare_runs import SITE, control_ok, side

PLAIN = {
    "real_mover_known": "real moving body, already known",
    "real_mover_unknown": "real mover, in no list we have",
    "real_known": "real, the engine is right, already catalogued",
    "real_other": "real and interesting",
    "static_source": "fixed star or galaxy",
    "artifact": "instrument artifact",
    "no_source": "nothing there",
    "engine_claim_wrong": "real object, the engine's claim about it is wrong",
    "unsure": "could not be settled",
}
KIND = {"mover": "mover", "high_pm": "fast star", "spectral": "spectral lead", "shape": "shape"}
RUN_LABEL = {"agents": "Opus agents (round 1)", "opus-5-5_medium": "Opus", "sonnet-5-5_medium": "Sonnet",
             "haiku-5-5_high": "Haiku high", "haiku-5-5_medium": "Haiku medium",
             "sonnet-5-5_medium_v3a": "Sonnet, improved tools", "sonnet-5-5_medium_v3b": "Sonnet, improved tools (repeat)",
             "sonnet-5-5_medium_v3_x10": "Sonnet, 10 per agent", "sonnet-5-5_medium_v3_x10rev": "Sonnet, 10 per agent (reversed)",
             "haiku-5-5_high_v3": "Haiku high, improved tools"}


def _one_line(s, n=400) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1].rsplit(" ", 1)[0] + "…"


def _cands() -> dict:
    p = os.path.join(os.environ.get("SKYSHIFT_VET_DATA", os.path.expanduser("~/.cache/skyshift-vet/data")), "candidates.json")
    try:
        return {c["id"]: c for c in json.load(open(p))["candidates"]}
    except Exception:  # noqa: BLE001
        return {}


def _link(c) -> str:
    if not c:
        return ""
    q = urlencode({"ra": f"{c['ra']:.5f}", "dec": f"{c['dec']:.5f}", "clock": c["clock"], "det": c["det"]})
    return f" · [open in the viewer]({SITE}?{q}#/explore)"


def write(c: dict, names: list[str], main: str, out: str) -> None:
    F, runs, stats = c["findings"], c["runs"], c["stats"]
    cands = _cands()
    full = [n for n in names if stats[n]["records"] >= 0.9 * len(F)]  # runs that covered the whole round

    def best(i):  # the main run's record, or the first run that has one
        return next((runs[n][i] for n in [main] + names if i in runs[n]), None)

    def verdicts(i) -> str:
        parts = [f"{RUN_LABEL.get(n, n)}: {runs[n][i]['verdict']}" for n in names if i in runs[n]]
        sides = {side(runs[n][i]["verdict"]) for n in full if i in runs[n]}
        return " · ".join(parts) + (" · **the checkers disagree**" if len(sides) > 1 else "")

    def block(i) -> list[str]:
        r, f = best(i), F[i]
        L = [f"### `{i}`", f"{KIND[f['kind']]}{_link(cands.get(i))}", "",
             f"- **What it is:** {_one_line(r.get('what_it_is'))}",
             f"- **What settled it:** {_one_line(r.get('decisive'))}"]
        ep = [e for e in (r.get("engine_problems") or []) if str(e).strip()]
        if ep:
            L.append("- **Engine problem:** " + _one_line(ep[0], 500) + (f" (+{len(ep) - 1} more in the record)" if len(ep) > 1 else ""))
        L += [f"- **Verdicts:** {verdicts(i)}", ""]
        return L

    groups = {"good_new": [], "good_known": [], "open": [], "mistake": [], "control": []}
    for i, f in F.items():
        r = best(i)
        if r is None:
            continue
        v = r["verdict"]
        if "truth" in f:
            groups["control"].append(i)
        elif v in ("real_mover_unknown", "real_other") or (side(v) == "keep" and r.get("interest") in ("medium", "high")):
            groups["good_new"].append(i)
        elif side(v) == "keep":
            groups["good_known"].append(i)
        elif v == "unsure":
            groups["open"].append(i)
        else:
            groups["mistake"].append(i)
    order = {"high": 0, "medium": 1, "low": 2, "none": 3}
    for g in groups.values():
        g.sort(key=lambda i: (F[i]["kind"], order.get(best(i).get("interest"), 3), -float(best(i).get("confidence") or 0)))

    L = [f"# Vetting round 1: all {len(F)} findings, checked",
         "",
         f"Every finding of round 1 (16 Nov 2026) with what it really is and, where the engine was wrong, why. Each was checked "
         f"against the images, our own numbers and the catalogues with the tools in `scripts/vet/` and the `vet-finding` skill. "
         f"The group comes from the {RUN_LABEL.get(main, main)} run; the other checkers' verdicts are listed beside it. "
         f"Written by `scripts/vet/compare_runs.py --doc`; the full check records are in `~/.cache/skyshift-vet/rounds/r1/`.",
         "",
         "| Group | Findings |", "|---|---:|",
         f"| [Good: real and worth a look](#1-good-real-and-worth-a-look) | {len(groups['good_new'])} |",
         f"| [Good: real, but already known](#2-good-real-but-already-known) | {len(groups['good_known'])} |",
         f"| [Possible: could not be settled](#3-possible-could-not-be-settled) | {len(groups['open'])} |",
         f"| [Engine mistakes](#4-engine-mistakes) | {len(groups['mistake'])} |",
         f"| [Controls (answer known in advance)](#5-controls) | {len(groups['control'])} |",
         "",
         "## The checkers compared", "",
         "The same findings went to each checker. \"Same side\" means both keep, both drop, or both leave it open.", "",
         f"| Checker | Records | Controls right | Same verdict as {RUN_LABEL.get(main, main)} | Same side | Cost | Tool calls per finding |",
         "|---|---:|---:|---:|---:|---:|---:|"]
    for n in names:
        s, u = stats[n], stats[n]["usage"]
        agree = ("-", "-") if n == main else (f"{s['same_verdict']}/{s['compared']}", f"{s['same_side']}/{s['compared']}")
        L.append(f"| {RUN_LABEL.get(n, n)} | {s['records']} | {s['controls_ok']}/{s['controls']} | {agree[0]} | {agree[1]} | "
                 + (f"${u['usd']:.2f} | {u['tool_calls'] / max(1, s['records']):.1f} |" if u else "- | - |"))
    L += ["", "## All findings at a glance", "", "| Finding | Kind | Result | Checkers on the same side |", "|---|---|---|---:|"]
    title = {"good_new": "Good: worth a look", "good_known": "Good: already known", "open": "Possible", "mistake": "Engine mistake", "control": "Control"}
    for g in ("good_new", "good_known", "open", "mistake", "control"):
        for i in groups[g]:
            v = best(i)["verdict"]
            have = [n for n in full if i in runs[n]]
            same = sum(side(runs[n][i]["verdict"]) == side(v) for n in have)
            L.append(f"| `{i}` | {KIND[F[i]['kind']]} | {title[g]}: {PLAIN[v]} | {same}/{len(have)} |")
    heads = (("good_new", "1. Good: real and worth a look", "Real objects where our measurement adds something, or that deserve a human's time."),
             ("good_known", "2. Good: real, but already known", "The engine is right about these, and they are already in a catalogue or an orbit list."),
             ("open", "3. Possible: could not be settled", "The checks that would decide these could not be run, or pointed both ways."),
             ("mistake", "4. Engine mistakes", "Not what the engine says. Grouped by what the object really is."))
    for g, h, intro in heads:
        L += ["", f"## {h}", "", intro, ""]
        if not groups[g]:
            L += ["None.", ""]
        if g == "mistake":
            for v in ("artifact", "static_source", "engine_claim_wrong", "no_source"):
                ids = [i for i in groups[g] if best(i)["verdict"] == v]
                if ids:
                    L += [f"**{PLAIN[v].capitalize()} ({len(ids)})**", ""]
                    for i in ids:
                        L += block(i)
        else:
            for i in groups[g]:
                L += block(i)
    L += ["", "## 5. Controls", "", "Findings whose answer we knew in advance (known asteroids and fixed stars), mixed in unmarked to test the checkers.", "",
          "| Finding | Truth | " + " | ".join(RUN_LABEL.get(n, n) for n in names) + " |", "|---|---|" + "---|" * len(names)]
    for i in groups["control"]:
        f = F[i]
        cells = [("✓ " if control_ok(f, runs[n][i]["verdict"]) else "✗ ") + runs[n][i]["verdict"] if i in runs[n] else "-" for n in names]
        L.append(f"| `{i}` | {f['truth'].replace('_', ' ')} ({f['truth_detail']}) | " + " | ".join(cells) + " |")
    open(out, "w").write("\n".join(L) + "\n")
