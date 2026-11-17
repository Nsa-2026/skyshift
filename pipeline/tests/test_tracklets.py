"""tracklets: movers of one visit linked across sessions, per-mover rates, recovered dots, score's recall with them."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from skyshift_pipeline import config as C
from skyshift_pipeline import tracklets as T
from skyshift_pipeline.link import nonfixed_dots_table
from skyshift_pipeline.score import recall_report, summarize_units
from skyshift_pipeline.sky import cell_of
from skyshift_pipeline.store import Store, photolist_rel, results_rel

ZONE = C.Zone("trk", 120.0, 126.0, 15.0, 21.0)
T0 = 60950.0
ACC = (-0.026, 0.004)  # ″/h², the hunt's first visit
# (ra, dec at T0, rate east ″/h, rate north ″/h): main-belt-like, crossing each other
ASTEROIDS = [(121.0, 16.0, 40.0, 10.0), (123.0, 18.0, -25.0, 5.0), (122.0, 17.5, 15.0, -30.0), (124.5, 16.5, 70.0, 20.0),
             (121.5, 19.5, 30.0, -12.0), (125.0, 20.0, -45.0, -15.0)]


def session_times(rng) -> np.ndarray:
    """22 sessions over ~9 days, gaps 2-20 h (hours from T0)."""
    return np.cumsum(np.r_[0.0, rng.uniform(2.0, 20.0, 21)])


def position(k: int, t_h: np.ndarray, acc=ACC) -> tuple[np.ndarray, np.ndarray]:
    ra0, dec0, vx, vy = ASTEROIDS[k]
    x = vx * t_h + 0.5 * acc[0] * t_h ** 2
    y = vy * t_h + 0.5 * acc[1] * t_h ** 2
    return T.tan_radec(ra0, dec0, x, y)


def scene(seed: int = 3, noise_per_session: int = 25, drop: dict | None = None):
    """(movers, photos, truth per mover: asteroid index or -1). drop: {asteroid: [session numbers]} left out."""
    rng = np.random.default_rng(seed)
    ts = session_times(rng)
    drop = drop or {}
    rows, photos = [], []
    for s, th in enumerate(ts, start=1):
        for e in range(3):
            photos.append({"obs_id": f"S{s}E{e}", "detector": 1, "mjd": T0 + (th + e * 2.1 / 60) / 24, "session": s, "visit": 1})
        tm = th + 2.1 / 60  # the movers' mean time
        for k in range(len(ASTEROIDS)):
            if s in drop.get(k, []):
                continue
            ra, dec = position(k, np.array([tm]))
            snr = 8.0 + 4 * k
            err = float(T.mover_sigma(snr, 2)) * 0.6
            rows.append({"ra": float(ra[0]) + rng.normal(0, err) / 3600 / np.cos(np.deg2rad(dec[0])),
                         "dec": float(dec[0]) + rng.normal(0, err) / 3600, "mjd": T0 + tm / 24, "session": s, "visit": 1,
                         "n_dots": 2, "snr": snr, "truth": k})
        for _ in range(noise_per_session):
            rows.append({"ra": rng.uniform(ZONE.ra_min, ZONE.ra_max), "dec": rng.uniform(ZONE.dec_min, ZONE.dec_max),
                         "mjd": T0 + (tm + rng.uniform(-0.03, 0.03)) / 24, "session": s, "visit": 1, "n_dots": 2,
                         "snr": rng.uniform(6, 30), "truth": -1})
    m = pd.DataFrame(rows)
    m["cell"] = cell_of(m["ra"].to_numpy(), m["dec"].to_numpy())
    m["session"], m["visit"] = m["session"].astype(np.int32), m["visit"].astype(np.int16)
    return m, pd.DataFrame(photos), ts


def test_tangent_plane_round_trip():
    ra, dec = np.array([120.3, 359.9, 0.2]), np.array([15.0, -30.0, 60.0])
    x, y = T.tan_xy(ra + 0.5, dec - 0.3, ra, dec)
    r2, d2 = T.tan_radec(ra + 0.5, dec - 0.3, x, y)
    assert np.allclose(r2, ra % 360, atol=1e-9) and np.allclose(d2, dec, atol=1e-9)
    x, y = T.tan_xy(120.0, 15.0, 120.0, 15.0 + 10 / 3600)
    assert x == pytest.approx(0, abs=1e-6) and y == pytest.approx(10.0, rel=1e-6)


def test_tracks_found_pure_with_rates():
    m, photos, _ = scene()
    tr, rec, st = T.build(m.drop(columns="truth"), photos, ZONE, None)
    truth = m.sort_values(["visit", "session", "mjd", "ra", "dec"], kind="stable")["truth"].to_numpy()
    lab = tr["tracklet_id"].to_numpy()
    for k, (_, _, vx, vy) in enumerate(ASTEROIDS):
        on = lab[truth == k]
        assert (on >= 0).all() and np.unique(on).size == 1, f"asteroid {k}: {on}"
        assert set(truth[lab == on[0]]) == {k}  # pure
        r = tr[lab == on[0]]
        assert r["n_sessions"].iloc[0] == 22 and r["n_movers"].iloc[0] == 22
        # rate at T0 + t: |v + a t|; compare at each mover's time
        th = (r["mjd"].to_numpy() - T0) * 24
        want = np.hypot(vx + ACC[0] * th, vy + ACC[1] * th)
        assert np.allclose(r["rate_arcsec_h"], want, atol=0.5)
        assert (r["rate_snr"] > 50).all() and (r["track_chi2_nu"] < 3).all()
        pa = np.rad2deg(np.arctan2(vx + ACC[0] * th, vy + ACC[1] * th)) % 360
        assert np.allclose((r["pa_deg"] - pa + 180) % 360 - 180, 0, atol=2)
    assert (lab[truth < 0] < 0).all()  # no noise mover on a track
    assert st["tracklets_by_sessions"] == {"22": len(ASTEROIDS)}
    assert st["control"]["tracks"] == 0
    assert st["visits"]["1"]["accel_tracks"] == len(ASTEROIDS) and np.allclose(st["visits"]["1"]["accel_arcsec_h2"], ACC, atol=0.004)
    # unlinked movers: n_sessions 1, no rate
    assert (tr.loc[lab < 0, "n_sessions"] == 1).all() and tr.loc[lab < 0, "rate_arcsec_h"].isna().all()
    assert list(tr.columns) == T.TRACK_COLUMNS and tr["mover_id"].is_unique
    assert tr["mover_id"].iloc[0].startswith("trk-")


def test_two_sessions_are_no_track_and_gaps_are_bridged():
    m, photos, ts = scene(drop={0: list(range(3, 23)), 1: [5, 6, 7, 8]})  # asteroid 0: 2 sessions only; 1: a ~gap
    tr, _, _ = T.build(m.drop(columns="truth"), photos, ZONE, None, control=False)
    truth = m.sort_values(["visit", "session", "mjd", "ra", "dec"], kind="stable")["truth"].to_numpy()
    lab = tr["tracklet_id"].to_numpy()
    assert (lab[truth == 0] < 0).all()
    assert C.TRACK_SEED_MAX_GAP_H < ts[8] - ts[3] <= C.TRACK_MAX_GAP_H  # 41 h without asteroid 1: one track across it
    on = lab[truth == 1]
    assert np.unique(on).size == 1 and (on >= 0).all() and on.size == 18


def test_recovery_finds_the_missing_sighting_only():
    m, photos, ts = scene(drop={2: [10]})
    s = 10
    th = ts[s - 1] + 2.1 / 60
    ra, dec = position(2, np.array([th]))
    fd = pd.DataFrame({
        "ra": [ra[0] + 0.6 / 3600, ra[0] + 60 / 3600, 122.9], "dec": [dec[0] - 0.5 / 3600, dec[0], 19.0],
        "mjd": [T0 + th / 24] * 3, "session": np.int32(s), "visit": np.int16(1), "obs_id": ["S10E1"] * 3,
        "detector": np.int8(1), "dot_row": np.arange(3, dtype=np.int32), "snr": np.float32(7.0),
        "flux_ujy": np.float32(50.0), "flags": np.int32(0), "flags_peak": np.int32(0), "elong": np.float32(1.1),
        "near_fixed": False})
    tr, rec, st = T.build(m.drop(columns="truth"), photos, ZONE, fd)
    assert len(rec) == 1 and rec["dot_row"].iloc[0] == 0 and rec["session"].iloc[0] == s
    assert rec["sep_arcsec"].iloc[0] < 2.0 and list(rec.columns) == T.DOT_COLUMNS
    tid = rec["tracklet_id"].iloc[0]
    assert (tr.loc[tr["tracklet_id"] == tid, "n_recovered_sessions"] == 1).all()
    assert st["recovered_dots"] == 1 and st["tracklets_with_recovery"] == 1

def test_load_free_dots_options(tmp_path, monkeypatch):
    store = Store(str(tmp_path))
    fd = pd.DataFrame({"ra": [1.0, 2.0, 3.0], "dec": 0.0, "near_fixed": [True, False, False], "elong": [1.0, 1.0, 4.0]})
    assert T.load_free_dots(store, ZONE) is None
    store.write_parquet(results_rel(ZONE.name, T.FREE_DOTS), fd)
    monkeypatch.setattr(C, "TRACK_RECOVER_NEAR_FIXED", True)
    assert T.load_free_dots(store, ZONE)["ra"].tolist() == [1.0, 2.0]  # elongated: never
    monkeypatch.setattr(C, "TRACK_RECOVER_NEAR_FIXED", False)
    assert T.load_free_dots(store, ZONE)["ra"].tolist() == [2.0]
    monkeypatch.setattr(C, "TRACK_RECOVER_NEAR_FIXED", True)
    store.write_parquet(results_rel(ZONE.name, T.FREE_DOTS), fd.assign(in_fixed=[True, False, False]))
    assert T.load_free_dots(store, ZONE)["ra"].tolist() == [1.0, 2.0]
    monkeypatch.setattr(C, "TRACK_RECOVER_IN_FIXED", False)
    assert T.load_free_dots(store, ZONE)["ra"].tolist() == [2.0]  # off-star member dots left out


def test_run_writes_tables_and_score_counts_recovered(tmp_path):
    m, photos, ts = scene(drop={2: [10]})
    store = Store(str(tmp_path))
    store.write_parquet(photolist_rel(ZONE.name), photos)
    store.write_parquet(results_rel(ZONE.name, "movers.parquet"), m.drop(columns="truth"))
    st = T.run(store, ZONE)  # no nonfixed_dots.parquet: tracks only
    assert st["nonfixed_dots"] is None and store.exists(results_rel(ZONE.name, "tracklets.parquet"))
    assert len(store.read_parquet(results_rel(ZONE.name, "tracklet_dots.parquet"))) == 0
    # score: a unit found only by a recovered dot counts in recall, not in recall_movers
    p = pd.DataFrame({"unit": [0, 0, 1, 2], "name": ["a", "a", "b", "c"], "number": [1, 1, 2, 3], "v": [14.0, 14.0, 15.0, 13.0],
                      "mjd": [1.0, 1.001, 2.0, 3.0], "session": [1, 1, 2, 3]})
    f = np.array([False, False, False, True])
    u = summarize_units(p, f, f, np.zeros(4, bool), np.zeros(4, bool), np.array([True, False, False, False]), np.zeros(4, bool))
    rep = recall_report(u)
    assert rep["n_found"] == 2 and rep["n_found_movers"] == 1
    assert rep["recall"] == pytest.approx(2 / 3, abs=1e-4) and rep["recall_movers"] == pytest.approx(1 / 3, abs=1e-4)
    assert rep["control_recall"] == 0.0 and rep["per_object"]["found"] == 2
    u_old = summarize_units(p, f, f, np.zeros(4, bool), np.zeros(4, bool))  # without tracklets: as before
    assert recall_report(u_old)["recall"] == recall_report(u_old)["recall_movers"]


def test_nonfixed_dots_table():
    dots = pd.DataFrame({"ra": [121.0, 121.0 + 5 / 3600, 121.5, 121.6, 121.7], "dec": [16.0] * 5,
                         "mjd": [T0] * 5, "session": np.int32(1), "visit": np.int16(1), "photo": [0, 0, 0, 1, 1],
                         "dot_row": np.arange(5, dtype=np.int32), "detector": np.int8(1), "snr": 9.0, "flux_ujy": 80.0,
                         "flags": [0, 0, 1 << 31, 0, 0], "flags_peak": 0, "elong": [1.0, 1.0, 1.2, 5.0, np.nan]})
    sid = np.array([7, -1, -1, -1, -1])
    fixed = pd.DataFrame({"source_id": [7], "ra": [121.0], "dec": [16.0]})
    photos = pd.DataFrame({"obs_id": ["A", "B"], "detector": [1, 1]})
    fd = nonfixed_dots_table(dots, sid, fixed, photos)
    assert fd["dot_row"].tolist() == [1, 2, 4]  # member and elongated dots left out, NaN elong kept
    assert fd["near_fixed"].tolist() == [True, False, False]
    assert fd["obs_id"].astype(str).tolist() == ["A", "A", "B"] and fd["flags"].iloc[1] < 0  # bit 31 as int32


def test_no_movers(tmp_path):
    store = Store(str(tmp_path))
    _, photos, _ = scene()
    store.write_parquet(photolist_rel(ZONE.name), photos)
    store.write_parquet(results_rel(ZONE.name, "movers.parquet"), pd.DataFrame())
    st = T.run(store, ZONE)
    assert st["movers"] == 0 and st["tracks"] == 0
    tr = store.read_parquet(results_rel(ZONE.name, "tracklets.parquet"))
    assert len(tr) == 0 and list(tr.columns) == T.TRACK_COLUMNS


def test_time_budget(monkeypatch):
    m, photos, _ = scene()
    monkeypatch.setattr(C, "TRACK_VISIT_MAX_S", -1.0)
    tr, _, st = T.build(m.drop(columns="truth"), photos, ZONE, None, control=False)
    assert st["tracks"] == 0 and st["visits"]["1"]["seeds_skipped_time"] > 0 and (tr["tracklet_id"] < 0).all()


def test_failure_leaves_empty_tables(tmp_path, monkeypatch):
    store = Store(str(tmp_path))
    m, photos, _ = scene()
    store.write_parquet(photolist_rel(ZONE.name), photos)
    store.write_parquet(results_rel(ZONE.name, "movers.parquet"), m.drop(columns="truth"))

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(T, "build", boom)
    st = T.run(store, ZONE)
    assert "boom" in st["error"]
    assert len(store.read_parquet(results_rel(ZONE.name, "tracklet_dots.parquet"))) == 0
    assert list(store.read_parquet(results_rel(ZONE.name, "tracklets.parquet")).columns) == T.TRACK_COLUMNS


def test_fast_movers_of_two_sessions_link_when_their_own_motions_agree(monkeypatch):
    """fast_tracks: two 400″/h movers of sessions 4 and 5 whose chord agrees with both own velocities (link's rate and PA
    within the session) make a 2-session track; a fast mover on that line in session 6 moving the other way doesn't join;
    the slow asteroids keep their tracks; the control (sessions moved apart) has no fast track."""
    m, photos, ts = scene(noise_per_session=10)
    m = m.assign(rate_arcsec_h=np.nan, pa_deg=np.nan, n_exposures=2)
    rows = []
    th = {s: ts[s - 1] + 2.1 / 60 for s in (4, 5, 6)}
    for s, pa, truth in ((4, 90.0, 10), (5, 90.0, 10), (6, 270.0, 11)):
        ra, dec = T.tan_radec(122.5, 18.5, np.array([400.0 * (th[s] - th[4])]), np.array([0.0]))
        rows.append({"ra": float(ra[0]), "dec": float(dec[0]), "mjd": T0 + th[s] / 24, "session": s, "visit": 1, "n_dots": 3,
                     "snr": 20.0, "truth": truth, "rate_arcsec_h": 400.0, "pa_deg": pa, "n_exposures": 3})
    f = pd.DataFrame(rows)
    f["cell"] = cell_of(f["ra"].to_numpy(), f["dec"].to_numpy())
    m = pd.concat([m, f.astype({"session": np.int32, "visit": np.int16})], ignore_index=True)
    tr, _, st = T.build(m.drop(columns="truth"), photos, ZONE, None)
    truth = m.sort_values(["visit", "session", "mjd", "ra", "dec"], kind="stable")["truth"].to_numpy()
    lab = tr["tracklet_id"].to_numpy()
    on = lab[truth == 10]
    assert (on >= 0).all() and np.unique(on).size == 1 and set(truth[lab == on[0]]) == {10}
    r = tr[lab == on[0]]
    assert (r["n_sessions"] == 2).all() and np.allclose(r["rate_arcsec_h"], 400.0, rtol=0.01)
    assert (lab[truth == 11] < 0).all() and st["fast_tracks"] == 1 and st["control"]["fast_tracks"] == 0
    assert st["tracklets_by_sessions"]["22"] == len(ASTEROIDS)
    monkeypatch.setattr(C, "TRACK_FAST_RATE_MAX_ARCSEC_H", None)
    tr, _, st = T.build(m.drop(columns="truth"), photos, ZONE, None, control=False)
    assert (tr["tracklet_id"].to_numpy()[truth == 10] < 0).all() and st["fast_tracks"] == 0


def test_recovery_marks_dots_from_inside_a_fixed_group():
    """A dot that link left in a star's group but flagged off its star (nonfixed_dots `in_fixed`) is recovered on a
    track's path like any free dot, and counted apart."""
    m, photos, ts = scene(drop={2: [10]})
    th = ts[9] + 2.1 / 60
    ra, dec = position(2, np.array([th]))
    fd = pd.DataFrame({
        "ra": [ra[0] + 0.6 / 3600, 122.9], "dec": [dec[0] - 0.5 / 3600, 19.0], "mjd": [T0 + th / 24] * 2,
        "session": np.int32(10), "visit": np.int16(1), "obs_id": ["S10E1"] * 2, "detector": np.int8(1),
        "dot_row": np.arange(2, dtype=np.int32), "snr": np.float32(7.0), "flux_ujy": np.float32(50.0), "flags": np.int32(0),
        "flags_peak": np.int32(0), "elong": np.float32(1.1), "near_fixed": [True, False], "in_fixed": [True, False]})
    _, rec, st = T.build(m.drop(columns="truth"), photos, ZONE, fd)
    assert len(rec) == 1 and bool(rec["in_fixed"].iloc[0]) and st["recovered_in_fixed"] == 1
    assert st["control"]["recovered_in_fixed"] == 0


