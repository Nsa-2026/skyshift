"""Step `astrometry`: SPHEREx-only position, proper motion (and parallax) of every fixed source.

Per source with >= ASTRO_MIN_DOTS member dots, a weighted least-squares fit on the tangent plane about
the source's median position:
    east_i  = x0 + pmra  · t_i + ϖ · Pe_i
    north_i = y0 + pmdec · t_i + ϖ · Pn_i
t_i in Julian years from the source's weighted mean epoch (written as epoch_mjd, so position and
motion are uncorrelated); weights 1 / pos_err_arcsec² per axis; Pe, Pn the parallax factors from the
observer's barycentric position: the Earth's (astropy's built-in ephemeris) plus SPHEREx's geocentric
offset (X_SC, Y_SC, Z_SC in km, from the dot files or the photo list, when present; < 1e-4 AU, so it
barely matters). Parallax is fitted only with >= ASTRO_PARALLAX_MIN_VISITS visits; with two visits six
months apart it is degenerate with the proper motion. One refit without dots beyond ASTRO_CLIP_SIGMA.
Errors are the formal ones × √max(1, χ²_ν). Vectorised: normal equations from bincounts, one batched
5×5 solve; no loop per source.

Frame correction (ASTRO_FRAME_CORRECT): each photo's WCS can be off by a fraction of an arcsec as a
whole. Quiet Gaia stars (crossids, |pm| < ASTRO_FRAME_PM_MAX) are moved to the photo's time; the median
offset of their dots in that photo (>= ASTRO_FRAME_MIN_STARS stars) is subtracted from all its dots.
This ties our frame to Gaia's (proper motions become absolute); each star weighs 1/thousands in its
photo's offset, so the comparison with Gaia below stays honest.

Writes results/<zone>/astrometry.parquet and astrometry_report.json: SPHEREx proper motion vs Gaia for
matched stars (bias, robust scatter, median error, pull scatter), overall and by median dot S/N,
with and without the frame correction, and the fast Gaia stars one by one.
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd
from skyshift_core import tangent_offset_arcsec
from skyshift_core.astrometry import DAYS_PER_JYEAR

from . import config as C
from . import log
from .motion_io import member_dots
from .spectra import count_distinct, group_median
from .store import Store, results_rel

L = log.get("astrometry")

AU_KM = 149_597_870.7
COLUMNS = ["source_id", "ra", "dec", "epoch_mjd", "ra_err_arcsec", "dec_err_arcsec", "pmra", "pmdec", "pmra_err",
           "pmdec_err", "pm", "pm_snr", "parallax", "parallax_err", "fit", "n_dots", "n_used", "n_visits", "span_yr",
           "chi2_nu", "snr_median", "frame_corrected"]


def observer_xyz_au(mjd: np.ndarray, sc_km: np.ndarray | None = None) -> np.ndarray:
    """(n, 3) barycentric ICRS position of the observer in AU: Earth + SPHEREx's geocentric offset."""
    from astropy.coordinates import get_body_barycentric
    from astropy.time import Time

    mjd = np.asarray(mjd, np.float64)
    out = np.zeros((mjd.size, 3))
    fin = np.isfinite(mjd)
    if fin.any():
        u, inv = np.unique(np.round(mjd[fin], 6), return_inverse=True)
        pos = get_body_barycentric("earth", Time(u, format="mjd", scale="utc"))
        xyz = np.stack([pos.x.to_value("AU"), pos.y.to_value("AU"), pos.z.to_value("AU")], axis=1)
        out[fin] = xyz[inv.ravel()]
    if sc_km is not None:
        sc = np.asarray(sc_km, np.float64)
        out += np.where(np.isfinite(sc), sc, 0.0) / AU_KM
    return out


def parallax_factors(ra_deg, dec_deg, xyz_au) -> tuple[np.ndarray, np.ndarray]:
    """(Pe, Pn): parallactic shift (east, north) per unit parallax for an observer at xyz_au."""
    a, d = np.deg2rad(ra_deg), np.deg2rad(dec_deg)
    X, Y, Z = xyz_au[:, 0], xyz_au[:, 1], xyz_au[:, 2]
    return X * np.sin(a) - Y * np.cos(a), X * np.cos(a) * np.sin(d) + Y * np.sin(a) * np.sin(d) - Z * np.cos(d)


