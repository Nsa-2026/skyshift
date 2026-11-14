"""Collect V1-V11 into web/public/data/validation.json (contracts/api.md, "validation.json").

Every check is printed even when its number is poor; a check with no input yet is "pending".

  V1  data/v1_targets.json                      (v1_targets.py)
  V2  <site-data>/<slug>/object.json `measure`   (scripts/precompute, PR #13) + data/targets.json
  V3  the same `measure`: sigma_arcsec from the reference stars
  V4  data/v4_asteroids.json                    (v4_asteroids.py)
  V5  <results>/<zone>/recall.json              (pipeline score; gs://nasa-glazing-data/results/)
  V6  cut (S2)          V7  cut (S3)
  V8  data/v8_spectra.json (v8_spectra.py: pilot stars vs 2MASS Ks and CatWISE W1)
      + --demo-check JSON (scripts/demo_check.py --json): median time to first frame
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


# --- status rules (pure, tested in tests/test_build_validation.py) ----------------------------
# A poor number is printed with "partial" or "fail"; nothing is rounded up to a pass.

def v1_status(found: int, tested: int) -> str:
    return "pass" if found == tested == 5 else ("partial" if found >= 3 else "fail")


def v2_status(shift_error_pct: float | None, pa_error_deg: float | None) -> str:
    """Barnard's Star within 15% and 10 deg of Gaia."""
    if shift_error_pct is None or pa_error_deg is None:
        return "pending"
    return "pass" if abs(shift_error_pct) <= 15 and pa_error_deg <= 10 else "fail"


def v3_status(n_ref_stars: int | None) -> str:
    return "pass" if (n_ref_stars or 0) >= 10 else "partial"


def v4_status(found: int, tested: int, median_offset_px: float | None, control_rate: float | None) -> str:
    ok = median_offset_px is not None and median_offset_px < 1.0 and control_rate is not None and control_rate < 0.05
    return "pass" if ok and found >= 0.8 * tested else ("partial" if found > 0 else "fail")


def v5_status(recall: float | None, control_recall: float | None, threshold: float = 0.5) -> str:
    """Gate G2: recall of known asteroids at V < 16 within 12" >= 50%, control near 0 (< 5%)."""
    if recall is None:
        return "pending"
    if recall >= threshold and (control_recall or 0) < 0.05:
        return "pass"
    return "partial" if recall > 0 else "fail"


def v8_status(spectra_pass: bool | None, median_first_frame_s: float | None, speed_measured: bool) -> str:
    """Both halves must pass for "pass"; one half missing is "partial"; any failed half is "fail"."""
    halves = []
    if spectra_pass is not None:
        halves.append("pass" if spectra_pass else "fail")
    if speed_measured:  # a run where half or more requests failed has no median: that is a fail
        halves.append("pass" if median_first_frame_s is not None and median_first_frame_s < 15 else "fail")
    if not halves:
        return "pending"
    if "fail" in halves:
        return "fail"
    return "pass" if len(halves) == 2 else "partial"


