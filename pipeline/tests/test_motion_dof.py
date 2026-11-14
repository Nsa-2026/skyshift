"""Fits with (almost) no freedom after the detector zero points, and the fit cut of highpm: a fast motion Gaia or
CatWISE confirms counts from a lower significance than an unconfirmed one (hunt, 14 Nov night). Synthetic data only."""

import numpy as np
import pandas as pd
import pytest
from skyshift_core import tangent_deproject

from skyshift_pipeline import astrometry as AS
from skyshift_pipeline import config as C
from skyshift_pipeline import highpm as HP

from test_highpm_selection import vstats
from test_motion import STAR, hp_sources, session_dots
from test_pilot_fixes import fit_rows


def test_motion_dof_counts_detectors_seen_in_two_visits():
    """2 × (used dots − zero points) − 2 over the detectors seen in both visits; in a source seen in two visits a
    detector seen in one only gives nothing; a single-visit source counts all of them; with parallax one less; without
    zero points the source is one zero point."""
    # source 0: D1 2+1 dots across the visits, D2–D4 one dot each (the hunt's 6-dot fakes): 2 × (3 − 1) − 2 = 2
    # source 1: every detector in one visit only: nothing between the visits
    # source 2: D1 and D2 with 5 + 5 dots each: 2 × (20 − 2) − 2 = 34
    # source 3: one visit, D1 4 dots and D2 3: its motion is within the visit, 2 × (7 − 2) − 2 = 8
    rows = [(0, 1, 1), (0, 1, 1), (0, 1, 2), (0, 2, 1), (0, 3, 2), (0, 4, 1),
            (1, 1, 1), (1, 1, 1), (1, 2, 2), (1, 2, 2), (1, 3, 1), (1, 4, 2),
            *[(2, det, v) for det in (1, 2) for v in (1, 2) for _ in range(5)],
            *[(3, 1, 2)] * 4, *[(3, 2, 2)] * 3]
    s = np.array([r[0] for r in rows])
    det = np.array([r[1] for r in rows])
    vis = np.array([r[2] for r in rows])
    z = np.unique(s * 8 + det, return_inverse=True)[1].ravel()
    keep = np.ones(len(rows), bool)
    no_plx = np.zeros(4, bool)
    assert AS.motion_dof(s, 4, keep, vis, no_plx, z).tolist() == [2, -2, 34, 8]
    assert AS.motion_dof(s, 4, keep, vis, np.ones(4, bool), z).tolist() == [1, -3, 33, 7]
    assert AS.motion_dof(s, 4, keep, vis, no_plx).tolist() == [8, 8, 36, 10]   # no zero points: 2 × (n − 1) − 2
    # dots left out of the fit don't count: source 0's one D1 dot in visit 2 out → no detector spans the visits
    keep[2] = False
    assert AS.motion_dof(s, 4, keep, vis, no_plx, z).tolist()[0] == -2
    # where the zero-point fit failed the source keeps the plain fit's count
    okz = np.array([False, True, True, True])
    assert AS.motion_dof(s, 4, np.ones(len(rows), bool), vis, no_plx, z, okz).tolist() == [8, -2, 34, 8]


def few_dot_fake(sid, rng):
    """A still star seen in 6 dots: D1 twice in visit 1 and once in visit 2, D2–D4 once each, the visit-2 D1 dot 1.5″ off."""
    plan = [(1, 1, 61000.0), (1, 1, 61000.3), (1, 2, 61182.0), (2, 1, 61000.1), (3, 2, 61182.1), (4, 1, 61000.2)]
    out = []
    for i, (det, v, mjd) in enumerate(plan):
        e, n = rng.normal(0, 0.05, 2) + ((1.5, 0.0) if (det, v) == (1, 2) else (0.0, 0.0))
        r, d = tangent_deproject(STAR[0], STAR[1], e, n)
        out.append(dict(source_id=sid, ra=float(r), dec=float(d), mjd=mjd, pos_err_arcsec=0.05, visit=v, session=100 + i,
                        photo=1000 + sid * 10 + i, obs_id=f"f{sid}_{i}", detector=det, snr=50.0, flags_peak=0))
    return pd.DataFrame(out)


def test_astrometry_drops_fits_without_motion_freedom(rng):
    """The hunt's 6-dot fakes: one detector seen in both visits, the others' zero points take all their dots. The fit
    is a formal 3″/yr at > 4.5σ from one dot; with too few degrees of freedom it is not written. A well-sampled star is."""
    d = pd.concat([few_dot_fake(7, rng), session_dots(rng, sid=1)], ignore_index=True)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(C, "ASTRO_MIN_MOTION_DOF", -10**6)
        loose, st0 = AS.fit(d, None, frame=False)
    fake = loose.set_index("source_id").loc[7]
    assert fake["pm"] > 1500 and fake["pm_snr"] > 4.5 and st0["dropped_motion_dof"] == 0
    out, st = AS.fit(d, None, frame=False)
    assert out["source_id"].tolist() == [1] and st["dropped_motion_dof"] == 1 and st["fitted"] == 1
    assert C.ASTRO_MIN_MOTION_DOF > 2


