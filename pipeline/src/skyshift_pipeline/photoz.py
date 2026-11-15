"""Step `photoz`: rough photometric redshifts for galaxy-like sources from their 102-channel spectra.

Templates: the 12 EAZY `tweak_fsps_QSF_12_v3` templates (pipeline/data/, see its README), F_ν in the rest frame.
Model of channel c at redshift z: the template's mean F_ν over the channel's rest-frame range
[lo/(1+z), hi/(1+z)] (mean in ln λ, as the channels are log-spaced). Every (z, template) is fitted with a free
amplitude (amplitude >= 0), σ² = err² + (PHOTOZ_TEMPLATE_ERR × flux)². z grid PHOTOZ_Z_MIN..MAX step PHOTOZ_Z_STEP.
p(z) ∝ Σ_templates exp(−(χ² − χ²_min) / (2 s)), s = max(1, χ²_min / dof): a bad fit widens p(z) instead of
giving a falsely sharp one.
  z_best    peak of p(z)            z_lo, z_hi  16th and 84th percentiles of p(z) (68% interval)
  chi2      χ²_min (best z and template); chi2_nu = chi2 / (n_channels − 2)
  odds      p(|z − z_best| < PHOTOZ_ODDS_DZ × (1 + z_best))
  good      odds >= PHOTOZ_GOOD_ODDS and chi2_nu <= PHOTOZ_GOOD_CHI2_NU and n_channels >= PHOTOZ_GOOD_MIN_CHANNELS
            and z_best > PHOTOZ_GOOD_MIN_Z (z ≈ 0 is where stars and failed fits land)
Also pah_33_excess_z / pah_33_sig_z: the PAH 3.3 µm index (config.INDEX_WINDOWS) moved to z_best, and
label_pah_33_z: PAH emission at the galaxy's redshift, the label candidates.py lists (good; sig_z >= INDEX_SIG;
excess_z >= LABEL_MIN_DEPTH; the moved band window measured in >= LABEL_MIN_VISITS visits when sources.parquet has
visit_bits; while the moved blue continuum window still overlaps the water-ice band (z ≲ 0.05), no ice dip at
z = 0, sig < LABEL_SUPPORT_SIG, as classify's label_pah_33).

Validation: SDSS DR17 spectroscopic galaxies in the zone (SkyServer SQL via astroquery.sdss, cached in
cache/sdss/<zone>.parquet), matched within PHOTOZ_SDSS_MATCH_ARCSEC → photoz_report.json with σ_NMAD of
Δz/(1+z), the outlier fraction (|Δz|/(1+z) > PHOTOZ_OUTLIER), bias and n, overall, for `good` fits and for
galaxies brighter than PHOTOZ_BRIGHT_AB in the K window.
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import config as C
from . import indices as IX
from . import log
from .store import Store, results_rel

L = log.get("photoz")

DATA = Path(__file__).resolve().parents[2] / "data"


# --- templates and the model grid -----------------------------------------------------------
def load_templates(name: str = C.PHOTOZ_TEMPLATES) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """(rest µm (n,), F_ν (n_templates, n), template names)."""
    df = pd.read_csv(DATA / name, comment="#")
    names = [c for c in df.columns if c != "um"]
    return df["um"].to_numpy(np.float64), df[names].to_numpy(np.float64).T, names


def z_grid() -> np.ndarray:
    n = int(round((C.PHOTOZ_Z_MAX - C.PHOTOZ_Z_MIN) / C.PHOTOZ_Z_STEP)) + 1
    return np.round(C.PHOTOZ_Z_MIN + C.PHOTOZ_Z_STEP * np.arange(n), 6)


def model_grid(z: np.ndarray, um: np.ndarray, tmpl: np.ndarray,
               lo: np.ndarray = IX.LO, hi: np.ndarray = IX.HI) -> np.ndarray:
    """(n_z, n_templates, n_channels) mean template F_ν over each channel's rest-frame range."""
    x = np.log(um)
    # cumulative ∫ F d ln λ on the template grid (trapezoids)
    cum = np.concatenate([np.zeros((tmpl.shape[0], 1)),
                          np.cumsum(0.5 * (tmpl[:, 1:] + tmpl[:, :-1]) * np.diff(x), axis=1)], axis=1)
    a = np.log(lo)[None, :] - np.log1p(z)[:, None]  # (n_z, n_ch)
    b = np.log(hi)[None, :] - np.log1p(z)[:, None]
    if a.min() < x[0] or b.max() > x[-1]:
        raise ValueError("templates don't cover the channels at every z")
    ca = np.stack([np.interp(a, x, c) for c in cum], 1)  # (n_z, n_t, n_ch)
    cb = np.stack([np.interp(b, x, c) for c in cum], 1)
    return (cb - ca) / (b - a)[:, None, :]


