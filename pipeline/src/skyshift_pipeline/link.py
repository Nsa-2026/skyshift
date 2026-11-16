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
6. Session stacks (config SESSION_STACK, env SKYSHIFT_SESSION_STACK=1, and the `sessionstack` step's files; off: nothing
   here changes): coadd.load_stack_rows gives one row per stacked source of a session (the session-mean mjd, n_exp)
   that has no real dot of its session and detector within SESSION_STACK_DEDUP_ARCSEC (no glitches, nothing near a
   saturated pixel). They join step 1's friends-of-friends as one dot per session, so a source too faint for one photo
   becomes a fixed source after >= FIXED_MIN_SESSIONS sessions (and a slow body a single-visit source for orphans).
   They never pair inside a session (no exposure) and are not in members.parquet (no dot file row):
   stack_members.parquet lists them (source_id, key, stack_row, session, detector, mjd, ra, dec, flux_ujy, snr, n_exp,
   pos_err_arcsec). A stacked source that moves inside its session (coadd.moving_rows) and is in no fixed group gives
   its per-photo positions as dots (dot_row −1): steps 2–4 and the shuffled control treat them as any dot.
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
from .store import Store, dots_dir, dots_rel, photolist_rel, results_rel
from .vet import dot_sigma, whole_pixel

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


def bright_members(dots: pd.DataFrame, lab: np.ndarray, cand: np.ndarray, agg: pd.DataFrame | None = None,
                   stats: dict | None = None) -> np.ndarray:
    """Dots of the would-be fixed groups (cand) with flux >= FIXED_RELEASE_FLUX_RATIO × their group's median flux in their
    detector, not saturated at the peak: an asteroid chained into a fainter star (Alekto s75: 5.6× in D3).
    FIXED_RELEASE_FLUX_SAFE (agg: the groups' median ra, dec, index = label): only such a dot that also sits off the
    group (> FIXED_RELEASE_SESSION_ARCSEC + FIXED_RELEASE_SESSION_SIGMA × its σ from the median), or whose group has its
    bright dots in one session only and sits > FIXED_RELEASE_FLUX_ONE_SESSION_ARCSEC off: a variable star's bright state
    lies on the star and lasts over sessions (a Mira, a young star), an asteroid crossing is one session 2–5″ off.
    stats: receives release_bright_kept_variable (bright dots left in their group by that rule)."""
    out = np.zeros(lab.size, bool)
    if C.FIXED_RELEASE_FLUX_RATIO is None or "flux_ujy" not in dots or "detector" not in dots or not cand.any():
        return out
    idx = np.flatnonzero(cand)
    flux = dots["flux_ujy"].to_numpy(np.float64)[idx]
    key = lab[idx] * 8 + dots["detector"].to_numpy(np.int64)[idx]
    med = pd.Series(flux).groupby(key).transform("median").to_numpy(np.float64)
    bright = (med > 0) & (flux >= C.FIXED_RELEASE_FLUX_RATIO * med) & _not_saturated(dots, idx)
    if C.FIXED_RELEASE_FLUX_SAFE and agg is not None and bright.any():
        b = idx[bright]
        g = lab[b]
        off = angsep(dots["ra"].to_numpy(np.float64)[b], dots["dec"].to_numpy(np.float64)[b],
                     agg["ra"].reindex(g).to_numpy(), agg["dec"].reindex(g).to_numpy()) * 3600.0
        sig = dot_sigma_arcsec(dots["snr"].to_numpy(np.float64)[b])
        n_sess = pd.Series(dots["session"].to_numpy(np.int64)[b]).groupby(g).transform("nunique").to_numpy()
        ok = (off > C.FIXED_RELEASE_SESSION_ARCSEC + C.FIXED_RELEASE_SESSION_SIGMA * sig) | (
            (n_sess == 1) & (off > C.FIXED_RELEASE_FLUX_ONE_SESSION_ARCSEC))
        if stats is not None:
            stats["release_bright_kept_variable"] = int((~ok).sum())
        bright[np.flatnonzero(bright)[~ok]] = False
    out[idx] = bright
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
          "release_groups_undone": 0, "release_bright_kept_variable": 0}
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
    bright = bright_members(dots, lab, cand, agg, st)
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


