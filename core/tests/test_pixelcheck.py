import numpy as np
import pytest

from skyshift_core import pixelcheck as P
from skyshift_core.flags import HOT, OVERFLOW, TRANSIENT

from conftest import gaussian_image

SIG = 1.22 / 2.3548  # PSF sigma, px


def sky(shape=(33, 400), noise=0.02, seed=1):
    return np.random.default_rng(seed).normal(0.0, noise, shape) + 1.5  # flat zodi-like level, MJy/sr


def test_forced_flux_recovers_a_star_and_measures_the_noise():
    img = sky()
    img += gaussian_image(img.shape, [200.3], [16.4], [2.0], SIG)  # 2 MJy/sr summed
    f = P.forced_flux(img, [200.3], [16.4], conv=1.0)[0]
    assert f.flux_ujy == pytest.approx(2.0, rel=0.06)  # the aperture correction restores the 3% outside r = 2 px
    # empirical noise of an r = 2 px aperture with a median background: ~ sqrt(12.6) x 0.02 = 0.071
    assert 0.05 < f.err_ujy < 0.1
    assert f.snr > 20 and f.ok and not f.masked
    empty = P.forced_flux(img, [80.0], [16.0], conv=1.0)[0]
    assert abs(empty.snr) < 4
    assert abs(f.dx - 0.0) < 0.15 and abs(f.dy) < 0.15  # the light is centred on the place
    off = P.forced_flux(img, [201.6], [16.4], conv=1.0)[0]  # 1.3 px west of the star
    assert off.dx == pytest.approx(-1.3, abs=0.35)


def test_forced_flux_subtracts_the_cutout_zero_point():
    img = sky() + 0.0
    f0 = P.forced_flux(img, [200.0], [16.0], conv=1.0)[0]
    assert abs(f0.zero_ujy) < 0.5 * f0.err_ujy


def test_forced_flux_masked_core_and_same_seed_same_answer():
    img = sky()
    flags = np.zeros(img.shape, np.int64)
    flags[16, 100] = HOT
    a = P.forced_flux(img, [100.0, 250.0], [16.0, 16.0], flags=flags, seed=3)
    b = P.forced_flux(img, [100.0, 250.0], [16.0, 16.0], flags=flags, seed=3)
    assert a[0].masked and not a[0].ok and not a[1].masked
    assert a[1].err_ujy == b[1].err_ujy


def test_moment_shape_star_vs_single_pixel_hit():
    img = sky(noise=0.002)
    img += gaussian_image(img.shape, [100.2], [16.3], [3.0], SIG)
    img[16, 300] += 3.0  # a cosmic-ray hit: all in one pixel
    size, el, th = P.moment_shape(img, [100.2, 300.0], [16.3, 16.0])
    assert 0.55 < size[0] < 0.85  # an undersampled star: ~0.6-0.8 px (the pipeline's 3.7-4.9")
    assert size[1] < 0.35
    assert el[0] < 1.3
    edge = P.moment_shape(img, [2.0], [2.0])[0]
    assert np.isnan(edge[0])


def test_size_ratio_uses_stars_of_similar_snr():
    star_size = np.r_[np.full(10, 0.75), np.full(10, 0.6)]
    star_snr = np.r_[np.full(10, 8.0), np.full(10, 100.0)]
    r, n = P.size_ratio(0.375, 9.0, star_size, star_snr)
    assert n == 10 and r == pytest.approx(0.5)
    r, n = P.size_ratio(0.375, 9.0, star_size[:5], star_snr[:5])
    assert np.isnan(r) and n == 5


def test_combine():
    f, e, n = P.combine([1.0, 3.0, np.nan], [1.0, 1.0, 1.0])
    assert (f, n) == (2.0, 2) and e == pytest.approx(1 / np.sqrt(2))
    assert P.combine([], [])[2] == 0


def test_partner_colour_verdicts():
    # D6 saw 1,800; D3 measured 76 +- 41: not seen; D6/D3 >= 1800 / (76 + 123) = 9.0, inside the known range
    v = P.partner_colour(6, 1800.0, 76.0, 41.0)
    assert v["verdict"] == "normal" and v["bound_kind"] == "lower" and v["bound"] == pytest.approx(9.05, rel=0.01)
    assert v["partner"] == 3
    # D4 alone, D1 deep: D4/D1 >= 2 > 1.1: redder than every known asteroid
    assert P.partner_colour(4, 600.0, 0.0, 100.0)["verdict"] == "red"
    # D2 saw it; D5 measured little and deep: D5/D2 <= 0.15 < 0.21: bluer than every known asteroid
    v = P.partner_colour(2, 2000.0, -20.0, 100.0)
    assert v["verdict"] == "blue" and v["bound_kind"] == "upper" and v["bound"] == pytest.approx(0.15)
    v = P.partner_colour(4, 1000.0, 2000.0, 100.0)
    assert v["verdict"] == "seen" and v["ratio"] == pytest.approx(0.5) and v["in_range"]
    assert P.partner_colour(4, 1000.0, np.nan, 100.0)["verdict"] == "untested"


