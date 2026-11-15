"""Step `stack` (stack.py) on synthetic photos: grid, splat, the joint ePSF fit (fluxes, a close pair, tiles), peaks,
the artifact zone, and a whole run on cached cutouts of two visits. Offline."""

from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest
from astropy.io import fits
from astropy.wcs import WCS
from skyshift_core.level2 import EPSF

from skyshift_pipeline import config as C
from skyshift_pipeline import stack as S
from skyshift_pipeline.store import Store

ZONE = C.Zone("t_syn", 150.0, 150.06, 10.0, 10.06)
PIX = C.STACK_NATIVE_PIX_ARCSEC
SIGMA_PX = 0.65  # synthetic ePSF: a Gaussian of this σ in native pixels (FWHM 9.4″, about the real ePSF)


def gauss_epsf(xc: float, yc: float) -> EPSF:
    ov = 5
    t = (np.arange(33) - 16) / ov
    X, Y = np.meshgrid(t, t)
    p = np.exp(-(X ** 2 + Y ** 2) / (2 * SIGMA_PX ** 2))
    return EPSF(psf=(p / p.sum())[None], xcenter=np.array([xc]), ycenter=np.array([yc]), oversample=(ov, ov))


def header(ra0: float, dec0: float, n: int, det: int = 2, mjd: float = 61000.0) -> fits.Header:
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [ra0, dec0]
    w.wcs.crpix = [n / 2 + 0.5, n / 2 + 0.5]
    w.wcs.cdelt = [-PIX / 3600.0, PIX / 3600.0]
    h = w.to_header()
    h["NAXIS1"], h["NAXIS2"], h["DETECTOR"], h["MJD-AVG"] = n, n, det, mjd
    return h


def render(wcs: WCS, n: int, stars: list[tuple[float, float, float]]) -> np.ndarray:
    """Point sources (ra, dec, total flux) through the Gaussian ePSF, evaluated at pixel centres."""
    img = np.zeros((n, n))
    yy, xx = np.mgrid[0:n, 0:n]
    for ra, dec, f in stars:
        x, y = wcs.world_to_pixel_values(ra, dec)
        g = np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * SIGMA_PX ** 2))
        img += f * g / (2 * math.pi * SIGMA_PX ** 2)
    return img


def off(dx: float, dy: float, ra: float = 150.03, dec: float = 10.03) -> tuple[float, float]:
    return ra + dx / 3600.0 / math.cos(math.radians(dec)), dec + dy / 3600.0


def test_grid_round_trip_and_orientation():
    g = S.zone_grid(ZONE)
    ra, dec = np.array([150.01, 150.05]), np.array([10.01, 10.05])
    x, y = g.xy(ra, dec)
    r2, d2 = g.radec(x, y)
    assert np.allclose(r2, ra, atol=1e-10) and np.allclose(d2, dec, atol=1e-10)
    assert x[1] < x[0] and y[1] > y[0]  # east is left (x grows west), north up
    assert g.nx * g.pix >= 0.06 * 3600 * math.cos(math.radians(10.06)) + 2 * C.STACK_MARGIN_ARCSEC - 2 * g.pix


def test_splat_keeps_the_weights_and_the_mean():
    acc = S.Acc(np.zeros((20, 20)), np.zeros((20, 20)), np.zeros((20, 20), np.int32))
    rng = np.random.default_rng(3)
    u, v = rng.uniform(2, 17, 500), rng.uniform(2, 17, 500)
    w = rng.uniform(0.5, 2.0, 500)
    S.splat(acc, u, v, np.full(500, 3.0), w)
    assert acc.w.sum() == pytest.approx(w.sum())
    hit = acc.w > 0
    assert np.allclose(acc.s[hit] / acc.w[hit], 3.0)
    S.splat(acc, np.array([5.5]), np.array([5.5]), np.array([1.0]), np.array([0.0]))  # no weight: nothing
    assert acc.w.sum() == pytest.approx(w.sum())


