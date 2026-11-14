"""Step `lightcurves`: brightness against time for every fixed source and catalogue star.

The SPHEREx catch: every photo sees a source at a different wavelength (the filter changes across
the detector), so a raw time series mixes time and colour. A red star measured at 0.8 µm one day and
1.0 µm the next looks "variable" when it isn't. So:
- points are compared only within one detector;
- each point is divided by a model of the source's spectrum at its own wavelength: the 102-channel
  spectrum from sources.parquet (the channel the point falls in), scaled per (source, detector,
  origin) by the median of flux / model (PSF and aperture fluxes differ by an aperture correction);
  without a spectrum (catalogue stars with only forced photometry, or an empty channel), a weighted
  quadratic in ln(wavelength) fitted to that (source, detector)'s own points.
`rel_flux` = flux / (scale × model) is ≈ 1 for a constant source; its scatter and chi2_nu measure
variability.

Error model (pilot, 14 Nov: the quoted errors were too small, median chi2_nu 1.96 for dot light curves and
1.28 for forced ones, so 8.8% / 4.2% came out "variable"). Per (detector, origin) the error becomes
    σ_cal² = (k · σ)² + (floor · model)²
fitted on the Gaia-matched light curves (all of them when there are too few): first the shape, from the
robust variance (1.4826 MAD)² of (flux − model) / σ in S/N bins, ≈ k² + floor² · S/N²; then k and floor are
scaled together so that the median chi2_nu of those light curves is 1 (the median ignores the few real
variables). chi2_nu and `variable` use σ_cal; chi2_nu_raw keeps the old value. The fitted values go to
lightcurves_report.json and the step summary.
Bad photos: a photo (origin, detector, mjd) whose Gaia-matched points sit off as a group (|median of
(flux − model) / σ_cal| > LC_PHOTO_MAX_MEDIAN_CHI over >= LC_PHOTO_MIN_REF points) gets `photo_ok` false;
its points stay in the table but are left out of chi2_nu, chi2_nu_raw and scatter_rel. (Pilot: one forced D4
photo, 7,698 stars at a median −2.8σ, made 1,172 of the 1,714 forced-D4 "variables".) Photos the `qa` step graded
red (results/<zone>/qa_photos.parquet, when it exists) get photo_ok false the same way (config QA_LIGHTCURVES).
One point is not a variable: `variable` tests chi2_nu_robust, chi2_nu without the light curve's single worst
point (an unflagged cosmic-ray hit or a bad pixel), against LC_VARIABLE_CHI2_NU.

Points: dots of fixed sources (members; PSF flux when the dot files have it, else aperture flux;
MASK bits out), and forced photometry at catalogue positions (forced/<obs_id>D<det>.parquet),
non-detections included, tied to a source through crossids (Gaia, CatWISE, 2MASS ref_id).

Writes results/<zone>/lightcurves.parquet (one row per point) and lightcurves_summary.parquet
(one row per (source or ref, detector, origin)).
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd
from skyshift_core import MASK_BITS

from . import config as C
from . import log
from .motion_io import load_forced, member_dots
from .spectra import NCH, channel_of, group_median
from .store import Store, results_rel

L = log.get("lightcurves")

SUMMARY_COLUMNS = ["source_id", "ref_id", "detector", "origin", "n", "n_detected", "flux_median_ujy", "um_median",
                   "scatter_rel", "chi2_nu", "amp_rel", "visits", "visit_median_rel", "visit_ratio", "variable",
                   "chi2_nu_raw", "chi2_nu_robust"]
POINT_COLUMNS = ["source_id", "ref_id", "origin", "mjd", "visit", "detector", "wavelength_um", "flux_ujy",
                 "flux_err_ujy", "detected", "model_ujy", "rel_flux", "flux_err_cal_ujy", "photo_ok"]


def points_from_dots(d: pd.DataFrame) -> pd.DataFrame:
    flags = d["flags"].to_numpy(np.int64) & 0xFFFFFFFF
    psf = d["flux_psf_ujy"].to_numpy(np.float64)
    psf_e = d["flux_psf_err_ujy"].to_numpy(np.float64)
    use_psf = np.isfinite(psf) & np.isfinite(psf_e)
    f = np.where(use_psf, psf, d["flux_ujy"].to_numpy(np.float64))
    e = np.where(use_psf, psf_e, d["flux_err_ujy"].to_numpy(np.float64))
    ok = ((flags & MASK_BITS) == 0) & np.isfinite(f) & np.isfinite(e) & (e > 0)
    return pd.DataFrame({
        "source_id": d["source_id"].to_numpy(np.int64)[ok], "ref_id": "", "origin": "dot",
        "mjd": d["mjd"].to_numpy(np.float64)[ok], "visit": d["visit"].to_numpy(np.int16)[ok],
        "detector": d["detector"].to_numpy(np.int8)[ok], "wavelength_um": d["wavelength_um"].to_numpy(np.float32)[ok],
        "flux_ujy": f[ok].astype(np.float32), "flux_err_ujy": e[ok].astype(np.float32), "detected": True,
    })


def points_from_forced(f: pd.DataFrame, crossids: pd.DataFrame | None) -> pd.DataFrame:
    sid = np.full(len(f), -1, np.int64)
    if crossids is not None and len(crossids) and len(f):
        ref = f["ref_id"].astype(str)
        for col in ("gaia_ref_id", "catwise_ref_id", "tmass_ref_id"):
            if col not in crossids:
                continue
            m = crossids[crossids[col].astype(str) != ""]
            lut = pd.Series(m["source_id"].to_numpy(np.int64), index=m[col].astype(str).to_numpy())
            lut = lut[~lut.index.duplicated()]
            hit = lut.reindex(ref.to_numpy()).to_numpy()
            fill = (sid < 0) & np.isfinite(hit)
            sid[fill] = hit[fill].astype(np.int64)
    e = f["flux_err_ujy"].to_numpy(np.float64)
    fl = (f["flags"].to_numpy(np.int64) & 0xFFFFFFFF) if "flags" in f else np.zeros(len(f), np.int64)
    ok = np.isfinite(f["flux_ujy"].to_numpy(np.float64)) & np.isfinite(e) & (e > 0) & ((fl & MASK_BITS) == 0)
    return pd.DataFrame({
        "source_id": sid[ok], "ref_id": f["ref_id"].astype(str).to_numpy()[ok], "origin": "forced",
        "mjd": f["mjd"].to_numpy(np.float64)[ok], "visit": f["visit"].to_numpy(np.int16)[ok],
        "detector": f["detector"].to_numpy(np.int8)[ok], "wavelength_um": f["wavelength_um"].to_numpy(np.float32)[ok],
        "flux_ujy": f["flux_ujy"].to_numpy(np.float32)[ok], "flux_err_ujy": e[ok].astype(np.float32),
        "detected": f["detected"].to_numpy(bool)[ok],
    })


def group_codes(p: pd.DataFrame) -> tuple[np.ndarray, int]:
    """One code per (source or ref, detector, origin)."""
    sid = p["source_id"].to_numpy(np.int64)
    key = sid.copy()
    no = sid < 0
    if no.any():
        key[no] = -1 - pd.factorize(p["ref_id"].to_numpy()[no])[0].astype(np.int64)
    k = key * 16 + p["detector"].to_numpy(np.int64) * 2 + (p["origin"].to_numpy() == "forced")
    codes, uniq = pd.factorize(k)
    return codes.astype(np.int64), len(uniq)


LC_POLY_MIN_POINTS = 6


def poly_model(gl: np.ndarray, G: int, x: np.ndarray, y: np.ndarray, w: np.ndarray, deg: int = 2) -> np.ndarray:
    """Per-group weighted polynomial fit of y(x), evaluated at each point (batched normal equations)."""
    k = deg + 1
    sw = np.bincount(gl, w, G)
    x0 = np.bincount(gl, w * x, G) / np.maximum(sw, 1e-300)
    xc = x - x0[gl]
    A = np.zeros((G, k, k))
    for a in range(k):
        for b in range(a, k):
            A[:, a, b] = A[:, b, a] = np.bincount(gl, w * xc ** (a + b), G)
    rhs = np.stack([np.bincount(gl, w * y * xc ** a, G) for a in range(k)], axis=1)
    n = np.bincount(gl, minlength=G)
    few = n < LC_POLY_MIN_POINTS
    A[few, 1:, :] = 0.0
    A[few, :, 1:] = 0.0
    A[few, 1:, 1:] = np.eye(k - 1)
    rhs[few, 1:] = 0.0
    bad = ~(np.abs(np.linalg.det(A)) > 0)
    A[bad] = np.eye(k)
    rhs[bad] = 0.0
    c = np.linalg.solve(A, rhs[..., None])[..., 0]
    c[bad] = np.nan
    return sum(c[gl, a] * xc ** a for a in range(k))


def add_model(p: pd.DataFrame, sources: pd.DataFrame | None) -> pd.DataFrame:
    """model_ujy and rel_flux per point (see the module docstring)."""
    g, G = group_codes(p)
    f = p["flux_ujy"].to_numpy(np.float64)
    e = p["flux_err_ujy"].to_numpy(np.float64)
    wl = p["wavelength_um"].to_numpy(np.float64)
    model = np.full(len(p), np.nan)
    if sources is not None and len(sources):
        spec = np.stack(sources["flux_ujy"].to_numpy()).astype(np.float64) if len(sources) else np.zeros((0, NCH))
        pos = pd.Index(sources["source_id"].to_numpy(np.int64)).get_indexer(p["source_id"].to_numpy(np.int64))
        ch = channel_of(p["detector"].to_numpy(), wl)
        ok = (pos >= 0) & (ch >= 0)
        model[ok] = spec[pos[ok], ch[ok]]
        model[~(model > 0)] = np.nan
    # scale per group for the spectrum model
    has = np.isfinite(model)
    scale = np.ones(G)
    if has.any():
        sc = group_median(g[has], f[has] / model[has], G)
        scale = np.where(np.isfinite(sc) & (sc > 0), sc, 1.0)
    # groups where any point lacks a spectrum model: a weighted quadratic in ln(wavelength), fitted to
    # the group's own points (a weighted mean with fewer than LC_POLY_MIN_POINTS points)
    line_g = np.zeros(G, bool)
    line_g[g[~has]] = True
    lp = line_g[g]
    if lp.any():
        model[lp] = poly_model(g[lp], G, np.log(np.clip(np.nan_to_num(wl[lp], nan=1.0), 0.1, None)), f[lp], 1.0 / e[lp] ** 2)
        scale[line_g] = 1.0
    sm = scale[g] * model
    with np.errstate(invalid="ignore", divide="ignore"):
        rel = np.where(np.abs(sm) > 0, f / sm, np.nan)
    p = p.copy()
    p["model_ujy"] = sm.astype(np.float32)
    p["rel_flux"] = rel.astype(np.float32)
    return p


# --- error model -----------------------------------------------------------------------------
def reference_points(p: pd.DataFrame, crossids: pd.DataFrame | None) -> np.ndarray:
    """True for points of Gaia-matched light curves: dots of a source with a Gaia crossid, forced
    photometry of a `gaia:` target."""
    ref = p["ref_id"].astype(str).str.startswith("gaia:").to_numpy()
    if crossids is not None and len(crossids) and "gaia_ref_id" in crossids:
        gs = crossids.loc[crossids["gaia_ref_id"].fillna("").astype(str) != "", "source_id"].to_numpy(np.int64)
        ref = ref | ((p["origin"].astype(str).to_numpy() == "dot") & np.isin(p["source_id"].to_numpy(np.int64), gs))
    return ref


def group_chi2_nu(g: np.ndarray, G: int, f, m, e) -> np.ndarray:
    """Σ ((f − m) / e)² / (n − 1) per group (points without a model count in n but add 0, as before)."""
    n = np.bincount(g, minlength=G)
    with np.errstate(invalid="ignore", divide="ignore"):
        chi = np.where(np.isfinite(m) & (e > 0), ((f - m) / e) ** 2, 0.0)
        return np.bincount(g, chi, G) / np.maximum(n - 1, 1)


def robust_chi2_nu(g: np.ndarray, G: int, f, m, e) -> np.ndarray:
    """group_chi2_nu without each group's LC_VARIABLE_DROP_WORST largest terms (dof reduced to match)."""
    n = np.bincount(g, minlength=G)
    with np.errstate(invalid="ignore", divide="ignore"):
        chi = np.where(np.isfinite(m) & (e > 0), ((f - m) / e) ** 2, 0.0)
    tot = np.bincount(g, chi, G)
    k = int(C.LC_VARIABLE_DROP_WORST)
    if k > 0 and g.size:
        o = np.lexsort((-chi, g))  # by group, largest chi first
        gs = g[o]
        start = np.r_[0, np.flatnonzero(np.diff(gs)) + 1]
        rank = np.arange(gs.size) - np.repeat(start, np.diff(np.r_[start, gs.size]))
        top = rank < k
        tot = tot - np.bincount(gs[top], chi[o][top], G)
    with np.errstate(invalid="ignore", divide="ignore"):
        return tot / np.maximum(n - 1 - k, 1)