# --- session stacks: free stack rows join the recovery (SESSION_STACK on) -----------------------------------------
def stack_fd(m_ts, picks):
    """nonfixed_dots rows as link writes them for session-stack rows: picks [(asteroid, session)] on their paths at the
    session's mean time, plus one stack row far from any track."""
    rows = []
    for n, (k, s) in enumerate(picks):
        th = m_ts[s - 1] + 2.1 / 60  # the photos' mean time (3 photos 2.1 min apart)
        ra, dec = position(k, np.array([th]))
        rows.append({"ra": ra[0] + 0.4 / 3600, "dec": dec[0] + 0.3 / 3600, "mjd": T0 + th / 24, "session": s,
                     "stack_key": f"stack{s}", "stack_row": n})
    rows.append({"ra": 122.9, "dec": 19.0, "mjd": T0 + (m_ts[9] + 2.1 / 60) / 24, "session": 10, "stack_key": "stack10",
                 "stack_row": 9})
    fd = pd.DataFrame(rows)
    n = len(fd)
    return fd.assign(session=fd["session"].astype(np.int32), visit=np.int16(1), obs_id="", detector=np.int8(1),
                     dot_row=np.full(n, -1, np.int32), snr=np.float32(6.0), flux_ujy=np.float32(20.0), flags=np.int32(0),
                     flags_peak=np.int32(0), elong=np.float32(1.0), near_fixed=False, in_fixed=False,
                     stack_row=fd["stack_row"].astype(np.int32))


