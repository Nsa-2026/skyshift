"""Stamps (27 Nov, agent "comet-stamps"; stamps.py, stamplist.py, the dots hook): patches around listed positions
(known comets) cut by the dots worker into <folder>_stamps/, for stacking later without reading a photo again."""

import gzip
import json

import numpy as np
import pandas as pd
import pytest

import skyshift_pipeline.config as C
from skyshift_core import open_level2, pixel_to_sky
from skyshift_pipeline import dots as D
from skyshift_pipeline import stamplist as SL
from skyshift_pipeline import stamps as SP

from test_dots_extras import synthetic_mef

V2 = C.DOTS_V2_SETTINGS


def test_encode_roundtrip_and_loss():
    rng = np.random.default_rng(0)
    sigma = 0.02
    img = rng.normal(0.3, sigma, (41, 37)).astype(np.float32)
    img[3, 4] = np.nan
    img[5, 5] = 900.0  # a saturated star's peak: int32 holds it
    var = np.full(img.shape, sigma ** 2, np.float32)
    var[7, 7] = 1e3  # 1,500× the median noise
    q = sigma / C.STAMP_QUANT
    back = SP.decode(SP.encode(img, "q", q), "q", img.shape, q)
    ok = np.isfinite(img)
    assert np.isnan(back[3, 4]) and np.isfinite(back[ok]).all()
    err = back[ok] - img[ok]
    assert np.abs(err).max() <= q / 2 * 1.001 + 1e-6 * 900
    assert np.std(err) / sigma < 0.02  # 0.018σ: adds 0.016% to the noise variance
    v = SP.decode(SP.encode(var, "s16", q), "s16", var.shape, q)
    assert np.allclose(v, var, rtol=1.5e-3)
    fl = rng.integers(0, 2 ** 31 - 1, img.shape).astype(np.int32)
    assert np.array_equal(SP.decode(SP.encode(fl, "i32", q), "i32", fl.shape, q), fl)
    b = rng.random(img.shape) > 0.5
    assert np.array_equal(SP.decode(SP.encode(b, "u8", q), "u8", b.shape, q).astype(bool), b)
    w = np.linspace(1.6, 2.4, img.size).reshape(img.shape)
    assert np.abs(SP.decode(SP.encode(w, "w", q), "w", w.shape, q) - w).max() <= C.STAMP_WAVE_STEP_UM / 2 + 1e-7


def test_topocentric_parallax_and_rates():
    ra, dec, delta = np.array([45.0]), np.array([20.0]), np.array([0.5])
    u = SP._unit(ra, dec)[0]
    e, n = SP._east_north(ra, dec)
    s_km = 7000.0 * e[0]  # SPHEREx 7,000 km east of the Earth's centre
    r2, d2, rr, rd, par = SP.topocentric(ra, dec, [0.0], [0.0], delta, [60000.0], 60000.0, s_km, np.zeros(3))
    expect = 7000.0 / (0.5 * SL.AU_KM) * SL.RAD_ARCSEC
    assert par[0] == pytest.approx(expect, rel=1e-6)
    # the place moves west (away from the observer's offset), by the parallax
    east = (r2[0] - 45.0) * np.cos(np.radians(20.0)) * 3600
    assert east == pytest.approx(-expect, rel=1e-4) and abs((d2[0] - 20.0) * 3600) < 1e-3
    # the geocentric vector minus SPHEREx's: the same direction as the formula
    v = 0.5 * u - s_km / SL.AU_KM
    v /= np.linalg.norm(v)
    assert np.degrees(np.arctan2(v[1], v[0])) == pytest.approx(r2[0], abs=1e-9)
    # rates: geocentric 100″/h east; SPHEREx moving 7 km/s east adds −7/(0.5 AU) rad/s
    r2, d2, rr, rd, _ = SP.topocentric(ra, dec, [100.0], [0.0], delta, [60000.0], 60000.0, np.zeros(3), 7.0 * e[0])
    extra = -7.0 / (0.5 * SL.AU_KM) * SL.RAD_ARCSEC * 3600
    assert rr[0] == pytest.approx(100.0 + extra, rel=1e-6) and abs(rd[0]) < 1e-6
    # the time step: one hour at 100″/h east
    r3, d3, *_ = SP.topocentric(ra, dec, [100.0], [0.0], delta, [60000.0], 60000.0 + 1 / 24, None, None)
    assert (r3[0] - 45.0) * np.cos(np.radians(dec[0])) * 3600 == pytest.approx(100.0, rel=1e-4)
    # a fixed target (delta NaN) ignores SPHEREx's offset; no SPHEREx position = geocentric
    r4, d4, _, _, p4 = SP.topocentric(ra, dec, [0.0], [0.0], [np.nan], [60000.0], 60000.0, s_km, None)
    assert r4[0] == pytest.approx(45.0, abs=1e-12) and p4[0] == 0.0