def _grid(lo_hi_step) -> np.ndarray:
    lo, hi, step = lo_hi_step
    return np.arange(lo, hi + step / 2, step)


def fit_error_model(p: pd.DataFrame, ref: np.ndarray) -> tuple[np.ndarray, dict]:
    """flux_err_cal per point and {"<origin>_D<det>": fitted values} (module docstring)."""
    f = p["flux_ujy"].to_numpy(np.float64)
    e = p["flux_err_ujy"].to_numpy(np.float64)
    m = p["model_ujy"].to_numpy(np.float64)
    g, G = group_codes(p)
    n_g = np.bincount(g, minlength=G)
    with np.errstate(invalid="ignore", divide="ignore"):
        r = (f - m) / e
        s = np.abs(m) / e
    fin = np.isfinite(r) & np.isfinite(s) & (n_g[g] >= C.LC_MIN_POINTS)
    k_pt, fl_pt = np.ones(len(p)), np.zeros(len(p))
    origin = p["origin"].astype(str).to_numpy()
    det = p["detector"].to_numpy(np.int64)
    kg, fg = _grid(C.LC_ERR_K_GRID), _grid(C.LC_ERR_FLOOR_GRID)
    out: dict[str, dict] = {}
    for o in ("dot", "forced"):
        for d in np.unique(det[origin == o]):
            sel = (origin == o) & (det == d)
            ok = sel & fin
            use, basis = ok & ref, "gaia"
            if np.unique(g[use]).size < C.LC_ERR_MIN_GROUPS:
                use, basis = ok, "all"
            groups = np.unique(g[use])
            key = f"{o}_D{int(d)}"
            if not C.LC_ERR_MODEL or groups.size < C.LC_ERR_MIN_GROUPS:
                out[key] = {"basis": "none", "groups": int(groups.size), "k": 1.0, "floor": 0.0}
                continue
            # 1. shape: robust variance of the normalised residual against S/N
            rr, ss = r[use], s[use]
            edges = np.unique(np.quantile(ss, np.linspace(0, 1, C.LC_ERR_SNR_BINS + 1)))
            b = np.clip(np.searchsorted(edges, ss, side="right") - 1, 0, max(edges.size - 2, 0))
            sb, vb = [], []
            for i in range(max(edges.size - 1, 1)):
                mm = b == i
                if mm.sum() < C.LC_ERR_MIN_BIN_POINTS:
                    continue
                x = rr[mm]
                v = (1.4826 * np.median(np.abs(x - np.median(x)))) ** 2
                if v > 0:
                    sb.append(np.median(ss[mm]))
                    vb.append(v)
            sb, vb = np.asarray(sb), np.asarray(vb)
            if sb.size >= 2:
                pred = kg[:, None, None] ** 2 + (fg[None, :, None] * sb[None, None, :]) ** 2
                cost = ((np.log(pred) - np.log(vb)) ** 2).sum(-1)
                ik, jf = np.unravel_index(np.argmin(cost), cost.shape)
                k, fl = float(kg[ik]), float(fg[jf])
            else:
                k, fl = float(np.sqrt(np.median(vb))) if vb.size else 1.0, 0.0
            # 2. level: median chi2_nu of the reference light curves = 1
            gl = np.searchsorted(groups, g[use])
            before = group_chi2_nu(gl, groups.size, f[use], m[use], e[use])
            cal = np.sqrt((k * e[use]) ** 2 + (fl * m[use]) ** 2)
            c2 = float(np.median(group_chi2_nu(gl, groups.size, f[use], m[use], cal)))
            if np.isfinite(c2) and c2 > 0:
                k, fl = k * np.sqrt(c2), fl * np.sqrt(c2)
            k_pt[sel], fl_pt[sel] = k, fl
            out[key] = {"basis": basis, "groups": int(groups.size), "points": int(use.sum()), "k": k,
                        "floor": fl, "median_chi2_nu_before": round(float(np.median(before)), 3),
                        "snr_bins": [round(float(x), 1) for x in sb],
                        "robust_sd_bins": [round(float(np.sqrt(x)), 3) for x in vb]}
    return _calibrated(e, m, k_pt, fl_pt), out


