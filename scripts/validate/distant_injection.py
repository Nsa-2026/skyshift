"""How sensitive is the distant-planet search (pipeline step `orphans`)? Table-level injection on a zone's own tables.

Synthetic planets on circular orbits at 70-1000 AU (prograde or retrograde, ecliptic latitude within +-3 deg) are
observed at the real cadence of their spot: the zone's photos that cover it in each visit, 3, 6 or 20 dots per visit.
Their dots get the drift fit's own error model as noise and go through orphans.fit_drift / drift_ok; the drift-fitted
halves are added to the zone's real drift_ok orphans (so chance partners compete) and paired with
orphans.candidate_pairs / score_pairs, exactly as the step does. A planet is recovered when its own two halves form a
passing pair. Fluxes: D3 only, 400 uJy with 15% scatter and a 15% error.

What it does not test: detection, linking into one source per visit, the catalogue and bright-star cuts, and the
zone edge (planets start >= 10' inside). It answers "if both visits saw the planet as an orphan, does the pairing find
it, at the right distance?". Photo-level completeness is the pipeline's `completeness` step.

Needs the zone's tables after the `orphans` step (local root: photolists/zones/<zone>.parquet,
results/<zone>/orphans.parquet and sources.parquet); reads nothing else. Run with the pipeline's environment:

  cd pipeline && uv run python ../scripts/validate/distant_injection.py --root <root> [--zone hunt]
      [--out ../scripts/validate/data/distant_injection_hunt.json]

Hunt zone (14 Nov tables, 442 real orphans, 522 planets): ~7 min on a laptop. First run for PR #132 (seed 7): same numbers.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from skyshift_pipeline import config as C
from skyshift_pipeline import orphans as O
from skyshift_pipeline.sky import Footprint, offset_radec
from skyshift_pipeline.spectra import NDET

HERE = Path(__file__).resolve().parent
DISTANCES_AU = (70.0, 100.0, 150.0, 200.0, 300.0, 500.0, 700.0, 1000.0)
DOTS_PER_VISIT = (3, 6, 20)
TRIES = 30           # planets drawn per (distance, dots) cell; those whose spots fall outside the zone are skipped
POS_ERR = 0.45       # quoted pos_err (arcsec) of a faint orphan dot (S/N 5-8); scaled and floored as the fit does
T1, T2 = 60975.0, 61145.0  # the hunt's two visits (Oct 2025, Mar 2026): planet positions to place the spots
EDGE_MIN_ARCSEC = 600.0
SEED = 7


def planet(r, lam0, beta0, mjd, pro):
    """Direction from Earth (barycentric) to a body on a circular orbit of radius r AU."""
    n = pro * 2 * np.pi / (r ** 1.5 * 365.25)
    lam = np.deg2rad(lam0) + n * (np.asarray(mjd) - 60970.0)
    b = np.deg2rad(beta0)
    x, y, z = r * np.cos(lam) * np.cos(b), r * np.sin(lam) * np.cos(b), r * np.sin(b) * np.ones_like(lam)
    P = np.stack([x, y * np.cos(O.EPS) - z * np.sin(O.EPS), y * np.sin(O.EPS) + z * np.cos(O.EPS)], -1)
    E, _ = O.earth_posvel(mjd)
    return O.xyz_to_radec(P - E)


def run(root: str, zone_name: str, seed: int = SEED, tries: int = TRIES) -> dict:
    rng = np.random.default_rng(seed)
    zone = C.ZONES[zone_name]
    photos = pd.read_parquet(f"{root}/photolists/zones/{zone_name}.parquet")
    fps = [Footprint.from_s_region(s) for s in photos.s_region]
    o = pd.read_parquet(f"{root}/results/{zone_name}/orphans.parquet")
    real = o[o.drift_ok].reset_index(drop=True).copy()
    real["ra"], real["dec"], real["mjd"] = real.ra_fit, real.dec_fit, real.mjd_fit
    tab = pq.read_table(f"{root}/results/{zone_name}/sources.parquet", columns=["source_id", "flux_ujy", "err_ujy"],
                        filters=[("source_id", "in", real.source_id.astype(np.int64).tolist())]).to_pandas()
    tab = tab.set_index("source_id").reindex(real.source_id.to_numpy())
    spec = np.stack([np.asarray(x, float) for x in tab.flux_ujy])
    err = np.stack([np.asarray(x, float) for x in tab.err_ujy])
    rflux, rrel = O.detector_flux_errors(spec, err)

    def times_at(ra, dec, visit, n):
        m = np.array([fp.contains(np.array([ra]), np.array([dec]))[0] for fp in fps]) & (photos.visit.to_numpy() == visit)
        q = photos[m]
        if q.session.nunique() < 3:
            return None
        ses = (rng.choice(q.session.unique(), size=min(n, q.session.nunique()), replace=False)
               if n <= q.session.nunique() else q.session.unique())
        rows = q[q.session.isin(ses)].groupby("session").head(max(1, n // max(len(ses), 1)))
        return rows.mjd.to_numpy()[:n] if len(rows) >= 3 else None

    cells = []
    for r in DISTANCES_AU:
        for ndots in DOTS_PER_VISIT:
            dots, meta = [], []
            for k in range(tries):
                lam0 = rng.uniform(121.5, 130.0)
                beta0 = rng.uniform(-3, 3)
                pro = rng.choice([1.0, -1.0])
                ra1, de1 = planet(r, lam0, beta0, np.array([T1]), pro)
                if not zone.contains(ra1, de1)[0] or O.edge_distance_arcsec(ra1, de1, zone)[0] < EDGE_MIN_ARCSEC:
                    continue
                ok = True
                for v, sid in ((1, 10_000 * k + 1), (2, 10_000 * k + 2)):
                    tv = T1 if v == 1 else T2
                    rv, dv = planet(r, lam0, beta0, np.array([tv]), pro)
                    t = times_at(rv[0], dv[0], v, ndots)
                    if t is None or not zone.contains(rv, dv)[0]:
                        ok = False
                        break
                    ra, dec = planet(r, lam0, beta0, t, pro)
                    s = np.hypot(C.ORPHANS_POS_ERR_SCALE * POS_ERR, C.ORPHANS_POS_FLOOR_ARCSEC)
                    ra, dec = offset_radec(ra, dec, rng.normal(0, s, t.size), rng.normal(0, s, t.size))
                    dots.append(pd.DataFrame({"source_id": sid, "ra": ra, "dec": dec, "mjd": t, "pos_err_arcsec": POS_ERR,
                                              "session": np.round(t * 200).astype(int), "visit": v}))
                if ok:
                    meta.append(k)
            if not dots:
                continue
            d = pd.concat(dots, ignore_index=True)
            f = O.fit_drift(d)
            f = f[f.source_id.isin([10_000 * k + v for k in meta for v in (1, 2)])]
            f["visit"] = np.where(f.source_id % 10 == 1, 1, 2)
            f["drift_ok"] = f.fit_ok & O.drift_ok(f.rate, f.rate_err)
            cols = ["source_id", "ra", "dec", "mjd", "visit", "drift_e", "drift_n", "drift_e_err", "drift_n_err"]
            both = pd.concat([real[cols], f[f.drift_ok][cols]], ignore_index=True)
            ninj = int(f.drift_ok.sum())
            fl = np.full((ninj, NDET), np.nan)
            rel = np.full((ninj, NDET), np.nan)
            fl[:, 2] = 400.0 * np.exp(rng.normal(0, 0.15, ninj))
            rel[:, 2] = 0.15
            flux, frel = np.vstack([rflux, fl]), np.vstack([rrel, rel])
            E, VE = O.earth_posvel(both.mjd.to_numpy())
            both["_E"], both["_VE"] = list(E), list(VE)
            i, j = O.candidate_pairs(both.ra.to_numpy(), both.dec.to_numpy(), both.visit.to_numpy(), both.mjd.to_numpy())
            p = O.score_pairs(both, i, j, flux, frel)
            sid = both.source_id.to_numpy()
            hits = {}
            if len(p):
                pp = p[p.passes]
                for a, b, dist in zip(sid[pp.i], sid[pp.j], pp.distance_au):
                    if a // 10_000 == b // 10_000 and a % 10 == 1 and a < 10_000 * 100 and b % 10 == 2:
                        hits[a // 10_000] = float(dist)
            ok_ids = set(f[f.drift_ok].source_id)
            both_ok = sum((10_000 * k + 1 in ok_ids) and (10_000 * k + 2 in ok_ids) for k in meta)
            cells.append({"distance_au": r, "dots_per_visit": ndots, "injected": len(meta), "drift_ok_both": int(both_ok),
                          "recovered": len(hits), "distance_fit_au": sorted(round(x, 1) for x in hits.values())})
            print(f"r={r:6.0f} dots/visit={ndots:2d}: injected {len(meta):2d}, both drift_ok {both_ok:2d}, recovered "
                  f"{len(hits):2d} ({len(hits) / max(len(meta), 1):.0%})", flush=True)
    return summarise(cells, zone, seed, len(real))


def summarise(cells: list[dict], zone, seed: int, n_real: int) -> dict:
    per = []
    for r in DISTANCES_AU:
        cs = [c for c in cells if c["distance_au"] == r]
        if not cs:
            continue
        inj, rec = sum(c["injected"] for c in cs), sum(c["recovered"] for c in cs)
        fits = [x for c in cs for x in c["distance_fit_au"]]
        per.append({"distance_au": r, "injected": inj, "recovered": rec,
                    "recovered_frac": round(rec / inj, 3) if inj else None,
                    "distance_fit_median_au": round(float(np.median(fits)), 1) if fits else None,
                    "distance_err_median_frac": round(float(np.median(np.abs(np.array(fits) / r - 1))), 3) if fits else None})
    fr = [c["recovered"] / c["injected"] for c in cells if c["injected"]]
    n_inj, n_rec = sum(c["injected"] for c in cells), sum(c["recovered"] for c in cells)
    errs = [abs(x / c["distance_au"] - 1) for c in cells for x in c["distance_fit_au"]]
    return {
        "generated": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "zone": zone.name, "zone_box": {"ra": [zone.ra_min, zone.ra_max], "dec": [zone.dec_min, zone.dec_max]},
        "seed": seed, "real_orphans": n_real, "dots_per_visit": list(DOTS_PER_VISIT),
        "method": ("table-level: synthetic planets at the zone's real cadence, the drift fit's error model as noise, "
                   "added to the zone's real drift-fitted orphans and paired by the orphans step's own code; recovered "
                   "= its two halves form a passing pair. Detection, linking and the catalogue cuts are not tested."),
        "distances": per, "cells": cells, "injected": n_inj, "recovered": n_rec,
        "recovered_frac": round(n_rec / n_inj, 3) if n_inj else None,
        "median_cell_recovered_frac": round(float(np.median(fr)), 3) if fr else None,
        "min_cell_recovered_frac": round(float(np.min(fr)), 3) if fr else None,
        "distance_err_median_frac": round(float(np.median(errs)), 3) if errs else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", required=True, help="local root with the zone's photo list and orphans/sources tables")
    ap.add_argument("--zone", default="hunt")
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--out", type=Path, help="default scripts/validate/data/distant_injection_<zone>.json")
    a = ap.parse_args()
    t0 = time.time()
    rep = run(a.root, a.zone, a.seed)
    out = a.out or HERE / "data" / f"distant_injection_{a.zone}.json"
    out.write_text(json.dumps(rep, indent=1) + "\n")
    print(f"-> {out} ({time.time() - t0:.0f} s): recovered {rep['recovered']} of {rep['injected']}, "
          f"min {rep['min_cell_recovered_frac']}, distance error median {rep['distance_err_median_frac']}")


if __name__ == "__main__":
    main()
