"""More of SkyShift's data: the measured layer (detections, tracks, shifts), batch checks of many
positions, and community votes (Firestore, read-only).

Register with `@app.tool()`, `@app.resource()`, `@app.prompt()` on the shared app."""
from __future__ import annotations

import asyncio
import os
import time
import weakref
from collections import defaultdict
from typing import Annotated, Any, Literal
from urllib.parse import quote, urlencode

from pydantic import Field

from . import candidates as cands
from .client import Client, SkyShiftError
from .server import _frames_source, _json, app, client

# ---------------------------------------------------------------- measured layer (contracts/api.md v3.3)

CLASSES = ("star", "known", "mover", "suspect", "artifact", "other")
Class = Literal["star", "known", "mover", "suspect", "artifact", "other"]

STAGE_MEANING = {
    "final": "The zone's final pass: calibrated, checked against Gaia and WISE.",
    "provisional": "Overnight results, checked against Gaia only; suspect lists are several times "
                   "longer than the final ones.",
    "extracted": "Only the photos' dots exist (no zone has combined them yet): detections are the dots "
                 "alone, classed star / known / artifact / other; no source_id, tracks or shifts.",
}
REASON_MEANING = {
    "not_processed": "No pipeline zone covers this position yet.",
    "processing": "A zone covers it, but its results are not combined yet.",
    "no_dots": "The zone has results, but none of this response's photos has a dot file.",
    "unavailable": "The results could not be read in time. Asking again helps (not cached).",
}
CLASS_MEANING = {
    "star": "a linked source with a Gaia DR3 match",
    "known": "matches a known asteroid/comet prediction of the same epoch (hours clock)",
    "mover": "in a pipeline mover vetted out of the suspects (e.g. near a catalogued star)",
    "suspect": "hours: in an unexplained pipeline mover (suspects); months: a vetted new high-pm fit",
    "artifact": "flag rules (ghost, crosstalk, halo, bloom, masked peak, elongated) or an unvetted high-pm fit",
    "other": "everything else: a source without Gaia (galaxy, faint or red star) or a lone dot",
}
UNITS = {
    "ra, dec": "deg ICRS (raw, no calib corrections, <= 0.2 arcsec)",
    "snr": "hours: the dot's S/N; months: sqrt(sum snr^2) of the visit's dots",
    "flux_ujy": "µJy in an r = 2 px aperture, raw (x 1.033 for the total)",
    "mjd, mjd_from, mjd_to": "MJD (UTC)",
    "dt_yr": "years",
    "dra_arcsec, ddec_arcsec, shift_arcsec, err_arcsec": "arcsec (dra already x cos dec; err is 1 sigma per axis)",
    "pa_deg": "deg, east of north",
    "pmra, pmdec, pm_err": "mas/yr (pmra x cos dec; proper motion only, no parallax)",
    "pm_snr": "pm / pm_err",
}
FOVS = {"months": (5, 15), "hours": (20, 60)}


def _nearest_fov(clock: str, field_arcmin: float) -> int:
    return min(FOVS.get(clock, (5,)), key=lambda f: abs(f - field_arcmin))


