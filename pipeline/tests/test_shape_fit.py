"""Two-source fit of the shape candidates (shape_fit.py) on synthetic stamps: a Gaussian ePSF, photos at random rolls,
a star with or without a companion. Offline."""

from __future__ import annotations

import math
import warnings

import numpy as np
import pandas as pd
import pytest
from astropy.wcs import WCS

from skyshift_core.level2 import EPSF
from skyshift_pipeline import shape_fit as SF

RA0, DEC0 = 129.7, 16.6
PIX = 6.15  # arcsec per pixel


def gaussian_epsf(sigma_px: float = 0.8) -> EPSF:
    o = 5
    k = (np.arange(33) - 16) / o
    g = np.exp(-0.5 * (k[None, :] ** 2 + k[:, None] ** 2) / sigma_px ** 2)
    return EPSF(psf=(g / g.sum())[None].astype(np.float64), xcenter=np.array([1020.0]), ycenter=np.array([1020.0]),
                oversample=(o, o))


def make_stamps(epsf: EPSF, comp=None, ratios=None, n_per_det: int = 16, dets=(1, 4), noise: float = 0.03,
                seed: int = 1, visits=(1, 2), flux: float = 60.0, data_epsfs=None):
    """Photos of a star at (RA0, DEC0) and, with comp = {visit: (east, north)″}, a companion of flux ratio
    ratios[det]; each photo at a random roll and sub-pixel position; data_epsfs: the photos' true PSFs, one drawn
    per photo (the fit uses `epsf`)."""
    rng = np.random.default_rng(seed)
    h = SF.FIT_HALF_PX
    out = []
    for det in dets:
        for j in range(n_per_det):
            visit = visits[j % len(visits)]
            th = rng.uniform(0, 2 * np.pi)
            # J: (east, north)″ per pixel (x, y); a mirror like a real sky image (east = −x at roll 0)
            J = PIX * np.array([[-math.cos(th), math.sin(th)], [math.sin(th), math.cos(th)]])
            A = np.linalg.inv(J)
            x1, y1 = 500 + rng.uniform(-0.5, 0.5), 700 + rng.uniform(-0.5, 0.5)
            ix0, iy0 = int(round(x1)) - h, int(round(y1)) - h
            k = np.arange(2 * h + 1)
            px = (ix0 + k[None, :] + 0 * k[:, None]).astype(float)
            py = (iy0 + k[:, None] + 0 * k[None, :]).astype(float)
            z = np.zeros(px.shape, np.int64)
            e = epsf if data_epsfs is None else data_epsfs[int(rng.integers(len(data_epsfs)))]
            img = 0.3 + flux * e.evaluate(px - x1, py - y1, z)
            if comp is not None and visit in comp:
                u = A @ np.asarray(comp[visit], float)
                img += flux * ratios[det] * epsf.evaluate(px - x1 - u[0], py - y1 - u[1], z)
            var = np.full(img.shape, noise ** 2)
            img = img + rng.normal(0, noise, img.shape)
            w = WCS(naxis=2)
            w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
            w.wcs.crval = [RA0, DEC0]
            w.wcs.crpix = [x1 + 1, y1 + 1]
            w.wcs.cd = J / 3600.0
            out.append(SF.Stamp(key=f"o{det}_{j}", det=det, visit=visit, mjd=60000.0 + 180 * (visit - 1) + j * 0.1,
                                collection="spherex_dr1", data=img.astype(np.float32), var=var.astype(np.float32),
                                flags=np.zeros(img.shape, np.int32), ix0=ix0, iy0=iy0,
                                x=x1 + rng.normal(0, 0.1), y=y1 + rng.normal(0, 0.1), A=A, wcs=w))
    return out


@pytest.fixture(scope="module")
def epsf():
    return gaussian_epsf()