def frame_offsets(d: pd.DataFrame, east: np.ndarray, north: np.ndarray, ra0: np.ndarray, dec0: np.ndarray,
                  s: np.ndarray, sid_u: np.ndarray, crossids: pd.DataFrame | None, xyz: np.ndarray) -> tuple[np.ndarray, np.ndarray, dict]:
    """Per-dot (east, north) offset of its photo against Gaia (0 where the photo has too few stars)."""
    zero = np.zeros(len(d))
    if crossids is None or not len(crossids) or "gaia_ra" not in crossids:
        return zero, zero, {"photos_corrected": 0}
    x = crossids.set_index("source_id").reindex(sid_u)
    pm = np.hypot(x["gaia_pmra"].to_numpy(np.float64), x["gaia_pmdec"].to_numpy(np.float64))
    ref_src = np.isfinite(x["gaia_ra"].to_numpy(np.float64)) & (np.nan_to_num(pm, nan=np.inf) < C.ASTRO_FRAME_PM_MAX)
    m = ref_src[s]
    if not m.any():
        return zero, zero, {"photos_corrected": 0}
    xs = x.iloc[s[m]]
    ge, gn = tangent_offset_arcsec(ra0[s[m]], dec0[s[m]], xs["gaia_ra"].to_numpy(np.float64), xs["gaia_dec"].to_numpy(np.float64))
    dt = (d["mjd"].to_numpy(np.float64)[m] - xs["gaia_epoch_mjd"].to_numpy(np.float64)) / DAYS_PER_JYEAR
    ge = ge + np.nan_to_num(xs["gaia_pmra"].to_numpy(np.float64)) * dt / 1000.0
    gn = gn + np.nan_to_num(xs["gaia_pmdec"].to_numpy(np.float64)) * dt / 1000.0
    pe, pn = parallax_factors(ra0[s[m]], dec0[s[m]], xyz[m])
    plx = np.nan_to_num(xs["gaia_parallax"].to_numpy(np.float64)) / 1000.0
    re, rn = east[m] - (ge + plx * pe), north[m] - (gn + plx * pn)
    photo = d["photo"].to_numpy(np.int64)
    P = int(photo.max()) + 1 if len(photo) else 0
    ph = photo[m]
    okp = ph >= 0
    n_ref = np.bincount(ph[okp], minlength=P)
    oe = group_median(ph[okp], re[okp], P)
    on = group_median(ph[okp], rn[okp], P)
    good = (n_ref >= C.ASTRO_FRAME_MIN_STARS) & np.isfinite(oe) & np.isfinite(on)
    oe, on = np.where(good, oe, 0.0), np.where(good, on, 0.0)
    pd_ = np.where(photo >= 0, photo, 0)
    fe = np.where(photo >= 0, oe[pd_], 0.0)
    fn = np.where(photo >= 0, on[pd_], 0.0)
    off = np.hypot(oe[good], on[good])
    st = {"photos_corrected": int(good.sum()), "frame_ref_dots": int(m.sum()),
          "frame_offset_median_arcsec": float(np.median(off)) if off.size else None,
          "frame_offset_p90_arcsec": float(np.percentile(off, 90)) if off.size else None}
    return fe, fn, st


