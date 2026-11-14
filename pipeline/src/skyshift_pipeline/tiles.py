"""The combining steps per sky tile, in parallel processes, merged into the zone's usual results.

`all` on a zone larger than TILE_AUTO_MIN_DEG2 (or with --tiles / --tile-deg) runs link → spectra → crossid → calib →
spectra → classify → photoz → colours → lightcurves → astrometry → highpm → orphans → tracklets → score → suspects like
this:

  prepare   tile grid; per tile its photo list (photos whose footprint box meets the tile's core + border) and its slice
            of refcat/<zone>.parquet; the zone's dot files mirrored into the work dir without the columns no combining
            step reads (TILE_SLIM_DOTS: same rows, so dot_row holds); the known-object predictions, by session in parallel
  pass 1    per tile, unchanged step code on a TileStore (the tile is a small zone): link, spectra, crossid. Keep what the
            core owns: a fixed source whose link position (fixed.ra/dec) is in the core, its members, crossids and
            spectrum; a mover whose position is in the core. Check that the border was wide enough (no fixed group
            near the core reaches the border's outer edge); else redo the tile with a doubled border
  merge 1   fixed, members, movers, sources, crossids → results/<zone>/ (source_id is sky-wide: no renumbering)
  calib     once, zone-wide: calib.run on the tiles' Gaia-matched and bright member dots (raw), the merged raw spectra
  qa        once, zone-wide: qa.run, the dot files' footers and shape columns read from the local slim mirror →
            results/<zone>/qa_photos.parquet before suspects starts; pass 2b's light curves leave its red photos out
  pass 2a   per tile, calib.json shared: spectra, colours; light-curve points with their models; the classify shape dots;
            the astrometry frame residuals of quiet Gaia stars
  reduce    zone-wide: the classify stellar locus, the light-curve error model and bad photos, per-photo frame offsets
  pass 2b   per tile, with those numbers: classify, photoz, light-curve errors and summaries, astrometry
  merge 2   sources, classes, photoz, colours, lightcurves(+summary), astrometry → results/<zone>/; the zone reports
            (photoz vs SDSS, lightcurves, astrometry) computed on the merged tables
  zone      tracklets (after merge 1, next to calib; before score), highpm, orphans, score and suspects once on the
            merged tables (tracks cross tiles; their 120″–15′ controls and orphans' 300–6000″ pairs need no tile border)

The outputs have today's names and columns. Owned rows of a tile are exactly what the untiled run gives for them when
the border holds every group the core touches (link's friends-of-friends chains, a mover's pairs and its 6″ veto,
crossid's 2.5–3″, a forced target's ties), and every zone-wide number is fitted once on everything.
With TILE_ZONE_EDGE_MARGIN the zone's own edge is a tile edge too: tiles there also load the border outside the zone
box, so stars on the zone edge are not cut (untiled runs clip dots at the zone box). See pipeline/CLAUDE.md, Tiles.
"""

from __future__ import annotations

import fcntl
import io
import math
import multiprocessing as mp
import os
import resource
import shutil
import sys
import tempfile
import time
from concurrent.futures import Future, ProcessPoolExecutor, as_completed
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from . import config as C
from . import log
from .link import FREEDOTS_NAME, NONFIXED_DOTS_NAME
from .sky import parse_polygon
from .store import Store, dots_rel, photolist_rel, results_rel

L = log.get("tiles")

ZONE_SHARED = ("calib.json", "calib_photos.parquet")  # results/<tile>/ names that are the zone's own files (pass 2)
ZONE_ROW = "_zone_row"  # extra photo-list column: the photo's row in the zone's photo list


# --- the tile grid ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Tile:
    index: int
    core: C.Zone   # the box this tile owns; cores partition the zone box (half-open, as Zone.contains)
    ext: C.Zone    # core + border: the "zone" the steps see; name = the tile's name
    margin_arcsec: float

    @property
    def name(self) -> str:
        return self.ext.name


def area_deg2(z: C.Zone) -> float:
    return (z.ra_max - z.ra_min) * math.cos(math.radians((z.dec_min + z.dec_max) / 2)) * (z.dec_max - z.dec_min)


def ext_box(zone: C.Zone, core: C.Zone, margin_arcsec: float, edge_margin: bool, name: str) -> C.Zone:
    m = margin_arcsec / 3600.0
    d0, d1 = core.dec_min - m, core.dec_max + m
    cosd = math.cos(math.radians(min(89.0, max(abs(d0), abs(d1)))))
    r0, r1 = core.ra_min - m / cosd, core.ra_max + m / cosd
    if not edge_margin:
        r0, r1 = max(r0, zone.ra_min), min(r1, zone.ra_max)
        d0, d1 = max(d0, zone.dec_min), min(d1, zone.dec_max)
    return C.Zone(name, r0, r1, max(d0, -90.0), min(d1, 90.0))


def sides_deg(zone: C.Zone) -> tuple[float, float]:
    """The zone box's width and height on the sky (deg)."""
    return (zone.ra_max - zone.ra_min) * math.cos(math.radians((zone.dec_min + zone.dec_max) / 2)), zone.dec_max - zone.dec_min


def grid_for_deg(zone: C.Zone, tile_deg: float) -> tuple[int, int]:
    w, h = sides_deg(zone)
    return max(1, round(w / tile_deg)), max(1, round(h / tile_deg))


def grid_for_workers(zone: C.Zone, workers: int, max_deg: float = C.TILE_MAX_DEG,
                     pad_deg: float = C.TILE_COST_PAD_DEG) -> tuple[int, int]:
    """nx × ny minimising ceil(nx·ny / workers) × (side_x + pad)(side_y + pad), sides <= max_deg (config, Tiles)."""
    w, h = sides_deg(zone)
    best = None
    for nx in range(max(1, math.ceil(w / max_deg)), max(1, math.ceil(w / max_deg)) + 64):
        for ny in range(max(1, math.ceil(h / max_deg)), max(1, math.ceil(h / max_deg)) + 64):
            cost = math.ceil(nx * ny / max(workers, 1)) * (w / nx + pad_deg) * (h / ny + pad_deg)
            key = (round(cost, 9), nx * ny, abs(math.log(w / nx) - math.log(h / ny)))
            if best is None or key < best[0]:
                best = (key, nx, ny)
    return best[1], best[2]


def grid_for_count(zone: C.Zone, n: int) -> tuple[int, int]:
    """About n tiles, as square as the zone allows."""
    w, h = sides_deg(zone)
    cands = [(nx, max(1, round(n / nx))) for nx in range(1, max(n, 1) + 1)]
    return min(cands, key=lambda c: (abs(c[0] * c[1] - n), abs(math.log(w / c[0]) - math.log(h / c[1]))))


def plan(zone: C.Zone, grid: float | tuple[int, int], margin_arcsec: float = C.TILE_MARGIN_ARCSEC,
         edge_margin: bool = C.TILE_ZONE_EDGE_MARGIN) -> list[Tile]:
    """nx × ny equal cores over the zone box (grid: (nx, ny), or a core side in degrees), each with its border."""
    nx, ny = grid_for_deg(zone, grid) if isinstance(grid, (int, float)) else (int(grid[0]), int(grid[1]))
    de = np.linspace(zone.dec_min, zone.dec_max, ny + 1)
    re = np.linspace(zone.ra_min, zone.ra_max, nx + 1)
    out = []
    for j in range(ny):
        for i in range(nx):
            k = j * nx + i
            name = f"{zone.name}__t{k:04d}"
            core = C.Zone(name, float(re[i]), float(re[i + 1]), float(de[j]), float(de[j + 1]))
            out.append(Tile(k, core, ext_box(zone, core, margin_arcsec, edge_margin, name), margin_arcsec))
    return out


def with_margin(zone: C.Zone, t: Tile, margin_arcsec: float, edge_margin: bool) -> Tile:
    return replace(t, ext=ext_box(zone, t.core, margin_arcsec, edge_margin, t.name), margin_arcsec=margin_arcsec)


def choose(zone: C.Zone, tiles: str | None, tile_deg: float | None, workers: int) -> tuple[int, int] | None:
    """The tile grid (nx, ny), or None for an untiled run. tiles: 'auto' (zones over TILE_AUTO_MIN_DEG2, grid for the
    workers), 'off', or about N tiles; tile_deg: cores of about that side (any zone)."""
    if tile_deg:
        return grid_for_deg(zone, float(tile_deg))
    if tiles in (None, "auto"):
        return grid_for_workers(zone, workers) if area_deg2(zone) > C.TILE_AUTO_MIN_DEG2 else None
    if str(tiles).lower() in ("off", "0", "no", "none"):
        return None
    return grid_for_count(zone, max(1, int(tiles)))


