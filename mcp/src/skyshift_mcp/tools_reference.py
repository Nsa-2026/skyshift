"""Reference for the model: featured targets, validation results (V1–V11), MCP resources (contracts,
flags, domain facts) and MCP prompts (explain_candidate, triage_movers, check_my_target).

Register with `@app.tool()`, `@app.resource()`, `@app.prompt()` on the shared app.

Package data (src/skyshift_mcp/data/) are byte copies of repo files, checked by tests/test_reference.py:
  data/targets.json            ← web/src/data/targets.json (bundled into the site's JS, not served)
  data/contracts/api.md        ← contracts/api.md
  data/contracts/flags.md      ← contracts/flags.md
"""
from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlencode

from pydantic import Field

from .client import SkyShiftError
from .server import _json, app, client

# ---------------------------------------------------------------- package data

# Bundled copy → the repo file it mirrors. Run from a checkout (uv run --project mcp), the repo
# file wins, so a contract or targets change shows up without refreshing the copy; an installed
# package falls back to the copy. tests/test_reference.py warns when a copy is stale.
SOURCES = {
    ("targets.json",): ("web", "src", "data", "targets.json"),
    ("contracts", "api.md"): ("contracts", "api.md"),
    ("contracts", "flags.md"): ("contracts", "flags.md"),
}
_REPO = Path(__file__).resolve().parents[3]


def _data_text(*parts: str) -> str:
    src = SOURCES.get(parts)
    if src and (_REPO / "mcp" / "pyproject.toml").is_file() and _REPO.joinpath(*src).is_file():
        return _REPO.joinpath(*src).read_text(encoding="utf-8")
    return resources.files("skyshift_mcp").joinpath("data", *parts).read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def targets() -> list[dict[str, Any]]:
    """SkyShift's featured targets (the bundled copy of web/src/data/targets.json)."""
    return json.loads(_data_text("targets.json"))["targets"]


# ---------------------------------------------------------------- featured targets

def _target_page(t: dict[str, Any]) -> str:
    q = urlencode({"t": t["slug"], "clock": t["clock"], "fov": t["fov"], "det": t["det"]})
    return client().url(f"/?{q}#/explore")


def _match_target(query: str) -> list[dict[str, Any]]:
    q = query.strip().lower()
    exact = [t for t in targets() if q in (t["slug"].lower(), t["name"].lower(), t.get("short", "").lower())]
    return exact or [t for t in targets() if q in t["slug"].lower() or q in t["name"].lower()
                     or q in t.get("short", "").lower()]


@app.tool()
async def featured_targets(
    target: Annotated[str | None, Field(description="Only targets whose slug or name contains this, e.g. 'barnard' or 'ATLAS'. Omit for all")] = None,
) -> str:
    """SkyShift's featured targets: five test objects (Barnard's Star, brown dwarfs Epsilon Indi B and
    WISE 0855−0714, variable stars RW Aurigae and R Draconis), comet 3I/ATLAS and two asteroids. Each has its prepared view (clock,
    field `fov` in arcmin, detector `det`), what to look for and the catalogue expectation. Their
    frames and measurements are precomputed static files, so blink(target=<name>) and
    measure(target=<name>) answer fast."""
    rows = _match_target(target) if target else targets()
    if not rows:
        raise SkyShiftError(f"no featured target matches {target!r}; featured: "
                            + ", ".join(t["name"] for t in targets()))
    return _json({
        "count": len(rows),
        "targets": [{**t, "page": _target_page(t), "static_data": client().url(f"/data/{t['slug']}/index.json")}
                    for t in rows],
        "use": "blink(target=<name>) and measure(target=<name>) read /data/<slug>/; "
               "ra, dec are the prepared field's centre (ICRS degrees)",
    })


# ---------------------------------------------------------------- validation (V1–V11)

CHECK_IDS = tuple(f"V{i}" for i in range(1, 12))

