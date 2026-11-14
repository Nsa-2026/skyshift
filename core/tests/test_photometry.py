import numpy as np
import pytest

from skyshift_core import aperture_photometry, open_level2, pixel_solid_angle_sr, to_ujy, ujy_per_mjysr

from conftest import gaussian_image, tan_wcs


def test_unit_conversion_6152_pixel():
    w = tan_wcs(180.0, 0.0, (101, 101), 6.152)
    sr = (6.152 / 3600 * np.pi / 180) ** 2
    assert pixel_solid_angle_sr(w) == pytest.approx(sr, rel=1e-6)
    assert ujy_per_mjysr(w) == pytest.approx(889.5, abs=0.5)  # 1 MJy/sr * 8.895e-10 sr = 889.5 uJy
    assert ujy_per_mjysr(w, 50, 50) == pytest.approx(ujy_per_mjysr(w), rel=1e-6)
    assert to_ujy(2.0, w) == pytest.approx(2 * 889.5, abs=1)


def test_unit_conversion_real_wcs(cutout_path):
    l2 = open_level2(cutout_path)
    lin = ujy_per_mjysr(l2.wcs)
    loc = ujy_per_mjysr(l2.wcs, 50, 50)
    assert lin == pytest.approx(889.7, abs=1.0)
    assert loc == pytest.approx(lin, rel=0.03)  # SIP changes the local area by a few %


def test_aperture_photometry_synthetic():
    w = tan_wcs(10.0, 20.0, (64, 64))
    img = gaussian_image((64, 64), [31.2], [30.7], [100.0], 1.0) + 3.0  # 100 MJy/sr summed, flat sky 3
    ph = aperture_photometry(img, 31.2, 30.7, w, r=4.0, bkg_annulus=(6.0, 9.0), variance=np.full(img.shape, 0.01))
    assert ph.flux_mjysr[0] == pytest.approx(100.0, rel=0.01)
    assert ph.flux_ujy[0] == pytest.approx(100.0 * ujy_per_mjysr(w), rel=0.01)
    assert 0 < ph.err_ujy[0] < 0.05 * ph.flux_ujy[0]


def test_aperture_photometry_fixture(cutout_path, cutout_stars):
    l2 = open_level2(cutout_path, variance=True)
    s = cutout_stars["stars"][0]
    ph = aperture_photometry(l2.data, s["x"], s["y"], l2.wcs, flags=l2.flags, variance=l2.variance)
    assert ph.flux_ujy[0] > 0
    assert ph.flux_ujy[0] / ph.err_ujy[0] > 30


def test_aperture_correction_only_at_2px(monkeypatch):
    import skyshift_core.photometry as P
    assert P.aperture_correction(2.0) == P.APERTURE_CORRECTION == 1.033
    assert P.aperture_correction(4.0) == 1.0
    w = tan_wcs(10.0, 20.0, (64, 64))
    img = gaussian_image((64, 64), [31.2], [30.7], [100.0], 1.0) + 3.0
    var = np.full(img.shape, 0.01)
    corrected = aperture_photometry(img, 31.2, 30.7, w, r=2.0, bkg_annulus=(6.0, 9.0), variance=var)
    monkeypatch.setattr(P, "APERTURE_CORRECTION", 1.0)
    raw = aperture_photometry(img, 31.2, 30.7, w, r=2.0, bkg_annulus=(6.0, 9.0), variance=var)
    assert corrected.flux_mjysr[0] == pytest.approx(1.033 * raw.flux_mjysr[0], rel=1e-9)
    assert corrected.err_ujy[0] == pytest.approx(1.033 * raw.err_ujy[0], rel=1e-9)
