import numpy as np
import pandas as pd
import pytest
from scipy.stats import norm

from skyshift_pipeline import classify as CL
from skyshift_pipeline import config as C
from skyshift_pipeline import indices as IX
from skyshift_pipeline.cli import main
from skyshift_pipeline.spectra import to_arrow
from skyshift_pipeline.store import Store, results_rel

NS = 600  # noisy realisations per test
REL_ERR = 0.02


def power_law(alpha=-1.5, f0=1000.0):
    return f0 * IX.CENTRE ** alpha


def noisy(model, rng, n=NS, rel_err=REL_ERR):
    err = np.broadcast_to(rel_err * np.abs(model), (n, model.size)).copy()
    return model + rng.normal(size=err.shape) * err, err


def in_window(a, b):
    return (IX.CENTRE >= a) & (IX.CENTRE <= b)


# --- indices -----------------------------------------------------------------------------------
@pytest.mark.parametrize("name", list(C.INDEX_WINDOWS))
def test_index_is_zero_on_smooth_spectra(name, rng):
    kind, band, cont = C.INDEX_WINDOWS[name]
    key = f"{name}_{'depth' if kind == 'abs' else 'excess'}"
    for alpha in (-2.0, -0.5, 0.0, 1.0):  # Rayleigh-Jeans to rising
        f, e = noisy(power_law(alpha), rng)
        out = IX.all_indices(f, e)
        sig = out[f"{name}_sig"]
        assert abs(np.mean(sig)) < 0.15, (alpha, np.mean(sig))
        assert 0.85 < np.std(sig) < 1.15, (alpha, np.std(sig))
        assert abs(np.median(out[key])) < 0.01
        assert np.mean(np.abs(sig) >= C.INDEX_SIG) < 0.01


@pytest.mark.parametrize("name", list(C.INDEX_WINDOWS))
def test_index_recovers_injected_band(name, rng):
    kind, band, cont = C.INDEX_WINDOWS[name]
    depth = 0.15
    model = power_law(-1.0)
    inj = model * np.where(in_window(*band), 1 - depth if kind == "abs" else 1 + depth, 1.0)
    # expected value and significance: the index of the noiseless spectrum, with the noise's errors
    err0 = REL_ERR * np.abs(inj)
    exp_val, exp_sig = IX.index(inj[None], err0[None], kind, band, cont)
    assert 0.5 * depth < exp_val[0] <= 1.05 * depth  # partial edge channels dilute a little, never more
    assert exp_sig[0] > 4  # 15% at 2% per channel: the narrow PAH band and extrapolated CO continuum are the weakest
    f, e = noisy(inj, rng)
    val, sig = IX.index(f, e, kind, band, cont)
    assert abs(np.mean(sig) - exp_sig[0]) < 0.15 + 0.03 * exp_sig[0]
    assert 0.8 < np.std(sig) < 1.25
    assert abs(np.median(val) - exp_val[0]) < 0.01
    # the detection rate at INDEX_SIG is what a unit Gaussian around the expected significance gives
    assert abs(np.mean(sig >= C.INDEX_SIG) - norm.cdf(exp_sig[0] - C.INDEX_SIG)) < 0.06


def test_index_is_nan_without_coverage_and_window_errors():
    f = power_law()[None].copy()
    e = 0.02 * f
    f[0, in_window(2.9, 3.2)] = np.nan
    v, s = IX.index(f, e, *C.INDEX_WINDOWS["h2o_ice_305"][:1], *C.INDEX_WINDOWS["h2o_ice_305"][1:])
    assert np.isnan(v[0]) and np.isnan(s[0])
    # a window's error is sqrt(Σ w² σ²) / Σ w: flat spectrum, n full channels -> σ / √n
    flat = np.full((1, 102), 100.0)
    lo, hi = IX.LO[52], IX.HI[55]  # exactly channels 52-55
    m, err, lam = IX.window_mean(flat, np.full((1, 102), 4.0), lo, hi)
    assert np.isclose(m[0], 100) and np.isclose(err[0], 2.0) and IX.LO[52] < lam[0] < IX.HI[55]


