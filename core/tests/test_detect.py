import numpy as np
import pytest

from skyshift_core import centroid, detect, open_level2
from skyshift_core.detect import detection_snr, no_data_near
from skyshift_core.flags import OVERFLOW, SOURCE

from conftest import gaussian_image

PSF_SIGMA = 1.22 / 2.3548  # SPHEREx FWHM 7.53" = 1.22 px


def test_detect_finds_fixture_stars(cutout_path, cutout_stars):
    l2 = open_level2(cutout_path, variance=True)
    det = detect(l2.data, l2.flags, variance=l2.variance)
    assert len(det) >= 20
    ref = [s for s in cutout_stars["stars"] if 8 <= s["x"] <= 92 and 8 <= s["y"] <= 92]
    assert len(ref) >= 10
    offsets = []
    for s in ref:
        d = np.hypot(det["x"] - s["x"], det["y"] - s["y"])
        i = int(np.argmin(d))
        assert d[i] < 0.3, s
        assert det["snr"][i] > 30
        assert det["flux"][i] == pytest.approx(s["flux_mjysr"], rel=0.15)
        offsets.append(d[i])
    # cutout and full-frame centroids agree well (different backgrounds only)
    assert np.median(offsets) < 0.1


def test_detect_clean_param_default_and_passed_to_sep(cutout_path, monkeypatch):
    """clean_param 1.0 is sep's default (the sky run is unchanged); a larger value keeps every dot of the default
    and may add faint ones that sep's cleaning had merged into a brighter source's wings."""
    l2 = open_level2(cutout_path, variance=True)
    a = detect(l2.data, l2.flags, variance=l2.variance, thresh=2.0)
    b = detect(l2.data, l2.flags, variance=l2.variance, thresh=2.0, clean_param=1.0)
    assert np.array_equal(a, b)
    c = detect(l2.data, l2.flags, variance=l2.variance, thresh=2.0, clean_param=3.0)
    assert len(c) >= len(a)
    for x, y in zip(a["x"], a["y"]):
        assert np.hypot(c["x"] - x, c["y"] - y).min() < 0.5
    import sep

    seen = []
    real = sep.extract

    def spy(*args, **kw):
        seen.append(kw.get("clean_param"))
        return real(*args, **kw)

    monkeypatch.setattr(sep, "extract", spy)
    detect(l2.data, l2.flags, variance=l2.variance, thresh=2.0, clean_param=3.0)
    assert seen == [3.0]


def test_detect_masks_bad_pixels():
    img = gaussian_image((64, 64), [20.3, 40.7], [30.2, 25.6], [500, 500], 1.0)
    rng = np.random.default_rng(3)
    img += rng.normal(0, 0.5, img.shape)
    flags = np.full(img.shape, SOURCE, dtype=np.int32)  # SOURCE everywhere must not hide anything
    img[50, 50] = 1e4  # cosmic ray, flagged TRANSIENT
    flags[50, 50] |= 1
    det = detect(img, flags, thresh=5)
    assert len(det) == 2
    assert not np.any(np.hypot(det["x"] - 50, det["y"] - 50) < 2)


@pytest.mark.parametrize("sigma", [PSF_SIGMA, 1.0])
def test_centroid_precision_synthetic(sigma):
    rng = np.random.default_rng(42)
    errs = []
    for _ in range(40):
        x0, y0 = 30 + rng.uniform(-0.5, 0.5), 30 + rng.uniform(-0.5, 0.5)
        img = gaussian_image((61, 61), x0, y0, 2000.0, sigma) + rng.normal(0, 0.05, (61, 61))
        c = centroid(img, round(x0) + 0.4, round(y0) - 0.4)
        assert c is not None
        errs.append(np.hypot(c[0] - x0, c[1] - y0))
        det = detect(img, thresh=10)
        assert len(det) == 1
        errs.append(np.hypot(det["x"][0] - x0, det["y"][0] - y0))
    assert max(errs) < 0.05


def test_centroid_on_wings_when_core_saturated():
    x0, y0 = 30.3, 29.6
    img = gaussian_image((61, 61), x0, y0, 5000.0, 1.0)
    flags = np.full(img.shape, SOURCE, dtype=np.int32)
    flags[29:31, 30:31] |= OVERFLOW  # core pixels saturated
    img[flags & OVERFLOW != 0] = 0.0  # garbage in the core
    c = centroid(img, 30, 30, flags=flags)
    assert c is not None
    assert np.hypot(c[0] - x0, c[1] - y0) < 0.25


def test_detection_snr_star_versus_noise():
    rng = np.random.default_rng(3)
    img = gaussian_image((81, 81), [40.0, 20.0], [40.0, 60.0], [1.0, 0.3], PSF_SIGMA) + rng.normal(0, 0.02, (81, 81))
    assert detection_snr(img, 40.0, 40.0) > 15
    assert 5 < detection_snr(img, 20.0, 60.0) < 15
    noise = [detection_snr(img, x, 15.0) for x in range(10, 75, 8)]  # empty sky
    assert max(noise) < 4.0


def test_detection_snr_reads_a_saturated_core_on_its_wings():
    rng = np.random.default_rng(4)
    img = gaussian_image((81, 81), [40.0], [40.0], [50.0], 1.5) + rng.normal(0, 0.02, (81, 81))
    img[38:43, 38:43] = np.nan  # the core masked in every exposure
    assert detection_snr(img, 40.0, 40.0) > 20


def test_no_data_near_edge_but_not_a_saturated_hole():
    img = np.ones((81, 81))
    img[:, 60:] = np.nan  # past the detector's edge
    img[39:42, 19:22] = np.nan  # a saturated core
    assert no_data_near(img, 57.0, 40.0, 4.0)
    assert not no_data_near(img, 50.0, 40.0, 4.0)
    assert not no_data_near(img, 20.0, 40.0, 4.0)
    assert no_data_near(img, 100.0, 40.0, 4.0)  # off the frame
    big = np.ones((81, 81))
    big[20:40, 20:40] = np.nan  # 400 px masked inside the frame: missing sky too
    assert no_data_near(big, 42.0, 30.0, 4.0)