def _solve(s, N, w, t, east, north, Pe, Pn, use_plx):
    """Batched normal equations → (params (N,5), cov (N,5,5))."""
    def S(v):
        return np.bincount(s, w * v, N)
    one = np.ones_like(t)
    A = np.zeros((N, 5, 5))
    A[:, 0, 0] = A[:, 1, 1] = S(one)
    A[:, 0, 2] = A[:, 2, 0] = A[:, 1, 3] = A[:, 3, 1] = S(t)
    A[:, 2, 2] = A[:, 3, 3] = S(t * t)
    A[:, 0, 4] = A[:, 4, 0] = S(Pe)
    A[:, 1, 4] = A[:, 4, 1] = S(Pn)
    A[:, 2, 4] = A[:, 4, 2] = S(t * Pe)
    A[:, 3, 4] = A[:, 4, 3] = S(t * Pn)
    A[:, 4, 4] = S(Pe * Pe + Pn * Pn)
    b = np.stack([S(east), S(north), S(t * east), S(t * north), S(Pe * east + Pn * north)], axis=1)
    nop = ~use_plx
    A[nop, 4, :] = 0.0
    A[nop, :, 4] = 0.0
    A[nop, 4, 4] = 1.0
    b[nop, 4] = 0.0
    # sources with no time leverage would be singular: make them solvable, they are dropped later
    bad = np.abs(np.linalg.det(A)) < 1e-30
    A[bad] = np.eye(5)
    b[bad] = 0.0
    cov = np.linalg.inv(A)
    par = np.einsum("nij,nj->ni", cov, b)
    cov[bad] = np.nan
    par[bad] = np.nan
    return par, cov


def fit(d: pd.DataFrame, crossids: pd.DataFrame | None = None, frame: bool = True) -> tuple[pd.DataFrame, dict]:
    """Member dots (source_id, ra, dec, mjd, pos_err_arcsec, visit, photo, snr, x/y/z_sc_km) → one row per
    fitted source (COLUMNS)."""
    pe_ = d["pos_err_arcsec"].to_numpy(np.float64)
    ok = np.isfinite(pe_) & (pe_ > 0) & np.isfinite(d["mjd"].to_numpy(np.float64))
    d = d[ok]
    sid_u, s = np.unique(d["source_id"].to_numpy(np.int64), return_inverse=True)
    s = s.ravel()
    N = sid_u.size
    n_dots = np.bincount(s, minlength=N)
    ra, dec = d["ra"].to_numpy(np.float64), d["dec"].to_numpy(np.float64)
    ra0 = group_median(s, ra, N)
    dec0 = group_median(s, dec, N)
    mjd = d["mjd"].to_numpy(np.float64)
    err = d["pos_err_arcsec"].to_numpy(np.float64)
    east, north = tangent_offset_arcsec(ra0[s], dec0[s], ra, dec)
    sc = np.stack([d[c].to_numpy(np.float64) for c in ("x_sc_km", "y_sc_km", "z_sc_km")], axis=1) if "x_sc_km" in d else None
    xyz = observer_xyz_au(mjd, sc)
    st: dict = {"spacecraft_position_dots": int(np.isfinite(sc).all(axis=1).sum()) if sc is not None else 0}
    corrected = np.zeros(N, bool)
    if frame:
        fe, fn, fst = frame_offsets(d, east, north, ra0, dec0, s, sid_u, crossids, xyz)
        st.update(fst)
        east, north = east - fe, north - fn
        corrected = np.bincount(s, (fe != 0) | (fn != 0), N) > 0
    Pe, Pn = parallax_factors(ra0[s], dec0[s], xyz)
    n_visits = count_distinct(s, d["visit"].to_numpy(np.int64), N)
    use_plx = n_visits >= C.ASTRO_PARALLAX_MIN_VISITS
    w = 1.0 / err ** 2
    keep = np.ones(len(d), bool)
    for it in range(2):
        wk = np.where(keep, w, 0.0)
        sw = np.bincount(s, wk, N)
        tref = np.bincount(s, wk * mjd, N) / np.maximum(sw, 1e-300)
        t = (mjd - tref[s]) / DAYS_PER_JYEAR
        par, cov = _solve(s, N, wk, t, east, north, Pe, Pn, use_plx)
        pi = par[s]
        re = east - (pi[:, 0] + pi[:, 2] * t + pi[:, 4] * Pe)
        rn = north - (pi[:, 1] + pi[:, 3] * t + pi[:, 4] * Pn)
        n_used = np.bincount(s, keep, N)
        npar = np.where(use_plx, 5, 4)
        chi = np.bincount(s, wk * (re * re + rn * rn), N)
        with np.errstate(invalid="ignore", divide="ignore"):
            chi2_nu = chi / np.maximum(2 * n_used - npar, 1)
        if it == 0:
            infl = np.sqrt(np.maximum(1.0, chi2_nu))[s]
            keep = np.maximum(np.abs(re), np.abs(rn)) <= C.ASTRO_CLIP_SIGMA * err * infl
    scale = np.sqrt(np.maximum(1.0, chi2_nu))
    sig = np.sqrt(np.einsum("nii->ni", cov)) * scale[:, None]
    mx = pd.Series(mjd).groupby(s).max().reindex(range(N)).to_numpy()
    mn = pd.Series(mjd).groupby(s).min().reindex(range(N)).to_numpy()
    span = (mx - mn) / DAYS_PER_JYEAR
    from skyshift_core import tangent_deproject
    ra_fit, dec_fit = tangent_deproject(ra0, dec0, par[:, 0], par[:, 1])
    pmra, pmdec = par[:, 2] * 1000.0, par[:, 3] * 1000.0
    pmra_e, pmdec_e = sig[:, 2] * 1000.0, sig[:, 3] * 1000.0
    pm = np.hypot(pmra, pmdec)
    with np.errstate(invalid="ignore", divide="ignore"):
        pm_err = np.sqrt((pmra * pmra_e) ** 2 + (pmdec * pmdec_e) ** 2) / pm
        pm_snr = pm / np.where(pm > 0, pm_err, np.hypot(pmra_e, pmdec_e))
    snr = d["snr"].to_numpy(np.float64) if "snr" in d else np.full(len(d), np.nan)
    out = pd.DataFrame({
        "source_id": sid_u, "ra": ra_fit, "dec": dec_fit, "epoch_mjd": tref,
        "ra_err_arcsec": sig[:, 0].astype(np.float32), "dec_err_arcsec": sig[:, 1].astype(np.float32),
        "pmra": pmra.astype(np.float32), "pmdec": pmdec.astype(np.float32),
        "pmra_err": pmra_e.astype(np.float32), "pmdec_err": pmdec_e.astype(np.float32),
        "pm": pm.astype(np.float32), "pm_snr": pm_snr.astype(np.float32),
        "parallax": np.where(use_plx, par[:, 4] * 1000.0, np.nan).astype(np.float32),
        "parallax_err": np.where(use_plx, sig[:, 4] * 1000.0, np.nan).astype(np.float32),
        "fit": np.where(use_plx, "pm+plx", "pm"),
        "n_dots": n_dots.astype(np.int32), "n_used": n_used.astype(np.int32), "n_visits": n_visits.astype(np.int16),
        "span_yr": span.astype(np.float32), "chi2_nu": chi2_nu.astype(np.float32),
        "snr_median": group_median(s, snr, N).astype(np.float32), "frame_corrected": corrected,
    })
    good = (n_dots >= C.ASTRO_MIN_DOTS) & np.isfinite(pmra) & (span > 0)
    st.update({"sources_in": int(N), "fitted": int(good.sum()), "with_parallax": int((good & use_plx).sum())})
    return out[good].reset_index(drop=True)[COLUMNS], st


