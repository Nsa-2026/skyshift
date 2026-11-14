"""highpm selection (14 Nov, hunt diagnosis): parallax-aware Gaia / CatWISE checks, sessions per visit, Gaia-confirmed
motions, the false-positive control, and the export of vetted rows only. Synthetic data only."""

import numpy as np
import pandas as pd
import pytest

from skyshift_pipeline import candidates as K
from skyshift_pipeline import config as C
from skyshift_pipeline import highpm as HP
from skyshift_pipeline.astrometry import observer_xyz_au, parallax_factors

from test_motion import hp_sources
from test_pilot_fixes import fit_rows

KE, KN = -4.1, 1.0  # the hunt's parallax leak (mas/yr per mas of parallax)


def vstats(ids, sessions=20, ke=KE, kn=KN):
    return pd.DataFrame({"source_id": ids, "k_e": ke, "k_n": kn, "sessions_min": sessions})


def one_fit(**over):
    return pd.DataFrame([fit_rows(source_id=1, **over)])


def test_visit_stats_parallax_leak_and_sessions():
    """k = cov(t, parallax factor) / var(t); for two equal visits it is the parallax-factor change over the time
    between them. sessions_min counts the sessions of the sparser visit."""
    ra, dec = 125.0, 19.0
    t1, t2 = 60975.0, 61145.0
    mjd = np.r_[t1 + np.arange(4) * 0.5, t2 + np.arange(2) * 0.5]
    p = pd.DataFrame({"obs_id": [f"o{i}" for i in range(6)], "detector": 1, "mjd": mjd, "visit": [1, 1, 1, 1, 2, 2],
                      "session": [1, 2, 3, 4, 5, 6]})
    m = pd.DataFrame({"source_id": 7, "obs_id": p["obs_id"], "detector": 1})
    pos = pd.DataFrame({"source_id": [7], "ra": [ra], "dec": [dec]})
    v = HP.visit_stats(m, p, pos, [7, 8]).set_index("source_id")
    pe, pn = parallax_factors(np.full(6, ra), np.full(6, dec), observer_xyz_au(mjd))
    t = mjd / 365.25
    assert v.loc[7, "k_e"] == pytest.approx(np.polyfit(t, pe, 1)[0], rel=1e-6)
    assert v.loc[7, "k_n"] == pytest.approx(np.polyfit(t, pn, 1)[0], rel=1e-6)
    assert -5 < v.loc[7, "k_e"] < -3          # near the ecliptic at quadrature: ~ −4 mas/yr per mas
    assert v.loc[7, "sessions_min"] == 2
    assert v.loc[8, "sessions_min"] == 0 and v.loc[8, "k_e"] == 0.0   # no dots


def test_gaia_comparison_counts_the_parallax_leak():
    """A nearby Gaia star (541 mas/yr, ϖ 54.7 mas) measured with its parallax leak is confirmed, not 'disagrees'."""
    src = hp_sources()
    gra, gde, plx = -520.9, 146.7, 54.7
    ast = one_fit(pmra=gra + plx * KE, pmdec=gde + plx * KN, pm=float(np.hypot(gra + plx * KE, gde + plx * KN)),
                  pmra_err=40.0, pmdec_err=40.0, pm_snr=19.0)
    xid = pd.DataFrame({"source_id": [1], "gaia_ref_id": ["gaia:1"], "gaia_id": [1], "gaia_pmra": [gra], "gaia_pmdec": [gde],
                        "gaia_parallax": [plx], "catwise_name": [""], "catwise_pmra": [np.nan], "catwise_pmdec": [np.nan]})
    ok = HP.compute(src, None, xid, ast, vstats=vstats([1])).query("method == 'fit'").iloc[0]
    assert ok["vetted"] and ok["status"] == "gaia" and ok["reason"] == ""
    # without the leak (k = 0) the same star looked like a mismatch
    assert HP.compute(src, None, xid, ast).query("method == 'fit'").iloc[0]["reason"] == "gaia_pm_disagrees"


