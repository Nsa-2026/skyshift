"""candidates.json `checks`, `status` and `known_name` (contracts/api.md v3.23): what could make a candidate something
else, measured, each with exactly what to look at. The rules and texts live here; the exporter (candidates.py) gathers
the inputs and calls them; tests/test_candidate_checks.py holds every rule and text.

A check is {code, level, text, look, values}. level `strong`: a measured sign it is not new or not real; `warn`: can't
be ruled out, look; `ok`: a measured sign for it. Nothing is hidden for a check: a strong check multiplies the score by
STRONG_FACTOR, a warn by WARN_FACTOR, ok by 1, except where the kind's own score already counts the same thing
(counted_elsewhere: a grade C mover's vetting gates, a mover's known-object term, a high_pm reason weight).

Reference numbers quoted in the texts (REF) were measured on 16 Nov on hunt, ecl04, ecl08 and ecl11: 4,646 movers on
tracklets tracks (real moving objects, nearly all known asteroids; the shuffled controls made 0-1 tracks) against the
480 suspects of the same zones (the list). Rates and directions come from each zone's own tracks, per visit.

Check producers outside the exporter (scripts/candidates/<name>_check.py) write side files
{source, made, candidates_file, candidates: {id: {checks, status, known_name}}}; merge_side_files folds them in:
a producer's check replaces the exporter's check with the same code (among producers the stronger level wins, then the
first source by name; a producer's `known_body` also replaces the exporter's `known_nearby`; the check of one of a
mover's own failed vetting gates stays when the producer's of that code is weaker), status known > flaw > open, a strong
check whose code ends in `_proven` makes it a flaw, and a known-vs-flaw conflict adds a warn check `status_conflict`.

values keys (contracts/api.md v3.24 and its follow-up in PR #369): known_nearby / known_body `rate_arcsec_h`, `pa_deg`
are ours (in-session), `body_rate_arcsec_h`, `body_pa_deg`, `v`, `name` the body's, `sep_arcsec` the nearest and
`max_sep_arcsec` the farthest separation; a 2MASS Ks magnitude is `ks_mag` (`tmass_ks_mag` with the catalogue prefix).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

LEVELS = ("strong", "warn", "ok")
STRONG_FACTOR = 0.3   # every strong clue below is seen on <= 1-7% of known asteroids but on 9-35% of the list
WARN_FACTOR = 0.95    # (likelihood ratio <= 0.05-0.2); 0.3 is milder on purpose: the reference is known asteroids and a
                      # new kind (a near-Earth asteroid, a comet: 306P sits in a 0.05% rate cell) may differ. Two strong
                      # clues (x 0.09) rank a mover with the grade C ones (x 0.1), as the 16 Nov audit proposed.
STATUSES = ("open", "known", "flaw")

# --- reference numbers (16 Nov, hunt + ecl04 + ecl08 + ecl11: 4,646 track movers vs 480 suspects) -----------------
REF = {
    "outlier_peak_any": 0.68,   # known asteroids with OUTLIER at the peak of at least one dot (list: 0.28)
    "outlier_peak_dot": 0.56,   # ... share of their dots with it at the peak (list: 0.23)
    "halo_any": 0.22,           # HALO in some dot's 3x3 (at a peak: 0.066)
    "halo_peak": 0.066,
    "bloom_any": 0.004,
    "crosstalk_any": 0.001,
    "ghost_any": 0.0,
    "single": {1: 0.026, 2: 0.12, 3: 0.067, 4: 0.0032, 5: 0.016, 6: 0.068},   # seen in that detector alone
    "single_list": {1: 0.042, 2: 0.065, 3: 0.15, 4: 0.35, 5: 0.088, 6: 0.16},
    "partner_also": {4: 0.81, 5: 0.58, 6: 0.44},  # artefacts.md: forced photometry in the partner finds the asteroid
    "two_exposures": 0.16,      # seen in only 2 exposures (list: 0.92)
    "two_exp_not_significant": 0.78,  # hunt: real asteroids seen in 2 exposures with motion under 3 sigma
    "flux_ratio_p99": 2.2,      # brightest / faintest dot in one detector: 98.4% of known asteroids below (list: 78%)
    "partner_seen": 0.69,       # both detectors of a pair at the same moment (list: 0.013)
}
FLUX_JUMP = 2.2               # flux_jump: warn above this (config MOVER_FLUX_RATIO_MAX comment: known 99% < 2.2)
FLUX_JUMP_GATE = 4.0          # the vetting's flux_ratio gate (config VET_FLUX_RATIO_MAX)
ODD_DPA_DEG = 45.0            # odd_direction: more than this from the visit's median direction (known: 0-1.5% of them)
MIN_REF_TRACKS = 30           # fewer track movers in a visit: use the zone's pooled visits
FAST_QUANTILE = 0.99          # fast: faster than this share of the visit's known asteroids
FAST_HELD_ARCSEC_H = 400.0    # fast_held: config VET_A_FAST_ARCSEC_H (suspects.track_grades keeps such a track's B at B)
PARTNER_EDGE_PX = 15.0        # the partner detector covers a dot this far inside the frame (pairs sit ~71″ = 12 px apart)
DETECTOR_PX = 2040
EDGE_PX = 6.0                 # config VET_EDGE_PX
BRIGHT_STAR_G = 10.0          # bright_star: a Gaia star brighter than this ...
BRIGHT_STAR_ARCSEC = 120.0    # ... within this (fragments' bright_* use the same 120″, G < 10 / K < 7 / W1 < 7)
ICE_GROUP_DEG = 1.0           # behind_cloud: other ice detections within this
FEW_CHANNELS = 51             # few_channels: fewer of the 102 channels measured than this (half)
SIMULTANEOUS = {1: 4, 2: 5, 3: 6, 4: 1, 5: 2, 6: 3}
FLAG_BITS = {"flag_outlier": (19,), "flag_crosstalk": (20,), "flag_ghost": (22, 23, 24), "flag_bloom": (26,),
             "flag_halo": (28,)}
FLAG_WORDS = {"flag_crosstalk": ("CROSSTALK", "an electronic echo of a bright star elsewhere on the detector"),
              "flag_ghost": ("GHOST", "an optical reflection of a bright star"),
              "flag_bloom": ("BLOOM", "charge spilling out of a saturated star"),
              "flag_halo": ("HALO", "the glow around a bright star")}
FLAG_REF = {"flag_crosstalk": "crosstalk_any", "flag_ghost": "ghost_any", "flag_bloom": "bloom_any", "flag_halo": "halo_any"}
# the vetting's gates (vet.py) as check codes; on a grade C mover the C factor (x 0.1) stands for them
GATE_CODES = {"not_vetted": "not_vetted", "hot_pixel": "hot_pixel", "bad_column": "bad_column", "edge": "edge",
              "masked_peak": "masked_peak", "sharp_outlier": "sharp", "very_sharp": "sharp", "flux_ratio": "flux_jump",
              "red_photo": "red_photo", "2mass": "catalogue_star", "allwise": "catalogue_star",
              # the suspects' strong warnings (16 Nov: formerly vetoes, now grade C reasons)
              "gaia": "catalogue_star", "catwise": "catalogue_star", "bloom": "flag_bloom", "halo": "flag_halo",
              "bright_star": "bright_star"}
CATALOG_REASONS = {"gaia": "Gaia DR3", "catwise": "CatWISE2020", "2mass": "2MASS", "allwise": "AllWISE"}
CATALOG_PREFIX = {"gaia": "Gaia DR3", "catwise": "CatWISE2020", "tmass": "2MASS", "allwise": "AllWISE"}  # warn_values
# checks a kind's own score already weighs (counted_elsewhere)
HIGHPM_COUNTED = {"impossible_motion": "implausible_pm", "few_dots": "few_dots", "gaia_disagrees": "gaia_pm_disagrees"}


def check(code: str, level: str, text: str, look: str = "", values: dict | None = None) -> dict:
    if level not in LEVELS:
        raise ValueError(f"check level {level!r}")
    vals = None
    if values:
        vals = {k: _clean(v) for k, v in values.items() if _clean(v) is not None}
        vals = vals or None
    return {"code": code, "level": level, "text": text, "look": look, "values": vals}


def _clean(v):
    if v is None:
        return None
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    if isinstance(v, (int, np.integer)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        return round(float(v), 4) if math.isfinite(float(v)) else None
    return v if isinstance(v, (str, list, dict)) else str(v)


def sort_checks(checks: list[dict]) -> list[dict]:
    """strong, warn, ok; in the order made within a level; one per code (the first)."""
    seen, out = set(), []
    for c in checks:
        if c["code"] not in seen:
            seen.add(c["code"])
            out.append(c)
    return sorted(out, key=lambda c: LEVELS.index(c["level"]) if c["level"] in LEVELS else len(LEVELS))


def counted_elsewhere(cand: dict, chk: dict) -> bool:
    """True where the candidate's own score formula already counts this check (no second factor)."""
    kind, code = cand.get("kind"), chk["code"]
    if kind == "mover":
        if code in ("known_nearby", "known_body"):  # the known-object term (0.5 at 30″ rising to 1 at 300″)
            return True
        # grade C's x 0.1 stands for its failed gates (in `reasons`): their checks add nothing more
        return cand.get("grade") == "C" and any(GATE_CODES.get(r) == code for r in cand.get("reasons") or [])
    if kind == "high_pm":
        return HIGHPM_COUNTED.get(code) in set(cand.get("reasons") or [])
    return False