def _calibrated(e: np.ndarray, m: np.ndarray, k: np.ndarray, fl: np.ndarray) -> np.ndarray:
    with np.errstate(invalid="ignore"):
        return np.sqrt((k * e) ** 2 + (fl * np.where(np.isfinite(m), m, 0.0)) ** 2)


def apply_error_model(p: pd.DataFrame, model: dict) -> np.ndarray:
    """flux_err_cal per point from a fitted model ({"<origin>_D<det>": {"k", "floor"}}, as fit_error_model returns it):
    tiles.py fits the model once on the whole zone and every tile applies it; k = 1, floor = 0 where it has no entry."""
    origin = p["origin"].astype(str).to_numpy()
    det = p["detector"].to_numpy(np.int64)
    k_pt, fl_pt = np.ones(len(p)), np.zeros(len(p))
    for key, v in model.items():
        o, d = key.rsplit("_D", 1)
        sel = (origin == o) & (det == int(d))
        k_pt[sel], fl_pt[sel] = v["k"], v["floor"]
    return _calibrated(p["flux_err_ujy"].to_numpy(np.float64), p["model_ujy"].to_numpy(np.float64), k_pt, fl_pt)


def photo_check(p: pd.DataFrame, ref: np.ndarray, cal: np.ndarray) -> tuple[np.ndarray, list[dict]]:
    """photo_ok per point and the list of bad photos (module docstring)."""
    with np.errstate(invalid="ignore", divide="ignore"):
        chi = (p["flux_ujy"].to_numpy(np.float64) - p["model_ujy"].to_numpy(np.float64)) / cal
    key = pd.MultiIndex.from_arrays([p["origin"].astype(str).to_numpy(), p["detector"].to_numpy(np.int64),
                                     p["mjd"].to_numpy(np.float64)])
    code, uniq = pd.factorize(key)
    use = ref & np.isfinite(chi)
    nref = np.bincount(code[use], minlength=len(uniq))
    med = group_median(code[use], chi[use], len(uniq))
    bad = (nref >= C.LC_PHOTO_MIN_REF) & (np.abs(med) > C.LC_PHOTO_MAX_MEDIAN_CHI)
    rows = [{"origin": o, "detector": int(d), "mjd": round(float(t), 6), "ref_points": int(nref[i]),
             "median_chi": round(float(med[i]), 2)} for i, (o, d, t) in enumerate(uniq) if bad[i]]
    return ~bad[code], sorted(rows, key=lambda r: (r["origin"], r["detector"], r["mjd"]))