# --- the fit ---------------------------------------------------------------------------------
def fit(flux: np.ndarray, err: np.ndarray, M: np.ndarray, z: np.ndarray, chunk: int = C.PHOTOZ_CHUNK) -> pd.DataFrame:
    """Fit every spectrum (rows of flux/err, (N, n_ch)) against M (n_z, n_t, n_ch)."""
    nz, nt, nch = M.shape
    Mf = M.reshape(nz * nt, nch)
    Mf2 = Mf * Mf
    N = flux.shape[0]
    res = {k: np.full(N, np.nan) for k in ("z_best", "z_lo", "z_hi", "chi2", "chi2_nu", "odds", "amp")}
    res["template"] = np.zeros(N, np.int16)
    res["n_channels"] = np.zeros(N, np.int16)
    for s0 in range(0, N, chunk):
        f = flux[s0:s0 + chunk]
        e = err[s0:s0 + chunk]
        have = np.isfinite(f) & np.isfinite(e) & (e > 0)
        f0 = np.where(have, f, 0.0)
        var = np.where(have, e * e + (C.PHOTOZ_TEMPLATE_ERR * f0) ** 2, np.inf)
        w = np.where(have, 1.0 / var, 0.0)
        n = have.sum(1)
        sfm = (w * f0) @ Mf.T            # (n, nz*nt)
        smm = w @ Mf2.T
        sff = (w * f0 * f0).sum(1)
        with np.errstate(invalid="ignore", divide="ignore"):
            amp = np.where(smm > 0, sfm / smm, 0.0)
        amp = np.clip(amp, 0.0, None)
        chi2 = sff[:, None] - 2 * amp * sfm + amp * amp * smm
        chi2 = chi2.reshape(-1, nz, nt)
        amp = amp.reshape(-1, nz, nt)
        cmin = chi2.min(axis=(1, 2))
        dof = np.maximum(n - 2, 1)
        scale = np.maximum(1.0, cmin / dof)
        pz = np.exp(-(chi2 - cmin[:, None, None]) / (2 * scale[:, None, None])).sum(2)  # (n, nz)
        pz /= pz.sum(1, keepdims=True)
        k = np.argmax(pz, 1)
        zb = z[k]
        cdf = np.cumsum(pz, 1)
        zlo = z[np.minimum((cdf < 0.16).sum(1), nz - 1)]
        zhi = z[np.minimum((cdf < 0.84).sum(1), nz - 1)]
        win = np.abs(z[None, :] - zb[:, None]) <= C.PHOTOZ_ODDS_DZ * (1 + zb[:, None])
        odds = (pz * win).sum(1)
        flat = chi2.reshape(chi2.shape[0], -1).argmin(1)
        bt = flat % nt
        ok = n >= C.PHOTOZ_MIN_CHANNELS
        sl = slice(s0, s0 + f.shape[0])
        res["z_best"][sl] = np.where(ok, zb, np.nan)
        res["z_lo"][sl] = np.where(ok, zlo, np.nan)
        res["z_hi"][sl] = np.where(ok, zhi, np.nan)
        res["chi2"][sl] = np.where(ok, cmin, np.nan)
        res["chi2_nu"][sl] = np.where(ok, cmin / dof, np.nan)
        res["odds"][sl] = np.where(ok, odds, np.nan)
        res["amp"][sl] = np.where(ok, amp.reshape(amp.shape[0], -1)[np.arange(amp.shape[0]), flat], np.nan)
        res["template"][sl] = np.where(ok, bt + 1, 0)
        res["n_channels"][sl] = n
    out = pd.DataFrame(res)
    for c in ("z_best", "z_lo", "z_hi", "chi2", "chi2_nu", "odds"):
        out[c] = out[c].astype(np.float32)
    out["good"] = ((out["odds"] >= C.PHOTOZ_GOOD_ODDS) & (out["chi2_nu"] <= C.PHOTOZ_GOOD_CHI2_NU)
                   & (out["n_channels"] >= C.PHOTOZ_GOOD_MIN_CHANNELS) & (out["z_best"] > C.PHOTOZ_GOOD_MIN_Z))
    return out.drop(columns="amp")


