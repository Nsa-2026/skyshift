"""Watch catalogued stars: what changed between SPHEREx visits (prototype, 16 Nov; not in `all`).

The project lead's idea: track even known stars and say when something about one changes. SPHEREx sees each spot every
six months (more often near the ecliptic poles), so every catalogued star gets, per visit, a brightness in each of the
six detectors, a colour across them and a position. This module turns the stored tables into alerts, each with a
measured false-alarm rate (docs/watch-changes.md has the design, the measurements and the delivery options).

Kinds of change, all from stored tables (never photos):
- `brightness`: a star's brightness in one detector, visit against visit, beyond its own scatter (lightcurves' points:
  rel_flux = flux / its spectrum at that wavelength, so colour and wavelength are handled as lightcurves does; one
  detector at a time, never across). Each visit's points are combined per light curve (source or catalogue ref,
  detector, origin): clipped weighted mean, error × √χ²_ν, sessions counted. A zone-wide zero point per (detector,
  origin, visit) is removed (median over the Gaia-matched light curves), then a floor per (detector, origin) is fitted
  so that the Gaia-matched changes have a robust spread of 1. A star alerts when one detector changes by
  >= CHANGES_Z sigma and >= CHANGES_MIN_FRAC, another detector agrees in sign at >= CHANGES_Z_CONFIRM sigma, and the
  change survives dropping that visit's most deviant session.
- `colour`: the same visit pair across detectors: ln(after / before) against ln(wavelength), weighted line; the slope
  (a colour change, not a level change) beyond CHANGES_COLOUR_Z sigma and >= CHANGES_COLOUR_MIN across 0.9–4.7 µm.
- `position` (measured, NOT an alert): astrometry's position at its epoch against the Gaia DR3 star moved there with
  Gaia's proper motion (parallax left out: stars with Gaia parallax <= CHANGES_POS_MAX_PLX_MAS only), error =
  astrometry's ra/dec error × a k fitted on the zone's Gaia stars; Gaia neighbours within CHANGES_POS_NEIGHBOUR_ARCSEC
  predict how far they pull the light centre; and our motion over the visits against Gaia's. Pilot (15 Nov tables):
  12% of quiet Gaia stars sit >= 5σ off Gaia's place (uncatalogued faint neighbours in 6″ pixels), and the motion
  test fires on 0.8 per 1,000 with 300–10,000 mas/yr "motions" for stars Gaia measures at a few mas/yr: SPHEREx's
  two visits (1σ 30–1,600 mas/yr) cannot test a Gaia proper motion. Columns pos_off / motion_off say where a star
  sits beyond 5σ; the candidates list does not use them (docs/watch-changes.md).
- Who is listed (brightness, colour): stars with their own SPHEREx source, and catalogue stars measured only by forced
  photometry OUTSIDE the zone box (link never ran there). A catalogue star inside the zone with no source of its own
  (link folded its dots into a neighbour's group) changes between visits 26 per 1,000 in the pilot with no more Gaia
  variables than chance: crowding, not the star (population()).
- `appeared` / `vanished`: orphans.parquet's clean rows (single-visit, no Gaia / CatWISE / 2MASS entry, the other
  visit covered the spot with >= 6 photos), static within the visit (no drift at 3 sigma), no known asteroid, bright
  enough (S/N >= CHANGES_NEW_MIN_SNR) that the other visit should have seen it: in the latest visit = appeared, in an
  earlier one = vanished.

Null test (the false-alarm rate): the same brightness and colour rules on the two halves of each visit (its sessions
split in time). Within two to four weeks almost no star changes, so what fires there is measurement trouble (and the
few fast variables): alerts per 1,000 stars in the halves are the false-alarm rate of the visit-to-visit alerts.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .spectra import group_median

# --- thresholds (prototype; CHANGES_* in config.py when it joins the run) ------------------------------------
CHANGES_SESSION_GAP_D = 10.0 / 1440.0   # a session: points less than 10 minutes apart (the visit rule's session)
CHANGES_CLIP = 5.0                      # a point this many sigma off its visit's median is left out (cosmic ray, edge)
CHANGES_MIN_POINTS = 3                  # per visit (or half) and light curve
CHANGES_MIN_SESSIONS = 2                # the changed visit must hold this many sessions (one session is one look)
CHANGES_Z = 5.0                         # brightness: one detector changed by this many sigma ...
CHANGES_MIN_FRAC = 0.05                 # ... and by at least 5% of the star's brightness ...
CHANGES_Z_CONFIRM = 2.0                 # ... and another detector agrees in sign at this many sigma
CHANGES_FLOOR_SNR = 0.02                # floor fit: light curves whose statistical error is below this (S/N > 50)
CHANGES_FLOOR_MIN = 200                 # ... at least this many, else the floor is 0
CHANGES_COLOUR_Z = 5.0                  # colour: slope of ln(after/before) vs ln(wavelength) beyond this many sigma ...
CHANGES_COLOUR_MIN = 0.10               # ... and changing the D1 / D6 ratio by at least 10%
CHANGES_COLOUR_MIN_DET = 4              # ... measured in at least this many detectors
CHANGES_POS_Z = 5.0                     # position: offset from Gaia's prediction beyond this many sigma (2D, as χ²(2)) ...
CHANGES_POS_MIN_ARCSEC = 0.3            # ... and at least this far (1/20 of a pixel)
CHANGES_POS_MAX_PLX_MAS = 3.0           # parallax is not modelled in the 15 Nov tables: Gaia parallax up to this only
CHANGES_POS_NEIGHBOUR_ARCSEC = 12.0     # Gaia neighbours this close pull the SPHEREx light centre
CHANGES_POS_EXPLAINED = 0.5             # an offset is a blend when the neighbours predict at least this share of it
CHANGES_NEW_MIN_SNR = 20.0              # appeared / vanished: the source's S/N (its best detector's spectrum)
CHANGES_NEW_STATIC_Z = 3.0              # ... and no drift at this many sigma (a transient stays put; a TNO drifts)
CHANGES_NEW_MIN_DOTS = 8                # ... seen in this many photos of its visit (three MWIR glitches link too) ...
CHANGES_NEW_MIN_DETECTORS = 2           # ... by this many detectors
CHANGES_SUMMARY_FLUX_BINS = 30          # summary fallback: brightness bins of the population's scatter
CHANGES_NEIGHBOUR_ARCSEC = 20.0         # bright neighbour: a Gaia star this close (3 px) ...
CHANGES_NEIGHBOUR_DMAG = -2.5           # ... at least this much brighter (10×) holds the star in its wings
CHANGES_NEIGHBOUR_DEFAULT_G = 18.0      # ... the star's G when it has none (CatWISE-only)

# detector centres (µm) for the colour slope when the points carry no wavelength
DET_UM = {1: 0.908, 2: 1.326, 3: 1.960, 4: 3.020, 5: 4.090, 6: 4.690}
DET_RANGE = {1: "0.75–1.1 µm", 2: "1.1–1.6 µm", 3: "1.6–2.4 µm", 4: "2.4–3.8 µm", 5: "3.8–4.4 µm", 6: "4.4–5.0 µm"}

VISIT_COLUMNS = ["star", "source_id", "ref_id", "detector", "origin", "visit", "half", "n", "n_sessions", "mjd_mid",
                 "mean", "err", "chi2_nu", "median"]


# --- per-visit statistics ----------------------------------------------------------------------------------------
def star_keys(source_id: np.ndarray, ref_id: np.ndarray) -> np.ndarray:
    """One int64 per star: the source_id where it has one (>= 0), else −1 − a hash of its catalogue ref (the same
    in every table, so keys of different reads agree)."""
    sid = np.asarray(source_id, np.int64)
    key = sid.copy()
    no = sid < 0
    if no.any():
        refs = pd.Series(np.asarray(ref_id, dtype=object)[no]).astype(str)
        h = pd.util.hash_pandas_object(refs, index=False).to_numpy(np.uint64) & np.uint64((1 << 62) - 1)
        key[no] = -1 - h.astype(np.int64)
    return key


def _first_of_group(g: np.ndarray, G: int) -> np.ndarray:
    first = np.full(G, -1, np.int64)
    first[g[::-1]] = np.arange(g.size)[::-1]
    return first


def visit_table(points: pd.DataFrame, halves: bool = False) -> pd.DataFrame:
    """Per light curve (star, detector, origin) and visit (and, with halves, each half of the visit's sessions):
    n, sessions, mid time, clipped weighted mean of rel_flux and its error (× √χ²_ν when above 1), median.
    points: lightcurves.parquet rows (source_id, ref_id, origin, mjd, visit, detector, rel_flux, flux_err_cal_ujy,
    model_ujy, photo_ok)."""
    p = points
    rel = p["rel_flux"].to_numpy(np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        err = p["flux_err_cal_ujy"].to_numpy(np.float64) / np.abs(p["model_ujy"].to_numpy(np.float64))
    ok = np.isfinite(rel) & np.isfinite(err) & (err > 0)
    if "photo_ok" in p:
        ok &= p["photo_ok"].to_numpy(bool)
    sid = p["source_id"].to_numpy(np.int64)[ok]
    ref = p["ref_id"].to_numpy(dtype=object)[ok]
    star = star_keys(sid, ref)
    det = p["detector"].to_numpy(np.int64)[ok]
    forced = (p["origin"].astype(str).to_numpy() == "forced")[ok]
    vis = p["visit"].to_numpy(np.int64)[ok]
    mjd = p["mjd"].to_numpy(np.float64)[ok]
    rel, err = rel[ok], err[ok]
    if not rel.size:
        return pd.DataFrame({c: pd.Series(dtype="float64") for c in VISIT_COLUMNS})
    lc, _ = pd.factorize(pd.MultiIndex.from_arrays([star, det, forced]))
    o = np.lexsort((mjd, vis, lc))
    star, det, forced, vis, mjd, rel, err, lc, sid, ref = (a[o] for a in (star, det, forced, vis, mjd, rel, err, lc, sid, ref))
    # sessions within (light curve, visit)
    newgrp = np.r_[True, (lc[1:] != lc[:-1]) | (vis[1:] != vis[:-1])]
    newses = newgrp | np.r_[True, np.diff(mjd) > CHANGES_SESSION_GAP_D]
    ses_global = np.cumsum(newses) - 1
    grp = np.cumsum(newgrp) - 1
    ses_first_in_grp = ses_global[_first_of_group(grp, grp.max() + 1)]
    ses = ses_global - ses_first_in_grp[grp]                       # 0.. within the visit
    nses = np.bincount(grp, newses.astype(np.int64))
    if halves:
        half = (2 * ses >= nses[grp]).astype(np.int64)             # first / second half of the visit's sessions
        half[nses[grp] < 2] = -1
    else:
        half = np.zeros(rel.size, np.int64)
    keep = half >= 0
    gkey = grp * 2 + half
    gcode, guniq = pd.factorize(gkey[keep])
    G = len(guniq)
    r, e = rel[keep], err[keep]
    # clip
    med = group_median(gcode, r, G)
    mad = 1.4826 * group_median(gcode, np.abs(r - med[gcode]), G)
    good = np.abs(r - med[gcode]) <= CHANGES_CLIP * np.maximum(e, mad[gcode])
    w = np.where(good, 1.0 / e ** 2, 0.0)
    sw = np.bincount(gcode, w, G)
    n = np.bincount(gcode, good.astype(np.float64), G)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = np.bincount(gcode, w * r, G) / sw
        chi2 = np.bincount(gcode, w * (r - mean[gcode]) ** 2, G)
        chi2_nu = np.where(n > 1, chi2 / np.maximum(n - 1, 1), np.nan)
        ierr = 1.0 / np.sqrt(sw)
    errv = ierr * np.sqrt(np.maximum(1.0, np.nan_to_num(chi2_nu, nan=1.0)))
    f = _first_of_group(gcode, G)
    kk = np.flatnonzero(keep)[f]
    ns_out = (count_sessions(gcode, ses[keep], G) if halves else nses[grp[kk]])
    out = pd.DataFrame({
        "star": star[kk], "source_id": sid[kk], "ref_id": ref[kk], "detector": det[kk].astype(np.int8),
        "origin": np.where(forced[kk], "forced", "dot"), "visit": vis[kk].astype(np.int16),
        "half": half[keep][f].astype(np.int8), "n": n.astype(np.int32), "n_sessions": np.asarray(ns_out, np.int32),
        "mjd_mid": 0.5 * (pd.Series(mjd[keep]).groupby(gcode).min().to_numpy() + pd.Series(mjd[keep]).groupby(gcode).max().to_numpy()),
        "mean": mean.astype(np.float32), "err": errv.astype(np.float32), "chi2_nu": chi2_nu.astype(np.float32),
        "median": med.astype(np.float32),
    })
    return out[out["n"] >= CHANGES_MIN_POINTS].reset_index(drop=True)


def count_sessions(g: np.ndarray, ses: np.ndarray, G: int) -> np.ndarray:
    k = np.unique((g.astype(np.int64) << 20) | ses.astype(np.int64))
    return np.bincount(k >> 20, minlength=G)


def visit_table_from_summary(summ: pd.DataFrame) -> pd.DataFrame:
    """The same table, approximately, from lightcurves_summary.parquet alone (per-visit medians, no per-visit n or
    error). A star's own scatter_rel holds its change (a 40% step makes it ~0.2), so the error comes from the
    population instead: per (detector, origin) the median scatter_rel of the light curves at its brightness
    (CHANGES_SUMMARY_FLUX_BINS quantile bins of flux_median_ujy), err = 1.2533 × that / √(n / visits). Used where the
    points table is too large to read (hunt: 1.5 GB); the floor fit absorbs the rest. No halves (no null test)."""
    s = summ[summ["visits"].map(len) >= 1]
    nv = s["visits"].map(len).to_numpy()
    idx = np.repeat(np.arange(len(s)), nv)
    vis = np.concatenate(s["visits"].to_numpy()).astype(np.int16) if len(s) else np.zeros(0, np.int16)
    vm = np.concatenate(s["visit_median_rel"].to_numpy()).astype(np.float64) if len(s) else np.zeros(0)
    n_each = s["n"].to_numpy(np.float64)[idx] / nv[idx]
    expected = np.full(len(s), np.nan)
    lf = np.log10(np.clip(s["flux_median_ujy"].to_numpy(np.float64), 1e-3, None))
    sc = s["scatter_rel"].to_numpy(np.float64)
    keyd = s["detector"].to_numpy(np.int64) * 2 + (s["origin"].astype(str).to_numpy() == "forced")
    for kd in np.unique(keyd):
        m = (keyd == kd) & np.isfinite(lf) & np.isfinite(sc)
        if m.sum() < 10:
            expected[keyd == kd] = np.nanmedian(sc[keyd == kd]) if (keyd == kd).any() else np.nan
            continue
        edges = np.unique(np.quantile(lf[m], np.linspace(0, 1, CHANGES_SUMMARY_FLUX_BINS + 1)))
        b = np.clip(np.searchsorted(edges, lf, side="right") - 1, 0, max(edges.size - 2, 0))
        med = group_median(b[m], sc[m], max(edges.size - 1, 1))
        sel = keyd == kd
        expected[sel] = med[b[sel]]
    err = 1.2533 * expected[idx] / np.sqrt(np.maximum(n_each, 1.0))
    sid = s["source_id"].to_numpy(np.int64)[idx]
    ref = s["ref_id"].to_numpy(dtype=object)[idx]
    out = pd.DataFrame({
        "star": star_keys(sid, ref), "source_id": sid, "ref_id": ref, "detector": s["detector"].to_numpy(np.int8)[idx],
        "origin": s["origin"].astype(str).to_numpy()[idx], "visit": vis, "half": np.int8(0),
        "n": np.round(n_each).astype(np.int32), "n_sessions": np.int32(CHANGES_MIN_SESSIONS), "mjd_mid": np.nan,
        "mean": vm.astype(np.float32), "err": err.astype(np.float32), "chi2_nu": np.float32(np.nan),
        "median": vm.astype(np.float32),
    })
    ok = np.isfinite(out["mean"].to_numpy()) & np.isfinite(out["err"].to_numpy()) & (out["err"].to_numpy() > 0)
    return out[ok & (out["n"].to_numpy() >= CHANGES_MIN_POINTS)].reset_index(drop=True)


def zero_points(vt: pd.DataFrame, ref_stars: np.ndarray) -> tuple[pd.DataFrame, list[dict]]:
    """Divide each (detector, origin, visit, half)'s means by the median mean of the reference stars' light curves
    there (a whole visit a little brighter or fainter in one detector is calibration, not the stars)."""
    vt = vt.copy()
    isref = np.isin(vt["star"].to_numpy(np.int64), ref_stars)
    key = pd.MultiIndex.from_arrays([vt["detector"].to_numpy(), vt["origin"].to_numpy(), vt["visit"].to_numpy(),
                                     vt["half"].to_numpy()])
    code, uniq = pd.factorize(key)
    zp = group_median(code[isref], vt["mean"].to_numpy(np.float64)[isref], len(uniq))
    nref = np.bincount(code[isref], minlength=len(uniq))
    zp = np.where(np.isfinite(zp) & (nref >= 20) & (zp > 0), zp, 1.0)
    vt["mean"] = (vt["mean"].to_numpy(np.float64) / zp[code]).astype(np.float32)
    vt["err"] = (vt["err"].to_numpy(np.float64) / zp[code]).astype(np.float32)
    vt["median"] = (vt["median"].to_numpy(np.float64) / zp[code]).astype(np.float32)
    rows = [{"detector": int(d), "origin": str(o), "visit": int(v), "half": int(h), "zero_point": round(float(zp[i]), 4),
             "ref_light_curves": int(nref[i])} for i, (d, o, v, h) in enumerate(uniq)]
    return vt, sorted(rows, key=lambda r: (r["origin"], r["detector"], r["visit"], r["half"]))


# --- brightness change -------------------------------------------------------------------------------------------
def pairs(vt: pd.DataFrame, before: dict, after: dict) -> pd.DataFrame:
    """One row per light curve measured in both (before, after: {"visit": v[, "half": h]} or {"visits": [..]}).
    `before` with "visits" combines those visits (weighted) as the history; d = after − before (in units of the star's
    usual brightness), sd = statistical error."""
    def pick(sel):
        m = np.ones(len(vt), bool)
        if "visits" in sel:
            m &= np.isin(vt["visit"].to_numpy(), sel["visits"])
        else:
            m &= vt["visit"].to_numpy() == sel["visit"]
        m &= vt["half"].to_numpy() == sel.get("half", 0)
        x = vt[m]
        if "visits" in sel and len(x):
            w = 1.0 / x["err"].to_numpy(np.float64) ** 2
            k = pd.MultiIndex.from_arrays([x["star"].to_numpy(), x["detector"].to_numpy(), x["origin"].to_numpy()])
            c, u = pd.factorize(k)
            sw = np.bincount(c, w)
            mean = np.bincount(c, w * x["mean"].to_numpy(np.float64)) / sw
            f = _first_of_group(c, len(u))
            x = x.iloc[f].assign(mean=mean, err=1.0 / np.sqrt(sw), n=np.bincount(c, x["n"].to_numpy()),
                                 n_sessions=np.bincount(c, x["n_sessions"].to_numpy()))
        return x
    a, b = pick(before), pick(after)
    keys = ["star", "detector", "origin"]
    m = a[keys + ["source_id", "ref_id", "mean", "err", "n", "n_sessions", "mjd_mid"]].merge(
        b[keys + ["mean", "err", "n", "n_sessions", "mjd_mid"]], on=keys, suffixes=("_a", "_b"))
    m["d"] = (m["mean_b"] - m["mean_a"]).astype(np.float64)
    m["sd"] = np.sqrt(m["err_a"].astype(np.float64) ** 2 + m["err_b"].astype(np.float64) ** 2)
    return m


CHANGES_FLOOR_BINS = 8                  # floor fit: bins of the statistical error (quantiles)
CHANGES_K_GRID = (0.8, 4.0, 0.02)       # ... grid of k
CHANGES_FLOOR_GRID = (0.0, 0.15, 0.001)  # ... grid of the floor (fraction of the star's brightness)


def _robust_sd(x: np.ndarray) -> float:
    return 1.4826 * float(np.median(np.abs(x - np.median(x)))) if x.size else float("nan")


def fit_k_floor(dd: np.ndarray, sd: np.ndarray) -> tuple[float, float]:
    """k and floor such that dd / √((k·sd)² + floor²) has a robust spread of 1: the robust variance of dd in bins of
    sd against k²·sd² + floor² (grid, least squares in log; one bin: k alone), then both scaled together so that the
    robust spread of all of them is 1 (the few real changes barely move a median)."""
    fin = np.isfinite(dd) & np.isfinite(sd) & (sd > 0)
    dd, sd = dd[fin], sd[fin]
    k, fl = 1.0, 0.0
    if dd.size < CHANGES_FLOOR_MIN:
        return k, fl
    kg = np.arange(CHANGES_K_GRID[0], CHANGES_K_GRID[1] + 1e-9, CHANGES_K_GRID[2])
    fg = np.arange(CHANGES_FLOOR_GRID[0], CHANGES_FLOOR_GRID[1] + 1e-9, CHANGES_FLOOR_GRID[2])
    edges = np.unique(np.quantile(sd, np.linspace(0, 1, CHANGES_FLOOR_BINS + 1)))
    b = np.clip(np.searchsorted(edges, sd, side="right") - 1, 0, max(edges.size - 2, 0))
    sb, vb = [], []
    for i in range(max(edges.size - 1, 1)):
        mm = b == i
        if mm.sum() >= 30:
            v = _robust_sd(dd[mm]) ** 2
            if v > 0:
                sb.append(np.sqrt(np.median(sd[mm] ** 2)))
                vb.append(v)
    sb, vb = np.asarray(sb), np.asarray(vb)
    if sb.size >= 2 and sb.max() > 1.2 * sb.min():
        pred = (kg[:, None, None] * sb[None, None, :]) ** 2 + fg[None, :, None] ** 2
        cost = ((np.log(pred + 1e-30) - np.log(vb)) ** 2).sum(-1)
        ik, jf = np.unravel_index(np.argmin(cost), cost.shape)
        k, fl = float(kg[ik]), float(fg[jf])
    s = _robust_sd(dd / np.sqrt((k * sd) ** 2 + fl ** 2))
    if np.isfinite(s) and s > 0:
        k, fl = k * s, fl * s
    return k, fl


def fit_floor(pr: pd.DataFrame, ref_stars: np.ndarray) -> dict:
    """Per (detector, origin): fit_k_floor on the reference stars' light curves (as lightcurves' error model, one
    level up: visit means instead of points)."""
    out = {}
    isref = np.isin(pr["star"].to_numpy(np.int64), ref_stars)
    for (d, o), x in pr[isref].groupby(["detector", "origin"]):
        dd, sd = x["d"].to_numpy(np.float64), x["sd"].to_numpy(np.float64)
        k, fl = fit_k_floor(dd, sd)
        fin = np.isfinite(dd) & np.isfinite(sd) & (sd > 0)
        z_all = dd[fin] / np.sqrt((k * sd[fin]) ** 2 + fl ** 2)
        out[f"{o}_D{int(d)}"] = {"k": round(k, 3), "floor": round(fl, 4), "ref_light_curves": int(fin.sum()),
                                 "robust_sd_after": round(_robust_sd(z_all), 3) if z_all.size else None,
                                 "beyond_5_sigma": round(float(np.mean(np.abs(z_all) >= 5.0)), 5) if z_all.size else None}
    return out


def apply_floor(pr: pd.DataFrame, model: dict) -> pd.DataFrame:
    pr = pr.copy()
    k = np.ones(len(pr))
    fl = np.zeros(len(pr))
    key = pr["origin"].astype(str) + "_D" + pr["detector"].astype(int).astype(str)
    for name, v in model.items():
        m = (key == name).to_numpy()
        k[m], fl[m] = v["k"], v["floor"]
    pr["sigma"] = np.sqrt((k * pr["sd"].to_numpy(np.float64)) ** 2 + fl ** 2)
    pr["z"] = pr["d"].to_numpy(np.float64) / pr["sigma"].to_numpy()
    with np.errstate(invalid="ignore", divide="ignore"):
        pr["frac"] = pr["d"].to_numpy(np.float64) / np.maximum(pr["mean_a"].to_numpy(np.float64), 1e-3)
    return pr


def best_per_detector(pr: pd.DataFrame) -> pd.DataFrame:
    """One light curve per (star, detector): the origin with the smaller sigma (dots and forced photometry measure the
    same pixels); `other_z` keeps the other origin's z (NaN without one)."""
    pr = pr.sort_values(["star", "detector", "sigma"], kind="stable")
    first = ~pr.duplicated(["star", "detector"])
    best = pr[first].copy()
    other = pr[~first].drop_duplicates(["star", "detector"])[["star", "detector", "z"]].rename(columns={"z": "other_z"})
    return best.merge(other, on=["star", "detector"], how="left")