def off_star_members(dots: pd.DataFrame, dot_sid: np.ndarray, fixed: pd.DataFrame) -> np.ndarray:
    """Member dots of fixed groups that sit off their star in their photo (an asteroid crossing it, chained in by the 4″
    FoF): more than FIXED_OFF_ARCSEC + FIXED_OFF_SIGMA × σ (dot_sigma_arcsec) from the group's median position, or a
    second dot of the group in one photo (not the nearest) more than FIXED_OFF_ARCSEC off; not saturated at the peak, elong
    <= MOVER_MAX_ELONG; minus those with another such dot of the group within FIXED_OFF_REPEAT_ARCSEC in another session
    (a neighbour or a wing piece of the star: it stays put, an asteroid doesn't). tracklets recovers them only on a
    track's predicted path. None (FIXED_OFF_ARCSEC) = none."""
    out = np.zeros(len(dots), bool)
    m = dot_sid >= 0
    if C.FIXED_OFF_ARCSEC is None or not m.any() or not len(fixed):
        return out
    idx = np.flatnonzero(m)
    sid = dot_sid[idx]
    fs = fixed["source_id"].to_numpy(np.int64)
    order_f = np.argsort(fs, kind="stable")
    k = order_f[np.searchsorted(fs, sid, sorter=order_f)]
    ra, dec = dots["ra"].to_numpy(np.float64)[idx], dots["dec"].to_numpy(np.float64)[idx]
    off = angsep(ra, dec, fixed["ra"].to_numpy(np.float64)[k], fixed["dec"].to_numpy(np.float64)[k]) * 3600.0
    far = off > C.FIXED_OFF_ARCSEC + C.FIXED_OFF_SIGMA * dot_sigma_arcsec(dots["snr"].to_numpy(np.float64)[idx])
    second = np.zeros(idx.size, bool)
    if "photo" in dots:
        photo = dots["photo"].to_numpy(np.int64)[idx]
        o = np.lexsort((off, photo, sid))
        first = np.r_[True, (np.diff(sid[o]) != 0) | (np.diff(photo[o]) != 0)]
        second[o[~first]] = True
        second &= off > C.FIXED_OFF_ARCSEC
    c = (far | second) & _not_saturated(dots, idx)
    if "elong" in dots:
        c &= ~(dots["elong"].to_numpy(np.float64)[idx] > C.MOVER_MAX_ELONG)
    ci = np.flatnonzero(c)
    if ci.size and C.FIXED_OFF_REPEAT_ARCSEC:
        lab = fof_groups(ra[ci], dec[ci], C.FIXED_OFF_REPEAT_ARCSEC, min(C.FIXED_SNAP_ARCSEC, C.FIXED_OFF_REPEAT_ARCSEC / 2))
        sess = dots["session"].to_numpy(np.int64)[idx[ci]]
        n_sess = pd.Series(sess).groupby(lab).transform("nunique").to_numpy()
        c[ci[n_sess > 1]] = False
    out[idx[c]] = True
    return out


def nonfixed_dots_table(dots: pd.DataFrame, dot_sid: np.ndarray, fixed: pd.DataFrame, photos: pd.DataFrame) -> pd.DataFrame:
    """nonfixed_dots.parquet (for `tracklets`' recovery): every dot in no fixed group with elong <= MOVER_MAX_ELONG (the
    mover dots included), `near_fixed` where within FIXED_VETO_ARCSEC of a fixed source; plus (16 Nov) the fixed groups'
    member dots that sit off their star (off_star_members, `in_fixed` True, near_fixed True); sorted by session, mjd, ra,
    dec."""
    off = off_star_members(dots, dot_sid, fixed)
    d = dots[(dot_sid < 0) | off]
    in_fixed = off[(dot_sid < 0) | off]
    elong = d["elong"].to_numpy(np.float64)
    d, in_fixed = d[~(elong > C.MOVER_MAX_ELONG)], in_fixed[~(elong > C.MOVER_MAX_ELONG)]
    near = in_fixed.copy()
    if len(fixed) and len(d):
        sep, _ = SkyTree(fixed["ra"].to_numpy(), fixed["dec"].to_numpy()).nearest(d["ra"].to_numpy(), d["dec"].to_numpy())
        near |= sep <= C.FIXED_VETO_ARCSEC
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
        "elong": d["elong"].to_numpy(np.float32), "near_fixed": near, "in_fixed": in_fixed})
    return out.sort_values(["session", "mjd", "ra", "dec"], kind="stable").reset_index(drop=True)


