"""The reverse check: known moving bodies that crossed a zone, and whether our engine saw each one.

Run from pipeline/:  uv run python ../scripts/vet/known_check.py <zone> [<zone> ...] --out DIR

The list: one row per known body (MPC asteroids and comets, JPL Horizons planets and moons) and session in which
at least one photo held its predicted position. For each row:
  expected   exposures holding it, how far inside the photo, predicted V, rate and direction, distance to our nearest
             fixed source (the engine drops detections within 6" of one)
  seen       a mover of ours in that session within 5" of the prediction (movers.parquet, before the cuts)
  numbers    our rate and direction against the prediction, position offset, detections, S/N
  why_missed for misses, the first explanation that applies: one_exposure (a mover needs two exposures or two
             detectors), near_fixed (< 8"), edge (< 15 px inside), faint (V above the zone's 50% limit), else
             unexplained: these are the ones worth sending an agent to.
Writes <out>/known_<zone>.csv, <out>/known_unexplained_<zone>.csv and prints the summary.
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vetlib as V  # noqa: E402
from skyshift_pipeline.sky import SkyTree, angsep, offset_radec  # noqa: E402


def build(zone: str) -> pd.DataFrame:
    pred, mv, fx = V.table(zone, "pred"), V.table(zone, "movers"), V.table(zone, "fixed")
    if pred is None or mv is None:
        sys.exit(f"NOT AVAILABLE: pred/movers for {zone} (run prep_zone.py {zone} without --lite)")
    pred = pred.assign(exp=(pred.mjd * 1440).round(1))
    g = pred.groupby(["name", "session"], sort=False)
    k = g.agg(cls=("class", "first"), v=("v", "median"), ra=("ra", "mean"), dec=("dec", "mean"), mjd=("mjd", "mean"),
              vx=("dra_arcsec_h", "mean"), vy=("ddec_arcsec_h", "mean"), n_photos=("obs_id", "size"),
              n_exposures=("exp", "nunique"), n_detectors=("detector", "nunique"), edge_px=("edge_px", "min"),
              detectors=("detector", lambda s: "".join(str(d) for d in sorted(set(s))))).reset_index()
    k["rate"] = np.hypot(k.vx, k.vy)
    k["pa"] = np.degrees(np.arctan2(k.vx, k.vy)) % 360.0
    k["fixed_sep"] = SkyTree(fx.ra.to_numpy(float), fx.dec.to_numpy(float)).nearest(k.ra.to_numpy(float), k.dec.to_numpy(float))[0]
    # our movers of the same session near the prediction moved to the mover's time
    m = mv[["id", "session", "ra", "dec", "mjd", "rate_arcsec_h", "pa_deg", "n_dots", "n_exposures", "snr", "flux_ujy", "det",
            "is_suspect", "suspect_grade"]]
    by_s = {s: d for s, d in m.groupby("session")}
    cols = {c: [] for c in ("seen", "our_id", "sep", "our_rate", "our_pa", "our_n_dots", "our_n_exposures", "our_snr", "our_flux_ujy",
                            "our_det", "listed_as_candidate")}
    for r in k.itertuples():
        d = by_s.get(r.session)
        best = None
        if d is not None and len(d):
            dt = (d.mjd.to_numpy(float) - r.mjd) * 24.0
            r2, d2 = offset_radec(r.ra, r.dec, r.vx * dt, r.vy * dt)
            sep = angsep(d.ra.to_numpy(float), d.dec.to_numpy(float), r2, d2) * 3600.0
            j = int(np.argmin(sep))
            if sep[j] <= 5.0:
                best = (d.iloc[j], float(sep[j]))
        if best is None:
            for c, v in zip(cols, (False, "", np.nan, np.nan, np.nan, 0, 0, np.nan, np.nan, 0, False)):
                cols[c].append(v)
        else:
            b, s = best
            for c, v in zip(cols, (True, b.id, s, b.rate_arcsec_h, b.pa_deg, int(b.n_dots), int(b.n_exposures), float(b.snr),
                                   float(b.flux_ujy), int(b.det), bool(b.is_suspect))):
                cols[c].append(v)
    for c, v in cols.items():
        k[c] = v
    k["rate_err"] = k.our_rate - k.rate
    k["pa_err"] = (k.our_pa - k.pa + 180.0) % 360.0 - 180.0
    # the zone's 50% limit in V: the faintest 1-mag bin (bodies well placed) where at least half are seen
    well = k[(k.n_exposures >= 2) & (k.fixed_sep > 8) & (k.edge_px > 15) & np.isfinite(k.v)]
    lim = np.nan
    for lo in np.arange(10, 22, 0.5):
        b = well[(well.v >= lo) & (well.v < lo + 1)]
        if len(b) >= 8 and b.seen.mean() >= 0.5:
            lim = lo + 1
    k.attrs["v50"] = lim

    def why(r):
        if r.seen:
            return ""
        if r.n_exposures < 2 and r.n_detectors < 2:
            return "one_exposure"
        if r.fixed_sep < 8:
            return "near_fixed"
        if r.edge_px < 15:
            return "edge"
        if not np.isfinite(r.v) or (np.isfinite(lim) and r.v > lim):
            return "faint"
        return "unexplained"
    k["why_missed"] = [why(r) for r in k.itertuples()]
    k.insert(0, "zone", zone)
    return k


def summary(k: pd.DataFrame) -> None:
    zone = k.zone.iloc[0]
    print(f"\n== {zone}: {k.name.nunique()} known bodies in {len(k)} body-sessions; seen {int(k.seen.sum())} "
          f"({k.seen.mean():.0%}); V at which half of the well-placed ones are seen: {k.attrs.get('v50')}")
    print("  by class: " + "; ".join(f"{c} {int(g.seen.sum())}/{len(g)}" for c, g in k.groupby("cls") if len(g) >= 5))
    well = k[(k.n_exposures >= 2) & (k.fixed_sep > 8) & (k.edge_px > 15)]
    print("  well placed (2+ exposures, > 8\" from a fixed source, > 15 px inside), seen by predicted V:")
    for lo in range(10, 21, 2):
        b = well[(well.v >= lo) & (well.v < lo + 2)]
        if len(b):
            print(f"    V {lo}-{lo + 2}: {int(b.seen.sum())}/{len(b)} = {b.seen.mean():.0%}")
    print("  misses by reason: " + ", ".join(f"{w} {n}" for w, n in k[~k.seen].why_missed.value_counts().items()))
    s = k[k.seen]
    for lab, d in (("2 exposures", s[s.our_n_exposures == 2]), ("3+ exposures", s[s.our_n_exposures >= 3])):
        d = d[np.isfinite(d.rate_err)]
        if len(d):
            big = (d.rate_err.abs() > np.maximum(20.0, 0.5 * d.rate)).mean()
            print(f"  our numbers, seen with {lab} (n={len(d)}): position offset median {d.sep.median():.1f}\"; rate error median "
                  f"{d.rate_err.abs().median():.0f}\"/h (true rates median {d.rate.median():.0f}\"/h); direction error median "
                  f"{d.pa_err.abs().median():.0f}°; rate wrong by > 50% and > 20\"/h: {big:.0%}")
    print(f"  seen ones that the site still lists as unknown candidates: {int(s.listed_as_candidate.sum())}")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("zones", nargs="+"); ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    for z in a.zones:
        k = build(z)
        k.round(4).to_csv(f"{a.out}/known_{z}.csv", index=False)
        u = k[k.why_missed == "unexplained"].sort_values("v")
        u.round(4).to_csv(f"{a.out}/known_unexplained_{z}.csv", index=False)
        summary(k)
        print(f"  unexplained misses: {len(u)}; brightest: " + "; ".join(f"{r.name} V={r.v:.1f} s{r.session}" for r in u.head(6).itertuples()))


if __name__ == "__main__":
    main()