def box_distance_arcsec(ra, dec, box: C.Zone) -> np.ndarray:
    """Distance (″, small-angle) from each position to the box; 0 inside."""
    ra, dec = np.asarray(ra, np.float64), np.asarray(dec, np.float64)
    cosd = np.cos(np.deg2rad(dec))
    dx = np.maximum.reduce([box.ra_min - ra, np.zeros_like(ra), ra - box.ra_max]) * cosd
    dy = np.maximum.reduce([box.dec_min - dec, np.zeros_like(dec), dec - box.dec_max])
    return np.hypot(dx, dy) * 3600.0


def inner_edge_distance_arcsec(ra, dec, t: Tile, zone: C.Zone, edge_margin: bool) -> np.ndarray:
    """Distance (″) inside the tile's border to its outer edge; edges that are the zone's own box in a clipped run
    (no border loaded there, as untiled) don't count."""
    ra, dec = np.asarray(ra, np.float64), np.asarray(dec, np.float64)
    cosd = np.cos(np.deg2rad(dec))
    e = t.ext
    out = np.full(ra.shape, np.inf)
    sides = ((ra - e.ra_min) * cosd, e.ra_min, zone.ra_min), ((e.ra_max - ra) * cosd, e.ra_max, zone.ra_max), \
            (dec - e.dec_min, e.dec_min, zone.dec_min), (e.dec_max - dec, e.dec_max, zone.dec_max)
    for dist, edge, zedge in sides:
        if edge_margin or edge != zedge:
            out = np.minimum(out, dist * 3600.0)
    return out


# --- per-tile inputs -------------------------------------------------------------------------------------------
def photo_boxes(photos: pd.DataFrame, pad_deg: float = 0.1) -> np.ndarray:
    """(n, 4) ra_lo, ra_hi, dec_lo, dec_hi of each photo's footprint vertices (+ pad for the great-circle edges)."""
    out = np.full((len(photos), 4), np.nan)
    for k, (s, ra0) in enumerate(zip(photos["s_region"], photos["s_ra"] if "s_ra" in photos else photos["mjd"] * 0)):
        v = parse_polygon(s)
        ra = ra0 + ((v[:, 0] - ra0 + 180.0) % 360.0 - 180.0) if "s_ra" in photos else v[:, 0]
        cosd = max(math.cos(math.radians(float(np.max(np.abs(v[:, 1]))))), 0.01)
        out[k] = (ra.min() - pad_deg / cosd, ra.max() + pad_deg / cosd, v[:, 1].min() - pad_deg, v[:, 1].max() + pad_deg)
    return out


def tile_photos(photos: pd.DataFrame, boxes: np.ndarray, box: C.Zone) -> pd.DataFrame:
    m = (boxes[:, 1] >= box.ra_min) & (boxes[:, 0] <= box.ra_max) & (boxes[:, 3] >= box.dec_min) & (boxes[:, 2] <= box.dec_max)
    return photos[m].reset_index(drop=True)


def moved(cat: pd.DataFrame, mjd: float) -> tuple[np.ndarray, np.ndarray]:
    """Catalogue positions moved by proper motion (mas/yr, pmra × cos dec) to mjd; NaN pm or epoch = not moved."""
    ra, dec = cat["ra"].to_numpy(np.float64), cat["dec"].to_numpy(np.float64)
    if "epoch_mjd" not in cat:
        return ra, dec
    dt = np.nan_to_num((mjd - cat["epoch_mjd"].to_numpy(np.float64)) / 365.25)
    pmra = np.nan_to_num(cat["pmra"].to_numpy(np.float64)) if "pmra" in cat else 0.0
    pmdec = np.nan_to_num(cat["pmdec"].to_numpy(np.float64)) if "pmdec" in cat else 0.0
    d2 = dec + pmdec * dt / 3.6e6
    return ra + pmra * dt / 3.6e6 / np.cos(np.deg2rad(dec)), d2


