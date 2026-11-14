"""Step 4, `link`: fixed sources, then movers.

Only dots inside the zone box are used (most of a photo's dots fall outside it).
1. fixed: friends-of-friends within FIXED_LINK_ARCSEC over all dots; a group seen in
   >= FIXED_MIN_SESSIONS sessions is a fixed source (stars, galaxies). Before that, release_far_members takes out of
   such a group the dots farther than FIXED_RELEASE_ARCSEC from its median, those >= FIXED_RELEASE_FLUX_RATIO × its
   median flux in their detector, and the off dots of a session that sits off the group's other sessions (of its visit):
   an asteroid chained into a star by the FoF comes back out. Released dots regroup among themselves; such a group in
   >= FIXED_MIN_SESSIONS sessions stays a fixed source only if catalogued (refcat/<zone>.parquet) or seen in >= half of
   its parent's sessions, else its far dots go back to the parent (star wing fragments are not new sources).
   Each fixed source gets a `source_id`
   (source_id_of: cell × SOURCE_ID_PER_CELL + rank in the cell) and its dots are listed in
   members.parquet (source_id, obs_id, detector, dot_row) for `spectra`, less those member_mask leaves out
   (a masked core at the peak; a second dot in one photo). Those still belong to the group for the veto.
2. Dots in a fixed group or within FIXED_VETO_ARCSEC of a fixed source are dropped, unless >= FIXED_VETO_BRIGHT_RATIO ×
   the flux of every fixed source that close (an asteroid passing a fainter star).
3. The rest (elong <= MOVER_MAX_ELONG) are paired inside each session:
   - two exposures (different obs_id) separated by <= PAIR_BASE_ARCSEC + PAIR_RATE × Δt (minutes);
   - or a simultaneous detector pair (same obs_id, D1+D4 / D2+D5 / D3+D6) within
     SIMULTANEOUS_PAIR_ARCSEC (the two footprints are offset ~71″ but see the same sky);
   - two dots that both have a simultaneous partner also pair up to PAIR_CONFIRMED_RATE × Δt (fast movers).
   Pairs chain into one candidate (an asteroid seen in 3–5 exposures); track_fit gives its straight-line
   rate, direction, line residual and per-detector flux ratio.
4. A candidate is a mover if the sessions whose footprints contain it number >= MOVER_MIN_COVER_SESSIONS
   and it passes vet_reason (MOVER_VET): a dot in every exposure of its session that covers the track (one miss allowed
   from 3 exposures on, or with 2 exposures that both have a simultaneous detector pair; an exposure doesn't cover where
   the track is < MOVER_COVER_EDGE_PX inside the photo, outside the zone box, next to a fixed source, or merged into
   another detection), at least two exposures (or its one exposure) away from the frame edges, on a straight line, one
   flux per detector, and no free dot at its place in another session (a star seen in < 3 sessions). Each rule counts
   in the stats (vetoed_<rule>).
5. nonfixed_dots.parquet: every dot in no fixed group with elong <= MOVER_MAX_ELONG (near_fixed: within
   FIXED_VETO_ARCSEC of one), where `tracklets` looks for a track's missed sightings.
Sessions are the zone's time runs (photolist); visit labels only choose the reference sessions of the release.
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
from .refcat import refcat_rel
from .sky import Footprint, SkyTree, angsep, cell_of, chord, fof_groups, offset_radec, radec_to_xyz, sessions_covering
from .store import Store, dots_rel, photolist_rel, results_rel

L = log.get("link")

LOAD_COLUMNS = ["detector", "mjd", "ra", "dec", "flux_ujy", "snr", "flags", "flags_peak", "elong"]
# carried into each mover's dot lists for the suspects' gates (vet.py); NaN where a dot file lacks them
VET_LOAD_COLUMNS = ["x", "y", "a_arcsec", "b_arcsec", "psf_chi2_nu"]
VET_COLUMNS = ["x", "y", "size", "psf_chi2_nu"]  # size = √(a_arcsec · b_arcsec)


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
        df = df[[c for c in LOAD_COLUMNS + VET_LOAD_COLUMNS if c in df.columns]]
        df = df.assign(size=np.sqrt(df["a_arcsec"] * df["b_arcsec"]).astype(np.float32)
                       if "a_arcsec" in df and "b_arcsec" in df else np.float32(np.nan)).drop(
            columns=["a_arcsec", "b_arcsec"], errors="ignore")
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
    cols = [*LOAD_COLUMNS, "session", "visit", "photo", "exposure", "dot_row", *VET_COLUMNS]
    if not parts:
        return pd.DataFrame({c: pd.Series(dtype="float64") for c in cols}), missing
    df = pd.concat(parts, ignore_index=True)
    for c in ("flags_peak", "elong", *VET_COLUMNS):
        if c not in df:
            df[c] = 0 if c == "flags_peak" else np.float32(np.nan)
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


def dot_sigma_arcsec(snr) -> np.ndarray:
    """1σ position scatter of a dot (″): contracts/dots.md's pos_err from its S/N, × LINK_POS_ERR_SCALE (raw pos_err is
    1.4–1.8× too small against Gaia); 10″ × the scale where the S/N is missing or not positive."""
    snr = np.asarray(snr, np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        e = np.sqrt((C.POS_ERR_K * C.PSF_SIGMA_ARCSEC / snr) ** 2 + C.POS_ERR_FLOOR_ARCSEC ** 2)
    return np.where(np.isfinite(e) & (snr > 0), e, 10.0) * C.LINK_POS_ERR_SCALE


def _not_saturated(dots: pd.DataFrame, idx: np.ndarray) -> np.ndarray:
    if "flags_peak" not in dots:
        return np.ones(idx.size, bool)
    return (dots["flags_peak"].to_numpy(np.int64)[idx] & SAT_BITS) == 0


def bright_members(dots: pd.DataFrame, lab: np.ndarray, cand: np.ndarray) -> np.ndarray:
    """Dots of the would-be fixed groups (cand) with flux >= FIXED_RELEASE_FLUX_RATIO × their group's median flux in their
    detector, not saturated at the peak: an asteroid chained into a fainter star (Alekto s75: 5.6× in D3)."""
    out = np.zeros(lab.size, bool)
    if C.FIXED_RELEASE_FLUX_RATIO is None or "flux_ujy" not in dots or "detector" not in dots or not cand.any():
        return out
    idx = np.flatnonzero(cand)
    flux = dots["flux_ujy"].to_numpy(np.float64)[idx]
    key = lab[idx] * 8 + dots["detector"].to_numpy(np.int64)[idx]
    med = pd.Series(flux).groupby(key).transform("median").to_numpy(np.float64)
    out[idx] = (med > 0) & (flux >= C.FIXED_RELEASE_FLUX_RATIO * med) & _not_saturated(dots, idx)
    return out


def offset_sessions(dots: pd.DataFrame, lab: np.ndarray, cand: np.ndarray, agg: pd.DataFrame) -> np.ndarray:
    """Dots of the would-be fixed groups (cand) released from an off session: one whose mean position lies more than
    FIXED_RELEASE_SESSION_ARCSEC + FIXED_RELEASE_SESSION_SIGMA × σ (of that mean) from the median of the group's session
    means (those of its visit when the group has >= FIXED_MIN_SESSIONS sessions there: a fast star moves between visits,
    not within one; else all) and more than FIXED_RELEASE_SESSION_SPREAD × the median offset of those sessions (a slow
    mover drifts through all of them and stays one group). Of such a session only the dots themselves more than
    FIXED_RELEASE_SESSION_ARCSEC + FIXED_RELEASE_SESSION_SIGMA × their σ from that median go (the star's own dots stay).
    An asteroid's one-session chain 2–6″ off a star (Ljuba s242: 6.2″ off a group of 4 sessions). agg: the groups'
    median ra, dec (index = label)."""
    out = np.zeros(lab.size, bool)
    if C.FIXED_RELEASE_SESSION_ARCSEC is None or not cand.any():
        return out
    idx = np.flatnonzero(cand)
    g = lab[idx]
    ra0, dec0 = agg["ra"].reindex(g).to_numpy(), agg["dec"].reindex(g).to_numpy()
    x = ((dots["ra"].to_numpy(np.float64)[idx] - ra0 + 180.0) % 360.0 - 180.0) * np.cos(np.deg2rad(dec0)) * 3600.0
    y = (dots["dec"].to_numpy(np.float64)[idx] - dec0) * 3600.0
    s2 = dot_sigma_arcsec(dots["snr"].to_numpy(np.float64)[idx]) ** 2
    visit = dots["visit"].to_numpy(np.int64)[idx] if "visit" in dots else np.zeros(idx.size, np.int64)
    d = pd.DataFrame({"g": g, "s": dots["session"].to_numpy(np.int64)[idx], "v": visit, "x": x, "y": y, "s2": s2})
    ss = d.groupby(["g", "s"]).agg(v=("v", "first"), x=("x", "mean"), y=("y", "mean"), s2=("s2", "sum"), n=("x", "size"))
    ss["sig"] = np.sqrt(ss["s2"]) / ss["n"]
    ss = ss.reset_index()
    by_v = ss.groupby(["g", "v"]).agg(xv=("x", "median"), yv=("y", "median"), nv=("s", "size")).reset_index()
    by_g = ss.groupby("g").agg(xg=("x", "median"), yg=("y", "median")).reset_index()
    ss = ss.merge(by_v, on=["g", "v"], how="left").merge(by_g, on="g", how="left")
    in_visit = ss["nv"].to_numpy() >= C.FIXED_MIN_SESSIONS
    xr = np.where(in_visit, ss["xv"].to_numpy(), ss["xg"].to_numpy())
    yr = np.where(in_visit, ss["yv"].to_numpy(), ss["yg"].to_numpy())
    off = np.hypot(ss["x"].to_numpy() - xr, ss["y"].to_numpy() - yr)
    lim = C.FIXED_RELEASE_SESSION_ARCSEC + C.FIXED_RELEASE_SESSION_SIGMA * ss["sig"].to_numpy()
    if C.FIXED_RELEASE_SESSION_SPREAD is not None:  # a drift through every session (slow mover) is no outlier session
        ref = np.where(in_visit, ss["v"].to_numpy(np.int64), np.iinfo(np.int64).min)
        spread = pd.Series(off).groupby([ss["g"].to_numpy(), ref]).transform("median").to_numpy(np.float64)
        lim = np.maximum(lim, C.FIXED_RELEASE_SESSION_SPREAD * spread)
    bad = off > lim
    if bad.any():  # of such a session, the dots off the reference themselves (the star's own dots stay members)
        key = pd.MultiIndex.from_arrays([ss["g"].to_numpy(), ss["s"].to_numpy()])
        dkey = pd.MultiIndex.from_arrays([g, d["s"].to_numpy()])
        pos = key.get_indexer(dkey)
        own = np.hypot(x - xr[pos], y - yr[pos]) > C.FIXED_RELEASE_SESSION_ARCSEC + C.FIXED_RELEASE_SESSION_SIGMA * np.sqrt(s2)
        out[idx] = bad[pos] & own
    return out


def catalogued(ra: np.ndarray, dec: np.ndarray, mjd: np.ndarray, refcat: pd.DataFrame | None,
               radius: float = C.FIXED_RELEASE_CATALOG_ARCSEC) -> np.ndarray:
    """True where a refcat entry (Gaia, CatWISE, 2MASS), moved by its proper motion to that position's mjd, is within
    radius. refcat: ra, dec and optionally epoch_mjd, pmra, pmdec (mas/yr, pmra × cos dec); None = nothing catalogued."""
    out = np.zeros(len(ra), bool)
    if refcat is None or not len(refcat) or not len(ra):
        return out
    cra, cdec = refcat["ra"].to_numpy(np.float64), refcat["dec"].to_numpy(np.float64)
    hits = SkyTree(cra, cdec).query_radius(ra, dec, radius + 30.0)  # 30″: 1.2″/yr over 25 years (2MASS epochs)
    n = np.array([len(h) for h in hits], np.int64)
    if not n.sum():
        return out
    gi = np.repeat(np.arange(len(ra)), n)
    ci = np.concatenate([np.asarray(h, np.int64) for h in hits if len(h)])
    has_pm = all(c in refcat for c in ("epoch_mjd", "pmra", "pmdec"))
    dt = np.nan_to_num((np.asarray(mjd, np.float64)[gi] - refcat["epoch_mjd"].to_numpy(np.float64)[ci]) / 365.25) if has_pm else 0.0
    pmra = np.nan_to_num(refcat["pmra"].to_numpy(np.float64)[ci]) if has_pm else 0.0
    pmdec = np.nan_to_num(refcat["pmdec"].to_numpy(np.float64)[ci]) if has_pm else 0.0
    d2 = cdec[ci] + pmdec * dt / 3.6e6
    r2 = cra[ci] + pmra * dt / 3.6e6 / np.maximum(np.cos(np.deg2rad(cdec[ci])), 1e-6)
    close = angsep(np.asarray(ra, np.float64)[gi], np.asarray(dec, np.float64)[gi], r2, d2) * 3600.0 <= radius
    out[np.unique(gi[close])] = True
    return out


def release_far_members(dots: pd.DataFrame, lab: np.ndarray, refcat: pd.DataFrame | None = None,
                        stats: dict | None = None) -> np.ndarray:
    """FoF labels with members of would-be fixed groups (seen in >= FIXED_MIN_SESSIONS sessions) split off:
    - far: dots farther than FIXED_RELEASE_ARCSEC from the group's median position;
    - bright: dots >= FIXED_RELEASE_FLUX_RATIO × the group's median flux in their detector (bright_members);
    - offset: every dot of a session whose mean sits off the group's other sessions (offset_sessions).
    The released dots get new labels from a second FoF among themselves (a second star chained to the first stays a group;
    an asteroid's one-session chain becomes free). A released group seen in >= FIXED_MIN_SESSIONS sessions stays a group
    (a fixed source) only when catalogued (refcat within FIXED_RELEASE_CATALOG_ARCSEC) or seen in >= FIXED_RELEASE_KEEP_SHARE
    of its parent's sessions; otherwise its far-only dots go back to the parent and its bright/offset dots get one label
    per session (free: wing fragments are not new sources). Groups below the session count are left as they are.
    stats: a dict that receives the counts (release_*)."""
    lab = np.asarray(lab, np.int64)
    st = {"release_far_dots": 0, "release_bright_dots": 0, "release_offset_dots": 0, "release_groups_kept": 0,
          "release_groups_undone": 0}
    if stats is not None:
        stats.update(st)
    if lab.size == 0 or C.FIXED_RELEASE_ARCSEC is None:
        return lab
    ra, dec = dots["ra"].to_numpy(np.float64), dots["dec"].to_numpy(np.float64)
    sess = dots["session"].to_numpy(np.int64)
    d = pd.DataFrame({"g": lab, "session": sess, "ra": ra, "dec": dec})
    agg = d.groupby("g").agg(n_sessions=("session", "nunique"), ra=("ra", "median"), dec=("dec", "median"))
    big = agg.index[agg["n_sessions"] >= C.FIXED_MIN_SESSIONS].to_numpy()
    cand = np.isin(lab, big)
    if not cand.any():
        return lab
    mra = agg["ra"].reindex(lab[cand]).to_numpy()
    mdec = agg["dec"].reindex(lab[cand]).to_numpy()
    far = np.zeros(lab.size, dtype=bool)
    far[cand] = angsep(ra[cand], dec[cand], mra, mdec) * 3600.0 > C.FIXED_RELEASE_ARCSEC
    bright = bright_members(dots, lab, cand)
    offset = offset_sessions(dots, lab, cand, agg)
    loose = bright | offset  # never fixed again as a fragment group
    rel = far | loose
    st.update(release_far_dots=int(far.sum()), release_bright_dots=int(bright.sum()), release_offset_dots=int(offset.sum()))
    if not rel.any():
        if stats is not None:
            stats.update(st)
        return lab
    out = lab.copy()
    idx = np.flatnonzero(rel)
    new = lab.max() + 1 + fof_groups(ra[idx], dec[idx], C.FIXED_LINK_ARCSEC, C.FIXED_SNAP_ARCSEC)
    out[idx] = new
    if C.FIXED_RELEASE_KEEP_SHARE is not None:
        mjd = dots["mjd"].to_numpy(np.float64)[idx] if "mjd" in dots else np.zeros(idx.size)
        r = pd.DataFrame({"g": new, "parent": lab[idx], "session": sess[idx], "ra": ra[idx], "dec": dec[idx], "mjd": mjd})
        ga = r.groupby("g").agg(n_sessions=("session", "nunique"), ra=("ra", "median"), dec=("dec", "median"),
                                mjd=("mjd", "median"))
        ga = ga[ga["n_sessions"] >= C.FIXED_MIN_SESSIONS]
        if len(ga):
            # parent = the original group most of its dots came from (ties: the lower label)
            pc = (r[r["g"].isin(ga.index)].groupby(["g", "parent"]).size().rename("n").reset_index()
                  .sort_values(["g", "n", "parent"], ascending=[True, False, True]).drop_duplicates("g").set_index("g")["parent"])
            parent_sessions = agg["n_sessions"].reindex(pc.reindex(ga.index).to_numpy()).to_numpy(np.float64)
            keep = ga["n_sessions"].to_numpy(np.float64) >= C.FIXED_RELEASE_KEEP_SHARE * parent_sessions
            keep |= catalogued(ga["ra"].to_numpy(), ga["dec"].to_numpy(), ga["mjd"].to_numpy(), refcat)
            st.update(release_groups_kept=int(keep.sum()), release_groups_undone=int((~keep).sum()))
            undone = np.isin(new, ga.index.to_numpy()[~keep])
            back = undone & ~loose[idx]
            out[idx[back]] = lab[idx[back]]
            free = undone & loose[idx]
            if free.any():  # one label per (group, session): below FIXED_MIN_SESSIONS by construction
                _, code = np.unique(np.stack([new[free], sess[idx[free]]], axis=1), axis=0, return_inverse=True)
                out[idx[free]] = max(int(lab.max()), int(new.max())) + 1 + code.ravel()
    if stats is not None:
        stats.update(st)
    return out


def fixed_groups(dots: pd.DataFrame, refcat: pd.DataFrame | None = None,
                 stats: dict | None = None) -> tuple[pd.DataFrame, np.ndarray]:
    """(fixed sources sorted by source_id, per-dot source_id; -1 where the dot is in no fixed group).
    refcat: refcat/<zone>.parquet (load_refcat) for release_far_members' catalogue test; stats: receives its counts."""
    if len(dots) == 0:
        return pd.DataFrame({c: pd.Series(dtype="int64" if c in ("source_id", "cell") else "float64")
                             for c in FIXED_COLUMNS}), np.zeros(0, np.int64)
    lab = fof_groups(dots["ra"].to_numpy(), dots["dec"].to_numpy(), C.FIXED_LINK_ARCSEC, C.FIXED_SNAP_ARCSEC)
    lab = release_far_members(dots, lab, refcat, stats)
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


FREEDOTS_NAME = "freedots.parquet"


def freedots_table(free: pd.DataFrame, photos: pd.DataFrame) -> pd.DataFrame:
    """freedots.parquet: one row per free dot (not in or near a fixed source, elong <= MOVER_MAX_ELONG; the dots movers
    are paired from): obs_id, detector, x, y (pixels in the photo), ra, dec. The suspects' hot-pixel and column maps."""
    pos = photos.index.get_indexer(free["photo"].to_numpy()) if len(free) else np.zeros(0, np.int64)
    obs = pd.Categorical(photos["obs_id"].astype(str))
    out = pd.DataFrame({
        "obs_id": pd.Categorical.from_codes(obs.codes[pos], obs.categories),
        "detector": free["detector"].to_numpy().astype(np.int8),
        "x": free["x"].to_numpy().astype(np.float32), "y": free["y"].to_numpy().astype(np.float32),
        "ra": free["ra"].to_numpy(np.float64), "dec": free["dec"].to_numpy(np.float64),
    })
    return out.sort_values(["obs_id", "detector", "x", "y"], kind="stable").reset_index(drop=True)


NONFIXED_DOTS_NAME = "nonfixed_dots.parquet"


def nonfixed_dots_table(dots: pd.DataFrame, dot_sid: np.ndarray, fixed: pd.DataFrame, photos: pd.DataFrame) -> pd.DataFrame:
    """nonfixed_dots.parquet (for `tracklets`' recovery): every dot in no fixed group with elong <= MOVER_MAX_ELONG (the
    mover dots included), `near_fixed` where within FIXED_VETO_ARCSEC of a fixed source; sorted by session, mjd, ra, dec."""
    d = dots[dot_sid < 0]
    elong = d["elong"].to_numpy(np.float64)
    d = d[~(elong > C.MOVER_MAX_ELONG)]
    near = np.zeros(len(d), bool)
    if len(fixed) and len(d):
        sep, _ = SkyTree(fixed["ra"].to_numpy(), fixed["dec"].to_numpy()).nearest(d["ra"].to_numpy(), d["dec"].to_numpy())
        near = sep <= C.FIXED_VETO_ARCSEC
    pos = photos.index.get_indexer(d["photo"].to_numpy())
    obs = pd.Categorical(photos["obs_id"].astype(str))
    out = pd.DataFrame({
        "ra": d["ra"].to_numpy(np.float64), "dec": d["dec"].to_numpy(np.float64), "mjd": d["mjd"].to_numpy(np.float64),
        "session": d["session"].to_numpy(np.int32), "visit": d["visit"].to_numpy(np.int16),
        "obs_id": pd.Categorical.from_codes(obs.codes[pos], obs.categories) if len(d) else pd.Categorical([], obs.categories),
        "detector": d["detector"].to_numpy(np.int8), "dot_row": d["dot_row"].to_numpy(np.int32),
        "snr": d["snr"].to_numpy(np.float32), "flux_ujy": d["flux_ujy"].to_numpy(np.float32),
        "flags": (d["flags"].to_numpy(np.int64) & 0xFFFFFFFF).astype(np.uint32).view(np.int32),
        "flags_peak": (d["flags_peak"].to_numpy(np.int64) & 0xFFFFFFFF).astype(np.uint32).view(np.int32),
        "elong": d["elong"].to_numpy(np.float32), "near_fixed": near})
    return out.sort_values(["session", "mjd", "ra", "dec"], kind="stable").reset_index(drop=True)


def pair_candidates(d: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Valid pairs (i, j) of dots in one session (row positions in d) and their kind
    (0 = two exposures, 1 = simultaneous detectors). Two dots that each have a simultaneous partner
    (a detector pair confirms each detection) also pair across exposures up to PAIR_CONFIRMED_RATE_ARCSEC_PER_MIN:
    fast movers (> ~140″/h) step too far between exposures for the plain rule."""
    n = len(d)
    if n < 2:
        return np.zeros((0, 2), dtype=np.int64), np.zeros(0, dtype=np.int8)
    from scipy.spatial import cKDTree
    mjd = d["mjd"].to_numpy(np.float64)
    span_min = (mjd.max() - mjd.min()) * 1440.0
    rmax = max(C.PAIR_BASE_ARCSEC + C.PAIR_RATE_ARCSEC_PER_MIN * span_min, C.SIMULTANEOUS_PAIR_ARCSEC)
    ra, dec = d["ra"].to_numpy(), d["dec"].to_numpy()
    exp = d["exposure"].to_numpy()
    det = d["detector"].to_numpy(np.int64)
    xyz = radec_to_xyz(ra, dec)

    def judge(pairs: np.ndarray, rate_per_min: float, confirmed: np.ndarray | None):
        i, j = pairs[:, 0], pairs[:, 1]
        sep = angsep(ra[i], dec[i], ra[j], dec[j]) * 3600.0
        dt_min = np.abs(mjd[i] - mjd[j]) * 1440.0
        two_exp = (exp[i] != exp[j]) & (sep <= C.PAIR_BASE_ARCSEC + rate_per_min * dt_min)
        if confirmed is not None:
            two_exp &= confirmed[i] & confirmed[j]
        pair_ok = np.zeros(len(i), dtype=bool)
        for a, b in C.SIMULTANEOUS_PAIRS:
            pair_ok |= ((det[i] == a) & (det[j] == b)) | ((det[i] == b) & (det[j] == a))
        simul = (exp[i] == exp[j]) & pair_ok & (sep <= C.SIMULTANEOUS_PAIR_ARCSEC)
        return two_exp, simul

    pairs = cKDTree(xyz).query_pairs(chord(rmax), output_type="ndarray")
    if len(pairs) == 0:
        return np.zeros((0, 2), dtype=np.int64), np.zeros(0, dtype=np.int8)
    two_exp, simul = judge(pairs, C.PAIR_RATE_ARCSEC_PER_MIN, None)
    keep = two_exp | simul
    out, kind = pairs[keep], np.where(two_exp[keep], 0, 1).astype(np.int8)
    if C.PAIR_CONFIRMED_RATE_ARCSEC_PER_MIN and simul.any():
        confirmed = np.zeros(n, dtype=bool)
        confirmed[pairs[simul].ravel()] = True
        idx = np.flatnonzero(confirmed)
        rfast = C.PAIR_BASE_ARCSEC + C.PAIR_CONFIRMED_RATE_ARCSEC_PER_MIN * span_min
        fp = cKDTree(xyz[idx]).query_pairs(chord(rfast), output_type="ndarray") if idx.size >= 2 else np.zeros((0, 2), np.int64)
        if len(fp):
            fp = np.sort(idx[fp], axis=1)
            fast, _ = judge(fp, C.PAIR_CONFIRMED_RATE_ARCSEC_PER_MIN, confirmed)
            fp = fp[fast]
            have = set(map(tuple, np.sort(out, axis=1).tolist()))
            new = np.array([p for p in fp.tolist() if tuple(p) not in have], dtype=np.int64).reshape(-1, 2)
            out = np.concatenate([out, new])
            kind = np.concatenate([kind, np.zeros(len(new), np.int8)])
    return out, kind


def track_fit(ra, dec, mjd, exposure, detector, flux, snr=None) -> dict:
    """A candidate's straight-line motion from its dots: one position per exposure (mean of its dots), a
    least-squares line in time. rate_arcsec_h / pa_deg (east of north) NaN with one exposure; line_resid_arcsec =
    the largest distance of an exposure's position from the line (NaN under 3 exposures); flux_ratio = the
    largest max/min flux among one detector's dots (1 if no detector has two); dup_dots = dots beyond one per
    (exposure, detector); motion_snr (two exposures, with snr): the distance between the two exposures' positions over
    its σ (dot_sigma_arcsec), else NaN. `_vx`, `_vy` (″/h east, north) and `_t0` (mean MJD) let classify place the
    track in every photo of the session."""
    ra = np.asarray(ra, np.float64)
    dec = np.asarray(dec, np.float64)
    mjd = np.asarray(mjd, np.float64)
    ra0, dec0, t0 = float(ra.mean()), float(dec.mean()), float(mjd.mean())
    x = ((ra - ra0 + 180.0) % 360.0 - 180.0) * np.cos(np.deg2rad(dec0)) * 3600.0
    y = (dec - dec0) * 3600.0
    th = (mjd - t0) * 24.0
    ue, inv = np.unique(np.asarray(exposure), return_inverse=True)
    cnt = np.bincount(inv).astype(np.float64)
    xe, ye, te = (np.bincount(inv, v) / cnt for v in (x, y, th))
    out = {"rate_arcsec_h": np.nan, "pa_deg": np.nan, "line_resid_arcsec": np.nan, "motion_snr": np.nan,
           "_vx": 0.0, "_vy": 0.0, "_t0": t0}
    if ue.size == 2 and snr is not None:
        se = np.sqrt(np.bincount(inv, dot_sigma_arcsec(snr) ** 2)) / cnt  # σ of each exposure's mean position
        out["motion_snr"] = float(np.hypot(xe[1] - xe[0], ye[1] - ye[0]) / max(float(np.hypot(*se)), 1e-9))
    if ue.size >= 2 and np.ptp(te) > 0:
        tm = te.mean()
        vx = float(np.sum((te - tm) * (xe - xe.mean())) / np.sum((te - tm) ** 2))
        vy = float(np.sum((te - tm) * (ye - ye.mean())) / np.sum((te - tm) ** 2))
        out.update(rate_arcsec_h=float(np.hypot(vx, vy)), pa_deg=float(np.degrees(np.arctan2(vx, vy)) % 360.0), _vx=vx, _vy=vy)
        if ue.size >= 3:
            rx = xe - (xe.mean() + vx * (te - tm))
            ry = ye - (ye.mean() + vy * (te - tm))
            out["line_resid_arcsec"] = float(np.hypot(rx, ry).max())
    det = np.asarray(detector, np.int64)
    flux = np.asarray(flux, np.float64)
    ratio = 1.0
    for dd in np.unique(det):
        f = flux[det == dd]
        if f.size >= 2 and f.min() > 0:
            ratio = max(ratio, float(f.max() / f.min()))
        elif f.size >= 2:
            ratio = np.inf
    out["flux_ratio"] = ratio
    out["dup_dots"] = int(len(det) - len(set(zip(np.asarray(exposure).tolist(), det.tolist()))))
    return out


def inside_box(zone: C.Zone, ra: np.ndarray, dec: np.ndarray, margin_arcsec: float = 0.0) -> np.ndarray:
    """True where (ra, dec) lies inside the zone box less margin_arcsec on every side."""
    m = margin_arcsec / 3600.0
    mr = m / np.maximum(np.cos(np.deg2rad(np.asarray(dec, np.float64))), 1e-6)
    return ((ra >= zone.ra_min + mr) & (ra < zone.ra_max - mr) & (dec >= zone.dec_min + m) & (dec < zone.dec_max - m))


def exposure_coverage(cand: pd.DataFrame, photos: pd.DataFrame, footprints: list[Footprint],
                      fixed: pd.DataFrame | None = None, dots: pd.DataFrame | None = None,
                      zone: C.Zone | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(cover_exposures, missed_exposures, edge_exposures) per candidate, from the photos of its session with the
    track moved along its fitted line to each photo's time:
    - cover: exposures (distinct photo times) with a photo holding the track >= MOVER_COVER_EDGE_PX inside, inside the
      zone box less MOVER_COVER_ZONE_MARGIN_ARCSEC (link clips dots at the box; a 2-dot track's direction is rough),
      not within FIXED_VETO_ARCSEC + MOVER_COVER_FIXED_SLACK_ARCSEC of a fixed source (link drops dots there), and,
      unless the candidate has a dot in that photo, with no dot of the photo (`dots`: every dot, fixed members included)
      within MOVER_COVER_BLEND_ARCSEC of the track nor one of S/N >= MOVER_COVER_BRIGHT[0] within MOVER_COVER_BRIGHT[1]
      (the track merged into another source's detection: no dot of its own is expected);
    - missed: covering exposures with none of the candidate's dots;
    - edge_exposures: exposures in which every dot of the candidate sits in a photo (same time and detector) that
      holds the track less than MOVER_EDGE_VETO_PX inside: frame-edge dots (truncated stars, the outermost
      columns) pair into fake movers.
    A dot belongs to a photo when its time is within 5 s of the photo's and its detector is the photo's.
    MOVER_COVER_FIXED_SLACK_ARCSEC None: no exclusion near fixed sources; dots / zone None: no blend / zone test."""
    n = len(cand)
    cover = np.zeros(n, np.int64)
    missed = np.zeros(n, np.int64)
    edge = np.zeros(n, np.int64)
    if n == 0:
        return cover, missed, edge
    ps = photos["session"].to_numpy(np.int64)
    pm = photos["mjd"].to_numpy(np.float64)
    pdet = photos["detector"].to_numpy(np.int64) if "detector" in photos else np.zeros(len(photos), np.int64)
    edge_deg = C.MOVER_COVER_EDGE_PX * C.PIXEL_ARCSEC / 3600.0
    veto_edge_deg = C.MOVER_EDGE_VETO_PX * C.PIXEL_ARCSEC / 3600.0
    use_fixed = fixed is not None and len(fixed) > 0 and C.MOVER_COVER_FIXED_SLACK_ARCSEC is not None
    ftree = SkyTree(fixed["ra"].to_numpy(np.float64), fixed["dec"].to_numpy(np.float64)) if use_fixed else None
    blend = dots is not None and len(dots) > 0 and (C.MOVER_COVER_BLEND_ARCSEC is not None or C.MOVER_COVER_BRIGHT is not None)
    if blend:
        d_sess = dots["session"].to_numpy(np.int64)
        by_sess = pd.Series(np.arange(len(dots))).groupby(d_sess).indices
        d_mjd, d_det = dots["mjd"].to_numpy(np.float64), dots["detector"].to_numpy(np.int64)
        d_ra, d_dec, d_snr = dots["ra"].to_numpy(np.float64), dots["dec"].to_numpy(np.float64), dots["snr"].to_numpy(np.float64)
    seen_td = cand["_seen"].to_numpy()
    for s, ci in pd.Series(np.arange(n)).groupby(cand["session"].to_numpy(np.int64)):
        ci = ci.to_numpy()
        ks = np.flatnonzero(ps == s)
        if ks.size == 0:
            continue
        times = np.unique(np.round(pm[ks] * 86400.0)) / 86400.0  # one per exposure (D1–D6 share MJD-AVG)
        cov = np.zeros((ci.size, times.size), dtype=bool)
        seen = np.zeros_like(cov)
        clean = np.zeros_like(cov)
        ra, dec = cand["ra"].to_numpy(np.float64)[ci], cand["dec"].to_numpy(np.float64)[ci]
        vx, vy = cand["_vx"].to_numpy(np.float64)[ci], cand["_vy"].to_numpy(np.float64)[ci]
        t0 = cand["_t0"].to_numpy(np.float64)[ci]
        sd = by_sess.get(s, np.zeros(0, np.int64)) if blend else None
        for k in ks:
            j = int(np.argmin(np.abs(times - pm[k])))
            h = (pm[k] - t0) * 24.0
            r2, d2 = offset_radec(ra, dec, vx * h, vy * h)
            ed = footprints[k].edge_distance_deg(r2, d2)
            inside = ed >= edge_deg
            here = np.array([any(abs(t - pm[k]) * 86400.0 <= 5.0 and dd == pdet[k] for t, dd in seen_td[i]) for i in ci])
            seen[:, j] |= here
            clean[:, j] |= here & (ed >= veto_edge_deg)
            if zone is not None:
                inside &= inside_box(zone, r2, d2, C.MOVER_COVER_ZONE_MARGIN_ARCSEC)
            if ftree is not None and inside.any():
                fsep, _ = ftree.nearest(r2, d2)
                inside &= fsep > C.FIXED_VETO_ARCSEC + C.MOVER_COVER_FIXED_SLACK_ARCSEC
            if blend and inside.any() and sd.size:
                q = sd[(np.abs(d_mjd[sd] - pm[k]) * 86400.0 <= 5.0) & (d_det[sd] == pdet[k])]
                merged = np.zeros(ci.size, bool)  # (a seen exposure covers: its own dot is there)
                if q.size and C.MOVER_COVER_BLEND_ARCSEC is not None:
                    bsep, _ = SkyTree(d_ra[q], d_dec[q]).nearest(r2, d2)
                    merged |= bsep <= C.MOVER_COVER_BLEND_ARCSEC
                if q.size and C.MOVER_COVER_BRIGHT is not None:
                    qb = q[d_snr[q] >= C.MOVER_COVER_BRIGHT[0]]
                    if qb.size:
                        bsep, _ = SkyTree(d_ra[qb], d_dec[qb]).nearest(r2, d2)
                        merged |= bsep <= C.MOVER_COVER_BRIGHT[1]
                inside &= here | ~merged
            cov[:, j] |= inside
        cover[ci] = cov.sum(axis=1)
        missed[ci] = (cov & ~seen).sum(axis=1)
        edge[ci] = (seen & ~clean).sum(axis=1)
    return cover, missed, edge


VET_RULES = ("edge", "line", "dup", "flux", "missed", "still")


def vet_reason(m: pd.DataFrame) -> np.ndarray:
    """Why a candidate is not a mover ("" = it is): the first failing rule of VET_RULES.
    edge (fewer than min(2, n_exposures) exposures with a dot away from the photo edges), line (>= 3 exposures off a
    straight line by > MOVER_LINE_MAX_ARCSEC), dup (two dots of one exposure and detector), flux (one detector's
    fluxes differ by more than MOVER_FLUX_RATIO_MAX), missed (exposures covering the track that show nothing:
    > MOVER_MAX_MISSED_FEW with <= 2 exposures seen, > MOVER_MAX_MISSED_PAIRED with 2 that both have a simultaneous
    detector pair, > MOVER_MAX_MISSED with >= 3), still (2 exposures and motion_snr < MOVER_STILL_SIGMA, or `still_free`: a free
    dot of another session within MOVER_STILL_FREE_ARCSEC). Columns a table lacks (old movers) fail nothing."""
    n = len(m)
    out = np.full(n, "", dtype=object)
    if n == 0:
        return out
    nexp = m["n_exposures"].to_numpy(np.int64)
    paired = m["paired_exposures"].to_numpy(np.int64) if "paired_exposures" in m else np.zeros(n, np.int64)
    allowed = np.where(nexp >= 3, C.MOVER_MAX_MISSED,
                       np.where((nexp == 2) & (paired >= 2), C.MOVER_MAX_MISSED_PAIRED, C.MOVER_MAX_MISSED_FEW))
    clean = nexp - (m["edge_exposures"].to_numpy(np.int64) if "edge_exposures" in m else 0)
    msnr = m["motion_snr"].to_numpy(np.float64) if "motion_snr" in m else np.full(n, np.nan)
    still = (nexp == 2) & (msnr < C.MOVER_STILL_SIGMA) if C.MOVER_STILL_SIGMA is not None else np.zeros(n, bool)
    if "still_free" in m:
        still |= m["still_free"].to_numpy(bool)
    bad = {"edge": clean < np.minimum(2, nexp),
           "line": np.nan_to_num(m["line_resid_arcsec"].to_numpy(np.float64)) > C.MOVER_LINE_MAX_ARCSEC,
           "dup": m["dup_dots"].to_numpy(np.int64) > 0,
           "flux": m["flux_ratio"].to_numpy(np.float64) > C.MOVER_FLUX_RATIO_MAX,
           "missed": m["missed_exposures"].to_numpy(np.int64) > allowed,
           "still": still}
    for name in VET_RULES[::-1]:
        out[bad[name]] = name
    return out


def _dot_lists(d: pd.DataFrame, m: np.ndarray, A: dict, fp: np.ndarray) -> dict:
    """One mover's dots as lists (time order), for the suspects' gates: dot_exp (exposure codes; classify turns them
    into dot_obs names), dot_det, dot_x, dot_y, dot_mjd, dot_flux, dot_snr, dot_size, dot_chi2, dot_fpk, dot_row."""
    o = m[np.lexsort((A["ra"][m], A["detector"][m], A["mjd"][m]))]

    def col(c, dt):
        return d[c].to_numpy()[o].astype(dt) if c in d else np.full(o.size, np.nan, dt)

    return {"dot_exp": A["exposure"][o].astype(np.int64), "dot_det": A["detector"][o].astype(np.int8),
            "dot_x": col("x", np.float32), "dot_y": col("y", np.float32), "dot_mjd": A["mjd"][o].astype(np.float64),
            "dot_flux": A["flux_ujy"][o].astype(np.float32), "dot_snr": A["snr"][o].astype(np.float32),
            "dot_size": col("size", np.float32), "dot_chi2": col("psf_chi2_nu", np.float32),
            "dot_fpk": (fp[o] & 0xFFFFFFFF).astype(np.uint32).view(np.int32),
            "dot_row": d["dot_row"].to_numpy()[o].astype(np.int32) if "dot_row" in d else np.full(o.size, -1, np.int32)}


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
            tr = track_fit(A["ra"][m], A["dec"][m], A["mjd"][m], A["exposure"][m], A["detector"][m], A["flux_ujy"][m],
                           A["snr"][m])
            dets_by_exp: dict = {}
            for e_, d_ in zip(A["exposure"][m].tolist(), A["detector"][m].astype(int).tolist()):
                dets_by_exp.setdefault(e_, set()).add(d_)
            paired = sum(any(a in ds and b in ds for a, b in C.SIMULTANEOUS_PAIRS) for ds in dets_by_exp.values())
            rows.append({**tr,
                "ra": float(A["ra"][m].mean()), "dec": float(A["dec"][m].mean()), "mjd": float(A["mjd"][m].mean()),
                "session": int(sess), "visit": int(np.bincount(A["visit"][m].astype(np.int64)).argmax()),
                "n_dots": int(m.size), "n_exposures": len(exps), "paired_exposures": int(paired),
                "detectors": ",".join(str(x) for x in sorted(set(A["detector"][m].astype(int).tolist()))),
                "det": int(A["detector"][k]),
                "evidence": "two_exposures" if len(exps) >= 2 else "two_detectors",
                "snr": float(A["snr"][m].max()), "flux_ujy": float(np.median(A["flux_ujy"][m])),
                "flags": int(np.bitwise_or.reduce(fl[m])), "flags_peak_all": int(np.bitwise_and.reduce(fp[m])),
                "exposures": exps, **_dot_lists(d, m, A, fp),
                "_seen": sorted(set(zip(A["mjd"][m].astype(np.float64).tolist(), A["detector"][m].astype(np.int64).tolist()))),
            })
    return pd.DataFrame(rows)


def bright_near_fixed(free: pd.DataFrame, near: np.ndarray, dots: pd.DataFrame, dot_sid: np.ndarray,
                      fixed: pd.DataFrame, ftree: SkyTree) -> np.ndarray:
    """Per free dot: within FIXED_VETO_ARCSEC of fixed sources (near) and >= FIXED_VETO_BRIGHT_RATIO × the median flux of
    each of them in the dot's detector (its median over all its dots where it has none there), not saturated at the peak:
    an asteroid passing a fainter star keeps its dot (Alekto s75, Bohlinia s61)."""
    out = np.zeros(len(free), bool)
    if C.FIXED_VETO_BRIGHT_RATIO is None or not near.any() or "flux_ujy" not in free or "detector" not in free:
        return out
    m = dot_sid >= 0
    med = pd.Series(dots["flux_ujy"].to_numpy(np.float64)[m]).groupby(
        dot_sid[m] * 8 + dots["detector"].to_numpy(np.int64)[m]).median()
    idx = np.flatnonzero(near)
    hits = ftree.query_radius(free["ra"].to_numpy(np.float64)[idx], free["dec"].to_numpy(np.float64)[idx], C.FIXED_VETO_ARCSEC)
    n = np.array([len(h) for h in hits], np.int64)
    if not n.sum():
        return out
    gi = np.repeat(np.arange(idx.size), n)
    fj = np.concatenate([np.asarray(h, np.int64) for h in hits if len(h)])
    flux = free["flux_ujy"].to_numpy(np.float64)[idx]
    det = free["detector"].to_numpy(np.int64)[idx]
    ref = med.reindex(fixed["source_id"].to_numpy(np.int64)[fj] * 8 + det[gi]).to_numpy(np.float64)
    ref = np.where(np.isfinite(ref), ref, fixed["flux_ujy"].to_numpy(np.float64)[fj])
    ok = (ref > 0) & (flux[gi] >= C.FIXED_VETO_BRIGHT_RATIO * ref)
    all_ok = (np.bincount(gi, weights=ok.astype(np.float64), minlength=idx.size) == n) & (n > 0)
    keep = (free["flags_peak"].to_numpy(np.int64)[idx] & SAT_BITS) == 0 if "flags_peak" in free else True
    out[idx] = all_ok & keep
    return out


def still_free(movers: pd.DataFrame, free: pd.DataFrame) -> np.ndarray:
    """Per candidate: a star seen in fewer than FIXED_MIN_SESSIONS sessions, so never fixed. Free dots (the pairing set) of
    other sessions within MOVER_STILL_FREE_ARCSEC of its position: in >= MOVER_STILL_FREE_MIN_SESSIONS sessions, or one
    in a detector of its own dots with a flux within MOVER_STILL_FREE_FLUX_RATIO of their median there. Boxes (14 Nov): 190
    of 372 unmatched candidates vs 3 of 101 known asteroids had such a dot; those 3 only in another detector or 8× fainter."""
    n = len(movers)
    out = np.zeros(n, bool)
    if C.MOVER_STILL_FREE_ARCSEC is None or not n or not len(free):
        return out
    fsess, fdet = free["session"].to_numpy(np.int64), free["detector"].to_numpy(np.int64)
    fflux = free["flux_ujy"].to_numpy(np.float64) if "flux_ujy" in free else np.full(len(free), np.nan)
    hits = SkyTree(free["ra"].to_numpy(np.float64), free["dec"].to_numpy(np.float64)).query_radius(
        movers["ra"].to_numpy(np.float64), movers["dec"].to_numpy(np.float64), C.MOVER_STILL_FREE_ARCSEC)
    sess = movers["session"].to_numpy(np.int64)
    lists = "dot_det" in movers and "dot_flux" in movers
    lnr = np.log(C.MOVER_STILL_FREE_FLUX_RATIO)
    for i, h in enumerate(hits):
        h = np.asarray(h, np.int64)
        h = h[fsess[h] != sess[i]]
        if not h.size:
            continue
        if np.unique(fsess[h]).size >= C.MOVER_STILL_FREE_MIN_SESSIONS:
            out[i] = True
            continue
        if not lists:
            continue
        dd = np.asarray(movers["dot_det"].iat[i], np.int64)
        ff = np.asarray(movers["dot_flux"].iat[i], np.float64)
        for j in h:
            f0 = ff[dd == fdet[j]]
            if f0.size and np.median(f0) > 0 and fflux[j] > 0 and abs(np.log(fflux[j] / np.median(f0))) <= lnr:
                out[i] = True
                break
    return out


def classify(dots: pd.DataFrame, photos: pd.DataFrame, footprints: list[Footprint] | None = None,
             exposure_names: list[str] | None = None,
             fixed_result: tuple[pd.DataFrame, np.ndarray] | None = None,
             free_out: dict | None = None, zone: C.Zone | None = None) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """dots (already clipped to the zone) -> (fixed, movers, counts).
    fixed_result: fixed_groups(dots) if already computed. free_out: a dict that receives the free dots ("free": the
    dots the movers are paired from, for the suspects' hot-pixel and column maps). zone: the box the dots were clipped to
    (track positions outside it don't cover an exposure); None = no such test."""
    st: dict = {"dots": int(len(dots))}
    fixed, dot_sid = fixed_result if fixed_result is not None else fixed_groups(dots)
    member = dot_sid >= 0
    st["fixed"] = int(len(fixed))
    free = dots[~member]
    st["dots_near_fixed_bright_kept"] = 0
    if len(fixed) and len(free):
        ftree = SkyTree(fixed["ra"].to_numpy(), fixed["dec"].to_numpy())
        sep, _ = ftree.nearest(free["ra"].to_numpy(), free["dec"].to_numpy())
        near = sep <= C.FIXED_VETO_ARCSEC
        bright = bright_near_fixed(free, near, dots, dot_sid, fixed, ftree)
        st["dots_near_fixed_bright_kept"] = int(bright.sum())
        free = free[~near | bright]
    st["dots_near_fixed_dropped"] = int(len(dots) - len(free))
    elong = free["elong"].to_numpy(np.float64)
    free = free[~(elong > C.MOVER_MAX_ELONG)]
    st["dots_free"] = int(len(free))
    if free_out is not None:
        free_out["free"] = free
    cand = find_candidates(free)
    st["candidates"] = int(len(cand))
    if len(cand) == 0:
        return fixed, cand, {**st, "movers": 0}
    if footprints is None:
        footprints = [Footprint.from_s_region(s) for s in photos["s_region"]]
    cand["cover_sessions"] = sessions_covering(cand["ra"].to_numpy(), cand["dec"].to_numpy(), footprints, photos["session"].to_numpy())
    movers = cand[cand["cover_sessions"] >= C.MOVER_MIN_COVER_SESSIONS].reset_index(drop=True)
    st["dropped_under_covered"] = int(len(cand) - len(movers))
    cols = ["cover_exposures", "missed_exposures", "edge_exposures"]
    if "mjd" in photos:
        cover, missed, edge = exposure_coverage(movers, photos, footprints, fixed, dots, zone)
    else:  # a photo list without times: nothing to say about missed exposures or edges
        cover, missed, edge = movers["n_exposures"].to_numpy(np.int64), np.zeros(len(movers), np.int64), np.zeros(len(movers), np.int64)
    for c, v in zip(cols, (cover, missed, edge)):
        movers[c] = v
    movers["still_free"] = still_free(movers, free)
    reason = vet_reason(movers)
    for r in VET_RULES:
        st[f"vetoed_{r}"] = int((reason == r).sum())
    if C.MOVER_VET:
        movers = movers[reason == ""].reset_index(drop=True)
    movers = movers.drop(columns=["_vx", "_vy", "_t0", "_seen"])
    names = exposure_names
    movers["obs_ids"] = [",".join(names[i] if names else str(i) for i in ex) for ex in movers.pop("exposures")]
    movers["dot_obs"] = [[names[i] if names else str(i) for i in ex] for ex in movers.pop("dot_exp")]
    movers["cell"] = cell_of(movers["ra"].to_numpy(), movers["dec"].to_numpy())
    for c in ("flags", "flags_peak_all"):
        movers[c] = (movers[c].to_numpy(np.int64) & 0xFFFFFFFF).astype(np.uint32).view(np.int32)
    movers = movers.astype({"session": "int32", "visit": "int16", "n_dots": "int32", "det": "int8", "cell": "int64"})
    st.update({"movers": int(len(movers)),
               "movers_two_exposures": int((movers["evidence"] == "two_exposures").sum()),
               "movers_two_detectors": int((movers["evidence"] == "two_detectors").sum())})
    return fixed, movers, st


REFCAT_COLUMNS = ["ra", "dec", "epoch_mjd", "pmra", "pmdec"]


def load_refcat(store: Store, zone: C.Zone) -> pd.DataFrame | None:
    """refcat/<zone>.parquet's positions and proper motions (release_far_members' catalogue test); None when the file is
    missing or unreadable (then only FIXED_RELEASE_KEEP_SHARE decides)."""
    rel = refcat_rel(zone.name)
    try:
        if not store.exists(rel):
            return None
        try:
            return store.read_parquet(rel, columns=REFCAT_COLUMNS)
        except Exception:  # noqa: BLE001  (a refcat without proper motions)
            return store.read_parquet(rel, columns=["ra", "dec"])
    except Exception:  # noqa: BLE001
        L.warning("link %s: refcat unreadable; released groups kept by session share only", zone.name)
        return None


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
    rst: dict = {}
    fixed_result = fixed_groups(dots, load_refcat(store, zone), rst)
    free_out: dict = {}
    fixed, movers, stats = classify(dots, photos, exposure_names=sorted(photos["obs_id"].unique()), fixed_result=fixed_result,
                                    free_out=free_out, zone=zone)
    stats.update(rst)
    measured, mst = member_mask(dots, fixed_result[1], fixed_result[0])
    members = members_table(dots, fixed_result[1], photos, keep=measured)
    freedots = freedots_table(free_out.get("free", dots.iloc[:0]), photos)
    if keep is not None:
        keep.update(dots=dots, dot_sid=fixed_result[1])
    stats["fixed_member_dots"] = int(len(members))
    stats.update(mst)
    try:  # tracklets' input; never worth failing link for
        free = nonfixed_dots_table(dots, fixed_result[1], fixed, photos)
        stats["nonfixed_dots_written"] = int(len(free))
    except Exception as e:  # noqa: BLE001
        L.exception("link %s: nonfixed_dots.parquet not written (tracklets will link without recovery)", zone.name)
        free, stats["nonfixed_dots_error"] = None, repr(e)[:300]
    L.info("link %s: classified in %.0f s: %s", zone.name, time.time() - t1, stats)
    store.write_parquet(results_rel(zone.name, "fixed.parquet"), fixed)
    store.write_parquet(results_rel(zone.name, "members.parquet"), members)
    store.write_parquet(results_rel(zone.name, "movers.parquet"), movers)
    store.write_parquet(results_rel(zone.name, FREEDOTS_NAME), freedots)
    if free is not None:
        store.write_parquet(results_rel(zone.name, NONFIXED_DOTS_NAME), free)
    return {**stats, "photos_missing_dots": int(missing), "wall_s": round(time.time() - t0, 1)}
