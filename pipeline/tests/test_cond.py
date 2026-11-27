"""The conditions table of extraction v2 (cond.py, config COND*): tiles, the negative-residual peaks of the matched
filter, the table's columns and footer, the dots worker's hooks (dots unchanged with it on or off; a failure leaves the
photo its dots and an error in the dot file's footer)."""

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest

import skyshift_pipeline.config as C
from skyshift_core import open_level2
from skyshift_core.flags import COLD, HALO, OVERFLOW, TRANSIENT
from skyshift_pipeline import cond as CD
from skyshift_pipeline import dots as D
from skyshift_pipeline import psf as P

from test_dots_extras import epsf, star_field, synthetic_mef  # noqa: F401  (epsf is a fixture)

V2 = C.DOTS_V2_SETTINGS


def test_tiles_grid_counts_and_quantiles():
    T = CD.Tiles((100, 70), 32, 2)
    assert (T.nty, T.ntx, T.n) == (4, 3, 12)
    tx, ty, xc, yc, npx = T.centres()
    assert npx.sum() == 100 * 70 and npx[-1] == (70 - 64) * (100 - 96)
    assert (xc[:3] == [15.5, 47.5, 66.5]).all() and yc[-1] == 97.5
    # positions: pixel centres at integers, clipped to the photo
    assert list(T.of([0, 31.4, 31.6, 69.9, -3], [0, 0, 0, 99.9, 120])) == [0, 0, 1, 11, 9]
    c = T.count([1, 2, 40, np.nan], [1, 1, 1, 5])
    assert c[0] == 2 and c[1] == 1 and c.sum() == 3
    m = np.zeros((100, 70), bool)
    m[96:, 64:] = True
    assert T.pixel_count(m)[-1] == 24 and T.pixel_count(m).sum() == 24
    # quantiles of rows with NaN padding; an empty row is NaN
    B = np.array([[1, 2, 3, np.nan], [np.nan] * 4, [5, 5, 7, 9]], np.float32)
    med, lo = CD.quantiles(B, [0.5, 0.0])
    assert med[0] == 2 and np.isnan(med[1]) and med[2] == 6 and lo[2] == 5
    # sampled blocks: every 2nd pixel of each tile
    a = np.arange(100 * 70, dtype=np.float32).reshape(100, 70)
    Bk = T.blocks(a)
    assert Bk.shape == (12, 16 * 16) and Bk[0, 0] == 0 and Bk[0, 1] == 2 and Bk[1, 0] == 32
    assert np.isfinite(Bk[-1]).sum() == 3 * 2  # the last tile: 6 x 4 px sampled every 2nd -> 3 x 2


def test_circles_touching():
    T = CD.Tiles((64, 64), 32)
    # a circle centred in tile 0 reaching 3 px across the vertical edge, and one far outside
    n = CD.circles_touching(T, np.array([[28.0, 10.0, 6.0], [200.0, 200.0, 5.0]]))
    assert list(n) == [1, 1, 0, 0]


