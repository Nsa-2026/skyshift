"""scripts/candidates/frames.py helpers (no network). Run from server/, like the script:

    cd server && uv run pytest ../scripts/candidates/tests
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import frames as F  # noqa: E402

MIN = 1.0 / 1440.0


def cand(cid: str, kind: str = "mover", score: float = 0.5, grade=None, **kw) -> dict:
    return {"id": cid, "kind": kind, "score": score, "grade": grade, "det": 2, "mjd": 61000.0,
            "reasons": ["two_exposures"], "why": "", **kw}


def epochs(*minutes: float) -> list[dict]:
    return [{"mjd_mid": 61000.0 + m * MIN} for m in minutes]


def test_choose_top_scores_then_every_a_b_mover_never_c():
    cs = [cand("m1", score=0.9), cand("m2", score=0.8), cand("m3", score=0.7, grade="C"),
          cand("m4", score=0.1, grade="B"), cand("m5", score=0.05, grade="A"), cand("m6", score=0.95, grade="C"),
          cand("s1", kind="shape", score=0.4), cand("p1", kind="high_pm", score=0.3, det=None)]
    got = [c["id"] for c in F.choose(cs, {"mover": 2, "shape": 1, "high_pm": 5})]
    # top 2 movers by score (grade C left out), the shape, then A and B whatever --top says
    assert got == ["m1", "m2", "s1", "m5", "m4"]
    assert [c["id"] for c in F.choose(cs, {"mover": 0})] == ["m5", "m4"]
    assert "shape=15" in F.DEFAULT_TOP


def test_own_times_from_the_why():
    two = cand("a", why="Seen at different places in 2 exposures 4.3 minutes apart, moving about 49″ an hour; ...")
    assert [round((t - 61000.0) / MIN, 2) for t in F.own_times(two)] == [-2.15, 2.15]
    three = cand("b", why="Seen at different places in 3 exposures over 6.4 minutes, moving about 38″ an hour; ...")
    assert [round((t - 61000.0) / MIN, 2) for t in F.own_times(three)] == [-3.2, 3.2]
    det2 = cand("c", reasons=["two_detectors"], why="Seen by detectors D2 and D5 at the same moment, in one exposure only; ...")
    assert F.own_times(det2) == [61000.0]
    assert F.own_times(cand("d", why="something else")) is None


def test_missing_own_exposures():
    c = cand("a", why="Seen at different places in 2 exposures 4.3 minutes apart; ...")
    assert F.missing_own(c, epochs(-2.15, 2.15, 4.3)) is None
    # the review's cases: one frame only, or the second exposure missing
    assert "+2.15 min" in F.missing_own(c, epochs(-2.15))
    assert "+2.15 min" in F.missing_own(c, epochs(-4.32, -2.15))
    c2 = cand("b", why="Seen at different places in 2 exposures 2.1 minutes apart; ...")
    assert "-1.05 min" in F.missing_own(c2, epochs(1.07, 3.22))
    # no span in the why: an exposure on each side of mjd within the session
    c3 = cand("c", why="")
    assert F.missing_own(c3, epochs(-1.07, 1.07)) is None
    assert F.missing_own(c3, epochs(1.07, 3.22)) is not None
    assert F.missing_own(c3, epochs(-30.0, 1.07)) is not None


def test_drop_without_own(tmp_path):
    good = cand("good", why="in 2 exposures 2.1 minutes apart")
    bad = cand("bad", why="in 2 exposures 2.1 minutes apart")
    star = cand("star", kind="high_pm")
    for c, eps in ((good, epochs(-1.07, 1.07)), (bad, epochs(1.07)), (star, epochs(500.0))):
        (tmp_path / c["id"]).mkdir()
        (tmp_path / c["id"] / "frames.json").write_text(json.dumps({"epochs": eps}))
    F.drop_without_own([good, bad, star], tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["good", "star"]


def test_needs_build(tmp_path):
    c = cand("x", why="in 2 exposures 2.1 minutes apart")
    assert F.needs_build(c, tmp_path)
    d = tmp_path / "cand" / "x"
    d.mkdir(parents=True)
    (d / "frames.json").write_text(json.dumps({"epochs": epochs(1.07)}))
    assert F.needs_build(c, tmp_path)
    (d / "frames.json").write_text(json.dumps({"epochs": epochs(-1.07, 1.07)}))
    assert not F.needs_build(c, tmp_path)


def test_needs_build_when_the_detector_changed(tmp_path):
    """A re-export moved hunt-pm-63015007016600 from D4 to D5; its D4 frames must be rebuilt."""
    c = cand("s", kind="high_pm", det=5)
    d = tmp_path / "cand" / "s"
    d.mkdir(parents=True)
    (d / "frames.json").write_text(json.dumps({"epochs": [{"mjd_mid": 61000.0, "det": 4}, {"mjd_mid": 61180.0, "det": 4}]}))
    assert F.needs_build(c, tmp_path)
    (d / "frames.json").write_text(json.dumps({"epochs": [{"mjd_mid": 61000.0, "det": 5}, {"mjd_mid": 61180.0, "det": 5}]}))
    assert not F.needs_build(c, tmp_path)


@pytest.mark.parametrize("attempts,ok,refreshes", [
    ([(-1.07, 1.07)], True, [False]),                  # first build has its exposures
    ([(1.07,), (-1.07, 1.07)], True, [False, True]),   # partial search, then the full one
    ([(1.07,), (1.07,)], False, [False, True]),        # still partial: skipped
    ([None, None], False, [False, True]),              # no frames at all
])
def test_build_mover_checked_retries_once(monkeypatch, attempts, ok, refreshes):
    c = cand("x", why="in 2 exposures 2.1 minutes apart")
    calls = []

    def fake(c_, cache, refresh=False):
        calls.append(refresh)
        got = attempts[len(calls) - 1]
        return None if got is None else ({"epochs": epochs(*got)}, {})

    monkeypatch.setattr(F, "build_mover", fake)
    assert (F.build_mover_checked(c, cache=None) is not None) == ok
    assert calls == refreshes
