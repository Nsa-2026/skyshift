import numpy as np
import pandas as pd
import pytest

from skyshift_core import flags_under
from skyshift_pipeline.dots import DOT_COLUMNS, bad_finast, or3x3, pos_err_arcsec, process_photo
from skyshift_pipeline.sky import cell_of
from skyshift_pipeline.store import Store, dots_rel

from conftest import CUTOUT

CONTRACT = ["obs_id", "detector", "mjd", "session", "visit", "ra", "dec", "pos_err_arcsec", "x", "y", "flux_ujy",
            "flux_err_ujy", "wavelength_um", "bandwidth_um", "elong", "snr", "flags", "flags_peak", "cell"]


def test_dots_on_real_cutout(tmp_path):
    st = process_photo(str(tmp_path), "2025W18_1B_0074_2", 1, str(CUTOUT), 7, 2)
    assert st["status"] == "done", st
    df = pd.read_parquet(tmp_path / dots_rel("2025W18_1B_0074_2", 1))
    assert list(df.columns) == CONTRACT == list(DOT_COLUMNS)
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