# What each check shows, in plain words (from the notes in validation.json and contracts/api.md).
MEANS = {
    "V1": "The five featured test objects (Barnard's Star, Epsilon Indi B, WISE 0855−0714, RW Aurigae, "
          "R Draconis) are found where catalogues predict them, visit by visit.",
    "V2": "The shift measured between the first and last visit agrees with Gaia DR3's prediction (WISE 0855: "
          "published astrometry). dev_sigma = |shift − predicted| / sigma.",
    "V3": "How precisely a shift is measured: the scatter of quiet Gaia stars' visit-to-visit shifts in the same "
          "cutouts (V2's error bar). The slowest motion seen at 3σ is 3σ / dt.",
    "V4": "Targeted: numbered asteroids 1–60 are detected where NASA's IRSA MOST predicts them; the same test "
          "3′ away (the control) almost never fires.",
    "V5": "Blind: the pipeline's mover search, which knows nothing of asteroids, finds known asteroids and "
          "comets (Minor Planet Center orbits) in the hunt and pilot zones. recall = share of known-object "
          "sessions found; control = predictions moved 2′.",
    "V6": "Fast-star search (S2): cut for the one-day build, not run.",
    "V7": "Brown-dwarf search (S3): cut for the one-day build, not run.",
    "V8": "SPHEREx fluxes agree with 2MASS Ks and WISE W1 (ratio near 1), so spectra are on the right scale; "
          "and how long the live API takes to send the first frame.",
    "V9": "Proper motions fitted from SPHEREx positions agree with Gaia DR3 on average (bias near 0) and their "
          "error bars are honest (pull scatter near 1); `scatter` is the per-star noise in mas/yr.",
    "V10": "Redshifts from SPHEREx's 102-channel spectra (EAZY templates) agree with SDSS DR17 spectroscopic "
           "redshifts for bright galaxies; sigma_NMAD is the typical error in dz/(1+z).",
    "V11": "Asteroid diameters and albedos fitted to SPHEREx D1–D6 light (reflected light + NEATM) compared "
           "with published values.",
}

SUMMARY_KEYS = ("id", "claim", "status", "headline", "value", "unit", "expected")


def _check_id(s: str) -> str:
    s = s.strip().upper()
    return f"V{s}" if s.isdigit() else s


@app.tool()
async def validation(
    check_id: Annotated[str | None, Field(description="One check for full detail (note and rows), e.g. 'V2'. Omit for a summary of all")] = None,
    max_rows: Annotated[int, Field(ge=0, le=500, description="With check_id: at most this many rows")] = 100,
) -> str:
    """How we know SkyShift works: the eleven validation checks V1–V11 from /data/validation.json
    (the site's 'How we know it works' page). Without check_id: every check's status
    (pass/partial/fail/pending), headline numbers, expectation and what it means. With check_id:
    that check in full, with its note (method, row keys) and rows."""
    data = await client().get_json("/data/validation.json")
    checks = data.get("checks") or []
    head = {"generated": data.get("generated"), "data_release": data.get("data_release"),
            "page": client().url("/#/results")}
    if check_id:
        cid = _check_id(check_id)
        c = next((c for c in checks if str(c.get("id", "")).upper() == cid), None)
        if c is None:
            if cid in CHECK_IDS:   # contracts/api.md: an id missing from the file counts as pending
                return _json({**head, "check": {"id": cid, "status": "pending", "in_file": False,
                                                "means": MEANS.get(cid)}})
            raise SkyShiftError(f"no check {check_id!r}; checks: " + ", ".join(c.get("id", "?") for c in checks))
        rows = c.get("rows") or []
        out = {**c, "means": MEANS.get(cid), "rows_total": len(rows), "rows": rows[:max_rows]}
        return _json({**head, "check": out})
    summary = [{**{k: c.get(k) for k in SUMMARY_KEYS}, "means": MEANS.get(str(c.get("id"))),
                "rows": len(c.get("rows") or [])} for c in checks]
    have = {str(c.get("id")) for c in checks}
    summary += [{"id": i, "status": "pending", "in_file": False, "means": MEANS[i]}
                for i in CHECK_IDS if i not in have]
    status: dict[str, int] = {}
    for c in summary:
        status[c["status"]] = status.get(c["status"], 0) + 1
    return _json({**head, "status_counts": status, "checks": summary,
                  "detail": "validation(check_id='V2') gives a check's note and rows"})


# ---------------------------------------------------------------- resources