def gate_codes(cand: dict) -> set[str]:
    """The check codes of a mover's own failed vetting gates (its `reasons`, GATE_CODES); empty for other kinds."""
    if cand.get("kind") != "mover":
        return set()
    return {GATE_CODES[r] for r in cand.get("reasons") or [] if r in GATE_CODES}


def factor(cand: dict, checks: list[dict] | None = None) -> float:
    """The product of the checks' score factors (strong STRONG_FACTOR, warn WARN_FACTOR, ok 1)."""
    f = 1.0
    for c in cand.get("checks") if checks is None else checks:
        if counted_elsewhere(cand, c):
            continue
        f *= STRONG_FACTOR if c["level"] == "strong" else WARN_FACTOR if c["level"] == "warn" else 1.0
    return f


# --- words -------------------------------------------------------------------------------------------------------
def pct(x: float) -> str:
    """A share as a percentage in plain words: 0.0032 -> '0.3%', 0.68 -> '68%'."""
    p = 100.0 * float(x)
    if p == 0:
        return "0%"
    if p < 1:
        return f"{p:.1f}%"
    return f"{p:.0f}%"


def one_in(x: float) -> str:
    return f"1 in {round(1.0 / x):,}" if x > 0 else "none"


def arcsec(x: float) -> str:
    return f"{x:.0f}″" if x >= 100 else f"{x:.1f}″" if x >= 10 else f"{x:.2f}″" if x < 1 else f"{x:.1f}″"


def dist(x: float) -> str:
    return f"{x / 60.0:.1f}′" if x >= 60 else f"{x:.0f}″"


def days(d: float) -> str:
    return f"{d * 24:.0f} hours" if d < 1.0 else f"{d:.1f} days"


def count(k: int, n: int, noun: str = "dot") -> str:
    return f"{k} of its {n} {noun}s" if n != 1 else f"its only {noun}"


def exposures_of(mjd) -> np.ndarray:
    """Exposure index of each time: times within 5 s are one exposure (D1-D6 share MJD-AVG; link uses the same)."""
    t = np.asarray(mjd, np.float64) * 86400.0
    if not t.size:
        return np.zeros(0, np.int64)
    order = np.argsort(t, kind="stable")
    grp = np.zeros(t.size, np.int64)
    grp[order] = np.concatenate([[0], np.cumsum(np.diff(t[order]) > 5.0)])
    return grp


# --- movers ------------------------------------------------------------------------------------------------------
def mover_checks(m: dict) -> list[dict]:
    """Every exporter check of one mover. m holds (missing keys = not known):
    dots: {det, x, y, mjd, flux, snr, size, fpk, obs (lists, one entry per dot), flags3 (3x3 flags per dot or None)},
    flags (the OR of the dots' 3x3 flags), evidence, n_exposures, det, grade, gates (failed vet gates),
    rate, rate_err, pa (its own dots' fit, else link's in-session motion with rate_err None; None without),
    session_rate, session_pa (the suspects row's in-session motion; known bodies are compared on it), no_motion,
    track (sessions, rate, rate/sigma) or None, on_track, fast_held (sessions, track rate) or None, nearest_known,
    known_name (the suspects' nearest_known_name), near_source, repeat (sep ″, days) or None, excess (det, visit,
    ratio) or None, visit, ref (zone_motion_ref) or None, control_tracks (the zone's shuffled-control tracks, None
    unknown), partner_photos (set of (obs_id, det) photographed, None unknown), bright (G, sep ″) or None,
    star_size (per dot, the detector's stellar size at its S/N) or None, warn (the suspects' warn_codes ->
    values) or None."""
    out: list[dict] = []
    d = m.get("dots") or {}
    det = np.asarray(d.get("det", []), np.int64)
    n = int(det.size)
    exp = exposures_of(d.get("mjd", [])) if n else np.zeros(0, np.int64)
    main = int(m.get("det") or (det[0] if n else 2))
    gates = list(m.get("gates") or [])
    for f in (_flag_checks, _detector_checks, _motion_checks, _flux_checks, _place_checks, _gate_checks):
        out += f(m, d, det, exp, n, main, gates)
    out = _suspects_warnings(m, out)
    return sort_checks(out)


def _flag_checks(m, d, det, exp, n, main, gates) -> list[dict]:
    out = []
    fpk = np.asarray(d.get("fpk", []), np.int64) & 0xFFFFFFFF
    f3 = d.get("flags3")
    f3 = np.asarray(f3, np.int64) & 0xFFFFFFFF if f3 is not None and len(f3) == n else None
    allf = int(m.get("flags") or 0) & 0xFFFFFFFF
    for code, bits in FLAG_BITS.items():
        mask = sum(1 << b for b in bits)
        peak = int(((fpk & mask) != 0).sum()) if fpk.size == n else 0
        k3 = int(((f3 & mask) != 0).sum()) if f3 is not None else None
        anyf = bool(allf & mask) or peak > 0 or bool(k3)
        if code == "flag_outlier":
            if peak > 0 and n:
                out.append(check(
                    code, "ok",
                    f"NASA's change check (OUTLIER) marks the peak of {count(peak, n)}, as it does for most moving "
                    f"objects: {pct(REF['outlier_peak_any'])} of known asteroids have it on a dot (the list: 28%).",
                    f"Blink D{main} between its exposures on the hours clock: the dot should step to a new place, "
                    "not blink on and off in one spot.",
                    {"dots_peak": peak, "dots": n, "known_share": REF["outlier_peak_any"]}))
            continue
        gated = code[5:] in gates      # the suspects' bloom / halo reason: the bit at every dot's peak
        if not anyf and not gated:
            continue
        name, what = FLAG_WORDS[code]
        every = (n > 0 and peak == n) or gated
        where = (f"around {count(k3, n)}" if k3 is not None and k3 > 0 else "around at least one of its dots")
        at_peak = f", at the peak of {peak}" if peak and not every else ""
        if every:
            where = (f"at the peak of every one of its {n} dots" if n > 1 else "at the peak of its only dot") if n \
                else "at the peak of every one of its dots"
        ref = REF[FLAG_REF[code]]
        text = (f"{name} ({what}) is set {where}{at_peak}; known asteroids carry it {pct(ref)} of the time"
                + (", but almost never at the peak" if code == "flag_halo" else "") + ".")
        look = (f"Blink D{main} on the hours clock and look for a bright star nearby: light from a star stays put "
                "next to it while a real mover crosses the field.")
        out.append(check(code, "strong" if every else "warn", text, look,
                         {"dots_flagged": k3, "dots_peak": peak, "dots": n, "known_share": ref}))
    return out