def test_two_sources_found_where_they_are(epsf):
    comp = {1: (3.0, -10.0), 2: (3.0, -10.0)}
    st = make_stamps(epsf, comp, {1: 0.25, 4: 0.5})
    f = SF.fit_source(st, {1: epsf, 4: epsf}, np.zeros((0, 2)), 7)
    assert f.n_photos == 32 and f.n_dets == 2
    assert f.east == pytest.approx(3.0, abs=0.3) and f.north == pytest.approx(-10.0, abs=0.3)
    assert f.sep == pytest.approx(math.hypot(3, 10), abs=0.3) and f.pa == pytest.approx(163.3, abs=2)
    assert f.ratio[1] == pytest.approx(0.25, abs=0.03) and f.ratio[4] == pytest.approx(0.5, abs=0.04)
    assert f.snr[1] > 20 and f.split and f.dchi2 > 1000
    assert np.isfinite(f.sep_err) and f.sep_err < 0.5
    # the companion's sky position: the star's + the offset
    from skyshift_core import tangent_offset_arcsec
    e, n = tangent_offset_arcsec(RA0, DEC0, f.ra, f.dec)
    assert e == pytest.approx(3.0, abs=0.3) and n == pytest.approx(-10.0, abs=0.3)
    assert f.moves is False and f.move_arcsec < 1.0
    row = SF.result_row(f)
    assert row["companion_split"] and row["companion_det"] in (1, 4)
    assert len(row["companion_visit_ra"]) == 2 and row["fit_n_photos"] == 32
    words = SF.companion_words(row)
    assert "south" in words and "fainter at 0.93 µm" in words and "stays put" in words


def test_a_single_star_is_not_split(epsf):
    st = make_stamps(epsf, None, None, seed=3)
    f = SF.fit_source(st, {1: epsf, 4: epsf}, np.zeros((0, 2)), 8, visits=False)
    assert not f.split
    assert f.dchi2 < 4 * f.n_photos  # noise: about one per photo (a flux each) plus the offset
    assert SF.companion_words(SF.result_row(f)) is None


def test_a_bright_star_with_psf_errors_is_not_split(epsf):
    """Each photo's true PSF a little narrower or wider than the model: a free F2 per photo soaks some of it up (χ²
    drops), but its sign changes from photo to photo, so the companion's S/N stays low."""
    st = make_stamps(epsf, None, None, seed=7, n_per_det=20, flux=200.0,
                     data_epsfs=[gaussian_epsf(0.7), gaussian_epsf(0.9)])
    f = SF.fit_source(st, {1: epsf, 4: epsf}, np.zeros((0, 2)), 12, visits=False)
    assert f.dchi2 > 2 * f.n_photos  # the PSF errors lower χ² ...
    assert not f.split  # ... but they are not an object


def test_a_companion_that_moves_between_visits(epsf):
    comp = {1: (0.0, -10.0), 2: (9.0, 4.0)}
    st = make_stamps(epsf, comp, {1: 0.4, 4: 0.4}, seed=5, n_per_det=20)
    f = SF.fit_source(st, {1: epsf, 4: epsf}, np.zeros((0, 2)), 9)
    v = {r["visit"]: r for r in f.visits}
    assert (v[1]["east"], v[1]["north"]) == (pytest.approx(0.0, abs=0.5), pytest.approx(-10.0, abs=0.5))
    assert (v[2]["east"], v[2]["north"]) == (pytest.approx(9.0, abs=0.5), pytest.approx(4.0, abs=0.5))
    assert f.moves is True and f.move_arcsec == pytest.approx(math.hypot(9, 14), abs=0.7)
    assert f.split  # fitted per visit: each visit sees it clearly
    words = SF.companion_words(SF.result_row(f))
    assert "moved 17″ between visits (something passing by?)" in words and "not in Gaia" not in words


def test_catalogued_neighbour_is_fitted_not_found(epsf):
    """A catalogued star in the stamp gets its own ePSF: the companion search doesn't pick it up."""
    st = make_stamps(epsf, {1: (-12.0, 8.0), 2: (-12.0, 8.0)}, {1: 0.6, 4: 0.6}, seed=11)
    f = SF.fit_source(st, {1: epsf, 4: epsf}, np.array([[-12.0, 8.0]]), 10, visits=False)
    assert not f.split