def photoz_frame(src: pd.DataFrame) -> pd.DataFrame:
    """Photo-z for every row of src (source_id, flux_ujy, err_ujy list columns)."""
    um, tmpl, _ = load_templates()
    z = z_grid()
    M = model_grid(z, um, tmpl)
    flux, err = IX.stack(src["flux_ujy"]), IX.stack(src["err_ujy"])
    out = fit(flux, err, M, z)
    out.insert(0, "source_id", src["source_id"].to_numpy(np.int64))
    kind, band, cont = C.INDEX_WINDOWS["pah_33"]
    zb = out["z_best"].to_numpy(np.float64)
    v, s = IX.index(flux, err, kind, band, cont, scale=1 + np.where(np.isfinite(zb), zb, 0.0))
    out["pah_33_excess_z"] = np.where(np.isfinite(zb), v, np.nan).astype(np.float32)
    out["pah_33_sig_z"] = np.where(np.isfinite(zb), s, np.nan).astype(np.float32)
    seen = True
    if "visit_bits" in src and len(src):
        vb = np.stack([np.asarray(x, np.int64) for x in src["visit_bits"]])
        seen = IX.window_visits(vb, *band, scale=1 + np.where(np.isfinite(zb), zb, 0.0)) >= C.LABEL_MIN_VISITS
    _, ice_sig = IX.index(flux, err, *C.INDEX_WINDOWS["h2o_ice_305"])
    with np.errstate(invalid="ignore"):
        ice_overlap = cont[0][0] * (1 + zb) < C.INDEX_WINDOWS["h2o_ice_305"][1][1]  # moved continuum inside the ice band
        out["label_pah_33_z"] = (out["good"].to_numpy(bool) & (s >= C.INDEX_SIG) & (v >= C.LABEL_MIN_DEPTH["pah_33"])
                                 & seen & ~(ice_overlap & (ice_sig >= C.LABEL_SUPPORT_SIG)))
    out["mag_ab_k"] = IX.magnitudes(flux, err)["mag_ab_k"]
    return out


# --- validation against SDSS DR17 --------------------------------------------------------------
def sdss_specz(store: Store, zone: C.Zone) -> pd.DataFrame:
    """SDSS DR17 spectroscopic galaxies in the zone (zWarning = 0), cached."""
    rel = f"cache/sdss/{zone.name}.parquet"
    if store.exists(rel):
        return store.read_parquet(rel)
    from astroquery.sdss import SDSS

    parts = []
    # one query per degree of Dec keeps each answer well under SkyServer's row limit and timeout
    for d0 in np.arange(zone.dec_min, zone.dec_max, 1.0):
        d1 = min(d0 + 1.0, zone.dec_max)
        q = ("SELECT s.ra, s.dec, s.z, s.zErr FROM SpecObj AS s "
             f"WHERE s.ra BETWEEN {zone.ra_min} AND {zone.ra_max} AND s.dec >= {d0:.6f} AND s.dec < {d1:.6f} "
             "AND s.class = 'GALAXY' AND s.zWarning = 0")
        t = SDSS.query_sql(q, data_release=17, timeout=180)
        if t is not None and len(t):
            parts.append(t.to_pandas())
    df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["ra", "dec", "z", "zErr"])
    df = df.astype({"ra": "float64", "dec": "float64", "z": "float64", "zErr": "float64"})
    store.write_parquet(rel, df)
    return df


def nmad_stats(zp: np.ndarray, zs: np.ndarray) -> dict:
    dz = (zp - zs) / (1 + zs)
    if dz.size == 0:
        return {"n": 0, "sigma_nmad": None, "outlier_frac": None, "bias": None}
    return {"n": int(dz.size),
            "sigma_nmad": float(1.4826 * np.median(np.abs(dz - np.median(dz)))),
            "outlier_frac": float(np.mean(np.abs(dz) > C.PHOTOZ_OUTLIER)),
            "bias": float(np.median(dz))}


