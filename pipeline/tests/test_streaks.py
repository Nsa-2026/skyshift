"""Streak search (streaks.py): fast movers' trails on the matched-filter pass's residual, their own file."""

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import skyshift_pipeline.config as C
from skyshift_core import open_level2
from skyshift_pipeline import dots as D
from skyshift_pipeline import streaks as S

from test_dots_extras import epsf, synthetic_mef  # noqa: F401  (epsf is a fixture)

V2 = C.DOTS_V2_SETTINGS


def _noise_with_trails(trails, n=512, seed=0, table=None):
    """Unit-noise image with trails (x, y, L, theta_deg, snr) drawn from the Gaussian-PSF kernel (S/N for their own
    kernel)."""
    rng = np.random.default_rng(seed)
    img = rng.normal(size=(n, n))
    table, os_ = S.psf_table(None) if table is None else table
    for x, y, L, thd, snr in trails:
        ix, iy = int(round(x)), int(round(y))
        K = S.trail_kernel(table, os_, L, np.radians(thd), phase=(x - ix, y - iy))
        h = K.shape[0] // 2
        img[iy - h:iy + h + 1, ix - h:ix + h + 1] += snr / np.sqrt((K * K).sum()) * K
    return img


def _match(st, x, y, L, thd):
    d = np.hypot(st["x"] - x, st["y"] - y)
    dth = np.abs((st["theta_deg"] - thd + 90) % 180 - 90)
    ok = (d <= max(1.5, 0.1 * L)) & (dth <= 20)
    return np.flatnonzero(ok)


def test_trail_kernel_shape_and_sum():
    table, os_ = S.psf_table(None)
    K0 = S.trail_kernel(table, os_, 0.0, 0.0)
    K = S.trail_kernel(table, os_, 10.0, np.radians(30))
    assert abs(K0.sum() - 1) < 0.05 and abs(K.sum() - 1) < 0.05  # a narrow Gaussian sampled at pixel centres
    assert K.shape[0] == 2 * S.kernel_half(10.0) + 1
    h = K.shape[0] // 2
    # symmetric about its centre, longest along 30°: the far pixels on the line hold light, across it none
    assert np.allclose(K, K[::-1, ::-1], atol=0.1 * K.max())  # samples rounded to 0.2 px
    ex, ey = int(round(4.5 * np.cos(np.radians(30)))), int(round(4.5 * np.sin(np.radians(30))))
    assert K[h + ey, h + ex] > 10 * K[h + ex, h - ey]
    # a trail spreads its light: its peak is far below a point source's
    assert K.max() < 0.3 * K0.max()


def test_bank_angles_scale_with_length():
    bank = S.make_bank((4.0, 9.0), end_step_px=1.0)
    n4 = sum(1 for L, _ in bank if L == 4.0)
    n9 = sum(1 for L, _ in bank if L == 9.0)
    assert n4 >= 4 and n9 > n4
    assert all(0 <= t < np.pi for _, t in bank)


def test_finds_short_and_long_trails_in_noise():
    """Trails 6, 9, 20 and 40 px at S/N 10–15 in unit noise: each found with its centre within ~1 px (10% of the
    length for long ones), angle within 20°, length within 30%; pure noise elsewhere gives (almost) nothing."""
    trails = [(100.3, 110.6, 6, 40.0, 12), (300.1, 120.2, 9, 120.0, 12), (110.0, 360.4, 20, 10.0, 12),
              (330.7, 350.2, 40, 75.0, 15)]
    img = _noise_with_trails(trails)
    r = S.streak_search(img, np.ones_like(img), np.zeros(img.shape, bool), None)
    st = r.streaks
    for x, y, L, thd, snr in trails:
        j = _match(st, x, y, L, thd)
        assert j.size, (x, y, L, thd, pd.DataFrame(st).round(1).to_string())
        k = j[np.argmax(st["snr"][j])]
        assert abs(st["length_px"][k] / L - 1) < 0.3, (L, st["length_px"][k])
        assert st["snr"][k] > 0.6 * snr
    assert r.info["n_streaks"] <= len(trails) + 2


