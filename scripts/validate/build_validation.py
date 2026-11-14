"""Collect V1-V11 into web/public/data/validation.json (contracts/api.md, "validation.json").

Every check is printed even when its number is poor; a check with no input yet is "pending".

  V1  data/v1_targets.json                      (v1_targets.py)
  V2  <site-data>/<slug>/object.json `measure`   (scripts/precompute, PR #13) + data/targets.json
  V3  the same `measure`: sigma_arcsec from the reference stars
  V4  data/v4_asteroids.json                    (v4_asteroids.py)
  V5  <results>/<zone>/recall.json              (pipeline score; gs://nasa-glazing-data/results/)
  V6  cut (S2)          V7  cut (S3)
  V8  --demo-check JSON (scripts/demo_check.py --json): median time to first frame
      (the spectra half of V8 is not built: pending)
  V9  <results>/<zone>/astrometry_report.json   (pipeline astrometry)
  V10 <results>/<zone>/photoz_report.json       (pipeline photoz)
  V11 scripts/science/data/asteroid_sizes.json  (scripts/science asteroids.py)

  uv run python build_validation.py --site-data ../../web/public/data --results .cache/results \\
      --zone pilot --demo-check /path/to/demo_check.json
  uv run python build_validation.py --fetch-results pilot      # gcloud storage cp the zone's reports first
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
from pathlib import Path

from _common import BUCKET, CACHE, DATA, today, write_json

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
SITE_DATA = REPO / "web" / "public" / "data"
OUT = SITE_DATA / "validation.json"
SIZES = REPO / "scripts" / "science" / "data" / "asteroid_sizes.json"
TARGET_SLUGS = ("barnard", "epsindib", "wise0855", "rwaur", "rdra")
DATA_RELEASE = "SPHEREx DR1 + QR3 (IRSA, as of 14 Nov 2026)"


def load(path: Path | None) -> dict | None:
    if path is None or not path.exists():
        return None
    return json.loads(path.read_text())


def check(id_: str, claim: str, status: str, headline: str, value=None, unit=None, expected="", note=None, rows=None) -> dict:
    return {"id": id_, "claim": claim, "status": status, "headline": headline, "value": value, "unit": unit,
            "expected": expected, "note": note, "rows": rows or []}


def r(x, n=2):
    return None if x is None or (isinstance(x, float) and not math.isfinite(x)) else round(float(x), n)


# --- V1 ---------------------------------------------------------------------------------------

def v1() -> dict:
    claim = "Five test objects recovered"
    exp = "5 of 5; WISE 0855 only in D5-D6"
    d = load(DATA / "v1_targets.json")
    if not d:
        return check("V1", claim, "pending", "Not run yet", expected=exp)
    n, tot = d["targets_recovered"], d["targets_tested"]
    rows = [{"slug": t["slug"], "recovered": t["recovered"], "visits": t["visits"], "visits_recovered": t["visits_recovered"],
             "stacks_expected": t["stacks_expected"], "stacks_recovered": t["stacks_recovered"],
             "detectors": ",".join(f"D{x}" for x in t["detectors_recovered"]), "median_offset_px": t["median_offset_px"]}
            for t in d["targets"]]
    missed = [t["slug"] for t in d["targets"] if not t["recovered"]]
    nv, nv_ok = sum(t["visits"] for t in d["targets"]), sum(t["visits_recovered"] for t in d["targets"])
    offs = sorted(t["median_offset_px"] for t in d["targets"] if t["median_offset_px"] is not None)
    status = "pass" if n == tot == 5 else ("partial" if n >= 3 else "fail")
    note = ("Recovered = a 5 sigma source (aperture S/N >= 5) within 2 px of the predicted position (Gaia DR3 moved to the "
            "visit's mid-time; published astrometry for WISE 0855) in a median stack of up to 5 photos of one visit and one "
            "expected detector. Saturated cores: Gaussian fit to the wings, or the centre of the masked core. Rows: slug, "
            "recovered, visits, visits_recovered, stacks_expected, stacks_recovered, detectors, median_offset_px.")
    w = next((t for t in d["targets"] if t["slug"] == "wise0855"), None)
    if w:
        note += (f" WISE 0855-0714 (D5-D6 only, every photo of a visit stacked): {w['visits_recovered']} of {w['visits']} visits; "
                 "its first visit has only 8 D5 and 3 D6 photos. In D1-D4 a background star lies 1-2 px from it: those stacks "
                 "are not counted.")
    return check("V1", claim, status,
                 f"{n} of {tot} test objects found where predicted, in {nv_ok} of {nv} visits"
                 + (f"; median offset {offs[len(offs) // 2]} px" if offs else "")
                 + (f" ({', '.join(missed)} missed)" if missed else ""),
                 value=n, unit="targets", expected=exp, note=note, rows=rows)


# --- V2, V3 -----------------------------------------------------------------------------------

def _gaia_pa(t: dict) -> float:
    return math.degrees(math.atan2(t["pmra"], t["pmdec"])) % 360.0


def _pa_diff(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def v2_v3(site: Path) -> tuple[dict, dict]:
    targets = {t["slug"]: t for t in json.loads((DATA / "targets.json").read_text())["targets"]}
    rows = []
    for slug in TARGET_SLUGS:
        o = load(site / slug / "object.json")
        m = (o or {}).get("measure") or None
        t = targets.get(slug, {})
        if not m:
            rows.append({"slug": slug, "measured": False})
            continue
        pred_pa = _gaia_pa(t) if t else None
        shift, pred = m.get("shift_arcsec"), m.get("predicted_arcsec")
        rows.append({"slug": slug, "measured": True, "shift_arcsec": r(shift), "predicted_arcsec": r(pred),
                     "sigma_arcsec": r(m.get("sigma_arcsec")), "pa_deg": r(m.get("pa_deg"), 1),
                     "predicted_pa_deg": r(pred_pa, 1), "dt_yr": r(m.get("dt_yr"), 3),
                     "rate_arcsec_yr": r(m.get("rate_arcsec_yr")), "n_ref_stars": m.get("n_ref_stars"),
                     # % and PA errors only mean something for a target that moves (> 1")
                     "shift_error_pct": r(100 * (shift - pred) / pred, 1) if shift and pred and pred > 1 else None,
                     "pa_error_deg": r(_pa_diff(m["pa_deg"], pred_pa), 1) if pred and pred > 1 and pred_pa is not None and m.get("pa_deg") is not None else None})
    b = next((x for x in rows if x["slug"] == "barnard" and x["measured"]), None)
    claim2, exp2 = "Motion matches Gaia", "Barnard's Star within 15% and 10 deg of Gaia (10.39\"/yr)"
    note2 = ("Shift = position in the last visit stack minus the first, against reference stars in the same cutouts; "
             "predicted = Gaia DR3 proper motion and parallax over the same dates (WISE 0855: published astrometry). "
             "Rows: slug, shift_arcsec, predicted_arcsec, sigma_arcsec, pa_deg, predicted_pa_deg, dt_yr, shift_error_pct, pa_error_deg.")
    if b is None:
        c2 = check("V2", claim2, "pending", "Barnard's Star not measured yet", expected=exp2, note=note2, rows=rows)
    else:
        ok = abs(b["shift_error_pct"]) <= 15 and b["pa_error_deg"] <= 10
        c2 = check("V2", claim2, "pass" if ok else "fail",
                   f"Barnard's Star moved {b['shift_arcsec']}\" ± {b['sigma_arcsec']}\" at PA {b['pa_deg']}° in {b['dt_yr']} yr; "
                   f"Gaia predicts {b['predicted_arcsec']}\" ({b['shift_error_pct']:+.1f}%, {b['pa_error_deg']}°)",
                   value=b["shift_arcsec"], unit="arcsec", expected=exp2, note=note2, rows=rows)
    claim3, exp3 = "Position error floor", "Floor ~0.25\" (first photo); 10 or more reference stars"
    note3 = ("sigma_arcsec is the scatter of the visit-to-visit shifts of quiet Gaia stars (G 13-17, < 20 mas/yr) in the "
             "same cutouts; it is V2's error bar. Slowest motion visible at 3 sigma = 3 sigma / dt. Rows: slug, sigma_arcsec, "
             "n_ref_stars, dt_yr, min_rate_3sigma_arcsec_yr.")
    rows3 = [{"slug": x["slug"], "sigma_arcsec": x["sigma_arcsec"], "n_ref_stars": x["n_ref_stars"], "dt_yr": x["dt_yr"],
              "min_rate_3sigma_arcsec_yr": r(3 * x["sigma_arcsec"] / x["dt_yr"]) if x.get("sigma_arcsec") and x.get("dt_yr") else None}
             for x in rows if x["measured"]]
    if b is None:
        c3 = check("V3", claim3, "pending", "Not measured yet", unit="arcsec", expected=exp3, note=note3, rows=rows3)
    else:
        b3 = next(x for x in rows3 if x["slug"] == "barnard")
        c3 = check("V3", claim3, "pass" if (b3["n_ref_stars"] or 0) >= 10 else "partial",
                   f"Position error {b3['sigma_arcsec']}\" from {b3['n_ref_stars']} reference stars: motions above "
                   f"{b3['min_rate_3sigma_arcsec_yr']}\"/yr are seen at 3 sigma in one year",
                   value=b3["sigma_arcsec"], unit="arcsec", expected=exp3, note=note3, rows=rows3)
    return c2, c3


# --- V4 ---------------------------------------------------------------------------------------

def v4() -> dict:
    claim, exp = "Asteroids re-found, targeted", "N of 60; median offset under 1 px; control under 5%"
    d = load(DATA / "v4_asteroids.json")
    if not d:
        return check("V4", claim, "pending", "Not run yet", expected=exp)
    n, tot = d["asteroids_found"], d["asteroids_tested"]
    ctrl_rate, off = d["photo_control_rate"], d["median_offset_px"]
    ok = off is not None and off < 1.0 and ctrl_rate is not None and ctrl_rate < 0.05
    status = "pass" if ok and n >= 0.8 * tot else ("partial" if n > 0 else "fail")
    rows = [{k: a[k] for k in ("number", "name", "vmag_median", "photos", "found_photos", "found", "median_offset_px",
                               "control_photos", "control_found")} for a in d["rows"]]
    note = (f"MOST's predicted position in up to 5 photos per asteroid (D2-D3). Found = a 5 sigma detection within 2 px that is "
            f"absent (4 sigma) from a reference stack of the same sky 0.25-12 days away. Control = the same test 3' away. "
            f"Per photo: {d['photos_found']}/{d['photos_measured']} found, control {d['photos_control_found']}/{d['photos_measured']}; "
            f"per asteroid control {d['asteroids_control_found']}/{tot}. Rows: number, name, vmag_median, photos, found_photos, "
            f"found, median_offset_px, control_photos, control_found.")
    return check("V4", claim, status,
                 f"Re-found {n} of {tot} asteroids where NASA predicted them; median offset {off} px "
                 f"({d['median_offset_arcsec']}\"); control 3' away: {100 * ctrl_rate:.1f}% of photos, "
                 f"{d['asteroids_control_found']} of {tot} asteroids",
                 value=n, unit="asteroids", expected=exp, note=note, rows=rows)


# --- V5, V9, V10 (pipeline reports) -----------------------------------------------------------

def v5(res: Path | None, zone: str) -> dict:
    claim, exp = "Asteroids re-found, blind (S1)", "Recall at V < 16 >= 50% (gate G2); control near 0"
    d = load(res / zone / "recall.json") if res else None
    if not d:
        return check("V5", claim, "pending", "Numbers after the pilot", expected=exp)
    rows = [{"v_min": b.get("v_min"), "v_max": b.get("v_max"), "n": b.get("n"), "found": b.get("found"),
             "recall": r(b.get("recall"), 3), "control_recall": r(b.get("control_recall"), 3)} for b in d.get("by_v", [])]
    rec, ctl = d.get("recall"), d.get("control_recall")
    po = d.get("per_object") or {}
    return check("V5", claim, "pass" if d.get("g2_pass") else "fail",
                 f"{zone}: {d.get('n_found')} of {d.get('n_known')} known asteroid sessions at V < {d.get('v_max'):g} found "
                 f"as movers ({100 * (rec or 0):.0f}%); control {100 * (ctl or 0):.0f}%"
                 + (f"; {po.get('found')} of {po.get('n')} objects" if po.get("n") else ""),
                 value=r(rec, 3), unit="recall", expected=exp,
                 note=f"Zone {zone}. A known object counts as found when a mover lies within the match radius of its prediction "
                      "at the photo's MJD-AVG; control: every prediction moved 2'. Rows: v_min, v_max, n, found, recall, control_recall.",
                 rows=rows)


def v9(res: Path | None, zone: str) -> dict:
    claim, exp = "Astrometry matches Gaia", "Bias near zero; pull scatter near 1"
    d = load(res / zone / "astrometry_report.json") if res else None
    best = (d or {}).get("vs_gaia_frame_corrected") or (d or {}).get("vs_gaia_raw")
    if not best or not best.get("matched"):
        return check("V9", claim, "pending", "Numbers after the pilot", expected=exp)
    pa, pd_ = best.get("pmra", {}), best.get("pmdec", {})
    pulls = [x.get("pull_scatter") for x in (pa, pd_) if x.get("pull_scatter") is not None]
    ok = pulls and all(0.7 <= p <= 1.5 for p in pulls)
    rows = [{"axis": k, **{kk: v.get(kk) for kk in ("n", "bias", "scatter", "pull_scatter")}} for k, v in (("pmra", pa), ("pmdec", pd_))]
    return check("V9", claim, "pass" if ok else "partial",
                 f"{best.get('matched'):,} stars vs Gaia: proper-motion bias {r(pa.get('bias'), 0):g} (RA) and "
                 f"{r(pd_.get('bias'), 0):g} (Dec) mas/yr, scatter {r(pa.get('scatter'), 0):g} and {r(pd_.get('scatter'), 0):g} mas/yr; "
                 f"pull scatter {', '.join(f'{p:.2f}' for p in pulls)} (1 = honest error bars)",
                 value=pa.get("scatter"), unit="mas/yr", expected=exp,
                 note=f"Zone {zone}; proper motions fitted over all dots with SPHEREx's own position as the observer. "
                      "Pass when both pull scatters are 0.7-1.5. Rows: axis, n, bias, scatter, pull_scatter (mas/yr).",
                 rows=rows)


def v10(res: Path | None, zone: str) -> dict:
    claim, exp = "Galaxy distances match SDSS", "sigma_NMAD ~0.02 for bright galaxies (AB < 17)"
    d = load(res / zone / "photoz_report.json") if res else None
    d = (d or {}).get("sdss_dr17") or d  # the pipeline nests the SDSS comparison
    if not d or not (d.get("all") or {}).get("n"):
        return check("V10", claim, "pending", "Numbers after the pilot", expected=exp)
    bright = next((v for k, v in d.items() if k.startswith("k_ab_lt_")), None) or d["all"]
    s = bright.get("sigma_nmad")
    status = "pass" if s is not None and s <= 0.05 else ("partial" if s is not None and s <= 0.1 else "fail")
    rows = [{"sample": k, **{kk: r(v.get(kk), 4) if kk != "n" else v.get("n") for kk in ("n", "sigma_nmad", "outlier_frac", "bias")}}
            for k, v in d.items() if isinstance(v, dict) and "sigma_nmad" in v]
    rows += [{"sample": f"K_AB {k}", **{kk: r(v.get(kk), 4) if kk != "n" else v.get("n") for kk in ("n", "sigma_nmad", "outlier_frac", "bias")}}
             for k, v in (d.get("by_k_ab") or {}).items()]
    return check("V10", claim, status,
                 f"{bright.get('n')} bright galaxies: sigma_NMAD {r(s, 3)}, outliers {100 * (bright.get('outlier_frac') or 0):.0f}%",
                 value=r(s, 4), unit="dz/(1+z)", expected=exp,
                 note=f"Zone {zone}; EAZY templates on 102-channel spectra vs SDSS DR17 spectroscopic redshifts. "
                      "Pass <= 0.05, partial <= 0.1. Rows: sample, n, sigma_nmad, outlier_frac, bias.", rows=rows)


# --- V8, V11 ----------------------------------------------------------------------------------

def v8(demo: Path | None) -> dict:
    claim, exp = "Spectra and speed", "Spectra within 20% of 2MASS Ks / WISE W1; first frame under 15 s"
    d = load(demo)
    med = (d or {}).get("median_first_frame_s")
    note = "Speed: scripts/demo_check.py, random sky coordinates through /api/frames. Spectra half: not built yet."
    if med is None:
        return check("V8", claim, "pending", "Not measured yet", unit="s", expected=exp, note=note)
    rnd = d.get("random", [])
    rows = [{"ra": x.get("ra"), "dec": x.get("dec"), "clock": x.get("clock"), "ok": x.get("ok"),
             "first_frame_s": x.get("first_frame_s")} for x in rnd]
    n_ok = sum(1 for x in rnd if x.get("ok"))
    return check("V8", claim, "partial" if med < 15 else "fail",
                 f"Median time to first frame {med:.1f} s over {n_ok} random coordinates ({d.get('base')}); spectra check pending",
                 value=r(med, 1), unit="s", expected=exp, note=note + " Rows: ra, dec, clock, ok, first_frame_s.", rows=rows)


def v11() -> dict:
    claim, exp = "Asteroid sizes match published values", "Diameter ratio near 1"
    d = load(SIZES)
    if not d:
        return check("V11", claim, "pending", "Not run yet", expected=exp)
    s = d["summary"]
    rd, rp = s.get("ratio_D", {}), s.get("ratio_pv", {})
    rows = []
    for a in d.get("asteroids", []):
        row = {"number": a.get("number"), "name": a.get("name"), "n_points": a.get("n_points")}
        for k in ("D_km", "D_pub_km", "ratio_D", "pv", "pv_pub", "ratio_pv", "eta"):
            if k in a and not isinstance(a[k], (dict, list)):
                row[k] = a[k]
        rows.append(row)
    med = rd.get("median")
    status = "pass" if med is not None and abs(med - 1) <= 0.15 else "partial"
    return check("V11", claim, status,
                 f"{rd.get('n')} asteroids: diameter {r(med)}x published (scatter {100 * (rd.get('scatter_mad') or 0):.0f}%); "
                 f"albedo {r(rp.get('median'))}x (scatter {100 * (rp.get('scatter_mad') or 0):.0f}%)",
                 value=r(med, 3), unit="ratio", expected=exp,
                 note="scripts/science/asteroids.py: reflected light + NEATM fitted to D1-D6 cutouts at MOST's positions. "
                      "Ceres saturates. Rows: number, name, n_points and the fitted vs published values present in the input.",
                 rows=rows)


def fetch_results(zone: str, dest: Path) -> None:
    gcloud = shutil.which("gcloud")
    if not gcloud:
        raise SystemExit("gcloud not on PATH: copy the reports by hand into " + str(dest / zone))
    (dest / zone).mkdir(parents=True, exist_ok=True)
    for name in ("recall.json", "astrometry_report.json", "photoz_report.json"):
        subprocess.run([gcloud, "storage", "cp", f"{BUCKET}/results/{zone}/{name}", str(dest / zone / name)], check=False)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site-data", type=Path, default=SITE_DATA, help="folder with <slug>/object.json (default web/public/data)")
    ap.add_argument("--results", type=Path, default=CACHE / "results", help="folder with <zone>/recall.json etc.")
    ap.add_argument("--zone", default="pilot", help="pipeline zone for V5, V9, V10 (pilot or hunt)")
    ap.add_argument("--fetch-results", metavar="ZONE", help="gcloud storage cp the zone's reports into --results first")
    ap.add_argument("--demo-check", type=Path, help="JSON written by scripts/demo_check.py --json (V8)")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    if args.fetch_results:
        fetch_results(args.fetch_results, args.results)
        args.zone = args.fetch_results
    c2, c3 = v2_v3(args.site_data)
    checks = [
        v1(), c2, c3, v4(), v5(args.results, args.zone),
        check("V6", "Fast stars (S2)", "pending", "Cut for the one-day build", expected="Slope near 1",
              note="S2 was cut on Saturday; the pipeline's high-PM search (V9 data) covers fast stars in the zone."),
        check("V7", "Brown dwarfs (S3)", "pending", "Cut for the one-day build", expected="A count", note="S3 was cut on Saturday."),
        v8(args.demo_check), v9(args.results, args.zone), v10(args.results, args.zone), v11(),
    ]
    write_json(args.out, {"generated": today(), "data_release": DATA_RELEASE, "checks": checks})
    for c in checks:
        print(f"{c['id']:4s} {c['status']:8s} {c['headline']}")
    print(f"-> {args.out}")


if __name__ == "__main__":
    main()
