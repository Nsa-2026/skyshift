"""Mover flaws (16 Nov, docs/research/2026-11-16-engine-flaws-from-vetting.md): a hot pixel lit at other sky places,
the same pixel in two exposures, whole-pixel centroids, wide dots, a pair across detectors, the motion's significance,
a fixed source or Gaia star at the spot, a bright star's glow, two Gaia stars either side, exposures that photographed
the track without a dot; their warnings and levels (warn_levels) in suspects.json."""

import json

import numpy as np
import pandas as pd

from skyshift_pipeline import config as C
from skyshift_pipeline import link as LK
from skyshift_pipeline import suspects as S
from skyshift_pipeline import vet as V
from skyshift_pipeline.sky import offset_radec

from test_photolist import square
from test_vet import NO_PRED, _run_zone, clean, mover

OVERFLOW = 1 << 1


def free_at(xy, ra, dec, n, det=4, prefix="F"):
    """Free dots on the pixels xy in n exposures, all at the sky place (ra, dec)."""
    rows = [dict(obs=f"{prefix}{j}", det=det, x=x, y=y, ra=ra, dec=dec) for j in range(n) for x, y in xy]
    return pd.DataFrame(rows)


def test_hot_pixel_counts_only_other_sky_places():
    s = pd.DataFrame([clean(0)])  # dots (1000, 1000.37) in E1 and (1003, 885.37) in E2, D4, at ra 121, dec 15
    far = free_at([(1000.4, 1000.0)], 122.0, 15.0, 5)
    out, st = V.vet(s, s, far, None, None)
    assert st["hot_pixel_map"] == "other_sky" and out["reasons"][0] == ["hot_pixel"]
    assert out["hot_exposures_max"][0] == 5 and out["hot_exposures_min"][0] == 0
    w = S.Warnings(1)
    S.gate_warnings(out, w)
    assert w.codes[0] == ["hot_pixel"] and w.levels[0] == {"hot_pixel": "strong"}
    assert w.values[0]["hot_pixel"] == {"exposures_max": 5, "exposures_min": 0, "n_dots": 2, "away_arcsec": 30.0}
    # the same 5 exposures at the mover's own sky place: a source there (a star, Europa), not the detector
    here = free_at([(1000.4, 1000.0)], 121.0 + 10 / 3600, 15.0, 5)
    out, _ = V.vet(s, s, here, None, None)
    assert out["reasons"][0] == [] and out["hot_exposures_max"][0] == 0
    # 3 other places: a warning only
    out, _ = V.vet(s, s, free_at([(1000.4, 1000.0)], 122.0, 15.0, 3), None, None)
    w = S.Warnings(1)
    S.gate_warnings(out, w)
    assert out["reasons"][0] == [] and w.codes[0] == ["hot_pixel"] and w.levels[0] == {}


def test_hot_pixel_proven_when_every_dot_sits_on_one():
    s = pd.DataFrame([clean(0)])
    free = free_at([(1000.4, 1000.0), (1003.2, 885.0)], 122.0, 15.0, 3)
    out, _ = V.vet(s, s, free, None, None)
    assert out["reasons"][0] == ["hot_pixel"] and out["hot_exposures_min"][0] == 3
    w = S.Warnings(1)
    S.gate_warnings(out, w)
    assert w.codes[0] == ["hot_pixel", "hot_pixel_proven"]
    assert w.levels[0] == {"hot_pixel": "strong", "hot_pixel_proven": "strong"}
    assert json.loads(S.warn_columns(w)["warn_levels"][0]) == {"hot_pixel": "strong", "hot_pixel_proven": "strong"}


