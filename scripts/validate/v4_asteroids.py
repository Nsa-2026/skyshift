"""V4: asteroids 1-60 re-found where NASA's MOST predicts them, with a control 3' away.

For each asteroid, up to 5 photos spread over its MOST list (detectors D2-D3, prediction at least
0.2 deg inside the photo). Each photo gets an 81 x 81 px box (8.3') around MOST's predicted
position and a reference stack: the median of up to 3 photos of the same detector taken 0.25-12 days
away, when the asteroid was at least 90" from that spot, resampled onto the box.

  found   = a 5 sigma detection within 2 px of the prediction in the photo,
            and nothing (4 sigma) within 3 px of the same spot in the reference stack.
  control = the same test 3' from the prediction, in a random direction (seeded): the chance rate.

An asteroid counts as re-found when at least one of its photos is found. The control is counted the
same way. Per-photo rates are reported too.

Input: data/most_1_60.csv (gs://nasa-glazing-data/results/most_1_60.csv; written by most_asteroids.py).
Output: data/v4_asteroids.json (summary + one row per asteroid) and data/v4_photos.csv.

  uv run python v4_asteroids.py              # ~1200 boxes, ~10 min cold, cached in .cache/boxes/
  uv run python v4_asteroids.py --first 1 --last 5 --per 2   # quick look
"""

from __future__ import annotations

import argparse
import csv
import math
import random
from collections import defaultdict
from pathlib import Path

import numpy as np

from _boxes import OffPhoto, angsep_arcsec, fetch_boxes, load_box, offset_position, reference_stack, source_near
from _common import DATA, PIXEL_ARCSEC, today, write_json

MOST_CSV = DATA / "most_1_60.csv"
OUT_JSON = DATA / "v4_asteroids.json"
OUT_CSV = DATA / "v4_photos.csv"

DETECTORS = (2, 3)
HALF_PX = 40            # 81 x 81 px box
MATCH_PX = 2.0          # "within 2 px"
STACK_MATCH_PX = 3.0    # "absent from the stack": nothing within 3 px. With 2 px, a static star at 1.9 px in
                        # the photo and 2.1 px in the resampled stack counted as new (5 of 6 control hits).
CONTROL_ARCSEC = 180.0  # control 3' away
MIN_REF_DT, MAX_REF_DT = 0.25, 12.0   # days between the photo and its references
MIN_REF_AWAY = 90.0     # arcsec: the asteroid must be at least this far from the spot in a reference
MAX_CENTRE_DEG = 1.55   # spot within this distance of a photo's centre (photo half-width 1.74 deg)
N_REFS = 3
SEED = 20261114


def load_most(path: Path) -> dict[int, list[dict]]:
    by_num: dict[int, list[dict]] = defaultdict(list)
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            for k in ("mjd_mid", "ra_obj", "dec_obj", "vmag", "dist_ctr", "image_ra", "image_dec"):
                r[k] = float(r[k])
            r["detector"] = int(r["detector"])
            r["number"] = int(r["number"])
            by_num[r["number"]].append(r)
    for rows in by_num.values():
        rows.sort(key=lambda r: r["mjd_mid"])
    return by_num


def references(row: dict, rows: list[dict]) -> list[dict]:
    """Photos of the same detector, 0.25-12 days away, covering row's spot, asteroid >= 90" away."""
    cands = []
    for r in rows:
        if r is row or r["detector"] != row["detector"]:
            continue
        dt = abs(r["mjd_mid"] - row["mjd_mid"])
        if not (MIN_REF_DT <= dt <= MAX_REF_DT):
            continue
        if angsep_arcsec(r["ra_obj"], r["dec_obj"], row["ra_obj"], row["dec_obj"]) < MIN_REF_AWAY:
            continue
        if angsep_arcsec(r["image_ra"], r["image_dec"], row["ra_obj"], row["dec_obj"]) > MAX_CENTRE_DEG * 3600:
            continue
        cands.append((dt, r))
    cands.sort(key=lambda c: c[0])
    # one reference per exposure (obs_id), nearest in time first
    out, seen = [], set()
    for _, r in cands:
        if r["obs_id"] not in seen:
            out.append(r)
            seen.add(r["obs_id"])
    return out