def _stats(dx: np.ndarray, sx: np.ndarray) -> dict:
    ok = np.isfinite(dx) & np.isfinite(sx) & (sx > 0)
    dx, sx = dx[ok], sx[ok]
    if dx.size == 0:
        return {"n": 0}
    med = float(np.median(dx))
    return {"n": int(dx.size), "bias": round(med, 3), "scatter": round(float(1.4826 * np.median(np.abs(dx - med))), 3),
            "median_err": round(float(np.median(sx)), 3),
            "pull_scatter": round(float(1.4826 * np.median(np.abs(dx / sx - np.median(dx / sx)))), 3)}


def report(fit_tab: pd.DataFrame, crossids: pd.DataFrame | None) -> dict:
    """SPHEREx-only proper motion (and parallax) against Gaia DR3 for matched stars (mas/yr, mas)."""
    if crossids is None or not len(crossids) or not len(fit_tab):
        return {"matched": 0}
    j = fit_tab.merge(crossids[["source_id", "gaia_id", "gaia_ref_id", "gaia_g", "gaia_pmra", "gaia_pmdec", "gaia_parallax", "gaia_n"]],
                      on="source_id")
    j = j[(j["gaia_ref_id"] != "") & np.isfinite(j["gaia_pmra"]) & (j["gaia_n"] == 1)]
    out: dict = {"matched": int(len(j)), "units": "mas/yr (pm), mas (parallax); delta = SPHEREx − Gaia"}
    if not len(j):
        return out
    dra = (j["pmra"] - j["gaia_pmra"]).to_numpy(np.float64)
    ddec = (j["pmdec"] - j["gaia_pmdec"]).to_numpy(np.float64)
    out["pmra"] = _stats(dra, j["pmra_err"].to_numpy(np.float64))
    out["pmdec"] = _stats(ddec, j["pmdec_err"].to_numpy(np.float64))
    bins = C.ASTRO_REPORT_SNR_BINS
    out["by_snr"] = []
    snr = j["snr_median"].to_numpy(np.float64)
    for lo, hi in zip(bins[:-1], bins[1:]):
        m = (snr >= lo) & (snr < hi)
        out["by_snr"].append({"snr": [lo, hi if hi < 1e8 else None],
                              "pmra": _stats(dra[m], j["pmra_err"].to_numpy(np.float64)[m]),
                              "pmdec": _stats(ddec[m], j["pmdec_err"].to_numpy(np.float64)[m])})
    p = j[j["fit"] == "pm+plx"]
    if len(p):
        out["parallax"] = _stats((p["parallax"] - p["gaia_parallax"]).to_numpy(np.float64), p["parallax_err"].to_numpy(np.float64))
    gpm = np.hypot(j["gaia_pmra"], j["gaia_pmdec"]).to_numpy()
    fast = j[gpm > C.HIGHPM_RECALL_MAS_YR].assign(gaia_pm=gpm[gpm > C.HIGHPM_RECALL_MAS_YR]).sort_values("gaia_pm", ascending=False)
    out["fast_gaia_stars"] = [
        {"source_id": int(r.source_id), "gaia_id": int(r.gaia_id), "G": None if not np.isfinite(r.gaia_g) else round(float(r.gaia_g), 2),
         "gaia_pmra": round(float(r.gaia_pmra), 1), "gaia_pmdec": round(float(r.gaia_pmdec), 1),
         "pmra": round(float(r.pmra), 1), "pmdec": round(float(r.pmdec), 1),
         "pmra_err": round(float(r.pmra_err), 1), "pmdec_err": round(float(r.pmdec_err), 1), "n_visits": int(r.n_visits)}
        for r in fast.head(50).itertuples()]
    return out