def test_same_pixel_whole_pixel_wide_and_cross_detector():
    rows = [clean(0), clean(1), clean(2), clean(3, size=(7.0, 7.0)), clean(4, size=(7.0, 7.0), fpk=(OVERFLOW, 0)),
            mover(5, [("E1", 1, 900, 900.4, 300.0, 8.0, 4.6, 0), ("E2", 4, 903, 785.4, 300.0, 8.0, 4.9, 0)]),
            mover(6, [("E1", 4, 1100.2, 1100.4, 300.0, 8.0, 4.8, 0), ("E2", 4, 1100.6, 1100.9, 300.0, 8.0, 4.8, 0)])]
    s = pd.DataFrame(rows)
    s.at[1, "dot_y"] = np.array([1000.0, 885.0], np.float32)  # both dots on whole pixels (x 1010, 1013)
    s.at[2, "dot_y"] = np.array([1000.0, 885.4], np.float32)  # one
    out, st = V.vet(s, s, None, None, None)
    r = dict(zip(out["cell"], out["reasons"]))
    assert r[0] == [] and r[1] == ["whole_pixel"] and r[2] == []
    assert r[3] == ["wide"] and r[4] == []  # 7.0″ = 1.46× a star at S/N 8; one dot saturated: one unsaturated dot left
    assert r[5] == ["cross_detector"] and r[6] == ["same_pixel"]
    assert round(out["same_pixel_px"][6], 2) == 0.5 and out["whole_dots"].tolist()[:3] == [0, 2, 1]
    w = S.Warnings(len(out))
    S.gate_warnings(out, w)
    lv = dict(zip(out["cell"], w.levels))
    codes = dict(zip(out["cell"], w.codes))
    assert codes[1] == ["whole_pixel"] and lv[1] == {"whole_pixel": "strong"}
    assert codes[2] == ["whole_pixel"] and lv[2] == {}
    assert codes[3] == ["wide"] and lv[3] == {"wide": "strong"}
    assert codes[4] == ["wide"] and lv[4] == {}  # its median is wide: a warning
    assert codes[5] == ["cross_detector"] and lv[5] == {"cross_detector": "strong"}
    assert codes[6] == ["same_pixel"] and lv[6] == {"same_pixel": "strong"}
    assert st["gates"]["whole_pixel"] == 1 and st["gates"]["wide"] == 1


def test_motion_sigma_and_the_whole_pixel_error():
    s = pd.DataFrame([clean(0), clean(1)])  # two exposures 1 minute apart, S/N 8
    s["rate_arcsec_h"] = 30.0
    s.at[1, "dot_y"] = np.array([1000.0, 885.0], np.float32)
    sig = float(LK.dot_sigma_arcsec(8.0))
    ms = V.motion_sigma(s)
    assert abs(ms[0] - 30.0 / (sig * np.sqrt(2) * 60.0)) < 1e-6
    sig_w = np.hypot(sig, C.LINK_WHOLE_PIXEL_SIGMA_PX * C.PIXEL_ARCSEC)
    assert abs(ms[1] - 30.0 / (sig_w * np.sqrt(2) * 60.0)) < 1e-6
    out, _ = V.vet(s, s, None, None, None)
    w = S.Warnings(2)
    S.gate_warnings(out, w)
    assert "may_not_move" in w.codes[0] and w.values[0]["may_not_move"]["sigma"] == round(ms[0], 1)
    one = pd.DataFrame([mover(2, [("E1", 1, 900, 900.4, 300.0, 8.0, 4.6, 0), ("E1", 4, 900, 900.4, 300.0, 8.0, 4.9, 0)])])
    one["rate_arcsec_h"] = np.nan
    assert np.isnan(V.motion_sigma(one)[0])


def test_link_track_fit_takes_the_whole_pixel_error():
    ra, dec, mjd = [121.0, 121.0 + 1 / 3600], [15.0, 15.0], [61000.3, 61000.3 + 2 / 1440]
    a = LK.track_fit(ra, dec, mjd, [0, 1], [4, 4], [300.0, 300.0], [20.0, 20.0])
    b = LK.track_fit(ra, dec, mjd, [0, 1], [4, 4], [300.0, 300.0], [20.0, 20.0], whole=[True, True])
    assert b["motion_snr"] < 0.5 * a["motion_snr"] and b["rate_arcsec_h"] == a["rate_arcsec_h"]


def _flaw_rows():
    """Suspects a–h at ra 121 + 0.05 k, dec 15 (D4 unless said), flux 300 µJy."""
    rows = [clean(k) for k in range(8)]
    s = pd.DataFrame(rows)
    s["detectors"] = "4"
    s["paired_exposures"] = 0
    s["n_exposures"] = 2
    return s


