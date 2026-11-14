import numpy as np
import pandas as pd
import pytest

from skyshift_core import flags_under
from skyshift_pipeline.dots import (DOT_COLUMNS, DOT_LIST_COLUMNS, bad_finast, or3x3, photo_meta,
                                    pos_err_arcsec, process_photo)
from skyshift_pipeline.sky import cell_of
from skyshift_pipeline.store import Store, dots_rel

from conftest import CUTOUT

CONTRACT = ["obs_id", "detector", "mjd", "session", "visit", "ra", "dec", "pos_err_arcsec", "x", "y", "flux_ujy",
            "flux_err_ujy", "wavelength_um", "bandwidth_um", "elong", "snr", "flags", "flags_peak", "cell",
            "zodi_mjysr", "bkg_mjysr", "peak_mjysr", "a_arcsec", "b_arcsec", "theta_deg",
            "flux_psf_ujy", "flux_psf_err_ujy", "psf_chi2_nu",
            "x_psf", "y_psf", "ra_psf", "dec_psf", "pos_err_psf_arcsec", "psf_pos_ok", "sep_flag",  # v4
            "flux_ap_ujy", "flux_ap_err_ujy"]


def test_dots_on_real_cutout(tmp_path):
    st = process_photo(str(tmp_path), "2025W18_1B_0074_2", 1, str(CUTOUT), 7, 2)
    assert st["status"] == "done", st
    df = pd.read_parquet(tmp_path / dots_rel("2025W18_1B_0074_2", 1))
    assert list(df.columns) == CONTRACT == list(DOT_COLUMNS) + list(DOT_LIST_COLUMNS)
    for c, t in DOT_COLUMNS.items():
        assert str(df[c].dtype) == t, c
    assert len(df) > 50 and st["n_dots"] == len(df)
    assert (df["detector"] == 1).all() and df["mjd"].iloc[0] == pytest.approx(60794.017655738)
    assert ((df["wavelength_um"] > 0.75) & (df["wavelength_um"] < 1.13)).all()
    assert (df["bandwidth_um"] > 0).all()
    assert (df["pos_err_arcsec"] >= 0.25).all()
    assert np.nanmedian(df["elong"]) < 1.6
    np.testing.assert_array_equal(df["cell"], cell_of(df["ra"], df["dec"]))
    assert (df["session"] == 7).all() and (df["visit"] == 2).all()
    assert (df["snr"] >= 5).all()
    # flags = OR of the 3×3 pixels = core.flags_under(r=1.5) at the dot's pixel
    from skyshift_core import open_level2
    l2 = open_level2(CUTOUT)
    for r in df.head(20).itertuples():
        assert r.flags == flags_under(l2.flags, round(r.x), round(r.y), 1.5)
        assert (r.flags & r.flags_peak) == r.flags_peak
    # brightest star: flux in µJy = MJy/sr sum × ~889.7; well measured
    assert df["snr"].max() > 100 and df["flux_ujy"].max() > 1e4
    # SOURCE bit (21) is recorded under most dots, never masked
    assert ((df["flags"] & (1 << 21)) != 0).mean() > 0.5
    # rows stay in core.detect's order (brightest aperture flux first): members.parquet's dot_row relies on it
    assert (np.diff(df["flux_ujy"].to_numpy()) <= 0).all()
    # the cutout has no ZODI / EPSF HDU: those columns are NaN, the rest are filled
    assert df["zodi_mjysr"].isna().all() and df["flux_psf_ujy"].isna().all()
    # ... and no ePSF position fit: the v4 position columns carry the centroid
    assert not df["psf_pos_ok"].any()
    np.testing.assert_array_equal(df["x_psf"], df["x"])
    np.testing.assert_array_equal(df["ra_psf"], df["ra"])
    np.testing.assert_array_equal(df["pos_err_psf_arcsec"], df["pos_err_arcsec"])
    assert df["bkg_mjysr"].notna().all() and (df["peak_mjysr"] > 0).all()
    ok = df["elong"].notna()
    np.testing.assert_allclose(df["elong"][ok], (df["a_arcsec"] / df["b_arcsec"])[ok], rtol=1e-4)
    ap = np.stack(df["flux_ap_ujy"].to_numpy())
    assert ap.shape == (len(df), 5) and ap.dtype == np.float32
    bright = df["snr"].to_numpy() > 50
    assert np.median(ap[bright, 2] / ap[bright, 0]) > 1.1  # r = 2 px holds more than r = 1 px
    # r = 2 px with a local annulus agrees with flux_ujy (r = 2 px on the background map) for bright dots
    assert 0.8 < np.median(ap[bright, 2] / df["flux_ujy"].to_numpy()[bright]) < 1.2
    # photo metadata round trip
    meta = photo_meta(tmp_path / dots_rel("2025W18_1B_0074_2", 1))
    assert meta["obs_id"] == "2025W18_1B_0074_2" and meta["detector"] == 1 and meta["n_dots"] == len(df)
    assert meta["header"]["MJD-AVG"] == pytest.approx(60794.017655738)
    assert meta["header"]["FINAST"] == 0 and meta["header"]["L2 N_OVERFLOW"] == 2976
    assert meta["aperture_radii_px"] == [1.0, 1.5, 2.0, 3.0, 4.5]
    assert meta["zodi_median_mjysr"] is None and meta["read_s"] >= 0 and meta["detect_s"] > 0
    assert len(meta["grids"]["bkg_mjysr"]) == 32 and len(meta["grids"]["bkg_mjysr"][0]) == 32
    assert meta["calibration"]["psf_moments"]["fwhm_arcsec"] > 5
    # restart: the existing file is skipped
    assert process_photo(str(tmp_path), "2025W18_1B_0074_2", 1, str(CUTOUT))["status"] == "skipped"


