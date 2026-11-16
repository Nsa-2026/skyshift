"""Signal checks of the featured fields, done before the list is published (precompute, P2).

Every signal of web/public/science-data/<slug>/analysis*.json (slow movers, fast movers, cold sources; every detector
published) is checked against Gaia DR3, CatWISE2020 and the known asteroids, comets, planets and moons, and the answer is
written into the same file, so the Viewer's Signals list shows each signal with its checks at once and lists the certain
ones apart ("Explained by a catalogue") before anyone sees them:

    cd pipeline && uv run python ../scripts/precompute/signal_checks.py [--only barnard,epsindib] [--dry-run]
        [--root ~/skyshift-data] [--cache <root>/cache/signal_checks] [--known-cache <root>/cache/known_check]

Uses scripts/candidates/catalogue_check.py and known_check.py (with the pipeline's known modules). Rerunnable and
idempotent: catalogue answers, exposure lists, SPHEREx's positions and the predictions are cached in --cache; a file
whose checks did not change is not rewritten.

The rules are the Viewer's own (web/src/product/vetting.ts, PR #366; contract v3.23 for candidates), ported line by
line so a signal gets the same verdict and the same sentences here as in the browser:
  certain  a known body within BODY_CERTAIN″ of every sighting, with its rate within 20% and its direction within 15°
           when both move → check_status `known`, known_name, check `known_body` (strong). A slow mover on the same Gaia
           or CatWISE star at every visit, moving the same way → `known` too, check `known_star` (strong).
  warn     a catalogue source within STAR_RADIUS″ (`catalogue_star`), or a known body within BODY_NEAR″ that isn't
           certain (`known_nearby`), with the numbers and what to look at in the Viewer.
  ok       nothing near it in any of them (`catalogues_checked`).
What it checks against (more than the browser can ask the server for):
  Gaia DR3      NASA/IPAC IRSA gaia_dr3_source (scripts/candidates/catalogue_check.py's TAP and cache), within 3′ of the
                field, G ≤ 19.5 or a motion over 100 mas/yr (the server's /api/gaia selection), moved from 2016.0.
  CatWISE2020   IRSA catwise_2020 within the server's radius (2.84′ for a 4′ field), moved from 2015.4 and also at its
                catalogue place.
  known bodies  every MPC asteroid at any brightness and every MPC comet (cometels completed with allcometels: 306P/LINEAR
                is one the server's list lacks), moved with kete to each sighting's own time and seen from SPHEREx (the
                exposure's IMAGE header; the Earth's centre for a slow mover's visit mid-time, which is no exposure); planets
                and their moons from JPL Horizons near a planet (known_check.py).
Analysis candidates already have `status` (unvetted, single_visit, …), so the v3.23 status is written as `check_status`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import math
import os
import sys
import time
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
SCIENCE = ROOT / "web" / "public" / "science-data"
SLUGS = ("barnard", "epsindib", "wise0855", "rwaur", "rdra")
DATA = Path(os.environ.get("SKYSHIFT_DATA", str(Path.home() / "skyshift-data")))
STALE_WARNING = "vetoes have not been run"   # the analysis's own warning, answered by these checks

log = logging.getLogger("signal-checks")

# ---- the Viewer's rules (web/src/product/vetting.ts) ---------------------------------------------------------------
GAIA_EPOCH_MJD = 57388.5     # Gaia DR3 positions: J2016.0
CATWISE_EPOCH_MJD = 57170.0  # CatWISE2020 positions: MJD 57170 (2015.4)
PIXEL_ARCSEC = 6.15          # the analysis grid's native pixel
STAR_RADIUS = 6              # ″: a catalogue star this close may be the signal
BODY_CERTAIN = 10            # ″: a known body this close at every sighting (with its motion) is it
BODY_NEAR = 30               # ″: a known body this close at any sighting can't be ruled out
RATE_TOLERANCE = 0.2
PA_TOLERANCE = 15            # degrees
SESSION_DAYS = 0.02          # a sighting belongs to the session prediction this close (session answers only)
HOURS_PER_YEAR = 365.25 * 24
RAD = math.pi / 180
SERVICES = ("gaia", "catwise", "mpc")
SERVICE_NAME = {"gaia": "Gaia DR3", "catwise": "CatWISE2020", "mpc": "MPC orbits"}
LEVEL_RANK = {"strong": 0, "warn": 1, "ok": 2}
# What the browser says it covered (the server's index), and what this step covers.
WEB_BODIES = "no known asteroid (V ≤ 21) or comet"
ALL_BODIES = "no known asteroid (at any brightness), comet, planet or moon"
# Catalogue fields, as the server asks them (gaia.py, enrichment.py)
GAIA_RADIUS_ARCSEC = 180.0   # /api/gaia?r=3
GAIA_G_LIMIT, GAIA_FAST_PM, GAIA_MAX = 19.5, 100.0, 2500


def num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def fmod(a: float, b: float) -> float:
    """JavaScript's %: the remainder keeps the dividend's sign."""
    return math.fmod(a, b)


def to_fixed(x: float, digits: int) -> str:
    """JavaScript's Number.prototype.toFixed: the exact binary value, ties away from zero."""
    if not math.isfinite(x):
        return "NaN" if math.isnan(x) else ("Infinity" if x > 0 else "-Infinity")
    return format(Decimal(x).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP), "f")


def js_round(x: float) -> int:
    """JavaScript's Math.round: halves go up."""
    return int(math.floor(x + 0.5))


def fixed_num(x: float, digits: int) -> float:
    """`+x.toFixed(digits)`."""
    return float(to_fixed(x, digits))


def date_label(mjd: float) -> str:
    """evidence.ts dateLabel: the UTC date of an MJD."""
    ms = math.trunc((mjd - 40587) * 86400000)
    return (dt.datetime(1970, 1, 1) + dt.timedelta(milliseconds=ms)).date().isoformat()


def separation(a: dict, b: dict) -> float:
    """evidence.ts separation (haversine), arcsec."""
    dra = fmod(a["ra"] - b["ra"] + 540, 360) - 180
    h = math.sin((a["dec"] - b["dec"]) * RAD / 2) ** 2 + \
        math.cos(a["dec"] * RAD) * math.cos(b["dec"] * RAD) * math.sin(dra * RAD / 2) ** 2
    return 2 * math.asin(math.sqrt(min(1.0, max(0.0, h)))) / RAD * 3600


