"""orphans: the distant-planet route, on synthetic planet tracks (no photos, no network)."""

import json

import numpy as np
import pandas as pd
import pytest
from skyshift_core import tangent_offset_arcsec
from test_photolist import square

from skyshift_pipeline import config as C
from skyshift_pipeline import orphans as O
from skyshift_pipeline.cli import main
from skyshift_pipeline.photolist import add_sessions
from skyshift_pipeline.sky import offset_radec
from skyshift_pipeline.spectra import NCH_DET, NDET
from skyshift_pipeline.store import Store, dots_rel, photolist_rel, results_rel

ZONE = C.Zone("t_orph", 123.0, 129.0, 16.0, 22.0)
T1, T2 = 60968.0, 61139.0  # the hunt zone's two visits (Oct 2025, Mar 2026)


def planet_radec(r_au, lam0_deg, mjd, mjd0=T1, beta_deg=0.0, prograde=1.0):
    """Geocentric (barycentric Earth) direction of a body on a circular orbit of radius r_au."""
    n = prograde * 2 * np.pi / (r_au ** 1.5 * 365.25)
    lam = np.deg2rad(lam0_deg) + n * (np.asarray(mjd, np.float64) - mjd0)
    b = np.deg2rad(beta_deg)
    x, y, z = r_au * np.cos(lam) * np.cos(b), r_au * np.sin(lam) * np.cos(b), r_au * np.sin(b) * np.ones_like(lam)
    P = np.stack([x, y * np.cos(O.EPS) - z * np.sin(O.EPS), y * np.sin(O.EPS) + z * np.cos(O.EPS)], -1)
    E, _ = O.earth_posvel(mjd)
    return O.xyz_to_radec(P - E)


def side(r_au, lam0, t, err=0.005, prograde=1.0):
    """What a visit measures of the planet at time t: position, drift (″/h) and its error, Earth's state."""
    ra, dec = planet_radec(r_au, lam0, np.array([t - 0.5, t, t + 0.5]), prograde=prograde)
    de, dn = tangent_offset_arcsec(ra[1], dec[1], ra[[0, 2]], dec[[0, 2]])
    E, VE = O.earth_posvel(np.array([t]))
    return {"ra": ra[1:2], "dec": dec[1:2], "mjd": np.array([t]), "E": E, "VE": VE,
            "ve": np.array([(de[1] - de[0]) / 24.0]), "vn": np.array([(dn[1] - dn[0]) / 24.0]),
            "se": np.array([err]), "sn": np.array([err])}


@pytest.mark.parametrize("r_au", [70.0, 150.0, 300.0, 1000.0])
@pytest.mark.parametrize("prograde", [1.0, -1.0])
def test_pair_model_recovers_the_distance_of_a_planet(r_au, prograde):
    a, b = side(r_au, 125.0, T1 + 5, prograde=prograde), side(r_au, 125.0, T2 + 5, prograde=prograde)
    m = O.pair_model(a, b)
    assert abs(m["r_best"][0] / r_au - 1) < 0.05
    assert m["chi2"][0] < 10 and m["bound"][0]
    dl, db = O.ecliptic_offset(a["ra"], a["dec"], b["ra"], b["dec"])
    assert abs(db[0]) < 1.0 and 300 < abs(dl[0]) < 7000  # along ecliptic longitude, in the searched range


def test_pair_model_rejects_wrong_drifts_and_unbound_pairs():
    a, b = side(300.0, 125.0, T1 + 5), side(300.0, 125.0, T2 + 5)
    still = {**b, "ve": np.array([0.0]), "vn": np.array([0.0])}  # a still source can't be the partner
    assert O.pair_model(a, still)["chi2"][0] > 50
    off = dict(b)
    off["ra"], off["dec"] = offset_radec(b["ra"], b["dec"], 0.0, 2000.0)  # 2000″ off the ecliptic at 300 AU: unbound
    m = O.pair_model(a, off)
    assert not np.isfinite(m["chi2"][0]) or m["r_best"][0] < 100  # only a much nearer orbit could do it


