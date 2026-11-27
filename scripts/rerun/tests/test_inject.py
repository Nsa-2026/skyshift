"""Tests for scripts/rerun/inject.py (offline): the fakes' kernels and S/N, the noise added, the matching rules, the
recovery fit, and one job end to end on the real cutout (fixtures/fits) through the dots worker, with and without the
probe (the probe must leave every output unchanged)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("skyshift_pipeline")
HERE = Path(__file__).resolve().parents[1]
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "pipeline" / "tests"))
import inject as I  # noqa: E402
import skyshift_pipeline.config as C  # noqa: E402
from skyshift_core import open_level2  # noqa: E402
from skyshift_core.level2 import EPSF  # noqa: E402
from skyshift_pipeline import dots as D  # noqa: E402
from test_dots_extras import EPSF_FILE, epsf_hdu, synthetic_mef  # noqa: E402

STEP = 2.1 / 1440.0


@pytest.fixture(scope="module")
def epsf():
    return EPSF.from_hdu(epsf_hdu(EPSF_FILE))


def flat_clean(shape=(64, 64), sigma=0.05, k=0.0) -> I.Clean:
    return I.Clean(noise=np.full(shape, sigma), bkg=np.zeros(shape), bad=np.zeros(shape, bool),
                   dsat=np.full(shape, 1e4), conv=889.0, gain_k=k, noise_median=sigma, shape=shape)


def _paste(img, K, ix, iy, amp):
    h = K.shape[0] // 2
    img[iy - h:iy + h + 1, ix - h:ix + h + 1] += amp * K


def test_point_and_trail_stamps_hold_the_flux(epsf):
    K, ix, iy = I.point_stamp(epsf, 32.3, 31.8)
    assert abs(K.sum() - 1.0) < 0.03 and (ix, iy) == (32, 32)
    T, _, _ = I.trail_stamp(epsf, 32.3, 31.8, 12.0, 0.4)
    assert abs(T.sum() - 1.0) < 0.05
    # the trail's light lies along its direction
    yy, xx = np.indices(T.shape) - T.shape[0] // 2
    along = (xx * np.cos(0.4) + yy * np.sin(0.4))
    across = (-xx * np.sin(0.4) + yy * np.cos(0.4))
    assert np.sqrt((T * along ** 2).sum() / T.sum()) > 3 * np.sqrt((T * across ** 2).sum() / T.sum())


@pytest.mark.parametrize("profile", ["gauss", "coma"])
def test_fuzzy_stamps_hold_the_flux_and_grow_with_size(epsf, profile):
    widths = []
    for size in (1.5, 3.0, 6.0):
        w, H = I.fuzzy_weights(5, size, profile, False, 0.0)
        assert abs(w.sum() - 1.0) < 1e-9
        K, _, _ = I.fuzzy_stamp(epsf, 32.0, 32.0, size, profile, False, 0.0)
        assert abs(K.sum() - 1.0) < 0.06
        yy, xx = np.indices(K.shape) - K.shape[0] // 2
        widths.append(np.sqrt((K * (xx ** 2 + yy ** 2)).sum() / K.sum()))
    assert widths[0] < widths[1] < widths[2]
    if profile == "gauss":   # x the PSF: the second moment grows ~ with size
        assert 1.5 < widths[2] / widths[1] < 2.5
    # a tail moves the light off centre along its angle
    K, _, _ = I.fuzzy_stamp(epsf, 32.0, 32.0, 3.0, profile, True, 0.0)
    yy, xx = np.indices(K.shape) - K.shape[0] // 2
    assert (K * xx).sum() / K.sum() > 0.5 and abs((K * yy).sum() / K.sum()) < 0.2


def test_optimal_snr_is_what_an_optimal_filter_measures(epsf):
    """amp_for gives the target S/N; on noise realisations the inverse-variance optimal filter of the fake's own shape
    measures that S/N on average with unit scatter (flat noise, no source noise)."""
    cl = flat_clean()
    K, ix, iy = I.fuzzy_stamp(epsf, 32.4, 31.7, 2.0, "gauss", False, 0.0)
    amp = I.amp_for(K, ix, iy, cl, epsf, 32.4, 31.7, 8.0)
    s = I.stamp_snr(K, ix, iy, cl, epsf, 32.4, 31.7, amp)
    assert abs(s["opt"] - 8.0) < 1e-6
    assert s["pt"] <= s["opt"] + 1e-9 and s["ap"] < s["opt"]
    rng = np.random.default_rng(3)
    est = []
    for _ in range(400):
        d = amp * K + rng.normal(0, 0.05, K.shape)
        est.append(np.sum(K * d) / 0.05 ** 2 / np.sqrt(np.sum(K * K) / 0.05 ** 2))
    assert abs(np.mean(est) - 8.0) < 0.15 and abs(np.std(est) - 1.0) < 0.1


def test_source_noise_raises_the_amplitude_for_a_target_snr(epsf):
    K, ix, iy = I.point_stamp(epsf, 32.0, 32.0)
    a0 = I.amp_for(K, ix, iy, flat_clean(k=0.0), epsf, 32.0, 32.0, 30.0)
    cl = flat_clean(k=0.01)
    a1 = I.amp_for(K, ix, iy, cl, epsf, 32.0, 32.0, 30.0)
    assert a1 > a0
    assert abs(I.stamp_snr(K, ix, iy, cl, epsf, 32.0, 32.0, a1)["opt"] - 30.0) < 1e-3


class _L2:
    def __init__(self, shape=(64, 64)):
        self.data = np.zeros(shape, np.float32)
        self.variance = np.full(shape, 0.0025, np.float32)


def test_inject_adds_the_model_its_photon_noise_and_variance(epsf):
    l2 = _L2((200, 200))
    fakes = []
    for k in range(25):
        x, y = 20.0 + 38 * (k % 5) + 0.3, 20.0 + 38 * (k // 5) - 0.2
        K, ix, iy = I.trail_stamp(epsf, x, y, 6.0, 0.3)
        fakes.append(I.Fake({"amp": 50.0, "x": x, "y": y}, K, ix, iy))
    out, model = I.inject(l2, fakes, 0.01, seed=5)
    assert np.isclose(model.sum(), 50.0 * sum(f.K.sum() for f in fakes))
    on = model > 1.0
    assert np.allclose(out.variance[on], 0.0025 + 0.01 * model[on], rtol=1e-5)
    z = (out.data[on] - model[on]) / np.sqrt(0.01 * model[on])
    assert abs(z.std() - 1.0) < 0.1 and abs(z.mean()) < 0.1
    assert np.all(out.data[~(model > 0)] == 0) and l2.data.sum() == 0     # the photo itself untouched


def test_seg_dist_and_angles():
    d = I.seg_dist(np.array([5.0, 12.0, 5.0]), np.array([1.0, 0.0, 0.0]), 0.0, 0.0, 10.0, 0.0)
    assert np.allclose(d, [1.0, 2.0, 0.0])
    assert np.allclose(I._ang([5.0, 175.0, 90.0], [175.0, 5.0, 0.0]), [10.0, 10.0, 90.0])


def _truth(rows):
    t = pd.DataFrame(rows)
    for c, v in (("size", 1.0), ("length_px", 0.0), ("theta_deg", 0.0), ("mask_r1", 0.0), ("edge_px", 100.0),
                 ("glow_own", False), ("dsat_px", 1e4), ("bkg_loss_det", 0.0), ("det1_d", np.inf),
                 ("mf_snr_truth", 3.0), ("mf_block_sigma", 1.0)):
        if c not in t:
            t[c] = v
    return t


def test_match_points_found_rules_and_miss_causes():
    tr = _truth([
        {"kind": "point", "x": 10.0, "y": 10.0},                                   # a dot on it
        {"kind": "point", "x": 40.0, "y": 10.0},                                   # a mirror-refused peak
        {"kind": "trail", "x": 70.0, "y": 40.0, "length_px": 20.0, "theta_deg": 30.0},  # a streak row
        {"kind": "point", "x": 10.0, "y": 70.0, "mf_snr_truth": 2.0},              # nothing: faint
        {"kind": "point", "x": 40.0, "y": 70.0, "mf_snr_truth": 6.0},              # a brighter dot 3 px off: blend
        {"kind": "fuzzy", "x": 70.0, "y": 70.0, "size": 4.0},                      # an ext row 1.2 px off
    ])
    th = np.radians(30.0)
    o = {"dots": pd.DataFrame({"x": [10.3, 43.0], "y": [9.9, 70.0], "snr": [9.0, 40.0], "snr_mf": [11.0, 50.0],
                               "flux_ujy": [100.0, 900.0], "sep_flag": [0, 0], "dot_flag": [0, 0], "flags": [0, 0]}),
         "peaks": pd.DataFrame({"x": [40.2], "y": [10.1], "snr_mf": [5.5], "why": [2], "flags": [0]}),
         "ext": pd.DataFrame({"x": [71.2], "y": [70.0], "snr_ext": [6.0], "snr_point": [3.0], "ext_scale": [4],
                              "near_dot_px": [np.inf]}),
         "streaks": pd.DataFrame({"x": [70.5], "y": [40.2], "x1": [70.5 - 10 * np.cos(th)], "y1": [40.2 - 10 * np.sin(th)],
                                  "x2": [70.5 + 10 * np.cos(th)], "y2": [40.2 + 10 * np.sin(th)], "length_px": [20.0],
                                  "theta_deg": [32.0], "snr": [9.0], "snr_local": [8.0], "why": [0],
                                  "rate_arcsec_h": [3900.0]})}
    m = I.match_points(tr, o, tr["x"].to_numpy(), tr["y"].to_numpy())
    assert m["found"].tolist() == [True, False, True, False, False, True]
    assert m["found_peak"].tolist()[1] and m["found_any"].tolist()[1]
    assert m["found_streak"].tolist()[2] and m["found_ext"].tolist()[5]
    t = pd.concat([tr, m], axis=1)
    cause = I.miss_cause(t, 6.0)
    assert cause[1] == "peak_mirror" and cause[3] == "faint" and cause[4] == "blend" and cause[0] == ""
    # a streak at the wrong angle is no match for a long trail
    o2 = dict(o, streaks=o["streaks"].assign(theta_deg=[80.0]))
    assert not I.match_points(tr, o2, tr["x"].to_numpy(), tr["y"].to_numpy())["found_streak"][2]


def test_trail_miss_causes_rejected_streak_and_pieces():
    """A long trail with only a rejected streak row is named by the row's first reason; one broken into dots along it
    is `pieces`; a short one (< 2 px) is a point for the causes."""
    tr = _truth([
        {"kind": "trail", "x": 30.0, "y": 30.0, "length_px": 12.0, "theta_deg": 0.0, "mf_snr_truth": 9.0},
        {"kind": "trail", "x": 30.0, "y": 70.0, "length_px": 12.0, "theta_deg": 0.0, "mf_snr_truth": 9.0},
        {"kind": "trail", "x": 70.0, "y": 70.0, "length_px": 1.0, "theta_deg": 0.0, "mf_snr_truth": 9.0},
    ])
    o = {"dots": pd.DataFrame({"x": [34.5, 25.5, 70.0 + 3.0], "y": [70.0, 70.0, 70.0], "snr": [6.0, 6.0, 30.0],
                               "snr_mf": [6.0, 6.0, 30.0], "flux_ujy": [1.0, 1.0, 9.0], "sep_flag": [0, 0, 0],
                               "dot_flag": [0, 0, 0], "flags": [0, 0, 0]}),
         "streaks": pd.DataFrame({"x": [30.2], "y": [30.1], "x1": [24.0], "y1": [30.0], "x2": [36.0], "y2": [30.0],
                                  "length_px": [12.0], "theta_deg": [1.0], "snr": [9.0], "snr_local": [8.0],
                                  "why": [2 | 256], "rate_arcsec_h": [2300.0]})}
    m = I.match_points(tr, o, tr["x"].to_numpy(), tr["y"].to_numpy())
    assert not m["found"].any() and m["found_streak_any"][0] and m["dot_on_trail"][1]
    cause = I.miss_cause(pd.concat([tr, m], axis=1), 6.0)
    assert cause[0] == "streak_rejected:lumpy" and cause[1] == "pieces" and cause[2] == "blend"


def test_local_bins_edge_strip_and_clean_blocks(tmp_path):
    """edge_bin marks the EDGE_STRIP_PX strip; mf_clean reads the photo's matched-filter block scale (1 = clean) from
    the dot file's footer, and the area table splits by both."""
    import json as _json

    import pyarrow as pa
    import pyarrow.parquet as pq
    g = np.ones((32, 32))
    g[0, 0] = 1.6                                         # one wide block at the origin
    folder = "dots_x"
    (tmp_path / folder).mkdir()
    t = pa.table({"x": [1.0]}).replace_schema_metadata(
        {"skyshift_photo": _json.dumps({"mf": {"sigma_grid": [32, g.tolist()]}})})
    pq.write_table(t, tmp_path / folder / "OBSD2.parquet")
    sc = I.mf_scale_grid(tmp_path, folder, "OBS", 2)
    assert sc.shape == (32, 32) and sc[0, 0] == 1.6 and sc[5, 5] == 1.0
    assert I.mf_scale_grid(tmp_path, folder, "OBS", 3) is None
    cell = 8
    n = I.SHAPE[0] // cell + 1
    cond = {"cell": cell, "dsat": np.full((n, n), 100.0), "mask": np.zeros((n, n)), "noise_rel": np.ones((n, n))}
    dens = np.zeros((32, 32))
    b = I.local_bins(np.array([10.0, 1000.0, 2030.0]), np.array([10.0, 1000.0, 1000.0]), cond, dens, None, I.SHAPE,
                     sc)
    assert b["edge_bin"].tolist() == [1, 0, 1] and b["mf_clean"].tolist() == [False, True, True]
    a = I.area_table(cond, dens, None, I.SHAPE, sc)
    tot = a["deg2"].sum()
    edge = a.loc[a["edge_bin"] == 1, "deg2"].sum() / tot
    assert abs(edge - (1 - ((I.SHAPE[0] - 2 * I.EDGE_STRIP_PX) / I.SHAPE[0]) ** 2)) < 0.02
    assert 0 < a.loc[~a["mf_clean"], "deg2"].sum() / tot < 0.01


