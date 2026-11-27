"""Known-body forced photometry (27 Nov, agent "known-forced"; knownlist.py, knownforced.py, the dots hook): every
known asteroid, comet, planet and moon on a photo measured at its predicted place, into <folder>_known/."""

import numpy as np
import pandas as pd
import pytest

import skyshift_pipeline.config as C
from skyshift_core import open_level2, pixel_to_sky
from skyshift_pipeline import dots as D
from skyshift_pipeline import knownforced as KF
from skyshift_pipeline import knownlist as KL
from skyshift_pipeline.psf import psf_photometry

from test_dots_extras import synthetic_mef

V2 = C.DOTS_V2_SETTINGS


def _row(obs_id, det, ra, dec, name="(99999)", kind="asteroid", mjd=60000.0, rate=(20.0, -10.0), sigma=0.5, v=19.0,
         delta=1.2):
    return pd.DataFrame([{"obs_id": obs_id, "detector": det, "kind": kind, "name": name, "number": 99999, "cls": "MBA",
                          "mjd_ref": mjd, "ra": ra, "dec": dec, "dra_arcsec_h": rate[0], "ddec_arcsec_h": rate[1],
                          "delta_au": delta, "r_au": 2.1, "phase_deg": 20.0, "v_pred": v, "sigma_arcsec": sigma,
                          "u_param": 0, "edge_deg": 0.5, "h": 15.0, "g": 0.15}])


def _write_list(root, rows: pd.DataFrame, period: str, kind: str = "asteroids"):
    d = root / "knownlists" / kind
    d.mkdir(parents=True, exist_ok=True)
    KL.write_list(pd.concat([KL.empty_list(), rows], ignore_index=True), d / f"{period}.parquet")
    return d.parent


def test_hg_mag_matches_kete():
    kete = pytest.importorskip("kete")
    sun2obj = np.array([2.0, 0.5, 0.1])
    sun2obs = np.array([1.0, 0.0, 0.0])
    rel = sun2obj - sun2obs
    r, d = np.linalg.norm(sun2obj), np.linalg.norm(rel)
    ph = np.degrees(np.arccos(np.dot(-sun2obj, -rel) / (r * d)))
    want = kete.flux.hg_apparent_mag(kete.Vector(list(sun2obj)), kete.Vector(list(sun2obs)), 15.0, 0.15)
    assert KL.hg_mag(15.0, 0.15, r, d, ph) == pytest.approx(want, abs=0.02)
    assert np.isnan(KL.hg_mag(15.0, 0.15, 1.0, 1.0, 170.0))


def test_sigma_and_search_radius():
    s = KL.sigma_from_u([0, 5, -1, 9], [60000.0, 60000.0, 60000.0, np.nan], 60000.0 + 365.25)
    assert s[0] == pytest.approx(0.1)  # 1 yr × 1″ per 10 yr
    assert s[1] == pytest.approx(1680.0 * 0.1)
    assert np.isnan(s[2]) and s[3] == pytest.approx(648000.0)  # unknown last obs: one decade
    r, nos, big = KF.search_radius([0.1, 5.0, np.nan, 50.0])
    assert list(r) == [C.KNOWN_SEARCH_FLOOR_ARCSEC, 15.0, C.KNOWN_SEARCH_COMET_ARCSEC, C.KNOWN_SEARCH_CAP_ARCSEC]
    assert list(nos) == [False, False, True, False] and list(big) == [False, False, False, True]


def test_list_file_reads_only_its_photo(tmp_path):
    rows = pd.concat([_row(f"P1_A_{k:04d}_1", d, 10.0 + k * 1e-3, 5.0, name=f"({k})") for k in range(300)
                      for d in (1, 2)], ignore_index=True)
    p = tmp_path / "x.parquet"
    import pyarrow.parquet as pq
    old = C.KNOWN_LIST_ROW_GROUP
    try:
        C.KNOWN_LIST_ROW_GROUP = 50
        KL.write_list(rows, p)
    finally:
        C.KNOWN_LIST_ROW_GROUP = old
    assert pq.ParquetFile(p).num_row_groups == 12
    got = KL.read_photo(str(p), "P1_A_0123_1", 2)
    assert list(got["name"]) == ["(123)"] and int(got["detector"].iloc[0]) == 2
    assert list(got.columns) == list(KL.KNOWN_LIST_COLUMNS)


