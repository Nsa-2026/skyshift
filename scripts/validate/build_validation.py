"""Collect V1-V11 into web/public/data/validation.json (contracts/api.md, "validation.json").

Every check is printed even when its number is poor; a check with no input yet is "pending".

  V1  data/v1_targets.json                      (v1_targets.py)
  V2  <site-data>/<slug>/object.json `measure`   (scripts/precompute, PR #13) + data/targets.json
  V3  the same `measure`: sigma_arcsec from the reference stars
  V4  data/v4_asteroids.json                    (v4_asteroids.py)
  V5  <results>/<zone>/recall.json + summary.json (pipeline score; gs://nasa-glazing-data/results/)
  V6  cut (S2)          V7  cut (S3)
  V8  data/v8_spectra_<zone>.json (v8_spectra.py: the zone's stars vs 2MASS Ks and CatWISE W1)
      + data/demo_check.json (scripts/demo_check.py --json; --demo-check PATH overrides): median time to first frame
  V9  <results>/<zone>/astrometry_report.json   (pipeline astrometry)
  V10 <results>/<zone>/photoz_report.json       (pipeline photoz)
  V11 scripts/science/data/asteroid_sizes.json  (scripts/science asteroids.py)
  suspects.json: <results>/<zone>/suspects.json of every zone, merged (merge_suspects)
  distant.json: the distant-planet search (distant.py): <results>/<zone>/distant.json, else data/distant_<zone>.json,
      + data/distant_injection_<zone>.json; the first zone with a run

Zones (--zones, default hunt,pilot): V5, V8 spectra, V9, V10 and suspects.json take their headline from the first
zone that has the input and keep the others in the rows (field `zone`) and the note. Without the hunt's reports the
pilot becomes the headline by itself.

  uv run python build_validation.py --site-data ../../web/public/data --fetch-results   # copy both zones' reports first
  uv run python build_validation.py --zones pilot                                       # one zone only
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
from pathlib import Path

import distant
from _common import BUCKET, CACHE, DATA, today, write_json

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
SITE_DATA = REPO / "web" / "public" / "data"
OUT = SITE_DATA / "validation.json"
SIZES = REPO / "scripts" / "science" / "data" / "asteroid_sizes.json"
DEMO_CHECK = DATA / "demo_check.json"  # scripts/demo_check.py --random 10 --json <this> (V8 speed)
TARGET_SLUGS = ("barnard", "epsindib", "wise0855", "rwaur", "rdra")
DATA_RELEASE = "SPHEREx DR1 + QR3 (IRSA, as of 14 Nov 2026)"
ZONES = ("hunt", "pilot")  # headline first: the hunt is the bigger sample; the pilot holds gate G2
G2_ZONE = "pilot"  # gate G2 was decided on the pilot before the hunt ran
REPORTS = ("recall.json", "astrometry_report.json", "photoz_report.json", "suspects.json", "summary.json",
           "distant.json")


def load(path: Path | None) -> dict | None:
    if path is None or not path.exists():
        return None
    return json.loads(path.read_text())


def as_zones(zones: str | list[str] | tuple[str, ...]) -> list[str]:
    """'hunt,pilot' or ['hunt', 'pilot'] -> ['hunt', 'pilot']."""
    if isinstance(zones, str):
        zones = zones.split(",")
    return [z.strip() for z in zones if z.strip()]


def zone_inputs(res: Path | None, zones, name: str) -> list[tuple[str, dict]]:
    """[(zone, <res>/<zone>/<name>)] in the order of `zones`, for the zones that have the file."""
    out = []
    for z in as_zones(zones):
        d = load(res / z / name) if res else None
        if d:
            out.append((z, d))
    return out


def inside(res: Path | None, zone: str, other: str) -> bool:
    """True when `zone`'s box (summary.json) lies inside `other`'s: then `other`'s numbers include `zone`'s sky."""
    a = ((load(res / zone / "summary.json") if res else None) or {}).get("zone") or {}
    b = ((load(res / other / "summary.json") if res else None) or {}).get("zone") or {}
    if zone == other or not all(x.get(k) for x in (a, b) for k in ("ra", "dec")):
        return False
    return all(b[k][0] <= a[k][0] and a[k][1] <= b[k][1] for k in ("ra", "dec"))


def zone_text(res: Path | None, zone: str, zones=(), cap: bool = True) -> str:
    """'Pilot zone (RA 120.5-124.1, Dec +14.4 to +17.9, 990 photos; inside the hunt zone, so the hunt's numbers
    include it)' from the zones' summary.json; just 'Pilot zone' without one."""
    s = load(res / zone / "summary.json") if res else None
    box, photos = (s or {}).get("zone") or {}, (((s or {}).get("steps") or {}).get("photolist") or {}).get("photos")
    parts = []
    if box.get("ra") and box.get("dec"):
        parts.append(f"RA {box['ra'][0]:g}-{box['ra'][1]:g}, Dec {box['dec'][0]:+g} to {box['dec'][1]:+g}")
    if photos:
        parts.append(f"{photos:,} photos")
    txt = ", ".join(parts)
    host = next((o for o in as_zones(zones) if inside(res, zone, o)), None)
    if host:
        txt += ("; " if txt else "") + f"inside the {host} zone, so the {host}'s numbers include it"
    return f"{zone.capitalize() if cap else zone} zone" + (f" ({txt})" if txt else "")


def missing_text(zones, reported: list[str]) -> str:
    """' No hunt zone report yet.' for the zones asked for that have no input."""
    miss = [z for z in as_zones(zones) if z not in reported]
    return f" No {', '.join(miss)} zone report yet." if miss else ""


def demo_check_path(arg: Path | None) -> Path | None:
    """V8 speed input: an explicit --demo-check wins (and must exist); else data/demo_check.json if present."""
    if arg is not None:
        if not arg.exists():
            raise SystemExit(f"--demo-check {arg}: no such file")
        return arg
    return DEMO_CHECK if DEMO_CHECK.exists() else None


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


def v10_status(sigma_nmad: float | None) -> str:
    """Bright-galaxy sigma_NMAD: pass <= 0.05, partial <= 0.1."""
    if sigma_nmad is None:
        return "fail"
    return "pass" if sigma_nmad <= 0.05 else ("partial" if sigma_nmad <= 0.1 else "fail")


def worst(statuses: list[str]) -> str:
    """The poorest status of several zones (pending ones ignored): nothing is rounded up to a pass."""
    order = ("fail", "partial", "pass")
    seen = [s for s in statuses if s in order]
    return min(seen, key=order.index) if seen else "pending"


def dev_sigma(shift: float | None, predicted: float | None, sigma: float | None) -> float | None:
    """|measured - predicted| shift in units of the shift's error bar."""
    if shift is None or predicted is None or not sigma:
        return None
    return abs(shift - predicted) / sigma