def _cut(stars, n=64, noise=1.0, seed=0, ra0=150.03, dec0=10.03):
    h = header(ra0, dec0, n)
    wcs = S._celestial_wcs(h)
    img = render(wcs, n, stars) + np.random.default_rng(seed).normal(0, noise, (n, n))
    meta = {"obs_id": f"o{seed}", "detector": 2, "visit": 0, "session": 0, "mjd": 61000.0, "x0": 0, "y0": 0,
            "shape": [n, n], "epsf": "x", "conv": 889.0, "bad_frac": 0.0}
    return S.Cut(meta, img.astype(np.float32), np.full((n, n), noise, np.float32), np.zeros((n, n), np.int32), wcs,
                 gauss_epsf(n / 2, n / 2))


def test_joint_fit_recovers_fluxes_and_splits_a_close_pair():
    a, b = off(-4, 0), off(4, 0)  # 8″ apart: 1.3 native pixels
    lone = off(0, 60)
    stars = [(a[0], a[1], 400.0), (b[0], b[1], 200.0), (lone[0], lone[1], 300.0)]
    cut = _cut(stars)
    src = S.Sources.plain(np.array([a[0], b[0], lone[0]]), np.array([a[1], b[1], lone[1]]))
    r = S.fit_photo(cut, src)
    assert list(r["idx"]) == [0, 1, 2]
    f, e = r["flux"], r["err"]
    assert np.all(np.abs(f - [400, 200, 300]) < 4 * e)
    assert e[0] == pytest.approx(e[2], rel=0.05)  # contracts: the error leaves the neighbours' covariance out
    assert np.all(r["free_ok"]) and np.all(np.hypot(r["dx"], r["dy"]) < 0.15)  # native px: < 1″
    assert np.nanmedian(r["chi2"]) == pytest.approx(1.0, abs=0.5)


def test_tiled_fit_equals_the_whole_block(monkeypatch):
    rng = np.random.default_rng(5)
    n = 96
    pts = [(rng.uniform(-260, 260), rng.uniform(-260, 260)) for _ in range(12)]
    radec = [off(*p) for p in pts]
    cut = _cut([(r, d, 250.0) for r, d in radec], n=n, seed=2)
    src = S.Sources.plain(np.array([p[0] for p in radec]), np.array([p[1] for p in radec]))
    whole = S.fit_photo(cut, src)
    monkeypatch.setattr(C, "STACK_FIT_TILE_PX", 32)
    tiled = S.fit_photo(cut, src)
    assert list(tiled["idx"]) == list(whole["idx"])
    # sources > 6 px apart don't share pixels, so the tiles' answers are the whole block's
    xy = np.column_stack([whole["x"], whole["y"]])
    d = np.hypot(*(xy[:, None] - xy[None]).transpose(2, 0, 1)) + np.eye(len(xy)) * 99
    lone = d.min(axis=1) > 2 * C.STACK_STAMP_HALF + 1
    assert lone.sum() >= 5
    assert np.allclose(tiled["flux"][lone], whole["flux"][lone], rtol=1e-6, atol=1e-6)


def test_solve_handles_empty_and_identical_columns():
    from scipy import sparse

    A = sparse.csr_matrix(np.array([[1.0, 1.0, 0.0], [2.0, 2.0, 0.0], [0.5, 0.5, 0.0]]))
    x = S._solve((A.T @ A).tocsr(), A.T @ np.array([2.0, 4.0, 1.0]))
    assert np.all(np.isfinite(x)) and x[0] + x[1] == pytest.approx(2.0, rel=1e-3) and x[2] == 0.0


def test_peaks_and_merge():
    snr = np.zeros((40, 40))
    snr[10, 10], snr[10, 11] = 9.0, 8.0     # one peak (3×3 maximum)
    snr[30, 30] = 6.0
    snr[30, 31] = 5.5                        # not a maximum
    snr[20, 5] = 4.0                         # below threshold
    m = S.Map("all", snr, snr, np.ones_like(snr, bool), 1.0)
    pk = S.peaks(m)
    assert sorted(zip(pk["y"].round().astype(int), pk["x"].round().astype(int))) == [(10, 10), (30, 30)]
    two = pd.concat([pk, pk.assign(x=pk["x"] + 0.5, name="v0")], ignore_index=True)
    merged = S.merge_peaks(two, 1.25)
    assert len(merged) == 2