def test_photo_rows_lookup(tmp_path, monkeypatch):
    from skyshift_pipeline.store import Store
    rows = pd.concat([_row("P1_A_0001_1", 2, 10.0, 5.0), _row("P1_A_0001_1", 3, 10.0, 5.0)], ignore_index=True)
    lists = _write_list(tmp_path, rows, "P1_A")
    _write_list(tmp_path, _row("P1_A_0001_1", 2, 10.0, 5.0, name="2P/Encke", kind="comet"), "P1_A", "comets")
    monkeypatch.setattr(C, "KNOWN_LIST_DIR", "knownlists")
    got, info = KF.photo_rows(Store(str(tmp_path)), "P1_A_0001_1", 2)
    assert sorted(got["name"]) == ["(99999)", "2P/Encke"] and info["kinds"] == {"asteroids": 1, "comets": 1,
                                                                                "planets": "missing"}
    assert list(got["list_row"]) == [0, 1]
    monkeypatch.setattr(C, "KNOWN_LIST_DIR", str(lists))
    got, _ = KF.photo_rows(Store(str(tmp_path / "elsewhere")), "P1_A_0001_1", 3)
    assert len(got) == 1
    monkeypatch.setattr(C, "KNOWN_LIST_DIR", str(tmp_path / "nothing"))
    assert KF.photo_rows(Store(str(tmp_path)), "P1_A_0001_1", 3) == (None, {"list": "none"})


def _photo(tmp_path):
    path = synthetic_mef(tmp_path)
    l2 = open_level2(path, variance=True, epsf=True, zodi=True)
    l2, _, pre = D.mask_dark_holes(l2)
    return path, l2, D.photo_context(l2, pre=pre)


def test_fit_models_is_psf_photometry(tmp_path):
    _, l2, ctx = _photo(tmp_path)
    ny, nx = l2.data.shape
    rng = np.random.default_rng(1)
    x, y = rng.uniform(10, nx - 10, 30), rng.uniform(10, ny - 10, 30)
    ph = psf_photometry(np.asarray(l2.data), x, y, l2.epsf, noise=ctx.noise, bad=ctx.bad)
    P, ix, iy = l2.epsf.stamps(x, y, C.PSF_FIT_HALF_PX)
    f, e, c, n = KF.fit_models(np.asarray(l2.data), P, ix, iy, ctx.noise, ctx.bad, C.PSF_FIT_HALF_PX)
    assert np.allclose(f, ph.flux, equal_nan=True) and np.allclose(e, ph.flux_err, equal_nan=True)
    assert np.array_equal(n, ph.npix)


def test_smeared_flux_recovers_a_trail(tmp_path):
    """A trail injected into an empty spot: the smeared fit gives its flux, the point fit much less."""
    _, l2, ctx = _photo(tmp_path)
    ny, nx = l2.data.shape
    x0, y0, L = nx / 2 + 0.3, ny / 2 - 0.2, 12.0
    m = 49
    t = np.linspace(-0.5, 0.5, m)
    xs, ys = x0 + t * L, y0 + 0 * t
    h = C.PSF_FIT_HALF_PX + 6
    ix, iy = int(np.rint(x0)), int(np.rint(y0))
    P = l2.epsf.stamps_at(xs, ys, np.full(m, ix), np.full(m, iy), h)[0].mean(axis=0)
    F = 200.0 * float(np.nanmedian(ctx.noise))  # bright: well above the noise
    l2.data = np.array(l2.data, np.float64)
    l2.data[iy - h:iy + h + 1, ix - h:ix + h + 1] += F * P
    f, e, c, capped = KF.smeared_fluxes(l2, ctx, np.array([x0]), np.array([y0]), np.array([x0 - L / 2]),
                                        np.array([y0]), np.array([x0 + L / 2]), np.array([y0]))
    assert not capped[0]
    assert f[0] / ctx.conv == pytest.approx(F, rel=0.15)
    ph = psf_photometry(np.asarray(l2.data), np.array([x0]), np.array([y0]), l2.epsf, noise=ctx.noise, bad=ctx.bad)
    assert ph.flux[0] < 0.5 * F


