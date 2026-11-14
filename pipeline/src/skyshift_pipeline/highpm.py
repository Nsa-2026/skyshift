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
motion agrees with ours (HIGHPM_CATWISE_PM_TOL) is a bonus (`catwise_pm_agree`). `red_ratio` =
D6 / D2 flux (4.4–5.0 µm over 1.1–1.6 µm): large for cold objects (methane, CO absorption bluewards).

Control (highpm_report.json `control_pairs`): the same pairing with one visit's sources moved
HIGHPM_CONTROL_OFFSET_ARCSEC: the number of chance pairs to expect among the "pair" candidates.
Recall check (highpm_report.json): Gaia DR3 stars faster than HIGHPM_RECALL_MAS_YR in the zone, how
many we detected as a source, how many came out as candidates.

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
           "gaia_ref_id", "gaia_id", "catwise_name", "catwise_pm_agree", "score"]


def source_visits(store: Store, zone: C.Zone) -> pd.DataFrame:
    """Per source: its visit (-1 if seen in several) and mean mjd, from members + the photo list."""
    m = store.read_parquet(results_rel(zone.name, "members.parquet"), columns=["source_id", "obs_id", "detector"])
    p = store.read_parquet(photolist_rel(zone.name), columns=["obs_id", "detector", "mjd", "visit"])
    key = pd.MultiIndex.from_arrays([p["obs_id"].astype(str), p["detector"].astype(np.int64)])
    pos = key.get_indexer(pd.MultiIndex.from_arrays([m["obs_id"].astype(str), m["detector"].astype(np.int64)]))
    ok = pos >= 0
    df = pd.DataFrame({"source_id": m["source_id"].to_numpy()[ok], "mjd": p["mjd"].to_numpy()[pos[ok]],
                       "visit": p["visit"].to_numpy()[pos[ok]]})
    g = df.groupby("source_id").agg(mjd=("mjd", "mean"), vmin=("visit", "min"), vmax=("visit", "max"))
    g["visit"] = np.where(g["vmin"] == g["vmax"], g["vmin"], -1).astype(np.int64)
    return g[["mjd", "visit"]].reset_index()


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


def control_pairs(src: pd.DataFrame) -> int:
    """Chance pairs: the same pairing with every even visit's single-visit sources moved
    HIGHPM_CONTROL_OFFSET_ARCSEC east (no real mover survives that; coverage not tested)."""
    from .sky import offset_radec
    c = src.copy()
    even = (c["visit"].to_numpy() >= 0) & (c["visit"].to_numpy() % 2 == 0)
    r, d = offset_radec(c["ra"].to_numpy()[even], c["dec"].to_numpy()[even], C.HIGHPM_CONTROL_OFFSET_ARCSEC, 0.0)
    c.loc[even, "ra"], c.loc[even, "dec"] = r, d
    spec = np.stack(c["flux_ujy"].to_numpy()).astype(np.float64) if len(c) else np.zeros((0, NDET * NCH_DET))
    return int(len(pair_movers(c, detector_fluxes(spec), None)))


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
    })


