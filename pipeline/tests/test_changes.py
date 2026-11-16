"""Watch prototype (changes.py): per-visit statistics, brightness / colour / position alerts, appeared / vanished."""

import numpy as np
import pandas as pd
import pytest

from skyshift_pipeline import changes as CH

RNG = np.random.default_rng(16)


def _points(n_stars=400, changes=None, sigma=0.02, sessions=4, per_session=3, visits=(1, 2), dets=(1, 2, 3, 4, 5, 6)):
    """Constant stars (rel_flux ≈ 1 ± sigma) in every detector and visit; changes: {star: {det: factor in the last
    visit}}. Star i is source i, Gaia-matched when i is even."""
    changes = changes or {}
    rows = []
    for s in range(n_stars):
        for d in dets:
            for v in visits:
                t0 = 61000.0 + 180.0 * (v - 1) + s * 1e-4
                for k in range(sessions):
                    for j in range(per_session):
                        mjd = t0 + 1.0 * k + j * 2.1 / 1440.0
                        f = changes.get(s, {}).get(d, 1.0) if v == visits[-1] else 1.0
                        rows.append((s, "", "dot", mjd, v, d, f + RNG.normal(0, sigma), sigma * 100.0, 100.0, True))
    p = pd.DataFrame(rows, columns=["source_id", "ref_id", "origin", "mjd", "visit", "detector", "rel_flux",
                                    "flux_err_cal_ujy", "model_ujy", "photo_ok"])
    return p


def _run(p):
    vt = CH.visit_table(p)
    refs = np.unique(vt["star"].to_numpy()[vt["source_id"].to_numpy() % 2 == 0])
    vt, _zps = CH.zero_points(vt, refs)
    pr = CH.pairs(vt, {"visit": 1}, {"visit": 2})
    floors = CH.fit_floor(pr, refs)
    pr = CH.apply_floor(pr, floors)
    best = CH.best_per_detector(pr)
    return vt, best, CH.star_brightness(best), CH.star_colour(best)


def test_visit_table_mean_error_sessions_and_clip():
    p = _points(n_stars=1, dets=(2,))
    # one cosmic-ray point far off
    p.loc[5, "rel_flux"] = 3.0
    vt = CH.visit_table(p)
    assert len(vt) == 2
    r = vt[vt["visit"] == 1].iloc[0]
    assert r["n"] == 11 and r["n_sessions"] == 4          # the hit is clipped, 4 sessions of 3
    assert abs(r["mean"] - 1.0) < 0.03
    assert 0.004 < r["err"] < 0.02                         # ≈ 0.02 / √11, × √χ²_ν at most


def test_halves_split_sessions():
    p = _points(n_stars=1, dets=(1,))
    vh = CH.visit_table(p, halves=True)
    assert sorted(vh["half"].unique().tolist()) == [0, 1]
    assert (vh["n_sessions"] == 2).all() and (vh["n"] == 6).all()


def test_brightness_alert_needs_a_second_detector():
    p = _points(changes={7: {1: 0.6, 2: 0.65, 3: 0.7}, 9: {4: 0.6}})
    _, _best, sb, _sc = _run(p)
    a = sb.set_index("star")
    assert a.loc[7, "alert"] and a.loc[7, "agree"] >= 2
    assert a.loc[9, "alert_one_det"] and not a.loc[9, "alert"]     # one detector alone: not an alert
    assert sb["alert"].sum() == 1                                  # no constant star alerts
    assert a.loc[7, "frac"] < -0.25


def test_zero_point_removes_a_visit_wide_offset():
    p = _points(n_stars=300, dets=(2, 3))
    p.loc[(p["visit"] == 2) & (p["detector"] == 2), "rel_flux"] *= 1.08    # whole visit 8% bright in D2
    vt = CH.visit_table(p)
    refs = np.unique(vt["star"].to_numpy())
    vt2, zps = CH.zero_points(vt, refs)
    z = {(r["detector"], r["visit"]): r["zero_point"] for r in zps}
    assert z[(2, 2)] / z[(2, 1)] == pytest.approx(1.08, abs=0.01)
    pr = CH.apply_floor(CH.pairs(vt2, {"visit": 1}, {"visit": 2}), CH.fit_floor(CH.pairs(vt2, {"visit": 1}, {"visit": 2}), refs))
    assert np.abs(pr["frac"]).max() < 0.05