def test_noise_alone_and_a_bright_star_make_no_streak(epsf):  # noqa: F811
    """Pure noise: no streak at the threshold; a bright point source (S/N ~60): a point explains it, no streak."""
    rng = np.random.default_rng(3)
    img = rng.normal(size=(300, 300))
    E = epsf.shifted(900, 900)
    P, ix, iy = E.stamps([150.2], [149.7], half=6)
    img[iy[0] - 6:iy[0] + 7, ix[0] - 6:ix[0] + 7] += 60 / np.sqrt((P[0] ** 2).sum()) * P[0]
    r = S.streak_search(img, np.ones_like(img), np.zeros(img.shape, bool), E)
    st = r.streaks
    near = np.hypot(st["x"] - 150, st["y"] - 150) < 6
    assert not near.any(), pd.DataFrame(st).round(2).to_string()
    assert r.info["n_streaks"] <= 1


def test_why_bits_masked_and_dot():
    """A trail with a dot on it brighter than itself (WHY_DOT); one along a masked column (masked_frac, WHY_AXIS)."""
    trails = [(120.0, 120.0, 9, 30.0, 14), (300.0, 200.0, 9, 90.5, 14)]
    img = _noise_with_trails(trails)
    bad = np.zeros(img.shape, bool)
    bad[190:211, 300] = True
    img[bad] = 0.0
    r = S.streak_search(img, np.ones_like(img), bad, None, dot_x=[120.5], dot_y=[120.2], dot_snr=[40.0])
    st = r.streaks
    j = _match(st, 120, 120, 9, 30.0)
    assert j.size and all(st["why"][j] & S.WHY_DOT) and st["n_dots"][j[0]] == 1
    j = _match(st, 300, 200, 9, 90.5)
    if j.size:
        assert all(st["masked_frac"][j] > 0) and all(st["why"][j] & S.WHY_AXIS)


def test_streaks_table_columns_sky_and_rate(tmp_path):
    """The file's columns and types; ends on the sky, length in ″ (6.15″ per pixel) and the rate over XPOSURE."""
    l2 = open_level2(synthetic_mef(tmp_path), variance=True, epsf=True)
    st = {k: np.zeros(1) for k in S.STREAK_KEYS}
    st.update(x=np.array([50.0]), y=np.array([40.0]), x1=np.array([45.0]), y1=np.array([40.0]),
              x2=np.array([55.0]), y2=np.array([40.0]), length_px=np.array([10.0]), theta_deg=np.array([0.0]),
              flux=np.array([2.0]), flux_err=np.array([0.2]), snr=np.array([10.0]), why=np.array([0]))
    df = S.streaks_table(l2, S.StreakResult(st, {}), 889.0)
    assert list(df.columns) == list(S.STREAK_COLUMNS) and str(df.why.dtype) == "int16"
    assert abs(df.length_arcsec.iloc[0] - 10 * C.PIXEL_ARCSEC) < 1.0
    xp = float(l2.header.get("XPOSURE") or C.STREAK_EXPOSURE_S)
    assert abs(df.rate_arcsec_h.iloc[0] - df.length_arcsec.iloc[0] / xp * 3600) < 1e-3
    assert 0 <= df.pa_deg.iloc[0] < 180 and abs(df.flux_ujy.iloc[0] - 1778.0) < 1e-3
    assert len(S.streaks_table(l2, None, 889.0)) == 0
    assert S.streaks_rel("OBS", 3, "dots_x") == "dots_x_streaks/OBSD3.parquet"


