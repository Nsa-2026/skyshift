"""Offline tests for build_validation: the status rules, pending checks, and the contract shape."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import build_validation as bv  # noqa: E402

KEYS = {"id", "claim", "status", "headline", "value", "unit", "expected", "note", "rows"}


def test_v1_status():
    assert bv.v1_status(5, 5) == "pass"
    assert bv.v1_status(4, 5) == "partial"
    assert bv.v1_status(3, 5) == "partial"
    assert bv.v1_status(2, 5) == "fail"


def test_v2_status_barnard_limits():
    assert bv.v2_status(5.5, 2.2) == "pass"
    assert bv.v2_status(-15.0, 10.0) == "pass"
    assert bv.v2_status(15.1, 2.0) == "fail"
    assert bv.v2_status(5.0, 10.5) == "fail"
    assert bv.v2_status(None, 2.0) == "pending"


def test_v3_status():
    assert bv.v3_status(344) == "pass"
    assert bv.v3_status(9) == "partial"
    assert bv.v3_status(None) == "partial"


def test_v4_status():
    assert bv.v4_status(60, 60, 0.13, 0.01) == "pass"
    assert bv.v4_status(48, 60, 0.13, 0.01) == "pass"
    assert bv.v4_status(47, 60, 0.13, 0.01) == "partial"
    assert bv.v4_status(60, 60, 1.2, 0.01) == "partial"  # offsets too large
    assert bv.v4_status(60, 60, 0.13, 0.06) == "partial"  # control too high
    assert bv.v4_status(0, 60, None, None) == "fail"


def test_v5_status_gate_g2():
    assert bv.v5_status(0.93, 0.0) == "pass"
    assert bv.v5_status(0.50, 0.0) == "pass"
    assert bv.v5_status(0.49, 0.0) == "partial"
    assert bv.v5_status(0.93, 0.10) == "partial"  # chance rate too high: not a pass
    assert bv.v5_status(0.0, 0.0) == "fail"
    assert bv.v5_status(None, None) == "pending"


def test_v8_status_two_halves():
    assert bv.v8_status(None, None, False) == "pending"
    assert bv.v8_status(True, None, False) == "partial"  # spectra only
    assert bv.v8_status(None, 4.0, True) == "partial"  # speed only
    assert bv.v8_status(True, 4.0, True) == "pass"
    assert bv.v8_status(True, 15.0, True) == "fail"  # under 15 s means strictly under
    assert bv.v8_status(True, None, True) == "fail"  # half or more requests failed
    assert bv.v8_status(False, 4.0, True) == "fail"


def test_v10_status_and_worst_of_zones():
    assert bv.v10_status(0.02) == "pass"
    assert bv.v10_status(0.07) == "partial"
    assert bv.v10_status(0.2) == "fail"
    assert bv.v10_status(None) == "fail"
    assert bv.worst(["pass", "partial"]) == "partial"
    assert bv.worst(["pass", "fail", "partial"]) == "fail"
    assert bv.worst(["pass", "pending"]) == "pass"
    assert bv.worst([]) == "pending"


def test_dev_sigma():
    assert bv.dev_sigma(10.83, 10.27, 0.36) == pytest.approx(1.556, abs=1e-3)
    assert bv.dev_sigma(6.05, 6.49, 0.42) == pytest.approx(1.048, abs=1e-3)
    assert bv.dev_sigma(None, 1.0, 0.3) is None
    assert bv.dev_sigma(1.0, 1.0, 0.0) is None


def test_missing_inputs_are_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(bv, "DATA", tmp_path)  # no v1/v4/v8 data
    monkeypatch.setattr(bv, "SIZES", tmp_path / "none.json")
    for c in (bv.v1(), bv.v4(), bv.v5(tmp_path, "pilot"), bv.v8(None), bv.v9(None, "pilot"), bv.v10(None, "pilot"), bv.v11()):
        assert c["status"] == "pending", c["id"]
        assert c["headline"]
        assert set(c) == KEYS


def _demo(tmp_path, times, ok=None):
    rows = [{"ra": 10.0 * i, "dec": 0.0, "clock": clk, "ok": True if ok is None else ok[i], "first_frame_s": t}
            for i, (t, clk) in enumerate(times)]
    good = sorted(r["first_frame_s"] if r["ok"] else float("inf") for r in rows)
    med = good[len(good) // 2] if len(good) % 2 else (good[len(good) // 2 - 1] + good[len(good) // 2]) / 2
    d = {"generated": "2026-11-14T18:00:00+00:00", "api_base": "https://skyshift.web.app", "seed": 1, "random": rows,
         "n_random": len(rows), "median_first_frame_s": med if med != float("inf") else None,
         "median_first_frame_s_by_clock": {}}
    p = tmp_path / "demo.json"
    p.write_text(json.dumps(d))
    return p


def test_v8_speed_slow_is_fail_and_printed(tmp_path, monkeypatch):
    monkeypatch.setattr(bv, "DATA", tmp_path)  # no spectra: speed half alone
    c = bv.v8(_demo(tmp_path, [(20.0, "months"), (22.0, "hours"), (30.0, "months")]))
    assert c["status"] == "fail"
    assert "22.0 s" in c["headline"]
    assert "2026-11-14T18:00:00" in c["note"]
    assert c["note"].rstrip().endswith("(ra, dec, clock, ok, first_frame_s).")


def test_v8_all_failed_is_fail_with_causes(tmp_path, monkeypatch):
    monkeypatch.setattr(bv, "DATA", tmp_path)
    p = _demo(tmp_path, [(None, "months"), (None, "hours")], ok=[False, False])
    d = json.loads(p.read_text())
    d["random"][0].update(status=429, error="HTTP 429: Rate exceeded.")
    d["random"][1].update(status=None, error="timeout after 60 s")
    p.write_text(json.dumps(d))
    c = bv.v8(p)
    assert c["status"] == "fail"
    assert "only 0 of 2 requests answered" in c["headline"]
    assert "1 HTTP 429" in c["note"] and "1 no answer within the timeout" in c["note"]


def test_v8_speed_fast_is_partial_without_spectra(tmp_path, monkeypatch):
    monkeypatch.setattr(bv, "DATA", tmp_path)
    c = bv.v8(_demo(tmp_path, [(3.0, "months"), (4.0, "hours"), (5.0, "months")]))
    assert c["status"] == "partial"


def test_notes_end_with_row_keys():
    """On the committed data, every note that has rows lists their keys last."""
    site = bv.SITE_DATA
    if not (site / "barnard" / "object.json").exists():
        pytest.skip("no site data")
    c2, c3 = bv.v2_v3(site)
    for c in (bv.v1(), c2, c3, bv.v4(), bv.v8(None)):
        if c["status"] == "pending":
            continue
        assert set(c) == KEYS
        tail = c["note"].rsplit("Rows:", 1)
        assert len(tail) == 2 and ". " not in tail[1], c["id"]  # the row keys are the last phrase
        for row in c["rows"][:3]:
            for k in row:
                assert k in tail[1], (c["id"], k)


def test_valid_suspects():
    s = {"id": "x", "ra": 1.0, "dec": 2.0, "mjd": 60000.0, "clock": "hours", "det": 3, "evidence": "two_exposures",
         "n_dots": 2, "snr": 10.0, "flux_ujy": 100.0, "cell": 1, "nearest_known_arcsec": None}
    assert bv.valid_suspects({"suspects": [s]})
    assert not bv.valid_suspects({"suspects": [s], "synthetic": True})
    assert not bv.valid_suspects({"suspects": [{**s, "evidence": "vibes"}]})
    assert not bv.valid_suspects({"suspects": [{k: v for k, v in s.items() if k != "cell"}]})


def test_demo_check_path_default_and_override(tmp_path, monkeypatch):
    """V8 speed reads data/demo_check.json without a flag, so a rebuild keeps it; --demo-check still wins."""
    default = tmp_path / "demo_check.json"
    monkeypatch.setattr(bv, "DEMO_CHECK", default)
    assert bv.demo_check_path(None) is None  # no run yet: speed stays pending
    default.write_text("{}")
    assert bv.demo_check_path(None) == default
    other = tmp_path / "other.json"
    other.write_text("{}")
    assert bv.demo_check_path(other) == other
    with pytest.raises(SystemExit):
        bv.demo_check_path(tmp_path / "missing.json")


def test_committed_demo_check_gives_a_measured_speed_half():
    """The committed demo_check.py run turns into a measured speed half (its numbers printed, never pending)."""
    if not bv.DEMO_CHECK.exists():
        pytest.skip("no data/demo_check.json")
    d = json.loads(bv.DEMO_CHECK.read_text())
    c = bv.v8(bv.DEMO_CHECK)
    assert c["status"] in ("pass", "fail")
    assert "speed not measured" not in c["headline"]
    assert f"{d['n_random_ok']} of {d['n_random']}" in c["headline"] or "median time to first frame" in c["headline"]
    assert sum(1 for r in c["rows"] if r.get("part") == "speed") == d["n_random"]


def _v1_data(rdra_missed: list[tuple[int, int]]) -> dict:
    """v1_targets.json with one target, R Draconis: 2 visits x D1-D6, `rdra_missed` = [(visit, detector)] not found."""
    rows = [{"slug": "rdra", "visit": v, "detector": det, "photos": 5, "expected": True,
             "recovered": (v, det) not in rdra_missed} for v in (1, 2) for det in range(1, 7)]
    n_ok = sum(r["recovered"] for r in rows)
    visits_ok = len({r["visit"] for r in rows if r["recovered"]})
    return {"targets_tested": 1, "targets_recovered": 1, "rows": rows,
            "targets": [{"slug": "rdra", "recovered": True, "visits": 2, "visits_recovered": visits_ok,
                         "stacks_expected": 12, "stacks_recovered": n_ok, "detectors_recovered": [1, 2, 3, 4, 5, 6],
                         "median_offset_px": 0.2}]}


def test_v1_note_counts_misses_from_the_stack_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(bv, "DATA", tmp_path)  # no targets.json: names fall back to the slug
    text = bv.v1_stack_text(_v1_data([(1, 4), (2, 5)]))
    assert text == (" rdra (expected in D1-D6): found in 10 of 12 stacks, 2 of 2 visits; "
                    "missed in visit 1 D4 (5 photos), visit 2 D5 (5 photos).")
    assert bv.v1_stack_text(_v1_data([])) == ""  # every stack found: no sentence, nothing left over


def test_v1_note_agrees_with_rows():
    """On the committed v1_targets.json, the V1 note quotes the same stack counts as the V1 rows."""
    c = bv.v1()
    if c["status"] == "pending":
        pytest.skip("no data/v1_targets.json")
    names = bv._target_names()
    assert "not yet explained" not in c["note"]
    for row in c["rows"]:
        name = names.get(row["slug"], row["slug"])
        claim = (f"found in {row['stacks_recovered']} of {row['stacks_expected']} stacks, "
                 f"{row['visits_recovered']} of {row['visits']} visits")
        if f" {name} (expected in" in c["note"]:
            assert claim in c["note"], row["slug"]
        else:  # no sentence only when every expected stack was found
            assert row["stacks_recovered"] == row["stacks_expected"], row["slug"]


# --- two zones: hunt headline, pilot kept ---------------------------------------------------------

def _by_v(rows):
    return [{"v_min": a, "v_max": b, "n": n, "found": f, "recall": f / n, "control_recall": 0.0} for a, b, n, f in rows]


HUNT_BY_V = _by_v([(None, 12.0, 41, 40), (12.0, 14.0, 56, 52), (14.0, 15.0, 102, 93), (15.0, 16.0, 258, 228),
                   (16.0, 17.0, 349, 291)])
PILOT_BY_V = _by_v([(None, 12.0, 18, 17), (12.0, 14.0, 5, 5), (14.0, 15.0, 28, 25), (15.0, 16.0, 39, 36),
                    (16.0, 17.0, 70, 58)])
HUNT_BOX, PILOT_BOX = ([120.5, 130.5], [14.4, 24.4]), ([120.5, 124.1], [14.4, 17.9])


def _zone(root, zone, generated, by_v, box, photos, objects, source="mpc", score_generated=None):
    """One zone's recall.json and summary.json under root/zone."""
    gate = [b for b in by_v if b["v_max"] <= 16]
    n, f = sum(b["n"] for b in gate), sum(b["found"] for b in gate)
    rec = {"zone": zone, "generated": generated, "match_arcsec": 12.0, "control_offset_arcsec": 120.0,
           "unit": "one known object in one session with >= 2 exposures covering it", "photos_with_predictions": photos,
           "v_max": 16.0, "n_known": n, "n_found": f, "recall": f / n, "control_recall": 0.0,
           "per_object": {"n": objects, "found": objects}, "v_12_to_16": {"n": 50, "found": 46},  # stale on purpose
           "g2_threshold": 0.5, "by_v": by_v}
    summ = {"zone": {"name": zone, "ra": box[0], "dec": box[1]},
            "steps": {"photolist": {"photos": photos}, "known": {"source": source},
                      "score": {"generated": score_generated or generated}}}
    d = root / zone
    d.mkdir(parents=True, exist_ok=True)
    (d / "recall.json").write_text(json.dumps(rec))
    (d / "summary.json").write_text(json.dumps(summ))


