"""Pick a blind scenario round: real movers of known kinds, with the known-body answer hidden from the checker.

Run from pipeline/:  uv run python ../scripts/vet/make_scenarios.py <round dir> [--junk-from <earlier round dir>]

Every real one is a mover of ours within 2" of a known body (so the truth is certain), picked by what makes it
hard: faint with two exposures, close to a fixed source, one detector, two detectors at one moment, slow, fast,
a comet, a near-Earth object, a planet or moon, and some easy ones. Junk with a known answer is mixed in: movers
sitting on a Gaia star, and (with --junk-from) movers that every checker of an earlier round called an artifact.
Run it with api_round.py --hide-known: a checker that drops a real one here would drop a real unknown object.
Writes <round>/findings.json with the hidden truth.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vetlib as V  # noqa: E402

MOVER_ZONES = ("hunt", "ecl17", "ecl05", "ecl10")
# name, how many, the test on a known-body mover row (the first that fits claims the row)
REAL = (
    ("comet", 3, lambda k: k.known_class.astype(str).str.startswith("Comet")),
    ("planet or moon", 4, lambda k: k.known_class.isin(["planet", "moon"])),
    ("near-Earth object", 3, lambda k: k.known_class.isin(["Amor", "Apollo", "Aten"])),
    ("fast, over 60 arcsec/h", 4, lambda k: (k.known_rate > 60) & (k.n_exposures >= 2)),
    ("slow, under 8 arcsec/h", 5, lambda k: (k.known_rate < 8) & (k.n_exposures >= 2)),
    ("6-10 arcsec from a fixed source", 6, lambda k: k.fixed_sep.between(6, 10) & (k.n_exposures >= 2)),
    ("two detectors at one moment only", 5, lambda k: k.evidence == "two_detectors"),
    ("faint, two exposures", 6, lambda k: (k.snr < 8) & (k.n_exposures == 2)),
    ("one detector, D5 or D6", 4, lambda k: k.detectors.astype(str).isin(["5", "6"]) & (k.n_exposures >= 2)),
    ("easy: bright, 3+ exposures", 4, lambda k: (k.snr >= 15) & (k.n_exposures >= 3)),
)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("round"); ap.add_argument("--junk-from"); ap.add_argument("--stars", type=int, default=8)
    ap.add_argument("--artifacts", type=int, default=10)
    a = ap.parse_args()
    os.makedirs(a.round, exist_ok=True)
    rng = np.random.default_rng(2)
    mv = pd.concat([V.table(z, "movers").assign(zone=z) for z in MOVER_ZONES], ignore_index=True)
    used = set()
    if a.junk_from:
        used = {f["id"] for f in json.load(open(f"{a.junk_from}/findings.json"))}
    known = mv[(mv.known_sep <= 2) & ~mv.id.isin(used)]
    F, taken = [], set()
    for name, n, test in REAL:
        pool = known[test(known) & ~known.id.isin(taken)]
        # spread over the zones
        pick = pool.groupby("zone", group_keys=False).apply(lambda d: d.sample(min(len(d), n), random_state=int(rng.integers(1e9))))
        pick = pick.sample(min(n, len(pick)), random_state=int(rng.integers(1e9)))
        for r in pick.itertuples():
            taken.add(r.id)
            F.append({"id": r.id, "kind": "mover", "why_picked": name, "truth": "real_mover",
                      "truth_detail": f"{r.known_name} ({r.known_class}, V={r.known_v:.1f}, {r.known_rate:.0f}\"/h), S/N {r.snr:.0f}, "
                                      f"{int(r.n_exposures)} exposures, D{r.detectors}"})
    stars = mv[(mv.gaia_sep <= 1.0) & ~(mv.known_sep <= 60) & ~mv.id.isin(used)]
    for r in stars.sample(min(a.stars, len(stars)), random_state=3).itertuples():
        F.append({"id": r.id, "kind": "mover", "why_picked": "junk: on a Gaia star", "truth": "junk",
                  "truth_detail": f"Gaia star {r.gaia_sep:.2f}\" away, G={r.gaia_g:.1f}"})
    if a.junk_from:  # artifacts on which every run of the earlier round agreed
        runs = [json.load(open(p)) for p in glob.glob(f"{a.junk_from}/api/*/records.json")]
        runs.append([r for p in glob.glob(f"{a.junk_from}/out/batch_*/records.json") for r in json.load(open(p))])
        votes: dict[str, list] = {}
        for R in runs:
            if len(R) >= 100:  # whole-round runs only
                for r in R:
                    votes.setdefault(r["id"], []).append(r.get("verdict"))
        agreed = sorted(i for i, v in votes.items() if len(v) >= 3 and set(v) == {"artifact"} and V.mover_row(i) is not None)
        for i in rng.choice(agreed, min(a.artifacts, len(agreed)), replace=False):
            F.append({"id": str(i), "kind": "mover", "why_picked": "junk: artifact for every earlier checker", "truth": "junk",
                      "truth_detail": f"artifact in {len(votes[str(i)])} earlier runs"})
    order = rng.permutation(len(F))
    F = [F[i] for i in order]
    json.dump(F, open(f"{a.round}/findings.json", "w"), indent=1)
    from collections import Counter
    print(len(F), "findings:", dict(Counter(f["why_picked"] for f in F)))


if __name__ == "__main__":
    main()
