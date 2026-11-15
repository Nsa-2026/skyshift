"""V6 (fast stars, S2) and V7 (brown dwarfs, S3): V2's measurement on many more objects.

Run from server/ (it imports skyshift_server, like scripts/precompute):

    cd server
    uv run python ../scripts/validate/v6_v7_motion.py --select GCNS_CSV   # data/v6_stars.json, data/v7_dwarfs.json
    uv run python ../scripts/validate/v6_v7_motion.py                     # data/v6_fast_stars.json, data/v7_brown_dwarfs.json
    uv run python ../scripts/validate/v6_v7_motion.py --only v7 --limit 3

Lists (docs/skyshift-hackathon-plan.html, "How we know it works"; _motion.py has the cuts):
- V6: the 40 fastest Gaia stars with G > 12.
- V7: brown dwarfs faster than 1"/yr. The plan's Gagne list was never loaded (CDS is down), so it is
  Gaia's own ultracool dwarfs (absolute G > 16.5, G - RP > 1.5, parallax at 10 sigma) faster than 1"/yr,
  plus WISE 0855-0714 (not in Gaia; published astrometry, data/targets.json).
Both lists come from the Gaia Catalogue of Nearby Stars (GCNS: Gaia EDR3 within 100 pc, GAVO's TAP,
gcns.main with pm > 1"/yr: 628 stars), because on 15 Nov ESA's Gaia archive did not answer and a sorted
query over the whole of gaia_source timed out on the ARI mirror. EDR3 astrometry is DR3's (unchanged), and a
star faster than 1"/yr beyond 100 pc would need over 470 km/s. --select writes the lists from a CSV of
`SELECT * FROM gcns.main WHERE pmra*pmra+pmdec*pmdec > 1000000`.

Per object, exactly V2's measurement (server obj.measure, the same code as /api/object and the featured
targets): the object's Gaia model (proper motion and parallax), every SPHEREx photo covering it (server
search; while IRSA's search is down, SkyShift's photo lists (search_fallback), and where no list reaches, the
pipeline's index of every Level 2 file on NASA's S3 copy, photolists/s3index/, copied once into .cache/s3index/
with `gcloud storage cp -r gs://nasa-glazing-data/photolists/s3index/* .cache/s3index/`), a short spectrum
(SPECTRUM_PER_DET exposures per detector) to pick the detector the way /api/object does (best S/N, not
saturated), then the first and last visit stacks of that detector (at most PER_VISIT exposures each, spread
over the visit) on the 15' grid, with quiet Gaia stars as the reference frame and error floor. The
reference stars' Gaia cones come from the ARI Heidelberg mirror of gaiadr3.gaia_source (same table and
columns as ESA's), cached like the server's.

The prediction is the Gaia model moved between the two stacks' median times, parallax included
(_motion.predicted_shift). `blend_*`: the nearest Gaia star no more than BLEND_DMAG fainter than the object (in G)
within the measurement's 2 px centroid search radius (12.3") of it at either stack time (both moved to that time),
from Gaia alone, before looking at the measurement: the centroid can lock onto such a neighbour (a fast white dwarf
7.7" from a G 14 star measured 0.14" for 2.52"). G is no guide to a brown dwarf's infrared brightness. Rows keep both vectors, so build_validation.py fits the slope (V6) and counts the
objects shifting the predicted way (V7). An object that cannot be measured keeps its reason (contracts/api.md
measure_reason, or no_photos / no_detector).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import math
import os
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# NASA's S3 copy first, one range request per HDU with timeouts (as Cloud Run); IRSA's cutouts only as a fallback
os.environ.setdefault("SKYSHIFT_PIXELS", "s3first")

import requests  # noqa: E402
from skyshift_core import propagate  # noqa: E402
from skyshift_core import tangent_offset_arcsec  # noqa: E402
from skyshift_server import blink, cutouts, gaia, obj, search, search_fallback  # noqa: E402
from skyshift_server.cache import Cache  # noqa: E402
from skyshift_server.errors import UpstreamError  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _motion as M  # noqa: E402

DATA = HERE / "data"
CENTER_MJD = 61045.0  # middle of the SPHEREx data so far (as scripts/precompute)
SPECTRUM_PER_DET = 3
PER_VISIT = 10
ARI_TAP = "https://gaia.ari.uni-heidelberg.de/tap/sync"
S3INDEX = HERE / ".cache" / "s3index"  # gs://nasa-glazing-data/photolists/s3index/ (pipeline s3index.py)
BLEND_DMAG = 1.0  # a neighbour up to this much fainter than the object (G) counts as a blend
BLEND_R_ARCSEC = 2.0 * 6.15  # obj.measure's centroid search radius for an object with a model (2 px)
GAVO_GCNS = "GAVO DC TAP gcns.main (Gaia EDR3 within 100 pc), pmra^2 + pmdec^2 > 1e6"
CHECKS = {
    "v6": {"list": DATA / "v6_stars.json", "out": DATA / "v6_fast_stars.json"},
    "v7": {"list": DATA / "v7_dwarfs.json", "out": DATA / "v7_brown_dwarfs.json"},
}

log = logging.getLogger("v6v7")


# --- Gaia cones from the ARI mirror -----------------------------------------------------------

def _ari_tap(adql: str) -> dict:
    """gaia._tap's answer shape ({metadata:[{name}], data:[[...]]}) from ARI's CSV."""
    last = ""
    for attempt in range(4):
        try:
            r = requests.post(ARI_TAP, data={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv", "QUERY": adql},
                              timeout=120, headers={"User-Agent": "SkyShift/0.1"})
            if r.status_code == 200 and not r.text.lstrip().startswith("<"):
                rows = list(csv.reader(io.StringIO(r.text)))
                names = rows[0]
                data = [[None if v == "" else v for v in row] for row in rows[1:]]
                return {"metadata": [{"name": n} for n in names], "data": data}
            last = f"HTTP {r.status_code}: {r.text[:200]}"
        except requests.RequestException as e:
            last = repr(e)
        time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"ARI Gaia mirror failed: {last}")


gaia._tap = _ari_tap


# --- lists ------------------------------------------------------------------------------------

def _row_out(r: dict) -> dict:
    g, rp = M._f(r.get("phot_g_mean_mag")), M._f(r.get("phot_rp_mean_mag"))
    plx = M._f(r.get("parallax"))
    return {"source_id": str(r["source_id"]), "ra": float(r["ra"]), "dec": float(r["dec"]),
            "pmra": float(r["pmra"]), "pmdec": float(r["pmdec"]), "pm": round(float(r["pm"]), 2),
            "parallax": plx, "parallax_error": M._f(r.get("parallax_error")), "phot_g_mean_mag": g,
            "phot_rp_mean_mag": rp, "abs_g": None if M.abs_g(g, plx) is None else round(M.abs_g(g, plx), 2),
            "ruwe": M._f(r.get("ruwe")), "name_2mass": r.get("name_2mass") or None,
            "w2mpro": M._f(r.get("w2mpro_pm_wise"))}


def select(gcns_csv: Path) -> None:
    rows = list(csv.DictReader(gcns_csv.open()))
    for r in rows:
        r["pm"] = math.hypot(float(r["pmra"]), float(r["pmdec"]))
    meta = {"source": GAVO_GCNS, "queried": time.strftime("%Y-%m-%d"), "n_input": len(rows)}
    v6 = M.select_v6(rows)
    json_out(CHECKS["v6"]["list"], {**meta, "cut": f"G > {M.V6_G_MIN}, the {M.V6_COUNT} fastest",
                                    "stars": [_row_out(r) for r in v6]})
    v7 = M.select_v7(rows)
    json_out(CHECKS["v7"]["list"], {**meta, "cut": f"pm > {M.V7_PM_MIN:g} mas/yr, absolute G > {M.V7_MG_MIN}, "
                                    f"G - RP > {M.V7_GRP_MIN}, parallax / error > {M.V7_PLX_SNR_MIN:g}",
                                    "stars": [_row_out(r) for r in v7]})
    print(f"V6 {len(v6)} stars, V7 {len(v7)} dwarfs from {len(rows)} GCNS rows")


def models(check: str) -> list[dict]:
    d = json.loads(CHECKS[check]["list"].read_text())
    out = []
    for r in d["stars"]:
        m = M.gaia_model(r, name=f"Gaia DR3 {r['source_id']}" + (f" (2MASS J{r['name_2mass']})" if r.get("name_2mass") else ""))
        m.update({"g": r.get("phot_g_mean_mag"), "abs_g": r.get("abs_g"), "ruwe": r.get("ruwe"), "pm": r["pm"]})
        out.append(m)
    if check == "v7":  # the built-in brown dwarf Gaia doesn't have
        for t in json.loads((DATA / "targets.json").read_text())["targets"]:
            if t["slug"] == "wise0855":
                out.append({"id": None, "slug": t["slug"], "name": t["name"], "ra": t["ra"], "dec": t["dec"],
                            "pmra": t["pmra"], "pmdec": t["pmdec"], "parallax": t["parallax"],
                            "epoch_mjd": t.get("ref_mjd", M.GAIA_EPOCH_MJD), "g": None, "abs_g": None, "ruwe": None,
                            "pm": round(math.hypot(t["pmra"], t["pmdec"]), 2),
                            "dets": tuple(int(x) for x in t["dets"]) if t.get("dets") else None})  # else obj's (5, 6)
    return out


# --- photos where no SkyShift list reaches: the S3 index ---------------------------------------

_s3index = None
_s3index_lock = __import__("threading").Lock()


def s3index_table():
    """Every Level 2 file in photolists/s3index/ (DR1 + QR3), one row per (obs_id, detector): DR1 first, then
    the newest version (the pipeline's photolist.dedupe). Read once (~1.6 million rows)."""
    global _s3index
    with _s3index_lock:
        if _s3index is None:
            import pandas as pd

            files = sorted(S3INDEX.glob("*/*.parquet"))
            if not files:
                raise FileNotFoundError(f"no S3 index in {S3INDEX} (see the module docstring)")
            cols = ["obs_id", "collection", "detector", "t_min", "t_max", "s_ra", "s_dec", "s_region", "s3_key"]
            df = pd.concat([pd.read_parquet(f, columns=cols) for f in files], ignore_index=True)
            df["_pref"] = df["collection"].map({"spherex_dr1": 0, "spherex_qr3": 1})
            df = df[df["_pref"].notna()].sort_values(["obs_id", "detector", "_pref", "s3_key"],
                                                     ascending=[True, True, True, False])
            _s3index = df.drop_duplicates(["obs_id", "detector"], keep="first").reset_index(drop=True)
            log.warning("S3 index: %d photos from %d shards", len(_s3index), len(files))
        return _s3index


def search_s3index(ra: float, dec: float) -> list:
    """search.rows_to_images over the S3 index rows whose footprint contains (ra, dec)."""
    df = s3index_table()
    near = df[search_fallback.angsep(df["s_ra"].to_numpy(), df["s_dec"].to_numpy(), ra, dec)
              < search_fallback.NEAR_CENTRE_DEG]
    inside = near[search_fallback.contains(near["s_region"].to_numpy(), ra, dec)]
    rows = [search_fallback.sia_row(r.obs_id, r.collection, int(r.detector), r.t_min, r.t_max, r.s3_key, r.s_region)
            for r in inside.itertuples()]
    return search.rows_to_images(rows)


def find_photos(ra: float, dec: float, cache: Cache) -> tuple[list, str]:
    """(images, where from): IRSA, else SkyShift's photo lists (search.search), else the S3 index."""
    try:
        found = search.search(ra, dec, cache=cache)
        if found.images:
            return found.images, ("irsa" if not found.note else "photo_lists")
    except UpstreamError:  # IRSA's search is down and no SkyShift photo list covers this spot
        pass
    return search_s3index(ra, dec), "s3_index"


def blend(m: dict, stars: list[dict], mjds: list[float]) -> tuple[float, float] | None:
    """(distance arcsec, G) of the nearest Gaia star, not the object, no more than BLEND_DMAG fainter than it,
    within BLEND_R_ARCSEC of it at any of mjds; None without one (or when the object has no G)."""
    if m.get("g") is None:
        return None
    best = None
    for t in mjds:
        ra0, dec0 = obj.position_at(m, t)
        for st in stars:
            if st.get("id") == m.get("id") or st.get("g") is None or st["g"] > m["g"] + BLEND_DMAG:
                continue
            ra1, dec1 = obj.position_at(st, t, parallax=False) if st.get("pmra") is not None else (st["ra"], st["dec"])
            e, n = tangent_offset_arcsec(ra0, dec0, ra1, dec1)
            d = float(math.hypot(e, n))
            if d <= BLEND_R_ARCSEC and (best is None or d < best[0]):
                best = (round(d, 2), st["g"])
    return best


# --- one object -------------------------------------------------------------------------------

def measure_one(m: dict, cache: Cache) -> dict:
    row = {"source_id": m.get("id"), "name": m["name"], "g": m.get("g"), "abs_g": m.get("abs_g"), "ruwe": m.get("ruwe"),
           "pm_mas_yr": m["pm"], "measured": False, "reason": None}
    t0 = time.monotonic()
    ra, dec = (float(v) for v in propagate(m["ra"], m["dec"], m["pmra"], m["pmdec"], CENTER_MJD,
                                            epoch_mjd=m["epoch_mjd"], parallax=None))
    row.update({"ra": round(ra, 6), "dec": round(dec, 6)})
    images, row["search"] = find_photos(ra, dec, cache)
    if not images:
        row["reason"] = "no_photos"
        return row
    row["n_photos"] = len(images)
    ref_f = blink._side.submit(gaia.cone, ra, dec, obj.MEASURE_FOV / 2.0 * 2**0.5, cache=cache)
    near = gaia.cone(ra, dec, obj.GAIA_CONE_ARCMIN, cache=cache)
    spec_images = obj.spectrum_images(images, SPECTRUM_PER_DET)
    l2s = cutouts.fetch_many(spec_images, ra, dec, cutouts.tier_for_field(5), cache, variance=True)
    spectrum = obj.spectrum_points(spec_images, l2s, m, ra, dec, stars=near, dropped=Counter())
    pick = obj.pick_detector(spectrum, images, obj.measure_detectors(m))
    if pick is None:
        row["reason"] = "few_visits"
        return row
    det, v1, v2 = pick
    row.update({"det": det, "visits": [v1, v2]})
    reasons: list[str] = []
    got = obj.measure(ra, dec, m, images, det, v1, v2, cache, per_visit=PER_VISIT, ref_stars=ref_f, reasons=reasons)
    row["seconds"] = round(time.monotonic() - t0, 1)
    if got is None:
        row["reason"] = obj.SHIFT_REASONS.get(reasons[0], "not_detected") if reasons else "few_visits"
        if reasons:
            row["shift_error"] = reasons[0]
        return row
    out, extra = got
    bl = blend(m, ref_f.result(), extra["mjd"])
    row.update({"blend_arcsec": bl[0] if bl else None, "blend_g": bl[1] if bl else None})
    pe, pn = M.predicted_shift(m, extra["mjd"][0], extra["mjd"][1])
    me, mn = float(extra["d_east_arcsec"]), float(extra["d_north_arcsec"])
    pred = math.hypot(pe, pn)
    row.update({
        "measured": True, "shift_arcsec": round(out["shift_arcsec"], 3), "sigma_arcsec": round(out["sigma_arcsec"], 3),
        "n_ref_stars": out["n_ref_stars"], "dt_yr": round(out["dt_yr"], 3), "pa_deg": round(M.pa_deg(me, mn), 1),
        "d_east_arcsec": round(me, 3), "d_north_arcsec": round(mn, 3),
        "pred_east_arcsec": round(pe, 3), "pred_north_arcsec": round(pn, 3), "predicted_arcsec": round(pred, 3),
        "predicted_pa_deg": round(M.pa_deg(pe, pn), 1), "angle_deg": round(M.angle_deg(me, mn, pe, pn), 1),
        "dev_sigma": round(math.hypot(me - pe, mn - pn) / out["sigma_arcsec"], 2) if out["sigma_arcsec"] else None,
        "n_exposures": extra["n_exposures"], "target_snr": extra["target_snr"], "core_filled": extra["core_filled"],
    })
    return row


def run(check: str, limit: int | None, workers: int, cache: Cache) -> None:
    ms = models(check)[:limit] if limit else models(check)
    t0 = time.monotonic()

    def one(m: dict) -> dict:
        try:
            r = measure_one(m, cache)
        except Exception as e:  # noqa: BLE001 - one object's failure is a row, not the end of the run
            log.exception("%s failed", m["name"])
            r = {"source_id": m.get("id"), "name": m["name"], "pm_mas_yr": m["pm"], "measured": False,
                 "reason": "error", "error": repr(e)[:300]}
        print(f"{check} {r['name'][:40]:40s} {r.get('det', '-')} "
              + (f"shift {r['shift_arcsec']:.2f}\" pred {r['predicted_arcsec']:.2f}\" +- {r['sigma_arcsec']:.2f} "
                 f"angle {r['angle_deg']:.0f}" if r["measured"] else f"no: {r['reason']}"), flush=True)
        return r

    with ThreadPoolExecutor(workers) as ex:
        rows = list(ex.map(one, ms))
    meta = json.loads(CHECKS[check]["list"].read_text())
    json_out(CHECKS[check]["out"], {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "check": check.upper(),
        "list_source": meta["source"], "list_cut": meta["cut"], "per_visit": PER_VISIT,
        "spectrum_per_det": SPECTRUM_PER_DET, "pixels": os.environ.get("SKYSHIFT_PIXELS"),
        "gaia_cones": ARI_TAP, "minutes": round((time.monotonic() - t0) / 60, 1), "rows": rows})
    n = sum(r["measured"] for r in rows)
    print(f"{check}: {n} of {len(rows)} measured -> {CHECKS[check]['out']}")


def json_out(path: Path, obj_: dict) -> None:
    path.write_text(json.dumps(obj_, indent=1, ensure_ascii=False) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--select", type=Path, metavar="GCNS_CSV", help="write the two lists from this CSV and stop")
    ap.add_argument("--only", choices=["v6", "v7"], help="one check (default both)")
    ap.add_argument("--limit", type=int, help="first N objects of each list (a test)")
    ap.add_argument("--workers", type=int, default=4, help="objects measured at once")
    ap.add_argument("--cache", help="cache folder (default: the server's, SKYSHIFT_CACHE or ~/.cache/skyshift)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if args.select:
        select(args.select)
        return
    cache = Cache(args.cache)
    for check in [args.only] if args.only else ["v6", "v7"]:
        run(check, args.limit, args.workers, cache)


if __name__ == "__main__":
    main()
