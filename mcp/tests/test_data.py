"""tools_data: measured_dots, batch_check, top_voted / votes_for, against the offline site (conftest)
plus a fake Firestore REST endpoint and synthetic `measured` layers (contracts/api.md v3.3)."""
import asyncio
import json
import re

import httpx
import pytest

from skyshift_mcp import server, tools_data
from skyshift_mcp.client import SkyShiftError

from conftest import PUBLIC, make_offline, network, site_file

FS = "https://firestore.test/v1/projects/p/databases/(default)/documents"
CAND_MOVER = "hunt-664250069368-87801613"   # in fixtures/api/candidates_sample.json, with static frames


def run(coro):
    return asyncio.run(coro)


def _json_response(obj, status=200):
    return httpx.Response(status, json=obj)


# ---------------------------------------------------------------- measured_dots

def _months_measured():
    det = lambda epoch, ra, snr, cls, sid, gaia=None: {
        "epoch_id": epoch, "ra": ra, "dec": 4.77, "snr": snr, "flux_ujy": 100.0 * snr, "n_dots": 4,
        "class": cls, "source_id": sid, "gaia_id": gaia, "known_name": None, "track_id": None,
        "grade": None, "reasons": ["masked_peak"] if cls == "artifact" else []}
    shift = lambda sid, cls, pm_snr: {
        "source_id": sid, "gaia_id": None, "class": cls, "ra": 269.44, "dec": 4.77, "mjd_from": 60913.3,
        "mjd_to": 61110.4, "dt_yr": 0.54, "dra_arcsec": -0.4, "ddec_arcsec": 5.6, "shift_arcsec": 5.61,
        "err_arcsec": 0.05, "pa_deg": 355.9, "pmra": -800.0, "pmdec": 10300.0, "pm_err": 90.0, "pm_snr": pm_snr}
    return {
        "zone": "t_barnard", "stage": "final", "zone_box": {"ra": [269.0, 270.0], "dec": [4.3, 5.3]},
        "clock": "months", "photos": 40, "photos_missing": 2, "truncated": False,
        "counts": {"star": 3, "known": 0, "mover": 0, "suspect": 1, "artifact": 1, "other": 1},
        "detections": [det("d2-v1", 269.446, 900.0, "star", 1, "4472832130942575872"),
                       det("d2-v1", 269.45, 12.0, "other", 2),
                       det("d2-v2", 269.446, 950.0, "star", 1, "4472832130942575872"),
                       det("d5-v1", 269.446, 400.0, "star", 1, "4472832130942575872"),
                       det("d2-v2", 269.43, 30.0, "artifact", 3),
                       det("d2-v1", 269.44, 8.0, "suspect", 4)],
        "tracks": [],
        "shifts": [shift(4, "suspect", 4.0), shift(1, "star", 62.0), shift(3, "artifact", 7.5)],
    }


def _hours_measured(epochs):
    e0, e1 = epochs[0]["id"], epochs[1]["id"]
    d = lambda e, snr, cls, tid=None: {
        "epoch_id": e, "ra": 123.6, "dec": 20.7, "snr": snr, "flux_ujy": 10.0 * snr, "n_dots": 1, "class": cls,
        "source_id": None, "gaia_id": None, "known_name": None, "track_id": tid, "grade": "B" if tid else None,
        "reasons": []}
    return {
        "zone": "hunt", "stage": "provisional", "zone_box": {"ra": [120.5, 130.5], "dec": [14.4, 24.4]},
        "clock": "hours", "photos": 3, "photos_missing": 0, "truncated": True,
        "counts": {"star": 300, "known": 0, "mover": 0, "suspect": 2, "artifact": 0, "other": 50},
        "detections": [d(e0, 22.7, "suspect", CAND_MOVER), d(e1, 21.0, "suspect", CAND_MOVER),
                       d(e0, 310.0, "star"), d(e1, 5.5, "other")],
        "tracks": [{"id": CAND_MOVER, "class": "suspect", "suspect_id": CAND_MOVER, "known_name": None,
                    "evidence": "two_exposures",
                    "dots": [{"ra": 123.59944, "dec": 20.70959, "mjd": epochs[0]["mjd_mid"], "epoch_id": e0},
                             {"ra": 123.59951, "dec": 20.70962, "mjd": epochs[1]["mjd_mid"], "epoch_id": e1}]},
                   {"id": "hunt-m1-2", "class": "artifact", "suspect_id": None, "known_name": None,
                    "evidence": "two_detectors", "dots": [{"ra": 123.61, "dec": 20.71, "mjd": 1.0, "epoch_id": e1}]}],
        "shifts": [],
    }