def star_brightness(best: pd.DataFrame) -> pd.DataFrame:
    """Per star: its strongest detector, how many others agree (same sign, |z| >= CHANGES_Z_CONFIRM), how many
    disagree, and the alert rule (module docstring; the one-session test is separate)."""
    b = best.assign(az=np.abs(best["z"].to_numpy()))
    b = b[np.isfinite(b["az"].to_numpy())]
    top = b.sort_values(["star", "az"], ascending=[True, False], kind="stable").drop_duplicates("star")
    sign = np.sign(top.set_index("star")["z"])
    s = sign.reindex(b["star"]).to_numpy()
    zz = b["z"].to_numpy()
    istop = b.set_index(["star", "detector"]).index.isin(top.set_index(["star", "detector"]).index)
    agree = (~istop) & (np.sign(zz) == s) & (np.abs(zz) >= CHANGES_Z_CONFIRM)
    disagree = (~istop) & (np.sign(zz) == -s) & (np.abs(zz) >= CHANGES_Z_CONFIRM)
    cnt = pd.DataFrame({"star": b["star"].to_numpy(), "agree": agree, "disagree": disagree, "n_det": 1}).groupby("star").sum()
    out = top.set_index("star").join(cnt).reset_index()
    out["alert"] = ((out["az"] >= CHANGES_Z) & (np.abs(out["frac"]) >= CHANGES_MIN_FRAC) & (out["agree"] >= 1)).to_numpy()
    out["alert_one_det"] = ((out["az"] >= CHANGES_Z) & (np.abs(out["frac"]) >= CHANGES_MIN_FRAC)).to_numpy()
    return out


