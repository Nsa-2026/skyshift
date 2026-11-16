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
   >= HIGHPM_FIT_MIN_SNR (slower movers that stay linked as one source); from HIGHPM_FIT_MIN_SNR_GAIA when Gaia
   confirms a fast motion (its pm + ϖ·k >= HIGHPM_FIT_MIN_MAS_YR, agreeing with ours), from HIGHPM_FIT_MIN_SNR_CATWISE
   when CatWISE does (no Gaia pm; CatWISE's own |pm| >= HIGHPM_FIT_MIN_MAS_YR and HIGHPM_CW_CONFIRM_SNR × its error,
   agreeing). An unconfirmed motion never counts on weaker evidence than HIGHPM_FIT_MIN_SNR.
`status` = "new" when neither side matches Gaia (crossids), else "gaia". A CatWISE match whose proper
motion agrees with ours (parallax-aware, below) is a bonus (`catwise_pm_agree`); CatWISE's motion of a saturated
source brighter than W1 HIGHPM_CW_SAT_W1_MAX counts for nothing (neither checks nor disproves). `red_ratio` =
D6 / D2 flux (4.4–5.0 µm over 1.1–1.6 µm): large for cold objects (methane, CO absorption bluewards).

Deep checks (deep_check, HIGHPM_DEEP_CHECK) before a vetted row counts as "new": refcat stops at G 20 and crossid's
2MASS match ignores motion. Gaia DR3 with no magnitude cut (ESA TAP sync, HIGHPM_DEEP_BATCH rows per query) around
our position and around where our motion, less a parallax leak, puts us at 2016.0: a source whose own motion brings it
to us is the counterpart (status "gaia", vetted unless its motion is not ours: gaia_pm_disagrees / gaia_slow). Else a
2MASS source (refcat) where our motion puts us at its epoch makes it status "2mass". A failed query leaves the rows
"new" (report deep_check.deep_gaia_checked false); nothing raises.

Parallax: with two visits half a year apart a fit's pm is the true pm plus ϖ·(k_e, k_n), the parallax leak
(visit_stats: k = cov(t, parallax factor) / var(t) over the source's dots; hunt k_e ≈ −4.1, k_n ≈ +1.0). So a
Gaia counterpart is compared as its pm + its parallax × k (gaia_check), and a CatWISE counterpart (no parallax)
as its pm + ϖ·k for the best 0 ≤ ϖ ≤ HIGHPM_CW_PLX_PER_PM × its |pm| (catwise_check; _SAT for saturated
sources). A nearby Planet X moves AND shows parallax: both count as signal in pm_snr.
From 16 Nov astrometry fits the motion model of skyshift_core.motion (`fit` mode, written here as `fit_mode`): with
Gaia's parallax as a prior (pm+gaia_plx) or its own parallax (pm+plx) the pm holds no leak, so k is 0 for those rows
(effective_leak); pm and pm_leak rows use astrometry's own weighted leak (plx_leak_ra, plx_leak_dec). Tables written
before keep visit_stats' k. A fit astrometry calls `unsteady` (its visits go out and back instead of along a line:
visit-level chi2_nu > skyshift_core.motion.UNSTEADY_CHI2_NU) gets reason `unsteady`, even when Gaia agrees.

Control (highpm_report.json `control_pairs`, `pair_vs_control`): the same pairing, with the same coverage
cut, after moving every even visit's single-visit sources by each HIGHPM_CONTROL_OFFSETS: the mean is the
number of chance pairs to expect, per separation bin (HIGHPM_PAIR_SEP_BINS).

Vetting (columns `vetted`, `reason`; every candidate is kept): a candidate is vetted when `reason` is empty.
Reasons: short_span, few_dots, few_sessions (< HIGHPM_FIT_MIN_SESSIONS sessions in a visit), bad_fit, unsteady, over_pm_cap
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
           "gaia_ref_id", "gaia_id", "catwise_name", "catwise_pm_agree", "score", "vetted", "reason", "fit_mode",
           "counterpart", "counterpart_id", "counterpart_sep_arcsec", "counterpart_epoch_mjd"]
REASONS = ("short_span", "few_dots", "few_sessions", "bad_fit", "over_pm_cap", "gaia_pm_disagrees",
           "catwise_pm_disagrees", "unchecked", "at_control_level", "gaia_slow", "unsteady", "gaia_still", "allwise_still")
# counterpart: what lists our object. compute: gaia_pm (crossid Gaia match with its own pm), gaia_crossid (crossid Gaia
# match, no Gaia pm), catwise_pm (crossid CatWISE motion agrees); deep_check: gaia_pm, gaia_position, gaia_still,
# catwise_pm, tmass_position, allwise_position, allwise_still; "" = none.
COUNTERPARTS = ("gaia_pm", "gaia_crossid", "gaia_position", "gaia_still", "catwise_pm", "tmass_position",
                "allwise_position", "allwise_still")
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


