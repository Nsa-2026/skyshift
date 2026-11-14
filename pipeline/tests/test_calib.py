"""calib: pixel-phase correction and pos_err calibration on synthetic dots (no photos, no network)."""

import json

import numpy as np
import pandas as pd
import pytest
from skyshift_core import tangent_deproject, tangent_offset_arcsec

from skyshift_pipeline import calib as CB
from skyshift_pipeline import config as C
from skyshift_pipeline import motion_io as MI
from skyshift_pipeline import spectra as SP
from skyshift_pipeline.cli import main
from skyshift_pipeline.dots import DOT_COLUMNS
from skyshift_pipeline.sky import cell_of
from skyshift_pipeline.store import Store, dots_rel, photolist_rel, results_rel

ZONE = C.ZONES["pilot"]
CENTRE = (122.3, 16.1)
GAIA_EPOCH = 57388.5
# injected: measured − true, pixels, as a function of the measured phase
TRUE_X = {"sin": [-0.07, 0.01, 0.0], "cos": [0.0, 0.0, 0.0]}
TRUE_Y = {"sin": [-0.09, 0.015, -0.005], "cos": [0.005, 0.0, 0.0]}
A_TRUE, FLOOR_TRUE = 5.0, 0.15  # ″: the dots' real per-axis scatter = sqrt((5/snr)² + 0.15²)


def photo_J(i):
    a = np.deg2rad(13.0 + 40 * i)
    return 6.15 * np.array([[-np.cos(a), np.sin(a)], [np.sin(a), np.cos(a)]])


def make(rng, n_photos=6, per_photo=3000, det=2, distort=0.0):
    """Dots (one per source) of n_photos photos and the matching crossids. distort: per-photo affine WCS
    error (″ at the frame edge, random per photo and axis)."""
    dots, xid = [], []
    sid0 = 1
    for i in range(n_photos):
        J = photo_J(i)
        off = rng.normal(0, 0.3, 2)  # the photo's WCS is off as a whole
        mjd = 61000.0 + 0.3 * i
        x = rng.uniform(20, 2020, per_photo)
        y = rng.uniform(20, 2020, per_photo)
        snr = np.exp(rng.uniform(np.log(5), np.log(800), per_photo))
        sig = np.hypot(A_TRUE / snr, FLOOR_TRUE) / 6.15  # px
        xt = x - CB.shift_px(TRUE_X, CB.phase_of(x)) - rng.normal(0, 1, per_photo) * sig
        yt = y - CB.shift_px(TRUE_Y, CB.phase_of(y)) - rng.normal(0, 1, per_photo) * sig
        e, n = J @ np.vstack([x - 1020, y - 1020])
        u, v = (x - 1020) / 1020, (y - 1020) / 1020
        aff = rng.normal(0, distort, 4) if distort else np.zeros(4)
        ra, dec = tangent_deproject(*CENTRE, e + off[0] + aff[0] * u + aff[1] * v, n + off[1] + aff[2] * u + aff[3] * v)
        et, nt = J @ np.vstack([xt - 1020, yt - 1020])
        pmra, pmdec = rng.normal(0, 30, per_photo), rng.normal(0, 30, per_photo)
        dt = (mjd - GAIA_EPOCH) / 365.25
        gra, gdec = tangent_deproject(*CENTRE, et - pmra * dt / 1000, nt - pmdec * dt / 1000)
        sid = np.arange(sid0, sid0 + per_photo)
        sid0 += per_photo
        sat = np.zeros(per_photo, np.int64)
        sat[:20] = 0x2  # a few saturated: never phase-corrected
        dots.append(pd.DataFrame({
            "obs_id": f"P{i}", "detector": det, "mjd": mjd, "session": i, "visit": 1, "ra": ra, "dec": dec,
            "pos_err_arcsec": np.hypot(1.4 * 3.2 / snr, 0.25), "x": x, "y": y, "flux_ujy": 100.0, "flux_err_ujy": 1.0,
            "wavelength_um": 1.3, "bandwidth_um": 0.02, "elong": 1.1, "snr": snr, "flags": sat, "flags_peak": sat,
            "cell": cell_of(ra, dec), "source_id": sid}))
        xid.append(pd.DataFrame({"source_id": sid, "gaia_ref_id": [f"gaia:{s}" for s in sid], "gaia_n": 1,
                                 "gaia_ra": gra, "gaia_dec": gdec, "gaia_epoch_mjd": GAIA_EPOCH,
                                 "gaia_pmra": pmra, "gaia_pmdec": pmdec, "gaia_parallax": 1.0}))
    return pd.concat(dots, ignore_index=True), pd.concat(xid, ignore_index=True)