# --- colour change -------------------------------------------------------------------------------------------------
def star_colour(best: pd.DataFrame, um: dict | None = None) -> pd.DataFrame:
    """Per star with >= CHANGES_COLOUR_MIN_DET detectors: weighted line ln(after/before) = a + b·(ln λ − mean);
    b, its error (× √χ²_ν when above 1), the D1→D6 change exp(b·ln(4.69/0.908)) − 1 and the alert rule."""
    um = um or DET_UM
    x = best[(best["mean_a"] > 0.05) & (best["mean_b"] > 0.05)]
    lnr = np.log(x["mean_b"].to_numpy(np.float64) / x["mean_a"].to_numpy(np.float64))
    # sigma of ln ratio from the floored sigma of d, shared between the two visits
    s = x["sigma"].to_numpy(np.float64) / np.sqrt(x["mean_a"].to_numpy(np.float64) * x["mean_b"].to_numpy(np.float64))
    lx = np.log(np.array([um[int(d)] for d in x["detector"].to_numpy()]))
    g, u = pd.factorize(x["star"].to_numpy())
    G = len(u)
    w = 1.0 / s ** 2
    sw = np.bincount(g, w, G)
    n = np.bincount(g, minlength=G)
    xm = np.bincount(g, w * lx, G) / sw
    ym = np.bincount(g, w * lnr, G) / sw
    dx = lx - xm[g]
    sxx = np.bincount(g, w * dx * dx, G)
    with np.errstate(invalid="ignore", divide="ignore"):
        b = np.bincount(g, w * dx * (lnr - ym[g]), G) / sxx
        res = lnr - ym[g] - b[g] * dx
        chi2 = np.bincount(g, w * res * res, G)
        chi2_nu = np.where(n > 2, chi2 / np.maximum(n - 2, 1), np.nan)
        be = np.sqrt(1.0 / sxx) * np.sqrt(np.maximum(1.0, np.nan_to_num(chi2_nu, nan=1.0)))
    span = math.log(um[6] / um[1])
    out = pd.DataFrame({"star": u, "n_det_colour": n, "level": ym, "slope": b, "slope_err": be, "colour_chi2_nu": chi2_nu,
                        "d1_d6_change": np.exp(b * span) - 1.0})
    out["colour_z"] = out["slope"] / out["slope_err"]
    out["colour_alert"] = ((out["n_det_colour"] >= CHANGES_COLOUR_MIN_DET) & (np.abs(out["colour_z"]) >= CHANGES_COLOUR_Z)
                           & (np.abs(out["d1_d6_change"]) >= CHANGES_COLOUR_MIN)).to_numpy()
    return out


