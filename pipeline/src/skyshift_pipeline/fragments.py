"""Step `fragments`: tag bright-star ring fragments, saturated stars and blended pairs, from stored tables only.

Why (hunt detection review, 14 Nov). IRSA flags 30–180 pixels around each saturated core TRANSIENT, SUR_ERROR and BLOOM;
our mask zeroes that island, so sep detects the star's wings as a ring of "fixed sources" without a catalogue entry
(16 per G < 6 star within 60″, 1.5 at G 8–9). Their member dots carry BLOOM in 98% (Gaia stars' dots: 0%). The same
BLOOM majority on a source that does match a catalogue is the saturated star itself. Separately, two stars closer than
the PSF (FWHM 7.5″) merge into one source, which crossid's 2.5″ may match to neither: it then looks "new".

One row per sources.parquet row (results/<zone>/fragments.parquet; contracts/dots.md, `fragments`):
- n_member_dots, bloom_frac, transient_frac: members.parquet → the dot files' `flags` (3×3 OR), projected reads (one
  read per dot file, as orphans); sat_frac: OVERFLOW or NONLINEAR at the peak (`flags_peak`; NaN when the files lack it).
- cat_dist_arcsec: nearest Gaia DR3 / 2MASS / CatWISE2020 entry of refcat/<zone>.parquet, Gaia moved by its proper motion
  to the source's epoch (crossids `epoch_mjd`, else crossid.source_epochs); match_dist_arcsec the same over
  FRAGMENTS_MATCH_CATALOGUES only (the tag's test). NaN beyond FRAGMENTS_CAT_SEARCH_ARCSEC. Measured from the source's
  position (sources.parquet) and, where it differs, from link's (fixed.parquet); the smaller wins: a saturated star's
  member dots are mostly its wings, so its spectrum position can sit 5–9″ off the star (hunt: 162 such stars).
- bright_ref_id, bright_dist_arcsec, bright_mag, bright_band: the nearest star with G < FRAGMENTS_BRIGHT_G, K < _K or
  W1 < _W1 within FRAGMENTS_BRIGHT_ARCSEC (G alone misses red giants).
- n_gaia_8, blend_ids: Gaia stars G < FRAGMENTS_BLEND_G_MAX within FRAGMENTS_BLEND_ARCSEC (their ids, nearest first,
  when there are >= FRAGMENTS_BLEND_MIN_GAIA; else empty); blend_sep_arcsec, blend_dg: the nearest two's separation
  and |ΔG| (NaN otherwise).
- tag, the first that applies: `bright_fragment` (bloom_frac > FRAGMENTS_BLOOM_FRAC, no match within
  FRAGMENTS_MATCH_ARCSEC), `saturated_star` (the same BLOOM majority with a match: keep it out of photometry and
  astrometry), `blend` (n_gaia_8 >= FRAGMENTS_BLEND_MIN_GAIA), else "".
- parent_gaia_id, parent_mag, parent_dist_arcsec, parent_source_id (bright_fragment rows; −1 / NaN otherwise): the
  ring's star (`parents`) and its own fixed source, so one big star never reads as many small ones.
Consumers (each works unchanged without the file, `load` → None): highpm (bright_fragment → reason `fragment`, never
vetted; blend → status gaia), orphans (drops both), shape (drops bright_fragment), candidates.py (never lists a
bright_fragment source; blend → reason `blend`).
Writes results/<zone>/fragments.parquet and fragments.json. Zone level (tiled runs: once on the merged tables, the dot
files from the local slim mirror). Rerun per zone: fragments, then highpm, orphans, shape, candidates; nothing else.
"""

from __future__ import annotations

import io
import itertools
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree
from skyshift_core import tangent_deproject
from skyshift_core.astrometry import DAYS_PER_JYEAR
from skyshift_core.flags import BLOOM, SAT_BITS, TRANSIENT

from . import config as C
from . import log
from .crossid import ref_offset_at, source_epochs
from .motion_io import gaia_numeric_id, load_refcat
from .sky import angsep, chord, radec_to_xyz
from .store import Store, dots_rel, results_rel

L = log.get("fragments")

FILE = "fragments.parquet"
REPORT = "fragments.json"
TAGS = ("bright_fragment", "saturated_star", "blend")
COLUMNS = ["source_id", "n_member_dots", "bloom_frac", "transient_frac", "sat_frac", "cat_dist_arcsec", "match_dist_arcsec",
           "bright_ref_id", "bright_dist_arcsec", "bright_mag", "bright_band", "n_gaia_8", "blend_ids", "blend_sep_arcsec",
           "blend_dg", "tag", "parent_source_id", "parent_gaia_id", "parent_mag", "parent_dist_arcsec"]