def _detector_checks(m, d, det, exp, n, main, gates) -> list[dict]:
    out = []
    if not n:
        return out
    dets = sorted(set(det.tolist()))
    pairs_seen = 0
    pair_name = ""
    for e in np.unique(exp):
        ds = set(det[exp == e].tolist())
        for a in (1, 2, 3):
            if a in ds and a + 3 in ds:
                pairs_seen += 1
                pair_name = f"D{a} and D{a + 3}"
                break
    n_exp = int(np.unique(exp).size)
    if pairs_seen:
        out.append(check(
            "partner_seen", "ok",
            f"{pair_name} both saw it at the same moment "
            + (f"in {pairs_seen} of its {n_exp} exposures" if n_exp > 1 else "in its one exposure")
            + ": real light, since a glitch hits one detector's electronics only "
            f"({pct(REF['partner_seen'])} of known asteroids show this; the list: 1%).",
            f"Switch the Viewer between {pair_name} at the same exposure: the dot should sit at the same place in "
            "both.",
            {"exposures_both": pairs_seen, "exposures": n_exp}))
        return out
    if len(dets) != 1:
        return out
    dd = dets[0]
    if dd in (1, 2):   # SWIR alone is the common pattern of faint real asteroids (D2 alone 12%): no check
        return out
    p = SIMULTANEOUS[dd]
    photos = m.get("partner_photos")
    obs = list(d.get("obs", []))
    x, y = np.asarray(d.get("x", []), np.float64), np.asarray(d.get("y", []), np.float64)
    covered = None
    if photos is not None and len(obs) == n:
        inside = (np.isfinite(x) & np.isfinite(y) & (x >= PARTNER_EDGE_PX) & (x <= DETECTOR_PX - 1 - PARTNER_EDGE_PX)
                  & (y >= PARTNER_EDGE_PX) & (y <= DETECTOR_PX - 1 - PARTNER_EDGE_PX)) if x.size == n else np.ones(n, bool)
        covered = len({int(e) for e, o, ok in zip(exp, obs, inside) if ok and (str(o), p) in photos})
    known = REF["single"][dd]
    listed = REF["single_list"][dd]
    if covered == 0:
        cov = f"D{p}, which looks at the same sky at the same moment, has no photo of the spot here, so this can't be told"
    elif covered is None:
        cov = f"D{p} sees the same sky at the same moment and has no dot there"
    else:
        cov = (f"D{p} photographed the same spot at the same moment{'s' if covered > 1 else ''} "
               f"({covered} exposure{'s' if covered != 1 else ''}) and has no dot there")
    if dd == 4:
        level = "strong" if covered != 0 else "warn"
        text = (f"Seen only in D4 ({n_exp} exposure{'s' if n_exp != 1 else ''}); {cov}. Known asteroids are seen in D4 "
                f"alone {one_in(known)} times ({pct(known)}), camera glitches often are ({pct(listed)} of the list): "
                "an asteroid bright at 2.4–3.8 µm is brighter still at 0.75–1.1 µm.")
        look = (f"Switch the Viewer between D4 and D1 at the same exposure: a real asteroid is usually there in D1 too "
                f"({pct(REF['partner_also'][4])} of the known ones seen in D4 alone show in D1 when measured).")
    else:
        level = "warn"
        why = ("real asteroids do this too when they glow with heat" if dd in (5, 6)
               else "faint real asteroids do this too")
        text = (f"Seen only in D{dd} ({n_exp} exposure{'s' if n_exp != 1 else ''}); {cov}. {why[0].upper()}{why[1:]} "
                f"({pct(known)} of known asteroids), and so do camera glitches ({pct(listed)} of the list).")
        look = (f"Switch the Viewer between D{dd} and D{p} at the same exposure: faint light at the same place in "
                f"D{p} supports a real object; nothing at all is still normal for "
                + ("a warm, nearby one." if dd in (5, 6) else "a faint one."))
    out.append(check("one_detector", level, text, look,
                     {"detector": dd, "partner": p, "exposures": n_exp, "partner_photos": covered,
                      "known_share": known, "list_share": listed}))
    return out


def zone_motion_ref(visit: np.ndarray, on_track: np.ndarray, rate: np.ndarray, pa: np.ndarray) -> dict:
    """The zone's known asteroids' motion per visit, from tracklets.parquet's rows on a track: {visit: {n, pa_med,
    rate_q (FAST_QUANTILE), rate_max, rates, dpa}}, plus "all" (every visit pooled, dpa against each visit's median)."""
    visit, on = np.asarray(visit, np.int64), np.asarray(on_track, bool)
    rate, pa = np.asarray(rate, np.float64), np.asarray(pa, np.float64)
    ok = on & np.isfinite(rate) & np.isfinite(pa)
    out: dict = {}
    pooled_r, pooled_d = [], []
    for v in np.unique(visit[ok]):
        s = ok & (visit == v)
        a = np.radians(pa[s])
        med = float(np.degrees(np.arctan2(np.median(np.sin(a)), np.median(np.cos(a)))) % 360.0)
        dpa = np.abs((pa[s] - med + 180.0) % 360.0 - 180.0)
        out[int(v)] = {"n": int(s.sum()), "pa_med": med, "rate_q": float(np.quantile(rate[s], FAST_QUANTILE)),
                       "rate_max": float(rate[s].max()), "rates": rate[s], "dpa": dpa}
        pooled_r.append(rate[s])
        pooled_d.append(dpa)
    if pooled_r:
        r, dp = np.concatenate(pooled_r), np.concatenate(pooled_d)
        out["all"] = {"n": int(r.size), "pa_med": None, "rate_q": float(np.quantile(r, FAST_QUANTILE)),
                      "rate_max": float(r.max()), "rates": r, "dpa": dp}
    return out