def compute(src: pd.DataFrame, footprints_by_visit, crossids: pd.DataFrame | None, ast: pd.DataFrame | None) -> pd.DataFrame:
    spec = np.stack(src["flux_ujy"].to_numpy()).astype(np.float64) if len(src) else np.zeros((0, NDET * NCH_DET))
    dflux = detector_fluxes(spec)
    pairs = pair_movers(src, dflux, footprints_by_visit)
    fits = fit_movers(ast)
    if len(fits):
        pos = pd.Index(src["source_id"].to_numpy(np.int64)).get_indexer(fits["source_id"].to_numpy(np.int64))
        okp = pos >= 0
        red = np.full(len(fits), np.nan)
        red[okp] = dflux[pos[okp], 5] / dflux[pos[okp], 1]
        fits["red_ratio"] = red.astype(np.float32)
    out = pd.concat([x for x in (pairs, fits) if len(x)], ignore_index=True) if (len(pairs) or len(fits)) else pd.DataFrame(columns=[*COLUMNS])
    if not len(out):
        return pd.DataFrame({c: pd.Series(dtype="float64") for c in COLUMNS})
    # Gaia and CatWISE, from either side
    gref = np.full(len(out), "", dtype=object)
    gid = np.full(len(out), -1, np.int64)
    cw = np.full(len(out), "", dtype=object)
    agree = np.zeros(len(out), bool)
    if crossids is not None and len(crossids):
        x = crossids.set_index("source_id")
        for col in ("source_id", "source_id_b"):
            r = x.reindex(out[col].to_numpy(np.int64))
            g = r["gaia_ref_id"].fillna("").astype(str).to_numpy()
            fill = (gref == "") & (g != "")
            gref[fill] = g[fill]
            gid[fill] = r["gaia_id"].to_numpy()[fill]
            nm = r["catwise_name"].fillna("").astype(str).to_numpy()
            cpm_ra, cpm_de = r["catwise_pmra"].to_numpy(np.float64), r["catwise_pmdec"].to_numpy(np.float64)
            dpm = np.hypot(cpm_ra - out["pmra"].to_numpy(np.float64), cpm_de - out["pmdec"].to_numpy(np.float64))
            tol = np.maximum(C.HIGHPM_CATWISE_PM_TOL * out["pm"].to_numpy(np.float64), 100.0)
            agree |= np.isfinite(dpm) & (dpm <= tol)
            cw[(cw == "") & (nm != "")] = nm[(cw == "") & (nm != "")]
    out["gaia_ref_id"] = gref.astype(str)
    out["gaia_id"] = gid
    out["catwise_name"] = cw.astype(str)
    out["catwise_pm_agree"] = agree
    out["status"] = np.where(out["gaia_ref_id"] == "", "new", "gaia")
    # new first, then pairs before fits, then best score
    out["_r"] = (out["status"] != "new").astype(int) * 2 + (out["method"] != "pair").astype(int)
    out = out.sort_values(["_r", "score"]).reset_index(drop=True)
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
    pmf = np.hypot(fast["pmra"], fast["pmdec"]).to_numpy() if len(fast) else np.zeros(0)
    return {"gaia_fast_in_zone": int(len(fast)), "detected_as_source": int(detected.sum()), "recovered": int(rec.sum()),
            "recall_of_detected": round(float(rec[detected].mean()), 3) if detected.any() else None,
            "pm_threshold_mas_yr": C.HIGHPM_RECALL_MAS_YR,
            "stars": [{"gaia_ref_id": i, "pm": round(float(p), 1), "g": None if not np.isfinite(gm) else round(float(gm), 2),
                       "detected": bool(dd), "recovered": bool(rr)}
                      for i, p, gm, dd, rr in zip(ids, pmf, fast["g"].to_numpy(np.float64) if len(fast) else [], detected, rec)][:200]}


def run(store: Store, zone: C.Zone, workers: int = 16) -> dict:
    t0 = time.time()
    src = store.read_parquet(results_rel(zone.name, "sources.parquet"), columns=["source_id", "ra", "dec", "pos_err_arcsec", "flux_ujy"])
    sv = source_visits(store, zone)
    src = src.merge(sv, on="source_id", how="left")
    src["visit"] = src["visit"].fillna(-1).astype(np.int64)
    photos = store.read_parquet(photolist_rel(zone.name), columns=["s_region", "visit"])
    fps: dict[int, list[Footprint]] = {}
    for s_region, v in zip(photos["s_region"], photos["visit"]):
        fps.setdefault(int(v), []).append(Footprint.from_s_region(s_region))
    xid_rel = results_rel(zone.name, "crossids.parquet")
    crossids = store.read_parquet(xid_rel) if store.exists(xid_rel) else None
    ast_rel = results_rel(zone.name, "astrometry.parquet")
    ast = store.read_parquet(ast_rel) if store.exists(ast_rel) else None
    cands = compute(src, fps, crossids, ast)
    ctrl = control_pairs(src)
    store.write_parquet(results_rel(zone.name, "highpm_candidates.parquet"), cands)
    refcat = load_refcat(store, zone)
    mjd_ref = float(np.nanmedian(src["mjd"])) if len(src) else 61000.0
    rep = {"zone": zone.name, "candidates": int(len(cands)), "new": int((cands["status"] == "new").sum()),
           "by_method": {m: int((cands["method"] == m).sum()) for m in ("pair", "fit")},
           "single_visit_sources": int((src["visit"] >= 0).sum()),
           "control_pairs": ctrl,
           "recall": recall(cands, crossids, refcat, zone, mjd_ref)}
    store.write_json(results_rel(zone.name, "highpm_report.json"), rep)
    st = {k: v for k, v in rep.items() if k != "recall"}
    st["recall"] = {k: v for k, v in rep["recall"].items() if k != "stars"}
    st["wall_s"] = round(time.time() - t0, 1)
    L.info("highpm %s: %s", zone.name, st)
    return st