def pa_of(east: float, north: float) -> float:
    return fmod(fmod(math.atan2(east, north) / RAD, 360) + 360, 360)


def angle_between(a: float, b: float) -> float:
    d = abs(fmod(fmod(a - b, 360) + 360, 360))
    return min(d, 360 - d)


def offset(p: dict, east: float, north: float) -> dict:
    """Move a position by east/north offsets in arcsec."""
    return {"ra": p["ra"] + east / 3600 / math.cos(p["dec"] * RAD), "dec": p["dec"] + north / 3600}


def motion_between(a: dict, b: dict) -> dict | None:
    hours = (b["mjd"] - a["mjd"]) * 24
    if not abs(hours) > 0.01:
        return None
    east = (fmod(b["ra"] - a["ra"] + 540, 360) - 180) * 3600 * math.cos(((a["dec"] + b["dec"]) / 2) * RAD)
    north = (b["dec"] - a["dec"]) * 3600
    sign = 1 if hours > 0 else -1
    return {"arcsec_h": math.hypot(east, north) / abs(hours), "pa_deg": pa_of(east * sign, north * sign)}


def same_motion(ours: dict, theirs: dict) -> bool:
    """Same motion by the contract's rule: rate within 20% of the catalogue's, direction within 15°."""
    return (abs(ours["arcsec_h"] - theirs["arcsec_h"]) <= RATE_TOLERANCE * theirs["arcsec_h"]
            and angle_between(ours["pa_deg"], theirs["pa_deg"]) <= PA_TOLERANCE)


def from_year(pmra: float, pmdec: float) -> dict:
    """Motion of ″/yr components (pmra × cos dec, pmdec)."""
    return {"arcsec_h": math.hypot(pmra, pmdec) / HOURS_PER_YEAR, "pa_deg": pa_of(pmra, pmdec)}


def signal_track(kind: str, c: dict, frames: list[dict] | None, detector: int) -> dict | None:
    """Where and when a signal was seen (vetting.ts signalTrack). A sighting keeps its frame index `t` when it is an
    exposure (fast movers, cold sources), so SPHEREx's own position can be found for it."""
    def at(x0: float, y0: float) -> Callable[[float, float], dict]:
        return lambda x, y: offset(c, -(x - x0) * PIXEL_ARCSEC, (y - y0) * PIXEL_ARCSEC)

    def frame(t: Any) -> dict | None:
        return frames[t] if isinstance(t, int) and 0 <= t < len(frames) else None

    if kind == "fast":
        motion = ({"arcsec_h": math.hypot(c["vx"], c["vy"]) * PIXEL_ARCSEC / 24, "pa_deg": pa_of(-c["vx"], c["vy"])}
                  if num(c.get("vx")) and num(c.get("vy")) else None)
        if frames and c.get("detections") and num(c.get("x0")) and num(c.get("y0")):
            sky = at(c["x0"], c["y0"])
            seen = [{"mjd": frame(d["t"])["mjd"], **sky(d["x"], d["y"]), "t": d["t"]} if frame(d.get("t")) else None
                    for d in c["detections"]]
            if all(seen):
                return {"sightings": seen, "motion": motion}
        return {"sightings": [{"mjd": c["t0"], "ra": c["ra"], "dec": c["dec"]}], "motion": motion} if num(c.get("t0")) else None
    if kind == "slow":
        motion = (from_year(c["pmra_arcsec_yr"], c["pmdec_arcsec_yr"])
                  if num(c.get("pmra_arcsec_yr")) and num(c.get("pmdec_arcsec_yr")) else None)
        if frames and c.get("points") and num(c.get("x")) and num(c.get("y")):
            sky = at(c["x"], c["y"])

            def mid(visit: int) -> float | None:  # the middle of its detector's first and last exposure in that visit
                t = [f["mjd"] for f in frames if f["det"] == detector and f["visit"] == visit]
                return (min(t) + max(t)) / 2 if t else None
            seen = []
            for p in c["points"]:
                t = mid(p["visit"])
                seen.append(None if t is None else {"mjd": t, **sky(p["x"], p["y"])})
            if all(seen):
                return {"sightings": seen, "motion": motion}
        return {"sightings": [{"mjd": c["mjd_ref"], "ra": c["ra"], "dec": c["dec"]}], "motion": motion} \
            if num(c.get("mjd_ref")) else None
    if frames and c.get("detections") and num(c.get("x")) and num(c.get("y")):
        sky = at(c["x"], c["y"])
        seen = [{"mjd": frame(d["t"])["mjd"], **sky(d["x"], d["y"]), "t": d["t"]} if frame(d.get("t")) else None
                for d in c["detections"]]
        if all(seen):
            return {"sightings": seen, "motion": None, "still": True}
    return None


def mean_place(t: dict) -> dict:
    """A still source's one place: the mean of its sightings, at their mean time."""
    s = t["sightings"]
    if not t.get("still") or len(s) < 2:
        return t
    def m(f):
        return sum(f(x) for x in s) / len(s)
    ra0 = s[0]["ra"]
    ra = fmod(ra0 + m(lambda x: fmod(x["ra"] - ra0 + 540, 360) - 180) + 360, 360)
    return {"sightings": [{"mjd": m(lambda x: x["mjd"]), "ra": ra, "dec": m(lambda x: x["dec"])}], "motion": None,
            "still": True, "mean": True}


def gaia_at(s: dict, mjd: float) -> dict:
    years = (mjd - GAIA_EPOCH_MJD) / 365.25
    pmra, pmdec = s.get("pmra"), s.get("pmdec")
    return offset(s, (0 if pmra is None else pmra) * years / 1000, (0 if pmdec is None else pmdec) * years / 1000)


def catwise_at(s: dict, mjd: float) -> dict:
    years = (mjd - CATWISE_EPOCH_MJD) / 365.25
    pmra, pmdec = s.get("pmra"), s.get("pmdec")
    return offset(s, (0 if pmra is None else pmra) * years, (0 if pmdec is None else pmdec) * years)