def test_size_rule_and_magnitude():
    h = SL.half_px([0.67, 2.8, 0.05, 20.0], [110.0, 99.0, 500.0, 2.0])
    # 306P at 0.67 AU: a 1e5 km coma is 33 px, + 0.5 px of motion + 20 px margin; 3I and far comets: the floor; near: cap
    assert h[0] == 54 and h[1] == C.STAMP_HALF_MIN_PX and h[2] == C.STAMP_HALF_MAX_PX and h[3] == C.STAMP_HALF_MIN_PX
    assert SL.mag_pred(10.0, 4.0, 2.0, 1.0) == pytest.approx(10.0 + 10.0 * np.log10(2.0))
    assert SL.period_of("2025W34_1B_0173_2") == "2025W34_1B"


def _write_list(tmp, rows: pd.DataFrame, period: str, kind: str = "comets"):
    d = tmp / "stamplists" / kind
    d.mkdir(parents=True, exist_ok=True)
    full = SL.empty_list()
    rows = pd.concat([full, rows], ignore_index=True).astype({c: t for c, t in SL.LIST_COLUMNS.items() if t != "str"})
    rows.to_parquet(d / f"{period}.parquet", index=False)
    return d.parent


def _row(obs_id, det, ra, dec, name="C/2099 A1", delta=1.0, half=30, mjd=60000.0):
    return pd.DataFrame([{"obs_id": obs_id, "detector": det, "kind": "comet", "name": name, "mjd_ref": mjd, "ra": ra,
                          "dec": dec, "dra_arcsec_h": 20.0, "ddec_arcsec_h": -10.0, "delta_au": delta, "r_au": 1.5,
                          "phase_deg": 30.0, "mag_pred": 18.0, "sigma_arcsec": np.nan, "half_px": half,
                          "edge_deg": 0.1, "orbit_epoch_mjd": 61000.0, "orbit_file": "cometels", "h": 12.0, "g": 4.0}])


def test_photo_rows_lookup(tmp_path, monkeypatch):
    from skyshift_pipeline.store import Store
    SP._LISTS.clear()
    rows = pd.concat([_row("P1_A_0001_1", 2, 10.0, 5.0), _row("P1_A_0001_1", 3, 10.0, 5.0),
                      _row("P1_A_0001_1", 2, 10.1, 5.0, name="2P/Encke")], ignore_index=True)
    _write_list(tmp_path, rows, "P1_A")
    store = Store(str(tmp_path))
    monkeypatch.setattr(C, "STAMP_LIST_DIR", "stamplists")  # relative to the root
    got, info = SP.photo_rows(store, "P1_A_0001_1", 2)
    assert sorted(got["name"]) == ["2P/Encke", "C/2099 A1"] and info["kinds"]["comets"] == 2
    assert list(got["list_row"]) == [0, 2]
    got, info = SP.photo_rows(store, "P1_A_0002_1", 2)
    assert got.empty and info["kinds"]["comets"] == 0
    got, info = SP.photo_rows(store, "P9_Z_0001_1", 2)  # a period with no file
    assert got.empty and info["kinds"]["comets"] == "missing"
    SP._LISTS.clear()
    monkeypatch.setattr(C, "STAMP_LIST_DIR", str(tmp_path / "stamplists"))  # absolute
    got, _ = SP.photo_rows(Store(str(tmp_path / "elsewhere")), "P1_A_0001_1", 3)
    assert len(got) == 1
    monkeypatch.setattr(C, "STAMP_LIST_DIR", str(tmp_path / "nothing"))
    got, info = SP.photo_rows(store, "P1_A_0001_1", 3)
    assert got is None and info == {"list": "none"}
    SP._LISTS.clear()