def test_neighbours_brightness_and_own_entries():
    ref = pd.DataFrame({
        "cat": ["gaia", "catwise", "gaia", "gaia", "catwise", "catwise"],
        "ra": [RA0, RA0, RA0 + 20 / 3600 / math.cos(math.radians(DEC0)), RA0, RA0, RA0 - 30 / 3600 / math.cos(math.radians(DEC0))],
        "dec": [DEC0, DEC0 + 0.5 / 3600, DEC0, DEC0 + 25 / 3600, DEC0 - 40 / 3600, DEC0],
        "epoch_mjd": [57388.5, 57170.0, 57388.5, 57388.5, 57170.0, 57170.0],
        "pmra": [0.0] * 6, "pmdec": [0.0] * 6,
        "g_mag": [16.0, np.nan, 18.5, 19.5, np.nan, np.nan], "w1_mag": [np.nan, 13.0, np.nan, np.nan, 15.5, 17.0]})
    nb = SF._neighbours(ref, RA0, DEC0, 61000.0, (RA0, DEC0), 55.0)
    # own Gaia and CatWISE out; G 18.5 at 20″ E in, G 19.5 (3.5 mag fainter) out; W1 15.5 at 40″ S in, W1 17 out
    assert sorted(map(tuple, np.round(nb).astype(int))) == [(0, -40), (20, 0)]


def test_catalogue_entry_at_the_companion():
    ref = pd.DataFrame({"cat": ["gaia", "2mass"], "ra": [RA0, RA0], "dec": [DEC0 + 1.0 / 3600, DEC0 + 20.0 / 3600],
                        "epoch_mjd": [57388.5, 51000.0], "pmra": [0.0, np.nan], "pmdec": [0.0, np.nan],
                        "g_mag": [19.84, np.nan], "w1_mag": [np.nan, np.nan], "k_mag": [np.nan, 14.2]})
    assert SF.catalogued_at(ref, RA0, DEC0, 61000.0) == "Gaia G 19.8"
    assert SF.catalogued_at(ref, RA0, DEC0 + 19.0 / 3600, 61000.0) == "2MASS K 14.2"
    assert SF.catalogued_at(ref, RA0, DEC0 + 10.0 / 3600, 61000.0) == ""
    row = {"companion_split": True, "companion_sep_arcsec": 8.0, "companion_pa_deg": 90.0, "two_vs_one_dchi2": 900.0,
           "companion_moves": None, "companion_catalog": "Gaia G 19.8", "companion_flux_ratio_d3": 0.3,
           "companion_snr_d3": 12.0}
    words = SF.companion_words(row)
    assert "8″ east" in words and "about 1.3 mag fainter at 2 µm" in words and "catalogued there as Gaia G 19.8" in words


def test_why_and_empty_rows():
    row = {"companion_split": True, "companion_sep_arcsec": 10.2, "companion_pa_deg": 181.0, "two_vs_one_dchi2": 31234.0,
           "companion_moves": False, **{f"companion_flux_ratio_d{d}": 0.2 for d in range(1, 7)},
           **{f"companion_snr_d{d}": 10.0 for d in range(1, 7)}}
    why = SF._why_with("Looks like two objects here; worth a look.", row)
    assert why.startswith("Looks like two objects here; worth a look. Fit: a two-object fit puts a second object 10″ south")
    assert "1.7 mag fainter at 0.93 µm and 1.7 mag fainter at 4.7 µm" in why and "Δχ² 31,234" in why
    assert SF._why_with(why, row) == why  # rewording twice keeps one fit sentence
    nofit = SF._why_with("Base.", {"companion_split": False, "two_vs_one_dchi2": 12.0})
    assert nofit == "Base. Fit: two objects don't fit clearly better than one here."
    assert SF._why_with("Base.", {"companion_split": False, "two_vs_one_dchi2": float("nan")}) == "Base."
    e = SF.empty_columns(pd.DataFrame({"source_id": pd.Series([], dtype=np.int64), "why": pd.Series([], dtype=str)}))
    assert set(SF.RESULT_COLUMNS) <= set(e.columns) and len(e) == 0


def test_run_never_raises(tmp_path):
    from skyshift_pipeline.store import Store

    rows = pd.DataFrame({"source_id": [1], "ra": [RA0], "dec": [DEC0], "mjd": [61000.0], "why": ["w"], "reasons": ["elongated"]})
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        out, info = SF.run(Store(str(tmp_path)), "nozone", rows)
    assert "error" in info and len(out) == 1 and out["why"].iloc[0] == "w"
    assert set(SF.RESULT_COLUMNS) <= set(out.columns)