# --- one-session test ----------------------------------------------------------------------------------------------
def survives_session_drop(points: pd.DataFrame, rows: pd.DataFrame, floors: dict, zps: list[dict]) -> np.ndarray:
    """For each alert row (source_id, ref_id, detector, origin, visit_a, visit_b, sigma): recompute the after-visit mean without
    its most deviant session (and the before-visit's likewise) and say whether |z| stays >= CHANGES_Z. visit_a may be
    a list (the history: every earlier visit, each with its own zero point). points: only these light curves' points
    (cheap)."""
    ok = np.zeros(len(rows), bool)
    if not len(rows):
        return ok
    zp = {(r["detector"], r["origin"], r["visit"]): r["zero_point"] for r in zps if r["half"] == 0}
    p = points
    sid = p["source_id"].to_numpy(np.int64)
    refs = p["ref_id"].astype(str).to_numpy()
    det = p["detector"].to_numpy()
    org = p["origin"].astype(str).to_numpy()
    for i, r in enumerate(rows.itertuples(index=False)):
        who = (sid == r.source_id) if r.source_id >= 0 else ((sid < 0) & (refs == str(r.ref_id)))
        sel = who & (det == r.detector) & (org == r.origin)
        if "photo_ok" in p:
            sel &= p["photo_ok"].to_numpy(bool)
        x = p[sel]
        means = []
        for v in (r.visit_a, r.visit_b):
            vs = [int(u) for u in np.atleast_1d(v)]
            y = x[np.isin(x["visit"].to_numpy(), vs)].sort_values("mjd")
            if not len(y):
                means.append(np.nan)
                continue
            z0 = np.array([zp.get((r.detector, r.origin, int(u)), 1.0) for u in y["visit"].to_numpy()])
            rel = y["rel_flux"].to_numpy(np.float64) / z0
            e = y["flux_err_cal_ujy"].to_numpy(np.float64) / np.abs(y["model_ujy"].to_numpy(np.float64))
            ses = np.cumsum(np.r_[True, np.diff(y["mjd"].to_numpy()) > CHANGES_SESSION_GAP_D])
            us = np.unique(ses)
            if us.size < 2:
                means.append(np.nan)
                continue
            w = 1.0 / e ** 2
            sm = np.array([np.sum(w[ses == s] * rel[ses == s]) / np.sum(w[ses == s]) for s in us])
            full = np.sum(w * rel) / np.sum(w)
            worst = us[np.argmax(np.abs(sm - full))]
            k = ses != worst
            means.append(np.sum(w[k] * rel[k]) / np.sum(w[k]))
        ok[i] = np.isfinite(means[0]) and np.isfinite(means[1]) and abs(means[1] - means[0]) / r.sigma >= CHANGES_Z
    return ok


