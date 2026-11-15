"""Unsaturated dots' offsets fixed in detector pixels (astrometry.pixel_offsets: ASTRO_PIXEL_MAP, ASTRO_BRIGHT_OFFSET).
The 180° roll between visits turns a pixel offset into fake motion: a static map gives each star its own (it sits at
another x in each visit), a brightness term gives bright stars a shared one. Synthetic data only."""

import numpy as np
import pandas as pd
import pytest
from skyshift_core import GAIA_EPOCH_MJD, tangent_deproject

from skyshift_pipeline import astrometry as AS
from skyshift_pipeline import config as C

from test_sat_astrometry import J_VISIT

STAR = (126.30, 21.25)
A_MAP = 0.03      # px: the map's amplitude along x
DX_BRIGHT = -0.012  # px along x for dots 0.5 mag above D1's saturation (AB 11.0 against 10.5)


@pytest.fixture(autouse=True)
def small_cells(monkeypatch):
    """4 × 4 map cells and small minimum counts, so a few hundred stars measure every cell."""
    monkeypatch.setattr(C, "ASTRO_MAP_CELL_PX", 510)
    monkeypatch.setattr(C, "ASTRO_MAP_MIN_DOTS", 20)
    monkeypatch.setattr(C, "ASTRO_BRIGHT_MIN_DOTS", 100)


def map_truth(x: np.ndarray) -> np.ndarray:
    """Δx (px) of the synthetic detector map, the same in every photo: +A_MAP to −A_MAP across the cell centres
    (what a 4-cell map holds exactly)."""
    centres = (np.arange(4) + 0.5) * C.ASTRO_MAP_CELL_PX
    return np.interp(np.asarray(x, np.float64), centres, A_MAP * np.array([1.0, 1 / 3, -1 / 3, -1.0]))


def scene(rng, n_faint=300, n_bright=40, n_sat=10, per_visit=20, err=0.04, map_on=True, bright_on=True, movers=()):
    """Still Gaia stars (faint: AB 16; bright: AB 11, DX_BRIGHT px off; saturated: their own
    flag, never touched here) and movers (pmra, pmdec; faint), one D1 dot each per photo, two visits rolled 180°. Each star
    has its own x in each visit (the map then moves it); y walks along the detector within a visit (the filter)."""
    stars = []
    for i in range(n_faint + n_bright + n_sat):
        kind = "faint" if i < n_faint else "bright" if i < n_faint + n_bright else "sat"
        stars.append((100 + i, STAR[0] + rng.uniform(-0.5, 0.5), STAR[1] + rng.uniform(-0.5, 0.5), 0.0, 0.0, kind,
                      rng.uniform(40, 2000, 2), rng.uniform(40, 1000)))
    for i, (pmra, pmdec) in enumerate(movers):
        stars.append((9000 + i, STAR[0] + 0.3 * i, STAR[1] - 0.6, pmra, pmdec, "faint", rng.uniform(40, 2000, 2), rng.uniform(40, 1000)))
    rows, photo = [], 0
    for v, t0 in ((1, 61000.0), (2, 61182.0)):
        J = J_VISIT[v]
        for k in range(per_visit):
            mjd = t0 + k
            for sid, ra, dec, pmra, pmdec, kind, xs, y0 in stars:
                x, y = xs[v - 1], y0 + 50.0 * k
                dt = (mjd - GAIA_EPOCH_MJD) / 365.25
                e, n = pmra * dt / 1000.0 + rng.normal(0, err), pmdec * dt / 1000.0 + rng.normal(0, err)
                dx = (map_truth(x) if map_on and kind != "sat" else 0.0) + (DX_BRIGHT if bright_on and kind == "bright" else 0.0)
                e, n = e + J[0, 0] * dx, n + J[1, 0] * dx
                r, d = tangent_deproject(ra, dec, e, n)
                rows.append(dict(source_id=sid, obs_id=f"p{photo}", detector=1, photo=photo, ra=float(r), dec=float(d), mjd=mjd,
                                 pos_err_arcsec=err, visit=v, session=photo, x=x, y=y,
                                 snr={"faint": 40.0, "bright": 300.0, "sat": 500.0}[kind],
                                 flux_ujy=10 ** ((23.9 - {"faint": 16.0, "bright": 11.0, "sat": 9.0}[kind]) / 2.5),
                                 flags_peak=0x2 if kind == "sat" else 0))
            photo += 1
    d = pd.DataFrame(rows)
    jac = pd.DataFrame({"obs_id": [f"p{p}" for p in range(photo)], "detector": np.int8(1)})
    for c, (i, j) in zip(AS.JAC_COLUMNS, ((0, 0), (0, 1), (1, 0), (1, 1))):
        jac[c] = [J_VISIT[1 if p < per_visit else 2][i, j] for p in range(photo)]
    gstars = [s for s in stars if s[0] < 9000]
    xid = pd.DataFrame({"source_id": [s[0] for s in gstars], "gaia_ref_id": [f"gaia:{s[0]}" for s in gstars],
                        "gaia_id": [s[0] for s in gstars], "gaia_n": 1, "gaia_ra": [s[1] for s in gstars],
                        "gaia_dec": [s[2] for s in gstars], "gaia_epoch_mjd": GAIA_EPOCH_MJD, "gaia_pmra": 0.0,
                        "gaia_pmdec": 0.0, "gaia_parallax": 0.0, "gaia_g": [15.0 if s[5] == "faint" else 10.0 for s in gstars],
                        "j": [14.0 if s[5] == "faint" else 8.0 for s in gstars]})
    return d, jac, xid