def test_fit_drift_measures_a_slow_line_and_a_still_source(rng):
    t = T1 + np.repeat(np.arange(20) * 0.45, 2)
    ra0, dec0 = 126.0, 19.0
    rows = []
    for sid, (ve, vn) in ((1, (0.05, -0.02)), (2, (0.0, 0.0))):
        h = (t - t.mean()) * 24
        ra, dec = offset_radec(np.full(t.size, ra0), np.full(t.size, dec0), ve * h + rng.normal(0, 0.2, t.size),
                               vn * h + rng.normal(0, 0.2, t.size))
        rows.append(pd.DataFrame({"source_id": sid, "ra": ra, "dec": dec, "mjd": t, "pos_err_arcsec": 0.2,
                                  "session": np.repeat(np.arange(20), 2)}))
    f = O.fit_drift(pd.concat(rows, ignore_index=True)).set_index("source_id")
    assert f.loc[1, "fit_ok"] and abs(f.loc[1, "drift_e"] - 0.05) < 0.005 and abs(f.loc[1, "drift_n"] + 0.02) < 0.005
    assert f.loc[1, "n_sessions_fit"] == 20 and f.loc[1, "rate_err"] < 0.005
    assert f.loc[2, "rate"] < 0.005
    assert O.drift_ok(f["rate"], f["rate_err"]).tolist() == [True, True]  # no floor: a far planet can drift ~0.002″/h
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(C, "ORPHANS_RATE_MIN", 0.02)
        assert O.drift_ok(f["rate"], f["rate_err"]).tolist() == [True, False]  # still: inconsistent with >= 0.02″/h at 3σ
        mp.setattr(C, "ORPHANS_RATE_MAX", 0.03)
        assert O.drift_ok(f["rate"], f["rate_err"]).tolist() == [False, False]


def test_controls_shift_only_the_later_visit_inside_the_box():
    ra = np.array([359.5, 0.5, 1.0, 359.8])
    dec = np.array([10.0, 11.0, 12.0, 10.5])
    later = np.array([False, True, True, False])
    r2, d2 = O.control_shift(ra, dec, later, np.random.default_rng(0))
    assert np.allclose(r2[~later], ra[~later]) and np.allclose(d2[~later], dec[~later])
    ru = (r2 + 180) % 360 - 180
    assert (ru >= -0.5 - 1e-5).all() and (ru <= 1.0 + 1e-5).all() and (d2 >= 10.0 - 1e-5).all() and (d2 <= 12.0 + 1e-5).all()
    assert not np.allclose(d2[later], dec[later])


# --- end to end on a synthetic zone -----------------------------------------------------------------------------
def spectrum(d1: float, d4: float) -> tuple[np.ndarray, np.ndarray]:
    f = np.full(NDET * NCH_DET, np.nan, np.float32)
    f[3:9] = d1
    f[3 * NCH_DET + 3:3 * NCH_DET + 9] = d4
    return f, np.where(np.isfinite(f), np.float32(0.05 * np.nanmax([d1, d4])), np.nan).astype(np.float32)