def test_v5_hunt_headline_pilot_kept(tmp_path):
    _zone(tmp_path, "hunt", "2026-11-14T13:39:27Z", HUNT_BY_V, HUNT_BOX, 3692, 30)
    _zone(tmp_path, "pilot", "2026-11-14T13:12:08Z", PILOT_BY_V, PILOT_BOX, 990, 8)
    c = bv.v5(tmp_path)  # default zones: hunt, pilot
    assert set(c) == KEYS and c["status"] == "pass"
    assert c["headline"].startswith("Hunt zone: 413 of 457 known asteroid sessions at V < 16 found as movers (90%)")
    assert "Pilot zone (gate G2): 83 of 90 (92%), control 0%, 8 of 8 objects" in c["headline"]
    assert c["value"] == pytest.approx(413 / 457, abs=1e-3)
    assert [r["zone"] for r in c["rows"]] == ["hunt"] * 5 + ["pilot"] * 5
    # V 12-16 counts come from the rows, not from the report's own summary field (stale here: 46 of 50)
    assert "V 12-16 alone: 373 of 416" in c["note"] and "V 12-16 alone: 66 of 72" in c["note"]
    assert "46 of 50" not in c["note"]
    assert "2026-11-14T13:39:27Z" in c["note"] and "2026-11-14T13:12:08Z" in c["note"]
    assert "Minor Planet Center" in c["note"] and "skybot" not in json.dumps(c).lower()
    assert "inside the hunt zone" in c["note"] and "decided on the pilot zone" in c["note"]
    assert c["note"].endswith("Rows: zone, v_min, v_max, n, found, recall, control_recall.")


