"""Extraction v2 (16 Nov; docs/extraction-v2.md): the ePSF-matched-filter pass (sep_flag 0x400), the v2 dot columns
(snr_mf, n_transient, n_masked, partner_flux_ujy, partner_err_ujy), the peaks file, the v2 photo metadata and the
partner-detector pairing of process_photos. All behind config DOTS_V2 (off)."""

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest

import skyshift_pipeline.config as C
from skyshift_core import open_level2
from skyshift_core.flags import OVERFLOW, TRANSIENT
from skyshift_pipeline import dots as D
from skyshift_pipeline import psf as P

from test_dots_extras import epsf, star_field, synthetic_mef  # noqa: F401  (epsf is a fixture)

V2 = C.DOTS_V2_SETTINGS
SIGMA = 1.0


def test_settings_folder_and_switch(monkeypatch):
    assert not C.DOTS_V2  # nothing changes until the rerun
    assert C.zone_detect("hunt") == C.DetectSettings() and C.zone_detect("hunt").folder == "dots"
    assert V2.folder == "dots_deblend1e-4_cp3_g6_psf_glow_v2"
    assert V2.v2 and V2.glow and V2.psf_pass and V2.sat_guard_px == 6.0 and V2.clean_param == 3.0
    assert C.DetectSettings(1e-4, 3.0, 6.0, True, True).folder == "dots_deblend1e-4_cp3_g6_psf_glow"  # unchanged
    monkeypatch.setattr(C, "DOTS_V2", True)
    assert C.zone_detect("hunt") == V2 and C.zone_detect("t_rdra") == V2 and C.zone_detect(None) == V2
    # sep_flag is int16 in the dot file; sep's own bits are 0x1-0x80, the second pass 0x200
    assert C.SEP_FLAG_MF_PASS == 0x400 and C.SEP_FLAG_MF_PASS < 2 ** 15
    assert C.SEP_FLAG_MF_PASS & (C.SEP_FLAG_PSF_PASS | 0xFF) == 0
    assert C.PARTNER_DETECTOR == {1: 4, 4: 1, 2: 5, 5: 2, 3: 6, 6: 3}


def test_matched_filter_snr_and_flux(epsf):  # noqa: F811
    E = epsf.shifted(1000, 1000)
    x0, y0, F = 30.0, 33.0, 12.0  # centred on a pixel: the kernel's own phase
    img = star_field(E, [(x0, y0)], F, sky=0.0, sigma=SIGMA, seed=3)
    noise, bad = np.full(img.shape, SIGMA), np.zeros(img.shape, bool)
    snr, flux, den, phase = P.matched_filter(img, noise, bad, E)
    assert (phase == 0).all()  # one phase (config MF_PHASES 1)
    K = P.mf_kernel(E, int(E.zone([x0], [y0])[0]))
    expect = F * np.sqrt((K ** 2).sum()) / SIGMA  # the optimal S/N of this star
    assert snr[33, 30] == pytest.approx(expect, abs=3.0)
    assert flux[33, 30] == pytest.approx(F, abs=3.0 * SIGMA / np.sqrt((K ** 2).sum()))
    assert np.unravel_index(np.argmax(snr), snr.shape) == (33, 30)
    assert den[33, 30] == pytest.approx((K ** 2).sum() / SIGMA ** 2, rel=1e-6)
    # mf_at = the map's largest value in the 3 x 3 pixels around the position
    v = P.mf_at(img, noise, bad, np.array([30.4, 10.0]), np.array([32.6, 10.0]), E)
    assert v[0] == pytest.approx(snr[32:35, 29:32].max(), rel=1e-9)
    assert v[1] == pytest.approx(snr[9:12, 9:12].max(), rel=1e-9)
    # bad pixels weigh nothing: masking the star's pixels removes it
    bad2 = bad.copy()
    bad2[31:36, 28:33] = True
    s2, _, _, _ = P.matched_filter(np.where(bad2, 0.0, img), noise, bad2, E)
    assert abs(s2[33, 30]) < 3.0 and snr[33, 30] > 8.0  # only the noise of the unmasked kernel edge is left
    # without an ePSF: a Gaussian of the PSF's FWHM
    g = P.mf_kernel(None, -1)
    assert g.shape == (7, 7) and g.sum() == pytest.approx(1.0) and g[3, 3] == g.max()
    # two phases per axis: a star half a pixel right of a pixel centre is found by the +0.5 px kernel
    img2 = star_field(E, [(x0 + 0.5, y0)], F, sky=0.0, sigma=SIGMA, seed=3)
    s1, _, _, _ = P.matched_filter(img2, noise, bad, E, phases=1)
    s4, _, _, ph4 = P.matched_filter(img2, noise, bad, E, phases=2)
    assert P.mf_phases(2) == [(0.0, 0.0), (0.5, 0.0), (0.0, 0.5), (0.5, 0.5)]
    assert s4.max() > s1.max() and ph4[33, 30] == 1
    x, y, ix, iy, s = P.mf_peaks(s4, 0.6 * s4.max(), ph4, 2)
    assert len(x) == 1 and (x[0], y[0]) == (30.5, 33.0)


