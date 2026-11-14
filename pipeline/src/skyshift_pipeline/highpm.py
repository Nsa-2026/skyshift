"""Step `highpm`: a "Planet X"-style search for nearby, cold, fast-moving objects that Gaia missed.

A brown dwarf or cold planet a few parsecs away moves arcseconds a year and is too red and faint at
optical wavelengths for Gaia, but bright at 3–5 µm, where SPHEREx sees. Two routes:

1. "pair": a source fast enough (more than ~8″/yr) is no longer linked across visits by `link`'s 4″
   friends-of-friends, so it becomes two fixed sources each seen in ONE visit. We pair a single-visit
   source with a single-visit source of another visit HIGHPM_MIN_SEP_ARCSEC–HIGHPM_MAX_SEP_ARCSEC away
   when: both positions were covered by the other visit's photos (>= HIGHPM_MIN_COVER_PHOTOS, so the
   absence is real), the same-detector flux agrees within HIGHPM_FLUX_RATIO_MAX in every shared
   detector, and the spectrum shape agrees (rms of ln ratio across detectors <= HIGHPM_SHAPE_MAX).
   Each source keeps its best partner (smallest |ln ratio| + shape).
2. "fit": astrometry.parquet's SPHEREx-only proper motion >= HIGHPM_FIT_MIN_MAS_YR at
   >= HIGHPM_FIT_MIN_SNR (slower movers that stay linked as one source).
`status` = "new" when neither side matches Gaia (crossids), else "gaia". A CatWISE match whose proper
motion agrees with ours (parallax-aware, below) is a bonus (`catwise_pm_agree`). `red_ratio` =
D6 / D2 flux (4.4–5.0 µm over 1.1–1.6 µm): large for cold objects (methane, CO absorption bluewards).

Parallax: with two visits half a year apart a fit's pm is the true pm plus ϖ·(k_e, k_n), the parallax leak
(visit_stats: k = cov(t, parallax factor) / var(t) over the source's dots; hunt k_e ≈ −4.1, k_n ≈ +1.0). So a
Gaia counterpart is compared as its pm + its parallax × k (gaia_check), and a CatWISE counterpart (no parallax)
as its pm + ϖ·k for the best 0 ≤ ϖ ≤ HIGHPM_CW_PLX_PER_PM × its |pm| (catwise_check; _SAT for saturated
sources). A nearby Planet X moves AND shows parallax: both count as signal in pm_snr.

Control (highpm_report.json `control_pairs`, `pair_vs_control`): the same pairing, with the same coverage
cut, after moving every even visit's single-visit sources by each HIGHPM_CONTROL_OFFSETS: the mean is the
number of chance pairs to expect, per separation bin (HIGHPM_PAIR_SEP_BINS).

Vetting (columns `vetted`, `reason`; every candidate is kept): a candidate is vetted when `reason` is empty.
Reasons: short_span, few_dots, few_sessions (< HIGHPM_FIT_MIN_SESSIONS sessions in a visit), bad_fit, over_pm_cap
(fit: HIGHPM_FIT_*); gaia_pm_disagrees (a Gaia counterpart with its own pm, and pm + parallax leak is not ours: the
counterpart is a slower star at that spot, HIGHPM_PM_AGREE_SIGMA); catwise_pm_disagrees (no Gaia pm to check,
and the CatWISE counterpart's motion is not ours); unchecked (fit with neither a Gaia nor a CatWISE
motion to check it, and under HIGHPM_NOCAT_MIN_SNR or saturated: an uncatalogued star never saturates; hunt: the
only 3 still-star false positives left were G 7–8 saturated stars CatWISE does not list); at_control_level (pairs: its separation bin has no more pairs
than the control, Poisson P >= HIGHPM_PAIR_MAX_P). A motion Gaia confirms is a known moving star: the quality
reasons (short_span ... over_pm_cap) don't apply to it. Only vetted rows are leads (candidates.py exports only
those). The report counts "new" as status new and vetted; `new_unvetted` and `reasons` show what was cut.
Recall check (highpm_report.json): Gaia DR3 stars faster than HIGHPM_RECALL_MAS_YR in the zone, how
many we detected as a source, how many came out as candidates. False-positive control (`fp_control`): the
same selection and vetting on Gaia-5p stars that are still for us (HIGHPM_STILL_*) with their Gaia entry hidden;
the share that comes out vetted (per 100k) times the zone's fitted sources without a Gaia pm = the fakes to
expect among the vetted "new" ones.

Writes results/<zone>/highpm_candidates.parquet and highpm_report.json.
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd
from skyshift_core import tangent_offset_arcsec
from skyshift_core.astrometry import DAYS_PER_JYEAR

from . import config as C
from . import log
from .crossid import crossmatch
from .motion_io import load_refcat
from .sky import Footprint, SkyTree
from .spectra import NCH_DET, NDET
from .store import Store, photolist_rel, results_rel

L = log.get("highpm")

COLUMNS = ["method", "status", "source_id", "source_id_b", "ra", "dec", "ra_b", "dec_b", "mjd", "mjd_b", "visit", "visit_b",
           "sep_arcsec", "pmra", "pmdec", "pm", "pm_err", "flux_ratio", "shape_rms", "n_common_det", "red_ratio",
           "gaia_ref_id", "gaia_id", "catwise_name", "catwise_pm_agree", "score", "vetted", "reason"]
REASONS = ("short_span", "few_dots", "few_sessions", "bad_fit", "over_pm_cap", "gaia_pm_disagrees",
           "catwise_pm_disagrees", "unchecked", "at_control_level", "gaia_slow")
QUALITY = ("short_span", "few_dots", "few_sessions", "bad_fit", "over_pm_cap")  # not applied to a Gaia-confirmed motion


def source_visits(store: Store, zone: C.Zone, m: pd.DataFrame | None = None, p: pd.DataFrame | None = None) -> pd.DataFrame:
    """Per source: its visit (-1 if seen in several) and mean mjd, from members + the photo list
    (m, p: those tables already read)."""
    if m is None:
        m = store.read_parquet(results_rel(zone.name, "members.parquet"), columns=["source_id", "obs_id", "detector"])
    if p is None:
        p = store.read_parquet(photolist_rel(zone.name), columns=["obs_id", "detector", "mjd", "visit"])
    key = pd.MultiIndex.from_arrays([p["obs_id"].astype(str), p["detector"].astype(np.int64)])
    pos = key.get_indexer(pd.MultiIndex.from_arrays([m["obs_id"].astype(str), m["detector"].astype(np.int64)]))
    ok = pos >= 0
    df = pd.DataFrame({"source_id": m["source_id"].to_numpy()[ok], "mjd": p["mjd"].to_numpy()[pos[ok]],
                       "visit": p["visit"].to_numpy()[pos[ok]]})
    g = df.groupby("source_id").agg(mjd=("mjd", "mean"), vmin=("visit", "min"), vmax=("visit", "max"))
    g["visit"] = np.where(g["vmin"] == g["vmax"], g["vmin"], -1).astype(np.int64)
    return g[["mjd", "visit"]].reset_index()


def visit_stats(m: pd.DataFrame, p: pd.DataFrame, pos: pd.DataFrame, ids) -> pd.DataFrame:
    """Per source in `ids`: k_e, k_n (parallax leak of a proper-motion fit over its dots, mas/yr per mas: the fit's
    pm = true pm + ϖ·k) and sessions_min (fewest sessions in any visit it is seen in).
    m: members (source_id, obs_id, detector); p: photo list (obs_id, detector, mjd, visit[, session]);
    pos: source_id, ra, dec. Unweighted over the dots (the fit weights them by their errors: close enough)."""
    from .astrometry import observer_xyz_au, parallax_factors

    ids = np.unique(np.asarray(ids, np.int64))
    empty = pd.DataFrame({"source_id": ids, "k_e": 0.0, "k_n": 0.0, "sessions_min": np.int64(0)})
    if not len(ids) or not len(m) or not len(p):
        return empty
    m = m[np.isin(m["source_id"].to_numpy(np.int64), ids)]
    key = pd.MultiIndex.from_arrays([p["obs_id"].astype(str), p["detector"].astype(np.int64)])
    j = key.get_indexer(pd.MultiIndex.from_arrays([m["obs_id"].astype(str), m["detector"].astype(np.int64)]))
    pi = pd.Index(pos["source_id"].to_numpy(np.int64)).get_indexer(m["source_id"].to_numpy(np.int64))
    ok = (j >= 0) & (pi >= 0)
    if not ok.any():
        return empty
    sid, j, pi = m["source_id"].to_numpy(np.int64)[ok], j[ok], pi[ok]
    mjd = p["mjd"].to_numpy(np.float64)[j]
    vis = p["visit"].to_numpy(np.int64)[j]
    ses = (p["session"].to_numpy(np.int64)[j] if "session" in p
           else np.floor(mjd * 144.0).astype(np.int64))  # no session column: 10-minute bins
    umjd, inv = np.unique(np.round(mjd, 6), return_inverse=True)
    xyz = observer_xyz_au(umjd)[inv.ravel()]
    pe, pn = parallax_factors(pos["ra"].to_numpy(np.float64)[pi], pos["dec"].to_numpy(np.float64)[pi], xyz)
    u, g = np.unique(sid, return_inverse=True)
    n = np.bincount(g).astype(np.float64)
    t = mjd / DAYS_PER_JYEAR
    dt = t - (np.bincount(g, t) / n)[g]
    vt = np.bincount(g, dt * dt)
    with np.errstate(invalid="ignore", divide="ignore"):
        ke = np.where(vt > 0, np.bincount(g, dt * (pe - (np.bincount(g, pe) / n)[g])) / vt, 0.0)
        kn = np.where(vt > 0, np.bincount(g, dt * (pn - (np.bincount(g, pn) / n)[g])) / vt, 0.0)
    sv = pd.DataFrame({"g": g, "v": vis, "s": ses}).drop_duplicates()
    smin = sv.groupby(["g", "v"]).size().groupby(level="g").min().reindex(np.arange(len(u)), fill_value=0)
    out = pd.DataFrame({"source_id": u, "k_e": ke, "k_n": kn, "sessions_min": smin.to_numpy(np.int64)})
    return empty[["source_id"]].merge(out, on="source_id", how="left").fillna({"k_e": 0.0, "k_n": 0.0, "sessions_min": 0}).astype(
        {"sessions_min": np.int64})


def gaia_check(out: pd.DataFrame, gpmra, gpmdec, gplx, k_e, k_n,
               predicted: bool = False) -> tuple[np.ndarray, ...]:
    """(has a Gaia pm, disagrees): Gaia's pm + its parallax × the leak k (what our fit should have measured)
    against ours; disagrees beyond max(HIGHPM_PM_AGREE_SIGMA × pm_err, HIGHPM_CATWISE_PM_TOL × |predicted|, 100).
    predicted: also return |Gaia's pm + parallax × leak| (mas/yr), the motion Gaia says our fit should see."""
    plx = np.nan_to_num(np.asarray(gplx, np.float64), nan=0.0)
    pra = np.asarray(gpmra, np.float64) + plx * np.nan_to_num(k_e)
    pde = np.asarray(gpmdec, np.float64) + plx * np.nan_to_num(k_n)
    d = np.hypot(out["pmra"].to_numpy(np.float64) - pra, out["pmdec"].to_numpy(np.float64) - pde)
    tol = np.maximum.reduce([C.HIGHPM_PM_AGREE_SIGMA * np.nan_to_num(out["pm_err"].to_numpy(np.float64)),
                             C.HIGHPM_CATWISE_PM_TOL * np.nan_to_num(np.hypot(pra, pde)), np.full(len(out), 100.0)])
    has = np.isfinite(d)
    if predicted:
        return has, has & (d > tol), np.hypot(pra, pde)
    return has, has & (d > tol)