@app.tool()
async def measured_dots(
    target: Annotated[str | None, Field(description="Name or coordinates in any format")] = None,
    ra: float | None = None,
    dec: float | None = None,
    candidate_id: Annotated[str | None, Field(description="A candidates.json id (uses its clock and detector)")] = None,
    clock: Annotated[Literal["months", "hours"] | None,
                     Field(description="months = one stack per visit (shifts); hours = single exposures (tracks)")] = None,
    fov: Annotated[int | None, Field(description="Field in arcmin: months 5 or 15; hours 20 or 60")] = None,
    det: Annotated[int | None, Field(ge=1, le=6, description="Keep only this detector's epochs. Omit for every detector in a static file (live answers hold one detector, default 2)")] = None,
    classes: Annotated[list[Class] | None, Field(description="Keep only these classes (detections, tracks and shifts)")] = None,
    epoch_id: Annotated[str | None, Field(description="Keep only detections of this epoch (an epochs[].id, e.g. 'd2-v1'), and tracks with a dot in it")] = None,
    limit: Annotated[int, Field(ge=1, le=500)] = 50,
) -> str:
    """What SkyShift's own pipeline DETECTED in a field (the `measured` layer of a frames response),
    as opposed to the catalogue predictions (Gaia, known asteroids). Same inputs and source choice
    as `blink`: a candidate's static frames, a featured target's static set, else live /api/frames.
    A static file that predates the measured layer is followed by a live /api/frames call.

    Returns the zone, its stage, per-class counts (before any cap), detections (filtered, highest
    S/N first), tracks (hours clock: movers with their dots in time order) and shifts (months clock:
    fitted proper motions, highest pm_snr first), with units. When nothing is measured here it
    returns measured_reason and what it means."""
    det_given = det
    url, params, det, note = await _frames_source(target, ra, dec, candidate_id, clock, fov, det)
    c = client()
    data = await (c.api("/api/frames", **params) if params else c.get_json(url))
    source = "live /api/frames" if params else "static " + url
    if not params and "measured" not in data:
        # Static file written before v3.3: "not computed". Ask the live API for the same field,
        # unless the frames follow a moving object (no fixed field to measure).
        if any(e.get("center") for e in data.get("epochs") or []):
            source += " (no measured layer; frames follow a moving object, so no live fallback)"
        else:
            field = (data.get("size") or [0])[0] * (data.get("pixel_arcsec") or 0) / 60
            live = {"ra": data["center"]["ra"], "dec": data["center"]["dec"], "clock": data["clock"],
                    "fov": _nearest_fov(data["clock"], field), "det": det}
            data = await c.api("/api/frames", **live)
            source = f"live /api/frames {urlencode(live)} (the static file {url} has no measured layer yet)"
            det_given = det_given or det
            cand = note.get("candidate") or {}
            if data.get("clock") == "hours" and cand.get("mjd") is not None:
                inside = any(abs((e.get("mjd_mid") or 0) - cand["mjd"]) < 0.1 for e in data.get("epochs") or [])
                note["live_hours_note"] = (
                    "Live hours frames show one visit (the one with the most exposures of this detector); "
                    f"the candidate's session (MJD {cand['mjd']:.3f}) is "
                    + ("in it." if inside else "NOT in it, so its own dots and track are not in this answer."))
    epochs = data.get("epochs") or []
    epoch_det = {e["id"]: e.get("det") for e in epochs}
    out: dict[str, Any] = {**note, "source": source, "clock": data.get("clock"), "center": data.get("center"),
                           "det_filter": det_given,
                           "detectors_in_response": sorted({d for d in epoch_det.values() if d is not None})}
    m = data.get("measured")
    if not m:
        reason = data.get("measured_reason")
        out.update({"measured": None, "measured_reason": reason,
                    "meaning": REASON_MEANING.get(reason) if reason else
                    "Not computed: this frames response has no measured layer (written before v3.3)."})
        return _json(out)
    if epoch_id is not None and epoch_id not in epoch_det:
        raise SkyShiftError(f"no epoch {epoch_id!r} in this response; epochs: {sorted(epoch_det)[:40]}")
    keep_cls = set(classes) if classes else None
    keep_ep = {i for i, d in epoch_det.items() if det_given is None or d == det_given}

    dets = [d for d in m.get("detections") or []
            if (keep_cls is None or d.get("class") in keep_cls)
            and (d.get("epoch_id") in keep_ep or d.get("epoch_id") not in epoch_det)
            and (epoch_id is None or d.get("epoch_id") == epoch_id)]
    dets.sort(key=lambda d: -(d.get("snr") or 0.0))
    tracks = [t for t in m.get("tracks") or []
              if (keep_cls is None or t.get("class") in keep_cls)
              and (epoch_id is None or any(p.get("epoch_id") == epoch_id for p in t.get("dots") or []))]
    shifts = [s for s in m.get("shifts") or [] if keep_cls is None or s.get("class") in keep_cls]
    shifts.sort(key=lambda s: -(s.get("pm_snr") or 0.0))
    per_epoch: dict[str, int] = defaultdict(int)
    for d in dets:
        per_epoch[d.get("epoch_id")] += 1
    out.update({
        "zone": m.get("zone"), "zone_box": m.get("zone_box"),
        "stage": m.get("stage"), "stage_meaning": STAGE_MEANING.get(m.get("stage")),
        "photos": m.get("photos"), "photos_missing": m.get("photos_missing"),
        "truncated": m.get("truncated"),
        "counts": m.get("counts"),
        "counts_note": "Detections per class in the whole response, before the file's cap (<= 250 per "
                       "epoch) and before this tool's filters.",
        "class_meaning": CLASS_MEANING,
        "epochs": [{"id": e["id"], "det": e.get("det"), "mjd_mid": e.get("mjd_mid"),
                    "detections_matching": per_epoch.get(e["id"], 0)}
                   for e in epochs if e["id"] in keep_ep],
        "detections_matching": len(dets), "detections_shown": min(limit, len(dets)),
        "detections": dets[:limit],
        "tracks_matching": len(tracks), "tracks": tracks[:limit],
        "shifts_matching": len(shifts), "shifts": shifts[:limit],
        "units": UNITS,
    })
    return _json(out)


