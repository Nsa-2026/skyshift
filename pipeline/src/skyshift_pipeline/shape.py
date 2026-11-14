"""Shape review: fixed sources that look like two objects where the catalogues list one (candidates.json kind `shape`).

A moving object passing next to a star, or an unknown companion, adds light beside the star: its dots come out
more stretched than a single star's, along the line between the two. The test works on the dots' second moments
(dot files' a_arcsec, b_arcsec, theta_deg, contracts/dots.md):

1. Each dot's moment ellipse is turned into an ellipticity vector on the sky, e = ((Q_NN − Q_EE), 2 Q_EN) / (Q_EE + Q_NN),
   with Q = J Q_pix Jᵀ and J the photo's pixel → (east, north) Jacobian (an affine fit of its dots' x, y against
   their sources' sky positions). An ellipse along position angle φ (east of north) gives e ∝ (cos 2φ, sin 2φ).
2. The PSF is itself slightly stretched, differently across the detector and with wavelength (the filter changes
   along y). So each dot's e is taken relative to the median e of single reference stars (isolated, unsaturated
   Gaia stars) in the same photo and the same quarter-by-quarter region of the detector: the residual r.
3. The noise of r per detector and S/N comes from the reference stars too. A source's mean residual r̄ (weighted)
   and its error give a significance s = |r̄| / σ (Rayleigh: under the null P(> s) = e^(−s²/2)), the excess
   ellipticity |r̄| and the sky PA of the stretch, ½ atan2(r̄₂, r̄₁). Random noise points anywhere and averages
   away; a second object stays at one PA on the sky in every photo, and the source lands on different pixels and
   wavelengths each time. The detector turning ~180° between visits does NOT test the PSF: an ellipse turned by
   180° is the same ellipse, so a stretch fixed to the detector repeats in every visit too.
4. `steady_pa`: the short-wavelength (D1–D3) and the long-wavelength (D4–D6) detectors each show it (s ≥ 2) along
   the same PA (≤ 30°): two arrays behind a dichroic with their own optics and PSFs agree, which a PSF or detector
   stretch would not do. `one_visit` per visit: one visit shows it (s ≥ 3) and the others don't (s < 1.5): something
   that passed by.

What is kept (select): s ≥ SHAPE_SIG_MIN, excess ≥ SHAPE_E_MIN, consistent PA, ≥ SHAPE_MIN_DOTS clean dots; no second
catalogue object within SHAPE_NEIGHBOUR_ARCSEC (any Gaia G < 20, CatWISE or 2MASS entry other than the object's own)
and none within SHAPE_WIDE_ARCSEC less than SHAPE_WIDE_DMAG fainter than the object (the moments' annulus reaches 34″);
the catalogued neighbours within SHAPE_NEIGHBOUR_FAR_ARCSEC, weighted by flux ratio and the share of their light inside
the moments' window (neighbour_ellipticity) and summed as a vector (2 × their PA), predict along the measured stretch
less than SHAPE_NEIGHBOUR_E_FRAC of its excess (a perpendicular neighbour would stretch it the other way); not saturated; no artefact
flags on ≥ 25% of its dots; not within SHAPE_BRIGHT_ARCSEC of a Gaia G < SHAPE_BRIGHT_G star; not a galaxy:
classify's galaxy, Gaia's galaxy_or_qso, a good photo-z, or without a Gaia primary an extended profile
(extended_score > SHAPE_GALAXY_EXT or size_ratio > SHAPE_GALAXY_SIZE; with a Gaia star a pair is extended too, so
not there); the SHAPE_CAP most significant. Review (15 Nov) of the first 49: 34 galaxy-like with no Gaia star and
10 with a catalogued neighbour along the stretch (2MASS at 7–13″, Gaia or CatWISE at 15–25″); 5 left.
The checks (shape_report.json): the rate at which held-out single Gaia stars fire and the recall and PA on Gaia pairs
3–10″ apart, at the rule's and at the list's threshold.

Honest limit (hunt, 14 Nov): the stretch is real signal, but faint uncatalogued neighbours are common in this
field, so isolated single Gaia stars fire too (4% at s ≥ 6 against 77% of Gaia pairs; 0.17% at s ≥ 20 against 21%).
Pure noise would rarely pass s ≥ 6; the high SHAPE_SIG_MIN keeps only the most extreme cases. A listed source is a
lead for a human, not a detection: many are probably faint background stars or compact galaxies.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

SHAPE_SIG_MIN = 20.0           # significance of the mean excess ellipticity; pure noise would need ~5, but single Gaia stars in
                               # this crowded field fire 4% at 6 (faint uncatalogued neighbours) and 0.17% at 20: see the report
SHAPE_E_MIN = 0.04             # and at least this excess (|r̄|): bright stars' tiny errors must not make 1% systematics look real
SHAPE_MIN_DOTS = 10
SHAPE_SNR_MIN = 10.0           # dots used for shapes
SHAPE_EDGE_PX = 12
SHAPE_REGIONS = 4              # reference medians in 4 × 4 regions of each photo
SHAPE_REF_MIN = 8              # reference dots needed in a region (else the whole photo's median)
SHAPE_SNR_BINS = (10.0, 20.0, 40.0, 80.0, 160.0, np.inf)
SHAPE_VISIT_SIG = 2.0          # a visit "shows" the stretch
SHAPE_VISIT_ONE = 3.0          # one_visit: this visit shows it clearly ...
SHAPE_VISIT_NONE = 1.5         # ... and every other visit (>= SHAPE_VISIT_MIN_DOTS dots) is below this
SHAPE_VISIT_MIN_DOTS = 5
SHAPE_PA_TOL_DEG = 30.0
SHAPE_NEIGHBOUR_ARCSEC = 15.0  # a second catalogue object this close could explain the stretch
SHAPE_PRIMARY_ARCSEC = 6.0     # the catalogue object that is "the" source
SHAPE_SAME_ARCSEC = 3.0        # a Gaia and a CatWISE entry this close are one object
SHAPE_NEIGHBOUR_DMAG = float("inf")  # any catalogued neighbour explains it (with 3 mag, single stars fired 20% vs 12% at sig 6)
SHAPE_WIDE_ARCSEC = 35.0       # the moments' window (15″) and background annulus (21–34″) feel neighbours this far out:
SHAPE_WIDE_DMAG = 2.5          # one within this of the primary's magnitude (or brighter) explains a stretch (hunt: the
SHAPE_WIDE_NO_PRIMARY = (16.0, 13.0)  # first try's top 4 had a Gaia star at 15.2–15.5″); no primary: G, W1 brighter than these
# catalogued neighbours to SHAPE_NEIGHBOUR_FAR_ARCSEC weighted by their light inside the moments' window (dots.py
# shape_moments: pixels within 2.5 px, a star's trace a² + b² ≈ 2 × 0.6² px²): a neighbour of flux ratio f at r
# adds ≈ f × Φ((R − r) / σ_psf) × min(r, R)² to the trace along its direction (neighbour_ellipticity)
SHAPE_NEIGHBOUR_FAR_ARCSEC = 20.0
SHAPE_NEIGHBOUR_E_MAX = 0.02   # no measured stretch (catalogue context alone): predicted ellipticity that would explain one
SHAPE_NEIGHBOUR_E_FRAC = 0.25  # predicted along the measured PA ≥ this share of the excess: the neighbours explain it
                               # (review rows #13, #25, #29 kept: 0.08 or perpendicular; #35, #38, #41: 1.5–2.1, dropped)
SHAPE_MOMENT_R_ARCSEC = 2.5 * 6.152
SHAPE_PSF_SIGMA_ARCSEC = 7.53 / 2.355
SHAPE_STAR_TRACE_ARCSEC2 = 2 * (0.6 * 6.152) ** 2
SHAPE_GALAXY_EXT = 1.0         # no Gaia primary and classify's extended_score above this: a galaxy (review: 34 of 49)
SHAPE_GALAXY_SIZE = 1.12       # ... or size_ratio above this
SHAPE_BRIGHT_G = 9.0
SHAPE_BRIGHT_ARCSEC = 60.0
SHAPE_FLAG_SHARE = 0.25        # artefact flags (ghost, bloom, halo, crosstalk) on this share of the dots: dropped
SHAPE_CAP = 150
SHAPE_SIG_SCALE = 15.0         # score = (1 − e^(−s/15)) × weights
SHAPE_ONE_VISIT_WEIGHT = 0.8
SAT_BITS = 0x00008002
MASK_BITS = 0x0A02CEC7
OUTLIER = 1 << 19
ARTEFACT_BITS = (1 << 20) | (1 << 22) | (1 << 23) | (1 << 24) | (1 << 26) | (1 << 28)  # crosstalk, ghost*, bloom, halo


# --- geometry --------------------------------------------------------------------------------------
def photo_jacobians(photo: np.ndarray, x: np.ndarray, y: np.ndarray, east: np.ndarray, north: np.ndarray,
                    min_dots: int = 20) -> dict[int, np.ndarray]:
    """Per photo, the 2×2 Jacobian d(east, north)/d(x, y) (″/px) of an affine fit of tangent-plane offsets
    (east, north, ″, around any fixed point) on pixel positions, with one 5σ clip."""
    out: dict[int, np.ndarray] = {}
    order = np.argsort(photo, kind="stable")
    p = photo[order]
    cuts = np.flatnonzero(np.diff(p)) + 1
    for idx in np.split(order, cuts):
        if len(idx) < min_dots:
            continue
        A = np.column_stack([np.ones(len(idx)), x[idx], y[idx]])
        keep = np.ones(len(idx), bool)
        for _ in range(2):
            ce, *_ = np.linalg.lstsq(A[keep], east[idx][keep], rcond=None)
            cn, *_ = np.linalg.lstsq(A[keep], north[idx][keep], rcond=None)
            res = np.hypot(east[idx] - A @ ce, north[idx] - A @ cn)
            mad = np.median(res[keep]) * 1.4826 + 1e-3
            keep = res < 5 * mad
        out[int(photo[idx[0]])] = np.array([[ce[1], ce[2]], [cn[1], cn[2]]])
    return out


def sky_ellipticity(a: np.ndarray, b: np.ndarray, theta_deg: np.ndarray, J: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Ellipticity vector on the sky (e1, e2) = (Q_NN − Q_EE, 2 Q_EN) / (Q_EE + Q_NN) of dots with moments
    (a, b, theta in pixel coordinates, theta from +x towards +y); J: per-dot Jacobians, shape (n, 2, 2) or (2, 2).
    An ellipse along PA φ (east of north) gives (cos 2φ, sin 2φ) × (a² − b²) / (a² + b²)."""
    t = np.deg2rad(np.asarray(theta_deg, np.float64))
    a2, b2 = np.asarray(a, np.float64) ** 2, np.asarray(b, np.float64) ** 2
    c, s = np.cos(t), np.sin(t)
    qxx = a2 * c * c + b2 * s * s
    qyy = a2 * s * s + b2 * c * c
    qxy = (a2 - b2) * c * s
    J = np.broadcast_to(np.asarray(J, np.float64), (len(t), 2, 2))
    j00, j01, j10, j11 = J[:, 0, 0], J[:, 0, 1], J[:, 1, 0], J[:, 1, 1]
    qee = j00 * j00 * qxx + 2 * j00 * j01 * qxy + j01 * j01 * qyy
    qnn = j10 * j10 * qxx + 2 * j10 * j11 * qxy + j11 * j11 * qyy
    qen = j00 * j10 * qxx + (j00 * j11 + j01 * j10) * qxy + j01 * j11 * qyy
    tr = qee + qnn
    with np.errstate(invalid="ignore", divide="ignore"):
        return (qnn - qee) / tr, 2 * qen / tr


