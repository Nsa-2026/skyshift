import os
from pathlib import Path

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


# --- ZODI and EPSF (optional reads) -----------------------------------------------------------
EPSF_FILE = Path(__file__).parent / "data" / "epsf_d1_centre3x3.npz"  # 9 real zones (BINX, BINY 9-11)

def epsf_hdu(path) -> "fits.BinTableHDU":
    """An EPSF HDU (as in a Level 2 file) built from the real zones saved in tests/data/*.npz
    (*.fits is git-ignored outside fixtures/)."""
    z = np.load(path)
    cols = [fits.Column(name=c, format=f, array=z[c], **kw) for c, f, kw in [
        ("BINX", "J", {}), ("BINY", "J", {}), ("XCENTER", "E", {"unit": "pixel"}), ("YCENTER", "E", {"unit": "pixel"}),
        ("XWIDTH", "E", {"unit": "pixel"}), ("YWIDTH", "E", {"unit": "pixel"}), ("NSTAR", "J", {}),
        ("NEFF_MEAN", "E", {}), ("CWAVE", "E", {"unit": "um"}), ("CBAND", "E", {"unit": "um"}),
        ("EPSF", "1089D", {"dim": "(33,33)"})]]
    hdu = fits.BinTableHDU.from_columns(cols, name="EPSF")
    hdu.header["OVSMPX"], hdu.header["OVSMPY"] = int(z["OVSMPX"]), int(z["OVSMPY"])
    hdu.header["ORDERING"] = "x-fast then y"
    return hdu



def synthetic_mef(tmp_path, cutout_path):
    """The fixture cutout plus a ZODI plane and a real 3 x 3-zone EPSF table (the cutout has neither)."""
    with fits.open(cutout_path) as h:
        hdus = [fits.PrimaryHDU(header=h[0].header)]
        hdus.append(fits.ImageHDU(h["IMAGE"].data, h["IMAGE"].header, name="IMAGE"))
        hdus.append(fits.CompImageHDU(h["FLAGS"].data, name="FLAGS"))
        hdus.append(fits.ImageHDU(h["VARIANCE"].data, name="VARIANCE"))
        ny, nx = h["IMAGE"].data.shape
        zodi = (0.1 + 1e-4 * np.arange(nx)[None, :] + 2e-4 * np.arange(ny)[:, None]).astype(np.float32)
        z = fits.ImageHDU(zodi, name="ZODI")
        z.header["BUNIT"] = "MJy / sr"
        hdus.append(z)
        hdus.append(epsf_hdu(EPSF_FILE))
        hdus.append(fits.BinTableHDU(h["WCS-WAVE"].data, h["WCS-WAVE"].header, name="WCS-WAVE"))
    p = tmp_path / "synthetic_l2.fits"
    fits.HDUList(hdus).writeto(p)
    return p, zodi


def test_zodi_epsf_default_off_and_missing(cutout_path):
    l2 = open_level2(cutout_path)
    assert l2.zodi is None and l2.epsf is None
    l2 = open_level2(cutout_path, zodi=True, epsf=True)  # the cutout has no ZODI / EPSF HDU
    assert l2.zodi is None and l2.epsf is None


def test_zodi_and_epsf_reads(tmp_path, cutout_path):
    p, zodi = synthetic_mef(tmp_path, cutout_path)
    l2 = open_level2(p, zodi=True, epsf=True)
    assert l2.zodi.dtype == np.float32
    np.testing.assert_array_equal(l2.zodi, zodi)
    sub = open_level2(p, zodi=True, epsf=True, region=(10, 60, 20, 90))
    np.testing.assert_array_equal(sub.zodi, zodi[20:90, 10:60])
    assert sub.epsf.origin == (10, 20)
    assert l2.epsf.psf.shape == (9, 33, 33) and l2.epsf.oversample == (5, 5)
    np.testing.assert_allclose(l2.epsf.psf.sum(axis=(1, 2)), 1.0, atol=1e-6)
    # zone lookup: each zone centre maps to its own row; the region shift is applied
    E = l2.epsf
    for k in range(9):
        assert E.zone(E.xcenter[k], E.ycenter[k]) == k
        assert sub.epsf.zone(E.xcenter[k] - 10, E.ycenter[k] - 20) == k
    assert E.zone(-1e4, -1e4) == int(np.argmin(E.xcenter + E.ycenter))  # off the grid: nearest zone
    # metadata columns come along
    assert set(["NSTAR", "NEFF_MEAN", "CWAVE", "CBAND"]) <= set(E.table.columns.names)
    assert l2.primary_header is not None