def test_artifact_zone_from_brighter_sources():
    g = S.zone_grid(ZONE)
    bright = off(0, 0)
    pts = [bright, off(5, 0), off(25, 0), off(40, 0), off(0, -70)]
    ra, dec = np.array([p[0] for p in pts]), np.array([p[1] for p in pts])
    snr = np.array([500.0, 20.0, 20.0, 20.0, 20.0])
    d = S.brighter_sep(ra, dec, snr, g)
    assert np.isnan(d[0]) and d[1] == pytest.approx(5.0, abs=0.01) and d[2] == pytest.approx(25.0, abs=0.01)
    assert d[3] == pytest.approx(40.0, abs=0.01) and np.isnan(d[4])  # beyond STACK_ARTIFACT_RADIUS_ARCSEC
    assert np.isnan(S.brighter_sep(ra, dec, np.array([500.0, 200.0, 20.0, 20.0, 20.0]), g)[1])  # not 5× brighter


# --- a whole run on cached cutouts --------------------------------------------------------------------------------


STARS = {  # name: (dx″, dy″ from the zone centre, flux per photo in MJy/sr·px, Gaia G or None)
    "bright": (-60.0, -50.0, 3000.0, 12.5),
    "pair_a": (40.0, 40.0, 120.0, 16.0),
    "pair_b": (40.0, 50.0, 100.0, 16.2),
    "faint": (-40.0, 50.0, 9.0, None),      # S/N ~2.5 per photo: only the stacks see it
    "mid": (60.0, -50.0, 60.0, 17.0),
}


NPHOT = 12  # photos per visit: enough dithers to fill the 2″ grid (a real visit has ~18 per detector)


def _synthetic_zone(tmp_path):
    root, cache = tmp_path / "bucket", tmp_path / "cache"
    cache.mkdir(parents=True)
    st = Store(str(root))
    n = 96
    rows = []
    ra_c, dec_c = ZONE.centre
    stars = [(*off(dx, dy, ra_c, dec_c), f) for dx, dy, f, _ in STARS.values()]
    k = 0
    dith = np.random.default_rng(7).uniform(-12.0, 12.0, (2, NPHOT, 2))  # each photo's pointing offset, arcsec
    for visit, t0 in ((0, 61000.0), (1, 61180.0)):
        for e in range(NPHOT):
            dra, ddec = off(*dith[visit, e], ra_c, dec_c)
            h = header(dra, ddec, n, det=2, mjd=t0 + 0.1 * e)
            wcs = S._celestial_wcs(h)
            img = render(wcs, n, stars) + np.random.default_rng(10 + k).normal(0, 1.0, (n, n))
            obs = f"syn{k}"
            meta = {"obs_id": obs, "detector": 2, "visit": visit, "session": e, "mjd": t0 + 0.1 * e, "x0": 0, "y0": 0,
                    "shape": [n, n], "epsf": "2_syn", "conv": 889.0, "bad_frac": 0.0}
            np.savez(S.cut_path(str(cache), obs, 2), sub=img.astype(np.float32), noise=np.ones((n, n), np.float32),
                     flags=np.zeros((n, n), np.int32), header=np.asarray(h.tostring()),
                     meta=np.asarray(json.dumps(meta)))
            corners = wcs.pixel_to_world_values(np.array([0, n - 1, n - 1, 0]), np.array([0, 0, n - 1, n - 1]))
            rows.append({"obs_id": obs, "detector": 2, "visit": visit, "session": e, "mjd": t0 + 0.1 * e,
                         "url": f"file:///nowhere/{obs}.fits",
                         "s_region": "POLYGON ICRS " + " ".join(f"{a} {b}" for a, b in zip(*corners))})
            k += 1
    e = gauss_epsf(n / 2, n / 2)
    np.savez(S.epsf_path(str(cache), "2_syn"), psf=e.psf, xcenter=e.xcenter, ycenter=e.ycenter,
             oversample=np.asarray(e.oversample))
    st.write_parquet("photolists/zones/t_syn.parquet", pd.DataFrame(rows))
    gaia = [(name, *off(dx, dy, ra_c, dec_c), g) for name, (dx, dy, _, g) in STARS.items() if g is not None]
    st.write_parquet("refcat/t_syn.parquet", pd.DataFrame({
        "ref_id": [f"gaia:{i + 1}" for i in range(len(gaia))], "cat": "gaia",
        "ra": [r for _, r, _, _ in gaia], "dec": [d for _, _, d, _ in gaia], "epoch_mjd": S.GAIA_EPOCH_MJD,
        "pmra": 0.0, "pmdec": 0.0, "parallax": 0.0, "g_mag": [g for *_, g in gaia], "w1_mag": np.nan,
        "w2_mag": np.nan, "j_mag": np.nan}))
    # the single-photo sources: the bright star, one star of the pair (merged) and mid
    single = [STARS["bright"], (40.0, 45.0), STARS["mid"]]
    sra, sdec = zip(*(off(s[0], s[1], ra_c, dec_c) for s in single))
    st.write_parquet("results/t_syn/sources.parquet", pd.DataFrame({
        "source_id": np.array([11, 22, 33], np.int64), "ra": sra, "dec": sdec}))
    return st, str(cache)


