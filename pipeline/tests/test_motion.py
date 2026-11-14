"""crossid, colours, lightcurves, astrometry, highpm on synthetic data (no photos, no network)."""

import json

import numpy as np
import pandas as pd
import pyarrow as pa
import pytest
from skyshift_core import propagate, tangent_deproject, tangent_offset_arcsec

from skyshift_pipeline import astrometry as AS
from skyshift_pipeline import colours as CO
from skyshift_pipeline import config as C
from skyshift_pipeline import crossid as XI
from skyshift_pipeline import highpm as HP
from skyshift_pipeline import lightcurves as LC
from skyshift_pipeline import motion_io as MI
from skyshift_pipeline import spectra as SP
from skyshift_pipeline.cli import main
from skyshift_pipeline.dots import DOT_COLUMNS
from skyshift_pipeline.photolist import add_sessions
from skyshift_pipeline.sky import Footprint, cell_of, offset_radec
from skyshift_pipeline.store import Store, dots_rel, photolist_rel, results_rel

from test_photolist import square

STAR = (122.30, 16.10)
ZONE = C.ZONES["pilot"]
GAIA_EPOCH = 57388.5


# --- crossid ---------------------------------------------------------------------------------
def write_refcat(store, rows):
    store.write_parquet(C.REFCAT_REL.format(zone="pilot"), pd.DataFrame(rows))


def test_crossid_moves_gaia_with_pm_and_picks_the_right_star(tmp_path):
    store = Store(str(tmp_path))
    mjd = 61000.0
    pmra, pmdec = 2000.0, -1000.0  # mas/yr: ~ 22″ between 2016 and 2025.9
    dt = (mjd - GAIA_EPOCH) / 365.25
    # the Gaia J2016 position that lands on STAR at mjd
    g0 = tangent_deproject(*STAR, -pmra * dt / 1000, -pmdec * dt / 1000)
    decoy = offset_radec(*STAR, 2.3, 0.0)   # a still Gaia star 2.3″ away: within the radius, but further
    other = offset_radec(*STAR, 60.0, 0.0)  # a second source, matched to a still star
    dtc = (mjd - 57170.0) / 365.25
    cw = tangent_deproject(*STAR, -pmra * dtc / 1000, -pmdec * dtc / 1000)
    write_refcat(store, [
        dict(ref_id="4000000000000000001", cat="gaia_dr3", ra=float(g0[0]), dec=float(g0[1]), epoch_mjd=GAIA_EPOCH,
             pmra=pmra, pmdec=pmdec, parallax=50.0, phot_g_mean_mag=12.0, bp_rp=2.5),
        dict(ref_id="4000000000000000002", cat="gaia_dr3", ra=decoy[0], dec=decoy[1], epoch_mjd=GAIA_EPOCH,
             pmra=0.0, pmdec=0.0, parallax=0.1, phot_g_mean_mag=18.0, bp_rp=1.0),
        dict(ref_id="4000000000000000003", cat="gaia_dr3", ra=other[0], dec=other[1], epoch_mjd=GAIA_EPOCH,
             pmra=5.0, pmdec=5.0, parallax=1.0, phot_g_mean_mag=15.0, bp_rp=1.2),
        dict(ref_id="cw1", cat="catwise2020", name="J082912.00+160600.0", ra=float(cw[0]), dec=float(cw[1]), epoch_mjd=57170.0,
             pmra=pmra, pmdec=pmdec, parallax=np.nan, w1mpro=9.0, w2mpro=8.5),
        dict(ref_id="tm1", cat="2mass", ra=other[0], dec=other[1], epoch_mjd=51544.0, pmra=np.nan, pmdec=np.nan,
             parallax=np.nan, j_m=10.0, h_m=9.5, k_m=9.3),
    ])
    ref = MI.load_refcat(store, ZONE)
    assert set(ref["cat"]) == {"gaia", "catwise", "tmass"}
    src = pd.DataFrame({"source_id": [1, 2], "ra": [STAR[0], other[0]], "dec": [STAR[1], other[1]]})
    out = XI.compute(src, np.array([mjd, mjd]), ref)
    a, b = out.iloc[0], out.iloc[1]
    assert a["gaia_id"] == 4000000000000000001 and a["gaia_sep_arcsec"] < 0.6  # parallax (≤ 50 mas) and epoch only
    assert a["gaia_n"] == 2 and a["gaia_g"] == 12.0 and abs(a["gaia_pmra"] - 2000) < 1e-3
    # the CatWISE entry is at its 2015.4 epoch: moved by its pm it lands on the source too
    assert a["catwise_name"] == "J082912.00+160600.0" and a["catwise_sep_arcsec"] < 0.2 and a["w2"] == 8.5
    assert b["gaia_id"] == 4000000000000000003 and b["tmass_ref_id"] == "tm1" and b["j"] == 10.0
    assert a["tmass_ref_id"] == ""
    # without the proper motion the moving star is 22″ away and the decoy would win
    still = ref.assign(pmra=0.0, pmdec=0.0, parallax=np.nan)
    assert XI.compute(src, np.array([mjd, mjd]), still).iloc[0]["gaia_id"] == 4000000000000000002


