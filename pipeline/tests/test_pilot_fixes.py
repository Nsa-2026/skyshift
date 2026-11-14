"""Pilot fixes (14 Nov): light-curve error model, band labels, fast-mover vetting. Synthetic data only."""

import numpy as np
import pandas as pd

from skyshift_pipeline import classify as CL
from skyshift_pipeline import config as C
from skyshift_pipeline import highpm as HP
from skyshift_pipeline import lightcurves as LC
from skyshift_pipeline.sky import Footprint, offset_radec

from test_motion import STAR, hp_sources
from test_photolist import square


# --- 1. light curves: error model -------------------------------------------------------------
K_TRUE, FLOOR_TRUE = 1.3, 0.03


def lc_points(rng, n_stars=300, n_pts=36, det=1, origin="dot", sid0=0):
    """Constant stars whose real scatter is (1.3 σ)² + (3% flux)², σ = the quoted error; the model is the truth."""
    f0 = np.exp(rng.uniform(np.log(300), np.log(30000), n_stars))
    snr = np.exp(rng.uniform(np.log(8), np.log(150), n_stars))
    rows = []
    for i in range(n_stars):
        e = f0[i] / snr[i]
        sd = np.hypot(K_TRUE * e, FLOOR_TRUE * f0[i])
        mjd = 61000 + np.arange(n_pts) * 0.5 + np.where(np.arange(n_pts) >= n_pts // 2, 182.0, 0.0)
        f = f0[i] + rng.normal(0, sd, n_pts)
        rows.append(pd.DataFrame({
            "source_id": sid0 + i, "ref_id": "" if origin == "dot" else f"gaia:{sid0 + i}", "origin": origin,
            "mjd": mjd, "visit": np.where(np.arange(n_pts) >= n_pts // 2, 2, 1).astype(np.int16),
            "detector": np.int8(det), "wavelength_um": np.float32(0.9), "flux_ujy": f, "flux_err_ujy": e,
            "detected": True, "model_ujy": f0[i], "rel_flux": f / f0[i]}))
    return pd.concat(rows, ignore_index=True)


def run_model(p, crossids):
    ref = LC.reference_points(p, crossids)
    cal, model = LC.fit_error_model(p, ref)
    p = p.assign(flux_err_cal_ujy=cal)
    ok, bad = LC.photo_check(p, ref, cal)
    p = p.assign(photo_ok=ok)
    return p, LC.summarise(p), model, bad


def test_error_model_recovers_scale_and_floor_and_quiets_constant_stars(rng):
    p = pd.concat([lc_points(rng), lc_points(rng, origin="forced", sid0=10_000)], ignore_index=True)
    xid = pd.DataFrame({"source_id": np.arange(300), "gaia_ref_id": [f"gaia:{i}" for i in range(300)]})
    p, s, model, bad = run_model(p, xid)
    for key in ("dot_D1", "forced_D1"):
        m = model[key]
        assert m["basis"] == "gaia" and m["groups"] == 300
        assert abs(m["k"] - K_TRUE) < 0.15 and abs(m["floor"] - FLOOR_TRUE) < 0.006, m
        assert m["median_chi2_nu_before"] > 1.5
    assert abs(s["chi2_nu"].median() - 1.0) < 0.05 and s["chi2_nu_raw"].median() > 1.5
    # with the quoted errors many constant stars pass the chi2 cut; with the model almost none
    assert (s["chi2_nu_raw"] > C.LC_VARIABLE_CHI2_NU).mean() > 0.1
    assert (s["chi2_nu"] > C.LC_VARIABLE_CHI2_NU).mean() < 0.01 and s["variable"].mean() < 0.01
    assert not bad
    # the calibrated error is never NaN and grows with the floor at high S/N
    assert np.isfinite(p["flux_err_cal_ujy"]).all()


def test_error_model_keeps_a_real_variable_and_ignores_one_outlier(rng):
    p = lc_points(rng, n_stars=200)
    xid = pd.DataFrame({"source_id": np.arange(200), "gaia_ref_id": "gaia:x"})
    var = lc_points(rng, n_stars=1, sid0=900)
    var["flux_ujy"] = var["model_ujy"] * (1 + 0.3 * np.sin(np.arange(len(var))))  # ±30%
    hit = lc_points(rng, n_stars=1, sid0=901)
    hit.loc[5, "flux_ujy"] = hit.loc[5, "model_ujy"] * 3  # a single cosmic-ray-like point
    for q in (var, hit):
        q["rel_flux"] = q["flux_ujy"] / q["model_ujy"]
    p, s, _, _ = run_model(pd.concat([p, var, hit], ignore_index=True), xid)
    s = s.set_index("source_id")
    assert s.loc[900, "variable"]
    assert s.loc[901, "chi2_nu"] > C.LC_VARIABLE_CHI2_NU and not s.loc[901, "variable"]


def test_too_few_light_curves_leave_the_errors_alone(rng):
    p = lc_points(rng, n_stars=C.LC_ERR_MIN_GROUPS - 1)
    p, s, model, _ = run_model(p, None)
    assert model["dot_D1"]["basis"] == "none"
    assert np.allclose(p["flux_err_cal_ujy"], p["flux_err_ujy"])
    assert np.allclose(s["chi2_nu"], s["chi2_nu_raw"])


def test_a_photo_off_as_a_group_is_left_out(rng):
    p = lc_points(rng, n_stars=200, origin="forced")
    t_bad = p["mjd"].iloc[3]
    m = p["mjd"] == t_bad
    p.loc[m, "flux_ujy"] *= 0.80  # every star 20% low in one photo (e.g. forced positions off)
    p["rel_flux"] = p["flux_ujy"] / p["model_ujy"]
    p, s, _, bad = run_model(p, None)
    assert [b["mjd"] for b in bad] == [round(t_bad, 6)] and bad[0]["median_chi"] < -C.LC_PHOTO_MAX_MEDIAN_CHI
    assert not p.loc[m, "photo_ok"].any() and p.loc[~m, "photo_ok"].all()
    assert s["variable"].mean() < 0.02


# --- 2. band labels ---------------------------------------------------------------------------
def labels_frame(rows):
    base = {"class": "unsure", "gaia_sep_arcsec": np.nan, "gaia_g": np.nan, "gaia_bp_rp": np.nan,
            "j_m46_ab": 0.5, "extended_score": 0.0}
    for name, (kind, _, _) in C.INDEX_WINDOWS.items():
        base[f"{name}_{'depth' if kind == 'abs' else 'excess'}"] = 0.0
        base[f"{name}_sig"] = 0.0
    return pd.DataFrame([{**base, **r} for r in rows])


def test_ice_label_guards():
    ice = {"h2o_ice_305_depth": 0.3, "h2o_ice_305_sig": 8.0}
    df = labels_frame([
        ice,                                                                         # 0 reddened, no Gaia: ice
        {**ice, "gaia_sep_arcsec": 0.3, "gaia_g": 15.0, "gaia_bp_rp": 2.8},          # 1 M dwarf (cool)
        {**ice, "h2o_14_depth": 0.1, "h2o_14_sig": 4.0},                             # 2 water vapour seen
        {**ice, "class": "galaxy"},                                                  # 3 galaxy
        {**ice, "j_m46_ab": -1.4},                                                   # 4 not reddened
        {**ice, "extended_score": 1.8},                                              # 5 extended
        {**ice, "h2o_ice_305_depth": 0.05},                                          # 6 too shallow
        {**ice, "gaia_sep_arcsec": 0.3, "gaia_g": 16.0, "gaia_bp_rp": 1.5},          # 7 warm Gaia star, reddened: ice
        ice,                                                                         # 8 saturated
    ])
    sat = np.zeros(len(df), bool)
    sat[8] = True
    lab = CL.band_labels(df, sat)["label_h2o_ice"]
    assert lab.tolist() == [True, False, False, False, False, False, False, True, False]


def test_methane_label_needs_no_gaia_red_and_a_second_band():
    ch4 = {"ch4_33_depth": 0.4, "ch4_33_sig": 9.0, "ch4_16_depth": 0.2, "ch4_16_sig": 4.0, "j_m46_ab": 1.5}
    df = labels_frame([
        ch4,                                                                # 0 T dwarf-like
        {**ch4, "gaia_sep_arcsec": 0.2, "gaia_g": 15.0, "gaia_bp_rp": 2.5},  # 1 Gaia star: impossible
        {**ch4, "gaia_sep_arcsec": 0.2, "gaia_g": 20.1},                     # 2 very faint in Gaia: allowed
        {**ch4, "ch4_16_sig": 1.0},                                         # 3 only one band, no water
        {**ch4, "ch4_16_sig": 1.0, "h2o_14_sig": 3.5},                      # 4 water supports it
        {**ch4, "class": "galaxy"},                                         # 5 galaxy
        {**ch4, "j_m46_ab": -1.0},                                          # 6 not red
        {**ch4, "ch4_33_depth": 0.1},                                       # 7 too shallow, and 1.6 µm alone ...
    ])
    lab = CL.band_labels(df, np.zeros(len(df), bool))
    assert lab["label_ch4"].tolist() == [True, False, True, False, True, False, False, False]


def test_vapour_co_and_pah_labels():
    df = labels_frame([
        {"h2o_19_depth": 0.08, "h2o_19_sig": 6.0, "class": "star", "gaia_g": 14.0, "gaia_bp_rp": 2.6},
        {"h2o_19_depth": 0.08, "h2o_19_sig": 6.0, "class": "galaxy"},
        {"co_23_depth": 0.1, "co_23_sig": 7.0, "class": "star"},
        {"pah_33_excess": 0.3, "pah_33_sig": 6.0, "class": "star", "gaia_g": 12.0},
        {"pah_33_excess": 0.3, "pah_33_sig": 6.0, "class": "galaxy"},
    ])
    lab = CL.band_labels(df, np.zeros(len(df), bool))
    assert lab["label_h2o_vapour"].tolist() == [True, False, False, False, False]
    assert lab["label_co_23"].tolist() == [False, False, True, False, False]
    assert lab["label_pah_33"].tolist() == [False, False, False, False, True]
    assert not lab["label_h2o_ice"].any() and not lab["label_ch4"].any()


# --- 3. fast movers: vetting ------------------------------------------------------------------
def fit_rows(**over):
    base = dict(source_id=3, ra=STAR[0], dec=STAR[1], epoch_mjd=61091.0, pmra=6000.0, pmdec=0.0, pm=6000.0,
                pm_snr=12.0, pmra_err=500.0, pmdec_err=500.0, n_visits=2, span_yr=0.5, chi2_nu=1.2, n_dots=200)
    return {**base, **over}


def test_fit_candidates_are_vetted_not_deleted():
    src = hp_sources()
    ast = pd.DataFrame([
        fit_rows(source_id=1),                                     # clean, no counterpart, 12σ: vetted new
        fit_rows(source_id=2, n_dots=8),                           # barely detected
        fit_rows(source_id=3, chi2_nu=40.0),                       # bad fit
        fit_rows(source_id=4, pm=11_000.0, pmra=11_000.0),         # faster than any star
        fit_rows(source_id=5),                                     # sits on a slow CatWISE source
        fit_rows(source_id=6, pm=600.0, pmra=600.0, pmra_err=80.0, pmdec_err=80.0, pm_snr=7.5),  # Gaia says 10 mas/yr
        fit_rows(source_id=7, pm=550.0, pmra=0.0, pmdec=-550.0, pmra_err=95.0, pmdec_err=95.0, pm_snr=5.8),  # Gaia agrees
        fit_rows(source_id=8, span_yr=0.05, n_visits=1),           # one visit
        fit_rows(source_id=9, pm_snr=6.0),                         # no counterpart to check a 6σ motion
    ])
    xid = pd.DataFrame({"source_id": [5, 6, 7], "gaia_ref_id": ["", "gaia:6", "gaia:7"], "gaia_id": [-1, 6, 7],
                        "gaia_pmra": [np.nan, 8.0, 10.0], "gaia_pmdec": [np.nan, 5.0, -500.0],
                        "catwise_name": ["J1", "", ""], "catwise_pmra": [20.0, np.nan, np.nan],
                        "catwise_pmdec": [-10.0, np.nan, np.nan]})
    out = HP.compute(src, None, xid, ast)
    out = out[out["method"] == "fit"].set_index("source_id")
    assert len(out) == 9  # nothing deleted
    reason = out["reason"].to_dict()
    assert reason[1] == "" and out.loc[1, "vetted"] and out.loc[1, "status"] == "new"
    assert reason[2] == "few_dots" and reason[3] == "bad_fit" and reason[4] == "over_pm_cap"
    assert reason[5] == "catwise_pm_disagrees" and reason[6] == "gaia_pm_disagrees" and reason[8] == "short_span"
    assert reason[7] == "" and out.loc[7, "status"] == "gaia"
    assert reason[9] == "unchecked"
    # vetted first
    assert out["vetted"].tolist()[:2] == [True, True]


def test_pairs_are_tested_against_the_control():
    src = hp_sources()
    fps = {1: [Footprint.from_s_region(square(*STAR))] * 2, 2: [Footprint.from_s_region(square(*STAR))] * 2}
    real = HP.pair_movers(src, HP.detector_fluxes(np.stack(src["flux_ujy"].to_numpy()).astype(float)), fps)
    assert len(real) == 1 and abs(real["sep_arcsec"].iloc[0] - 30) < 0.1
    # a control as busy as the real sky: the pair is at the control level, not "new"
    busy = HP.pair_vs_control(real["sep_arcsec"].to_numpy(), [np.array([15.0, 25.0])] * 4)
    out = HP.compute(src, fps, None, None, busy)
    assert len(out) == 1 and not out.iloc[0]["vetted"] and out.iloc[0]["reason"] == "at_control_level"
    # many real pairs in a bin where the control has none: they beat it
    many = np.full(6, 35.0)
    quiet = HP.pair_vs_control(many, [np.zeros(0)] * 4)
    b = [x for x in quiet if x["sep_arcsec"] == [30.0, 60.0]][0]
    assert b["real"] == 6 and b["control_mean"] == 0 and b["beats_control"]
    assert HP.compute(src, fps, None, None, quiet).iloc[0]["vetted"]
    # without a control the pair is not judged on it
    assert HP.compute(src, fps, None, None).iloc[0]["reason"] == ""


def test_control_uses_the_same_coverage_cut():
    src = hp_sources()
    fps = {1: [Footprint.from_s_region(square(*STAR))] * 2, 2: [Footprint.from_s_region(square(*STAR))] * 2}
    seps = HP.control_separations(src, fps)
    assert len(seps) == len(C.HIGHPM_CONTROL_OFFSETS) and all(len(s) == 0 for s in seps)
    # a planted chance partner 15′ east of a visit-1 source, as the east control would see it
    far = offset_radec(*STAR, -900.0 + 20.0, 0.0)
    extra = pd.DataFrame({"source_id": [50], "ra": [far[0]], "dec": [far[1]], "visit": [2], "mjd": [61182.0],
                          "pos_err_arcsec": np.float32(0.1), "flux_ujy": [src.loc[0, "flux_ujy"]]})
    s2 = pd.concat([src, extra], ignore_index=True)
    assert HP.control_pairs(s2, None) > 0