# ---- known bodies: where each one is at each sighting ----------------------------------------------------------------
# A provider answers, for a track, {name: {"at": [{"pos", "motion", "v"} | None per sighting], "source": "MPC orbits" |
# "JPL Horizons"}} and how many sightings it could not check. Two: the server's per-session answer (as the browser
# reads /api/enrichment?kind=asteroids) and kete's prediction at each sighting itself (this step).

def body_at_session(preds: list[dict], mjd: float) -> dict | None:
    """vetting.ts bodyAt: the body's prediction for that session, moved within the session by its own motion (from its
    neighbouring prediction in the same visit)."""
    best = None
    for p in preds:
        if abs(p["mjd"] - mjd) <= SESSION_DAYS and (best is None or abs(p["mjd"] - mjd) < abs(best["mjd"] - mjd)):
            best = p
    if best is None:
        return None
    b = best
    nxt = sorted([q for q in preds if q is not b and q["visit"] == b["visit"] and abs(q["mjd"] - b["mjd"]) > 0.001
                  and abs(q["mjd"] - b["mjd"]) <= 3], key=lambda q: abs(q["mjd"] - b["mjd"]))
    motion = motion_between(b, nxt[0]) if nxt else None
    hours = (mjd - b["mjd"]) * 24
    if motion:
        pos = {"mjd": mjd, **offset(b, motion["arcsec_h"] * hours * math.sin(motion["pa_deg"] * RAD),
                                    motion["arcsec_h"] * hours * math.cos(motion["pa_deg"] * RAD))}
    else:
        pos = {"mjd": mjd, "ra": b["ra"], "dec": b["dec"]}
    return {"pos": pos, "motion": motion, "v": b.get("v")}


def session_bodies(rows: list[dict], unchecked: list[float] | None = None):
    """The browser's provider: /api/enrichment?kind=asteroids rows ({name, ra, dec, v, visit, mjd}, one per session)."""
    def provide(t: dict) -> tuple[dict, int]:
        missed = sum(1 for s in t["sightings"] if any(abs(m - s["mjd"]) <= SESSION_DAYS for m in (unchecked or [])))
        names: dict[str, list[dict]] = {}
        for p in rows:
            names.setdefault(p["name"], []).append(p)
        return {n: {"at": [body_at_session(ps, s["mjd"]) for s in t["sightings"]], "source": "MPC orbits"}
                for n, ps in names.items()}, missed
    return provide


def sighting_bodies(hits: list[list[dict]], unchecked: list[bool]):
    """This step's provider: kete's (and Horizons') predictions at each sighting itself. hits[i]: the bodies predicted
    near sighting i, {name, ra, dec, rate (″/h), pa (deg), v, source}; unchecked[i]: outside the orbit index's time."""
    def provide(t: dict) -> tuple[dict, int]:
        out: dict[str, dict] = {}
        for i, (s, hs) in enumerate(zip(t["sightings"], hits)):
            for h in hs:
                body = out.setdefault(h["name"], {"at": [None] * len(t["sightings"]), "source": h.get("source", "MPC orbits")})
                here = {"pos": {"mjd": s["mjd"], "ra": h["ra"], "dec": h["dec"]},
                        "motion": {"arcsec_h": h["rate"], "pa_deg": h["pa"]} if num(h.get("rate")) and num(h.get("pa")) else None,
                        "v": h.get("v")}
                old = body["at"][i]
                if old is None or separation(here["pos"], s) < separation(old["pos"], s):
                    body["at"][i] = here
        return out, sum(1 for u in unchecked if u)
    return provide


# ---- words (vetting.ts) -------------------------------------------------------------------------------------------------
def arcsec(v: float) -> str:
    return f"{to_fixed(v, 1) if v < 10 else js_round(v)}″"


def rates(a: float, b: float) -> tuple[str, str]:
    """Two rates in one unit, chosen by the larger: ″/h, ″/day or ″/yr."""
    big = max(a, b)
    k, unit, digits = (1, "″/h", 1) if big >= 1 else (24, "″/day", 1) if big * 24 >= 1 else (HOURS_PER_YEAR, "″/yr", 2)
    return f"{to_fixed(a * k, digits)}{unit}", f"{to_fixed(b * k, digits)}{unit}"


def pa_words(m: dict) -> str:
    return f"PA {js_round(m['pa_deg'])}°"


def mag(v: Any, band: str) -> str:
    return f"{band} {to_fixed(v, 1)}" if num(v) else ""


def plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def motion_vs(theirs: dict, ours: dict | None) -> str:
    if not ours:
        return f"moving {rates(theirs['arcsec_h'], theirs['arcsec_h'])[0]} toward {pa_words(theirs)}"
    t, o = rates(theirs["arcsec_h"], ours["arcsec_h"])
    return f"moving {t} toward {pa_words(theirs)} (ours {o} toward {pa_words(ours)})"


def ours_values(kind: str, m: dict | None) -> dict:
    """Check `values` keys, one meaning each (contract v3.25): `sep_arcsec` the nearest, `max_sep_arcsec` the farthest;
    `rate_arcsec_h` (a slow mover's `rate_arcsec_yr`) and `pa_deg` ours; `body_rate_arcsec_h`, `body_pa_deg` a known
    body's; `star_rate_arcsec_yr`, `star_pa_deg` a catalogued star's (vetting.ts oursValues, bodyValues)."""
    if not m:
        return {}
    if kind == "slow":
        return {"rate_arcsec_yr": fixed_num(m["arcsec_h"] * HOURS_PER_YEAR, 3), "pa_deg": js_round(m["pa_deg"])}
    return {"rate_arcsec_h": fixed_num(m["arcsec_h"], 3), "pa_deg": js_round(m["pa_deg"])}


def body_values(m: dict | None) -> dict:
    return {"body_rate_arcsec_h": fixed_num(m["arcsec_h"], 3), "body_pa_deg": js_round(m["pa_deg"])} if m else {}