FACTS = """\
# SkyShift: reference facts

## What SkyShift is
A web tool (https://skyshift.web.app) for NASA Space Apps 2026, challenge "Planet X and SPHEREx".
Paste coordinates and blink any spot of NASA's SPHEREx infrared all-sky survey on two clocks,
measure what moved, and check it against Gaia DR3 and known asteroid and comet positions. A
pipeline also searched two zones of sky and published candidates (/data/candidates.json) for
people to review and vote on. Data: SPHEREx DR1 + QR3 from IRSA (collections spherex_dr1,
spherex_qr3).

## The two clocks
- months: one stack per visit. SPHEREx sees each spot for a visit of 2–4 weeks about every six
  months (about 21 sessions, 55–60 exposures). Shows stars with high proper motion and brown
  dwarfs. Fields 5′ (2.05″/px, 3× oversampled) or 15′ (3.08″/px, 2×).
- hours: single exposures inside one visit. An exposure is 113 s; a session is 1–5 exposures
  2.1 min apart. Shows asteroids, comets and other fast movers. Fields 20′ (native 6.15″/px) or
  60′ (12.3″/px, binned 2×). A mover shifts only a few arcsec between exposures: zoom in.

## The visit rule
Sort the exposure times at a position; start a new visit where the gap exceeds 60 days; cut any
visit longer than 75 days (near the ecliptic poles) into ceil(days / 30) equal parts. A session
is a run of exposures with gaps under 10 minutes. Visits are numbered 1, 2, … from the earliest at
that position. There are no fixed "passes": the dates depend on the position.
Time of an exposure: MJD-AVG from the IMAGE header (UTC, the exposure's midpoint).

## Instrument
- 6 detectors, 102 spectral channels in all (17 per detector):
  D1 0.75–1.1 µm, D2 1.1–1.6, D3 1.6–2.4 (SWIR); D4 2.4–3.8, D5 3.8–4.4, D6 4.4–5.0 (MWIR).
- Pixel 6.15″ (6.152″ measured). PSF FWHM 7.53″. Images 2040 × 2040 pixels.
- D1+D4, D2+D5 and D3+D6 are exposed at the same moment: a change between the two detectors of
  a pair is colour, not motion. Compare brightness only within one detector.
- Saturation (50% of stars saturated at the peak): D1 J_AB 10.5, D2 J_AB 10.8, D3 K_AB 11.0,
  D4 W1_AB 11.1, D5 W2_AB 9.6, D6 W2_AB 9.0. Barnard's Star, RW Aurigae and R Draconis saturate:
  their positions come from the wings.

## Units
Positions ICRS degrees; field size and search radius arcmin; shifts and position errors arcsec;
proper motion mas/yr (Gaia's pmra already × cos dec), rates of movers ″/h; time MJD (UTC); flux µJy;
wavelength µm; frame values MJy/sr (1 MJy/sr = 889 µJy in a 6.15″ pixel). Position angles are
degrees east of north, 0–360. Photometry: an r = 2 px aperture holds 96.8% of a star's light
(aperture correction 1.033); fluxes agree with 2MASS to 2–4%. NASA's zodiacal-light model (ZODI)
is not subtracted from the images.

## Pixel flags (contracts/flags.md)
Masked for detection and photometry: TRANSIENT, OVERFLOW, SUR_ERROR, NONFUNC, DICHROIC,
MISSING_DATA, HOT, COLD, PHANMISS, NONLINEAR, PERSIST, STREAK, SNOWBALL. Recorded, not masked
(they appear in a candidate's `flags`, lower case): OUTLIER, CROSSTALK, GHOST, GHOST_FPA,
GHOST_EXT, BLOOM, HALO. Never masked: SOURCE (set on most pixels) and FULLSAMPLE. OVERFLOW or
NONLINEAR in a source's core means saturated.

## Candidates (/data/candidates.json; full vocabulary in skyshift://contracts/api)
- kind: `mover` ("Fast mover", moves within hours: an uncatalogued asteroid or comet?, hours
  clock); `high_pm` ("Moving star", moves between visits: a nearby star or brown dwarf?, months
  clock; only rows that passed every pipeline check are listed); `spectral` ("Odd spectrum":
  molecule bands worth a look, months clock).
- score 0–1: higher = more promising. Doubts become `reasons` and lower the score; nothing is
  dropped for being doubtful.
- grade (movers only; null for high_pm and spectral): A = candidate mover, on a track over ≥ 2
  sessions with motion ≥ 5σ and no catalogue or known-object match. B = passes every vetting gate,
  but its motion is not shown (most are 2 dots of one session, and at S/N 7–9 the position error
  exceeds the motion between exposures): an "unexplained one-session source". C = fails a vetting
  gate or has a catalogue source within 6″: most likely an artefact or a catalogued source (score
  × 0.1).
- reasons: why it was listed and what to be careful about. Evidence: `two_exposures`,
  `two_detectors` (one moment, a simultaneous detector pair), `pm_fit`, `pm_pair`, `vetted`,
  `catwise_pm_agrees`. Cautions (mover): `known_object_near`, `motion_not_significant`,
  `bad_pixels_near`, `outlier_flag`, `detector_excess`, `repeat_position`, `gaia_unchecked`,
  `catwise_unchecked`, and the vetting gates that make a C (`not_vetted`, `hot_pixel`,
  `bad_column`, `edge`, `masked_peak`, `sharp_outlier`, `very_sharp`, `flux_ratio`, `red_photo`,
  `2mass`, `allwise`). Cautions (high_pm): `short_span`, `few_dots`, `bad_fit`, `over_pm_cap`,
  `gaia_pm_disagrees`, `catwise_pm_disagrees`, `at_control_level`, `implausible_pm`. Spectral:
  `no_gaia`, `gaia_nearby`, `class_star`, `class_galaxy`, `class_unsure`, `variable`.
- labels (high_pm, spectral): `red`, `brown_dwarf_like`, `ch4`, `h2o_ice`, `co2_ice`, `co_ice`,
  `h2o_vapour`, `co_23`, `pah_33`. Leads, not classifications.
- flags: record-only artefact bits near it (`outlier`, `crosstalk`, `ghost`, `ghost_fpa`,
  `ghost_ext`, `bloom`, `halo`).
- nearest_known_arcsec (movers): the nearest known asteroid or comet predicted in the same
  session; null = none within 600″.
- why: one plain sentence on why it is listed.

## Honest limits
- Candidates are leads for people to check, not discoveries. Most are artefacts or known objects.
  Never call one a new planet or a discovery.
- The pipeline searched two zones only: hunt (RA 120.5–130.5, Dec +14.4 to +24.4, 3,692 photos)
  and pilot (inside it). Elsewhere SkyShift offers the live blink and measurement, not a search.
- Shift precision on the months clock: 0.36–0.9″ per field (V3), so motions slower than about
  1″/yr are not seen at 3σ in one year. Per-star proper motions from the zone pipeline scatter
  about 145 mas/yr against Gaia (V9). (Numbers of 14 Nov 2026; the validation tool has the
  current ones.)
- Known asteroids and comets come from Minor Planet Center orbits (propagated with kete): an
  object missing from those orbits cannot be matched.
- Saturated stars are measured on their wings. Ghosts, halos, crosstalk and the other
  record-only flags mark pixels that can mimic a source: a candidate with `flags` needs care.
- Validation checks V6 (fast stars) and V7 (brown dwarfs) were cut and stay pending.
"""


