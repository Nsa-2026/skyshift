"""brown_dwarf_like needs water where water can be seen, AllWISE colours veto AGN (16 Nov: 136 of 165 labels were
methane-only galaxies or AGN), and crossid's 2MASS match allows a borrowed pm's error along the track."""
import numpy as np
import pandas as pd
from astropy.table import Table
from skyshift_core import tangent_deproject
from skyshift_pipeline import classify as CL
from skyshift_pipeline import config as C
from skyshift_pipeline import crossid as XI
from skyshift_pipeline import indices as IX

from test_classify import no_gaia, sources_frame, t_dwarf_spectrum


def methane_only(bright_nir=True):
    """Flat in Fν (red: J − [4.6] = 0) with a deep 3.2–3.45 µm dip and no water: a galaxy's PAH-side "methane"."""
    lam = IX.CENTRE
    f = np.full(lam.size, 500.0) * np.where((lam > 3.15) & (lam < 3.55), 0.5, 1.0)
    src = sources_frame([1], f)
    if not bright_nir:  # a Y dwarf: nothing measurable at 1.1–2.4 µm
        e = np.where((lam > 1.1) & (lam < 2.4), 50 * f, 0.02 * f)
        src.at[0, "err_ujy"] = e.astype(np.float32)
    return src


def classify(src):
    sids = src["source_id"].to_numpy(np.int64)
    return CL.classify_frame(src, CL.shape_features(None, sids), no_gaia(sids)).iloc[0]


def test_methane_without_water_where_water_shows_is_not_a_brown_dwarf():
    r = classify(methane_only())
    assert r["ch4_33_sig"] >= 5 and r["ch4_33_depth"] > 0.25 and abs(r["h2o_14_depth"]) < 0.05
    assert CL.water_testable(pd.DataFrame([r]))[0]
    assert not r["brown_dwarf_like"] and r["bd_veto"] == "no_water" and r["bd_evidence"] == "ch4_33"


def test_methane_alone_counts_where_water_cannot_be_seen():
    r = classify(methane_only(bright_nir=False))
    assert not CL.water_testable(pd.DataFrame([r]))[0]
    assert r["brown_dwarf_like"] and r["bd_veto"] == ""


def test_t_dwarf_with_water_stays():
    r = classify(sources_frame([1], t_dwarf_spectrum()))
    assert r["brown_dwarf_like"] and ("h2o_14" in r["bd_evidence"] or "h2o_19" in r["bd_evidence"])


def allwise_fake(rows, calls=None):
    """rows: (i, w1, w2, w3, w3snr, sep″) per answer."""
    def tap(adql, uploads):
        if calls is not None:
            calls.append(len(uploads["u"]))
        u = uploads["u"]
        return Table({"i": [r[0] for r in rows], "designation": [f"J{r[0]}" for r in rows],
                      "ra": [float(u["ra"][r[0]]) + r[5] / 3600.0 for r in rows], "dec": [float(u["dec"][r[0]]) for r in rows],
                      "w1mpro": [r[1] for r in rows], "w2mpro": [r[2] for r in rows], "w3mpro": [r[3] for r in rows],
                      "w3snr": [r[4] for r in rows], "ext_flg": [0] * len(rows)})
    return tap


def test_agn_colours_veto_a_brown_dwarf_like_row():
    td = t_dwarf_spectrum()
    src = sources_frame([1, 2, 3, 4], td)
    sids = src["source_id"].to_numpy(np.int64)
    out = CL.classify_frame(src, CL.shape_features(None, sids), no_gaia(sids))
    assert out["brown_dwarf_like"].all()
    calls = []
    # 1: AGN (W2 − W3 4.0, W3 S/N 30, W1 − W2 0.7); 2: a Y dwarf (W1 − W2 2.6, W3-bright); 3: W3 undetected; 4: none
    tap = allwise_fake([(0, 15.0, 14.3, 10.3, 30.0, 0.4), (1, 17.0, 14.4, 11.9, 8.0, 0.3), (2, 15.0, 14.3, 10.3, 2.0, 0.2)],
                       calls)
    v, st = CL.agn_veto(out, src, tap)
    assert v["brown_dwarf_like"].tolist() == [False, True, True, True] and v["bd_veto"].tolist()[0] == "agn_colours"
    assert np.isclose(v["allwise_w2_w3"].iloc[0], 4.0) and np.isnan(v["allwise_w2_w3"].iloc[3])
    assert st == {"checked": True, "rows": 4, "vetoed": 1} and calls == [4]

    def down(adql, uploads):
        raise TimeoutError("IRSA down")

    v2, st2 = CL.agn_veto(out, src, down)
    assert v2["brown_dwarf_like"].all() and st2["checked"] is False and "IRSA down" in st2["error"]