# --- V1 ---------------------------------------------------------------------------------------

# Why a target shows up in detectors where it is not expected (a fact about the sky, not a result).
V1_UNEXPECTED_WHY = {"wise0855": "a background star lies 1-2 px from it"}


def _dets(dets: list[int]) -> str:
    """[1, 2, 3, 4] -> 'D1-D4'; [4, 6] -> 'D4, D6'."""
    dets = sorted(dets)
    if len(dets) > 1 and dets == list(range(dets[0], dets[-1] + 1)):
        return f"D{dets[0]}-D{dets[-1]}"
    return ", ".join(f"D{x}" for x in dets)


def _target_names() -> dict[str, str]:
    t = load(DATA / "targets.json") or {}
    return {x["slug"]: str(x.get("name") or x["slug"]).replace("−", "-") for x in t.get("targets", [])}


def v1_stack_text(d: dict) -> str:
    """Per target with a missed stack or a detection outside its expected detectors, computed from the per-stack
    rows of v1_targets.json: ' Name (expected in D5-D6): found in 3 of 6 stacks, 2 of 3 visits; missed in visit 1 D5
    (8 photos), ...; also found in D1-D4, not counted (why).' Targets found in every expected stack get no sentence."""
    names = _target_names()
    out = []
    for t in d["targets"]:
        stacks = [s for s in d.get("rows", []) if s.get("slug") == t["slug"]]
        expected = [s for s in stacks if s.get("expected")]
        miss = sorted((s for s in expected if not s.get("recovered")), key=lambda s: (s["visit"], s["detector"]))
        extra = sorted({s["detector"] for s in stacks if not s.get("expected") and s.get("recovered")})
        if not miss and not extra:
            continue
        txt = (f" {names.get(t['slug'], t['slug'])} (expected in {_dets(list({s['detector'] for s in expected}))}): "
               f"found in {len(expected) - len(miss)} of {len(expected)} stacks, {t['visits_recovered']} of {t['visits']} visits")
        if miss:
            txt += "; missed in " + ", ".join(f"visit {s['visit']} D{s['detector']} ({s['photos']} photos)" for s in miss)
        if extra:
            why = V1_UNEXPECTED_WHY.get(t["slug"])
            txt += f"; also found in {_dets(extra)}, not counted" + (f" ({why})" if why else "")
        out.append(txt + ".")
    return "".join(out)

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
            "visit's mid-time; published astrometry for WISE 0855) in a median stack of up to 5 photos (every photo for "
            "WISE 0855) of one visit and one expected detector. Saturated cores: Gaussian fit to the wings, or the centre of "
            "the masked core (scripts/validate/v1_targets.py, 41 px boxes read from NASA's S3 copy)."
            + v1_stack_text(d)
            + " Rows: slug, recovered, visits, visits_recovered, stacks_expected, stacks_recovered, detectors, "
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