def catwise_check(out: pd.DataFrame, cpmra, cpmdec, cerr, k_e, k_n, plx_per_pm) -> tuple[np.ndarray, np.ndarray]:
    """(has a CatWISE pm, agrees). CatWISE fits no parallax, so ours may exceed its pm by a leak ϖ·k with
    0 <= ϖ <= plx_per_pm × |pm_cw| (the ϖ that fits best); agrees when the rest is within
    max(HIGHPM_PM_AGREE_SIGMA × (pm_err ⊕ CatWISE's error), HIGHPM_CW_REL_TOL × |pm_cw|, 100 mas/yr)."""
    ke, kn = np.nan_to_num(np.asarray(k_e, np.float64)), np.nan_to_num(np.asarray(k_n, np.float64))
    cra, cde = np.asarray(cpmra, np.float64), np.asarray(cpmdec, np.float64)
    dx, dy = out["pmra"].to_numpy(np.float64) - cra, out["pmdec"].to_numpy(np.float64) - cde
    pm_cw = np.hypot(cra, cde)
    kk = ke * ke + kn * kn
    with np.errstate(invalid="ignore", divide="ignore"):
        w = np.where(kk > 0, np.clip((dx * ke + dy * kn) / kk, 0.0, np.nan_to_num(plx_per_pm * pm_cw)), 0.0)
    res = np.hypot(dx - w * ke, dy - w * kn)
    sig = np.hypot(np.nan_to_num(out["pm_err"].to_numpy(np.float64)), np.nan_to_num(np.asarray(cerr, np.float64)))
    tol = np.maximum.reduce([C.HIGHPM_PM_AGREE_SIGMA * sig, C.HIGHPM_CW_REL_TOL * np.nan_to_num(pm_cw), np.full(len(out), 100.0)])
    has = np.isfinite(dx) & np.isfinite(dy)
    return has, has & (res <= tol)