def test_scaled_windows_follow_redshift(rng):
    """PAH 3.3 µm emission at z = 0.1 is found with scale 1.1 and not at scale 1."""
    kind, band, cont = C.INDEX_WINDOWS["pah_33"]
    model = power_law(-1.0) * np.where(in_window(band[0] * 1.1, band[1] * 1.1), 1.3, 1.0)
    f, e = noisy(model, rng, n=50)
    _, s1 = IX.index(f, e, kind, band, cont, scale=np.full(50, 1.1))
    _, s0 = IX.index(f, e, kind, band, cont)
    assert np.median(s1) > 7 and np.median(s0) < 3


# --- classification ----------------------------------------------------------------------------
PSF_SIGMA = 3.2
R_AP = (6.0, 15.0)  # arcsec, the two synthetic apertures


def gauss_frac(r, sigma):
    return 1 - np.exp(-r * r / (2 * sigma * sigma))


def shape_dots(rng, sid, sigma_gal, n=30, snr=30.0):
    """Dots of a source whose profile is a Gaussian of σ_gal (0 = point source) seen through the PSF:
    second-moment size, aperture fluxes from the Gaussian curve of growth, PSF-fit flux and χ²."""
    det = rng.integers(1, 7, n)
    psf = PSF_SIGMA * (1 + 0.05 * (det - 1))  # the PSF grows with wavelength: the locus is per detector
    sig = np.sqrt(psf ** 2 + sigma_gal ** 2)
    noise = rng.normal(1, 1 / snr, (5, n))
    tot = 1000.0
    ap1 = tot * gauss_frac(R_AP[0], sig) * noise[0]
    ap2 = tot * gauss_frac(R_AP[1], sig) * noise[1]
    psf_flux = tot * (psf / sig) ** 2 * noise[2]  # a PSF fit to a broader profile catches its core
    chi2 = (1 + 0.02 * snr * (sigma_gal / psf) ** 2 * 10) * noise[3]
    return pd.DataFrame({"source_id": sid, "detector": det.astype(np.int8), "snr": snr,
                         "a_arcsec": 0.6 * sig * noise[4], "b_arcsec": 0.6 * sig * noise[4],
                         "psf_chi2_nu": chi2, "flux_ujy": ap1, "flux_psf_ujy": psf_flux,
                         "flux_ap6_ujy": ap1, "flux_ap15_ujy": ap2})


def sources_frame(sids, model=None, saturated=None):
    model = power_law(-2.0) if model is None else model
    n = len(sids)
    return pd.DataFrame({
        "source_id": np.asarray(sids, np.int64), "ra": 122.3 + 0.01 * np.arange(n), "dec": np.full(n, 16.1),
        "saturated": np.zeros(n, bool) if saturated is None else saturated,
        "flux_ujy": [model.astype(np.float32)] * n, "err_ujy": [(0.02 * model).astype(np.float32)] * n,
        "n": [np.full(102, 3, np.int16)] * n,
    })


def no_gaia(sids):
    return pd.DataFrame({"source_id": sids, **{c: np.nan for c in CL.GAIA_COLS}})


