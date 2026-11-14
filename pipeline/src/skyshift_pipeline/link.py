"""Step 4, `link`: which dots are fixed, which move, which are junk.

Dots inside the zone box are grouped by their 6″ cell merged with its 8 neighbours (connected
components over occupied cells, so a source on a cell edge isn't split). For each group:
- fixed: seen in >= FIXED_MIN_SESSIONS sessions (stars, galaxies);
- mover: seen in exactly one session, AND (>= 2 photos of that session OR a simultaneous detector
  pair D1+D4 / D2+D5 / D3+D6 of one obs_id), AND the tile is covered by >= MOVER_MIN_COVER_SESSIONS
  sessions, so "absent elsewhere" means something. Only dots with elong <= MOVER_MAX_ELONG count
  as mover evidence (longer ones are trails, streaks or cosmic-ray hits);
- anything else is dropped.
Sessions of a group are counted with core.sessions() on its dots' times (a run of exposures with
gaps under 10 minutes), so the fixed/mover logic depends on sessions only, never on visit labels.
The dot table's session/visit labels are refreshed from this zone's tile map when loading (a dot
file made for the pilot carries the pilot's labels).
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from skyshift_core import sessions

from . import config as C
from . import log
from .sky import cell_of, neighbour_cells, tile_of
from .store import Store, dots_rel, photolist_rel, results_rel

L = log.get("link")

LOAD_COLUMNS = ["detector", "mjd", "ra", "dec", "flux_ujy", "snr", "flags", "elong"]


def load_dots(store: Store, photos: pd.DataFrame, tilemap: pd.DataFrame, zone: C.Zone,
              workers: int = 16) -> tuple[pd.DataFrame, int]:
    """All dot files of the zone's photos, only dots inside the zone box. Returns (dots, missing).

    Strings are replaced by integers to keep tens of millions of dots small: `photo` is the row in
    the photo list, `exposure` a number per obs_id. session/visit are refreshed from this zone's
    tile map: (tile, obs_id, detector) -> session, visit (0 if the tile map has no entry).
    """
    from .dots import session_visit, tile_lookup

    lookup = tile_lookup(tilemap)
    exp_code = {o: i for i, o in enumerate(sorted(photos["obs_id"].unique()))}

    def one(r):
        try:
            df = store.read_parquet(dots_rel(r.obs_id, int(r.detector)))
        except FileNotFoundError:
            return None
        df = df[LOAD_COLUMNS]
        df = df[zone.contains(df["ra"].to_numpy(), df["dec"].to_numpy())].reset_index(drop=True)
        tl = lookup.get((r.obs_id, int(r.detector)), {})
        t = tile_of(df["ra"].to_numpy(), df["dec"].to_numpy())
        sv = session_visit(t, tl)
        df["session"] = sv[:, 0].astype(np.int32)
        df["visit"] = sv[:, 1].astype(np.int16)
        df["photo"] = np.int32(r.Index)
        df["exposure"] = np.int32(exp_code[r.obs_id])
        return df

    with ThreadPoolExecutor(max_workers=max(4, min(workers, 32))) as ex:
        parts = list(ex.map(one, photos.itertuples()))
    missing = sum(p is None for p in parts)
    parts = [p for p in parts if p is not None and len(p)]
    cols = [*LOAD_COLUMNS, "session", "visit", "photo", "exposure"]
    if not parts:
        return pd.DataFrame(columns=cols), missing
    return pd.concat(parts, ignore_index=True)[cols], missing


def cluster_ids(ra: np.ndarray, dec: np.ndarray) -> np.ndarray:
    """Group id per dot: connected components of occupied 6″ cells under 8-neighbour adjacency."""
    n = ra.size
    if n == 0:
        return np.zeros(0, dtype=np.int64)
    cells = cell_of(ra, dec)
    occ, own = np.unique(cells, return_inverse=True)
    nb = neighbour_cells(ra, dec)  # (n, 9)
    pos = np.searchsorted(occ, nb)
    pos_c = np.clip(pos, 0, occ.size - 1)
    hit = occ[pos_c] == nb
    rows = np.repeat(own, 9)[hit.ravel()]
    cols = pos_c.ravel()[hit.ravel()]
    g = coo_matrix((np.ones(rows.size, dtype=np.int8), (rows, cols)), shape=(occ.size, occ.size))
    _, lab = connected_components(g, directed=False)
    return lab[own].astype(np.int64)


def classify(dots: pd.DataFrame, tiles: pd.DataFrame, exposure_names: list[str] | None = None) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """dots (in the zone) -> (fixed, movers, counts). See the module docstring for the rules."""
    stats = {"dots": int(len(dots))}
    if len(dots) == 0:
        return pd.DataFrame(), pd.DataFrame(), stats
    lab = cluster_ids(dots["ra"].to_numpy(np.float64), dots["dec"].to_numpy(np.float64))
    order = np.lexsort((dots["mjd"].to_numpy(), lab))
    d = dots.iloc[order].reset_index(drop=True)
    lab = lab[order]
    starts = np.flatnonzero(np.r_[True, lab[1:] != lab[:-1]])
    ends = np.r_[starts[1:], lab.size]
    sizes = ends - starts
    stats["groups"] = int(starts.size)
    stats["single_dot_groups"] = int((sizes == 1).sum())

    cover = dict(zip(tiles["tile"].astype(np.int64), tiles["n_sessions"].astype(int)))
    mjd = d["mjd"].to_numpy(np.float64)
    obs = d["exposure"].to_numpy(np.int64)
    det = d["detector"].to_numpy(np.int64)
    ra = d["ra"].to_numpy(np.float64)
    dec = d["dec"].to_numpy(np.float64)
    snr = d["snr"].to_numpy(np.float64)
    flux = d["flux_ujy"].to_numpy(np.float64)
    flg = d["flags"].to_numpy(np.int64) & 0xFFFFFFFF
    sess_col = d["session"].to_numpy(np.int64)
    visit_col = d["visit"].to_numpy(np.int64)
    pairs = {frozenset(p) for p in C.SIMULTANEOUS_PAIRS}
    elong = d["elong"].to_numpy(np.float64) if "elong" in d else np.ones(len(d))
    # NaN elong (too faint to measure) counts as round; > MOVER_MAX_ELONG is a trail or a hit
    shape_ok = ~(elong > C.MOVER_MAX_ELONG)

    fixed_rows, mover_rows = [], []
    n_two_sessions = n_one_session_junk = n_under_covered = 0
    for s, e in zip(starts[sizes >= 2], ends[sizes >= 2]):
        t = mjd[s:e]
        n_sess = int(sessions(t).max())
        if n_sess >= C.FIXED_MIN_SESSIONS:
            fixed_rows.append((float(np.median(ra[s:e])), float(np.median(dec[s:e])), n_sess, e - s,
                               float(snr[s:e].max()), float(np.median(flux[s:e])), int(np.bitwise_or.reduce(flg[s:e]))))
            continue
        if n_sess != 1:
            n_two_sessions += 1
            continue
        ok = shape_ok[s:e]
        o = obs[s:e][ok]
        dd = det[s:e][ok]
        n_photos = len(set(o.tolist()))
        evidence = None
        if n_photos >= C.MOVER_MIN_PHOTOS:
            evidence = "two_exposures"
        else:
            dset = set(dd.tolist())
            if any(frozenset((a, b)) in pairs for a in dset for b in dset if a < b):
                evidence = "two_detectors"
        if evidence is None:
            n_one_session_junk += 1
            continue
        mra, mdec = float(np.mean(ra[s:e])), float(np.mean(dec[s:e]))
        tile = int(tile_of(mra, mdec))
        n_cover = cover.get(tile, 0)
        if n_cover < C.MOVER_MIN_COVER_SESSIONS:
            n_under_covered += 1
            continue
        k = s + int(np.argmax(snr[s:e]))
        mover_rows.append({
            "ra": mra, "dec": mdec, "mjd": float(np.mean(t)),
            "session": int(np.bincount(sess_col[s:e]).argmax()), "visit": int(np.bincount(visit_col[s:e]).argmax()),
            "n_dots": int(e - s), "n_exposures": n_photos,
            "detectors": ",".join(str(x) for x in sorted(set(dd.tolist()))), "det": int(det[k]),
            "evidence": evidence, "snr": float(snr[s:e].max()), "flux_ujy": float(np.median(flux[s:e])),
            "flags": int(np.bitwise_or.reduce(flg[s:e])), "tile": tile, "cover_sessions": int(n_cover),
            "exposures": sorted(set(o.tolist())),
        })
    fixed = pd.DataFrame(fixed_rows, columns=["ra", "dec", "n_sessions", "n_dots", "snr", "flux_ujy", "flags"])
    if len(fixed):
        fixed["cell"] = cell_of(fixed["ra"].to_numpy(), fixed["dec"].to_numpy())
    movers = pd.DataFrame(mover_rows)
    if len(movers):
        names = exposure_names
        movers["obs_ids"] = [",".join(names[i] if names else str(i) for i in ex) for ex in movers.pop("exposures")]
    if len(movers):
        movers["cell"] = cell_of(movers["ra"].to_numpy(), movers["dec"].to_numpy())
        movers["flags"] = (movers["flags"].to_numpy(np.int64) & 0xFFFFFFFF).astype(np.uint32).view(np.int32)
        movers = movers.astype({"session": "int32", "visit": "int16", "n_dots": "int32", "det": "int8", "cell": "int64"})
    if len(fixed):
        fixed["flags"] = (fixed["flags"].to_numpy(np.int64) & 0xFFFFFFFF).astype(np.uint32).view(np.int32)
    stats.update({
        "fixed": int(len(fixed)), "movers": int(len(movers)),
        "movers_two_exposures": int((movers["evidence"] == "two_exposures").sum()) if len(movers) else 0,
        "movers_two_detectors": int((movers["evidence"] == "two_detectors").sum()) if len(movers) else 0,
        "dropped_two_sessions": n_two_sessions, "dropped_one_photo": n_one_session_junk,
        "dropped_under_covered": n_under_covered,
    })
    return fixed, movers, stats


def run(store: Store, zone: C.Zone, workers: int = 16) -> dict:
    t0 = time.time()
    photos = store.read_parquet(photolist_rel(zone.name))
    tilemap = store.read_parquet(photolist_rel(zone.name, "tilemap"))
    tiles = store.read_parquet(photolist_rel(zone.name, "tiles"))
    dots, missing = load_dots(store, photos, tilemap, zone, workers)
    L.info("link %s: %d dots in the zone from %d photos (%d photos have no dot file yet) in %.0f s",
           zone.name, len(dots), len(photos) - missing, missing, time.time() - t0)
    if missing:
        L.warning("link %s: %d photos are missing dot files; run `dots` first for a complete result", zone.name, missing)
    t1 = time.time()
    fixed, movers, stats = classify(dots, tiles, sorted(photos["obs_id"].unique()))
    L.info("link %s: classified in %.0f s: %s", zone.name, time.time() - t1, stats)
    store.write_parquet(results_rel(zone.name, "fixed.parquet"), fixed)
    store.write_parquet(results_rel(zone.name, "movers.parquet"), movers)
    return {**stats, "photos_missing_dots": int(missing), "wall_s": round(time.time() - t0, 1)}