def test_mf_search_finds_an_offset_source(tmp_path):
    _, l2, ctx = _photo(tmp_path)
    ny, nx = l2.data.shape
    xs, ys = nx / 2 + 2.0, ny / 2 + 1.0
    P, ix, iy = l2.epsf.stamps([xs], [ys], 4)
    F = 3000.0 * float(np.nanmedian(ctx.noise))  # far above the cutout's own stars
    sub = np.array(ctx.sub)
    sub[iy[0] - 4:iy[0] + 5, ix[0] - 4:ix[0] + 5] += F * P[0]
    ctx.sub = sub
    snr0, pk, px, py, pf = KF.mf_search(ctx, l2.epsf, np.array([nx / 2, nx / 2]), np.array([ny / 2, ny / 2]),
                                        np.array([5.0, 1.0]))
    assert pk[0] > 20 and abs(px[0] - xs) < 0.6 and abs(py[0] - ys) < 0.6
    assert pk[1] < pk[0]  # the 1 px circle does not reach it


def test_hook_writes_known_and_never_changes_other_files(tmp_path, monkeypatch):
    """process_photos on the real cutout with KNOWN_FORCED off and on: every other table identical (dots, peaks, ext,
    streaks, cond, stamps), the footer differs only by `known`; the known file has a row at a bright star (flux as its
    dot's, dot_near), an empty place (flux ~0), a place off the edge within the search radius (off_edge, NaN flux),
    and none for a place far away or on another detector; a failing measurement leaves every other file the same."""
    path = synthetic_mef(tmp_path)

    def load(url=None):
        return open_level2(path, variance=True, epsf=True, zodi=True)

    monkeypatch.setattr(C, "DOTS_V2", True)
    monkeypatch.setattr(D, "load_photo", load)
    monkeypatch.setattr(C, "KNOWN_FORCED", False)
    r_off = tmp_path / "off"
    st = D.process_photos(str(r_off), [("OBS", 1, "u1", 0, 0)], prefetch=False)
    assert st[0]["status"] == "done" and "n_known" not in st[0]
    dots = pd.read_parquet(r_off / f"{V2.folder}/OBSD1.parquet")
    bright = dots[dots["dot_flag"] == 0].sort_values("snr", ascending=False).iloc[0]
    l2 = load()
    ny, nx = np.shape(l2.data)
    e_ra, e_dec = pixel_to_sky(l2.wcs, [-2.0], [ny / 2])
    f_ra, f_dec = pixel_to_sky(l2.wcs, [nx / 2], [ny / 2])
    rng = np.random.default_rng(3)
    for _ in range(100):  # an empty place: no dot within 5 px
        ex, ey = rng.uniform(20, nx - 20), rng.uniform(20, ny - 20)
        if np.hypot(dots["x"] - ex, dots["y"] - ey).min() > 5:
            break
    m_ra, m_dec = pixel_to_sky(l2.wcs, [ex], [ey])
    mjd = float(l2.mjd_avg)
    rows = pd.concat([_row("OBS", 1, bright["ra"], bright["dec"], name="star", rate=(0.0, 0.0), delta=np.nan, mjd=mjd),
                      _row("OBS", 1, m_ra[0], m_dec[0], name="empty", mjd=mjd),
                      _row("OBS", 1, e_ra[0], e_dec[0], name="edge", sigma=np.nan, mjd=mjd),
                      _row("OBS", 1, f_ra[0] + 3.0, f_dec[0], name="far", mjd=mjd),
                      _row("OBS", 2, f_ra[0], f_dec[0], name="other det", mjd=mjd)], ignore_index=True)
    lists = _write_list(tmp_path, rows, "OBS")
    monkeypatch.setattr(C, "KNOWN_LIST_DIR", str(lists))
    monkeypatch.setattr(C, "KNOWN_FORCED", True)
    r_on = tmp_path / "on"
    st = D.process_photos(str(r_on), [("OBS", 1, "u1", 0, 0)], prefetch=False)
    assert st[0]["status"] == "done" and st[0]["n_known"] == 3 and st[0]["known_bytes"] > 0, st[0]

    def same_files(a, b):
        import pyarrow.parquet as pq
        names = sorted(p.relative_to(a) for p in a.rglob("*.parquet") if "_known/" not in str(p.relative_to(a if p.is_relative_to(a) else b)))
        assert names == sorted(p.relative_to(b) for p in b.rglob("*.parquet") if "_known/" not in str(p.relative_to(a if p.is_relative_to(a) else b)))
        for n in names:
            pd.testing.assert_frame_equal(pd.read_parquet(a / n), pd.read_parquet(b / n))
            ma = {k: v for k, v in (pq.read_schema(a / n).metadata or {}).items() if k != b"pandas"}
            mb = {k: v for k, v in (pq.read_schema(b / n).metadata or {}).items() if k != b"pandas"}
            if str(n).startswith(V2.folder + "/"):
                fa, fb = D.photo_meta(str(a / n)), D.photo_meta(str(b / n))
                fb.pop("known", None)
                for k in set(fa) | set(fb):
                    if not k.endswith("_s") and k not in ("timing", "written", "created"):
                        assert fa.get(k) == fb.get(k) or k in ("read_s", "detect_s", "forced_s"), k
            else:
                assert set(ma) == set(mb), n
        return names

    names = same_files(r_off, r_on)
    assert any("_cond" in str(n) for n in names) and any("_peaks" in str(n) for n in names), names
    meta = D.photo_meta(str(r_on / f"{V2.folder}/OBSD1.parquet"))["known"]
    assert meta["rows_list"] == 4 and meta["rows"] == 3 and meta["off_photo"] == 1 and meta["off_edge"] == 1
    df, kmeta = KF.read_known(str(r_on / KF.known_rel("OBS", 1, V2.folder)))
    assert list(df.columns) == list(KF.ROW_COLUMNS) and kmeta["flags"] == KF.KNOWN_FLAGS
    s = df.set_index("name")
    star = s.loc["star"]
    assert star["flux_psf_ujy"] == pytest.approx(bright["flux_psf_ujy"], rel=0.05)
    assert star["known_flag"] & KF.KNOWN_FLAGS["dot_near"] and star["dot_sep_arcsec"] < 1.0
    assert star["peak_snr"] > 10 and star["peak_sep_arcsec"] < C.PIXEL_ARCSEC  # pixel grid + parabola: < 1 px
    emp = s.loc["empty"]
    assert abs(emp["flux_psf_ujy"] / emp["flux_psf_err_ujy"]) < 5 and not emp["known_flag"] & KF.KNOWN_FLAGS["off_edge"]
    assert np.isfinite(emp["wavelength_um"]) and np.isfinite(emp["noise_mjysr"])
    ed = s.loc["edge"]
    assert ed["known_flag"] & KF.KNOWN_FLAGS["off_edge"] and ed["known_flag"] & KF.KNOWN_FLAGS["no_sigma"]
    assert np.isnan(ed["flux_psf_ujy"]) and ed["edge_px"] < 0 and ed["search_arcsec"] == C.KNOWN_SEARCH_COMET_ARCSEC

    def boom(*a, **k):
        raise RuntimeError("no known today")

    monkeypatch.setattr(KF, "measure", boom)
    r_bad = tmp_path / "bad"
    st = D.process_photos(str(r_bad), [("OBS", 1, "u1", 0, 0)], prefetch=False)
    assert st[0]["status"] == "done" and "no known today" in st[0]["known_error"]
    same_files(r_off, r_bad)
    assert not (r_bad / KF.known_rel("OBS", 1, V2.folder)).exists()