def test_mf_pass_finds_a_faint_star_the_aperture_cut_drops(epsf):  # noqa: F811
    from skyshift_core import detect
    E = epsf.shifted(1000, 1000)
    rng = np.random.default_rng(11)
    bright = (20.3, 20.6)
    faint = (44.05, 41.95)  # near a pixel centre: the kernel's phase (this D1 ePSF is very sharp)
    img = star_field(E, [bright], 400.0, sky=0.0, sigma=SIGMA, seed=4)
    Pf, ix, iy = E.stamps([faint[0]], [faint[1]], half=6)
    K = P.mf_kernel(E, int(E.zone([faint[0]], [faint[1]])[0]))
    img[iy[0] - 6:iy[0] + 7, ix[0] - 6:ix[0] + 7] += 7.0 / np.sqrt((K ** 2).sum()) * Pf[0]  # MF S/N ~7, aperture ~4
    var = np.full(img.shape, SIGMA ** 2)
    d = detect(img, None, variance=var, thresh=C.DETECT_THRESH, box=64)
    d = d[d["snr"] >= C.DOT_MIN_SNR]
    assert not (np.hypot(d["x"] - faint[0], d["y"] - faint[1]) < 1.5).any()  # the sky setting's cut misses it
    noise, bad = np.full(img.shape, SIGMA), np.zeros(img.shape, bool)
    mp = P.mf_pass(img, noise, bad, E, d["x"], d["y"])
    hit = np.hypot(mp.dots["x"] - faint[0], mp.dots["y"] - faint[1]) < 0.7
    assert hit.sum() == 1 and len(mp.dots) == 1
    assert mp.snr_mf[hit][0] >= C.MF_PASS_SNR and mp.dots["snr"][hit][0] < C.DOT_MIN_SNR
    assert mp.info["n_mf_pass"] == 1
    # the bright star itself is already a dot: not again, not a peak
    assert not (np.hypot(mp.peaks["x"] - bright[0], mp.peaks["y"] - bright[1]) < 1.0).any()
    # pure noise: no dot, and every peak is a 4-5 sigma peak (why 0)
    noise_img = rng.normal(0, SIGMA, (64, 64))
    m0 = P.mf_pass(noise_img, noise, bad, E, np.zeros(0), np.zeros(0))
    assert len(m0.dots) == 0
    assert (m0.peaks["snr_mf"] < C.MF_PASS_SNR).all() and (m0.peaks["why"] == P.PEAK_BELOW).all()
    # the saturation guard: the same star 3 px from an OVERFLOW pixel is a peak (why 1), not a dot
    dsat = np.full(img.shape, np.inf)
    dsat[iy[0] - 3:iy[0] + 4, ix[0] - 3:ix[0] + 4] = 3.0
    mg = P.mf_pass(img, noise, bad, E, d["x"], d["y"], sat_dist=dsat, sat_guard_px=6.0)
    assert len(mg.dots) == 0
    k = np.hypot(mg.peaks["x"] - faint[0], mg.peaks["y"] - faint[1]) < 1.0
    assert k.sum() == 1 and mg.peaks["why"][k][0] == P.PEAK_GUARD and mg.info["n_peaks_guard"] == 1


def test_mf_pass_mirror_test_beside_a_subtracted_star(epsf):  # noqa: F811
    E = epsf.shifted(1000, 1000)
    x0, y0 = 30.2, 31.7
    img = star_field(E, [(x0, y0)], 600.0, sky=0.0, sigma=SIGMA, seed=5)
    # a PSF-mismatch residual: a symmetric ring of light 2 px around the star (both sides), not a companion
    ring = np.zeros_like(img)
    for dx, dy in ((2, 0), (-2, 0)):
        ring[int(round(y0)) + dy, int(round(x0)) + dx] = 8.0
    noise, bad = np.full(img.shape, SIGMA), np.zeros(img.shape, bool)
    mp = P.mf_pass(ring + np.random.default_rng(6).normal(0, SIGMA, img.shape), noise, bad, E,
                   np.array([x0]), np.array([y0]), primary_x=np.array([x0]), primary_y=np.array([y0]))
    assert len(mp.dots) == 0  # both sides equal: the mirror test refuses them
    assert (mp.peaks["why"] == P.PEAK_MIRROR).sum() >= 1
    # rejected peaks (18 Nov): with refused_flags the same peaks are dots carrying the mirror bit, not peaks
    mf = P.mf_pass(ring + np.random.default_rng(6).normal(0, SIGMA, img.shape), noise, bad, E,
                   np.array([x0]), np.array([y0]), primary_x=np.array([x0]), primary_y=np.array([y0]),
                   refused_flags=D.MF_REFUSED_FLAGS)
    assert len(mf.dots) == (mp.peaks["why"] == P.PEAK_MIRROR).sum() == mf.info["n_mf_mirror"]
    assert (mf.dots["sep_flag"] & C.dot_flag_mem(C.DOT_FLAG_MF_MIRROR)).all() and mf.info["n_mf_raw"] == 0
    assert not (mf.peaks["why"] == P.PEAK_MIRROR).any()


def _faint_and_noise(E, snr_mf: float, seed: int = 4):
    """A 64 x 64 noise image (sigma 1) with one point source of matched-filter S/N ~snr_mf near a pixel centre."""
    faint = (32.05, 30.95)
    img = np.random.default_rng(seed).normal(0, SIGMA, (64, 64))
    Pf, ix, iy = E.stamps([faint[0]], [faint[1]], half=6)
    K = P.mf_kernel(E, int(E.zone([faint[0]], [faint[1]])[0]))
    img[iy[0] - 6:iy[0] + 7, ix[0] - 6:ix[0] + 7] += snr_mf / np.sqrt((K ** 2).sum()) * Pf[0]
    return img, faint, (ix[0], iy[0])