def test_dots_v2_writes_a_streak_file_and_the_switch(tmp_path, monkeypatch):
    """process_photos (DOTS_V2): a streak file next to the dot and peaks files, its counts in the footer; a trail
    injected into the real cutout is in it. SKYSHIFT_STREAKS off: no file, dots unchanged."""
    path = synthetic_mef(tmp_path)
    monkeypatch.setattr(C, "DOTS_V2", True)
    l0 = open_level2(path, variance=True, epsf=True)
    ny, nx = np.shape(l0.data)
    ctx = D.photo_context(l0, {})
    base = D.dots_table(l0, "X", 1, settings=V2)
    xy = base[["x", "y"]].to_numpy()
    spots = [(x + 0.3, y - 0.2) for y in range(16, ny - 16, 2) for x in range(16, nx - 16, 2)
             if np.hypot(xy[:, 0] - x, xy[:, 1] - y).min() > 5 and not ctx.bad[y - 6:y + 7, x - 6:x + 7].any()]
    assert spots
    x0, y0 = spots[len(spots) // 2]
    table, os_ = S.psf_table(l0.epsf)
    K = S.trail_kernel(table, os_, 9.0, np.radians(35.0), phase=(x0 - round(x0), y0 - round(y0)))
    h = K.shape[0] // 2
    noise = float(np.median(ctx.noise0[int(round(y0)) - h:int(round(y0)) + h + 1, int(round(x0)) - h:int(round(x0)) + h + 1]))
    F = 25 * noise / np.sqrt((K * K).sum())   # crowded Cygnus cutout: each length's own scatter is 2-2.8x the noise

    def load(url):
        l2 = open_level2(path, variance=True, epsf=True)
        l2.zodi = None
        d = np.asarray(l2.data, np.float64).copy()
        d[int(round(y0)) - h:int(round(y0)) + h + 1, int(round(x0)) - h:int(round(x0)) + h + 1] += F * K
        l2.data = d.astype(np.float32)
        return l2

    monkeypatch.setattr(D, "load_photo", load)
    root = tmp_path / "root"
    st = D.process_photos(str(root), [("OBS", 1, "u1", 0, 0)], prefetch=False)
    assert st[0]["status"] == "done" and st[0]["n_streaks"] >= 1 and st[0]["streaks_bytes"] > 0
    sp = root / S.streaks_rel("OBS", 1, V2.folder)
    sf = pd.read_parquet(sp)
    assert list(sf.columns) == list(S.STREAK_COLUMNS)
    near = (np.hypot(sf.x - x0, sf.y - y0) <= 2.0) & (np.abs((sf.theta_deg - 35.0 + 90) % 180 - 90) <= 20)
    assert near.any(), (x0, y0, sf[np.hypot(sf.x - x0, sf.y - y0) < 15].round(1).to_string(), len(sf))
    m = D.photo_meta(str(root / f"{V2.folder}/OBSD1.parquet"))
    assert m["streaks"]["n_streaks"] == len(sf) and m["streaks"]["config"]["snr"] == C.STREAK_SNR
    dots_on = pd.read_parquet(root / f"{V2.folder}/OBSD1.parquet")
    monkeypatch.setattr(C, "STREAKS", False)
    root2 = tmp_path / "root2"
    D.process_photos(str(root2), [("OBS", 1, "u1", 0, 0)], prefetch=False)
    assert not (root2 / S.streaks_rel("OBS", 1, V2.folder)).exists()
    dots_off = pd.read_parquet(root2 / f"{V2.folder}/OBSD1.parquet")
    pd.testing.assert_frame_equal(dots_on, dots_off)


def test_streak_failure_never_costs_the_dots(tmp_path, monkeypatch):
    path = synthetic_mef(tmp_path)
    monkeypatch.setattr(C, "DOTS_V2", True)

    def load(url):
        l2 = open_level2(path, variance=True, epsf=True)
        l2.zodi = None
        return l2

    def boom(*a, **k):
        raise RuntimeError("no")

    monkeypatch.setattr(D, "load_photo", load)
    monkeypatch.setattr(S, "streak_search", boom)
    st = D.process_photos(str(tmp_path / "r"), [("OBS", 1, "u1", 0, 0)], prefetch=False)
    assert st[0]["status"] == "done" and st[0]["n_dots"] > 0, st[0].get("error")
    m = D.photo_meta(str(tmp_path / "r" / f"{V2.folder}/OBSD1.parquet"))
    assert m["streaks"]["error"].startswith("RuntimeError") and st[0]["streaks_error"]
