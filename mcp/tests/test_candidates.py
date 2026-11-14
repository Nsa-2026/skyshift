import math

import pytest

from skyshift_mcp import candidates as cands


def test_separation_arcmin():
    assert cands.separation_arcmin(10, 0, 10, 1) == pytest.approx(60.0)
    assert cands.separation_arcmin(0, 60, 1, 60) == pytest.approx(30.0, rel=1e-3)  # cos 60° = 0.5
    assert cands.separation_arcmin(359.9, 0, 0.1, 0) == pytest.approx(12.0, rel=1e-6)


def test_select_kind_and_score(sample):
    movers = cands.select(sample, kind="mover")
    assert movers and all(c["kind"] == "mover" for c in movers)
    good = cands.select(sample, min_score=0.5)
    assert all(c["score"] >= 0.5 for c in good)
    with pytest.raises(ValueError):
        cands.select(sample, kind="planet")


def test_select_known_keeps_null(sample):
    rows = [{"id": "a", "nearest_known_arcsec": None}, {"id": "b", "nearest_known_arcsec": 50.0},
            {"id": "c", "nearest_known_arcsec": 500.0}]
    assert [c["id"] for c in cands.select(rows, min_known_arcsec=100)] == ["a", "c"]


def test_select_near_adds_distance(sample):
    c0 = sample[0]
    rows = cands.select(sample, near=(c0["ra"], c0["dec"], 1.0))
    assert rows[0]["distance_arcmin"] == pytest.approx(0.0, abs=1e-6)
    assert all(r["distance_arcmin"] <= 1.0 for r in rows)
    assert "distance_arcmin" not in c0  # input not mutated


def test_rank_grade_then_score():
    rows = [{"id": "c", "grade": "C", "score": 0.99}, {"id": "n", "grade": None, "score": 0.5},
            {"id": "a", "grade": "A", "score": 0.2}, {"id": "a2", "grade": "A", "score": 0.9}]
    assert [r["id"] for r in cands.rank(rows)] == ["a2", "a", "n", "c"]


def test_rank_rate_uses_pm_for_stars():
    rows = [{"id": "star", "rate_arcsec_h": None, "pm_mas_yr": 5000.0},
            {"id": "mover", "rate_arcsec_h": 30.0, "pm_mas_yr": None}]
    assert [r["id"] for r in cands.rank(rows, "rate")] == ["mover", "star"]
    with pytest.raises(ValueError):
        cands.rank(rows, "brightness")


def test_rank_rate_never_compares_arcsec_per_hour_with_mas_per_year():
    rows = [{"id": "fast_star", "kind": "high_pm", "rate_arcsec_h": None, "pm_mas_yr": 9000.0, "score": 0.9},
            {"id": "slow_mover", "kind": "mover", "rate_arcsec_h": 0.5, "pm_mas_yr": None, "score": 0.1},
            {"id": "spec", "kind": "spectral", "rate_arcsec_h": None, "pm_mas_yr": None, "score": 0.99},
            {"id": "star", "kind": "high_pm", "rate_arcsec_h": None, "pm_mas_yr": 300.0, "score": 0.2},
            {"id": "mover", "kind": "mover", "rate_arcsec_h": 40.0, "pm_mas_yr": None, "score": 0.3}]
    assert [r["id"] for r in cands.rank(rows, "rate")] == ["mover", "slow_mover", "fast_star", "star", "spec"]
    assert "mas/yr" in cands.RATE_NOTE and "″/h" in cands.RATE_NOTE


def test_has_grades(sample):
    assert not cands.has_grades(sample)
    assert cands.has_grades([{"grade": None}, {"grade": "B"}])


def test_summarise_flags_static_frames(sample):
    s = cands.summarise({**sample[0], "frames_url": "cand/x/frames.json"})
    assert s["has_static_frames"] is True and "frames_url" not in s
    assert not math.isnan(s["score"])