def detector_fluxes(spec: np.ndarray) -> np.ndarray:
    """(N, 102) channel fluxes → (N, 6) median flux per detector (NaN where empty)."""
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmedian(spec.reshape(len(spec), NDET, NCH_DET), axis=2)


def covered(ra, dec, fps: list[Footprint]) -> np.ndarray:
    """Number of footprints containing each position."""
    n = np.zeros(len(ra), np.int64)
    for fp in fps:
        n += fp.contains(ra, dec)
    return n


def pair_movers(src: pd.DataFrame, dflux: np.ndarray, footprints_by_visit: dict[int, list[Footprint]] | None) -> pd.DataFrame:
    """Pairs of single-visit sources (see the module docstring). src: source_id, ra, dec, mjd, visit
    (−1 = several), pos_err_arcsec; dflux: (len(src), 6)."""
    single = np.flatnonzero(src["visit"].to_numpy() >= 0)
    if single.size < 2:
        return pd.DataFrame()
    ra, dec = src["ra"].to_numpy(np.float64)[single], src["dec"].to_numpy(np.float64)[single]
    vis = src["visit"].to_numpy(np.int64)[single]
    mjd = src["mjd"].to_numpy(np.float64)[single]
    tree = SkyTree(ra, dec)
    from .sky import chord
    pairs = tree.tree.query_pairs(chord(C.HIGHPM_MAX_SEP_ARCSEC), output_type="ndarray")
    if len(pairs) == 0:
        return pd.DataFrame()
    i, j = pairs[:, 0], pairs[:, 1]
    keep = vis[i] != vis[j]
    i, j = i[keep], j[keep]
    swap = mjd[i] > mjd[j]
    i, j = np.where(swap, j, i), np.where(swap, i, j)
    de, dn = tangent_offset_arcsec(ra[i], dec[i], ra[j], dec[j])
    sep = np.hypot(de, dn)
    keep = (sep >= C.HIGHPM_MIN_SEP_ARCSEC) & (sep <= C.HIGHPM_MAX_SEP_ARCSEC)
    i, j, de, dn, sep = i[keep], j[keep], de[keep], dn[keep], sep[keep]
    fa, fb = dflux[single[i]], dflux[single[j]]
    with np.errstate(invalid="ignore", divide="ignore"):
        lr = np.log(fb / fa)
    common = np.isfinite(lr)
    nc = common.sum(axis=1)
    lrz = np.where(common, lr, 0.0)
    mean_lr = lrz.sum(axis=1) / np.maximum(nc, 1)
    shape = np.sqrt((np.where(common, lr - mean_lr[:, None], 0.0) ** 2).sum(axis=1) / np.maximum(nc, 1))
    maxabs = np.where(common, np.abs(lr), 0.0).max(axis=1) if len(lr) else np.zeros(0)
    ok = (nc >= 1) & (maxabs <= np.log(C.HIGHPM_FLUX_RATIO_MAX)) & (shape <= C.HIGHPM_SHAPE_MAX)
    i, j, de, dn, sep, mean_lr, shape, nc = i[ok], j[ok], de[ok], dn[ok], sep[ok], mean_lr[ok], shape[ok], nc[ok]
    if footprints_by_visit is not None and i.size:
        cov_a = np.zeros(i.size, np.int64)
        cov_b = np.zeros(i.size, np.int64)
        for v in np.unique(np.r_[vis[i], vis[j]]):
            fps = footprints_by_visit.get(int(v), [])
            mb = vis[i] == v  # B's position must be covered in A's visit
            if mb.any():
                cov_b[mb] = covered(ra[j][mb], dec[j][mb], fps)
            ma = vis[j] == v  # A's position must be covered in B's visit
            if ma.any():
                cov_a[ma] = covered(ra[i][ma], dec[i][ma], fps)
        ok = (cov_a >= C.HIGHPM_MIN_COVER_PHOTOS) & (cov_b >= C.HIGHPM_MIN_COVER_PHOTOS)
        i, j, de, dn, sep, mean_lr, shape, nc = i[ok], j[ok], de[ok], dn[ok], sep[ok], mean_lr[ok], shape[ok], nc[ok]
    if i.size == 0:
        return pd.DataFrame()
    score = np.abs(mean_lr) + shape
    # best partner per source, one-to-one (greedy by score)
    order = np.argsort(score, kind="stable")
    used = set()
    sel = []
    for k in order:
        a, b = int(i[k]), int(j[k])
        if a in used or b in used:
            continue
        used.update((a, b))
        sel.append(k)
    sel = np.asarray(sel, np.int64)
    i, j, de, dn, sep, mean_lr, shape, nc, score = (x[sel] for x in (i, j, de, dn, sep, mean_lr, shape, nc, score))
    dt = (mjd[j] - mjd[i]) / DAYS_PER_JYEAR
    pe = src["pos_err_arcsec"].to_numpy(np.float64) if "pos_err_arcsec" in src else np.full(len(src), 0.3)
    perr = np.hypot(pe[single[i]], pe[single[j]]) / dt * 1000.0
    pmra, pmdec = de / dt * 1000.0, dn / dt * 1000.0
    ia, ib = single[i], single[j]
    with np.errstate(invalid="ignore", divide="ignore"):
        rr = np.stack([dflux[ia, 5] / dflux[ia, 1], dflux[ib, 5] / dflux[ib, 1]])
    k = np.isfinite(rr).sum(axis=0)
    red = np.where(k > 0, np.nansum(np.where(np.isfinite(rr), rr, 0.0), axis=0) / np.maximum(k, 1), np.nan)
    return pd.DataFrame({
        "method": "pair", "source_id": src["source_id"].to_numpy(np.int64)[ia], "source_id_b": src["source_id"].to_numpy(np.int64)[ib],
        "ra": ra[i], "dec": dec[i], "ra_b": ra[j], "dec_b": dec[j], "mjd": mjd[i], "mjd_b": mjd[j],
        "visit": vis[i].astype(np.int16), "visit_b": vis[j].astype(np.int16), "sep_arcsec": sep.astype(np.float32),
        "pmra": pmra.astype(np.float32), "pmdec": pmdec.astype(np.float32), "pm": np.hypot(pmra, pmdec).astype(np.float32),
        "pm_err": perr.astype(np.float32), "flux_ratio": np.exp(mean_lr).astype(np.float32), "shape_rms": shape.astype(np.float32),
        "n_common_det": nc.astype(np.int8), "red_ratio": red.astype(np.float32), "score": score.astype(np.float32),
        "_ia": ia, "_ib": ib,
    })