def write_store(tmp_path, dots, xid):
    store = Store(str(tmp_path))
    ph = dots.groupby(["obs_id", "detector"], as_index=False).agg(mjd=("mjd", "first"), session=("session", "first"),
                                                                    visit=("visit", "first"))
    store.write_parquet(photolist_rel("pilot"), ph)
    rows = []
    for (o, d), g in dots.groupby(["obs_id", "detector"]):
        g = g.reset_index(drop=True)
        store.write_parquet(dots_rel(o, d), g.drop(columns="source_id").astype({k: v for k, v in DOT_COLUMNS.items() if k in g}))
        rows.append(pd.DataFrame({"source_id": g["source_id"], "obs_id": o, "detector": np.int8(d), "dot_row": np.arange(len(g), dtype=np.int32)}))
    store.write_parquet(results_rel("pilot", "members.parquet"), pd.concat(rows, ignore_index=True))
    store.write_parquet(results_rel("pilot", "crossids.parquet"), xid)
    return store


def residuals(d, xid):
    x = xid.set_index("source_id").loc[d["source_id"]]
    dt = (d["mjd"].to_numpy() - GAIA_EPOCH) / 365.25
    e, n = tangent_offset_arcsec(x["gaia_ra"].to_numpy(), x["gaia_dec"].to_numpy(), d["ra"].to_numpy(), d["dec"].to_numpy())
    e, n = e - x["gaia_pmra"].to_numpy() * dt / 1000, n - x["gaia_pmdec"].to_numpy() * dt / 1000
    # remove each photo's median (the WCS offset)
    e = e - pd.Series(e).groupby(d["obs_id"].to_numpy()).transform("median").to_numpy()
    n = n - pd.Series(n).groupby(d["obs_id"].to_numpy()).transform("median").to_numpy()
    return e, n


def test_photo_jacobian_recovers_the_affine_map(rng):
    d, _ = make(rng, n_photos=1, per_photo=500)
    J = CB.photo_jacobian(d["x"], d["y"], d["ra"], d["dec"])
    assert np.allclose(J, photo_J(0), atol=0.01)  # 0.2%: the dots are projected about their median, not the centre
    assert np.isnan(CB.photo_jacobian(d["x"][:10], d["y"][:10], d["ra"][:10], d["dec"][:10])).all()


def test_fit_recovers_injected_s_curve_distortion_and_pos_err(rng):
    d, xid = make(rng, distort=0.4)
    d["photo"] = d["obs_id"].str[1:].astype(int)
    jac = np.stack([CB.photo_jacobian(g["x"], g["y"], g["ra"], g["dec"]) for _, g in d.groupby("photo")])
    cal, photos = CB.fit(d, jac, xid)
    # distortion: affine chosen by cross-validation (quadratic gains nothing), one row per photo
    assert cal["distortion"]["order"] == 1 and len(photos) == 6 and (photos["order"] == 1).all()
    cv = {r["order"]: r["heldout_scatter"] for r in cal["distortion"]["cross_validation"]}
    assert cv[1] < 0.8 * cv[0]
    tab = cal["phase"]["detectors"]["2"]
    grid = np.linspace(-0.5, 0.5, 41)
    assert np.max(np.abs(CB.shift_px(tab["x"], grid) - CB.shift_px(TRUE_X, grid))) < 0.01
    assert np.max(np.abs(CB.shift_px(tab["y"], grid) - CB.shift_px(TRUE_Y, grid))) < 0.01
    pe = cal["pos_err"]["detectors"]["2"]
    assert abs(pe["a_arcsec"] - A_TRUE) < 0.4 and abs(pe["floor_arcsec"] - FLOOR_TRUE) < 0.03
    # apply: bright stars' scatter drops to the floor; pulls ≈ 1 at every S/N
    J = jac[d["photo"].to_numpy()]
    cal["_photos"] = photos
    ce, cn = CB.photo_coefs(cal, d["obs_id"], d["detector"])
    pe, pn = CB.poly_offsets(ce, cn, d["x"], d["y"])
    ra, dec, err, ok = CB.apply(cal, d["detector"], d["x"], d["y"], d["ra"], d["dec"], d["snr"], d["flags_peak"], d["pos_err_arcsec"], J, pe, pn)
    assert not ok[d["flags_peak"].to_numpy() != 0].any() and ok[d["flags_peak"].to_numpy() == 0].all()
    e0, n0 = residuals(d, xid)
    e1, n1 = residuals(d.assign(ra=ra, dec=dec), xid)
    bright = (d["snr"].to_numpy() > 100) & (d["flags_peak"].to_numpy() == 0)
    assert CB.rob(e1[bright]) < 0.5 * CB.rob(e0[bright])
    assert CB.rob(n1[bright]) < 0.2 and CB.rob(e1[bright]) < 0.2
    for lo, hi in ((5, 15), (15, 60), (60, 1e9)):
        m = (d["snr"].to_numpy() >= lo) & (d["snr"].to_numpy() < hi) & (d["flags_peak"].to_numpy() == 0)
        pull = np.r_[e1[m] / err[m], n1[m] / err[m]]
        assert 0.9 < CB.rob(pull) < 1.1, (lo, CB.rob(pull))
    s = cal["summary"]["2"]
    assert s["pull_raw"] > 1.2 and 0.9 < s["pull_corrected"] < 1.1
    assert abs(s["pull_corrected"] - 1) < 0.1