def test_hook_writes_stamps_and_never_changes_dots(tmp_path, monkeypatch):
    """process_photos on the real cutout: with a list row at a place in the photo, the stamp file holds every plane at
    the predicted pixel (the place moved to SPHEREx), the dot table is the same as with stamps off, the footer says what
    was cut; a failing cut leaves the dots and puts the error in the footer; STAMPS off: no file, no footer key."""
    path = synthetic_mef(tmp_path)

    def load(url=None):
        l2 = open_level2(path, variance=True, epsf=True, zodi=True)
        if url:
            l2.detector = int(url[-1])
        return l2

    l2 = load()
    ny, nx = np.shape(l2.data)
    ra0, dec0 = pixel_to_sky(l2.wcs, [nx / 2 + 0.3], [ny / 2 - 0.2])
    edge_ra, edge_dec = pixel_to_sky(l2.wcs, [3.0], [ny / 2])
    rows = pd.concat([_row("OBS", 1, ra0[0], dec0[0], mjd=float(l2.mjd_avg) - 0.001),
                      _row("OBS", 1, edge_ra[0], edge_dec[0], name="P/edge", half=12, mjd=float(l2.mjd_avg)),
                      _row("OBS", 1, ra0[0] + 5.0, dec0[0], name="P/far"),
                      _row("OBS", 2, ra0[0], dec0[0], name="other detector")], ignore_index=True)
    lists = _write_list(tmp_path, rows, "OBS")
    SP._LISTS.clear()
    monkeypatch.setattr(C, "STAMP_LIST_DIR", str(lists))
    monkeypatch.setattr(C, "DOTS_V2", True)
    monkeypatch.setattr(D, "load_photo", load)

    def dots_of(root):
        return pd.read_parquet(root / f"{V2.folder}/OBSD1.parquet")

    monkeypatch.setattr(C, "STAMPS", False)
    r_off = tmp_path / "off"
    st = D.process_photos(str(r_off), [("OBS", 1, "u1", 0, 0)], prefetch=False)
    assert st[0]["status"] == "done" and "n_stamps" not in st[0]
    assert not (r_off / SP.stamps_rel("OBS", 1, V2.folder)).exists()
    assert "stamps" not in D.photo_meta(str(r_off / f"{V2.folder}/OBSD1.parquet"))

    monkeypatch.setattr(C, "STAMPS", True)
    r_on = tmp_path / "on"
    st = D.process_photos(str(r_on), [("OBS", 1, "u1", 0, 0)], prefetch=False)
    assert st[0]["status"] == "done" and st[0].get("n_stamps") == 2 and st[0]["stamps_bytes"] > 0, {k: v for k, v in st[0].items() if "stamp" in k or "v2" in k}
    pd.testing.assert_frame_equal(dots_of(r_on), dots_of(r_off))
    m = D.photo_meta(str(r_on / f"{V2.folder}/OBSD1.parquet"))["stamps"]
    assert m["rows"] == 3 and m["cut"] == 2 and m["off_photo"] == 1 and m["file"] == f"{V2.folder}_stamps"
    df, meta = SP.read_stamps(str(r_on / SP.stamps_rel("OBS", 1, V2.folder)))
    assert list(df["name"]) == ["C/2099 A1", "P/edge"]
    r = df.iloc[0]
    assert (r["nx"], r["ny"]) == (61, 61) and r["image"].shape == (61, 61)
    # the place at MJD-AVG from SPHEREx: the list's 1 ms earlier position moved by its rates, then the parallax
    sc_p, sc_v = SP.spacecraft(l2.header)
    ra1, dec1, *_ = SP.topocentric([ra0[0]], [dec0[0]], [20.0], [-10.0], [1.0], [float(l2.mjd_avg) - 0.001],
                                   float(l2.mjd_avg), sc_p, sc_v)
    assert r["ra_pred"] == pytest.approx(ra1[0], abs=1e-10) and r["observer"] == ("spacecraft" if sc_p is not None
                                                                                  else "geocentre")
    ix, iy = int(np.rint(r["x_pred"])), int(np.rint(r["y_pred"]))
    assert (r["x0"], r["y0"]) == (ix - 30, iy - 30)
    sl = (slice(r["y0"], r["y0"] + 61), slice(r["x0"], r["x0"] + 61))
    ok = np.isfinite(l2.data[sl])
    assert np.abs(r["image"][ok] - l2.data[sl][ok]).max() <= r["q_step"] / 2 + 1e-6
    assert np.array_equal(r["flags"], l2.flags[sl]) and np.allclose(r["variance"], l2.variance[sl], rtol=2e-3)
    assert np.allclose(r["zodi"], l2.zodi[sl], atol=r["q_step"])
    assert np.isfinite(r["resid"]).mean() > 0.5 and np.isfinite(r["wave"]).all()
    assert np.allclose(r["wave"], l2.wave.wavelength(*np.meshgrid(np.arange(r["x0"], r["x0"] + 61),
                                                                   np.arange(r["y0"], r["y0"] + 61))), atol=1e-5)
    # the stamp's WCS puts its pixels where the photo's does
    w = SP.stamp_wcs(meta, r)
    a = pixel_to_sky(w, [0.0, 60.0], [0.0, 60.0])
    b = pixel_to_sky(l2.wcs, [r["x0"], r["x0"] + 60.0], [r["y0"], r["y0"] + 60.0])
    assert np.allclose(a, b, atol=1e-9)
    e = df.iloc[1]
    assert e["x0"] == 0 and e["nx"] < 25 and e["ny"] == 25  # clipped at the photo's edge

    def boom(*a, **k):
        raise RuntimeError("no stamps today")

    monkeypatch.setattr(SP, "cut", boom)
    r_bad = tmp_path / "bad"
    st = D.process_photos(str(r_bad), [("OBS", 1, "u1", 0, 0)], prefetch=False)
    assert st[0]["status"] == "done" and "no stamps today" in st[0]["stamps_error"]
    pd.testing.assert_frame_equal(dots_of(r_bad), dots_of(r_off))
    assert "no stamps today" in D.photo_meta(str(r_bad / f"{V2.folder}/OBSD1.parquet"))["stamps"]["error"]
    assert not (r_bad / SP.stamps_rel("OBS", 1, V2.folder)).exists()
    SP._LISTS.clear()