def test_refloor_moves_the_peaks_floor_and_the_chance_with_it():
    F = pd.DataFrame({"kind": ["point"] * 4, "peak_d": [0.2, 0.2, 5.0, 0.3], "peak_snr": [3.2, 4.4, 9.0, 3.7], "peak_why": [0] * 4,
                      "r_match": [1.0] * 4, "found": [False] * 4, "found_ext": [False] * 4,
                      "found_streak_any": [False] * 4, "dot_on_trail": [False] * 4, "c_peak_d": [0.1, 9.0, 9.0, 0.5],
                      "c_peak_snr": [3.4, 0.0, 0.0, 4.2], "c_r_match": [1.0] * 4, "c_found": [False] * 4,
                      "c_found_ext": [False] * 4, "c_found_streak_any": [False] * 4, "c_dot_on_trail": [False] * 4,
                      "c_valid": [True] * 4, "length_px": [0.0] * 4, "mf_snr_truth": [3.0] * 4})
    f4, f3 = I.refloor(F, 4.0), I.refloor(F, 3.0)
    assert f4["found_any"].tolist() == [False, True, False, False] and f3["found_any"].tolist() == [True, True, False,
                                                                                                    True]
    assert f4["c_found_any"].tolist() == [False, False, False, True] and f3["c_found_any"].sum() == 2
    assert f4["cause"][0] == "faint" and f3["cause"][0] == "peak_below"