def _with_measured(rel, measured, reason=None):
    data = json.loads(site_file(rel).read_text())
    data["measured"] = measured(data["epochs"]) if callable(measured) else measured
    data["measured_reason"] = reason
    return data


@pytest.fixture
def measured_site(monkeypatch):
    calls = []

    def extra(req):
        p = req.url.path
        if p == "/data/barnard/frames_months_5.json":
            return _json_response(_with_measured("data/barnard/frames_months_5.json", _months_measured()))
        if p == f"/data/cand/{CAND_MOVER}/frames.json":
            return _json_response(_with_measured(f"data/cand/{CAND_MOVER}/frames.json", _hours_measured))
        if p == "/data/barnard/frames_hours_20.json":   # a static file without the layer (zone not combined yet)
            data = json.loads((PUBLIC / "data/barnard/frames_hours_20.json").read_text())
            data.pop("measured", None)
            data.pop("measured_reason", None)
            return _json_response(data)
        if p == "/api/frames":
            calls.append(dict(req.url.params))
            if req.url.params.get("clock") == "hours":   # the Barnard hours fallback: zone not combined yet
                data = json.loads((PUBLIC / "data/barnard/frames_hours_20.json").read_text())
                return _json_response({**data, "measured": None, "measured_reason": "processing"})
        return None

    make_offline(monkeypatch, extra)
    return calls


def test_measured_static_months(measured_site):
    out = json.loads(run(tools_data.measured_dots(target="Barnard's Star")))
    assert out["source"].startswith("static ") and "/data/barnard/frames_months_5.json" in out["source"]
    assert out["zone"] == "t_barnard" and out["stage"] == "final" and "Gaia and WISE" in out["stage_meaning"]
    assert out["counts"]["star"] == 3 and out["det_filter"] is None
    snrs = [d["snr"] for d in out["detections"]]
    assert snrs == sorted(snrs, reverse=True) and len(snrs) == 6           # every detector
    assert [s["pm_snr"] for s in out["shifts"]] == [62.0, 7.5, 4.0]
    assert out["units"]["flux_ujy"].startswith("µJy") and measured_site == []   # no live call


def test_measured_filters(measured_site):
    out = json.loads(run(tools_data.measured_dots(target="Barnard's Star", det=2, classes=["star", "suspect"])))
    assert {d["epoch_id"] for d in out["detections"]} <= {"d2-v1", "d2-v2"}
    assert {d["class"] for d in out["detections"]} == {"star", "suspect"}
    assert [s["class"] for s in out["shifts"]] == ["star", "suspect"]
    assert all(e["det"] == 2 for e in out["epochs"])
    one = json.loads(run(tools_data.measured_dots(target="Barnard's Star", epoch_id="d2-v1", limit=1)))
    assert one["detections_matching"] == 3 and one["detections_shown"] == 1
    assert one["detections"][0]["snr"] == 900.0
    with pytest.raises(SkyShiftError, match="no epoch"):
        run(tools_data.measured_dots(target="Barnard's Star", epoch_id="d9-v9"))


def test_measured_candidate_tracks(measured_site):
    out = json.loads(run(tools_data.measured_dots(candidate_id=CAND_MOVER, classes=["suspect"])))
    assert out["candidate"]["id"] == CAND_MOVER and out["stage"] == "provisional"
    assert out["truncated"] is True and out["tracks_matching"] == 1
    assert out["tracks"][0]["suspect_id"] == CAND_MOVER and len(out["tracks"][0]["dots"]) == 2
    assert all(d["class"] == "suspect" for d in out["detections"]) and out["shifts"] == []