def test_asteroid_rows_with_kete(tmp_path):
    """knownlist on two made-up asteroids: the one in the photo gets its kete place, distances and H-G V; the other
    (far away) no row."""
    kete = pytest.importorskip("kete")
    orb = pd.DataFrame({"desig": ["2099 AA1", "2099 ZZ9"], "g_phase": [0.15, np.nan], "h_mag": [16.0, 18.0],
                        "group_name": ["MBA", "MBA"], "peri_dist": [2.0, 2.5], "ecc": [0.1, 0.05],
                        "incl": [5.0, 25.0], "lon_node": [80.0, 250.0], "peri_arg": [10.0, 100.0],
                        "peri_time": [2460900.5, 2461100.5], "epoch": [2460950.5, 2460950.5],
                        "arc_len": [1000.0, 10.0], "name": [None, None]})
    snap = tmp_path / "mpcorb.parquet"
    orb.to_parquet(snap)
    o = KL.asteroid_orbits(snap)
    assert list(o["o_name"]) == ["2099 AA1", "2099 ZZ9"] and list(o["u_param"]) == [-1, -1]
    from skyshift_pipeline import known_mpc as KM
    st = KM.build_states(orb)
    mjd = 60950.0
    jd = kete.Time.from_mjd(mjd, scaling="utc").jd
    earth = kete.spice.get_state("Earth", jd)
    s0 = kete.propagate_n_body(st, jd)[0]
    eq = (s0.pos - earth.pos).as_equatorial
    ra = float(np.degrees(np.arctan2(eq.y, eq.x)) % 360)
    dec = float(np.degrees(np.arcsin(eq.z / np.linalg.norm([eq.x, eq.y, eq.z]))))
    s = 0.8
    poly = f"POLYGON ICRS {ra - s} {dec - s} {ra + s} {dec - s} {ra + s} {dec + s} {ra - s} {dec + s}"
    photos = pd.DataFrame({"obs_id": ["T_A_0001_1"], "detector": [2], "mjd": [mjd], "s_region": [poly], "s_ra": [ra],
                           "s_dec": [dec]})
    rows = KL.asteroid_rows(photos, o)
    pd.testing.assert_frame_equal(KL.asteroid_rows(photos, o, prefilter=True), rows)  # the test lists' prefilter
    assert list(rows.columns) == list(KL.KNOWN_LIST_COLUMNS) and len(rows) == 1
    r = rows.iloc[0]
    assert r["name"] == "2099 AA1" and r["kind"] == "asteroid" and r["cls"] == "MBA" and r["number"] == -1
    assert abs((r["ra"] - ra) * np.cos(np.radians(dec))) * 3600 < 120 and abs(r["dec"] - dec) * 3600 < 120
    assert r["v_pred"] == pytest.approx(KL.hg_mag(16.0, 0.15, r["r_au"], r["delta_au"], r["phase_deg"]), abs=1e-3)
    assert np.isnan(r["sigma_arcsec"]) and r["edge_deg"] > 0.5