# --- V5, V9, V10 (pipeline reports, one or more zones) ------------------------------------------

KNOWN_SOURCE = "mpc"  # V5 publishes only recall reports scored with MPC known objects


def known_source(rec: dict, summary: dict | None) -> tuple[str | None, str | None]:
    """(source, orbit snapshot) behind a recall.json: its own `known_source` if the pipeline wrote one, else the known
    step of the same run's summary.json (the run whose score time matches). (None, None) when neither says."""
    if rec.get("known_source"):
        return rec["known_source"], rec.get("known_orbits")
    st = (summary or {}).get("steps") or {}
    known = st.get("known") or {}
    if (st.get("score") or {}).get("generated") == rec.get("generated") and known.get("source"):
        return known["source"], known.get("orbits")
    return None, None


def tracked(d: dict) -> bool:
    """True when a recall.json counts tracklets' recovered dots in `recall` (it then has `recall_movers`, movers alone)."""
    return d.get("recall_movers") is not None


def recall_rows(d: dict) -> list[dict]:
    return [{"v_min": b.get("v_min"), "v_max": b.get("v_max"), "n": b.get("n"), "found": b.get("found"),
             "recall": r(b.get("recall"), 3), "control_recall": r(b.get("control_recall"), 3)} for b in d.get("by_v", [])]


def bright_unsaturated(rows: list[dict], v_max: float) -> tuple[int, int]:
    """(found, n) summed over the rows from V 12 to v_max: the unsaturated part of the gate sample."""
    sel = [x for x in rows if x["v_min"] is not None and x["v_min"] >= 12 and x["v_max"] is not None and x["v_max"] <= v_max]
    return sum(x["found"] or 0 for x in sel), sum(x["n"] or 0 for x in sel)


def _pct(x) -> str:
    return f"{100 * (x or 0):.0f}%"