def test_gaia_confirmed_motion_ignores_quality_reasons_but_not_a_mismatch():
    src = hp_sources()
    ast = one_fit(pmra=0.0, pmdec=-900.0, pm=900.0, pmra_err=150.0, pmdec_err=150.0, pm_snr=6.0, chi2_nu=21.0)
    xid = pd.DataFrame({"source_id": [1], "gaia_ref_id": ["gaia:1"], "gaia_id": [1], "gaia_pmra": [-10.0],
                        "gaia_pmdec": [-890.0], "gaia_parallax": [0.0], "catwise_name": [""], "catwise_pmra": [np.nan],
                        "catwise_pmdec": [np.nan]})
    assert HP.compute(src, None, xid, ast, vstats=vstats([1], sessions=1)).query("method == 'fit'").iloc[0]["vetted"]
    xid["gaia_pmdec"] = -20.0  # Gaia: a slow star → the quality reasons apply again, and the mismatch
    r = HP.compute(src, None, xid, ast, vstats=vstats([1], sessions=1)).query("method == 'fit'").iloc[0]["reason"]
    assert r == "few_sessions,bad_fit,gaia_pm_disagrees"


def test_catwise_check_allows_a_physical_parallax_only():
    """No Gaia: ours may exceed CatWISE's pm by ϖ·k with ϖ <= 0.25 |pm_cw| (0.1 for saturated sources)."""
    src = hp_sources()
    cra, cde = -300.0, 0.0

    def run(plx, saturated=False):
        s = src.copy()
        s["saturated"] = saturated
        pra, pde = cra + plx * KE, cde + plx * KN
        ast = one_fit(pmra=pra, pmdec=pde, pm=float(np.hypot(pra, pde)), pmra_err=20.0, pmdec_err=20.0, pm_snr=20.0)
        xid = pd.DataFrame({"source_id": [1], "gaia_ref_id": [""], "gaia_id": [-1], "catwise_name": ["J1"],
                            "catwise_pmra": [cra], "catwise_pmdec": [cde]})
        return HP.compute(s, None, xid, ast, vstats=vstats([1])).query("method == 'fit'").iloc[0]

    near = run(60.0)                      # ϖ 60 <= 75 mas: a nearby star, both motions agree
    assert near["vetted"] and near["catwise_pm_agree"] and near["status"] == "new"
    far = run(150.0)                      # would need ϖ 150 mas with 300 mas/yr: 9 km/s, not allowed
    assert far["reason"] == "catwise_pm_disagrees" and not far["catwise_pm_agree"]
    assert run(60.0, saturated=True)["reason"] == "catwise_pm_disagrees"   # saturated: ϖ <= 30 mas
    assert run(20.0, saturated=True)["vetted"]


def test_catwise_errors_widen_the_tolerance():
    src = hp_sources()
    ast = one_fit(pmra=600.0, pmdec=0.0, pm=600.0, pmra_err=20.0, pmdec_err=20.0, pm_snr=30.0)
    xid = pd.DataFrame({"source_id": [1], "gaia_ref_id": [""], "gaia_id": [-1], "catwise_name": ["J1"],
                        "catwise_ref_id": ["cw1"], "catwise_pmra": [250.0], "catwise_pmdec": [0.0]})
    assert HP.compute(src, None, xid, ast, vstats=vstats([1], ke=0.0, kn=0.0)).query("method == 'fit'").iloc[0]["reason"] == "catwise_pm_disagrees"
    err = pd.Series([150.0], index=["cw1"])  # a faint CatWISE source: 3 × 150 ⊕ 20 > 350
    assert HP.compute(src, None, xid, ast, vstats=vstats([1], ke=0.0, kn=0.0), cw_err=err).query("method == 'fit'").iloc[0]["vetted"]


def test_fp_control_hides_gaia_and_counts_what_survives():
    src = hp_sources()
    src["saturated"] = False
    ids = [1, 2, 3, 4]
    ast = pd.DataFrame([fit_rows(source_id=1, pm=500.0, pmra=500.0, pmra_err=40.0, pmdec_err=40.0, pm_snr=12.5),   # CatWISE still: caught
                        fit_rows(source_id=2, pm=500.0, pmra=500.0, pmra_err=40.0, pmdec_err=40.0, pm_snr=12.5),   # nothing to check, 12.5σ: FP
                        fit_rows(source_id=3, pm=50.0, pmra=50.0, pmra_err=40.0, pmdec_err=40.0, pm_snr=1.2),      # not selected
                        fit_rows(source_id=4, pm=800.0, pmra=800.0, pmra_err=40.0, pmdec_err=40.0, pm_snr=20.0)])  # no Gaia at all
    xid = pd.DataFrame({"source_id": ids, "gaia_ref_id": ["g1", "g2", "g3", ""], "gaia_id": [1, 2, 3, -1],
                        "gaia_pmra": [5.0, 3.0, 1.0, np.nan], "gaia_pmdec": [0.0, 0.0, 0.0, np.nan],
                        "gaia_parallax": [1.0, 1.0, 1.0, np.nan], "catwise_name": ["J1", "", "", ""],
                        "catwise_pmra": [5.0, np.nan, np.nan, np.nan], "catwise_pmdec": [0.0, np.nan, np.nan, np.nan]})
    fp = HP.fp_control(src, xid, ast, vstats(ids), None)
    assert fp["still_stars"] == 3 and fp["selected"] == 2 and fp["false_positives"] == 1
    assert fp["per_100k"] == pytest.approx(1e5 / 3, rel=1e-3)
    assert fp["sources_without_gaia_pm"] == 1 and fp["expected_new_fakes"] == pytest.approx(1 / 3, abs=0.01)


