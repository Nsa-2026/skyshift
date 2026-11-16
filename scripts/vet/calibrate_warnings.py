"""How often does each automatic packet warning fire on REAL movers? (known asteroids, comets, moons)

Run from pipeline/:  uv run python ../scripts/vet/calibrate_warnings.py [--n 400]

Round 1 held no real mover, so the warnings were first worded from junk alone. This counts the warnings that need no
image (hot pixel, same pixel, whole-pixel position, shape, fixed source nearby, between two stars, bright star) on a
sample of movers within 2" of a known body, and on the movers the site lists (almost all junk in round 1). A warning
that fires on many real movers must not be read as proof of junk. The image-based warnings are scored by the blind round.
"""
from __future__ import annotations

import argparse
import collections
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vetlib as V  # noqa: E402

ZONES = ("hunt", "ecl17", "ecl05", "ecl10")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=400)
    a = ap.parse_args()
    cnt = {"real": collections.Counter(), "listed": collections.Counter()}
    tot = collections.Counter()
    for z in ZONES:
        mv = V.table(z, "movers")
        ctx = {"fixed": V.table(z, "fixed"), "refcat": V.table(z, "refcat"), "freedots": V.table(z, "freedots")}
        groups = {"real": mv[mv.known_sep <= 2], "listed": mv[mv.is_suspect]}
        for g, df in groups.items():
            df = df.sample(min(len(df), a.n // len(ZONES)), random_state=1)
            for _, r in df.iterrows():
                W = V.warnings_for(r, V.mover_dots(r), [], {}, ctx)
                tot[g] += 1
                for name in {w.split(":")[0].split(" G=")[0].split(" ")[0] + " " + w.split(":")[0].split(" ")[1] if " " in w.split(":")[0] else w.split(":")[0] for w in W}:
                    cnt[g][name] += 1
    names = sorted(set(cnt["real"]) | set(cnt["listed"]), key=lambda n: -cnt["listed"][n])
    print(f"warning (first words)        real movers (n={tot['real']})   movers on the site's list (n={tot['listed']})")
    for n in names:
        print(f"  {n:26s} {cnt['real'][n] / tot['real']:10.1%}              {cnt['listed'][n] / tot['listed']:10.1%}")


if __name__ == "__main__":
    main()