def control_separations(src: pd.DataFrame, footprints_by_visit=None) -> list[np.ndarray]:
    """Chance pairs: for each HIGHPM_CONTROL_OFFSETS, the same pairing (and coverage cut) with every even
    visit's single-visit sources moved by that offset (no real mover survives it). One array of pair
    separations (″) per offset."""
    from .sky import offset_radec
    spec = np.stack(src["flux_ujy"].to_numpy()).astype(np.float64) if len(src) else np.zeros((0, NDET * NCH_DET))
    dflux = detector_fluxes(spec)
    even = (src["visit"].to_numpy() >= 0) & (src["visit"].to_numpy() % 2 == 0)
    out = []
    for de, dn in C.HIGHPM_CONTROL_OFFSETS:
        c = src.copy()
        r, d = offset_radec(c["ra"].to_numpy()[even], c["dec"].to_numpy()[even], de, dn)
        c.loc[even, "ra"], c.loc[even, "dec"] = r, d
        pr = pair_movers(c, dflux, footprints_by_visit)
        out.append(pr["sep_arcsec"].to_numpy(np.float64) if len(pr) else np.zeros(0))
    return out


def control_pairs(src: pd.DataFrame, footprints_by_visit=None) -> float:
    """Mean number of chance pairs over the control offsets."""
    seps = control_separations(src, footprints_by_visit)
    return float(np.mean([len(x) for x in seps])) if seps else 0.0


def pair_vs_control(real_sep: np.ndarray, ctrl: list[np.ndarray]) -> list[dict]:
    """Per separation bin: real pairs, control mean, Poisson P(>= real), and whether the bin beats the control.
    The control mean gets one chance pair added over all offsets (so an empty control is not a zero rate)."""
    from scipy.stats import poisson
    edges = np.asarray(C.HIGHPM_PAIR_SEP_BINS, np.float64)
    k = max(len(ctrl), 1)
    out = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        n = int(((real_sep >= lo) & (real_sep < hi)).sum())
        tot = int(sum(((c >= lo) & (c < hi)).sum() for c in ctrl))
        lam = (tot + 1) / k
        p = float(poisson.sf(n - 1, lam)) if n > 0 else 1.0
        out.append({"sep_arcsec": [float(lo), float(hi)], "real": n, "control_mean": round(tot / k, 2),
                    "p": round(p, 4), "beats_control": bool(n > 0 and p < C.HIGHPM_PAIR_MAX_P)})
    return out


