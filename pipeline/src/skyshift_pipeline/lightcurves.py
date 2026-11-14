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
                   "scatter_rel", "chi2_nu", "amp_rel", "visits", "visit_median_rel", "visit_ratio", "variable"]
POINT_COLUMNS = ["source_id", "ref_id", "origin", "mjd", "visit", "detector", "wavelength_um", "flux_ujy",
                 "flux_err_ujy", "detected", "model_ujy", "rel_flux"]


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
    ok = np.isfinite(f["flux_ujy"].to_numpy(np.float64)) & np.isfinite(e) & (e > 0)
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


def summarise(p: pd.DataFrame) -> pd.DataFrame:
    g, G = group_codes(p)
    first = np.full(G, -1, np.int64)
    first[g[::-1]] = np.arange(len(g))[::-1]
    f = p["flux_ujy"].to_numpy(np.float64)
    e = p["flux_err_ujy"].to_numpy(np.float64)
    m = p["model_ujy"].to_numpy(np.float64)
    rel = p["rel_flux"].to_numpy(np.float64)
    n = np.bincount(g, minlength=G)
    fin = np.isfinite(rel)
    med_rel = group_median(g[fin], rel[fin], G)
    scatter = 1.4826 * group_median(g[fin], np.abs(rel[fin] - med_rel[g[fin]]), G)
    chi = np.where(np.isfinite(m), ((f - m) / e) ** 2, 0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        chi2_nu = np.bincount(g, chi, G) / np.maximum(n - 1, 1)
    q = pd.Series(rel[fin]).groupby(g[fin]).quantile([0.05, 0.95]).unstack()
    amp = np.full(G, np.nan)
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
    })
    out["variable"] = ((out["n"] >= C.LC_MIN_POINTS) & (out["chi2_nu"] > C.LC_VARIABLE_CHI2_NU)
                       & (out["scatter_rel"] > C.LC_VARIABLE_SCATTER)).to_numpy()
    out = out[out["n"] >= C.LC_MIN_POINTS]
    return out.sort_values(["source_id", "ref_id", "detector", "origin"]).reset_index(drop=True)


def compute(dots: pd.DataFrame | None, forced: pd.DataFrame | None, crossids: pd.DataFrame | None,
            sources: pd.DataFrame | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    parts = []
    if dots is not None and len(dots):
        parts.append(points_from_dots(dots))
    if forced is not None and len(forced):
        parts.append(points_from_forced(forced, crossids))
    if not parts:
        empty = pd.DataFrame({c: pd.Series(dtype="float64") for c in POINT_COLUMNS})
        return empty, pd.DataFrame({c: pd.Series(dtype="float64") for c in SUMMARY_COLUMNS})
    p = pd.concat(parts, ignore_index=True)
    p = add_model(p, sources)
    p = p.sort_values(["source_id", "ref_id", "detector", "mjd"], kind="stable").reset_index(drop=True)
    s = summarise(p)
    p["origin"] = p["origin"].astype("category")
    return p[POINT_COLUMNS], s


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
    pts, summ = compute(d, forced, crossids, sources)
    store.write_parquet(results_rel(zone.name, "lightcurves.parquet"), pts)
    store.write_parquet(results_rel(zone.name, "lightcurves_summary.parquet"), summ)
    st = {**st, "points": int(len(pts)), "forced_points": int(len(forced)),
          "summary_rows": int(len(summ)), "variable_rows": int(summ["variable"].sum()) if len(summ) else 0,
          "variable_sources": int(summ.loc[summ["variable"], "source_id"].nunique()) if len(summ) else 0,
          "wall_s": round(time.time() - t0, 1)}
    L.info("lightcurves %s: %s", zone.name, st)
    return st