def test_negative_map_is_the_filter_on_minus_the_image(epsf):  # noqa: F811
    """matched_filter(neg=...) gives exactly matched_filter(-sub)'s S/N (several phases), and mf_pass's negative
    peaks are mf_peaks on that map; nothing mf_pass returns changes."""
    E = epsf.shifted(1000, 1000)
    img = star_field(E, [(20.0, 22.0), (45.3, 40.6)], 30.0, sky=0.0, sigma=1.0, shape=(64, 64), seed=7)
    noise, bad = np.full(img.shape, 1.0), np.zeros(img.shape, bool)
    bad[5:8, 50:53] = True
    img[bad] = 0.0
    for phases in (1, 2):
        neg: dict = {}
        s, f, d, ph = P.matched_filter(img, noise, bad, E, phases=phases, neg=neg)
        s0, f0, d0, ph0 = P.matched_filter(img, noise, bad, E, phases=phases)
        sm, _, _, _ = P.matched_filter(-img, noise, bad, E, phases=phases)
        assert np.array_equal(s, s0) and np.array_equal(f, f0) and np.array_equal(d, d0) and np.array_equal(ph, ph0)
        assert np.allclose(neg["snr"], sm, atol=1e-12, rtol=0)
    a = P.mf_pass(img, noise, bad, E, np.zeros(0), np.zeros(0), min_snr=5.0)
    b = P.mf_pass(img, noise, bad, E, np.zeros(0), np.zeros(0), min_snr=5.0, neg_snr=2.5)
    assert a.neg is None and b.neg is not None
    assert np.array_equal(a.dots, b.dots) and np.array_equal(a.den, b.den)
    assert all(np.array_equal(a.peaks[k], b.peaks[k]) for k in a.peaks)
    sm, _, dm, _ = P.matched_filter(-img, noise, bad, E)
    _, _, ix, iy, sv = P.mf_peaks(np.where(dm > 0, sm, -np.inf), 2.5)
    assert np.array_equal(b.neg["ix"], ix) and np.array_equal(b.neg["iy"], iy) and np.allclose(b.neg["snr"], sv)
    assert len(ix) >= 1 and (sv >= 2.5).all()


def _photo(tmp_path):
    l2 = open_level2(synthetic_mef(tmp_path), variance=True, epsf=True, zodi=True)
    return l2