def test_mf_pass_refused_peaks_become_flagged_dots(epsf):  # noqa: F811
    """Rejected peaks (18 Nov): a peak the saturation guard or the excluded region refuses is a dot with that bit
    (every test that refuses it), measured like any matched-filter dot; dot_flag's bits are distinct, and their
    in-memory form never touches sep_flag's own 16 bits."""
    E = epsf.shifted(1000, 1000)
    img, faint, (ix, iy) = _faint_and_noise(E, 8.0)
    noise, bad = np.full(img.shape, SIGMA), np.zeros(img.shape, bool)
    none = np.zeros(0)
    plain = P.mf_pass(img, noise, bad, E, none, none)
    assert len(plain.dots) == 1 and plain.dots["sep_flag"][0] >> C.DOT_FLAG_SHIFT == 0
    dsat = np.full(img.shape, np.inf)
    dsat[iy - 3:iy + 4, ix - 3:ix + 4] = 3.0
    excl = np.zeros(img.shape, bool)
    excl[iy - 1:iy + 2, ix - 1:ix + 2] = True
    mg = P.mf_pass(img, noise, bad, E, none, none, sat_dist=dsat, sat_guard_px=6.0, exclude=excl,
                   refused_flags=D.MF_REFUSED_FLAGS)
    assert len(mg.dots) == 1 and not (np.hypot(mg.peaks["x"] - faint[0], mg.peaks["y"] - faint[1]) < 1.0).any()
    f = int(mg.dots["sep_flag"][0])
    assert f >> C.DOT_FLAG_SHIFT == C.DOT_FLAG_MF_GUARD | C.DOT_FLAG_MF_GLOW_PIECE
    # measured as without the guard (same position and aperture flux)
    for c in ("x", "y", "flux", "snr"):
        assert mg.dots[c][0] == pytest.approx(plain.dots[c][0])
    assert mg.info["n_mf_guard"] == mg.info["n_mf_excluded"] == 1 and mg.info["n_mf_raw"] == 0
    # only the guard's why in the dict: the excluded region still keeps it a peak (why 3), the old behaviour
    m3 = P.mf_pass(img, noise, bad, E, none, none, exclude=excl, refused_flags={P.PEAK_GUARD: C.dot_flag_mem(C.DOT_FLAG_MF_GUARD)})
    assert len(m3.dots) == 0 and (m3.peaks["why"] == P.PEAK_EXCLUDED).sum() == 1
    # dot_flag: one distinct bit per meaning, all listed in config.DOT_FLAGS; in memory above sep_flag's 16 bits and
    # inside an int32
    bits = list(C.DOT_FLAGS.values())
    assert len(set(bits)) == len(bits) and all(b & (b - 1) == 0 and b > 0 for b in bits)
    assert sum(bits) < 2 ** (31 - C.DOT_FLAG_SHIFT) and C.SEP_FLAG_MASK == 0xFFFF
    used = 0xFF | C.SEP_FLAG_PSF_PASS | C.SEP_FLAG_MF_PASS | C.SEP_FLAG_CENTROID_FALLBACK
    assert all(C.dot_flag_mem(b) & used == 0 for b in bits)
    assert not any(k.startswith("SEP_FLAG_") and v & ~0xFFFF for k, v in vars(C).items() if isinstance(v, int))


def test_mf_pass_low_snr_dots(epsf):  # noqa: F811
    """Peaks from low_snr to min_snr are dots with low_flag; below low_snr they stay peaks (why 0)."""
    E = epsf.shifted(1000, 1000)
    noise, bad = np.full((64, 64), SIGMA), np.zeros((64, 64), bool)
    none = np.zeros(0)
    for seed in range(50):  # the first noise draw that leaves the source's peak at S/N 4.5-5
        img, faint, _ = _faint_and_noise(E, 4.8, seed=seed)
        m0 = P.mf_pass(img, noise, bad, E, none, none)
        k = np.hypot(m0.peaks["x"] - faint[0], m0.peaks["y"] - faint[1]) < 1.0
        if k.sum() == 1 and 4.5 <= m0.peaks["snr_mf"][k][0] < 5.0:
            break
    assert k.sum() == 1 and 4.5 <= m0.peaks["snr_mf"][k][0] < 5.0  # the 4-5 peak
    low = C.dot_flag_mem(C.DOT_FLAG_MF_LOW_SNR)
    m1 = P.mf_pass(img, noise, bad, E, none, none, low_snr=4.5, low_flag=low)
    hit = np.hypot(m1.dots["x"] - faint[0], m1.dots["y"] - faint[1]) < 1.0
    assert hit.sum() == 1 and m1.dots["sep_flag"][hit][0] & low
    assert (m1.dots["sep_flag"] & low).all() and m1.info["n_mf_low_snr"] == len(m1.dots)
    assert (m1.peaks["snr_mf"] < 4.5).all() and len(m1.dots) + len(m1.peaks["x"]) == len(m0.dots) + len(m0.peaks["x"])
    # low_snr >= min_snr: nothing changes
    m2 = P.mf_pass(img, noise, bad, E, none, none, low_snr=5.0, low_flag=low)
    assert len(m2.dots) == len(m0.dots) and len(m2.peaks["x"]) == len(m0.peaks["x"])


