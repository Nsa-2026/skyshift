"""rb_score (rb_score.py): features from a suspects row, the stored trees, exemption and the order inside a tier."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from skyshift_pipeline import candidates as K
from skyshift_pipeline import rb_score as RB


def _row(i: int, **kw) -> dict:
    """A two-exposure D2 mover with its dot lists (link's columns)."""
    r = {"id": f"hunt-{i}-1", "ra": 125.0 + i * 0.01, "dec": 20.0, "mjd": 61000.0, "session": 1, "visit": 1, "det": 2,
         "detectors": "2", "n_dots": 2, "n_exposures": 2, "evidence": "two_exposures", "snr": 20.0, "flux_ujy": 500.0,
         "flags": 0, "obs_ids": "", "cell": 1, "nearest_known_arcsec": np.nan, "gaia_checked": True,
         "catwise_checked": True, "rate_arcsec_h": 80.0, "pa_deg": 90.0, "flux_ratio": 1.1, "dup_dots": 0,
         "cover_exposures": 3, "missed_exposures": 0, "edge_exposures": 0, "still_free": 0, "paired_exposures": 0,
         "near_source_arcsec": np.nan,
         "dot_det": np.array([2, 2]), "dot_x": np.array([100.3, 215.7]), "dot_y": np.array([50.2, 51.6]),
         "dot_mjd": np.array([61000.0, 61000.0015]), "dot_flux": np.array([500.0, 520.0]),
         "dot_snr": np.array([80.0, 85.0]), "dot_size": np.array([6.0, 6.2]), "dot_chi2": np.array([1.1, 1.3]),
         "dot_fpk": np.array([0, 1 << 19]), "dot_obs": np.array(["a_1", "a_2"], dtype=object)}
    r.update(kw)
    return r


def test_features_from_the_row_alone():
    s = pd.DataFrame([_row(0), _row(1, near_source_arcsec=3.5, flags=(1 << 28), evidence="two_detectors",
                                    dot_det=np.array([2, 5]), dot_fpk=np.array([1 << 21, 1 << 21]),
                                    dot_x=np.array([100.0, 100.0]), dot_y=np.array([50.0, 51.6]))])
    F = RB.features(s)
    assert list(F.columns) == list(RB.FEATURES) and len(F) == 2
    a, b = F.iloc[0], F.iloc[1]
    assert a["log_snr"] == pytest.approx(np.log10(20.0)) and a["n_det"] == 1 and a["has_pair"] == 0
    assert a["near_source"] == RB.NEAR_SOURCE_CAP and b["near_source"] == 3.5
    assert a["fpk_outlier_share"] == 0.5 and a["fpk_source_all"] == 0 and b["fpk_source_all"] == 1
    assert b["has_pair"] == 1 and b["two_detectors"] == 1 and b["flag_halo"] == 1 and a["flag_halo"] == 0
    assert b["whole_share"] == 0.5 and a["whole_share"] == 0     # (100.0, 50.0): a whole pixel
    assert a["cross_detector"] == 0 and b["cross_detector"] == 1
    # no rate, motion significance or known-body column is a feature
    assert not {"rate", "rate_arcsec_h", "motion_snr", "nearest_known_arcsec", "line_resid"} & set(RB.FEATURES)
    # an older table without still_free / paired_exposures: NaN, not an error
    F2 = RB.features(s.drop(columns=["still_free", "paired_exposures", "near_source_arcsec"]))
    assert np.isnan(F2["still_free"]).all() and (F2["near_source"] == RB.NEAR_SOURCE_CAP).all()


def _stump(feature: int, thr: float, lo: float, hi: float, missing_left: bool = True) -> dict:
    return {"feature": [feature, 0, 0], "threshold": [thr, 0.0, 0.0], "left": [1, 0, 0], "right": [2, 0, 0],
            "missing_left": [missing_left, False, False], "leaf": [False, True, True], "value": [0.0, lo, hi]}


def test_trees_predict_like_the_rule():
    m = {"features": list(RB.FEATURES), "baseline": 0.5, "trees": [_stump(0, 1.0, -1.0, 2.0), _stump(1, 3.0, 0.0, 1.0, False)]}
    X = np.full((3, len(RB.FEATURES)), 0.0)
    X[0, 0], X[1, 0], X[2, 0] = 0.5, 1.5, np.nan
    X[:, 1] = [2.0, 4.0, np.nan]
    raw = RB.raw_predict(m, X)
    assert raw == pytest.approx([0.5 - 1.0 + 0.0, 0.5 + 2.0 + 1.0, 0.5 - 1.0 + 1.0])  # NaN: left, then right
    assert RB.predict(m, X) == pytest.approx(1 / (1 + np.exp(-raw)))


def test_stored_model_loads_and_scores():
    m = RB.load_model()
    assert tuple(m["features"]) == RB.FEATURES and m["trees"]
    assert m["trained"]["blind_ids_left_out"] > 0 and m["trained"]["held_out"]["per_zone"]
    s = pd.DataFrame([_row(0), _row(1)])
    p = RB.score_rows(s, m)
    assert ((p >= 0) & (p <= 1)).all()
    s.loc[1, "dot_det"] = None
    assert np.isnan(RB.score_rows(s, m)[1])           # no dot lists: nothing to say


def test_slow_and_wide_are_exempt():
    assert RB.is_slow(4.0) and not RB.is_slow(40.0) and not RB.is_slow(None) and not RB.is_slow(np.nan)
    assert RB.is_slow(None, None, 2)                               # two exposures, no rate in the table: may be slow
    assert RB.is_slow(25.0, 10.0) and not RB.is_slow(25.0, 5.0)        # under 3σ: the motion isn't significant
    assert RB.is_wide({"reasons": ["wide_blend"]}) and RB.is_wide({"checks": [{"code": "wide", "level": "warn"}]})
    assert not RB.is_wide({"reasons": ["two_exposures"], "checks": [{"code": "sharp"}]})


def _m(i, score, rb, grade="B", **kw):
    return {"id": f"m{i}", "kind": "mover", "score": score, "rb_score": rb, "grade": grade, "status": "open",
            "reasons": ["two_exposures"], "checks": [], **kw}


def test_reorder_keeps_exempt_places_and_tiers():
    cs = [_m(0, 0.9, 0.1), _m(1, 0.8, None), _m(2, 0.7, 0.9), _m(3, 0.6, 0.5),
          _m(4, 0.5, 0.99, grade="C"), _m(5, 0.4, 0.2, grade="C"),
          _m(6, 0.3, 0.95, checks=[{"code": "x", "level": "strong"}]),
          _m(7, 0.2, 0.3, reasons=["two_exposures", "motion_not_significant"]),
          {"id": "h", "kind": "high_pm", "score": 0.95}]
    before = {c["id"]: c["score"] for c in cs}
    st = RB.reorder(cs)
    s = {c["id"]: c["score"] for c in cs}
    assert s["m1"] == before["m1"] and s["h"] == before["h"]                # exempt and other kinds: untouched
    assert (s["m2"], s["m3"], s["m0"]) == (0.9, 0.7, 0.6)                    # B: rb order on the B slots
    assert (s["m4"], s["m5"]) == (0.5, 0.4)                                  # C stays C, already in rb order
    assert s["m6"] == 0.3 and s["m7"] == 0.2                                 # a tier of one: nothing to swap
    assert st["ranked"] == 7 and st["exempt"] == 1 and st["score_changed"] == 3
    again = [dict(c) for c in cs]
    RB.reorder(again)
    assert {c["id"]: c["score"] for c in again} == s                        # idempotent
    cs[0]["checks"] = [{"code": "wide", "level": "warn"}]                     # a producer finds it wide since
    RB.reorder(cs)
    assert cs[0]["rb_score"] is None and cs[0]["score"] == 0.6               # exempt from now on, keeps its place


def test_exporter_sets_rb_score_and_exempts(monkeypatch):
    rows = [_row(0), _row(1, rate_arcsec_h=3.0), _row(2, warn_codes=np.array(["wide"], dtype=object),
                                                     warn_values=json.dumps({"wide": {"ratio_med": 1.5}})),
            _row(3, dot_det=None)]
    s = pd.DataFrame(rows)
    a, slow, wide, nodots = K.movers(s, "hunt", None)
    assert 0.0 <= a["rb_score"] <= 1.0
    assert slow["rb_score"] is None and wide["rb_score"] is None and nodots["rb_score"] is None
    assert K.finish(a)["rb_score"] == round(a["rb_score"], 3) and list(K.finish(a)) == list(K.FIELDS)
    monkeypatch.setattr(RB, "MODEL_PATH", RB.MODEL_PATH.with_name("missing.json"))
    RB.load_model.cache_clear()
    try:
        assert all(c["rb_score"] is None for c in K.movers(s, "hunt", None))   # no model: existing order, no error
    finally:
        RB.load_model.cache_clear()


def test_merge_only_reorders_and_leaves_old_files_alone(tmp_path):
    """--merge-only: a file without rb_score keeps its order; with rb_score, movers are reordered inside their tier."""
    def doc(cands):
        return {"generated": "x", "data_release": "DR1", "zones": ["hunt"], "counts": {}, "candidates": cands}
    base = dict(kind="mover", zone="hunt", zones=["hunt"], ra=1.0, dec=2.0, mjd=3.0, clock="hours", det=2, grade="B",
                status="open", known_name=None, reasons=["two_exposures"], labels=[], flags=[], why="", checks=[])
    old = [{**base, "id": "a", "score": 0.9}, {**base, "id": "b", "score": 0.5}]
    out, _ = K.merge_only_doc(doc(old), tmp_path)
    assert [(c["id"], c["score"], c["rb_score"]) for c in out["candidates"]] == [("a", 0.9, None), ("b", 0.5, None)]
    new = [{**base, "id": "a", "score": 0.9, "rb_score": 0.2}, {**base, "id": "b", "score": 0.5, "rb_score": 0.8}]
    out, st = K.merge_only_doc(doc(new), tmp_path)
    assert [(c["id"], c["score"]) for c in out["candidates"]] == [("b", 0.9), ("a", 0.5)]
    assert st["rb_score"]["score_changed"] == 2


def test_comet_exempts_and_keeps_its_place():
    assert RB.is_comet({"checks": [{"code": "known_body", "level": "strong", "values": {"body": "comet"}}]})
    assert not RB.is_comet({"checks": [{"code": "known_body", "level": "strong", "values": {"body": "asteroid"}}]})
    assert not RB.is_comet({"checks": [{"code": "known_nearby", "level": "warn", "values": {"body": "comet"}}]})

    cands = [
        _m(0, 0.9, 0.1),
        _m(1, 0.7, 0.2, checks=[{"code": "known_body", "level": "strong", "values": {"body": "comet"}}]),
        _m(2, 0.5, 0.95),
        _m(3, 0.4, 0.8, status="known", checks=[{"code": "known_body", "level": "strong", "values": {"body": "asteroid"}}]),
    ]
    st = RB.reorder(cands)
    by = {c["id"]: c for c in cands}
    assert by["m1"]["rb_score"] is None and by["m1"]["score"] == 0.7
    assert by["m3"]["rb_score"] == 0.8 and by["m3"]["score"] == 0.4
    assert by["m2"]["score"] == 0.9 and by["m0"]["score"] == 0.5
    assert st["score_changed"] == 2

    s = pd.DataFrame([_row(0), _row(1)])
    comet_cand, other_cand = K.movers(s, "hunt", None, comets={s.loc[0, "id"]: "306P/LINEAR"})
    assert comet_cand["rb_score"] is None and other_cand["rb_score"] is not None
    plain_a, _ = K.movers(s, "hunt", None)
    assert comet_cand["score"] == plain_a["score"]