def fit_movers(ast: pd.DataFrame | None) -> pd.DataFrame:
    if ast is None or not len(ast):
        return pd.DataFrame()
    m = (ast["pm"] >= C.HIGHPM_FIT_MIN_MAS_YR) & (ast["pm_snr"] >= C.HIGHPM_FIT_MIN_SNR)
    a = ast[m]
    err = np.hypot(a["pmra_err"], a["pmdec_err"]) / np.sqrt(2)
    return pd.DataFrame({
        "method": "fit", "source_id": a["source_id"].to_numpy(np.int64), "source_id_b": np.int64(-1),
        "ra": a["ra"].to_numpy(), "dec": a["dec"].to_numpy(), "ra_b": np.nan, "dec_b": np.nan,
        "mjd": a["epoch_mjd"].to_numpy(), "mjd_b": np.nan, "visit": np.int16(-1), "visit_b": np.int16(-1),
        "sep_arcsec": np.float32(np.nan), "pmra": a["pmra"].to_numpy(np.float32), "pmdec": a["pmdec"].to_numpy(np.float32),
        "pm": a["pm"].to_numpy(np.float32), "pm_err": err.to_numpy(np.float32), "flux_ratio": np.float32(1.0),
        "shape_rms": np.float32(0.0), "n_common_det": np.int8(0), "red_ratio": np.float32(np.nan),
        "score": (1.0 / a["pm_snr"]).to_numpy(np.float32),
        **{f"_{c}": (a[c].to_numpy(np.float64) if c in a else np.full(len(a), np.nan))
           for c in ("n_visits", "span_yr", "chi2_nu", "n_dots", "n_used", "pm_snr")},
    })


def pm_disagrees(pmra_c, pmdec_c, out: pd.DataFrame) -> np.ndarray:
    """A counterpart's own proper motion (pmra_c, pmdec_c) disagrees with the candidate's (finite only; no parallax)."""
    d = np.hypot(np.asarray(pmra_c, np.float64) - out["pmra"].to_numpy(np.float64),
                 np.asarray(pmdec_c, np.float64) - out["pmdec"].to_numpy(np.float64))
    tol = np.maximum.reduce([C.HIGHPM_PM_AGREE_SIGMA * np.nan_to_num(out["pm_err"].to_numpy(np.float64)),
                             C.HIGHPM_CATWISE_PM_TOL * out["pm"].to_numpy(np.float64), np.full(len(out), 100.0)])
    return np.isfinite(d) & (d > tol)


def vet(out: pd.DataFrame, gaia_dis: np.ndarray, cw_dis: np.ndarray, pair_bins: list[dict] | None,
        confirmed: np.ndarray | None = None, checked: np.ndarray | None = None,
        gaia_slow: np.ndarray | None = None) -> pd.DataFrame:
    """`vetted` and `reason` (module docstring). pair_bins None: pairs are not tested against the control.
    confirmed: Gaia confirms the motion (the QUALITY reasons don't apply); checked: a Gaia or CatWISE motion exists
    to check it against (None: every row counts as checked); gaia_slow: Gaia agrees but predicts under
    HIGHPM_FIT_MIN_MAS_YR (pm + parallax leak), so the row is a known slow star, not a mover to list."""
    n = len(out)
    fit = (out["method"] == "fit").to_numpy()
    col = lambda c: out[c].to_numpy(np.float64) if c in out else np.full(n, np.nan)  # noqa: E731
    conf = np.zeros(n, bool) if confirmed is None else np.asarray(confirmed, bool)
    chk = np.ones(n, bool) if checked is None else np.asarray(checked, bool)
    with np.errstate(invalid="ignore"):
        bad = {
            "short_span": fit & ((col("_n_visits") < C.HIGHPM_FIT_MIN_VISITS) | (col("_span_yr") < C.HIGHPM_FIT_MIN_SPAN_YR)),
            "few_dots": fit & (np.fmin(col("_n_dots"), col("_n_used")) < C.HIGHPM_FIT_MIN_DOTS),  # dots the fit used
            "few_sessions": fit & (col("_sessions_min") < C.HIGHPM_FIT_MIN_SESSIONS),
            "bad_fit": fit & (col("_chi2_nu") > C.HIGHPM_FIT_MAX_CHI2_NU),
            "over_pm_cap": fit & (col("pm") > C.HIGHPM_FIT_MAX_PM_MAS_YR),
            "gaia_pm_disagrees": np.asarray(gaia_dis, bool),
            "catwise_pm_disagrees": np.asarray(cw_dis, bool),
            "unchecked": fit & ~chk & ((col("_pm_snr") < C.HIGHPM_NOCAT_MIN_SNR) | (col("_saturated") > 0)),
            "at_control_level": np.zeros(n, bool),
            "gaia_slow": np.zeros(n, bool) if gaia_slow is None else np.asarray(gaia_slow, bool),
        }
    for k in QUALITY:
        bad[k] &= ~conf
    if pair_bins is not None:
        sep = col("sep_arcsec")
        ok_bin = np.zeros(n, bool)
        for b in pair_bins:
            lo, hi = b["sep_arcsec"]
            if b["beats_control"]:
                ok_bin |= (sep >= lo) & (sep < hi)
        bad["at_control_level"] = ~fit & ~ok_bin
    names = np.array(REASONS)
    bits = np.stack([bad[k] for k in REASONS], 1) if n else np.zeros((0, len(REASONS)), bool)
    out["reason"] = [",".join(names[b]) for b in bits]
    out["vetted"] = ~bits.any(axis=1) if n else np.zeros(0, bool)
    return out