def qa_red_mask(p: pd.DataFrame, red: pd.DataFrame | None) -> np.ndarray:
    """True for the points from photos `qa` graded red (qa.red_photos), dots and forced alike: matched by detector and
    exposure time (both tables carry the photo's MJD-AVG; to the second, exposures are ≥ 2 min apart)."""
    if red is None or not len(red) or not len(p):
        return np.zeros(len(p), bool)

    def key(det, mjd):
        return np.asarray(det, np.int64) * 10**12 + np.round(np.asarray(mjd, np.float64) * 86400.0).astype(np.int64)

    return np.isin(key(p["detector"].to_numpy(), p["mjd"].to_numpy()), key(red["detector"].to_numpy(), red["mjd"].to_numpy()))


def summarise(p: pd.DataFrame) -> pd.DataFrame:
    g, G = group_codes(p)
    first = np.full(G, -1, np.int64)
    first[g[::-1]] = np.arange(len(g))[::-1]
    f = p["flux_ujy"].to_numpy(np.float64)
    e_raw = p["flux_err_ujy"].to_numpy(np.float64)
    e = p["flux_err_cal_ujy"].to_numpy(np.float64) if "flux_err_cal_ujy" in p else e_raw
    m = p["model_ujy"].to_numpy(np.float64)
    rel = p["rel_flux"].to_numpy(np.float64)
    pok = p["photo_ok"].to_numpy(bool) if "photo_ok" in p else np.ones(len(p), bool)
    n = np.bincount(g, minlength=G)
    fin = np.isfinite(rel)
    fo = fin & pok
    med_ok = group_median(g[fo], rel[fo], G)
    scatter = 1.4826 * group_median(g[fo], np.abs(rel[fo] - med_ok[g[fo]]), G)
    chi2_nu = group_chi2_nu(g[pok], G, f[pok], m[pok], e[pok])
    chi2_nu_raw = group_chi2_nu(g[pok], G, f[pok], m[pok], e_raw[pok])
    chi2_nu_robust = robust_chi2_nu(g[pok], G, f[pok], m[pok], e[pok])
    amp = np.full(G, np.nan)
    if fin.any():  # (no finite rel_flux at all: an empty quantile table)
        q = pd.Series(rel[fin]).groupby(g[fin]).quantile([0.05, 0.95]).unstack()
        amp[q.index.to_numpy()] = (q[0.95] - q[0.05]).to_numpy()
    vm = pd.DataFrame({"g": g[fin], "v": p["visit"].to_numpy(np.int64)[fin], "r": rel[fin]}).groupby(["g", "v"], sort=True)["r"].median().reset_index()
    vl = vm.groupby("g").agg(visits=("v", list), visit_median_rel=("r", list))
    visits = pd.Series([[]] * G, dtype=object)
    vmed = pd.Series([[]] * G, dtype=object)
    visits.iloc[vl.index.to_numpy()] = [np.asarray(x, np.int16) for x in vl["visits"]]
    vmed.iloc[vl.index.to_numpy()] = [np.asarray(x, np.float32) for x in vl["visit_median_rel"]]
    vfirst = vm.groupby("g")["r"].first().reindex(range(G)).to_numpy()
    vlast = vm.groupby("g")["r"].last().reindex(range(G)).to_numpy()
    nv = vm.groupby("g").size().reindex(range(G)).fillna(0).to_numpy()
    with np.errstate(invalid="ignore", divide="ignore"):
        vratio = np.where(nv >= 2, vlast / vfirst, np.nan)
    out = pd.DataFrame({
        "source_id": p["source_id"].to_numpy(np.int64)[first], "ref_id": p["ref_id"].to_numpy()[first],
        "detector": p["detector"].to_numpy(np.int8)[first], "origin": p["origin"].to_numpy()[first],
        "n": n.astype(np.int32), "n_detected": np.bincount(g, p["detected"].to_numpy(bool).astype(np.float64), G).astype(np.int32),
        "flux_median_ujy": group_median(g, f, G).astype(np.float32),
        "um_median": group_median(g, p["wavelength_um"].to_numpy(np.float64), G).astype(np.float32),
        "scatter_rel": scatter.astype(np.float32), "chi2_nu": chi2_nu.astype(np.float32), "amp_rel": amp.astype(np.float32),
        "visits": visits.to_numpy(), "visit_median_rel": vmed.to_numpy(), "visit_ratio": vratio.astype(np.float32),
        "chi2_nu_raw": chi2_nu_raw.astype(np.float32), "chi2_nu_robust": chi2_nu_robust.astype(np.float32),
    })
    out["variable"] = ((out["n"] >= C.LC_MIN_POINTS) & (out["chi2_nu_robust"] > C.LC_VARIABLE_CHI2_NU)
                       & (out["scatter_rel"] > C.LC_VARIABLE_SCATTER)).to_numpy()
    out = out[out["n"] >= C.LC_MIN_POINTS]
    return out[SUMMARY_COLUMNS].sort_values(["source_id", "ref_id", "detector", "origin"]).reset_index(drop=True)