def _motion_checks(m, d, det, exp, n, main, gates) -> list[dict]:
    out = []
    ev = m.get("evidence")
    n_exp = int(np.unique(exp).size) if n else int(m.get("n_exposures") or 0)
    track = m.get("track")
    on_track = bool(m.get("on_track")) or track is not None
    mjd = np.asarray(d.get("mjd", []), np.float64)
    span = float((mjd.max() - mjd.min()) * 1440.0) if mjd.size else None
    if ev == "two_detectors" and n_exp <= 1:
        out.append(check(
            "two_photos", "warn",
            "Seen in one exposure only (two detectors at the same moment), so its motion is unknown: it could be a "
            "satellite glint or a faint source that doesn't move.",
            f"Blink the exposures just before and after in D{main} on the hours clock: a source that doesn't move "
            "stays there, a mover has gone or moved on.",
            {"exposures": 1}))
    elif n_exp == 2 and not on_track:
        out.append(check(
            "two_photos", "warn",
            f"Seen in only 2 exposures{f', {span:.1f} minutes apart' if span else ''}: two unrelated specks can line up "
            f"by chance, and two points always make a straight line ({pct(REF['two_exposures'])} of known asteroids "
            "are seen only twice in a session; 92% of the list).",
            f"Blink the two exposures in D{main} on the hours clock, then look at the next session: a real mover turns "
            "up again further along the same line.",
            {"exposures": 2, "minutes": span}))
    rate, err, pa = m.get("rate"), m.get("rate_err"), m.get("pa")
    if m.get("no_motion") and rate is not None and err:
        sig = rate / err if err > 0 else 0.0
        out.append(check(
            "may_not_move", "warn",
            f"Its dots moved about {rate:.0f} ± {err:.0f}″ an hour ({sig:.1f}σ), within their position errors, so it "
            f"may not move at all; real asteroids seen twice often look like this too "
            f"({pct(REF['two_exp_not_significant'])} of them in the hunt zone).",
            f"Blink D{main} between its exposures and compare with the stars next to it: does the dot shift against them?",
            {"rate_arcsec_h": rate, "rate_err_arcsec_h": err, "sigma": sig}))
    if track is not None:
        ns, trate, tsnr = track
        ctl = m.get("control_tracks")
        sig = f", {tsnr:.0f}σ" if tsnr is not None and math.isfinite(tsnr) else ""
        out.append(_seen_again(m.get("track_info") or {}, ns, trate, sig, ctl, main))
        if ctl is None:
            pass
        elif ctl == 0:
            out.append(check(
                "chance_tracks", "ok",
                f"Followed over {ns} sessions of its visit at {trate:.1f}″ an hour{sig}; the zone's shuffled control "
                "(the same search with sessions mixed up) made no track at all, so chance pairs don't line up like this "
                "here.",
                f"Step through the sessions of its visit in D{main} on the hours clock: the dot should keep moving "
                "along one smooth path.",
                {"sessions": ns, "rate_arcsec_h": trate, "control_tracks": 0}))
        else:
            out.append(check(
                "chance_tracks", "warn",
                f"Followed over {ns} sessions at {trate:.1f}″ an hour{sig}, but the zone's shuffled control (sessions "
                f"mixed up) also made {ctl} track{'s' if ctl != 1 else ''}, so chance tracks happen here.",
                f"Step through the sessions of its visit in D{main}: check every session's dot, not only the first.",
                {"sessions": ns, "rate_arcsec_h": trate, "control_tracks": ctl}))
    fh = m.get("fast_held")
    if fh is not None:
        ns, trate = fh
        out.append(check(
            "fast_held", "warn",
            f"Followed over {ns} sessions at {trate:.0f}″ an hour, faster than {FAST_HELD_ARCSEC_H:.0f}″ an hour, where "
            "tracks of 2 sessions also join up glitches of one detector; no dot found again along its path and no "
            "partner detector backs it, so it stays grade B instead of A.",
            f"Switch the Viewer between D{main} and D{SIMULTANEOUS.get(main, main)} at the same exposure, then step "
            "through its sessions: a real fast mover shows in both detectors and keeps to one line.",
            {"sessions": ns, "rate_arcsec_h": trate, "limit_arcsec_h": FAST_HELD_ARCSEC_H}))
    ref = m.get("ref") or {}
    if rate is None or m.get("no_motion"):
        return out
    v = m.get("visit")
    r = ref.get(v) if v in ref and ref[v]["n"] >= MIN_REF_TRACKS else ref.get("all")
    if not r or r["n"] < MIN_REF_TRACKS:
        return out
    faster = int((r["rates"] >= rate).sum())
    fast = rate > r["rate_q"]
    if fast:
        pm = f" ± {err:.0f}" if err else ""
        out.append(check(
            "fast", "warn",
            f"Moves {rate:.0f}{pm}″ an hour; {pct(FAST_QUANTILE)} of the {r['n']:,} known asteroids followed in "
            f"this visit move slower than {r['rate_q']:.0f}″ an hour (fastest {r['rate_max']:.0f}).",
            f"Blink D{main} on the hours clock: a real fast mover makes long, even steps along a straight line.",
            {"rate_arcsec_h": rate, "rate_err_arcsec_h": err, "known_quantile_arcsec_h": r["rate_q"],
             "known_n": r["n"], "known_faster": faster}))
    pa_med = ref[v]["pa_med"] if v in ref and ref[v]["n"] >= MIN_REF_TRACKS else None
    if pa is None or pa_med is None:
        return out
    dpa = float(abs((pa - pa_med + 180.0) % 360.0 - 180.0))
    if dpa <= ODD_DPA_DEG:
        return out
    rv = ref[v]
    off = rv["dpa"] >= dpa
    both = int((off & (rv["rates"] >= rate)).sum()) if fast else int(off.sum())
    strong = fast and both == 0 and not on_track
    out.append(check(
        "odd_direction", "strong" if strong else "warn",
        f"Moves towards PA {pa:.0f}°, {dpa:.0f}° away from the common direction of this visit's asteroids (PA "
        f"{pa_med:.0f}°, the median of {rv['n']:,} known ones); {'none' if both == 0 else both} of them move "
        + ("this fast and this far off." if fast else "this far off.")
        + (" A chance pair of glitches points anywhere." if strong else ""),
        f"Blink D{main} on the hours clock and follow the dot's steps: compare their direction with the other "
        "asteroids in the field.",
        {"pa_deg": pa, "dpa_deg": dpa, "visit_pa_deg": pa_med, "known_n": rv["n"], "known_like_it": both}))
    return out


def _seen_again(ti: dict, ns: int, trate: float, sig: str, ctl, main: int) -> dict:
    """`seen_again` (ok): the mover is on a tracklets track, i.e. found again in other sessions of its visit, the
    strongest sign for a real moving object; values: the track (track_info) and the zone's controls."""
    f = {k: _fin(v) for k, v in ti.items()}
    nm, nrec, span = f.get("n_movers"), f.get("n_recovered_sessions"), f.get("span_h")
    tpa, srate, spa = f.get("pa_deg"), f.get("session_rate_arcsec_h"), f.get("session_pa_deg")
    text = (f"Seen again: followed over {ns} sessions of its visit"
            + (f" ({int(nm)} movers" + (f", and dots found again along its path in {int(nrec)} more session"
                                         f"{'s' if int(nrec) != 1 else ''}" if nrec else "") + ")" if nm else "")
            + (f" over {span:.0f} hours" if span else "")
            + f" at {trate:.1f}″ an hour" + (f" towards PA {tpa:.0f}°" if tpa is not None else "") + sig
            + (f"; within its own session {srate:.0f}″ an hour" + (f" towards PA {spa:.0f}°" if spa is not None else "")
               if srate is not None else "") + ".")
    cparts = []
    if ctl is not None:
        cparts.append(f"the same search with the sessions shuffled made {'no' if ctl == 0 else ctl} "
                      f"track{'s' if ctl != 1 else ''}" + (f" (against {int(f['zone_tracks']):,} real ones)"
                                                          if f.get("zone_tracks") else ""))
    crec = f.get("control_recovered_dots")
    if crec is not None:
        cparts.append(f"tracks moved 2′ off found {int(crec)} chance dot{'s' if int(crec) != 1 else ''} along their "
                      "paths" + (f" (against {int(f['zone_recovered_dots']):,} on the real paths)"
                                 if f.get("zone_recovered_dots") else ""))
    if cparts:
        text += " In this zone " + " and ".join(cparts) + "."
    text += " A real object turns up again along one smooth path; chance specks rarely do."
    ren = {"rate_arcsec_h": "track_rate_arcsec_h", "pa_deg": "track_pa_deg", "n_sessions": "sessions"}
    vals = {"sessions": ns, "track_rate_arcsec_h": trate, "control_tracks": ctl,
            **{ren.get(k, k): v for k, v in ti.items() if v is not None}}
    return check("seen_again", "ok", text,
                 f"Step through the sessions of its visit in D{main} on the hours clock: the dot should turn up "
                 "along one smooth path.", vals)


def _flux_checks(m, d, det, exp, n, main, gates) -> list[dict]:
    out = []
    flux = np.asarray(d.get("flux", []), np.float64)
    if flux.size != n or not n:
        return out
    worst, wd = 0.0, None
    for dd in np.unique(det):
        f = flux[det == dd]
        if f.size < 2:
            continue
        r = math.inf if np.nanmin(f) <= 0 else float(np.nanmax(f) / np.nanmin(f))
        if r > worst:
            worst, wd = r, int(dd)
    if wd is None or worst <= FLUX_JUMP:
        return out
    gate = "flux_ratio" in gates or worst > FLUX_JUMP_GATE
    rtxt = "to nothing" if not math.isfinite(worst) else f"{worst:.1f}×"
    out.append(check(
        "flux_jump", "strong" if gate else "warn",
        f"Its D{wd} brightness changes {rtxt} between exposures; known asteroids change less than {FLUX_JUMP}× "
        "(98% of them)" + (": a cosmic-ray hit paired with a fainter speck looks like this." if gate else "."),
        f"Blink D{wd} between its exposures: is one dot much brighter or sharper than the others?",
        {"ratio": worst if math.isfinite(worst) else None, "detector": wd, "known_max_ratio": FLUX_JUMP}))
    return out