def test_run_on_cached_cutouts_finds_the_faint_star_and_splits_the_pair(tmp_path):
    st, cache = _synthetic_zone(tmp_path)
    out = S.run(st, ZONE, workers=1, cache=cache)
    assert out["photos"] == 2 * NPHOT and out["photos_used"] == 2 * NPHOT
    s = pd.read_parquet(st.path("results/t_syn/stack_sources.parquet"))
    f = pd.read_parquet(st.path("results/t_syn/stack_forced.parquet"))
    rep = json.loads(st.read_bytes("results/t_syn/stack.json"))
    ra_c, dec_c = ZONE.centre
    g = S.zone_grid(ZONE)

    def at(name):
        dx, dy = STARS[name][:2]
        ra, dec = off(dx, dy, ra_c, dec_c)
        i, d = S.nearest([ra], [dec], s["ra"].to_numpy(), s["dec"].to_numpy(), 2.5, g.ra0, g.dec0)
        assert i[0] >= 0, f"{name} not found"
        return s.iloc[int(i[0])]

    a, b, faint = at("pair_a"), at("pair_b"), at("faint")
    assert a["stack_id"] != b["stack_id"]
    assert a["gaia_ref_id"] and b["gaia_ref_id"] and a["pos_gaia"] and b["pos_gaia"]
    assert faint["new"] and faint["cat"] == "" and not faint["transient"] and faint["snr_best"] >= C.STACK_KEEP_SNR
    assert faint["source_id"] == -1 and not faint["artifact_zone"]
    assert list(faint["visits"]) == [0, 1] and len(faint["mjd_visit"]) == 2
    mid = 0.1 * (NPHOT - 1) / 2
    assert faint["mjd_visit"][0] == pytest.approx(61000.0 + mid) and faint["mjd_visit"][1] == pytest.approx(61180.0 + mid)
    conv = 889.0
    assert a["flux_ujy_d2"] == pytest.approx(120.0 * conv, rel=0.15)
    assert at("bright")["flux_ujy_d2"] == pytest.approx(3000.0 * conv, rel=0.05)
    assert at("bright")["source_id"] == 11 and not at("bright")["new"]
    assert np.isnan(s["flux_ujy_d1"]).all()  # no D1 photo
    per = f.groupby("stack_id").size()
    assert per.loc[faint["stack_id"]] == 2 * NPHOT and set(f["visit"]) == {0, 1}
    assert {"stack_id", "obs_id", "detector", "visit", "mjd", "x", "y", "flux_ujy", "flux_err_ujy", "snr", "chi2",
            "free_ok"} <= set(f.columns)
    assert rep["sources"] == len(s) and rep["new"] >= 1 and rep["gaia_pairs_6_15"]["split_stack"] == 1
    assert rep["new_stack_class"] == int((s["new"] & ~s["artifact_zone"]).sum())