def pa_of(e1, e2) -> np.ndarray:
    """Position angle (deg east of north, 0–180) of the long axis of an ellipticity vector."""
    return np.mod(0.5 * np.degrees(np.arctan2(np.asarray(e2, np.float64), np.asarray(e1, np.float64))), 180.0)


def pa_diff(p1, p2) -> np.ndarray:
    """Smallest difference between two axes (deg, 0–90)."""
    d = np.mod(np.asarray(p1, np.float64) - np.asarray(p2, np.float64), 180.0)
    return np.minimum(d, 180.0 - d)


# --- dots ------------------------------------------------------------------------------------------
def usable_dots(d: pd.DataFrame) -> np.ndarray:
    """Dots whose moments can be trusted: S/N ≥ SHAPE_SNR_MIN, unsaturated, no masked pixel in the 3×3, no
    OUTLIER at the peak, no artefact bit, away from the edge, finite moments."""
    fl = d["flags"].to_numpy(np.int64) & 0xFFFFFFFF
    fp = d["flags_peak"].to_numpy(np.int64) & 0xFFFFFFFF
    x, y = d["x"].to_numpy(np.float64), d["y"].to_numpy(np.float64)
    a, b = d["a_arcsec"].to_numpy(np.float64), d["b_arcsec"].to_numpy(np.float64)
    return ((d["snr"].to_numpy(np.float64) >= SHAPE_SNR_MIN) & ((fp & SAT_BITS) == 0) & ((fl & MASK_BITS) == 0)
            & ((fp & OUTLIER) == 0) & ((fl & ARTEFACT_BITS) == 0)
            & (x >= SHAPE_EDGE_PX) & (x <= 2039 - SHAPE_EDGE_PX) & (y >= SHAPE_EDGE_PX) & (y <= 2039 - SHAPE_EDGE_PX)
            & np.isfinite(a) & np.isfinite(b) & (b > 0) & np.isfinite(d["theta_deg"].to_numpy(np.float64)))


