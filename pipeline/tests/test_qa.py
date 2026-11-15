"""qa: footers read with one ranged read, grades against the zone's other photos, red photos out of light curves."""

from __future__ import annotations

import io
import json

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from skyshift_pipeline import config as C
from skyshift_pipeline import lightcurves, qa
from skyshift_pipeline.store import Store, dots_rel, photolist_rel, results_rel

ZONE = C.Zone("qatest", 10.0, 12.0, 0.0, 2.0)


def _meta(obs, det, mjd, n_dots=3300, fwhm=9.5, psf_ap=1.02, psf_n=400, gaia_n=600, dra=0.1, ddec=0.0, sig=0.3, saa=60.0,
          satstrks=1, status="done"):
    return {"schema": 1, "obs_id": obs, "detector": det, "status": status, "n_dots": n_dots,
            "header": {"MJD-AVG": mjd, "SGT_MIN_GCD2SAA": saa, "L2 SATSTRKS": satstrks, "MATCH_NSTARS": 500, "FINAST": 0},
            "calibration": {"astrometry_vs_gaia": {"n": gaia_n, "dra_cosdec_arcsec": dra, "ddec_arcsec": ddec,
                                                   "sigma_ra_arcsec": sig, "sigma_dec_arcsec": sig},
                            "psf_moments": {"n": psf_n, "fwhm_arcsec": fwhm},
                            "psf_over_aperture_flux": {"n": psf_n, "median": psf_ap}},
            "grids": {"n": 32, "bkg_mjysr": [[0.1] * 32] * 32}}


def _write_dots(store: Store, meta: dict, n: int = 60, sharp: int = 0, outlier: int = 0) -> None:
    """A dot file with `n` bright star-like dots, `sharp` of them much sharper (`outlier` of those with OUTLIER at the peak)."""
    a = np.full(n, 4.0, np.float32)
    a[:sharp] = 1.5
    pk = np.zeros(n, np.int32)
    pk[:outlier] = qa.OUTLIER_BIT
    t = pa.table({"snr": np.full(n, 80.0, np.float32), "a_arcsec": a, "b_arcsec": a, "flags": np.zeros(n, np.int32),
                  "flags_peak": pk, "ra": np.zeros(n)})
    t = t.replace_schema_metadata({C.PHOTO_META_KEY: json.dumps(meta)})
    buf = io.BytesIO()
    pq.write_table(t, buf)
    store.write_bytes(dots_rel(meta["obs_id"], meta["detector"]), buf.getvalue())


