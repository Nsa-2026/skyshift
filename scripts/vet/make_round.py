"""Pick the findings of a vetting round and write the batches for the scientist agents.

Run from pipeline/:  uv run python ../scripts/vet/make_round.py <round dir> [--per-batch 10]

Round 1 mix (100 findings + 10 hidden controls with a known answer):
  every fast star the engine calls "no catalogue lists this motion", every shape candidate, 15 spectral leads,
  64 movers of the prepared mover zones (those the numeric rule calls mover or unclear first, then some it calls
  static), and 10 controls: 5 movers within 2" of a known asteroid and 5 on a Gaia star (not in the site's list).
Writes <round>/findings.json (with the hidden truth; agents must not read it) and <round>/batches/batch_NN.json.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vetlib as V  # noqa: E402

MOVER_ZONES = ("hunt", "ecl17", "ecl05", "ecl10")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("round"); ap.add_argument("--per-batch", type=int, default=10)
    a = ap.parse_args()
    os.makedirs(f"{a.round}/batches", exist_ok=True); os.makedirs(f"{a.round}/select_img", exist_ok=True)
    rng = np.random.default_rng(1)
    cands = V.candidates()
    F = []
    for c in cands.values():
        if c["kind"] == "high_pm" and "same motion" not in c["why"] and (V.DATA / c["zone"] / "photos.parquet").exists():
            F.append({"id": c["id"], "kind": "high_pm", "why_picked": "engine: no catalogue lists this motion"})
        if c["kind"] == "shape":
            F.append({"id": c["id"], "kind": "shape", "why_picked": "every shape candidate"})
    sp = sorted([c for c in cands.values() if c["kind"] == "spectral" and c["zone"] in MOVER_ZONES], key=lambda c: -c["score"])
    picked, quota = [], {"brown_dwarf_like": 9, "ice": 3, "pah_33": 3, "other": 0}
    for c in sp:
        k = "brown_dwarf_like" if "brown_dwarf_like" in c["labels"] else "ice" if any("ice" in x for x in c["labels"]) \
            else "pah_33" if "pah_33" in c["labels"] else "other"
        if quota[k] > 0:
            quota[k] -= 1
            picked.append({"id": c["id"], "kind": "spectral", "why_picked": f"top score, {k}"})
    F += picked
    # movers: the numeric rule on every current suspect of the mover zones
    movers = []
    cache = f"{a.round}/movers_numbers.json"
    done = {m["id"]: m for m in json.load(open(cache))["movers_measured"]} if os.path.exists(cache) else {}
    with ThreadPoolExecutor(16) as ex:
        for z in MOVER_ZONES:
            mv, sus, ph = V.table(z, "movers"), V.table(z, "suspects"), V.table(z, "photos")
            cur = mv[mv.id.isin(set(sus.id))]
            for _, r in cur.iterrows():
                if r.id in done:
                    movers.append(done[r.id])
                    continue
                out = V.build_packet(r, ph, f"{a.round}/select_img/{r.id}.png", mode="blind", pool=ex)
                g = sus[sus.id == r.id].grade.iloc[0]
                movers.append({"id": r.id, "kind": "mover", "grade": g, "numbers": out["numbers"], "snr": float(r.snr)})
                print(r.id, g, out["numbers"], flush=True)
            # controls
            kn = mv[(mv.known_sep <= 2) & ~mv.is_suspect & (mv.snr.between(8, 40)) & (mv.cover_sessions >= 3)]
            st = mv[(mv.gaia_sep <= 1.0) & ~(mv.known_sep <= 60) & ~mv.is_suspect & (mv.cover_sessions >= 3)]
            for df, truth, n in ((kn, "known_asteroid", 2 if z != "hunt" else 1), (st, "gaia_star", 1 if z != "hunt" else 2)):
                for _, r in df.sample(min(n, len(df)), random_state=int(rng.integers(1e9))).iterrows():
                    F.append({"id": r.id, "kind": "mover", "why_picked": "control", "truth": truth,
                              "truth_detail": str(r.known_name) if truth == "known_asteroid" else f"Gaia star {r.gaia_sep:.2f}\" away, G={r.gaia_g:.1f}"})
    order = {"A": 0, "B": 1, "C": 2}
    live = sorted([m for m in movers if m["numbers"] != "static"], key=lambda m: (order.get(m["grade"], 3), -m["snr"]))
    stat = sorted([m for m in movers if m["numbers"] == "static"], key=lambda m: (order.get(m["grade"], 3), -m["snr"]))
    take = live[:54]
    take += stat[:64 - len(take)]
    for m in take:
        F.append({"id": m["id"], "kind": "mover", "why_picked": f"suspect grade {m['grade']}, numeric rule: {m['numbers']}"})
    json.dump({"movers_measured": movers}, open(f"{a.round}/movers_numbers.json", "w"), indent=1)
    # batches: kinds spread evenly, order shuffled so controls are not recognisable by position
    by = {}
    for f in F:
        by.setdefault(f["kind"], []).append(f)
    for v in by.values():
        rng.shuffle(v)
    nb = int(np.ceil(len(F) / a.per_batch))
    batches = [[] for _ in range(nb)]
    i = 0
    for k in sorted(by, key=lambda k: len(by[k])):
        for f in by[k]:
            batches[i % nb].append(f); i += 1
    for n, b in enumerate(batches):
        rng.shuffle(b)
        for f in b:
            f["batch"] = n
        json.dump([{"id": f["id"], "kind": f["kind"]} for f in b], open(f"{a.round}/batches/batch_{n:02d}.json", "w"), indent=1)
    json.dump(F, open(f"{a.round}/findings.json", "w"), indent=1)
    from collections import Counter
    print("findings", len(F), dict(Counter(f["kind"] for f in F)), "controls", sum("truth" in f for f in F), "batches", nb)
    print("current suspects by numeric rule:", dict(Counter(m["numbers"] for m in movers)), "of", len(movers))


if __name__ == "__main__":
    main()
