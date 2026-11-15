"""SkyShift MCP server (stdio by default). Tools wrap the public site: static files first, then /api/.

Run:  uv run --project mcp skyshift-mcp            (stdio, for Claude Code / Claude Desktop)
      uv run --project mcp skyshift-mcp --http     (streamable HTTP on 127.0.0.1:8765/mcp)
Site: SKYSHIFT_BASE (default https://skyshift.web.app).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import unicodedata
from typing import Annotated, Any, Literal
from urllib.parse import urljoin

from mcp.server.fastmcp import FastMCP, Image
from pydantic import Field

from . import candidates as cands
from . import frames as fr
from .client import Client, SkyShiftError

INSTRUCTIONS = """\
SkyShift blinks NASA SPHEREx infrared sky images on two clocks: visits about six months apart
(`months`: stars with high proper motion, brown dwarfs) and exposures minutes apart inside one
visit (`hours`: asteroids, comets, fast movers). It checks motion against Gaia DR3 and against
known asteroids and comets predicted from Minor Planet Center orbits.

Units: positions ICRS degrees; field size and search radius arcmin; shifts arcsec; proper motion
mas/yr; time MJD (UTC); flux µJy; wavelength µm. Detectors D1–D6 cover 0.75–5.0 µm; D1+D4,
D2+D5, D3+D6 are exposed at the same moment, so a change between those pairs is colour, not motion.
Never call a dot a new planet: candidates are leads for people to check, most are artefacts or
known objects. Quote each candidate's `why`, `reasons` and `flags` and say what is uncertain.