def refcat_reach(cat: pd.DataFrame, mjd: float, span_yr: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Each catalogue row's position at the zone's median time and its reach (″): 10″ + its motion over the zone's
    time span (Barnard's Star: 10″/yr)."""
    ra, dec = moved(cat, mjd)
    pm = np.hypot(np.nan_to_num(cat["pmra"].to_numpy(np.float64)) if "pmra" in cat else np.zeros(len(cat)),
                  np.nan_to_num(cat["pmdec"].to_numpy(np.float64)) if "pmdec" in cat else np.zeros(len(cat))) / 1000.0
    return ra, dec, 10.0 + pm * max(span_yr, 0.0)


def refcat_slice(cat: pd.DataFrame, reach: tuple[np.ndarray, np.ndarray, np.ndarray], box: C.Zone) -> pd.DataFrame:
    """The rows whose position at the zone's median time is within their reach of the box."""
    if not len(cat):
        return cat
    ra, dec, slack = reach
    near = (ra >= box.ra_min - 1.0) & (ra <= box.ra_max + 1.0) & (dec >= box.dec_min - 1.0) & (dec <= box.dec_max + 1.0)
    i = np.flatnonzero(near)
    keep = i[box_distance_arcsec(ra[i], dec[i], box) <= slack[i]]
    return cat.iloc[keep].reset_index(drop=True)


def forced_box(t: Tile, zone: C.Zone, edge_margin: bool) -> C.Zone:
    """Forced rows a tile loads. Its core + border; clipped at the zone edge (`--zone-edge clip`, as untiled), an edge
    tile also takes everything beyond the zone edge on its side: the untiled run keeps light curves of every forced
    target in the zone's photos (with the sky-wide forced list, up to a photo's size outside the zone)."""
    e = t.ext
    if edge_margin:
        return C.Zone(e.name, e.ra_min, e.ra_max, e.dec_min, e.dec_max)
    far = 1e3
    return C.Zone(e.name, -far if t.core.ra_min <= zone.ra_min else e.ra_min, far if t.core.ra_max >= zone.ra_max else e.ra_max,
                  -far if t.core.dec_min <= zone.dec_min else e.dec_min, far if t.core.dec_max >= zone.dec_max else e.dec_max)


def owns(t: Tile, zone: C.Zone, ra, dec, edge_margin: bool) -> np.ndarray:
    """True where this tile's core holds the position; clipped at the zone edge, positions outside the zone are first
    clamped into it (the edge tile keeps them, as untiled); with the border, they belong to the zone next door."""
    ra, dec = np.asarray(ra, np.float64), np.asarray(dec, np.float64)
    if not edge_margin:
        ra = np.clip(ra, zone.ra_min, np.nextafter(zone.ra_max, -np.inf))
        dec = np.clip(dec, zone.dec_min, np.nextafter(zone.dec_max, -np.inf))
    return t.core.contains(ra, dec)


def forced_owned(seen: list[pd.DataFrame], t: Tile, zone: C.Zone, edge_margin: bool) -> np.ndarray:
    """ref_ids of the forced targets this tile owns: each target's position in its earliest row (every tile that could
    own it sees all its rows, so they all pick the same one)."""
    if not seen:
        return np.zeros(0, dtype=object)
    f = pd.concat(seen, ignore_index=True)
    f = f.assign(ref_id=f["ref_id"].astype(str)).sort_values(["ref_id", "mjd", "ra", "dec"], kind="stable")
    first = f.drop_duplicates("ref_id")
    return first["ref_id"].to_numpy()[owns(t, zone, first["ra"].to_numpy(), first["dec"].to_numpy(), edge_margin)]


# --- the store a tile's steps see ------------------------------------------------------------------------------
class _RouterFS:
    """store.fs for a TileStore: info() on a routed path goes to the filesystem that holds it."""

    def __init__(self, ts: "TileStore"):
        self.ts = ts

    def _fs(self, path: str):
        return self.ts.work.fs if str(path).startswith(self.ts.work.base + "/") else self.ts.base.fs

    def info(self, path: str, **kw):
        return self._fs(path).info(path, **kw)

    def exists(self, path: str, **kw):
        return self._fs(path).exists(path, **kw)

    def __getattr__(self, name):
        return getattr(self.ts.base.fs, name)


class TileStore:
    """A Store view in which the tile is a zone: photolists/zones/<tile>.parquet, refcat/<tile>.parquet and
    results/<tile>/ live in the work dir; results/<tile>/calib.json is the zone's (pass 2 only); dots/ is the slim
    mirror; forced/ rows are cut to the tile's forced box; cache/<…>/<tile> is the zone's; everything else the bucket's."""

    def __init__(self, root: str, work_dir: str, zone: C.Zone, tile: Tile, slim: bool, forced_mirror: bool,
                 zone_calib: bool, edge_margin: bool):
        self.base = Store(root)
        self.work = Store(work_dir)
        self.zone, self.tile = zone, tile
        self.slim, self.forced_mirror, self.zone_calib = slim, forced_mirror, zone_calib
        self.root = f"{self.base.root}#{tile.name}"  # cache keys (calib.load, motion_io) per tile
        self.local = self.work.local
        self.fs = _RouterFS(self)
        self.fbox = forced_box(tile, zone, edge_margin)
        self.forced_seen: list[pd.DataFrame] = []  # (ref_id, ra, dec, mjd) of every forced row read (forced_owned)

    def tile_dir(self, *parts: str) -> str:
        return "/".join(("tiles", self.tile.name, *parts))

    def route(self, rel: str) -> tuple[Store, str]:
        rel = rel.lstrip("/")
        t, z = self.tile.name, self.zone.name
        if rel == photolist_rel(t):
            return self.work, self.tile_dir("photolist.parquet")
        pre = f"results/{t}/"
        if rel.startswith(pre):
            name = rel[len(pre):]
            if self.zone_calib and name in ZONE_SHARED:
                return self.base, results_rel(z, name)
            return self.work, self.tile_dir("results", name)
        if rel in (f"refcat/{t}.parquet", f"refcat/{t}_forced.parquet"):
            return self.work, self.tile_dir(rel)
        if rel.startswith("dots/") and self.slim:
            return self.work, rel
        if rel.startswith(f"{C.FORCED_DIR}/") or rel == C.FORCED_DIR:
            return (self.work if self.forced_mirror else self.base), rel
        if rel.startswith("cache/"):
            return self.base, "/".join(z if p == t else (z + p[len(t):] if p.startswith(t + ".") else p) for p in rel.split("/"))
        return self.base, rel

    def path(self, rel: str) -> str:
        s, r = self.route(rel)
        return s.path(r)

    def exists(self, rel: str) -> bool:
        s, r = self.route(rel)
        return s.exists(r)

    def read_bytes(self, rel: str) -> bytes:
        s, r = self.route(rel)
        return s.read_bytes(r)

    def write_bytes(self, rel: str, data: bytes) -> None:
        s, r = self.route(rel)
        s.write_bytes(r, data)

    def write_parquet(self, rel: str, df: pd.DataFrame) -> int:
        s, r = self.route(rel)
        return s.write_parquet(r, df)

    def write_table(self, rel: str, table) -> int:
        s, r = self.route(rel)
        return s.write_table(r, table)

    def read_parquet(self, rel: str, columns: list[str] | None = None) -> pd.DataFrame:
        s, r = self.route(rel)
        if not rel.startswith(f"{C.FORCED_DIR}/"):
            return s.read_parquet(r, columns)
        cols = None if columns is None else list(dict.fromkeys([*columns, "ra", "dec"]))
        df = s.read_parquet(r, cols)
        if "ra" in df and "dec" in df:
            df = df[self.fbox.contains(df["ra"].to_numpy(np.float64), df["dec"].to_numpy(np.float64))].reset_index(drop=True)
            if {"ref_id", "mjd"} <= set(df.columns):
                self.forced_seen.append(df[["ref_id", "ra", "dec", "mjd"]])
        return df if columns is None else df[[c for c in columns if c in df]]

    def write_json(self, rel: str, obj) -> None:
        s, r = self.route(rel)
        s.write_json(r, obj)

    def read_json(self, rel: str):
        s, r = self.route(rel)
        return s.read_json(r)

    def listdir(self, rel: str) -> list[str]:
        s, r = self.route(rel)
        return s.listdir(r)


# --- worker side -----------------------------------------------------------------------------------------------
def _ctx_store(ctx: dict, tile: Tile, zone_calib: bool) -> TileStore:
    return TileStore(ctx["root"], ctx["work"], ctx["zone"], tile, ctx["slim"], ctx["forced_mirror"], zone_calib,
                     ctx["edge_margin"])


def _owned_dir(ctx: dict, tile: Tile) -> Store:
    return Store(os.path.join(ctx["work"], "tiles", tile.name, "owned"))


def _peak_rss_mb() -> float:
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(r / 1e6 if sys.platform == "darwin" else r / 1e3, 1)


def _gaia_ids(xid: pd.DataFrame) -> np.ndarray:
    return xid.loc[xid["gaia_ref_id"].fillna("").astype(str) != "", "source_id"].to_numpy(np.int64)


def border_check(dots: pd.DataFrame, dot_sid: np.ndarray, t: Tile, zone: C.Zone, edge_margin: bool) -> dict:
    """Fixed groups with a dot within TILE_EDGE_TOUCH_ARCSEC of the border's outer edge may be cut there; it matters
    when the group also comes within TILE_EDGE_CHECK_ARCSEC of the core (owned, or a veto of an owned mover)."""
    if not len(dots):
        return {"border_groups": 0, "border_threats": 0}
    ra, dec = dots["ra"].to_numpy(np.float64), dots["dec"].to_numpy(np.float64)
    near = inner_edge_distance_arcsec(ra, dec, t, zone, edge_margin) < C.TILE_EDGE_TOUCH_ARCSEC
    touching = np.unique(dot_sid[near & (dot_sid >= 0)])
    if not touching.size:
        return {"border_groups": 0, "border_threats": 0}
    m = np.isin(dot_sid, touching)
    dc = pd.Series(box_distance_arcsec(ra[m], dec[m], t.core)).groupby(dot_sid[m]).min()
    return {"border_groups": int(touching.size), "border_threats": int((dc < C.TILE_EDGE_CHECK_ARCSEC).sum()),
            "border_threat_min_core_arcsec": round(float(dc.min()), 1)}


def pass1(ctx: dict, tile: Tile) -> dict:
    """link, spectra, crossid on the tile; owned rows and the calib inputs → the tile's owned/ dir."""
    from . import crossid, link, motion_io, spectra
    t0 = time.time()
    ts = _ctx_store(ctx, tile, zone_calib=False)  # pass 1 is raw, as `all` before calib
    tz, thr = tile.ext, ctx["threads"]
    keep: dict = {}
    st: dict = {"tile": tile.name, "margin_arcsec": tile.margin_arcsec}
    st["link"] = link.run(ts, tz, thr, keep=keep)
    st["border"] = border_check(keep["dots"], keep["dot_sid"], tile, ctx["zone"], ctx["edge_margin"])
    del keep
    st["spectra"] = spectra.run(ts, tz, thr)
    st["crossid"] = crossid.run(ts, tz, thr)
    own = _owned_dir(ctx, tile)
    fixed = ts.read_parquet(results_rel(tz.name, "fixed.parquet"))
    owned = fixed["source_id"].to_numpy(np.int64)[tile.core.contains(fixed["ra"].to_numpy(), fixed["dec"].to_numpy())]
    own.write_parquet("fixed.parquet", fixed[fixed["source_id"].isin(owned)])
    own.write_parquet("owned_ids.parquet", pd.DataFrame({"source_id": owned}))
    m = ts.read_parquet(results_rel(tz.name, "members.parquet"))
    own.write_parquet("members.parquet", m[m["source_id"].isin(owned)])
    mv = ts.read_parquet(results_rel(tz.name, "movers.parquet"))
    if len(mv):
        mv = mv[tile.core.contains(mv["ra"].to_numpy(np.float64), mv["dec"].to_numpy(np.float64))]
    own.write_parquet("movers.parquet", mv)
    frel = results_rel(tz.name, link.FREEDOTS_NAME)
    if ts.exists(frel):  # free dots (suspects' hot-pixel and column maps), owned by position like the movers
        fd = ts.read_parquet(frel)
        own.write_parquet(link.FREEDOTS_NAME, fd[tile.core.contains(fd["ra"].to_numpy(np.float64), fd["dec"].to_numpy(np.float64))])
    nrel = results_rel(tz.name, link.NONFIXED_DOTS_NAME)
    if ts.exists(nrel):  # tracklets' recovery: owned by the dot's position
        fd = ts.read_parquet(nrel)
        own.write_parquet(link.NONFIXED_DOTS_NAME, fd[tile.core.contains(fd["ra"].to_numpy(np.float64), fd["dec"].to_numpy(np.float64))])
    src = pq.read_table(io.BytesIO(ts.read_bytes(results_rel(tz.name, "sources.parquet"))))
    own.write_table("sources.parquet", src.filter(pa.array(np.isin(src.column("source_id").to_numpy(), owned))))
    xid = ts.read_parquet(results_rel(tz.name, "crossids.parquet"))
    own.write_parquet("crossids.parquet", xid[xid["source_id"].isin(owned)])
    # calib inputs: raw member dots of owned sources that calib can use, photo = row in the zone's photo list
    d, _, jac = motion_io.member_dots_raw(ts, tz, thr)
    photos = ts.read_parquet(photolist_rel(tz.name), columns=["obs_id", "detector", ZONE_ROW])
    keep_rows = d["source_id"].isin(owned).to_numpy() & (
        d["source_id"].isin(_gaia_ids(xid)).to_numpy() | (d["snr"].to_numpy(np.float64) >= C.TILE_CALIB_MIN_SNR))
    d = d[keep_rows].reset_index(drop=True)
    zr = photos[ZONE_ROW].to_numpy(np.int64)
    p = d["photo"].to_numpy(np.int64)
    d["photo"] = np.where(p >= 0, zr[np.clip(p, 0, max(len(zr) - 1, 0))] if len(zr) else -1, -1).astype(np.int32)
    own.write_parquet("calib_dots.parquet", d)
    ok = np.isfinite(jac).all(axis=(1, 2))
    own.write_parquet("calib_jac.parquet", pd.DataFrame({ZONE_ROW: zr[ok], **{f"j{a}{b}": jac[ok, a, b] for a in (0, 1) for b in (0, 1)}}))
    motion_io.clear_cache()
    st.update({"owned_sources": int(len(owned)), "owned_movers": int(len(mv)), "calib_dots": int(len(d)),
               "photos": int(len(photos)), "wall_s": round(time.time() - t0, 1), "peak_rss_mb": _peak_rss_mb()})
    return st


def _group_codes_with_ref(p: pd.DataFrame, ref: np.ndarray) -> np.ndarray:
    """True for the points of light curves (lightcurves.group_codes) that hold a reference point."""
    from .lightcurves import group_codes
    g, G = group_codes(p)
    has = np.zeros(G, bool)
    has[g[ref]] = True
    return has[g]


def pass2a(ctx: dict, tile: Tile) -> dict:
    """spectra (calibrated), colours; light-curve points; classify shape dots; astrometry frame residuals."""
    from . import astrometry, classify, colours, lightcurves, motion_io, spectra
    t0 = time.time()
    ts = _ctx_store(ctx, tile, zone_calib=True)
    tz, thr = tile.ext, ctx["threads"]
    own = _owned_dir(ctx, tile)
    owned = own.read_parquet("owned_ids.parquet")["source_id"].to_numpy(np.int64)
    st: dict = {"tile": tile.name}
    st["spectra"] = spectra.run(ts, tz, thr)
    src = pq.read_table(io.BytesIO(ts.read_bytes(results_rel(tz.name, "sources.parquet"))))
    own.write_table("sources_cal.parquet", src.filter(pa.array(np.isin(src.column("source_id").to_numpy(), owned))))
    # classify, phase a: the shape dots (kept for phase b) and the locus inputs of the owned sources
    sp = ts.read_parquet(results_rel(tz.name, "sources.parquet"), columns=["source_id", "ra", "dec"]).sort_values("source_id")
    gaia, _ = classify.load_gaia(ts, tz, sp)
    gstars = classify.gaia_star_ids(gaia, sp["source_id"].to_numpy(np.int64))
    sdots = classify.load_shape_dots(ts, tz, thr)
    if sdots is not None:
        own.write_parquet("shape_dots.parquet", sdots)
        own.write_parquet("locus_values.parquet", classify.locus_values(sdots[sdots["source_id"].isin(owned)], gstars))
    st["colours"] = colours.run(ts, tz, thr)
    co = ts.read_parquet(results_rel(tz.name, "colours.parquet"))
    own.write_parquet("colours.parquet", co[co["source_id"].isin(owned)] if len(co) else co)
    # light curves, phase a: points with their models, owned by source or (no source) by forced target
    d, mst = motion_io.member_dots(ts, tz, thr)
    forced = motion_io.load_forced(ts, tz, thr)
    xid_rel = results_rel(tz.name, "crossids.parquet")
    xid = ts.read_parquet(xid_rel) if ts.exists(xid_rel) else None
    sources = ts.read_parquet(results_rel(tz.name, "sources.parquet"), columns=["source_id", "flux_ujy"])
    p = lightcurves.points(d, forced, xid, sources)
    st["lc_forced_rows"] = int(len(forced))
    if p is not None:
        sid = p["source_id"].to_numpy(np.int64)
        mine = np.isin(sid, owned)
        nos = sid < 0
        if nos.any():
            fo = forced_owned(ts.forced_seen, tile, ctx["zone"], ctx["edge_margin"])
            mine[nos] = np.isin(p["ref_id"].astype(str).to_numpy()[nos], fo)
            st["lc_forced_targets_owned"] = int(len(fo))
        ref = lightcurves.reference_points(p, xid)
        p = p[mine].reset_index(drop=True)
        ref = ref[mine]
        p["ref"] = ref
        p["ref_group"] = _group_codes_with_ref(p, ref) if len(p) else np.zeros(0, bool)
        own.write_parquet("lc_points_a.parquet", p)
        st["lc_points"] = int(len(p))
        st["lc_pairs"] = sorted({f"{o}_D{int(k)}" for o, k in zip(p["origin"].astype(str), p["detector"])})
    # astrometry, phase a: the frame residuals of quiet Gaia stars; the owned member dots for phase b
    dd = d[d["source_id"].isin(owned)].reset_index(drop=True)
    own.write_parquet("astro_dots.parquet", dd)
    own.write_parquet("frame_residuals.parquet", astrometry.frame_residuals(dd, xid))
    motion_io.clear_cache()
    st.update({"member_dots": {k: v for k, v in mst.items() if not isinstance(v, (dict, list))},
               "wall_s": round(time.time() - t0, 1), "peak_rss_mb": _peak_rss_mb()})
    return st


def pass2b(ctx: dict, tile: Tile, locus: dict, lc_model: dict, bad_keys: pd.DataFrame, offsets: pd.DataFrame | None,
           qa_red: pd.DataFrame | None = None) -> dict:
    """classify (zone locus), photoz, light-curve errors (zone model, bad photos and qa's red ones) and summaries,
    astrometry (zone frame offsets)."""
    from . import astrometry, classify, lightcurves, photoz
    t0 = time.time()
    ts = _ctx_store(ctx, tile, zone_calib=True)
    tz, thr = tile.ext, ctx["threads"]
    own = _owned_dir(ctx, tile)
    owned = own.read_parquet("owned_ids.parquet")["source_id"].to_numpy(np.int64)
    st: dict = {"tile": tile.name}
    sdots = own.read_parquet("shape_dots.parquet") if own.exists("shape_dots.parquet") else None
    st["classify"] = classify.run(ts, tz, thr, locus=locus, dots=sdots)
    cl = ts.read_parquet(results_rel(tz.name, "classes.parquet"))
    own.write_parquet("classes.parquet", cl[cl["source_id"].isin(owned)])
    st["photoz"] = photoz.run(ts, tz, validate_sdss=False)
    pz = ts.read_parquet(results_rel(tz.name, "photoz.parquet"))
    own.write_parquet("photoz.parquet", pz[pz["source_id"].isin(owned)])
    st["photoz_selection"] = st["photoz"].get("selection")
    if own.exists("lc_points_a.parquet"):
        p = own.read_parquet("lc_points_a.parquet")
        ref = p.pop("ref").to_numpy(bool)
        p.pop("ref_group")
        cal = lightcurves.apply_error_model(p, lc_model)
        key = pd.MultiIndex.from_arrays([p["origin"].astype(str).to_numpy(), p["detector"].to_numpy(np.int64),
                                         p["mjd"].to_numpy(np.float64)])
        bad = pd.MultiIndex.from_arrays([bad_keys["origin"].astype(str).to_numpy(), bad_keys["detector"].to_numpy(np.int64),
                                         bad_keys["mjd"].to_numpy(np.float64)]) if len(bad_keys) else None
        photo_ok = ~key.isin(bad) if bad is not None else np.ones(len(p), bool)
        photo_ok &= ~lightcurves.qa_red_mask(p, qa_red)
        p, s = lightcurves.finish(p, cal, photo_ok)
        own.write_parquet("lightcurves.parquet", p[lightcurves.POINT_COLUMNS])
        own.write_parquet("lightcurves_summary.parquet", s)
        rk = pd.DataFrame({"source_id": p["source_id"].to_numpy(np.int64)[ref], "ref_id": p["ref_id"].to_numpy()[ref],
                           "detector": p["detector"].to_numpy(np.int8)[ref],
                           "origin": p["origin"].astype(str).to_numpy()[ref]}).drop_duplicates()
        own.write_parquet("lc_ref_keys.parquet", rk)
    d = own.read_parquet("astro_dots.parquet")
    xid_rel = results_rel(tz.name, "crossids.parquet")
    xid = ts.read_parquet(xid_rel) if ts.exists(xid_rel) else None
    raw, st_raw = astrometry.fit(d, xid, frame=False)
    if C.ASTRO_FRAME_CORRECT and xid is not None:
        cor, st_cor = astrometry.fit(d, xid, frame=True, offsets=offsets)
    else:
        cor, st_cor = raw, st_raw
    own.write_parquet("astrometry.parquet", cor)
    own.write_parquet("astrometry_raw.parquet", raw)
    st["astrometry"] = {k: v for k, v in st_cor.items() if k in ("fitted", "with_parallax", "spacecraft_position_dots", "sources_in")}
    st["astrometry"]["corrected"] = cor is not raw
    st.update({"wall_s": round(time.time() - t0, 1), "peak_rss_mb": _peak_rss_mb()})
    return st


def prepare_photos(ctx: dict, names: list[str]) -> dict:
    """Mirror dot files into the work dir without TILE_SLIM_DROP_COLUMNS (and forced files when the bucket is remote)."""
    base, work = Store(ctx["root"]), Store(ctx["work"])
    n = nb = 0
    for name in names:
        if ctx["slim"]:
            try:
                raw = base.read_bytes(f"dots/{name}")
            except FileNotFoundError:
                raw = None
            if raw is not None:
                t = pq.read_table(io.BytesIO(raw))
                drop = [c for c in C.TILE_SLIM_DROP_COLUMNS if c in t.column_names]
                if drop:
                    t = t.drop_columns(drop)
                    buf = io.BytesIO()
                    pq.write_table(t, buf, compression="zstd")
                    raw = buf.getvalue()
                work.write_bytes(f"dots/{name}", raw)
                n += 1
                nb += len(raw)
        if ctx["forced_mirror"]:
            try:
                work.write_bytes(f"{C.FORCED_DIR}/{name}", base.read_bytes(f"{C.FORCED_DIR}/{name}"))
            except FileNotFoundError:
                pass
    return {"dots": n, "bytes": nb}


def predictions_part(root: str, photos: pd.DataFrame, zone: C.Zone) -> pd.DataFrame:
    from .known import load_predictions
    return load_predictions(Store(root), photos, zone)


LC_FIT_COLUMNS = ["source_id", "ref_id", "origin", "detector", "mjd", "flux_ujy", "flux_err_ujy", "model_ujy", "ref", "ref_group"]


def lc_points(work: str, tile_names: list[str], filters: list) -> pd.DataFrame:
    """Phase-a light-curve points of every tile matching a pyarrow filter, with what the zone fit reads. Each light
    curve lives in one tile, in the untiled order, so the per-curve sums come out the same."""
    parts = []
    for n in tile_names:
        f = os.path.join(work, "tiles", n, "owned", "lc_points_a.parquet")
        if os.path.exists(f):
            parts.append(_plain(pq.read_table(f, columns=LC_FIT_COLUMNS, filters=filters)))
    P = pa.concat_tables(parts, promote_options="permissive").to_pandas() if parts else pd.DataFrame(columns=LC_FIT_COLUMNS)
    P["ref_id"] = P["ref_id"].astype(str)
    P["origin"] = P["origin"].astype(str)
    return P


def lc_fit_pair(work: str, tile_names: list[str], key: str) -> dict:
    """The light-curve error model and bad photos of one (origin, detector), over the whole zone: as untiled, from the
    light curves that hold a Gaia point, or from all of the pair's points when those are too few (basis "all"/"none").
    Pairs are independent in lightcurves.fit_error_model and photo_check, so they run as parallel tasks."""
    from . import lightcurves
    o, d = key.rsplit("_D", 1)
    pair = [("origin", "=", o), ("detector", "=", int(d))]
    P = lc_points(work, tile_names, [("ref_group", "=", True), *pair])
    model = lightcurves.fit_error_model(P, P["ref"].to_numpy(bool))[1] if len(P) else {}
    if model.get(key, {}).get("basis") != "gaia":
        P = lc_points(work, tile_names, pair)
        model = lightcurves.fit_error_model(P, P["ref"].to_numpy(bool))[1] if len(P) else {}
    if not len(P):
        return {"model": {}, "bad_rows": [], "bad_keys": pd.DataFrame(columns=["origin", "detector", "mjd"]), "points": 0}
    ok, rows = lightcurves.photo_check(P, P["ref"].to_numpy(bool), lightcurves.apply_error_model(P, model))
    b = P.loc[~ok, ["origin", "detector", "mjd"]].drop_duplicates()
    return {"model": {k: v for k, v in model.items() if k == key}, "bad_rows": rows, "points": int(len(P)),
            "basis": model.get(key, {}).get("basis"),
            "bad_keys": b.assign(origin=b["origin"].astype(str), detector=b["detector"].astype(np.int64)).reset_index(drop=True)}


def zone_step(step: str, root: str, zone: C.Zone, pred_path: str) -> dict:
    """tracklets, score or suspects on the merged tables (they need only movers, free dots, fixed, tracklets and the
    predictions: merge 1), in a pool process while calib and pass 2 go on."""
    from . import score, suspects, tracklets
    t0 = time.time()
    if step == "tracklets":
        out = tracklets.run(Store(root), zone)
    else:
        pred = pd.read_parquet(pred_path)
        out = (score if step == "score" else suspects).run(Store(root), zone, pred=pred)
    return {**out, "step_seconds": round(time.time() - t0, 1)}


def _read_or_none(path: str) -> pd.DataFrame | None:
    """The predictions parquet for orphans' known-object tags; None when unreadable (orphans then loads them itself)."""
    try:
        return pd.read_parquet(path)
    except Exception:  # noqa: BLE001
        return None


def _run_task(fn, *a):
    """Pool entry point: a crash says which tile."""
    try:
        return fn(*a)
    except Exception as e:  # noqa: BLE001
        who = a[1].name if len(a) > 1 and isinstance(a[1], Tile) else ""
        raise RuntimeError(f"tile task {fn.__name__} {who} failed: {e!r}") from e


# --- orchestration ---------------------------------------------------------------------------------------------
class _Pool:
    """A process pool (spawn, as dots), or inline calls with workers == 1."""

    def __init__(self, workers: int):
        self.workers = workers
        self.ex = (ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context("spawn"), initializer=log.setup)
                   if workers > 1 else None)

    def submit(self, fn, *a) -> Future:
        if self.ex is not None:
            return self.ex.submit(_run_task, fn, *a)
        f: Future = Future()
        try:
            f.set_result(_run_task(fn, *a))
        except Exception as e:  # noqa: BLE001
            f.set_exception(e)
        return f

    def map(self, fn, argsets: list[tuple]) -> list:
        if self.ex is None:
            return [_run_task(fn, *a) for a in argsets]
        futs = {self.ex.submit(_run_task, fn, *a): i for i, a in enumerate(argsets)}
        out: list = [None] * len(argsets)
        for f in as_completed(futs):
            out[futs[f]] = f.result()
        return out

    def close(self):
        if self.ex is not None:
            self.ex.shutdown()


def _read_parts(work: str, tiles: list[Tile], name: str, columns: list[str] | None = None) -> list[pa.Table]:
    out = []
    for t in tiles:
        p = os.path.join(work, "tiles", t.name, "owned", name)
        if os.path.exists(p):
            out.append(pq.read_table(p, columns=columns))
    return out


def _plain(t: pa.Table) -> pa.Table:
    """Dictionary (category) columns as plain values, so tiles' tables concatenate."""
    cols = [c.cast(c.type.value_type) if pa.types.is_dictionary(c.type) else c for c in t.columns]
    return pa.Table.from_arrays(cols, names=t.column_names)


def _concat(parts: list[pa.Table], sort: list[str] | None = None, category: tuple[str, ...] = ()) -> pa.Table | None:
    if not parts:
        return None
    full = [p for p in parts if p.num_rows and p.num_columns]
    if not full:
        return parts[0]
    t = pa.concat_tables([_plain(p).replace_schema_metadata(None) for p in full], promote_options="permissive")
    if sort:
        key = pd.DataFrame({c: t.column(c).to_numpy(zero_copy_only=False) for c in sort})
        t = t.take(pa.array(key.sort_values(sort, kind="stable").index.to_numpy()))
    for c in category:
        if c in t.column_names:
            t = t.set_column(t.column_names.index(c), c, t.column(c).dictionary_encode())
    return t


def _write(store: Store, zone: C.Zone, name: str, t: pa.Table | None) -> int:
    if t is None:
        return 0
    store.write_bytes(results_rel(zone.name, name), _table_bytes(t))
    return int(t.num_rows)


def _table_bytes(t: pa.Table) -> bytes:
    buf = io.BytesIO()
    pq.write_table(t, buf, compression="zstd")
    return buf.getvalue()


def _write_stream(store: Store, zone: C.Zone, name: str, work: str, tiles: list[Tile], category: tuple[str, ...] = ()) -> int:
    """Concatenate the tiles' owned/<name> into results/<zone>/<name> one tile at a time (bounded memory)."""
    paths = [p for p in (os.path.join(work, "tiles", t.name, "owned", name) for t in tiles) if os.path.exists(p)]
    if not paths:
        return 0
    tmp = os.path.join(work, f"merged-{name}")
    n = 0

    def norm(t: pa.Table) -> pa.Table:
        t = _plain(t).replace_schema_metadata(None)
        for c in category:
            if c in t.column_names:
                t = t.set_column(t.column_names.index(c), c, t.column(c).cast(pa.string()).dictionary_encode())
        return t

    first = next((p for p in paths if pq.read_metadata(p).num_rows), paths[0])
    schema = norm(pq.read_table(first)).schema
    with pq.ParquetWriter(tmp, schema, compression="zstd") as writer:
        for p in paths:
            t = pq.read_table(p)
            if t.num_rows:
                t = norm(t)
                writer.write_table(t.cast(schema) if t.schema != schema else t)
                n += t.num_rows
    rel = results_rel(zone.name, name)
    if store.local:
        os.makedirs(os.path.dirname(store.path(rel)), exist_ok=True)
        shutil.move(tmp, store.path(rel))
    else:
        store.fs.put_file(tmp, store.path(rel))
        os.remove(tmp)
    return n


def _df(parts: list[pa.Table]) -> pd.DataFrame:
    t = _concat(parts)
    return t.to_pandas() if t is not None else pd.DataFrame()


LOCK_NAME = ".running"


def _alive(path: str, pid: str) -> bool:
    """Is the run that made this work dir still going? Its lock file is flock'ed for the run's life (a lock dies with its
    process, and with the machine), so a reused pid after a restart can't fool it; dirs without one fall back to the pid."""
    lock = os.path.join(path, LOCK_NAME)
    if os.path.exists(lock):
        try:
            with open(lock, "a") as f:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(f, fcntl.LOCK_UN)
            return False
        except BlockingIOError:
            return True
        except OSError:
            pass
    try:
        os.kill(int(pid), 0)
        return int(pid) != os.getpid()
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def clear_stale(base: str, zone: str) -> list[str]:
    """Remove work dirs of earlier runs of this zone (<zone>-<time>-<pid>) that are no longer running: a pre-empted
    machine restarts the zone from the top, and their slim dot mirrors would fill the disk."""
    gone = []
    if not os.path.isdir(base):
        return gone
    for n in os.listdir(base):
        head, _, pid = n.rpartition("-")
        if not head.startswith(f"{zone}-") or head[len(zone) + 1:].count("-") or not pid.isdigit():
            continue
        if _alive(os.path.join(base, n), pid):
            continue  # that run is still going
        shutil.rmtree(os.path.join(base, n), ignore_errors=True)
        gone.append(n)
    if gone:
        L.info("tiles %s: removed %d stale work dirs: %s", zone, len(gone), gone)
    return gone


class Run:
    """One tiled run of the combining steps of a zone."""

    def __init__(self, store: Store, zone: C.Zone, grid: float | tuple[int, int], workers: int, work_dir: str | None = None,
                 margin_arcsec: float | None = None, edge_margin: bool | None = None, slim: bool | None = None,
                 keep_work: bool = False):
        zone = C.Zone(zone.name, zone.ra_min, zone.ra_max, zone.dec_min, zone.dec_max)  # this config's class (picklable)
        self.store, self.zone, self.workers = store, zone, max(1, int(workers))
        self.edge_margin = C.TILE_ZONE_EDGE_MARGIN if edge_margin is None else bool(edge_margin)
        self.margin = C.TILE_MARGIN_ARCSEC if margin_arcsec is None else float(margin_arcsec)
        self.tiles = plan(zone, grid, self.margin, self.edge_margin)
        nx = len({t.core.ra_min for t in self.tiles})
        ny = len(self.tiles) // nx
        w, h = sides_deg(zone)
        self.grid, self.sides = (nx, ny), (round(w / nx, 4), round(h / ny, 4))
        base = work_dir or os.path.join(tempfile.gettempdir(), "skyshift-tiles")
        clear_stale(base, zone.name)
        # a fresh dir per run: nothing a stopped (pre-empted) run left half-written is ever read again
        self.work = os.path.abspath(os.path.join(base, f"{zone.name}-{time.strftime('%Y%m%dT%H%M%S')}-{os.getpid()}"))
        os.makedirs(self.work, exist_ok=True)
        self._lock = open(os.path.join(self.work, LOCK_NAME), "w")  # held until this process ends (see clear_stale)
        fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.keep_work = keep_work
        self.ctx = {"root": store.root, "work": self.work, "zone": zone, "slim": C.TILE_SLIM_DOTS if slim is None else bool(slim),
                    "forced_mirror": not store.local, "edge_margin": self.edge_margin,
                    "threads": max(C.TILE_THREADS, 1)}
        self.times: dict[str, float] = {}
        self.qa_red: pd.DataFrame | None = None
        self.stats: dict = {"tiles": len(self.tiles), "grid": list(self.grid), "tile_deg": list(self.sides), "margin_arcsec": self.margin,
                            "zone_edge_margin": self.edge_margin, "workers": self.workers, "work_dir": self.work}

    # -- helpers
    def _timed(self, name: str, t0: float) -> None:
        self.times[name] = round(time.time() - t0, 1)
        L.info("tiles %s: %s done in %.1f s", self.zone.name, name, self.times[name])

    def _tile_inputs(self, tiles: list[Tile]) -> None:
        """Photo list and refcat slices for these tiles (work dir)."""
        work = Store(self.work)
        for t in tiles:
            tp = tile_photos(self.photos, self.boxes, t.ext)
            work.write_parquet(f"tiles/{t.name}/photolist.parquet", tp)
            for rel, cat in self.refcats.items():
                work.write_parquet(f"tiles/{t.name}/refcat/{t.name}{rel}", refcat_slice(cat, self.reach[rel], t.ext))

    def prepare(self, pool: _Pool) -> None:
        t0 = time.time()
        z = self.zone
        photos = self.store.read_parquet(photolist_rel(z.name)).reset_index(drop=True)
        photos[ZONE_ROW] = np.arange(len(photos), dtype=np.int64)
        self.photos = photos
        self.boxes = photo_boxes(photos)
        mjd = photos["mjd"].to_numpy(np.float64) if len(photos) else np.zeros(1)
        self.t_med = float(np.median(mjd))
        self.span_yr = float(np.max(np.abs(mjd - self.t_med))) / 365.25 if len(photos) else 0.0
        self.refcats = {}
        for suffix in (".parquet", "_forced.parquet"):
            rel = f"refcat/{z.name}{suffix}"
            if self.store.exists(rel):
                self.refcats[suffix] = self.store.read_parquet(rel)
        self.reach = {k: refcat_reach(v, self.t_med, self.span_yr) for k, v in self.refcats.items()}
        self._tile_inputs(self.tiles)
        # the zone's dot files, slimmed, into the work dir
        have = set(self.store.listdir("dots"))
        names = [n for n in (f"{o}D{int(d)}.parquet" for o, d in zip(photos["obs_id"], photos["detector"])) if n in have]
        if self.ctx["slim"] or self.ctx["forced_mirror"]:
            self._check_disk(names)
        if self.ctx["slim"] or self.ctx["forced_mirror"]:
            k = max(1, len(names) // (self.workers * 4) + 1)
            res = pool.map(prepare_photos, [(self.ctx, names[i:i + k]) for i in range(0, len(names), k)])
            self.stats["slim_dots"] = {"files": sum(r["dots"] for r in res), "bytes": sum(r["bytes"] for r in res)}
        self.stats["photos"] = int(len(photos))
        self.stats["tile_photos"] = [int(len(tile_photos(photos, self.boxes, t.ext))) for t in self.tiles]
        self._timed("prepare", t0)

    def _check_disk(self, names: list[str]) -> None:
        """Mirror only when the work dir's disk has room: the mirror (estimated from up to 16 files) and as much again for
        the tiles' own files, plus TILE_DISK_RESERVE_GB. Otherwise the tiles read from the root (same results)."""
        if not names:
            return
        sample = names[:: max(1, len(names) // 16)][:16]
        per = 0.0
        for n in sample:
            for rel, on in ((f"dots/{n}", self.ctx["slim"]), (f"{C.FORCED_DIR}/{n}", self.ctx["forced_mirror"])):
                if on:
                    try:
                        per += float(self.store.fs.size(self.store.path(rel)) or 0)
                    except (FileNotFoundError, OSError):
                        pass
        need = 2.0 * per / len(sample) * len(names)
        free = float(shutil.disk_usage(self.work).free)
        self.stats["disk"] = {"mirror_estimate_gb": round(need / 2e9, 2), "free_gb": round(free / 1e9, 2)}
        if need + C.TILE_DISK_RESERVE_GB * 1e9 > free:
            L.warning("tiles %s: %.1f GB free in %s, the mirror and tile files need ~%.1f GB: reading dot and forced files "
                      "from the root instead (slower, same results)", self.zone.name, free / 1e9, self.work, need / 1e9)
            self.ctx["slim"] = self.ctx["forced_mirror"] = False
            self.stats["disk"]["mirror"] = False

    def submit_predictions(self, pool: _Pool) -> list[Future]:
        """known.load_predictions over the zone, whole sessions per task (in session order)."""
        p = self.photos.drop(columns=[ZONE_ROW])
        sess = np.sort(p["session"].unique())
        chunks = [c for c in np.array_split(sess, min(len(sess), self.workers * 2)) if len(c)] if len(sess) else []
        if not chunks:
            return [pool.submit(predictions_part, self.store.root, p.iloc[:0], self.zone)]
        return [pool.submit(predictions_part, self.store.root, p[p["session"].isin(c)], self.zone) for c in chunks]

    def collect_predictions(self, futs: list[Future]) -> str:
        """The predictions, concatenated as known.load_predictions gives them, in the work dir (path)."""
        t0 = time.time()
        parts = [f.result() for f in futs]
        full = [x for x in parts if len(x)]
        pred = pd.concat(full, ignore_index=True) if full else parts[0]
        path = os.path.join(self.work, "predictions.parquet")
        pred.to_parquet(path, index=False)
        self.stats["predictions"] = int(len(pred))
        self._timed("predictions", t0)
        return path

    def run_pass1(self, pool: _Pool) -> None:
        t0 = time.time()
        todo = list(self.tiles)
        done: dict[int, dict] = {}
        retries = 0
        for attempt in range(C.TILE_MAX_RETRIES + 1):
            res = pool.map(pass1, [(self.ctx, t) for t in todo])
            redo = []
            for t, r in zip(todo, res):
                done[t.index] = r
                if r["border"]["border_threats"] and attempt < C.TILE_MAX_RETRIES:
                    redo.append(t)
            if not redo:
                break
            L.warning("tiles %s: border too narrow on %d tiles (%s): redone with a doubled border", self.zone.name, len(redo),
                      ", ".join(t.name for t in redo))
            retries += len(redo)
            todo = []
            for t in redo:
                shutil.rmtree(os.path.join(self.work, "tiles", t.name), ignore_errors=True)
                nt = with_margin(self.zone, t, t.margin_arcsec * 2, self.edge_margin)
                self.tiles[self.tiles.index(t)] = nt
                todo.append(nt)
            self._tile_inputs(todo)
        self.pass1_stats = [done[t.index] for t in self.tiles]
        left = [s["tile"] for s in self.pass1_stats if s["border"]["border_threats"]]
        if left:
            L.warning("tiles %s: border still too narrow after %d retries on %s: groups there may differ from an untiled run",
                      self.zone.name, C.TILE_MAX_RETRIES, left)
        self.stats["border"] = {"retried_tiles": retries, "unresolved_tiles": left,
                                "groups_touching_border": sum(s["border"]["border_groups"] for s in self.pass1_stats)}
        self._timed("pass1", t0)

    def merge1(self) -> dict:
        t0 = time.time()
        z, w, T = self.zone, self.work, self.tiles
        fixed = _concat(_read_parts(w, T, "fixed.parquet"), ["source_id"])
        sid = fixed.column("source_id").to_numpy() if fixed is not None else np.zeros(0, np.int64)
        if np.unique(sid).size != sid.size:
            raise RuntimeError(f"tiles {z.name}: duplicate source_id after the merge ({sid.size - np.unique(sid).size})")
        cells = pd.Series(fixed.column("cell").to_numpy()).value_counts() if fixed is not None and fixed.num_rows else pd.Series(dtype=int)
        out = {"fixed": _write(self.store, z, "fixed.parquet", fixed),
               "members": _write(self.store, z, "members.parquet",
                                 # a source's members all come from one tile, already sorted by obs_id, detector, dot_row
                                 _concat(_read_parts(w, T, "members.parquet"), ["source_id"], ("obs_id",))),
               "movers": _write(self.store, z, "movers.parquet", _concat(_read_parts(w, T, "movers.parquet"), ["session", "mjd", "ra", "dec"])),
               "freedots": _write(self.store, z, FREEDOTS_NAME, _concat(_read_parts(w, T, FREEDOTS_NAME),
                                                                         ["obs_id", "detector", "x", "y"], ("obs_id",))),
               "nonfixed_dots": _write(self.store, z, NONFIXED_DOTS_NAME, _concat(_read_parts(w, T, NONFIXED_DOTS_NAME),
                                                                                   ["session", "mjd", "ra", "dec"], ("obs_id",))),
               "sources": _write(self.store, z, "sources.parquet", _concat(_read_parts(w, T, "sources.parquet"), ["source_id"])),
               "crossids": _write(self.store, z, "crossids.parquet", _concat(_read_parts(w, T, "crossids.parquet"), ["source_id"])),
               "cells_with_2_or_more_sources": int((cells > 1).sum()), "max_sources_per_cell": int(cells.max()) if len(cells) else 0}
        from .spectra import channels
        self.store.write_json(results_rel(z.name, "channels.json"), channels())
        self._timed("merge1", t0)
        return out

    def calib(self) -> dict:
        """calib.run, unchanged, on the tiles' calib dots: member_dots_raw and the spectra it would rebuild from them are
        handed over (calib.fit's own `spec` argument), so the fit sees exactly the untiled inputs it uses."""
        from . import calib, motion_io
        t0 = time.time()
        z = self.zone
        d = _df(_read_parts(self.work, self.tiles, "calib_dots.parquet"))
        if len(d):
            d = d.sort_values(["source_id", "mjd", "detector"], kind="stable").reset_index(drop=True)
        jac = np.full((len(self.photos), 2, 2), np.nan, np.float32)
        for t in _read_parts(self.work, self.tiles, "calib_jac.parquet"):
            j = t.to_pandas()
            r = j[ZONE_ROW].to_numpy(np.int64)
            for a in (0, 1):
                for b in (0, 1):
                    jac[r, a, b] = j[f"j{a}{b}"].to_numpy(np.float32)
        spec = self.store.read_parquet(results_rel(z.name, "sources.parquet")) if self.store.exists(results_rel(z.name, "sources.parquet")) else None
        orig_mdr, orig_fit = motion_io.member_dots_raw, calib.fit
        motion_io.member_dots_raw = lambda store, zone, workers=16: (d, {"member_dots_used": int(len(d))}, jac)
        calib.fit = lambda d_, jac_, xid_, spec_=None, **kw: orig_fit(d_, jac_, xid_, spec, **kw)  # (ref=: borrowing)
        try:
            out = calib.run(self.store, z, self.workers)
        finally:
            motion_io.member_dots_raw, calib.fit = orig_mdr, orig_fit
        out["tiled_calib_dots"] = int(len(d))
        self._timed("calib", t0)
        return out

    def qa(self) -> dict:
        """qa.run once on the zone (after calib, which it reads), the dot files from the local slim mirror when there is
        one (shape columns too, QA_SHAPES auto); a failure is logged and the run goes on."""
        from . import qa
        t0 = time.time()
        out = qa.run_safe(self.store, self.zone, self.workers, dots_store=Store(self.work) if self.ctx["slim"] else None)
        self.qa_red = qa.red_photos(self.store, self.zone)
        self._timed("qa", t0)
        out["step_seconds"] = self.times["qa"]
        return out

    def reduce2(self, pool: _Pool) -> dict:
        """The zone-wide numbers of pass 2: stellar locus, light-curve error model and bad photos, frame offsets."""
        from . import astrometry, classify, lightcurves
        t0 = time.time()
        w, T = self.work, self.tiles
        lv = _df(_read_parts(w, T, "locus_values.parquet"))
        self.locus = classify.stellar_locus(lv) if len(lv) else {}
        # light-curve error model and bad photos, per (origin, detector) in parallel (lc_fit_pair)
        pairs = sorted({k for x in self.pass2a_stats for k in x.get("lc_pairs", [])})
        fits = pool.map(lc_fit_pair, [(w, [t.name for t in T], k) for k in pairs])
        self.lc_model = {k: v for f in fits for k, v in f["model"].items()}
        self.bad_rows = sorted((r for f in fits for r in f["bad_rows"]), key=lambda r: (r["origin"], r["detector"], r["mjd"]))
        keys = [f["bad_keys"] for f in fits if len(f["bad_keys"])]
        self.bad_keys = (pd.concat(keys, ignore_index=True) if keys else
                         pd.DataFrame({"origin": pd.Series(dtype=str), "detector": pd.Series(dtype=np.int64), "mjd": pd.Series(dtype=np.float64)}))
        st: dict = {"lc_fit_points": sum(f["points"] for f in fits), "lc_bad_photos": len(self.bad_rows),
                    "lc_pairs_fitted_on_all_points": [k for k, f in zip(pairs, fits) if f.get("basis") not in ("gaia", None)]}
        res = _df(_read_parts(w, T, "frame_residuals.parquet"))
        self.offsets, self.frame_stats = (astrometry.photo_offsets(res) if len(res) else
                                          (pd.DataFrame({"obs_id": [], "detector": [], "oe": [], "on": [], "n_ref": [], "good": []}),
                                           {"photos_corrected": 0}))
        st.update({"locus": {f"{k}_D{d}": round(v, 5) for (k, d), v in sorted(self.locus.items())}, **self.frame_stats})
        self._timed("reduce2", t0)
        return st

    def merge2(self, xid: pd.DataFrame | None) -> dict:
        from . import astrometry, lightcurves, photoz
        t0 = time.time()
        z, w, T, s = self.zone, self.work, self.tiles, self.store
        out: dict = {}
        src = _concat(_read_parts(w, T, "sources_cal.parquet"), ["source_id"])
        out["sources"] = _write(s, z, "sources.parquet", src)
        cl = _concat(_read_parts(w, T, "classes.parquet"), ["source_id"])
        out["classes"] = _write(s, z, "classes.parquet", cl)
        pz = _concat(_read_parts(w, T, "photoz.parquet"), ["source_id"])
        out["photoz"] = _write(s, z, "photoz.parquet", pz)
        out["colours"] = _write(s, z, "colours.parquet", _concat(_read_parts(w, T, "colours.parquet"), ["source_id", "det_a"]))
        # light curves: one tile at a time into one file (the zone's points are the biggest table); rows in tile order,
        # each tile's sorted by source_id, ref_id, detector, mjd as untiled
        out["lightcurves"] = _write_stream(s, z, "lightcurves.parquet", w, T, ("origin",))
        summ = _concat(_read_parts(w, T, "lightcurves_summary.parquet"), ["source_id", "ref_id", "detector", "origin"])
        out["lightcurves_summary"] = _write(s, z, "lightcurves_summary.parquet", summ)
        if summ is not None and self.lc_model:
            sdf = summ.to_pandas()
            rk = _df(_read_parts(w, T, "lc_ref_keys.parquet"))
            rep = lightcurves.error_report(rk, sdf, np.ones(len(rk), bool), self.lc_model)
            rep["bad_photos"] = self.bad_rows
            if self.qa_red is not None:
                rep["qa_red_photos"] = int(len(self.qa_red))
            s.write_json(results_rel(z.name, "lightcurves_report.json"), {"zone": z.name, **rep})
            out["lightcurves_variable_share"] = {o: rep[o]["variable_share"] for o in ("dot", "forced") if o in rep}
        # photoz report on the merged tables (SDSS DR17 once per zone)
        if pz is not None:
            pzd = pz.to_pandas()
            srcd = src.to_pandas() if src is not None else pd.DataFrame()
            sel = [st.get("photoz_selection") for st in self.pass2b_stats if st.get("photoz_selection")]
            chosen = sel[0] if sel else "all sources (no classes.parquet)"
            if chosen.startswith("classes") and "saturated" in srcd:
                srcd = srcd[~srcd["saturated"].to_numpy(bool)]
            rep = photoz.report(s, z, pzd, srcd, len(pzd), chosen)
            s.write_json(results_rel(z.name, "photoz_report.json"), rep)
            out["photoz_report"] = {k: v for k, v in rep.items() if k != "sdss_dr17"}
        # astrometry
        cor = _concat(_read_parts(w, T, "astrometry.parquet"), ["source_id"])
        raw = _concat(_read_parts(w, T, "astrometry_raw.parquet"), ["source_id"])
        out["astrometry"] = _write(s, z, "astrometry.parquet", cor)
        corrected = any(st["astrometry"]["corrected"] for st in self.pass2b_stats)
        sums = {k: sum(st["astrometry"].get(k, 0) for st in self.pass2b_stats) for k in ("fitted", "with_parallax", "spacecraft_position_dots")}
        cor_df = cor.to_pandas() if cor is not None else pd.DataFrame(columns=astrometry.COLUMNS)
        raw_df = raw.to_pandas() if raw is not None else pd.DataFrame(columns=astrometry.COLUMNS)
        rep = {"zone": z.name, "frame_correction": self.frame_stats if corrected else {"photos_corrected": 0},
               "fitted": sums["fitted"], "with_parallax": sums["with_parallax"],
               "spacecraft_position_dots": sums["spacecraft_position_dots"],
               "vs_gaia_frame_corrected": astrometry.report(cor_df, xid) if corrected else None,
               "vs_gaia_raw": astrometry.report(raw_df, xid)}
        s.write_json(results_rel(z.name, "astrometry_report.json"), rep)
        self._timed("merge2", t0)
        return out

    # -- the whole run
    def run(self) -> dict:
        from . import highpm, orphans
        t_all = time.time()
        z, s = self.zone, self.store
        L.info("tiles %s: %d × %d tiles of %.2f° × %.2f° (border %.0f″, zone edge %s), %d workers, work dir %s", z.name,
               *self.grid, *self.sides, self.margin, "bordered" if self.edge_margin else "clipped", self.workers, self.work)
        pool = _Pool(self.workers)
        steps: dict = {}
        ok = False
        try:
            self.prepare(pool)
            self.run_pass1(pool)
            steps["link"] = self.merge1()
            trk = pool.submit(zone_step, "tracklets", s.root, z, "")  # movers + free dots only: next to calib
            pred_futs = self.submit_predictions(pool)  # in the pool while calib runs here
            steps["calib"] = self.calib()
            steps["qa"] = self.qa()  # before suspects starts: it reads qa_photos.parquet
            pred_path = self.collect_predictions(pred_futs)
            steps["tracklets"] = trk.result()  # score reads its recovered dots (suspects may read tracklets.parquet)
            self.times["tracklets"] = steps["tracklets"]["step_seconds"]
            tail = {k: pool.submit(zone_step, k, s.root, z, pred_path) for k in ("score", "suspects")}
            t0 = time.time()
            self.pass2a_stats = pool.map(pass2a, [(self.ctx, t) for t in self.tiles])
            self._timed("pass2a", t0)
            steps["reduce"] = self.reduce2(pool)
            t0 = time.time()
            self.pass2b_stats = pool.map(pass2b, [(self.ctx, t, self.locus, self.lc_model, self.bad_keys, self.offsets, self.qa_red)
                                                   for t in self.tiles])
            self._timed("pass2b", t0)
            xid = s.read_parquet(results_rel(z.name, "crossids.parquet")) if s.exists(results_rel(z.name, "crossids.parquet")) else None
            steps["merge"] = self.merge2(xid)
            t0 = time.time()
            steps["highpm"] = highpm.run(s, z, self.workers)
            self._timed("highpm", t0)
            t0 = time.time()
            steps["orphans"] = orphans.run(s, z, self.workers, pred=_read_or_none(pred_path))  # never raises
            self._timed("orphans", t0)
            t_wait = time.time()
            for k, f in tail.items():  # score and suspects ran next to pass 2; only what is left is waited for
                steps[k] = f.result()
                self.times[k] = steps[k]["step_seconds"]
            self.times["score_suspects_wait"] = round(time.time() - t_wait, 1)
            ok = True
        finally:
            pool.close()
            if ok and not self.keep_work:
                shutil.rmtree(self.work, ignore_errors=True)
            elif not ok:
                L.error("tiles %s: failed; the work dir is kept for a look: %s", z.name, self.work)
        kids = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
        self.stats.update({
            "seconds": self.times, "wall_s": round(time.time() - t_all, 1),
            "peak_rss_mb_orchestrator": _peak_rss_mb(),
            "peak_rss_mb_worker": round(kids / 1e6 if sys.platform == "darwin" else kids / 1e3, 1),
            "peak_rss_mb_tile_max": max([st.get("peak_rss_mb", 0) for st in [*self.pass1_stats, *self.pass2a_stats, *self.pass2b_stats]] or [0]),
            "owned_sources": sum(st["owned_sources"] for st in self.pass1_stats),
            "owned_movers": sum(st["owned_movers"] for st in self.pass1_stats)})
        sec = self.times
        for k, names in (("link", ("prepare", "pass1", "merge1")), ("calib", ("calib",)),
                         ("merge", ("pass2a", "reduce2", "pass2b", "merge2")), ("highpm", ("highpm",)),
                         ("orphans", ("orphans",))):
            if isinstance(steps.get(k), dict):
                steps[k]["step_seconds"] = round(sum(sec.get(n, 0.0) for n in names), 1)
        self.stats["step_seconds"] = self.stats["wall_s"]
        steps["tiles"] = self.stats
        L.info("tiles %s: done in %.1f s: %s", z.name, self.stats["wall_s"], self.times)
        return steps


def run(store: Store, zone: C.Zone, grid: float | tuple[int, int], workers: int, **kw) -> dict:
    """The combining steps of `zone`, tiled (grid: (nx, ny) or a core side in degrees); per-step stats for summary.json."""
    return Run(store, zone, grid, workers, **kw).run()