def _place_checks(m, d, det, exp, n, main, gates) -> list[dict]:
    out = []
    nk = m.get("nearest_known")
    name = m.get("known_name") or None
    if nk is not None and math.isfinite(nk) and nk <= 120.0:
        out.append(check(
            "known_nearby", "warn",
            f"A known asteroid, comet, planet or moon{f', {name},' if name else ''} was predicted {dist(nk)} away at "
            "that time: it may be that object, a little off its predicted place.",
            "Blink the hours clock and compare its motion with the known object's predicted path.",
            {"sep_arcsec": nk, **({"name": name} if name else {})}))
    rep = m.get("repeat")
    if rep is not None:
        sep, dd = rep
        rate = sep / (dd * 24.0) if dd > 0 else None
        out.append(check(
            "repeat_position", "strong",
            f"Another unexplained mover was seen {dist(sep)} from here {days(dd)} apart"
            + (f" (an implied {rate:.2f}″ an hour)" if rate is not None else "")
            + ": probably a source that stays put (a star or galaxy), not a fast mover.",
            "Blink this spot on the months clock: something that is there in every visit is not an asteroid.",
            {"sep_arcsec": sep, "days": dd, "implied_rate_arcsec_h": rate}))
    exc = m.get("excess")
    if exc is not None:
        dd, v, ratio = exc
        out.append(check(
            "detector_excess", "warn",
            f"In visit {v} D{dd} has {ratio:.1f}× this zone's usual share of unexplained movers per mover, most of "
            "them glitches; this says nothing about this object itself.",
            f"Look at the same exposure in D{SIMULTANEOUS.get(dd, dd)}: glitches stay in one detector.",
            {"detector": dd, "visit": v, "ratio": ratio}))
    ns = m.get("near_source")
    if ns is not None and math.isfinite(ns) and not (set(CATALOG_REASONS) & set(gates)):
        out.append(check(
            "catalogue_star", "warn",
            f"A catalogued or fixed source lies {arcsec(ns)} away: the dot may be its wing or a blend with it.",
            "Blink this spot on the months clock: if a source sits there in other visits, the dot may be part of it.",
            {"sep_arcsec": ns}))
    br = m.get("bright")
    bw = (m.get("warn") or {}).get("bright_star") or {}
    if "bright_star" in gates:     # the suspects' strong form: a bright fixed source within 15″
        sep = bw.get("sep_arcsec")
        out.append(check(
            "bright_star", "strong",
            f"A bright fixed source (S/N ≥ 100) lies {dist(sep) if sep is not None else 'within 15″'} away: its glow, "
            "ghosts and spikes make dots; real asteroids have one this close 0.2–1.2% of the time, the unexplained "
            "movers 11–34%.",
            f"Blink D{main} on the hours clock and check whether the dot sits on the bright source's ring or spike.",
            bw or None))
    elif br is not None:
        g, sep = br
        out.append(check(
            "bright_star", "warn",
            f"A G {g:.1f} star lies {dist(sep)} away: bright stars make ghosts, halos and spikes that can look like dots.",
            f"Blink D{main} on the hours clock and check whether the dot sits on a spike or ring of that star.",
            {"g_mag": g, "sep_arcsec": sep}))
    x, y = np.asarray(d.get("x", []), np.float64), np.asarray(d.get("y", []), np.float64)
    if x.size == n and n:
        hot = _same_pixel(det, x, y, exp)
        if hot is not None:
            dd, k = hot
            out.append(check(
                "hot_pixel_proven", "strong",
                f"All {n} of its dots sit on the same D{dd} pixel (within 1 px) in {k} exposures although the sky "
                "moved under it: a hot pixel, not light from the sky.",
                f"Step through its exposures in D{dd}: the dot stays at one place on the detector while the stars shift.",
                {"detector": dd, "exposures": k}))
    return out


def _same_pixel(det, x, y, exp) -> tuple[int, int] | None:
    """(detector, exposures) when every dot is on one detector within 1 px in x and y over >= 2 exposures."""
    if np.unique(det).size != 1 or np.unique(exp).size < 2:
        return None
    ok = np.isfinite(x) & np.isfinite(y)
    if not ok.all():
        return None
    if np.ptp(x) <= 2.0 and np.ptp(y) <= 2.0 and np.all(np.abs(x - np.median(x)) <= 1.0) and \
            np.all(np.abs(y - np.median(y)) <= 1.0):
        return int(det[0]), int(np.unique(exp).size)
    return None


PEAK_BIT_WORDS = {0: "TRANSIENT (a cosmic ray)", 1: "OVERFLOW", 2: "SUR_ERROR", 6: "NONFUNC (a dead pixel)",
                  7: "DICHROIC", 9: "MISSING_DATA", 10: "HOT", 11: "COLD", 14: "PHANMISS", 17: "PERSIST (an "
                  "afterimage of a bright star)", 25: "STREAK (a satellite trail)", 27: "SNOWBALL"}


def _gate_checks(m, d, det, exp, n, main, gates) -> list[dict]:
    out = []
    g = set(gates)
    x, y = np.asarray(d.get("x", []), np.float64), np.asarray(d.get("y", []), np.float64)
    if "not_vetted" in g:
        out.append(check("not_vetted", "warn", "The vetting could not run on it: its table has no dot positions.",
                         f"Blink D{main} on the hours clock and judge the dots by eye.", None))
    if "hot_pixel" in g:
        out.append(check(
            "hot_pixel", "strong",
            "One of its dots sits on a detector pixel where the zone's other photos also have dots (free dots in 5 or "
            "more exposures, or movers in 3 or more): a hot pixel NASA's map misses.",
            f"Step through its exposures in D{main}: a hot pixel stays at one place on the detector while the stars "
            "shift.", None))
    if "bad_column" in g:
        out.append(check(
            "bad_column", "strong",
            "One of its dots sits on a detector column that holds 3 or more other dots in the same photo (a bad "
            "column).", f"Look along the column in D{main}: a bad column shows a line of specks.", None))
    if "edge" in g or (x.size == n and n):
        hi = DETECTOR_PX - 1 - EDGE_PX
        if x.size == n and n:
            dist_px = np.minimum.reduce([x, y, DETECTOR_PX - 1 - x, DETECTOR_PX - 1 - y])
            bad = np.isfinite(dist_px) & ((x < EDGE_PX) | (x > hi) | (y < EDGE_PX) | (y > hi))
        else:
            dist_px, bad = np.zeros(0), np.zeros(0, bool)
        if bad.any() or "edge" in g:
            px = float(np.nanmin(dist_px[bad])) if bad.any() else None
            k = int(bad.sum())
            dd = int(det[bad][0]) if bad.any() else main
            out.append(check(
                "edge", "strong",
                (f"{count(k, n).capitalize()} sit{'s' if k == 1 else ''} {max(px, 0):.1f} px from the D{dd} detector's "
                 "edge" if px is not None else "A dot sits at the detector's edge")
                + ", where stars cut off by the frame make fake dots.",
                f"In D{dd}, check whether the dot is cut by the frame's edge.",
                {"edge_px": px, "dots": k} if px is not None else None))
    if "masked_peak" in g:
        fpk = np.asarray(d.get("fpk", []), np.int64) & 0xFFFFFFFF
        bits = sorted({b for v in fpk for b in PEAK_BIT_WORDS if b not in (1, 15) and (int(v) >> b) & 1})
        words = ", ".join(PEAK_BIT_WORDS[b] for b in bits) or "a mask bit"
        out.append(check(
            "masked_peak", "strong",
            f"A dot has a pixel NASA masked at its peak ({words}): its light may not come from the sky.",
            f"Blink D{main} between its exposures: a masked speck shows in one exposure only.",
            {"bits": [PEAK_BIT_WORDS[b].split(" ")[0] for b in bits]} if bits else None))
    if g & {"sharp_outlier", "very_sharp"}:
        size = np.asarray(d.get("size", []), np.float64)
        star = np.asarray(m.get("star_size") if m.get("star_size") is not None else [], np.float64)
        if size.size == n and star.size == n and n and np.isfinite(size / star).any():
            ratio = size / star
            i = int(np.nanargmin(ratio))
            text = (f"Its sharpest dot (D{int(det[i])}, S/N {float(np.asarray(d['snr'])[i]):.0f}) is {size[i]:.1f}″ across, "
                    f"{ratio[i]:.2f}× a star's size at that S/N: sharper than the telescope can focus light from the "
                    "sky (a cosmic ray?).")
            vals = {"size_arcsec": size[i], "star_size_arcsec": star[i], "ratio": ratio[i]}
        else:
            text = "A dot is sharper than the telescope can focus light from the sky (a cosmic ray?)."
            vals = None
        out.append(check("sharp", "strong", text,
                         f"Zoom in on D{main} on the hours clock: a cosmic ray is a tight speck, a real object is as "
                         "soft as the stars.", vals))
    if "red_photo" in g:
        out.append(check("red_photo", "strong",
                         "Every one of its dots comes from a photo that failed our quality check (a bad pointing, the "
                         "South Atlantic radiation zone or a burst of glitches).",
                         f"Compare with the same spot in another session of D{main}.", None))
    cats = [c for c in CATALOG_REASONS if c in g]
    if cats:
        cw = {RENAME_VALUES.get(k, k): x for k, x in ((m.get("warn") or {}).get("catalogue_star") or {}).items()}
        names = [CATALOG_REASONS[c] for c in cats]
        sep, fm = _fin(cw.get("sep_arcsec")), _fin(cw.get("fainter_mag"))
        text = (f"{' and '.join(names)} list{'s' if len(names) == 1 else ''} a source "
                + (f"{sep:.1f}″ away" if sep is not None else "within 6″ of it")
                + (f", only {fm:.1f} mag fainter than the mover" if fm is not None else "")
                + ": probably that star's own light, not a mover"
                + (" (a catalogue source this close and this bright: real asteroids 0.2–2%, the unexplained movers "
                   "14–64%)." if fm is not None else "."))
        out.append(check("catalogue_star", "strong", text,
                         "Blink this spot on the months clock: a star there in other visits explains the dot.",
                         {"catalogs": names, **cw} if cw else {"catalogs": names, "within_arcsec": 6.0}))
    return out