# --- position against Gaia -----------------------------------------------------------------------------------------
def neighbour_pull(ra0, dec0, g0, ra_n, dec_n, g_n) -> tuple[float, float]:
    """Light-centre shift (″ east, north) that neighbours of G magnitudes g_n at (ra_n, dec_n) give a star of G g0."""
    if not len(ra_n):
        return 0.0, 0.0
    f0 = 10 ** (-0.4 * g0)
    fn = 10 ** (-0.4 * np.asarray(g_n, np.float64))
    de = (np.asarray(ra_n) - ra0) * math.cos(math.radians(dec0)) * 3600.0
    dn = (np.asarray(dec_n) - dec0) * 3600.0
    tot = f0 + fn.sum()
    return float(np.sum(fn * de) / tot), float(np.sum(fn * dn) / tot)


def position_offsets(ast: pd.DataFrame, xid: pd.DataFrame, refcat: pd.DataFrame | None = None) -> tuple[pd.DataFrame, dict]:
    """Per Gaia-matched source in astrometry: its offset (″ east, north) from the Gaia star moved to the fit's epoch
    with Gaia's proper motion, the error, the floor fitted on the zone (robust spread of the offsets per S/N bin
    against the stated errors), the 2D significance and what the Gaia neighbours predict (refcat Gaia rows: ra, dec,
    g_mag); with astrometry's pmra/pmdec, also our motion against Gaia's (dpm, dpm_sigma, dpm_z: the same k fit).
    pos_off / motion_off: beyond CHANGES_POS_Z (diagnostics, not alerts: see the module docstring)."""
    m = ast.merge(xid[["source_id", "gaia_ref_id", "gaia_id", "gaia_ra", "gaia_dec", "gaia_epoch_mjd", "gaia_g",
                       "gaia_pmra", "gaia_pmdec", "gaia_parallax", "gaia_n"]], on="source_id")
    m = m[m["gaia_ref_id"].fillna("").astype(str) != ""].copy()
    dt_yr = (m["epoch_mjd"].to_numpy(np.float64) - m["gaia_epoch_mjd"].to_numpy(np.float64)) / 365.25
    has_pm = np.isfinite(m["gaia_pmra"].to_numpy(np.float64)) & np.isfinite(m["gaia_pmdec"].to_numpy(np.float64))
    pmra = np.nan_to_num(m["gaia_pmra"].to_numpy(np.float64))
    pmde = np.nan_to_num(m["gaia_pmdec"].to_numpy(np.float64))
    cosd = np.cos(np.radians(m["gaia_dec"].to_numpy(np.float64)))
    ra_g = m["gaia_ra"].to_numpy(np.float64) + pmra * dt_yr / 3.6e6 / cosd
    de_g = m["gaia_dec"].to_numpy(np.float64) + pmde * dt_yr / 3.6e6
    m["d_east"] = (m["ra"].to_numpy(np.float64) - ra_g) * cosd * 3600.0
    m["d_north"] = (m["dec"].to_numpy(np.float64) - de_g) * 3600.0
    m["gaia_has_pm"] = has_pm
    m["dt_yr"] = dt_yr
    e = np.sqrt(0.5 * (m["ra_err_arcsec"].to_numpy(np.float64) ** 2 + m["dec_err_arcsec"].to_numpy(np.float64) ** 2))
    m["pos_err"] = e
    # floor: robust spread of the offsets of Gaia stars with a motion, quiet (parallax small), alone (gaia_n == 1)
    quiet = has_pm & (np.nan_to_num(m["gaia_parallax"].to_numpy(np.float64), nan=0.0) <= CHANGES_POS_MAX_PLX_MAS) & (
        m["gaia_n"].fillna(1).to_numpy() <= 1)
    off = np.r_[m["d_east"].to_numpy()[quiet], m["d_north"].to_numpy()[quiet]]
    ee = np.r_[e[quiet], e[quiet]]
    rep = {"quiet_stars": int(quiet.sum())}
    k, floor = fit_k_floor(off, ee)
    if quiet.sum():
        rep.update({"median_offset_east": round(float(np.median(m["d_east"].to_numpy()[quiet])), 4),
                    "median_offset_north": round(float(np.median(m["d_north"].to_numpy()[quiet])), 4)})
    rep.update({"k": round(k, 3), "floor_arcsec": round(floor, 4)})
    sig = np.sqrt((k * e) ** 2 + floor ** 2)
    m["pos_sigma"] = sig
    m["pos_sep"] = np.hypot(m["d_east"].to_numpy(), m["d_north"].to_numpy())
    chi2 = (m["d_east"].to_numpy() ** 2 + m["d_north"].to_numpy() ** 2) / sig ** 2
    m["pos_chi2"] = chi2
    # χ²(2) survival = exp(−χ²/2); express as the one-sided Gaussian z of the same tail
    from scipy.stats import norm
    m["pos_z"] = norm.isf(np.clip(np.exp(-0.5 * chi2), 1e-300, 1.0))
    m["pull_east"], m["pull_north"] = 0.0, 0.0
    if refcat is not None and len(refcat):
        _neighbour_pulls(m, refcat)
    with np.errstate(invalid="ignore", divide="ignore"):
        proj = (m["pull_east"] * m["d_east"] + m["pull_north"] * m["d_north"]) / np.maximum(m["pos_sep"], 1e-9) ** 2
    m["explained"] = proj.to_numpy() >= CHANGES_POS_EXPLAINED
    plx_ok = np.nan_to_num(m["gaia_parallax"].to_numpy(np.float64), nan=0.0) <= CHANGES_POS_MAX_PLX_MAS
    m["pos_off"] = (plx_ok & (m["pos_z"].to_numpy() >= CHANGES_POS_Z)
                    & (m["pos_sep"].to_numpy() >= CHANGES_POS_MIN_ARCSEC))
    m["dpm"], m["dpm_sigma"], m["dpm_z"] = np.nan, np.nan, np.nan
    m["motion_off"] = False
    if {"pmra", "pmdec", "pmra_err", "pmdec_err"} <= set(m.columns):
        dra = m["pmra"].to_numpy(np.float64) - pmra
        dde = m["pmdec"].to_numpy(np.float64) - pmde
        pe = np.sqrt(0.5 * (m["pmra_err"].to_numpy(np.float64) ** 2 + m["pmdec_err"].to_numpy(np.float64) ** 2))
        km, fm = fit_k_floor(np.r_[dra[quiet], dde[quiet]], np.r_[pe[quiet], pe[quiet]])
        ps = np.sqrt((km * pe) ** 2 + fm ** 2)
        m["dpm"], m["dpm_sigma"] = np.hypot(dra, dde), ps
        m["dpm_z"] = norm.isf(np.clip(np.exp(-0.5 * (dra ** 2 + dde ** 2) / ps ** 2), 1e-300, 1.0))
        m["motion_off"] = plx_ok & has_pm & (m["dpm_z"].to_numpy() >= CHANGES_POS_Z)
        rep.update({"motion_k": round(km, 3), "motion_floor_mas_yr": round(fm, 3)})
    return m, rep