def test_v5_says_recovered_on_a_track_when_recall_counts_tracklets(tmp_path):
    """A recall.json with recall_movers (tracklets, #150): `recall` also counts dots recovered on a track."""
    _zone(tmp_path, "hunt", "2026-11-14T13:39:27Z", HUNT_BY_V, HUNT_BOX, 3692, 30)
    rec = json.loads((tmp_path / "hunt" / "recall.json").read_text())
    rec.update({"n_found": 433, "recall": 433 / 457, "n_found_movers": 413, "recall_movers": 413 / 457,
                "control_recall_movers": 0.0})
    (tmp_path / "hunt" / "recall.json").write_text(json.dumps(rec))
    c = bv.v5(tmp_path, "hunt")
    assert c["headline"].startswith("Hunt zone: 433 of 457 known asteroid sessions at V < 16 found (as movers or "
                                    "recovered on a track) (95%); control 0%")
    assert "movers alone: 413 of 457 (90%)" in c["note"] and "a dot recovered on a mover track's predicted path" in c["note"]
    assert c["value"] == pytest.approx(433 / 457, abs=1e-3)


def test_v5_falls_back_to_the_pilot_without_hunt_reports(tmp_path):
    _zone(tmp_path, "pilot", "2026-11-14T13:12:08Z", PILOT_BY_V, PILOT_BOX, 990, 8)
    c = bv.v5(tmp_path, "hunt,pilot")
    assert c["headline"].startswith("Pilot zone: 83 of 90 known asteroid sessions")
    assert "(gate G2)" not in c["headline"]
    assert "No hunt zone report yet." in c["note"]
    assert {r["zone"] for r in c["rows"]} == {"pilot"}