Typical flow: top_candidates or candidates_near → get_candidate → blink (look at the image) →
known_objects at the candidate's mjd → measure for months-clock motion.
"""

app = FastMCP("skyshift", instructions=INSTRUCTIONS)
_client: Client | None = None


def client() -> Client:
    global _client
    if _client is None:
        _client = Client()
    return _client


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1)


def _norm_name(s: str) -> str:
    """Lowercase, Greek epsilon spelled out, only letters and digits (as the server's resolve.py)."""
    s = unicodedata.normalize("NFKC", s).lower().replace("ε", "eps").replace("epsilon", "eps")
    return re.sub(r"[^a-z0-9]", "", s)


def _featured(slug: str | None) -> dict[str, Any] | None:
    """A featured target of targets.json by slug."""
    from .tools_reference import targets  # registers on `app` after this module: import when used
    return next((t for t in targets() if t["slug"] == slug), None) if slug else None


def _featured_by_name(query: str) -> dict[str, Any] | None:
    """A featured target whose slug, name or short name is `query`, ignoring case, spaces and
    punctuation; an asteroid also by its name without the number ('Elpis' for '(59) Elpis')."""
    from .tools_reference import targets
    q = _norm_name(query)
    if not q:
        return None
    for t in targets():
        names = [t["slug"], t["name"], t.get("short") or "",
                 re.sub(r"^\s*(\(\d+\)|comet)\s*", "", t["name"], flags=re.I)]
        if q in {_norm_name(n) for n in names if n}:
            return t
    return None


async def resolve_name(query: str) -> dict[str, Any]:
    """/api/resolve, else a featured target by name (case-insensitive), in /api/resolve's shape with
    the target's prepared field centre. /api/resolve knows the five test objects and 3I/ATLAS by
    several names, but not the featured asteroids; a featured name also works when it is down."""
    try:
        return await client().resolve(query)
    except SkyShiftError:
        t = _featured_by_name(query)
        if t is None:
            raise
        return {"q": query, "name": t["name"], "ra": t["ra"], "dec": t["dec"], "slug": t["slug"], "source": "builtin"}


async def _position(target: str | None, ra: float | None, dec: float | None) -> dict[str, Any]:
    """{ra, dec, name, slug} from a name / coordinate string, or from ra and dec."""
    if target:
        r = await resolve_name(target)
        return {"ra": r["ra"], "dec": r["dec"], "name": r.get("name"), "slug": r.get("slug")}
    if ra is None or dec is None:
        raise SkyShiftError("give a target (name or coordinates) or both ra and dec")
    return {"ra": ra, "dec": dec, "name": None, "slug": None}


# ---------------------------------------------------------------- candidates

@app.tool()
async def top_candidates(
    kind: Annotated[Literal["mover", "high_pm", "spectral", "shape"] | None,
                    Field(description="mover = moves within hours (asteroid or comet?); high_pm = moves between visits (nearby star or brown dwarf?); spectral = odd spectrum; shape = stretched like two objects where catalogues list one. Omit for all")] = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 10,
    sort: Annotated[Literal["score", "snr", "rate"],
                    Field(description="rate: movers by rate_arcsec_h (″/h), then high_pm by pm_mas_yr (mas/yr); the units are never compared")] = "score",
    min_score: Annotated[float | None, Field(ge=0, le=1)] = None,
    min_snr: float | None = None,
    grade: Annotated[Literal["A", "B", "C"] | None, Field(description="Movers' vetting grade; C = failed a gate, likely artefact. A file written before the vetting has no grades (`notes` says so)")] = None,
    zone: str | None = None,
    det: Annotated[int | None, Field(ge=1, le=6)] = None,
    exclude_gaia: Annotated[bool, Field(description="Drop candidates with a Gaia DR3 source at their position")] = False,
    exclude_flagged: Annotated[bool, Field(description="Drop candidates with artefact flags (ghost, crosstalk, halo, …) nearby")] = False,
    min_known_arcsec: Annotated[float | None, Field(description="Movers only: keep those whose nearest known asteroid/comet is at least this far (arcsec)")] = None,
) -> str:
    """The most promising SkyShift candidates from the pipeline's published list (/data/candidates.json),
    filtered and ranked. Returns counts, the matching total and a compact row per candidate."""
    data = await client().candidates()
    rows = cands.select(data["candidates"], kind=kind, zone=zone, det=det, min_score=min_score,
                        min_snr=min_snr, grade=grade, exclude_gaia=exclude_gaia,
                        exclude_flagged=exclude_flagged, min_known_arcsec=min_known_arcsec)
    rows = cands.rank(rows, sort)
    notes = []
    if grade and not cands.has_grades(data["candidates"]):
        notes.append(f"this file has no grades: candidates.json (generated {data.get('generated')}) was written "
                     f"before the movers' vetting, so grade={grade} matches nothing. Drop the grade filter")
    elif grade and kind not in (None, "mover"):
        notes.append("grades are given to movers only")
    if sort == "rate":
        notes.append(cands.RATE_NOTE)
    return _json({
        "generated": data.get("generated"), "data_release": data.get("data_release"),
        "zones": data.get("zones"), "counts_in_file": data.get("counts"),
        **({"notes": notes} if notes else {}),
        "matching": len(rows), "shown": min(limit, len(rows)),
        "candidates": [cands.summarise(c) for c in rows[:limit]],
    })


@app.tool()
async def candidates_near(
    target: Annotated[str | None, Field(description="Name or coordinates in any format, e.g. 'Barnard's Star' or '17h57m48s +04d41m36s'")] = None,
    ra: float | None = None,
    dec: float | None = None,
    radius_arcmin: Annotated[float, Field(gt=0, le=600)] = 30.0,
    kind: Literal["mover", "high_pm", "spectral", "shape"] | None = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
) -> str:
    """SkyShift candidates within a radius of a position, nearest first (cone search)."""
    pos = await _position(target, ra, dec)
    data = await client().candidates()
    rows = cands.select(data["candidates"], kind=kind, near=(pos["ra"], pos["dec"], radius_arcmin))
    rows = cands.rank(rows, "distance")
    return _json({"center": pos, "radius_arcmin": radius_arcmin, "matching": len(rows),
                  "candidates": [{**cands.summarise(c), "distance_arcmin": c["distance_arcmin"]}
                                 for c in rows[:limit]]})


@app.tool()
async def get_candidate(candidate_id: Annotated[str, Field(description="A candidates.json id, e.g. 'hunt-664250069368-87801613'")]) -> str:
    """Everything SkyShift knows about one candidate: the full record, with a link to its page."""
    data = await client().candidates()
    c = cands.find(data["candidates"], candidate_id)
    if c is None:
        raise SkyShiftError(f"no candidate {candidate_id!r} in candidates.json (generated {data.get('generated')})")
    out = dict(c)
    if c.get("frames_url"):
        out["frames_url"] = urljoin(client().url("/data/candidates.json"), c["frames_url"])
    fov = 20 if c["clock"] == "hours" else 5
    out["blink_page"] = client().url(
        f"/?ra={c['ra']:.5f}&dec={c['dec']:.5f}&clock={c['clock']}&fov={fov}&det={c['det']}#/explore")
    return _json(out)


# ---------------------------------------------------------------- blink

async def _frames_source(target: str | None, ra: float | None, dec: float | None,
                         candidate_id: str | None, clock: str | None, fov: int | None,
                         det: int | None) -> tuple[str, dict[str, Any] | None, int | None, dict[str, Any]]:
    """(url of a frames JSON, live params or None, detector, note). Static files first:
    a candidate's cand/<id>/frames.json, a featured target's /data/<slug>/ set, else /api/frames."""
    c = client()
    note: dict[str, Any] = {}
    if candidate_id:
        data = await c.candidates()
        cand = cands.find(data["candidates"], candidate_id)
        if cand is None:
            raise SkyShiftError(f"no candidate {candidate_id!r}")
        note["candidate"] = cands.summarise(cand)
        det = det or cand.get("det")
        if cand.get("frames_url") and (clock in (None, cand["clock"])):
            return urljoin(c.url("/data/candidates.json"), cand["frames_url"]), None, det, note
        ra, dec, clock = cand["ra"], cand["dec"], clock or cand["clock"]
    else:
        pos = await _position(target, ra, dec)
        ra, dec = pos["ra"], pos["dec"]
        note["target"] = pos
        featured = _featured(pos.get("slug"))
        if pos.get("slug"):
            idx = await c.get_json(f"/data/{pos['slug']}/index.json")
            sets = idx.get("frames") or []
            pick = [s for s in sets if (clock is None or s["clock"] == clock) and (fov is None or s["fov"] == fov)]
            if clock is None and fov is None:
                pick = [s for s in sets if s["file"] == idx.get("own")] or pick
            elif fov is None:  # a clock but no field: that clock's default field (contracts/api.md)
                default = {"months": 5, "hours": 20}.get(clock)
                pick.sort(key=lambda s: s["fov"] != default)
            pick = [s for s in pick if det is None or det in s.get("dets", [])] or []
            if pick:
                s = pick[0]
                det = det or _best_det(s.get("dets", []), featured)
                return c.url(f"/data/{pos['slug']}/{s['file']}"), None, det, note
        det = det or (featured or {}).get("det")
    params = {"ra": ra, "dec": dec, "clock": clock or "months", "fov": fov, "det": det or 2}
    return c.url("/api/frames"), params, det or 2, note


def _best_det(dets: list[int], featured: dict[str, Any] | None) -> int:
    """The detector to show of those in a frames set: the featured target's own `det` (targets.json:
    WISE 0855−0714 is invisible short of 3 µm, so D6), else one of its `dets`, else the nearest to
    its `det`; without a featured target D2, else the first."""
    if featured and featured.get("det") and dets:
        want = featured["det"]
        for d in [want, *(featured.get("dets") or [])]:
            if d in dets:
                return d
        return min(dets, key=lambda d: (abs(d - want), d))
    return 2 if 2 in dets else dets[0]


def _fmt_dt(days: float) -> str:
    h = abs(days) * 24.0
    if h < 1.0:
        return f"{h * 60.0:.1f} min"
    return f"{h:.1f} hours" if h < 48.0 else f"{h / 24.0:.1f} days"


def _session_note(cand: dict[str, Any] | None, data: dict[str, Any], det: int | None, live: bool) -> dict[str, Any] | None:
    """For an hours-clock candidate: how far the nearest frame of `det` is from the find's own time,
    and a warning when the frames may not, or do not, show the find's session. None otherwise."""
    if not cand or cand.get("clock") != "hours" or data.get("clock") != "hours" or not cand.get("mjd"):
        return None
    eps = [e for e in data.get("epochs") or [] if det is None or e.get("det") == det]
    if not eps:
        return None
    mjd = float(cand["mjd"])
    near = min(eps, key=lambda e: abs(e["mjd_mid"] - mjd))
    dt = near["mjd_mid"] - mjd
    out: dict[str, Any] = {"find_mjd": mjd, "nearest_epoch_id": near["id"],
                           "nearest_frame_minutes": round(dt * 1440.0, 2)}
    if abs(dt) > 1.0 / 24.0:
        out["warning"] = (f"The find's own exposures are NOT in these frames: the nearest frame is "
                          f"{_fmt_dt(dt)} {'after' if dt > 0 else 'before'} the find (MJD {mjd:.4f}). "
                          "The mover will not be seen here; these frames show another session.")
    elif live:
        out["warning"] = (f"Live /api/frames has no time parameter: its hours frames show the visit with the most "
                          f"exposures, which may not include the find's session. Here the nearest frame is "
                          f"{_fmt_dt(dt)} from the find (MJD {mjd:.4f}).")
    return out


def _measured_summary(m: dict[str, Any] | None, reason: str | None) -> dict[str, Any] | None:
    if not m:
        return {"available": False, "reason": reason} if reason else None
    shifts = sorted(m.get("shifts") or [], key=lambda s: -(s.get("pm_snr") or 0))[:5]
    return {"zone": m.get("zone"), "stage": m.get("stage"), "counts": m.get("counts"),
            "tracks": [{k: t.get(k) for k in ("id", "class", "suspect_id", "known_name", "evidence")}
                       for t in (m.get("tracks") or [])][:10],
            "top_shifts": shifts}


@app.tool()
async def blink(
    target: Annotated[str | None, Field(description="Name or coordinates in any format")] = None,
    ra: float | None = None,
    dec: float | None = None,
    candidate_id: Annotated[str | None, Field(description="Blink a candidate from candidates.json (uses its clock, detector and static frames; an hours-clock candidate shows the frames nearest its own time, and `find_session` says how near they are)")] = None,
    clock: Annotated[Literal["months", "hours"] | None,
                     Field(description="months = one stack per visit (~6 months apart); hours = single exposures of one visit")] = None,
    fov: Annotated[int | None, Field(description="Field in arcmin: months 5 or 15; hours 20 or 60")] = None,
    det: Annotated[int | None, Field(ge=1, le=6, description="Detector 1–6 (default: the candidate's or featured target's own detector, e.g. D6 for WISE 0855−0714; else D2, 1.1–1.6 µm)")] = None,
    max_frames: Annotated[int, Field(ge=2, le=8)] = 4,
    zoom_arcmin: Annotated[float | None, Field(gt=0, description="Crop to this many arcmin around the centre. Use 1–3 for an hours-clock mover: it moves only a few arcsec in minutes")] = None,
    overlays: Annotated[bool, Field(description="Draw Gaia stars, known asteroids/comets and SkyShift's measured detections on the frames, with a legend")] = True,
    max_gaia_g: Annotated[float, Field(ge=0, le=25, description=f"Circle Gaia DR3 stars down to this G magnitude (at most {fr.GAIA_MAX_MARKS} a frame, brightest first)")] = 19.0,
    max_known_v: Annotated[float, Field(ge=0, le=40, description="Mark known asteroids/comets down to this V magnitude; null V is always marked")] = 18.0,
    animate: Annotated[bool, Field(description="Also return a looping animated GIF of the frames (same stretch and marks, 0.7 s a frame)")] = False,
) -> list:
    """Look at a spot of the SPHEREx sky over time. Returns the epochs' metadata and ONE image: the
    frames left to right in time (north up, east left, same stretch), then a last − first difference
    (white = brighter later, black = fainter, grey = unchanged). Orange ticks mark the field centre;
    purple = no data. Something that moved shows as a black-white pair in the difference panel.

    Marks on the frames (overlays=True; none on the difference panel; a legend strip underneath):
    thin cyan circles = Gaia DR3 stars moved to each frame's date with their proper motion (bigger
    = brighter, dimmer cyan = G > 16), so a high-proper-motion star's circle follows it from frame
    to frame and a blob without a circle is not a Gaia star down to max_gaia_g (crowded fields show
    the brightest 150 a frame: zoom in for all); green diamonds + name = known asteroids/comets
    predicted at that frame's time, down to max_known_v (MPC orbits; `source` in known_objects); SkyShift's measured detections, by class: red thick square = suspect
    (a pipeline lead), yellow thick square = mover vetted out, green square = matched a known object,
    magenta x = artefact, blue + = Gaia star, grey dot = other. `overlays` in the metadata counts
    the marks drawn per frame (and Gaia stars / known objects left out as fainter than the limits).
    animate=True adds a second image: an animated GIF of the same frames (blink by eye)."""
    url, params, det, note = await _frames_source(target, ra, dec, candidate_id, clock, fov, det)
    c = client()
    data = await (c.api("/api/frames", **params) if params else c.get_json(url))
    all_det = [e for e in data["epochs"] if e.get("det") == det]
    if not all_det:
        have = sorted({e.get("det") for e in data["epochs"]})
        raise SkyShiftError(f"no epochs for detector {det} here (detectors with data: {have})")
    session = _session_note(note.get("candidate"), data, det, live=bool(params))
    # an hours-clock candidate: the frames nearest its own time, not an even spread over the set
    shown = fr.pick_epochs(all_det, det, max_frames, around_mjd=session["find_mjd"] if session else None)
    bins = await asyncio.gather(*(c.get_bytes(urljoin(url, e["data_url"])) for e in shown))
    full = [fr.decode(b, data["size"], e["scale"], e["zero"]) for b, e in zip(bins, shown)]
    arrays = [fr.crop(f, data["pixel_arcsec"], zoom_arcmin) for f in full]
    labels = [fr.label_for(e) for e in shown]
    marks = legend = None
    if overlays:
        origin = fr.crop_origin(full[0].shape, data["pixel_arcsec"], zoom_arcmin)
        marks = [fr.epoch_marks(data, e, a.shape, origin, max_gaia_g=max_gaia_g, max_known_v=max_known_v)
                 for e, a in zip(shown, arrays)]
        legend = fr.legend(max_gaia_g=max_gaia_g, max_known_v=max_known_v,
                           gaia_capped=any(m.gaia_capped for m in marks),
                           measured=bool(data.get("measured")), measured_reason=data.get("measured_reason"),
                           with_diff=len(arrays) >= 2)
    png = fr.contact_sheet(arrays, labels, marks=marks, legend_items=legend)
    ids = {e["id"] for e in shown}
    meta = {
        **note,
        **({"find_session": session} if session else {}),
        "source": "live /api/frames" if params else "static " + url,
        "clock": data["clock"], "det": det, "center": data["center"],
        "pixel_arcsec": data["pixel_arcsec"], "size": data["size"],
        "field_arcmin": round(data["size"][0] * data["pixel_arcsec"] / 60, 2),
        "image_field_arcmin": round(arrays[0].shape[1] * data["pixel_arcsec"] / 60, 2),
        "epochs_for_det": len(all_det), "epochs_shown": [
            {k: e.get(k) for k in ("id", "kind", "mjd_mid", "mjd_start", "mjd_end", "um_min", "um_max",
                                   "n_exposures", "flag_frac", "sat_frac")} for e in shown],
        "gaia_stars_in_field": len(data.get("gaia") or []),
        "known_objects": [k for k in (data.get("known") or []) if k.get("epoch_id") in ids],
        "measured": _measured_summary(data.get("measured"), data.get("measured_reason")),
        "overlays": _overlay_counts(shown, marks, max_gaia_g, max_known_v),
        "data_release": data.get("data_release"), "archive_checked": data.get("archive_checked"),
    }
    out = [_json(meta), Image(data=png, format="png")]
    if animate:
        out.append(Image(data=fr.animation(arrays, labels, marks, legend), format="gif"))
    return out


def _overlay_counts(shown: list[dict[str, Any]], marks: list[fr.Marks] | None,
                    max_gaia_g: float, max_known_v: float) -> dict[str, Any] | None:
    """Marks drawn per frame and in all, for blink's metadata. None when overlays are off."""
    if marks is None:
        return None
    per = [{"epoch_id": e["id"], **m.counts()} for e, m in zip(shown, marks)]
    total = {"gaia": sum(p["gaia"] for p in per), "known": sum(p["known"] for p in per),
             "measured": {k: sum(p["measured"][k] for p in per) for k in fr.CLASS_COLOR}}
    return {"max_gaia_g": max_gaia_g, "max_known_v": max_known_v, "drawn": total, "per_frame": per}


# ---------------------------------------------------------------- measure, catalogues

@app.tool()
async def measure(
    target: Annotated[str | None, Field(description="Name or coordinates in any format")] = None,
    ra: float | None = None,
    dec: float | None = None,
    spectrum: Annotated[bool, Field(description="Include the full SPHEREx spectrum points")] = False,
) -> str:
    """SkyShift's measurement at a position: shift between the first and last visit (arcsec, position
    angle, rate in ″/yr with its sigma, Gaia's prediction), the Gaia DR3 match, and the spectrum."""
    pos = await _position(target, ra, dec)
    c = client()
    if pos.get("slug"):
        data = await c.get_json(f"/data/{pos['slug']}/object.json")
        source = f"static /data/{pos['slug']}/object.json"
    else:
        data = await c.api("/api/object", ra=pos["ra"], dec=pos["dec"])
        source = "live /api/object"
    spec = data.get("spectrum") or []
    out = {k: v for k, v in data.items() if k != "spectrum"}
    out["position"] = pos
    out["source"] = source
    out["spectrum_points"] = len(spec)
    if spec:
        out["spectrum_um_range"] = [min(p["um"] for p in spec), max(p["um"] for p in spec)]
    if spectrum:
        out["spectrum"] = spec
    return _json(out)


@app.tool()
async def known_objects(
    ra: float,
    dec: float,
    mjd: Annotated[float, Field(description="Time, MJD UTC (a candidate's mjd, or an epoch's mjd_mid)")],
    radius_arcmin: Annotated[float, Field(gt=0, le=120)] = 10.0,
) -> str:
    """Known asteroids and comets predicted near a position at a time (Minor Planet Center orbits,
    propagated with kete). Use it to check whether a mover is already catalogued."""
    return _json(await client().api("/api/known", ra=ra, dec=dec, r=radius_arcmin, mjd=mjd))


@app.tool()
async def gaia_stars(
    ra: float,
    dec: float,
    radius_arcmin: Annotated[float, Field(gt=0, le=30)] = 2.0,
    limit: Annotated[int, Field(ge=1, le=500)] = 50,
) -> str:
    """Gaia DR3 stars near a position at epoch J2016.0 (pmra, pmdec in mas/yr; parallax in mas),
    brightest first."""
    data = await client().api("/api/gaia", ra=ra, dec=dec, r=radius_arcmin)
    stars = sorted(data.get("gaia") or [], key=lambda s: s.get("g") if s.get("g") is not None else 99)
    return _json({"total": len(stars), "shown": min(limit, len(stars)), "gaia": stars[:limit]})


@app.tool()
async def resolve(query: Annotated[str, Field(description="A built-in name (e.g. 'Barnard's Star', '3I/ATLAS') or coordinates in any format")]) -> str:
    """Turn a name or coordinate string into ICRS degrees. `slug` is set for SkyShift's featured targets
    (the asteroids too, e.g. 'Elpis': their position is the prepared field's centre)."""
    return _json(await resolve_name(query))


# Tools, resources and prompts in their own modules register on `app` when imported. One module
# per work stream, so parallel branches don't edit the same lines here.
from . import tools_reference, tools_data, tools_spectrum  # noqa: E402,F401


def main() -> None:
    import logging
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one INFO line per request otherwise
    p = argparse.ArgumentParser(description="SkyShift MCP server")
    p.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    p.add_argument("--port", type=int, default=8765)
    a = p.parse_args()
    if a.http:
        app.settings.port = a.port
        app.run(transport="streamable-http")
    else:
        app.run()


if __name__ == "__main__":
    main()