def test_build_columns_counts_and_footer(tmp_path):
    l2 = _photo(tmp_path)
    flags = np.asarray(l2.flags).copy()
    flags[0:3, 0:4] |= TRANSIENT
    flags[1, 1] |= OVERFLOW
    flags[2, 5] |= COLD | HALO
    l2.flags = flags
    counts, extras = {}, {}
    ctx = D.photo_context(l2, {})
    dots = D.dots_table(l2, "X", 2, settings=V2, ctx=ctx, counts=counts, extras=extras)
    assert {"mf_den", "mf_stats", "mf_neg", "glow_px", "guard_px"} <= set(extras)
    forced = pd.DataFrame({"ref_id": ["1", "2", "allwise:J1"], "x": [3.0, 4.0, 5.0], "y": [3.0, 4.0, 5.0],
                           "flux_psf_ujy": [100.0, 1000.0, 50.0]})
    peaks = D.peaks_table(l2, ctx, extras["peaks"], dots)
    t = CD.build(l2, ctx, extras, dots, forced, peaks, None, None, tile_px=32, obs_id="X", detector=2)
    df = t.to_pandas()
    ny, nx = np.shape(l2.data)
    T = CD.Tiles((ny, nx), 32)
    assert list(df.columns) == list(CD.COLUMNS) and len(df) == T.n
    assert [str(t.schema.field(c).type) for c in ("tile", "wl_um", "n_dots", "n_glow")] == \
        ["int16", "halffloat", "uint16", "uint8"]
    assert df.n_px.sum() == ny * nx
    assert df.n_dots.sum() == len(dots)
    assert df.n_dots_sep.sum() + df.n_dots_psf.sum() + df.n_dots_mf.sum() == len(dots)
    assert df.n_dots_mf.sum() == int(((dots.sep_flag & C.SEP_FLAG_MF_PASS) != 0).sum())
    assert df.n_dots_flagged.sum() == int((dots.dot_flag != 0).sum())
    for k, b in C.DOT_FLAGS.items():
        assert df[f"n_df_{k}"].sum() == int(((dots.dot_flag & b) != 0).sum())
    assert df.n_peaks.sum() == len(peaks) and df.n_ext.sum() == 0
    f = flags.astype(np.int64)
    assert df.n_transient.sum() == int(((f & TRANSIENT) != 0).sum()) and df.n_transient[0] >= 12
    assert df.n_cold.sum() == int(((f & COLD) != 0).sum()) and df.n_halo.sum() == int(((f & HALO) != 0).sum())
    assert df.n_sat.sum() == int(((f & (OVERFLOW | (1 << 15))) != 0).sum())
    assert df.n_good.sum() == int((~ctx.bad).sum())
    assert df.n_gaia[0] == 2 and df.n_red[0] == 1 and df.gaia_ab_min[0] == pytest.approx(23.9 - 7.5, abs=0.01)
    assert df.n_guard_px.sum() == int(extras["guard_px"].sum()) and df.n_glow_px.sum() == int(extras["glow_px"].sum())
    # the depth: median threshold = 5 x the matched filter's point-source sigma where the block scale is 1
    ps = ctx.conv / np.sqrt(extras["mf_den"][16, 16])
    assert df.psf_sigma_ujy[0] == pytest.approx(ps, rel=0.3) and (df.thresh_ujy >= 4.99 * df.psf_sigma_ujy).all()
    assert (df.wl_um > 0.7).all() and (df.bkg_rms_mjysr > 0).all() and (df.zodi_mjysr == np.float16(0.128)).all()
    # negative peaks: counted from the stored ones at each threshold
    ng = extras["mf_neg"]
    ns = np.asarray(ng["snr"])
    _, sc = CD.block_values((ny, nx), extras["mf_stats"], ng["iy"], ng["ix"])
    assert len(ns) and (ns >= min(C.COND_NEG_SNR)).all()
    assert df.n_neg_4.sum() == int((ns >= 4.0 * sc).sum()) >= df.n_neg_45.sum() >= df.n_neg_5.sum()
    assert df.n_neg_5.sum() == int((ns >= 5.0 * sc).sum())
    clear = CD.neg_clear(ng["ix"], ng["iy"], dots, extras["glow_px"], extras["guard_px"])
    assert df.n_neg_4_clear.sum() == int((clear & (ns >= 4.0 * sc)).sum()) <= df.n_neg_4.sum()
    assert df.n_neg_5_clear.sum() == int((clear & (ns >= 5.0 * sc)).sum()) <= df.n_neg_5.sum()
    # clear: not within PSF_PASS_R_PX of a bright dot, not in the glow or guard regions
    bright = dots[dots.snr >= C.PSF_PASS_PRIMARY_SNR]
    for x, y, ok in zip(ng["ix"], ng["iy"], clear):
        near = (np.hypot(bright.x - x, bright.y - y) <= C.PSF_PASS_R_PX).any()
        assert ok == (not near and not extras["glow_px"][y, x] and not extras["guard_px"][y, x])
    # the same lookup as mf_pass: a block with sigma > MF_SIGMA_MIN_SCALE scales the threshold by its sigma
    g = np.asarray(extras["mf_stats"]["sigma_grid"])
    assert set(np.unique(sc)) <= set(np.unique(np.where(g > C.MF_SIGMA_MIN_SCALE, g, 1.0)))
    s = t.schema.metadata[CD.META_KEY.encode()]
    import json
    m = json.loads(s)
    assert m["tile_px"] == 32 and m["n_tiles"] == T.n and m["total"]["n_dots"] == len(dots)
    assert m["neg"]["snr"] == list(C.COND_NEG_SNR) and m["inputs"]["mf_neg"]
    # bytes: the summary survives, types too
    p = tmp_path / "c.parquet"
    p.write_bytes(CD.table_bytes(t))
    back = pq.read_table(p)
    assert back.schema.field("n_dots").type == t.schema.field("n_dots").type
    assert CD.read_summary(p)["n_tiles"] == T.n
    assert back.to_pandas().equals(df)