def test_v5_publishes_only_mpc_scored_reports(tmp_path, capsys):
    _zone(tmp_path, "pilot", "2026-11-14T13:12:08Z", PILOT_BY_V, PILOT_BOX, 990, 8)
    _zone(tmp_path, "hunt", "2026-11-14T13:39:27Z", HUNT_BY_V, HUNT_BOX, 3692, 30, source="other")
    c = bv.v5(tmp_path)
    assert c["headline"].startswith("Pilot zone:") and {r["zone"] for r in c["rows"]} == {"pilot"}
    assert "The hunt zone's report is left out" in c["note"] and "No hunt zone report yet" not in c["note"]
    assert "left out" in capsys.readouterr().out
    # summary.json of another run (a different score time): the source is unknown, so the report is left out
    _zone(tmp_path, "hunt", "2026-11-14T13:39:27Z", HUNT_BY_V, HUNT_BOX, 3692, 30, score_generated="2026-11-14T10:00:00Z")
    assert bv.v5(tmp_path)["headline"].startswith("Pilot zone:")
    (tmp_path / "hunt" / "summary.json").unlink()
    assert bv.v5(tmp_path)["headline"].startswith("Pilot zone:")
    # a report that names its own source needs no summary
    rec = json.loads((tmp_path / "hunt" / "recall.json").read_text())
    (tmp_path / "hunt" / "recall.json").write_text(json.dumps({**rec, "known_source": "mpc"}))
    assert bv.v5(tmp_path)["headline"].startswith("Hunt zone:")