def test_mf_pass_converged_pair_keeps_the_unflagged_dot(epsf):  # noqa: F811
    """Two peaks whose centroids converge on one source: the dot without a rejected-peak bit wins over a brighter
    flagged one."""
    none = np.zeros(0)
    # a source with two 3 x 3 maxima 2 px apart (a brighter left one) whose windowed centroids meet in the middle
    img = np.random.default_rng(3).normal(0, SIGMA, (40, 40)) * 0.01
    img[20, 17:22] += [2.0, 40.0, 25.0, 36.0, 2.0]
    noise, bad = np.full(img.shape, SIGMA), np.zeros(img.shape, bool)
    _, _, _, _, s = P.mf_peaks(P.matched_filter(img, noise, bad, None)[0], 5.0)
    a = P.mf_pass(img, noise, bad, None, none, none)
    assert len(s) == 2 and len(a.dots) == 1  # two peaks, one dot
    excl = np.zeros(img.shape, bool)
    excl[20, 18] = True  # the brighter peak is refused (flagged): the other's dot is kept
    b = P.mf_pass(img, noise, bad, None, none, none, exclude=excl, refused_flags=D.MF_REFUSED_FLAGS)
    assert len(b.dots) == 1 and not b.dots["sep_flag"][0] & C.dot_flag_mem(C.DOT_FLAG_MF_GLOW_PIECE)


def _photo(tmp_path):
    l2 = open_level2(synthetic_mef(tmp_path), variance=True, epsf=True)
    l2.zodi = None
    return l2