def test_measured_static_without_layer_falls_back_live(measured_site):
    out = json.loads(run(tools_data.measured_dots(target="Barnard's Star", clock="hours", fov=20)))
    assert out["measured"] is None and out["measured_reason"] == "processing"
    assert "not combined" in out["meaning"] and out["source"].startswith("live /api/frames")
    assert measured_site and measured_site[0]["clock"] == "hours" and measured_site[0]["fov"] == "20"


def test_measured_absent_means_not_computed(offline):
    out = json.loads(run(tools_data.measured_dots(ra=10.0, dec=20.0)))   # live fixture has no `measured`
    assert out["measured"] is None and out["measured_reason"] is None
    assert "Not computed" in out["meaning"]


def test_measured_moving_target_no_fallback(monkeypatch):
    def extra(req):
        if req.url.path == "/api/resolve":
            return _json_response({"q": "3I/ATLAS", "name": "3I/ATLAS", "ra": 166.97, "dec": 5.23,
                                   "slug": "3i-atlas", "source": "builtin"})
        return None
    make_offline(monkeypatch, extra)
    out = json.loads(run(tools_data.measured_dots(target="3I/ATLAS")))
    assert out["measured"] is None and "moving object" in out["source"]


# ---------------------------------------------------------------- batch_check

def _batch_site(monkeypatch, object_delay=0.0):
    stats = {"object": 0, "in_flight": 0, "max_in_flight": 0, "resolve": 0}

    async def slow_object(req):
        stats["object"] += 1
        stats["in_flight"] += 1
        stats["max_in_flight"] = max(stats["max_in_flight"], stats["in_flight"])
        await asyncio.sleep(object_delay)
        stats["in_flight"] -= 1
        return httpx.Response(200, content=(PUBLIC.parents[1] / "fixtures/api/object_barnard.json").read_bytes())

    def extra(req):
        p = req.url.path
        if p == "/api/resolve":
            stats["resolve"] += 1
            q = req.url.params.get("q", "")
            if "barnard" in q.lower() or q.startswith("08h55m"):
                return None                       # conftest: resolve_barnard / resolve_coords (WISE 0855)
            m = re.fullmatch(r"\s*(-?[\d.]+)[ ,]+(-?[\d.]+)\s*", q)
            if m:
                return _json_response({"q": q, "name": None, "ra": float(m[1]), "dec": float(m[2]),
                                       "slug": None, "source": "coords"})
            return _json_response({"error": {"code": "bad_request", "message": f"can't read {q!r}"}}, 400)
        if p == "/api/object":
            return slow_object(req)
        return None

    make_offline(monkeypatch, extra)
    return stats


def test_batch_check_rows(monkeypatch):
    stats = _batch_site(monkeypatch)
    lines = ["Barnard's Star", "08h55m10.831s -7d14m42.53s", "123.599449 20.709591", "not a position", "",
             "123.5994495 20.7095911", "10 20"]
    out = json.loads(run(tools_data.batch_check(lines)))
    rows = {r["line"]: r for r in out["rows"]}
    assert out["positions"] == 7 and out["bad_lines"] == 2 and out["resolved"] == 5
    assert rows[1]["featured_slug"] == "barnard" and rows[1]["site_link"].endswith("/?t=barnard#/explore")
    assert rows[2]["featured_slug"] == "wise0855" and "featured_note" in rows[2]   # inside its 5′ field
    assert rows[3]["featured_slug"] is None and rows[3]["candidates"][0]["id"] == CAND_MOVER
    assert rows[3]["site_link"].endswith("/?ra=123.59945&dec=20.70959&clock=months&fov=5&det=2#/explore")
    assert "bad_request" in rows[4]["error"] and rows[5]["error"] == "empty line"
    assert rows[6]["same_as_line"] == 3
    assert rows[7]["candidates_within"] == 0 and rows[7]["candidates"] == []
    assert stats["object"] == 0 and all("measured" not in r for r in out["rows"])