def _astrometry(root, zone, n, pull):
    axis = {"n": n, "bias": 2.5, "scatter": 146.0, "pull_scatter": pull}
    (root / zone).mkdir(parents=True, exist_ok=True)
    (root / zone / "astrometry_report.json").write_text(json.dumps(
        {"vs_gaia_frame_corrected": {"matched": n, "pmra": axis, "pmdec": axis}}))


def _photoz(root, zone, n, s):
    b = {"n": n, "sigma_nmad": s, "outlier_frac": 0.027, "bias": 0.004}
    (root / zone).mkdir(parents=True, exist_ok=True)
    (root / zone / "photoz_report.json").write_text(json.dumps(
        {"sdss_dr17": {"all": b, "k_ab_lt_17": b, "by_k_ab": {"15..16": b}}}))


def test_v9_v10_two_zones_and_the_poorer_status_wins(tmp_path):
    _astrometry(tmp_path, "hunt", 211326, 0.97)
    _astrometry(tmp_path, "pilot", 32446, 1.6)  # the pilot's pulls out of range: not a pass overall
    c = bv.v9(tmp_path)
    assert c["headline"].startswith("211,326 stars vs Gaia (hunt zone)") and "Pilot zone: 32,446 stars" in c["headline"]
    assert c["status"] == "partial"
    assert [r["zone"] for r in c["rows"]] == ["hunt", "hunt", "pilot", "pilot"]
    _photoz(tmp_path, "hunt", 8552, 0.0228)
    _photoz(tmp_path, "pilot", 1211, 0.0209)
    c = bv.v10(tmp_path)
    assert c["status"] == "pass" and c["value"] == pytest.approx(0.0228)
    assert c["headline"].startswith("8,552 bright galaxies (hunt zone): sigma_NMAD 0.023")
    assert {r["zone"] for r in c["rows"]} == {"hunt", "pilot"}
    assert c["note"].endswith("Rows: zone, sample, n, sigma_nmad, outlier_frac, bias.")


def _spectra(path, zone, ks, w1, ok=True):
    def band(m):
        return {"clean": {"n": 1000, "median": m, "scatter": 0.03},
                "by_mag": [{"band": "x", "mag_min": 13, "mag_max": 14, "n": 10, "median": m, "scatter": 0.05}]}
    path.write_text(json.dumps({"zone": zone, "sources": 5000, "pass": ok, "bands": {"Ks": band(ks), "W1": band(w1)}}))


def test_v8_spectra_two_zones(tmp_path, monkeypatch):
    monkeypatch.setattr(bv, "DATA", tmp_path)
    _spectra(tmp_path / "v8_spectra_hunt.json", "hunt", 0.9925, 1.0291)
    _spectra(tmp_path / "v8_spectra_pilot.json", "pilot", 0.9933, 1.0265)
    c = bv.v8(None)
    assert c["value"] == 0.9925 and c["status"] == "partial"  # spectra only, no speed run
    assert "(hunt zone, 10-13 mag; pilot zone Ks 0.993, W1 1.026)" in c["headline"]
    assert {r["zone"] for r in c["rows"]} == {"hunt", "pilot"}
    _spectra(tmp_path / "v8_spectra_pilot.json", "pilot", 1.3, 1.0265, ok=False)
    assert bv.v8(None)["status"] == "fail"  # every zone must pass
    (tmp_path / "v8_spectra_hunt.json").unlink()
    assert "(pilot zone, 10-13 mag)" in bv.v8(None)["headline"]