def effective_leak(vstats: pd.DataFrame | None, ast: pd.DataFrame | None) -> pd.DataFrame | None:
    """visit_stats' rows with k_e, k_n replaced by the leak left in astrometry's pm (tables from 16 Nov, columns `fit`,
    plx_leak_ra, plx_leak_dec): 0 where the fit took parallax out (Gaia's prior or its own), its own weighted leak
    where it fitted none. Rows without such a fit keep visit_stats' k."""
    if vstats is None or ast is None or not len(vstats) or not {"fit", "plx_leak_ra", "plx_leak_dec"} <= set(ast.columns):
        return vstats
    from skyshift_core.motion import PARALLAX_MODES
    a = ast.drop_duplicates("source_id").set_index("source_id").reindex(vstats["source_id"].to_numpy(np.int64))
    has = a["fit"].notna().to_numpy()
    plx = np.isin(a["fit"].fillna("").astype(str).to_numpy(), PARALLAX_MODES)
    out = vstats.copy()
    for k, c in (("k_e", "plx_leak_ra"), ("k_n", "plx_leak_dec")):
        own = np.where(plx, 0.0, np.nan_to_num(a[c].to_numpy(np.float64)))
        out[k] = np.where(has, own, out[k].to_numpy(np.float64))
    return out


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
    """astrometry fits >= HIGHPM_FIT_MIN_MAS_YR down to the lower, confirmed-only significance (compute drops those under
    HIGHPM_FIT_MIN_SNR that no catalogue motion confirms)."""
    if ast is None or not len(ast):
        return pd.DataFrame()
    m = (ast["pm"] >= C.HIGHPM_FIT_MIN_MAS_YR) & (ast["pm_snr"] >= min(C.HIGHPM_FIT_MIN_SNR, C.HIGHPM_FIT_MIN_SNR_GAIA))
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
        "fit_mode": a["fit"].astype(str).to_numpy() if "fit" in a else "",
        "_unsteady": a["unsteady"].fillna(False).to_numpy(bool).astype(np.float64) if "unsteady" in a else 0.0,
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
            # pairs too (16 Nov): every vetted pair of the sky run was 23–115″/yr and real ≈ control in all 61 zones
            "over_pm_cap": col("pm") > C.HIGHPM_FIT_MAX_PM_MAS_YR,
            "gaia_pm_disagrees": np.asarray(gaia_dis, bool),
            "catwise_pm_disagrees": np.asarray(cw_dis, bool),
            "unchecked": fit & ~chk & ((col("_pm_snr") < C.HIGHPM_NOCAT_MIN_SNR) | (col("_saturated") > 0)),
            "at_control_level": np.zeros(n, bool),
            "gaia_slow": np.zeros(n, bool) if gaia_slow is None else np.asarray(gaia_slow, bool),
            "unsteady": fit & (col("_unsteady") > 0),  # out and back, not a line: never a lead, even when Gaia agrees
            "gaia_still": np.zeros(n, bool),     # deep_check's
            "allwise_still": np.zeros(n, bool),
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
        return empty.astype({"vetted": bool, "reason": str, "method": str, "status": str, "fit_mode": str,
                             "counterpart": str, "counterpart_id": str})
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
    cw_fast = np.zeros(n, bool)    # CatWISE's own motion is >= HIGHPM_FIT_MIN_MAS_YR (and HIGHPM_CW_CONFIRM_SNR) and agrees
    g_sep = np.full(n, np.nan)     # the crossid Gaia / CatWISE match's separation (counterpart_sep_arcsec)
    cw_sep = np.full(n, np.nan)
    if crossids is not None and len(crossids):
        x = crossids.set_index("source_id")
        for col in ("source_id", "source_id_b"):
            r = x.reindex(out[col].to_numpy(np.int64))
            g = r["gaia_ref_id"].fillna("").astype(str).to_numpy()
            fill = (gref == "") & (g != "")
            gref[fill] = g[fill]
            gid[fill] = r["gaia_id"].to_numpy()[fill]
            if "gaia_sep_arcsec" in r:
                g_sep[fill] = r["gaia_sep_arcsec"].to_numpy(np.float64)[fill]
            nm = r["catwise_name"].fillna("").astype(str).to_numpy()
            cerr = np.zeros(n)
            if cw_err is not None and "catwise_ref_id" in r:
                cerr = np.nan_to_num(cw_err.reindex(r["catwise_ref_id"].astype(str).to_numpy()).to_numpy(np.float64))
            has, ok = catwise_check(out, r["catwise_pmra"].to_numpy(np.float64), r["catwise_pmdec"].to_numpy(np.float64),
                                    cerr, ke, kn, plx_per_pm)
            # a saturated source brighter than W1 8: CatWISE's motion neither checks nor disproves ours
            w1 = r["w1"].to_numpy(np.float64) if "w1" in r else np.full(n, np.nan)
            with np.errstate(invalid="ignore"):
                has &= ~(sat & (w1 < C.HIGHPM_CW_SAT_W1_MAX))
            ok &= has
            agree |= ok
            cw_has |= has
            pm_cw = np.hypot(r["catwise_pmra"].to_numpy(np.float64), r["catwise_pmdec"].to_numpy(np.float64))
            with np.errstate(invalid="ignore"):
                cw_fast |= ok & (pm_cw >= C.HIGHPM_FIT_MIN_MAS_YR) & (pm_cw >= C.HIGHPM_CW_CONFIRM_SNR * cerr)
            if "catwise_sep_arcsec" in r:
                cw_sep[(cw == "") & (nm != "")] = r["catwise_sep_arcsec"].to_numpy(np.float64)[(cw == "") & (nm != "")]
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
    out["fit_mode"] = out["fit_mode"].fillna("").astype(str) if "fit_mode" in out else ""  # pairs: ""
    # what lists our object (deep_check adds more): a Gaia match with its own pm, one without (a position-only Gaia
    # source at our position: deep_check looks again), an agreeing CatWISE motion
    has_g = gref != ""
    out["counterpart"] = np.select([has_g & g_has, has_g, agree], ["gaia_pm", "gaia_crossid", "catwise_pm"], "")
    out["counterpart_id"] = np.where(has_g, gref, np.where(agree, cw, "")).astype(str)
    out["counterpart_sep_arcsec"] = np.where(has_g, g_sep, np.where(agree, cw_sep, np.nan)).astype(np.float32)
    out["counterpart_epoch_mjd"] = np.where(has_g | agree, out["mjd"].to_numpy(np.float64), np.nan)
    confirmed = g_has & ~gaia_dis
    # CatWISE judges only where Gaia has no motion to compare (Gaia: parallax known, far smaller errors)
    out = vet(out, gaia_dis, cw_dis & ~g_has, pair_bins, confirmed=confirmed, checked=g_has | cw_has,
              gaia_slow=g_slow & ~gaia_dis)
    # under HIGHPM_FIT_MIN_SNR a fit is a candidate only when a catalogue confirms a fast motion: Gaia's (pm + ϖ·k >=
    # HIGHPM_FIT_MIN_MAS_YR, agreeing) from HIGHPM_FIT_MIN_SNR_GAIA, CatWISE's (no Gaia pm) from HIGHPM_FIT_MIN_SNR_CATWISE
    fsnr = out["_pm_snr"].to_numpy(np.float64) if "_pm_snr" in out else np.full(n, np.inf)
    with np.errstate(invalid="ignore"):
        low_ok = ((confirmed & ~g_slow & (fsnr >= C.HIGHPM_FIT_MIN_SNR_GAIA))
                  | (cw_fast & ~g_has & (fsnr >= C.HIGHPM_FIT_MIN_SNR_CATWISE)))
        drop = (out["method"] == "fit").to_numpy() & ~(fsnr >= C.HIGHPM_FIT_MIN_SNR) & ~low_ok
    return _ordered(out[~drop])


