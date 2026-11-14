"""Saturated dots' offset fixed in detector pixels (astrometry, ASTRO_SAT_OFFSET) and the high-pm checks that came with
it (15 Nov review): CatWISE's motion of W1 < 8 saturated sources, and the deep Gaia / 2MASS checks before "new".
Synthetic data only; the ESA query is faked."""

import numpy as np
import pandas as pd
import pytest
from skyshift_core import GAIA_EPOCH_MJD, propagate, tangent_deproject

from skyshift_pipeline import astrometry as AS
from skyshift_pipeline import config as C
from skyshift_pipeline import highpm as HP

from test_highpm_selection import KE, KN, one_fit, vstats
from test_motion import hp_sources

STAR = (126.30, 21.25)
SCALE = 6.15
DX_SAT = -0.04  # px along the detector's x axis: the hunt's D1


def rot(pa_x_deg: float) -> np.ndarray:
    """Jacobian (″/px, rows east, north; columns x, y) with +x at position angle pa_x_deg and +y 90° further."""
    ax, ay = np.deg2rad(pa_x_deg), np.deg2rad(pa_x_deg + 90.0)
    return SCALE * np.array([[np.sin(ax), np.sin(ay)], [np.cos(ax), np.cos(ay)]])


J_VISIT = {1: rot(283.0), 2: rot(103.0)}  # the hunt: +x to PA 283° in visit 1, rolled 180° in visit 2


