import numpy as np
import pandas as pd
import pytest

from skyshift_pipeline.dots import DOT_COLUMNS, pos_err_arcsec, process_photo
from skyshift_pipeline.sky import cell_of, tile_of
from skyshift_pipeline.store import Store, dots_rel

from conftest import CUTOUT

CONTRACT = ["obs_id", "detector", "mjd", "session", "visit", "ra", "dec", "pos_err_arcsec", "x", "y", "flux_ujy",
            "flux_err_ujy", "wavelength_um", "bandwidth_um", "elong", "snr", "flags", "cell"]


def test_dots_on_real_cutout(tmp_path):
    store = Store(str(tmp_path))
    # the cutout is in Cygnus, outside our zones: give its tile a session and visit by hand
    from skyshift_core import open_level2
    l2 = open_level2(CUTOUT)
    ra0, dec0 = l2.wcs.all_pix2world(50, 50, 0)
    tile = int(tile_of(float(ra0), float(dec0)))
    st = process_photo(str(tmp_path), "2025W18_1B_0074_2", 1, str(CUTOUT), {tile: (7, 2)})
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
    assert ((df["session"] == 7) == (tile_of(df["ra"], df["dec"]) == tile)).all()
    # brightest star: flux in µJy = MJy/sr sum × ~889.7; well measured
    assert df["snr"].max() > 100 and df["flux_ujy"].max() > 1e4
    # SOURCE bit (21) is recorded under most dots, never masked
    assert ((df["flags"] & (1 << 21)) != 0).mean() > 0.5
    # restart: the existing file is skipped
    assert process_photo(str(tmp_path), "2025W18_1B_0074_2", 1, str(CUTOUT), {})["status"] == "skipped"


def test_bad_photo_is_logged_not_fatal(tmp_path, monkeypatch):
    monkeypatch.setattr("skyshift_pipeline.config.NET_RETRIES", 1)
    st = process_photo(str(tmp_path), "X", 1, str(tmp_path / "missing.fits"), {})
    assert st["status"] == "failed" and st["error"]


def test_pos_err():
    e = pos_err_arcsec(np.array([4.48, 1e6, 0.0]))
    assert e[0] == pytest.approx(np.hypot(1.0, 0.25), rel=1e-3)
    assert e[1] == pytest.approx(0.25, rel=1e-3)
    assert np.isnan(e[2])