def test_floor_is_fitted_on_bright_reference_light_curves():
    p = _points(n_stars=600, dets=(2,), sigma=0.004)
    # visit-to-visit jitter of 2% per star that the per-point errors do not know about
    jit = RNG.normal(0, 0.02, 600)
    m = p["visit"] == 2
    p.loc[m, "rel_flux"] += jit[p.loc[m, "source_id"].to_numpy()]
    vt = CH.visit_table(p)
    refs = np.unique(vt["star"].to_numpy())
    pr = CH.pairs(vt, {"visit": 1}, {"visit": 2})
    fl = CH.fit_floor(pr, refs)["dot_D2"]
    assert fl["floor"] == pytest.approx(0.02, abs=0.006)
    assert fl["robust_sd_after"] == pytest.approx(1.0, abs=0.15)


def test_colour_alert_on_a_wavelength_dependent_change():
    # a Mira-like change: −50% at D1 falling to −5% at D6
    ch = {11: {1: 0.5, 2: 0.6, 3: 0.72, 4: 0.85, 5: 0.92, 6: 0.95}, 12: {d: 0.7 for d in range(1, 7)}}
    _, _best, sb, sc = _run(_points(changes=ch, sigma=0.01))
    c = sc.set_index("star")
    assert c.loc[11, "colour_alert"] and c.loc[11, "slope"] > 0          # redder: the blue end dropped more
    assert not c.loc[12, "colour_alert"]                                 # grey change: brightness only
    assert sb.set_index("star").loc[12, "alert"]
    assert sc["colour_alert"].sum() == 1


def test_one_session_change_does_not_survive():
    p = _points(n_stars=1, dets=(2, 3), sessions=5)
    last = (p["visit"] == 2) & (p["mjd"] < p.loc[p["visit"] == 2, "mjd"].min() + 0.5)
    p.loc[last, "rel_flux"] = 0.5                                        # one session of visit 2 only
    rows = pd.DataFrame([{"star": 0, "source_id": 0, "ref_id": "", "detector": 2, "origin": "dot", "visit_a": 1, "visit_b": 2, "sigma": 0.01}])
    zps = [{"detector": 2, "origin": "dot", "visit": v, "half": 0, "zero_point": 1.0} for v in (1, 2)]
    assert not CH.survives_session_drop(p, rows, {}, zps)[0]
    p2 = _points(n_stars=1, dets=(2,), changes={0: {2: 0.5}})
    assert CH.survives_session_drop(p2, rows, {}, zps)[0]


def test_summary_fallback_gives_the_same_alert():
    p = _points(n_stars=300, changes={7: {1: 0.6, 2: 0.65, 3: 0.7}}, dets=(1, 2, 3))
    from skyshift_pipeline.lightcurves import summarise
    p = p.assign(detected=True, wavelength_um=1.0, flux_ujy=p["rel_flux"] * 100.0, flux_err_ujy=2.0,
                 model_ujy=100.0, flux_err_cal_ujy=2.0)
    s = summarise(p)
    vt = CH.visit_table_from_summary(s)
    refs = np.unique(vt["star"].to_numpy())
    vt, _ = CH.zero_points(vt, refs)
    pr = CH.pairs(vt, {"visit": 1}, {"visit": 2})
    pr = CH.apply_floor(pr, CH.fit_floor(pr, refs))
    sb = CH.star_brightness(CH.best_per_detector(pr))
    assert sb.loc[sb["alert"], "star"].tolist() == [7]