def test_logistic_fit_recovers_the_curve():
    rng = np.random.default_rng(11)
    s = np.exp(rng.uniform(np.log(2), np.log(40), 6000))
    A, m, w, c = 0.97, np.log(5.0), 0.12, 0.05
    p = c + (1 - c) * A / (1 + np.exp(-(np.log(s) - m) / w))
    found = rng.uniform(size=s.size) < p
    r = I.logistic_fit(s, found, c)
    assert abs(r["A"] - A) < 0.02
    true50 = np.exp(m - w * np.log(A / 0.5 - 1))
    true90 = np.exp(m - w * np.log(A / 0.9 - 1))
    assert abs(r["s50"] - true50) < 0.2 and abs(r["s90"] - true90) < 0.6
    assert np.isnan(I.logistic_fit(s, rng.uniform(size=s.size) < 0.6 * p, 0.0)["s90"])   # plateau 58%: no S/N 90%


def test_movers_move_coherently_between_photos(epsf, tmp_path):
    path = synthetic_mef(tmp_path)
    l2 = open_level2(path, variance=True, epsf=True)
    cl = flat_clean(l2.data.shape, sigma=float(np.nanmedian(np.sqrt(l2.variance))))
    mv = I.make_movers(l2, cl, "S_1", 1, 1, seed=4, n=4, grid=40)
    assert len(mv.df) == 4
    a = I.mover_fakes(l2, cl, mv, "S_1", 1, 1)
    l2b = open_level2(path, variance=True, epsf=True)
    l2b.mjd_avg = float(l2.mjd_avg) + STEP
    b = I.mover_fakes(l2b, cl, mv, "S_2", 1, 1)
    ra = {f.row["mover_id"]: (f.row["ra"], f.row["dec"], f.row["step_arcsec"]) for f in a}
    for f in b:
        r0, d0, step = ra[f.row["mover_id"]]
        moved = np.hypot((f.row["ra"] - r0) * np.cos(np.radians(d0)), f.row["dec"] - d0) * 3600
        assert abs(moved - step * STEP * 86400 / I.EXPOSURE_GAP_S) < 0.05 + 0.01 * step
    # the partner detector at the same moment sees it at the same sky place
    l2p = open_level2(path, variance=True, epsf=True)
    l2p.detector = 4
    p = I.mover_fakes(l2p, cl, mv, "S_1", 4, 1)
    assert [(round(f.row["ra"], 7), round(f.row["dec"], 7)) for f in p] == \
        [(round(f.row["ra"], 7), round(f.row["dec"], 7)) for f in a]