def validate(pz: pd.DataFrame, src: pd.DataFrame, spec: pd.DataFrame) -> dict:
    """Match photo-z sources to SDSS within PHOTOZ_SDSS_MATCH_ARCSEC and summarise."""
    from scipy.spatial import cKDTree

    def xyz(r, d):
        r, d = np.deg2rad(np.asarray(r, np.float64)), np.deg2rad(np.asarray(d, np.float64))
        return np.column_stack([np.cos(d) * np.cos(r), np.cos(d) * np.sin(r), np.sin(d)])

    rep = {"sdss_galaxies_in_zone": int(len(spec)), "match_arcsec": C.PHOTOZ_SDSS_MATCH_ARCSEC,
           "outlier_definition": f"|dz|/(1+z_spec) > {C.PHOTOZ_OUTLIER}"}
    pos = src.set_index("source_id").loc[pz["source_id"], ["ra", "dec"]]
    fitted = np.isfinite(pz["z_best"].to_numpy())
    if not len(spec) or not fitted.any():
        rep.update({"all": nmad_stats(np.zeros(0), np.zeros(0))})
        return rep
    tree = cKDTree(xyz(spec["ra"], spec["dec"]))
    chord = 2 * np.sin(np.deg2rad(C.PHOTOZ_SDSS_MATCH_ARCSEC / 3600) / 2)
    dist, j = tree.query(xyz(pos["ra"], pos["dec"]), distance_upper_bound=chord)
    m = np.isfinite(dist) & fitted
    zp = pz["z_best"].to_numpy(np.float64)[m]
    zs = spec["z"].to_numpy(np.float64)[j[m]]
    good = pz["good"].to_numpy(bool)[m]
    kmag = pz["mag_ab_k"].to_numpy(np.float64)[m]
    rep["all"] = nmad_stats(zp, zs)
    rep["good"] = nmad_stats(zp[good], zs[good])
    with np.errstate(invalid="ignore"):
        b = kmag < C.PHOTOZ_BRIGHT_AB
    rep[f"k_ab_lt_{C.PHOTOZ_BRIGHT_AB:g}"] = nmad_stats(zp[b], zs[b])
    rep["by_k_ab"] = {}
    for lo, hi in ((-99, 15), (15, 16), (16, 17), (17, 18), (18, 99)):
        with np.errstate(invalid="ignore"):
            mm = (kmag >= lo) & (kmag < hi)
        rep["by_k_ab"][f"{lo}..{hi}"] = nmad_stats(zp[mm], zs[mm])
    rep["z_spec_median"] = float(np.median(zs))
    return rep


def report(store: Store, zone: C.Zone, pz: pd.DataFrame, src: pd.DataFrame, n_input: int, chosen: str,
           validate_sdss: bool = True) -> dict:
    """photoz_report.json: counts and, against SDSS DR17, the zone's photo-z quality (tiles.py: on the merged tables)."""
    rep = {"zone": zone.name, "fitted": int(np.isfinite(pz["z_best"]).sum()), "input": int(n_input), "selection": chosen,
           "good": int(pz["good"].sum()),
           "label_pah_33_z": int(pz["label_pah_33_z"].sum()) if "label_pah_33_z" in pz else 0, "templates": C.PHOTOZ_TEMPLATES,
           "z_grid": [C.PHOTOZ_Z_MIN, C.PHOTOZ_Z_MAX, C.PHOTOZ_Z_STEP]}
    if validate_sdss:
        try:
            rep["sdss_dr17"] = validate(pz, src, sdss_specz(store, zone))
        except Exception as e:  # noqa: BLE001  (network; the photo-z table is written anyway)
            L.warning("photoz %s: SDSS validation failed: %s", zone.name, e)
            rep["sdss_dr17"] = {"error": str(e)[:300]}
    return rep


def run(store: Store, zone: C.Zone, validate_sdss: bool = True) -> dict:
    t0 = time.time()
    src = store.read_parquet(results_rel(zone.name, "sources.parquet"))
    rel = results_rel(zone.name, "classes.parquet")
    if store.exists(rel):
        cls = store.read_parquet(rel, columns=["source_id", "class"])
        pick = set(cls.loc[cls["class"].isin(C.PHOTOZ_CLASSES), "source_id"])
        if "saturated" in src:
            src = src[~src["saturated"].to_numpy(bool)]
        sub = src[src["source_id"].isin(pick)].reset_index(drop=True)
        chosen = f"classes {list(C.PHOTOZ_CLASSES)}, not saturated"
    else:
        sub = src.reset_index(drop=True)
        chosen = "all sources (no classes.parquet)"
    pz = photoz_frame(sub)
    t1 = time.time()
    nbytes = store.write_parquet(results_rel(zone.name, "photoz.parquet"), pz)
    rep = report(store, zone, pz, src, len(sub), chosen, validate_sdss)
    store.write_json(results_rel(zone.name, "photoz_report.json"), rep)
    st = {**{k: v for k, v in rep.items() if k != "sdss_dr17"},
          "sdss_all": rep.get("sdss_dr17", {}).get("all"), "photoz_bytes": int(nbytes),
          "fit_s": round(t1 - t0, 1), "wall_s": round(time.time() - t0, 1)}
    L.info("photoz %s: %s", zone.name, st)
    return st