def _astrometry(n=400):
    ra0, de0 = 121.0 + RNG.uniform(0, 1, n), 15.0 + RNG.uniform(0, 1, n)
    xid = pd.DataFrame({"source_id": np.arange(n), "gaia_ref_id": [f"gaia:{i}" for i in range(n)],
                        "gaia_id": np.arange(n) + 10**15, "gaia_ra": ra0, "gaia_dec": de0, "gaia_epoch_mjd": 57388.5,
                        "gaia_g": 15.0, "gaia_pmra": 0.0, "gaia_pmdec": 0.0, "gaia_parallax": 0.5, "gaia_n": 1})
    e = 0.05
    ast = pd.DataFrame({"source_id": np.arange(n),
                        "ra": ra0 + RNG.normal(0, e, n) / 3600 / np.cos(np.radians(de0)),
                        "dec": de0 + RNG.normal(0, e, n) / 3600, "epoch_mjd": 61000.0,
                        "ra_err_arcsec": e, "dec_err_arcsec": e})
    return ast, xid


def test_position_offset_alert_and_neighbour_pull():
    ast, xid = _astrometry()
    ast.loc[3, "dec"] += 1.0 / 3600            # 1″ north of Gaia's prediction, nothing nearby
    ast.loc[5, "dec"] += 1.0 / 3600            # 1″ north, with an equally bright Gaia neighbour 2″ north
    ref = pd.DataFrame({"cat": "gaia", "ra": [xid.loc[5, "gaia_ra"]], "dec": [xid.loc[5, "gaia_dec"] + 2.0 / 3600],
                        "g_mag": [15.0]})
    m, rep = CH.position_offsets(ast, xid, ref)
    m = m.set_index("source_id")
    assert m.loc[3, "pos_off"] and not m.loc[3, "explained"]
    assert m.loc[3, "pos_sep"] == pytest.approx(1.0, abs=0.2)
    assert m.loc[5, "explained"]                # the neighbour predicts the 1″ pull
    assert m["pos_off"].sum() == 2
    assert rep["k"] == pytest.approx(1.0, abs=0.2)


def test_position_moved_gaia_star_and_parallax_cut():
    ast, xid = _astrometry()
    xid.loc[7, "gaia_pmdec"] = 100.0            # 100 mas/yr over 9.5 yr: Gaia's prediction moves 0.95″ ...
    ast.loc[7, "dec"] += 0.95 / 3600            # ... and SPHEREx sees it there: not off
    ast.loc[8, "dec"] += 2.0 / 3600
    xid.loc[8, "gaia_parallax"] = 50.0          # a nearby star: parallax not modelled here, so not tested
    m, _ = CH.position_offsets(ast, xid)
    m = m.set_index("source_id")
    assert not m.loc[7, "pos_off"] and not m.loc[8, "pos_off"]


def test_motion_against_gaia():
    ast, xid = _astrometry()
    e = 50.0                                    # mas/yr: two visits half a year apart
    ast["pmra"], ast["pmdec"] = RNG.normal(0, e, len(ast)), RNG.normal(0, e, len(ast))
    ast["pmra_err"], ast["pmdec_err"] = e, e
    ast.loc[4, "pmdec"] = 600.0                 # 12σ off Gaia's 0
    m, rep = CH.position_offsets(ast, xid)
    m = m.set_index("source_id")
    assert m.loc[4, "motion_off"] and m.loc[4, "dpm"] == pytest.approx(600.0, abs=150.0)
    assert m["motion_off"].sum() <= 2 and rep["motion_k"] == pytest.approx(1.0, abs=0.2)


def test_population_inside_outside_the_zone():
    pop = CH.population(np.array([5, -1, -1]), np.array(["", "gaia:1", "gaia:2"]), np.array([121.0, 121.0, 119.0]),
                        np.array([15.0, 15.0, 15.0]), (120.5, 124.1, 14.4, 17.9))
    assert pop.tolist() == ["source", "forced_inside", "forced_outside"]


def test_history_text_and_checks():
    r = pd.Series({"name": "Gaia DR3 7", "detector": 3, "visit_a": [1, 2, 3], "visit_b": 4, "mjd_mid_a": np.nan,
                   "mjd_mid_b": 61500.0, "mean_a": 1.0, "mean_b": 1.3, "frac": 0.3, "z": 8.0, "agree": 2,
                   "disagree": 1, "other_z": -3.0, "origin": "dot", "survives_session_drop": 0.0,
                   "gaia_variable": True, "gaia_class": "LPV", "nb_sep": 14.0, "nb_dmag": -1.2})
    text, _look, values = CH.brightness_text(r, [(2, 0.2, 4.0)])
    assert "earlier visits 1–3 together" in text and values["visit_a"] == [1, 2, 3]
    checks = CH.brightness_checks(r)
    codes = [c["code"] for c in checks]
    assert codes[0] == "one_session" and checks[0]["level"] == "strong"
    assert {"detectors_disagree", "origin_disagrees", "bright_neighbour", "other_detectors_agree", "known_variable"} <= set(codes)
    assert [c["level"] for c in checks] == sorted([c["level"] for c in checks], key=["strong", "warn", "ok"].index)


