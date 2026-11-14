"""Step `astrometry`: SPHEREx-only position, proper motion (and parallax) of every fixed source.

Per source with >= ASTRO_MIN_DOTS member dots, a weighted least-squares fit on the tangent plane about
the source's median position:
    east_i  = x0 + pmra  · t_i + ϖ · Pe_i
    north_i = y0 + pmdec · t_i + ϖ · Pn_i
t_i in Julian years from the source's weighted mean epoch (written as epoch_mjd, so position and
motion are uncorrelated); weights 1 / pos_err_rel_arcsec² per axis (calib's epoch-to-epoch error, without each
star's constant offset from Gaia; pos_err_arcsec where absent), and that constant part, the mean of
pos_err_arcsec² − pos_err_rel_arcsec² over the source's dots, added to ra_err/dec_err only; Pe, Pn the parallax factors from the
observer's barycentric position: the Earth's (astropy's built-in ephemeris) plus SPHEREx's geocentric
offset (X_SC, Y_SC, Z_SC in km, from the dot files or the photo list, when present; < 1e-4 AU, so it
barely matters). Parallax is fitted only with >= ASTRO_PARALLAX_MIN_VISITS visits; with two visits six
months apart it is degenerate with the proper motion. One refit without dots beyond ASTRO_CLIP_SIGMA.
Errors are the formal ones, × √χ²_ν where χ²_ν is beyond its ASTRO_CHI2_INFLATE_P quantile for the source's
degrees of freedom (√max(1, χ²_ν) everywhere inflated small-ν sources by chance: pulls 0.85 against Gaia). Vectorised: normal equations from bincounts, one batched
5×5 solve; no loop per source.

The hunt tail (15 Nov: 622 Gaia-still stars at ≥ 150 mas/yr, ≥ 5σ; in a 2°×2° test box 40 → 9 with these, Gaia as truth):
- ASTRO_CLEAN_DOTS (clean_mask): dots with a masked, unsaturated peak (centroid on the wings) and extra dots of one photo
  (a bright star's wing fragments) are left out from the start; n_dots still counts them, n_used doesn't.
- ASTRO_DETECTOR_ZEROPOINTS: a position zero point per source × detector, eliminated exactly (dots demeaned per
  detector, Frisch–Waugh), so pm and parallax come only from position changes within a detector. A star's constant
  colour-dependent offset per detector, with detectors weighing differently in the two visits, was fake motion.
- ASTRO_SESSION_ROBUST: a session's dots share errors (pointing, simultaneous detector pairs, a passing neighbour), so
  the motion errors are also the session-clustered sandwich (≥ ASTRO_ROBUST_MIN_SESSIONS sessions), whichever is larger.
- ASTRO_DETECTOR_ROBUST: detectors that disagree about the motion (a ghost or halo in some bands in one visit) inflate
  the errors to the detector-clustered sandwich where it beats its ASTRO_DETECTOR_ROBUST_P χ² quantile.
ra/dec and their errors stay those of the plain fit (no zero points: the position is every detector's mean).

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


def frame_refs(d: pd.DataFrame, east: np.ndarray, north: np.ndarray, ra0: np.ndarray, dec0: np.ndarray,
               s: np.ndarray, sid_u: np.ndarray, crossids: pd.DataFrame | None,
               xyz: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """(mask of the reference dots: quiet Gaia stars, their east and north residual against Gaia ″), or None."""
    if crossids is None or not len(crossids) or "gaia_ra" not in crossids:
        return None
    x = crossids.set_index("source_id").reindex(sid_u)
    pm = np.hypot(x["gaia_pmra"].to_numpy(np.float64), x["gaia_pmdec"].to_numpy(np.float64))
    ref_src = np.isfinite(x["gaia_ra"].to_numpy(np.float64)) & (np.nan_to_num(pm, nan=np.inf) < C.ASTRO_FRAME_PM_MAX)
    m = ref_src[s]
    if not m.any():
        return None
    xs = x.iloc[s[m]]
    ge, gn = tangent_offset_arcsec(ra0[s[m]], dec0[s[m]], xs["gaia_ra"].to_numpy(np.float64), xs["gaia_dec"].to_numpy(np.float64))
    dt = (d["mjd"].to_numpy(np.float64)[m] - xs["gaia_epoch_mjd"].to_numpy(np.float64)) / DAYS_PER_JYEAR
    ge = ge + np.nan_to_num(xs["gaia_pmra"].to_numpy(np.float64)) * dt / 1000.0
    gn = gn + np.nan_to_num(xs["gaia_pmdec"].to_numpy(np.float64)) * dt / 1000.0
    pe, pn = parallax_factors(ra0[s[m]], dec0[s[m]], xyz[m])
    plx = np.nan_to_num(xs["gaia_parallax"].to_numpy(np.float64)) / 1000.0
    return m, east[m] - (ge + plx * pe), north[m] - (gn + plx * pn)


def frame_offsets(d: pd.DataFrame, east: np.ndarray, north: np.ndarray, ra0: np.ndarray, dec0: np.ndarray,
                  s: np.ndarray, sid_u: np.ndarray, crossids: pd.DataFrame | None, xyz: np.ndarray,
                  offsets: pd.DataFrame | None = None) -> tuple[np.ndarray, np.ndarray, dict]:
    """Per-dot (east, north) offset of its photo against Gaia (0 where the photo has too few stars).
    offsets: per-photo offsets fitted elsewhere (photo_offsets over the whole zone, tiles.py), used as they are."""
    zero = np.zeros(len(d))
    if offsets is not None:
        if not len(offsets) or not len(d):
            return zero, zero, {"photos_corrected": 0}
        o = offsets[offsets["good"].to_numpy(bool)]
        key = pd.MultiIndex.from_arrays([o["obs_id"].astype(str), o["detector"].to_numpy(np.int64)])
        pos = key.get_indexer(pd.MultiIndex.from_arrays([d["obs_id"].astype(str).to_numpy(), d["detector"].to_numpy(np.int64)]))
        ok = pos >= 0
        fe, fn = zero.copy(), zero.copy()
        fe[ok], fn[ok] = o["oe"].to_numpy(np.float64)[pos[ok]], o["on"].to_numpy(np.float64)[pos[ok]]
        return fe, fn, {"photos_corrected": int(len(o))}
    refs = frame_refs(d, east, north, ra0, dec0, s, sid_u, crossids, xyz)
    if refs is None:
        return zero, zero, {"photos_corrected": 0}
    m, re, rn = refs
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


def _cluster_cov(s, N, cl, wk, t, re, rn, Pe, Pn, cov, k) -> tuple[np.ndarray, np.ndarray]:
    """Cluster-robust (sandwich) covariance of the fit, clusters = cl (one code per source × session):
    cov · Σ_c g_c g_cᵀ · cov × G/(G − k), g_c = Σ_{i∈c} w_i r_i x_i over both axes' rows, G the source's clusters with
    used dots, k (per source) the parameters per axis each cluster's residuals are fitted with. Errors shared by a session's dots (the same pointing, simultaneous detector
    pairs, a neighbour or a ghost that moves with the roll) show up as session means scattering beyond the formal error,
    which the formal covariance (and χ²_ν, dominated by the many dots) misses. Returns (cov_robust, G)."""
    _, c = np.unique(cl, return_inverse=True)
    c = c.ravel()
    K = int(c.max()) + 1 if c.size else 0
    used = wk > 0
    cs = np.zeros(K, np.int64)
    cs[c] = s
    g = np.stack([np.bincount(c, wk * re, K), np.bincount(c, wk * rn, K), np.bincount(c, wk * re * t, K),
                  np.bincount(c, wk * rn * t, K), np.bincount(c, wk * (re * Pe + rn * Pn), K)], axis=1)
    G = np.bincount(cs, np.bincount(c, used, K) > 0, N)
    M = np.zeros((N, 5, 5))
    for a in range(5):
        for b in range(a, 5):
            M[:, a, b] = M[:, b, a] = np.bincount(cs, g[:, a] * g[:, b], N)
    with np.errstate(invalid="ignore", divide="ignore"):
        corr = np.where(G > k + 1, G / (G - k), np.nan)
    return np.einsum("nij,njk,nkl->nil", cov, M, cov) * corr[:, None, None], G


def clean_mask(d: pd.DataFrame, s: np.ndarray, N: int, east: np.ndarray, north: np.ndarray) -> np.ndarray:
    """Dots whose position can be trusted for a motion fit (False: left out from the start).
    (a) A masked pixel at the peak that is not saturation (core MASK_BITS without SAT_BITS, e.g. PERSIST): the centroid
    lands on the wings, 3–8″ off (hunt: 1.4–1.8% of member dots, mostly in one visit). A source keeps them all if every
    one of its dots has it. Saturated dots stay (calib gives them their own, larger error).
    (b) Two or more dots of one photo in one source (a bright star plus fragments of its own wings): only the one
    nearest the source's median position stays."""
    from skyshift_core import MASK_BITS, SAT_BITS
    keep = np.ones(len(d), bool)
    if "flags_peak" in d:
        fp = pd.to_numeric(d["flags_peak"], errors="coerce").fillna(0).to_numpy().astype(np.int64)
        bad = ((fp & (MASK_BITS & ~SAT_BITS)) != 0) & ((fp & SAT_BITS) == 0)
        all_bad = np.bincount(s, ~bad, N) == 0
        keep &= ~bad | all_bad[s]
    if "photo" in d:
        ph = d["photo"].to_numpy(np.int64)
        ok = ph >= 0
        if "obs_id" in d and "detector" in d:  # photos missing from the photo list: by file
            ph = np.where(ok, ph, -1 - pd.factorize(d["obs_id"].astype(str) + "D" + d["detector"].astype(str))[0])
        r = np.hypot(east, north)
        o = np.lexsort((r, ph, s))
        first = np.ones(o.size, bool)
        first[1:] = (s[o][1:] != s[o][:-1]) | (ph[o][1:] != ph[o][:-1])
        dup = np.zeros(len(d), bool)
        dup[o] = ~first
        keep &= ~dup
    return keep