def run(store: Store, zone: C.Zone, workers: int = 16) -> dict:
    t0 = time.time()
    d, st = member_dots(store, zone, workers)
    xid_rel = results_rel(zone.name, "crossids.parquet")
    crossids = store.read_parquet(xid_rel) if store.exists(xid_rel) else None
    if crossids is None:
        L.warning("astrometry %s: no crossids.parquet: no frame correction, no Gaia comparison", zone.name)
    raw, st_raw = fit(d, crossids, frame=False)
    if C.ASTRO_FRAME_CORRECT and crossids is not None:
        cor, st_cor = fit(d, crossids, frame=True)
    else:
        cor, st_cor = raw, st_raw
    store.write_parquet(results_rel(zone.name, "astrometry.parquet"), cor)
    rep = {"zone": zone.name, "frame_correction": {k: v for k, v in st_cor.items() if k.startswith(("photos", "frame"))},
           "fitted": st_cor.get("fitted", 0), "with_parallax": st_cor.get("with_parallax", 0),
           "spacecraft_position_dots": st_cor.get("spacecraft_position_dots", 0),
           "vs_gaia_frame_corrected": report(cor, crossids) if cor is not raw else None,
           "vs_gaia_raw": report(raw, crossids)}
    store.write_json(results_rel(zone.name, "astrometry_report.json"), rep)
    out = {**st, **st_cor, "wall_s": round(time.time() - t0, 1)}
    best = rep["vs_gaia_frame_corrected"] or rep["vs_gaia_raw"]
    if best.get("matched"):
        out["gaia_pmra_bias_scatter"] = [best["pmra"].get("bias"), best["pmra"].get("scatter")]
    L.info("astrometry %s: %s", zone.name, out)
    return out