def v5(res: Path | None, zones=ZONES) -> dict:
    claim, exp = "Asteroids re-found, blind (S1)", "Recall at V < 16 >= 50% (gate G2); control near 0"
    reps, left_out = [], []
    for z, d in zone_inputs(res, zones, "recall.json"):
        src, orbits = known_source(d, load(res / z / "summary.json"))
        if src != KNOWN_SOURCE:
            print(f"V5: {z} recall.json ({d.get('generated')}) was scored with known objects from {src or 'an unrecorded source'}, "
                  f"not {KNOWN_SOURCE}: left out (rescore the zone with MPC)")
            left_out.append(z)
            continue
        reps.append((z, d, orbits))
    skipped = "".join(f" The {z} zone's report is left out: its known objects are not recorded as MPC's." for z in left_out)
    if not reps:
        return check("V5", claim, "pending", "Numbers after the pilot", expected=exp, note=skipped.strip() or None)
    zs = [z for z, _, _ in reps]
    rows, heads, zone_notes, statuses = [], [], [], []
    for i, (z, d, orbits) in enumerate(reps):
        zr = recall_rows(d)
        rows += [{"zone": z, **x} for x in zr]
        rec, ctl, vmax = d.get("recall"), d.get("control_recall"), d.get("v_max") or 16
        statuses.append(v5_status(rec, ctl, d.get("g2_threshold") or 0.5))
        po = d.get("per_object") or {}
        objs = f"{po.get('found')} of {po.get('n')} objects" if po.get("n") else ""
        label = z.capitalize() + " zone" + (" (gate G2)" if z == G2_ZONE and len(reps) > 1 else "")
        if i == 0:
            # since tracklets (#150) recall.json `recall` also counts dots recovered on a mover track (`recall_movers`:
            # movers alone); an older report has no recall_movers and its recall is movers alone
            how = "found (as movers or recovered on a track)" if tracked(d) else "found as movers"
            heads.append(f"{label}: {d.get('n_found')} of {d.get('n_known')} known asteroid sessions at V < {vmax:g} {how} "
                         f"({_pct(rec)}); control {_pct(ctl)}" + (f"; {objs}" if objs else ""))
        else:
            heads.append(f"{label}: {d.get('n_found')} of {d.get('n_known')} ({_pct(rec)}), control {_pct(ctl)}"
                         + (f", {objs}" if objs else ""))
        f12, n12 = bright_unsaturated(zr, vmax)
        alone = (f"; movers alone: {d.get('n_found_movers')} of {d.get('n_known')} ({_pct(d.get('recall_movers'))})"
                 if tracked(d) else "")
        zone_notes.append(f" {zone_text(res, z, zs)}: {d.get('photos_with_predictions'):,} photos with predictions, pipeline "
                          f"report of {d.get('generated')}"
                          + (f", MPC orbit snapshot {orbits}" if orbits else "")
                          + (f"; V 12-{vmax:g} alone: {f12} of {n12}" if n12 else "") + alone + ".")
    d0 = reps[0][1]
    found_by = ("a blind-search mover, or a dot recovered on a mover track's predicted path in that session, lies"
                if tracked(d0) else "a blind-search mover lies")
    note = (f"Unit: {d0.get('unit')}. Known objects: Minor Planet Center orbits, propagated to each photo's MJD-AVG as "
            f"seen from SPHEREx. One counts as found when {found_by} within {d0.get('match_arcsec'):g}\" of "
            f"its predicted position; control: every prediction moved {(d0.get('control_offset_arcsec') or 120) / 60:g}'. "
            f"Gate G2 is recall >= {100 * (d0.get('g2_threshold') or 0.5):.0f}% at V < {d0.get('v_max'):g}"
            + (f", decided on the {G2_ZONE} zone before the hunt ran" if len(reps) > 1 and G2_ZONE in zs else "")
            + ". Objects brighter than V 12 saturate but are still counted."
            + "".join(zone_notes) + skipped + missing_text(zones, zs + left_out)
            + " Rows: zone, v_min, v_max, n, found, recall, control_recall.")
    return check("V5", claim, worst(statuses), ". ".join(heads), value=r(d0.get("recall"), 3), unit="recall",
                 expected=exp, note=note, rows=rows)