def fast_triplets(d: pd.DataFrame, known: np.ndarray) -> np.ndarray:
    """Pairs (i, j) of dots in one session (row positions in d) beyond the plain rule that belong to a straight,
    evenly spaced chain of >= 3 exposures: two dots of different exposures within PAIR_BASE_ARCSEC +
    PAIR_FAST_RATE_ARCSEC_PER_MIN × Δt, and a dot of a third exposure within PAIR_FAST_LINE_ARCSEC of their constant-rate
    line at that exposure's time; above PAIR_FAST_TRAIL_MIN_ARCSEC_H the three dots' median elong must also reach half the
    trail's expected stretch (PAIR_FAST_TRAIL_FRAC). Each such triplet gives its three pairs. known: pairs already found
    (sorted rows), left out. A fast mover without a detector partner (D5/D6 heat glow, or too faint in the partner) moves
    5–35″ between exposures 2.1 min apart; two unrelated dots rarely line up with a third at the right spacing."""
    from scipy.spatial import cKDTree
    out = np.zeros((0, 2), np.int64)
    if not C.PAIR_FAST_RATE_ARCSEC_PER_MIN or len(d) < 3:
        return out
    exp = d["exposure"].to_numpy(np.int64)
    if np.unique(exp).size < 3:
        return out
    mjd = d["mjd"].to_numpy(np.float64)
    ra, dec = d["ra"].to_numpy(np.float64), d["dec"].to_numpy(np.float64)
    span_min = (mjd.max() - mjd.min()) * 1440.0
    xyz = radec_to_xyz(ra, dec)
    tree = cKDTree(xyz)
    rmax = C.PAIR_BASE_ARCSEC + C.PAIR_FAST_RATE_ARCSEC_PER_MIN * span_min
    pr = tree.query_pairs(chord(rmax), output_type="ndarray")
    if not len(pr):
        return out
    i, k = pr[:, 0], pr[:, 1]
    sep = angsep(ra[i], dec[i], ra[k], dec[k]) * 3600.0
    dt_min = np.abs(mjd[k] - mjd[i]) * 1440.0
    ok = (exp[i] != exp[k]) & (dt_min > 0) & (sep <= C.PAIR_BASE_ARCSEC + C.PAIR_FAST_RATE_ARCSEC_PER_MIN * dt_min)
    ok &= sep > C.PAIR_BASE_ARCSEC + C.PAIR_RATE_ARCSEC_PER_MIN * dt_min  # the plain rule already pairs the rest
    i, k = i[ok], k[ok]
    if not i.size:
        return out
    # each exposure's time (D1–D6 share MJD-AVG; within 5 s = one exposure)
    ue, first = np.unique(exp, return_index=True)
    te = mjd[first]
    # the line through i and k on the plane tangent at i (″ east, north), at every other exposure's time
    cosd = np.cos(np.deg2rad(dec[i]))
    vx = ((ra[k] - ra[i] + 180.0) % 360.0 - 180.0) * cosd * 3600.0 / (mjd[k] - mjd[i])
    vy = (dec[k] - dec[i]) * 3600.0 / (mjd[k] - mjd[i])
    # the trail test (PAIR_FAST_TRAIL_*): the elong the chain's dots must reach at the pair's rate (0: none)
    need = np.zeros(i.size)
    el = d["elong"].to_numpy(np.float64) if "elong" in d else None
    if C.PAIR_FAST_TRAIL_MIN_ARCSEC_H is not None and el is not None:
        rate_h = np.hypot(vx, vy) / 24.0
        L = rate_h * C.PAIR_EXPOSURE_S / 3600.0
        f = np.sqrt(1.0 + L ** 2 / (12.0 * C.PSF_SIGMA_ARCSEC ** 2))
        need = np.where(rate_h > C.PAIR_FAST_TRAIL_MIN_ARCSEC_H, 1.0 + C.PAIR_FAST_TRAIL_FRAC * (f - 1.0), 0.0)
    found = []
    for e, t in zip(ue, te):
        m = (exp[i] != e) & (exp[k] != e)
        if not m.any():
            continue
        dtd = t - mjd[i[m]]
        pra, pdec = offset_radec(ra[i[m]], dec[i[m]], vx[m] * dtd, vy[m] * dtd)
        hits = tree.query_ball_point(radec_to_xyz(pra, pdec), chord(C.PAIR_FAST_LINE_ARCSEC))
        for a, b, nd, h in zip(i[m], k[m], need[m], hits):
            for j in h:
                if exp[j] == e and (nd <= 0 or not np.nanmedian([el[a], el[j], el[b]]) < nd):
                    found.extend(((a, j), (j, b), (a, b)))
    if not found:
        return out
    f = np.unique(np.sort(np.asarray(found, np.int64), axis=1), axis=0)
    if len(known):
        kk = np.sort(known, axis=1)
        have = set(map(tuple, kk.tolist()))
        f = np.array([p for p in f.tolist() if tuple(p) not in have], np.int64).reshape(-1, 2)
    return f