def test_batch_check_measure_cap(monkeypatch):
    stats = _batch_site(monkeypatch, object_delay=0.02)
    lines = ["Barnard's Star"] + [f"{10 + i} 20" for i in range(7)]
    out = json.loads(run(tools_data.batch_check(lines, measure=True)))
    rows = out["rows"]
    assert rows[0]["measured"]["source"] == "static /data/barnard/object.json"
    assert [r["measured"]["source"] for r in rows[1:5]] == ["live /api/object"] * 4
    assert all("skipped" in r["measured"] for r in rows[5:])
    assert stats["object"] == 4 and stats["max_in_flight"] <= tools_data.MEASURE_CONCURRENCY
    assert rows[1]["measured"]["measure"]["rate_arcsec_yr"] > 5


def test_batch_check_limit(offline):
    with pytest.raises(SkyShiftError, match="at most 50"):
        run(tools_data.batch_check([f"{i} 0" for i in range(51)]))


# ---------------------------------------------------------------- votes (Firestore)

def _vote(cid, answer, voter, ts):
    return {"name": f"projects/p/databases/(default)/documents/votes/{voter}-{ts}",
            "fields": {"candidate_id": {"stringValue": cid}, "answer": {"stringValue": answer},
                       "voter": {"stringValue": voter}, "ts": {"timestampValue": ts}}}


FLAG = {"name": "projects/p/databases/(default)/documents/candidates/flagUSER1",
        "fields": {"ra": {"doubleValue": 269.46242904723454}, "dec": {"doubleValue": 4.796790913524428},
                   "mjd": {"doubleValue": 61117.630771}, "clock": {"stringValue": "months"},
                   "source": {"stringValue": "user"}, "created": {"timestampValue": "2026-11-14T13:42:21Z"}}}


def _base_votes():
    return [_vote(CAND_MOVER, "mover", "SECRETVOTER-a", "2026-11-14T10:00:00Z"),
            _vote(CAND_MOVER, "mover", "SECRETVOTER-a", "2026-11-14T10:05:00Z"),
            _vote(CAND_MOVER, "mover", "SECRETVOTER-b", "2026-11-14T11:00:00Z"),
            _vote(CAND_MOVER, "junk", "SECRETVOTER-c", "2026-11-14T12:00:00Z"),
            _vote("flagUSER1", "changing", "SECRETVOTER-a", "2026-11-14T13:00:00Z"),
            _vote("flagUSER1", "changing", "SECRETVOTER-d", "2026-11-14T13:30:00Z"),
            _vote("ghostid", "unsure", "SECRETVOTER-e", "2026-11-14T14:00:00Z")]


@pytest.fixture
def firestore(monkeypatch):
    monkeypatch.setenv("SKYSHIFT_FIRESTORE", FS)
    state = {"docs": _base_votes(), "pages": 0, "fail": False}

    def extra(req):
        if req.url.host != "firestore.test":
            return None
        path = req.url.path
        if state["fail"]:
            return _json_response({"error": {"code": 403, "message": "Missing or insufficient permissions.",
                                             "status": "PERMISSION_DENIED"}}, 403)
        if path.endswith("/documents/votes"):
            state["pages"] += 1
            size = int(req.url.params.get("pageSize", 20))
            start = int(req.url.params.get("pageToken") or 0)
            chunk = state["docs"][start:start + size]
            body = {"documents": chunk} if chunk else {}
            if start + size < len(state["docs"]):
                body["nextPageToken"] = str(start + size)
            return _json_response(body)
        if path.endswith("/documents/candidates/flagUSER1"):
            return _json_response(FLAG)
        if "/documents/candidates/" in path:
            return _json_response({"error": {"code": 404, "message": "No document to update", "status": "NOT_FOUND"}}, 404)
        return _json_response({"error": {"code": 404, "message": path}}, 404)

    make_offline(monkeypatch, extra)
    return state