# --- member dots' flags ------------------------------------------------------------------------------------------
def member_flag_shares(store: Store, zone: C.Zone, sid: np.ndarray, workers: int = 16,
                       dots_store: Store | None = None) -> tuple[pd.DataFrame, dict]:
    """Per source (order of `sid`): member dots read, and how many carry BLOOM / TRANSIENT in `flags` and a saturated
    peak in `flags_peak` (-1 sums where no file had flags_peak). One projected read per dot file."""
    n = len(sid)
    m = store.read_parquet(results_rel(zone.name, "members.parquet"), columns=["source_id", "obs_id", "detector", "dot_row"])
    k = pd.Index(np.asarray(sid, np.int64)).get_indexer(m["source_id"].to_numpy(np.int64)).astype(np.int64)
    m = m.assign(_k=k)[k >= 0]
    groups = m.groupby(["obs_id", "detector"], observed=True, sort=True).indices
    kk, rows = m["_k"].to_numpy(np.int64), m["dot_row"].to_numpy(np.int64)
    del m
    ds = dots_store or store
    st = {"member_rows": int(len(kk)), "files": len(groups), "files_missing": 0, "rows_missing": 0, "files_without_flags_peak": 0}

    def one(item):
        (obs_id, det), idx = item
        rel = dots_rel(str(obs_id), int(det))
        try:
            import pyarrow.parquet as pq
            try:
                with ds.fs.open(ds.path(rel), "rb") as f:
                    pf = pq.ParquetFile(f)
                    cols = [c for c in ("flags", "flags_peak") if c in pf.schema_arrow.names]
                    t = pf.read(columns=cols)
            except FileNotFoundError:
                raise
            except Exception:  # noqa: BLE001  a store without ranged reads: the whole file
                t = pq.read_table(io.BytesIO(ds.read_bytes(rel)))
        except FileNotFoundError:
            return None
        r = rows[idx]
        ok = r < t.num_rows
        r = r[ok]
        fl = (t.column("flags").to_numpy(zero_copy_only=False).astype(np.int64)[r] if "flags" in t.column_names
              else np.zeros(len(r), np.int64))
        fp = t.column("flags_peak").to_numpy(zero_copy_only=False).astype(np.int64)[r] if "flags_peak" in t.column_names else None
        return kk[idx[ok]], int((~ok).sum()), (fl & BLOOM) != 0, (fl & TRANSIENT) != 0, None if fp is None else (fp & SAT_BITS) != 0

    cnt, bloom, trans = np.zeros(n), np.zeros(n), np.zeros(n)
    sat, sat_n = np.zeros(n), np.zeros(n)
    with ThreadPoolExecutor(max_workers=max(4, min(workers, 32))) as ex:
        for res in ex.map(one, groups.items()):
            if res is None:
                st["files_missing"] += 1
                continue
            kr, miss, b, tr, sp = res
            st["rows_missing"] += miss
            cnt += np.bincount(kr, minlength=n)
            bloom += np.bincount(kr, weights=b, minlength=n)
            trans += np.bincount(kr, weights=tr, minlength=n)
            if sp is None:
                st["files_without_flags_peak"] += 1
            else:
                sat_n += np.bincount(kr, minlength=n)
                sat += np.bincount(kr, weights=sp, minlength=n)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = pd.DataFrame({"n_member_dots": cnt.astype(np.int32),
                            "bloom_frac": np.where(cnt > 0, bloom / cnt, np.nan).astype(np.float32),
                            "transient_frac": np.where(cnt > 0, trans / cnt, np.nan).astype(np.float32),
                            "sat_frac": np.where(sat_n > 0, sat / sat_n, np.nan).astype(np.float32)})
    st["member_dots"] = int(cnt.sum())
    return out, st


