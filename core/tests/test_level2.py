import os

import numpy as np
import pytest
from astropy.io import fits
from astropy.wcs import WCS

from skyshift_core import detector_from_bandpass, open_level2
from skyshift_core.photometry import ujy_per_mjysr


def test_open_cutout(cutout_path, cutout_stars):
    l2 = open_level2(cutout_path)
    assert l2.data.shape == l2.flags.shape == (101, 101)
    assert l2.data.dtype == np.float32 and l2.flags.dtype == np.int32
    assert l2.variance is None
    assert l2.detector == 1
    assert l2.obsid == "2025W18_1B_0074_2"
    assert l2.mjd_avg == pytest.approx(60794.017655738)
    assert l2.mjd_beg < l2.mjd_avg < l2.mjd_end
    assert l2.header["BUNIT"].replace(" ", "") == "MJy/sr"
    # star positions round-trip through the cutout WCS to the full-frame sky positions
    for s in cutout_stars["stars"]:
        ra, dec = l2.wcs.all_pix2world(s["x"], s["y"], 0)
        assert abs((ra - s["ra"]) * np.cos(np.deg2rad(dec))) * 3600 < 0.01
        assert abs(dec - s["dec"]) * 3600 < 0.01


def test_variance_and_region(cutout_path):
    l2 = open_level2(cutout_path, variance=True)
    assert l2.variance is not None and l2.variance.shape == (101, 101)
    sub = open_level2(cutout_path, variance=True, region=(10, 60, 20, 90))
    assert sub.data.shape == (70, 50) and sub.origin == (10, 20)
    np.testing.assert_array_equal(sub.data, l2.data[20:90, 10:60])
    np.testing.assert_array_equal(sub.flags, l2.flags[20:90, 10:60])
    np.testing.assert_array_equal(sub.variance, l2.variance[20:90, 10:60])
    # WCS and wavelength follow the region
    a = np.array(l2.wcs.all_pix2world(35.3, 47.8, 0))
    b = np.array(sub.wcs.all_pix2world(25.3, 27.8, 0))
    np.testing.assert_allclose(a, b, atol=1e-9)
    assert sub.wavelength(25.3, 27.8) == pytest.approx(l2.wavelength(35.3, 47.8))


def test_wavelength_matches_astropy_tab(cutout_path):
    """Our bilinear WCS-WAVE helper agrees with wcslib's -TAB evaluation (WCS key 'W')."""
    l2 = open_level2(cutout_path)
    with fits.open(cutout_path) as h:
        ww = WCS(h["IMAGE"].header, fobj=h, key="W")
        for x, y in [(0, 0), (50, 50), (100, 100), (12.5, 77.25)]:
            ref = ww.wcs_pix2world([[x, y]], 0)[0]
            assert l2.wavelength(x, y) == pytest.approx(ref[0], abs=1e-6)
            assert l2.wave.bandwidth(x, y) == pytest.approx(ref[1], abs=1e-6)
    lo, hi = l2.wavelength_range()
    assert 0.75 <= lo < hi <= 1.13  # D1 covers 0.75-1.1 um
    assert l2.wavelength_map().shape == (101, 101)


def test_pixel_scale(cutout_path):
    l2 = open_level2(cutout_path)
    conv = ujy_per_mjysr(l2.wcs)
    assert conv == pytest.approx(889.7, abs=1.0)


def test_detector_from_bandpass():
    assert detector_from_bandpass("SPHEREx-D2") == 2
    with pytest.raises(ValueError):
        detector_from_bandpass("SPHEREx-D7")
    with pytest.raises(ValueError):
        detector_from_bandpass("2MASS-Ks")


@pytest.mark.network
@pytest.mark.skipif(not os.environ.get("SKYSHIFT_NETWORK"), reason="set SKYSHIFT_NETWORK=1")
def test_open_url_region(cutout_path, cutout_stars):
    """Range-read the fixture's box straight from IRSA's public S3 bucket over HTTPS."""
    url = ("https://nasa-irsa-spherex.s3.amazonaws.com/dr1/level2/2025W18_1B/l2b-v27-2026-196/1/"
           "level2_2025W18_1B_0074_2D1_spx_l2b-v27-2026-196.fits")
    x0, y0, n = cutout_stars["x0"], cutout_stars["y0"], cutout_stars["size"]
    l2 = open_level2(url, region=(x0, x0 + n, y0, y0 + n))
    ref = open_level2(cutout_path)
    assert l2.detector == 1 and l2.mjd_avg == ref.mjd_avg
    np.testing.assert_array_equal(l2.data, ref.data)
    np.testing.assert_array_equal(l2.flags, ref.flags)
    np.testing.assert_allclose(l2.wcs.all_pix2world(50, 50, 0), ref.wcs.all_pix2world(50, 50, 0), atol=1e-9)
    assert l2.wavelength(50, 50) == pytest.approx(ref.wavelength(50, 50))