def _fin(v) -> float | None:
    return float(v) if isinstance(v, (int, float, np.integer, np.floating)) and math.isfinite(float(v)) else None


SUSPECTS_TEXT_WINS = ("catalogue_star", "bright_star", "known_nearby")  # their numbers say more than the exporter's
RENAME_VALUES = {"tmass_k_mag": "tmass_ks_mag"}  # the suspects' warn_values keys under the contract's (2MASS Ks: ks_mag)
KNOWN_RATE_TOL = 0.5          # known_nearby "the motions agree": config SUSPECT_KNOWN_RATE_TOL ...
KNOWN_PA_TOL_DEG = 45.0       # ... and SUSPECT_KNOWN_PA_TOL_DEG (the suspects' moving-alike cut without its 3σ terms)


def _known_values(v: dict, m: dict) -> dict:
    """The suspects' known_nearby warn_values under the v3.24 follow-up's keys (the `known` producer's too): name (the
    suspects' nearest_known_name), v, sep_arcsec, body_rate_arcsec_h, body_pa_deg (the body's), rate_arcsec_h, pa_deg
    (ours), rate_ratio (ours / the body's), dpa_deg. Ours is the in-session motion, never a track's chord: the suspects'
    own (their values, null when not measured), else the row's session_rate_arcsec_h / session_pa_deg (m session_rate,
    session_pa), else the exporter's fit of its dots (m rate, pa)."""
    if "rate_arcsec_h" in v or "pa_deg" in v:
        rate, pa = _fin(v.get("rate_arcsec_h")), _fin(v.get("pa_deg"))
    elif _fin(m.get("session_rate")) is not None:
        rate, pa = _fin(m.get("session_rate")), _fin(m.get("session_pa"))
    else:
        rate, pa = _fin(m.get("rate")), _fin(m.get("pa"))
    kr, kpa = _fin(v.get("known_rate_arcsec_h")), _fin(v.get("known_pa_deg"))
    ratio = rate / kr if rate is not None and kr else None
    dpa = float(abs((pa - kpa + 180.0) % 360.0 - 180.0)) if pa is not None and kpa is not None else None
    out = {"name": m.get("known_name") or None, "v": _fin(v.get("known_v_mag")), "sep_arcsec": _fin(v.get("sep_arcsec")),
           "body_rate_arcsec_h": kr, "body_pa_deg": kpa, "rate_arcsec_h": rate, "pa_deg": pa, "rate_ratio": ratio,
           "dpa_deg": dpa}
    return {k: x for k, x in out.items() if x is not None}


def _warn_text(code: str, v: dict, m: dict) -> tuple[str, str] | None:
    """Words for one of the suspects' warn codes (suspects.py, 16 Nov) from its warn_values; None when unknown."""
    main = int(m.get("det") or 2)
    f = {k: _fin(x) for k, x in v.items()}
    if code == "catalogue_star":
        names = [n for p, n in CATALOG_PREFIX.items() if f.get(f"{p}_sep_arcsec") is not None]
        parts = []
        if names:
            fm = f.get("fainter_mag")
            sep = f.get("sep_arcsec")
            parts.append(f"{' and '.join(names)} list{'s' if len(names) == 1 else ''} a source "
                         + (f"{sep:.1f}″ away" if sep is not None else "within 6″")
                         + (f", {fm:.1f} mag fainter than the mover" if fm is not None else "")
                         + ": real asteroids pass such fainter neighbours often, but the dot may also be that source")
        if f.get("source_mask_all_dots"):
            parts.append("NASA's SOURCE mask covers the peak of every one of its dots (its own source list puts "
                         "something here)")
        if not parts:
            return None
        text = "; ".join(parts) + "."
        return text[0].upper() + text[1:], ("Blink this spot on the months clock: a source that is there in other "
                                            "visits explains the dot.")
    if code == "bright_star":
        sep = f.get("sep_arcsec")
        if sep is None:
            return None
        mag, snr = f.get("star_ab_mag"), f.get("star_snr")
        what = ", ".join(x for x in (f"AB {mag:.1f}" if mag is not None else "",
                                     f"S/N {snr:,.0f}" if snr is not None else "") if x)
        return ((f"A bright fixed source{f' ({what})' if what else ''} lies {dist(sep)} away: its glow, ghosts and "
                 "spikes can make dots; real asteroids have one within 30″ 1–7% of the time, the unexplained movers "
                 "18–43%."),
                f"Blink D{main} on the hours clock and check whether the dot sits on the bright source's ring or spike.")
    if code == "known_nearby":   # v: already under the known producer's keys (_known_values)
        k = {"rate_ratio": None, "dpa_deg": None, **v}
        sep = _fin(k.get("sep_arcsec"))
        if sep is None:
            return None
        kr, kpa, kv = _fin(k.get("body_rate_arcsec_h")), _fin(k.get("body_pa_deg")), _fin(k.get("v"))
        rate, pa = _fin(k.get("rate_arcsec_h")), _fin(k.get("pa_deg"))
        name, at_v = k.get("name"), (f" at V {kv:.1f}" if kv is not None else "")
        who = f", {name}{at_v}," if name else at_v
        text = (f"A known body{who} was predicted {dist(sep)} away"
                + (f", moving {kr:.0f}″ an hour towards PA {kpa:.0f}°" if kr is not None and kpa is not None else ""))
        if rate is not None and pa is not None and kr is not None and kpa is not None:
            alike = k["rate_ratio"] is not None and abs(k["rate_ratio"] - 1.0) <= KNOWN_RATE_TOL \
                and k["dpa_deg"] <= KNOWN_PA_TOL_DEG
            text += (f"; this one moves {rate:.0f}″ an hour towards PA {pa:.0f}° within its session"
                     + (": the motions agree, so it is probably that body a little off its predicted place."
                        if alike else ": a different motion, so probably not that body."))
        else:
            text += ": its own motion could not be compared; if it moves alike, it is that body a little off its place."
        return text, "Blink the hours clock and compare its steps with the known body's predicted path."
    if code == "persistence":
        k, n = f.get("dots_at_peak"), f.get("n_dots")
        return ("PERSIST (an afterimage of a bright star seen earlier on the same pixels) is set at the peak of "
                + (f"{count(int(k), int(n))}" if k is not None and n else "a dot")
                + ": the dot may be left-over charge, not light from the sky.",
                f"Step back one exposure in D{main}: a bright star on these pixels just before explains an afterimage.")
    if code in FLAG_WORDS:
        name, what = FLAG_WORDS[code]
        k = f.get("dots_at_peak")
        return (f"{name} ({what}) is set around at least one of its dots"
                + (f", at the peak of {int(k)}" if k else "") + ".",
                f"Blink D{main} on the hours clock and look for a bright star nearby.")
    if code == "edge" and f.get("edge_px") is not None:
        return ((f"A dot sits {f['edge_px']:.1f} px from the detector's edge, where stars cut off by the frame make "
                 "fake dots."), f"In D{main}, check whether the dot is cut by the frame's edge.")
    if code == "sharp" and f.get("size_ratio") is not None:
        return ((f"Its sharpest dot is {f['size_ratio']:.2f}× a star's size at that S/N: sharper than the telescope "
                 "can focus light from the sky (a cosmic ray?)."),
                (f"Zoom in on D{main} on the hours clock: a cosmic ray is a tight speck, a real object is as soft as "
                 "the stars."))
    if code == "flux_jump" and f.get("ratio") is not None:
        return ((f"Its brightness changes {f['ratio']:.1f}× between exposures in one detector; known asteroids "
                 f"change less than {FLUX_JUMP}×."),
                f"Blink D{main} between its exposures: is one dot much brighter than the others?")
    return None


