"""Fine detection v2 for the featured-target zones (15 Nov night; contracts/dots.md): sep clean_param 3, the saturation
guard, and the ePSF second pass (psf.residual_companions) whose dots carry sep_flag 0x200 and are measured on the
residual."""

import numpy as np
import pytest

import skyshift_pipeline.config as C
from skyshift_core import detect, open_level2
from skyshift_core.flags import OVERFLOW
from skyshift_pipeline import dots as D
from skyshift_pipeline.psf import residual_companions

from test_dots_extras import epsf, star_field, synthetic_mef  # noqa: F401  (epsf is a fixture)

FINE = C.DetectSettings(1e-4, 3.0, 6.0, True, True)  # + the glow model (16 Nov)
FINE_NO_GLOW = C.DetectSettings(1e-4, 3.0, 6.0, True)


def test_settings_and_folder():
    assert C.zone_detect("t_rdra") == FINE and C.zone_detect("hunt") == C.DetectSettings()
    assert C.DetectSettings().is_sky and not FINE.is_sky
    assert FINE.folder == "dots_deblend1e-4_cp3_g6_psf_glow"
    assert C.dots_dir_for(1e-4) == "dots_deblend1e-4"  # tonight's plain 1e-4 folder keeps its name
    assert C.dots_dir_for(0.005, 3.0) == "dots_cp3"
    assert C.dots_dir_for(0.005, 1.0, 6.0) == "dots_g6"
    assert C.SEP_FLAG_PSF_PASS == 0x200 and C.SEP_FLAG_PSF_PASS < 2 ** 15  # sep_flag is int16 in the dot file


FLUX, SIGMA = 600.0, 1.0  # primary S/N ~200, as a bright star on a real photo


def _pair_image(E, sep_px=2.5, ratio=0.15, seed=1):
    x0, y0 = 32.2, 31.7
    img = star_field(E, [(x0, y0)], FLUX, sky=0.5, sigma=SIGMA, seed=seed)
    P, ix, iy = E.stamps([x0 + sep_px], [y0 + 0.4], half=6)
    img[iy[0] - 6:iy[0] + 7, ix[0] - 6:ix[0] + 7] += ratio * FLUX * P[0]
    return img, (x0, y0), (x0 + sep_px, y0 + 0.4)


def _first_pass(img):
    var = np.full(img.shape, SIGMA ** 2)
    d = detect(img, None, variance=var, thresh=C.DETECT_THRESH, box=64)
    return d[d["snr"] >= C.DOT_MIN_SNR]


def test_second_pass_splits_a_pair_sep_merges(epsf):  # noqa: F811
    E = epsf.shifted(1000, 1000)
    img, prim, comp = _pair_image(E)
    d = _first_pass(img)
    assert not (np.hypot(d["x"] - comp[0], d["y"] - comp[1]) < 1.0).any()  # merged into the bright star
    sub = img - 0.5
    noise, bad = np.full(img.shape, SIGMA), np.zeros(img.shape, bool)
    c = residual_companions(sub, noise, bad, d["x"], d["y"], d["snr"], np.zeros(len(d), bool), E)
    hit = np.hypot(c.dots["x"] - comp[0], c.dots["y"] - comp[1]) < 0.7
    assert hit.sum() == 1 and len(c.dots) == 1
    assert c.dots["flux"][hit][0] == pytest.approx(0.15 * FLUX * 0.968, rel=0.35)  # r = 2 px aperture on the residual
    assert c.info["n_primary"] >= 1
    # a single star leaves nothing behind
    img1 = star_field(E, [(32.2, 31.7)], FLUX, sky=0.5, sigma=SIGMA, seed=2)
    d1 = _first_pass(img1)
    c1 = residual_companions(img1 - 0.5, noise, bad, d1["x"], d1["y"], d1["snr"], np.zeros(len(d1), bool), E)
    assert len(c1.dots) == 0


def test_second_pass_skips_saturated_dots(epsf):  # noqa: F811
    E = epsf.shifted(1000, 1000)
    img, prim, comp = _pair_image(E)
    d = _first_pass(img)
    noise, bad = np.full(img.shape, SIGMA), np.zeros(img.shape, bool)
    c = residual_companions(img - 0.5, noise, bad, d["x"], d["y"], d["snr"], np.ones(len(d), bool), E)
    assert len(c.dots) == 0 and c.info["n_primary"] == 0


def _photo(tmp_path):
    l2 = open_level2(synthetic_mef(tmp_path), variance=True, epsf=True)
    l2.zodi = None
    return l2