def scene(rng, n_quiet=60, n_sat=24, per_visit=20, err=0.04, movers=()):
    """Quiet Gaia stars (unsaturated), saturated Gaia stars (every dot DX_SAT px off along x) and saturated movers
    (pmra, pmdec), one D1 dot each per photo; two visits, the detector rolled 180° between them."""
    stars = [(100 + i, STAR[0] + 0.01 * (i % 10), STAR[1] + 0.01 * (i // 10), 0.0, 0.0, False) for i in range(n_quiet)]
    stars += [(500 + i, STAR[0] + 0.005 + 0.01 * (i % 10), STAR[1] + 0.005 + 0.01 * (i // 10), 0.0, 0.0, True) for i in range(n_sat)]
    stars += [(900 + i, STAR[0] + 0.002 + 0.01 * i, STAR[1] - 0.01, pmra, pmdec, True) for i, (pmra, pmdec) in enumerate(movers)]
    rows, photo = [], 0
    for v, t0 in ((1, 61000.0), (2, 61182.0)):
        for k in range(per_visit):
            mjd = t0 + k
            J = J_VISIT[v]
            for sid, ra, dec, pmra, pmdec, sat in stars:
                r, d = propagate(ra, dec, pmra, pmdec, mjd, epoch_mjd=GAIA_EPOCH_MJD)
                e, n = rng.normal(0, err, 2)
                if sat:
                    e, n = e + J[0, 0] * DX_SAT, n + J[1, 0] * DX_SAT
                r, d = tangent_deproject(float(r), float(d), e, n)
                rows.append(dict(source_id=sid, obs_id=f"p{photo}", detector=1, photo=photo, ra=float(r), dec=float(d), mjd=mjd,
                                 pos_err_arcsec=err, visit=v, session=photo, snr=300.0 if sat else 50.0,
                                 flags_peak=0x2 if sat else 0))
            photo += 1
    d = pd.DataFrame(rows)
    jac = pd.DataFrame({"obs_id": [f"p{p}" for p in range(photo)], "detector": np.int8(1)})
    for c, (i, j) in zip(AS.JAC_COLUMNS, ((0, 0), (0, 1), (1, 0), (1, 1))):
        jac[c] = [J_VISIT[1 if p < per_visit else 2][i, j] for p in range(photo)]
    xid = pd.DataFrame({"source_id": [s[0] for s in stars], "gaia_ref_id": [f"gaia:{s[0]}" for s in stars],
                        "gaia_id": [s[0] for s in stars], "gaia_n": 1, "gaia_ra": [s[1] for s in stars],
                        "gaia_dec": [s[2] for s in stars], "gaia_epoch_mjd": GAIA_EPOCH_MJD,
                        "gaia_pmra": [s[3] for s in stars], "gaia_pmdec": [s[4] for s in stars], "gaia_parallax": 0.0,
                        "gaia_g": [9.5 if s[5] else 15.0 for s in stars], "j": [8.0 if s[5] else 14.0 for s in stars]})
    return d, jac, xid


def test_saturated_offset_in_pixels_is_measured_and_removed(rng):
    """A saturated dot sits DX_SAT px along x; the 180° roll turns it into fake RA motion (−960 mas/yr here). Fitted on
    the quiet saturated Gaia stars in pixels, removed through each photo's Jacobian: the motion is gone, a saturated
    mover keeps its own."""
    d, jac, xid = scene(rng, movers=[(400.0, -250.0)])
    raw, _ = AS.fit(d, xid, frame=True)  # no Jacobians: no correction
    fixed, st = AS.fit(d, xid, frame=True, jac=jac)
    tab = st["sat_table"]
    d1 = tab[(tab["detector"] == 1) & (tab["depth_bin"] == "all")].iloc[0]
    assert d1["applied"] and abs(d1["dx_px"] - DX_SAT) < 0.003 and abs(d1["dy_px"]) < 0.003
    # depth: J 8.0 → AB 8.91 against D1's 10.5: −1.59, the −2.5..−1 bin
    assert tab[(tab["detector"] == 1) & (tab["depth_bin"] == "-2.5..-1")].iloc[0]["n"] == d1["n"]
    assert st["saturated_dots_corrected"] == st["saturated_dots"] == int((d["flags_peak"] == 2).sum())
    r, f = raw.set_index("source_id"), fixed.set_index("source_id")
    sat_ids = [s for s in r.index if 500 <= s < 900]
    assert np.median(r.loc[sat_ids, "pmra"]) < -800                       # the fake westward motion
    assert abs(np.median(f.loc[sat_ids, "pmra"])) < 30 and abs(np.median(f.loc[sat_ids, "pmdec"])) < 30
    quiet = [s for s in r.index if s < 500]
    assert np.allclose(r.loc[quiet, "pmra"], f.loc[quiet, "pmra"], atol=1e-6)  # unsaturated dots untouched
    m = f.loc[900]
    assert abs(m["pmra"] - 400) < 4 * m["pmra_err"] + 15 and abs(m["pmdec"] + 250) < 4 * m["pmdec_err"] + 15
    rep = AS.sat_report(tab, st["saturated_dots"], st["saturated_dots_corrected"])
    assert rep["enabled"] and rep["measured_on_dots"] == d1["n"] and any(o["applied"] for o in rep["offsets"])


def test_saturated_offsets_from_tiles_equal_the_untiled_fit(rng):
    """tiles.py: sat_residuals per tile (raw), sat_offsets on all of them after the zone's frame offsets — the same table
    the untiled fit measures, and the same fit when that table is passed."""
    d, jac, xid = scene(rng, n_quiet=40, n_sat=16)
    rng2 = np.random.default_rng(5)
    shift = {p: rng2.normal(0, 0.3, 2) for p in d["photo"].unique()}  # every photo's WCS off a little
    e = np.array([shift[p][0] for p in d["photo"]])
    n = np.array([shift[p][1] for p in d["photo"]])
    d["ra"], d["dec"] = tangent_deproject(d["ra"].to_numpy(), d["dec"].to_numpy(), e, n)
    cor, st = AS.fit(d, xid, frame=True, jac=jac)
    untiled = st["sat_table"]
    halves = [d[d["source_id"] % 2 == k] for k in (0, 1)]  # two "tiles" owning alternate sources
    res = pd.concat([AS.sat_residuals(h, xid, jac) for h in halves], ignore_index=True)
    off, _ = AS.photo_offsets(pd.concat([AS.frame_residuals(h, xid) for h in halves], ignore_index=True))
    tiled = AS.sat_offsets(res, off)
    pd.testing.assert_frame_equal(tiled, untiled, rtol=1e-9)
    cor2, _ = AS.fit(d, xid, frame=True, offsets=off, jac=jac, sat=tiled)
    pd.testing.assert_frame_equal(cor, cor2, rtol=1e-9)


def test_sat_depth_and_shift_pick_the_bin():
    xid = pd.DataFrame({"source_id": [1, 2, 3], "j": [8.0, 11.5, np.nan], "k": [7.0, np.nan, np.nan],
                        "w1": [np.nan] * 3, "w2": [np.nan] * 3})
    dep = AS.sat_depth(xid, np.array([1, 2, 3, 1]), np.array([1, 1, 1, 3]))
    assert dep[0] == pytest.approx(8.0 + 0.91 - 10.5) and dep[1] == pytest.approx(11.5 + 0.91 - 10.5)
    assert np.isnan(dep[2]) and dep[3] == pytest.approx(7.0 + 1.85 - 11.0)
    tab = pd.DataFrame([dict(detector=1, depth_bin="all", depth_min=np.nan, depth_max=np.nan, dx_px=-0.03, dy_px=0.0,
                             dx_err_px=0.001, dy_err_px=0.001, n=500, applied=True),
                        dict(detector=1, depth_bin="-2.5..-1", depth_min=-2.5, depth_max=-1.0, dx_px=-0.05, dy_px=0.01,
                             dx_err_px=0.001, dy_err_px=0.001, n=300, applied=True),
                        dict(detector=1, depth_bin=">=1.5", depth_min=1.5, depth_max=np.inf, dx_px=0.02, dy_px=0.0,
                             dx_err_px=0.001, dy_err_px=0.001, n=50, applied=False)], columns=AS.SAT_COLUMNS)
    J = np.repeat(np.eye(2)[None] * SCALE, 4, axis=0)
    de, dn = AS.sat_shift(np.array([1, 1, 1, 2]), np.array([-1.59, 1.91, np.nan, -1.5]), J, tab)
    assert de.tolist() == pytest.approx([-0.05 * SCALE, -0.03 * SCALE, -0.03 * SCALE, 0.0])  # bin, "all" (bin not applied), "all", none
    assert dn.tolist() == pytest.approx([0.01 * SCALE, 0.0, 0.0, 0.0])


# --- highpm: CatWISE of bright saturated sources -----------------------------------------------------------------
def test_catwise_motion_of_a_bright_saturated_source_checks_nothing():
    """fp_control's last 2 fakes (hunt): G 8 still stars, saturated, whose fake motion CatWISE 'confirmed' (W1 < 8)."""
    def run(saturated, w1):
        src = hp_sources().assign(saturated=saturated)
        ast = one_fit(pmra=-650.0, pmdec=-200.0, pm=680.0, pmra_err=150.0, pmdec_err=150.0, pm_snr=4.6)
        xid = pd.DataFrame({"source_id": [1], "gaia_ref_id": [""], "gaia_id": [-1], "catwise_name": ["J1"],
                            "catwise_pmra": [-600.0], "catwise_pmdec": [-180.0], "w1": [w1]})
        return HP.compute(src, None, xid, ast, vstats=vstats([1])).query("method == 'fit'").iloc[0]

    bright = run(True, 7.2)
    assert not bright["vetted"] and bright["reason"] == "unchecked" and not bright["catwise_pm_agree"]
    assert run(True, 9.0)["vetted"] and run(True, 9.0)["catwise_pm_agree"]   # fainter: CatWISE's motion counts
    assert run(False, 7.2)["vetted"]                                          # unsaturated: counts too


# --- highpm: deep Gaia and 2MASS before "new" ---------------------------------------------------------------------
OUR = dict(ra=126.3278, dec=21.2621, epoch_mjd=61056.0, pmra=-951.5, pmdec=-187.7, pm=969.8, pmra_err=67.0,
           pmdec_err=67.0, pm_snr=14.4)
TRUE_PM, PLX = (-504.5, -302.7), 92.6  # Gaia DR3 664214600677732864, the hunt's one vetted "new" row (15 Nov)


def new_row():
    src = hp_sources()
    ast = one_fit(**OUR)
    xid = pd.DataFrame({"source_id": [1], "gaia_ref_id": [""], "gaia_id": [-1], "catwise_name": ["J1"],
                        "catwise_pmra": [-880.0], "catwise_pmdec": [-215.0], "w1": [13.0]})
    c = HP.compute(src, None, xid, ast, vstats=vstats([1]))
    assert c.query("method == 'fit'").iloc[0][["status", "vetted"]].tolist() == ["new", True]
    return c


def gaia_table(rows):
    return pd.DataFrame([dict(source_id=sid, ra=ra, dec=dec, pmra=pmra, pmdec=pmdec, parallax=plx, phot_g_mean_mag=g,
                              pmra_error=1.0, pmdec_error=1.0, parallax_error=1.0) for sid, ra, dec, pmra, pmdec, plx, g in rows])


def l_dwarf_2016():
    ra, dec = propagate(OUR["ra"], OUR["dec"], *TRUE_PM, GAIA_EPOCH_MJD, epoch_mjd=OUR["epoch_mjd"])
    return float(ra), float(dec)


def test_deep_gaia_finds_the_faint_known_dwarf():
    """G 20.4, not in refcat (G < 20): found around where our motion less a parallax leak puts us in 2016; its own motion
    plus ϖ·k agrees with ours → status gaia, still vetted (a known nearby dwarf, not a discovery)."""
    c = new_row()
    queries = []

    def tap(adql):
        queries.append(adql)
        return gaia_table([(664214600677732864, *l_dwarf_2016(), *TRUE_PM, PLX, 20.40),
                           (1234, OUR["ra"] + 0.01, OUR["dec"], 0.0, 0.0, 0.5, 19.0)])  # elsewhere: ignored

    out, rep = HP.deep_check(c, vstats([1]), pd.DataFrame(columns=["cat", "ra", "dec", "epoch_mjd", "ref_id", "j"]), tap=tap)
    r = out.query("method == 'fit'").iloc[0]
    assert r["status"] == "gaia" and r["gaia_id"] == 664214600677732864 and r["gaia_ref_id"] == "gaia:664214600677732864"
    assert r["vetted"] and r["reason"] == ""
    assert rep["deep_gaia_checked"] and rep["rows"] == 1 and rep["gaia_agrees"] == 1
    assert len(queries) == 1 and "gaiadr3.gaia_source" in queries[0] and queries[0].count("CONTAINS") == 2
    assert "phot_g_mean_mag <" not in queries[0]  # no magnitude cut


def test_deep_gaia_still_star_at_our_spot_disproves_the_motion():
    c = new_row()
    tap = lambda adql: gaia_table([(77, OUR["ra"], OUR["dec"] + 0.5 / 3600, 2.0, -1.0, 0.8, 20.6)])  # noqa: E731
    out, rep = HP.deep_check(c, vstats([1]), pd.DataFrame(columns=["cat", "ra", "dec", "epoch_mjd", "ref_id", "j"]), tap=tap)
    r = out.query("method == 'fit'").iloc[0]
    assert r["status"] == "gaia" and r["reason"] == "gaia_pm_disagrees" and not r["vetted"] and rep["gaia_disagrees"] == 1


def test_deep_gaia_network_failure_keeps_new_and_says_so():
    c = new_row()

    def tap(adql):
        raise TimeoutError("ESA down")

    out, rep = HP.deep_check(c, vstats([1]), pd.DataFrame(columns=["cat", "ra", "dec", "epoch_mjd", "ref_id", "j"]), tap=tap)
    r = out.query("method == 'fit'").iloc[0]
    assert r["status"] == "new" and r["vetted"] and rep["deep_gaia_checked"] is False and "ESA down" in rep["error"]


def test_deep_2mass_where_our_motion_was_in_2000():
    """No Gaia answer: a 2MASS source 16″ away, where our motion less ϖ·k (ϖ ≈ 93 mas) puts us at its epoch → '2mass'.
    The same source at the plain back-propagated position (no leak) would be 12″ off: the leak matters."""
    c = new_row()
    ep = 51200.0
    ra, dec = propagate(OUR["ra"], OUR["dec"], *TRUE_PM, ep, epoch_mjd=OUR["epoch_mjd"])
    ref = pd.DataFrame({"ref_id": ["2mass:J08251968+2115521", "2mass:Jfar"], "cat": ["tmass", "tmass"],
                        "ra": [float(ra), float(ra) + 0.01], "dec": [float(dec), float(dec)], "epoch_mjd": [ep, ep],
                        "j": [15.1, 12.0]})
    out, rep = HP.deep_check(c, vstats([1]), ref, tap=lambda adql: gaia_table([]))
    r = out.query("method == 'fit'").iloc[0]
    assert r["status"] == "2mass" and r["vetted"] and rep["tmass_matched"] == 1
    m = rep["matches"][0]["tmass"]
    assert m["ref_id"] == "2mass:J08251968+2115521" and m["offset_now_arcsec"] > 10 and 50 < m["parallax_used_mas"] < 150
    # J far from our D2 brightness: not ours
    d2 = pd.Series([12.0], index=[1])
    assert HP.deep_check(c, vstats([1]), ref.iloc[:1], d2, tap=lambda adql: gaia_table([]))[0].query(
        "method == 'fit'").iloc[0]["status"] == "new"


def test_deep_check_only_touches_vetted_new_rows():
    c = new_row()
    c.loc[c["method"] == "fit", "vetted"] = False
    c.loc[c["method"] == "fit", "reason"] = "few_dots"
    keep = c["vetted"] & (c["status"] == "new")
    called = []

    def tap(adql):
        called.append(adql)
        return gaia_table([(1234, 10.0, -10.0, 0.0, 0.0, 0.5, 19.0)])  # far away: no counterpart

    out, rep = HP.deep_check(c, vstats([1]), pd.DataFrame(columns=["cat", "ra", "dec", "epoch_mjd", "ref_id", "j"]), tap=tap)
    assert rep["rows"] == int(keep.sum()) and bool(called) == bool(keep.any())
    assert rep["gaia_matched"] == 0 and rep["tmass_matched"] == 0
    pd.testing.assert_frame_equal(out.query("method == 'fit'").reset_index(drop=True),
                                  c.query("method == 'fit'").reset_index(drop=True))