def _suspects_warnings(m: dict, out: list[dict]) -> list[dict]:
    """The suspects' warn_codes (16 Nov: formerly vetoes, now kept) as warn checks, numbers from warn_values. Where the
    exporter made the same code its level stays and the suspects' numbers join its values; for SUSPECTS_TEXT_WINS (the
    catalogue names and magnitudes, the bright source's S/N, the known body's motion) their words replace a warn's."""
    w = m.get("warn") or {}
    have = {c["code"]: c for c in out}
    main = int(m.get("det") or 2)
    for code, vals in w.items():
        vals = {k: _clean(x) for k, x in (vals if isinstance(vals, dict) else {}).items()}
        if code == "known_nearby":
            vals = _known_values(vals, m)
        vals = {RENAME_VALUES.get(k, k): x for k, x in vals.items()}
        words = _warn_text(code, vals, m)
        if code in have:
            c = have[code]
            c["values"] = {**(c["values"] or {}), **{k: x for k, x in vals.items() if x is not None}} or None
            if words and code in SUSPECTS_TEXT_WINS and c["level"] != "strong":
                c["text"], c["look"] = words
            continue
        if words is None:
            nums = ", ".join(f"{k.replace('_', ' ')} {x:g}" if isinstance(x, (int, float)) else f"{k.replace('_', ' ')} {x}"
                             for k, x in vals.items() if x is not None)
            words = (f"Kept with a warning from the suspects step ({code.replace('_', ' ')}{': ' + nums if nums else ''}).",
                     f"Blink D{main} on the hours clock and look at the dots by eye.")
        out.append(check(code, "warn", words[0], words[1], vals))
    return out


# --- high proper motion --------------------------------------------------------------------------------------------
def highpm_checks(h: dict) -> list[dict]:
    """h: reasons, pm (mas/yr), method, fit_mode, saturated, n_dots, blend (fragments: sep, dG) or None."""
    out = []
    rs = set(h.get("reasons") or [])
    det = int(h.get("det") or 2)
    pm = h.get("pm")
    if "implausible_pm" in rs and pm is not None:
        out.append(check(
            "impossible_motion", "strong",
            f"Moves {pm / 1000:.1f}″ a year; no star moves faster than 10.4″ a year (Barnard's Star): almost certainly "
            "two different sources, one in each visit.",
            f"Blink the visits on the months clock in D{det}: if one source fades out and another lights up nearby, "
            "they are two.", {"pm_arcsec_yr": pm / 1000.0, "fastest_star_arcsec_yr": 10.4}))
    if "gaia_pm_disagrees" in rs:
        out.append(check("gaia_disagrees", "strong",
                         "Gaia lists a star here with a different motion: likely a mismatch with that star.",
                         f"Blink the visits on the months clock in D{det} and compare with Gaia's arrow.", None))
    if h.get("method") == "fit" and str(h.get("fit_mode") or "") in ("pm", "pm_leak"):
        out.append(check(
            "parallax_leak", "warn",
            "Fitted without a parallax: with two visits half a year apart, a nearby star's yearly wobble can show as "
            "extra motion (about 4 mas a year per mas of parallax in the hunt zone).",
            f"Blink the visits on the months clock in D{det}: a real mover keeps going the same way in every visit.",
            {"fit_mode": str(h.get("fit_mode"))}))
    if "few_dots" in rs:
        nd = h.get("n_dots")
        out.append(check("few_dots", "warn",
                         f"Few detections ({nd} dots)" if nd else "Few detections (fewer than 20 dots)",
                         f"Blink the visits on the months clock in D{det}.", {"dots": nd}))
    if h.get("saturated"):
        out.append(check(
            "saturated", "warn",
            "Saturated (2 or more dots with a burnt-out core): its place is measured on the wings, which can shift it "
            "(before the 14 Nov fix such stars showed up to 110 mas a year of fake motion).",
            "Blink the visits on the months clock in D4–D6, where bright stars saturate less.", None))
    nc = h.get("n_common_det")
    if h.get("method") == "pair" and nc is not None and nc <= 1:
        out.append(check(
            "one_detector_shift", "warn",
            ("The two visits' sources share only one detector" if nc == 1 else "The two visits' sources share no detector")
            + ", so the match of their brightness rests on " + ("one detector's dots" if nc == 1 else "nothing")
            + ": two unrelated sources, or a glitch in that detector, can pass for one moving star.",
            "Blink the visits on the months clock in every detector: a real star shows in the same detectors in both.",
            {"common_detectors": nc}))
    b = h.get("blend")
    if b is not None:
        if h.get("gaia_match"):   # a Gaia star whose motion we confirm (hunt: all 16 blends): its size may be off
            out.append(_blend(b, "warn", det, "our measured motion may be pulled by the pair (Gaia's own motion is "
                                              "the one to trust)"))
        else:
            out.append(_blend(b, "strong", det, "a shift between visits may be the pair, not motion"))
    return sort_checks(out)


def _blend(b: tuple, level: str, det: int, what: str) -> dict:
    sep, dg, k = b
    txt = (f"{k if k and k > 2 else 'Two'} Gaia stars lie within 8″ of it"
           + (f" ({sep:.1f}″ apart, {dg:.1f} mag different)" if sep is not None and math.isfinite(sep) else "")
           + f": the 7.5″ image may merge them, so {what}.")
    return check("blend", level, txt[0].upper() + txt[1:],
                 f"Zoom in on D{det} on the months clock: two stars side by side look like one stretched blob.",
                 {"sep_arcsec": sep, "dg_mag": dg, "gaia_stars": k})


# --- spectral ------------------------------------------------------------------------------------------------------
def spectral_checks(s: dict) -> list[dict]:
    """s: labels, pah_sig, ice_bands (µm strings), ice_neighbours (other ice detections within ICE_GROUP_DEG; None
    unknown), n_channels, bright (band, mag, sep) or None, blend (sep, dG, n) or None, catalogs (searched names),
    catwise_match, det."""
    out = []
    labs = [x for x in (s.get("labels") or []) if x != "red"]
    det = int(s.get("det") or 2)
    if labs == ["pah_33"]:
        sig = s.get("pah_sig")
        out.append(check(
            "pah_only", "warn",
            "Only PAH emission at 3.3 µm" + (f" ({sig:.0f}σ)" if sig else "") + ", the glow of dust in star-forming "
            "galaxies: most likely a galaxy, not a cold star.",
            "Blink the months clock: a galaxy looks a little soft and never moves; the 3.3 µm bump is in D4.",
            {"sigma": sig}))
    ices = [x for x in labs if x.endswith("_ice")]
    if ices:
        k = s.get("ice_neighbours")
        words = " and ".join({"h2o_ice": "water ice", "co2_ice": "CO₂ ice", "co_ice": "CO ice"}[x] for x in ices)
        grp = (f"; {k} other ice detection{'s' if k != 1 else ''} lie within {ICE_GROUP_DEG:.0f}°, a cloud in front of "
               "many stars" if k else "")
        out.append(check(
            "behind_cloud", "warn",
            f"{words[0].upper()}{words[1:]} absorption: starlight passing through a cold dust cloud, not the star "
            f"itself{grp}.",
            "Look at the field on the Sky page: dark clouds show as patches with fewer stars.",
            {"ice_neighbours": k}))
    nc = s.get("n_channels")
    if nc is not None and nc < FEW_CHANNELS:
        out.append(check(
            "few_channels", "warn",
            f"Its spectrum has values in only {nc} of the 102 channels, so a band may rest on few points.",
            "Open its spectrum: check that the band is drawn by several points, not one or two.",
            {"channels": nc, "of": 102}))
    br = s.get("bright")
    if br is not None:
        out.append(_bright_neighbour(br, det, "its glow can add light to some channels"))
    b = s.get("blend")
    if b is not None:
        out.append(_blend(b, "warn", det, "the spectrum may mix the two"))
    cats = list(s.get("catalogs") or [])
    if cats:
        full = {"Gaia DR3", "CatWISE2020", "2MASS"} <= set(cats)
        cw = s.get("catwise_match")
        if full:
            text = ("Gaia DR3, CatWISE2020 and 2MASS were searched"
                    + ("; CatWISE2020 lists it." if cw else "; CatWISE2020 has no entry within 3″ of it."))
            out.append(check("catalogues_checked", "ok", text, "", {"catalogs": cats}))
        else:
            missing = [c for c in ("CatWISE2020", "2MASS") if c not in cats]
            out.append(check(
                "catalogues_checked", "warn",
                f"Only {', '.join(cats)} was searched here: this zone's reference catalogue lacks "
                f"{' and '.join(missing)}, so an infrared catalogue may still list it.",
                "Check IRSA's catalogues (CatWISE2020, AllWISE, 2MASS) at its position before calling it new.",
                {"catalogs": cats, "missing": missing}))
    return sort_checks(out)