def test_step_writes_calib_and_loaders_apply_it(tmp_path, rng):
    MI.clear_cache()
    d, xid = make(rng, n_photos=4, per_photo=2000, distort=0.3)
    store = write_store(tmp_path, d, xid)
    raw, st = MI.member_dots(store, ZONE, workers=2)
    # no calib.json: exactly the dot files' values
    assert st["calib_applied"] is False
    m = raw.merge(d[["source_id", "ra", "dec", "pos_err_arcsec"]], on="source_id", suffixes=("", "_file"))
    assert (m["ra"] == m["ra_file"]).all() and (m["dec"] == m["dec_file"]).all()
    assert (m["pos_err_arcsec"] == m["pos_err_arcsec_file"].astype(np.float32)).all()

    assert main(["calib", "--zone", "pilot", "--root", str(tmp_path), "--workers", "2"]) == 0
    cal = json.loads((tmp_path / "results/pilot/calib.json").read_text())
    assert set(cal["phase"]["detectors"]) == {"2"} and cal["photos_with_jacobian"] == 4
    assert store.exists(results_rel("pilot", CB.PHOTOS_REL))
    cor, st = MI.member_dots(store, ZONE, workers=2)
    assert st["calib_applied"] is True and st["calib_phase_corrected_dots"] == len(d) - 4 * 20
    assert st["calib_distortion_corrected_dots"] == len(d)
    e0, n0 = residuals(raw, xid)
    e1, n1 = residuals(cor, xid)
    bright = (raw["snr"].to_numpy() > 100) & (raw["flags_peak"].to_numpy() == 0)
    assert CB.rob(n1[bright]) < 0.6 * CB.rob(n0[bright])
    assert not np.allclose(cor["pos_err_arcsec"], raw["pos_err_arcsec"])
    # the cached raw frame is untouched, and calibrated=False still gives it
    raw2, st2 = MI.member_dots(store, ZONE, workers=2, calibrated=False)
    assert st2["calib_applied"] is False and (raw2["ra"].to_numpy() == raw["ra"].to_numpy()).all()
    assert (cor["flux_ujy"] == raw["flux_ujy"]).all()  # no flux model fitted on one-dot sources

    # flux corrections in the loader: a detector scale and a phase term
    cal["aperture"] = {"factor_ap": {"2": 1.05}, "factor_psf": {"2": 1.01}}
    cal["flux_phase"] = {"detectors": {"2": {"ap": {"cos_x": [0.02, 0.0], "cos_y": [0.0, 0.0], "cross": 0.0}}}}
    (tmp_path / "results/pilot/calib.json").write_text(json.dumps(cal) + " ")
    cor2, _ = MI.member_dots(store, ZONE, workers=2)
    want = raw["flux_ujy"].to_numpy(np.float64) * 1.05 * np.exp(-0.02 * np.cos(2 * np.pi * CB.phase_of(raw["x"])))
    assert np.allclose(cor2["flux_ujy"], want, rtol=1e-6)
    assert np.allclose(cor2["flux_err_ujy"] / raw["flux_err_ujy"], cor2["flux_ujy"] / raw["flux_ujy"], rtol=1e-6)


