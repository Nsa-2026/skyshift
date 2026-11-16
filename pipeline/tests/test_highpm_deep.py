"""highpm's deep check against catalogue entries with no motion of their own (16 Nov, vetting round 1): Gaia DR3
position-only sources and AllWISE seen at their own epoch on our track (counterparts) or at our position when our motion
says we had left (gaia_still / allwise_still), CatWISE2020's agreeing motion, and the 10.5″/yr cap for pairs.
Synthetic rows; the ESA and IRSA services are faked."""
import numpy as np
import pandas as pd
from astropy.table import Table
from skyshift_core import GAIA_EPOCH_MJD, propagate
from skyshift_pipeline import highpm as HP
from skyshift_pipeline.sky import Footprint, offset_radec

from test_highpm_selection import vstats
from test_motion import STAR, hp_sources
from test_photolist import square
from test_highpm_selection import one_fit
from test_sat_astrometry import OUR, TRUE_PM, gaia_table

NO_REF = pd.DataFrame(columns=["cat", "ra", "dec", "epoch_mjd", "ref_id", "j"])
NO_GAIA = pd.DataFrame(columns=["source_id", "ra", "dec", "pmra", "pmdec", "parallax", "phot_g_mean_mag"])
AW_EPOCH = 55400.0


def new_row():
    """test_sat_astrometry's vetted "new" fit (14.4σ, 970 mas/yr), with no crossid CatWISE match."""
    xid = pd.DataFrame({"source_id": [1], "gaia_ref_id": [""], "gaia_id": [-1], "catwise_name": [""],
                        "catwise_pmra": [np.nan], "catwise_pmdec": [np.nan], "w1": [np.nan]})
    c = HP.compute(hp_sources(), None, xid, one_fit(**OUR), vstats=vstats([1]))
    f = c.query("method == 'fit'").iloc[0]
    assert (f["status"], f["vetted"], f["counterpart"]) == ("new", True, "")
    return c


def at(mjd, pm=TRUE_PM):
    """Where the object of new_row() (true motion pm) was at mjd."""
    ra, dec = propagate(OUR["ra"], OUR["dec"], *pm, mjd, epoch_mjd=OUR["epoch_mjd"])
    return float(ra), float(dec)


def pos_only(rows):
    """Gaia rows with a position only (2-parameter solution: pm and parallax NaN)."""
    return gaia_table([(sid, ra, dec, np.nan, np.nan, np.nan, g) for sid, ra, dec, g in rows])


def irsa_with(catwise=(), allwise=(), calls=None):
    """A fake IRSA: catwise rows (name, ra_pm, dec_pm, pmra, pmdec mas/yr, w1), allwise rows (designation, ra, dec, w1)."""
    def irsa(adql, uploads):
        if calls is not None:
            calls.append((adql, len(uploads["u"])))
        if "catwise_2020" in adql:
            return Table({"source_name": [c[0] for c in catwise], "ra": [c[1] for c in catwise], "dec": [c[2] for c in catwise],
                          "ra_pm": [c[1] for c in catwise], "dec_pm": [c[2] for c in catwise],
                          "pmra": [c[3] / 1000.0 for c in catwise], "pmdec": [c[4] / 1000.0 for c in catwise],
                          "sigpmra": [0.02] * len(catwise), "sigpmdec": [0.02] * len(catwise),
                          "w1mpro": [c[5] for c in catwise], "w2mpro": [c[5] - 0.5 for c in catwise],
                          "meanobsmjd": [57170.0] * len(catwise)})
        return Table({"designation": [a[0] for a in allwise], "ra": [a[1] for a in allwise], "dec": [a[2] for a in allwise],
                      "w1mpro": [a[3] for a in allwise], "w2mpro": [a[3] - 0.4 for a in allwise],
                      "w1mjdmean": [AW_EPOCH] * len(allwise)})
    return irsa


def deep(c, tap=None, irsa=None, d_ab=None, leak=True):
    """leak False: k = 0 (a fit with its parallax taken out, or a pair); else the hunt's k, whose leak range (ϖ up to
    0.25 |pm|) can take this row's whole motion back to 2016 away, so no still verdict is possible for it."""
    out, rep = HP.deep_check(c, vstats([1]) if leak else vstats([1], ke=0.0, kn=0.0), NO_REF, tap=tap or (lambda adql: NO_GAIA),
                             irsa=irsa or irsa_with(), d_ab=d_ab)
    return out.query("method == 'fit'").iloc[0], rep