def seen_when(t: dict, k: int) -> str:
    n = len(t["sightings"])
    return "" if n == 1 else f" at all {n} sightings" if k == n else f" at {k} of its {n} sightings"


def within(t: dict, k: int, radius: int) -> str:
    return "" if len(t["sightings"]) == 1 else f", within {radius}″{seen_when(t, k)}"


def look_for(kind: str, det: int, what: str, mjd: float, name: str = "") -> str:
    day = date_label(mjd)
    if what == "body":
        return f"Blink Hours on D{det} around {day}: see whether the dot follows {name or 'the known body'}'s predicted path."
    if kind == "cold":
        return ("On Months, compare D3 with D6 here: if the star shows in D3 too, the long-wavelength light is probably the star's."
                if what == "star" else
                "On Months at D6, check that the signal is centred on this WISE source: if it is, it is already catalogued.")
    if kind == "fast":
        return (f"Blink Hours on D{det} around {day}: see whether the dot sits still on the "
                f"{'star' if what == 'star' else 'WISE source'} there or moves past it.")
    return f"Blink Months on D{det}: see whether the shift belongs to this {'star' if what == 'star' else 'source'} or comes from a neighbour blended with it."


def put(source: str) -> str:
    """"MPC orbits put" / "JPL Horizons puts"."""
    return f"{source} {'puts' if source == 'JPL Horizons' else 'put'}"


# ---- one signal -----------------------------------------------------------------------------------------------------
def star_hits(t: dict, place: Callable[[float], list[dict]]) -> list[dict]:
    by_name: dict[str, dict] = {}
    n = len(t["sightings"])
    for i, s in enumerate(t["sightings"]):
        for star in place(s["mjd"]):
            sep = separation(star, s)
            hit = by_name.get(star["name"])
            if hit is None:
                hit = by_name[star["name"]] = {"name": star["name"], "seps": [math.inf] * n, "k": 0, "nearest": math.inf,
                                               "nearestAt": s["mjd"], "detail": star["detail"], "motion": star["motion"],
                                               "catalogue": star["catalogue"]}
            hit["seps"][i] = min(hit["seps"][i], sep)
    hits = list(by_name.values())
    for h in hits:
        h["k"] = sum(1 for v in h["seps"] if v <= STAR_RADIUS)
        i = h["seps"].index(min(h["seps"]))
        h["nearest"], h["nearestAt"] = h["seps"][i], t["sightings"][i]["mjd"]
    return sorted([h for h in hits if h["k"] > 0], key=lambda h: (-h["k"], h["nearest"]))


def check(code: str, level: str, text: str, look: str, values: dict | None) -> dict:
    return {"code": code, "level": level, "text": text, "look": look, "values": values}