# --- catalogue neighbours ------------------------------------------------------------------------------------------
def gaia_pairs(ra: np.ndarray, dec: np.ndarray, mjd: np.ndarray, g: pd.DataFrame, radius_arcsec: float, offsets: bool = False
               ) -> tuple[np.ndarray, ...]:
    """Every (source, Gaia row) pair within radius_arcsec, Gaia moved by its proper motion to each source's own epoch
    (no parallax): (source index, row in g, separation ″), + the star's (east, north) offset ″ from the source if
    `offsets`. Exact: each row is searched with radius + its motion over the sources' epoch span, then measured at the
    source's epoch."""
    e0 = np.zeros(0, np.int64)
    if not len(ra) or not len(g):
        return (e0, e0, np.zeros(0)) + ((np.zeros(0), np.zeros(0)) if offsets else ())
    t0 = float(np.nanmedian(mjd))
    span = float(np.nanmax(np.abs(mjd - t0))) / DAYS_PER_JYEAR
    pmra, pmdec = np.nan_to_num(g["pmra"].to_numpy(np.float64)), np.nan_to_num(g["pmdec"].to_numpy(np.float64))
    dt = np.nan_to_num((t0 - g["epoch_mjd"].to_numpy(np.float64)) / DAYS_PER_JYEAR)
    gra, gdec = tangent_deproject(g["ra"].to_numpy(np.float64), g["dec"].to_numpy(np.float64), pmra * dt / 1e3, pmdec * dt / 1e3)
    r = radius_arcsec + np.hypot(pmra, pmdec) / 1e3 * span + 0.01
    lists = cKDTree(radec_to_xyz(ra, dec)).query_ball_point(radec_to_xyz(gra, gdec), chord(r))
    lens = np.fromiter((len(x) for x in lists), np.int64, len(lists))
    if not lens.sum():
        return (e0, e0, np.zeros(0)) + ((np.zeros(0), np.zeros(0)) if offsets else ())
    gi = np.repeat(np.arange(len(g)), lens)
    si = np.fromiter(itertools.chain.from_iterable(lists), np.int64, int(lens.sum()))
    e, n = ref_offset_at(ra[si], dec[si], g.iloc[gi].reset_index(drop=True), mjd[si], parallax=False)
    d = np.hypot(e, n)
    ok = d <= radius_arcsec
    return (si[ok], gi[ok], d[ok]) + ((e[ok], n[ok]) if offsets else ())


def nearest_static(ra: np.ndarray, dec: np.ndarray, ref: pd.DataFrame, radius_arcsec: float) -> tuple[np.ndarray, np.ndarray]:
    """Nearest row of `ref` (positions as listed, not moved) within radius: (separation ″ or NaN, row or -1)."""
    n = len(ra)
    if not n or not len(ref):
        return np.full(n, np.nan), np.full(n, -1, np.int64)
    d, i = cKDTree(radec_to_xyz(ref["ra"].to_numpy(np.float64), ref["dec"].to_numpy(np.float64))).query(
        radec_to_xyz(ra, dec), k=1, distance_upper_bound=chord(radius_arcsec))
    ok = np.isfinite(d)
    sep = np.where(ok, np.rad2deg(2 * np.arcsin(np.clip(np.where(ok, d, 0) / 2, 0, 1))) * 3600.0, np.nan)
    return sep, np.where(ok, i, -1).astype(np.int64)