def v9(res: Path | None, zones=ZONES) -> dict:
    claim, exp = "Astrometry matches Gaia", "Bias near zero; pull scatter near 1"
    reps = [(z, b) for z, d in zone_inputs(res, zones, "astrometry_report.json")
            if (b := d.get("vs_gaia_frame_corrected") or d.get("vs_gaia_raw")) and b.get("matched")]
    if not reps:
        return check("V9", claim, "pending", "Numbers after the pilot", expected=exp)
    zs = [z for z, _ in reps]
    rows, heads, statuses = [], [], []
    for i, (z, best) in enumerate(reps):
        pa, pd_ = best.get("pmra", {}), best.get("pmdec", {})
        pulls = [x.get("pull_scatter") for x in (pa, pd_) if x.get("pull_scatter") is not None]
        statuses.append("pass" if pulls and all(0.7 <= p <= 1.5 for p in pulls) else "partial")
        rows += [{"zone": z, "axis": k, **{kk: v.get(kk) for kk in ("n", "bias", "scatter", "pull_scatter")}}
                 for k, v in (("pmra", pa), ("pmdec", pd_))]
        bias = f"{r(pa.get('bias'), 0):g} (RA) and {r(pd_.get('bias'), 0):g} (Dec) mas/yr"
        scat = f"{r(pa.get('scatter'), 0):g} and {r(pd_.get('scatter'), 0):g} mas/yr"
        pull = ", ".join(f"{p:.2f}" for p in pulls)
        if i == 0:
            heads.append(f"{best.get('matched'):,} stars vs Gaia ({z} zone): proper-motion bias {bias}, scatter {scat}; "
                         f"pull scatter {pull} (1 = honest error bars)")
        else:
            heads.append(f"{z.capitalize()} zone: {best.get('matched'):,} stars, bias {bias}, scatter {scat}, pull scatter {pull}")
    return check("V9", claim, worst(statuses), ". ".join(heads), value=reps[0][1].get("pmra", {}).get("scatter"),
                 unit="mas/yr", expected=exp,
                 note="Proper motions fitted over all dots with SPHEREx's own position as the observer, against Gaia DR3. "
                      + "Zones: " + "; ".join(zone_text(res, z, zs, cap=False) for z in zs) + "." + missing_text(zones, zs)
                      + " Pass when both pull scatters are 0.7-1.5 in every zone. "
                        "Rows: zone, axis, n, bias, scatter, pull_scatter (mas/yr).",
                 rows=rows)


def v10(res: Path | None, zones=ZONES) -> dict:
    claim, exp = "Galaxy distances match SDSS", "sigma_NMAD ~0.02 for bright galaxies (AB < 17)"
    reps = [(z, s) for z, d in zone_inputs(res, zones, "photoz_report.json")
            if (s := d.get("sdss_dr17") or d) and (s.get("all") or {}).get("n")]  # the pipeline nests the SDSS comparison
    if not reps:
        return check("V10", claim, "pending", "Numbers after the pilot", expected=exp)
    zs = [z for z, _ in reps]
    keys = ("n", "sigma_nmad", "outlier_frac", "bias")
    rows, heads, statuses, values = [], [], [], []
    for i, (z, d) in enumerate(reps):
        bright = next((v for k, v in d.items() if k.startswith("k_ab_lt_")), None) or d["all"]
        s = bright.get("sigma_nmad")
        statuses.append(v10_status(s))
        values.append(r(s, 4))
        rows += [{"zone": z, "sample": k, **{kk: r(v.get(kk), 4) if kk != "n" else v.get("n") for kk in keys}}
                 for k, v in d.items() if isinstance(v, dict) and "sigma_nmad" in v]
        rows += [{"zone": z, "sample": f"K_AB {k}", **{kk: r(v.get(kk), 4) if kk != "n" else v.get("n") for kk in keys}}
                 for k, v in (d.get("by_k_ab") or {}).items()]
        txt = f"sigma_NMAD {r(s, 3)}, outliers {100 * (bright.get('outlier_frac') or 0):.0f}%"
        heads.append(f"{bright.get('n'):,} bright galaxies ({z} zone): {txt}" if i == 0
                     else f"{z.capitalize()} zone: {bright.get('n'):,}, {txt}")
    return check("V10", claim, worst(statuses), ". ".join(heads), value=values[0], unit="dz/(1+z)", expected=exp,
                 note="EAZY templates on 102-channel spectra vs SDSS DR17 spectroscopic redshifts; bright = K_AB < 17. "
                      + "Zones: " + "; ".join(zone_text(res, z, zs, cap=False) for z in zs) + "." + missing_text(zones, zs)
                      + " Pass <= 0.05, partial <= 0.1, in every zone. Rows: zone, sample, n, sigma_nmad, outlier_frac, bias.",
                 rows=rows)


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


def spectra_path(zone: str) -> Path:
    """V8 spectra of one zone, written by `v8_spectra.py --zones <zone>`."""
    return DATA / f"v8_spectra_{zone}.json"


