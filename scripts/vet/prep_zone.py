"""Fill the vetting harness' local data for one zone from the bucket (read only).

Run from pipeline/:  uv run python ../scripts/vet/prep_zone.py <zone> [--lite] [--root gs://nasa-glazing-data]

Writes <data>/<zone>/ (data = env SKYSHIFT_VET_DATA, default ~/.cache/skyshift-vet/data):
  photos, refcat, fixed, suspects, highpm, shape, freedots (parquet), channels.json,
  sources.parquet (only the rows of this zone's candidates in <data>/candidates.json),
  and unless --lite: movers.parquet (every mover before the cuts, with id, is_suspect, and the nearest known body,
  Gaia star and fixed source) and pred.parquet (known bodies per photo). The predictions take 4-6 minutes to load.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vetlib as V  # noqa: E402
from skyshift_pipeline import config as C  # noqa: E402
from skyshift_pipeline.refcat import move_to_epoch, refcat_rel  # noqa: E402
from skyshift_pipeline.sky import SkyTree, angsep, offset_radec  # noqa: E402
from skyshift_pipeline.store import Store, photolist_rel  # noqa: E402
from skyshift_pipeline.tracklets import mover_ids  # noqa: E402

REF_COLS = ["cat", "ra", "dec", "epoch_mjd", "pmra", "pmdec", "parallax", "g_mag", "bp_mag", "rp_mag", "ruwe", "galaxy_or_qso",
            "w1_mag", "w2_mag", "j_mag", "h_mag", "k_mag"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("zone"); ap.add_argument("--lite", action="store_true"); ap.add_argument("--root", default="gs://nasa-glazing-data")
    a = ap.parse_args()
    zone = C.ZONES[a.zone]
    od = V.DATA / a.zone
    od.mkdir(parents=True, exist_ok=True)
    st = Store(a.root)
    t0 = time.time()

    def say(*x):
        print(a.zone, *x, f"[{time.time() - t0:.0f} s]", flush=True)

    photos = st.read_parquet(photolist_rel(a.zone)); photos.to_parquet(od / "photos.parquet")
    ref = st.read_parquet(refcat_rel(a.zone), columns=REF_COLS); ref.to_parquet(od / "refcat.parquet")
    fx = st.read_parquet(f"results/{a.zone}/fixed.parquet"); fx.to_parquet(od / "fixed.parquet")
    for src, dst in (("suspects.parquet", "suspects"), ("highpm_candidates.parquet", "highpm"), ("shape_candidates.parquet", "shape"),
                     ("freedots.parquet", "freedots")):
        if st.exists(f"results/{a.zone}/{src}"):
            st.read_parquet(f"results/{a.zone}/{src}").to_parquet(od / f"{dst}.parquet")
    if st.exists(f"results/{a.zone}/channels.json"):
        json.dump(st.read_json(f"results/{a.zone}/channels.json"), open(od / "channels.json", "w"))
    say("photos", len(photos), "refcat", len(ref), "fixed", len(fx))
    # spectra of this zone's candidates only (sources.parquet is 100+ MB)
    ids = {i for c, r in V.candidates().items() if r["zone"] == a.zone and r["kind"] != "mover" for i in V.source_ids(c)}
    if ids and st.exists(f"results/{a.zone}/sources.parquet"):
        import pyarrow.parquet as pq
        tb = pq.read_table(io.BytesIO(st.read_bytes(f"results/{a.zone}/sources.parquet")), filters=[("source_id", "in", sorted(ids))])
        tb.to_pandas().to_parquet(od / "sources.parquet")
        say("sources rows", tb.num_rows, "of", len(ids), "candidates")
    if a.lite:
        return
    mv = st.read_parquet(f"results/{a.zone}/movers.parquet")
    mv.insert(0, "id", list(mover_ids(zone, mv)))
    sus = st.read_parquet(f"results/{a.zone}/suspects.parquet", columns=["id", "grade"])
    mv = mv.merge(sus.rename(columns={"grade": "suspect_grade"}), on="id", how="left")
    mv["is_suspect"] = mv["suspect_grade"].notna()
    from skyshift_pipeline.known import load_predictions
    pred = load_predictions(st, photos, zone)
    say("movers", len(mv), "suspects", int(mv.is_suspect.sum()), "predictions", len(pred))
    kn = {k: np.full(len(mv), np.nan) for k in ("sep", "v", "rate", "pa")}
    kname = np.full(len(mv), None, object); kcls = np.full(len(mv), None, object)
    if len(pred):
        tree = SkyTree(pred.ra.to_numpy(float), pred.dec.to_numpy(float))
        pm, ps = pred.mjd.to_numpy(float), pred.session.to_numpy(np.int64)
        pra, pdec = pred.ra.to_numpy(float), pred.dec.to_numpy(float)
        vx, vy = pred.dra_arcsec_h.to_numpy(float), pred.ddec_arcsec_h.to_numpy(float)
        for i, (ra, dec, mjd, s) in enumerate(zip(mv.ra, mv.dec, mv.mjd, mv.session)):
            c = tree.query_radius(ra, dec, 600.0)[0]
            if not c:
                continue
            c = np.asarray(c); c = c[ps[c] == int(s)]
            if not c.size:
                continue
            dt = (mjd - pm[c]) * 24.0
            r2, d2 = offset_radec(pra[c], pdec[c], vx[c] * dt, vy[c] * dt)
            sep = angsep(ra, dec, r2, d2) * 3600.0
            j = int(np.argmin(sep)); k = c[j]
            kn["sep"][i], kn["v"][i] = sep[j], pred.v.iloc[k]
            kn["rate"][i], kn["pa"][i] = float(np.hypot(vx[k], vy[k])), float(np.degrees(np.arctan2(vx[k], vy[k])) % 360.0)
            kname[i], kcls[i] = pred.name.iloc[k], pred["class"].iloc[k]
    mv["known_sep"], mv["known_v"], mv["known_rate"], mv["known_pa"] = kn["sep"], kn["v"], kn["rate"], kn["pa"]
    mv["known_name"], mv["known_class"] = kname, kcls
    g = ref[ref.cat == "gaia"].reset_index(drop=True)
    gra, gdec = move_to_epoch(g, float(mv.mjd.median()))
    sep, idx = SkyTree(np.asarray(gra, float), np.asarray(gdec, float)).nearest(mv.ra.to_numpy(float), mv.dec.to_numpy(float))
    mv["gaia_sep"] = sep
    mv["gaia_g"] = np.where(np.isfinite(sep), g.g_mag.to_numpy()[np.clip(idx, 0, len(g) - 1)], np.nan)
    mv["fixed_sep"] = SkyTree(fx.ra.to_numpy(float), fx.dec.to_numpy(float)).nearest(mv.ra.to_numpy(float), mv.dec.to_numpy(float))[0]
    mv.to_parquet(od / "movers.parquet")
    pred.to_parquet(od / "pred.parquet")
    k = mv.known_sep <= 5
    say("done; movers within 5\" of a known body", int(k.sum()), "; within 2\" of a Gaia star", int((mv.gaia_sep <= 2).sum()))


if __name__ == "__main__":
    main()