def _neighbour_pulls(m: pd.DataFrame, refcat: pd.DataFrame) -> None:
    """Fill pull_east / pull_north (″) for the rows that could alert (the others need none)."""
    from scipy.spatial import cKDTree

    cand = np.flatnonzero((m["pos_sep"].to_numpy() >= CHANGES_POS_MIN_ARCSEC) & (m["pos_z"].to_numpy() >= 3.0))
    if not cand.size:
        return
    g = refcat[refcat["cat"].astype(str) == "gaia"] if "cat" in refcat else refcat
    ra, de, gm = g["ra"].to_numpy(np.float64), g["dec"].to_numpy(np.float64), g["g_mag"].to_numpy(np.float64)
    ok = np.isfinite(gm)
    ra, de, gm = ra[ok], de[ok], gm[ok]
    d0 = np.radians(np.nanmedian(de))
    tree = cKDTree(np.c_[ra * math.cos(d0), de])
    pe, pn = np.zeros(len(m)), np.zeros(len(m))
    for i in cand:
        r0 = m["gaia_ra"].iat[i]
        dd0 = m["gaia_dec"].iat[i]
        g0 = m["gaia_g"].iat[i]
        idx = tree.query_ball_point([r0 * math.cos(d0), dd0], CHANGES_POS_NEIGHBOUR_ARCSEC / 3600.0 * 1.2)
        idx = [j for j in idx if (abs(ra[j] - r0) * 3600 * math.cos(math.radians(dd0))) ** 2 + ((de[j] - dd0) * 3600) ** 2
               <= CHANGES_POS_NEIGHBOUR_ARCSEC ** 2 and not (abs(ra[j] - r0) < 1e-6 and abs(de[j] - dd0) < 1e-6)]
        if idx and np.isfinite(g0):
            pe[i], pn[i] = neighbour_pull(r0, dd0, g0, ra[idx], de[idx], gm[idx])
    m["pull_east"], m["pull_north"] = pe, pn


def _unit(ra, dec) -> np.ndarray:
    r, d = np.radians(np.asarray(ra, np.float64)), np.radians(np.asarray(dec, np.float64))
    return np.c_[np.cos(d) * np.cos(r), np.cos(d) * np.sin(r), np.sin(d)]