def _rel_col(d: pd.DataFrame) -> str:
    """calib's epoch-to-epoch error when present (without each star's constant offset from Gaia), else pos_err."""
    return "pos_err_rel_arcsec" if "pos_err_rel_arcsec" in d else "pos_err_arcsec"


def _setup(d: pd.DataFrame):
    """The usable dots and, per dot, its source's median position, tangent-plane offset and observer position."""
    pe_ = d[_rel_col(d)].to_numpy(np.float64)
    ok = np.isfinite(pe_) & (pe_ > 0) & np.isfinite(d["mjd"].to_numpy(np.float64))
    d = d[ok]
    sid_u, s = np.unique(d["source_id"].to_numpy(np.int64), return_inverse=True)
    s = s.ravel()
    N = sid_u.size
    ra, dec = d["ra"].to_numpy(np.float64), d["dec"].to_numpy(np.float64)
    ra0 = group_median(s, ra, N)
    dec0 = group_median(s, dec, N)
    east, north = tangent_offset_arcsec(ra0[s], dec0[s], ra, dec)
    sc = np.stack([d[c].to_numpy(np.float64) for c in ("x_sc_km", "y_sc_km", "z_sc_km")], axis=1) if "x_sc_km" in d else None
    xyz = observer_xyz_au(d["mjd"].to_numpy(np.float64), sc)
    return d, sid_u, s, N, ra0, dec0, east, north, sc, xyz