def test_spectra_loader_uses_calib_and_is_unchanged_without(tmp_path, rng):
    d, xid = make(rng, n_photos=3, per_photo=1500)
    store = write_store(tmp_path, d, xid)
    store.write_parquet(results_rel("pilot", "fixed.parquet"), d[["source_id", "ra", "dec"]])
    a, sa = SP.load_members(store, ZONE, workers=2)
    assert sa["calib_applied"] is False
    m = a.merge(d[["source_id", "ra", "dec"]], on="source_id", suffixes=("", "_file"))
    assert (m["ra"] == m["ra_file"]).all() and (m["dec"] == m["dec_file"]).all()
    assert list(a.columns) == ["source_id", "detector", "session", "visit", *SP.DOT_COLUMNS]
    assert main(["calib", "--zone", "pilot", "--root", str(tmp_path), "--workers", "2"]) == 0
    b, sb = SP.load_members(store, ZONE, workers=2)
    assert sb["calib_applied"] is True and sb["calib_phase_corrected_dots"] == len(d) - 3 * 20
    assert list(b.columns) == list(a.columns)
    e0, n0 = residuals(a.assign(mjd=a["source_id"].map(d.set_index("source_id")["mjd"]), obs_id=a["session"]), xid)
    e1, n1 = residuals(b.assign(mjd=b["source_id"].map(d.set_index("source_id")["mjd"]), obs_id=b["session"]), xid)
    snr = b["source_id"].map(d.set_index("source_id")["snr"]).to_numpy()
    assert CB.rob(n1[snr > 100]) < 0.6 * CB.rob(n0[snr > 100])


def test_calib_without_crossids_or_stars_writes_nothing(tmp_path, rng):
    MI.clear_cache()
    d, xid = make(rng, n_photos=2, per_photo=100)
    store = write_store(tmp_path, d, xid)
    assert main(["calib", "--zone", "pilot", "--root", str(tmp_path), "--workers", "2"]) == 0
    assert not store.exists(results_rel("pilot", C.CALIB_REL))
    (tmp_path / "results/pilot/crossids.parquet").unlink()
    assert CB.run(store, ZONE) == {"calib": "skipped: no crossids"}
    assert CB.load(store, ZONE) is None


def test_apply_without_jacobian_or_model_only_rescales_errors():
    cal = {"phase": {"detectors": {"1": {"x": TRUE_X, "y": TRUE_Y}}},
           "pos_err": {"detectors": {"1": {"a_arcsec": 5.0, "floor_arcsec": 0.1}}}}
    ra, dec, err, ok = CB.apply(cal, [1, 1, 3], [10.3, 10.3, 10.3], [5.2, 5.2, 5.2], [122.0] * 3, [16.0] * 3, [50.0] * 3, [0, 0, 0],
                                [0.3] * 3, np.stack([np.full((2, 2), np.nan), photo_J(0), photo_J(0)]))
    assert list(ok) == [False, True, False]  # no Jacobian; corrected; detector without a table
    assert ra[0] == 122.0 and ra[2] == 122.0 and ra[1] != 122.0
    assert err[0] == pytest.approx(np.hypot(0.1, 0.1)) and err[2] == 0.3


def test_flux_phase_fit_recovers_injected_terms(rng):
    n = 20000
    px, py = rng.uniform(-0.5, 0.5, n), rng.uniform(-0.5, 0.5, n)
    true = 0.02 * np.cos(2 * np.pi * px) + 0.01 * np.cos(2 * np.pi * py) - 0.005 * np.cos(4 * np.pi * py)
    r = 0.04 + true + rng.normal(0, 0.01, n)
    t = CB.fit_flux_phase(np.full(n, 4), px, py, r, np.ones(n, bool))["4"]
    assert t["cos_x"][0] == pytest.approx(0.02, abs=1e-3) and t["cos_y"][1] == pytest.approx(-0.005, abs=1e-3)
    assert t["const"] == pytest.approx(0.04, abs=1e-3) and t["scatter_after"] < 0.6 * t["scatter_before"]
    f = CB.flux_factor({"flux_phase": {"detectors": {"4": {"ap": t}}}}, np.full(n, 4), px + 100, py + 7)
    assert CB.rob(np.log(f) + true) < 2e-3  # the factor undoes the phase term (any whole-pixel position)