# ---------------------------------------------------------------- batch check

BATCH_MAX = 50
MEASURE_MAX = 5
RESOLVE_CONCURRENCY = 4
MEASURE_CONCURRENCY = 2
# web/src/data/targets.json's fixed-position (months clock) targets, as web/src/app/state.ts
# featuredNear uses them. Centres and fields come from the site's /data/<slug>/index.json.
FEATURED_FIELDS = ("barnard", "epsindib", "wise0855", "rwaur", "rdra")
_featured: weakref.WeakKeyDictionary[Client, list[dict[str, Any]]] = weakref.WeakKeyDictionary()


async def _featured_fields(c: Client) -> list[dict[str, Any]]:
    if c in _featured:
        return _featured[c]

    async def one(slug: str) -> dict[str, Any] | None:
        try:
            idx = await c.get_json(f"/data/{slug}/index.json")
        except SkyShiftError:
            return None
        own = next((s for s in idx.get("frames") or [] if s.get("file") == idx.get("own")), None)
        if not idx.get("center") or not own or own.get("clock") != "months":
            return None
        return {"slug": slug, "name": idx.get("name"), "ra": idx["center"]["ra"],
                "dec": idx["center"]["dec"], "half_field_arcmin": own["fov"] / 2}

    got = [f for f in await asyncio.gather(*(one(s) for s in FEATURED_FIELDS)) if f]
    _featured[c] = got
    return got


def _featured_near(fields: list[dict[str, Any]], ra: float, dec: float) -> dict[str, Any] | None:
    """The featured field containing (ra, dec), nearest centre first (web featuredNear)."""
    best = None
    for f in fields:
        d = cands.separation_arcmin(ra, dec, f["ra"], f["dec"])
        if d <= f["half_field_arcmin"] and (best is None or d < best[0]):
            best = (d, f)
    return best[1] if best else None


def _blink_link(c: Client, ra: float, dec: float, slug: str | None) -> str:
    if slug:
        return c.url(f"/?t={quote(slug)}#/explore")
    return c.url(f"/?ra={ra:.5f}&dec={dec:.5f}&clock=months&fov=5&det=2#/explore")


def _measure_summary(data: dict[str, Any], source: str) -> dict[str, Any]:
    g = data.get("gaia")
    return {"source": source, "name": data.get("name"),
            "gaia": {k: g.get(k) for k in ("id", "g", "pmra", "pmdec", "parallax")} if g else None,
            "known_ucd": data.get("known_ucd"),
            "measure": data.get("measure"), "measure_reason": data.get("measure_reason"),
            "measure_hint": data.get("measure_hint"),
            "spectrum_points": len(data.get("spectrum") or []),
            "spectrum_reason": data.get("spectrum_reason")}