def test_recovery_takes_session_stack_rows_where_the_track_does_not_smear(monkeypatch):
    """A faint asteroid seen only in a session's stack (link's free stack rows in nonfixed_dots) joins its track as a
    recovered dot at the session's mean time, with its stack key and row; where the track moves more than
    TRACK_RECOVER_STACK_SMEAR_ARCSEC over the session (the stack smeared it), only real dots count."""
    monkeypatch.setattr(C, "SESSION_STACK", True)
    m, photos, ts = scene(drop={2: [10], 3: [12]})
    fd = stack_fd(ts, [(2, 10), (3, 12)])  # 34″/h and 73″/h: 2.3″ and 5.1″ over a 4.2-min session
    _, rec, st = T.build(m.drop(columns="truth"), photos, ZONE, fd)
    assert list(rec.columns) == T.DOT_COLUMNS + T.STACK_DOT_COLUMNS
    assert sorted(rec["stack_key"].tolist()) == ["stack10", "stack12"] and (rec["dot_row"] == -1).all()
    assert (rec["obs_id"] == "").all() and (rec["sep_arcsec"] < 2.0).all()
    assert st["nonfixed_stack_rows"] == 3 and st["recovered_stack_rows"] == 2
    assert st["control"]["recovered_stack_rows"] == 0
    monkeypatch.setattr(C, "TRACK_RECOVER_STACK_SMEAR_ARCSEC", 3.0)
    _, rec, st = T.build(m.drop(columns="truth"), photos, ZONE, fd)
    assert rec["stack_key"].tolist() == ["stack10"] and st["recovered_stack_rows"] == 1