def _pm(tab: pd.DataFrame, ids) -> pd.DataFrame:
    return tab.set_index("source_id").loc[list(ids), ["pmra", "pmdec", "pmra_err", "pmdec_err"]]


def test_detector_map_is_measured_and_removed(rng, monkeypatch):
    """A static map along x (±0.03 px): every star sits at another x in each visit, so each gets its own fake motion
    (here up to ±650 mas/yr). Fitted in pixels on the quiet stars, removed through each photo's Jacobian: gone; a mover
    keeps its own motion."""
    d, jac, xid = scene(rng, bright_on=False, movers=[(400.0, -250.0)])
    monkeypatch.setattr(C, "ASTRO_PIXEL_MAP", False)
    monkeypatch.setattr(C, "ASTRO_BRIGHT_OFFSET", False)
    raw, _ = AS.fit(d, xid, frame=True, jac=jac)
    monkeypatch.setattr(C, "ASTRO_PIXEL_MAP", True)
    fixed, st = AS.fit(d, xid, frame=True, jac=jac)
    still = [s for s in range(100, 400)]
    r, f = _pm(raw, still), _pm(fixed, still)
    assert (np.abs(r["pmra"] / r["pmra_err"]) > 5).sum() > 30                       # the fake motions, star by star
    assert (np.abs(f["pmra"] / f["pmra_err"]) > 5).sum() == 0
    assert np.std(f["pmra"]) < 0.3 * np.std(r["pmra"])
    m = fixed.set_index("source_id").loc[9000]
    assert abs(m["pmra"] - 400) < 4 * m["pmra_err"] + 15 and abs(m["pmdec"] + 250) < 4 * m["pmdec_err"] + 15
    mt = st["pix_tables"]["map"]
    assert mt["applied"].all() and len(mt) == 16
    centre = (mt["cx"].to_numpy() + 0.5) * C.ASTRO_MAP_CELL_PX
    assert np.allclose(mt["dx_px"], map_truth(centre), atol=0.25 * A_MAP)
    assert np.abs(mt["dy_px"]).max() < 0.2 * A_MAP
    assert st["pix_tables"]["bright"].empty
    rep = AS.pixel_report(st["pix_tables"], st["unsaturated_dots"], st["pixel_dots_corrected"])
    m1 = rep["map"][0]
    assert rep["enabled"]["map"] and m1["detector"] == 1 and m1["cells_applied"] == 16 and len(m1["dx_px"]) == 4
    assert st["pixel_dots_corrected"] == st["unsaturated_dots"] == int((d["flags_peak"] == 0).sum())