def test_same_pixel_hot_pixel_rule():
    ra = [10.0, 10.0 + 700 / 3600.0]
    dec = [5.0, 5.0]
    hot = P.same_pixel([4, 4], [500.2, 500.9], [800.0, 800.6], ra, dec)
    assert hot["flaw"] and hot["per_det"][4]["sky_arcsec"] > 600
    moving = P.same_pixel([4, 4], [500.2, 500.9], [800.0, 685.0], ra, dec)
    assert not moving["flaw"]
    still_sky = P.same_pixel([4, 4], [500.2, 500.4], [800.0, 800.1], [10.0, 10.0], [5.0, 5.0])
    assert not still_sky["flaw"]  # the sky didn't move: no pixel test
    assert not P.same_pixel([1, 4], [1, 1], [1, 1], ra, dec)["flaw"]  # one dot per detector: nothing to compare


def test_one_photo_verdicts():
    n = 8
    nothing = np.zeros(n)
    err = np.full(n, 100.0)
    # a faint fixed source: 400 +- 35 in the others against 900 circled
    assert P.one_photo(900.0, 150.0, np.full(n, 400.0), err)["verdict"] == "steady"
    assert P.one_photo(900.0, 150.0, nothing, err, size_ratio=0.5)["verdict"] == "cosmic_ray"
    # NASA's TRANSIENT flag beside it is reported but decides nothing (it sits beside real asteroids' dots too)
    v = P.one_photo(900.0, 150.0, nothing, err, size_ratio=1.0, transient_px=2)
    assert v["verdict"] == "flash" and v["transient_px"] == 2
    assert P.one_photo(900.0, 150.0, nothing, err, size_ratio=1.0, moved=True)["verdict"] == "moved"
    v = P.one_photo(900.0, 150.0, nothing, err, size_ratio=1.0)
    assert v["verdict"] == "flash" and v["limit_ratio"] == pytest.approx(3 * 100 / np.sqrt(8) / 900)
    assert P.one_photo(900.0, 150.0, nothing[:1], err[:1])["verdict"] == "too_few"
    assert P.one_photo(300.0, 60.0, nothing, np.full(n, 400.0))["verdict"] == "unclear"
    # the steady light is centred 1.5 px east in every other photo: a neighbour spilling in, not a source at the place
    off = np.tile([1.5, 0.1], (n, 1))
    v = P.one_photo(900.0, 150.0, np.full(n, 400.0), err, other_offset=off)
    assert v["verdict"] == "neighbour" and v["offset_px"] == pytest.approx(np.hypot(1.5, 0.1))
    noisy = np.random.default_rng(2).normal(0, 0.8, (n, 2))  # a source at the place: offsets scatter around 0
    assert P.one_photo(900.0, 150.0, np.full(n, 400.0), err, other_offset=noisy)["verdict"] == "steady"
    # the steady case: 900 ± 150 now against 400 ± 35: 2.2× but only 3.2σ apart: steady, with the numbers
    v = P.one_photo(900.0, 150.0, np.full(n, 400.0), err)
    assert v["diff_ujy"] == pytest.approx(500.0) and v["diff_snr"] == pytest.approx(500 / np.hypot(150, 100 / np.sqrt(8)))
    # an asteroid passing a star: 3000 ± 100 now against 400 ± 35 at other times: 7.5×, 24σ apart: brighter, also
    # where the star's light is centred beside the place (Erato: a star 8.8″ away)
    v = P.one_photo(3000.0, 100.0, np.full(n, 400.0), err)
    assert v["verdict"] == "brighter" and v["diff_snr"] > 20
    assert P.one_photo(3000.0, 100.0, np.full(n, 400.0), err, other_offset=off)["verdict"] == "brighter"
    # 1.4× the source, though 8σ apart: not brighter (the source alone, a little variable or mismeasured)
    assert P.one_photo(2800.0, 100.0, np.full(n, 2000.0), err)["verdict"] == "steady"
    assert np.isnan(P.one_photo(900.0, 150.0, nothing[:0], err[:0])["diff_snr"])


def test_path_summary():
    own = 800.0
    found = P.path_summary(own, [700.0, 650.0, 20.0], [150.0, 150.0, 150.0])
    assert found["verdict"] == "found" and found["n_found"] == 2
    absent = P.path_summary(own, [10.0, -30.0, 5.0], [100.0, 100.0, 100.0])
    assert absent["verdict"] == "absent" and absent["n_should"] == 3
    # a search whose chance level is 5 sigma doesn't count a 4 sigma peak
    assert P.path_summary(own, [600.0, 600.0], [150.0, 150.0], chance=[5.0, 5.0])["verdict"] != "found"


def test_persistence_and_flag_helpers():
    p = P.persistence(500.0, [20000.0, 100.0], [False, False])
    assert p["bright"] and not p["saturated"]
    assert P.persistence(500.0, [100.0], [True])["saturated"]
    assert not P.persistence(500.0, [1000.0], [False])["bright"]
    f = np.zeros((20, 20), np.int64)
    f[10, 12] = TRANSIENT
    f[3, 3] = OVERFLOW
    assert P.transient_pixels(f, 10.0, 10.0) == 1
    assert P.transient_pixels(f, 10.0, 16.0) == 0
    assert P.saturated_near(f, 3.5, 3.5) and not P.saturated_near(f, 10.0, 10.0)