@app.resource("skyshift://facts", name="facts", mime_type="text/markdown",
              description="SkyShift in one page: the two clocks, detectors D1–D6, pixel and PSF, the visit "
                          "rule, units, flags, what candidate kinds, grades and reasons mean, honest limits")
def facts() -> str:
    return FACTS


@app.resource("skyshift://contracts/api", name="contract-api", mime_type="text/markdown",
              description="contracts/api.md: every SkyShift endpoint and static file, with field names, "
                          "units, the visit rule, validation.json and candidates.json vocabularies")
def contract_api() -> str:
    return _data_text("contracts", "api.md")


@app.resource("skyshift://contracts/flags", name="contract-flags", mime_type="text/markdown",
              description="contracts/flags.md: what SkyShift does with each SPHEREx FLAGS bit "
                          "(mask, record, never mask) and the saturation bits")
def contract_flags() -> str:
    return _data_text("contracts", "flags.md")


# ---------------------------------------------------------------- prompts

def _q(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


VERDICTS = """\
End with a verdict, exactly one of:
- likely artefact: flags, failed gates (grade C), detector_excess, repeat_position, a sharp or
  single-pixel look, or nothing visible in the blink;
- known object: a known asteroid or comet predicted at the right place and time;
- known star motion: Gaia or CatWISE already lists this motion;
- worth a human look: nothing above explains it.
Quote the candidate's `why`, `reasons`, `flags` and `grade` (or say they are empty or null), and
name what is uncertain (S/N, motion significance, unchecked catalogues, saturation, crowding).
Never call it a discovery or a new planet: at most, a lead for people to check and vote on."""


@app.prompt()
def explain_candidate(candidate_id: str) -> str:
    """Investigate one SkyShift candidate with the tools and give an honest verdict."""
    cid = _q(candidate_id)
    return f"""\
Explain SkyShift candidate {cid}. Read the resource skyshift://facts if you need the vocabulary.

1. get_candidate(candidate_id={cid}): note kind, clock, det, mjd, ra, dec, score, snr, grade,
   reasons, flags, why, nearest_known_arcsec, gaia_match, catwise_match, rate_arcsec_h or
   pm_mas_yr, labels and blink_page.
2. Look at it:
   - hours clock (mover): blink(candidate_id={cid}, zoom_arcmin=2). A real mover is a dot that
     changes place from frame to frame and leaves a black-white pair in the difference panel; a
     dot that stays put, sits on one pixel, or appears in one frame only is suspect.
   - months clock (high_pm, spectral): blink(candidate_id={cid}) shows the 5′ field; for wider
     context blink(ra=<ra>, dec=<dec>, clock="months", fov=15, det=<det>).
   Describe what you see in the image, and the epochs' flag_frac, sat_frac and known_objects.
3. Movers: known_objects(ra=<ra>, dec=<dec>, mjd=<mjd>, radius_arcmin=15). Is anything predicted
   near it at that time? Compare the distance with nearest_known_arcsec.
4. Months clock (high_pm, spectral): measure(ra=<ra>, dec=<dec>). Compare shift_arcsec, pa_deg
   and rate_arcsec_yr (with sigma_arcsec) with the candidate's pm_mas_yr and pa_deg, and with
   Gaia's prediction (predicted_arcsec). If measure is null, say why (measure_reason).
5. If a tool named `spectrum` is available, call it at the candidate's position and check the
   molecule bands its labels claim; otherwise measure(ra=<ra>, dec=<dec>, spectrum=true) gives
   the spectrum points.

{VERDICTS}
Finish with the candidate's page (blink_page) so a person can vote on it."""


@app.prompt()
def triage_movers(limit: int = 5) -> str:
    """Rank SkyShift's best unexplained fast movers and say which deserve a human vote."""
    n = int(limit)
    return f"""\
Triage SkyShift's fast movers. Read the resource skyshift://facts if you need the vocabulary.

1. top_candidates(kind="mover", exclude_flagged=true, limit={n * 3}, sort="score"). Drop grade C
   (failed vetting gates: most likely artefacts) and keep the best {n}, grade A first.
   If fewer than {n} remain, say so; do not pad the list with grade C or flagged movers.
2. For each one, briefly:
   - blink(candidate_id=<id>, zoom_arcmin=2): does a dot move between frames (a black-white pair
     in the difference panel), or does it stay put or appear once?
   - known_objects(ra=<ra>, dec=<dec>, mjd=<mjd>, radius_arcmin=15): is a known asteroid or
     comet predicted there?
   - Quote its `why`, `grade`, `reasons` (cautions such as motion_not_significant,
     detector_excess, repeat_position, known_object_near) and snr.
3. Rank them: which deserve a human vote on the site, and why; which look like artefacts or known
   objects. Give each one's verdict (likely artefact / known object / worth a human look) in one
   line, and its page: get_candidate(candidate_id=<id>) gives blink_page; votes are cast at
   {client().url("/#/c/")}<id>.

Grade B movers' motion is usually not significant (two dots of one session): say so. These are
leads for people to check, never discoveries."""


@app.prompt()
def check_my_target(target: str) -> str:
    """Check a user's own object (a name or coordinates) with SkyShift: motion, blink, nearby candidates."""
    t = _q(target)
    return f"""\
Check {t} with SkyShift. Read the resource skyshift://facts if you need the vocabulary.

1. resolve(query={t}) for its ICRS position. If it is a featured target (featured_targets() lists
   them; resolve gives a `slug`), say so: its data is precomputed.
2. measure(target={t}): the shift between the first and last visit (shift_arcsec, pa_deg,
   rate_arcsec_yr, sigma_arcsec, n_ref_stars), Gaia's prediction (predicted_arcsec) and the
   spectrum. If measure is null, explain measure_reason (and measure_hint for moved_away).
3. blink(target={t}, clock="months"): look at the visits about six months apart and the last − first
   difference. A star that moved leaves a black-white pair; a variable star changes brightness
   in place. For an asteroid or comet use blink(target={t}, clock="hours", zoom_arcmin=2) instead.
4. candidates_near(target={t}, radius_arcmin=30): has SkyShift's pipeline listed anything nearby?
   (It searched two zones only; outside them an empty list means "not searched", not "nothing there".)
5. Context on precision: validation(check_id="V3") (shift error floor) and validation(check_id="V2")
   (motions of test stars against Gaia). Compare the target's sigma_arcsec with them.

Report what SkyShift measured, how it compares with catalogues, what is uncertain (saturation,
crowding, few reference stars, short time span) and a link to blink it on the site:
{client().url("/")}?ra=<ra>&dec=<dec>&clock=months#/explore (for a featured target, the page
link from featured_targets). Do not claim anything the measurements don't show."""