def make_zone(tmp_path, rng):
    store = Store(str(tmp_path))
    rows = []
    for v, base in ((1, T1), (2, T2)):
        for s in range(20):
            for e in range(2):
                for det in (1, 4):
                    rows.append(dict(obs_id=f"V{v}S{s:02d}E{e}", detector=det, mjd=base + s * 0.45 + e * 2.1 / 1440,
                                     s_region=square(126.0, 19.0, half=3.5), collection="spherex_dr1", url="", s3_key="",
                                     size_bytes=1))
    photos = add_sessions(pd.DataFrame(rows))
    store.write_parquet(photolist_rel(ZONE.name), photos)
    # (source_id, visits, position(mjd) -> (ra, dec), flux D1, flux D4, gaia_n, max snr)
    lam_open = 121.3
    still = (125.0, 18.0)
    star = (125.5, 20.5)
    frag = offset_radec(*star, 8.0, 0.0)
    fast = (126.8, 18.3)
    src = [
        (101, (1,), lambda t: planet_radec(300.0, 125.0, t), 300.0, 200.0, 0, 30.0),   # the planet, visit 1
        (102, (2,), lambda t: planet_radec(300.0, 125.0, t), 300.0, 200.0, 0, 30.0),   # ... and visit 2
        (103, (1,), lambda t: planet_radec(300.0, lam_open, t), 250.0, 150.0, 0, 25.0),  # a planet whose partner left
        (201, (1,), lambda t: (np.full(len(t), still[0]), np.full(len(t), still[1])), 150.0, 100.0, 0, 15.0),  # still orphan
        (202, (1,), lambda t: (np.full(len(t), 124.5), np.full(len(t), 20.0)), 150.0, 100.0, 1, 15.0),  # Gaia star
        (203, (1, 2), lambda t: (np.full(len(t), star[0]), np.full(len(t), star[1])), 9e4, 6e4, 1, 900.0),  # bright star
        (204, (1,), lambda t: (np.full(len(t), frag[0]), np.full(len(t), frag[1])), 300.0, 200.0, 0, 12.0),  # its fragment
        (205, (1,), lambda t: (np.full(len(t), 123.02), np.full(len(t), 19.0)), 150.0, 100.0, 0, 15.0),  # zone edge
        (206, (2,), lambda t: offset_radec(np.full(len(t), fast[0]), np.full(len(t), fast[1]),  # unrelated slow mover
                                           0.3 * (t - T2 - 4) * 24, -0.1 * (t - T2 - 4) * 24), 300.0, 200.0, 0, 30.0),
    ]
    dots, members = {}, []
    for sid, visits, pos, f1, f4, _, _ in src:
        q = photos[photos["visit"].isin(visits)]
        ra, dec = pos(q["mjd"].to_numpy())
        ra, dec = offset_radec(ra, dec, rng.normal(0, 0.15, len(q)), rng.normal(0, 0.15, len(q)))
        for k, p in enumerate(q.itertuples()):
            d = dots.setdefault((p.obs_id, p.detector), [])
            members.append((sid, p.obs_id, p.detector, len(d)))
            f = f1 if p.detector == 1 else f4
            d.append(dict(mjd=p.mjd, ra=ra[k], dec=dec[k], pos_err_arcsec=np.float32(0.15), snr=np.float32(min(f / 20, 999)),
                          flux_ujy=np.float32(f)))
    for (obs, det), d in dots.items():
        store.write_parquet(dots_rel(obs, det), pd.DataFrame(d))
    store.write_parquet(results_rel(ZONE.name, "members.parquet"),
                        pd.DataFrame(members, columns=["source_id", "obs_id", "detector", "dot_row"]))
    first = {sid: pos(np.array([T1 + 4.5 if 1 in v else T2 + 4.5])) for sid, v, pos, *_ in src}
    fixed = pd.DataFrame({"source_id": [s[0] for s in src], "ra": [first[s[0]][0][0] for s in src],
                          "dec": [first[s[0]][1][0] for s in src], "snr": [s[6] for s in src],
                          "n_sessions": 20, "n_dots": 80, "flux_ujy": [s[3] for s in src]})
    store.write_parquet(results_rel(ZONE.name, "fixed.parquet"), fixed)
    store.write_parquet(results_rel(ZONE.name, "crossids.parquet"), pd.DataFrame({
        "source_id": fixed["source_id"], "gaia_n": [s[5] for s in src], "catwise_n": 0, "tmass_n": 0,
        "gaia_ref_id": ["gaia:1" if s[5] else "" for s in src], "catwise_ref_id": "", "tmass_ref_id": ""}))
    specs = [spectrum(s[3], s[4]) for s in src]
    store.write_parquet(results_rel(ZONE.name, "sources.parquet"), pd.DataFrame({
        "source_id": fixed["source_id"], "ra": fixed["ra"], "dec": fixed["dec"], "n_dots": 80, "n_sessions": 20,
        "saturated": False, "flux_ujy": [x[0] for x in specs], "err_ujy": [x[1] for x in specs]}))
    return store


def test_orphans_finds_an_injected_planet_and_counts_chance_pairs(tmp_path, rng, monkeypatch):
    store = make_zone(tmp_path, rng)
    monkeypatch.setitem(C.ZONES, ZONE.name, ZONE)
    monkeypatch.setattr(C, "ORPHANS_CONTROL_N", 10)
    assert main(["orphans", "--zone", ZONE.name, "--root", str(tmp_path), "--workers", "2"]) == 0
    o = store.read_parquet(results_rel(ZONE.name, "orphans.parquet")).set_index("source_id")
    assert 202 not in o.index and 203 not in o.index          # Gaia match; seen in both visits
    assert o.loc[204, "reason"] == "bright_fragment" and o.loc[205, "reason"] == "zone_edge"
    assert o.loc[[101, 102, 103, 201, 206], "drift_ok"].all() and abs(o.loc[201, "rate"]) < 0.01
    assert o.loc[[101, 102], "in_pair"].all() and not o.loc[[103, 201, 206], "in_pair"].any()
    assert o.loc[101, "track_in_zone"] == 1.0 and o.loc[103, "track_in_zone"] < 1.0
    rep = json.loads((tmp_path / f"results/{ZONE.name}/distant.json").read_text())
    assert len(rep["pairs"]) == 1
    p = rep["pairs"][0]
    assert (p["source_id_a"], p["source_id_b"]) == (101, 102)
    assert abs(p["distance_au"] / 300 - 1) < 0.1 and p["n_common_det"] == 2 and abs(p["flux_ratio"] - 1) < 0.05
    assert 300 < p["sep_arcsec"] < 6000 and abs(p["dlat_arcsec"]) < 0.1 * p["sep_arcsec"]
    assert rep["control"]["n"] == 10 and rep["control"]["expected_chance_pairs"] < 0.5
    assert [x["source_id"] for x in rep["open"]] == [103]
    assert rep["counts"]["orphans"] == 5 and rep["counts"]["pairs"] == 1  # 101, 102, 103, 201, 206
    summ = json.loads((tmp_path / f"results/{ZONE.name}/summary.json").read_text())["steps"]["orphans"]
    assert summ["pairs"] == 1 and "error" not in summ