def test_load_free_dots_keeps_stack_rows_only_when_switched_on(tmp_path, monkeypatch):
    m, photos, ts = scene()
    real = pd.DataFrame({"ra": [121.0], "dec": [16.0], "mjd": [T0], "session": np.int32(1), "visit": np.int16(1),
                         "obs_id": ["S1E0"], "detector": np.int8(1), "dot_row": np.int32(4), "snr": np.float32(7.0),
                         "flux_ujy": np.float32(50.0), "flags": np.int32(0), "flags_peak": np.int32(0),
                         "elong": np.float32(1.1), "near_fixed": False, "in_fixed": False, "stack_key": "",
                         "stack_row": np.int32(-1)})
    store = Store(str(tmp_path))
    store.write_parquet(results_rel(ZONE.name, T.FREE_DOTS), pd.concat([real, stack_fd(ts, [(2, 10)])], ignore_index=True))
    monkeypatch.setattr(C, "SESSION_STACK", False)
    off = T.load_free_dots(store, ZONE)
    assert len(off) == 1 and "stack_row" not in off and off["dot_row"].tolist() == [4]
    _, rec, st = T.build(m.drop(columns="truth"), photos, ZONE, off)
    assert list(rec.columns) == T.DOT_COLUMNS and "recovered_stack_rows" not in st  # switched off: as before
    monkeypatch.setattr(C, "SESSION_STACK", True)
    on = T.load_free_dots(store, ZONE)
    assert len(on) == 3 and int(T.stack_rows(on).sum()) == 2