def _suspect(id_, ra, dec, mjd):
    return {"id": id_, "ra": ra, "dec": dec, "mjd": mjd, "clock": "hours", "det": 4, "evidence": "two_exposures",
            "n_dots": 2, "snr": 10.0, "flux_ujy": 900.0, "cell": 1, "nearest_known_arcsec": None}


def test_merge_suspects_hunt_first_pilot_kept_no_repeats():
    hunt = {"generated": "2026-11-14T13:39:56Z", "data_release": "DR1", "zone": "hunt",
            "suspects": [_suspect("hunt-1-1", 121.0, 15.0, 60970.0), _suspect("hunt-2-2", 128.0, 22.0, 61140.0)]}
    pilot = {"generated": "2026-11-14T13:12:18Z", "data_release": "DR1", "zone": "pilot",
             "suspects": [_suspect("pilot-1-1", 121.0 + 1e-5, 15.0, 60970.0 + 1e-5),  # the same mover as hunt-1-1
                          _suspect("pilot-3-3", 122.0, 16.0, 60971.0)]}  # found by the pilot only
    m = bv.merge_suspects([("hunt", hunt), ("pilot", pilot)])
    assert m["zone"] == "hunt" and m["zones"] == ["hunt", "pilot"] and m["generated"] == hunt["generated"]
    assert [s["id"] for s in m["suspects"]] == ["hunt-1-1", "hunt-2-2", "pilot-3-3"]
    assert [s["zones"] for s in m["suspects"]] == [["hunt", "pilot"], ["hunt"], ["pilot"]]
    assert [s["zone"] for s in m["suspects"]] == ["hunt", "hunt", "pilot"]
    assert bv.valid_suspects(m)  # still the contract's shape: two optional fields added
    # ungraded files (no zone-level vetting fields): nothing added
    assert not set(bv.ZONE_FIELDS) & set(m) and "vetting" not in m
    # a file that doesn't match the contract is skipped; nothing valid gives None
    bad = {"suspects": [{"id": "x"}]}
    assert bv.merge_suspects([("hunt", bad), ("pilot", pilot)])["zone"] == "pilot"
    assert bv.merge_suspects([("hunt", bad)]) is None


def test_merge_suspects_keeps_each_zones_vetting():
    """vetoes, provisional, grades, gates (contracts/api.md suspects.json, 14 Nov) survive the merge per zone, as the
    zone's file has them: the headline zone's at the top level, every graded zone's under `vetting`."""
    def graded(s, grade, reasons):
        return {**s, "grade": grade, "reasons": reasons, "near_source_arcsec": None}

    hunt_v = {"vetoes": ["mpc", "gaia", "catwise"], "provisional": False, "grades": {"A": 0, "B": 1, "C": 1},
              "gates": {"edge": 1, "hot_pixel": 0}}
    pilot_v = {"vetoes": ["mpc", "gaia"], "provisional": True, "grades": {"A": 0, "B": 1, "C": 0}, "gates": {}}
    hunt = {"generated": "2026-11-15T01:00:00Z", "data_release": "DR1", "zone": "hunt", **hunt_v,
            "suspects": [graded(_suspect("hunt-1-1", 121.0, 15.0, 60970.0), "B", []),
                         graded(_suspect("hunt-2-2", 128.0, 22.0, 61140.0), "C", ["edge"])]}
    pilot = {"generated": "2026-11-15T00:30:00Z", "data_release": "DR1", "zone": "pilot", **pilot_v,
             "suspects": [graded(_suspect("pilot-3-3", 122.0, 16.0, 60971.0), "B", [])]}
    m = bv.merge_suspects([("hunt", hunt), ("pilot", pilot)])
    assert {k: m[k] for k in bv.ZONE_FIELDS} == hunt_v               # the headline zone's, as in a one-zone file
    assert m["vetting"] == {"hunt": hunt_v, "pilot": pilot_v}
    assert list(m) == ["generated", "data_release", "zone", *bv.ZONE_FIELDS, "zones", "vetting", "suspects"]
    assert [(s["id"], s["grade"], s["reasons"]) for s in m["suspects"]] == [
        ("hunt-1-1", "B", []), ("hunt-2-2", "C", ["edge"]), ("pilot-3-3", "B", [])]
    assert bv.valid_suspects(m)
    # an ungraded headline zone with a graded one after it: nothing at the top level, the graded zone in `vetting`
    old = {k: v for k, v in hunt.items() if k not in bv.ZONE_FIELDS}
    m2 = bv.merge_suspects([("hunt", old), ("pilot", pilot)])
    assert not set(bv.ZONE_FIELDS) & set(m2) and m2["vetting"] == {"pilot": pilot_v}
    # a partial file keeps what it has, nothing invented
    m3 = bv.merge_suspects([("hunt", {**old, "provisional": True})])
    assert m3["provisional"] is True and m3["vetting"] == {"hunt": {"provisional": True}} and "vetoes" not in m3