def test_orphans_never_fails_the_run(tmp_path, monkeypatch):
    monkeypatch.setitem(C.ZONES, ZONE.name, ZONE)
    assert main(["orphans", "--zone", ZONE.name, "--root", str(tmp_path)]) == 0  # no tables at all
    summ = json.loads((tmp_path / f"results/{ZONE.name}/summary.json").read_text())["steps"]["orphans"]
    assert "error" in summ


def test_orphans_tags_a_known_slow_body_but_not_a_passing_asteroid(tmp_path, rng):
    store = make_zone(tmp_path, rng)
    t = T1 + np.arange(0, 9, 0.45)
    ra, dec = planet_radec(300.0, 125.0, t)  # a known TNO on planet 101's track
    de, dn = tangent_offset_arcsec(ra[:-1], dec[:-1], ra[1:], dec[1:])
    rate_e, rate_n = np.r_[de, de[-1]] / (0.45 * 24), np.r_[dn, dn[-1]] / (0.45 * 24)
    # an asteroid at 30″/h that crosses the still orphan (201, at 125.0, 18.0) exactly at one session
    ast_ra, ast_dec = offset_radec(np.full(len(t), 125.0), np.full(len(t), 18.0), 30.0 * (t - t[5]) * 24, 0.0 * t)
    pred = pd.DataFrame({
        "name": ["(90377) Sedna"] * len(t) + ["(1234) Fast"] * len(t), "number": "", "v": 20.0,
        "ra": np.r_[ra, ast_ra], "dec": np.r_[dec, ast_dec], "mjd": np.r_[t, t],
        "dra_arcsec_h": np.r_[rate_e, np.full(len(t), 30.0)], "ddec_arcsec_h": np.r_[rate_n, np.zeros(len(t))],
        "class": "TNO", "edge_px": 100.0, "obs_id": "x", "detector": 1, "session": 0})
    st = O.run(store, ZONE, 2, pred=pred)
    assert "error" not in st and st["known_orphans"] == 1
    o = store.read_parquet(results_rel(ZONE.name, "orphans.parquet")).set_index("source_id")
    assert o.loc[101, "known_name"] == "(90377) Sedna" and o.loc[101, "known_sep_arcsec"] < 5
    assert o.loc[201, "known_name"] == ""
    rep = json.loads((tmp_path / f"results/{ZONE.name}/distant.json").read_text())
    assert rep["pairs"][0]["known_a"] == "(90377) Sedna" and rep["pairs"][0]["known_b"] is None
    assert [k["source_id"] for k in rep["known"]] == [101]


# --- the pairing window (pair_window) and session-stack sources ---------------------------------------------------
@pytest.mark.parametrize("t0", [60968.0, 61050.0, 61139.0, 61230.0])
def test_pair_window_keeps_a_far_planet_near_the_ecliptic_pole(t0):
    """Visits ~30 days apart near the pole: a 1000 AU planet moves 60–220″ (the old fixed window started at 300″), in
    any direction in ecliptic coordinates (the old ecliptic-latitude cut dropped most); backwards or across: no pair."""
    mjd = np.array([t0, t0 + 30.0])
    ra, dec = planet_radec(1000.0, 125.0, mjd, beta_deg=88.5)
    de, dn = tangent_offset_arcsec(ra[0], dec[0], ra[1:], dec[1:])
    sep = float(np.hypot(de, dn)[0])
    lo, hi, ue, un = O.pair_window(ra[:1], dec[:1], mjd[:1], mjd[1:])
    assert 60 < sep < 220 and lo[0] <= sep <= hi[0] and abs(np.hypot(ue[0], un[0]) - 1) < 1e-9
    i, j = O.candidate_pairs(ra, dec, np.array([1, 2]), mjd)
    assert (i.tolist(), j.tolist()) == ([0], [1])
    for e, n in ((-de[0], -dn[0]), (dn[0], -de[0])):  # backwards; at right angles
        r2, d2 = offset_radec(ra[:1], dec[:1], np.array([e]), np.array([n]))
        i, _ = O.candidate_pairs(np.r_[ra[:1], r2], np.r_[dec[:1], d2], np.array([1, 2]), mjd)
        assert len(i) == 0


