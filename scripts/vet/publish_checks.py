"""Turn the AI checker's records into a check producer's side file (contracts/api.md v3.24, Side files).

    python3 scripts/vet/publish_checks.py ~/.cache/skyshift-vet/rounds/r1 --out ~/skyshift-data/checks/ai.json

Every finding the checkers looked at gets one check, code `ai_review`, with what it is and what settled it:
  ok      real (a real mover, a real fast star, a real catalogued object)
  warn    not settled: a checker was unsure, or the checkers disagree
  strong  every checker drops it, but without the proof the fake rule needs (listed under "Less likely")
A proven fake gets `ai_fake_proven` (strong) instead, which the exporter's merge turns into status `flaw`: listed apart
from To check, never deleted. The fake rule (16 Nov, approved by the user): no checker keeps it, and two checkers drop it
with confidence >= 0.8; for a mover, the latest checker also names a measured proof (junk_proof).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from compare_runs import load_run  # noqa: E402

MIN_CONF = 0.8
NO_PROOF = (None, "", "none", "not_applicable")
# Latest harness first: its record gives the words. Movers: Sonnet high on the hard ones (routed), else Sonnet v4.
MOVER_RUNS = ["sonnet_routed_v4", "sonnet-5-5_medium_v4", "opus-5-5_medium", "sonnet-5-5_medium_v3a", "sonnet-5-5_medium_v3b"]
OTHER_RUNS = ["sonnet-5-5_medium_v3a", "opus-5-5_medium", "sonnet-5-5_medium_v3b"]
MODEL = {"sonnet_routed_v4": "Sonnet", "sonnet-5-5_medium_v4": "Sonnet", "opus-5-5_medium": "Opus",
         "sonnet-5-5_medium_v3a": "Sonnet", "sonnet-5-5_medium_v3b": "Sonnet"}
PROOF_TEXT = {
    "hot_pixel_seen_at_other_sky_positions": "a hot pixel: the same detector pixel lights up in photos of other parts of the sky",
    "same_detector_pixel_without_telescope_step": "the same detector pixel in exposures where the telescope did not step",
    "same_brightness_at_same_spot_at_other_times": "a fixed source: the same brightness at the same place on other days",
    "trail_across_the_stamp": "a trail across the picture (a satellite or a streak)",
    "one_pixel_spike_no_psf": "a one-pixel spike with none of a star's spread",
    "glow_or_ghost_tied_to_a_bright_star_or_detector_column": "a glow or ghost of a bright star, or a detector column",
    "nothing_at_the_position": "nothing at the position",
}
PROOF_LOOK = {
    "hot_pixel_seen_at_other_sky_positions": "In the Viewer's hours clock, step through the exposures: the dot stays on one place of the photo while the stars jump.",
    "same_detector_pixel_without_telescope_step": "In the Viewer's hours clock, step through the exposures: the dot stays on one place of the photo.",
    "same_brightness_at_same_spot_at_other_times": "In the Viewer's months clock, compare the visits: the same dot sits at the circle every time.",
    "trail_across_the_stamp": "In the Viewer, look at the exposure itself: a line crosses the circle.",
    "one_pixel_spike_no_psf": "In the Viewer, zoom on the dot: one bright pixel, no soft edges like the stars around it.",
    "glow_or_ghost_tied_to_a_bright_star_or_detector_column": "In the Viewer, zoom out: a bright star or a detector column sits next to it.",
    "nothing_at_the_position": "In the Viewer, look at the circle: nothing stands out there.",
}


def drops(kind: str, v: str) -> bool:
    """A verdict that says the finding is not what the engine claims. A fast star whose motion is real but mis-measured
    (engine_claim_wrong) is not a drop; a spectral or shape lead whose claim is false is."""
    if v in ("static_source", "artifact", "no_source"):
        return True
    return v == "engine_claim_wrong" and kind in ("spectral", "shape")


def keeps(v: str) -> bool:
    return v in ("real_mover_known", "real_mover_unknown", "real_known", "real_other")


def label(kind: str, r: dict) -> str:
    v = r["verdict"]
    if kind == "mover":
        return {"real_mover_unknown": "a real moving object, in no list we read", "real_mover_known": "a real moving object, already known",
                "unsure": "could not be settled", "static_source": "a fixed star or galaxy, not a mover",
                "artifact": "an instrument artifact, not a mover", "no_source": "nothing there"}.get(v, v)
    if kind == "high_pm":
        return {"real_other": "a real moving star; no catalogue we read lists its motion", "real_known": "a real moving star, already catalogued",
                "engine_claim_wrong": "a real moving star, but the engine's motion is off", "static_source": "two fixed sources, not one moving star",
                "unsure": "could not be settled"}.get(v, v)
    return {"real_other": "real, and not in the catalogues we read", "real_known": "real, and already catalogued",
            "engine_claim_wrong": "the engine's claim is wrong", "unsure": "could not be settled"}.get(v, v)


def one(text: str, n: int = 320) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= n else s[: n - 1].rsplit(" ", 1)[0] + "…"


def review(kind: str, recs: list[tuple[str, dict]]) -> dict:
    """The check for one finding from its records [(run, record)], latest harness first."""
    main_run, r = recs[0]
    vs = [x["verdict"] for _, x in recs]
    conf = [float(x.get("confidence") or 0) for _, x in recs]
    keep = [keeps(v) for v in vs]
    drop = [drops(kind, v) for v in vs]
    sure_drops = sum(d and c >= MIN_CONF for d, c in zip(drop, conf))
    proof = r.get("junk_proof")
    proven = (not any(keep) and sure_drops >= 2 and drop[0] and conf[0] >= MIN_CONF
              and (kind != "mover" or proof not in NO_PROOF))
    if proven:
        code, level = "ai_fake_proven", "strong"
    elif keeps(r["verdict"]) and not any(drop):
        code, level = "ai_review", "ok"
    elif all(drop):
        code, level = "ai_review", "strong"
    else:
        code, level = "ai_review", "warn"
    worth = level == "ok" and (r["verdict"] in ("real_mover_unknown", "real_other") or r.get("interest") in ("medium", "high"))
    same = sum(keeps(v) == keeps(r["verdict"]) and drops(kind, v) == drops(kind, r["verdict"]) for v in vs)
    models = sorted({MODEL.get(n, n) for n, _ in recs})
    head = (f"AI check ({' and '.join(models)}, {len(recs)} independent looks, {same} of {len(recs)} agree): "
            + ("worth a look: " if worth else "") + label(kind, r) + ".")
    why = PROOF_TEXT.get(proof) if kind == "mover" and code == "ai_fake_proven" else None
    text = " ".join(x for x in (head, one(r.get("what_it_is")), ("Proof: " + why + ".") if why else "",
                                "Settled by: " + one(r.get("decisive"), 260)) if x)
    look = PROOF_LOOK.get(proof, "") if code == "ai_fake_proven" and kind == "mover" else ""
    return {"code": code, "level": level, "text": text, "look": look,
            "values": {"verdict": r["verdict"], "confidence": round(conf[0], 2), "agree": same, "looks": len(recs),
                       "interest": r.get("interest"), "worth_a_look": worth}}


def catalogued(cat: dict, cid: str) -> str | None:
    """The engine's catalogue check, when it is strong: the object is already catalogued (e.g. CatWISE2020 lists the motion,
    which the AI checker's tools do not read). Its text, or None."""
    for c in (cat.get(cid) or {}).get("checks") or []:
        if c.get("code") == "catalogues_checked" and c.get("level") == "strong":
            return str(c.get("text") or "")
    return None