def vet_signal(kind: str, t: dict | None, cats: dict, det: int, bodies_phrase: str = WEB_BODIES) -> dict:
    """vetting.ts vetSignal. cats: {"gaia": rows | None, "catwise": rows | None, "mpc": provider | None}; None: the
    service failed. Gaia rows {id, ra, dec, pmra, pmdec, parallax, g} (mas/yr), CatWISE rows {name, ra, dec, w1mpro,
    w2mpro, pmra, pmdec} (″/yr), mpc a provider (session_bodies, sighting_bodies)."""
    checks: list[dict] = []
    found = {"gaia": False, "catwise": False, "body": False, "clear": False}
    failed = [s for s in SERVICES if cats.get(s) is None]
    status, known_name, known_kind = "open", None, None
    if not t or not t["sightings"]:
        checks.append(check("catalogue_unchecked", "warn",
                            "Not checked against catalogues: the times of its observations did not load, so catalogue "
                            "positions could not be moved to them",
                            "Reload the page to try again; until then a catalogued star or known body can't be ruled out.", None))
        return {"status": status, "known_name": known_name, "known_kind": known_kind, "checks": checks, "found": found,
                "failed": [], "untimed": True}
    n, first = len(t["sightings"]), t["sightings"][0]["mjd"]
    clean: list[str] = []

    provider = cats.get("mpc")
    if provider is not None:
        bodies, missed = provider(t)
        hits = []
        for name, body in bodies.items():
            at = body["at"]
            motion = next((b["motion"] for b in at if b and b["motion"]), None)
            v = next((b.get("v") for b in at if b), None)
            seps = [separation(b["pos"], t["sightings"][i]) if b else None for i, b in enumerate(at)]
            valid = [x for x in seps if x is not None]
            if not valid:
                continue
            nearest = min(valid)
            if nearest > BODY_NEAR:
                continue
            place_all = all(x is not None and x <= BODY_CERTAIN for x in seps)
            certain = place_all and (not motion or not t["motion"] or same_motion(t["motion"], motion))
            hits.append({"name": name, "seps": seps, "k": sum(1 for x in valid if x <= BODY_NEAR), "nearest": nearest,
                         "nearestAt": t["sightings"][seps.index(nearest)]["mjd"], "motion": motion,
                         "v": v if num(v) else None, "certain": certain, "placeAll": place_all, "source": body["source"]})
        hits.sort(key=lambda h: (-int(h["certain"]), h["nearest"]))
        sure = next((h for h in hits if h["certain"]), None)
        if sure:
            status, known_name, known_kind = "known", sure["name"], "body"
            worst = max(x or 0 for x in sure["seps"])
            vals = {"sep_arcsec": fixed_num(sure["nearest"], 2), "max_sep_arcsec": fixed_num(worst, 2), "sightings": n,
                    **body_values(sure["motion"]), **ours_values(kind, t["motion"])}
            checks.append(check(
                "known_body", "strong",
                f"{put(sure['source'])} {sure['name']}{f' (V {to_fixed(sure['v'], 1)})' if sure['v'] is not None else ''} "
                f"within {arcsec(worst)} of it{seen_when(t, n)}{', ' + motion_vs(sure['motion'], t['motion']) if sure['motion'] else ''}",
                look_for(kind, det, "body", first, sure["name"]), vals))
        for h in ([] if sure else hits[:2]):  # once certain, other bodies passing by add nothing to check
            found["body"] = True
            if h["placeAll"] and h["motion"] and t["motion"]:
                why = f", but {motion_vs(h['motion'], t['motion'])}"
            elif h["motion"]:
                why = f", {motion_vs(h['motion'], t['motion'])}"
            else:
                why = ""
            vals = {"sep_arcsec": fixed_num(h["nearest"], 2), "sightings": h["k"],
                    **body_values(h["motion"]), **ours_values(kind, t["motion"])}
            checks.append(check(
                "known_nearby", "warn",
                f"{put(h['source'])} {h['name']}{f' (V {to_fixed(h['v'], 1)})' if h['v'] is not None else ''} "
                f"{arcsec(h['nearest'])} from it on {date_label(h['nearestAt'])}{within(t, h['k'], BODY_NEAR)}{why}",
                look_for(kind, det, "body", h["nearestAt"], h["name"]), vals))
        if missed:
            checks.append(check(
                "catalogue_unchecked", "warn",
                f"MPC orbits were not checked at {'its sighting' if n == 1 else f'{missed} of its {n} sightings'}: "
                f"the known-object index doesn't reach {'that time' if n == 1 else 'those times'}", "", {"sightings": missed}))
        if not hits and not missed:
            clean.append("mpc")

    place = mean_place(t)
    m = len(place["sightings"])

    def stars(service: str) -> None:
        nonlocal status, known_name, known_kind
        rows = cats.get(service)
        if rows is None:
            return
        if not rows:
            checks.append(check("catalogue_empty", "warn",
                                f"{SERVICE_NAME[service]} lists no source anywhere in this field (it can leave gaps around "
                                "very bright stars), so it says nothing about this signal", "", None))
            return
        if service == "gaia":
            def at_time(mjd: float) -> list[dict]:
                return [{"mjd": mjd, **gaia_at(s, mjd), "name": f"Gaia DR3 {s['id']}", "detail": mag(s.get("g"), "G"),
                         "motion": from_year(s["pmra"] / 1000, s["pmdec"] / 1000) if num(s.get("pmra")) and num(s.get("pmdec")) else None,
                         "catalogue": "gaia"} for s in rows]
        else:
            def at_time(mjd: float) -> list[dict]:
                out = []
                for s in rows:
                    if not (num(s.get("ra")) and num(s.get("dec"))):
                        continue
                    detail = ", ".join(x for x in (mag(s.get("w1mpro"), "W1"), mag(s.get("w2mpro"), "W2")) if x)
                    motion = from_year(s["pmra"], s["pmdec"]) if num(s.get("pmra")) and num(s.get("pmdec")) else None
                    # A faint source's catalogued motion can be noise: compare at its catalogue epoch too, keep the nearer.
                    common = {"name": f"CatWISE2020 {s['name']}", "detail": detail, "motion": motion, "catalogue": "catwise"}
                    out += [{"mjd": mjd, **catwise_at(s, mjd), **common}, {"mjd": mjd, "ra": s["ra"], "dec": s["dec"], **common}]
                return out
        hits = star_hits(place, at_time)
        if not hits:
            clean.append(service)
            return
        best = hits[0]
        same_star = bool(t["motion"] and best["motion"] and best["k"] == m and same_motion(t["motion"], best["motion"]))
        if known_kind == "star" and same_star:  # the other catalogue's entry for a star already named
            line = next((c for c in checks if c["code"] == "known_star"), None)
            if line:
                line["text"] += f" ({SERVICE_NAME[service]} lists it too, as {best['name'].split(' ', 1)[1]})"
        if status == "known":
            return
        if kind == "slow" and status == "open" and same_star and t["motion"] and best["motion"]:
            status, known_name, known_kind = "known", best["name"], "star"
            worst = max(best["seps"])
            checks.append(check(
                "known_star", "strong",
                f"{best['name']}{f' ({best['detail']})' if best['detail'] else ''} is within {arcsec(worst)} of it"
                f"{seen_when(place, m)}, {motion_vs(best['motion'], t['motion'])}: this is that star's own catalogued motion",
                f"Blink Months on D{det}: the star should step the same way each visit.",
                {"sep_arcsec": fixed_num(best["nearest"], 2), "max_sep_arcsec": fixed_num(worst, 2),
                 "star_rate_arcsec_yr": fixed_num(best["motion"]["arcsec_h"] * HOURS_PER_YEAR, 3),
                 "star_pa_deg": js_round(best["motion"]["pa_deg"]), **ours_values(kind, t["motion"])}))
            return
        found[service] = True
        epoch = ("the star moved from 2016.0 with its catalogued motion" if service == "gaia"
                 else "catalogue epoch 2015.4, moved with its catalogued motion")
        more = f"; {plural(len(hits) - 1, 'more source')} within {STAR_RADIUS}″" if len(hits) > 1 else ""
        motion = f", {motion_vs(best['motion'], t['motion'])}" if kind == "slow" and best["motion"] else ""
        whose = "its mean place" if place.get("mean") else "it"
        checks.append(check(
            "catalogue_star", "warn",
            f"{best['name']}{f' ({best['detail']})' if best['detail'] else ''} sits {arcsec(best['nearest'])} from {whose}"
            f"{f' on {date_label(best['nearestAt'])}' if m > 1 else ''}{within(place, best['k'], STAR_RADIUS)}{motion} ({epoch}{more})",
            look_for(kind, det, "star" if service == "gaia" else "catwise", best["nearestAt"]),
            {"sep_arcsec": fixed_num(best["nearest"], 2), "sightings": best["k"], "sources": len(hits), "catalogue": service}))

    stars("gaia")
    stars("catwise")

    for service in failed:
        checks.append(check("catalogue_unchecked", "warn", f"Not checked against {SERVICE_NAME[service]}: the service did not answer",
                            "Try again later; until then what that catalogue lists here can't be ruled out.", None))

    if clean and status == "open":
        clean.sort(key=SERVICES.index)
        names = [SERVICE_NAME[s] for s in clean]
        listed = names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"
        parts = []
        star_names = ["Gaia" if s == "gaia" else "CatWISE" for s in clean if s != "mpc"]
        if star_names:
            parts.append(f"nothing in {' or '.join(star_names)} within {STAR_RADIUS}″")
        if "mpc" in clean:
            parts.append(f"{bodies_phrase} predicted within {BODY_NEAR}″{' at its times' if n > 1 else ''}")
        checks.append(check("catalogues_checked", "ok", f"{listed} checked: {', and '.join(parts)}", "", None))
    found["clear"] = (status == "open" and not found["gaia"] and not found["catwise"] and not found["body"]
                      and not failed and len(clean) == 3)
    order = sorted(range(len(checks)), key=lambda i: (LEVEL_RANK[checks[i]["level"]], i))
    return {"status": status, "known_name": known_name, "known_kind": known_kind, "checks": [checks[i] for i in order],
            "found": found, "failed": failed, "untimed": False}