def v8(demo: Path | None, zones=ZONES, res: Path | None = None) -> dict:
    claim, exp = "Spectra and speed", "Spectra within 20% of 2MASS Ks / WISE W1; first frame under 15 s"
    sps = [(z, sp) for z in as_zones(zones) if (sp := load(spectra_path(z)))]
    sp = sps[0][1] if sps else None  # the headline zone
    d = load(demo)
    med = (d or {}).get("median_first_frame_s")
    speed_measured = bool(d and d.get("n_random"))
    if not sp and not speed_measured:
        return check("V8", claim, "pending", "Not measured yet", unit="ratio", expected=exp)
    parts, rows, zone_notes = [], [], []
    for i, (z, s) in enumerate(sps):
        for name, b in s["bands"].items():
            rows += [{"part": "spectra", "zone": z, **r_} for r_ in b["by_mag"]]
        clean = [f"{name} {b['clean']['median']:.3f} (scatter {100 * b['clean']['scatter']:.1f}%, {b['clean']['n']:,} stars)"
                 for name, b in s["bands"].items()]
        if i == 0:
            parts = clean
        zone_notes.append(f" {zone_text(res, z, [x for x, _ in sps])}: {', '.join(clean)} of {s['sources']:,} sources.")
    others = "".join(f"; {z} zone " + ", ".join(f"{name} {b['clean']['median']:.3f}" for name, b in s["bands"].items())
                     for z, s in sps[1:])
    head = (("SPHEREx / catalogue flux, " + "; ".join(parts) + f" ({sps[0][0]} zone, 10-13 mag{others})") if sp
            else "Spectra not measured yet")
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
    # every zone's spectra must pass (nothing rounded up); the headline zone gives the value
    st = v8_status(all(s["pass"] for _, s in sps) if sps else None, med, speed_measured)
    value = sp["bands"]["Ks"]["clean"]["median"] if sp else None
    faint = sorted(100 * x["scatter"] for x in rows if x["part"] == "spectra" and x["zone"] == sps[0][0]
                   and x.get("mag_min", 0) >= 13 and x.get("scatter") is not None) if sps else []
    note = ("Spectra: every star of the zone matched one-to-one to Gaia, 2MASS and CatWISE, not saturated or variable; "
            "SPHEREx channels averaged over Ks 1.99-2.31 um and W1 2.80-3.80 um vs the catalogue magnitudes (zero points "
            "666.7 and 309.54 Jy), no colour terms (scripts/validate/v8_spectra.py)"
            + (f"; fainter than 13 mag the scatter grows to {faint[0]:.1f}-{faint[-1]:.1f}%." if faint else ".")
            + "".join(zone_notes) + (missing_text(zones, [z for z, _ in sps]) if sps else "")
            + speed_note
            + " Rows: part=spectra (zone, band, mag_min, mag_max, n, median, scatter) or part=speed "
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


def _sep_arcsec(a: dict, b: dict) -> float:
    """Small-angle separation of two suspects, arcsec (they are at most a few arcsec apart when they match)."""
    dra = ((a["ra"] - b["ra"] + 180.0) % 360.0 - 180.0) * math.cos(math.radians((a["dec"] + b["dec"]) / 2))
    return 3600.0 * math.hypot(dra, a["dec"] - b["dec"])


def merge_suspects(files: list[tuple[str, dict]], match_arcsec: float = 6.0, match_min: float = 2.0) -> dict | None:
    """The site's suspects.json from several zones' pipeline files, headline zone first.

    Every suspect gets `zone` (the run its entry comes from) and `zones` (every zone whose run found it). A suspect
    that an earlier zone already lists (same mover: within `match_arcsec` and `match_min` minutes; the pilot box lies
    inside the hunt zone) is not repeated: the earlier entry adds the zone to its `zones`. The top level keeps the
    contract's `zone` (the headline zone) and adds `zones`. Files that don't match the contract are skipped."""
    files = [(z, d) for z, d in files if valid_suspects(d)]
    if not files:
        return None
    out: list[dict] = []
    for z, d in files:
        for s in d["suspects"]:
            twin = next((o for o in out if o["zone"] != z and abs(o["mjd"] - s["mjd"]) * 1440 <= match_min
                         and _sep_arcsec(o, s) <= match_arcsec), None)
            if twin is not None:
                if z not in twin["zones"]:
                    twin["zones"].append(z)
                continue
            out.append({**s, "zone": z, "zones": [z]})
    z0, d0 = files[0]
    return {"generated": d0.get("generated"), "data_release": d0.get("data_release"), "zone": z0,
            "zones": [z for z, _ in files], "suspects": out}


def fetch_results(zone: str, dest: Path) -> None:
    gcloud = shutil.which("gcloud")
    if not gcloud:
        raise SystemExit("gcloud not on PATH: copy the reports by hand into " + str(dest / zone))
    (dest / zone).mkdir(parents=True, exist_ok=True)
    for name in REPORTS:
        subprocess.run([gcloud, "storage", "cp", f"{BUCKET}/results/{zone}/{name}", str(dest / zone / name)], check=False)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site-data", type=Path, default=SITE_DATA, help="folder with <slug>/object.json (default web/public/data)")
    ap.add_argument("--results", type=Path, default=CACHE / "results", help="folder with <zone>/recall.json etc.")
    ap.add_argument("--zones", default=",".join(ZONES),
                    help="pipeline zones for V5, V8 spectra, V9, V10 and suspects.json, headline first (default hunt,pilot)")
    ap.add_argument("--zone", help="one zone only (same as --zones ZONE)")
    ap.add_argument("--fetch-results", nargs="?", const="", metavar="ZONES",
                    help="gcloud storage cp the reports of ZONES (default: every --zones zone) into --results first")
    ap.add_argument("--demo-check", type=Path,
                    help="JSON written by scripts/demo_check.py --json (V8 speed; default data/demo_check.json if it exists)")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--suspects-out", type=Path, default=SITE_DATA / "suspects.json",
                    help="write the zones' merged suspects here when the pipeline wrote real ones (default web/public/data)")
    ap.add_argument("--distant-out", type=Path, default=SITE_DATA / "distant.json",
                    help="write the distant-planet search here when a zone has a run (default web/public/data)")
    args = ap.parse_args()

    zones = as_zones(args.zone or args.zones)
    if args.fetch_results is not None:
        for z in as_zones(args.fetch_results) or zones:
            fetch_results(z, args.results)
    c2, c3 = v2_v3(args.site_data)
    checks = [
        v1(), c2, c3, v4(), v5(args.results, zones),
        check("V6", "Fast stars (S2)", "pending", "Cut for the one-day build", expected="Slope near 1",
              note="S2 was cut on Saturday; the pipeline's high-PM search (V9 data) covers fast stars in the zone."),
        check("V7", "Brown dwarfs (S3)", "pending", "Cut for the one-day build", expected="A count", note="S3 was cut on Saturday."),
        v8(demo_check_path(args.demo_check), zones, args.results), v9(args.results, zones), v10(args.results, zones), v11(),
    ]
    write_json(args.out, {"generated": today(), "data_release": DATA_RELEASE, "checks": checks})
    for c in checks:
        print(f"{c['id']:4s} {c['status']:8s} {c['headline']}")
    print(f"-> {args.out}")
    files = zone_inputs(args.results, zones, "suspects.json")
    for z, d in files:
        if not valid_suspects(d):
            print(f"suspects.json for {z} does not match contracts/api.md; left out")
    sus = merge_suspects(files)
    if sus:
        write_json(args.suspects_out, sus)
        per = ", ".join(f"{z} {len(d['suspects'])}" for z, d in files if valid_suspects(d))
        print(f"-> {args.suspects_out} ({len(sus['suspects'])} suspects, headline zone {sus['zone']}; per zone: {per})")
    dist = distant.write_site_file(args.results, zones, args.distant_out, DATA_RELEASE)
    if dist:
        c, k = dist["counts"], dist["control"]
        print(f"-> {args.distant_out} ({dist['zone']}: {c['pairs_tested']} pairs tested, {c['pairs']} passing, "
              f"chance {k['expected_pairs']}, Poisson p {k['poisson_p']})")


if __name__ == "__main__":
    main()