def test_crossid_without_refcat_is_empty_not_an_error(tmp_path):
    ref = MI.load_refcat(Store(str(tmp_path)), ZONE)
    out = XI.compute(pd.DataFrame({"source_id": [1], "ra": [STAR[0]], "dec": [STAR[1]]}), np.array([61000.0]), ref)
    assert out.loc[0, "gaia_id"] == -1 and out.loc[0, "gaia_ref_id"] == "" and np.isnan(out.loc[0, "gaia_sep_arcsec"])


# --- colours ---------------------------------------------------------------------------------
def test_simultaneous_colour_ignores_variability(rng):
    n = 40
    var = 1 + 0.5 * np.sin(np.arange(n))  # the star varies by ±50 % from photo to photo
    rows = []
    for k in range(n):
        for det, f in ((1, 1000.0), (4, 250.0), (2, 800.0)):  # D2 has no D5 partner: no D2D5 colour
            rows.append(dict(source_id=7, obs_id=f"O{k}", detector=det, flux_ujy=f * var[k] * (1 + rng.normal(0, 0.01)),
                             flux_err_ujy=0.01 * f * var[k], wavelength_um=0.9 if det == 1 else 3.0, flags=0))
    rows.append(dict(source_id=7, obs_id="O0", detector=4, flux_ujy=1e6, flux_err_ujy=1.0, wavelength_um=3.0, flags=1 << 10))  # HOT: out
    out = CO.compute(pd.DataFrame(rows))
    assert out["pair"].tolist() == ["D1D4"]
    r = out.iloc[0]
    assert r["n"] == n and abs(r["colour_ab"] - 2.5 * np.log10(0.25)) < 3 * r["colour_err"] + 1e-3
    assert 0.001 < r["colour_err"] < 0.01 and abs(r["ratio"] - 0.25) < 0.005
    assert r["um_a"] == pytest.approx(0.9) and r["um_b"] == pytest.approx(3.0)


# --- lightcurves -----------------------------------------------------------------------------
def steep(wl):
    """Steep inside each detector: across D1 (0.75–1.1 µm) the flux changes by a factor ~3."""
    return 2000.0 * wl ** -3.0