def category(v: dict) -> str:
    """Each signal once, by what explains it best (vetting.ts category)."""
    if v["untimed"]:
        return "untimed"
    if v["known_kind"]:
        return v["known_kind"]
    f = v["found"]
    return "near" if f["body"] else "gaia" if f["gaia"] else "catwise" if f["catwise"] else "clear" if f["clear"] else "partial"


# ---- the catalogues -------------------------------------------------------------------------------------------------
def _candidates_module(name: str):
    """scripts/candidates/<name>.py (catalogue_check, known_check)."""
    sys.path.insert(0, str(ROOT / "scripts" / "candidates"))
    try:
        return __import__(name)
    finally:
        sys.path.pop(0)


def field_rows(cat: str, ra: float, dec: float, radius: float, cache_dir: Path) -> list[dict]:
    """IRSA's rows of `cat` (catalogue_check.CATALOGUES) within `radius`″ of (ra, dec): catalogue_check's TAP query and
    cache. Raises when IRSA doesn't answer (nothing is written then)."""
    cc = _candidates_module("catalogue_check")
    cache = cc.Cache(cache_dir)
    answers = cache.load(cat)
    key = cc.qkey(ra, dec, radius)
    if key not in answers:
        for attempt in range(1, cc.NET_RETRIES + 1):
            try:
                answers[key] = cc.irsa_crossmatch(cat, [(ra, dec, radius)])[0]
                cache.save(cat)
                break
            except Exception as e:  # noqa: BLE001
                log.warning("IRSA %s at %.5f %+.5f, try %d: %s", cat, ra, dec, attempt, str(e)[:200])
                if attempt == cc.NET_RETRIES:
                    raise
                time.sleep(10 * attempt)
    return answers[key]


def gaia_field(ra: float, dec: float, cache_dir: Path) -> list[dict]:
    """The field's Gaia DR3 stars as /api/gaia?r=3 gives them (G ≤ 19.5 or a motion over 100 mas/yr, brightest first,
    at most 2,500), from IRSA."""
    rows = field_rows("gaia", ra, dec, GAIA_RADIUS_ARCSEC, cache_dir)
    def pm(r):
        return math.hypot(r["pmra"], r["pmdec"]) if num(r.get("pmra")) and num(r.get("pmdec")) else -1.0
    keep = [r for r in rows if (num(r.get("phot_g_mean_mag")) and r["phot_g_mean_mag"] <= GAIA_G_LIMIT) or pm(r) > GAIA_FAST_PM]
    keep.sort(key=lambda r: (not num(r.get("phot_g_mean_mag")), r.get("phot_g_mean_mag") or 0.0))
    return [{"id": str(r["source_id"]), "ra": r["ra"], "dec": r["dec"], "pmra": r.get("pmra"), "pmdec": r.get("pmdec"),
             "parallax": r.get("parallax"), "g": r.get("phot_g_mean_mag")} for r in keep[:GAIA_MAX]]


def catwise_radius_arcsec(coverage_arcmin: float) -> float:
    """enrichment.py: the field's size / 120 × 1.42 degrees."""
    return coverage_arcmin / 120 * 1.42 * 3600


def catwise_field(ra: float, dec: float, coverage_arcmin: float, cache_dir: Path) -> list[dict]:
    """The field's CatWISE2020 sources as /api/enrichment?kind=catwise gives them (″/yr motions), from IRSA."""
    rows = field_rows("catwise", ra, dec, catwise_radius_arcsec(coverage_arcmin), cache_dir)
    return [{"name": r["source_name"], "ra": r.get("ra"), "dec": r.get("dec"), "w1mpro": r.get("w1mpro"),
             "w2mpro": r.get("w2mpro"), "pmra": r.get("pmra"), "pmdec": r.get("pmdec")} for r in rows]


# ---- known bodies: kete at each sighting, from SPHEREx (known_check.py) -----------------------------------------------
def exposure_ids(slug: str, center: dict, frames: list[dict], cache_dir: Path) -> list[dict | None]:
    """obs_id and S3 URL of every science frame (same detector, mid-time within 3 s), from IRSA's photo list of a box
    0.01° around the field (the pipeline's photolist TAP query); cached per field."""
    f = cache_dir / "exposures" / f"{slug}.json"
    if f.exists():
        photos = json.loads(f.read_text())
    else:
        from skyshift_pipeline import config as C
        from skyshift_pipeline import photolist as P
        half = 0.01
        cosd = max(math.cos(math.radians(center["dec"])), 1e-3)
        zone = C.Zone(f"signals_{slug}", center["ra"] - half / cosd, center["ra"] + half / cosd,
                      center["dec"] - half, center["dec"] + half)
        raw, _ = P.tap_query(zone)
        clean, _ = P.clean(raw)
        photos = [{"obs_id": r.obs_id, "det": int(r.detector), "mjd": float(r.mjd), "url": r.url} for r in clean.itertuples()]
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(photos))
    out: list[dict | None] = []
    for fr in frames:
        best = min((p for p in photos if p["det"] == fr["det"]), key=lambda p: abs(p["mjd"] - fr["mjd"]), default=None)
        out.append(best if best is not None and abs(best["mjd"] - fr["mjd"]) * 86400 <= 3 else None)
    return out