def _east(s, k, arcsec):
    return offset_radec(s.loc[k, "ra"], s.loc[k, "dec"], arcsec, 0.0)


def test_fixed_source_gaia_glow_and_blend():
    s = _flaw_rows()
    fx = []
    for k, sep, ratio in ((0, 7.0, 2.0), (1, 3.0, 0.49), (2, 8.0, 0.8), (3, 8.8, 0.32), (5, 12.0, 5.0)):
        r, d = _east(s, k, sep)
        fx.append(dict(ra=r, dec=d, snr=90.0, flux_ujy=ratio * float(s.loc[k, "flux_ujy"])))
    fixed = pd.DataFrame(fx)
    flaws = pd.DataFrame({"motion_sigma": [5.0, 1.9, 1.5, 4.0, 2.0, 1.0, 5.0, 5.0],
                          "wide_ratio_med": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.5, 1.0]})
    gaia = pd.DataFrame({"sep": [np.inf, 2.95, np.inf, 8.7, 1.0, np.inf, np.inf, np.inf], "g_mag": [np.nan, 16.4, np.nan, 15.1,
                                                                                              17.0, np.nan, np.nan, np.nan]})
    s.loc[6, "detectors"] = "6"
    g5 = offset_radec(s.loc[6, "ra"], s.loc[6, "dec"], 300.0, 0.0)
    b1, b2 = _east(s, 7, 6.0), _east(s, 7, -6.0)
    ref = pd.DataFrame([dict(cat="gaia", ra=g5[0], dec=g5[1], epoch_mjd=s.loc[0, "mjd"], pmra=0.0, pmdec=0.0, g_mag=5.0),
                        dict(cat="gaia", ra=b1[0], dec=b1[1], epoch_mjd=s.loc[0, "mjd"], pmra=0.0, pmdec=0.0, g_mag=15.0),
                        dict(cat="gaia", ra=b2[0], dec=b2[1], epoch_mjd=s.loc[0, "mjd"], pmra=0.0, pmdec=0.0, g_mag=15.5)])
    w = S.Warnings(len(s))
    st = S.mover_flaws(s, fixed, ref, {"gaia": gaia}, None, C.ZONES["pilot"], flaws, w)
    lv = [w.levels[k].get("fixed_source") for k in range(len(s))]
    # a: as bright as the mover; b: Seraphina (a star 3″ away at 0.49× and 1.9σ: the dot is 2× the star) a warning;
    # c: 0.8× and 1.5σ; d: Erato (8.8″, 0.32×, 4σ) a warning; e: a Gaia star 1″ away and 2σ; f: 12″ away: nothing
    assert lv[:6] == ["strong", None, "strong", None, "strong", None]
    assert "fixed_source" in w.codes[1] and "fixed_source" in w.codes[3] and "fixed_source" not in w.codes[5]
    assert w.values[1]["fixed_source"] == {"sep_arcsec": 3.0, "flux_ratio": 0.49, "fixed_snr": 90, "motion_sigma": 1.9}
    assert w.values[4]["fixed_source"] == {"gaia_sep_arcsec": 1.0, "gaia_g_mag": 17.0, "motion_sigma": 2.0}
    assert list(np.flatnonzero(w.gates["fixed_source"])) == [0, 2, 4] and st["fixed_source"] == {"warn": 5, "strong": 3}
    # g: one detector (D6), no partner, median 1.5× a star, a G 5 star 5′ away
    assert w.codes[6] == ["glow"] and w.levels[6] == {"glow": "strong"} and w.values[6]["glow"]["star_sep_arcsec"] == 300.0
    assert list(np.flatnonzero(w.gates["glow"])) == [6]
    # h: two Gaia stars 6″ either side: a blend warning
    assert w.codes[7] == ["blend"] and w.levels[7] == {} and w.values[7]["blend"]["apart_arcsec"] == 12.0