def test_top_voted_aggregates_and_joins(firestore):
    raw = run(tools_data.top_voted())
    assert "SECRETVOTER" not in raw
    out = json.loads(raw)
    assert out["vote_documents"] == 7 and out["candidates_voted"] == 3 and not out["truncated"]
    first, second, third = out["rows"]
    assert first["candidate_id"] == CAND_MOVER and first["votes"] == 4 and first["voters"] == 3
    assert first["by_answer"] == {"mover": 3, "changing": 0, "junk": 1, "unsure": 0}
    assert first["voters_by_answer"]["mover"] == 2
    assert first["what"].startswith("pipeline candidate") and first["candidate"]["kind"] == "mover"
    assert second["candidate_id"] == "flagUSER1" and second["flag"]["clock"] == "months"
    assert second["flag"]["ra"] == pytest.approx(269.462429) and "blink_page" in second
    assert third["what"] == "unknown"


def test_top_voted_by_answer_and_votes_for(firestore):
    out = json.loads(run(tools_data.top_voted(answer="changing")))
    assert [r["candidate_id"] for r in out["rows"]] == ["flagUSER1"] and out["matching"] == 1
    raw = run(tools_data.votes_for(CAND_MOVER))
    assert "SECRETVOTER" not in raw and "voter\"" not in raw
    one = json.loads(raw)
    assert one["votes"] == 4 and one["voters"] == 3 and one["last_ts"] == "2026-11-14T12:00:00Z"
    assert one["latest"][0] == {"answer": "junk", "ts": "2026-11-14T12:00:00Z"}
    none = json.loads(run(tools_data.votes_for("nobody-voted")))
    assert none["votes"] == 0 and none["what"] == "unknown"


def test_votes_paging_and_cache(firestore):
    firestore["docs"] = [_vote(f"c{i % 40}", "mover", f"SECRETVOTER-{i}", f"2026-11-14T10:{i % 60:02d}:00Z")
                         for i in range(700)]
    out = json.loads(run(tools_data.top_voted(limit=3)))
    assert out["vote_documents"] == 700 and firestore["pages"] == 3 and not out["truncated"]
    run(tools_data.votes_for("c1"))                          # cached: no new page
    assert firestore["pages"] == 3


def test_votes_cap(firestore, monkeypatch):
    firestore["docs"] = [_vote(f"c{i % 40}", "mover", f"SECRETVOTER-{i}", "2026-11-14T10:00:00Z")
                         for i in range(700)]
    monkeypatch.setattr(tools_data, "VOTE_CAP", 500)
    out = json.loads(run(tools_data.top_voted(limit=1)))
    assert out["vote_documents"] == 500 and out["truncated"] is True and firestore["pages"] == 2


def test_votes_firestore_error(firestore):
    firestore["fail"] = True
    with pytest.raises(SkyShiftError, match="Firestore.*403"):
        run(tools_data.top_voted())


def test_protocol_lists_data_tools(offline):
    from mcp.shared.memory import create_connected_server_and_client_session

    async def go():
        async with create_connected_server_and_client_session(server.app._mcp_server) as s:
            return {t.name for t in (await s.list_tools()).tools}

    assert {"measured_dots", "batch_check", "top_voted", "votes_for"} <= run(go())


# ---------------------------------------------------------------- live (SKYSHIFT_NETWORK=1)

@network
def test_live_data_tools():
    server._client = None

    async def go():   # one event loop: the real httpx client's pool is bound to it
        return [json.loads(x) for x in (
            await tools_data.top_voted(limit=3),
            await tools_data.batch_check(["Barnard's Star", "08h55m10.831s -7d14m42.53s", "123.599449 20.709591"]),
            await tools_data.measured_dots(target="Barnard's Star"))]

    votes, batch, m = run(go())
    server._client = None
    assert votes["vote_documents"] >= 0 and '"voter"' not in json.dumps(votes["rows"])
    assert batch["resolved"] == 3 and batch["rows"][0]["featured_slug"] == "barnard"
    assert batch["rows"][1]["featured_slug"] == "wise0855"
    assert "zone" in m or "measured_reason" in m


def test_featured_fields_from_targets_json():
    assert tools_data.FEATURED_FIELDS == ("barnard", "epsindib", "wise0855", "rwaur", "rdra")