def spherex_positions(urls: dict[str, str], cache_dir: Path) -> dict[str, dict | None]:
    """X_SC..VZ_SC of each exposure (one 12 KB range read of its IMAGE header, known_check.read_sc), cached."""
    kc = _candidates_module("known_check")
    f = cache_dir / "observers.json"
    have = json.loads(f.read_text()) if f.exists() else {}
    todo = sorted(o for o in urls if o not in have)
    for i, o in enumerate(todo):
        try:
            sc = kc.read_sc(urls[o])
            have[o] = sc if all(sc.get(k) is not None for k in kc.SC_KEYS) else None
        except Exception as e:  # noqa: BLE001
            log.warning("no IMAGE header for %s: %s (the Earth's centre is used)", o, str(e)[:120])
            have[o] = None
        if (i + 1) % 50 == 0 or i + 1 == len(todo):
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(have))
            log.info("SPHEREx positions: %d/%d headers read", i + 1, len(todo))
    return {o: have.get(o) for o in urls}


class KnownBodies:
    """known_check.py's orbits (every MPC asteroid at any V, every comet), index and kete predictions, and its Horizons
    planets and moons, at any list of sightings, each seen from SPHEREx when it is an exposure."""

    def __init__(self, root: str, known_cache: Path, cache_dir: Path):
        self.root, self.known_cache, self.cache_dir = root, known_cache, cache_dir

    def exposures(self, slug: str, center: dict, frames: list[dict]) -> list[dict | None]:
        return exposure_ids(slug, center, frames, self.cache_dir)

    def positions(self, urls: dict[str, str]) -> dict[str, dict | None]:
        return spherex_positions(urls, self.cache_dir)

    def cached_index(self, label: str, mjd_lo: float, mjd_hi: float):
        """An all-V index known_check already built from the same orbit snapshots that covers these times (± its
        margin): reused as it is instead of building one for a slightly different span (tens of minutes)."""
        import numpy as np
        from skyshift_core import known_index as KI
        margin = self.kc.INDEX_MARGIN_DAYS
        for p in sorted(self.known_cache.glob("index_*.npz"), key=lambda p: -p.stat().st_mtime):
            if "_part" in p.name or ".tmp" in p.name:
                continue
            try:
                with np.load(p, allow_pickle=False) as z:  # the metadata only: an npz is read lazily
                    ok = (str(z["snapshot"]) == label and float(z["mjd_lo"]) <= mjd_lo - margin
                          and float(z["mjd_hi"]) >= mjd_hi + margin and not np.isfinite(float(z["v_keep"])))
            except (OSError, KeyError, ValueError):
                continue
            if ok:
                log.info("known index: reusing %s", p.name)
                return KI.load_index(str(p))
        return None

    def load(self, mjd_lo: float, mjd_hi: float) -> None:
        from skyshift_pipeline.store import Store
        kc = self.kc = _candidates_module("known_check")
        ast, com, self.label = kc.orbits(Store(self.root), self.known_cache)
        self.idx = self.cached_index(self.label, mjd_lo, mjd_hi) or \
            kc.index(self.known_cache, ast, com, self.label, mjd_lo, mjd_hi, None, self.root)
        self.names = kc.index_names(self.idx)
        self.full = kc.full_names(ast)
        self.n_ast, self.n_com = int(len(self.idx.ast_rows)), int(len(self.idx.comet_rows))
        del ast, com

    def predict(self, sightings: list[dict], obs_sc: dict[str, dict | None]) -> list[list[dict]]:
        """Per sighting {id, ra, dec, mjd, obs}: the bodies within known_check's NEARBY (2′) of it at its time."""
        kc = self.kc
        items = [{"id": s["id"], "ra": s["ra"], "dec": s["dec"], "mjd": s["mjd"], "rate": None, "pa": None,
                  "times": [s["mjd"]], "obs": [s.get("obs")], "from_dots": True} for s in sightings]
        mpc = kc.cached_predict(self.cache_dir, f"{self.label}|{self.idx.meta['built']}", items,
                                lambda its: kc.predict_mpc(self.idx, its, obs_sc), "signals")
        planets, _ = kc.predict_planets(items, self.cache_dir)
        out = []
        for it in items:
            hs = []
            for h in mpc[it["id"]]:
                name = str(self.names[h["row"]])
                hs.append({"name": self.full.get(name, name), "ra": h["ra"], "dec": h["dec"], "rate": h["rate"],
                           "pa": h["pa"], "v": h.get("v"), "source": "MPC orbits"})
            for h in planets[it["id"]]:
                hs.append({"name": h["name"], "ra": h["ra"], "dec": h["dec"], "rate": h["rate"], "pa": h["pa"],
                           "v": h.get("v"), "source": "JPL Horizons"})
            out.append(hs)
        return out

    def covers(self, mjd: float) -> bool:
        return self.idx.mjd_lo <= mjd <= self.idx.mjd_hi

    def about(self) -> str:
        return (f"MPC orbits of {self.n_ast:,} asteroids at any brightness and {self.n_com:,} comets ({self.label}), "
                "moved with kete to each sighting's time and seen from SPHEREx; planets and moons from JPL Horizons")


# ---- a field --------------------------------------------------------------------------------------------------------
KINDS = (("slow_movers", "slow"), ("fast_movers", "fast"), ("cold_candidates", "cold"))


def field_signals(folder: Path) -> list[tuple[Path, dict, list[tuple[str, int, str, dict]]]]:
    """[(analysis file, its JSON, [(list key, index, kind, candidate)])] of a published field, every detector."""
    out = []
    for path in sorted(folder.glob("analysis*.json")):
        a = json.loads(path.read_text())
        out.append((path, a, [(key, i, kind, c) for key, kind in KINDS for i, c in enumerate(a.get(key) or [])]))
    return out


def written(c: dict, v: dict) -> dict:
    """The candidate with its checks (v3.23 shape; `check_status` because analysis candidates have their own `status`)."""
    return {**{k: x for k, x in c.items() if k not in ("check_status", "known_name", "checks")},
            "check_status": v["status"], "known_name": v["known_name"], "checks": v["checks"]}


def summary(verdicts: list[dict]) -> str:
    counts: dict[str, int] = {}
    for v in verdicts:
        counts[category(v)] = counts.get(category(v), 0) + 1
    return ", ".join(f"{n} {k}" for k, n in sorted(counts.items())) or "no signals"