def test_unseen_counts_exposures_near_stars_too():
    s = _flaw_rows().iloc[[0]].reset_index(drop=True)  # dots in E1 (t0) and E2 (t0 + 1 min), D4
    s["session"] = 3
    s["rate_arcsec_h"], s["pa_deg"] = 30.0, 90.0
    t = [float(x) for x in s.loc[0, "dot_mjd"]]
    ra, dec = float(s.loc[0, "ra"]), float(s.loc[0, "dec"])
    photos = pd.DataFrame({"obs_id": ["E1", "E2", "E3", "E4"], "detector": 4, "session": 3,
                           "mjd": [t[0], t[1], t[1] + 1 / 1440, t[1] + 2 / 1440], "s_region": square(ra, dec, half=0.5)})
    cover, unseen = S.unseen_exposures(s, photos, C.ZONES["pilot"])
    assert cover[0] == 4 and unseen[0] == 2
    w = S.Warnings(1)
    flaws = pd.DataFrame({"motion_sigma": [5.0], "wide_ratio_med": [1.0]})
    S.mover_flaws(s, pd.DataFrame(columns=["ra", "dec", "snr", "flux_ujy"]), None, None, photos, C.ZONES["pilot"], flaws, w)
    assert w.codes[0] == ["unseen"] and w.levels[0] == {"unseen": "strong"} and w.values[0]["unseen"] == {"exposures": 2, "cover": 4}
    # one exposure missed: a warning; a one-exposure mover is not measured
    cover, unseen = S.unseen_exposures(s, photos.iloc[:3], C.ZONES["pilot"])
    assert unseen[0] == 1
    s1 = s.assign(n_exposures=1)
    assert S.unseen_exposures(s1, photos, C.ZONES["pilot"])[1][0] == -1


def test_run_writes_warn_levels_and_new_reasons(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "VET_ALLWISE", False)
    store, z, m = _run_zone(tmp_path)
    m = m.copy()
    m.at[1, "dot_size"] = np.array([7.0, 7.0], np.float32)  # cell 1: wide (both dots 1.46× a star)
    store.write_parquet("results/pilot/movers.parquet", m)
    S.run(store, z, pred=NO_PRED)
    j = store.read_json("results/pilot/suspects.json")
    by = {x["cell"]: x for x in j["suspects"]}
    assert by[1]["grade"] == "C" and by[1]["reasons"] == ["wide"]
    assert by[1]["warn_levels"] == {"wide": "strong"} and by[1]["warn_values"]["wide"]["ratio_min"] == 1.46
    assert by[2]["warn_levels"] == {} and j["gates"]["wide"] == 1
    p = store.read_parquet("results/pilot/suspects.parquet")
    assert set(V.FLAW_COLUMNS) <= set(p.columns) and "warn_levels" in p


def test_a_track_overrules_the_single_session_separators(tmp_path, monkeypatch):
    """On a track that makes A (>= 2 sessions, >= 5σ), wide (one of VET_TRACK_OVERRULES) no longer makes C: grade A, no
    reasons, the check a warning with its numbers. A reason outside the list still makes C."""
    monkeypatch.setattr(C, "VET_ALLWISE", False)
    store, z, m = _run_zone(tmp_path)
    m = m.copy()
    m.at[1, "dot_size"] = np.array([7.0, 7.0], np.float32)
    store.write_parquet("results/pilot/movers.parquet", m)
    S.run(store, z, pred=NO_PRED)
    p = store.read_parquet("results/pilot/suspects.parquet")
    ids = list(p["id"])
    tr = pd.DataFrame({"mover_id": ids, "tracklet_id": np.arange(len(ids)), "n_sessions": 3, "rate_arcsec_h": 30.0,
                       "pa_deg": 0.0, "rate_sigma": 1.0, "rate_snr": 30.0})
    store.write_parquet("results/pilot/tracklets.parquet", tr)
    st = S.run(store, z, pred=NO_PRED)
    j = store.read_json("results/pilot/suspects.json")
    by = {x["cell"]: x for x in j["suspects"]}
    assert by[1]["grade"] == "A" and by[1]["reasons"] == [] and by[1]["warn_levels"] == {}
    assert "wide" in by[1]["warn_codes"] and by[1]["warn_values"]["wide"]["ratio_min"] == 1.46
    assert st["vetting"]["tracklets"]["track_overruled"] == 1
    kept_c = [x for x in j["suspects"] if x["grade"] == "C"]
    assert kept_c and all(set(x["reasons"]) - set(C.VET_TRACK_OVERRULES) for x in kept_c)