def nearest_of_pairs(n: int, si: np.ndarray, d: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per source: the smallest d among its pairs (NaN if none) and that pair's index (-1)."""
    best, arg = np.full(n, np.nan), np.full(n, -1, np.int64)
    if len(si):
        order = np.lexsort((d, si))
        first = order[np.r_[True, np.diff(si[order]) != 0]]
        best[si[first]], arg[si[first]] = d[first], first
    return best, arg


def _fmin(near: dict[str, np.ndarray], keys) -> np.ndarray:
    a = np.stack([near[k] for k in keys])
    return np.where(np.isfinite(a).any(0), np.nanmin(np.where(np.isfinite(a), a, np.inf), 0), np.nan)


def nearest_distances(ra: np.ndarray, dec: np.ndarray, mjd: np.ndarray, refcat: pd.DataFrame,
                      gaia: tuple | None = None) -> dict[str, np.ndarray]:
    """Per catalogue (gaia, tmass, catwise) the nearest entry's separation (″, NaN beyond FRAGMENTS_CAT_SEARCH_ARCSEC);
    Gaia moved to each epoch by its proper motion. gaia: gaia_pairs' result for these positions, if already made."""
    n, cats = len(ra), refcat["cat"].to_numpy()
    si, _, d = gaia if gaia is not None else gaia_pairs(ra, dec, mjd, refcat[cats == "gaia"].reset_index(drop=True),
                                                       C.FRAGMENTS_CAT_SEARCH_ARCSEC)
    best, _ = nearest_of_pairs(n, si, np.where(d <= C.FRAGMENTS_CAT_SEARCH_ARCSEC, d, np.inf))
    near = {"gaia": np.where(np.isfinite(best), best, np.nan)}
    for cat in ("tmass", "catwise"):
        near[cat], _ = nearest_static(ra, dec, refcat[cats == cat].reset_index(drop=True), C.FRAGMENTS_CAT_SEARCH_ARCSEC)
    return near


def catalogue_columns(ra: np.ndarray, dec: np.ndarray, mjd: np.ndarray, refcat: pd.DataFrame,
                      link_pos: tuple[np.ndarray, np.ndarray] | None = None) -> tuple[pd.DataFrame, dict]:
    """cat_dist_arcsec, match_dist_arcsec, bright_*, n_gaia_8, blend_ids for sources at (ra, dec) and epochs mjd.
    link_pos: link's positions (fixed.parquet) of the same sources; where one is > 0.5″ from (ra, dec), the catalogue
    distances are also measured from it and the smaller wins (a saturated star's spectrum position can sit 5–9″ off it)."""
    n = len(ra)
    cats = refcat["cat"].to_numpy()
    g = refcat[cats == "gaia"].reset_index(drop=True)
    # Gaia, moved to each source's epoch: nearest (any G), the blend count (G < BLEND_G_MAX), bright neighbours
    rad = max(C.FRAGMENTS_CAT_SEARCH_ARCSEC, C.FRAGMENTS_BLEND_ARCSEC)
    si, gi, d, pe, pn = gaia_pairs(ra, dec, mjd, g, rad, offsets=True)
    near = nearest_distances(ra, dec, mjd, refcat, (si, gi, d))
    moved_link = 0
    if link_pos is not None:
        lra, ldec = (np.asarray(x, np.float64) for x in link_pos)
        with np.errstate(invalid="ignore"):
            off = np.flatnonzero(np.isfinite(lra) & (angsep(ra, dec, lra, ldec) * 3600.0 > 0.5))
        moved_link = int(len(off))
        if len(off):
            alt = nearest_distances(lra[off], ldec[off], mjd[off], refcat)
            for k, v in alt.items():
                near[k][off] = _fmin({"a": near[k][off], "b": v}, ("a", "b"))
    gmag = g["g"].to_numpy(np.float64)
    with np.errstate(invalid="ignore"):
        bl = (d <= C.FRAGMENTS_BLEND_ARCSEC) & (gmag[gi] < C.FRAGMENTS_BLEND_G_MAX)
    n8 = np.bincount(si[bl], minlength=n).astype(np.int16)
    blend_ids = [np.zeros(0, np.int64)] * n
    blend_sep, blend_dg = np.full(n, np.nan), np.full(n, np.nan)
    many = n8 >= C.FRAGMENTS_BLEND_MIN_GAIA
    if many.any():
        sel = bl & many[si]
        s2, g2, d2, e2, n2 = si[sel], gi[sel], d[sel], pe[sel], pn[sel]
        order = np.lexsort((d2, s2))
        s2, g2, e2, n2 = s2[order], g2[order], e2[order], n2[order]
        ids = gaia_numeric_id(g["ref_id"].to_numpy()[g2])
        cuts = np.flatnonzero(np.diff(s2)) + 1
        for s, part in zip(s2[np.r_[0, cuts]], np.split(ids, cuts)):
            blend_ids[int(s)] = part
        a = np.r_[0, cuts]  # each source's nearest two (every group has >= 2 rows): their separation and ΔG (>= 0)
        blend_sep[s2[a]] = np.hypot(e2[a] - e2[a + 1], n2[a] - n2[a + 1])
        blend_dg[s2[a]] = np.abs(gmag[g2[a]] - gmag[g2[a + 1]])
    # bright neighbours: Gaia G (moved), 2MASS K, CatWISE W1
    bd = np.full(n, np.nan)
    bref = np.full(n, "", dtype=object)
    bmag = np.full(n, np.nan)
    bband = np.full(n, "", dtype=object)

    def take(sep, refs, mags, band):
        better = np.isfinite(sep) & ~(sep >= bd)  # NaN bd: anything is better
        bd[better], bref[better], bmag[better], bband[better] = sep[better], refs[better], mags[better], band

    with np.errstate(invalid="ignore"):
        gb = g[gmag < C.FRAGMENTS_BRIGHT_G].reset_index(drop=True)
    si_b, gi_b, d_b = gaia_pairs(ra, dec, mjd, gb, C.FRAGMENTS_BRIGHT_ARCSEC)
    best, arg = nearest_of_pairs(n, si_b, d_b)
    has = arg >= 0
    rows = np.where(has, gi_b[np.where(has, arg, 0)] if len(gi_b) else 0, 0)
    take(best, gb["ref_id"].astype(str).to_numpy()[rows] if len(gb) else np.full(n, ""),
         gb["g"].to_numpy(np.float64)[rows] if len(gb) else np.full(n, np.nan), "G")
    for cat, col, lim, band in (("tmass", "k", C.FRAGMENTS_BRIGHT_K, "K"), ("catwise", "w1", C.FRAGMENTS_BRIGHT_W1, "W1")):
        r = refcat[cats == cat]
        with np.errstate(invalid="ignore"):
            r = r[r[col].to_numpy(np.float64) < lim].reset_index(drop=True)
        sep, i = nearest_static(ra, dec, r, C.FRAGMENTS_BRIGHT_ARCSEC)
        j = np.where(i >= 0, i, 0)
        take(sep, r["ref_id"].astype(str).to_numpy()[j] if len(r) else np.full(n, ""),
             r[col].to_numpy(np.float64)[j] if len(r) else np.full(n, np.nan), band)
    out = pd.DataFrame({
        "cat_dist_arcsec": _fmin(near, ("gaia", "tmass", "catwise")).astype(np.float32),
        "match_dist_arcsec": _fmin(near, C.FRAGMENTS_MATCH_CATALOGUES).astype(np.float32),
        "bright_ref_id": bref.astype(str), "bright_dist_arcsec": bd.astype(np.float32), "bright_mag": bmag.astype(np.float32),
        "bright_band": bband.astype(str), "n_gaia_8": n8, "blend_ids": blend_ids,
        "blend_sep_arcsec": blend_sep.astype(np.float32), "blend_dg": blend_dg.astype(np.float32)})
    st = {"refcat_rows": {k: int((cats == k).sum()) for k in ("gaia", "tmass", "catwise")}, "gaia_pairs": int(len(si)),
          "link_position_also": moved_link,
          "bright_stars": {"G": int(len(gb)), "K": int(((cats == "tmass") & (refcat["k"].to_numpy(np.float64) < C.FRAGMENTS_BRIGHT_K)).sum()),
                           "W1": int(((cats == "catwise") & (refcat["w1"].to_numpy(np.float64) < C.FRAGMENTS_BRIGHT_W1)).sum())}}
    return out, st


def tags(bloom_frac, match_dist, n_gaia_8) -> np.ndarray:
    """The tag per source, the first that applies: bright_fragment, saturated_star, blend, ""."""
    with np.errstate(invalid="ignore"):
        bloom = np.nan_to_num(np.asarray(bloom_frac, np.float64), nan=0.0) > C.FRAGMENTS_BLOOM_FRAC
        match = np.nan_to_num(np.asarray(match_dist, np.float64), nan=np.inf) <= C.FRAGMENTS_MATCH_ARCSEC
    blend = np.asarray(n_gaia_8) >= C.FRAGMENTS_BLEND_MIN_GAIA
    return np.select([bloom & ~match, bloom & match, blend], list(TAGS), "").astype(object)


def parents(ra: np.ndarray, dec: np.ndarray, mjd: np.ndarray, refcat: pd.DataFrame, sid: np.ndarray,
            positions: list[tuple[np.ndarray, np.ndarray]], xgaia: pd.Series | None = None) -> tuple[pd.DataFrame, dict]:
    """The bright star whose ring each position (a bright_fragment) is part of: the brightest Gaia star with
    G < FRAGMENTS_PARENT_G within FRAGMENTS_PARENT_ARCSEC, moved to the epoch (the nearer on a tie). parent_gaia_id
    (−1: none), parent_mag (its G), parent_dist_arcsec; parent_source_id: the star's own fixed source (−1: none), the
    source crossid matched to it (xgaia: gaia_id → source_id), else the nearest source within FRAGMENTS_MATCH_ARCSEC of
    the star in any of `positions` (the zone's sources' ra/dec arrays, aligned with `sid`: sources.parquet's, link's)."""
    n = len(ra)
    out = pd.DataFrame({"parent_source_id": np.full(n, -1, np.int64), "parent_gaia_id": np.full(n, -1, np.int64),
                        "parent_mag": np.full(n, np.nan, np.float32), "parent_dist_arcsec": np.full(n, np.nan, np.float32)})
    g = refcat[refcat["cat"].to_numpy() == "gaia"]
    with np.errstate(invalid="ignore"):
        g = g[g["g"].to_numpy(np.float64) < C.FRAGMENTS_PARENT_G].reset_index(drop=True)
    si, gi, d, pe, pn = gaia_pairs(ra, dec, mjd, g, C.FRAGMENTS_PARENT_ARCSEC, offsets=True)
    st = {"with_parent": 0, "with_parent_source": 0, "parent_source_by_crossid": 0}
    if not len(si):
        return out, st
    gm = g["g"].to_numpy(np.float64)[gi]
    order = np.lexsort((d, gm, si))
    first = order[np.r_[True, np.diff(si[order]) != 0]]
    s, j = si[first], gi[first]
    gid = gaia_numeric_id(g["ref_id"].to_numpy()[j])
    out.loc[s, "parent_gaia_id"] = gid
    out.loc[s, "parent_mag"] = gm[first].astype(np.float32)
    out.loc[s, "parent_dist_arcsec"] = d[first].astype(np.float32)
    psid = np.full(len(s), -1, np.int64)
    if xgaia is not None and len(xgaia):
        psid = xgaia.reindex(gid).fillna(-1).to_numpy(np.int64)
        st["parent_source_by_crossid"] = int((psid >= 0).sum())
    left = psid < 0
    if left.any():  # the star's position at that epoch, against every source position within the match radius
        sra, sdec = tangent_deproject(ra[s[left]], dec[s[left]], pe[first][left], pn[first][left])
        best, arg = np.full(int(left.sum()), np.inf), np.full(int(left.sum()), -1, np.int64)
        for pra, pdec in positions:
            ok = np.isfinite(pra) & np.isfinite(pdec)
            if not ok.any():
                continue
            idx = np.flatnonzero(ok)
            dd, ii = cKDTree(radec_to_xyz(pra[ok], pdec[ok])).query(radec_to_xyz(sra, sdec), k=1,
                                                                    distance_upper_bound=chord(C.FRAGMENTS_MATCH_ARCSEC))
            better = np.isfinite(dd) & (dd < best)
            best[better], arg[better] = dd[better], idx[ii[better]]
        psid[left] = np.where(arg >= 0, np.asarray(sid, np.int64)[np.maximum(arg, 0)], -1)
    out.loc[s, "parent_source_id"] = psid
    st["with_parent"], st["with_parent_source"] = int(len(s)), int((psid >= 0).sum())
    return out, st


# --- the step --------------------------------------------------------------------------------------------------
def compute(store: Store, zone: C.Zone, workers: int = 16, dots_store: Store | None = None) -> tuple[pd.DataFrame, dict]:
    t0 = time.time()
    src = store.read_parquet(results_rel(zone.name, "sources.parquet"), columns=["source_id", "ra", "dec"])
    sid = src["source_id"].to_numpy(np.int64)
    ra, dec = src["ra"].to_numpy(np.float64), src["dec"].to_numpy(np.float64)
    xrel = results_rel(zone.name, "crossids.parquet")
    mjd, xgaia = None, None
    if store.exists(xrel):
        try:
            x = store.read_parquet(xrel, columns=["source_id", "epoch_mjd", "gaia_id", "gaia_sep_arcsec"])
            xg = x[x["gaia_id"].to_numpy(np.int64) >= 0].sort_values("gaia_sep_arcsec", kind="stable").drop_duplicates("gaia_id")
            xgaia = pd.Series(xg["source_id"].to_numpy(np.int64), index=xg["gaia_id"].to_numpy(np.int64))
        except Exception:  # noqa: BLE001  a crossids table without the Gaia columns: parents by position only
            x = store.read_parquet(xrel, columns=["source_id", "epoch_mjd"])
        x = x.drop_duplicates("source_id").set_index("source_id")
        mjd = x["epoch_mjd"].reindex(sid).to_numpy(np.float64)
    if mjd is None or not np.isfinite(mjd).all():
        try:
            ep = source_epochs(store, zone, sid)
        except Exception:  # noqa: BLE001  no photo list here: the zone's median epoch (Gaia moves ≲ 0.1″ in half a year)
            if mjd is None or not np.isfinite(mjd).any():
                raise
            ep = np.full(len(sid), float(np.nanmedian(mjd)))
        mjd = ep if mjd is None else np.where(np.isfinite(mjd), mjd, ep)
    st: dict = {"sources": int(len(sid)), "crossids": store.exists(xrel)}
    shares, st["dots"] = member_flag_shares(store, zone, sid, workers, dots_store)
    secs = {"dots_s": round(time.time() - t0, 1)}
    frel = results_rel(zone.name, "fixed.parquet")
    link_pos = None
    if store.exists(frel):
        fx = store.read_parquet(frel, columns=["source_id", "ra", "dec"]).drop_duplicates("source_id").set_index("source_id")
        link_pos = (fx["ra"].reindex(sid).to_numpy(np.float64), fx["dec"].reindex(sid).to_numpy(np.float64))
    refcat = load_refcat(store, zone)
    cat, st["refcat"] = catalogue_columns(ra, dec, mjd, refcat, link_pos)
    out = pd.concat([pd.DataFrame({"source_id": sid}), shares, cat], axis=1)
    out["tag"] = tags(out["bloom_frac"], out["match_dist_arcsec"], out["n_gaia_8"]).astype(str)
    f = np.flatnonzero(out["tag"].to_numpy() == "bright_fragment")
    par, st["parents"] = parents(ra[f], dec[f], mjd[f], refcat, sid, [(ra, dec)] + ([link_pos] if link_pos else []), xgaia)
    for c in par.columns:  # −1 / NaN on every other row
        col = np.full(len(out), -1, np.int64) if par[c].dtype == np.int64 else np.full(len(out), np.nan, np.float32)
        col[f] = par[c].to_numpy()
        out[c] = col
    st["seconds"] = {**secs, "wall_s": round(time.time() - t0, 1)}  # in the summary, not in fragments.json
    return out[COLUMNS], st


def report(t: pd.DataFrame, st: dict, zone: C.Zone) -> dict:
    with np.errstate(invalid="ignore"):
        bloom = t["bloom_frac"].to_numpy(np.float64) > C.FRAGMENTS_BLOOM_FRAC
        match = t["match_dist_arcsec"].to_numpy(np.float64) <= C.FRAGMENTS_MATCH_ARCSEC
        cat3 = t["cat_dist_arcsec"].to_numpy(np.float64) <= C.FRAGMENTS_MATCH_ARCSEC
        bright60 = t["bright_dist_arcsec"].to_numpy(np.float64) <= 60.0
    blend = t["n_gaia_8"].to_numpy() >= C.FRAGMENTS_BLEND_MIN_GAIA
    tag = t["tag"].to_numpy()
    pm = np.nan_to_num(t["parent_mag"].to_numpy(np.float64), nan=99.0)
    return {
        "zone": zone.name, "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "rule": {k: (list(v) if isinstance(v, tuple) else v) for k, v in vars(C).items() if k.startswith("FRAGMENTS_")},
        "counts": {"sources": int(len(t)), "tags": {k: int((tag == k).sum()) for k in TAGS}, "untagged": int((tag == "").sum())},
        "conditions": {"bloom_majority": int(bloom.sum()), "bloom_majority_unmatched": int((bloom & ~match).sum()),
                       "bloom_majority_matched": int((bloom & match).sum()),
                       "bloom_majority_no_catalogue_incl_catwise": int((bloom & ~cat3).sum()),
                       "blend_n_gaia": int(blend.sum()), "blend_also_bloom_majority": int((blend & bloom).sum()),
                       "bright_fragment_bright_star_within_60": int(((tag == "bright_fragment") & bright60).sum())},
        "bright_fragment_bright_band": {b: int(((tag == "bright_fragment") & bright60 & (t["bright_band"].to_numpy() == b)).sum())
                                        for b in ("G", "K", "W1")},
        "bright_fragment_parents": {
            "with_parent": int(((tag == "bright_fragment") & (t["parent_gaia_id"].to_numpy() >= 0)).sum()),
            "with_parent_source": int(((tag == "bright_fragment") & (t["parent_source_id"].to_numpy() >= 0)).sum()),
            "parents": int(t.loc[tag == "bright_fragment", "parent_gaia_id"].pipe(lambda s: s[s >= 0]).nunique()),
            "by_parent_g": {f"{lo}-{hi}": int(((tag == "bright_fragment") & (pm >= lo) & (pm < hi)).sum())
                            for lo, hi in ((-2, 6), (6, 8), (8, 10), (10, 12))}},
        "blend_sep_arcsec_median": (round(float(np.nanmedian(t.loc[tag == "blend", "blend_sep_arcsec"].to_numpy(np.float64))), 2)
                                    if (tag == "blend").any() else None),
        "stats": {k: v for k, v in st.items() if k != "seconds"},
    }


def run(store: Store, zone: C.Zone, workers: int = 16, dots_store: Store | None = None) -> dict:
    """The step: fragments.parquet + fragments.json; returns the summary entry. Never raises (the error goes into the
    stats): every consumer works without the file, so `all` goes on."""
    try:
        t, st = compute(store, zone, workers, dots_store)
        store.write_parquet(results_rel(zone.name, FILE), t)
        rep = report(t, st, zone)
        store.write_json(results_rel(zone.name, REPORT), rep)
    except Exception as e:  # noqa: BLE001
        L.exception("fragments %s failed; the consumers run without tags", zone.name)
        return {"error": repr(e)[:500]}
    out = {**rep["counts"], "conditions": rep["conditions"], "dots": st["dots"], **st["seconds"]}
    L.info("fragments %s: %s", zone.name, out)
    return out


# --- for the consumers ---------------------------------------------------------------------------------------------
def load(store: Store, zone) -> pd.Series | None:
    """source_id → tag of the tagged sources, or None when results/<zone>/fragments.parquet is missing or unreadable
    (consumers then change nothing). zone: a config Zone or its name."""
    name = getattr(zone, "name", zone)
    rel = results_rel(name, FILE)
    try:
        if not store.exists(rel):
            return None
        t = store.read_parquet(rel, columns=["source_id", "tag"])
    except Exception:  # noqa: BLE001
        L.warning("fragments %s: %s unreadable; no tags used", name, rel, exc_info=True)
        return None
    tag = t["tag"].fillna("").astype(str).to_numpy()
    return pd.Series(tag[tag != ""], index=t["source_id"].to_numpy(np.int64)[tag != ""], dtype=object)


def tag_of(tg: pd.Series | None, source_id) -> np.ndarray:
    """The tag of each source_id ("" when untagged or without tags)."""
    sid = np.asarray(source_id, np.int64)
    if tg is None or not len(tg):
        return np.full(len(sid), "", dtype=object)
    return tg.reindex(sid).fillna("").to_numpy(object)


def apply_highpm(cands: pd.DataFrame, tg: pd.Series | None) -> tuple[pd.DataFrame, dict]:
    """highpm: a bright_fragment on either side → reason `fragment` (never vetted); a blend on either side → status
    gaia (Gaia lists the stars; not new). Re-sorted as highpm.compute (vetted, new, pairs first, then score)."""
    if tg is None or not len(cands):
        return cands, {"applied": tg is not None, "fragment": 0, "blend_not_new": 0}
    ta = tag_of(tg, cands["source_id"].to_numpy(np.int64))
    tb = tag_of(tg, cands["source_id_b"].fillna(-1).to_numpy(np.int64))
    frag = (ta == "bright_fragment") | (tb == "bright_fragment")
    blend = (ta == "blend") | (tb == "blend")
    c = cands.copy()
    r = c["reason"].fillna("").astype(str).to_numpy(object)
    c["reason"] = np.where(frag, np.where(r == "", "fragment", r + ",fragment"), r).astype(str)
    c["vetted"] = c["reason"].to_numpy() == ""
    was_new = (c["status"] == "new").to_numpy()
    c["status"] = np.where(blend, "gaia", c["status"].astype(str).to_numpy())
    key = (~c["vetted"]).astype(int) * 4 + (c["status"] != "new").astype(int) * 2 + (c["method"] != "pair").astype(int)
    c = c.assign(_r=key).sort_values(["_r", "score"], kind="stable").drop(columns="_r").reset_index(drop=True)
    return c, {"applied": True, "fragment": int(frag.sum()), "blend_not_new": int((blend & was_new).sum())}


def apply_candidates(cands: list[dict], tg: pd.Series | None) -> tuple[list[dict], dict]:
    """candidates.py: a candidate with a bright_fragment source (`_sources`) is not listed; one with a blend gets the
    reason `blend`. Candidates without `_sources` (movers) are untouched."""
    st = {"applied": tg is not None, "dropped_fragment": 0, "blend": 0}
    if tg is None:
        return cands, st
    keep = []
    for c in cands:
        t = set(tag_of(tg, c.get("_sources") or []))
        if "bright_fragment" in t:
            st["dropped_fragment"] += 1
            continue
        if "blend" in t and "blend" not in c["reasons"]:
            c["reasons"].append("blend")
            st["blend"] += 1
        keep.append(c)
    return keep, st