@app.tool()
async def batch_check(
    positions: Annotated[list[str], Field(min_length=1, max_length=BATCH_MAX,
                                          description="Up to 50 names or coordinate strings, one per item, e.g. ['Barnard\\'s Star', '08h55m10.831s -7d14m42.53s', '123.6 20.7']")],
    radius_arcmin: Annotated[float, Field(gt=0, le=60, description="Search radius for SkyShift candidates, arcmin")] = 2.0,
    measure: Annotated[bool, Field(description="Also measure each position (shift, rate, Gaia match). Live /api/object builds are heavy: only the first 5 positions, 2 at a time; featured names read their static object.json")] = False,
) -> str:
    """Check many positions at once. For each line: its ICRS position (via /api/resolve), the featured
    target it falls in (static frames ready), SkyShift candidates within radius_arcmin (nearest first)
    and a link that opens it in SkyShift's blink. Lines that can't be read are reported with the
    reason; the others still run. With measure=true the first 5 positions also get SkyShift's
    measurement (slow: a live /api/object build can take up to a minute; at most 2 run at once)."""
    if len(positions) > BATCH_MAX:
        raise SkyShiftError(f"at most {BATCH_MAX} positions per call (got {len(positions)})")
    c = client()
    sem = asyncio.Semaphore(RESOLVE_CONCURRENCY)

    async def resolve(line: int, text: str) -> dict[str, Any]:
        row: dict[str, Any] = {"line": line, "input": text}
        if not text.strip():
            return {**row, "error": "empty line"}
        async with sem:
            try:
                r = await c.resolve(text.strip())
            except SkyShiftError as e:
                return {**row, "error": str(e)}
        return {**row, "name": r.get("name"), "ra": r["ra"], "dec": r["dec"],
                "resolved_by": r.get("source"), "_slug": r.get("slug")}

    rows, data, fields = await asyncio.gather(
        asyncio.gather(*(resolve(i + 1, p) for i, p in enumerate(positions))),
        c.candidates(), _featured_fields(c))
    rows = list(rows)
    named_slug: dict[int, str] = {}   # line → the slug /api/resolve gave (a featured name)
    seen: list[dict[str, Any]] = []
    for row in rows:
        if "error" in row:
            continue
        ra, dec = row["ra"], row["dec"]
        named = row.pop("_slug")
        if named:
            named_slug[row["line"]] = named
        near = None if named else _featured_near(fields, ra, dec)
        slug = named or (near and near["slug"])
        row["featured_slug"] = slug
        if near:
            row["featured_note"] = f"inside the field of {near['name']}: static frames are ready"
        dup = next((s for s in seen if cands.separation_arcmin(ra, dec, s["ra"], s["dec"]) * 60 <= 1.0), None)
        if dup:
            row["same_as_line"] = dup["line"]
        seen.append(row)
        hits = cands.rank(cands.select(data["candidates"], near=(ra, dec, radius_arcmin)), "distance")
        row["candidates_within"] = len(hits)
        row["candidates"] = [{k: h.get(k) for k in ("id", "kind", "grade", "score", "snr", "clock", "det",
                                                    "distance_arcmin", "why")} for h in hits[:10]]
        row["site_link"] = _blink_link(c, ra, dec, slug)

    if measure:
        todo = [r for r in rows if "error" not in r and "same_as_line" not in r]
        msem = asyncio.Semaphore(MEASURE_CONCURRENCY)

        async def measure_one(row: dict[str, Any]) -> None:
            async with msem:
                try:
                    named = named_slug.get(row["line"])
                    if named:
                        obj = await c.get_json(f"/data/{named}/object.json")
                        row["measured"] = _measure_summary(obj, f"static /data/{named}/object.json")
                    else:
                        obj = await c.api("/api/object", ra=row["ra"], dec=row["dec"])
                        row["measured"] = _measure_summary(obj, "live /api/object")
                except SkyShiftError as e:
                    row["measured"] = {"error": str(e)}

        await asyncio.gather(*(measure_one(r) for r in todo[:MEASURE_MAX]))
        for r in todo[MEASURE_MAX:]:
            r["measured"] = {"skipped": f"measure runs on the first {MEASURE_MAX} positions only; "
                                        "call `measure` for the others"}

    bad = [r for r in rows if "error" in r]
    return _json({"positions": len(rows), "resolved": len(rows) - len(bad), "bad_lines": len(bad),
                  "radius_arcmin": radius_arcmin, "candidates_generated": data.get("generated"),
                  "rows": rows})


# ---------------------------------------------------------------- community votes (Firestore, read-only)

FIRESTORE_DEFAULT = "https://firestore.googleapis.com/v1/projects/nasa-glazing/databases/(default)/documents"
VOTE_PAGE = 300
VOTE_CAP = 5000
VOTES_TTL = 120.0
ANSWERS = ("mover", "changing", "junk", "unsure")
ANSWER_MEANING = {"mover": "it moves", "changing": "its brightness changes",
                  "junk": "an artefact or nothing", "unsure": "can't tell"}
_votes: weakref.WeakKeyDictionary[Client, dict[str, Any]] = weakref.WeakKeyDictionary()


def _firestore() -> str:
    return (os.environ.get("SKYSHIFT_FIRESTORE") or FIRESTORE_DEFAULT).rstrip("/")


def _fs_value(v: dict[str, Any]) -> Any:
    """A Firestore REST typed value → plain Python."""
    if "stringValue" in v:
        return v["stringValue"]
    if "doubleValue" in v:
        return float(v["doubleValue"])
    if "integerValue" in v:
        return int(v["integerValue"])
    if "booleanValue" in v:
        return bool(v["booleanValue"])
    if "timestampValue" in v:
        return v["timestampValue"]
    if "nullValue" in v:
        return None
    if "mapValue" in v:
        return {k: _fs_value(x) for k, x in (v["mapValue"].get("fields") or {}).items()}
    if "arrayValue" in v:
        return [_fs_value(x) for x in v["arrayValue"].get("values") or []]
    return next(iter(v.values()), None)  # geoPoint, reference, bytes: as given