def test_epsf_stamps_are_normalised_and_centred():
    from skyshift_core.level2 import EPSF
    E = EPSF.from_hdu(epsf_hdu(EPSF_FILE))
    rng = np.random.default_rng(2)
    k = np.arange(-4, 5)
    for _ in range(20):
        x, y = 1000 + rng.uniform(-0.5, 0.5), 1000 + rng.uniform(-0.5, 0.5)
        st = E.stamp(x, y, half=4)
        ix, iy = round(x), round(y)
        assert st.sum() == pytest.approx(1.0, abs=0.01)  # native pixels hold ~all the flux
        # first moment ~ the source position (pixel sampling of an asymmetric ePSF moves it < 0.1 px)
        assert (st * k[None, :]).sum() / st.sum() + ix == pytest.approx(x, abs=0.1)
        assert (st * k[:, None]).sum() / st.sum() + iy == pytest.approx(y, abs=0.1)
    # the oversampled arrays are centred on index 16 (within half an oversampled pixel)
    i = np.arange(33)
    for p in E.psf:
        assert (p.sum(axis=0) * i).sum() == pytest.approx(16, abs=0.5)
        assert (p.sum(axis=1) * i).sum() == pytest.approx(16, abs=0.5)
    # a whole-pixel shift moves the stamp by one pixel
    a = E.stamp(1000.2, 1000.1, half=3)
    b = E.stamp(1001.2, 1000.1, half=3)
    np.testing.assert_allclose(a, b, atol=1e-12)
    # vectorised stamps agree with single ones
    S, ix, iy = E.stamps([1000.2, 990.7], [1000.1, 1003.4], half=3)
    np.testing.assert_allclose(S[1], E.stamp(990.7, 1003.4, half=3))
    assert (ix == [1000, 991]).all() and (iy == [1000, 1003]).all()


def test_epsf_spline_gradient_and_separable_stamps():
    from scipy import ndimage
    from skyshift_core.level2 import EPSF
    E = EPSF.from_hdu(epsf_hdu(EPSF_FILE))
    rng = np.random.default_rng(3)
    dx, dy = rng.uniform(-3.4, 3.4, 500), rng.uniform(-3.4, 3.4, 500)
    z = rng.integers(0, E.psf.shape[0], 500)
    P, gx, gy = E.evaluate_grad(dx, dy, z)
    h = 1e-5
    np.testing.assert_allclose(gx, (E.evaluate(dx + h, dy, z) - E.evaluate(dx - h, dy, z)) / (2 * h), atol=1e-7)
    np.testing.assert_allclose(gy, (E.evaluate(dx, dy + h, z) - E.evaluate(dx, dy - h, z)) / (2 * h), atol=1e-7)
    # every zone, the first and last included, is its own 2-D spline (scipy's per-zone interpolation)
    for zz in (0, 4, E.psf.shape[0] - 1):
        ref = ndimage.map_coordinates(E.psf[zz], [16 + 5 * dy, 16 + 5 * dx], order=3, mode="grid-constant") * 25
        np.testing.assert_allclose(E.evaluate(dx, dy, np.full(500, zz)), ref, atol=1e-6)
    # separable stamps (fixed pixel grid, moved source) = evaluate on the same pixels
    x, y = 1000 + rng.uniform(-0.5, 0.5, 50), 1000 + rng.uniform(-0.5, 0.5, 50)
    ix, iy = np.rint(x).astype(int) + rng.integers(-1, 2, 50), np.rint(y).astype(int)
    S, Sx, Sy = E.stamps_at(x, y, ix, iy, 3, grad=True)
    k = np.arange(-3, 4)
    ddx = (ix - x)[:, None, None] + k[None, None, :]
    ddy = (iy - y)[:, None, None] + k[None, :, None]
    P, gx, gy = E.evaluate_grad(ddx, ddy, E.zone(x, y)[:, None, None])
    np.testing.assert_allclose(S, P, atol=1e-12)
    np.testing.assert_allclose(Sx, gx, atol=1e-12)
    np.testing.assert_allclose(Sy, gy, atol=1e-12)
