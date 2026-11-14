"""zodi, ePSF photometry, shape, multi-aperture, forced photometry and photo metadata of `dots`."""

import json

import numpy as np
import pandas as pd
import pytest
from astropy.io import fits

import skyshift_pipeline.config as C
from skyshift_pipeline import dots as D
from skyshift_pipeline.psf import psf_photometry
from skyshift_pipeline.store import Store, dots_rel
from skyshift_core import SAT_BITS
from skyshift_core.level2 import EPSF

from conftest import CUTOUT, REPO

EPSF_FILE = REPO / "core" / "tests" / "data" / "epsf_d1_centre3x3.npz"  # 9 real zones of the first photo
GAIA = REPO / "fixtures" / "fits" / "cutout_2025W18_1B_0074_2D1.gaia.json"
OBS = "2025W18_1B_0074_2"


def epsf_hdu(path) -> "fits.BinTableHDU":
    """An EPSF HDU (as in a Level 2 file) built from the real zones saved in tests/data/*.npz
    (*.fits is git-ignored outside fixtures/)."""
    z = np.load(path)
    cols = [fits.Column(name=c, format=f, array=z[c], **kw) for c, f, kw in [
        ("BINX", "J", {}), ("BINY", "J", {}), ("XCENTER", "E", {"unit": "pixel"}), ("YCENTER", "E", {"unit": "pixel"}),
        ("XWIDTH", "E", {"unit": "pixel"}), ("YWIDTH", "E", {"unit": "pixel"}), ("NSTAR", "J", {}),
        ("NEFF_MEAN", "E", {}), ("CWAVE", "E", {"unit": "um"}), ("CBAND", "E", {"unit": "um"}),
        ("EPSF", "1089D", {"dim": "(33,33)"})]]
    hdu = fits.BinTableHDU.from_columns(cols, name="EPSF")
    hdu.header["OVSMPX"], hdu.header["OVSMPY"] = int(z["OVSMPX"]), int(z["OVSMPY"])
    hdu.header["ORDERING"] = "x-fast then y"
    return hdu


@pytest.fixture(scope="module")
def epsf():
    return EPSF.from_hdu(epsf_hdu(EPSF_FILE))


def star_field(epsf, positions, flux, sky=0.5, sigma=0.05, shape=(64, 64), seed=0):
    """Stars made from the real ePSF (evaluated at pixel centres: no pixel integration) + noise.
    The fit uses the same zone, so origin moves the image to the zone grid's centre."""
    rng = np.random.default_rng(seed)
    img = np.full(shape, sky, np.float64)
    for x, y in positions:
        P, ix, iy = epsf.stamps([x], [y], half=6)
        img[iy[0] - 6:iy[0] + 7, ix[0] - 6:ix[0] + 7] += flux * P[0]
    return img + rng.normal(0, sigma, shape)