def _fs_fields(doc: dict[str, Any]) -> dict[str, Any]:
    return {k: _fs_value(v) for k, v in (doc.get("fields") or {}).items()}


def _state(c: Client) -> dict[str, Any]:
    st = _votes.get(c)
    if st is None:
        st = _votes[c] = {"lock": asyncio.Lock(), "votes": {}, "flags": {}}
    return st


async def _load_votes(c: Client) -> dict[str, Any]:
    """Every vote document (up to VOTE_CAP), cached VOTES_TTL seconds per Firestore base."""
    base = _firestore()
    st = _state(c)
    async with st["lock"]:
        hit = st["votes"].get(base)
        if hit and time.monotonic() - hit["at"] < VOTES_TTL:
            return hit
        votes: list[dict[str, Any]] = []
        token, pages, truncated = None, 0, False
        while True:
            try:
                page = await c.get_json(f"{base}/votes", {"pageSize": VOTE_PAGE, "pageToken": token})
            except SkyShiftError as e:
                raise SkyShiftError(f"could not read the community votes from Firestore: {e}") from e
            pages += 1
            votes.extend(_fs_fields(d) for d in page.get("documents") or [])
            token = page.get("nextPageToken")
            if len(votes) >= VOTE_CAP:
                truncated = bool(token) or len(votes) > VOTE_CAP
                votes = votes[:VOTE_CAP]
                break
            if not token:
                break
        hit = {"at": time.monotonic(), "fetched": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "votes": votes, "pages": pages, "truncated": truncated}
        st["votes"][base] = hit
        return hit