def _zone(tmp_path, specials: dict | None = None, calib_scatter: dict | None = None, shapes: dict | None = None) -> Store:
    """30 normal D6 photos in visit 1 and 30 in visit 2; `specials` {i: meta overrides} for photo i (visit 1: 0–29)."""
    store = Store(str(tmp_path))
    rows, cal = [], []
    for i in range(60):
        obs, mjd = f"2026W{10 + i // 30}_1A_{i:04d}_1", 61000.0 + i * 0.01 + (i // 30) * 180
        rows.append({"obs_id": obs, "detector": 6, "mjd": mjd, "visit": 1 + i // 30, "session": i})
        kw = (specials or {}).get(i, {})
        meta = _meta(obs, 6, mjd, **kw)
        if kw.get("status") == "missing":
            continue
        sh = (shapes or {}).get(i, (0, 0))
        _write_dots(store, meta, sharp=sh[0], outlier=sh[1])
        cal.append({"obs_id": obs, "detector": np.int8(6), "order": np.int8(2), "n_stars": np.int32(500),
                    "scatter_corrected": np.float32((calib_scatter or {}).get(i, 0.14 + 0.01 * (i % 3))),
                    "psf_scatter_corrected": np.float32(0.13)})
    store.write_parquet(photolist_rel(ZONE.name), pd.DataFrame(rows))
    store.write_parquet(results_rel(ZONE.name, "calib_photos.parquet"), pd.DataFrame(cal))
    return store


def test_read_meta_one_ranged_read_and_long_footers(tmp_path):
    store = _zone(tmp_path)
    rel = dots_rel("2026W10_1A_0003_1", 6)
    m = qa.read_meta(store, rel)
    assert m["obs_id"] == "2026W10_1A_0003_1" and m["header"]["SGT_MIN_GCD2SAA"] == 60.0
    assert qa.read_meta(store, rel, tail=64) == m  # footer longer than the tail: a second read
    assert qa._meta_or_status(store, dots_rel("NOPE", 1)) == (None, "missing")
    buf = io.BytesIO()
    pq.write_table(pa.table({"x": [1]}), buf)
    store.write_bytes("dots/OLD_1D1.parquet", buf.getvalue())
    assert qa._meta_or_status(store, "dots/OLD_1D1.parquet") == (None, "no_meta")


def test_grades_and_reasons(tmp_path):
    specials = {
        1: {"n_dots": 3300 * 4, "saa": 1.2, "satstrks": 34},   # SAA passage: 4× the dots
        2: {"n_dots": int(3300 * 1.6), "saa": 1.5},            # near the SAA: 1.6× is red there ...
        3: {"n_dots": int(3300 * 1.6), "saa": 40.0},           # ... and amber elsewhere
        4: {"fwhm": 3.9, "psf_ap": 0.51},                      # visit-3-D6-like artefact photo
        5: {"psf_ap": 0.70},
        6: {"dra": 2.0, "ddec": 0.5},                          # raw offset 1.9″ beyond the detector's median
        7: {"gaia_n": 5, "dra": 3.0},                          # too few Gaia stars: not checked
        8: {"status": "done_finast", "n_dots": 0},
        9: {"status": "missing"},
    }
    store = _zone(tmp_path, specials, calib_scatter={10: 0.75, 11: 0.33})
    out = qa.run(store, ZONE, workers=2)
    t = store.read_parquet(results_rel(ZONE.name, qa.PHOTOS_REL))
    assert list(t.columns[:4]) == ["obs_id", "detector", "grade", "reasons"]
    assert t["obs_id"].map(type).eq(str).all() and t["detector"].dtype == np.int64 and t["reasons"].map(type).eq(str).all()
    g = {int(o.split("_")[2]): (gr, r) for o, gr, r in zip(t["obs_id"], t["grade"], t["reasons"])}
    assert g[1][0] == "red" and {"dots_excess", "saa", "satstreaks"} <= set(g[1][1].split(","))
    assert g[2] == ("red", "dots_excess,saa")
    assert g[3] == ("amber", "dots_excess")
    assert g[4][0] == "red" and set(g[4][1].split(",")) == {"fwhm_low", "psf_ap_low"}
    assert g[5] == ("red", "psf_ap_low")
    assert g[6] == ("red", "astrometry_offset")
    assert g[7] == ("green", "")  # its 3″ offset rests on 5 stars: not checked (calib's scatter is)
    assert g[8] == ("red", "finast")
    assert 9 not in g and out["missing_dot_files"] == 1
    assert g[10] == ("red", "astrometry_scatter")   # 0.75″ = 5× the detector's 0.15″
    assert g[11] == ("amber", "astrometry_scatter")  # 2.2×
    assert all(g[i] == ("green", "") for i in range(12, 60))
    row = t.set_index("obs_id").loc["2026W10_1A_0007_1"]
    assert np.isnan(row["offset_rel_arcsec"]) and row["astrometry_source"] == "calib"
    assert t["astrometry_checked"].sum() == len(t) - 1  # all but the FINAST one
    rep = store.read_json(results_rel(ZONE.name, qa.REPORT_REL))
    assert rep["counts"] == {"green": 50, "amber": 2, "red": 7} and rep["per_detector"]["D6"]["red"] == 7
    assert rep["thresholds"]["QA_DOTS_RATIO_RED"] == C.QA_DOTS_RATIO_RED and rep["worst"][0]["grade"] == "red"
    assert out["counts"]["red"] == 7 and out["shapes"] is True  # a local root: dot columns read


def test_sharp_artefacts_and_outlier_alone(tmp_path, monkeypatch):
    shapes = {1: (40, 35), 2: (20, 15), 3: (3, 3), 4: (40, 0)}  # (sharp, sharp with OUTLIER) of 60 bright dots
    store = _zone(tmp_path, shapes=shapes)
    qa.run(store, ZONE, workers=2)
    t = store.read_parquet(results_rel(ZONE.name, qa.PHOTOS_REL)).set_index("obs_id")
    r = lambda i: t.loc[f"2026W10_1A_{i:04d}_1"]  # noqa: E731
    assert (r(1)["grade"], r(1)["reasons"]) == ("red", "sharp_artefacts")
    assert r(1)["sharp_frac"] == pytest.approx(40 / 60) and r(1)["sharp_outlier_frac"] == pytest.approx(35 / 40)
    assert (r(2)["grade"], r(2)["reasons"]) == ("amber", "sharp_artefacts")
    assert r(3)["grade"] == "green"  # a few sharp dots, all with OUTLIER: OUTLIER alone is never a reason
    assert (r(4)["grade"], r(4)["reasons"]) == ("amber", "sharp_artefacts")  # sharp but no OUTLIER: not red
    store2 = _zone(tmp_path / "b", shapes=shapes)
    monkeypatch.setattr(C, "QA_SHAPES", "off")
    st = qa.run(store2, ZONE, workers=2)
    t2 = store2.read_parquet(results_rel(ZONE.name, qa.PHOTOS_REL))
    assert st["shapes"] is False and t2["sharp_frac"].isna().all() and (t2["grade"] == "green").all()


def test_without_calib_falls_back_to_the_dot_files(tmp_path):
    store = _zone(tmp_path, {1: {"sig": 1.2}})
    store.fs.rm(store.path(results_rel(ZONE.name, "calib_photos.parquet")))
    st = qa.run(store, ZONE, workers=1)
    t = store.read_parquet(results_rel(ZONE.name, qa.PHOTOS_REL)).set_index("obs_id")
    assert st["calib_photos"] is False and (t["astrometry_source"] == "dots").all()
    assert (t.loc["2026W10_1A_0001_1", "grade"], t.loc["2026W10_1A_0001_1", "reasons"]) == ("red", "astrometry_scatter")


def test_run_safe_and_red_photos(tmp_path):
    store = Store(str(tmp_path))
    assert "error" in qa.run_safe(store, ZONE)  # no photo list
    assert qa.red_photos(store, ZONE) is None
    store = _zone(tmp_path / "z", {4: {"psf_ap": 0.5}})
    qa.run(store, ZONE)
    red = qa.red_photos(store, ZONE)
    assert list(red["obs_id"]) == ["2026W10_1A_0004_1"] and red["mjd"].iloc[0] == pytest.approx(61000.04)


def test_qa_red_mask_matches_detector_and_exposure():
    red = pd.DataFrame({"obs_id": ["a"], "detector": [4], "mjd": [61000.123456789]})
    p = pd.DataFrame({"detector": np.array([4, 1, 4, 4], np.int8), "mjd": [61000.123456789, 61000.123456789, 61000.1235, 61000.2]})
    assert lightcurves.qa_red_mask(p, red).tolist() == [True, False, False, False]
    assert not lightcurves.qa_red_mask(p, None).any()


def test_lightcurves_leave_red_photos_out(tmp_path):
    """On the tiles' synthetic root: qa grades the photo whose footer says PSF/aperture 0.5 red, and its light-curve
    points stay in the table with photo_ok false (tiled == untiled is tests/test_tiles.py)."""
    from test_tiles import RED_PHOTO, ZONE as TZ, make_root
    from skyshift_pipeline import calib, crossid, link, spectra
    store = make_root(tmp_path)
    for m in (link, spectra, crossid, calib):
        m.run(store, TZ, 2)
    out = qa.run(store, TZ, 2)
    assert out["counts"]["red"] == 1
    lightcurves.run(store, TZ, 2)
    pts = store.read_parquet(results_rel(TZ.name, "lightcurves.parquet"))
    ph = store.read_parquet(photolist_rel(TZ.name)).set_index(["obs_id", "detector"]).loc[RED_PHOTO]
    on = (pts["detector"] == RED_PHOTO[1]) & (np.abs(pts["mjd"] - ph["mjd"]) < 1e-6)
    assert on.sum() > 50 and not pts.loc[on, "photo_ok"].any() and pts.loc[~on, "photo_ok"].mean() > 0.9
    assert store.read_json(results_rel(TZ.name, "lightcurves_report.json"))["qa_red_photos"] == 1


def test_cli_runs_qa_after_calib(tmp_path, monkeypatch):
    from skyshift_pipeline import cli
    assert cli.STEPS.index("qa") == cli.STEPS.index("calib") + 1 and "qa" in cli.TILED_STEPS
    store = _zone(tmp_path, {4: {"psf_ap": 0.5}})
    monkeypatch.setitem(C.ZONES, ZONE.name, ZONE)
    assert cli.main(["qa", "--zone", ZONE.name, "--root", store.root, "--workers", "1"]) == 0
    summ = store.read_json(results_rel(ZONE.name, "summary.json"))["steps"]["qa"]
    assert summ["counts"]["red"] == 1 and "step_seconds" in summ