def test_period_rows_with_kete(tmp_path):
    """stamplist on two made-up comets: the rows hold the kete position of the one inside the photo, its distances,
    magnitude and size; the other (far away) is not listed."""
    kete = pytest.importorskip("kete")
    recs = [{"Orbit_type": "P", "Year_of_perihelion": 2025, "Month_of_perihelion": 9, "Day_of_perihelion": 1.0,
             "Perihelion_dist": 1.2, "e": 0.5, "Peri": 10.0, "Node": 80.0, "i": 5.0, "Epoch_year": 2025,
             "Epoch_month": 9, "Epoch_day": 1.0, "H": 14.0, "G": 4.0, "Designation_and_name": "999P/Test"},
            {"Orbit_type": "C", "Year_of_perihelion": 2026, "Month_of_perihelion": 3, "Day_of_perihelion": 1.0,
             "Perihelion_dist": 3.0, "e": 1.0, "Peri": 200.0, "Node": 300.0, "i": 120.0, "Epoch_year": 2025,
             "Epoch_month": 9, "Epoch_day": 1.0, "Designation_and_name": "C/2099 Z9 (Far)"}]
    for f in ("cometels", "allcometels"):
        with gzip.open(tmp_path / f"{f}.json.gz", "wt") as fh:
            json.dump(recs if f == "cometels" else [], fh)
    orb = SL.comet_orbits(tmp_path)
    assert list(orb["name"]) == ["999P/Test", "C/2099 Z9"] and orb["h"].iloc[0] == 14.0 and np.isnan(orb["h"].iloc[1])
    states = SL.comet_states(orb)
    mjd = 60950.0
    jd = kete.Time.from_mjd(mjd, scaling="utc").jd
    earth = kete.spice.get_state("Earth", jd)
    st = kete.propagate_n_body(states, jd)
    v = st[0].pos - earth.pos
    eq = v.as_equatorial
    ra = float(np.degrees(np.arctan2(eq.y, eq.x)) % 360)
    dec = float(np.degrees(np.arcsin(eq.z / np.linalg.norm([eq.x, eq.y, eq.z]))))
    s = 0.8
    poly = f"POLYGON ICRS {ra - s} {dec - s} {ra + s} {dec - s} {ra + s} {dec + s} {ra - s} {dec + s}"
    photos = pd.DataFrame({"obs_id": ["T_A_0001_1"], "detector": [3], "mjd": [mjd], "s_region": [poly]})
    rows = SL.period_rows(photos, orb)
    assert list(rows.columns) == list(SL.LIST_COLUMNS) and len(rows) == 1
    r = rows.iloc[0]
    assert r["name"] == "999P/Test" and r["kind"] == "comet" and r["edge_deg"] > 0.5
    # within light time of the instantaneous place (kete moves it back by Δ/c)
    assert abs((r["ra"] - ra) * np.cos(np.radians(dec))) * 3600 < 120 and abs(r["dec"] - dec) * 3600 < 120
    assert r["mag_pred"] == pytest.approx(SL.mag_pred(14.0, 4.0, r["r_au"], r["delta_au"]), abs=1e-4)
    assert r["half_px"] == SL.half_px([r["delta_au"]], [np.hypot(r["dra_arcsec_h"], r["ddec_arcsec_h"])])[0]
    # the magnitude cut keeps comets with no H
    assert SL.period_rows(photos, orb, mag_max=5.0).empty