def compute(src: pd.DataFrame, footprints_by_visit, crossids: pd.DataFrame | None, ast: pd.DataFrame | None,
            pair_bins: list[dict] | None = None, vstats: pd.DataFrame | None = None,
            cw_err: pd.Series | None = None) -> pd.DataFrame:
    """Candidates (module docstring). vstats: visit_stats of the fits' sources (None: no parallax leak, sessions
    not checked); cw_err: CatWISE pm error (mas/yr) by catwise_ref_id (None: 0)."""
    spec = np.stack(src["flux_ujy"].to_numpy()).astype(np.float64) if len(src) else np.zeros((0, NDET * NCH_DET))
    dflux = detector_fluxes(spec)
    pairs = pair_movers(src, dflux, footprints_by_visit)
    fits = fit_movers(ast)
    src_ix = pd.Index(src["source_id"].to_numpy(np.int64))
    if len(fits):
        pos = src_ix.get_indexer(fits["source_id"].to_numpy(np.int64))
        okp = pos >= 0
        red = np.full(len(fits), np.nan)
        red[okp] = dflux[pos[okp], 5] / dflux[pos[okp], 1]
        fits["red_ratio"] = red.astype(np.float32)
        if vstats is not None and len(vstats):
            v = vstats.set_index("source_id").reindex(fits["source_id"].to_numpy(np.int64))
            fits["_k_e"], fits["_k_n"] = v["k_e"].to_numpy(np.float64), v["k_n"].to_numpy(np.float64)
            fits["_sessions_min"] = v["sessions_min"].to_numpy(np.float64)
    out = pd.concat([x for x in (pairs, fits) if len(x)], ignore_index=True) if (len(pairs) or len(fits)) else pd.DataFrame(columns=[*COLUMNS])
    if not len(out):
        empty = pd.DataFrame({c: pd.Series(dtype="float64") for c in COLUMNS})
        return empty.astype({"vetted": bool, "reason": str, "method": str, "status": str})
    n = len(out)
    ke = out["_k_e"].to_numpy(np.float64) if "_k_e" in out else np.zeros(n)  # pairs: no leak (one visit each side)
    kn = out["_k_n"].to_numpy(np.float64) if "_k_n" in out else np.zeros(n)
    sat = np.zeros(n, bool)
    if "saturated" in src:
        sp = src_ix.get_indexer(out["source_id"].to_numpy(np.int64))
        sat[sp >= 0] = src["saturated"].fillna(False).to_numpy(bool)[sp[sp >= 0]]
    out["_saturated"] = sat.astype(np.float64)
    plx_per_pm = np.where(sat, C.HIGHPM_CW_PLX_PER_PM_SAT, C.HIGHPM_CW_PLX_PER_PM)
    # Gaia and CatWISE, from either side
    gref = np.full(n, "", dtype=object)
    gid = np.full(n, -1, np.int64)
    cw = np.full(n, "", dtype=object)
    agree = np.zeros(n, bool)
    g_has = np.zeros(n, bool)      # a Gaia counterpart with its own pm
    g_slow = np.zeros(n, bool)     # ... whose pm + parallax leak is under HIGHPM_FIT_MIN_MAS_YR: a known slow star
    gaia_dis = np.zeros(n, bool)
    cw_has = np.zeros(n, bool)
    cw_dis = np.zeros(n, bool)
    if crossids is not None and len(crossids):
        x = crossids.set_index("source_id")
        for col in ("source_id", "source_id_b"):
            r = x.reindex(out[col].to_numpy(np.int64))
            g = r["gaia_ref_id"].fillna("").astype(str).to_numpy()
            fill = (gref == "") & (g != "")
            gref[fill] = g[fill]
            gid[fill] = r["gaia_id"].to_numpy()[fill]
            nm = r["catwise_name"].fillna("").astype(str).to_numpy()
            cerr = np.zeros(n)
            if cw_err is not None and "catwise_ref_id" in r:
                cerr = np.nan_to_num(cw_err.reindex(r["catwise_ref_id"].astype(str).to_numpy()).to_numpy(np.float64))
            has, ok = catwise_check(out, r["catwise_pmra"].to_numpy(np.float64), r["catwise_pmdec"].to_numpy(np.float64),
                                    cerr, ke, kn, plx_per_pm)
            agree |= ok
            cw_has |= has
            cw[(cw == "") & (nm != "")] = nm[(cw == "") & (nm != "")]
            cw_dis |= (nm != "") & has & ~ok
            if "gaia_pmra" in r and "gaia_pmdec" in r:
                plx = r["gaia_parallax"].to_numpy(np.float64) if "gaia_parallax" in r else np.zeros(n)
                gh, gd, gp = gaia_check(out, r["gaia_pmra"].to_numpy(np.float64), r["gaia_pmdec"].to_numpy(np.float64),
                                        plx, ke, kn, predicted=True)
                g_slow |= (g != "") & gh & (gp < C.HIGHPM_FIT_MIN_MAS_YR)
                g_has |= (g != "") & gh
                gaia_dis |= (g != "") & gd
    out["gaia_ref_id"] = gref.astype(str)
    out["gaia_id"] = gid
    out["catwise_name"] = cw.astype(str)
    out["catwise_pm_agree"] = agree
    out["status"] = np.where(out["gaia_ref_id"] == "", "new", "gaia")
    confirmed = g_has & ~gaia_dis
    # CatWISE judges only where Gaia has no motion to compare (Gaia: parallax known, far smaller errors)
    out = vet(out, gaia_dis, cw_dis & ~g_has, pair_bins, confirmed=confirmed, checked=g_has | cw_has,
              gaia_slow=g_slow & ~gaia_dis)
    # vetted first, new before gaia, then pairs before fits, then best score
    out["_r"] = ((~out["vetted"]).astype(int) * 4 + (out["status"] != "new").astype(int) * 2
                 + (out["method"] != "pair").astype(int))
    out = out.sort_values(["_r", "score"], kind="stable").reset_index(drop=True)
    return out[COLUMNS]