@pytest.fixture
def cutout_session(tmp_path, monkeypatch):
    path = synthetic_mef(tmp_path)

    def load(url):
        obs, det = url.rsplit("|", 1)
        l2 = open_level2(path, variance=True, epsf=True)
        l2.zodi = None
        l2.detector = int(det)
        l2.obsid = obs
        l2.mjd_avg = float(l2.mjd_avg) + STEP * int(obs[-1])
        return l2

    monkeypatch.setattr(C, "DOTS_V2", True)
    monkeypatch.setattr(C, "SESSION_STACK", True)
    monkeypatch.setattr(C, "DOTS_V2_HOLES", C.DOTS_V2_HOLES)
    monkeypatch.setattr(D, "load_photo", load)
    photos = [{"obs_id": f"S1_0001_{k}", "detector": d, "url": f"S1_0001_{k}|{d}", "session": 5, "visit": 1,
               "mjd": 61000.0 + STEP * k, "zone": "z", "condition": "test"} for k in (1, 2) for d in (1, 4)]
    return photos


def _job(photos, out, **kw):
    j = {"name": "z_s5_p1", "zone": "z", "condition": "test", "photos": photos, "variant": "default", "round": 1,
         "grid": 24, "jitter": 3.0, "seed": 9, "neg": False, "stacks": True, "movers": False, "out": str(out),
         "root": str(out / "root")}
    j.update(kw)
    return j