def test_star_versus_extended_profiles(rng):
    stars = list(range(1, 81))
    gals = list(range(81, 101))
    dots = pd.concat([shape_dots(rng, s, 0.0) for s in stars] + [shape_dots(rng, s, 3.0) for s in gals], ignore_index=True)
    sids = np.array(stars + gals, np.int64)
    shape = CL.shape_features(dots, sids)
    st, ga = shape.iloc[:80], shape.iloc[80:]
    assert np.median(st["size_ratio"]) == pytest.approx(1, abs=0.02)
    assert (ga["size_ratio"] > 1.2).all() and (ga["cog_ratio"] > 1.1).all()
    assert (st["extended_score"] < C.CLASSIFY_EXT_STAR).mean() > 0.95
    assert (ga["extended_score"] > C.CLASSIFY_EXT_GALAXY).all()
    assert (shape["n_shape_dots"] == 30).all()

    gaia = no_gaia(sids)
    gaia.loc[:79, ["gaia_sep_arcsec", "gaia_g"]] = [0.3, 15.0]  # stars matched in Gaia, no parallax given
    gaia.loc[0, ["gaia_parallax", "gaia_parallax_error"]] = [10.0, 0.5]
    out = CL.classify_frame(sources_frame(sids), shape, gaia)
    cls = out["class"].to_numpy()
    assert (cls[80:] == "galaxy").all()
    assert (cls[:80] == "star").mean() > 0.95
    # point-like without Gaia: unsure; parallax wins over shape
    gaia2 = no_gaia(sids)
    gaia2.loc[80, ["gaia_sep_arcsec", "gaia_g", "gaia_parallax", "gaia_parallax_error"]] = [0.2, 16.0, 5.0, 0.2]
    out2 = CL.classify_frame(sources_frame(sids), shape, gaia2)
    assert out2["class"].iloc[80] == "star"
    assert (out2["class"].iloc[1:80] == "unsure").mean() > 0.95


def test_no_shape_columns_gives_nan_features():
    sids = np.array([1, 2], np.int64)
    dots = pd.DataFrame({"source_id": [1, 2], "detector": [1, 1], "snr": [30.0, 30.0], "flux_ujy": [1.0, 2.0]})
    sh = CL.shape_features(dots, sids)
    assert sh["extended_score"].isna().all() and (sh["n_shape_dots"] == 0).all()
    assert CL.shape_features(None, sids)["extended_score"].isna().all()


def t_dwarf_spectrum():
    """A cartoon T dwarf (µJy): Y/J/H/K peaks, CH4 at 1.6–1.8 and 2.2+ and 3.0–3.8, H2O at 1.4 and 1.9,
    bright at 4–4.2 and 4.5–5 (the W2 peak)."""
    lam = IX.CENTRE
    f = np.full(lam.size, 20.0)
    for c, w, a in ((1.27, 0.06, 120.0), (1.58, 0.04, 70.0), (2.1, 0.06, 35.0), (4.1, 0.15, 300.0), (4.7, 0.3, 450.0)):
        f += a * np.exp(-0.5 * ((lam - c) / w) ** 2)
    f *= np.where((lam > 3.15) & (lam < 3.6), 0.25, 1.0)
    return f


def test_brown_dwarf_like_flag():
    sids = np.array([1, 2, 3, 4], np.int64)
    td = t_dwarf_spectrum()
    src = pd.concat([sources_frame([1, 2], td), sources_frame([3], power_law(-2.0)),
                     sources_frame([4], td, saturated=np.array([True]))], ignore_index=True)
    gaia = no_gaia(sids)
    gaia.loc[1, ["gaia_sep_arcsec", "gaia_g"]] = [0.5, 12.0]  # source 2 is bright in Gaia: not a brown dwarf
    gaia.loc[2, ["gaia_sep_arcsec", "gaia_g"]] = [0.5, 12.0]
    out = CL.classify_frame(src, CL.shape_features(None, sids), gaia)
    assert out["brown_dwarf_like"].tolist() == [True, False, False, False]
    assert "ch4_33" in out["bd_evidence"].iloc[0] and "ch4_16" in out["bd_evidence"].iloc[0]
    assert out["ch4_33_depth"].iloc[0] > 0.5 and out["ch4_33_sig"].iloc[0] > 20
    assert out["j_m46_ab"].iloc[0] > 0 and out["j_m46_ab"].iloc[2] < -2
    assert out["bd_evidence"].iloc[2] == ""
    # an A0-like star: AB magnitudes from the window means
    assert np.isclose(out["mag_ab_j"].iloc[2], 23.9 - 2.5 * np.log10(1000 * 1.24 ** -2.0), atol=0.02)