def compute(dots: pd.DataFrame | None, forced: pd.DataFrame | None, crossids: pd.DataFrame | None,
            sources: pd.DataFrame | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    pts, summ, _ = compute_with_report(dots, forced, crossids, sources)
    return pts, summ


def compute_with_report(dots: pd.DataFrame | None, forced: pd.DataFrame | None, crossids: pd.DataFrame | None,
                        sources: pd.DataFrame | None, qa_red: pd.DataFrame | None = None) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    p = points(dots, forced, crossids, sources)
    if p is None:
        empty = pd.DataFrame({c: pd.Series(dtype="float64") for c in POINT_COLUMNS})
        return empty, pd.DataFrame({c: pd.Series(dtype="float64") for c in SUMMARY_COLUMNS}), {}
    ref = reference_points(p, crossids)
    cal, model = fit_error_model(p, ref)
    photo_ok, bad = photo_check(p, ref, cal)
    red = qa_red_mask(p, qa_red)
    p, s = finish(p, cal, photo_ok & ~red)
    rep = error_report(p, s, ref, model)
    rep["bad_photos"] = bad
    if qa_red is not None:
        rep["qa_red_photos"] = int(len(qa_red))
    return p[POINT_COLUMNS], s, rep


def points(dots: pd.DataFrame | None, forced: pd.DataFrame | None, crossids: pd.DataFrame | None,
           sources: pd.DataFrame | None) -> pd.DataFrame | None:
    """Every point (dots and forced photometry) with its model_ujy and rel_flux, sorted; None when there are none."""
    parts = []
    if dots is not None and len(dots):
        parts.append(points_from_dots(dots))
    if forced is not None and len(forced):
        parts.append(points_from_forced(forced, crossids))
    if not parts:
        return None
    p = pd.concat(parts, ignore_index=True)
    p = add_model(p, sources)
    return p.sort_values(["source_id", "ref_id", "detector", "mjd"], kind="stable").reset_index(drop=True)


def finish(p: pd.DataFrame, cal: np.ndarray, photo_ok: np.ndarray) -> tuple[pd.DataFrame, pd.DataFrame]:
    """points + the calibrated errors and photo_ok → (points with origin as category, summary)."""
    p["flux_err_cal_ujy"] = cal.astype(np.float32)
    p["photo_ok"] = photo_ok
    s = summarise(p)
    p["origin"] = p["origin"].astype("category")
    return p, s


def error_report(p: pd.DataFrame, s: pd.DataFrame, ref: np.ndarray, model: dict) -> dict:
    """The fitted error model and the variable share before (raw errors) and after, per origin, for
    all light curves and the Gaia-matched ones."""
    refkey = pd.DataFrame({"source_id": p["source_id"].to_numpy(np.int64)[ref], "ref_id": p["ref_id"].to_numpy()[ref],
                           "detector": p["detector"].to_numpy(np.int8)[ref],
                           "origin": p["origin"].astype(str).to_numpy()[ref]}).drop_duplicates()
    s = s.assign(origin=s["origin"].astype(str))
    s_ref = s.merge(refkey, on=["source_id", "ref_id", "detector", "origin"], how="inner")
    var_raw = ((s["n"] >= C.LC_MIN_POINTS) & (s["chi2_nu_raw"] > C.LC_VARIABLE_CHI2_NU)
               & (s["scatter_rel"] > C.LC_VARIABLE_SCATTER))
    var_raw_ref = ((s_ref["chi2_nu_raw"] > C.LC_VARIABLE_CHI2_NU) & (s_ref["scatter_rel"] > C.LC_VARIABLE_SCATTER))

    def share(x, o, col):
        m = x["origin"] == o
        return round(float(col[m].mean()), 4) if m.any() else None

    model = {key: {**v, "k": round(float(v["k"]), 4), "floor": round(float(v["floor"]), 4)} for key, v in model.items()}
    rep = {"model": model, "rule": "sigma_cal^2 = (k sigma)^2 + (floor model)^2 per (origin, detector)"}
    for o in ("dot", "forced"):
        so, sr = s[s["origin"] == o], s_ref[s_ref["origin"] == o]
        rep[o] = {
            "rows": int(len(so)), "gaia_rows": int(len(sr)),
            "variable_share_raw": share(s, o, var_raw), "variable_share": share(s, o, s["variable"]),
            "gaia_variable_share_raw": share(s_ref, o, var_raw_ref), "gaia_variable_share": share(s_ref, o, s_ref["variable"]),
            "median_chi2_nu_raw": round(float(so["chi2_nu_raw"].median()), 3) if len(so) else None,
            "median_chi2_nu": round(float(so["chi2_nu"].median()), 3) if len(so) else None,
        }
    return rep


def run(store: Store, zone: C.Zone, workers: int = 16) -> dict:
    t0 = time.time()
    d, st = member_dots(store, zone, workers)
    forced = load_forced(store, zone, workers)
    xid_rel = results_rel(zone.name, "crossids.parquet")
    crossids = store.read_parquet(xid_rel) if store.exists(xid_rel) else None
    src_rel = results_rel(zone.name, "sources.parquet")
    sources = store.read_parquet(src_rel, columns=["source_id", "flux_ujy"]) if store.exists(src_rel) else None
    if not len(forced):
        L.warning("lightcurves %s: no forced photometry yet (forced/): dots only", zone.name)
    from .qa import red_photos
    pts, summ, rep = compute_with_report(d, forced, crossids, sources, red_photos(store, zone))
    store.write_parquet(results_rel(zone.name, "lightcurves.parquet"), pts)
    store.write_parquet(results_rel(zone.name, "lightcurves_summary.parquet"), summ)
    store.write_json(results_rel(zone.name, "lightcurves_report.json"), {"zone": zone.name, **rep})
    st = {**st, "points": int(len(pts)), "forced_points": int(len(forced)),
          "error_model": {k: {"k": v["k"], "floor": v["floor"], "basis": v["basis"]} for k, v in rep.get("model", {}).items()},
          "variable_share": {o: rep[o]["variable_share"] for o in ("dot", "forced") if o in rep},
          "variable_share_raw": {o: rep[o]["variable_share_raw"] for o in ("dot", "forced") if o in rep},
          "bad_photos": len(rep.get("bad_photos", [])), "qa_red_photos": rep.get("qa_red_photos"),
          "summary_rows": int(len(summ)), "variable_rows": int(summ["variable"].sum()) if len(summ) else 0,
          "variable_sources": int(summ.loc[summ["variable"], "source_id"].nunique()) if len(summ) else 0,
          "wall_s": round(time.time() - t0, 1)}
    L.info("lightcurves %s: %s", zone.name, st)
    return st