# --- highpm: the fit cut -------------------------------------------------------------------------------------------------
def gaia_xid(gpmra, gpmdec, plx=0.0, cw=None):
    cra, cde = (np.nan, np.nan) if cw is None else cw
    return pd.DataFrame({"source_id": [1], "gaia_ref_id": ["gaia:1"], "gaia_id": [1], "gaia_pmra": [gpmra],
                         "gaia_pmdec": [gpmdec], "gaia_parallax": [plx], "catwise_name": ["J1" if cw else ""],
                         "catwise_pmra": [cra], "catwise_pmdec": [cde]})


def fits_of(ast, xid, cw_err=None):
    out = HP.compute(hp_sources(), None, xid, ast, vstats=vstats([1]), cw_err=cw_err)
    return out[out["method"] == "fit"]


def test_fit_cut_is_lower_for_a_motion_gaia_confirms():
    """A fast Gaia star measured at 3.4σ is counted (status gaia, vetted); the same fit without a catalogue motion, or
    against a slow Gaia star, is no candidate at all (the raw table keeps only >= HIGHPM_FIT_MIN_SNR there)."""
    assert C.HIGHPM_FIT_MIN_SNR_GAIA < 3.4 < C.HIGHPM_FIT_MIN_SNR
    ast = pd.DataFrame([fit_rows(source_id=1, pmra=-520.0, pmdec=0.0, pm=520.0, pmra_err=150.0, pmdec_err=150.0, pm_snr=3.4)])
    ok = fits_of(ast, gaia_xid(-480.0, 30.0))
    assert len(ok) == 1 and ok.iloc[0]["vetted"] and ok.iloc[0]["status"] == "gaia"
    assert ast["source_id"].isin(HP.fit_movers(ast)["source_id"]).all()
    none = pd.DataFrame({"source_id": [1], "gaia_ref_id": [""], "gaia_id": [-1], "catwise_name": [""],
                         "catwise_pmra": [np.nan], "catwise_pmdec": [np.nan]})
    assert fits_of(ast, none).empty
    assert fits_of(ast, gaia_xid(5.0, 0.0)).empty                     # Gaia: a still star (our 3.4σ is noise)
    assert fits_of(ast, gaia_xid(-100.0, 0.0)).empty                  # Gaia agrees but predicts < 150 mas/yr
    # at >= HIGHPM_FIT_MIN_SNR nothing changes: the row stays, with its reason
    strong = ast.assign(pm_snr=5.0, pmra_err=104.0, pmdec_err=104.0)
    assert fits_of(strong, none).iloc[0]["reason"] == "unchecked"


def test_catwise_confirms_only_its_own_fast_significant_motion():
    """No Gaia pm: a CatWISE motion confirms ours below HIGHPM_FIT_MIN_SNR only when it is itself fast and significant;
    agreeing within 3σ with a still CatWISE source at low significance confirms nothing."""
    cut = C.HIGHPM_FIT_MIN_SNR_CATWISE
    snr = cut + 0.5 * (C.HIGHPM_FIT_MIN_SNR - cut) if cut < C.HIGHPM_FIT_MIN_SNR else C.HIGHPM_FIT_MIN_SNR - 0.1
    err = 400.0 / snr
    ast = pd.DataFrame([fit_rows(source_id=1, pmra=-400.0, pmdec=0.0, pm=400.0, pmra_err=err, pmdec_err=err, pm_snr=snr)])
    xid = pd.DataFrame({"source_id": [1], "gaia_ref_id": [""], "gaia_id": [-1], "catwise_name": ["J1"],
                        "catwise_ref_id": ["cw1"], "catwise_pmra": [-380.0], "catwise_pmdec": [10.0]})
    fast = fits_of(ast, xid, cw_err=pd.Series([40.0], index=["cw1"]))
    assert len(fast) == (1 if cut < C.HIGHPM_FIT_MIN_SNR else 0)
    slow = xid.assign(catwise_pmra=-120.0, catwise_pmdec=0.0)       # agrees within 3σ, but CatWISE's own pm < 150
    assert fits_of(ast, slow, cw_err=pd.Series([40.0], index=["cw1"])).empty
    noisy = pd.Series([200.0], index=["cw1"])                         # CatWISE's own 380 mas/yr at < 3σ
    assert fits_of(ast, xid, cw_err=noisy).empty


def test_fp_control_selection_uses_the_confirmed_cut():
    """Hidden Gaia: a still star whose fit is 3.4σ fast is not selected (no catalogue motion confirms it)."""
    src = hp_sources()
    src["saturated"] = False
    ast = pd.DataFrame([fit_rows(source_id=1, pm=520.0, pmra=520.0, pmra_err=150.0, pmdec_err=150.0, pm_snr=3.4)])
    xid = pd.DataFrame({"source_id": [1], "gaia_ref_id": ["g1"], "gaia_id": [1], "gaia_pmra": [3.0], "gaia_pmdec": [0.0],
                        "gaia_parallax": [1.0], "catwise_name": [""], "catwise_pmra": [np.nan], "catwise_pmdec": [np.nan]})
    fp = HP.fp_control(src, xid, ast, vstats([1]), None)
    assert fp["still_stars"] == 1 and fp["selected"] == 0 and fp["false_positives"] == 0