@pytest.mark.parametrize("r_au", [40.0, 70.0, 1000.0])
def test_pair_window_holds_the_hunt_cadence_from_40_to_1000_au(r_au):
    mjd = np.array([T1 + 5, T2 + 5])
    ra, dec = planet_radec(r_au, 125.0, mjd)
    i, j = O.candidate_pairs(ra, dec, np.array([1, 2]), mjd)
    assert (i.tolist(), j.tolist()) == ([0], [1])
    # a 40 AU TNO drifts up to ~2.3″/h in a visit: inside drift_ok now (ORPHANS_RATE_MAX 2.5; was 1.0)
    assert O.drift_ok(np.array([2.0]), np.array([0.02])).tolist() == [True]
    assert O.drift_ok(np.array([3.5]), np.array([0.02])).tolist() == [False]
    assert O.window_cap_arcsec(mjd) >= O.pair_window(ra[:1], dec[:1], mjd[:1], mjd[1:])[1][0]


def test_orphans_pairs_a_planet_seen_only_in_session_stacks(tmp_path, rng, monkeypatch):
    """A 600 AU planet too faint for one photo: link put its session-stack rows into fixed groups of one visit each
    (stack_members.parquet), with no dots, no spectrum and no crossid row. With SESSION_STACK on it is an orphan in both
    visits, its drift comes from the stack rows, and the pair passes; off, nothing changes."""
    from skyshift_pipeline.link import STACK_MEMBER_COLUMNS, STACK_MEMBERS_NAME
    store = make_zone(tmp_path, rng)
    monkeypatch.setitem(C.ZONES, ZONE.name, ZONE)
    monkeypatch.setattr(C, "ORPHANS_CONTROL_N", 10)
    photos = store.read_parquet(photolist_rel(ZONE.name))
    rows = []
    for sid, visit in ((301, 1), (302, 2)):
        for s, g in photos[photos["visit"] == visit].groupby("session"):
            mjd = float(g["mjd"].mean())
            ra, dec = planet_radec(600.0, 124.0, np.array([mjd]))
            ra, dec = offset_radec(ra, dec, rng.normal(0, 0.2, 1), rng.normal(0, 0.2, 1))
            for det, f in ((1, 40.0), (4, 30.0)):
                rows.append(dict(source_id=sid, key=f"k{s}D{det}", stack_row=0, session=s, visit=visit, detector=det,
                                 mjd=mjd, ra=ra[0], dec=dec[0], flux_ujy=f, snr=6.0, n_exp=2, pos_err_arcsec=0.2))
    sm = pd.DataFrame(rows)[STACK_MEMBER_COLUMNS]
    store.write_parquet(results_rel(ZONE.name, STACK_MEMBERS_NAME), sm)
    fixed = store.read_parquet(results_rel(ZONE.name, "fixed.parquet"))
    pos = sm.groupby("source_id")[["ra", "dec"]].mean()
    store.write_parquet(results_rel(ZONE.name, "fixed.parquet"), pd.concat([fixed, pd.DataFrame({
        "source_id": pos.index.to_numpy(), "ra": pos["ra"].to_numpy(), "dec": pos["dec"].to_numpy(), "snr": 6.0,
        "n_sessions": 20, "n_dots": 0, "flux_ujy": 40.0})], ignore_index=True))

    def run():
        assert main(["orphans", "--zone", ZONE.name, "--root", str(tmp_path), "--workers", "2"]) == 0
        o = store.read_parquet(results_rel(ZONE.name, "orphans.parquet")).set_index("source_id")
        rep = json.loads((tmp_path / f"results/{ZONE.name}/distant.json").read_text())
        return o, {(p["source_id_a"], p["source_id_b"]): p for p in rep["pairs"]}, rep

    monkeypatch.setattr(C, "SESSION_STACK", False)
    o, pairs, _ = run()
    assert 301 not in o.index and list(pairs) == [(101, 102)]
    monkeypatch.setattr(C, "SESSION_STACK", True)
    o, pairs, rep = run()
    assert o.loc[[301, 302], "stack_only"].all() and o.loc[[301, 302], "drift_ok"].all()
    assert o.loc[[301, 302], "in_pair"].all() and (101, 102) in pairs
    p = pairs[(301, 302)]
    assert abs(p["distance_au"] / 600 - 1) < 0.1 and p["n_common_det"] == 2 and abs(p["flux_ratio"] - 1) < 0.05
    assert rep["control"]["expected_chance_pairs"] < 0.5