def test_local_fit_keeps_four_nearest_beyond_window():
    t = np.array([-80.0, -50.0, -25.0, -5.0, 0.0])
    vx, vy, ax, ay = 35.0, -10.0, -0.025, 0.008
    x = vx * t + 0.5 * ax * t ** 2
    y = vy * t + 0.5 * ay * t ** 2
    s = np.full(t.size, 0.4)
    # 52 h beyond t=0: only t=0 is inside 48 h; taking the 4 nearest keeps deg=2 quadratic accuracy
    f = T.local_fit(t, x, y, s, t0=40.0)
    assert f.deg == 2 and f.n == 4
    px, py = f.at(40.0)
    assert float(np.hypot(px[0] - (vx * 40.0 + 0.5 * ax * 1600.0), py[0] - (vy * 40.0 + 0.5 * ay * 1600.0))) < 0.1


def test_wide_session_gaps_link_and_recover_within_12_arcsec():
    # Sessions with a 44 h triplet step (> 36 h, <= 60 h) and an 85 h grow gap (> 60 h, <= 120 h) on a curved path
    # (ACC), plus a missing detection 50 h past the track at 9.5″ from the predicted path (<= 12″).
    ts = np.array([0.0, 44.0, 62.0, 74.0, 159.0, 209.0])
    vx, vy, acc = 32.0, 12.0, (-0.015, 0.002)
    ra0, dec0 = 122.0, 17.0
    photos, movers = [], []
    for s, th in enumerate(ts, start=1):
        for e in range(2):
            photos.append({"obs_id": f"W{s}E{e}", "detector": 1, "mjd": T0 + (th + e * 2.1 / 60) / 24,
                           "session": s, "visit": 1})
        if s <= 5:
            ra, dec = T.tan_radec(ra0, dec0, np.array([vx * th + 0.5 * acc[0] * th ** 2]),
                                  np.array([vy * th + 0.5 * acc[1] * th ** 2]))
            movers.append({"ra": float(ra[0]), "dec": float(dec[0]), "mjd": T0 + th / 24, "session": np.int32(s),
                           "visit": np.int16(1), "n_dots": 2, "snr": 15.0, "cell": int(cell_of(ra, dec)[0])})
    p_df = pd.DataFrame(photos)
    m_df = pd.DataFrame(movers)
    th6 = ts[5]
    pr, pd_ = T.tan_radec(ra0, dec0, np.array([vx * th6 + 0.5 * acc[0] * th6 ** 2 + 9.5]),
                          np.array([vy * th6 + 0.5 * acc[1] * th6 ** 2]))
    fd = pd.DataFrame({
        "ra": [float(pr[0])], "dec": [float(pd_[0])], "mjd": [T0 + th6 / 24], "session": np.int32(6),
        "visit": np.int16(1), "obs_id": ["W6E0"], "detector": np.int8(1), "dot_row": np.int32(0),
        "snr": np.float32(10.0), "flux_ujy": np.float32(80.0), "flags": np.int32(0), "flags_peak": np.int32(0),
        "elong": np.float32(1.1), "near_fixed": [False], "in_fixed": [False]})
    tr, rec, st = T.build(m_df, p_df, ZONE, fd)
    assert (tr["tracklet_id"] == 0).all() and len(tr) == 5
    assert len(rec) == 1 and 9.0 <= float(rec["sep_arcsec"].iloc[0]) <= 10.0
    assert st["control"]["tracks"] == 0 and st["control"]["recovered_dots"] == 0