def residuals(d: pd.DataFrame, is_ref: np.ndarray) -> pd.DataFrame:
    """Adds r1, r2: each dot's sky ellipticity minus the median of the reference dots (is_ref) in its photo's
    region (whole photo when the region has fewer than SHAPE_REF_MIN). Needs photo, x, y, e1, e2."""
    n = SHAPE_REGIONS
    reg = (np.clip(d["x"].to_numpy() // (2040 / n), 0, n - 1) * n + np.clip(d["y"].to_numpy() // (2040 / n), 0, n - 1)).astype(np.int64)
    key = d["photo"].to_numpy(np.int64) * (n * n) + reg
    ref = pd.DataFrame({"key": key[is_ref], "photo": d["photo"].to_numpy()[is_ref], "e1": d["e1"].to_numpy()[is_ref],
                        "e2": d["e2"].to_numpy()[is_ref]})
    g = ref.groupby("key").agg(n=("e1", "size"), m1=("e1", "median"), m2=("e2", "median"))
    gp = ref.groupby("photo").agg(m1=("e1", "median"), m2=("e2", "median"))
    g = g[g["n"] >= SHAPE_REF_MIN]
    m1 = pd.Series(key).map(g["m1"]).to_numpy()
    m2 = pd.Series(key).map(g["m2"]).to_numpy()
    p1 = d["photo"].map(gp["m1"]).to_numpy()
    p2 = d["photo"].map(gp["m2"]).to_numpy()
    m1 = np.where(np.isfinite(m1), m1, p1)
    m2 = np.where(np.isfinite(m2), m2, p2)
    out = d.copy()
    out["r1"], out["r2"] = d["e1"].to_numpy() - m1, d["e2"].to_numpy() - m2
    return out


def snr_bin(snr: np.ndarray) -> np.ndarray:
    return np.clip(np.searchsorted(np.asarray(SHAPE_SNR_BINS), snr, side="right") - 1, 0, len(SHAPE_SNR_BINS) - 2)


def noise_model(d: pd.DataFrame, is_ref: np.ndarray) -> dict[tuple[int, int], float]:
    """Per (detector, S/N bin): robust σ of one residual component of the reference dots."""
    r = d[is_ref]
    sb = snr_bin(r["snr"].to_numpy())
    out = {}
    for (det, b), g in r.groupby([r["det"].to_numpy(), sb]):
        v = np.r_[g["r1"].to_numpy(), g["r2"].to_numpy()]
        v = v[np.isfinite(v)]
        if len(v) >= 30:
            out[(int(det), int(b))] = float(1.4826 * np.median(np.abs(v - np.median(v))))
    return out


def dot_sigma(d: pd.DataFrame, noise: dict[tuple[int, int], float]) -> np.ndarray:
    sb = snr_bin(d["snr"].to_numpy())
    return np.array([noise.get((int(a), int(b)), np.nan) for a, b in zip(d["det"].to_numpy(), sb)])


def _mean_vec(r1, r2, w):
    sw = np.sum(w)
    m1, m2 = np.sum(w * r1) / sw, np.sum(w * r2) / sw
    n = len(r1)
    sig = 1.0 / np.sqrt(sw)
    if n >= 3:  # inflate by the residual scatter when it exceeds the noise model
        chi2 = (np.sum(w * (r1 - m1) ** 2) + np.sum(w * (r2 - m2) ** 2)) / (2 * n - 2)
        sig *= math.sqrt(max(1.0, chi2))
    return m1, m2, sig


ARMS = {"swir": (1, 2, 3), "mwir": (4, 5, 6)}  # D1–D3 and D4–D6: separate arrays behind the dichroic


def source_shapes(d: pd.DataFrame) -> pd.DataFrame:
    """Per source (d: usable dots with source_id, r1, r2, sigma, visit, det): n_dots, e1, e2 (mean residual), err,
    sig (|r̄| / err), excess (|r̄|), pa_deg; per visit v: n_v<v>, sig_v<v>, pa_v<v>; per arm (ARMS): n_<arm>,
    sig_<arm>, pa_<arm>; then n_visits_shown, pa_ok, steady_pa (both arms show it along one PA), one_visit."""
    d = d[np.isfinite(d["sigma"]) & (d["sigma"] > 0) & np.isfinite(d["r1"]) & np.isfinite(d["r2"])]
    visits = sorted(int(v) for v in d["visit"].dropna().unique())
    rows = []
    order = np.argsort(d["source_id"].to_numpy(), kind="stable")
    sid = d["source_id"].to_numpy()[order]
    r1, r2 = d["r1"].to_numpy()[order], d["r2"].to_numpy()[order]
    w = 1.0 / d["sigma"].to_numpy()[order] ** 2
    vis = d["visit"].to_numpy()[order]
    det = d["det"].to_numpy(np.int64)[order] if "det" in d else np.zeros(len(order), np.int64)
    cuts = np.flatnonzero(np.diff(sid)) + 1
    for idx in np.split(np.arange(len(sid)), cuts):
        if len(idx) == 0:
            continue
        m1, m2, err = _mean_vec(r1[idx], r2[idx], w[idx])
        row = {"source_id": int(sid[idx[0]]), "n_dots": len(idx), "e1": m1, "e2": m2, "err": err}
        for arm, dets in ARMS.items():
            k = idx[np.isin(det[idx], dets)]
            if len(k) >= SHAPE_VISIT_MIN_DOTS:
                a1, a2, ev = _mean_vec(r1[k], r2[k], w[k])
                row[f"n_{arm}"], row[f"sig_{arm}"], row[f"pa_{arm}"] = len(k), math.hypot(a1, a2) / ev, float(pa_of(a1, a2))
            else:
                row[f"n_{arm}"], row[f"sig_{arm}"], row[f"pa_{arm}"] = len(k), np.nan, np.nan
        for v in visits:
            k = idx[vis[idx] == v]
            if len(k) >= SHAPE_VISIT_MIN_DOTS:
                a1, a2, ev = _mean_vec(r1[k], r2[k], w[k])
                row[f"n_v{v}"], row[f"sig_v{v}"], row[f"pa_v{v}"] = len(k), math.hypot(a1, a2) / ev, float(pa_of(a1, a2))
            else:
                row[f"n_v{v}"], row[f"sig_v{v}"], row[f"pa_v{v}"] = len(k), np.nan, np.nan
        rows.append(row)
    s = pd.DataFrame(rows)
    if not len(s):
        return s
    s["excess"] = np.hypot(s["e1"], s["e2"])
    s["sig"] = s["excess"] / s["err"]
    s["pa_deg"] = pa_of(s["e1"], s["e2"])
    sigs = np.column_stack([s[f"sig_v{v}"].to_numpy() for v in visits]) if visits else np.zeros((len(s), 0))
    pas = np.column_stack([s[f"pa_v{v}"].to_numpy() for v in visits]) if visits else np.zeros((len(s), 0))
    shown = np.nan_to_num(sigs, nan=0.0) >= SHAPE_VISIT_SIG
    bad_pa = shown & (pa_diff(pas, s["pa_deg"].to_numpy()[:, None]) > SHAPE_PA_TOL_DEG)
    s["n_visits_shown"] = shown.sum(axis=1)
    s["pa_ok"] = ~bad_pa.any(axis=1)
    arms_show = (np.nan_to_num(s["sig_swir"].to_numpy(np.float64), nan=0.0) >= SHAPE_VISIT_SIG) & \
        (np.nan_to_num(s["sig_mwir"].to_numpy(np.float64), nan=0.0) >= SHAPE_VISIT_SIG)
    arms_agree = pa_diff(s["pa_swir"].to_numpy(np.float64), s["pa_mwir"].to_numpy(np.float64)) <= SHAPE_PA_TOL_DEG
    s["steady_pa"] = arms_show & arms_agree & s["pa_ok"].to_numpy(bool)
    strong = np.nan_to_num(sigs, nan=0.0) >= SHAPE_VISIT_ONE
    quiet = np.where(np.isfinite(sigs), sigs < SHAPE_VISIT_NONE, True)
    s["one_visit"] = (strong.sum(axis=1) == 1) & (strong | quiet).all(axis=1) & (np.isfinite(sigs).sum(axis=1) >= 2) & s["pa_ok"]
    return s


def fires(s: pd.DataFrame, sig_min: float | None = None, e_min: float | None = None) -> np.ndarray:
    """The shape test alone (no catalogue or artefact cuts): significant, large enough, one PA."""
    sig_min = SHAPE_SIG_MIN if sig_min is None else sig_min
    e_min = SHAPE_E_MIN if e_min is None else e_min
    return ((s["sig"] >= sig_min) & (s["excess"] >= e_min) & (s["n_dots"] >= SHAPE_MIN_DOTS) & s["pa_ok"]
            & (s["steady_pa"] | s["one_visit"])).to_numpy(bool)


# --- catalogue neighbours --------------------------------------------------------------------------
def neighbour_ellipticity(flux_ratio, sep_arcsec) -> np.ndarray:
    """The ellipticity a catalogued neighbour (flux ratio f to the object, r arcsec away) adds to the object's moments:
    f Φ((R − r) / σ) min(r, R)² over a star's trace (SHAPE_MOMENT_R_ARCSEC, SHAPE_PSF_SIGMA_ARCSEC,
    SHAPE_STAR_TRACE_ARCSEC2). A 3 mag fainter star at 10″ gives ~0.24, at 20″ ~0.04; 5 mag at 20″ ~0.006."""
    from scipy.special import ndtr

    f, r = np.asarray(flux_ratio, np.float64), np.asarray(sep_arcsec, np.float64)
    c = f * ndtr((SHAPE_MOMENT_R_ARCSEC - r) / SHAPE_PSF_SIGMA_ARCSEC) * np.minimum(r, SHAPE_MOMENT_R_ARCSEC) ** 2
    return c / SHAPE_STAR_TRACE_ARCSEC2


def _at_epoch(ra, dec, pmra, pmdec, dt_yr):
    """Positions moved by their proper motion (mas/yr, pmra × cos dec; NaN = none) over dt_yr years."""
    cosd = np.cos(np.deg2rad(dec))
    return (ra + np.nan_to_num(pmra) * dt_yr / 3.6e6 / cosd, dec + np.nan_to_num(pmdec) * dt_yr / 3.6e6)


def catalogue_context(ra: np.ndarray, dec: np.ndarray, refcat: pd.DataFrame) -> pd.DataFrame:
    """Per position: the primary Gaia (brightest G within SHAPE_PRIMARY_ARCSEC) and CatWISE (brightest W1) entries,
    and how many other entries within SHAPE_NEIGHBOUR_ARCSEC could explain a stretch: Gaia within
    SHAPE_NEIGHBOUR_DMAG of the primary in G (any Gaia when there is no Gaia primary, unless it is the CatWISE
    primary's own counterpart), CatWISE likewise in W1 (one within SHAPE_SAME_ARCSEC of the Gaia primary is the
    same object), any 2MASS entry that is not the primary's own (n_tm_explain; 2MASS's epoch ~2000: the primary is
    moved there with its proper motion before the SHAPE_SAME_ARCSEC test). e_neighbours: the largest, over the three
    catalogues, of the summed neighbour_ellipticity of the entries within SHAPE_NEIGHBOUR_FAR_ARCSEC as a vector
    (angle 2 × the neighbour's PA; e_neighbours_pa its PA) with the flux ratio against the primary in that catalogue's
    band (1 for a neighbour without one; a catalogue where the primary has no magnitude adds nothing). Also n_gaia_any (all Gaia within
    SHAPE_NEIGHBOUR_ARCSEC, G < 20), ruwe and galaxy_or_qso of the Gaia primary, a Gaia G < SHAPE_BRIGHT_G within
    SHAPE_BRIGHT_ARCSEC (bright_near) and the pair PA of the two brightest Gaia."""
    from .sky import SkyTree, angsep  # noqa: F401
    from skyshift_core import tangent_offset_arcsec

    out = {k: np.zeros(len(ra), np.int64) for k in ("n_gaia_any", "n_gaia_explain", "n_cw_explain", "n_wide_explain", "n_tm_explain")}
    e_nb = np.zeros(len(ra))
    e_nb_pa = np.full(len(ra), np.nan)
    gprim = np.full(len(ra), np.nan)
    gsep = np.full(len(ra), np.nan)
    ruwe = np.full(len(ra), np.nan)
    gq = np.zeros(len(ra), bool)
    has_cw = np.zeros(len(ra), bool)
    pair_pa = np.full(len(ra), np.nan)
    pair_sep = np.full(len(ra), np.nan)
    pair_dg = np.full(len(ra), np.nan)
    g = refcat[refcat["cat"] == "gaia"].reset_index(drop=True)
    w = refcat[refcat["cat"] == "catwise"].reset_index(drop=True)
    t = refcat[refcat["cat"].isin(["2mass", "tmass"])].reset_index(drop=True)
    gt, wt = SkyTree(g["ra"].to_numpy(), g["dec"].to_numpy()), SkyTree(w["ra"].to_numpy(), w["dec"].to_numpy())
    tt = SkyTree(t["ra"].to_numpy(), t["dec"].to_numpy())
    far = max(SHAPE_NEIGHBOUR_ARCSEC, SHAPE_NEIGHBOUR_FAR_ARCSEC)
    gh = gt.query_radius(ra, dec, far)
    wh = wt.query_radius(ra, dec, far)
    th = tt.query_radius(ra, dec, far)
    gw = gt.query_radius(ra, dec, SHAPE_WIDE_ARCSEC)
    ww = wt.query_radius(ra, dec, SHAPE_WIDE_ARCSEC)
    gG, gra, gdec = g["g_mag"].to_numpy(np.float64), g["ra"].to_numpy(), g["dec"].to_numpy()
    wW, wra, wdec = w["w1_mag"].to_numpy(np.float64), w["ra"].to_numpy(), w["dec"].to_numpy()
    tJ, tra, tdec = (t["j_mag"].to_numpy(np.float64) if "j_mag" in t else np.full(len(t), np.nan)), t["ra"].to_numpy(), t["dec"].to_numpy()

    def col(df, c, fill=np.nan):
        return pd.to_numeric(df[c], errors="coerce").to_numpy(np.float64) if c in df else np.full(len(df), fill)

    gpm, wpm = (col(g, "pmra"), col(g, "pmdec"), col(g, "epoch_mjd", 57388.5)), (col(w, "pmra"), col(w, "pmdec"), col(w, "epoch_mjd", 57170.0))
    tep = col(t, "epoch_mjd", 51544.5)
    gru = g["ruwe"].to_numpy(np.float64) if "ruwe" in g else np.full(len(g), np.nan)
    gqso = g["galaxy_or_qso"].to_numpy(bool) if "galaxy_or_qso" in g else np.zeros(len(g), bool)

    def flux_ratio(mag, mag_p):
        if not np.isfinite(mag_p):
            return np.zeros(len(mag))  # the primary has no magnitude in this band: no ratio to weigh with
        with np.errstate(invalid="ignore", over="ignore"):
            f = 10 ** (-0.4 * (mag - mag_p))
        return np.where(np.isfinite(f), f, 1.0)  # a neighbour without a magnitude: as bright as the object

    def vec(i, f, r, nra, ndec):
        e = neighbour_ellipticity(f, r)
        if len(e) == 0:
            return 0.0, 0.0
        de, dn = tangent_offset_arcsec(ra[i], dec[i], nra, ndec)
        ang = 2 * np.arctan2(de, dn)
        return float(np.sum(e * np.cos(ang))), float(np.sum(e * np.sin(ang)))

    for i in range(len(ra)):
        gi20, wi20, ti20 = np.asarray(gh[i], np.int64), np.asarray(wh[i], np.int64), np.asarray(th[i], np.int64)
        gs20 = _sep(ra[i], dec[i], gra[gi20], gdec[gi20])
        ws20 = _sep(ra[i], dec[i], wra[wi20], wdec[wi20])
        ts20 = _sep(ra[i], dec[i], tra[ti20], tdec[ti20])
        gi, gs = gi20[gs20 <= SHAPE_NEIGHBOUR_ARCSEC], gs20[gs20 <= SHAPE_NEIGHBOUR_ARCSEC]
        wi, ws = wi20[ws20 <= SHAPE_NEIGHBOUR_ARCSEC], ws20[ws20 <= SHAPE_NEIGHBOUR_ARCSEC]
        out["n_gaia_any"][i] = len(gi)
        gp = gi[gs <= SHAPE_PRIMARY_ARCSEC]
        p = gp[np.argmin(np.nan_to_num(gG[gp], nan=99.0))] if len(gp) else -1
        wp = wi[ws <= SHAPE_PRIMARY_ARCSEC]
        q = wp[np.argmin(np.nan_to_num(wW[wp], nan=99.0))] if len(wp) else -1
        has_cw[i] = q >= 0
        if p >= 0:
            gprim[i], gsep[i], ruwe[i], gq[i] = gG[p], float(_sep(ra[i], dec[i], gra[[p]], gdec[[p]])[0]), gru[p], gqso[p]
            others = gi[gi != p]
            out["n_gaia_explain"][i] = int(np.sum(~(gG[others] > gG[p] + SHAPE_NEIGHBOUR_DMAG)))
        else:
            others = gi
            if q >= 0 and len(others):  # the CatWISE primary's own Gaia counterpart is not a second object
                others = others[_sep(wra[q], wdec[q], gra[others], gdec[others]) > SHAPE_SAME_ARCSEC]
            out["n_gaia_explain"][i] = len(others)
        wo = wi[wi != q] if q >= 0 else wi
        if p >= 0 and len(wo):  # CatWISE's entry for the Gaia primary is the same object
            wo = wo[_sep(gra[p], gdec[p], wra[wo], wdec[wo]) > SHAPE_SAME_ARCSEC]
        if q >= 0:
            out["n_cw_explain"][i] = int(np.sum(~(wW[wo] > wW[q] + SHAPE_NEIGHBOUR_DMAG)))
        else:
            out["n_cw_explain"][i] = len(wo)
        # bright neighbours out to SHAPE_WIDE_ARCSEC (not the object's own entries)
        ga, wa = np.asarray(gw[i], np.int64), np.asarray(ww[i], np.int64)
        ga = ga[ga != p]
        if p < 0 and q >= 0 and len(ga):
            ga = ga[_sep(wra[q], wdec[q], gra[ga], gdec[ga]) > SHAPE_SAME_ARCSEC]
        wa = wa[wa != q]
        if p >= 0 and len(wa):
            wa = wa[_sep(gra[p], gdec[p], wra[wa], wdec[wa]) > SHAPE_SAME_ARCSEC]
        glim = gG[p] + SHAPE_WIDE_DMAG if p >= 0 else SHAPE_WIDE_NO_PRIMARY[0]
        wlim = wW[q] + SHAPE_WIDE_DMAG if q >= 0 and np.isfinite(wW[q]) else SHAPE_WIDE_NO_PRIMARY[1]
        out["n_wide_explain"][i] = int(np.sum(gG[ga] <= glim) + np.sum(wW[wa] <= wlim))
        # 2MASS: the primary's own entry (Gaia or CatWISE primary moved to the 2MASS epoch) is not a neighbour
        same_t = np.zeros(len(ti20), bool)
        for prim, (pra, pdec, pm) in ((p, (gra, gdec, gpm)), (q, (wra, wdec, wpm))):
            if prim >= 0 and len(ti20):
                dt = (tep[ti20] - pm[2][prim]) / 365.25
                a, b = _at_epoch(pra[prim], pdec[prim], pm[0][prim], pm[1][prim], dt)
                same_t |= _sep(a, b, tra[ti20], tdec[ti20]) <= SHAPE_SAME_ARCSEC
        tp = ti20[same_t][np.argmin(np.nan_to_num(tJ[ti20[same_t]], nan=99.0))] if same_t.any() else -1
        tn, tns = ti20[~same_t], ts20[~same_t]
        out["n_tm_explain"][i] = int(np.sum(tns <= SHAPE_NEIGHBOUR_ARCSEC))
        # every catalogued neighbour to SHAPE_NEIGHBOUR_FAR_ARCSEC, weighted by its light inside the window
        gn = gi20 != p
        if p < 0 and q >= 0 and len(gi20):
            gn &= _sep(wra[q], wdec[q], gra[gi20], gdec[gi20]) > SHAPE_SAME_ARCSEC
        wn = wi20 != q
        if p >= 0 and len(wi20):
            wn &= _sep(gra[p], gdec[p], wra[wi20], wdec[wi20]) > SHAPE_SAME_ARCSEC
        vs = [vec(i, flux_ratio(gG[gi20[gn]], gG[p] if p >= 0 else np.nan), gs20[gn], gra[gi20[gn]], gdec[gi20[gn]]),
              vec(i, flux_ratio(wW[wi20[wn]], wW[q] if q >= 0 else np.nan), ws20[wn], wra[wi20[wn]], wdec[wi20[wn]]),
              vec(i, flux_ratio(tJ[tn], tJ[tp] if tp >= 0 else np.nan), tns, tra[tn], tdec[tn])]
        k = int(np.argmax([np.hypot(*v) for v in vs]))
        e_nb[i] = float(np.hypot(*vs[k]))
        if e_nb[i] > 0:
            e_nb_pa[i] = float(np.mod(np.degrees(np.arctan2(vs[k][1], vs[k][0])) / 2, 180.0))
        if len(gi) >= 2:
            two = gi[np.argsort(np.nan_to_num(gG[gi], nan=99.0))[:2]]
            e, n = tangent_offset_arcsec(gra[two[0]], gdec[two[0]], gra[two[1]], gdec[two[1]])
            pair_pa[i] = float(np.mod(np.degrees(np.arctan2(e, n)), 180.0))
            pair_sep[i] = float(np.hypot(e, n))
            pair_dg[i] = float(abs(gG[two[1]] - gG[two[0]]))
    bright = g[g["g_mag"] < SHAPE_BRIGHT_G]
    bt = SkyTree(bright["ra"].to_numpy(), bright["dec"].to_numpy())
    bsep, _ = bt.nearest(ra, dec)
    return pd.DataFrame({**out, "e_neighbours": e_nb, "e_neighbours_pa": e_nb_pa, "gaia_g": gprim, "gaia_sep_arcsec": gsep, "ruwe": ruwe, "galaxy_or_qso": gq,
                         "catwise_match": has_cw, "bright_near": bsep <= SHAPE_BRIGHT_ARCSEC,
                         "pair_pa_deg": pair_pa, "pair_sep_arcsec": pair_sep, "pair_dg": pair_dg})


def _sep(ra0, dec0, ra, dec) -> np.ndarray:
    if len(ra) == 0:
        return np.zeros(0)
    from .sky import angsep
    return np.asarray(angsep(ra0, dec0, ra, dec), np.float64) * 3600.0  # arcsec


def neighbours_explain(t: pd.DataFrame) -> np.ndarray:
    """The catalogued neighbours' predicted ellipticity explains the stretch: projected on the measured PA (cos 2ΔPA)
    ≥ SHAPE_NEIGHBOUR_E_FRAC × excess; without a measured stretch (pa_deg, excess) its size ≥ SHAPE_NEIGHBOUR_E_MAX."""
    if "e_neighbours" not in t:
        return np.zeros(len(t), bool)
    e = t["e_neighbours"].to_numpy(np.float64)
    if {"pa_deg", "excess", "e_neighbours_pa"} <= set(t.columns):
        d = np.deg2rad(t["e_neighbours_pa"].to_numpy(np.float64) - t["pa_deg"].to_numpy(np.float64))
        along = np.where(e > 0, e * np.cos(2 * d), 0.0)
        return np.nan_to_num(along) >= SHAPE_NEIGHBOUR_E_FRAC * t["excess"].to_numpy(np.float64)
    return e >= SHAPE_NEIGHBOUR_E_MAX


def one_object(ctx: pd.DataFrame) -> np.ndarray:
    """The catalogues list exactly one object here (a Gaia or CatWISE primary, no neighbour that could explain a stretch)."""
    tm = ctx["n_tm_explain"].to_numpy() == 0 if "n_tm_explain" in ctx else True
    enb = ~neighbours_explain(ctx)
    return ((np.isfinite(ctx["gaia_g"]) | ctx["catwise_match"]) & (ctx["n_gaia_explain"] == 0)
            & (ctx["n_cw_explain"] == 0) & (ctx["n_wide_explain"] == 0)).to_numpy(bool) & tm & enb


# --- candidates -----------------------------------------------------------------------------------
def shape_score(sig, one_visit) -> np.ndarray:
    s = 1.0 - np.exp(-np.asarray(sig, np.float64) / SHAPE_SIG_SCALE)
    return np.where(np.asarray(one_visit, bool), s * SHAPE_ONE_VISIT_WEIGHT, s)


def shape_reasons(row) -> list[str]:
    out = ["elongated"]
    if row.get("steady_pa"):
        out.append("steady_pa")
    if row.get("one_visit"):
        out.append("one_visit")
    out.append("one_catalogue_source")
    if row.get("class") == "galaxy":
        out.append("class_galaxy")
    return out


def shape_why(row) -> str:
    pa = float(row["pa_deg"])
    head = (f"Looks like two objects here but the catalogues list one: the image is stretched along PA {pa:.0f}° "
            f"({row['sig']:.1f}σ over {int(row['n_dots'])} detections)")
    arms = ", the same way in the short- and long-wavelength detectors" if row.get("steady_pa") else ""
    if row.get("one_visit"):
        return head + " in one visit only, as if something passed by" + arms + "; worth a look."
    return head + arms + "; worth a look."


# --- the run -----------------------------------------------------------------------------------------
SHAPE_COLUMNS = ["x", "y", "a_arcsec", "b_arcsec", "theta_deg", "snr", "flags", "flags_peak"]
SHAPE_PHOTO_FRAC = 0.38        # share of the zone's photos read (~0.5 MB of columns each; hunt: ~1,400 photos, ~0.7 GB)
SHAPE_SEED = 20261114


def read_member_shapes(store, members: pd.DataFrame, photos: pd.DataFrame, workers: int = 32) -> pd.DataFrame:
    """The shape columns of the member dots of `photos` (range reads of SHAPE_COLUMNS), with source_id and photo."""
    from concurrent.futures import ThreadPoolExecutor

    import pyarrow.parquet as pq

    from .store import dots_rel

    key = members["obs_id"].astype(str) + "D" + members["detector"].astype(int).astype(str)
    pidx = dict(zip(photos["obs_id"].astype(str) + "D" + photos["detector"].astype(int).astype(str), photos["photo"]))
    sel = key.isin(pidx).to_numpy()
    m = members[sel]
    groups = pd.Series(np.arange(len(m))).groupby(key[sel].to_numpy()).indices
    src, rows = m["source_id"].to_numpy(np.int64), m["dot_row"].to_numpy(np.int64)

    def one(k):
        idx = groups[k]
        try:
            obs, det = k.rsplit("D", 1)
            with store.fs.open(store.path(dots_rel(obs, int(det))), "rb") as f:
                t = pq.ParquetFile(f).read(columns=SHAPE_COLUMNS).to_pandas()
        except Exception:  # noqa: BLE001
            return None
        r = rows[idx]
        ok = r < len(t)
        t = t.iloc[r[ok]].reset_index(drop=True)
        t.insert(0, "photo", np.int32(pidx[k]))
        t.insert(0, "source_id", src[idx][ok])
        return t

    with ThreadPoolExecutor(max(1, workers)) as ex:
        parts = [t for t in ex.map(one, list(groups)) if t is not None]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["source_id", "photo", *SHAPE_COLUMNS])


def held_out(source_id) -> np.ndarray:
    """Half of the sources, by a hash of source_id (ids are almost all even): the false-alarm test set. The other half
    is the reference."""
    s = np.asarray(source_id).astype(np.uint64)
    with np.errstate(over="ignore"):
        return (((s * np.uint64(0x9E3779B97F4A7C15)) >> np.uint64(40)) & np.uint64(1)).astype(bool)


def clean_sources(t: pd.DataFrame) -> np.ndarray:
    return (~t["saturated"].astype(bool) & ~t["bright_near"].astype(bool) & (t["art"].fillna(0) < SHAPE_FLAG_SHARE)).to_numpy()


def single_stars(t: pd.DataFrame) -> np.ndarray:
    """Isolated, unsaturated, ordinary single Gaia stars: the reference (and, held out, the false-alarm test)."""
    return (one_object(t) & clean_sources(t) & (t["n_gaia_any"] == 1).to_numpy() & np.isfinite(t["gaia_g"]).to_numpy()
            & (t["gaia_g"] > 11).to_numpy() & (t["gaia_g"] < 18.5).to_numpy() & (t["ruwe"] < 1.4).to_numpy()
            & (t["class"] != "galaxy").to_numpy() & ~t["galaxy_or_qso"].astype(bool).to_numpy())


def known_sources_only(d: pd.DataFrame, sources: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Member dots of sources that sources.parquet lists. A spectra rerun rewrites sources.parquet after link wrote
    members.parquet and can leave a member source out (hunt, 14 Nov: 2 sources): their dots go, with the count."""
    keep = d["source_id"].isin(sources["source_id"]).to_numpy()
    return d[keep].reset_index(drop=True), int((~keep).sum())


def measure(d: pd.DataFrame, photos: pd.DataFrame, sources: pd.DataFrame, refcat: pd.DataFrame,
            classes: pd.DataFrame | None) -> tuple[pd.DataFrame, dict]:
    """Shapes of every source with >= SHAPE_MIN_DOTS usable dots, with its catalogue context, the reference split
    (held_out: the test half, the rest the reference) and the noise model. d: read_member_shapes's table."""
    from skyshift_core import tangent_offset_arcsec

    d = d.merge(photos[["photo", "detector", "mjd", "visit"]], on="photo", how="left").rename(columns={"detector": "det"})
    d, n_orphan = known_sources_only(d, sources)
    pos = sources.set_index("source_id")
    sra, sdec = pos["ra"].reindex(d["source_id"]).to_numpy(), pos["dec"].reindex(d["source_id"]).to_numpy()
    cen = pd.DataFrame({"photo": d["photo"], "ra": sra, "dec": sdec}).groupby("photo")[["ra", "dec"]].median()
    east, north = tangent_offset_arcsec(cen["ra"].reindex(d["photo"]).to_numpy(), cen["dec"].reindex(d["photo"]).to_numpy(), sra, sdec)
    good = (d["snr"].to_numpy() >= 20) & ((d["flags_peak"].to_numpy(np.int64) & SAT_BITS) == 0) & np.isfinite(east)
    J = photo_jacobians(d["photo"].to_numpy()[good], d["x"].to_numpy(np.float64)[good], d["y"].to_numpy(np.float64)[good],
                        east[good], north[good])
    use = usable_dots(d) & d["photo"].isin(list(J)).to_numpy()
    du = d[use].reset_index(drop=True)
    Jn = {p: m / np.sqrt(abs(np.linalg.det(m))) for p, m in J.items()}
    du["e1"], du["e2"] = sky_ellipticity(du["a_arcsec"], du["b_arcsec"], du["theta_deg"], np.stack([Jn[p] for p in du["photo"].to_numpy()]))
    art = (d["flags"].to_numpy(np.int64) & 0xFFFFFFFF & ARTEFACT_BITS) != 0
    per = pd.DataFrame({"source_id": d["source_id"], "art": art, "mjd": d["mjd"]}).groupby("source_id").agg(art=("art", "mean"), mjd=("mjd", "mean"))
    cnt = du.groupby("source_id").size()
    ids = cnt[cnt >= SHAPE_MIN_DOTS].index.to_numpy()
    sub = pos.loc[ids]
    ctx = catalogue_context(sub["ra"].to_numpy(), sub["dec"].to_numpy(), refcat)
    ctx.insert(0, "source_id", ids)
    ctx = ctx.merge(sources[["source_id", "ra", "dec", "saturated"]], on="source_id")
    ctx["class"] = ctx["source_id"].map(classes.set_index("source_id")["class"]) if classes is not None and len(classes) else None
    for c in ("extended_score", "size_ratio", "photoz_good"):  # galaxy_like (main() joins photoz's `good` as photoz_good)
        if classes is not None and c in classes:
            ctx[c] = ctx["source_id"].map(classes.drop_duplicates("source_id").set_index("source_id")[c])
    ctx = ctx.merge(per, left_on="source_id", right_index=True, how="left")
    single = single_stars(ctx)
    sid = ctx["source_id"].to_numpy()
    is_ref = du["source_id"].isin(sid[single & ~held_out(sid)]).to_numpy()
    du = residuals(du, is_ref)
    noise = noise_model(du, is_ref)
    du["sigma"] = dot_sigma(du, noise)
    t = ctx.merge(source_shapes(du[du["source_id"].isin(ids)]), on="source_id", how="inner")
    scale = [float(np.sqrt(abs(np.linalg.det(m)))) for m in J.values()]
    info = {"photos_read": int(photos["photo"].nunique()), "photos_with_jacobian": len(J), "member_dots": len(d),
            "dots_without_source": n_orphan,
            "usable_dots": len(du), "sources_measured": len(t),
            "pixel_scale_arcsec_median": float(np.median(scale)) if scale else None,
            "reference_stars": int((single & ~held_out(sid)).sum()), "test_stars": int((single & held_out(sid)).sum()),
            "noise_sigma": {f"D{k[0]} S/N {SHAPE_SNR_BINS[k[1]]:g}+": round(v, 4) for k, v in sorted(noise.items())}}
    return t, info


def gaia_pairs(t: pd.DataFrame, max_dg: float = 1.0) -> np.ndarray:
    """Two Gaia stars 3–10″ apart within max_dg in G: what the test must see (the recall check)."""
    return (clean_sources(t) & (t["n_gaia_any"] == 2).to_numpy() & (t["pair_sep_arcsec"] >= 3).to_numpy()
            & (t["pair_sep_arcsec"] <= 10).to_numpy() & (t["pair_dg"] <= max_dg).to_numpy() & (t["class"] != "galaxy").to_numpy())


def checks(t: pd.DataFrame, sig_min: float | None = None) -> dict:
    """False alarms on held-out single stars (held_out) and recall on Gaia pairs, with the pairs' PA, at sig_min
    (default SHAPE_SIG_MIN). A single star that fires is not always noise: a faint uncatalogued neighbour is real."""
    f = fires(t, sig_min)
    test = single_stars(t) & held_out(t["source_id"].to_numpy())
    out = {"single_stars": int(test.sum()), "single_fire": int(f[test].sum()),
           "single_fire_rate": float(f[test].mean()) if test.any() else None, "single_by_g": {}, "pairs": {}}
    for lo, hi in [(11, 14), (14, 16), (16, 17.5), (17.5, 18.5)]:
        m = test & (t["gaia_g"] >= lo).to_numpy() & (t["gaia_g"] < hi).to_numpy()
        out["single_by_g"][f"G {lo}-{hi}"] = {"n": int(m.sum()), "fire": int(f[m].sum())}
    for dg in (1.0, 2.5):
        p = gaia_pairs(t, dg)
        row = {"n": int(p.sum()), "fire": int(f[p].sum()), "recall": float(f[p].mean()) if p.any() else None, "by_sep": {}}
        for lo, hi in [(3, 5), (5, 7), (7, 10)]:
            m = p & (t["pair_sep_arcsec"] >= lo).to_numpy() & (t["pair_sep_arcsec"] < hi).to_numpy()
            dpa = pa_diff(t["pa_deg"].to_numpy()[m & f], t["pair_pa_deg"].to_numpy()[m & f])
            row["by_sep"][f"{lo}-{hi}″"] = {"n": int(m.sum()), "fire": int(f[m].sum()),
                                            "pa_vs_gaia_median_deg": float(np.median(dpa)) if len(dpa) else None,
                                            "pa_within_20deg": float(np.mean(dpa < 20)) if len(dpa) else None}
        out["pairs"][f"dG<={dg:g}"] = row
    return out


def galaxy_like(t: pd.DataFrame) -> np.ndarray:
    """classify's galaxy, Gaia's galaxy_or_qso, a good photo-z (photoz_good), or no Gaia primary and an extended
    profile (extended_score > SHAPE_GALAXY_EXT or size_ratio > SHAPE_GALAXY_SIZE; NaN passes)."""
    def col(c, fill=np.nan):
        return pd.to_numeric(t[c], errors="coerce").to_numpy(np.float64) if c in t else np.full(len(t), fill)

    no_gaia = ~np.isfinite(col("gaia_g"))
    with np.errstate(invalid="ignore"):
        extended = (col("extended_score") > SHAPE_GALAXY_EXT) | (col("size_ratio") > SHAPE_GALAXY_SIZE)
    good_z = t["photoz_good"].fillna(False).astype(bool).to_numpy() if "photoz_good" in t else np.zeros(len(t), bool)
    return ((t["class"] == "galaxy").to_numpy() | t["galaxy_or_qso"].astype(bool).to_numpy() | good_z
            | (no_gaia & extended))


def select(t: pd.DataFrame, cap: int = SHAPE_CAP, exclude=None) -> tuple[pd.DataFrame, dict]:
    """The candidates: the shape test fires, one catalogue object, clean, not a galaxy (galaxy_like); the `cap` most
    significant. exclude: source_ids never listed (the fragments step's bright_fragment tags; None: that step hasn't run)."""
    f = fires(t)
    one = one_object(t) & clean_sources(t)
    gal = galaxy_like(t)
    frag = np.isin(t["source_id"].to_numpy(np.int64), np.asarray([] if exclude is None else exclude, np.int64))
    keep = f & one & ~gal & ~frag
    c = t[keep].sort_values("sig", ascending=False).head(cap).copy()
    info = {"one_object_clean": int(one.sum()), "fire": int((one & f).sum()), "galaxies_dropped": int((one & f & gal).sum()),
            "fragments_dropped": int((one & f & ~gal & frag).sum()), "fragment_tags": exclude is not None,
            "kept_before_cap": int(keep.sum()), "kept": len(c)}
    return c, info


def stats_from_sources(sources: pd.DataFrame) -> pd.DataFrame:
    """Per source: the detector where its spectrum has the highest S/N, that S/N and the median channel flux
    (as candidates.source_stats)."""
    import warnings

    from .spectra import NCH_DET, NDET

    if len(sources) == 0:  # nothing selected (pilot, 14 Nov): an empty list, not a crash in np.stack
        return pd.DataFrame({"source_id": np.zeros(0, np.int64), "det": np.zeros(0, np.int64), "snr": np.zeros(0),
                             "flux_ujy": np.zeros(0)})
    f = np.stack(sources["flux_ujy"].to_numpy()).astype(np.float64).reshape(len(sources), NDET, NCH_DET)
    e = np.stack(sources["err_ujy"].to_numpy()).astype(np.float64).reshape(len(sources), NDET, NCH_DET)
    with np.errstate(invalid="ignore", divide="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        s = np.where(np.isfinite(f) & np.isfinite(e) & (e > 0), f / e, 0.0)
        snr = np.sqrt(np.sum(np.where(s > 0, s, 0.0) ** 2, axis=2))
        flux = np.nanmedian(f, axis=2)
    best = np.argmax(snr, axis=1)
    k = np.arange(len(sources))
    return pd.DataFrame({"source_id": sources["source_id"].to_numpy(np.int64), "det": best + 1, "snr": snr[k, best],
                         "flux_ujy": flux[k, best]})


def candidate_table(c: pd.DataFrame, stats: pd.DataFrame) -> pd.DataFrame:
    """shape_candidates.parquet, what candidates.py exports: one row per candidate."""
    c = c.merge(stats, on="source_id", how="left")
    rows = c.to_dict("records")
    return pd.DataFrame({
        "source_id": c["source_id"].to_numpy(np.int64), "ra": c["ra"].to_numpy(), "dec": c["dec"].to_numpy(),
        "mjd": c["mjd"].to_numpy(), "det": c["det"].to_numpy(), "snr": c["snr"].to_numpy(), "flux_ujy": c["flux_ujy"].to_numpy(),
        "score": shape_score(c["sig"], c["one_visit"]), "sig": c["sig"].to_numpy(), "excess": c["excess"].to_numpy(),
        "pa_deg": c["pa_deg"].to_numpy(), "n_dots": c["n_dots"].to_numpy(), "steady_pa": c["steady_pa"].to_numpy(bool),
        "one_visit": c["one_visit"].to_numpy(bool), "gaia_match": np.isfinite(c["gaia_g"].to_numpy(np.float64)),
        "gaia_g": c["gaia_g"].to_numpy(), "ruwe": c["ruwe"].to_numpy(), "catwise_match": c["catwise_match"].to_numpy(bool),
        "reasons": [",".join(shape_reasons(r)) for r in rows], "why": [shape_why(r) for r in rows], "flags": "",
    })


def merge_into(candidates_json, rows_parquet, zone: str = "hunt") -> dict:
    """Add the shape rows to an existing candidates.json without re-running the exporter: the file's other
    candidates are kept as they are (shape rows already in it are replaced), the shape rows go through the
    exporter's own conversion (candidates.shape_candidates, finish) and the list is re-sorted with its key.
    Returns the new counts."""
    import json
    import shutil
    import tempfile
    from pathlib import Path

    from . import candidates as K
    from .store import Store, results_rel

    p = Path(candidates_json)
    doc = json.loads(p.read_text())
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(tmp)
        dst = Path(store.path(results_rel(zone, K.SHAPE_FILE)))
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(rows_parquet, dst)
        rows = K.shape_candidates(store, zone)
    rows = K.dedupe([c for c in doc["candidates"] if c["kind"] == "shape" and c["zone"] != zone], rows)
    gb = K.galactic_b([c["ra"] for c in rows], [c["dec"] for c in rows])
    frames = p.parent
    for c, b in zip(rows, gb):
        c["galactic_b"] = float(b)
        c["frames_url"] = f"cand/{c['id']}/frames.json" if (frames / "cand" / c["id"] / "frames.json").exists() else None
    keep = [c for c in doc["candidates"] if not (c["kind"] == "shape" and c["zone"] == zone)]
    zones = list(doc.get("zones") or [])
    if zone not in zones:
        zones.append(zone)
    # the other rows keep their order; shape (the last kind) goes after the zone's last row, best score first
    order = {z: i for i, z in enumerate(zones)}
    rows.sort(key=lambda c: (-c["score"], c["id"]))
    at = max((i + 1 for i, c in enumerate(keep) if order.get(c["zone"], len(order)) <= order[zone]), default=0)
    allc = keep[:at] + [K.finish(c) for c in rows] + keep[at:]
    doc["zones"] = zones
    doc["counts"] = {**doc.get("counts", {}), **{k: sum(c["kind"] == k for c in allc) for k in K.KINDS}}
    doc["candidates"] = allc
    p.write_text(K.dumps(doc))
    return doc["counts"]


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import time
    from pathlib import Path

    from .store import Store, photolist_rel, results_rel

    ap = argparse.ArgumentParser(prog="python -m skyshift_pipeline.shape", description=__doc__.split("\n")[0])
    ap.add_argument("--zone", default="hunt")
    ap.add_argument("--root", default="gs://nasa-glazing-data", help="dots/ and photolists/ (and the tables, without --tables)")
    ap.add_argument("--tables", help="local folder with members, sources, classes and refcat_<zone> parquet files")
    ap.add_argument("--out", help="folder for shape_candidates.parquet and shape_report.json (default results/<zone>/ in --root)")
    ap.add_argument("--frac", type=float, default=SHAPE_PHOTO_FRAC)
    ap.add_argument("--cap", type=int, default=SHAPE_CAP)
    ap.add_argument("--workers", type=int, default=32)
    ap.add_argument("--dots", help="reuse a member_shapes.parquet (photos.parquet next to it) from an earlier run")
    ap.add_argument("--keep", action="store_true", help="also write every measured source (shape_all.parquet) to --out")
    ap.add_argument("--merge-into", help="only add the rows of --rows to this candidates.json (no measurement)")
    ap.add_argument("--rows", help="with --merge-into: a shape_candidates.parquet")
    a = ap.parse_args(argv)
    if a.merge_into:
        print(json.dumps(merge_into(a.merge_into, a.rows, a.zone)))
        return 0
    t0 = time.time()
    store = Store(a.root)
    if a.out:
        Path(a.out).mkdir(parents=True, exist_ok=True)

    def table(name: str, rel: str, columns=None) -> pd.DataFrame:
        return pd.read_parquet(Path(a.tables) / name, columns=columns) if a.tables else store.read_parquet(rel, columns)

    if a.dots:
        d = pd.read_parquet(a.dots)
        photos = pd.read_parquet(Path(a.dots).with_name("photos.parquet"))
    else:
        pl = store.read_parquet(photolist_rel(a.zone), ["obs_id", "detector", "mjd", "visit"])
        members = table("members.parquet", results_rel(a.zone, "members.parquet"))
        photos = members[["obs_id", "detector"]].drop_duplicates().astype({"obs_id": str, "detector": int})
        photos = photos.merge(pl.astype({"obs_id": str, "detector": int}), on=["obs_id", "detector"], how="left")
        photos = photos.sort_values(["obs_id", "detector"]).reset_index(drop=True)
        photos = photos[np.random.default_rng(SHAPE_SEED).random(len(photos)) < a.frac].reset_index(drop=True)
        photos["photo"] = np.arange(len(photos))
        d = read_member_shapes(store, members, photos, a.workers)
        del members
    sources = table("sources.parquet", results_rel(a.zone, "sources.parquet"), ["source_id", "ra", "dec", "saturated", "flux_ujy", "err_ujy"])
    refcat = pd.read_parquet(Path(a.tables) / f"refcat_{a.zone}.parquet") if a.tables else store.read_parquet(f"refcat/{a.zone}.parquet")
    refcat = refcat[refcat["cat"].astype(str).isin(["gaia", "catwise", "2mass", "tmass"])].assign(cat=lambda r: r["cat"].astype(str))
    try:
        classes = table("classes.parquet", results_rel(a.zone, "classes.parquet"), ["source_id", "class", "extended_score", "size_ratio"])
    except Exception:  # noqa: BLE001
        classes = None
    try:  # a good photo-z makes it a galaxy (galaxy_like)
        pz = table("photoz.parquet", results_rel(a.zone, "photoz.parquet"), ["source_id", "good"])
        if classes is not None:
            classes = classes.merge(pz.rename(columns={"good": "photoz_good"}), on="source_id", how="left")
    except Exception:  # noqa: BLE001
        pass
    t, info = measure(d, photos, sources, refcat, classes)
    if a.keep and a.out:
        t.to_parquet(Path(a.out) / "shape_all.parquet", index=False)
    chk = checks(t)
    try:  # the fragments step's bright_fragment and duplicate tags (a piece or a second source of a star): never listed
        from .fragments import DROPPED
        fr = table("fragments.parquet", results_rel(a.zone, "fragments.parquet"), ["source_id", "tag"])
        frag = fr["source_id"].to_numpy(np.int64)[np.isin(fr["tag"].astype(str).to_numpy(), DROPPED)]
    except Exception:  # noqa: BLE001  not run for this zone yet: nothing dropped
        frag = None
    c, sel = select(t, a.cap, exclude=frag)
    # the list's own threshold (the cap makes it stricter than the rule) and the checks there
    sel["list_sig_min"] = float(c["sig"].min()) if len(c) else None
    chk_list = checks(t, sel["list_sig_min"]) if len(c) else None
    chk_by_sig = {f"sig>={s:g}": {k: checks(t, s)[k] for k in ("single_fire_rate",)} | {"pair_recall": checks(t, s)["pairs"]["dG<=1"]["recall"]}
                  for s in (5, 6, 10, 15, 20, 30)}
    out = candidate_table(c, stats_from_sources(sources[sources["source_id"].isin(c["source_id"])]))
    rule = {k: (str(v) if isinstance(v, tuple) or (isinstance(v, float) and not math.isfinite(v)) else v)
            for k, v in globals().items() if k.startswith("SHAPE_") and isinstance(v, (int, float, tuple))}
    report = {"zone": a.zone, "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "measure": info, "checks": chk,
              "checks_at_list_threshold": chk_list, "checks_by_sig": chk_by_sig,
              "selection": sel, "rule": rule, "seconds": round(time.time() - t0, 1)}
    if a.out:
        Path(a.out).mkdir(parents=True, exist_ok=True)
        out.to_parquet(Path(a.out) / "shape_candidates.parquet", index=False)
        (Path(a.out) / "shape_report.json").write_text(json.dumps(report, indent=1, default=str))
    else:
        store.write_parquet(results_rel(a.zone, "shape_candidates.parquet"), out)
        store.write_json(results_rel(a.zone, "shape_report.json"), report)
    print(json.dumps({"checks": chk, "at_list": chk_list, "by_sig": chk_by_sig, "selection": sel}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