def pair_candidates(d: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Valid pairs (i, j) of dots in one session (row positions in d) and their kind
    (0 = two exposures, 1 = simultaneous detectors, 2 = two exposures beyond the plain rule: fast). Two dots that each
    have a simultaneous partner (a detector pair confirms each detection) also pair across exposures up to
    PAIR_CONFIRMED_RATE_ARCSEC_PER_MIN, and any two dots up to PAIR_FAST_RATE_ARCSEC_PER_MIN as part of a straight
    chain of >= 3 exposures (fast_triplets): fast movers (> ~140″/h) step too far between exposures for the plain rule."""
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
        pairs = np.zeros((0, 2), dtype=np.int64)
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
            kind = np.concatenate([kind, np.full(len(new), 2, np.int8)])
    tri = fast_triplets(d, out)
    if len(tri):
        out = np.concatenate([out, tri])
        kind = np.concatenate([kind, np.full(len(tri), 2, np.int8)])
    return out, kind


def track_fit(ra, dec, mjd, exposure, detector, flux, snr=None, whole=None) -> dict:
    """A candidate's straight-line motion from its dots: one position per exposure (mean of its dots), a
    least-squares line in time. rate_arcsec_h / pa_deg (east of north) NaN with one exposure; line_resid_arcsec =
    the largest distance of an exposure's position from the line (NaN under 3 exposures); flux_ratio = the
    largest max/min flux among one detector's dots (1 if no detector has two); dup_dots = dots beyond one per
    (exposure, detector); motion_snr (two exposures, with snr): the distance between the two exposures' positions over
    its σ (dot_sigma_arcsec; whole: per dot, a whole-pixel centroid, whose rounding LINK_WHOLE_PIXEL_SIGMA_PX adds in
    quadrature, vet.dot_sigma), else NaN. `_vx`, `_vy` (″/h east, north) and `_t0` (mean MJD) let classify place the
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
        se = np.sqrt(np.bincount(inv, dot_sigma(snr, whole) ** 2)) / cnt  # σ of each exposure's mean position
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
        fast = np.zeros(n, dtype=bool)  # a dot of a pair beyond the plain rule (kind 2)
        fast[pairs[kind == 2].ravel()] = True
        idx = np.flatnonzero(used)
        idx = idx[np.argsort(lab[idx], kind="stable")]
        cuts = np.flatnonzero(np.diff(lab[idx])) + 1
        A = {c: d[c].to_numpy() for c in ("ra", "dec", "mjd", "visit", "exposure", "detector", "snr", "flux_ujy")}
        has_xy = "x" in d and "y" in d
        fl = d["flags"].to_numpy(np.int64) & 0xFFFFFFFF
        fp = d["flags_peak"].to_numpy(np.int64) & 0xFFFFFFFF
        for m in np.split(idx, cuts):
            k = m[int(np.argmax(A["snr"][m]))]
            exps = sorted(set(A["exposure"][m].tolist()))
            tr = track_fit(A["ra"][m], A["dec"][m], A["mjd"][m], A["exposure"][m], A["detector"][m], A["flux_ujy"][m],
                           A["snr"][m], whole_pixel(d["x"].to_numpy()[m], d["y"].to_numpy()[m]) if has_xy else None)
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
                "_fast": bool(fast[m].any()),
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
    st["fixed"] = int(len(fixed))
    free = free_dots(dots, dot_sid, fixed, st)
    if free_out is not None:
        free_out["free"] = free
    movers, mst = movers_from_free(free, dots, photos, footprints, fixed, zone, exposure_names)
    return fixed, movers, {**st, **mst}


def free_dots(dots: pd.DataFrame, dot_sid: np.ndarray, fixed: pd.DataFrame, st: dict | None = None) -> pd.DataFrame:
    """The dots movers are paired from: in no fixed group, not within FIXED_VETO_ARCSEC of a fixed source (unless
    bright_near_fixed), elong <= MOVER_MAX_ELONG. st: receives dots_near_fixed_bright_kept, dots_near_fixed_dropped,
    dots_free."""
    st = {} if st is None else st
    free = dots[dot_sid < 0]
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
    return free


def exposure_index(photos: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(session, time in s, rank in its session) of every exposure of the photo list (D1–D6 share MJD-AVG: one exposure
    per distinct second within a session), and per photo row its exposure's position in those arrays."""
    key = pd.DataFrame({"s": photos["session"].to_numpy(np.int64), "t": np.round(photos["mjd"].to_numpy(np.float64) * 86400.0)})
    u = key.drop_duplicates().sort_values(["s", "t"]).reset_index(drop=True)
    u["rank"] = u.groupby("s").cumcount()
    pos = pd.MultiIndex.from_frame(u[["s", "t"]]).get_indexer(pd.MultiIndex.from_frame(key))
    return u["s"].to_numpy(np.int64), u["t"].to_numpy(np.float64), u["rank"].to_numpy(np.int64), pos


def control_offsets(photos: pd.DataFrame, seed: int = C.CONTROL_SEED) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """The shuffled-exposure control's offsets (″ east, north) per exposure (exposure_index's arrays): LINK_CONTROL_OFFSET_
    ARCSEC long, directions spread evenly over each session's exposures (in time order) from a random start per session,
    so two exposures of one session are >= 2 R sin(π/n) apart. Returns (session, time s, east, north)."""
    s, t, rank, _ = exposure_index(photos)
    R = float(C.LINK_CONTROL_OFFSET_ARCSEC or 0.0)
    us, inv = np.unique(s, return_inverse=True)
    n = np.bincount(inv)
    phase = np.random.default_rng(seed).uniform(0.0, 2 * np.pi, us.size)
    ang = phase[inv] + 2 * np.pi * rank / n[inv]
    return s, t, R * np.sin(ang), R * np.cos(ang)


def _shift_to_exposures(df: pd.DataFrame, s: np.ndarray, t: np.ndarray, de: np.ndarray, dn: np.ndarray) -> pd.DataFrame:
    """df's positions moved by their exposure's offset (exposure = same session, time within 5 s)."""
    if not len(df):
        return df
    key = pd.MultiIndex.from_arrays([s, t])
    ds, dt = df["session"].to_numpy(np.int64), np.round(df["mjd"].to_numpy(np.float64) * 86400.0)
    pos = key.get_indexer(pd.MultiIndex.from_arrays([ds, dt]))
    if (pos < 0).any():  # a dot time rounded to another second than its photo's: the nearest exposure of its session
        for i in np.flatnonzero(pos < 0):
            c = np.flatnonzero(s == ds[i])
            pos[i] = c[int(np.argmin(np.abs(t[c] - dt[i])))] if c.size else 0
    ra, dec = offset_radec(df["ra"].to_numpy(np.float64), df["dec"].to_numpy(np.float64), de[pos], dn[pos])
    return df.assign(ra=ra, dec=dec)


def control_movers(free: pd.DataFrame, dots: pd.DataFrame, photos: pd.DataFrame, footprints: list[Footprint] | None,
                   exposure_names: list[str] | None = None, zone: C.Zone | None = None) -> tuple[pd.DataFrame, dict]:
    """The chance level of the mover stage (shuffled-exposure control): every exposure's free dots, all its dots (the
    coverage's blend test) and its photos' footprints moved by control_offsets, then paired, covered and vetted as the
    real ones (no zone box: shifted tracks may leave it; no fixed-source exclusion: the blend test sees the stars' dots).
    No real object (moving or still) links across exposures any more; a simultaneous detector pair stays together.
    Returns (control movers, their counts)."""
    if C.LINK_CONTROL_OFFSET_ARCSEC is None or not len(free) or "mjd" not in photos:
        return free.iloc[:0], {}
    s, t, de, dn = control_offsets(photos)
    f2 = _shift_to_exposures(free, s, t, de, dn)
    d2 = _shift_to_exposures(dots, s, t, de, dn)
    if footprints is None:
        footprints = [Footprint.from_s_region(x) for x in photos["s_region"]]
    _, _, _, ppos = exposure_index(photos)
    fps = []
    for fp, k in zip(footprints, ppos):
        v = fp.vertices
        r2, d2v = offset_radec(v[:, 0], v[:, 1], de[k], dn[k])
        fps.append(Footprint(np.column_stack([r2, d2v])))
    m, st = movers_from_free(f2, d2, photos, fps, None, None, exposure_names)
    return m, st


def control_counts(m: pd.DataFrame) -> dict:
    """summary.json link `control`: the control's movers, by evidence and beyond the plain rule's rate (~146″/h)."""
    if not len(m):
        return {"movers": 0, "two_exposures": 0, "two_detectors": 0, "fast": 0}
    plain = (C.PAIR_BASE_ARCSEC / 2.1 + C.PAIR_RATE_ARCSEC_PER_MIN) * 60.0  # ″/h at one exposure step (2.1 min)
    r = m["rate_arcsec_h"].to_numpy(np.float64)
    return {"movers": int(len(m)), "two_exposures": int((m["evidence"] == "two_exposures").sum()),
            "two_detectors": int((m["evidence"] == "two_detectors").sum()), "fast": int((np.nan_to_num(r) > plain).sum())}


def movers_from_free(free: pd.DataFrame, dots: pd.DataFrame, photos: pd.DataFrame, footprints: list[Footprint] | None,
                     fixed: pd.DataFrame | None, zone: C.Zone | None,
                     exposure_names: list[str] | None = None) -> tuple[pd.DataFrame, dict]:
    """Free dots -> movers (pairs chained per session, coverage, vet_reason) and counts."""
    st: dict = {}
    cand = find_candidates(free)
    st["candidates"] = int(len(cand))
    if len(cand) == 0:
        return cand, {**st, "movers": 0, "movers_fast_rule": 0}
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
    st["movers_fast_rule"] = int(movers["_fast"].sum())  # paired beyond the plain rule (confirmed or 3-exposure chain)
    movers = movers.drop(columns=["_vx", "_vy", "_t0", "_seen", "_fast"])
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
    return movers, st


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


STACK_MEMBER_COLUMNS = ["source_id", "key", "stack_row", "session", "visit", "detector", "mjd", "ra", "dec", "flux_ujy",
                        "snr", "n_exp", "pos_err_arcsec"]
STACK_MEMBERS_NAME = "stack_members.parquet"


def with_stack_rows(store: Store, photos: pd.DataFrame, zone: C.Zone, dots: pd.DataFrame) -> tuple[dict | None, dict]:
    """Session-stack rows for this photo list (module doc, 6) or None (SESSION_STACK off, no stacks, or an error:
    link then runs on the dots alone). Returns ({"rows", "pseudo"}, stats)."""
    if not C.SESSION_STACK:
        return None, {}
    from . import coadd
    try:
        rows, pseudo, st = coadd.load_stack_rows(store, photos, zone, dots)
    except Exception as e:  # noqa: BLE001  link never fails over the stacks
        L.exception("link %s: session-stack rows not loaded", zone.name)
        return None, {"error": repr(e)[:300]}
    if not len(rows):
        return None, st
    return {"rows": rows, "pseudo": pseudo}, st


def stack_members_table(rows: pd.DataFrame, sid: np.ndarray) -> pd.DataFrame:
    """stack_members.parquet: the stack rows of fixed groups (sid >= 0), sorted by source_id, key, stack_row."""
    m = sid >= 0
    out = rows.loc[m, [c for c in STACK_MEMBER_COLUMNS if c != "source_id"]].copy()
    out.insert(0, "source_id", sid[m].astype(np.int64))
    out = out.astype({"key": str, "stack_row": np.int32, "session": np.int32, "visit": np.int16, "detector": np.int8,
                      "n_exp": np.int8, "flux_ujy": np.float32, "snr": np.float32, "pos_err_arcsec": np.float32})
    return out.sort_values(["source_id", "key", "stack_row"]).reset_index(drop=True)[STACK_MEMBER_COLUMNS]


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
    stk, sst = with_stack_rows(store, photos, zone, dots)
    rst: dict = {}
    n_dots = len(dots)
    if stk is None:
        fixed_result = fixed_groups(dots, load_refcat(store, zone), rst)
        dots_cls, sid_cls = dots, fixed_result[1]
    else:  # stack rows join the friends-of-friends; classify sees the real dots + moving stack sources' photo positions
        cols = list(dots.columns)
        rows = stk["rows"]
        both = pd.concat([dots, rows[[c for c in cols if c in rows]]], ignore_index=True)
        fx, sid_all = fixed_groups(both, load_refcat(store, zone), rst)
        fixed_result = (fx, sid_all[:n_dots])
        stack_sid = sid_all[n_dots:]
        ps = stk["pseudo"]
        if ps is not None and len(ps):
            free_rows = set(zip(rows["key"].to_numpy()[stack_sid < 0], rows["stack_row"].to_numpy()[stack_sid < 0]))
            use = np.array([(k, r) in free_rows for k, r in zip(ps["key"], ps["stack_row"])], bool)
            sst["stack_moving_in_fixed"] = int(len({(k, r) for k, r in zip(ps["key"], ps["stack_row"])} - free_rows))
            ps = ps[use]
        else:
            sst["stack_moving_in_fixed"] = 0
        sst["stack_pseudo_dots_used"] = 0 if ps is None else int(len(ps))
        if ps is not None and len(ps):
            dots_cls = pd.concat([dots, ps[[c for c in cols if c in ps]]], ignore_index=True)
            sid_cls = np.r_[fixed_result[1], np.full(len(ps), -1, np.int64)]
        else:
            dots_cls, sid_cls = dots, fixed_result[1]
        smem = stack_members_table(rows, stack_sid)
        own = set(fixed_result[1][fixed_result[1] >= 0].tolist())
        sst.update({"stack_rows_in_fixed": int(len(smem)),
                    "fixed_with_stack_rows": int(smem["source_id"].nunique()),
                    "fixed_stack_only": int(len(set(smem["source_id"].tolist()) - own))})
    free_out: dict = {}
    fixed, movers, stats = classify(dots_cls, photos, exposure_names=sorted(photos["obs_id"].unique()),
                                    fixed_result=(fixed_result[0], sid_cls), free_out=free_out, zone=zone)
    stats.update(rst)
    if stk is not None or sst:
        if stk is not None and len(movers) and "dot_row" in movers:
            sst["movers_with_stack_dots"] = int(sum(bool(np.any(np.asarray(r) < 0)) for r in movers["dot_row"]))
        stats["session_stack"] = sst
    try:  # the chance level of the movers; never worth failing link for
        cm, _ = control_movers(free_out.get("free", dots_cls.iloc[:0]), dots_cls, photos, None, None, zone)
        stats["control"] = control_counts(cm)
        if stk is not None:  # control movers that use a session-stack pseudo-dot (dot_row -1)
            stats["control"]["with_stack_dots"] = (int(sum(bool(np.any(np.asarray(r) < 0)) for r in cm["dot_row"]))
                                                   if len(cm) and "dot_row" in cm else 0)
    except Exception as e:  # noqa: BLE001
        L.exception("link %s: shuffled-exposure control failed", zone.name)
        stats["control"] = {"error": repr(e)[:300]}
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
    if stk is not None:
        store.write_parquet(results_rel(zone.name, STACK_MEMBERS_NAME), smem)
    # the folder members.dot_row points into: later steps, candidates and the server read the dots from it
    return {**stats, "photos_missing_dots": int(missing), "dots_dir": dots_dir(), "wall_s": round(time.time() - t0, 1)}
