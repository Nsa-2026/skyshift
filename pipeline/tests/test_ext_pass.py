"""Extended-source pass (18 Nov, agent "comets"; psf.ext_kernel / ext_filter / ext_pass, dots._ext_pass, ext_table):
diffuse sources (comet comae, galaxies) a point-source filter misses, as rows of their own file <folder>_ext/ (the dots
never change). Extraction v2 only, config EXT_PASS (env SKYSHIFT_DOTS_EXT)."""

import numpy as np
import pandas as pd
import pytest

import skyshift_pipeline.config as C
from skyshift_core import open_level2
from skyshift_pipeline import dots as D
from skyshift_pipeline import psf as P

from test_dots_extras import epsf, star_field, synthetic_mef  # noqa: F401  (epsf is a fixture)

V2 = C.DOTS_V2_SETTINGS


def _blob(img, E, x, y, scale, total):
    """Add a source `scale` × the PSF wide (ext_kernel) of total flux `total` centred on pixel (x, y)."""
    K = P.ext_kernel(E, int(E.zone([x], [y])[0]) if E is not None else -1, scale)
    h = K.shape[0] // 2
    img[y - h:y + h + 1, x - h:x + h + 1] += total * K
    return K


def test_ext_kernel_and_filter(epsf):  # noqa: F811
    E = epsf.shifted(1000, 1000)
    z = int(E.zone([30.0], [30.0])[0])
    for sc in (1.0, 2.0, 4.0):
        K = P.ext_kernel(E, z, sc)
        assert K.sum() == pytest.approx(1.0, rel=0.02) and K.shape == (2 * P.ext_half(sc) + 1,) * 2
        assert np.unravel_index(np.argmax(K), K.shape) == (K.shape[0] // 2,) * 2
    assert P.ext_half(1.0) == C.MF_KERNEL_HALF and P.ext_half(4.0) == 7
    # scale 1 with the matched filter's half-size is the matched filter itself
    rng = np.random.default_rng(2)
    img = rng.normal(0, 1, (80, 80))
    noise, bad = np.ones(img.shape), np.zeros(img.shape, bool)
    s0, f0, d0, _ = P.matched_filter(img, noise, bad, E)
    s1, f1, d1 = P.ext_filter(img, noise, bad, E, 1.0, C.MF_KERNEL_HALF)
    assert np.allclose(s0, s1, atol=1e-9) and np.allclose(d0, d1, rtol=1e-9)
    # a point source: the wide filters see it at less S/N; a source 4 × wide: more, and its flux is the total
    pt = star_field(E, [(40.0, 40.0)], 100.0, sky=0.0, sigma=0.0, shape=(80, 80))
    sp = {sc: P.ext_filter(pt, noise, bad, E, sc)[0][40, 40] for sc in (1.0, 2.0, 4.0)}
    assert 0.65 < sp[2.0] / sp[1.0] < 0.85 and 0.3 < sp[4.0] / sp[1.0] < 0.55
    wide = np.zeros((80, 80))
    _blob(wide, E, 40, 40, 4.0, 100.0)
    sw = {sc: P.ext_filter(wide, noise, bad, E, sc) for sc in (1.0, 2.0, 4.0)}
    assert sw[4.0][0][40, 40] > 2.0 * sw[1.0][0][40, 40] and sw[4.0][0][40, 40] > sw[2.0][0][40, 40]
    assert sw[4.0][1][40, 40] == pytest.approx(100.0, rel=1e-6)


def test_ext_pass_finds_a_diffuse_blob_not_a_star(epsf):  # noqa: F811
    E = epsf.shifted(1000, 1000)
    rng = np.random.default_rng(5)
    shape = (128, 128)
    img = rng.normal(0, 1.0, shape)
    noise, bad = np.ones(shape), np.zeros(shape, bool)
    # a diffuse source 4 × the PSF wide: matched-filter (point) S/N ~3, 4 × filter S/N ~9
    K4 = _blob(img, E, 40, 40, 4.0, 0.0)
    total = 9.0 / np.sqrt((K4 ** 2).sum())
    _blob(img, E, 40, 40, 4.0, total)
    # a faint point source the dots missed (point S/N ~6) and a bright star that is a dot (S/N ~60)
    for (x, y), snr in (((90.0, 40.0), 6.0), ((60.0, 95.0), 60.0)):
        Pk, ix, iy = E.stamps([x], [y], half=6)
        Kp = P.mf_kernel(E, int(E.zone([x], [y])[0]))
        img[iy[0] - 6:iy[0] + 7, ix[0] - 6:ix[0] + 7] += snr / np.sqrt((Kp ** 2).sum()) * Pk[0]
    pt = P.ext_filter(img, noise, bad, E, 1.0, C.MF_KERNEL_HALF)[0]
    assert pt[38:43, 38:43].max() < C.MF_PASS_SNR  # the matched-filter pass would not make it a dot
    dx, dy = np.array([60.0]), np.array([95.0])
    ep = P.ext_pass(img, noise, bad, E, dx, dy, sub_x=dx, sub_y=dy)
    hit = np.hypot(ep.dots["x"] - 40, ep.dots["y"] - 40) < 1.5
    assert hit.sum() == 1 and ep.scale[hit][0] == 4 and ep.snr_ext[hit][0] >= C.EXT_PASS_SNR
    assert ep.snr_ext[hit][0] > ep.snr_point[hit][0]
    assert not (np.hypot(ep.dots["x"] - 90, ep.dots["y"] - 40) < 3).any()  # point-like: the ratio test refuses it
    assert not (np.hypot(ep.dots["x"] - 60, ep.dots["y"] - 95) < 3).any()  # the subtracted star leaves nothing
    assert ep.info["n_subtracted"] == 1 and ep.info["n_ext_pass"] == len(ep.dots)
    assert ep.info["s4"]["sigma_lo"] == pytest.approx(1.0, abs=0.2)
    # without the subtraction the star's wings are no extended source either (ratio test)
    e2 = P.ext_pass(img, noise, bad, E, dx, dy)
    assert not (np.hypot(e2.dots["x"] - 60, e2.dots["y"] - 95) < 3).any()
    # a dot on the blob already explains it
    e3 = P.ext_pass(img, noise, bad, E, np.r_[dx, 40.2], np.r_[dy, 39.8], sub_x=dx, sub_y=dy)
    assert not (np.hypot(e3.dots["x"] - 40, e3.dots["y"] - 40) < 3).any()
    # the saturation guard and an excluded region
    dsat = np.full(shape, np.inf)
    dsat[35:46, 35:46] = 2.0
    assert not (np.hypot(P.ext_pass(img, noise, bad, E, dx, dy, sat_dist=dsat, sat_guard_px=6.0).dots["x"] - 40,
                         0) < 3).any()
    ex = np.zeros(shape, bool)
    ex[38:43, 38:43] = True
    e4 = P.ext_pass(img, noise, bad, E, dx, dy, exclude=ex)
    assert not (np.hypot(e4.dots["x"] - 40, e4.dots["y"] - 40) < 3).any()
    # pure noise: nothing
    n0 = P.ext_pass(rng.normal(0, 1.0, shape), noise, bad, E, np.zeros(0), np.zeros(0))
    assert len(n0.dots) == 0


def test_v2_ext_file_and_switch(tmp_path, monkeypatch):
    """The hook on the real cutout (crowded: a faint blob there is taken by the point passes first, so the pass's own
    detection is tested above on synthetic images): the dots are the same with the pass on or off; every ext_pass row
    away from the final dots goes to <folder>_ext/ (EXT_COLUMNS), written next to the dot file by process_photos, its
    settings and map noise in the footer; with EXT_PASS off there is no ext file."""
    from skyshift_core.detect import measure_objects
    path = synthetic_mef(tmp_path)

    def load(url=None):
        l2 = open_level2(path, variance=True, epsf=True)
        l2.zodi = None
        if url:
            l2.detector = int(url[-1])
        return l2

    l2 = load()
    monkeypatch.setattr(C, "EXT_PASS", False)
    off = D.dots_table(l2, "X", 1, settings=V2, extras={})
    monkeypatch.setattr(C, "EXT_PASS", True)
    ny, nx = np.shape(l2.data)
    xy = off[["x", "y"]].to_numpy()
    ctx = D.photo_context(l2, {})
    spots = [(np.hypot(xy[:, 0] - x, xy[:, 1] - y).min(), x, y) for y in range(12, ny - 12) for x in range(12, nx - 12)
             if not ctx.bad[y - 4:y + 5, x - 4:x + 5].any()]
    dmin, sx, sy = max(spots)  # the emptiest clean spot
    assert dmin > 3, (ny, nx, dmin)
    real = P.ext_pass
    seen = {}

    def fake(resid, noise, bad, epsf, dot_x, dot_y, **k):
        ep = real(resid, noise, bad, epsf, dot_x, dot_y, **k)
        seen.update(k, n_dots=len(dot_x))
        objs = np.zeros(2, dtype=[("x", "f8"), ("y", "f8"), ("xpeak", "i4"), ("ypeak", "i4"), ("peak", "f8"),
                                  ("npix", "i4"), ("flag", "i4")])
        objs["x"], objs["y"] = [sx + 0.2, xy[0, 0] + 0.3], [sy - 0.1, xy[0, 1]]  # one free, one on a dot
        objs["xpeak"], objs["ypeak"] = np.rint(objs["x"]), np.rint(objs["y"])
        two = np.concatenate([measure_objects(np.ascontiguousarray(resid, np.float64),
                                              np.ascontiguousarray(noise, np.float64), np.ascontiguousarray(bad),
                                              objs[i:i + 1]) for i in range(2)])
        return P.ExtPass(np.concatenate([ep.dots, two]), np.r_[ep.snr_ext, 7.5, 6.0],
                         np.r_[ep.scale, 4, 2].astype(np.int8), ep.info, np.r_[ep.snr_point, 2.0, 2.0],
                         np.r_[ep.flux_ext, 1.0, 1.0])

    monkeypatch.setattr(D, "ext_pass", fake)
    counts, extras = {}, {}
    on = D.dots_table(l2, "X", 1, settings=V2, counts=counts, extras=extras)
    pd.testing.assert_frame_equal(on, off)  # the dots never change
    assert list(on.columns) == list(D.DOT_COLUMNS) + list(D.DOT_LIST_COLUMNS) + list(D.DOT_V2_COLUMNS)
    # every dot is in the dedup list, the ones not in the second pass's model are subtracted, the config is passed
    assert seen["n_dots"] >= len(on) * 0.9 and 0 < len(seen["sub_x"]) <= seen["n_dots"]
    assert seen["min_snr"] == C.EXT_PASS_SNR and seen["scales"] == C.EXT_SCALES and seen["sat_guard_px"] > 0
    et = D.ext_table(l2, ctx, extras["ext"], on)
    assert list(et.columns) == list(D.EXT_COLUMNS) and dict(et.dtypes.astype(str)) == D.EXT_COLUMNS
    hit = et[np.hypot(et.x - sx, et.y - sy) < 1.5]
    assert len(hit) == 1 and hit.ext_scale.iloc[0] == 4 and hit.snr_ext.iloc[0] == pytest.approx(7.5)
    assert hit.near_dot_px.iloc[0] > C.EXT_DEDUP_PX and (et.near_dot_px > C.EXT_DEDUP_PX).all()  # the one on a dot: out
    assert counts["n_ext_pass"] == len(extras["ext"]["x"]) - 2
    # written by process_photos next to the dot file; the footer has the settings and each map's noise
    monkeypatch.setattr(C, "DOTS_V2", True)
    monkeypatch.setattr(D, "load_photo", load)
    root = tmp_path / "root"
    st = D.process_photos(str(root), [("OBS", 1, "u1", 0, 0)], prefetch=False)
    assert st[0]["status"] == "done" and st[0]["n_ext"] >= 1
    e = pd.read_parquet(root / D.ext_rel("OBS", 1, V2.folder))
    assert list(e.columns) == list(D.EXT_COLUMNS) and len(e) == st[0]["n_ext"]
    m = D.photo_meta(str(root / f"{V2.folder}/OBSD1.parquet"))
    assert m["ext"]["min_snr"] == C.EXT_PASS_SNR and "sigma_lo" in m["ext"]["s4"]
    monkeypatch.setattr(C, "EXT_PASS", False)
    root2 = tmp_path / "root2"
    st2 = D.process_photos(str(root2), [("OBS", 1, "u1", 0, 0)], prefetch=False)
    assert st2[0]["status"] == "done" and "n_ext" not in st2[0]
    assert not (root2 / D.ext_rel("OBS", 1, V2.folder)).exists()
    assert "ext" not in D.photo_meta(str(root2 / f"{V2.folder}/OBSD1.parquet"))
