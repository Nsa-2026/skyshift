"""Step `astrometry`: SPHEREx-only position, proper motion (and parallax) of every fixed source.

Per source with >= ASTRO_MIN_DOTS member dots (and ASTRO_MIN_MOTION_DOF, below), a weighted least-squares fit on the tangent plane about
the source's median position:
    east_i  = x0 + pmra  · t_i + ϖ · Pe_i
    north_i = y0 + pmdec · t_i + ϖ · Pn_i
t_i in Julian years from the source's weighted mean epoch (written as epoch_mjd, so position and
motion are uncorrelated); weights 1 / pos_err_rel_arcsec² per axis (calib's epoch-to-epoch error, without each
star's constant offset from Gaia; pos_err_arcsec where absent), and that constant part, the mean of
pos_err_arcsec² − pos_err_rel_arcsec² over the source's dots, added to ra_err/dec_err only; Pe, Pn the parallax factors from the
observer's barycentric position: the Earth's (astropy's built-in ephemeris) plus SPHEREx's geocentric
offset (X_SC, Y_SC, Z_SC in km, from the dot files or the photo list, when present; < 1e-4 AU, so it
barely matters). One refit without dots beyond ASTRO_CLIP_SIGMA.

The motion model (16 Nov, skyshift_core.motion; column `fit`): with a Gaia DR3 match that has a parallax, ϖ is held at
Gaia's through a prior with Gaia's error (`pm+gaia_plx`, ASTRO_GAIA_PLX_PRIOR): with two visits six months apart a fit
without parallax measured μ + ϖ·k (hunt: k ≈ −4.1 mas/yr per mas in RA), so nearby stars got a fake extra motion. Without
Gaia, ϖ is fitted only with >= 3 visits whose parallax factors are not a straight line in time (both seasons: `pm+plx`);
else none (`pm`, or `pm_leak` when |k| > LEAK_SMALL). plx_leak_ra/dec = k, the fit's own weighted leak (in the zero-point
frame). Steadiness: each visit's mean residual against the fit (error √(scale²/Σw + ASTRO_VISIT_FLOOR_ARCSEC²); scale
ASTRO_VISIT_ERR_SCALE on calib's errors, _RAW on the dot files' own) gives visit_chi2_nu and wobble_arcsec (their
1/error²-weighted RMS distance from the fit); `unsteady` when it exceeds
core's UNSTEADY_CHI2_NU (the visits go out and back: blends, detector position, saturation).
Hunt, two visits (16 Nov, from the 15 Nov tables, pm − ϖ_Gaia·k): leak slope −4.12 → −0.02 (RA), +0.81 → −0.18 (Dec)
mas/yr per mas; Gaia stars at ϖ 20–50 mas |pull| > 3 17.6% → 0%. t_wise0855 (3 visits, refit): RA leak slope +1.38 →
+0.20; 5.7% of Gaia stars unsteady, their |pm − Gaia| median 358 vs 111 mas/yr.
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
- ASTRO_MIN_MOTION_DOF (motion_dof): with the zero points a detector seen in one visit only measures nothing between
  the visits, so a fit is written only when the detectors seen in both visits leave the motion this many degrees of
  freedom (2 × (their used dots − their number) − 2, − 1 with parallax). At 6 dots that was often 0–4: a χ² that
  could not check the errors and a motion from one or two dots.
ra/dec and their errors stay those of the plain fit (no zero points: the position is every detector's mean).

Frame correction (ASTRO_FRAME_CORRECT): each photo's WCS can be off by a fraction of an arcsec as a
whole. Quiet Gaia stars (crossids, |pm| < ASTRO_FRAME_PM_MAX) are moved to the photo's time; the median
offset of their dots in that photo (>= ASTRO_FRAME_MIN_STARS stars) is subtracted from all its dots.
This ties our frame to Gaia's (proper motions become absolute); each star weighs 1/thousands in its
photo's offset, so the comparison with Gaia below stays honest.

Saturated dots (ASTRO_SAT_OFFSET): a dot with OVERFLOW or NONLINEAR at its peak is centroided on the wings, and calib
leaves it uncorrected. Such dots sit a constant fraction of a pixel along the detector's −x axis, which points west in
one visit and east in the other (the 180° roll), so a saturated star got a fake RA motion (hunt: −110 mas/yr at
G 8–10) that the source × detector zero points cannot absorb (they take sky-fixed offsets only). Per detector and
depth bin (sat_depth: the source's magnitude in the detector's band against the saturation limit), the median offset
(Δx, Δy) in pixels of the saturated dots of the quiet Gaia stars against Gaia, after the frame offset, is subtracted
from every saturated dot through its photo's Jacobian
(motion_io's, from the whole dot file). Fitted once per zone, like the frame offsets (tiles: sat_residuals per tile,
sat_offsets on all of them); the report lists the offsets. The frame offsets then use unsaturated dots only.

Unsaturated dots (ASTRO_PIXEL_MAP, ASTRO_BRIGHT_OFFSET; 15 Nov): calib's per-photo distortion is at most quadratic, and
after it and the frame offsets the quiet Gaia stars' dots still sit off Gaia by amounts fixed in detector pixels, which
the roll turns into fake motion just the same: (1) a static map per detector (pixel_offsets `map`: the median offset in
ASTRO_MAP_CELL_PX cells, bilinear between cell centres), which gives each star its own fake motion because it sits at
another x in each visit; (2) a brightness term (`bright`: the median offset per bin of the dot's own depth, bright_level:
its AB magnitude from flux_ujy against its detector's saturation), the centroid moving along −x as a star nears
saturation below the flags and along +x at the faint end, which gave bright stars a shared westward fake motion and faint
ones an eastward one. Both are fitted on the zone's unsaturated dots of quiet Gaia
stars after the frame offsets (each on what the other leaves, ASTRO_PIXEL_ITER times; centroid and ePSF positions
separately) and subtracted from every unsaturated dot through its photo's Jacobian. Fitted once per zone from the frame
residuals (tiles: frame_residuals with the Jacobians per tile, pixel_offsets on all of them).

Writes results/<zone>/astrometry.parquet and astrometry_report.json: SPHEREx proper motion vs Gaia for
matched stars (bias, robust scatter, median error, pull scatter), overall, by median dot S/N and by Gaia G,
with and without the frame correction, the saturated-dot offsets, and the fast Gaia stars one by one.
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd
from skyshift_core import motion as M
from skyshift_core import tangent_offset_arcsec
from skyshift_core.astrometry import DAYS_PER_JYEAR

from . import config as C
from . import log
from .motion_io import member_dots, member_dots_raw
from .spectra import count_distinct, group_median
from .store import Store, results_rel

L = log.get("astrometry")

AU_KM = M.AU_KM
COLUMNS = ["source_id", "ra", "dec", "epoch_mjd", "ra_err_arcsec", "dec_err_arcsec", "pmra", "pmdec", "pmra_err",
           "pmdec_err", "pm", "pm_snr", "parallax", "parallax_err", "fit", "n_dots", "n_used", "n_visits", "span_yr",
           "chi2_nu", "snr_median", "frame_corrected", "plx_leak_ra", "plx_leak_dec", "visit_chi2_nu", "wobble_arcsec", "unsteady"]
# per source × visit (astrometry_visits.parquet, 16 Nov): the fitted sources fast enough for highpm's fit route
VISIT_COLUMNS = ["source_id", "visit", "mjd", "ra", "dec", "ra_err_arcsec", "dec_err_arcsec", "n_dots"]
JAC_COLUMNS = ["jac_ex", "jac_ey", "jac_nx", "jac_ny"]  # ″ per pixel: east per x, east per y, north per x, north per y
SAT_COLUMNS = ["detector", "depth_bin", "depth_min", "depth_max", "dx_px", "dy_px", "dx_err_px", "dy_err_px", "n",
               "applied"]


# the observer and the parallax factors live in core (skyshift_core.motion); kept here under their old names
observer_xyz_au = M.observer_xyz_au
parallax_factors = M.parallax_factors


def frame_refs(d: pd.DataFrame, east: np.ndarray, north: np.ndarray, ra0: np.ndarray, dec0: np.ndarray,
               s: np.ndarray, sid_u: np.ndarray, crossids: pd.DataFrame | None,
               xyz: np.ndarray, unsat: bool = False) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """(mask of the reference dots: quiet Gaia stars, their east and north residual against Gaia ″), or None.
    unsat: unsaturated dots only (the frame offsets, with ASTRO_SAT_OFFSET: a saturated dot's offset turns with the roll)."""
    if crossids is None or not len(crossids) or "gaia_ra" not in crossids:
        return None
    x = crossids.set_index("source_id").reindex(sid_u)
    pm = np.hypot(x["gaia_pmra"].to_numpy(np.float64), x["gaia_pmdec"].to_numpy(np.float64))
    ref_src = np.isfinite(x["gaia_ra"].to_numpy(np.float64)) & (np.nan_to_num(pm, nan=np.inf) < C.ASTRO_FRAME_PM_MAX)
    m = ref_src[s]
    if unsat:
        m &= ~sat_dots(d)
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
    refs = frame_refs(d, east, north, ra0, dec0, s, sid_u, crossids, xyz, unsat=C.ASTRO_SAT_OFFSET)
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


def _solve(s, N, w, t, east, north, Pe, Pn, use_plx, prior_val=None, prior_w=None):
    """Batched normal equations → (params (N,5), cov (N,5,5)). prior_val, prior_w (N,): a parallax prior (″, 1/″²; weight
    0 = none) on the sources that fit parallax (Gaia's: skyshift_core.motion.gaia_prior)."""
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
    if prior_w is not None:
        A[:, 4, 4] += prior_w
        b[:, 4] += prior_w * prior_val
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


def motion_dof(s: np.ndarray, N: int, keep: np.ndarray, visit: np.ndarray, use_plx: np.ndarray,
               z: np.ndarray | None = None, okz: np.ndarray | None = None) -> np.ndarray:
    """Per source, the degrees of freedom the motion keeps after its position zero points: 2 × (used dots − zero
    points) − (2 + parallax), over the zero points whose used dots span two or more visits. In a source seen in several
    visits, a detector seen in one of them only measures nothing between visits (its zero point takes up all its dots),
    so it counts for nothing; a source seen in one visit counts every detector (its motion is within the visit).
    z: each dot's source × detector zero point (None, or okz False for the source: one zero point, the plain fit).
    Hunt (14 Nov): the 6-dot fits had 0–4 of them; 1.8×10⁶ mas/yr at 18.9σ on a G 16.7 star from none (−2)."""
    keep = np.asarray(keep, bool)
    vis = np.asarray(visit, np.int64) & 0xFFFF
    plx = np.asarray(use_plx, bool).astype(np.int64)
    sk = s[keep].astype(np.int64)
    n_vis = np.bincount(np.unique((sk << 16) | vis[keep]) >> 16, minlength=N)  # visits with used dots, per source

    def dof(zz: np.ndarray) -> np.ndarray:
        Z = int(zz.max()) + 1 if zz.size else 0
        zk = zz[keep].astype(np.int64)
        nv = np.bincount(np.unique((zk << 16) | vis[keep]) >> 16, minlength=Z)  # distinct visits per zero point
        zsrc = np.zeros(Z, np.int64)
        zsrc[zz] = s
        use = (nv >= 2) | ((nv >= 1) & (n_vis[zsrc] <= 1))
        return 2 * (np.bincount(s, keep & use[zz], N) - np.bincount(zsrc, use, N)) - 2 - plx

    if z is None:  # no zero points: the source's position is its one zero point
        return dof(s).astype(np.int64)
    out = dof(z)
    if okz is not None and not np.all(okz):  # the zero-point fit failed for these: they keep the plain fit
        out = np.where(okz, out, dof(s))
    return out.astype(np.int64)


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


def frame_residuals(d: pd.DataFrame, crossids: pd.DataFrame | None, jac: pd.DataFrame | None = None) -> pd.DataFrame:
    """The reference dots' residuals against Gaia (obs_id, detector, e, n ″), as frame_offsets measures them: tiles.py
    collects them from every tile and takes the per-photo medians over the whole zone (photo_offsets). With the photos'
    Jacobians (photo_jacobians' table) also what pixel_offsets needs: PIX_COLUMNS and JAC_COLUMNS."""
    d, sid_u, s, N, ra0, dec0, east, north, sc, xyz = _setup(d)
    refs = frame_refs(d, east, north, ra0, dec0, s, sid_u, crossids, xyz, unsat=C.ASTRO_SAT_OFFSET)
    if refs is None:
        return pd.DataFrame({"obs_id": pd.Series(dtype=str), "detector": pd.Series(dtype=np.int8),
                             "e": pd.Series(dtype=np.float64), "n": pd.Series(dtype=np.float64)})
    m, re, rn = refs
    ok = d["photo"].to_numpy(np.int64)[m] >= 0
    out = pd.DataFrame({"obs_id": d["obs_id"].astype(str).to_numpy()[m][ok], "detector": d["detector"].to_numpy(np.int8)[m][ok],
                        "e": re[ok], "n": rn[ok]})
    if jac is not None:
        idx = np.flatnonzero(m)[ok]
        out = pd.concat([out, _pix_columns(d, idx, jac)], axis=1)
    return out


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


# --- saturated dots: an offset fixed in detector pixels ------------------------------------------------------------
def sat_dots(d: pd.DataFrame) -> np.ndarray:
    """Dots with OVERFLOW or NONLINEAR at the peak (CALIB_SAT_BITS): centroided on the wings, never an ePSF position."""
    if "flags_peak" not in d or not len(d):
        return np.zeros(len(d), bool)
    fp = pd.to_numeric(d["flags_peak"], errors="coerce").fillna(0).to_numpy().astype(np.int64)
    sat = (fp & C.CALIB_SAT_BITS) != 0
    if "pos_psf" in d:
        sat &= ~d["pos_psf"].fillna(False).to_numpy(bool)
    return sat


def photo_jacobians(d: pd.DataFrame, jac: np.ndarray) -> pd.DataFrame:
    """Per photo of d: obs_id, detector and its Jacobian (JAC_COLUMNS) from motion_io.member_dots_raw's `jac` (rows = d's
    `photo`; calib.photo_jacobian over the whole dot file, so a tile gets the untiled values). NaN: not measurable."""
    ph = d["photo"].to_numpy(np.int64) if len(d) else np.zeros(0, np.int64)
    u, i = np.unique(ph, return_index=True)
    ok = (u >= 0) & (u < len(jac))
    u, i = u[ok], i[ok]
    J = np.asarray(jac, np.float64)[u].reshape(len(u), 4)
    out = pd.DataFrame({"obs_id": d["obs_id"].iloc[i].astype(str).to_numpy(),
                        "detector": d["detector"].iloc[i].to_numpy(np.int8)})
    for k, c in enumerate(JAC_COLUMNS):
        out[c] = J[:, k]
    return out


def _jacobian_at(d: pd.DataFrame, idx: np.ndarray, jac: pd.DataFrame | None) -> np.ndarray:
    """(len(idx), 2, 2) Jacobian of the photo of each dot d.iloc[idx], from photo_jacobians' table (NaN: none)."""
    out = np.full((idx.size, 2, 2), np.nan)
    if jac is None or not len(jac) or not idx.size:
        return out
    key = pd.MultiIndex.from_arrays([jac["obs_id"].astype(str).to_numpy(), jac["detector"].to_numpy(np.int64)])
    pos = key.get_indexer(pd.MultiIndex.from_arrays([d["obs_id"].iloc[idx].astype(str).to_numpy(),
                                                     d["detector"].iloc[idx].to_numpy(np.int64)]))
    ok = pos >= 0
    out[ok] = jac[JAC_COLUMNS].to_numpy(np.float64)[pos[ok]].reshape(-1, 2, 2)
    return out


def sat_depth(crossids: pd.DataFrame | None, sid: np.ndarray, det: np.ndarray) -> np.ndarray:
    """How deep a source saturates in a detector: its catalogue magnitude in the detector's band (crossids j, k, w1, w2;
    Vega → AB) minus the AB magnitude at which half the stars saturate there (ASTRO_SAT_DEPTH_BANDS); < 0 = deeper.
    NaN without that magnitude."""
    out = np.full(len(sid), np.nan)
    if crossids is None or not len(crossids) or not len(sid):
        return out
    det = np.asarray(det, np.int64)
    x = crossids.set_index("source_id")
    for k, (band, ab, sat_ab) in C.ASTRO_SAT_DEPTH_BANDS.items():
        m = det == k
        if m.any() and band in x:
            out[m] = x[band].reindex(np.asarray(sid, np.int64)[m]).to_numpy(np.float64) + ab - sat_ab
    return out


def _empty_sat_rows() -> pd.DataFrame:
    return pd.DataFrame({"source_id": pd.Series(dtype=np.int64), "obs_id": pd.Series(dtype=str), "detector": pd.Series(dtype=np.int8),
                         **{c: pd.Series(dtype=np.float64) for c in ("depth", "e", "n", *JAC_COLUMNS)}})


def _sat_rows(d: pd.DataFrame, idx: np.ndarray, east, north, ra0, dec0, s, sid_u, crossids, xyz,
              jac: pd.DataFrame | None) -> tuple[np.ndarray, pd.DataFrame]:
    """Of the saturated dots d.iloc[idx], those of quiet Gaia stars (frame_refs' stars): (their rows in d, a table
    source_id, obs_id, detector, depth (sat_depth), e, n (″ against Gaia, as frame_refs), JAC_COLUMNS)."""
    refs = frame_refs(d.iloc[idx], east[idx], north[idx], ra0, dec0, s[idx], sid_u, crossids, xyz[idx]) if idx.size else None
    if refs is None:
        return np.zeros(0, np.int64), _empty_sat_rows()
    m, re, rn = refs
    k = idx[m]
    J = _jacobian_at(d, k, jac).reshape(-1, 4)
    det = d["detector"].iloc[k].to_numpy(np.int8)
    rows = pd.DataFrame({"source_id": sid_u[s[k]], "obs_id": d["obs_id"].iloc[k].astype(str).to_numpy(), "detector": det,
                         "depth": sat_depth(crossids, sid_u[s[k]], det), "e": re, "n": rn})
    for j, c in enumerate(JAC_COLUMNS):
        rows[c] = J[:, j]
    return k, rows


def sat_residuals(d: pd.DataFrame, crossids: pd.DataFrame | None, jac: pd.DataFrame | None) -> pd.DataFrame:
    """The saturated dots of quiet Gaia stars: source_id, obs_id, detector, depth, e, n (″ against Gaia, no frame offset
    removed) and their photo's Jacobian. tiles.py collects them from every tile and fits the zone's offsets (sat_offsets)."""
    sat = sat_dots(d)
    if not sat.any() or crossids is None:
        return _empty_sat_rows()
    sid = d["source_id"].to_numpy(np.int64)
    d = d[np.isin(sid, np.unique(sid[sat]))]  # whole sources: their median positions are fit's
    d, sid_u, s, N, ra0, dec0, east, north, sc, xyz = _setup(d)
    return _sat_rows(d, np.flatnonzero(sat_dots(d)), east, north, ra0, dec0, s, sid_u, crossids, xyz, jac)[1]


def _sat_bin_row(det: int, label: str, lo: float, hi: float, dx: np.ndarray, dy: np.ndarray) -> dict:
    n = int(dx.size)
    mx, my = (float(np.median(dx)), float(np.median(dy))) if n else (np.nan, np.nan)
    # error of a median: 1.2533 σ/√n, σ from the MAD
    ex = 1.2533 * 1.4826 * float(np.median(np.abs(dx - mx))) / np.sqrt(n) if n else np.nan
    ey = 1.2533 * 1.4826 * float(np.median(np.abs(dy - my))) / np.sqrt(n) if n else np.nan
    return {"detector": det, "depth_bin": label, "depth_min": lo, "depth_max": hi, "dx_px": mx, "dy_px": my,
            "dx_err_px": ex, "dy_err_px": ey, "n": n, "applied": bool(n >= C.ASTRO_SAT_MIN_DOTS)}


def sat_offsets(res: pd.DataFrame, offsets: pd.DataFrame | None = None) -> pd.DataFrame:
    """The saturated-dot offset in detector pixels: per detector (depth_bin "all") and per detector × ASTRO_SAT_DEPTH_BINS
    bin, the median (dx_px, dy_px) of sat_residuals rows, after removing each photo's frame offset (offsets: photo_offsets'
    table, `good` rows; None: none). `applied` with >= ASTRO_SAT_MIN_DOTS dots."""
    e = res["e"].to_numpy(np.float64) if len(res) else np.zeros(0)
    n = res["n"].to_numpy(np.float64) if len(res) else np.zeros(0)
    if offsets is not None and len(offsets) and len(res):
        o = offsets[offsets["good"].to_numpy(bool)]
        key = pd.MultiIndex.from_arrays([o["obs_id"].astype(str).to_numpy(), o["detector"].to_numpy(np.int64)])
        pos = key.get_indexer(pd.MultiIndex.from_arrays([res["obs_id"].astype(str).to_numpy(), res["detector"].to_numpy(np.int64)]))
        ok, p = pos >= 0, np.maximum(pos, 0)
        e = e - np.where(ok, o["oe"].to_numpy(np.float64)[p] if len(o) else 0.0, 0.0)
        n = n - np.where(ok, o["on"].to_numpy(np.float64)[p] if len(o) else 0.0, 0.0)
    J = res[JAC_COLUMNS].to_numpy(np.float64).reshape(-1, 2, 2) if len(res) else np.zeros((0, 2, 2))
    dj = J[:, 0, 0] * J[:, 1, 1] - J[:, 0, 1] * J[:, 1, 0]
    ok = np.isfinite(dj) & (np.abs(np.nan_to_num(dj)) > 1e-6) & np.isfinite(e) & np.isfinite(n)
    with np.errstate(invalid="ignore", divide="ignore"):
        dx = (J[:, 1, 1] * e - J[:, 0, 1] * n) / dj
        dy = (J[:, 0, 0] * n - J[:, 1, 0] * e) / dj
    det = res["detector"].to_numpy(np.int64) if len(res) else np.zeros(0, np.int64)
    dep = res["depth"].to_numpy(np.float64) if len(res) else np.zeros(0)
    edges = [-np.inf, *C.ASTRO_SAT_DEPTH_BINS, np.inf]
    rows = []
    for k in range(1, 7):
        mk = ok & (det == k)
        rows.append(_sat_bin_row(k, "all", np.nan, np.nan, dx[mk], dy[mk]))
        for lo, hi in zip(edges[:-1], edges[1:]):
            label = f"<{hi:g}" if not np.isfinite(lo) else f">={lo:g}" if not np.isfinite(hi) else f"{lo:g}..{hi:g}"
            with np.errstate(invalid="ignore"):
                m = mk & (dep >= lo) & (dep < hi)
            rows.append(_sat_bin_row(k, label, lo, hi, dx[m], dy[m]))
    return pd.DataFrame(rows, columns=SAT_COLUMNS)


def sat_shift(det: np.ndarray, depth: np.ndarray, J: np.ndarray, tab: pd.DataFrame | None) -> tuple[np.ndarray, np.ndarray]:
    """(east, north ″) to subtract from saturated dots of detectors det, depths `depth` (sat_depth; NaN: unknown) and
    photo Jacobians J (n, 2, 2): their depth bin's (dx, dy) where applied, else their detector's "all" one, else 0."""
    dx, dy = np.zeros(len(det)), np.zeros(len(det))
    if tab is None or not len(tab) or not len(det):
        return dx, dy
    t = tab[tab["applied"].to_numpy(bool)]
    for r in sorted(t.itertuples(), key=lambda r: r.depth_bin != "all"):  # "all" first, then the bins over it
        m = np.asarray(det) == r.detector
        if r.depth_bin != "all":
            with np.errstate(invalid="ignore"):
                m &= (depth >= r.depth_min) & (depth < r.depth_max)
        dx[m], dy[m] = r.dx_px, r.dy_px
    de = J[:, 0, 0] * dx + J[:, 0, 1] * dy
    dn = J[:, 1, 0] * dx + J[:, 1, 1] * dy
    ok = np.isfinite(de) & np.isfinite(dn)
    return np.where(ok, de, 0.0), np.where(ok, dn, 0.0)


def sat_report(tab: pd.DataFrame | None, dots: int = 0, corrected: int = 0) -> dict:
    """astrometry_report.json `saturated_offsets`: the table (pixels, per detector and depth bin) and dot counts."""
    def num(v, nd=4):
        return None if v is None or not np.isfinite(v) else round(float(v), nd)
    rows = [] if tab is None else [
        {"detector": int(r.detector), "depth_bin": r.depth_bin, "dx_px": num(r.dx_px), "dy_px": num(r.dy_px),
         "dx_err_px": num(r.dx_err_px), "dy_err_px": num(r.dy_err_px), "n": int(r.n), "applied": bool(r.applied)}
        for r in tab.itertuples()]
    return {"enabled": bool(C.ASTRO_SAT_OFFSET), "measured_on_dots": int(sum(r["n"] for r in rows if r["depth_bin"] == "all")),
            "saturated_dots": int(dots), "dots_corrected": int(corrected), "offsets": rows}


# --- unsaturated dots: a detector map and a brightness term, fixed in detector pixels ------------------------------
PIX_COLUMNS = ["source_id", "x", "y", "snr", "level", "psf"]
MAP_COLUMNS = ["kind", "detector", "cx", "cy", "dx_px", "dy_px", "n", "applied"]
BRIGHT_COLUMNS = ["kind", "detector", "bin", "lo", "hi", "dx_px", "dy_px", "dx_err_px", "dy_err_px", "n", "applied"]
KINDS = ("centroid", "psf")


def pixel_terms_on() -> bool:
    return bool(C.ASTRO_PIXEL_MAP or C.ASTRO_BRIGHT_OFFSET)


def bright_level(d: pd.DataFrame) -> np.ndarray:
    """The brightness the unsaturated offset follows, from the dot itself (no catalogue: a new object gets it too): its
    AB magnitude from flux_ujy (calibrated r = 2 px aperture, on the total-flux scale) minus the AB magnitude at which half
    the stars saturate in its detector (ASTRO_SAT_DEPTH_BANDS); < 0 = brighter. NaN without flux_ujy or detector, or
    flux <= 0. (The peak, peak_mjysr, tops out at ~100 MJy/sr in D1–D4 below the flags and separates the brightest
    unsaturated dots less well.)"""
    if "flux_ujy" not in d or "detector" not in d or not len(d):
        return np.full(len(d), np.nan)
    f = pd.to_numeric(d["flux_ujy"], errors="coerce").to_numpy(np.float64)
    det = d["detector"].to_numpy(np.int64)
    sat_ab = np.full(len(d), np.nan)
    for k, (_, _, ab) in C.ASTRO_SAT_DEPTH_BANDS.items():
        sat_ab[det == k] = ab
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(f > 0, 23.9 - 2.5 * np.log10(f), np.nan) - sat_ab


def _col(d: pd.DataFrame, name: str, default=np.nan, dtype=np.float64) -> np.ndarray:
    return d[name].to_numpy(dtype) if name in d else np.full(len(d), default, dtype)


def _pix_columns(d: pd.DataFrame, idx: np.ndarray, jac: pd.DataFrame | None) -> pd.DataFrame:
    """PIX_COLUMNS and JAC_COLUMNS of the dots d.iloc[idx] (x, y NaN without them: no map)."""
    sub = d.iloc[idx]
    out = pd.DataFrame({"source_id": sub["source_id"].to_numpy(np.int64), "x": _col(sub, "x").astype(np.float32), "y": _col(sub, "y").astype(np.float32),
                        "snr": _col(sub, "snr").astype(np.float32), "level": bright_level(sub).astype(np.float32),
                        "psf": sub["pos_psf"].fillna(False).to_numpy(bool) if "pos_psf" in sub else np.zeros(len(sub), bool)})
    J = _jacobian_at(d, idx, jac).reshape(-1, 4)
    for j, c in enumerate(JAC_COLUMNS):
        out[c] = J[:, j]
    return out


def _to_pixels(e: np.ndarray, n: np.ndarray, J: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(″ east, north) → (px along x, y) through J (n, 2, 2); NaN where J is singular or missing."""
    dj = J[:, 0, 0] * J[:, 1, 1] - J[:, 0, 1] * J[:, 1, 0]
    ok = np.isfinite(dj) & (np.abs(np.nan_to_num(dj)) > 1e-6)
    with np.errstate(invalid="ignore", divide="ignore"):
        dx = np.where(ok, (J[:, 1, 1] * e - J[:, 0, 1] * n) / dj, np.nan)
        dy = np.where(ok, (J[:, 0, 0] * n - J[:, 1, 0] * e) / dj, np.nan)
    return dx, dy


def _ncell() -> int:
    return int(np.ceil(C.DETECTOR_PX / C.ASTRO_MAP_CELL_PX))


def _map_eval(tab: pd.DataFrame | None, kind: np.ndarray, det: np.ndarray, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(Δx, Δy) px of the map at (x, y): bilinear between cell centres (clamped at the edges), 0 in cells not applied."""
    ox, oy = np.zeros(len(det)), np.zeros(len(det))
    if tab is None or not len(tab) or not len(det):
        return ox, oy
    nc, cell = _ncell(), float(C.ASTRO_MAP_CELL_PX)
    fx = np.clip(np.nan_to_num(np.asarray(x, np.float64), nan=-1.0) / cell - 0.5, 0.0, nc - 1.0)
    fy = np.clip(np.nan_to_num(np.asarray(y, np.float64), nan=-1.0) / cell - 0.5, 0.0, nc - 1.0)
    i0 = np.minimum(np.floor(fx).astype(np.int64), nc - 2)
    j0 = np.minimum(np.floor(fy).astype(np.int64), nc - 2)
    u, v = fx - i0, fy - j0
    good = np.isfinite(np.asarray(x, np.float64)) & np.isfinite(np.asarray(y, np.float64))
    t = tab[tab["applied"].to_numpy(bool)]
    for (kd, k), g in t.groupby(["kind", "detector"]):
        m = good & (kind == KINDS.index(kd)) & (det == k)
        if not m.any():
            continue
        for col, out in (("dx_px", ox), ("dy_px", oy)):
            G = np.zeros((nc, nc))
            G[g["cx"].to_numpy(np.int64), g["cy"].to_numpy(np.int64)] = g[col].to_numpy(np.float64)
            a, b, uu, vv = i0[m], j0[m], u[m], v[m]
            out[m] = (G[a, b] * (1 - uu) * (1 - vv) + G[a + 1, b] * uu * (1 - vv) + G[a, b + 1] * (1 - uu) * vv
                      + G[a + 1, b + 1] * uu * vv)
    return ox, oy


def _bright_eval(tab: pd.DataFrame | None, kind: np.ndarray, det: np.ndarray, level: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(Δx, Δy) px of the brightness table at each dot's level (its bin where applied; else 0, NaN level: 0)."""
    ox, oy = np.zeros(len(det)), np.zeros(len(det))
    if tab is None or not len(tab) or not len(det):
        return ox, oy
    t = tab[tab["applied"].to_numpy(bool)]
    for r in t.itertuples():
        with np.errstate(invalid="ignore"):
            m = (kind == KINDS.index(r.kind)) & (det == r.detector) & (level >= r.lo) & (level < r.hi)
        ox[m], oy[m] = r.dx_px, r.dy_px
    return ox, oy


def _median_err(v: np.ndarray) -> float:
    # error of a median: 1.2533 σ/√n, σ from the MAD
    return 1.2533 * 1.4826 * float(np.median(np.abs(v - np.median(v)))) / np.sqrt(v.size) if v.size else np.nan


def pixel_offsets(res: pd.DataFrame, offsets: pd.DataFrame | None = None) -> dict:
    """The unsaturated dots' offsets fixed in detector pixels, from frame_residuals rows with the pixel columns (jac
    given): each row's photo offset removed (offsets: photo_offsets' table, `good` rows; None: none), the residual turned
    into pixels, then per position kind (centroid, ePSF) and detector a map on ASTRO_MAP_CELL_PX cells (median of the
    S/N >= ASTRO_MAP_MIN_SNR dots, ASTRO_MAP_MIN_DOTS a cell) and a brightness table (median per ASTRO_BRIGHT_BINS bin of
    bright_level, ASTRO_BRIGHT_MIN_DOTS a bin), each fitted on what the other leaves, ASTRO_PIXEL_ITER times.
    Returns {"map": MAP_COLUMNS table, "bright": BRIGHT_COLUMNS table, "reference_dots": n}."""
    out = {"map": pd.DataFrame(columns=MAP_COLUMNS), "bright": pd.DataFrame(columns=BRIGHT_COLUMNS), "reference_dots": 0}
    if not len(res) or not set(JAC_COLUMNS) <= set(res.columns):
        return out
    e, n = res["e"].to_numpy(np.float64), res["n"].to_numpy(np.float64)
    if offsets is not None and len(offsets):
        o = offsets[offsets["good"].to_numpy(bool)]
        key = pd.MultiIndex.from_arrays([o["obs_id"].astype(str).to_numpy(), o["detector"].to_numpy(np.int64)])
        pos = key.get_indexer(pd.MultiIndex.from_arrays([res["obs_id"].astype(str).to_numpy(), res["detector"].to_numpy(np.int64)]))
        ok, p = pos >= 0, np.maximum(pos, 0)
        if len(o):
            e = e - np.where(ok, o["oe"].to_numpy(np.float64)[p], 0.0)
            n = n - np.where(ok, o["on"].to_numpy(np.float64)[p], 0.0)
    dx, dy = _to_pixels(e, n, res[JAC_COLUMNS].to_numpy(np.float64).reshape(-1, 2, 2))
    use = np.isfinite(dx) & np.isfinite(dy)
    det = res["detector"].to_numpy(np.int64)
    kind = res["psf"].to_numpy(bool).astype(np.int8)  # index into KINDS: 0 centroid, 1 ePSF
    x, y = res["x"].to_numpy(np.float64), res["y"].to_numpy(np.float64)
    snr, level = res["snr"].to_numpy(np.float64), res["level"].to_numpy(np.float64)
    nc, cell = _ncell(), C.ASTRO_MAP_CELL_PX
    in_map = use & np.isfinite(x) & np.isfinite(y) & (snr >= C.ASTRO_MAP_MIN_SNR)
    cx = np.clip(np.nan_to_num(x // cell, nan=0).astype(np.int64), 0, nc - 1)
    cy = np.clip(np.nan_to_num(y // cell, nan=0).astype(np.int64), 0, nc - 1)
    edges = np.array([-np.inf, *C.ASTRO_BRIGHT_BINS, np.inf])
    lb = np.digitize(level, edges) - 1  # NaN → len(edges) - 1: no bin
    in_bright = use & np.isfinite(level)
    mx = my = bx = by = np.zeros(len(res))
    mrows: list[dict] = []
    brows: list[dict] = []
    for it in range(max(1, C.ASTRO_PIXEL_ITER)):
        if C.ASTRO_PIXEL_MAP:
            mrows = []
            for (kd, k), idx in pd.DataFrame({"k": kind[in_map], "d": det[in_map]}).groupby(["k", "d"]).indices.items():
                ii = np.flatnonzero(in_map)[idx]
                c = cx[ii] * nc + cy[ii]
                g = pd.DataFrame({"c": c, "x": dx[ii] - bx[ii], "y": dy[ii] - by[ii]}).groupby("c").agg(
                    dx_px=("x", "median"), dy_px=("y", "median"), n=("x", "size"))
                for ci, r in g.iterrows():
                    mrows.append({"kind": KINDS[kd], "detector": int(k), "cx": int(ci // nc), "cy": int(ci % nc), "dx_px": float(r.dx_px),
                                  "dy_px": float(r.dy_px), "n": int(r.n), "applied": bool(r.n >= C.ASTRO_MAP_MIN_DOTS)})
            mtab = pd.DataFrame(mrows, columns=MAP_COLUMNS)
            mx, my = _map_eval(mtab, kind, det, x, y)
        if C.ASTRO_BRIGHT_OFFSET:
            brows = []
            for kc, kd in enumerate(KINDS):
                for k in range(1, 7):
                    mk = in_bright & (kind == kc) & (det == k)
                    if not mk.any():
                        continue
                    for b in range(len(edges) - 1):
                        m = mk & (lb == b)
                        if not m.any():
                            continue
                        vx, vy = dx[m] - mx[m], dy[m] - my[m]
                        lo, hi = edges[b], edges[b + 1]
                        label = f"<{hi:g}" if not np.isfinite(lo) else f">={lo:g}" if not np.isfinite(hi) else f"{lo:g}..{hi:g}"
                        brows.append({"kind": kd, "detector": k, "bin": label, "lo": float(lo), "hi": float(hi),
                                      "dx_px": float(np.median(vx)), "dy_px": float(np.median(vy)), "dx_err_px": _median_err(vx),
                                      "dy_err_px": _median_err(vy), "n": int(m.sum()), "applied": bool(m.sum() >= C.ASTRO_BRIGHT_MIN_DOTS)})
            btab = pd.DataFrame(brows, columns=BRIGHT_COLUMNS)
            bx, by = _bright_eval(btab, kind, det, level)
        if not (C.ASTRO_PIXEL_MAP and C.ASTRO_BRIGHT_OFFSET):
            break
    out["map"] = pd.DataFrame(mrows, columns=MAP_COLUMNS)
    out["bright"] = pd.DataFrame(brows, columns=BRIGHT_COLUMNS)
    out["reference_dots"] = int(use.sum())
    return out


def pixel_shift(d: pd.DataFrame, idx: np.ndarray, jac: pd.DataFrame | None, pix: dict | None) -> tuple[np.ndarray, np.ndarray]:
    """(east, north ″) to subtract from the dots d.iloc[idx]: pixel_offsets' map at their (x, y) plus their brightness
    bin's offset, through their photo's Jacobian (0 without one)."""
    z = np.zeros(idx.size)
    if pix is None or not idx.size:
        return z, z
    sub = d.iloc[idx]
    det = sub["detector"].to_numpy(np.int64)
    kind = (sub["pos_psf"].fillna(False).to_numpy(bool) if "pos_psf" in sub else np.zeros(idx.size, bool)).astype(np.int8)
    mx, my = _map_eval(pix.get("map") if C.ASTRO_PIXEL_MAP else None, kind, det, _col(sub, "x"), _col(sub, "y"))
    bx, by = _bright_eval(pix.get("bright") if C.ASTRO_BRIGHT_OFFSET else None, kind, det, bright_level(sub))
    dx, dy = mx + bx, my + by
    J = _jacobian_at(d, idx, jac)
    de = J[:, 0, 0] * dx + J[:, 0, 1] * dy
    dn = J[:, 1, 0] * dx + J[:, 1, 1] * dy
    ok = np.isfinite(de) & np.isfinite(dn)
    return np.where(ok, de, 0.0), np.where(ok, dn, 0.0)


def pixel_report(pix: dict | None, dots: int = 0, corrected: int = 0) -> dict:
    """astrometry_report.json `pixel_offsets`: per kind and detector the map's size (cells, rms and largest |Δ| in px)
    and its cells, the brightness table, and dot counts."""
    def num(v, nd=5):
        return None if v is None or not np.isfinite(v) else round(float(v), nd)
    out = {"enabled": {"map": bool(C.ASTRO_PIXEL_MAP), "bright": bool(C.ASTRO_BRIGHT_OFFSET)},
           "cell_px": int(C.ASTRO_MAP_CELL_PX), "level": "AB(flux_ujy) - detector saturation AB",
           "reference_dots": 0,
           "unsaturated_dots": int(dots), "dots_corrected": int(corrected), "map": [], "bright": []}
    if pix is None:
        return out
    out["reference_dots"] = int(pix.get("reference_dots", 0))
    nc = _ncell()
    mt = pix.get("map")
    if mt is not None and len(mt):
        for (kd, k), g in mt.groupby(["kind", "detector"]):
            a = g[g["applied"].to_numpy(bool)]
            gx, gy = np.full((nc, nc), np.nan), np.full((nc, nc), np.nan)
            gx[a["cx"].to_numpy(np.int64), a["cy"].to_numpy(np.int64)] = a["dx_px"].to_numpy(np.float64)
            gy[a["cx"].to_numpy(np.int64), a["cy"].to_numpy(np.int64)] = a["dy_px"].to_numpy(np.float64)
            out["map"].append({"kind": kd, "detector": int(k), "cells": int(len(g)), "cells_applied": int(len(a)),
                               "dots": int(g["n"].sum()),
                               "rms_dx_px": num(np.std(a["dx_px"])) if len(a) else None,
                               "rms_dy_px": num(np.std(a["dy_px"])) if len(a) else None,
                               "max_abs_px": num(np.max(np.abs(a[["dx_px", "dy_px"]].to_numpy()))) if len(a) else None,
                               "dx_px": [[num(v) for v in row] for row in gx], "dy_px": [[num(v) for v in row] for row in gy]})
    bt = pix.get("bright")
    if bt is not None and len(bt):
        out["bright"] = [{"kind": r.kind, "detector": int(r.detector), "bin": r.bin, "dx_px": num(r.dx_px), "dy_px": num(r.dy_px),
                          "dx_err_px": num(r.dx_err_px), "dy_err_px": num(r.dy_err_px), "n": int(r.n), "applied": bool(r.applied)}
                         for r in bt.itertuples()]
    return out


def gaia_parallax_prior(crossids: pd.DataFrame | None, sid: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per source in sid: (value ″, weight 1/″²) of its Gaia DR3 parallax prior (crossids' match: gaia_parallax and
    gaia_parallax_error; skyshift_core.motion.gaia_prior), weight 0 without one."""
    n = len(sid)
    if crossids is None or not len(crossids) or not {"gaia_parallax", "gaia_parallax_error"} <= set(crossids.columns):
        return np.zeros(n), np.zeros(n)
    x = crossids.drop_duplicates("source_id").set_index("source_id").reindex(np.asarray(sid, np.int64))
    ok = x["gaia_ref_id"].fillna("").astype(str).to_numpy() != "" if "gaia_ref_id" in x else np.ones(n, bool)
    v, w = M.gaia_prior(x["gaia_parallax"].to_numpy(np.float64), x["gaia_parallax_error"].to_numpy(np.float64))
    return v, np.where(ok, w, 0.0)


def fit(d: pd.DataFrame, crossids: pd.DataFrame | None = None, frame: bool = True,
        offsets: pd.DataFrame | None = None, jac: pd.DataFrame | None = None,
        sat: pd.DataFrame | None = None, pix: dict | None = None, diagnostics: bool = False) -> tuple[pd.DataFrame, dict]:
    """Member dots (source_id, ra, dec, mjd, pos_err_arcsec, visit, photo, snr, x/y/z_sc_km, flags_peak) → one row per
    fitted source (COLUMNS). offsets: per-photo frame offsets from photo_offsets (else measured on these dots).
    jac: photo_jacobians' table (None: no saturated-dot or pixel correction); sat: sat_offsets' table (None: measured on
    these dots, after the frame offsets, and returned in the stats as `sat_table`); pix: pixel_offsets' tables for the
    unsaturated dots (None: measured on these dots likewise, returned as `pix_tables`)."""
    d, sid_u, s, N, ra0, dec0, east, north, sc, xyz = _setup(d)
    n_dots = np.bincount(s, minlength=N)
    mjd = d["mjd"].to_numpy(np.float64)
    err = d[_rel_col(d)].to_numpy(np.float64)
    const2 = np.maximum(d["pos_err_arcsec"].to_numpy(np.float64) ** 2 - err ** 2, 0.0)  # star-constant part (calib)
    st: dict = {"spacecraft_position_dots": int(np.isfinite(sc).all(axis=1).sum()) if sc is not None else 0}
    corrected = np.zeros(N, bool)
    east0, north0 = east, north
    fe = fn = np.zeros(len(d))
    if frame:
        fe, fn, fst = frame_offsets(d, east, north, ra0, dec0, s, sid_u, crossids, xyz, offsets)
        st.update(fst)
        east, north = east - fe, north - fn
        corrected = np.bincount(s, (fe != 0) | (fn != 0), N) > 0
    if C.ASTRO_SAT_OFFSET and jac is not None:
        si = np.flatnonzero(sat_dots(d))
        if sat is None:  # measured on these dots, after the frame offsets (as tiles.py: sat_residuals, sat_offsets)
            k, rows = _sat_rows(d, si, east0, north0, ra0, dec0, s, sid_u, crossids, xyz, jac)
            rows["e"], rows["n"] = rows["e"].to_numpy(np.float64) - fe[k], rows["n"].to_numpy(np.float64) - fn[k]
            sat = sat_offsets(rows)
            st["sat_table"] = sat
        det_s = d["detector"].to_numpy(np.int64)[si]
        se, sn = sat_shift(det_s, sat_depth(crossids, sid_u[s[si]], det_s), _jacobian_at(d, si, jac), sat)
        east, north = east.copy(), north.copy()
        east[si] -= se
        north[si] -= sn
        st.update({"saturated_dots": int(si.size), "saturated_dots_corrected": int(((se != 0) | (sn != 0)).sum())})
    if pixel_terms_on() and jac is not None:
        if pix is None:  # measured on these dots, after the frame offsets (as tiles.py: frame_residuals, pixel_offsets)
            refs = frame_refs(d, east0, north0, ra0, dec0, s, sid_u, crossids, xyz, unsat=True)
            if refs is not None:
                m, re, rn = refs
                k = np.flatnonzero(m)
                rows = pd.concat([pd.DataFrame({"obs_id": d["obs_id"].astype(str).to_numpy()[k],
                                                "detector": d["detector"].to_numpy(np.int8)[k], "e": re - fe[k], "n": rn - fn[k]}),
                                  _pix_columns(d, k, jac)], axis=1)
                pix = pixel_offsets(rows)
            st["pix_tables"] = pix
        ui = np.flatnonzero(~sat_dots(d))
        pe_, pn_ = pixel_shift(d, ui, jac, pix)
        east, north = east.copy(), north.copy()
        east[ui] -= pe_
        north[ui] -= pn_
        st.update({"unsaturated_dots": int(ui.size), "pixel_dots_corrected": int(((pe_ != 0) | (pn_ != 0)).sum())})
    Pe, Pn = parallax_factors(ra0[s], dec0[s], xyz)
    n_visits = count_distinct(s, d["visit"].to_numpy(np.int64), N)
    # the motion model's mode per source (skyshift_core.motion): Gaia's parallax as a prior, our own parallax (visits in
    # both seasons), or none (then the leak k says how much parallax the pm holds)
    prior_val, prior_w = gaia_parallax_prior(crossids, sid_u)
    has_gaia = (prior_w > 0) & bool(C.ASTRO_GAIA_PLX_PRIOR)
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
        k_e, k_n, free = M.leak(s, N, wk, t, Pe, Pn, z if zp else None)
        n_vis_used = count_distinct(s[keep], d["visit"].to_numpy(np.int64)[keep], N) if keep.any() else np.zeros(N, np.int64)
        mode = M.choose_mode(has_gaia, n_vis_used, free, k_e, k_n)
        use_plx = np.isin(mode, M.PARALLAX_MODES)
        free_plx = mode == M.MODE_PLX  # a fitted parameter (Gaia's prior: known)
        pw = np.where(has_gaia, prior_w, 0.0)
        par, cov = _solve(s, N, wk, t, east, north, Pe, Pn, use_plx, prior_val, pw)
        n_used = np.bincount(s, keep, N)
        npar = np.where(free_plx, 5, 4)
        # the motion problem (pm, parallax): the plain fit, or with detector zero points removed (Frisch–Waugh)
        T, E, Nn, PE, PN, cv = t, east, north, Pe, Pn, cov
        if zp:
            swz = np.maximum(np.bincount(z, wk, Z), 1e-300)
            def dm(v):
                return v - (np.bincount(z, wk * v, Z) / swz)[z]
            Tz, Ez, Nz, PEz, PNz = dm(t), dm(east), dm(north), dm(Pe), dm(Pn)
            parz, covz = _solve(s, N, wk, Tz, Ez, Nz, PEz, PNz, use_plx, prior_val, pw)
            okz = np.isfinite(parz[:, 2]) & np.isfinite(parz[:, 3])
            par[okz, 2:] = parz[okz, 2:]
            cv = np.where(okz[:, None, None], covz, cov)
            o = okz[s]
            T, E, Nn, PE, PN = (np.where(o, a, b) for a, b in ((Tz, t), (Ez, east), (Nz, north), (PEz, Pe), (PNz, Pn)))
            p0 = np.where(okz[:, None], np.nan_to_num(parz[:, :2]), par[:, :2])
            zsrc = np.zeros(Z, np.int64)
            zsrc[z] = s
            n_zp = np.bincount(zsrc, np.bincount(z, keep, Z) > 0, N)  # zero points with used dots
            npar = np.where(okz, 2 * n_zp + 2 + free_plx, npar)
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
    vis_d = d["visit"].to_numpy(np.int64)
    mdof = motion_dof(s, N, keep, vis_d, free_plx, z if zp else None, okz if zp else None)
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
        cov_rob, n_sess = _cluster_cov(s, N, cl, wk, T, re, rn, PE, PN, cv, np.where(free_plx, 2.5, 2.0))
        enough = n_sess >= C.ASTRO_ROBUST_MIN_SESSIONS
        var_few = var / scale[:, None] ** 2 * np.maximum(1.0, np.nan_to_num(chi2_nu, nan=1.0))[:, None]
        var = np.where(enough[:, None], np.fmax(var, np.einsum("nii->ni", cov_rob)), np.maximum(var, var_few))
    if C.ASTRO_DETECTOR_ROBUST and "detector" in d:
        # detectors that disagree about the motion (a ghost, halo or neighbour that touches some bands in one visit):
        # the sandwich over detector clusters (with zero points: each detector's own motion; without: visit × detector),
        # where it beats its chance level (χ² quantile)
        det_ = d["detector"].to_numpy(np.int64) & 0xFF
        if zp:
            cl, k_ = (s.astype(np.int64) << 8) | det_, np.where(free_plx, 1.5, 1.0)
        else:
            vis = d["visit"].to_numpy(np.int64) if "visit" in d else np.zeros(len(d), np.int64)
            cl, k_ = (s.astype(np.int64) << 32) | ((vis & 0xFFFF) << 8) | det_, np.where(free_plx, 2.5, 2.0)
        cov_det, n_cl = _cluster_cov(s, N, cl, wk, T, re, rn, PE, PN, cv, k_)
        nu = np.maximum(n_cl - k_, 1.0)
        lim = chi2_dist.ppf(C.ASTRO_DETECTOR_ROBUST_P, nu) / nu
        vd = np.einsum("nii->ni", cov_det)
        excess_d = (vd > lim[:, None] * np.einsum("nii->ni", cv)) & (n_cl > k_ + 1)[:, None]
        var = np.where(excess_d, np.fmax(var, vd), var)
    sig = np.sqrt(var)
    # steadiness over the visits: each visit's mean residual against the line (+ parallax), out and back = `unsteady`
    raw_err = "pos_err_rel_arcsec" not in d or np.array_equal(err, d["pos_err_arcsec"].to_numpy(np.float64))  # no calib
    visit_chi2_nu, _, wobble = M.visit_steadiness(
        s, N, vis_d, wk, re, rn, 2 + free_plx.astype(np.int64), floor=C.ASTRO_VISIT_FLOOR_ARCSEC,
        scale=C.ASTRO_VISIT_ERR_SCALE_RAW if raw_err else C.ASTRO_VISIT_ERR_SCALE)
    unsteady = M.unsteady(visit_chi2_nu) if C.ASTRO_UNSTEADY else np.zeros(N, bool)
    visit_scale = C.ASTRO_VISIT_ERR_SCALE_RAW if raw_err else C.ASTRO_VISIT_ERR_SCALE
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
        "fit": mode,
        "n_dots": n_dots.astype(np.int32), "n_used": n_used.astype(np.int32), "n_visits": n_visits.astype(np.int16),
        "span_yr": span.astype(np.float32), "chi2_nu": chi2_nu.astype(np.float32),
        "snr_median": group_median(s, snr, N).astype(np.float32), "frame_corrected": corrected,
        "plx_leak_ra": k_e.astype(np.float32), "plx_leak_dec": k_n.astype(np.float32),
        "visit_chi2_nu": visit_chi2_nu.astype(np.float32), "wobble_arcsec": wobble.astype(np.float32),
        "unsteady": unsteady,
        "motion_dof": mdof.astype(np.int32),  # not written (COLUMNS): the fit's own diagnostics
        "plx_free": free.astype(np.float32),
    })
    good = (n_used >= C.ASTRO_MIN_DOTS) & np.isfinite(pmra) & (span > 0)  # used dots: clean_mask can leave 1 per visit
    few_dof = good & (mdof < C.ASTRO_MIN_MOTION_DOF)  # the zero points leave the motion (almost) no freedom
    good &= ~few_dof
    with np.errstate(invalid="ignore"):
        fast = good & (pm >= C.HIGHPM_FIT_MIN_MAS_YR) & (pm_snr >= min(C.HIGHPM_FIT_MIN_SNR, C.HIGHPM_FIT_MIN_SNR_GAIA))
    # the plain fit's line (its intercept at tref; the pm and parallax as written) at each visit's own time and parallax
    # factors, + the visit's mean residual (within detectors when the zero points are on)
    st["visit_positions"] = visit_positions(fast, s, vis_d, wk, t, Pe, Pn, re, rn, par, tref, ra0, dec0, sid_u,
                                            visit_scale, C.ASTRO_VISIT_FLOOR_ARCSEC)
    st.update({"sources_in": int(N), "fitted": int(good.sum()), "with_parallax": int((good & use_plx).sum()),
               "dropped_motion_dof": int(few_dof.sum()),
               "modes": {m: int((good & (mode == m)).sum()) for m in M.MODES}, "unsteady": int((good & unsteady).sum())})
    if diagnostics:
        return out[good].reset_index(drop=True), st
    return out[good].reset_index(drop=True)[COLUMNS], st


def visit_positions(sel: np.ndarray, s: np.ndarray, visit: np.ndarray, wk: np.ndarray, T: np.ndarray, PE: np.ndarray,
                    PN: np.ndarray, re: np.ndarray, rn: np.ndarray, par: np.ndarray, tref: np.ndarray, ra0: np.ndarray,
                    dec0: np.ndarray, sid_u: np.ndarray, scale: float, floor: float) -> pd.DataFrame:
    """Per selected source × visit (VISIT_COLUMNS): where the fit puts it at the visit's mean time (its line + parallax at
    the visit's weighted mean t and parallax factors) plus the visit's weighted mean residual: the visit's own position,
    in the fit's frame (frame, saturated-dot and pixel offsets taken out; detector zero points as the fit's). Errors per
    axis √(scale² / Σw + floor²), as the steadiness test (skyshift_core.motion.visit_steadiness): relative positions,
    the frame's own uncertainty not included. mjd: the visit's weighted mean dot time."""
    from skyshift_core import tangent_deproject
    use = sel[s] & (wk > 0)
    if not use.any():
        return pd.DataFrame({c: pd.Series(dtype=t) for c, t in zip(VISIT_COLUMNS, ("int64", "int16", "float64", "float64",
                                                                                   "float64", "float32", "float32", "int32"))})
    ss, vv, w = s[use], visit[use], wk[use]
    key = pd.MultiIndex.from_arrays([ss, vv])
    g = pd.DataFrame({"s": ss, "v": vv, "w": w, "wt": w * T[use], "wpe": w * PE[use], "wpn": w * PN[use],
                      "wre": w * re[use], "wrn": w * rn[use], "n": 1}, index=key).groupby(level=[0, 1]).sum()
    si = g.index.get_level_values(0).to_numpy(np.int64)
    sw = g["w"].to_numpy(np.float64)
    tv, pev, pnv = (g[c].to_numpy(np.float64) / sw for c in ("wt", "wpe", "wpn"))
    p = par[si]
    plx = np.nan_to_num(p[:, 4])
    e = p[:, 0] + p[:, 2] * tv + plx * pev + g["wre"].to_numpy(np.float64) / sw
    n = p[:, 1] + p[:, 3] * tv + plx * pnv + g["wrn"].to_numpy(np.float64) / sw
    ra, dec = tangent_deproject(ra0[si], dec0[si], e, n)
    err = np.sqrt(scale * scale / sw + floor * floor)
    return pd.DataFrame({"source_id": sid_u[si].astype(np.int64), "visit": g.index.get_level_values(1).to_numpy(np.int16),
                         "mjd": tref[si] + tv * DAYS_PER_JYEAR, "ra": np.asarray(ra, np.float64), "dec": np.asarray(dec, np.float64),
                         "ra_err_arcsec": err.astype(np.float32), "dec_err_arcsec": err.astype(np.float32),
                         "n_dots": g["n"].to_numpy(np.int32)})


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
    # by Gaia G: saturated stars (G < ~12) had a fake westward pmra before the saturated-dot offset (ASTRO_SAT_OFFSET)
    gg = j["gaia_g"].to_numpy(np.float64)
    out["by_gaia_g"] = []
    for lo, hi in zip(C.ASTRO_REPORT_G_BINS[:-1], C.ASTRO_REPORT_G_BINS[1:]):
        m = (gg >= lo) & (gg < hi)
        out["by_gaia_g"].append({"g": [lo, hi], "pmra": _stats(dra[m], j["pmra_err"].to_numpy(np.float64)[m]),
                                 "pmdec": _stats(ddec[m], j["pmdec_err"].to_numpy(np.float64)[m])})
    # the parallax leak left in our pm: weighted slope of (pm − Gaia's) against Gaia's parallax, stars with ϖ > 1 mas
    # (two visits half a year apart and no parallax in the fit: ~ −4 to −6 mas/yr per mas in RA; with Gaia's prior ~0)
    plx = j["gaia_parallax"].to_numpy(np.float64)
    out["parallax_leak"] = {"stars": 0}
    for ax, dv, ev in (("pmra", dra, j["pmra_err"].to_numpy(np.float64)), ("pmdec", ddec, j["pmdec_err"].to_numpy(np.float64))):
        m = np.isfinite(dv) & np.isfinite(plx) & (plx > 1.0) & np.isfinite(ev) & (ev > 0)
        if m.sum() >= 5:
            sw = 1.0 / ev[m]
            out["parallax_leak"][f"{ax}_per_mas"] = round(float(np.linalg.lstsq(np.c_[np.ones(m.sum()), plx[m]] * sw[:, None], dv[m] * sw,
                                                                                rcond=None)[0][1]), 3)
            out["parallax_leak"]["stars"] = int(m.sum())
    if "fit" in j:
        out["modes"] = {k: int(v) for k, v in j["fit"].value_counts().items()}
    if "unsteady" in j:
        u = j["unsteady"].fillna(False).to_numpy(bool)
        pull = np.hypot(dra / j["pmra_err"].to_numpy(np.float64), ddec / j["pmdec_err"].to_numpy(np.float64))
        out["unsteady"] = {"stars": int(u.sum()), "share": round(float(u.mean()), 4),
                           "median_dpm_unsteady": round(float(np.nanmedian(np.hypot(dra, ddec)[u])), 1) if u.any() else None,
                           "median_dpm_steady": round(float(np.nanmedian(np.hypot(dra, ddec)[~u])), 1) if (~u).any() else None,
                           "pull_over_4_flagged": int((u & (pull > 4)).sum()), "pull_over_4": int((pull > 4).sum())}
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


def model_counts(tab: pd.DataFrame) -> dict:
    """astrometry_report.json `modes` (rows per motion-model mode) and `unsteady` (rows), from the written table."""
    if tab is None or not len(tab) or "fit" not in tab:
        return {"modes": {}, "unsteady": 0}
    return {"modes": {m: int((tab["fit"] == m).sum()) for m in M.MODES},
            "unsteady": int(tab["unsteady"].fillna(False).astype(bool).sum()) if "unsteady" in tab else 0}


def run(store: Store, zone: C.Zone, workers: int = 16) -> dict:
    t0 = time.time()
    d, st = member_dots(store, zone, workers)
    # each photo's Jacobian (whole dot file; cached by member_dots) for the saturated-dot offset
    jac = photo_jacobians(d, member_dots_raw(store, zone, workers)[2]) if C.ASTRO_SAT_OFFSET or pixel_terms_on() else None
    xid_rel = results_rel(zone.name, "crossids.parquet")
    crossids = store.read_parquet(xid_rel) if store.exists(xid_rel) else None
    if crossids is None:
        L.warning("astrometry %s: no crossids.parquet: no frame correction, no Gaia comparison", zone.name)
    if C.ASTRO_FRAME_CORRECT and crossids is not None:
        # the saturated-dot and pixel offsets are measured after the frame offsets, then used by both fits (as tiles.py)
        cor, st_cor = fit(d, crossids, frame=True, jac=jac)
        sat, pix = st_cor.pop("sat_table", None), st_cor.pop("pix_tables", None)
        raw, st_raw = fit(d, crossids, frame=False, jac=jac, sat=sat, pix=pix)
    else:
        raw, st_raw = fit(d, crossids, frame=False, jac=jac)
        sat, pix = st_raw.pop("sat_table", None), st_raw.pop("pix_tables", None)
        cor, st_cor = raw, st_raw
    visits = st_cor.pop("visit_positions", None)
    st_raw.pop("visit_positions", None)
    store.write_parquet(results_rel(zone.name, "astrometry.parquet"), cor)
    if visits is not None:
        store.write_parquet(results_rel(zone.name, "astrometry_visits.parquet"), visits)
    rep = {"zone": zone.name, "frame_correction": {k: v for k, v in st_cor.items() if k.startswith(("photos", "frame"))},
           "fitted": st_cor.get("fitted", 0), "with_parallax": st_cor.get("with_parallax", 0),
           **model_counts(cor),
           "spacecraft_position_dots": st_cor.get("spacecraft_position_dots", 0),
           "saturated_offsets": sat_report(sat, st_cor.get("saturated_dots", 0), st_cor.get("saturated_dots_corrected", 0)),
           "pixel_offsets": pixel_report(pix, st_cor.get("unsaturated_dots", 0), st_cor.get("pixel_dots_corrected", 0)),
           "vs_gaia_frame_corrected": report(cor, crossids) if cor is not raw else None,
           "vs_gaia_raw": report(raw, crossids)}
    store.write_json(results_rel(zone.name, "astrometry_report.json"), rep)
    out = {**st, **st_cor, "wall_s": round(time.time() - t0, 1)}
    best = rep["vs_gaia_frame_corrected"] or rep["vs_gaia_raw"]
    if best.get("matched"):
        out["gaia_pmra_bias_scatter"] = [best["pmra"].get("bias"), best["pmra"].get("scatter")]
    L.info("astrometry %s: %s", zone.name, out)
    return out