def pick_photos(rows: list[dict], per: int) -> list[tuple[dict, list[dict]]]:
    """Up to `per` photos spread over time, each with >= 2 candidate references."""
    ok = [r for r in rows if r["detector"] in DETECTORS and r["dist_ctr"] <= MAX_CENTRE_DEG]
    ok = [(r, refs) for r in ok if len(refs := references(r, rows)) >= 2]
    if len(ok) <= per:
        return ok
    idx = np.unique(np.linspace(0, len(ok) - 1, per).round().astype(int))
    return [ok[i] for i in idx]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--most", type=Path, default=MOST_CSV)
    ap.add_argument("--first", type=int, default=1)
    ap.add_argument("--last", type=int, default=60)
    ap.add_argument("--per", type=int, default=5, help="photos per asteroid (default 5)")
    ap.add_argument("--workers", type=int, default=12)
    args = ap.parse_args()

    if not args.most.exists():
        raise SystemExit(f"{args.most} missing: gcloud storage cp gs://nasa-glazing-data/results/most_1_60.csv {DATA}/")
    most = load_most(args.most)
    rng = random.Random(SEED)

    # 1. plan: photos, their references (spare ones kept in case a box is off the photo), control spots
    plan = []
    for num in range(args.first, args.last + 1):
        rows = most.get(num, [])
        picked = pick_photos(rows, args.per)
        for row, refs in picked:
            pa = rng.uniform(0.0, 360.0)
            plan.append({"row": row, "refs": refs[: N_REFS + 2], "control": offset_position(row["ra_obj"], row["dec_obj"], CONTROL_ARCSEC, pa), "control_pa": pa})
        if not picked:
            print(f"  {num}: no photo with references", flush=True)

    # 2. fetch: one box per photo and per reference, all centred on the photo's predicted spot
    jobs = []
    for p in plan:
        ra, dec = p["row"]["ra_obj"], p["row"]["dec_obj"]
        jobs.append((p["row"]["s3_key"], ra, dec, HALF_PX))
        jobs += [(r["s3_key"], ra, dec, HALF_PX) for r in p["refs"]]
    got = fetch_boxes(jobs, workers=args.workers, label=f"V4: {len(plan)} photos")

    # 3. measure
    photos = []
    for p in plan:
        row = p["row"]
        ra, dec = row["ra_obj"], row["dec_obj"]
        rec = {"number": row["number"], "name": row["name"], "obs_id": row["obs_id"], "detector": row["detector"],
               "mjd_mid": row["mjd_mid"], "vmag": row["vmag"], "ra_pred": ra, "dec_pred": dec,
               "n_refs": 0, "status": "ok"}
        sci = got.get((row["s3_key"], ra, dec, HALF_PX))
        if not isinstance(sci, Path):
            rec["status"] = f"photo box failed: {sci}"
            photos.append(rec)
            continue
        box = load_box(sci)
        refs = []
        for r in p["refs"]:
            rp = got.get((r["s3_key"], ra, dec, HALF_PX))
            if isinstance(rp, Path) and len(refs) < N_REFS:
                refs.append(load_box(rp))
        rec["n_refs"] = len(refs)
        if len(refs) < 2:
            rec["status"] = "fewer than 2 references"
            photos.append(rec)
            continue
        stack, stack_bad = reference_stack(box, refs)

        for tag, (sra, sdec) in (("pred", (ra, dec)), ("ctrl", p["control"])):
            x, y = box.to_pixel(sra, sdec)
            hit = source_near(box.image, x, y, flags=box.flags, variance=box.variance, radius_px=MATCH_PX, thresh=5.0, wings=True)
            in_stack = source_near(stack, x, y, mask=stack_bad, radius_px=STACK_MATCH_PX, thresh=4.0)
            rec[f"{tag}_in_photo"] = hit.found
            rec[f"{tag}_in_stack"] = in_stack.found
            rec[f"{tag}_found"] = hit.found and not in_stack.found
            rec[f"{tag}_offset_px"] = None if hit.offset_px is None else round(hit.offset_px, 3)
            rec[f"{tag}_snr"] = None if hit.snr is None else round(hit.snr, 1)
            rec[f"{tag}_method"] = hit.method
        photos.append(rec)
        print(f"  {row['number']:>2} D{row['detector']} {row['obs_id']}: V {row['vmag']:.1f}  "
              f"found={rec['pred_found']} (photo {rec['pred_in_photo']}, stack {rec['pred_in_stack']}, "
              f"off {rec['pred_offset_px']})  control={rec['ctrl_found']}", flush=True)

    # 4. summarise
    measured = [r for r in photos if r["status"] == "ok"]
    by_ast: dict[int, list[dict]] = defaultdict(list)
    for r in measured:
        by_ast[r["number"]].append(r)
    rows_out = []
    for num in range(args.first, args.last + 1):
        rs = by_ast.get(num, [])
        name = rs[0]["name"] if rs else next((r["name"] for r in most.get(num, [])[:1]), str(num))
        offs = [r["pred_offset_px"] for r in rs if r["pred_found"]]
        rows_out.append({
            "number": num, "name": name,
            "vmag_median": round(float(np.median([r["vmag"] for r in rs])), 2) if rs else None,
            "photos": len(rs),
            "found_photos": sum(r["pred_found"] for r in rs),
            "found": any(r["pred_found"] for r in rs),
            "median_offset_px": round(float(np.median(offs)), 3) if offs else None,
            "control_photos": sum(r["ctrl_found"] for r in rs),
            "control_found": any(r["ctrl_found"] for r in rs),
            "saturated_wings": sum(r.get("pred_method") == "wings" for r in rs),
        })
    tested = [r for r in rows_out if r["photos"] > 0]
    n_found = sum(r["found"] for r in tested)
    n_ctrl = sum(r["control_found"] for r in tested)
    all_offs = [r["pred_offset_px"] for r in measured if r["pred_found"]]
    n_ph = len(measured)
    ph_found = sum(r["pred_found"] for r in measured)
    ph_ctrl = sum(r["ctrl_found"] for r in measured)
    summary = {
        "generated": today(),
        "check": "V4",
        "method": __doc__.split("Input:")[0].strip(),
        "asteroids_asked": args.last - args.first + 1,
        "asteroids_tested": len(tested),
        "asteroids_found": n_found,
        "asteroids_control_found": n_ctrl,
        "photos_measured": n_ph,
        "photos_found": ph_found,
        "photos_control_found": ph_ctrl,
        "photo_found_rate": round(ph_found / n_ph, 4) if n_ph else None,
        "photo_control_rate": round(ph_ctrl / n_ph, 4) if n_ph else None,
        "median_offset_px": round(float(np.median(all_offs)), 3) if all_offs else None,
        "median_offset_arcsec": round(float(np.median(all_offs)) * PIXEL_ARCSEC, 2) if all_offs else None,
        "photos_failed": [{"number": r["number"], "obs_id": r["obs_id"], "detector": r["detector"], "status": r["status"]}
                          for r in photos if r["status"] != "ok"],
        "params": {"detectors": list(DETECTORS), "half_px": HALF_PX, "match_px": MATCH_PX, "stack_match_px": STACK_MATCH_PX,
                   "control_arcsec": CONTROL_ARCSEC, "ref_dt_days": [MIN_REF_DT, MAX_REF_DT],
                   "ref_min_away_arcsec": MIN_REF_AWAY, "n_refs": N_REFS, "seed": SEED,
                   "thresh_photo_sigma": 5.0, "thresh_stack_sigma": 4.0},
        "rows": rows_out,
    }
    write_json(OUT_JSON, summary)
    keys = sorted({k for r in photos for k in r})
    with open(OUT_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(photos)
    print(f"\nV4: re-found {n_found} of {len(tested)} asteroids (control {n_ctrl}); "
          f"photos {ph_found}/{n_ph} found, control {ph_ctrl}/{n_ph}; "
          f"median offset {summary['median_offset_px']} px  -> {OUT_JSON.name}")


if __name__ == "__main__":
    main()
