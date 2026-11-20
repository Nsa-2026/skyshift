"""The glow model in the pipeline (16 Nov; core.glow, contracts/dots.md "Glow model"): the dots step detects on IMAGE
minus the bright stars' glow with noise x k, drops the star's pieces and measures its own dot on IMAGE; the stack
scales its S/N maps by the local scatter around bright stars and replaces a saturated star's hole rim by the star."""

import numpy as np
import pandas as pd
import pytest
from scipy import ndimage

import skyshift_pipeline.config as C
from skyshift_core.flags import OVERFLOW, SUR_ERROR
from skyshift_pipeline import dots as D
from skyshift_pipeline import stack as S

from test_fine_v2 import FINE, FINE_NO_GLOW, _photo
from test_dots_extras import synthetic_mef  # noqa: F401

X0, Y0, R_CORE = 50.3, 50.6, 3.6


def _with_saturated_star(l2, lumps=0.35, amp=25.0, seed=3):
    """A saturated star at (X0, Y0) on the real cutout: masked core (SUR_ERROR, OVERFLOW at the centre) and a lopsided
    power-law glow (~0.4 MJy/sr at 10 px, 15x the sky noise, as Barnard's Star in D1) broken into PSF-sized lumps."""
    ny, nx = np.shape(l2.data)
    yy, xx = np.mgrid[:ny, :nx]
    r, th = np.hypot(xx - X0, yy - Y0), np.arctan2(yy - Y0, xx - X0)
    s = ndimage.gaussian_filter(np.random.default_rng(seed).normal(0, 1, (ny, nx)), 1.0)
    glow = amp / (1 + (r / 2) ** 2) ** 1.25 * (1 + 0.3 * np.cos(th - 0.7)) * (1 + lumps * s / s.std())
    glow[r < R_CORE] = 0
    data = np.asarray(l2.data, np.float64) + glow
    data[r < R_CORE] = 3000.0
    l2.data = data.astype(np.float32)
    l2.variance = (np.asarray(l2.variance, np.float64) + 5e-4 * glow).astype(np.float32)
    fl = np.asarray(l2.flags).copy()
    fl[r < R_CORE] |= SUR_ERROR
    fl[r < 1.2] |= OVERFLOW
    l2.flags = fl
    return r


def _near(df, xy, tol):
    if len(df) == 0 or len(xy) == 0:
        return np.zeros(len(df), bool)
    d = np.hypot(df.x.to_numpy()[:, None] - xy[None, :, 0], df.y.to_numpy()[:, None] - xy[None, :, 1])
    return d.min(axis=1) <= tol