def _bright_neighbour(br: tuple, det: int, what: str) -> dict:
    band, mag, sep = br
    return check("bright_neighbour", "warn",
                 f"A {band} {mag:.1f} star lies {dist(sep)} away: {what}.",
                 f"Blink D{det} on the months clock and look for a halo or spike from the bright star across it.",
                 {"band": band, "mag": mag, "sep_arcsec": sep})


# --- shape ---------------------------------------------------------------------------------------------------------
def shape_checks(s: dict) -> list[dict]:
    """s: reasons, galaxy (classify's class or the shape row's class_galaxy), bright (band, mag, sep) or None, blend
    (sep, dG, n) or None, companion_catalog (the fit's second object's catalogue entry, "" none), det."""
    out = []
    det = int(s.get("det") or 2)
    b = s.get("blend")
    if b is not None:
        out.append(_blend(b, "strong", det, "the stretch may be the two catalogued stars"))
    cc = str(s.get("companion_catalog") or "")
    if cc:
        out.append(check(
            "catalogue_star", "strong",
            f"The second object the two-source fit found is catalogued ({cc}): a known neighbour, not a new companion.",
            f"Zoom in on D{det} on the months clock at the companion's place.", {"catalogue": cc}))
    if s.get("galaxy"):
        out.append(check(
            "galaxy", "warn", "Our classifier calls it extended (a galaxy?): galaxies are stretched by nature.",
            f"Zoom in on D{det}: a galaxy looks soft at every wavelength, two stars look like two points.", None))
    br = s.get("bright")
    if br is not None:
        out.append(_bright_neighbour(br, det, "its glow can stretch the image"))
    return sort_checks(out)


# --- status and side files -----------------------------------------------------------------------------------------
def status_of(checks: list[dict]) -> str:
    """flaw when a strong check's code ends in `_proven`, else open (known comes only from a producer)."""
    return "flaw" if any(c["level"] == "strong" and c["code"].endswith("_proven") for c in checks) else "open"


def load_side_files(folder: Path) -> list[dict]:
    """Every <folder>/*.json side file, sorted by name; unreadable ones are skipped (with their error)."""
    out = []
    for p in sorted(Path(folder).glob("*.json")):
        try:
            d = json.loads(p.read_text())
        except (OSError, ValueError) as e:
            out.append({"source": p.stem, "error": str(e), "candidates": {}})
            continue
        if not isinstance(d, dict) or not isinstance(d.get("candidates"), dict):
            out.append({"source": p.stem, "error": "no candidates object", "candidates": {}})
            continue
        d.setdefault("source", p.stem)
        out.append(d)
    return out


def _valid_check(c) -> dict | None:
    if not isinstance(c, dict) or not c.get("code") or c.get("level") not in LEVELS:
        return None
    vals = c.get("values")
    return {"code": str(c["code"]), "level": c["level"], "text": str(c.get("text") or ""), "look": str(c.get("look") or ""),
            "values": vals if isinstance(vals, dict) and vals else None}


def merge_one(cand: dict, entries: list[tuple[str, dict]]) -> dict:
    """Fold the producers' entries [(source, {checks, status, known_name})] into one candidate (in place; returns it).
    Score: re-weighed by the change in the checks' factor."""
    old = list(cand.get("checks") or [])
    f_old = factor(cand, old)
    by_code: dict[str, dict] = {}
    for src, e in entries:
        for c in e.get("checks") or []:
            c = _valid_check(c)
            if c is None:
                continue
            prev = by_code.get(c["code"])
            if prev is None or LEVELS.index(c["level"]) < LEVELS.index(prev["level"]):
                by_code[c["code"]] = c
    if "known_body" in by_code and "known_nearby" not in by_code:
        old = [c for c in old if c["code"] != "known_nearby"]  # v3.24: a certain known body replaces our known_nearby
    gates = gate_codes(cand)
    checks = []
    for c in old:
        new = by_code.pop(c["code"], None)
        if new is None:
            checks.append(c)
        elif c["code"] in gates and LEVELS.index(c["level"]) < LEVELS.index(new["level"]):
            checks.append(c)        # a vetting gate's check outranks a weaker measurement of the same code
        else:
            checks.append(new)
    checks += list(by_code.values())
    known = [(src, str(e.get("known_name") or "")) for src, e in entries if e.get("status") == "known"]
    flaws = [src for src, e in entries if e.get("status") == "flaw"]
    proven = [c["code"] for c in checks if c["level"] == "strong" and c["code"].endswith("_proven")]
    status = cand.get("status") or "open"
    name = cand.get("known_name")
    if known:
        status, name = "known", known[0][1] or name
        names = sorted({n for _, n in known if n})
        if len(names) > 1:
            checks.append(check("status_conflict", "warn",
                                f"Two checks name different known bodies here: {', '.join(names)}.",
                                "Compare the motion with each body's predicted path in the Viewer.", {"names": names}))
        if flaws or proven or cand.get("status") == "flaw":
            what = ", ".join(sorted(set(flaws + proven))) or "the exporter"
            checks.append(check("status_conflict", "warn",
                                f"{known[0][0]} names it {name}, but {what} says it is an instrument flaw: look at both.",
                                "Check the named body's predicted place and motion in the Viewer, then the flaw's own "
                                "check.", {"known_name": name, "flaw": what}))
    elif flaws or proven:
        status = "flaw"
    cand["status"], cand["known_name"] = status, (name if status == "known" else None)
    cand["checks"] = sort_checks(checks)
    f_new = factor(cand, cand["checks"])
    if cand.get("_base") is not None:          # during the export: the kind's base score is at hand
        cand["score"] = float(min(1.0, max(0.0, cand["_base"] * f_new)))
    elif cand.get("score") is not None and f_old > 0:  # an existing file: undo the old factors, apply the new
        cand["score"] = float(min(1.0, max(0.0, cand["score"] * f_new / f_old)))
    return cand


def clean_check(c: dict) -> dict:
    """A check as written to the file: the five fields, values JSON-safe (NaN/inf dropped), null when empty."""
    def js(v):
        if isinstance(v, dict):
            return {str(k): js(x) for k, x in v.items() if js(x) is not None}
        if isinstance(v, (list, tuple, np.ndarray)):
            return [js(x) for x in v]
        return _clean(v)
    vals = js(c.get("values")) if isinstance(c.get("values"), dict) else None
    return {"code": str(c["code"]), "level": str(c["level"]), "text": str(c.get("text") or ""),
            "look": str(c.get("look") or ""), "values": vals or None}


def merge_side_files(cands: list[dict], sides: list[dict]) -> dict:
    """Merge side files into the candidates (in place). Returns stats: per source, ids merged and ids not found."""
    by_id = {c["id"]: c for c in cands}
    entries: dict[str, list[tuple[str, dict]]] = {}
    st: dict = {}
    for s in sides:
        src = str(s.get("source"))
        found = missing = 0
        for cid, e in (s.get("candidates") or {}).items():
            if cid in by_id and isinstance(e, dict):
                entries.setdefault(cid, []).append((src, e))
                found += 1
            else:
                missing += 1
        st[src] = {"merged": found, "not_found": missing, **({"error": s["error"]} if s.get("error") else {})}
    for cid, es in entries.items():
        merge_one(by_id[cid], es)
    return st