def test_gaia_position_only_source_back_along_the_track_is_the_counterpart():
    """Round 1: 9 real fast stars (G 14–17, red) stayed `new` ("no catalogue lists this motion") although Gaia DR3 holds
    each 3–5″ back along the track, with a position and no motion. Now: status gaia, counterpart gaia_position, vetted."""
    r, rep = deep(new_row(), tap=lambda adql: pos_only([(4242, *at(GAIA_EPOCH_MJD), 16.5)]))
    assert r["status"] == "gaia" and r["gaia_id"] == 4242 and r["vetted"] and r["reason"] == ""
    assert r["counterpart"] == "gaia_position" and r["counterpart_id"] == "gaia:4242"
    assert r["counterpart_sep_arcsec"] < 3 and r["counterpart_epoch_mjd"] == GAIA_EPOCH_MJD
    assert rep["gaia_position"] == 1 and rep["matches"][0]["gaia"]["offset_now_arcsec"] > 5


def test_gaia_position_only_source_at_our_spot_is_a_still_source():
    """ecl34-pm-48750020658200-48749020657300: a G 21.2 position-only source 0.7″ from end A counted as a still Gaia
    star moved to our epoch (pm NaN → 0) → status gaia, still vetted, gaia_match true on the site. It is a source that
    was here in 2016 while our motion puts us 10″ away then: reason gaia_still, never vetted."""
    c = new_row()
    tap = lambda adql: pos_only([(77, OUR["ra"], OUR["dec"] + 0.7 / 3600, 21.2)])  # noqa: E731
    r, rep = deep(c, tap=tap, leak=False)
    assert r["status"] == "gaia" and r["reason"] == "gaia_still" and not r["vetted"] and r["counterpart"] == "gaia_still"
    assert rep["gaia_still"] == 1 and rep["gaia_matched"] == 0
    # v3.27: the entry is 0.7″ from where we are now (its own column) but ≥ 4.5″ from our track in 2016
    assert abs(r["counterpart_offset_now_arcsec"] - 0.7) < 0.05 and r["counterpart_sep_arcsec"] >= 4.5
    # with the hunt's leak, ϖ up to 0.25 |pm| could have kept the object within 3.4″ of here since 2016: no verdict
    r1, rep1 = deep(c, tap=tap)
    assert r1["vetted"] and r1["status"] == "new" and rep1["gaia_still"] == 0
    # both: the on-track source wins (it is our object; the still one a neighbour)
    both = lambda adql: pos_only([(77, OUR["ra"], OUR["dec"] + 0.7 / 3600, 21.2), (4242, *at(GAIA_EPOCH_MJD), 16.5)])  # noqa: E731
    r2, _ = deep(c, tap=both)
    assert r2["vetted"] and r2["counterpart"] == "gaia_position" and r2["gaia_id"] == 4242


def test_slow_motion_cannot_tell_a_still_source_from_its_own():
    """150 mas/yr moves 1.5″ since 2016: a position-only source at our spot proves nothing either way."""
    c = new_row()
    k = c["method"] == "fit"
    c.loc[k, ["pmra", "pmdec", "pm"]] = [-150.0, 0.0, 150.0]
    r, rep = deep(c, tap=lambda adql: pos_only([(77, OUR["ra"], OUR["dec"] + 0.5 / 3600, 20.0)]))
    assert r["status"] == "new" and r["vetted"] and r["counterpart"] == "" and rep["gaia_still"] == 0


def test_gaia_brighter_than_us_by_far_is_not_ours():
    """A G 12 position-only star on the track of a D1 18 source: not the same object."""
    d_ab = pd.DataFrame({f"d{k}": [18.0 if k == 1 else np.nan] for k in range(1, 7)}, index=[1])
    r, _ = deep(new_row(), tap=lambda adql: pos_only([(4242, *at(GAIA_EPOCH_MJD), 12.0)]), d_ab=d_ab)
    assert r["status"] == "new" and r["counterpart"] == ""


def test_allwise_on_the_track_lists_the_object():
    """Gliese 570 D (ecl22): no Gaia, AllWISE holds it 2010.5 on the back-track, CatWISE splits it into entries with
    wrong motions. Now: status allwise, counterpart allwise_position, vetted; a CatWISE motion that disagrees is ignored."""
    ra, dec = at(AW_EPOCH)
    calls = []
    cw = [("J1-split", *at(57170.0), 20.0, -5.0, 15.0)]  # a CatWISE entry at the 2015 spot with a wrong (still) motion
    r, rep = deep(new_row(), irsa=irsa_with(catwise=cw, allwise=[("J0825AW", ra, dec, 15.0)], calls=calls))
    assert r["status"] == "allwise" and r["vetted"] and r["counterpart"] == "allwise_position"
    assert r["counterpart_id"] == "allwise:J0825AW" and not r["catwise_pm_agree"]
    assert rep["allwise_matched"] == 1 and rep["catwise_disagrees_ignored"] == 0  # the split entry is not at our spot
    assert [n for _, n in calls] == [2, 2] and all("TAP_UPLOAD.u" in a for a, _ in calls)
    assert {a.split(" FROM ")[1].split()[0] for a, _ in calls} == {"catwise_2020", "allwise_p3as_psd"}