def test_planet_rows_with_a_stub_table():
    """planet_rows: a stub Horizons table puts Saturn and Titan on one photo of two; rows only there."""
    mjd = 60950.0
    poly = "POLYGON ICRS 9 -1 11 -1 11 1 9 1"
    far = "POLYGON ICRS 100 -1 102 -1 102 1 100 1"
    photos = pd.DataFrame({"obs_id": ["A_1", "B_1"], "detector": [1, 1], "mjd": [mjd, mjd], "s_region": [poly, far],
                           "s_ra": [10.0, 101.0], "s_dec": [0.0, 0.0]})

    def table(ra):
        t = np.array([mjd - 1, mjd + 1]) + KL.MJD_TO_JD
        return pd.DataFrame({"jd": t, "ra": [ra, ra + 0.01], "dec": [0.1, 0.1], "dra_arcsec_h": [1.5, 1.5],
                             "ddec_arcsec_h": [0.0, 0.0], "v": [0.5, 0.5], "delta_au": [9.0, 9.0]})

    calls = []

    def fetch(naif, j0, j1, step):
        calls.append(naif)
        return table(10.0 if naif in (699, 606) else 50.0)

    coarse = {p: table(10.0 if p == "Saturn" else 200.0) for p in C.PLANET_SYSTEMS}
    st: dict = {}
    rows = KL.planet_rows(photos, coarse, fetch=fetch, stats=st)
    assert set(rows["name"]) == {"Saturn", "Titan"} and set(rows["obs_id"]) == {"A_1"}
    assert dict(zip(rows["name"], rows["kind"])) == {"Saturn": "planet", "Titan": "moon"}
    assert st["horizons_queries"] == 1 + len(C.PLANET_SYSTEMS["Saturn"]["moons"]) and rows["delta_au"].iloc[0] == 9.0