def recall(cands: pd.DataFrame, crossids: pd.DataFrame | None, refcat: pd.DataFrame, zone: C.Zone, mjd_ref: float) -> dict:
    """Gaia stars faster than HIGHPM_RECALL_MAS_YR inside the zone: detected as a source, recovered as a candidate."""
    g = refcat[refcat["cat"] == "gaia"]
    pm = np.hypot(g["pmra"].to_numpy(np.float64), g["pmdec"].to_numpy(np.float64))
    fast = g[np.nan_to_num(pm) > C.HIGHPM_RECALL_MAS_YR].reset_index(drop=True)
    if len(fast):
        from skyshift_core import propagate
        r, d = propagate(fast["ra"].to_numpy(), fast["dec"].to_numpy(), fast["pmra"].to_numpy(), fast["pmdec"].to_numpy(),
                         mjd_ref, epoch_mjd=np.nan_to_num(fast["epoch_mjd"].to_numpy(), nan=57388.5))
        fast = fast[zone.contains(r, d)].reset_index(drop=True)
    ids = fast["ref_id"].astype(str).to_numpy()
    detected = np.isin(ids, crossids["gaia_ref_id"].astype(str).to_numpy()) if crossids is not None and len(crossids) else np.zeros(len(ids), bool)
    by_id = np.isin(ids, cands["gaia_ref_id"].astype(str).to_numpy()) if len(cands) else np.zeros(len(ids), bool)
    # or by position at each candidate's epoch (a candidate whose crossid failed still counts)
    near = np.zeros(len(ids), bool)
    if len(cands) and len(fast):
        k, _, _ = crossmatch(cands["ra"].to_numpy(np.float64), cands["dec"].to_numpy(np.float64),
                             cands["mjd"].to_numpy(np.float64), fast, C.HIGHPM_RECALL_ARCSEC)
        near[k[k >= 0]] = True
    rec = by_id | near
    rec_v = np.zeros(len(ids), bool)
    if len(cands) and "vetted" in cands and len(fast):
        v = cands[cands["vetted"].to_numpy(bool)]
        rec_v = np.isin(ids, v["gaia_ref_id"].astype(str).to_numpy()) if len(v) else rec_v
        if len(v):
            k, _, _ = crossmatch(v["ra"].to_numpy(np.float64), v["dec"].to_numpy(np.float64),
                                 v["mjd"].to_numpy(np.float64), fast, C.HIGHPM_RECALL_ARCSEC)
            rec_v[k[k >= 0]] = True
    pmf = np.hypot(fast["pmra"], fast["pmdec"]).to_numpy() if len(fast) else np.zeros(0)
    return {"gaia_fast_in_zone": int(len(fast)), "detected_as_source": int(detected.sum()), "recovered": int(rec.sum()),
            "recovered_vetted": int(rec_v.sum()),
            "recall_of_detected": round(float(rec[detected].mean()), 3) if detected.any() else None,
            "pm_threshold_mas_yr": C.HIGHPM_RECALL_MAS_YR,
            "stars": [{"gaia_ref_id": i, "pm": round(float(p), 1), "g": None if not np.isfinite(gm) else round(float(gm), 2),
                       "detected": bool(dd), "recovered": bool(rr), "recovered_vetted": bool(vv)}
                      for i, p, gm, dd, rr, vv in zip(ids, pmf, fast["g"].to_numpy(np.float64) if len(fast) else [],
                                                      detected, rec, rec_v)][:200]}


def catwise_pm_err(store: Store, zone: C.Zone) -> pd.Series | None:
    """CatWISE pm error (mas/yr, mean of the two axes) by ref_id, from refcat/<zone>.parquet (None if absent)."""
    rel = C.REFCAT_REL.format(zone=zone.name)
    try:
        r = store.read_parquet(rel, columns=["ref_id", "cat", "pmra_err", "pmdec_err"])
    except Exception:  # noqa: BLE001 - an old refcat without these columns: CatWISE errors count as 0
        return None
    r = r[r["cat"].astype(str) == "catwise"]
    e = np.hypot(r["pmra_err"].to_numpy(np.float64), r["pmdec_err"].to_numpy(np.float64)) / np.sqrt(2)
    return pd.Series(e, index=r["ref_id"].astype(str).to_numpy())