def test_dots_on_a_saturated_stars_glow(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    base = D.dots_table(_photo(tmp_path / "a"), "X", 1, settings=FINE_NO_GLOW)  # the real sources, no star
    real = base[base.snr >= 8][["x", "y"]].to_numpy()
    l2 = _photo(tmp_path / "b")
    _with_saturated_star(l2, lumps=0.6, amp=100.0)
    old = D.dots_table(l2, "X", 1, settings=FINE_NO_GLOW)
    counts: dict = {}
    new = D.dots_table(l2, "X", 1, settings=FINE, counts=counts)
    assert counts["n_glow_stars"] >= 1 and counts["n_glow_saturated"] >= 1 and counts["n_glow_extended"] >= 1

    def ring(df, a, b):
        rr = np.hypot(df.x - X0, df.y - Y0).to_numpy()
        return df[(rr >= a) & (rr < b)]

    # dots in the glow with no dot of the star-free photo within 1.5 px (a crowded real cutout: 245 dots in 101 px)
    every = base[["x", "y"]].to_numpy()
    fake_old = int((~_near(ring(old, 6, 35), every, 1.5)).sum())
    fake_new = int((~_near(ring(new, 6, 35), every, 1.5)).sum())
    assert fake_old >= 6 and fake_new <= fake_old - 3
    # the real sources inside the glow (S/N >= 8 without it) stay
    inside = real[(np.hypot(real[:, 0] - X0, real[:, 1] - Y0) >= 12) & (np.hypot(real[:, 0] - X0, real[:, 1] - Y0) < 35)]
    rec_old = _near(pd.DataFrame(inside, columns=["x", "y"]), old[["x", "y"]].to_numpy(), 1.0).sum()
    rec_new = _near(pd.DataFrame(inside, columns=["x", "y"]), new[["x", "y"]].to_numpy(), 1.0).sum()
    assert len(inside) >= 50 and rec_new >= rec_old - 1
    # the star itself: one dot (without the model its masked core's rim gives several)
    assert len(ring(old, 0, 8)) >= 2 and len(ring(new, 0, 8)) == 1
    assert len(ring(new, 0, R_CORE + 1.5)) == 1
    meta = D.detect_meta(settings=FINE, counts=counts)
    assert meta["glow"] is True and meta["glow_config"]["r_max_px"] == C.GLOW_R_MAX_PX and meta["n_glow_pieces"] >= 0
    assert FINE.folder == "dots_deblend1e-4_cp3_g6_psf_glow"


def test_no_bright_star_means_the_same_dots(tmp_path):
    """A photo with nothing to model gives the v2 dots exactly (the glow step changes nothing there)."""
    l2 = _photo(tmp_path)
    old = D.dots_table(l2, "X", 1, settings=FINE_NO_GLOW)
    counts: dict = {}
    new = D.dots_table(l2, "X", 1, settings=FINE, counts=counts)
    if counts.get("n_glow_stars", 0) == 0:
        pd.testing.assert_frame_equal(old.reset_index(drop=True), new.reset_index(drop=True))
    else:  # the cutout's brightest star is modelled: only dots near it may change
        assert abs(len(new) - len(old)) <= 3


def _glow_map(n=240, hole=7.0, seed=0, lumps=4.0):
    """An S/N map (2" grid) around a saturated star: a hole (not searched) of radius `hole` px, a bright rim, glow
    leftovers that are coherent over the photos (smooth lumps of S/N ~lumps out to ~60 px) and real sources."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[:n, :n]
    c = n / 2 + 0.3
    r = np.hypot(xx - c, yy - c)
    s = ndimage.gaussian_filter(rng.normal(0, 1, (n, n)), 1.5)
    snr = rng.normal(0, 1, (n, n)) + lumps * s / s.std() * np.exp(-r / 30.0)
    snr += 300.0 * np.exp(-((r - hole - 1.5) / 1.5) ** 2) * (1 + 0.3 * np.cos(3 * np.arctan2(yy - c, xx - c)))
    a = np.linspace(0, 2 * np.pi, 8, endpoint=False)
    src = np.c_[c + 45 * np.cos(a), c + 45 * np.sin(a)]
    for x, y in src:  # real sources at 90": S/N 40
        snr += 40.0 * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * 1.6 ** 2))
    ok = r > hole
    m = S.Map("v1_d1", np.where(ok, snr, 0.0), np.full((n, n), np.nan), ok, 1.0)
    return m, c, src


def test_stack_local_significance_and_the_hole_rim():
    m, c, src = _glow_map()
    raw = S.peaks(m)
    m2, centres = S.local_significance(m, gaia_xy=(np.array([c]), np.array([c])))
    assert len(centres[0]) == 1 and abs(centres[0][0] - c) < 1e-6 and centres[2][0] >= 6  # the hole, at the Gaia star
    assert m2.k is not None and m2.k.max() > 1.5 and np.all(m2.k[~m.ok | (np.hypot(*np.mgrid[:240, :240] - c) > 100)] == 1)
    pk = S.rim_peaks(S.peaks(m2), m2, centres)
    rr = np.hypot(pk.x - c, pk.y - c).to_numpy()
    rraw = np.hypot(raw.x - c, raw.y - c).to_numpy()
    real = lambda p: _near(p, src, 2.0)  # noqa: E731
    fake_raw = int(((rraw > 15) & (rraw < 80) & ~real(raw)).sum())
    fake_new = int(((rr > 15) & (rr < 80) & ~real(pk)).sum())
    assert fake_raw >= 10 and fake_new <= fake_raw // 5
    assert real(pk).sum() >= 7                                     # the S/N 40 sources at 90" stay
    centre = pk[rr < 1e-6]
    assert len(centre) == 1 and not ((rr > 1e-6) & (rr <= centres[2][0] + C.STACK_GLOW_RIM_ARCSEC / 2)).any()


def test_stack_without_bright_stars_is_unchanged():
    rng = np.random.default_rng(2)
    m = S.Map("v1_d1", rng.normal(0, 1, (120, 120)), np.full((120, 120), np.nan), np.ones((120, 120), bool), 1.0)
    m2, centres = S.local_significance(m)
    assert m2 is m and len(centres[0]) == 0
    pk = S.peaks(m)
    pd.testing.assert_frame_equal(S.rim_peaks(pk, m, centres), pk)


def _core_rows(df):
    if "dot_flag" not in df:  # not a v2 table: no core dots
        return df.iloc[:0]
    return df[(df.dot_flag.astype(int) & C.DOT_FLAG_SAT_CORE) != 0]


def _sat_star(l2, x0, y0, amp, r_mask, beta=1.5, column=False):
    """A saturated star with power-law wings amp (1 + r^2)^-beta at (x0, y0), masked (SUR_ERROR) within r_mask px,
    OVERFLOW within 1 px; `column`: a masked bad column touching the core's right side pulls the hole's centre off."""
    ny, nx = np.shape(l2.data)
    yy, xx = np.mgrid[:ny, :nx]
    r = np.hypot(xx - x0, yy - y0)
    data = np.asarray(l2.data, np.float64) + amp / (1 + r ** 2) ** beta
    hole = r < r_mask
    if column:
        hole |= (xx == int(round(x0 + r_mask))) & (np.abs(yy - y0) < r_mask + 3)
    data[hole] = 3000.0
    l2.data = data.astype(np.float32)
    fl = np.asarray(l2.flags).copy()
    fl[hole] |= SUR_ERROR
    fl[r < 1.0] |= OVERFLOW
    l2.flags = fl
    l2.variance = (np.asarray(l2.variance, np.float64) + 5e-4 * amp / (1 + r ** 2) ** beta).astype(np.float32)


def _two_saturated_stars(l2):
    """A big saturated star at (X0, Y0) (hole r 4 px whose ring breaks into pieces, hole centre pulled ~1 px by a bad
    column) and a small one at (X1, Y1) whose light is masked out to r = 2.6 px, so nothing is detected on it: only
    add_core_dots can give it a dot."""
    _sat_star(l2, X0, Y0, 2000.0, 4.0, column=True)
    _sat_star(l2, X1, Y1, 400.0, 2.6)


X1, Y1 = 20.4, 80.7


def test_v2_every_saturated_core_gets_one_dot_on_the_star(tmp_path):
    """Extraction v2 (18 Nov, dots.add_core_dots): each saturated star keeps exactly one dot, placed on the star by
    its wings (not on a ring piece or the hole's centre), flagged saturated, with the wings fit's flux."""
    l2 = _photo(tmp_path)
    _two_saturated_stars(l2)
    counts: dict = {}
    df = D.dots_table(l2, "X", 1, settings=C.DOTS_V2_SETTINGS, counts=counts)
    core = _core_rows(df)
    assert counts["n_sat_cores"] >= 2 and len(core) == counts["n_sat_cores"]
    for x0, y0 in ((X0, Y0), (X1, Y1)):
        d = np.hypot(core.x - x0, core.y - y0).to_numpy()
        k = int(np.argmin(d))
        assert d[k] < 0.5
        row = core.iloc[k]
        assert int(row["flags_peak"]) & OVERFLOW and int(row["flags"]) & OVERFLOW
        assert not row.psf_pos_ok and row.pos_err_arcsec > 0 and row.snr >= 5
        assert not int(row.sep_flag) & ~0x0FFF  # sep_flag keeps its own bits only
        if int(row.dot_flag) & C.DOT_FLAG_SAT_NOFIT:
            assert np.isnan(row.flux_ujy)
        else:
            assert row.flux_ujy > 0 and row.flux_psf_ujy == pytest.approx(row.flux_ujy * C.SAT_CORE_APCORR, rel=1e-5)
        assert int((np.hypot(df.x - x0, df.y - y0) < 2.0).sum()) == 1  # one dot on the core
    f = df.flux_ujy.to_numpy()
    assert np.all(np.diff(f[np.isfinite(f)]) <= 0)  # brightest first, NaN last
    assert df.flux_ujy.isna().sum() == 0 or np.isnan(f[-1])


def test_core_dots_only_in_v2_and_with_the_switch(tmp_path, monkeypatch):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    l2 = _photo(tmp_path / "a")
    _two_saturated_stars(l2)
    assert len(_core_rows(D.dots_table(l2, "X", 1, settings=FINE))) == 0  # the fine glow setting: as before
    monkeypatch.setattr(C, "SAT_CORE_DOTS", False)
    l2 = _photo(tmp_path / "b")
    _two_saturated_stars(l2)
    assert len(_core_rows(D.dots_table(l2, "X", 1, settings=C.DOTS_V2_SETTINGS))) == 0


def test_add_core_dots_adds_a_row_where_nothing_was_detected(tmp_path):
    l2 = _photo(tmp_path)
    _two_saturated_stars(l2)
    ctx = D.photo_context(l2)
    from skyshift_core.glow import fit_glow
    gl = fit_glow(l2.data, l2.flags, ctx.noise, epsf=l2.epsf)
    counts: dict = {}
    det, own, placed = D.add_core_dots(l2, ctx, gl, D._no_dets(), np.zeros(0, bool), counts, {})
    assert len(det) == counts["n_sat_cores"] == counts["n_sat_core_dots_new"] == len(placed) >= 2 and own.all()
    assert np.all(det["sep_flag"] & C.dot_flag_mem(C.DOT_FLAG_SAT_CORE)) and np.all(det["snr"] >= 50)
    for x0, y0 in ((X0, Y0), (X1, Y1)):
        assert np.min(np.hypot(det["x"] - x0, det["y"] - y0)) < 0.5
    # with a detection on the ring (a piece 3 px out), that row becomes the star's dot: no second row
    one = D._no_dets().copy()
    one = np.zeros(1, one.dtype)
    one["x"], one["y"], one["snr"], one["sep_flag"] = X0 + 3.0, Y0, 300.0, C.SEP_FLAG_CENTROID_FALLBACK | 1
    det2, own2, _ = D.add_core_dots(l2, ctx, gl, one, np.zeros(1, bool), {}, {})
    k = int(np.argmin(np.hypot(det2["x"] - X0, det2["y"] - Y0)))
    assert len(det2) == len(det) and own2[k] and det2["sep_flag"][k] == (1 | C.dot_flag_mem(C.DOT_FLAG_SAT_CORE))
    assert np.hypot(det2["x"][k] - X0, det2["y"][k] - Y0) < 0.5


def test_drop_core_duplicates_keeps_matched_filter_dots_off_the_star():
    """#413 x #414 (18 Nov): a matched-filter dot within SAT_CORE_DUP_PX of a saturated core's dot is that star again
    and goes; one farther out (Rhea, 10.8 px from Saturn) and any non-matched-filter dot stay."""
    cd = np.zeros(3, D._no_dets().dtype)
    cd["x"], cd["y"] = [10.5, 20.0, 10.0], [10.0, 10.0, 11.0]
    cd["sep_flag"] = [C.SEP_FLAG_MF_PASS | C.dot_flag_mem(C.DOT_FLAG_MF_GLOW_PIECE), C.SEP_FLAG_MF_PASS,
                      C.SEP_FLAG_PSF_PASS]
    counts: dict = {}
    placed = np.array([[10.0, 10.0, 1.0, 13.0]])
    out = D.drop_core_duplicates((cd, None), placed, counts)
    assert counts["n_sat_core_dup_mf"] == 1 and list(out[0]["x"]) == [20.0, 10.0]
    assert D.drop_core_duplicates(None, placed, {}) is None
    assert D.drop_core_duplicates((cd, None), np.zeros((0, 4)), {})[0] is cd
