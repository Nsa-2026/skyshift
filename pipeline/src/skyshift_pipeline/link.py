"""Step 4, `link`: fixed sources, then movers.

Only dots inside the zone box are used (most of a photo's dots fall outside it).
1. fixed: friends-of-friends within FIXED_LINK_ARCSEC over all dots; a group seen in
   >= FIXED_MIN_SESSIONS sessions is a fixed source (stars, galaxies). Each gets a `source_id`
   (source_id_of: cell × SOURCE_ID_PER_CELL + rank in the cell) and its dots are listed in
   members.parquet (source_id, obs_id, detector, dot_row) for `spectra`, less those member_mask leaves out
   (a masked core at the peak; a second dot in one photo). Those still belong to the group for the veto.
2. Dots in a fixed group or within FIXED_VETO_ARCSEC of a fixed source are dropped. Nothing else
   vetoes a candidate: a stray single dot in another session doesn't.
3. The rest (elong <= MOVER_MAX_ELONG) are paired inside each session:
   - two exposures (different obs_id) separated by <= PAIR_BASE_ARCSEC + PAIR_RATE × Δt (minutes);
   - or a simultaneous detector pair (same obs_id, D1+D4 / D2+D5 / D3+D6) within
     SIMULTANEOUS_PAIR_ARCSEC (the two footprints are offset ~71″ but see the same sky).
   Pairs chain into one candidate (an asteroid seen in 3–5 exposures).
4. A candidate is a mover if the sessions whose footprints contain it number >= MOVER_MIN_COVER_SESSIONS.
Sessions are the zone's time runs (photolist); visit labels are never used here.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from skyshift_core import MASK_BITS, SAT_BITS

from . import config as C
from . import log
from .sky import Footprint, SkyTree, angsep, cell_of, chord, fof_groups, radec_to_xyz, sessions_covering
from .store import Store, dots_rel, photolist_rel, results_rel

L = log.get("link")

LOAD_COLUMNS = ["detector", "mjd", "ra", "dec", "flux_ujy", "snr", "flags", "flags_peak", "elong"]


def load_dots(store: Store, photos: pd.DataFrame, zone: C.Zone, workers: int = 16) -> tuple[pd.DataFrame, int]:
    """All dot files of the zone's photos, clipped to the zone box. Returns (dots, missing).

    `photo` is the row in the photo list, `exposure` a number per obs_id; session and visit come
    from this zone's photo list (a dot file made for the pilot carries the pilot's labels).
    """
    exp_code = {o: i for i, o in enumerate(sorted(photos["obs_id"].unique()))}
    ignored: list[int] = []

    def one(r):
        try:
            df = store.read_parquet(dots_rel(r.obs_id, int(r.detector)))
        except FileNotFoundError:
            return None
        df = df[[c for c in LOAD_COLUMNS if c in df.columns]]
        # row position in the dot file as written (files are written once, never rewritten)
        df["dot_row"] = np.arange(len(df), dtype=np.int32)
        if "flags_peak" in df and len(df):
            fp = df["flags_peak"].to_numpy(np.int64)
            if np.mean((fp & C.SOURCE_BIT) != 0) > C.SOURCE_PHOTO_MAX_FRAC:
                # SOURCE on nearly every dot of this photo says nothing: clear it (in memory only)
                df["flags_peak"] = (fp & ~C.SOURCE_BIT).astype(np.int32)
                ignored.append(r.Index)
        df = df[zone.contains(df["ra"].to_numpy(), df["dec"].to_numpy())].reset_index(drop=True)
        df["session"] = np.int32(r.session)
        df["visit"] = np.int16(r.visit)
        df["photo"] = np.int32(r.Index)
        df["exposure"] = np.int32(exp_code[r.obs_id])
        return df

    with ThreadPoolExecutor(max_workers=max(4, min(workers, 32))) as ex:
        parts = list(ex.map(one, photos.itertuples()))
    missing = sum(p is None for p in parts)
    n_source_ignored = len(ignored)
    if n_source_ignored:
        L.info("SOURCE bit ignored in %d photos (set at the peak of > %.0f%% of their dots)", n_source_ignored, 100 * C.SOURCE_PHOTO_MAX_FRAC)
    parts = [p for p in parts if p is not None and len(p)]
    cols = [*LOAD_COLUMNS, "session", "visit", "photo", "exposure", "dot_row"]
    if not parts:
        return pd.DataFrame({c: pd.Series(dtype="float64") for c in cols}), missing
    df = pd.concat(parts, ignore_index=True)
    for c in ("flags_peak", "elong"):
        if c not in df:
            df[c] = 0 if c == "flags_peak" else np.nan
    return df[cols], missing


FIXED_COLUMNS = ["source_id", "ra", "dec", "n_sessions", "n_dots", "snr", "flux_ujy", "cell"]


def source_id_of(ra: np.ndarray, dec: np.ndarray) -> np.ndarray:
    """Deterministic int64 id from the median position: cell × SOURCE_ID_PER_CELL + rank in the
    cell (by RA, then Dec). The same input gives the same ids; the same star gets the same id in
    the pilot and the hunt zone unless a neighbour in its 6″ cell differs."""
    ra = np.asarray(ra, np.float64)
    dec = np.asarray(dec, np.float64)
    if ra.size == 0:
        return np.zeros(0, np.int64)
    cell = cell_of(ra, dec)
    order = np.lexsort((dec, ra, cell))
    cs = cell[order]
    start = np.r_[0, np.flatnonzero(np.diff(cs)) + 1]
    rank = np.arange(cs.size) - np.repeat(start, np.diff(np.r_[start, cs.size]))
    if rank.size and rank.max() >= C.SOURCE_ID_PER_CELL:
        raise ValueError("more fixed sources in one 6″ cell than SOURCE_ID_PER_CELL")
    out = np.empty(ra.size, np.int64)
    out[order] = cs * C.SOURCE_ID_PER_CELL + rank
    return out


def fixed_groups(dots: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray]:
    """(fixed sources sorted by source_id, per-dot source_id; -1 where the dot is in no fixed group)."""
    if len(dots) == 0:
        return pd.DataFrame({c: pd.Series(dtype="int64" if c in ("source_id", "cell") else "float64")
                             for c in FIXED_COLUMNS}), np.zeros(0, np.int64)
    lab = fof_groups(dots["ra"].to_numpy(), dots["dec"].to_numpy(), C.FIXED_LINK_ARCSEC, C.FIXED_SNAP_ARCSEC)
    d = pd.DataFrame({"g": lab, "session": dots["session"].to_numpy(), "ra": dots["ra"].to_numpy(),
                      "dec": dots["dec"].to_numpy(), "snr": dots["snr"].to_numpy(),
                      "flux": dots["flux_ujy"].to_numpy(np.float64)})  # float64: pandas' median of float32 is float32 only sometimes
    agg = d.groupby("g").agg(n_sessions=("session", "nunique"), n_dots=("ra", "size"), ra=("ra", "median"),
                             dec=("dec", "median"), snr=("snr", "max"), flux_ujy=("flux", "median"))
    fixed = agg[agg["n_sessions"] >= C.FIXED_MIN_SESSIONS]
    sid = source_id_of(fixed["ra"].to_numpy(), fixed["dec"].to_numpy())
    g2sid = pd.Series(sid, index=fixed.index)
    dot_sid = g2sid.reindex(lab).fillna(-1).to_numpy(np.int64)
    fixed = fixed.reset_index(drop=True)
    fixed.insert(0, "source_id", sid)
    fixed["cell"] = cell_of(fixed["ra"].to_numpy(), fixed["dec"].to_numpy())
    fixed = fixed.sort_values("source_id").reset_index(drop=True)
    return fixed[FIXED_COLUMNS], dot_sid


def find_fixed(dots: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray]:
    """(fixed sources, per-dot boolean 'belongs to a fixed group')."""
    fixed, dot_sid = fixed_groups(dots)
    return fixed, dot_sid >= 0


def member_mask(dots: pd.DataFrame, dot_sid: np.ndarray, fixed: pd.DataFrame) -> tuple[np.ndarray, dict]:
    """Which dots of fixed groups are measured (members.parquet), and counts of those left out.

    The group itself (dot_sid, fixed, the mover veto) is unchanged; only the measurement set is cleaned:
    - FIXED_MEMBER_DROP_MASKED_PEAK: a MASK bit other than saturation at the dot's peak pixel (unless the dot is
      saturated there). detect masks those pixels, so the centroid lands on the remaining wings, 1.5–5″ off
      (hunt: ~1.8% of member dots, up to 20% of one star's dots in one visit → fake proper motions).
      A source whose every dot is so flagged keeps them all.
    - FIXED_MEMBER_ONE_PER_PHOTO: at most one dot per (source, photo): the nearest to the group's position. The rest
      are wing fragments of a bright star or a neighbour chained into the group (hunt: 12.7% of the fake fast
      Gaia stars had > 10% of their photos with two or more dots, 0.2% of quiet Gaia stars).
    `dots` needs `photo` and `flags_peak`; `fixed` is fixed_groups' table (source_id sorted, ra, dec)."""
    keep = dot_sid >= 0
    st = {"member_dots_masked_peak": 0, "member_dots_second_in_photo": 0}
    if not keep.any():
        return keep, st
    if C.FIXED_MEMBER_DROP_MASKED_PEAK and "flags_peak" in dots:
        fp = dots["flags_peak"].to_numpy(np.int64) & 0xFFFFFFFF
        bad = keep & ((fp & (MASK_BITS & ~SAT_BITS)) != 0) & ((fp & SAT_BITS) == 0)
        if bad.any():
            # never empty a source: one whose every dot is flagged keeps them
            u, inv = np.unique(dot_sid[keep], return_inverse=True)
            inv = inv.ravel()
            n_all = np.bincount(inv, minlength=u.size)
            n_bad = np.bincount(inv, weights=bad[keep].astype(np.float64), minlength=u.size)
            spare = np.isin(dot_sid, u[n_bad >= n_all])
            bad &= ~spare
            keep &= ~bad
            st["member_dots_masked_peak"] = int(bad.sum())
    if C.FIXED_MEMBER_ONE_PER_PHOTO and "photo" in dots:
        idx = np.flatnonzero(keep)
        sid = dot_sid[idx]
        photo = dots["photo"].to_numpy(np.int64)[idx]
        fs = fixed["source_id"].to_numpy(np.int64)
        order_f = np.argsort(fs, kind="stable")
        k = order_f[np.searchsorted(fs, sid, sorter=order_f)]
        sep = angsep(dots["ra"].to_numpy(np.float64)[idx], dots["dec"].to_numpy(np.float64)[idx],
                     fixed["ra"].to_numpy(np.float64)[k], fixed["dec"].to_numpy(np.float64)[k])
        o = np.lexsort((sep, photo, sid))
        first = np.r_[True, (np.diff(sid[o]) != 0) | (np.diff(photo[o]) != 0)]
        drop = idx[o[~first]]
        keep[drop] = False
        st["member_dots_second_in_photo"] = int(drop.size)
    return keep, st


def members_table(dots: pd.DataFrame, dot_sid: np.ndarray, photos: pd.DataFrame,
                  keep: np.ndarray | None = None) -> pd.DataFrame:
    """members.parquet: one row per dot of a fixed source, sorted by source_id, obs_id, detector, dot_row.
    `dots` needs `photo` (row in the photo list) and `dot_row` (row in that photo's dot file).
    keep: member_mask's selection (default: every dot of every fixed group)."""
    m = dot_sid >= 0 if keep is None else (dot_sid >= 0) & keep
    # `photo` is the photo list's index label (load_dots); positions via get_indexer
    pos = photos.index.get_indexer(dots["photo"].to_numpy()[m])
    obs = pd.Categorical(photos["obs_id"].astype(str))
    out = pd.DataFrame({
        "source_id": dot_sid[m].astype(np.int64),
        "obs_id": pd.Categorical.from_codes(obs.codes[pos], obs.categories),
        "detector": photos["detector"].to_numpy()[pos].astype(np.int8),
        "dot_row": dots["dot_row"].to_numpy()[m].astype(np.int32),
    })
    return out.sort_values(["source_id", "obs_id", "detector", "dot_row"]).reset_index(drop=True)


def pair_candidates(d: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Valid pairs (i, j) of dots in one session (row positions in d) and their kind
    (0 = two exposures, 1 = simultaneous detectors)."""
    n = len(d)
    if n < 2:
        return np.zeros((0, 2), dtype=np.int64), np.zeros(0, dtype=np.int8)
    mjd = d["mjd"].to_numpy(np.float64)
    span_min = (mjd.max() - mjd.min()) * 1440.0
    rmax = max(C.PAIR_BASE_ARCSEC + C.PAIR_RATE_ARCSEC_PER_MIN * span_min, C.SIMULTANEOUS_PAIR_ARCSEC)
    xyz = radec_to_xyz(d["ra"].to_numpy(), d["dec"].to_numpy())
    from scipy.spatial import cKDTree
    pairs = cKDTree(xyz).query_pairs(chord(rmax), output_type="ndarray")
    if len(pairs) == 0:
        return np.zeros((0, 2), dtype=np.int64), np.zeros(0, dtype=np.int8)
    i, j = pairs[:, 0], pairs[:, 1]
    ra, dec = d["ra"].to_numpy(), d["dec"].to_numpy()
    sep = angsep(ra[i], dec[i], ra[j], dec[j]) * 3600.0
    exp = d["exposure"].to_numpy()
    det = d["detector"].to_numpy(np.int64)
    dt_min = np.abs(mjd[i] - mjd[j]) * 1440.0
    two_exp = (exp[i] != exp[j]) & (sep <= C.PAIR_BASE_ARCSEC + C.PAIR_RATE_ARCSEC_PER_MIN * dt_min)
    pair_ok = np.zeros(len(i), dtype=bool)
    for a, b in C.SIMULTANEOUS_PAIRS:
        pair_ok |= ((det[i] == a) & (det[j] == b)) | ((det[i] == b) & (det[j] == a))
    simul = (exp[i] == exp[j]) & pair_ok & (sep <= C.SIMULTANEOUS_PAIR_ARCSEC)
    keep = two_exp | simul
    return pairs[keep], np.where(two_exp[keep], 0, 1).astype(np.int8)


def find_candidates(dots: pd.DataFrame) -> pd.DataFrame:
    """Movers before the coverage test: chained pairs inside each session."""
    rows = []
    for sess, d in dots.groupby("session", sort=True):
        d = d.reset_index(drop=True)
        pairs, kind = pair_candidates(d)
        if len(pairs) == 0:
            continue
        n = len(d)
        g = coo_matrix((np.ones(len(pairs), dtype=np.int8), (pairs[:, 0], pairs[:, 1])), shape=(n, n))
        _, lab = connected_components(g, directed=False)
        used = np.zeros(n, dtype=bool)
        used[pairs.ravel()] = True
        idx = np.flatnonzero(used)
        idx = idx[np.argsort(lab[idx], kind="stable")]
        cuts = np.flatnonzero(np.diff(lab[idx])) + 1
        A = {c: d[c].to_numpy() for c in ("ra", "dec", "mjd", "visit", "exposure", "detector", "snr", "flux_ujy")}
        fl = d["flags"].to_numpy(np.int64) & 0xFFFFFFFF
        fp = d["flags_peak"].to_numpy(np.int64) & 0xFFFFFFFF
        for m in np.split(idx, cuts):
            k = m[int(np.argmax(A["snr"][m]))]
            exps = sorted(set(A["exposure"][m].tolist()))
            rows.append({
                "ra": float(A["ra"][m].mean()), "dec": float(A["dec"][m].mean()), "mjd": float(A["mjd"][m].mean()),
                "session": int(sess), "visit": int(np.bincount(A["visit"][m].astype(np.int64)).argmax()),
                "n_dots": int(m.size), "n_exposures": len(exps),
                "detectors": ",".join(str(x) for x in sorted(set(A["detector"][m].astype(int).tolist()))),
                "det": int(A["detector"][k]),
                "evidence": "two_exposures" if len(exps) >= 2 else "two_detectors",
                "snr": float(A["snr"][m].max()), "flux_ujy": float(np.median(A["flux_ujy"][m])),
                "flags": int(np.bitwise_or.reduce(fl[m])), "flags_peak_all": int(np.bitwise_and.reduce(fp[m])),
                "exposures": exps,
            })
    return pd.DataFrame(rows)


def classify(dots: pd.DataFrame, photos: pd.DataFrame, footprints: list[Footprint] | None = None,
             exposure_names: list[str] | None = None,
             fixed_result: tuple[pd.DataFrame, np.ndarray] | None = None) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """dots (already clipped to the zone) -> (fixed, movers, counts).
    fixed_result: fixed_groups(dots) if already computed."""
    st: dict = {"dots": int(len(dots))}
    fixed, dot_sid = fixed_result if fixed_result is not None else fixed_groups(dots)
    member = dot_sid >= 0
    st["fixed"] = int(len(fixed))
    free = dots[~member]
    if len(fixed) and len(free):
        sep, _ = SkyTree(fixed["ra"].to_numpy(), fixed["dec"].to_numpy()).nearest(free["ra"].to_numpy(), free["dec"].to_numpy())
        free = free[sep > C.FIXED_VETO_ARCSEC]
    st["dots_near_fixed_dropped"] = int(len(dots) - len(free))
    elong = free["elong"].to_numpy(np.float64)
    free = free[~(elong > C.MOVER_MAX_ELONG)]
    st["dots_free"] = int(len(free))
    cand = find_candidates(free)
    st["candidates"] = int(len(cand))
    if len(cand) == 0:
        return fixed, cand, {**st, "movers": 0}
    if footprints is None:
        footprints = [Footprint.from_s_region(s) for s in photos["s_region"]]
    cand["cover_sessions"] = sessions_covering(cand["ra"].to_numpy(), cand["dec"].to_numpy(), footprints, photos["session"].to_numpy())
    movers = cand[cand["cover_sessions"] >= C.MOVER_MIN_COVER_SESSIONS].reset_index(drop=True)
    st["dropped_under_covered"] = int(len(cand) - len(movers))
    names = exposure_names
    movers["obs_ids"] = [",".join(names[i] if names else str(i) for i in ex) for ex in movers.pop("exposures")]
    movers["cell"] = cell_of(movers["ra"].to_numpy(), movers["dec"].to_numpy())
    for c in ("flags", "flags_peak_all"):
        movers[c] = (movers[c].to_numpy(np.int64) & 0xFFFFFFFF).astype(np.uint32).view(np.int32)
    movers = movers.astype({"session": "int32", "visit": "int16", "n_dots": "int32", "det": "int8", "cell": "int64"})
    st.update({"movers": int(len(movers)),
               "movers_two_exposures": int((movers["evidence"] == "two_exposures").sum()),
               "movers_two_detectors": int((movers["evidence"] == "two_detectors").sum())})
    return fixed, movers, st


def run(store: Store, zone: C.Zone, workers: int = 16, keep: dict | None = None) -> dict:
    """keep: a dict that receives the zone's dots and their per-dot source_id (-1 = no fixed group); tiles.py
    checks with them that its border was wide enough."""
    t0 = time.time()
    photos = store.read_parquet(photolist_rel(zone.name))
    dots, missing = load_dots(store, photos, zone, workers)
    L.info("link %s: %d dots inside the zone from %d photos (%d photos have no dot file yet) in %.0f s",
           zone.name, len(dots), len(photos) - missing, missing, time.time() - t0)
    if missing:
        L.warning("link %s: %d photos are missing dot files; run `dots` first for a complete result", zone.name, missing)
    t1 = time.time()
    fixed_result = fixed_groups(dots)
    fixed, movers, stats = classify(dots, photos, exposure_names=sorted(photos["obs_id"].unique()), fixed_result=fixed_result)
    measured, mst = member_mask(dots, fixed_result[1], fixed_result[0])
    members = members_table(dots, fixed_result[1], photos, keep=measured)
    if keep is not None:
        keep.update(dots=dots, dot_sid=fixed_result[1])
    stats["fixed_member_dots"] = int(len(members))
    stats.update(mst)
    L.info("link %s: classified in %.0f s: %s", zone.name, time.time() - t1, stats)
    store.write_parquet(results_rel(zone.name, "fixed.parquet"), fixed)
    store.write_parquet(results_rel(zone.name, "members.parquet"), members)
    store.write_parquet(results_rel(zone.name, "movers.parquet"), movers)
    return {**stats, "photos_missing_dots": int(missing), "wall_s": round(time.time() - t0, 1)}