# --- V6, V7 -----------------------------------------------------------------------------------

def test_v6_status():
    assert bv.v6_status(30, 40, 0.97) == "pass"
    assert bv.v6_status(20, 40, 1.10) == "pass"
    assert bv.v6_status(19, 40, 1.00) == "partial"  # under half the list
    assert bv.v6_status(30, 40, 1.15) == "partial"
    assert bv.v6_status(30, 40, 0.75) == "fail"
    assert bv.v6_status(4, 40, 1.0) == "fail"
    assert bv.v6_status(0, 0, None) == "pending"


def test_v7_status():
    assert bv.v7_status(8, 10) == "pass"  # of the dwarfs whose predicted shift is visible
    assert bv.v7_status(4, 4) == "partial"  # too few to pass
    assert bv.v7_status(5, 10) == "partial"
    assert bv.v7_status(4, 10) == "fail"
    assert bv.v7_status(0, 0) == "fail"


def _motion_row(name, me, mn, pe, pn, sigma, measured=True, reason=None):
    import math

    import _motion

    if not measured:
        return {"name": name, "source_id": "1", "pm_mas_yr": 3000.0, "measured": False, "reason": reason}
    return {"name": name, "source_id": "1", "g": 14.0, "pm_mas_yr": 3000.0, "measured": True, "reason": None, "det": 5,
            "visits": [1, 3], "dt_yr": 1.0, "shift_arcsec": math.hypot(me, mn), "predicted_arcsec": math.hypot(pe, pn),
            "sigma_arcsec": sigma, "pa_deg": _motion.pa_deg(me, mn), "predicted_pa_deg": _motion.pa_deg(pe, pn),
            "angle_deg": _motion.angle_deg(me, mn, pe, pn), "dev_sigma": math.hypot(me - pe, mn - pn) / sigma,
            "d_east_arcsec": me, "d_north_arcsec": mn, "pred_east_arcsec": pe, "pred_north_arcsec": pn, "n_ref_stars": 50}


def _motion_file(tmp_path, name, rows):
    d = {"generated": "2026-11-15T12:00:00Z", "list_source": "test", "list_cut": "test", "per_visit": 10,
         "spectrum_per_det": 3, "rows": rows}
    (tmp_path / name).write_text(json.dumps(d))


def test_v6_v7_pending_without_input(tmp_path, monkeypatch):
    monkeypatch.setattr(bv, "DATA", tmp_path)
    for c in (bv.v6(), bv.v7()):
        assert c["status"] == "pending" and c["headline"] and set(c) == KEYS


def test_v6_slope_and_note(tmp_path, monkeypatch):
    monkeypatch.setattr(bv, "DATA", tmp_path)
    rows = [_motion_row(f"s{i}", 1.02 * pe, 1.02 * pn, pe, pn, 0.5) for i, (pe, pn) in
            enumerate([(-5.0, 1.0), (3.0, 4.0), (0.5, -6.0), (2.0, 2.0)] * 3)]
    rows.append(_motion_row("lost", 0, 0, 0, 0, 1, measured=False, reason="not_detected"))
    _motion_file(tmp_path, bv.V6_FILE, rows)
    c = bv.v6()
    assert c["value"] == pytest.approx(1.02, abs=1e-3)
    assert c["status"] == "pass"  # 12 of 13 measured, slope within 0.10
    assert c["headline"].startswith("12 of 13 fastest Gaia stars measured: slope 1.02")
    assert "1 not detected" in c["note"]
    tail = c["note"].rsplit("Rows:", 1)[1]
    assert ". " not in tail and all(k in tail for row in c["rows"] for k in row)