def frame_residuals(d: pd.DataFrame, crossids: pd.DataFrame | None) -> pd.DataFrame:
    """The reference dots' residuals against Gaia (obs_id, detector, e, n ″), as frame_offsets measures them: tiles.py
    collects them from every tile and takes the per-photo medians over the whole zone (photo_offsets)."""
    d, sid_u, s, N, ra0, dec0, east, north, sc, xyz = _setup(d)
    refs = frame_refs(d, east, north, ra0, dec0, s, sid_u, crossids, xyz)
    if refs is None:
        return pd.DataFrame({"obs_id": pd.Series(dtype=str), "detector": pd.Series(dtype=np.int8),
                             "e": pd.Series(dtype=np.float64), "n": pd.Series(dtype=np.float64)})
    m, re, rn = refs
    ok = d["photo"].to_numpy(np.int64)[m] >= 0
    return pd.DataFrame({"obs_id": d["obs_id"].astype(str).to_numpy()[m][ok], "detector": d["detector"].to_numpy(np.int8)[m][ok],
                         "e": re[ok], "n": rn[ok]})


def photo_offsets(res: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Per-photo median offset (oe, on ″) of frame_residuals rows, `good` with >= ASTRO_FRAME_MIN_STARS of them; and
    frame_offsets' statistics."""
    key = pd.MultiIndex.from_arrays([res["obs_id"].astype(str).to_numpy(), res["detector"].to_numpy(np.int64)])
    code, uniq = pd.factorize(key)
    P = len(uniq)
    n_ref = np.bincount(code, minlength=P)
    oe = group_median(code, res["e"].to_numpy(np.float64), P)
    on = group_median(code, res["n"].to_numpy(np.float64), P)
    good = (n_ref >= C.ASTRO_FRAME_MIN_STARS) & np.isfinite(oe) & np.isfinite(on)
    out = pd.DataFrame({"obs_id": [k[0] for k in uniq], "detector": np.array([k[1] for k in uniq], np.int8),
                        "oe": oe, "on": on, "n_ref": n_ref.astype(np.int32), "good": good})
    off = np.hypot(oe[good], on[good])
    st = {"photos_corrected": int(good.sum()), "frame_ref_dots": int(len(res)),
          "frame_offset_median_arcsec": float(np.median(off)) if off.size else None,
          "frame_offset_p90_arcsec": float(np.percentile(off, 90)) if off.size else None}
    return out, st


def fit(d: pd.DataFrame, crossids: pd.DataFrame | None = None, frame: bool = True,
        offsets: pd.DataFrame | None = None) -> tuple[pd.DataFrame, dict]:
    """Member dots (source_id, ra, dec, mjd, pos_err_arcsec, visit, photo, snr, x/y/z_sc_km) → one row per
    fitted source (COLUMNS). offsets: per-photo frame offsets from photo_offsets (else measured on these dots)."""
    d, sid_u, s, N, ra0, dec0, east, north, sc, xyz = _setup(d)
    n_dots = np.bincount(s, minlength=N)
    mjd = d["mjd"].to_numpy(np.float64)
    err = d[_rel_col(d)].to_numpy(np.float64)
    const2 = np.maximum(d["pos_err_arcsec"].to_numpy(np.float64) ** 2 - err ** 2, 0.0)  # star-constant part (calib)
    st: dict = {"spacecraft_position_dots": int(np.isfinite(sc).all(axis=1).sum()) if sc is not None else 0}
    corrected = np.zeros(N, bool)
    if frame:
        fe, fn, fst = frame_offsets(d, east, north, ra0, dec0, s, sid_u, crossids, xyz, offsets)
        st.update(fst)
        east, north = east - fe, north - fn
        corrected = np.bincount(s, (fe != 0) | (fn != 0), N) > 0
    Pe, Pn = parallax_factors(ra0[s], dec0[s], xyz)
    n_visits = count_distinct(s, d["visit"].to_numpy(np.int64), N)
    use_plx = n_visits >= C.ASTRO_PARALLAX_MIN_VISITS
    w = 1.0 / err ** 2
    keep0 = clean_mask(d, s, N, east, north) if C.ASTRO_CLEAN_DOTS else np.ones(len(d), bool)
    st["dots_cleaned"] = int((~keep0).sum())
    keep = keep0.copy()
    zp = C.ASTRO_DETECTOR_ZEROPOINTS and "detector" in d
    if zp:  # one position zero point per source × detector: motion only from changes within a detector
        _, z = np.unique((s.astype(np.int64) << 8) | (d["detector"].to_numpy(np.int64) & 0xFF), return_inverse=True)
        z = z.ravel()
        Z = int(z.max()) + 1 if z.size else 0
    for it in range(2):
        wk = np.where(keep, w, 0.0)
        sw = np.bincount(s, wk, N)
        tref = np.bincount(s, wk * mjd, N) / np.maximum(sw, 1e-300)
        t = (mjd - tref[s]) / DAYS_PER_JYEAR
        par, cov = _solve(s, N, wk, t, east, north, Pe, Pn, use_plx)
        n_used = np.bincount(s, keep, N)
        npar = np.where(use_plx, 5, 4)
        # the motion problem (pm, parallax): the plain fit, or with detector zero points removed (Frisch–Waugh)
        T, E, Nn, PE, PN, cv = t, east, north, Pe, Pn, cov
        if zp:
            swz = np.maximum(np.bincount(z, wk, Z), 1e-300)
            def dm(v):
                return v - (np.bincount(z, wk * v, Z) / swz)[z]
            Tz, Ez, Nz, PEz, PNz = dm(t), dm(east), dm(north), dm(Pe), dm(Pn)
            parz, covz = _solve(s, N, wk, Tz, Ez, Nz, PEz, PNz, use_plx)
            okz = np.isfinite(parz[:, 2]) & np.isfinite(parz[:, 3])
            par[okz, 2:] = parz[okz, 2:]
            cv = np.where(okz[:, None, None], covz, cov)
            o = okz[s]
            T, E, Nn, PE, PN = (np.where(o, a, b) for a, b in ((Tz, t), (Ez, east), (Nz, north), (PEz, Pe), (PNz, Pn)))
            p0 = np.where(okz[:, None], np.nan_to_num(parz[:, :2]), par[:, :2])
            zsrc = np.zeros(Z, np.int64)
            zsrc[z] = s
            n_zp = np.bincount(zsrc, np.bincount(z, keep, Z) > 0, N)  # zero points with used dots
            npar = np.where(okz, 2 * n_zp + 2 + use_plx, npar)
        else:
            p0 = par[:, :2]
        pi = par[s]
        re = E - (p0[s, 0] + pi[:, 2] * T + pi[:, 4] * PE)
        rn = Nn - (p0[s, 1] + pi[:, 3] * T + pi[:, 4] * PN)
        chi = np.bincount(s, wk * (re * re + rn * rn), N)
        with np.errstate(invalid="ignore", divide="ignore"):
            chi2_nu = chi / np.maximum(2 * n_used - npar, 1)
        if it == 0:
            infl = np.sqrt(np.maximum(1.0, chi2_nu))[s]
            keep = keep0 & (np.maximum(np.abs(re), np.abs(rn)) <= C.ASTRO_CLIP_SIGMA * err * infl)
    from scipy.stats import chi2 as chi2_dist
    dof = np.maximum(2 * n_used - npar, 1)
    excess = chi2_nu > chi2_dist.ppf(C.ASTRO_CHI2_INFLATE_P, dof) / dof
    scale = np.where(excess, np.sqrt(np.maximum(1.0, chi2_nu)), 1.0)
    var = np.einsum("nii->ni", cov) * scale[:, None] ** 2  # positions: the plain fit's
    var[:, 2:] = np.einsum("nii->ni", cv)[:, 2:] * scale[:, None] ** 2  # motion: the motion problem's
    if C.ASTRO_SESSION_ROBUST:
        # errors shared within a session: the session-clustered sandwich where it is larger (enough sessions), else
        # the formal errors × √max(1, χ²_ν) (no chance allowance: few sessions cannot show a correlated excess)
        sess = d["session"].to_numpy(np.int64) if "session" in d else np.arange(len(d), dtype=np.int64)
        cl = (s.astype(np.int64) << 32) | (sess & 0xFFFFFFFF)
        cov_rob, n_sess = _cluster_cov(s, N, cl, wk, T, re, rn, PE, PN, cv, np.where(use_plx, 2.5, 2.0))
        enough = n_sess >= C.ASTRO_ROBUST_MIN_SESSIONS
        var_few = var / scale[:, None] ** 2 * np.maximum(1.0, np.nan_to_num(chi2_nu, nan=1.0))[:, None]
        var = np.where(enough[:, None], np.fmax(var, np.einsum("nii->ni", cov_rob)), np.maximum(var, var_few))
    if C.ASTRO_DETECTOR_ROBUST and "detector" in d:
        # detectors that disagree about the motion (a ghost, halo or neighbour that touches some bands in one visit):
        # the sandwich over detector clusters (with zero points: each detector's own motion; without: visit × detector),
        # where it beats its chance level (χ² quantile)
        det_ = d["detector"].to_numpy(np.int64) & 0xFF
        if zp:
            cl, k_ = (s.astype(np.int64) << 8) | det_, np.where(use_plx, 1.5, 1.0)
        else:
            vis = d["visit"].to_numpy(np.int64) if "visit" in d else np.zeros(len(d), np.int64)
            cl, k_ = (s.astype(np.int64) << 32) | ((vis & 0xFFFF) << 8) | det_, np.where(use_plx, 2.5, 2.0)
        cov_det, n_cl = _cluster_cov(s, N, cl, wk, T, re, rn, PE, PN, cv, k_)
        nu = np.maximum(n_cl - k_, 1.0)
        lim = chi2_dist.ppf(C.ASTRO_DETECTOR_ROBUST_P, nu) / nu
        vd = np.einsum("nii->ni", cov_det)
        excess_d = (vd > lim[:, None] * np.einsum("nii->ni", cv)) & (n_cl > k_ + 1)[:, None]
        var = np.where(excess_d, np.fmax(var, vd), var)
    sig = np.sqrt(var)
    extra = np.bincount(s, const2, N) / np.maximum(n_dots, 1)  # 0 without calib's floor_rel
    for j in (0, 1):
        sig[:, j] = np.where(extra > 0, np.sqrt(sig[:, j] ** 2 + extra), sig[:, j])
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
    good = (n_used >= C.ASTRO_MIN_DOTS) & np.isfinite(pmra) & (span > 0)  # used dots: clean_mask can leave 1 per visit
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