def _ordered(out: pd.DataFrame) -> pd.DataFrame:
    """COLUMNS; vetted first, new before catalogued, then pairs before fits, then best score."""
    r = ((~out["vetted"].to_numpy(bool)).astype(int) * 4 + (out["status"] != "new").to_numpy().astype(int) * 2
         + (out["method"] != "pair").to_numpy().astype(int))
    return out.assign(_r=r).sort_values(["_r", "score"], kind="stable").reset_index(drop=True)[COLUMNS]


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


# --- deep checks before a vetted row counts as "new" ------------------------------------------------------------------
def deep_gaia_tap(adql: str):
    """ESA Gaia TAP, synchronous (its async jobs stalled for minutes, 14 Nov), HIGHPM_DEEP_TIMEOUT_S per request."""
    import pyvo
    import requests

    from .refcat import ESA_TAP

    class _Session(requests.Session):
        def request(self, *a, **k):
            k.setdefault("timeout", C.HIGHPM_DEEP_TIMEOUT_S)
            return super().request(*a, **k)

    return pyvo.dal.TAPService(ESA_TAP, session=_Session()).run_sync(adql, maxrec=C.REFCAT_MAXREC).to_table()


def deep_irsa_tap(adql: str, uploads: dict):
    """IRSA TAP, synchronous, with an uploaded table of circles (crossid.irsa_tap), HIGHPM_DEEP_TIMEOUT_S per request.
    Tests replace it (conftest: offline)."""
    from .crossid import irsa_tap
    return irsa_tap(adql, uploads, C.HIGHPM_DEEP_TIMEOUT_S)


def _leak(rows: pd.DataFrame, vstats: pd.DataFrame | None) -> tuple[np.ndarray, np.ndarray]:
    """Each row's parallax leak k (fits: visit_stats; pairs: none, as compute)."""
    ke, kn = np.zeros(len(rows)), np.zeros(len(rows))
    if vstats is not None and len(vstats) and len(rows):
        v = vstats.drop_duplicates("source_id").set_index("source_id").reindex(rows["source_id"].to_numpy(np.int64))
        fit = (rows["method"] == "fit").to_numpy()
        ke = np.where(fit, np.nan_to_num(v["k_e"].to_numpy(np.float64)), 0.0)
        kn = np.where(fit, np.nan_to_num(v["k_n"].to_numpy(np.float64)), 0.0)
    return ke, kn


def _segment(rows: pd.DataFrame, ke, kn, mjd_to) -> tuple[np.ndarray, ...]:
    """Our position moved to mjd_to with our pm (ra_a, dec_a) and with our pm less the largest parallax leak ϖ·k,
    ϖ = HIGHPM_CW_PLX_PER_PM × |pm| (ra_b, dec_b): where a nearby object was then lies between the two."""
    from .sky import offset_radec
    dt = (np.asarray(mjd_to, np.float64) - rows["mjd"].to_numpy(np.float64)) / DAYS_PER_JYEAR / 1000.0  # yr, mas → ″
    pmra, pmdec = rows["pmra"].to_numpy(np.float64), rows["pmdec"].to_numpy(np.float64)
    plx = C.HIGHPM_CW_PLX_PER_PM * np.hypot(pmra, pmdec)
    ra, dec = rows["ra"].to_numpy(np.float64), rows["dec"].to_numpy(np.float64)
    ra_a, dec_a = offset_radec(ra, dec, pmra * dt, pmdec * dt)
    ra_b, dec_b = offset_radec(ra, dec, (pmra - plx * ke) * dt, (pmdec - plx * kn) * dt)
    return ra_a, dec_a, ra_b, dec_b


