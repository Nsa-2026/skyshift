import numpy as np
import pytest

from skyshift_core import (
    GAIA_EPOCH_MJD,
    measure_shift,
    parallax_offset_arcsec,
    pixel_to_sky,
    position_angle_deg,
    propagate,
    select_reference_stars,
    sky_to_pixel,
    tangent_deproject,
    tangent_offset_arcsec,
)

from conftest import PIXEL_ARCSEC, gaussian_image, tan_wcs

# Gaia DR3 4472832130942575872 (Barnard's Star), epoch J2016.0
BARNARD = dict(ra=269.44850252543836, dec=4.739420051112412, pmra=-801.551, pmdec=10362.394, parallax=546.976)


def test_barnard_moves_north_10_39_per_year():
    b = BARNARD
    ra1, dec1 = propagate(b["ra"], b["dec"], b["pmra"], b["pmdec"], GAIA_EPOCH_MJD + 365.25)
    de, dn = tangent_offset_arcsec(b["ra"], b["dec"], ra1, dec1)
    assert np.hypot(de, dn) == pytest.approx(10.393, abs=0.002)
    assert dn == pytest.approx(10.362, abs=0.002) and de == pytest.approx(-0.8016, abs=0.001)
    assert position_angle_deg(de, dn) == pytest.approx(355.58, abs=0.02)  # nearly due north
    assert dec1 > b["dec"]


def test_propagate_zero_and_nan_motion():
    ra, dec = propagate(10.0, -30.0, np.nan, 0.0, 61000.0)
    assert ra == pytest.approx(10.0) and dec == pytest.approx(-30.0)


def test_tangent_roundtrip_near_pole():
    ra, dec = tangent_deproject(123.0, 89.99, 15.0, -7.0)
    de, dn = tangent_offset_arcsec(123.0, 89.99, ra, dec)
    assert de == pytest.approx(15.0, abs=1e-6) and dn == pytest.approx(-7.0, abs=1e-6)


def test_parallax_offset_bounded_and_annual():
    b = BARNARD
    t = GAIA_EPOCH_MJD + np.linspace(0, 365.25, 25)
    de, dn = parallax_offset_arcsec(b["ra"], b["dec"], b["parallax"], t)
    amp = np.hypot(de, dn)
    assert amp.max() <= 0.548 * 1.02 and amp.max() > 0.4
    assert de[0] == pytest.approx(de[-1], abs=0.01)  # one-year period


def test_pixel_sky_roundtrip(cutout_path):
    from skyshift_core import open_level2

    w = open_level2(cutout_path).wcs
    ra, dec = pixel_to_sky(w, [10.25, 80.5], [33.0, 70.75])
    x, y = sky_to_pixel(w, ra, dec)
    np.testing.assert_allclose(x, [10.25, 80.5], atol=1e-6)
    np.testing.assert_allclose(y, [33.0, 70.75], atol=1e-6)


def test_select_reference_stars():
    sel = select_reference_stars([12, 14, 16, 18, 15], [1, 5, 30, 1, np.nan], [1, 5, 0, 1, 0])
    assert list(sel) == [False, True, False, False, False]


def _scene(ra0, dec0, shape, star_radec, fluxes, offset_px=(0.0, 0.0), noise=0.02, seed=0):
    """Image of stars at sky positions, with a uniform pixel offset applied (WCS error)."""
    w = tan_wcs(ra0, dec0, shape)
    x, y = sky_to_pixel(w, *star_radec)
    img = gaussian_image(shape, x + offset_px[0], y + offset_px[1], fluxes, 1.22 / 2.3548)
    img += np.random.default_rng(seed).normal(0, noise, shape)
    return img, w