def test_v7_counts_the_predicted_direction_of_the_visible_ones(tmp_path, monkeypatch):
    monkeypatch.setattr(bv, "DATA", tmp_path)
    rows = [_motion_row("a", -2.0, 0.1, -2.1, 0.0, 0.4),     # predicted 5 sigma; measured 5 sigma, 3 deg: agrees
            _motion_row("b", 0.0, 2.0, -2.0, 0.0, 0.4),      # predicted 5 sigma; 90 deg off
            _motion_row("c", -0.6, 0.0, -1.0, 0.0, 0.4),     # predicted 2.5 sigma: not visible, left out
            _motion_row("d", 1.5, 1.5, 1.4, 1.6, 0.3),       # predicted 7 sigma; agrees
            _motion_row("e", -1.3, 0.0, -1.0, 0.0, 0.4)]     # predicted 2.5 sigma: left out though it "agrees"
    _motion_file(tmp_path, bv.V7_FILE, rows)
    c = bv.v7()
    assert c["value"] == 2 and c["unit"] == "objects"
    assert c["status"] == "partial"  # 2 of 3 visible: 67%, and fewer than 5
    assert c["headline"].startswith("2 of 3 brown dwarfs whose motion SPHEREx can show in one year")
    assert "of all 5 measured, 4 agree with the predicted shift within 3 sigma" in c["headline"]
    assert "every measured dwarf: 3 of 5 shift the predicted way" in c["note"]  # the first rule stays in the note
    assert "Visible but not the predicted way: b: 2.0\" for 2.0\"" in c["note"]
    assert "The other 2 are predicted to move 1.0-1.0\" between the visits against an error of 0.4-0.4\"" in c["note"]


def test_v7_visible_uses_the_prediction_not_the_measurement():
    import _motion

    assert _motion.visible({"predicted_arcsec": 1.2, "sigma_arcsec": 0.4})
    assert not _motion.visible({"predicted_arcsec": 1.1, "sigma_arcsec": 0.4, "shift_arcsec": 5.0})
    assert not _motion.visible({"predicted_arcsec": None, "sigma_arcsec": 0.4})


def test_v6_reports_the_slope_without_blends_but_the_status_uses_all(tmp_path, monkeypatch):
    monkeypatch.setattr(bv, "DATA", tmp_path)
    rows = [_motion_row(f"s{i}", pe, pn, pe, pn, 0.5) for i, (pe, pn) in enumerate([(-5.0, 1.0), (3.0, 4.0)] * 3)]
    bad = _motion_row("Gaia DR3 9 (2MASS J1)", -0.1, 0.05, -2.3, 1.1, 0.4)  # followed a static neighbour
    bad.update(blend_arcsec=7.7, blend_g=14.0, g=16.2)
    _motion_file(tmp_path, bv.V6_FILE, rows + [bad])
    c = bv.v6()
    assert c["value"] < 1.0  # every measured star counts
    assert "without the 1 blended with a brighter star, slope 1.0 +- 0.0" in c["headline"]
    assert "Gaia DR3 9 (G 16.2): G 14.0 at 7.7\", measured 0.11\" for 2.55\"" in c["note"]
    assert c["rows"][-1]["blend_arcsec"] == 7.7


def test_update_only_replaces_just_those_checks(tmp_path, monkeypatch):
    monkeypatch.setattr(bv, "DATA", tmp_path)
    out = tmp_path / "validation.json"
    keep = {"id": "V5", "claim": "x", "status": "pass", "headline": "kept", "value": 1, "unit": None, "expected": "",
            "note": None, "rows": []}
    old6 = dict(keep, id="V6", status="pending", headline="Cut")
    out.write_text(json.dumps({"generated": "2026-11-14", "data_release": "DR1", "checks": [keep, old6]}))
    _motion_file(tmp_path, bv.V6_FILE, [_motion_row(f"s{i}", 2.0, 1.0, 2.0, 1.0, 0.4) for i in range(6)])
    bv.update_only(out, ["V6"])
    d = json.loads(out.read_text())
    assert [c["id"] for c in d["checks"]] == ["V5", "V6"]
    assert d["checks"][0] == keep and d["checks"][1]["status"] == "pass"
    with pytest.raises(SystemExit):
        bv.update_only(out, ["V5"])  # a zone check needs its reports