def test_guard_drops_new_dots_near_saturated_pixels(tmp_path, monkeypatch):
    l2 = _photo(tmp_path)
    ref = detect(l2.data, l2.flags, variance=l2.variance, thresh=C.DETECT_THRESH)
    ref = ref[ref["snr"] >= C.DOT_MIN_SNR]
    ny, nx = np.shape(l2.data)
    # two empty spots (no dot within 4 px): one gets an OVERFLOW pixel 3 px away, the other stays clean
    spots = [(x, y) for y in range(12, ny - 12, 3) for x in range(12, nx - 12, 3)
             if np.hypot(ref["x"] - x, ref["y"] - y).min() > 4]
    (xa, ya), (xb, yb) = spots[0], next(s for s in spots if np.hypot(s[0] - spots[0][0], s[1] - spots[0][1]) > 20)
    flags = np.asarray(l2.flags).copy()
    flags[ya, xa + 3] |= OVERFLOW
    l2.flags = flags
    real = D.detect

    def fake(*a, **k):
        d = real(*a, **k)
        if k.get("clean_param", 1.0) != 1.0:  # the fine call: two extra dots the sky setting doesn't find
            extra = np.zeros(2, dtype=d.dtype)
            extra["x"], extra["y"] = [xa, xb], [ya, yb]
            extra["flux"], extra["flux_err"], extra["snr"] = 1.0, 0.1, 10.0
            d = np.sort(np.concatenate([d, extra]), order="flux")[::-1]
        return d

    monkeypatch.setattr(D, "detect", fake)
    counts: dict = {}
    df = D.dots_table(l2, "X", 1, settings=C.DetectSettings(1e-4, 3.0, 6.0, False), counts=counts)
    assert counts["n_guard_dropped"] >= 1
    assert not (np.hypot(df.x - xa, df.y - ya) < 0.5).any()
    assert (np.hypot(df.x - xb, df.y - yb) < 0.5).any()
    # without the guard both stay
    df0 = D.dots_table(l2, "X", 1, settings=C.DetectSettings(1e-4, 3.0, 0.0, False))
    assert (np.hypot(df0.x - xa, df0.y - ya) < 0.5).any()


def test_dots_table_second_pass_on_a_real_photo(tmp_path):
    """An ePSF pair (G-like primary + a companion at 15% of its flux, 2.5 px away) injected on an empty spot of the real
    cutout (real noise, background, flags, WCS): the sky setting merges it, fine v2 finds the companion, flags it 0x200,
    measures it on the residual (flux, round shape, ePSF position) and keeps rows brightest first."""
    l2 = _photo(tmp_path)
    base = D.dots_table(l2, "X", 1)
    ny, nx = np.shape(l2.data)
    xy = base[["x", "y"]].to_numpy()
    spot = next((x + 0.3, y - 0.2) for y in range(14, ny - 14) for x in range(14, nx - 14)
                if np.hypot(xy[:, 0] - x, xy[:, 1] - y).min() > 9)
    (px, py), (cx, cy) = spot, (spot[0] + 2.3, spot[1] + 1.0)
    Fp = 300.0  # MJy/sr summed: S/N in the hundreds on this cutout
    data = np.asarray(l2.data, np.float64).copy()
    for (x, y, f) in [(px, py, Fp), (cx, cy, 0.15 * Fp)]:
        P, ix, iy = l2.epsf.stamps([x], [y], half=5)
        data[iy[0] - 5:iy[0] + 6, ix[0] - 5:ix[0] + 6] += f * P[0]
    l2.data = data.astype(np.float32)
    sky = D.dots_table(l2, "X", 1)
    assert (np.hypot(sky.x - px, sky.y - py) < 1.0).any()
    assert not (np.hypot(sky.x - cx, sky.y - cy) < 1.0).any()
    counts: dict = {}
    # the second pass itself (v2 without the glow model: it leaves the companion's aperture alone, flux 1 % apart)
    fine = D.dots_table(l2, "X", 1, settings=FINE_NO_GLOW, counts=counts)
    hit = fine[np.hypot(fine.x - cx, fine.y - cy) < 0.6]
    assert len(hit) == 1
    h = hit.iloc[0]
    assert h.sep_flag & C.SEP_FLAG_PSF_PASS
    assert counts["n_psf_pass"] == int(((fine.sep_flag & C.SEP_FLAG_PSF_PASS) != 0).sum()) >= 1
    conv = D.ujy_per_mjysr(l2.wcs)
    assert h.flux_psf_ujy == pytest.approx(0.15 * Fp * conv, rel=0.25)  # measured on the residual
    assert h.psf_pos_ok and np.hypot(h.x_psf - cx, h.y_psf - cy) < 0.3
    assert h.elong < 2.0  # round on the residual: link can pair it (elong <= 3)
    assert (np.diff(fine.flux_ujy.to_numpy()) <= 0).all()
    assert list(fine.columns) == list(sky.columns)
    assert len(fine) > len(sky)