def test_crossid_2mass_allows_a_borrowed_pm_s_error_along_the_track():
    """hunt-sp-66757007063700 (2MASSW J0825+2115): CatWISE's pm (−621, −404) mas/yr over 27 years puts the source 4.4″
    past its 2MASS entry, 0.7″ across: matched now (CROSSID_PM_REL_TOL); the same 4.4″ across the track is not."""
    ra0, dec0, mjd, ep = 126.33, 21.26, 61056.0, 50822.0
    pm = (-620.8, -404.0)
    dt = (ep - mjd) / 365.25
    move = np.array(pm) * dt / 1000.0  # where the CatWISE pm puts it in 1998 (″ east, north)
    u = move / np.hypot(*move)
    short = move - 4.4 * u + 0.7 * np.array([u[1], -u[0]])  # the real entry: 4.4″ short along, 0.7″ across
    side = move + 4.4 * np.array([u[1], -u[0]])            # 4.4″ across: another source
    ref = pd.DataFrame({"ref_id": ["2mass:J0825", "2mass:Jside"], "cat": ["tmass", "tmass"],
                        "ra": [float(tangent_deproject(ra0, dec0, *short)[0]), float(tangent_deproject(ra0, dec0, *side)[0])],
                        "dec": [float(tangent_deproject(ra0, dec0, *short)[1]), float(tangent_deproject(ra0, dec0, *side)[1])],
                        "epoch_mjd": [ep, ep], "pmra": np.nan, "pmdec": np.nan, "parallax": np.nan})
    spm = (np.array([pm[0]]), np.array([pm[1]]))
    i, s, k = XI.crossmatch(np.array([ra0]), np.array([dec0]), np.array([mjd]), ref.iloc[:1].reset_index(drop=True), 3.0,
                            src_pm=spm)
    assert i[0] == 0 and 4.0 < s[0] < 5.0 and k[0] == 1
    i, s, k = XI.crossmatch(np.array([ra0]), np.array([dec0]), np.array([mjd]), ref.iloc[1:].reset_index(drop=True), 3.0,
                            src_pm=spm)
    assert i[0] == -1
    # the tolerance is only along: without it (0) the real entry is missed, as before
    old = C.CROSSID_PM_REL_TOL
    try:
        C.CROSSID_PM_REL_TOL = 0.0
        assert XI.crossmatch(np.array([ra0]), np.array([dec0]), np.array([mjd]), ref.iloc[:1].reset_index(drop=True), 3.0,
                             src_pm=spm)[0][0] == -1
    finally:
        C.CROSSID_PM_REL_TOL = old
    # a still source next to the fast one (the query circle is as wide as the fastest source needs): the plain
    # distance, not "no motion, so nothing is off the track" (a first version matched 2,894 still hunt sources)
    ras, decs = np.array([ra0, float(ref["ra"].iloc[1])]), np.array([dec0, float(ref["dec"].iloc[1]) + 5 / 3600])
    two = (np.array([pm[0], np.nan]), np.array([pm[1], np.nan]))
    i, s, k = XI.crossmatch(ras, decs, np.array([mjd, mjd]), ref, 3.0, src_pm=two)
    assert i.tolist() == [0, -1]
    decs[1] -= 4 / 3600  # 1″ from the side entry: matched
    i, s, k = XI.crossmatch(ras, decs, np.array([mjd, mjd]), ref, 3.0, src_pm=two)
    assert i.tolist() == [0, 1] and abs(s[1] - 1.0) < 0.01