def _circles(rows: pd.DataFrame, ke, kn, mjd_to, pad_yr: float = 0.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Two circles a row (ra, dec, radius in degrees): HIGHPM_DEEP_ARCSEC around our position, and around the middle of
    the segment our position moved back to mjd_to takes (_segment), covering both ends + HIGHPM_DEEP_ARCSEC + our
    motion over pad_yr (a catalogue whose epoch varies by that much)."""
    from .sky import angsep
    ra_a, dec_a, ra_b, dec_b = _segment(rows, ke, kn, mjd_to)
    ra_m, dec_m = (ra_a + ra_b) / 2.0, (dec_a + dec_b) / 2.0  # fine for ″-scale segments
    r_m = (angsep(ra_a, dec_a, ra_b, dec_b) / 2.0 + C.HIGHPM_DEEP_ARCSEC / 3600.0
           + rows["pm"].to_numpy(np.float64) * pad_yr / 3.6e6)
    n = len(rows)
    ra = np.column_stack([rows["ra"].to_numpy(np.float64), ra_m]).ravel()
    dec = np.column_stack([rows["dec"].to_numpy(np.float64), dec_m]).ravel()
    r = np.column_stack([np.full(n, C.HIGHPM_DEEP_ARCSEC / 3600.0), r_m]).ravel()
    return ra, dec, r


def deep_gaia_query(rows: pd.DataFrame, ke, kn, tap=None) -> pd.DataFrame:
    """Gaia DR3 sources of any G (refcat's columns, parse_gaia) within HIGHPM_DEEP_ARCSEC of each row's position or of
    the segment its position moved back to 2016.0 takes (_segment): one ESA query per HIGHPM_DEEP_BATCH rows, two
    circles a row. Raises when the service fails."""
    from skyshift_core import GAIA_EPOCH_MJD

    from .refcat import GAIA_SELECT, parse_gaia
    tap = tap or deep_gaia_tap
    ra, dec, r = _circles(rows, ke, kn, GAIA_EPOCH_MJD)
    circles = [f"1=CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {a:.7f}, {d:.7f}, {x:.7f}))" for a, d, x in zip(ra, dec, r)]
    step = 2 * C.HIGHPM_DEEP_BATCH
    parts = [parse_gaia(tap(f"SELECT {GAIA_SELECT} FROM gaiadr3.gaia_source WHERE {' OR '.join(circles[i:i + step])}"))
             for i in range(0, len(circles), step)]
    g = pd.concat(parts, ignore_index=True) if parts else parse_gaia(pd.DataFrame({"source_id": []}))
    return g.drop_duplicates("ref_id").reset_index(drop=True)


ALLWISE_DEEP_SELECT = "a.designation, a.ra, a.dec, a.w1mpro, a.w2mpro, a.w1mjdmean"


def deep_wise_query(rows: pd.DataFrame, ke, kn, irsa=None) -> dict[str, pd.DataFrame]:
    """CatWISE2020 (refcat's columns, parse_catwise: position at MJD 57170 with its own motion) and AllWISE (position at
    its mean W1 epoch, no usable motion: ref_id `allwise:<designation>`, ra, dec, epoch_mjd, w1_mag, w2_mag) around each
    row's position and the segment back to that catalogue's epoch (_circles), from IRSA: one query per catalogue per
    HIGHPM_DEEP_IRSA_ROWS rows. Raises when the service fails."""
    from astropy.table import Table

    from .refcat import CATWISE_PM_EPOCH_MJD, CATWISE_SELECT, _col, _df, parse_catwise
    irsa = irsa or deep_irsa_tap
    out: dict[str, pd.DataFrame] = {}
    cw_select = ", ".join("a." + c.strip() for c in CATWISE_SELECT.split(","))
    for cat, table, select, mjd_to, pad in (("catwise", "catwise_2020", cw_select, CATWISE_PM_EPOCH_MJD, 0.0),
                                            ("allwise", "allwise_p3as_psd", ALLWISE_DEEP_SELECT, C.HIGHPM_DEEP_ALLWISE_MJD,
                                             C.HIGHPM_DEEP_ALLWISE_PAD_YR)):
        parts = []
        for i0 in range(0, len(rows), C.HIGHPM_DEEP_IRSA_ROWS):
            sub = rows.iloc[i0:i0 + C.HIGHPM_DEEP_IRSA_ROWS]
            ra, dec, r = _circles(sub, ke[i0:i0 + len(sub)], kn[i0:i0 + len(sub)], mjd_to, pad)
            u = Table({"i": np.arange(len(ra)), "ra": ra, "dec": dec, "r": r})
            parts.append(_df(irsa(f"SELECT {select} FROM {table} a, TAP_UPLOAD.u u "
                                  "WHERE 1=CONTAINS(POINT('ICRS', a.ra, a.dec), CIRCLE('ICRS', u.ra, u.dec, u.r))", {"u": u})))
        df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
        if cat == "catwise":
            df = parse_catwise(df) if len(df) else parse_catwise(pd.DataFrame({"source_name": []}))
        else:
            n = len(df)
            ep = _col(df, "w1mjdmean")
            df = pd.DataFrame({
                "ref_id": ("allwise:" + df["designation"].astype(str).str.strip()).tolist() if n else [],
                "ra": _col(df, "ra"), "dec": _col(df, "dec"),
                "epoch_mjd": np.where(np.isfinite(ep), ep, C.HIGHPM_DEEP_ALLWISE_MJD),
                "w1_mag": _col(df, "w1mpro"), "w2_mag": _col(df, "w2mpro")})
        out[cat] = df.drop_duplicates("ref_id").reset_index(drop=True)
    return out


def _track(r, ke_i: float, kn_i: float, ra, dec, epoch_mjd) -> tuple[np.ndarray, ...]:
    """For catalogue entries seen at their own epochs (no motion of their own): (res, now, w, dt). res = distance (″) from
    the entry to where our motion, less a parallax leak ϖ·k (0 <= ϖ <= HIGHPM_CW_PLX_PER_PM × |pm|, the one that fits
    best: w, mas), puts us at the entry's epoch; now = its distance from our position; dt = entry epoch − ours (yr)."""
    e0, n0 = tangent_offset_arcsec(r["ra"], r["dec"], np.asarray(ra, np.float64), np.asarray(dec, np.float64))
    dt = (np.asarray(epoch_mjd, np.float64) - r["mjd"]) / DAYS_PER_JYEAR
    r0e, r0n = e0 - r["pmra"] * dt / 1000.0, n0 - r["pmdec"] * dt / 1000.0
    ve, vn = ke_i * dt / 1000.0, kn_i * dt / 1000.0  # ″ per mas of parallax
    vv = ve * ve + vn * vn
    with np.errstate(invalid="ignore", divide="ignore"):
        w = np.where(vv > 0, np.clip(-(r0e * ve + r0n * vn) / vv, 0.0, C.HIGHPM_CW_PLX_PER_PM * abs(r["pm"])), 0.0)
    return np.hypot(r0e + w * ve, r0n + w * vn), np.hypot(e0, n0), w, dt


def _track_verdicts(r, ke_i, kn_i, ra, dec, epoch_mjd, mag_ok) -> tuple[np.ndarray, np.ndarray, np.ndarray, tuple]:
    """(on, still, mag_ok, _track(...)) per catalogue entry seen at its own epoch. on: on our track (res within
    hypot(HIGHPM_DEEP_MATCH_ARCSEC, 2 × our pm error × |dt|)) and not at our position (now > HIGHPM_DEEP_MATCH_ARCSEC).
    still: at our position (now <= HIGHPM_DEEP_STILL_ARCSEC) while our motion, whatever the leak, puts us at least
    HIGHPM_DEEP_STILL_ARCSEC + max(HIGHPM_DEEP_MATCH_ARCSEC, 3 × our pm error × |dt|) away at its epoch: the catalogue saw
    a source here, then, where ours should not have been. Both need a matching brightness where known (mag_ok)."""
    t = _track(r, ke_i, kn_i, ra, dec, epoch_mjd)
    res, now, _, dt = t
    pe = abs(float(np.nan_to_num(r["pm_err"]))) * np.abs(dt) / 1000.0
    on = (res <= np.hypot(C.HIGHPM_DEEP_MATCH_ARCSEC, 2.0 * pe)) & (now > C.HIGHPM_DEEP_MATCH_ARCSEC) & mag_ok
    still = ((now <= C.HIGHPM_DEEP_STILL_ARCSEC)
             & (res >= C.HIGHPM_DEEP_STILL_ARCSEC + np.maximum(C.HIGHPM_DEEP_MATCH_ARCSEC, 3.0 * pe)) & mag_ok)
    return on, still, mag_ok, t


def _mag_ok(ref_ab: np.ndarray, our_ab: float, max_dmag: float, one_sided: bool = False) -> np.ndarray:
    """A catalogue magnitude (AB) agrees with ours where both are known: within max_dmag, or (one_sided: Gaia G against our
    D1, the colour of a red star unknown) not brighter than ours by more than max_dmag."""
    d = np.asarray(ref_ab, np.float64) - our_ab
    bad = (d < -max_dmag) if one_sided else (np.abs(d) > max_dmag)
    return ~np.nan_to_num(bad, nan=False).astype(bool)


def _round(d: dict | None) -> dict | None:
    if d is None:
        return None
    return {k: (round(v, 2) if isinstance(v, float) and np.isfinite(v) else (None if isinstance(v, float) else v))
            for k, v in d.items()}


def deep_gaia_match(rows: pd.DataFrame, ke, kn, gaia: pd.DataFrame, our_ab: np.ndarray | None = None) -> list[dict | None]:
    """Per row, its deep Gaia counterpart (kind):
    - `pm`: a source with its own motion that moves (from 2016.0) to within HIGHPM_DEEP_MATCH_ARCSEC of us at our epoch;
      one whose motion agrees with ours (gaia_check, parallax leak) first, then the nearest.
    - `position`: a source Gaia gives a position only (no pm: 2-parameter solution), at 2016.0 on our track back to then
      (_track_verdicts `on`; G not brighter than our D1 by more than HIGHPM_DEEP_TMASS_MAX_DMAG). Before 16 Nov such a
      source counted as still and could only match at our position: 9 real fast stars of round 1 stayed "new" with their
      Gaia counterpart 3–5″ back along the track, and a fixed pair end got status gaia from a still G 21 source.
    - `still`: a position-only source at our position while our motion puts us far from it in 2016.0 (`still`).
    Order: an agreeing pm source, an on-track position, a disagreeing pm source, a still one. None without one.
    our_ab: our AB magnitudes per row and detector (n × 6, NaN where unknown)."""
    from skyshift_core import GAIA_EPOCH_MJD, propagate

    from .sky import angsep
    out: list[dict | None] = []
    if not len(gaia):
        return [None] * len(rows)
    has_pm = np.isfinite(gaia["pmra"].to_numpy(np.float64)) & np.isfinite(gaia["pmdec"].to_numpy(np.float64))
    gp, g0 = gaia[has_pm].reset_index(drop=True), gaia[~has_pm].reset_index(drop=True)
    d1 = our_ab[:, 0] if our_ab is not None else np.full(len(rows), np.nan)
    for i in range(len(rows)):
        r = rows.iloc[[i]]
        pm_hit = None
        if len(gp):
            pra, pde = propagate(gp["ra"].to_numpy(), gp["dec"].to_numpy(), gp["pmra"].to_numpy(), gp["pmdec"].to_numpy(),
                                 float(r["mjd"].iat[0]), epoch_mjd=GAIA_EPOCH_MJD)
            sep = angsep(pra, pde, r["ra"].iat[0], r["dec"].iat[0]) * 3600.0
            near = np.flatnonzero(sep <= C.HIGHPM_DEEP_MATCH_ARCSEC)
            if near.size:
                g = gp.iloc[near]
                rr = pd.concat([r] * len(near), ignore_index=True)
                has, dis, pred = gaia_check(rr, g["pmra"].to_numpy(np.float64), g["pmdec"].to_numpy(np.float64),
                                            g["parallax"].to_numpy(np.float64), np.full(len(near), ke[i]),
                                            np.full(len(near), kn[i]), predicted=True)
                j = int(np.lexsort((sep[near], ~(has & ~dis)))[0])
                pm_hit = {"kind": "pm", "ref_id": str(g["ref_id"].iat[j]), "gaia_id": int(str(g["ref_id"].iat[j]).split(":")[-1]),
                          "g": float(g["g_mag"].iat[j]), "pmra": float(g["pmra"].iat[j]), "pmdec": float(g["pmdec"].iat[j]),
                          "parallax": float(g["parallax"].iat[j]), "sep_arcsec": float(sep[near][j]),
                          "has_pm": bool(has[j]), "agrees": bool(has[j] and not dis[j]), "predicted_pm": float(pred[j]),
                          "epoch_mjd": float(r["mjd"].iat[0])}
        pos_hit = still_hit = None
        if len(g0):
            ok = _mag_ok(g0["g_mag"].to_numpy(np.float64), float(d1[i]), C.HIGHPM_DEEP_TMASS_MAX_DMAG, one_sided=True)
            on, still, _, (res, now, w, _) = _track_verdicts(r.iloc[0], ke[i], kn[i], g0["ra"], g0["dec"],
                                                              np.full(len(g0), GAIA_EPOCH_MJD), ok)
            for kind, m, key in (("position", on, res), ("still", still, now)):
                if m.any():
                    j = int(np.flatnonzero(m)[np.argmin(key[m])])
                    hit = {"kind": kind, "ref_id": str(g0["ref_id"].iat[j]), "gaia_id": int(str(g0["ref_id"].iat[j]).split(":")[-1]),
                           "g": float(g0["g_mag"].iat[j]), "pmra": float("nan"), "pmdec": float("nan"),
                           "parallax": float("nan"), "sep_arcsec": float(res[j]), "offset_now_arcsec": float(now[j]),
                           "parallax_used_mas": float(w[j]), "has_pm": False, "agrees": False, "epoch_mjd": GAIA_EPOCH_MJD}
                    if kind == "position":
                        pos_hit = hit
                    else:
                        still_hit = hit
        if pm_hit is not None and pm_hit["agrees"]:
            out.append(pm_hit)
        else:
            out.append(pos_hit or pm_hit or still_hit)
    return out


def deep_position_match(rows: pd.DataFrame, ke, kn, ref: pd.DataFrame, our_ab: np.ndarray, mag_pairs) -> list[dict]:
    """Per row, {"on": the catalogue entry (seen at its own epoch, no motion) nearest our track, "still": the nearest one
    at our position that our motion says we had left} (_track_verdicts; None where none). mag_pairs: [(ref magnitude
    column, AB offset, our detector index)]: the first pair known on both sides judges the brightness, within
    HIGHPM_DEEP_TMASS_MAX_DMAG."""
    from .sky import angsep
    out = [{"on": None, "still": None} for _ in range(len(rows))]
    if not len(ref) or not len(rows):
        return out
    rra, rde, rep = (ref[c].to_numpy(np.float64) for c in ("ra", "dec", "epoch_mjd"))
    for i in range(len(rows)):
        r = rows.iloc[i]
        dt_max = float(np.nanmax(np.abs(rep - r["mjd"]))) / DAYS_PER_JYEAR if np.isfinite(rep).any() else 0.0
        # the track (any leak up to |k| = 6), 2 × our pm error over it, the match radius
        reach = ((abs(r["pm"]) * (1.0 + C.HIGHPM_CW_PLX_PER_PM * 6.0) + 2.0 * abs(np.nan_to_num(r["pm_err"])))
                 / 1000.0 * dt_max + 2.0 * C.HIGHPM_DEEP_ARCSEC)
        near = np.flatnonzero(angsep(rra, rde, r["ra"], r["dec"]) * 3600.0 <= reach)
        if not near.size:
            continue
        sub = ref.iloc[near]
        ok = np.ones(len(near), bool)
        decided = np.zeros(len(near), bool)
        for col, off, det in mag_pairs:
            m = sub[col].to_numpy(np.float64) + off if col in sub else np.full(len(near), np.nan)
            known = np.isfinite(m) & np.isfinite(our_ab[i, det]) & ~decided
            ok[known] = np.abs(m[known] - our_ab[i, det]) <= C.HIGHPM_DEEP_TMASS_MAX_DMAG
            decided |= known
        on, still, _, (res, now, w, _) = _track_verdicts(r, ke[i], kn[i], rra[near], rde[near], rep[near], ok)
        for key, m, by in (("on", on, res), ("still", still, now)):
            if m.any():
                j = int(np.flatnonzero(m)[np.argmin(by[m])])
                out[i][key] = {"ref_id": str(sub["ref_id"].iat[j]), "sep_arcsec": round(float(res[j]), 2),
                               "offset_now_arcsec": round(float(now[j]), 1), "parallax_used_mas": round(float(w[j]), 1),
                               "epoch_mjd": round(float(rep[near][j]), 1),
                               **{c: (None if c not in sub or not np.isfinite(sub[c].iat[j]) else round(float(sub[c].iat[j]) + o, 2))
                                  for c, o in ((mp[0], mp[1]) for mp in mag_pairs)}}
    return out


def deep_tmass_match(rows: pd.DataFrame, ke, kn, refcat: pd.DataFrame, d2_ab: np.ndarray) -> list[dict | None]:
    """Per row, a 2MASS source (refcat) where our motion, less a parallax leak ϖ·k (0 <= ϖ <= HIGHPM_CW_PLX_PER_PM × |pm|,
    the best one), puts us at its own epoch (_track_verdicts `on`), not at our own position, and with its J (AB) within
    HIGHPM_DEEP_TMASS_MAX_DMAG of our D2 flux (AB) where both are known; None without one."""
    tm = refcat[(refcat["cat"].to_numpy() == "tmass") & np.isfinite(refcat["epoch_mjd"].to_numpy(np.float64))]
    if not len(tm) or not len(rows):
        return [None] * len(rows)
    ab = np.full((len(rows), NDET), np.nan)
    ab[:, 1] = d2_ab
    m = deep_position_match(rows, ke, kn, tm.assign(j=tm["j"] if "j" in tm else np.nan), ab, [("j", 0.91, 1)])
    return [None if x["on"] is None else {**{k: v for k, v in x["on"].items() if k != "j"}, "j_ab": x["on"].get("j")}
            for x in m]


def deep_wise_match(rows: pd.DataFrame, ke, kn, wise: dict[str, pd.DataFrame], our_ab: np.ndarray,
                    plx_per_pm) -> tuple[list[dict | None], list[dict]]:
    """Per row: (CatWISE2020: the entry its own motion (from MJD 57170) brings within HIGHPM_DEEP_MATCH_ARCSEC of us, with
    `agrees` from catwise_check, an agreeing one first; AllWISE: deep_position_match on W1 (D4) or W2 (D6)).
    CatWISE's disagreement is only reported: CatWISE splits a fast mover into several entries with wrong motions
    (Gliese 570 D: three entries, 2015–2017 epochs), so only its agreement counts."""
    from skyshift_core import propagate

    from .sky import angsep
    cw = wise.get("catwise", pd.DataFrame())
    cw_out: list[dict | None] = [None] * len(rows)
    if len(cw):
        cwp = cw[np.isfinite(cw["pmra"].to_numpy(np.float64))].reset_index(drop=True)
        for i in range(len(rows)):
            r = rows.iloc[[i]]
            if not len(cwp):
                break
            pra, pde = propagate(cwp["ra"].to_numpy(), cwp["dec"].to_numpy(), cwp["pmra"].to_numpy(), cwp["pmdec"].to_numpy(),
                                 float(r["mjd"].iat[0]), epoch_mjd=cwp["epoch_mjd"].to_numpy(np.float64))
            sep = angsep(pra, pde, r["ra"].iat[0], r["dec"].iat[0]) * 3600.0
            near = np.flatnonzero(sep <= C.HIGHPM_DEEP_MATCH_ARCSEC)
            if not near.size:
                continue
            g = cwp.iloc[near]
            rr = pd.concat([r] * len(near), ignore_index=True)
            cerr = np.hypot(g["pmra_err"].to_numpy(np.float64), g["pmdec_err"].to_numpy(np.float64)) / np.sqrt(2)
            has, ok = catwise_check(rr, g["pmra"].to_numpy(np.float64), g["pmdec"].to_numpy(np.float64), cerr,
                                    np.full(len(near), ke[i]), np.full(len(near), kn[i]), np.full(len(near), plx_per_pm[i]))
            j = int(np.lexsort((sep[near], ~ok))[0])
            cw_out[i] = {"ref_id": str(g["ref_id"].iat[j]), "name": str(g["ref_id"].iat[j]).split(":", 1)[-1],
                         "pmra": float(g["pmra"].iat[j]), "pmdec": float(g["pmdec"].iat[j]), "sep_arcsec": float(sep[near][j]),
                         "w1": float(g["w1_mag"].iat[j]), "agrees": bool(ok[j]), "epoch_mjd": float(g["epoch_mjd"].iat[j])}
    aw = deep_position_match(rows, ke, kn, wise.get("allwise", pd.DataFrame()), our_ab,
                             [("w1_mag", 2.699, 3), ("w2_mag", 3.339, 5)])
    return cw_out, aw


DEEP_ROWS_COUNTERPARTS = ("", "gaia_crossid")  # deep_check looks again at vetted rows with these counterparts


def deep_check(cands: pd.DataFrame, vstats: pd.DataFrame | None, refcat: pd.DataFrame, d2_ab: pd.Series | None = None,
               tap=None, irsa=None, d_ab: pd.DataFrame | None = None) -> tuple[pd.DataFrame, dict]:
    """Before a vetted row counts as `new` (and for a vetted `gaia` row whose crossid Gaia match has no motion,
    counterpart `gaia_crossid`): Gaia DR3 with no magnitude cut (deep_gaia_query, deep_gaia_match), CatWISE2020 and AllWISE
    from IRSA (deep_wise_query, deep_wise_match) and 2MASS from refcat (deep_tmass_match), all following our motion back
    in time. Outcome (columns status, reason, counterpart*, gaia_ref_id, gaia_id, catwise_name, catwise_pm_agree):
    - Gaia `pm` counterpart: status `gaia`, counterpart `gaia_pm`, reasons `gaia_pm_disagrees` / `gaia_slow` as compute.
    - Gaia `position` counterpart (no Gaia motion, its 2016.0 position on our track): status `gaia`, counterpart
      `gaia_position`; still vetted (Gaia lists the star, not its motion: ours is the motion).
    - else CatWISE agreeing (catwise_pm_agree, catwise_name, counterpart `catwise_pm`), 2MASS on our track (status
      `2mass`, counterpart `tmass_position`), AllWISE on our track (status `allwise` unless 2mass, counterpart
      `allwise_position`): each a catalogue that lists the object; vetted stays as it was.
    - with none of those: a Gaia position-only source at our position that our motion says we had left by 2016.0 →
      status `gaia`, reason `gaia_still` (counterpart `gaia_still`); else such an AllWISE source (2010) → reason
      `allwise_still` (counterpart `allwise_still`). Never vetted: the catalogue saw a source where ours is now, where our
      motion says ours was not.
    A failed query keeps what it would have changed (deep_gaia_checked / deep_wise_checked false); never raises.
    d_ab: our AB magnitudes by source_id, columns d1..d6 (d2_ab: D2 alone, older callers)."""
    sel = (cands["vetted"].to_numpy(bool) & ((cands["status"] == "new").to_numpy()
                                             | np.isin(cands.get("counterpart", pd.Series("", index=cands.index)).fillna("")
                                                       .astype(str).to_numpy(), ["gaia_crossid"]))) if len(cands) else np.zeros(0, bool)
    idx = np.flatnonzero(sel)
    rep: dict = {"rows": int(idx.size), "deep_gaia_checked": True, "deep_wise_checked": True, "gaia_matched": 0,
                 "gaia_agrees": 0, "gaia_disagrees": 0, "gaia_position": 0, "gaia_still": 0, "catwise_agrees": 0,
                 "catwise_disagrees_ignored": 0, "tmass_matched": 0, "allwise_matched": 0, "allwise_still": 0, "matches": []}
    if not idx.size:
        return cands, rep
    cands = cands.copy()
    for c, fill in (("counterpart", ""), ("counterpart_id", "")):
        if c not in cands:
            cands[c] = fill
    for c in ("counterpart_sep_arcsec", "counterpart_epoch_mjd"):
        if c not in cands:
            cands[c] = np.nan
    rows = cands.iloc[idx].reset_index(drop=True)
    ke, kn = _leak(rows, vstats)
    sids = rows["source_id"].to_numpy(np.int64)
    our = np.full((len(rows), NDET), np.nan)
    if d_ab is not None and len(d_ab):
        our = d_ab.reindex(sids)[[f"d{k + 1}" for k in range(NDET)]].to_numpy(np.float64)
    elif d2_ab is not None:
        our[:, 1] = d2_ab.reindex(sids).to_numpy(np.float64)
    try:
        gm = deep_gaia_match(rows, ke, kn, deep_gaia_query(rows, ke, kn, tap), our)
    except Exception as e:  # noqa: BLE001 - the network: keep the rows new, say so
        L.warning("highpm: deep Gaia check failed (%s): %d vetted rows stay as they are", e, idx.size)
        rep.update({"deep_gaia_checked": False, "error": repr(e)[:200]})
        gm = [None] * len(rows)
    plx_per_pm = np.full(len(rows), C.HIGHPM_CW_PLX_PER_PM)
    try:
        cwm, awm = deep_wise_match(rows, ke, kn, deep_wise_query(rows, ke, kn, irsa), our, plx_per_pm)
    except Exception as e:  # noqa: BLE001 - the network
        L.warning("highpm: deep CatWISE/AllWISE check failed (%s)", e)
        rep.update({"deep_wise_checked": False, "wise_error": repr(e)[:200]})
        cwm, awm = [None] * len(rows), [{"on": None, "still": None}] * len(rows)
    tmm = deep_tmass_match(rows, ke, kn, refcat, our[:, 1])
    col = {c: np.array(cands[c], dtype=object) for c in ("status", "gaia_ref_id", "reason", "catwise_name", "counterpart",
                                                          "counterpart_id")}
    gid = np.array(cands["gaia_id"], dtype=np.int64)
    agree = np.array(cands["catwise_pm_agree"], dtype=bool)
    csep = np.array(cands["counterpart_sep_arcsec"], dtype=np.float64)
    cep = np.array(cands["counterpart_epoch_mjd"], dtype=np.float64)

    def add_reason(k, why):
        col["reason"][k] = why if not col["reason"][k] else f"{col['reason'][k]},{why}"

    def counterpart(k, kind, m):
        col["counterpart"][k], col["counterpart_id"][k] = kind, m["ref_id"]
        csep[k], cep[k] = m.get("sep_arcsec", np.nan), m.get("epoch_mjd", np.nan)

    for i, k in enumerate(idx):
        g, cw, aw, t = gm[i], cwm[i], awm[i], tmm[i]
        rep["matches"].append({"source_id": int(rows["source_id"].iat[i]), "method": str(rows["method"].iat[i]),
                               "pm": round(float(rows["pm"].iat[i]), 1), "gaia": _round(g), "catwise": _round(cw),
                               "tmass": t, "allwise": aw["on"], "allwise_still": aw["still"]})
        if g is not None and g["kind"] in ("pm", "position"):
            col["status"][k], col["gaia_ref_id"][k], gid[k] = "gaia", g["ref_id"], g["gaia_id"]
            rep["gaia_matched"] += 1
            if g["kind"] == "position":
                rep["gaia_position"] += 1
                counterpart(k, "gaia_position", g)
            else:
                counterpart(k, "gaia_pm", g)
                if not g["agrees"]:
                    add_reason(k, "gaia_pm_disagrees")
                    rep["gaia_disagrees"] += 1
                else:
                    rep["gaia_agrees"] += 1
                    if g["predicted_pm"] < C.HIGHPM_FIT_MIN_MAS_YR:
                        add_reason(k, "gaia_slow")
            continue
        found = False
        if cw is not None and cw["agrees"]:
            agree[k], found = True, True
            if not col["catwise_name"][k]:
                col["catwise_name"][k] = cw["name"]
            counterpart(k, "catwise_pm", cw)
            rep["catwise_agrees"] += 1
        elif cw is not None:
            rep["catwise_disagrees_ignored"] += 1
        if t is not None:
            col["status"][k], found = "2mass", True
            rep["tmass_matched"] += 1
            if col["counterpart"][k] in DEEP_ROWS_COUNTERPARTS:
                counterpart(k, "tmass_position", t)
        if aw["on"] is not None:
            found = True
            rep["allwise_matched"] += 1
            if col["status"][k] == "new":
                col["status"][k] = "allwise"
            if col["counterpart"][k] in DEEP_ROWS_COUNTERPARTS:
                counterpart(k, "allwise_position", aw["on"])
        if found:
            continue
        if g is not None and g["kind"] == "still":
            col["status"][k], col["gaia_ref_id"][k], gid[k] = "gaia", g["ref_id"], g["gaia_id"]
            add_reason(k, "gaia_still")
            counterpart(k, "gaia_still", g)
            rep["gaia_still"] += 1
        elif aw["still"] is not None:
            add_reason(k, "allwise_still")
            counterpart(k, "allwise_still", aw["still"])
            rep["allwise_still"] += 1
    for c, v in col.items():
        cands[c] = v.astype(str)
    cands["gaia_id"], cands["catwise_pm_agree"] = gid, agree
    cands["counterpart_sep_arcsec"], cands["counterpart_epoch_mjd"] = csep.astype(np.float32), cep
    cands["vetted"] = cands["reason"].to_numpy(object) == ""
    return _ordered(cands), rep


VISIT_COLUMNS = ["method", "source_id", "source_id_b", "visit", "mjd", "ra", "dec", "ra_err_arcsec", "dec_err_arcsec",
                 "n_dots"]


def visit_positions(cands: pd.DataFrame, src: pd.DataFrame, astv: pd.DataFrame | None) -> pd.DataFrame:
    """highpm_visits.parquet: each candidate's position per visit, keyed like the candidate (method, source_id,
    source_id_b). Fits: astrometry_visits.parquet (the visit's own position in the fit's frame, relative errors; tables
    written before 16 Nov have none: no rows). Pairs: each end's source position, pos_err_arcsec, visit and mean mjd."""
    parts = []
    if len(cands):
        f = cands[cands["method"] == "fit"][["method", "source_id", "source_id_b"]]
        if astv is not None and len(astv) and len(f):
            parts.append(f.merge(astv, on="source_id"))
        p = cands[cands["method"] == "pair"][["method", "source_id", "source_id_b"]]
        if len(p):
            sx = src.set_index("source_id")
            for end in ("source_id", "source_id_b"):
                r = sx.reindex(p[end].to_numpy(np.int64))
                err = r["pos_err_arcsec"].to_numpy(np.float32) if "pos_err_arcsec" in r else np.full(len(p), np.nan, np.float32)
                parts.append(p.assign(visit=r["visit"].to_numpy(), mjd=r["mjd"].to_numpy(np.float64),
                                      ra=r["ra"].to_numpy(np.float64), dec=r["dec"].to_numpy(np.float64),
                                      ra_err_arcsec=err, dec_err_arcsec=err, n_dots=np.int32(-1)))
    if not parts:
        return pd.DataFrame({c: pd.Series(dtype=t) for c, t in zip(VISIT_COLUMNS, ("object", "int64", "int64", "int16", "float64",
                                                                                   "float64", "float64", "float32", "float32",
                                                                                   "int32"))})
    out = pd.concat(parts, ignore_index=True)[VISIT_COLUMNS]
    return out.astype({"source_id": np.int64, "source_id_b": np.int64, "visit": np.int16, "n_dots": np.int32,
                       "ra_err_arcsec": np.float32, "dec_err_arcsec": np.float32}).sort_values(
        ["source_id", "source_id_b", "mjd"], kind="stable").reset_index(drop=True)


def run(store: Store, zone: C.Zone, workers: int = 16, tap=None, irsa=None) -> dict:
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
    vstats = effective_leak(vstats, ast)  # the leak left in astrometry's pm (0 where it took parallax out)
    del members
    cw_err = catwise_pm_err(store, zone)
    ctrl = control_separations(src, fps)
    spec = np.stack(src["flux_ujy"].to_numpy()).astype(np.float64) if len(src) else np.zeros((0, NDET * NCH_DET))
    dflux = detector_fluxes(spec)
    real = pair_movers(src, dflux, fps)
    bins = pair_vs_control(real["sep_arcsec"].to_numpy(np.float64) if len(real) else np.zeros(0), ctrl)
    cands = compute(src, fps, crossids, ast, bins, vstats, cw_err)
    refcat = load_refcat(store, zone)
    deep: dict = {"enabled": False}
    if C.HIGHPM_DEEP_CHECK:
        with np.errstate(invalid="ignore", divide="ignore"):
            ab = np.where(dflux > 0, -2.5 * np.log10(dflux / 3631e6), np.nan)
        d_ab = pd.DataFrame(ab, columns=[f"d{k + 1}" for k in range(NDET)], index=src["source_id"].to_numpy(np.int64))
        cands, deep = deep_check(cands, vstats, refcat, tap=tap, irsa=irsa, d_ab=d_ab)
        deep = {"enabled": True, **deep}
    from . import fragments  # bright_fragment, duplicate → reason `fragment` (never vetted); blend → not new (Gaia lists the stars)
    cands, frag_st = fragments.apply_highpm(cands, fragments.load(store, zone))
    store.write_parquet(results_rel(zone.name, "highpm_candidates.parquet"), cands)
    astv_rel = results_rel(zone.name, "astrometry_visits.parquet")
    astv = store.read_parquet(astv_rel) if store.exists(astv_rel) else None
    hv = visit_positions(cands, src, astv)
    store.write_parquet(results_rel(zone.name, "highpm_visits.parquet"), hv)
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
           "deep_check": deep, "fragments": frag_st,
           "visit_rows": int(len(hv)), "astrometry_visits": astv is not None,
           "recall": recall(cands, crossids, refcat, zone, mjd_ref)}
    store.write_json(results_rel(zone.name, "highpm_report.json"), rep)
    st = {k: v for k, v in rep.items() if k != "recall"}
    st["recall"] = {k: v for k, v in rep["recall"].items() if k != "stars"}
    st["wall_s"] = round(time.time() - t0, 1)
    L.info("highpm %s: %s", zone.name, st)
    return st