def _aggregate(votes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """candidate_id → counts. Voter ids are only counted (distinct), never returned."""
    acc: dict[str, dict[str, Any]] = {}
    for v in votes:
        cid, ans = v.get("candidate_id"), v.get("answer")
        if not isinstance(cid, str) or not cid:
            continue
        a = acc.setdefault(cid, {"votes": 0, "by_answer": dict.fromkeys(ANSWERS, 0), "_voters": set(),
                                 "_voters_by": defaultdict(set), "first_ts": None, "last_ts": None})
        a["votes"] += 1
        if ans in a["by_answer"]:
            a["by_answer"][ans] += 1
        voter = v.get("voter")
        if voter is not None:
            a["_voters"].add(voter)
            a["_voters_by"][ans].add(voter)
        ts = v.get("ts")
        if isinstance(ts, str):
            a["first_ts"] = min(a["first_ts"] or ts, ts)
            a["last_ts"] = max(a["last_ts"] or ts, ts)
    out = {}
    for cid, a in acc.items():
        out[cid] = {"candidate_id": cid, "votes": a["votes"], "voters": len(a["_voters"]),
                    "by_answer": a["by_answer"],
                    "voters_by_answer": {k: len(a["_voters_by"].get(k, ())) for k in ANSWERS},
                    "first_ts": a["first_ts"], "last_ts": a["last_ts"]}
    return out


async def _flag_doc(c: Client, cid: str) -> dict[str, Any] | None:
    """A Firestore `candidates` document (a user's or the pipeline's flag); None if there is none.
    Documents are create-only, so they are cached for good."""
    st = _state(c)
    key = (_firestore(), cid)
    if key in st["flags"]:
        return st["flags"][key]
    try:
        doc = await c.get_json(f"{_firestore()}/candidates/{quote(cid, safe='')}")
    except SkyShiftError as e:
        if "HTTP 404" in str(e):
            st["flags"][key] = None
            return None
        raise SkyShiftError(f"could not read Firestore candidates/{cid}: {e}") from e
    f = _fs_fields(doc)
    flag = {k: f.get(k) for k in ("ra", "dec", "mjd", "clock", "source", "created")}
    st["flags"][key] = flag
    return flag


async def _describe(c: Client, cid: str, cand_rows: list[dict[str, Any]]) -> dict[str, Any]:
    hit = cands.find(cand_rows, cid)
    if hit:
        fov = 20 if hit["clock"] == "hours" else 5
        return {"what": "pipeline candidate (candidates.json)", "candidate": cands.summarise(hit),
                "blink_page": c.url(f"/?ra={hit['ra']:.5f}&dec={hit['dec']:.5f}&clock={hit['clock']}"
                                    f"&fov={fov}&det={hit['det']}#/explore")}
    try:
        flag = await _flag_doc(c, cid)
    except SkyShiftError as e:
        return {"what": "unknown", "error": str(e)}
    if flag is None:
        return {"what": "unknown", "note": "not in candidates.json and no Firestore candidates document"}
    out: dict[str, Any] = {"what": f"flag on the site (Firestore candidates, source {flag.get('source')})",
                           "flag": flag}
    if isinstance(flag.get("ra"), float | int) and isinstance(flag.get("dec"), float | int):
        clock = flag.get("clock") if flag.get("clock") in FOVS else "months"
        out["blink_page"] = c.url(f"/?ra={flag['ra']:.5f}&dec={flag['dec']:.5f}&clock={clock}"
                                  f"&fov={FOVS[clock][0]}#/explore")
    return out


@app.tool()
async def top_voted(
    answer: Annotated[Literal["mover", "changing", "junk", "unsure"] | None,
                      Field(description="Rank by votes with this answer; omit to rank by all votes")] = None,
    limit: Annotated[int, Field(ge=1, le=50)] = 10,
) -> str:
    """What visitors of the SkyShift site voted on (Firestore `votes`, read-only, cached 2 minutes).
    Counts per candidate: all votes, distinct voters (voter ids are never shown), and per answer
    (mover = it moves, changing = brightness changes, junk = artefact or nothing, unsure). Each row
    says what was voted on: a pipeline candidate from candidates.json, or a flag a visitor placed
    (Firestore `candidates`: ra, dec, mjd, clock, source). Votes are opinions, not measurements."""
    c = client()
    loaded, data = await asyncio.gather(_load_votes(c), c.candidates())
    agg = list(_aggregate(loaded["votes"]).values())
    voted = len(agg)
    if answer:
        agg = [a for a in agg if a["by_answer"][answer] > 0]
        key = lambda a: (a["by_answer"][answer], a["voters_by_answer"][answer], a["last_ts"] or "")
    else:
        key = lambda a: (a["votes"], a["voters"], a["last_ts"] or "")
    agg.sort(key=key, reverse=True)
    rows = agg[:limit]
    sem = asyncio.Semaphore(4)

    async def join(a: dict[str, Any]) -> dict[str, Any]:
        async with sem:
            return {**a, **await _describe(c, a["candidate_id"], data["candidates"])}

    shown = await asyncio.gather(*(join(a) for a in rows))
    return _json({"source": "Firestore votes (read-only)", "fetched": loaded["fetched"],
                  "vote_documents": len(loaded["votes"]), "truncated": loaded["truncated"],
                  "truncated_note": f"only the first {VOTE_CAP} vote documents were read" if loaded["truncated"] else None,
                  "answer_meaning": ANSWER_MEANING, "answer": answer,
                  "candidates_voted": voted, "matching": len(agg),
                  "shown": len(shown), "rows": list(shown)})


@app.tool()
async def votes_for(candidate_id: Annotated[str, Field(min_length=1, max_length=128, description="A candidates.json id or a Firestore candidates document id")]) -> str:
    """Community votes on one candidate or flag (Firestore `votes`, read-only): counts per answer,
    distinct voters (ids never shown), first and last vote, the latest votes (answer and time only),
    and what was voted on."""
    c = client()
    loaded, data = await asyncio.gather(_load_votes(c), c.candidates())
    mine = [v for v in loaded["votes"] if v.get("candidate_id") == candidate_id]
    agg = _aggregate(mine).get(candidate_id) or {
        "candidate_id": candidate_id, "votes": 0, "voters": 0, "by_answer": dict.fromkeys(ANSWERS, 0),
        "voters_by_answer": dict.fromkeys(ANSWERS, 0), "first_ts": None, "last_ts": None}
    latest = sorted(({"answer": v.get("answer"), "ts": v.get("ts")} for v in mine),
                    key=lambda v: v["ts"] or "", reverse=True)[:20]
    return _json({"source": "Firestore votes (read-only)", "fetched": loaded["fetched"],
                  "vote_documents_read": len(loaded["votes"]), "truncated": loaded["truncated"],
                  "answer_meaning": ANSWER_MEANING, **agg, "latest": latest,
                  **await _describe(c, candidate_id, data["candidates"])})