def test_catwise_motion_that_agrees_lists_the_motion():
    """No refcat CatWISE (the ecliptic zones were built Gaia-only): IRSA's CatWISE2020 entry whose own motion brings it
    to us and agrees → catwise_pm_agree, catwise_name, counterpart catwise_pm; status stays new (contract: no Gaia)."""
    cw = [("J082518.6+211552", *at(57170.0), *TRUE_PM, 13.0)]  # CatWISE: the true motion (ours = it + ϖ·k)
    r, rep = deep(new_row(), irsa=irsa_with(catwise=cw))
    assert r["catwise_pm_agree"] and r["catwise_name"] == "J082518.6+211552" and r["counterpart"] == "catwise_pm"
    assert r["status"] == "new" and r["vetted"] and rep["catwise_agrees"] == 1
    # a still CatWISE source at our spot: reported, never used against us
    r2, rep2 = deep(new_row(), irsa=irsa_with(catwise=[("Jstill", OUR["ra"], OUR["dec"], 0.0, 0.0, 13.0)]))
    assert r2["vetted"] and not r2["catwise_pm_agree"] and rep2["catwise_disagrees_ignored"] == 1


def test_allwise_source_where_we_are_now_is_a_still_source():
    """Round 1's fixed pairs: each end has its own AllWISE source at its position. With nothing on our track and a W1
    like our D4: reason allwise_still. A W1 far from our D4 is another object: nothing."""
    d_ab = pd.DataFrame({f"d{k}": [15.0 + 2.699 if k == 4 else np.nan] for k in range(1, 7)}, index=[1])
    aw = [("Jhere", OUR["ra"] + 0.4 / 3600, OUR["dec"], 15.2)]
    r, rep = deep(new_row(), irsa=irsa_with(allwise=aw), d_ab=d_ab, leak=False)
    assert r["reason"] == "allwise_still" and not r["vetted"] and r["counterpart"] == "allwise_still" and rep["allwise_still"] == 1
    assert abs(r["counterpart_offset_now_arcsec"] - 0.4) < 0.1
    far = [("Jhere", OUR["ra"] + 0.4 / 3600, OUR["dec"], 11.0)]
    r2, _ = deep(new_row(), irsa=irsa_with(allwise=far), d_ab=d_ab, leak=False)
    assert r2["vetted"] and r2["counterpart"] == ""
    # an AllWISE source on our track as well: the object was there, the still one is a neighbour
    r3, _ = deep(new_row(), irsa=irsa_with(allwise=[*aw, ("Jtrack", *at(AW_EPOCH, pm=(OUR["pmra"], OUR["pmdec"])), 15.1)]),
                 d_ab=d_ab, leak=False)
    assert r3["vetted"] and r3["counterpart"] == "allwise_position"


def test_irsa_failure_keeps_the_rows_and_says_so():
    def down(adql, uploads):
        raise TimeoutError("IRSA down")

    out, rep = HP.deep_check(new_row(), vstats([1]), NO_REF, tap=lambda adql: pos_only([(4242, *at(GAIA_EPOCH_MJD), 16.5)]),
                             irsa=down)
    r = out.query("method == 'fit'").iloc[0]
    assert rep["deep_wise_checked"] is False and "IRSA down" in rep["wise_error"] and rep["deep_gaia_checked"]
    assert r["counterpart"] == "gaia_position"  # Gaia still did its part


def test_crossid_gaia_match_without_motion_is_looked_at_again():
    """A fit whose crossid Gaia match has no Gaia motion: status gaia, counterpart gaia_crossid (compute). The deep check
    looks again: the same position-only source at our spot while we moved 10″ since 2016 → gaia_still."""
    src = hp_sources()
    ast = one_fit(**OUR)
    xid = pd.DataFrame({"source_id": [1], "gaia_ref_id": ["gaia:77"], "gaia_id": [77], "gaia_sep_arcsec": [0.6],
                        "gaia_pmra": [np.nan], "gaia_pmdec": [np.nan], "gaia_parallax": [np.nan],
                        "catwise_name": [""], "catwise_pmra": [np.nan], "catwise_pmdec": [np.nan], "w1": [np.nan]})
    c = HP.compute(src, None, xid, ast, vstats=vstats([1]))
    f = c.query("method == 'fit'").iloc[0]
    assert f["status"] == "gaia" and f["counterpart"] == "gaia_crossid" and f["vetted"]
    assert abs(f["counterpart_sep_arcsec"] - 0.6) < 1e-6
    r, rep = deep(c, tap=lambda adql: pos_only([(77, OUR["ra"], OUR["dec"] + 0.6 / 3600, 20.9)]), leak=False)
    assert rep["rows"] == 1 and r["reason"] == "gaia_still" and not r["vetted"]