def test_brightness_offset_is_measured_and_removed(rng, monkeypatch):
    """Bright unsaturated dots sit DX_BRIGHT px along x: the roll gives every bright star the same fake RA motion
    (−250 mas/yr here). The brightness term (bins of the dot's own depth below saturation) removes it and leaves the faint stars alone; saturated
    dots are not touched (they have ASTRO_SAT_OFFSET)."""
    d, jac, xid = scene(rng, map_on=False)
    monkeypatch.setattr(C, "ASTRO_PIXEL_MAP", False)
    monkeypatch.setattr(C, "ASTRO_BRIGHT_OFFSET", False)
    raw, _ = AS.fit(d, xid, frame=True, jac=jac)
    monkeypatch.setattr(C, "ASTRO_BRIGHT_OFFSET", True)
    fixed, st = AS.fit(d, xid, frame=True, jac=jac)
    bright = range(400, 440)
    assert np.median(_pm(raw, bright)["pmra"]) < -150
    assert abs(np.median(_pm(fixed, bright)["pmra"])) < 25 and abs(np.median(_pm(fixed, bright)["pmdec"])) < 25
    assert abs(np.median(_pm(fixed, range(100, 400))["pmra"])) < 10
    bt = st["pix_tables"]["bright"].set_index("bin")
    assert bt.loc["0.5..1", "applied"] and abs(bt.loc["0.5..1", "dx_px"] - DX_BRIGHT) < 0.002
    assert abs(bt.loc["5..6", "dx_px"]) < 0.002
    assert st["pix_tables"]["map"].empty
    assert st["unsaturated_dots"] == int((d["flags_peak"] == 0).sum())


def test_pixel_offsets_from_tiles_equal_the_untiled_fit(rng):
    """tiles.py: frame_residuals with the Jacobians per tile, pixel_offsets on all of them after the zone's frame offsets
    — the tables the untiled fit measures, and the same fit when they are passed."""
    d, jac, xid = scene(rng, n_faint=200, n_bright=30, n_sat=4)
    rng2 = np.random.default_rng(5)
    shift = {p: rng2.normal(0, 0.3, 2) for p in d["photo"].unique()}  # every photo's WCS off a little
    e = np.array([shift[p][0] for p in d["photo"]])
    n = np.array([shift[p][1] for p in d["photo"]])
    d["ra"], d["dec"] = tangent_deproject(d["ra"].to_numpy(), d["dec"].to_numpy(), e, n)
    cor, st = AS.fit(d, xid, frame=True, jac=jac)
    untiled = st["pix_tables"]
    halves = [d[d["source_id"] % 2 == k] for k in (0, 1)]
    res = pd.concat([AS.frame_residuals(h, xid, jac) for h in halves], ignore_index=True)
    off, _ = AS.photo_offsets(res)
    tiled = AS.pixel_offsets(res, off)
    for k in ("map", "bright"):
        a = tiled[k].sort_values(list(tiled[k].columns[:4])).reset_index(drop=True)
        b = untiled[k].sort_values(list(untiled[k].columns[:4])).reset_index(drop=True)
        pd.testing.assert_frame_equal(a, b, rtol=1e-9, check_dtype=False)
    cor2, _ = AS.fit(d, xid, frame=True, offsets=off, jac=jac, pix=tiled)
    pd.testing.assert_frame_equal(cor, cor2, rtol=1e-9)


def test_pixel_terms_need_jacobians_and_columns(rng):
    """No Jacobians: nothing measured, nothing moved. Dots without x, y or flux_ujy: no map, no brightness term."""
    d, jac, xid = scene(rng, n_faint=60, n_bright=0, n_sat=0)
    _, st = AS.fit(d, xid, frame=True)
    assert "pix_tables" not in st and "pixel_dots_corrected" not in st
    _, st = AS.fit(d.drop(columns=["x", "y", "flux_ujy"]), xid, frame=True, jac=jac)
    assert st["pix_tables"]["map"].empty and st["pix_tables"]["bright"].empty and st["pixel_dots_corrected"] == 0
    assert AS.pixel_shift(d, np.arange(3), jac, None)[0].tolist() == [0.0, 0.0, 0.0]
    lev = AS.bright_level(pd.DataFrame({"flux_ujy": [10 ** ((23.9 - 12.0) / 2.5), 0.0, np.nan], "detector": [5, 1, 1]}))
    assert lev[0] == pytest.approx(12.0 - 9.6) and np.isnan(lev[1:]).all()