def build(round_dir: str, catalogue: dict | None = None) -> dict:
    F = {f["id"]: f for f in json.load(open(os.path.join(round_dir, "findings.json")))}
    names = list(dict.fromkeys(MOVER_RUNS + OTHER_RUNS))
    runs = {n: load_run(round_dir, n) for n in names if os.path.isdir(os.path.join(round_dir, "api", n))}
    out = {}
    for i, f in F.items():
        if "truth" in f:  # controls: their answer was known in advance
            continue
        order = MOVER_RUNS if f["kind"] == "mover" else OTHER_RUNS
        if f["kind"] == "mover" and "sonnet_routed_v4" in runs and i in runs["sonnet_routed_v4"]:
            order = [n for n in order if n != "sonnet-5-5_medium_v4"]  # the routed run is the v4 harness again, at high effort
        recs = [(n, runs[n][i]) for n in order if n in runs and i in runs[n] and runs[n][i].get("verdict")]
        if not recs:
            continue
        c = review(f["kind"], recs)
        cat = catalogued(catalogue or {}, i)
        if cat and c["level"] == "ok":  # the catalogue check read a catalogue the AI checker did not: not new after all
            c["text"] = c["text"].replace("worth a look: ", "").replace("no catalogue we read lists its motion", "its motion is catalogued")
            c["text"] += " Already catalogued: the engine's catalogue check found it (" + one(cat, 160) + ")."
            c["values"]["worth_a_look"] = False
        out[i] = {"checks": [c], "status": None, "known_name": None}
    return {"source": "ai", "made": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "candidates_file": "web/public/data/candidates.json", "candidates": out}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("round"); ap.add_argument("--out", required=True)
    ap.add_argument("--catalogue", default=os.path.expanduser("~/skyshift-data/checks/catalogue.json"),
                    help="the engine's catalogue side file: a strong catalogues_checked there means already catalogued")
    a = ap.parse_args()
    cat = json.load(open(a.catalogue)).get("candidates") if a.catalogue and os.path.exists(a.catalogue) else {}
    doc = build(os.path.expanduser(a.round), cat)
    os.makedirs(os.path.dirname(os.path.abspath(os.path.expanduser(a.out))), exist_ok=True)
    json.dump(doc, open(os.path.expanduser(a.out), "w"), indent=1, ensure_ascii=False)
    import collections
    n = collections.Counter((c["checks"][0]["code"], c["checks"][0]["level"]) for c in doc["candidates"].values())
    print(f"{len(doc['candidates'])} findings -> {a.out}: " + ", ".join(f"{k[0]} {k[1]} {v}" for k, v in sorted(n.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