def fp_control(src: pd.DataFrame, crossids: pd.DataFrame | None, ast: pd.DataFrame | None, vstats: pd.DataFrame | None,
               cw_err: pd.Series | None) -> dict:
    """The fit selection and vetting on Gaia-5p stars that are still for us (|pm| < HIGHPM_STILL_PM_MAX,
    ϖ < HIGHPM_STILL_PLX_MAX), with their Gaia entry hidden: what comes out vetted is a false positive. Its rate
    times the fitted sources without a Gaia pm = the fakes to expect among the vetted "new" candidates."""
    if ast is None or crossids is None or not len(ast) or "gaia_pmra" not in crossids:
        return {}
    x = crossids
    has_g = x["gaia_ref_id"].fillna("").astype(str).to_numpy() != ""
    gpm = np.hypot(x["gaia_pmra"].to_numpy(np.float64), x["gaia_pmdec"].to_numpy(np.float64))
    plx = x["gaia_parallax"].to_numpy(np.float64) if "gaia_parallax" in x else np.zeros(len(x))
    with np.errstate(invalid="ignore"):
        still = has_g & np.isfinite(gpm) & (gpm < C.HIGHPM_STILL_PM_MAX) & (np.nan_to_num(plx) < C.HIGHPM_STILL_PLX_MAX)
    with_pm = set(x["source_id"].to_numpy(np.int64)[has_g & np.isfinite(gpm)])
    a = ast[ast["source_id"].isin(x["source_id"].to_numpy(np.int64)[still])]
    hidden = x[x["source_id"].isin(a["source_id"])].copy()
    hidden["gaia_ref_id"] = ""
    hidden["gaia_id"] = -1
    for c in ("gaia_pmra", "gaia_pmdec", "gaia_parallax"):
        if c in hidden:
            hidden[c] = np.nan
    s = src[src["source_id"].isin(a["source_id"])].copy()
    s["visit"] = -1  # fits only, no pairs
    c = compute(s, None, hidden, a, None, vstats, cw_err)
    fp = int((c["vetted"].to_numpy(bool) & (c["method"] == "fit").to_numpy()).sum()) if len(c) else 0
    n_new = int((~ast["source_id"].isin(list(with_pm))).sum())
    rate = fp / len(a) if len(a) else 0.0
    return {"still_stars": int(len(a)), "selected": int((c["method"] == "fit").sum()) if len(c) else 0,
            "false_positives": fp, "per_100k": round(1e5 * rate, 2), "sources_without_gaia_pm": n_new,
            "expected_new_fakes": round(rate * n_new, 2),
            "still_definition": {"pm_max_mas_yr": C.HIGHPM_STILL_PM_MAX, "parallax_max_mas": C.HIGHPM_STILL_PLX_MAX}}


def run(store: Store, zone: C.Zone, workers: int = 16) -> dict:
    t0 = time.time()
    src = store.read_parquet(results_rel(zone.name, "sources.parquet"), columns=["source_id", "ra", "dec", "pos_err_arcsec", "flux_ujy"])
    try:
        sat = store.read_parquet(results_rel(zone.name, "sources.parquet"), columns=["source_id", "saturated"])
        src = src.merge(sat, on="source_id", how="left")
    except Exception:  # noqa: BLE001 - sources without `saturated`: every source counts as unsaturated
        pass
    members = store.read_parquet(results_rel(zone.name, "members.parquet"), columns=["source_id", "obs_id", "detector"])
    pl = store.read_parquet(photolist_rel(zone.name))
    sv = source_visits(store, zone, members, pl[["obs_id", "detector", "mjd", "visit"]])
    src = src.merge(sv, on="source_id", how="left")
    src["visit"] = src["visit"].fillna(-1).astype(np.int64)
    fps: dict[int, list[Footprint]] = {}
    for s_region, v in zip(pl["s_region"], pl["visit"]):
        fps.setdefault(int(v), []).append(Footprint.from_s_region(s_region))
    xid_rel = results_rel(zone.name, "crossids.parquet")
    crossids = store.read_parquet(xid_rel) if store.exists(xid_rel) else None
    ast_rel = results_rel(zone.name, "astrometry.parquet")
    ast = store.read_parquet(ast_rel) if store.exists(ast_rel) else None
    fit_ids = fit_movers(ast)["source_id"].to_numpy(np.int64) if ast is not None and len(ast) else np.zeros(0, np.int64)
    vstats = visit_stats(members, pl[[c for c in ("obs_id", "detector", "mjd", "visit", "session") if c in pl]],
                         src[["source_id", "ra", "dec"]], fit_ids)
    del members
    cw_err = catwise_pm_err(store, zone)
    ctrl = control_separations(src, fps)
    spec = np.stack(src["flux_ujy"].to_numpy()).astype(np.float64) if len(src) else np.zeros((0, NDET * NCH_DET))
    real = pair_movers(src, detector_fluxes(spec), fps)
    bins = pair_vs_control(real["sep_arcsec"].to_numpy(np.float64) if len(real) else np.zeros(0), ctrl)
    cands = compute(src, fps, crossids, ast, bins, vstats, cw_err)
    store.write_parquet(results_rel(zone.name, "highpm_candidates.parquet"), cands)
    refcat = load_refcat(store, zone)
    mjd_ref = float(np.nanmedian(src["mjd"])) if len(src) else 61000.0
    v, new = cands["vetted"].to_numpy(bool), (cands["status"] == "new").to_numpy()
    rep = {"zone": zone.name, "candidates": int(len(cands)), "vetted": int(v.sum()),
           "new": int((new & v).sum()), "new_unvetted": int((new & ~v).sum()),
           "by_method": {m: int((cands["method"] == m).sum()) for m in ("pair", "fit")},
           "by_method_vetted": {m: int(((cands["method"] == m).to_numpy() & v).sum()) for m in ("pair", "fit")},
           "reasons": {r: int(cands["reason"].str.split(",").apply(lambda x, r=r: r in x).sum()) for r in REASONS},
           "single_visit_sources": int((src["visit"] >= 0).sum()),
           "control_pairs": round(float(np.mean([len(x) for x in ctrl])), 2) if ctrl else 0.0,
           "control_offsets_arcsec": [list(o) for o in C.HIGHPM_CONTROL_OFFSETS],
           "pair_vs_control": bins,
           "fp_control": fp_control(src, crossids, ast, vstats, cw_err),
           "recall": recall(cands, crossids, refcat, zone, mjd_ref)}
    store.write_json(results_rel(zone.name, "highpm_report.json"), rep)
    st = {k: v for k, v in rep.items() if k != "recall"}
    st["recall"] = {k: v for k, v in rep["recall"].items() if k != "stars"}
    st["wall_s"] = round(time.time() - t0, 1)
    L.info("highpm %s: %s", zone.name, st)
    return st