def test_new_sources_appeared_vanished():
    orph = pd.DataFrame({"source_id": [1, 2, 3, 4, 5, 6, 7], "visit": [2, 1, 2, 2, 2, 2, 2],
                         "reason": ["", "", "bright_fragment", "", "", "", ""],
                         "known_name": ["", "", "", "(1) Ceres", "", "", ""],
                         "n_dots": [20, 12, 20, 20, 20, 3, 20],
                         "rate": [0.001, np.nan, 0.0, 0.0, 0.5, 0.0, 0.0], "rate_err": [0.01, np.nan, 0.01, 0.01, 0.01, 0.01, 0.01]})
    snr = pd.Series([30.0, 25.0, 40.0, 50.0, 60.0, 80.0, 50.0], index=[1, 2, 3, 4, 5, 6, 7])
    dets = pd.Series([0b11, 0b111, 0b11, 0b11, 0b11, 0b101000, 0b1000], index=[1, 2, 3, 4, 5, 6, 7])
    n = CH.new_sources(orph, last_visit=2, snr=snr, detectors=dets).set_index("source_id")
    # fragment, known asteroid, drifting, three dots (MWIR glitches) and one detector alone are out
    assert n.index.tolist() == [1, 2]
    assert n.loc[1, "kind"] == "appeared" and n.loc[2, "kind"] == "vanished"
    # the direction comes from the two visits compared, not from "is it the last visit": a source seen only in the
    # middle visit 2 of 3 whose other (covering) visit is 1 appeared; one whose other visit is 3 vanished
    mid = pd.DataFrame({"source_id": [8, 9], "visit": [2, 2], "other_visit": [1, 3], "reason": ["", ""],
                        "n_dots": [20, 20], "rate": [0.0, 0.0], "rate_err": [0.01, 0.01]})
    m = CH.new_sources(mid, last_visit=3).set_index("source_id")
    assert m.loc[8, "kind"] == "appeared" and m.loc[9, "kind"] == "vanished"


def test_texts_carry_the_numbers():
    r = pd.Series({"name": "Gaia DR3 42", "detector": 2, "visit_a": 1, "visit_b": 2, "mjd_mid_a": 61000.0,
                   "mjd_mid_b": 61180.0, "mean_a": 1.01, "mean_b": 0.62, "frac": -0.386, "z": -19.2, "agree": 2})
    text, look, values = CH.brightness_text(r, [(1, -0.35, -12.0), (3, -0.30, -9.0)])
    assert text.startswith("Gaia DR3 42 went from 1.01 to 0.62")
    assert "39% fainter" in text and "19.2σ" in text and "D1 -35%" in text
    assert "blink visit 1 against visit 2" in look
    assert values["sigma"] == -19.2


def test_bright_neighbour_found_within_20_arcsec():
    ref = pd.DataFrame({"cat": ["gaia", "gaia", "gaia", "catwise"], "ra": [10.0, 10.0, 10.1, 10.0],
                        "dec": [20.0, 20.0 + 12 / 3600, 20.0, 20.0 + 5 / 3600], "g_mag": [16.0, 10.0, 9.0, np.nan]})
    sep, dm = CH.bright_neighbours(np.array([10.0, 10.1 + 30 / 3600]), np.array([20.0, 20.0]), np.array([16.0, 16.0]), ref)
    assert sep[0] == pytest.approx(12.0, abs=0.1) and dm[0] == pytest.approx(-6.0)   # its own row (0″) is skipped
    assert np.isnan(sep[1])                                                         # the G 9 star is 28″ away