def test_compute_counterpart_from_crossids():
    src = hp_sources()
    ast = one_fit(**OUR)
    xid = pd.DataFrame({"source_id": [1], "gaia_ref_id": [""], "gaia_id": [-1], "catwise_name": ["J1"],
                        "catwise_sep_arcsec": [0.8], "catwise_pmra": [-880.0], "catwise_pmdec": [-215.0], "w1": [13.0]})
    f = HP.compute(src, None, xid, ast, vstats=vstats([1])).query("method == 'fit'").iloc[0]
    assert (f["counterpart"], f["counterpart_id"]) == ("catwise_pm", "J1") and abs(f["counterpart_sep_arcsec"] - 0.8) < 1e-6
    xg = xid.assign(gaia_ref_id="gaia:9", gaia_id=9, gaia_pmra=-504.5, gaia_pmdec=-302.7, gaia_parallax=92.6)
    g = HP.compute(src, None, xg, ast, vstats=vstats([1])).query("method == 'fit'").iloc[0]
    assert g["counterpart"] == "gaia_pm" and g["counterpart_id"] == "gaia:9"


def test_pairs_faster_than_any_star_are_not_leads_but_slow_ones_are():
    """16 Nov: every vetted pair of the sky run (7, 23–115″/yr) was two fixed sources or a fixed source and noise,
    and real pairs ≈ chance pairs in all 61 zones. A pair over HIGHPM_FIT_MAX_PM_MAS_YR (Barnard's Star) gets
    over_pm_cap like a fit; one at 8″/yr stays a lead."""
    fps = {1: [Footprint.from_s_region(square(*STAR))] * 2, 2: [Footprint.from_s_region(square(*STAR))] * 2}
    fast = HP.compute(hp_sources(), fps, None, None).iloc[0]
    assert fast["method"] == "pair" and fast["pm"] > 50_000 and fast["reason"] == "over_pm_cap" and not fast["vetted"]
    src = hp_sources()
    b = offset_radec(*STAR, 3.2, -2.4)  # 4″ in half a year
    src.loc[src["source_id"] == 2, ["ra", "dec"]] = [b[0], b[1]]
    slow = HP.compute(src, fps, None, None).iloc[0]
    assert slow["method"] == "pair" and 7_000 < slow["pm"] < 9_000 and slow["vetted"] and slow["reason"] == ""


def test_astrometry_writes_each_fast_sources_visit_positions(rng):
    """Per-visit positions (astrometry_visits.parquet → highpm_visits.parquet): for the fitted sources fast enough for
    highpm's fit route, each visit's position (the line at the visit's mean time + the visit's mean residual), mean time
    and error; still stars get none. The mover's visit positions sit where it really was at those times."""
    from skyshift_core import tangent_offset_arcsec
    from skyshift_pipeline import astrometry as AS
    from test_sat_astrometry import scene
    d, jac, xid = scene(rng, n_quiet=30, n_sat=0, movers=[(-3000.0, 1200.0)])
    d["flags_peak"] = 0
    fit, st = AS.fit(d, None, frame=False)
    v = st["visit_positions"]
    assert list(v.columns) == AS.VISIT_COLUMNS and set(v["source_id"]) == {900}
    assert len(v) == 2 and (v["n_dots"] == 20).all() and (v["ra_err_arcsec"] > 0).all()
    for _, r in v.iterrows():
        x = d[(d["source_id"] == 900) & (d["visit"] == r["visit"])]
        assert abs(r["mjd"] - x["mjd"].mean()) < 1e-6
        e, n = tangent_offset_arcsec(float(x["ra"].mean()), float(x["dec"].mean()), r["ra"], r["dec"])
        assert np.hypot(e, n) < 0.05  # the visit's own mean position (no parallax in the scene)
    # highpm: keyed like the candidates; pairs from both ends' source positions
    src = hp_sources()
    fps = {1: [Footprint.from_s_region(square(*STAR))] * 2, 2: [Footprint.from_s_region(square(*STAR))] * 2}
    c = pd.concat([HP.compute(src, fps, None, None), pd.DataFrame({"method": ["fit"], "source_id": [900], "source_id_b": [-1]})],
                  ignore_index=True)
    hv = HP.visit_positions(c, src, v)
    assert list(hv.columns) == HP.VISIT_COLUMNS and len(hv) == 4
    pair = hv[hv["method"] == "pair"]
    assert pair[["source_id", "source_id_b"]].drop_duplicates().values.tolist() == [[1, 2]]
    assert sorted(pair["visit"]) == [1, 2] and np.allclose(sorted(pair["mjd"]), [61000.0, 61182.0])
    assert (hv[hv["method"] == "fit"]["source_id"] == 900).all()