def test_off_star_dot_at_2p1_arcsec_recovered():
    from skyshift_pipeline.sky import offset_radec

    m, photos, ts = scene(drop={2: [10]})
    th = ts[9] + 2.1 / 60
    pra, pdec = position(2, np.array([th]))
    star_ra, star_dec = offset_radec(float(pra[0]), float(pdec[0]), 2.1, 0.0)
    fixed = pd.DataFrame({"source_id": [7], "ra": [star_ra], "dec": [star_dec]})
    dots = pd.DataFrame([
        {"ra": star_ra, "dec": star_dec, "mjd": T0 + ts[0] / 24, "session": np.int32(1), "visit": np.int16(1),
         "photo": 0, "detector": 1, "dot_row": 0, "snr": 9.0, "flux_ujy": 60.0, "flags": 0, "flags_peak": 0, "elong": 1.0},
        {"ra": star_ra, "dec": star_dec, "mjd": T0 + ts[1] / 24, "session": np.int32(2), "visit": np.int16(1),
         "photo": 3, "detector": 1, "dot_row": 0, "snr": 9.0, "flux_ujy": 60.0, "flags": 0, "flags_peak": 0, "elong": 1.0},
        {"ra": float(pra[0]), "dec": float(pdec[0]), "mjd": T0 + th / 24, "session": np.int32(10), "visit": np.int16(1),
         "photo": 27, "detector": 1, "dot_row": 0, "snr": 9.0, "flux_ujy": 60.0, "flags": 0, "flags_peak": 0, "elong": 1.0},
    ])
    fd = nonfixed_dots_table(dots, np.array([7, 7, 7]), fixed, photos)
    assert len(fd) == 1 and bool(fd["in_fixed"].iloc[0])
    _, rec, st = T.build(m.drop(columns="truth"), photos, ZONE, fd)
    assert len(rec) == 1 and bool(rec["in_fixed"].iloc[0]) and st["recovered_in_fixed"] == 1
    assert st["control"]["recovered_dots"] == 0