def test_dots_table_v2_on_a_real_cutout(tmp_path, monkeypatch):
    """A faint ePSF star injected on a spot of the real cutout with no dot within 6 px (real noise, background, flags,
    WCS): fine detection misses it, v2 finds it (sep_flag 0x400, snr_mf >= 5, ePSF position), and every v2 column is
    there; v1 tables are unchanged."""
    l2 = _photo(tmp_path)
    s_fine = C.DetectSettings(1e-4, 3.0, 6.0, True, True)
    base = D.dots_table(l2, "X", 1, settings=s_fine)
    ny, nx = np.shape(l2.data)
    xy = base[["x", "y"]].to_numpy()
    ctx = D.photo_context(l2, {})
    spots = [(x + 0.1, y - 0.05) for y in range(12, ny - 12) for x in range(12, nx - 12)
             if np.hypot(xy[:, 0] - x, xy[:, 1] - y).min() > 6 and not ctx.bad[y - 2:y + 3, x - 2:x + 3].any()]
    assert spots, (ny, nx, len(base))
    sx, sy = spots[len(spots) // 2]
    Pk, ix, iy = l2.epsf.stamps([sx], [sy], half=5)
    K = P.mf_kernel(l2.epsf, int(l2.epsf.zone([sx], [sy])[0]))
    nz = float(np.median(ctx.noise0[iy[0] - 3:iy[0] + 4, ix[0] - 3:ix[0] + 4]))
    F = 4.5 * nz / np.sqrt((K ** 2).sum())  # + a fainter source already there: matched-filter S/N ~7 in all
    data = np.asarray(l2.data, np.float64).copy()
    data[iy[0] - 5:iy[0] + 6, ix[0] - 5:ix[0] + 6] += F * Pk[0]
    l2.data = data.astype(np.float32)
    fine = D.dots_table(l2, "X", 1, settings=s_fine)
    assert not (np.hypot(fine.x - sx, fine.y - sy) < 1.0).any()
    counts, extras = {}, {}
    v2 = D.dots_table(l2, "X", 1, settings=V2, counts=counts, extras=extras)
    hit = v2[np.hypot(v2.x - sx, v2.y - sy) < 0.8]
    assert len(hit) == 1
    h = hit.iloc[0]
    assert h.sep_flag & C.SEP_FLAG_MF_PASS and not h.sep_flag & C.SEP_FLAG_PSF_PASS
    assert h.snr_mf >= C.MF_PASS_SNR  # sep (thresh 2/px, minarea 3) never made it a candidate
    assert h.psf_pos_ok and np.hypot(h.x_psf - sx, h.y_psf - sy) < 0.5
    # rejected peaks: glow_dots keeps a glow star's matched-filter pieces, flagged; those on a saturated core's own
    # dot also get 0x40 (flag_core_duplicates), none is dropped
    assert counts["n_mf_pass"] == int(((v2.sep_flag & C.SEP_FLAG_MF_PASS) != 0).sum()) >= 1
    assert counts["n_glow_pieces_mf"] >= 1
    assert int(((v2.dot_flag & C.DOT_FLAG_MF_GLOW_PIECE) != 0).sum()) == counts["n_glow_pieces_mf"]
    assert int(((v2.dot_flag & C.DOT_FLAG_SAT_CORE_DUP) != 0).sum()) == counts["n_sat_core_dup_mf_flagged"] >= 1
    with monkeypatch.context() as m:  # without core dots every matched-filter glow piece stays, flagged
        m.setattr(C, "SAT_CORE_DOTS", False)
        c0: dict = {}
        v0 = D.dots_table(l2, "X", 1, settings=V2, counts=c0, extras={})
        assert int(((v0.dot_flag & C.DOT_FLAG_MF_GLOW_PIECE) != 0).sum()) == c0["n_glow_pieces_mf"] >= 1
    mf_bits = (C.DOT_FLAG_MF_GUARD | C.DOT_FLAG_MF_MIRROR | C.DOT_FLAG_MF_GLOW_PIECE | C.DOT_FLAG_MF_LOW_SNR
               | C.DOT_FLAG_SAT_CORE_DUP)
    assert not (((v2.sep_flag & C.SEP_FLAG_MF_PASS) == 0) & ((v2.dot_flag & mf_bits) != 0)).any()  # only on MF dots
    assert v2.dot_flag.dtype == np.int32 and (v2.sep_flag.astype(int) & ~0x0FFF == 0).all()  # sep_flag: old bits only
    assert list(v2.columns) == list(D.DOT_COLUMNS) + list(D.DOT_LIST_COLUMNS) + list(D.DOT_V2_COLUMNS)
    assert list(fine.columns) == list(D.DOT_COLUMNS) + list(D.DOT_LIST_COLUMNS)
    assert v2.partner_flux_ujy.isna().all() and v2.partner_err_ujy.isna().all()
    assert (v2.snr_mf[v2.snr > 20] > v2.snr[v2.snr > 20] * 0.5).all()  # MF S/N of real stars is no smaller
    assert (v2.n_masked >= 0).all() and (v2.n_transient >= 0).all()
    # every first-pass dot of the fine setting is still there (v2 only adds)
    d, _ = D.cKDTree(v2[["x", "y"]].to_numpy()).query(fine[["x", "y"]].to_numpy())
    assert (d < 0.01).mean() > 0.99
    assert "peaks" in extras and "mf_den" in extras and extras["mf_den"].shape == np.shape(l2.data)
    # the Parquet round trip keeps the v2 columns and their types, after the list columns
    meta = D.photo_meta_dict(l2, "X", 1, {"status": "done"}, ctx, v2, None, D.detect_meta(V2.deblend_cont, V2.folder, V2, counts))
    meta.update(D.v2_meta(l2, ctx, extras))
    p = tmp_path / "v2.parquet"
    p.write_bytes(D.parquet_bytes(D.dots_arrow(v2, meta)))
    t = pq.read_table(p)
    assert t.schema.names[-len(D.DOT_V2_COLUMNS):] == list(D.DOT_V2_COLUMNS)
    assert str(t.schema.field("n_transient").type) == "int8" and str(t.schema.field("snr_mf").type) == "float"
    m = D.photo_meta(str(p))
    assert m["extraction"] == 2 and m["detect"]["v2"] and m["detect"]["n_mf_pass"] == counts["n_mf_pass"]
    assert len(m["grids_v2"]["depth5_ujy"]) == C.PHOTO_META_GRID and m["depth5_ujy_median"] > 0
    assert m["aperture_noise"]["sigma_ujy"] > 0 and m["aperture_noise"]["factor"] > 0
    # the sky orientation: a 6.15″ pixel, and one pixel step reproduces the WCS
    J = np.array(m["sky_jacobian_deg_px"])
    assert abs(np.sqrt(abs(np.linalg.det(J))) * 3600 - 6.15) < 0.1
    r0, d0 = D.pixel_to_sky(l2.wcs, np.array([100.0, 101.0]), np.array([80.0, 80.0]))
    e = (r0[1] - r0[0]) * np.cos(np.radians(d0[0]))
    assert abs(e - J[0][0]) * 3600 < 0.05 and abs((d0[1] - d0[0]) - J[1][0]) * 3600 < 0.05
    pk = D.peaks_table(l2, ctx, extras["peaks"], v2)
    assert list(pk.columns) == list(D.PEAK_COLUMNS) and (pk.snr_mf >= C.MF_PEAK_SNR).all()
    assert pk.why.isin([0, 1, 2, 3]).all()
    # no peak sits on a dot
    if len(pk):
        dd, _ = D.cKDTree(v2[["x", "y"]].to_numpy()).query(pk[["x", "y"]].to_numpy())
        assert (dd > C.MF_PASS_DEDUP_PX).all()


def test_v2_columns_transient_and_masked_counts(tmp_path):
    l2 = _photo(tmp_path)
    ctx = D.photo_context(l2, {})
    flags = np.zeros(np.shape(l2.data), np.int32)
    flags[20, 20] = TRANSIENT
    flags[20, 23] = TRANSIENT       # 3 px away: counted
    flags[20, 24] = TRANSIENT       # 4 px: not
    l2.flags = flags
    bad = np.zeros(np.shape(l2.data), bool)
    bad[30, 31] = True              # 1 px: in the aperture
    bad[30, 33] = True              # 3 px: not
    ctx.bad = bad
    v = D.v2_columns(l2, ctx, np.array([20.0, 30.0]), np.array([20.0, 30.0]))
    assert list(v["n_transient"]) == [2, 0]
    assert list(v["n_masked"]) == [0, 1]
    assert np.isnan(v["partner_flux_ujy"]).all()


def test_sat_cores_and_empirical_noise(tmp_path):
    l2 = _photo(tmp_path)
    flags = np.zeros(np.shape(l2.data), np.int32)
    flags[10:13, 40:42] = OVERFLOW
    flags[50, 5] = OVERFLOW
    cores = D.sat_cores(flags)
    assert cores[0] == [40.5, 11.0, 6] and cores[1] == [5.0, 50.0, 1]
    assert D.sat_cores(np.zeros((5, 5), np.int32)) == []
    # Gaussian noise of known sigma: the empirical aperture noise equals the formal one (factor ~1)
    rng = np.random.default_rng(2)
    ctx = SimpleNamespace(sub=rng.normal(0, 1.0, (200, 200)), noise0=np.ones((200, 200)),
                          bad=np.zeros((200, 200), bool), conv=10.0)
    e = D.empirical_noise(ctx)
    assert e["n"] == C.EMPIRICAL_NOISE_N and e["factor"] == pytest.approx(1.0, abs=0.15)
    assert e["formal_median_ujy"] == pytest.approx(10.0 * np.sqrt(np.pi * 4.0), rel=0.05)


def test_partner_fluxes_and_pairing(tmp_path):
    l2 = _photo(tmp_path)
    ctx = D.photo_context(l2, {})
    part = D.partner_image(l2, ctx)
    dots = D.dots_table(l2, "X", 1, settings=V2)
    f, e = D.partner_fluxes(dots, part)
    # the same photo as its own partner: the r = 2 px aperture at each dot's position is (about) its flux_ujy
    ok = np.isfinite(f) & (dots.snr.to_numpy() > 30) & ~dots.psf_pos_ok.to_numpy()
    ok |= np.isfinite(f) & (dots.snr.to_numpy() > 30)
    r = f[ok] / dots.flux_ujy.to_numpy()[ok]
    assert np.median(r) == pytest.approx(1.0, abs=0.1)
    assert (e[np.isfinite(e)] > 0).all()
    # outside the partner photo: NaN
    far = dots.iloc[:1].copy()
    far["ra"], far["ra_psf"] = far["ra"] + 5.0, far["ra_psf"] + 5.0
    f2, e2 = D.partner_fluxes(far, part)
    assert np.isnan(f2).all() and np.isnan(e2).all()
    # fill_partners fills both sides and names the partner
    a = D.Pending("a", dots.copy(), {"detector": 1, "obs_id": "X"}, {}, part)
    b = D.Pending("b", dots.copy(), {"detector": 4, "obs_id": "X"}, {}, part)
    D.fill_partners(a, b)
    assert np.isfinite(a.df.partner_flux_ujy).sum() > 0 and a.meta["partner"]["detector"] == 4
    assert b.meta["partner"]["detector"] == 1


def test_pair_order_and_exposure_groups():
    jobs = [(o, d, "u", 0, 0, {}) for o in ("A", "B") for d in range(1, 7)]
    order = [(j[0], j[1]) for j in D._pair_order(jobs)]
    assert order == [("A", 1), ("A", 4), ("A", 2), ("A", 5), ("A", 3), ("A", 6),
                     ("B", 1), ("B", 4), ("B", 2), ("B", 5), ("B", 3), ("B", 6)]
    rows = [SimpleNamespace(obs_id=o, detector=d) for o in ("A", "B", "C") for d in range(1, 7)]
    g = D.exposure_groups(rows, 4)
    assert [len(x) for x in g] == [6, 6, 6] and all(len({r.obs_id for r in x}) == 1 for x in g)
    assert [len(x) for x in D.exposure_groups(rows, 8)] == [12, 6]


def test_process_photos_v2_pairs_partners(tmp_path, monkeypatch):
    """process_photos with DOTS_V2: D1 is written after D4 with partner columns from it (same cutout loaded twice as
    the two detectors), a peaks file next to each dot file, the v2 metadata in the footer."""
    path = synthetic_mef(tmp_path)
    monkeypatch.setattr(C, "DOTS_V2", True)

    def load(url):
        l2 = open_level2(path, variance=True, epsf=True)
        l2.zodi = None
        l2.detector = int(url[-1])
        return l2

    monkeypatch.setattr(D, "load_photo", load)
    root = tmp_path / "root"
    st = D.process_photos(str(root), [("OBS", 1, "u1", 0, 0), ("OBS", 4, "u4", 0, 0)], prefetch=False)
    assert [s["status"] for s in st] == ["done", "done"] and [s["detector"] for s in st] == [1, 4]
    for det in (1, 4):
        p = root / f"{V2.folder}/OBSD{det}.parquet"
        df = pd.read_parquet(p)
        assert np.isfinite(df.partner_flux_ujy).mean() > 0.5
        m = D.photo_meta(str(p))
        assert m["partner"]["detector"] == C.PARTNER_DETECTOR[det] and m["n_partner"] > 0
        assert (root / D.peaks_rel("OBS", det, V2.folder)).exists()


def test_mf_pass_block_normalise_raises_the_threshold_only_where_the_map_is_wide(epsf, monkeypatch):  # noqa: F811
    """16 Nov, second pass: a patch whose real scatter is 2.5x the noise map (D2's top rows, crosstalk) makes MF-pass
    fakes at a constant threshold; "block" multiplies each peak's threshold by its block's own sigma (from the S/N map's
    lower half), so the patch makes none while a faint star on quiet sky is still found. The grid is in the stats."""
    monkeypatch.setattr(C, "MF_SIGMA_GRID", 4)
    E = epsf.shifted(1000, 1000)
    rng = np.random.default_rng(5)
    img = rng.normal(0, SIGMA, (128, 128))
    img[:, :64] *= 2.5
    star = (96.05, 63.95)
    Pf, ix, iy = E.stamps([star[0]], [star[1]], half=6)
    K = P.mf_kernel(E, int(E.zone([star[0]], [star[1]])[0]))
    img[iy[0] - 6:iy[0] + 7, ix[0] - 6:ix[0] + 7] += 8.0 / np.sqrt((K ** 2).sum()) * Pf[0]
    noise, bad = np.full(img.shape, SIGMA), np.zeros(img.shape, bool)
    off = P.mf_pass(img, noise, bad, E, np.zeros(0), np.zeros(0), normalise="off")
    blk = P.mf_pass(img, noise, bad, E, np.zeros(0), np.zeros(0), normalise="block")
    assert (off.dots["x"] < 60).sum() >= 3  # the wide patch's fakes
    assert (blk.dots["x"] < 64).sum() == 0
    assert (np.hypot(blk.dots["x"] - star[0], blk.dots["y"] - star[1]) < 0.7).sum() == 1
    st = blk.info["stats"]
    g = np.asarray(st["sigma_grid"])
    assert g.shape == (4, 4) and (g[:, :2] > 2.0).all() and (np.abs(g[:, 2:] - 1) < 0.25).all()
    assert {"sigma_lo", "median", "n_neg", "min_snr_used"} <= set(st) and st["n_neg"] >= 1
    # the same peaks either way: the ones under their block's threshold go to the peaks file
    assert len(off.dots) + off.info["n_peaks"] == len(blk.dots) + blk.info["n_peaks"]
    assert P.mf_pass(img, noise, bad, E, np.zeros(0), np.zeros(0), normalise=False).info["n_mf_raw"] == off.info["n_mf_raw"]


def test_mf_pass_block_mode_on_a_photo_with_few_data_pixels(epsf, monkeypatch):  # noqa: F811
    """v3.27: block mode on a photo with too few data pixels for the map's sigma (here 400) raised a KeyError
    (no sigma_grid), so the photo failed; now the grid is NaN, the threshold stays min_snr and the pass runs."""
    monkeypatch.setattr(C, "MF_SIGMA_GRID", 4)
    E = epsf.shifted(1000, 1000)
    img = np.random.default_rng(3).normal(0, SIGMA, (128, 128))
    bad = np.ones(img.shape, bool)
    bad[40:60, 40:60] = False
    img[bad] = 0.0
    out = P.mf_pass(img, np.full(img.shape, SIGMA), bad, E, np.zeros(0), np.zeros(0), normalise="block")
    st = out.info["stats"]
    assert not np.isfinite(st["sigma_lo"]) and np.asarray(st["sigma_grid"]).shape == (4, 4)
    assert st["min_snr_used"] == C.MF_PASS_SNR


def test_v2_dark_holes_per_detector_threshold_and_footer(tmp_path, monkeypatch):
    """process_photos with DOTS_V2: single dark pixels (-40 sigma, unflagged) become bad pixels before detection (no
    dot on them, counted in the footer's dark_holes), the MF threshold is the detector's own (MF_PASS_SNR_DET), and
    the footer has the block grid; with the hole mask off the footer says so."""
    path = synthetic_mef(tmp_path)
    base = open_level2(path, variance=True, epsf=True)
    base.zodi = None
    ctx = D.photo_context(base, {})
    xy = D.dots_table(base, "X", 1, settings=V2)[["x", "y"]].to_numpy()
    ny, nx = np.shape(base.data)
    spots = [(x, y) for y in range(8, ny - 8) for x in range(8, nx - 8)
             if np.hypot(xy[:, 0] - x, xy[:, 1] - y).min() > 4 and not ctx.bad[y - 2:y + 3, x - 2:x + 3].any()]
    assert len(spots) >= 3
    holes = [spots[0], spots[len(spots) // 2], spots[-1]]
    assert min(np.hypot(a[0] - b[0], a[1] - b[1]) for a, b in ((holes[0], holes[1]), (holes[1], holes[2]))) > 4
    nz = float(np.sqrt(np.nanmedian(np.asarray(base.variance, np.float64))))

    def load(url):
        l2 = open_level2(path, variance=True, epsf=True)
        l2.zodi = None
        l2.detector = int(url[-1])
        data = np.asarray(l2.data, np.float64).copy()
        for x, y in holes:
            data[y, x] -= 40 * nz
        l2.data = data.astype(np.float32)
        return l2

    monkeypatch.setattr(C, "DOTS_V2", True)
    monkeypatch.setattr(C, "MF_PASS_SNR_DET", {1: 5.5, 4: 6.5})
    monkeypatch.setattr(D, "load_photo", load)
    root = tmp_path / "root"
    st = D.process_photos(str(root), [("OBS", 1, "u1", 0, 0), ("OBS", 4, "u4", 0, 0)], prefetch=False)
    assert [s["status"] for s in st] == ["done", "done"]
    for det, t in ((1, 5.5), (4, 6.5)):
        p = root / f"{V2.folder}/OBSD{det}.parquet"
        m = D.photo_meta(str(p))
        assert m["dark_holes"]["n_holes"] >= len(holes) and m["dark_holes"]["config"]["sigma"] == C.HOLE_SIGMA
        assert m["detect"]["dark_holes"] is True and m["detect"]["mf_pass_snr"] == t
        assert m["mf"]["min_snr"] == t and m["mf"]["normalise"] == C.MF_PASS_NORMALISE
        g = m["mf"]["sigma_grid"]
        assert len(g) == C.MF_SIGMA_GRID and len(g[0]) == C.MF_SIGMA_GRID
    # the mask itself: those pixels NaN in a copy (the photo untouched), no pixel of a bright star's core
    l2 = load("u1")
    m2, info, pre = D.mask_dark_holes(l2)
    assert pre is None and m2 is not l2 and np.isfinite(np.asarray(l2.data)[[y for _, y in holes], [x for x, _ in holes]]).all()
    new = np.isnan(np.asarray(m2.data)) & ~np.isnan(np.asarray(l2.data))
    assert int(new.sum()) == info["n_px"] and all(new[y, x] for x, y in holes)
    df = pd.read_parquet(root / f"{V2.folder}/OBSD1.parquet")
    yy, xx = np.nonzero(new)
    bright = df[df.snr > 20]
    for x, y in zip(xx, yy):
        assert np.hypot(bright.x - x, bright.y - y).min() > 2.0
    monkeypatch.setattr(C, "DOTS_V2_HOLES", False)
    root2 = tmp_path / "root2"
    D.process_photos(str(root2), [("OBS", 1, "u1", 0, 0)], prefetch=False)
    m2 = D.photo_meta(str(root2 / f"{V2.folder}/OBSD1.parquet"))
    assert m2.get("dark_holes") is None and m2["detect"]["dark_holes"] is False


def test_v2_runs_the_mf_pass_when_the_first_pass_finds_nothing(tmp_path, monkeypatch):
    """A photo with no first-pass dot (a negative image, an empty field) still gets its matched-filter pass."""
    from skyshift_core.detect import DETECTION_DTYPE
    l2 = _photo(tmp_path)
    seen = []
    real = D._mf_pass

    def spy(*a, **k):
        seen.append(1)
        return real(*a, **k)

    monkeypatch.setattr(D, "_mf_pass", spy)
    monkeypatch.setattr(D, "detect", lambda *a, **k: np.zeros(0, dtype=DETECTION_DTYPE))
    counts, extras = {}, {}
    v2 = D.dots_table(l2, "X", 1, settings=V2, counts=counts, extras=extras)
    assert seen and "n_mf_raw" in counts and "peaks" in extras
    assert list(v2.columns) == list(D.DOT_COLUMNS) + list(D.DOT_LIST_COLUMNS) + list(D.DOT_V2_COLUMNS)
    rest = v2[(v2.dot_flag & C.DOT_FLAG_SAT_CORE) == 0]  # the cutout's saturated cores keep their dots (add_core_dots)
    assert len(rest) >= 1 and ((rest.sep_flag & C.SEP_FLAG_MF_PASS) != 0).all()


def test_centroid_fallback_bit_is_cores_and_clear_of_the_pass_bits():
    from skyshift_core.detect import SEP_FLAG_CENTROID_FALLBACK

    assert C.SEP_FLAG_CENTROID_FALLBACK == SEP_FLAG_CENTROID_FALLBACK
    assert not SEP_FLAG_CENTROID_FALLBACK & (C.SEP_FLAG_PSF_PASS | C.SEP_FLAG_MF_PASS | 0xFF)
    bits = [0xFF, C.SEP_FLAG_PSF_PASS, C.SEP_FLAG_MF_PASS, C.SEP_FLAG_CENTROID_FALLBACK]
    assert all(not (a & b) for i, a in enumerate(bits) for b in bits[i + 1:]) and max(bits) < 2 ** 15  # int16 column
    assert SEP_FLAG_CENTROID_FALLBACK <= np.iinfo(np.int16).max  # the dots column is int16


def test_lower_peaks_floor_adds_only_peaks(tmp_path, monkeypatch):
    """config MF_PEAK_SNR (env SKYSHIFT_MF_PEAK_SNR, 27 Nov): a lower floor adds peaks-file rows (why 0, below the old
    floor) and changes no dot, ext or streak row; the footer says which floor wrote the file."""
    l2 = _photo(tmp_path)
    tabs = {}
    for floor in (4.0, 3.0):
        monkeypatch.setattr(C, "MF_PEAK_SNR", floor)
        counts, extras = {}, {}
        dots = D.dots_table(l2, "X", 1, settings=V2, counts=counts, extras=extras)
        ctx = D.photo_context(l2, {})
        pk = D.peaks_table(l2, ctx, extras["peaks"], dots)
        tabs[floor] = (dots, pk, D.v2_meta(l2, ctx, extras), extras)
    d4, p4, m4, e4 = tabs[4.0]
    d3, p3, m3, e3 = tabs[3.0]
    pd.testing.assert_frame_equal(d4.reset_index(drop=True), d3.reset_index(drop=True))
    assert len(p3) > len(p4) and (p4.snr_mf >= 4.0).all() and (p3.snr_mf >= 3.0).all()
    hi = p3[p3.snr_mf >= 4.0].sort_values(["y", "x"]).reset_index(drop=True)
    pd.testing.assert_frame_equal(hi, p4.sort_values(["y", "x"]).reset_index(drop=True))
    assert (p3.loc[p3.snr_mf < 4.0, "why"] == P.PEAK_BELOW).all()
    assert m4["mf"]["peak_snr"] == 4.0 and m3["mf"]["peak_snr"] == 3.0
    for k in set(e4.get("ext", {})) | set(e3.get("ext", {})):
        np.testing.assert_array_equal(np.asarray(e4["ext"][k]), np.asarray(e3["ext"][k]))
    assert ("streaks" in e4) == ("streaks" in e3)


def test_peaks_floor_env():
    """SKYSHIFT_MF_PEAK_SNR sets the floor at import (default 4.0)."""
    import os
    import subprocess
    import sys
    code = "import skyshift_pipeline.config as C; print(C.MF_PEAK_SNR)"
    env = {k: v for k, v in os.environ.items() if k != "SKYSHIFT_MF_PEAK_SNR"}
    for value, want in ((None, 4.0), ("3.0", 3.0)):
        if value is not None:
            env["SKYSHIFT_MF_PEAK_SNR"] = value
        out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True)
        assert float(out.stdout.strip().splitlines()[-1]) == want