def _run(tmp_path, monkeypatch, name, cond=True, neg=True, break_build=False, break_write=False):
    path = tmp_path / "l2.fits"
    if not path.exists():
        synthetic_mef(tmp_path)
    monkeypatch.setattr(C, "DOTS_V2", True)
    monkeypatch.setattr(C, "COND", cond)
    monkeypatch.setattr(C, "COND_NEG", neg)
    monkeypatch.setattr(C, "COND_TILE_PX", 32)

    def load(url):
        l2 = open_level2(path, variance=True, epsf=True, zodi=True)
        l2.detector = int(url[-1])
        return l2

    monkeypatch.setattr(D, "load_photo", load)
    if break_build:
        def boom(*a, **k):
            raise RuntimeError("cond broke")
        monkeypatch.setattr(CD, "build", boom)
    if break_write:
        def boom_bytes(t):
            raise OSError("disk full")
        monkeypatch.setattr(CD, "table_bytes", boom_bytes)
    root = tmp_path / name
    st = D.process_photos(str(root), [("OBS", 2, "u2", 0, 0), ("OBS", 5, "u5", 0, 0)], prefetch=False)
    return root, st


def test_worker_writes_cond_and_dots_are_identical_on_and_off(tmp_path, monkeypatch):
    on, st_on = _run(tmp_path, monkeypatch, "on")
    off, st_off = _run(tmp_path, monkeypatch, "off", cond=False)
    assert [s["status"] for s in st_on + st_off] == ["done"] * 4
    for det in (2, 5):
        rel = f"{V2.folder}/OBSD{det}.parquet"
        a, b = pd.read_parquet(on / rel), pd.read_parquet(off / rel)
        assert a.equals(b)  # every dot, every column
        for side in ("_peaks", "_ext", "_streaks"):
            r = f"{V2.folder}{side}/OBSD{det}.parquet"
            assert pd.read_parquet(on / r).equals(pd.read_parquet(off / r))
        ma, mb = D.photo_meta(str(on / rel)), D.photo_meta(str(off / rel))
        assert "cond" not in mb and ma["cond"]["file"] == f"{V2.folder}_cond" and "error" not in ma["cond"]
        cp = on / CD.cond_rel("OBS", det, V2.folder)
        assert cp.exists() and not (off / CD.cond_rel("OBS", det, V2.folder)).exists()
        assert ma["cond"]["bytes"] == cp.stat().st_size and pd.read_parquet(cp).n_dots.sum() == len(a)
        assert CD.read_summary(cp)["detector"] == det
        for k in ("mf", "grids_v2", "detect"):  # the rest of the footer is the same
            assert ma[k] == mb[k]
    assert all(s["cond_bytes"] > 0 and s["cond_s"] >= 0 for s in st_on)
    # the negative count off: same dots, no n_neg counts, the summary says so
    noneg, _ = _run(tmp_path, monkeypatch, "noneg", neg=False)
    rel = f"{V2.folder}/OBSD2.parquet"
    assert pd.read_parquet(noneg / rel).equals(pd.read_parquet(off / rel))
    c = pd.read_parquet(noneg / CD.cond_rel("OBS", 2, V2.folder))
    assert c.n_neg_4.sum() == 0 and CD.read_summary(noneg / CD.cond_rel("OBS", 2, V2.folder))["neg"] is None


def test_cond_failure_keeps_the_dots(tmp_path, monkeypatch):
    with monkeypatch.context() as m:
        off, _ = _run(tmp_path, m, "off", cond=False)
    for kind in ("build", "write"):
        with monkeypatch.context() as m:
            root, st = _run(tmp_path, m, kind, break_build=kind == "build", break_write=kind == "write")
        assert [s["status"] for s in st] == ["done", "done"] and all(s["cond_error"] for s in st)
        for det in (2, 5):
            rel = f"{V2.folder}/OBSD{det}.parquet"
            assert pd.read_parquet(root / rel).equals(pd.read_parquet(off / rel))
            m = D.photo_meta(str(root / rel))
            assert ("cond broke" if kind == "build" else "disk full") in m["cond"]["error"]
            assert not (root / CD.cond_rel("OBS", det, V2.folder)).exists()