def run(slugs: list[str], science: Path, cache_dir: Path, known, dry_run: bool = False,
        catalogues: Callable | None = None) -> dict:
    """Check every signal of the given fields and write the answers into their analysis files. known: KnownBodies (or a
    stand-in with its methods); catalogues(slug, center, coverage_arcmin) -> {"gaia": rows, "catwise": rows} replaces
    IRSA (tests). Any failure raises before a file is written: a list is never published half checked."""
    fields = []
    for slug in slugs:
        folder = science / slug
        frames = json.loads((folder / "science.json").read_text())["frames"]
        center = json.loads((folder / "frames.json").read_text())["center"]
        fields.append((slug, folder, frames, center, field_signals(folder)))

    # Every sighting first: one orbit index and one kete run for all fields.
    tracks: dict[tuple, dict] = {}
    sightings: list[dict] = []
    for slug, _, frames, center, files in fields:
        ids = None
        for path, a, signals in files:
            for key, i, kind, c in signals:
                t = signal_track(kind, c, frames, a["detector"])
                if t is None:
                    raise SystemExit(f"{slug}/{path.name} {key}[{i}]: no times for its sightings (science.json frames)")
                tracks[(slug, path.name, key, i)] = t
                for k, s in enumerate(t["sightings"]):
                    obs = None
                    if "t" in s:  # an exposure: SPHEREx's own position (a slow mover's visit mid-time is none)
                        ids = ids if ids is not None else known.exposures(slug, center, frames)
                        obs = ids[s["t"]]
                    sightings.append({"id": f"{slug}/{path.name}/{key}/{i}/{k}", "ra": s["ra"], "dec": s["dec"],
                                      "mjd": s["mjd"], "obs": obs["obs_id"] if obs else None,
                                      "url": obs["url"] if obs else None})
    hits: dict[str, list[dict]] = {}
    if sightings:
        known.load(min(s["mjd"] for s in sightings), max(s["mjd"] for s in sightings))
        urls = {s["obs"]: s["url"] for s in sightings if s["obs"]}
        obs_sc = known.positions(urls)
        log.info("%d sightings, %d exposures (%d with SPHEREx's position)", len(sightings), len(urls),
                 sum(1 for v in obs_sc.values() if v))
        inside = [s for s in sightings if known.covers(s["mjd"])]
        for s, hs in zip(inside, known.predict(inside, obs_sc)):
            hits[s["id"]] = hs

    made = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    report: dict[str, Any] = {"made": made, "fields": {}}
    for slug, folder, frames, center, files in fields:
        coverage = files[0][1]["source"]["coverage_arcmin"] if files else 4.0
        cats_rows = catalogues(slug, center, coverage) if catalogues else {
            "gaia": gaia_field(center["ra"], center["dec"], cache_dir / "catalogue"),
            "catwise": catwise_field(center["ra"], center["dec"], coverage, cache_dir / "catalogue")}
        for path, a, signals in files:
            verdicts = []
            for key, i, kind, c in signals:
                t = tracks[(slug, path.name, key, i)]
                ids = [f"{slug}/{path.name}/{key}/{i}/{k}" for k in range(len(t["sightings"]))]
                provider = sighting_bodies([hits.get(x, []) for x in ids], [x not in hits for x in ids])
                v = vet_signal(kind, t, {**cats_rows, "mpc": provider}, a["detector"], ALL_BODIES)
                verdicts.append(v)
                a[key][i] = written(c, v)
            a["warnings"] = [w for w in a.get("warnings", []) if STALE_WARNING not in w]
            sources = {"gaia": f"Gaia DR3 (NASA/IPAC IRSA), within {GAIA_RADIUS_ARCSEC / 60:g}′: G ≤ {GAIA_G_LIMIT:g} or "
                               f"a motion over {GAIA_FAST_PM:g} mas/yr, moved from 2016.0 to each sighting's time",
                       "catwise": f"CatWISE2020 (NASA/IPAC IRSA), within {to_fixed(catwise_radius_arcsec(coverage) / 60, 2)}′, "
                                  "moved from 2015.4 and also at its catalogue place",
                       "mpc": known.about()}
            old = json.loads(path.read_text())
            same = (old.get("catalogue_checks", {}).get("sources") == sources and old.get("warnings") == a["warnings"]
                    and all(old.get(k) == a[k] for k, _ in KINDS if k in a))
            a["catalogue_checks"] = old["catalogue_checks"] if same else {"made": made, "sources": sources}
            counts = summary(verdicts)
            report["fields"][f"{slug}/{path.name}"] = {
                "signals": len(verdicts), "counts": counts, "unchanged": same,
                "known": [v["known_name"] for v in verdicts if v["status"] == "known"],
                "checks": [[c["level"] + ":" + c["code"] for c in v["checks"]] for v in verdicts]}
            log.info("%s/%s: %d signals: %s%s", slug, path.name, len(verdicts), counts, " (unchanged)" if same else "")
            if not dry_run and not same:
                path.write_text(json.dumps(a, ensure_ascii=False, allow_nan=False))
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", default=",".join(SLUGS), help="comma-separated slugs (default: the five featured fields)")
    ap.add_argument("--science", default=str(SCIENCE), help="the published fields' folder")
    ap.add_argument("--root", default=str(DATA), help="local folder with cache/mpc/orbits/ (known_check's --root)")
    ap.add_argument("--cache", default=None, help="default <root>/cache/signal_checks")
    ap.add_argument("--known-cache", default=None, help="known_check's cache (orbits, index): default <root>/cache/known_check")
    ap.add_argument("--dry-run", action="store_true", help="check and report, write nothing")
    ap.add_argument("--report", default=None, help="write the run's summary JSON here")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="[signal_checks %(asctime)s] %(message)s", datefmt="%H:%M:%S")
    cache = Path(a.cache or f"{a.root}/cache/signal_checks")
    known_cache = Path(a.known_cache or f"{a.root}/cache/known_check")
    slugs = [s for s in a.only.split(",") if s]
    report = run(slugs, Path(a.science), cache, KnownBodies(a.root, known_cache, cache), a.dry_run)
    if a.report:
        Path(a.report).write_text(json.dumps(report, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