def test_measure_shift_recovers_synthetic_shift():
    ra0, dec0 = 269.45, 4.74
    shape = (121, 121)
    rng = np.random.default_rng(7)
    n_ref = 15
    w0 = tan_wcs(ra0, dec0, shape)
    rx, ry = rng.uniform(10, 110, n_ref), rng.uniform(10, 110, n_ref)
    keep = np.hypot(rx - 60, ry - 60) > 6
    rx, ry = rx[keep], ry[keep]
    rra, rdec = pixel_to_sky(w0, rx, ry)

    # target moves 5.2" north and 0.4" west between the visits (Barnard-like, 6 months)
    t_ra1, t_dec1 = pixel_to_sky(w0, 60.2, 59.7)
    t_ra2, t_dec2 = tangent_deproject(t_ra1, t_dec1, -0.4, 5.2)
    f = np.r_[300.0, rng.uniform(50, 150, rra.size)]

    img1, w1 = _scene(ra0, dec0, shape, (np.r_[t_ra1, rra], np.r_[t_dec1, rdec]), f, seed=1)
    # second visit: whole frame offset by (0.3, -0.2) px, i.e. a WCS zero-point error
    img2, w2 = _scene(ra0, dec0, shape, (np.r_[t_ra2, rra], np.r_[t_dec2, rdec]), f, offset_px=(0.3, -0.2), seed=2)

    mjd1, mjd2 = 60950.0, 60950.0 + 182.6
    res = measure_shift(img1, img2, w1, w2, mjd1, mjd2, float(t_ra1), float(t_dec1),
                        ref_ra=rra, ref_dec=rdec, search_r=2.0)
    assert res.n_ref_stars >= 10
    assert res.d_north_arcsec == pytest.approx(5.2, abs=0.15)
    assert res.d_east_arcsec == pytest.approx(-0.4, abs=0.15)
    assert res.shift_arcsec == pytest.approx(np.hypot(5.2, 0.4), abs=0.15)
    assert res.pa_deg == pytest.approx(position_angle_deg(-0.4, 5.2), abs=2.0)
    assert res.dt_yr == pytest.approx(0.49993, abs=1e-4)
    assert res.rate_arcsec_yr == pytest.approx(res.shift_arcsec / res.dt_yr)
    assert res.sigma_arcsec < 0.2
    # the frame offset was measured from the refs (0.3 px east-left = -RA... check magnitude)
    assert np.hypot(*res.frame_offset_arcsec) == pytest.approx(np.hypot(0.3, 0.2) * PIXEL_ARCSEC, abs=0.1)
    # without the correction the raw shift carries the offset
    assert abs(res.raw_d_north_arcsec - 5.2) > 0.5
    m = res.to_measure()
    assert set(m) == {"shift_arcsec", "pa_deg", "dt_yr", "rate_arcsec_yr", "sigma_arcsec", "n_ref_stars", "predicted_arcsec"}
    assert m["predicted_arcsec"] is None


def test_measure_shift_with_gaia_prediction():
    """A Barnard-like star given with its Gaia motion: predicted and measured shifts agree."""
    b = BARNARD
    mjd1, mjd2 = 60950.0, 60950.0 + 182.6
    p1 = propagate(b["ra"], b["dec"], b["pmra"], b["pmdec"], mjd1)
    p2 = propagate(b["ra"], b["dec"], b["pmra"], b["pmdec"], mjd2)
    shape = (101, 101)
    w0 = tan_wcs(float(p1[0]), float(p1[1]), shape)
    rng = np.random.default_rng(11)
    rx, ry = rng.uniform(10, 90, 14), rng.uniform(10, 90, 14)
    keep = np.hypot(rx - 50, ry - 50) > 6
    rra, rdec = pixel_to_sky(w0, rx[keep], ry[keep])
    f = np.r_[400.0, np.full(rra.size, 100.0)]
    img1, w1 = _scene(float(p1[0]), float(p1[1]), shape, (np.r_[p1[0], rra], np.r_[p1[1], rdec]), f, seed=3)
    img2, w2 = _scene(float(p1[0]), float(p1[1]), shape, (np.r_[p2[0], rra], np.r_[p2[1], rdec]), f, seed=4)
    res = measure_shift(img1, img2, w1, w2, mjd1, mjd2, b["ra"], b["dec"],
                        target_pmra=b["pmra"], target_pmdec=b["pmdec"], ref_ra=rra, ref_dec=rdec)
    assert res.predicted_arcsec == pytest.approx(10.393 * 182.6 / 365.25, abs=0.01)
    assert res.shift_arcsec == pytest.approx(res.predicted_arcsec, abs=0.15)
    assert res.rate_arcsec_yr == pytest.approx(10.39, rel=0.03)


def test_fixture_matches_gaia(cutout_path):
    """Real data: WCS + fitted centroids land on Gaia DR3 positions moved to MJD-AVG."""
    import json

    from conftest import FIXTURES
    from skyshift_core import centroid, detect, open_level2

    gaia = json.loads((FIXTURES / "cutout_2025W18_1B_0074_2D1.gaia.json").read_text())["gaia"]
    g = {k: np.array([np.nan if s[k] is None else s[k] for s in gaia], float) for k in ("ra", "dec", "pmra", "pmdec")}
    l2 = open_level2(cutout_path, variance=True)
    gra, gdec = propagate(g["ra"], g["dec"], g["pmra"], g["pmdec"], l2.mjd_avg)
    det = detect(l2.data, l2.flags, variance=l2.variance)
    det = det[det["snr"] > 50]
    offs = []
    for s in det:
        c = centroid(l2.data, s["x"], s["y"], flags=l2.flags, method="fit")
        assert c is not None
        ra, dec = pixel_to_sky(l2.wcs, *c)
        de, dn = tangent_offset_arcsec(ra, dec, gra, gdec)
        i = int(np.argmin(np.hypot(de, dn)))
        if np.hypot(de[i], dn[i]) < 3.0:
            offs.append((de[i], dn[i]))
    offs = np.array(offs)
    assert len(offs) >= 0.9 * len(det) and len(offs) >= 30
    assert np.median(np.hypot(*offs.T)) < 0.5  # arcsec; measured 0.26" (0.04 px)
    assert np.all(np.abs(np.median(offs, axis=0)) < 0.3)