def test_export_lists_vetted_highpm_rows_only():
    h = pd.DataFrame({"source_id": [1, 2, 3], "vetted": [True, False, True], "reason": ["", "gaia_pm_disagrees", ""]})
    assert K.highpm_leads(h)["source_id"].tolist() == [1, 3]
    assert K.highpm_leads(pd.DataFrame()).empty


def test_selection_threshold_is_the_calibrated_one():
    # the hunt calibration (config comments): 4.5σ with the catalogue checks, 10σ with nothing to check against
    assert C.HIGHPM_FIT_MIN_SNR == 4.5 and C.HIGHPM_NOCAT_MIN_SNR == 10.0 and C.HIGHPM_FIT_MIN_SESSIONS == 3
    ast = pd.DataFrame([fit_rows(source_id=1, pm=400.0, pmra=400.0, pm_snr=4.6), fit_rows(source_id=2, pm=400.0, pmra=400.0, pm_snr=4.4),
                        fit_rows(source_id=3, pm=140.0, pmra=140.0, pm_snr=9.0)])
    assert HP.fit_movers(ast)["source_id"].tolist() == [1]


def test_uncatalogued_saturated_motion_is_unchecked():
    """Nothing to check against: 10σ is enough for a faint source, never for a saturated one (an uncatalogued star
    never saturates; hunt: the last 3 still-star false positives were G 7–8 stars that CatWISE does not list)."""
    src = hp_sources()
    ast = one_fit(pm=1300.0, pmra=-1300.0, pmra_err=110.0, pmdec_err=110.0, pm_snr=11.8)
    xid = pd.DataFrame({"source_id": [1], "gaia_ref_id": [""], "gaia_id": [-1], "catwise_name": [""],
                        "catwise_pmra": [np.nan], "catwise_pmdec": [np.nan]})

    def fit(s):
        return HP.compute(s, None, xid, ast, vstats=vstats([1])).query("method == 'fit'").iloc[0]

    assert fit(src.assign(saturated=False))["vetted"]
    assert fit(src.assign(saturated=True))["reason"] == "unchecked"


def test_gaia_slow_star_within_errors_is_not_listed():
    """A Gaia star our fit measures at 180 mas/yr, consistent within errors with Gaia's 80 mas/yr (ϖ 3 mas): Gaia
    agrees, but it predicts less than HIGHPM_FIT_MIN_MAS_YR, so it is a known slow star (gaia_slow), not a mover.
    A nearby star whose parallax leak lifts Gaia's prediction above the threshold stays listed."""
    src = hp_sources()
    ast = one_fit(pmra=-180.0, pmdec=0.0, pm=180.0, pmra_err=40.0, pmdec_err=40.0, pm_snr=4.6)
    xid = pd.DataFrame({"source_id": [1], "gaia_ref_id": ["gaia:1"], "gaia_id": [1], "gaia_pmra": [-80.0],
                        "gaia_pmdec": [0.0], "gaia_parallax": [3.0], "catwise_name": [""], "catwise_pmra": [np.nan],
                        "catwise_pmdec": [np.nan]})
    r = HP.compute(src, None, xid, ast, vstats=vstats([1])).query("method == 'fit'").iloc[0]
    assert not r["vetted"] and r["reason"] == "gaia_slow"
    xid["gaia_parallax"] = 25.0  # −80 + 25 × −4.1 = −182.5 mas/yr: Gaia itself predicts what we measured
    r = HP.compute(src, None, xid, ast, vstats=vstats([1])).query("method == 'fit'").iloc[0]
    assert r["vetted"] and r["reason"] == ""