def test_one_job_end_to_end_and_the_probe_changes_nothing(tmp_path, cutout_session):
    photos = cutout_session
    ra = I.run_job(_job(photos, tmp_path / "probe"))
    rb = I.run_job(_job(photos, tmp_path / "plain", probe=False))
    assert ra["folder"] == rb["folder"]
    files = sorted(p.relative_to(tmp_path / "probe" / "root") for p in (tmp_path / "probe" / "root").rglob("*.parquet"))
    assert any("_peaks" in str(f) for f in files) and any(str(f).startswith("forced") for f in files)
    for rel in files:
        pd.testing.assert_frame_equal(pd.read_parquet(tmp_path / "probe" / "root" / rel),
                                      pd.read_parquet(tmp_path / "plain" / "root" / rel), check_exact=True)
    truth = pd.read_parquet(tmp_path / "probe" / "truth" / "S1_0001_1D1.parquet")
    assert len(truth) >= 9 and {"det1_d", "mf_snr_truth", "bkg_loss", "glow_own"} <= set(truth.columns)
    # forced photometry ran at the fakes
    fo = pd.read_parquet(tmp_path / "probe" / "root" / "forced" / "S1_0001_1D1.parquet")
    assert set(truth["fake_id"]) <= set(fo["ref_id"])
    # matching: bright points are found, the controls rarely
    run_dir = tmp_path / "probe"
    r = I.match_photo((str(run_dir), ra["folder"], "S1_0001_1", 1, 6.0, {"zone": "z", "condition": "test"}))
    assert r["n"] == len(truth)
    m = pd.read_parquet(run_dir / "match" / "S1_0001_1D1.parquet")
    bright = m[(m["kind"] == "point") & (m["snr_opt"] >= 15) & (m["blend_d"] > 4)]
    if len(bright):
        assert bright["found"].mean() >= 0.8
    assert (m["c_found"] & m["c_valid"]).mean() <= 0.35
    # a negative job writes no truth, and its rows are false alarms
    rn = I.run_job(_job(photos, tmp_path / "neg", neg=True))
    assert rn["folder"] == ra["folder"] and not list((tmp_path / "neg" / "truth").glob("*.parquet"))


def test_variant_names_and_guard():
    assert I.guard_of("default") == float(C.DETECT_SAT_GUARD_PX)
    assert I.guard_of("guard10+peaks30") == 10.0
    for v in I.VARIANTS:
        assert all(b in I.VARIANTS for b in v.split("+"))