# --- Gaia inputs and the CLI -------------------------------------------------------------------
def test_gaia_from_crossids_wide_and_long():
    wide = pd.DataFrame({"source_id": [5, 6], "gaia_sep_arcsec": [0.4, np.nan], "phot_g_mean_mag": [14.0, np.nan],
                         "parallax": [3.0, np.nan], "parallax_error": [0.1, np.nan]})
    g = CL.gaia_from_crossids(wide)
    assert g["gaia_g"].tolist()[0] == 14.0 and np.isnan(g["gaia_bp_rp"]).all()
    long = pd.DataFrame({"source_id": [5, 5, 6], "catalog": ["gaia_dr3", "catwise", "gaia_dr3"],
                         "sep_arcsec": [0.4, 0.2, 1.5], "gaia_g": [14.0, np.nan, 18.0]})
    g = CL.gaia_from_crossids(long)
    assert g.set_index("source_id")["gaia_g"].to_dict() == {5: 14.0, 6: 18.0}
    assert CL.gaia_from_crossids(pd.DataFrame({"source_id": [1], "catwise_w1": [12.0]})) is None


def test_cli_classify_and_photoz_with_refcat_and_no_members(tmp_path, monkeypatch):
    from skyshift_pipeline import photoz as PZ

    store = Store(str(tmp_path))
    um, tmpl, _ = PZ.load_templates()
    M = PZ.model_grid(PZ.z_grid(), um, tmpl)
    src = sources_frame([10, 11, 12])
    src.at[1, "flux_ujy"] = (M[40, 3] * 300).astype(np.float32)  # a galaxy at z = 0.2
    src.at[1, "err_ujy"] = (M[40, 3] * 6 + 0.5).astype(np.float32)
    store.write_table(results_rel("pilot", "sources.parquet"), to_arrow(src))
    # no crossids, no members: Gaia comes from refcat; source 10 is a Gaia star 1″ away
    rc = pd.DataFrame({"catalog": ["gaia_dr3", "catwise"], "ra": [src.ra[0] + 1 / 3600, src.ra[2]], "dec": [16.1, 16.1],
                       "phot_g_mean_mag": [13.0, np.nan], "parallax": [20.0, np.nan], "parallax_error": [0.1, np.nan]})
    store.write_parquet("refcat/pilot.parquet", rc)
    assert main(["classify", "--zone", "pilot", "--root", str(tmp_path)]) == 0
    cl = store.read_parquet(results_rel("pilot", "classes.parquet"))
    assert cl["class"].tolist() == ["star", "unsure", "unsure"]
    assert abs(cl["gaia_sep_arcsec"].iloc[0] - 1.0) < 0.05 and np.isnan(cl["gaia_g"].iloc[2])

    spec = pd.DataFrame({"ra": [src.ra[1]], "dec": [16.1], "z": [0.2], "zErr": [1e-4]})
    monkeypatch.setattr(PZ, "sdss_specz", lambda store, zone: spec)
    assert main(["photoz", "--zone", "pilot", "--root", str(tmp_path)]) == 0
    pz = store.read_parquet(results_rel("pilot", "photoz.parquet"))
    assert sorted(pz["source_id"]) == [11, 12]  # the star isn't fitted
    row = pz.set_index("source_id").loc[11]
    assert abs(row["z_best"] - 0.2) < 0.02 and row["z_lo"] <= row["z_best"] <= row["z_hi"]
    rep = store.read_json(results_rel("pilot", "photoz_report.json"))
    assert rep["sdss_dr17"]["all"]["n"] == 1 and rep["sdss_dr17"]["all"]["sigma_nmad"] < 0.02