def dev_sigma(shift: float | None, predicted: float | None, sigma: float | None) -> float | None:
    """|measured - predicted| shift in units of the shift's error bar."""
    if shift is None or predicted is None or not sigma:
        return None
    return abs(shift - predicted) / sigma


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
    status = v1_status(n, tot)
    note = ("Recovered = a 5 sigma source (aperture S/N >= 5) within 2 px of the predicted position (Gaia DR3 moved to the "
            "visit's mid-time; published astrometry for WISE 0855) in a median stack of up to 5 photos of one visit and one "
            "expected detector. Saturated cores: Gaussian fit to the wings, or the centre of the masked core "
            "(scripts/validate/v1_targets.py, 41 px boxes read from NASA's S3 copy).")
    w = next((t for t in d["targets"] if t["slug"] == "wise0855"), None)
    if w:
        note += (f" WISE 0855-0714 (D5-D6 only, every photo of a visit stacked): {w['visits_recovered']} of {w['visits']} visits; "
                 "its first visit has only 8 D5 and 3 D6 photos. In D1-D4 a background star lies 1-2 px from it: those stacks "
                 "are not counted.")
    rd = next((t for t in d["targets"] if t["slug"] == "rdra"), None)
    if rd and rd["stacks_recovered"] < rd["stacks_expected"]:
        note += (f" R Draconis is found in {rd['stacks_recovered']} of {rd['stacks_expected']} stacks; the misses are in D4-D5 "
                 "while D1-D3 find it in the same visits (not yet explained).")
    note += (" Rows: slug, recovered, visits, visits_recovered, stacks_expected, stacks_recovered, detectors, "
             "median_offset_px.")
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
                     "dev_sigma": r(dev_sigma(shift, pred, m.get("sigma_arcsec")), 1),
                     # % and PA errors only mean something for a target that moves (> 1")
                     "shift_error_pct": r(100 * (shift - pred) / pred, 1) if shift and pred and pred > 1 else None,
                     "pa_error_deg": r(_pa_diff(m["pa_deg"], pred_pa), 1) if pred and pred > 1 and pred_pa is not None and m.get("pa_deg") is not None else None})
    b = next((x for x in rows if x["slug"] == "barnard" and x["measured"]), None)
    claim2, exp2 = "Motion matches Gaia", "Barnard's Star within 15% and 10 deg of Gaia (10.39\"/yr)"
    meas = [x for x in rows if x["measured"]]
    within = [x for x in meas if x["dev_sigma"] is not None and x["dev_sigma"] <= 3]
    worst = max((x for x in meas if x["dev_sigma"] is not None), key=lambda x: x["dev_sigma"], default=None)
    note2 = ("Shift = the target's position in the last visit stack minus the first, measured against reference stars in "
             "the same cutouts (site data <slug>/object.json, field measure); predicted = Gaia DR3 proper motion and "
             "parallax over the same dates (WISE 0855: published astrometry). dev_sigma = |shift - predicted| / sigma_arcsec. "
             "The pass rule is set on Barnard's Star; RW Aurigae and R Draconis barely move, so for them only dev_sigma "
             "means anything."
             + ((f" {len(within)} of {len(meas)} measured targets agree with the prediction within 3 sigma"
                 + (f" (largest: {worst['slug']} at {worst['dev_sigma']} sigma)." if worst else ".")) if meas else "")
             + " Rows: slug, measured, shift_arcsec, predicted_arcsec, sigma_arcsec, dev_sigma, pa_deg, predicted_pa_deg, "
               "dt_yr, rate_arcsec_yr, n_ref_stars, shift_error_pct, pa_error_deg.")
    if b is None:
        c2 = check("V2", claim2, "pending", "Barnard's Star not measured yet", expected=exp2, note=note2, rows=rows)
    else:
        c2 = check("V2", claim2, v2_status(b["shift_error_pct"], b["pa_error_deg"]),
                   f"Barnard's Star moved {b['shift_arcsec']}\" ± {b['sigma_arcsec']}\" at PA {b['pa_deg']}° in {b['dt_yr']} yr; "
                   f"Gaia predicts {b['predicted_arcsec']}\" ({b['shift_error_pct']:+.1f}%, {b['pa_error_deg']}°, "
                   f"{b['dev_sigma']} sigma); {len(within)} of {len(meas)} targets within 3 sigma",
                   value=b["shift_arcsec"], unit="arcsec", expected=exp2, note=note2, rows=rows)
    claim3, exp3 = "Position error floor", "Floor ~0.25\" (first photo); 10 or more reference stars"
    sig = sorted(x["sigma_arcsec"] for x in rows if x["measured"] and x.get("sigma_arcsec") is not None)
    note3 = ("sigma_arcsec is the scatter of the visit-to-visit shifts of quiet Gaia stars (G 13-17, under 20 mas/yr) in "
             "the same cutouts as the target; it is V2's error bar. The slowest motion visible at 3 sigma is 3 sigma / dt. "
             "The headline is Barnard's Star's field"
             + (f"; across the {len(sig)} measured fields sigma runs {sig[0]}-{sig[-1]}\" (fewer reference stars or a "
                "fainter target give a larger error)." if sig else ".")
             + " Rows: slug, sigma_arcsec, n_ref_stars, dt_yr, min_rate_3sigma_arcsec_yr.")
    rows3 = [{"slug": x["slug"], "sigma_arcsec": x["sigma_arcsec"], "n_ref_stars": x["n_ref_stars"], "dt_yr": x["dt_yr"],
              "min_rate_3sigma_arcsec_yr": r(3 * x["sigma_arcsec"] / x["dt_yr"]) if x.get("sigma_arcsec") and x.get("dt_yr") else None}
             for x in rows if x["measured"]]
    if b is None:
        c3 = check("V3", claim3, "pending", "Not measured yet", unit="arcsec", expected=exp3, note=note3, rows=rows3)
    else:
        b3 = next(x for x in rows3 if x["slug"] == "barnard")
        c3 = check("V3", claim3, v3_status(b3["n_ref_stars"]),
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
    status = v4_status(n, tot, off, ctrl_rate)
    rows = [{k: a[k] for k in ("number", "name", "vmag_median", "photos", "found_photos", "found", "median_offset_px",
                               "control_photos", "control_found")} for a in d["rows"]]
    note = (f"Asteroids numbered 1-60 at IRSA MOST's predicted position in up to 5 photos each (D2-D3, 81 px boxes read from "
            f"NASA's S3 copy). Found = a 5 sigma detection within 2 px that is "
            f"absent from a reference stack of the same sky 0.25-12 days away (nothing at 4 sigma within 3 px). Control = the same test 3' away. "
            f"Per photo: {d['photos_found']}/{d['photos_measured']} found, control {d['photos_control_found']}/{d['photos_measured']}; "
            f"per asteroid control {d['asteroids_control_found']}/{tot}. Photos not counted mostly have a static star within "
            f"3 px in the reference stack, so a detection there can't be told apart. Rows: number, name, vmag_median, photos, found_photos, "
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
    v12 = d.get("v_12_to_16") or {}
    return check("V5", claim, v5_status(rec, ctl, d.get("g2_threshold") or 0.5),
                 f"{zone}: {d.get('n_found')} of {d.get('n_known')} known asteroid sessions at V < {d.get('v_max'):g} found "
                 f"as movers ({100 * (rec or 0):.0f}%); control {100 * (ctl or 0):.0f}%"
                 + (f"; {po.get('found')} of {po.get('n')} objects" if po.get("n") else ""),
                 value=r(rec, 3), unit="recall", expected=exp,
                 note=f"Zone {zone} ({d.get('photos_with_predictions')} photos with predictions; pipeline report of "
                      f"{d.get('generated')}). Unit: {d.get('unit')}. Known asteroids from SkyBoT, moved to each photo's MJD-AVG; "
                      f"one counts as found when a blind-search mover lies within {d.get('match_arcsec'):g}\" of the prediction; "
                      f"control: every prediction moved {(d.get('control_offset_arcsec') or 120) / 60:g}'. Gate G2 is recall "
                      f">= {100 * (d.get('g2_threshold') or 0.5):.0f}% at V < {d.get('v_max'):g}"
                      + (f"; V 12-16 alone: {v12.get('found')} of {v12.get('n')}" if v12.get("n") else "")
                      + ". Objects brighter than V 12 saturate but are still counted. "
                      + ("The hunt zone is not finished; these are pilot numbers. " if zone == "pilot" else "")
                      + "Rows: v_min, v_max, n, found, recall, control_recall.",
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

def _failure_causes(rows: list[dict]) -> str:
    """'12 HTTP 429, 8 no answer within the timeout' for the failed demo_check requests."""
    causes: dict[str, int] = {}
    for x in rows:
        if x.get("ok"):
            continue
        err = str(x.get("error") or "")
        key = (f"HTTP {x['status']}" if x.get("status") else
               "no answer within the timeout" if "timeout" in err else "no response")
        causes[key] = causes.get(key, 0) + 1
    return ", ".join(f"{n} {k}" for k, n in sorted(causes.items(), key=lambda kv: -kv[1]))


def v8(demo: Path | None) -> dict:
    claim, exp = "Spectra and speed", "Spectra within 20% of 2MASS Ks / WISE W1; first frame under 15 s"
    sp = load(DATA / "v8_spectra.json")
    d = load(demo)
    med = (d or {}).get("median_first_frame_s")
    speed_measured = bool(d and d.get("n_random"))
    if not sp and not speed_measured:
        return check("V8", claim, "pending", "Not measured yet", unit="ratio", expected=exp)
    parts, rows = [], []
    if sp:
        for name, b in sp["bands"].items():
            c = b["clean"]
            parts.append(f"{name} {c['median']:.3f} (scatter {100 * c['scatter']:.1f}%, {c['n']:,} stars)")
            rows += [{"part": "spectra", **r_} for r_ in b["by_mag"]]
    head = ("SPHEREx / catalogue flux, " + "; ".join(parts) + f" ({sp['zone']}, 10-13 mag)") if sp else "Spectra not measured yet"
    speed_note = ""
    if speed_measured:
        rnd = d.get("random", [])
        n_ok = sum(1 for x in rnd if x.get("ok"))
        n_pts = len({(x.get("ra"), x.get("dec")) for x in rnd})
        n_slow = sum(1 for x in rnd if x.get("ok") and (x.get("first_frame_s") or 0) > 15)
        byc = d.get("median_first_frame_s_by_clock") or {}
        ok_t = sorted(x["first_frame_s"] for x in rnd if x.get("ok") and x.get("first_frame_s") is not None)
        if med is not None:
            head += f"; median time to first frame {med:.1f} s over {len(rnd)} live requests ({n_ok} ok)"
        else:
            head += (f"; live speed: only {n_ok} of {len(rnd)} requests answered"
                     + (f" ({ok_t[0]:.1f}-{ok_t[-1]:.1f} s to first frame)" if ok_t else "")
                     + f", the rest {_failure_causes(rnd)}")
        if d.get("generated"):
            head += f" ({d['generated'][:16].replace('T', ' ')} UTC)"
        rows += [{"part": "speed", "ra": x.get("ra"), "dec": x.get("dec"), "clock": x.get("clock"), "ok": x.get("ok"),
                  "first_frame_s": x.get("first_frame_s")} for x in rnd]
        speed_note = (f" Speed measured {d.get('generated')} against {d.get('api_base')}/api: {n_pts} random coordinates "
                      f"(uniform on the sphere, seed {d.get('seed')}), each on both clocks, timed from the /api/frames request to "
                      f"the first frame received; a failed request counts as slower than any success. "
                      + "".join(f"Median {c} {v:.1f} s. " for c, v in byc.items() if v is not None)
                      + (f"{n_slow} ok request(s) took over 15 s. " if n_slow else "")
                      + (f"{len(rnd) - n_ok} request(s) failed ({_failure_causes(rnd)}). " if n_ok < len(rnd) else "")
                      + "The API was being sped up while this ran, so the number can change.")
        st_t = sorted(t["first_frame_s"] for t in d.get("targets", []) if t.get("ok") and t.get("first_frame_s") is not None)
        if st_t:
            speed_note += (f" For comparison, the {len(st_t)} featured targets, served as static files, showed their first "
                           f"frame in {st_t[0]:.2f}-{st_t[-1]:.2f} s.")
    else:
        head += "; speed not measured yet (needs scripts/demo_check.py on the live site)"
    st = v8_status(sp["pass"] if sp else None, med, speed_measured)
    value = sp["bands"]["Ks"]["clean"]["median"] if sp else None
    note = ("Spectra: every pilot star matched one-to-one to Gaia, 2MASS and CatWISE, not saturated or variable; SPHEREx "
            "channels averaged over Ks 1.99-2.31 um and W1 2.80-3.80 um vs the catalogue magnitudes (zero points 666.7 and "
            "309.54 Jy), no colour terms (scripts/validate/v8_spectra.py); fainter than 13 mag the scatter grows to 6-12%."
            + speed_note
            + " Rows: part=spectra (band, mag_min, mag_max, n, median, scatter) or part=speed "
            "(ra, dec, clock, ok, first_frame_s).")
    return check("V8", claim, st, head, value=value, unit="ratio", expected=exp, note=note, rows=rows)


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


SUSPECT_KEYS = {"id", "ra", "dec", "mjd", "clock", "det", "evidence", "n_dots", "snr", "flux_ujy", "cell",
                "nearest_known_arcsec"}


def valid_suspects(d: dict) -> bool:
    """The pipeline's suspects.json has the contracts/api.md shape and is not a synthetic example."""
    return (isinstance(d.get("suspects"), list) and not d.get("synthetic")
            and all(SUSPECT_KEYS <= set(s) and s.get("evidence") in ("two_exposures", "two_detectors")
                    for s in d["suspects"]))


def fetch_results(zone: str, dest: Path) -> None:
    gcloud = shutil.which("gcloud")
    if not gcloud:
        raise SystemExit("gcloud not on PATH: copy the reports by hand into " + str(dest / zone))
    (dest / zone).mkdir(parents=True, exist_ok=True)
    for name in ("recall.json", "astrometry_report.json", "photoz_report.json", "suspects.json"):
        subprocess.run([gcloud, "storage", "cp", f"{BUCKET}/results/{zone}/{name}", str(dest / zone / name)], check=False)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site-data", type=Path, default=SITE_DATA, help="folder with <slug>/object.json (default web/public/data)")
    ap.add_argument("--results", type=Path, default=CACHE / "results", help="folder with <zone>/recall.json etc.")
    ap.add_argument("--zone", default="pilot", help="pipeline zone for V5, V9, V10 (pilot or hunt)")
    ap.add_argument("--fetch-results", metavar="ZONE", help="gcloud storage cp the zone's reports into --results first")
    ap.add_argument("--demo-check", type=Path, help="JSON written by scripts/demo_check.py --json (V8)")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--suspects-out", type=Path, default=SITE_DATA / "suspects.json",
                    help="copy the zone's real suspects.json here when the pipeline wrote one (default web/public/data)")
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
    sus = load(args.results / args.zone / "suspects.json")
    if sus and valid_suspects(sus):
        write_json(args.suspects_out, sus)
        print(f"-> {args.suspects_out} ({len(sus['suspects'])} suspects, zone {sus.get('zone')})")
    elif sus:
        print(f"suspects.json for {args.zone} does not match contracts/api.md; not copied")


if __name__ == "__main__":
    main()