def bright_neighbours(ra: np.ndarray, dec: np.ndarray, g: np.ndarray, refcat: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Per star: separation (″) and G − G_star of the brightest-relative Gaia star within CHANGES_NEIGHBOUR_ARCSEC
    (NaN: none). A star whose neighbour is >= |CHANGES_NEIGHBOUR_DMAG| mag brighter is measured in that star's wings,
    which change with the telescope's roll between visits (pilot: watched Gaia stars without a SPHEREx source of their
    own, G 15–17.5, sit 8–14″ from a G 9–12 star and alerted 19 per 1,000 between visits against 0.2 in the halves)."""
    from scipy.spatial import cKDTree

    n = len(ra)
    sep_out, dm_out = np.full(n, np.nan), np.full(n, np.nan)
    gg = refcat[refcat["cat"].astype(str) == "gaia"] if "cat" in refcat else refcat
    gm = gg["g_mag"].to_numpy(np.float64)
    ok = np.isfinite(gm)
    if not n or not ok.any():
        return sep_out, dm_out
    tree = cKDTree(_unit(gg["ra"].to_numpy(np.float64)[ok], gg["dec"].to_numpy(np.float64)[ok]))
    gm = gm[ok]
    pts = _unit(ra, dec)
    rad = 2 * math.sin(math.radians(CHANGES_NEIGHBOUR_ARCSEC / 3600.0) / 2)
    gstar = np.where(np.isfinite(np.asarray(g, np.float64)), np.asarray(g, np.float64), CHANGES_NEIGHBOUR_DEFAULT_G)
    for i, idx in enumerate(tree.query_ball_point(pts, rad)):
        if not idx:
            continue
        idx = np.asarray(idx)
        sep = np.degrees(2 * np.arcsin(np.linalg.norm(tree.data[idx] - pts[i], axis=1) / 2)) * 3600.0
        keep = sep > 0.5                                           # not the star itself
        if not keep.any():
            continue
        dm = gm[idx][keep] - gstar[i]
        j = int(np.argmin(dm))
        sep_out[i], dm_out[i] = sep[keep][j], dm[j]
    return sep_out, dm_out


# --- appeared / vanished -------------------------------------------------------------------------------------------
def new_sources(orph: pd.DataFrame, last_visit: int, snr: pd.Series | None = None,
                detectors: pd.Series | None = None) -> pd.DataFrame:
    """orphans.parquet's clean rows, static, no known body, S/N >= CHANGES_NEW_MIN_SNR (snr: per source_id, e.g.
    orphans' snr_max; without it every row), >= CHANGES_NEW_MIN_DOTS dots in >= CHANGES_NEW_MIN_DETECTORS detectors
    (detectors: per source_id, sources' bit mask; without it not tested) → kind appeared (latest visit) or vanished.
    Hunt (15 Nov tables): of 46 clean static orphans at S/N >= 20 seen in >= 3 sessions, 39 had 3 dots, mostly D4 + D6
    alone, spread over 4–25 days: three MWIR glitches that happen to fall within link's 4″, not a source."""
    o = orph[orph["reason"].fillna("").astype(str) == ""].copy()
    if "known_name" in o:
        o = o[o["known_name"].fillna("").astype(str) == ""]
    rate = o["rate"].to_numpy(np.float64) if "rate" in o else np.full(len(o), np.nan)
    rerr = o["rate_err"].to_numpy(np.float64) if "rate_err" in o else np.full(len(o), np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        o["static"] = ~(rate / rerr >= CHANGES_NEW_STATIC_Z)       # no fit (NaN) counts as static
    o = o[o["static"].to_numpy()]
    if snr is not None:
        o["snr"] = snr.reindex(o["source_id"].to_numpy()).to_numpy()
        o = o[o["snr"].to_numpy() >= CHANGES_NEW_MIN_SNR]
    if "n_dots" in o:
        o = o[o["n_dots"].to_numpy() >= CHANGES_NEW_MIN_DOTS]
    if detectors is not None:
        bits = detectors.reindex(o["source_id"].to_numpy()).fillna(0).to_numpy(np.int64)
        o["n_detectors"] = np.array([int(b).bit_count() for b in bits], np.int8)
        o = o[o["n_detectors"].to_numpy() >= CHANGES_NEW_MIN_DETECTORS]
    o["kind"] = np.where(o["visit"].to_numpy() == last_visit, "appeared", "vanished")
    return o.reset_index(drop=True)


# --- texts ----------------------------------------------------------------------------------------------------------
def mjd_date(mjd: float) -> str:
    if not np.isfinite(mjd):
        return "?"
    from astropy.time import Time
    return Time(mjd, format="mjd").to_value("iso", subfmt="date")


def name_of(row) -> str:
    ref = str(getattr(row, "ref_id", "") or "")
    gid = getattr(row, "gaia_id", None)
    if gid is not None and np.isfinite(gid) and int(gid) > 0:
        return f"Gaia DR3 {int(gid)}"
    if ref.startswith("gaia:"):
        return f"Gaia DR3 {ref.split(':', 1)[1]}"
    if ref.startswith("catwise:"):
        return f"CatWISE {ref.split(':', 1)[1]}"
    if ref.startswith("2mass:"):
        return f"2MASS {ref.split(':', 1)[1]}"
    return f"source {int(row.source_id)}"


def _label(r) -> str:
    """The star's name from a row: a Series' `name` is its index label, so read the field, else name_of."""
    v = r.get("name") if isinstance(r, (pd.Series, dict)) else getattr(r, "name", None)
    return str(v) if v not in (None, "") and not (isinstance(v, float) and math.isnan(v)) else name_of(r)


def brightness_text(r, others: list[tuple[int, float, float]]) -> tuple[str, str, dict]:
    """r: a star_brightness row (with visit_a, visit_b, mjd_mid_a, mjd_mid_b, name); others: (detector, frac, z).
    visit_a may be a list (the history: every earlier visit)."""
    pct = 100.0 * r.frac
    word = "brighter" if pct > 0 else "fainter"
    faded_out = r.mean_b < 0.2 and pct < 0
    va = [int(v) for v in np.atleast_1d(r.visit_a)]
    vb = int(r.visit_b)
    if len(va) == 1:
        before = f"visit {va[0]} ({mjd_date(r.mjd_mid_a)})"
        blink = f"blink visit {va[0]} against visit {vb}"
    else:
        before = f"its earlier visits {va[0]}–{va[-1]} together"
        blink = f"blink visit {va[-1]} (and earlier ones) against visit {vb}"
    agree = ", ".join(f"D{d} {100 * f:+.0f}% ({z:+.1f}σ)" for d, f, z in others) or "no other detector measured it"
    head = (f"{_label(r)} went from {r.mean_a:.2f} to {r.mean_b:.2f} of its usual brightness in D{int(r.detector)} "
            f"({DET_RANGE[int(r.detector)]}) between {before} and visit {vb} ({mjd_date(r.mjd_mid_b)}): "
            f"{abs(pct):.0f}% {word}, {abs(r.z):.1f}σ beyond its own scatter")
    if faded_out:
        head += " (it faded below detection)"
    text = f"{head}; other detectors: {agree}."
    look = (f"Viewer, months clock, D{int(r.detector)}: {blink}; it should look {word} in visit {vb} while the stars "
            f"around it stay the same.")
    values = {"detector": int(r.detector), "visit_a": va[0] if len(va) == 1 else va, "visit_b": vb,
              "rel_a": round(float(r.mean_a), 4), "rel_b": round(float(r.mean_b), 4), "change_frac": round(float(r.frac), 4),
              "sigma": round(float(r.z), 2), "agreeing_detectors": int(r.agree)}
    return text, look, values


def population(source_id: np.ndarray, ref_id: np.ndarray, ra: np.ndarray, dec: np.ndarray, box) -> np.ndarray:
    """Per star: `source` (its own SPHEREx source), `forced_outside` (a catalogue star measured only by forced
    photometry, outside the zone box: link never ran there) or `forced_inside` (inside the box with no source of its
    own: link folded its dots into a neighbour's group; not listed). box: (ra_min, ra_max, dec_min, dec_max)."""
    sid = np.asarray(source_id, np.int64)
    ra, dec = np.asarray(ra, np.float64), np.asarray(dec, np.float64)
    r0, r1, d0, d1 = box
    inside = (ra >= r0) & (ra <= r1) & (dec >= d0) & (dec <= d1)
    return np.where(sid >= 0, "source", np.where(inside, "forced_inside", "forced_outside"))


def brightness_checks(r) -> list[dict]:
    """v3.23-style checks for one listed brightness alert row (fields used when present: agree, disagree, other_z,
    origin, survives_session_drop, gaia_variable, gaia_class, nb_sep, nb_dmag, sign_blend, sign_saturated).
    Sorted strong, warn, ok."""
    out = []

    def add(code, level, text, look="", values=None):
        out.append({"code": code, "level": level, "text": text, "look": look, "values": values})

    det = int(r.detector)
    sd = getattr(r, "survives_session_drop", np.nan)
    if sd is not None and np.isfinite(sd) and not sd:
        add("one_session", "strong", f"Without its most deviant session in each visit the D{det} change falls below "
            f"{CHANGES_Z:.0f}σ: one session (one look) carries it.", f"Viewer, hours clock, D{det}: step through the "
            "visit's sessions; a real change shows in all of them.", None)
    dis = int(getattr(r, "disagree", 0) or 0)
    agr = int(getattr(r, "agree", 0) or 0)
    if dis:
        add("detectors_disagree", "warn", f"{dis} other detector(s) changed the opposite way at >= "
            f"{CHANGES_Z_CONFIRM:.0f}σ.", "Viewer, months clock: blink the same two visits in each detector.",
            {"disagreeing_detectors": dis})
    oz = getattr(r, "other_z", np.nan)
    if oz is not None and np.isfinite(oz) and np.sign(oz) != np.sign(r.z) and abs(oz) >= CHANGES_Z_CONFIRM:
        other = "forced photometry" if r.origin == "dot" else "its detections"
        add("origin_disagrees", "warn", f"In D{det} {other} says the opposite ({oz:+.1f}σ): the measurement, not "
            "the star, may have changed.", "", {"other_sigma": round(float(oz), 2)})
    nb, dm = getattr(r, "nb_sep", np.nan), getattr(r, "nb_dmag", np.nan)
    if nb is not None and np.isfinite(nb) and np.isfinite(dm) and dm < 0:
        add("bright_neighbour", "warn", f"A Gaia star {-dm:.1f} mag brighter sits {nb:.0f}″ away; its wings fall on "
            "this star differently when the telescope turns between visits.",
            f"Viewer, months clock, D{det}: does the neighbour's halo change shape between the visits?",
            {"sep_arcsec": round(float(nb), 1), "dmag": round(float(dm), 2)})
    if bool(getattr(r, "sign_blend", False)):
        add("blend", "warn", "Two or more Gaia stars G < 19 within 8″ share its light.", "", None)
    if bool(getattr(r, "sign_saturated", False)):
        add("saturated", "warn", "Its core saturates in some photos: the brightness comes from the wings.", "", None)
    if agr:
        add("other_detectors_agree", "ok", f"{agr} other detector(s) changed the same way at >= "
            f"{CHANGES_Z_CONFIRM:.0f}σ.", "", {"agreeing_detectors": agr})
    cls = str(getattr(r, "gaia_class", "") or "")
    if bool(getattr(r, "gaia_variable", False)) or cls:
        add("known_variable", "ok", "Gaia DR3 lists it as variable" + (f" (class {cls})" if cls else "") +
            ": a known variable doing what it does.", "", {"gaia_class": cls or None})
    order = {"strong": 0, "warn": 1, "ok": 2}
    return sorted(out, key=lambda c: order[c["level"]])


def colour_text(c, name: str, va: int, vb: int) -> tuple[str, str, dict]:
    redder = c.slope > 0
    text =(f"{name} changed colour between visit {va} and visit {vb}: its brightness ratio after/before rises by "
            f"{100 * c.d1_d6_change:+.0f}% from 0.9 µm (D1) to 4.7 µm (D6) ({abs(c.colour_z):.1f}σ, "
            f"{int(c.n_det_colour)} detectors): it became {'redder' if redder else 'bluer'}.")
    look = (f"Viewer, months clock: blink visit {va} against visit {vb} in D1, then in D6; the change should be "
            f"{'stronger in D1' if redder else 'stronger in D6'} than in the other.")
    values = {"visit_a": va, "visit_b": vb, "d1_d6_change": round(float(c.d1_d6_change), 4),
              "sigma": round(float(c.colour_z), 2), "detectors": int(c.n_det_colour)}
    return text, look, values


def compass(de: float, dn: float) -> str:
    ang = (math.degrees(math.atan2(de, dn)) + 360.0) % 360.0
    names = ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"]
    return names[int((ang + 22.5) // 45) % 8]


def position_text(r) -> tuple[str, str, dict]:
    pm_txt = "with Gaia's motion" if r.gaia_has_pm else "Gaia has no motion for it, so where it was in 2016"
    text = (f"{_label(r)} (G {r.gaia_g:.1f}) sits {r.pos_sep:.2f}″ ± {r.pos_sigma:.2f}″ {compass(r.d_east, r.d_north)} of where "
            f"Gaia DR3 puts it on {mjd_date(r.epoch_mjd)} ({pm_txt}; {r.pos_z:.1f}σ); "
            + ("Gaia neighbours within 12″ predict most of that pull (a blend)." if r.explained else
               f"Gaia neighbours within 12″ predict {math.hypot(r.pull_east, r.pull_north):.2f}″ of it.")
            + (f" Over {r.dt_yr:.1f} years that is {1000 * r.pos_sep / r.dt_yr:.0f} mas/yr of motion Gaia did not list."
               if not r.gaia_has_pm else ""))
    look = ("Viewer, months clock, D2, smallest field: compare the dot with the Gaia marker; the offset is a fraction "
            "of a 6.2″ pixel, so trust the numbers more than the picture.")
    values = {"sep_arcsec": round(float(r.pos_sep), 3), "sigma_arcsec": round(float(r.pos_sigma), 3),
              "east_arcsec": round(float(r.d_east), 3), "north_arcsec": round(float(r.d_north), 3),
              "sigma": round(float(r.pos_z), 2), "neighbour_pull_arcsec": round(math.hypot(r.pull_east, r.pull_north), 3)}
    return text, look, values
