"""V1: the five test objects recovered where Gaia (or the literature, for WISE 0855) puts them.

For each target, visit (contracts/api.md rule, skyshift_core.visits) and detector: up to 5 photos
spread over the visit, a 41 x 41 px box around the target's predicted position in each (Gaia DR3
position + proper motion moved to the photo's mid-time; data/photolists/<slug>.csv), median-stacked
on the first box's pixel grid.

  recovered = a 5 sigma detection (sep at 2.5 sigma per pixel, aperture S/N >= 5) within 2 px of the predicted position at the visit's mid-time in
              the stack. Saturated cores (OVERFLOW/NONLINEAR masked) fall back to a centroid fitted to
              the wings in each photo; the visit counts when most photos give one within 2 px.

A target is recovered in a visit when at least one expected detector recovers it, and recovered when
that happens in at least one visit; visits recovered / visits is reported next to it. WISE 0855-0714
is expected only in D5-D6 (and stacks every photo of a visit: it is near one photo's depth).

Output: data/v1_targets.json (summary + one row per target, visit and detector).

  uv run python v1_targets.py               # ~700 boxes, ~5 min cold, cached in .cache/boxes/
  uv run python v1_targets.py --only barnard
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict

import numpy as np

from skyshift_core import visits as visit_ids

from _boxes import fetch_boxes, load_box, reference_stack, source_near
from _common import DATA, PHOTOLISTS, PIXEL_ARCSEC, today, write_json

OUT_JSON = DATA / "v1_targets.json"
SLUGS = ("barnard", "epsindib", "wise0855", "rwaur", "rdra")
EXPECTED_DETS = {"wise0855": (5, 6)}
HALF_PX = 20
MIN_EDGE_PX = 25
MATCH_PX = 2.0
PER_STACK = 5
PIX_SIGMA = 2.5   # sep threshold per pixel (minarea 3) ...
MIN_SNR = 5.0     # ... and aperture S/N >= 5: a 5 sigma detection. 5 sigma per pixel missed WISE 0855 (aperture S/N 6-10).
# WISE 0855 is AB ~17.2 at 4.6 um, near one photo's depth: stack every photo in the visit
PER_STACK_BY_SLUG = {"wise0855": 80}


def load_rows(slug: str) -> list[dict]:
    with open(PHOTOLISTS / f"{slug}.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for k in ("mjd_mid", "obj_ra", "obj_dec", "edge_px"):
            r[k] = float(r[k])
        r["detector"] = int(r["detector"])
    # visit ids by the contract rule, on the exposure times of this position
    mjds = np.array([r["mjd_mid"] for r in rows])
    for r, v in zip(rows, visit_ids(mjds)):
        r["visit"] = int(v)
    return rows


def spread(rows: list[dict], n: int) -> list[dict]:
    rows = sorted(rows, key=lambda r: r["mjd_mid"])
    if len(rows) <= n:
        return rows
    return [rows[i] for i in np.unique(np.linspace(0, len(rows) - 1, n).round().astype(int))]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", default=",".join(SLUGS))
    ap.add_argument("--workers", type=int, default=12)
    args = ap.parse_args()
    slugs = [s for s in args.only.split(",") if s]

    groups: dict[tuple[str, int, int], list[dict]] = {}
    for slug in slugs:
        by = defaultdict(list)
        for r in load_rows(slug):
            if r["edge_px"] >= MIN_EDGE_PX:
                by[(slug, r["visit"], r["detector"])].append(r)
        for k in sorted(by):
            groups[k] = spread(by[k], PER_STACK_BY_SLUG.get(slug, PER_STACK))

    jobs = [(r["s3_key"], r["obj_ra"], r["obj_dec"], HALF_PX) for rs in groups.values() for r in rs]
    got = fetch_boxes(jobs, workers=args.workers, label=f"V1: {len(groups)} stacks")

    rows_out = []
    for (slug, visit, det), rs in groups.items():
        boxes = [(r, got.get((r["s3_key"], r["obj_ra"], r["obj_dec"], HALF_PX))) for r in rs]
        boxes = [(r, load_box(p)) for r, p in boxes if not isinstance(p, Exception) and p is not None]
        rec = {"slug": slug, "visit": visit, "detector": det, "photos": len(boxes),
               "mjd_mid": round(float(np.mean([r["mjd_mid"] for r in rs])), 4),
               "expected": det in EXPECTED_DETS.get(slug, range(1, 7))}
        if not boxes:
            rec.update(recovered=False, method="no photo", offset_px=None, offset_arcsec=None, snr=None)
            rows_out.append(rec)
            continue
        ra = float(np.mean([r["obj_ra"] for r, _ in boxes]))
        dec = float(np.mean([r["obj_dec"] for r, _ in boxes]))
        first = boxes[0][1]
        stack, bad = reference_stack(first, [b for _, b in boxes], mask_any=True)
        x, y = first.to_pixel(ra, dec)
        hit = source_near(stack, x, y, mask=bad, radius_px=MATCH_PX, thresh=PIX_SIGMA, min_snr=MIN_SNR, wings=True)
        method, offset, snr = hit.method, hit.offset_px, hit.snr
        if not hit.found:
            # saturated core: wings centroid photo by photo
            singles = [source_near(b.image, *b.to_pixel(r["obj_ra"], r["obj_dec"]), flags=b.flags,
                                   variance=b.variance, radius_px=MATCH_PX, thresh=PIX_SIGMA, min_snr=MIN_SNR, wings=True)
                       for r, b in boxes]
            ok = [h for h in singles if h.found]
            if len(ok) * 2 > len(singles):
                method = "wings" if any(h.method == "wings" for h in ok) else "photos"
                offset = float(np.median([h.offset_px for h in ok]))
                snr = None
                hit.found = True
        rec.update(recovered=bool(hit.found), method=method,
                   offset_px=None if offset is None else round(offset, 3),
                   offset_arcsec=None if offset is None else round(offset * PIXEL_ARCSEC, 2),
                   snr=None if snr is None else round(snr, 1),
                   ra_pred=round(ra, 7), dec_pred=round(dec, 7))
        rows_out.append(rec)
        print(f"  {slug:9s} visit {visit} D{det}: {len(boxes)} photos  recovered={rec['recovered']} "
              f"({method}, off {rec['offset_px']} px, snr {rec['snr']})", flush=True)

    targets = []
    for slug in slugs:
        rs = [r for r in rows_out if r["slug"] == slug]
        vis = sorted({r["visit"] for r in rs})
        vis_ok = [v for v in vis if any(r["recovered"] for r in rs if r["visit"] == v and r["expected"])]
        exp = [r for r in rs if r["expected"]]
        offs = [r["offset_px"] for r in exp if r["recovered"] and r["offset_px"] is not None]
        targets.append({
            "slug": slug, "visits": len(vis), "visits_recovered": len(vis_ok),
            "recovered": len(vis_ok) > 0,
            "recovered_every_visit": bool(vis) and len(vis_ok) == len(vis),
            "stacks_expected": len(exp), "stacks_recovered": sum(r["recovered"] for r in exp),
            "stacks_unexpected_recovered": sum(r["recovered"] for r in rs if not r["expected"]),
            "detectors_recovered": sorted({r["detector"] for r in rs if r["recovered"]}),
            "median_offset_px": round(float(np.median(offs)), 3) if offs else None,
            "saturated_wings": sum(r["method"] == "wings" for r in rs),
        })
    n_rec = sum(t["recovered"] for t in targets)
    write_json(OUT_JSON, {
        "generated": today(), "check": "V1", "method": __doc__.split("Output:")[0].strip(),
        "targets_tested": len(targets), "targets_recovered": n_rec,
        "params": {"half_px": HALF_PX, "match_px": MATCH_PX, "per_stack": PER_STACK, "pixel_sigma": PIX_SIGMA, "min_aperture_snr": MIN_SNR},
        "targets": targets, "rows": rows_out,
    })
    for t in targets:
        print(f"{t['slug']:9s} visits {t['visits_recovered']}/{t['visits']}  stacks {t['stacks_recovered']}/{t['stacks_expected']}  "
              f"dets {t['detectors_recovered']}  median off {t['median_offset_px']} px")
    print(f"\nV1: {n_rec} of {len(targets)} targets recovered; visits {sum(t['visits_recovered'] for t in targets)}"
          f"/{sum(t['visits'] for t in targets)}  -> {OUT_JSON.name}")


if __name__ == "__main__":
    main()