def test_psf_fit_recovers_flux_at_subpixel_offsets(epsf):
    E = epsf.shifted(1000, 1000)  # this image's pixel (0, 0) sits at detector (1000, 1000): centre zone
    offs = [(0.0, 0.0), (0.25, -0.1), (0.5, 0.5), (-0.37, 0.21), (0.1, -0.45), (-0.5, 0.0)]
    pulls, chi = [], []
    for seed in range(8):
        pos = [(12 + 13 * (k % 4) + dx, 12 + 13 * (k // 4) + dy) for k, (dx, dy) in enumerate(offs)]
        img = star_field(E, pos, flux=20.0, seed=seed)
        x, y = np.array(pos).T
        ph = psf_photometry(img, x, y, E, noise=np.full(img.shape, 0.05), model_frac=0.0)
        pulls.extend((ph.flux - 20.0) / ph.flux_err)
        chi.extend(ph.chi2_nu)
        np.testing.assert_allclose(ph.sky, 0.5, atol=0.03)
        assert (ph.npix == 49).all()
    pulls = np.array(pulls)
    assert abs(pulls.mean()) < 0.5 and 0.6 < pulls.std() < 1.5  # unbiased, errors right
    assert 0.7 < np.median(chi) < 1.3  # star-like: chi2_nu near 1
    # with the default 3% model term the flux is still recovered within its (larger) error
    ph = psf_photometry(img, x, y, E, noise=np.full(img.shape, 0.05))
    assert (np.abs(ph.flux - 20.0) < 3 * ph.flux_err).all()


def test_psf_fit_masked_core_and_extended_source(epsf):
    E = epsf.shifted(1000, 1000)
    img = star_field(E, [(20.3, 20.2)], flux=50.0, sigma=0.02, shape=(40, 40))
    bad = np.zeros(img.shape, bool)
    bad[20, 20] = True  # e.g. a saturated core pixel: left out, flux from the wings
    ph = psf_photometry(img, [20.3], [20.2], E, noise=np.full(img.shape, 0.02), bad=bad, model_frac=0.0)
    assert ph.npix[0] == 48 and abs(ph.flux[0] - 50.0) < 3 * ph.flux_err[0]
    # a broad source (σ = 2 px) is not star-like: chi2_nu >> 1
    yy, xx = np.mgrid[:40, :40]
    gal = 0.5 + 50.0 / (2 * np.pi * 4) * np.exp(-((xx - 20) ** 2 + (yy - 20) ** 2) / 8.0)
    gal += np.random.default_rng(1).normal(0, 0.02, gal.shape)
    ph = psf_photometry(gal, [20.0], [20.0], E, noise=np.full(gal.shape, 0.02), model_frac=0.0)
    assert ph.chi2_nu[0] > 20
    # too few pixels (off the image) -> NaN
    ph = psf_photometry(gal, [-2.6], [20.0], E, noise=np.full(gal.shape, 0.02))
    assert np.isnan(ph.flux[0])


def test_shape_of_elongated_source():
    yy, xx = np.mgrid[:41, :41].astype(float)
    th = np.deg2rad(30.0)
    u = (xx - 20.2) * np.cos(th) + (yy - 19.9) * np.sin(th)
    v = -(xx - 20.2) * np.sin(th) + (yy - 19.9) * np.cos(th)
    img = 1.0 + 100 * np.exp(-0.5 * (u**2 / 1.6**2 + v**2 / 0.7**2))
    el, a, b, theta = D.shape_moments(img, np.zeros(img.shape, bool), np.array([20.2]), np.array([19.9]))
    assert theta[0] == pytest.approx(30.0, abs=2.0)
    assert a[0] > b[0] and el[0] == pytest.approx(a[0] / b[0]) and el[0] > 1.5
    # round source: elong ~1; axis-aligned elongation along y: theta ~ 90
    rnd = 1.0 + 100 * np.exp(-0.5 * ((xx - 20) ** 2 + (yy - 20) ** 2) / 0.8**2)
    assert D.shape_moments(rnd, np.zeros(img.shape, bool), np.array([20.0]), np.array([20.0]))[0][0] < 1.05
    tall = 1.0 + 100 * np.exp(-0.5 * ((xx - 20) ** 2 / 0.7**2 + (yy - 20) ** 2 / 1.6**2))
    assert abs(D.shape_moments(tall, np.zeros(img.shape, bool), np.array([20.0]), np.array([20.0]))[3][0]) > 88


def _elongation_loop(data, bad, x, y, r=C.ELONG_R_PX, bkg=C.ELONG_BKG_PX):
    """The per-dot loop `elong` used before 14 Nov (reference for the vectorised version)."""
    ny, nx = data.shape
    h = int(np.ceil(bkg[1]))
    yy, xx = np.mgrid[-h:h + 1, -h:h + 1]
    out = np.full(x.size, np.nan)
    for k, (xi, yi) in enumerate(zip(x, y)):
        ix, iy = int(round(xi)), int(round(yi))
        if ix - h < 0 or iy - h < 0 or ix + h + 1 > nx or iy + h + 1 > ny:
            continue
        st = data[iy - h:iy + h + 1, ix - h:ix + h + 1].astype(np.float64)
        good = ~bad[iy - h:iy + h + 1, ix - h:ix + h + 1] & np.isfinite(st)
        dx, dy = xx - (xi - ix), yy - (yi - iy)
        rr = dx * dx + dy * dy
        ann = good & (rr >= bkg[0] ** 2) & (rr <= bkg[1] ** 2)
        if ann.sum() < 5:
            continue
        w = np.clip(np.where(good & (rr <= r * r), st - np.median(st[ann]), 0.0), 0.0, None)
        tot = w.sum()
        if tot <= 0:
            continue
        cxx, cyy, cxy = (w * dx * dx).sum() / tot, (w * dy * dy).sum() / tot, (w * dx * dy).sum() / tot
        tr, det = cxx + cyy, cxx * cyy - cxy * cxy
        disc = np.sqrt(max(tr * tr / 4.0 - det, 0.0))
        l1, l2 = tr / 2.0 + disc, tr / 2.0 - disc
        out[k] = np.sqrt(l1 / l2) if l2 > 1e-6 else 99.0
    return np.minimum(out, 99.0)


def test_vectorised_elong_matches_previous_loop():
    from skyshift_core import build_mask, open_level2
    l2 = open_level2(CUTOUT)
    bad = build_mask(l2.data, l2.flags)
    rng = np.random.default_rng(4)
    x, y = rng.uniform(-1, 101, 300), rng.uniform(-1, 101, 300)
    np.testing.assert_allclose(D.elongation(l2.data, bad, x, y), _elongation_loop(l2.data, bad, x, y),
                               rtol=1e-9, equal_nan=True)


# --- whole photo: ZODI + EPSF + forced + metadata on a synthetic MEF of the real cutout -------------
def synthetic_mef(tmp_path):
    with fits.open(CUTOUT) as h:
        ny, nx = h["IMAGE"].data.shape
        zodi = np.full((ny, nx), 0.128, np.float32)
        ep = epsf_hdu(EPSF_FILE)
        hdus = [fits.PrimaryHDU(header=h[0].header), fits.ImageHDU(h["IMAGE"].data, h["IMAGE"].header, name="IMAGE"),
                fits.CompImageHDU(h["FLAGS"].data, name="FLAGS"), fits.ImageHDU(h["VARIANCE"].data, name="VARIANCE"),
                fits.ImageHDU(zodi, name="ZODI"), ep,
                fits.BinTableHDU(h["WCS-WAVE"].data, h["WCS-WAVE"].header, name="WCS-WAVE")]
    p = tmp_path / "l2.fits"
    fits.HDUList(hdus).writeto(p)
    return p


def write_refcat(store: Store, zone: str, n_blank: int = 20) -> pd.DataFrame:
    g = json.loads(GAIA.read_text())["gaia"]
    rng = np.random.default_rng(5)
    ra0, dec0 = np.mean([s["ra"] for s in g]), np.mean([s["dec"] for s in g])
    ref = pd.DataFrame({
        "ref_id": [f"gaia:{s['id']}" for s in g] + [f"catwise:J{k}" for k in range(n_blank)],
        "ra": [s["ra"] for s in g] + list(ra0 + rng.uniform(-0.05, 0.05, n_blank)),
        "dec": [s["dec"] for s in g] + list(dec0 + rng.uniform(-0.05, 0.05, n_blank)),
        "epoch_mjd": [57388.5] * len(g) + [57170.0] * n_blank,
        "pmra": [s["pmra"] for s in g] + [np.nan] * n_blank,
        "pmdec": [s["pmdec"] for s in g] + [np.nan] * n_blank,
        "parallax": [s["parallax"] for s in g] + [np.nan] * n_blank,
    })
    store.write_parquet(D.refcat_rel(zone), ref)
    return ref


def gaia_scatter(dots: pd.DataFrame, rc: str, dc: str) -> float:
    """Robust per-axis scatter (″, mean of RA and Dec) of dots within 2″ of a fixture Gaia star at their MJD."""
    g = json.loads(GAIA.read_text())["gaia"]
    pm = lambda v: 0.0 if v is None else float(v)  # noqa: E731
    dt = (dots["mjd"].iloc[0] - 57388.5) / 365.25
    gde = np.array([s["dec"] for s in g])
    gra = np.array([s["ra"] for s in g]) + np.array([pm(s["pmra"]) for s in g]) * dt / 3.6e6 / np.cos(np.deg2rad(gde))
    gde = gde + np.array([pm(s["pmdec"]) for s in g]) * dt / 3.6e6
    ra, dec = dots[rc].to_numpy(), dots[dc].to_numpy()
    dra = (ra[:, None] - gra[None, :]) * np.cos(np.deg2rad(gde))[None, :] * 3600.0
    dde = (dec[:, None] - gde[None, :]) * 3600.0
    j = np.hypot(dra, dde).argmin(axis=1)
    k = np.arange(len(ra))
    m = np.hypot(dra[k, j], dde[k, j]) < 2.0
    assert m.sum() >= 30
    rob = lambda v: 1.4826 * np.median(np.abs(v - np.median(v)))  # noqa: E731
    return 0.5 * (rob(dra[k, j][m]) + rob(dde[k, j][m]))


@pytest.fixture
def cyg_zone(monkeypatch):
    monkeypatch.setitem(C.ZONES, "cygtest", C.Zone("cygtest", 303.0, 305.0, 41.0, 42.5))
    D._EPSF_CACHE.clear()
    D._REFCAT.clear()
    yield "cygtest"
    D._EPSF_CACHE.clear()
    D._REFCAT.clear()


def test_photo_with_zodi_epsf_forced_and_metadata(tmp_path, cyg_zone):
    src = synthetic_mef(tmp_path)
    root = tmp_path / "root"
    store = Store(str(root))
    ref = write_refcat(store, cyg_zone)
    st = D.process_photo(str(root), OBS, 1, str(src), 3, 1, cyg_zone)
    assert st["status"] == "done", st
    df = pd.read_parquet(root / dots_rel(OBS, 1))
    fo = pd.read_parquet(root / D.forced_rel(OBS, 1))
    assert st["n_forced"] == len(fo) > 100 and st["forced_bytes"] > 0
    # dots: zodi and ePSF columns are filled now
    np.testing.assert_allclose(df["zodi_mjysr"], 0.128, rtol=1e-6)
    good = (df["snr"] > 50) & ((df["flags"] & SAT_BITS) == 0)
    assert df.loc[good, "flux_psf_ujy"].notna().all()
    r = (df.loc[good, "flux_psf_ujy"] / df.loc[good, "flux_ujy"]).median()
    assert 0.8 < r < 1.3  # ≈ the aperture correction of r = 2 px
    # v4: ePSF positions for nearly every unsaturated dot; saturated dots keep the wings centroid
    sat = (df["flags"].to_numpy() & SAT_BITS) != 0
    assert sat.any() and df.loc[~sat, "psf_pos_ok"].mean() > 0.95 and not df.loc[sat, "psf_pos_ok"].any()
    np.testing.assert_array_equal(df.loc[sat, "x_psf"], df.loc[sat, "x"])
    np.testing.assert_array_equal(df.loc[sat, "ra_psf"], df.loc[sat, "ra"])
    ok = df["psf_pos_ok"].to_numpy()
    assert (np.hypot(df["x_psf"] - df["x"], df["y_psf"] - df["y"])[ok] <= C.PSF_POS_MAX_SHIFT_PX).all()
    assert (df.loc[ok, "pos_err_psf_arcsec"] >= C.PSF_POS_ERR_FLOOR_ARCSEC).all()
    assert (df["sep_flag"] != 0).any() and (df["sep_flag"] >= 0).all()
    # ... and closer to Gaia than the windowed centroid (real Cygnus stars: ~0.2″ vs ~0.55″ per axis)
    sig_c = gaia_scatter(df[good & ok], "ra", "dec")
    sig_p = gaia_scatter(df[good & ok], "ra_psf", "dec_psf")
    assert sig_p < 0.7 * sig_c and sig_p < 0.35
    assert D.photo_meta(root / dots_rel(OBS, 1))["psf_fit"]["position"] is True
    # forced: columns, types, every target inside the frame (>= 3 px) and the zone; blanks kept
    assert list(fo.columns) == list(D.FORCED_COLUMNS)
    for c, t in D.FORCED_COLUMNS.items():
        assert str(fo[c].dtype) == t, c
    assert ((fo["x"] >= 3) & (fo["x"] <= 97) & (fo["y"] >= 3) & (fo["y"] <= 97)).all()
    assert set(fo["ref_id"]) <= set(ref["ref_id"])
    blank = fo[fo["ref_id"].str.startswith("catwise:")]
    assert len(blank) > 5 and np.isfinite(blank["flux_psf_ujy"]).all()
    # metadata: header, timings, calibration against Gaia, grids
    meta = D.photo_meta(root / dots_rel(OBS, 1))
    assert meta["n_forced"] == len(fo) and meta["zodi_median_mjysr"] == pytest.approx(0.128)
    ast = meta["calibration"]["astrometry_vs_gaia"]
    assert ast["n"] >= 10 and abs(ast["dra_cosdec_arcsec"]) < 1.0 and abs(ast["ddec_arcsec"]) < 1.0
    assert meta["grids"]["zodi_mjysr"][5][7] == pytest.approx(0.128)
    assert meta["epsf_calib"] == "019f4f97-41f1-7f7c-a94b-cfc0f1d4c9c8"
    assert json.loads(json.dumps(meta)) == meta
    # restart skips; a second photo of the same detector reuses the cached ePSF
    assert D.process_photo(str(root), OBS, 1, str(src), 3, 1, cyg_zone)["status"] == "skipped"
    assert len(D._EPSF_CACHE) == 1
    l2 = D.load_photo(str(src))
    assert l2.epsf is next(iter(D._EPSF_CACHE.values()))


def test_psf_position_switch_off(tmp_path, cyg_zone, monkeypatch):
    monkeypatch.setattr(C, "DOTS_PSF_POSITION", False)
    src = synthetic_mef(tmp_path)
    root = tmp_path / "root"
    assert D.process_photo(str(root), OBS, 1, str(src), 3, 1)["status"] == "done"
    df = pd.read_parquet(root / dots_rel(OBS, 1))
    assert list(df.columns) == list(D.DOT_COLUMNS) + list(D.DOT_LIST_COLUMNS)
    assert not df["psf_pos_ok"].any() and df["flux_psf_ujy"].notna().any()
    np.testing.assert_array_equal(df["x_psf"], df["x"])
    np.testing.assert_array_equal(df["dec_psf"], df["dec"])
    assert D.photo_meta(root / dots_rel(OBS, 1))["psf_fit"]["position"] is False


def test_missing_refcat_skips_forced(tmp_path, cyg_zone):
    root = tmp_path / "root"
    st = D.process_photo(str(root), OBS, 1, str(CUTOUT), 0, 0, cyg_zone)
    assert st["status"] == "done" and st["n_forced"] == 0
    assert not (root / D.forced_rel(OBS, 1)).exists()
    assert D.photo_meta(root / dots_rel(OBS, 1))["n_forced"] is None


def test_finast_photo_has_metadata(tmp_path, cyg_zone):
    with fits.open(CUTOUT) as h:
        h["IMAGE"].header["FINAST"] = 3
        p = tmp_path / "bad.fits"
        h.writeto(p)
    root = tmp_path / "root"
    st = D.process_photo(str(root), OBS, 1, str(p), 0, 0, cyg_zone)
    assert st["status"] == "done_finast"
    df = pd.read_parquet(root / dots_rel(OBS, 1))
    assert len(df) == 0 and list(df.columns)[-2:] == list(D.DOT_LIST_COLUMNS)
    meta = D.photo_meta(root / dots_rel(OBS, 1))
    assert meta["status"] == "done_finast" and meta["header"]["FINAST"] == 3 and meta["n_dots"] == 0