def synthetic_spectra(rng, n_src=600, gain=(1.0, 0.97, 1.0, 0.95, 0.95, 0.95), alpha=None):
    """Power-law stars f = A (λ/1µm)^α measured with a per-detector gain; spectra table + catalogue rows."""
    from skyshift_pipeline.spectra import channels
    cen = np.array([c["um_centre"] for c in channels()])
    A = np.exp(rng.uniform(np.log(2e3), np.log(5e4), n_src))
    al = rng.uniform(-2.2, -1.6, n_src) if alpha is None else np.full(n_src, alpha)
    g = np.repeat(np.array(gain), 17)
    F = A[:, None] * cen[None, :] ** al[:, None] * g[None, :]
    spec = pd.DataFrame({"source_id": np.arange(n_src), "flux_ujy": list(F.astype(np.float32)),
                         "err_ujy": list((F * 0.01).astype(np.float32)), "variable": False, "saturated": False})
    def mag(lam, off):
        return 23.9 - 2.5 * np.log10(A * lam ** al) - off
    xid = pd.DataFrame({"source_id": np.arange(n_src), "gaia_n": 1, "tmass_n": 1, "catwise_n": 1,
                        **{col: mag(c, off) for (c, col, off) in C.CALIB_AP_BANDS.values()}})
    return spec, xid, A, al


def test_detector_scale_from_anchors_and_stitching(rng):
    gain = (1.0, 0.97, 0.97, 0.95, 0.95, 0.95)
    spec, xid, A, al = synthetic_spectra(rng, gain=gain)
    # J − Ks and W1 − W2 (Vega) inside the smooth-star cuts
    xid["k"] = xid["j"] - 0.5
    xid["w2"] = xid["w1"]
    sed = CB.catalogue_sed(xid)
    assert len(sed[0]) > 0
    # restore the true SED in the anchor (the cuts above only touched the colour columns)
    _, _, lam = sed
    lnf = np.stack([np.log(A * np.exp(l) ** al) for l in lam], 1)
    sed = (pd.Index(np.arange(len(A))), lnf, lam)
    # dots: each star at random wavelengths in every detector, flux = true × gain
    rows = []
    for k, (lo, hi) in enumerate(((0.75, 1.1), (1.1, 1.6), (1.6, 2.4), (2.4, 3.8), (3.8, 4.4), (4.4, 5.0))):
        wl = rng.uniform(lo, hi, (len(A), 6))
        f = A[:, None] * wl ** al[:, None] * gain[k]
        rows.append(pd.DataFrame({"source_id": np.repeat(np.arange(len(A)), 6), "detector": k + 1,
                                  "wl": wl.ravel(), "flux": f.ravel()}))
    dd = pd.concat(rows, ignore_index=True)
    anc = CB.anchors(sed, dd["source_id"], dd["detector"].to_numpy(), dd["wl"].to_numpy(), dd["flux"].to_numpy(),
                     dd["flux"].to_numpy() * 0.96, np.ones(len(dd), bool))
    got = {(a["band"], a["detector"]): a["ratio_ap"] for a in anc}
    assert ("J", 2) in got and ("W1", 4) in got and ("W2", 6) in got
    # anchors are exact only to the log-log interpolation; here the SED is a power law, so it is exact
    assert got[("J", 2)] == pytest.approx(1 / 0.97, rel=2e-3) and got[("W1", 4)] == pytest.approx(1 / 0.95, rel=2e-3)
    sti = CB.stitches(spec)
    assert len(sti) == 5 and sti[2]["ln_upper_over_lower"] == pytest.approx(np.log(0.95 / 0.97), abs=2e-3)
    lnf, res = CB.solve_factors(anc, sti)
    assert np.allclose(np.exp(lnf), 1 / np.array(gain), rtol=3e-3)  # D1 and D5 from stitching alone
    assert max(abs(r["residual"]) for r in res) < 3e-3
    # without stitching, D1 and D5 have no constraint: they take their arm neighbours' factor in fit_fluxes