def test_bad_photo_is_logged_not_fatal(tmp_path, monkeypatch):
    monkeypatch.setattr("skyshift_pipeline.config.NET_RETRIES", 1)
    st = process_photo(str(tmp_path), "X", 1, str(tmp_path / "missing.fits"))
    assert st["status"] == "failed" and st["error"]


def test_pos_err():
    e = pos_err_arcsec(np.array([4.48, 1e6, 0.0]))
    assert e[0] == pytest.approx(np.hypot(1.0, 0.25), rel=1e-3)
    assert e[1] == pytest.approx(0.25, rel=1e-3)
    assert np.isnan(e[2])


def test_or3x3_and_finast():
    f = np.zeros((5, 5), np.int32)
    f[0, 0] = 1
    f[4, 4] = 8
    o = or3x3(f)
    assert o[1, 1] == 1 and o[2, 2] == 0 and o[3, 3] == 8 and o[0, 0] == 1
    assert not bad_finast(0) and not bad_finast(None) and bad_finast(3) and bad_finast("x")


def _table_bits(path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    t = pq.read_table(path)
    meta = photo_meta(str(path))
    for k in ("read_s", "detect_s", "forced_s", "write_s", "stages_s"):
        meta.pop(k, None)
    cols = {}
    for name in t.column_names:
        a = t[name].combine_chunks()
        if pa.types.is_fixed_size_list(a.type):
            a = a.flatten()
        v = a.to_numpy(zero_copy_only=False)
        cols[name] = v.view(np.uint32 if v.dtype == np.float32 else np.uint64) if v.dtype.kind == "f" else v
    return cols, meta


@pytest.mark.parametrize("prefetch", [True, False])
def test_process_photos_same_files_as_process_photo(tmp_path, prefetch):
    from skyshift_pipeline.dots import process_photos
    one, many = tmp_path / "one", tmp_path / "many"
    photos = [("2025W18_1B_0074_2", 1, str(CUTOUT), 7, 2), ("2025W18_1B_0074_9", 1, str(CUTOUT), 8, 2)]
    for obs, det, url, s, v in photos:
        assert process_photo(str(one), obs, det, url, s, v)["status"] == "done"
    sts = process_photos(str(many), photos, prefetch=prefetch)
    assert [s["status"] for s in sts] == ["done", "done"]
    for obs, det, *_ in photos:
        a, ma = _table_bits(one / dots_rel(obs, det))
        b, mb = _table_bits(many / dots_rel(obs, det))
        assert a.keys() == b.keys() and ma == mb
        for c in a:
            assert np.array_equal(a[c], b[c]), c  # bitwise, NaN included
    # a rerun skips both; a missing photo fails alone without stopping the others
    assert {s["status"] for s in process_photos(str(many), photos, prefetch=prefetch)} == {"skipped"}


def test_process_photos_failed_read_does_not_stop_the_task(tmp_path, monkeypatch):
    from skyshift_pipeline.dots import process_photos
    monkeypatch.setattr("skyshift_pipeline.config.NET_RETRIES", 1)
    photos = [("X", 1, str(tmp_path / "missing.fits"), 0, 0), ("2025W18_1B_0074_2", 1, str(CUTOUT), 7, 2)]
    sts = process_photos(str(tmp_path), photos, prefetch=True)
    assert sts[0]["status"] == "failed" and sts[0]["error"] and sts[1]["status"] == "done"
