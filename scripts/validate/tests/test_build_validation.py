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