def lc_dots(rng, sid, variable=False, n=120):
    rows = []
    for det in (1, 2):
        lo, hi = C.SPECTRA_DETECTOR_UM[det]
        wl = rng.uniform(lo, hi, n)
        mjd = 61000 + np.sort(rng.uniform(0, 20, n)) + np.where(np.arange(n) >= n // 2, 182.0, 0.0)
        amp = 1 + (0.4 * np.sin(2 * np.pi * mjd / 7.3) if variable else 0.0)
        f = steep(wl) * amp
        e = 0.02 * steep(wl)
        rows.append(pd.DataFrame({
            "source_id": sid, "obs_id": [f"O{i}" for i in range(n)], "detector": np.int8(det), "mjd": mjd,
            "visit": np.where(np.arange(n) >= n // 2, 2, 1).astype(np.int16), "session": np.arange(n) // 3,
            "ra": STAR[0], "dec": STAR[1], "wavelength_um": wl, "flux_ujy": f + rng.normal(0, 1, n) * e, "flux_err_ujy": e,
            "flux_psf_ujy": np.nan, "flux_psf_err_ujy": np.nan, "flags": 0,
        }))
    return pd.concat(rows, ignore_index=True)


def test_lightcurve_constant_vs_variable_with_wavelength_dependent_flux(rng):
    d = pd.concat([lc_dots(rng, 1), lc_dots(rng, 2, variable=True)], ignore_index=True)
    sources = SP.compute(d.assign(flags=0))[["source_id", "flux_ujy"]]
    # forced photometry of a catalogue star (no source, no spectrum): steep and constant, some non-detections
    nf = 60
    wl = rng.uniform(0.75, 1.1, nf)
    forced = pd.DataFrame({"ref_id": "gaia:99", "mjd": 61000 + np.arange(nf) * 0.5, "detector": np.int8(1),
                           "flux_ujy": steep(wl) * 0.01 + rng.normal(0, 1, nf) * 0.5, "flux_err_ujy": 0.5,
                           "wavelength_um": wl, "detected": steep(wl) * 0.01 > 25, "visit": np.int16(1)})
    # forced photometry of source 1 via its Gaia match
    forced1 = forced.assign(ref_id="gaia:1", flux_ujy=steep(wl) * (1 + rng.normal(0, 0.02, nf)), flux_err_ujy=0.02 * steep(wl),
                            detected=True)
    crossids = pd.DataFrame({"source_id": [1, 2], "gaia_ref_id": ["gaia:1", ""], "catwise_ref_id": ["", ""], "tmass_ref_id": ["", ""]})
    pts, summ = LC.compute(d, pd.concat([forced, forced1], ignore_index=True), crossids, sources)
    assert list(pts.columns) == LC.POINT_COLUMNS
    # the catch: raw flux of the constant star scatters by a factor ~3 within one detector
    raw = d[(d["source_id"] == 1) & (d["detector"] == 1)]["flux_ujy"]
    assert raw.max() / raw.min() > 2.5
    s = summ.set_index(["source_id", "ref_id", "detector", "origin"])
    const = s.loc[(1, "", 1, "dot")]
    assert not const["variable"] and const["scatter_rel"] < 0.04 and const["chi2_nu"] < 2.5
    assert list(const["visits"]) == [1, 2] and abs(const["visit_ratio"] - 1) < 0.03
    var = s.loc[(2, "", 1, "dot")]
    assert var["variable"] and var["scatter_rel"] > 0.15 and var["amp_rel"] > 0.5
    # forced points tied to source 1 through crossids, normalised by its spectrum
    f1 = s.loc[(1, "gaia:1", 1, "forced")]
    assert f1["n"] == nf and not f1["variable"]
    # the catalogue-only star: line-in-wavelength model, constant, non-detections kept
    f99 = s.loc[(-1, "gaia:99", 1, "forced")]
    assert f99["n"] == nf and f99["n_detected"] < nf and not f99["variable"]


# --- astrometry ------------------------------------------------------------------------------
def astro_dots(rng, sid, ra, dec, pmra, pmdec, plx, visits_mjd, err=0.15, per_visit=40, photo_offsets=None, photo0=0):
    rows = []
    k = photo0
    for v, t0 in enumerate(visits_mjd, start=1):
        mjd = t0 + np.sort(rng.uniform(0, 20, per_visit))
        r, d = propagate(ra, dec, pmra, pmdec, mjd, epoch_mjd=GAIA_EPOCH, parallax=np.full(per_visit, plx))
        e = rng.normal(0, err, per_visit)
        n = rng.normal(0, err, per_visit)
        photo = np.arange(per_visit) + (v - 1) * per_visit
        if photo_offsets is not None:
            e = e + photo_offsets[photo, 0]
            n = n + photo_offsets[photo, 1]
        r, d = tangent_deproject(r, d, e, n)
        rows.append(pd.DataFrame({"source_id": sid, "ra": r, "dec": d, "mjd": mjd, "pos_err_arcsec": err, "visit": v,
                                  "photo": photo, "snr": 30.0, "x_sc_km": np.nan, "y_sc_km": np.nan, "z_sc_km": np.nan}))
    return pd.concat(rows, ignore_index=True)


VISITS3 = (61000.0, 61182.0, 61365.0)


def test_astrometry_recovers_pm_and_parallax(rng):
    d = pd.concat([astro_dots(rng, 1, *STAR, 800.0, -450.0, 120.0, VISITS3),
                   astro_dots(rng, 2, STAR[0] + 0.01, STAR[1], -300.0, 200.0, 0.0, VISITS3[:2])], ignore_index=True)
    out, st = AS.fit(d, None, frame=False)
    a, b = out.iloc[0], out.iloc[1]
    assert a["fit"] == "pm+plx" and b["fit"] == "pm" and st["with_parallax"] == 1
    for r, pmra, pmdec in ((a, 800, -450), (b, -300, 200)):
        assert abs(r["pmra"] - pmra) < 4 * r["pmra_err"] and abs(r["pmdec"] - pmdec) < 4 * r["pmdec_err"]
        assert r["pmra_err"] < 100 and r["pm_snr"] > 3
    assert abs(a["parallax"] - 120) < 4 * a["parallax_err"] and a["parallax_err"] < 40
    assert 0.6 < a["chi2_nu"] < 1.5
    # position at the fitted epoch is where the star was then
    r_, d_ = propagate(*STAR, 800.0, -450.0, a["epoch_mjd"], epoch_mjd=GAIA_EPOCH, parallax=120.0)
    e, n = tangent_offset_arcsec(r_, d_, a["ra"], a["dec"])
    assert np.hypot(e, n) < 0.2


def test_astrometry_frame_correction_and_gaia_report(rng):
    nph = 80
    offs = rng.normal(0, 0.6, (nph, 2))  # every photo's WCS off by ~0.6″
    parts, xid = [], []
    for i in range(30):  # quiet Gaia reference stars
        ra, dec = STAR[0] + 0.02 * (i % 6), STAR[1] + 0.02 * (i // 6)
        pmra, pmdec = rng.normal(0, 10, 2)
        parts.append(astro_dots(rng, 100 + i, ra, dec, pmra, pmdec, 1.0, VISITS3[:2], photo_offsets=offs))
        xid.append(dict(source_id=100 + i, gaia_id=i, gaia_ref_id=str(i), gaia_ra=ra, gaia_dec=dec, gaia_epoch_mjd=GAIA_EPOCH,
                        gaia_pmra=pmra, gaia_pmdec=pmdec, gaia_parallax=1.0, gaia_g=15.0, gaia_n=1))
    parts.append(astro_dots(rng, 1, *STAR, 500.0, 300.0, 0.0, VISITS3[:2], photo_offsets=offs))
    xid.append(dict(source_id=1, gaia_id=999, gaia_ref_id="999", gaia_ra=STAR[0], gaia_dec=STAR[1], gaia_epoch_mjd=GAIA_EPOCH,
                    gaia_pmra=500.0, gaia_pmdec=300.0, gaia_parallax=0.0, gaia_g=14.0, gaia_n=1))
    d, crossids = pd.concat(parts, ignore_index=True), pd.DataFrame(xid)
    raw, _ = AS.fit(d, crossids, frame=False)
    cor, st = AS.fit(d, crossids, frame=True)
    assert st["photos_corrected"] == nph and 0.4 < st["frame_offset_median_arcsec"] < 1.2
    t_raw, t_cor = raw.set_index("source_id").loc[1], cor.set_index("source_id").loc[1]
    assert t_cor["pmra_err"] < t_raw["pmra_err"] / 2 and t_cor["frame_corrected"]
    assert abs(t_cor["pmra"] - 500) < 4 * t_cor["pmra_err"] and abs(t_cor["pmdec"] - 300) < 4 * t_cor["pmdec_err"]
    rep = AS.report(cor, crossids)
    assert rep["matched"] == 31 and abs(rep["pmra"]["bias"]) < 40 and 0.6 < rep["pmra"]["pull_scatter"] < 1.5
    assert rep["pmra"]["scatter"] < 1.5 * rep["pmra"]["median_err"]
    assert [s["source_id"] for s in rep["fast_gaia_stars"]] == [1]
    assert sum(b["pmra"]["n"] for b in rep["by_snr"]) == 31


def test_observer_includes_spacecraft_offset():
    mjd = np.array([61000.0, 61000.0])
    xyz = AS.observer_xyz_au(mjd, np.array([[np.nan] * 3, [7000.0, 0.0, 0.0]]))
    assert abs(xyz[1, 0] - xyz[0, 0] - 7000.0 / AS.AU_KM) < 1e-12 and 0.9 < np.linalg.norm(xyz[0]) < 1.1


# --- highpm ----------------------------------------------------------------------------------
def spectrum(scale=1.0, red=1.0):
    wl = np.array([c["um_centre"] for c in SP.channels()])
    return (scale * 100 * wl ** (1.0 * red)).astype(np.float32)


def hp_sources():
    A = STAR
    B = offset_radec(*STAR, 24.0, -18.0)            # 30″ away one visit later: a planted mover
    S = offset_radec(*STAR, 300.0, 0.0)             # static, seen in both visits
    Cc = offset_radec(*STAR, 0.0, 400.0)            # single-visit, partner 20″ away but 5× brighter
    D = offset_radec(*Cc, 20.0, 0.0)
    E = offset_radec(*STAR, -400.0, 0.0)            # single-visit, partner with another spectrum shape
    F = offset_radec(*E, 0.0, 25.0)
    rows = [(1, A, 1, 61000.0, spectrum()), (2, B, 2, 61182.0, spectrum(1.1)), (3, S, -1, 61091.0, spectrum()),
            (4, Cc, 1, 61000.0, spectrum()), (5, D, 2, 61182.0, spectrum(5.0)),
            (6, E, 1, 61000.0, spectrum()), (7, F, 2, 61182.0, spectrum(1.0, red=-2.0))]
    return pd.DataFrame({"source_id": [r[0] for r in rows], "ra": [r[1][0] for r in rows], "dec": [r[1][1] for r in rows],
                         "visit": [r[2] for r in rows], "mjd": [r[3] for r in rows], "pos_err_arcsec": np.float32(0.1),
                         "flux_ujy": [r[4] for r in rows]})


def test_highpm_recovers_a_planted_mover_and_rejects_static_and_mismatched_pairs():
    src = hp_sources()
    fps = {1: [Footprint.from_s_region(square(*STAR))] * 2, 2: [Footprint.from_s_region(square(*STAR))] * 2}
    out = HP.compute(src, fps, None, None)
    assert len(out) == 1
    r = out.iloc[0]
    assert (r["source_id"], r["source_id_b"], r["method"], r["status"]) == (1, 2, "pair", "new")
    assert abs(r["sep_arcsec"] - 30.0) < 0.1 and abs(r["flux_ratio"] - 1.1) < 0.01
    assert abs(r["pmra"] - 24.0 / (182 / 365.25) * 1000) < 50 and r["pmdec"] < 0
    # A matched to Gaia → still listed, as "gaia"; the other visit didn't cover the position → nothing
    xid = pd.DataFrame({"source_id": [1], "gaia_ref_id": ["5"], "gaia_id": [5], "catwise_name": ["cw"],
                        "catwise_pmra": [r["pmra"]], "catwise_pmdec": [r["pmdec"]]})
    g = HP.compute(src, fps, xid, None).iloc[0]
    assert g["status"] == "gaia" and g["gaia_id"] == 5 and g["catwise_pm_agree"]
    far = {1: fps[1], 2: [Footprint.from_s_region(square(STAR[0] + 10, STAR[1]))] * 2}
    assert len(HP.compute(src, far, None, None)) == 0


def test_highpm_fit_route_and_gaia_recall():
    src = hp_sources()
    ast = pd.DataFrame({"source_id": [3], "ra": [src.loc[2, "ra"]], "dec": [src.loc[2, "dec"]], "epoch_mjd": [61091.0],
                        "pmra": [400.0], "pmdec": [-100.0], "pm": [412.3], "pm_snr": [12.0], "pmra_err": [30.0], "pmdec_err": [30.0]})
    xid = pd.DataFrame({"source_id": [3], "gaia_ref_id": ["77"], "gaia_id": [77], "catwise_name": [""],
                        "catwise_pmra": [np.nan], "catwise_pmdec": [np.nan]})
    out = HP.compute(src, None, xid, ast)
    fit = out[out["method"] == "fit"].iloc[0]
    assert fit["source_id"] == 3 and fit["status"] == "gaia"
    t = 61091.0
    dtyr = (t - GAIA_EPOCH) / 365.25
    g0 = tangent_deproject(src.loc[2, "ra"], src.loc[2, "dec"], -400 * dtyr / 1000, 100 * dtyr / 1000)
    refcat = pd.DataFrame({"ref_id": ["77", "78"], "cat": ["gaia", "gaia"], "ra": [float(g0[0]), 121.0], "dec": [float(g0[1]), 15.0],
                           "epoch_mjd": GAIA_EPOCH, "pmra": [400.0, 0.0], "pmdec": [-100.0, 300.0], "parallax": 10.0, "g": [11.0, 19.5]})
    rec = HP.recall(out, xid, refcat, ZONE, t)
    assert rec["gaia_fast_in_zone"] == 2 and rec["detected_as_source"] == 1 and rec["recovered"] == 1
    assert rec["recall_of_detected"] == 1.0


# --- the five steps end to end through the CLI -----------------------------------------------
def dots_df(obs_id, det, mjd, pts, wl, flux):
    ra = np.array([p[0] for p in pts]); dec = np.array([p[1] for p in pts])
    n = len(pts)
    df = pd.DataFrame({
        "obs_id": obs_id, "detector": det, "mjd": mjd, "session": 0, "visit": 0, "ra": ra, "dec": dec,
        "pos_err_arcsec": 0.3, "x": 1.0, "y": 1.0, "flux_ujy": flux, "flux_err_ujy": 5.0,
        "wavelength_um": wl, "bandwidth_um": 0.02, "elong": 1.1, "snr": np.full(n, 20.0), "flags": 0, "flags_peak": 0,
        "cell": cell_of(ra, dec),
    })
    return df.astype({k: v for k, v in DOT_COLUMNS.items() if k in df})


def test_motion_steps_end_to_end(tmp_path, rng):
    MI.clear_cache()
    store = Store(str(tmp_path))
    rows = []
    for v, base in ((1, 61000.0), (2, 61182.0)):
        for s in range(4):
            for e in range(2):
                for det in (1, 4):
                    rows.append(dict(obs_id=f"V{v}S{s}E{e}", detector=det, mjd=base + s * 0.3 + e * 2.1 / 1440,
                                     s_region=square(*STAR), collection="spherex_dr1", url="", s3_key="", size_bytes=1))
    photos = add_sessions(pd.DataFrame(rows))
    store.write_parquet(photolist_rel("pilot"), photos)
    still = offset_radec(*STAR, 60.0, 0.0)
    mover_b = offset_radec(*STAR, 20.0, 10.0)
    for p in photos.itertuples():
        pts, flux = [still], [500.0 if p.detector == 1 else 200.0]
        pts.append(STAR if p.visit == 1 else mover_b)  # a mover: 22″ between visits
        flux.append(300.0 if p.detector == 1 else 600.0)
        jit = [offset_radec(*q, *rng.normal(0, 0.2, 2)) for q in pts]
        df = dots_df(p.obs_id, p.detector, p.mjd, jit, 0.9 if p.detector == 1 else 3.0, np.array(flux))
        meta = {"schema": 1, "header": {"X_SC": 7000.0, "Y_SC": 0.0, "Z_SC": 0.0}}  # photo metadata, as `dots` writes it
        tab = pa.Table.from_pandas(df, preserve_index=False)
        store.write_table(dots_rel(p.obs_id, p.detector), tab.replace_schema_metadata({C.PHOTO_META_KEY: json.dumps(meta)}))
        store.write_parquet(f"forced/{p.obs_id}D{p.detector}.parquet", pd.DataFrame({
            "obs_id": [p.obs_id], "detector": np.int8(p.detector), "mjd": p.mjd, "ref_id": ["gaia:11"], "ra": still[0], "dec": still[1],
            "flux_psf_ujy": np.float32(flux[0] * 1.03), "flux_psf_err_ujy": np.float32(5.0), "wavelength_um": np.float32(0.9 if p.detector == 1 else 3.0),
            "flags": np.int32(0)}))
    # only the forced-photometry refcat exists: catalogue from the ref_id prefix
    store.write_parquet(C.REFCAT_FALLBACK_REL.format(zone="pilot"), pd.DataFrame([
        dict(ref_id="gaia:11", ra=still[0], dec=still[1], epoch_mjd=GAIA_EPOCH, pmra=0.0, pmdec=0.0, parallax=0.5, phot_g_mean_mag=14.0)]))
    root = ["--zone", "pilot", "--root", str(tmp_path), "--workers", "2"]
    for step in ("link", "spectra", *("crossid", "colours", "lightcurves", "astrometry", "highpm")):
        assert main([step, *root]) == 0, step
    res = lambda n: store.read_parquet(results_rel("pilot", n))  # noqa: E731
    xid, col, lc, ast, hp = (res(n) for n in ("crossids.parquet", "colours.parquet", "lightcurves.parquet",
                                              "astrometry.parquet", "highpm_candidates.parquet"))
    assert len(xid) == 3 and (xid["gaia_id"] == 11).sum() == 1
    assert set(col["pair"]) == {"D1D4"} and len(col) == 3
    c_still = col[col["source_id"] == xid.loc[xid["gaia_id"] == 11, "source_id"].iloc[0]].iloc[0]
    assert abs(c_still["colour_ab"] - 2.5 * np.log10(0.4)) < 0.01
    assert len(lc) == len(photos) * 3 and set(lc["origin"]) == {"dot", "forced"}
    sid_still = xid.loc[xid["gaia_id"] == 11, "source_id"].iloc[0]
    assert (lc.loc[lc["origin"] == "forced", "source_id"] == sid_still).all()
    assert len(ast) == 3 and (ast["fit"] == "pm").all()
    assert len(hp) == 1 and hp.iloc[0]["status"] == "new" and abs(hp.iloc[0]["sep_arcsec"] - np.hypot(20, 10)) < 1
    rep = json.loads((tmp_path / "results/pilot/highpm_report.json").read_text())
    assert rep["recall"]["gaia_fast_in_zone"] == 0
    arep = json.loads((tmp_path / "results/pilot/astrometry_report.json").read_text())
    assert arep["vs_gaia_raw"]["matched"] == 1 and arep["spacecraft_position_dots"] == len(photos) * 2
    summary = json.loads((tmp_path / "results/pilot/summary.json").read_text())
    assert {"crossid", "colours", "lightcurves", "astrometry", "highpm"} <= set(summary["steps"])
