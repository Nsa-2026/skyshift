"""Sky geometry: the 6″ cell (contracts/dots.md), tiles, footprints and fast matching.

Footprints (TAP s_region 'POLYGON ICRS ra1 dec1 ... raN decN') are tested in a local gnomonic
projection about the photo centre, which is exact for great-circle edges (they map to straight
lines) and fine over a 3.5° photo.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

from . import config as C

CELLS_PER_DEG = 600  # 3600″ / 6″
CELL_BAND_STRIDE = 10_000_000


# --- cells -----------------------------------------------------------------------------------
def cell_band(dec: ArrayLike) -> NDArray[np.int64]:
    return np.floor((np.asarray(dec, np.float64) + 90.0) * CELLS_PER_DEG).astype(np.int64)


def band_centre_dec(band: ArrayLike) -> NDArray[np.float64]:
    return (np.asarray(band, np.float64) + 0.5) / CELLS_PER_DEG - 90.0


def cell_col(ra: ArrayLike, band: ArrayLike) -> NDArray[np.int64]:
    cosd = np.cos(np.deg2rad(band_centre_dec(band)))
    return np.floor(np.asarray(ra, np.float64) * cosd * CELLS_PER_DEG).astype(np.int64)


def cell_of(ra: ArrayLike, dec: ArrayLike) -> NDArray[np.int64]:
    """contracts/dots.md: band = floor((dec+90)·600), col = floor(ra·cos(dec_band_centre)·600),
    cell = band·10_000_000 + col."""
    band = cell_band(dec)
    return band * CELL_BAND_STRIDE + cell_col(ra, band)


def neighbour_cells(ra: ArrayLike, dec: ArrayLike) -> NDArray[np.int64]:
    """The 9 cells around each position, shape (n, 9).

    Columns of adjacent bands don't line up (cos dec changes from band to band), so the
    neighbouring band's columns are computed from the position itself, not as col ± 1.
    """
    ra = np.asarray(ra, np.float64)
    band = cell_band(dec)
    out = []
    for db in (-1, 0, 1):
        b = band + db
        c = cell_col(ra, b)
        for dc in (-1, 0, 1):
            out.append(b * CELL_BAND_STRIDE + c + dc)
    return np.stack(out, axis=1)


# --- grouping ---------------------------------------------------------------------------------
def snap_cells(ra: ArrayLike, dec: ArrayLike, arcsec: float) -> NDArray[np.int64]:
    """Cell id on a grid of `arcsec` cells (same construction as the contract's 6″ cell)."""
    k = 3600.0 / arcsec
    band = np.floor((np.asarray(dec, np.float64) + 90.0) * k).astype(np.int64)
    cosd = np.cos(np.deg2rad((band + 0.5) / k - 90.0))
    col = np.floor(np.asarray(ra, np.float64) * cosd * k).astype(np.int64)
    return band * 100_000_000 + col


def fof_groups(ra: ArrayLike, dec: ArrayLike, link_arcsec: float, snap_arcsec: float = 1.0) -> NDArray[np.int64]:
    """Friends-of-friends group id per point, linking points closer than link_arcsec.

    Points are first snapped to snap_arcsec cells (a star's dots pile into a few cells), and the
    linking runs on cell centres, so a crowded field doesn't explode into millions of pairs.
    Accuracy is about ±snap_arcsec on the linking length.
    """
    ra = np.asarray(ra, np.float64)
    dec = np.asarray(dec, np.float64)
    if ra.size == 0:
        return np.zeros(0, dtype=np.int64)
    cells, inv = np.unique(snap_cells(ra, dec, snap_arcsec), return_inverse=True)
    inv = inv.ravel()
    xyz = radec_to_xyz(ra, dec)
    cen = np.zeros((cells.size, 3))
    np.add.at(cen, inv, xyz)
    cen /= np.linalg.norm(cen, axis=1, keepdims=True)
    pairs = cKDTree(cen).query_pairs(chord(link_arcsec), output_type="ndarray")
    g = coo_matrix((np.ones(len(pairs), dtype=np.int8), (pairs[:, 0], pairs[:, 1])), shape=(cells.size, cells.size))
    _, lab = connected_components(g, directed=False)
    return lab[inv].astype(np.int64)


def sessions_covering(ra: ArrayLike, dec: ArrayLike, footprints: list["Footprint"], photo_sessions: ArrayLike) -> NDArray[np.int64]:
    """Number of distinct sessions whose photo footprints contain each position."""
    ra = np.atleast_1d(np.asarray(ra, np.float64))
    dec = np.atleast_1d(np.asarray(dec, np.float64))
    ps = np.asarray(photo_sessions, np.int64)
    if ra.size == 0:
        return np.zeros(0, dtype=np.int64)
    sess = np.unique(ps)
    pos = {s: i for i, s in enumerate(sess)}
    seen = np.zeros((ra.size, sess.size), dtype=bool)
    for fp, s in zip(footprints, ps):
        inside = fp.contains(ra, dec)
        if inside.any():
            seen[inside, pos[int(s)]] = True
    return seen.sum(axis=1).astype(np.int64)


# --- projection and polygons -----------------------------------------------------------------
def gnomonic(ra0: float, dec0: float, ra: ArrayLike, dec: ArrayLike) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Tangent-plane (xi east, eta north) in degrees about (ra0, dec0)."""
    a0, d0 = np.deg2rad(ra0), np.deg2rad(dec0)
    a, d = np.deg2rad(np.asarray(ra, np.float64)), np.deg2rad(np.asarray(dec, np.float64))
    cosc = np.sin(d0) * np.sin(d) + np.cos(d0) * np.cos(d) * np.cos(a - a0)
    xi = np.cos(d) * np.sin(a - a0) / cosc
    eta = (np.cos(d0) * np.sin(d) - np.sin(d0) * np.cos(d) * np.cos(a - a0)) / cosc
    xi = np.where(cosc > 0, xi, np.inf)
    eta = np.where(cosc > 0, eta, np.inf)
    return np.rad2deg(xi), np.rad2deg(eta)


def parse_polygon(s_region: str) -> NDArray[np.float64]:
    """'POLYGON ICRS ra dec ra dec ...' -> (n, 2) vertices, closing vertex dropped."""
    parts = s_region.replace(",", " ").split()
    nums = [float(p) for p in parts if _is_num(p)]
    v = np.array(nums, dtype=np.float64).reshape(-1, 2)
    if len(v) > 1 and np.allclose(v[0], v[-1]):
        v = v[:-1]
    return v


def _is_num(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def polygon_centre(v: NDArray[np.float64]) -> tuple[float, float]:
    xyz = radec_to_xyz(v[:, 0], v[:, 1]).mean(axis=0)
    ra = float(np.rad2deg(np.arctan2(xyz[1], xyz[0])) % 360.0)
    dec = float(np.rad2deg(np.arcsin(xyz[2] / np.linalg.norm(xyz))))
    return ra, dec


class Footprint:
    """A photo's footprint: inside test and distance to its edge, in a local projection."""

    def __init__(self, vertices: NDArray[np.float64]):
        self.vertices = vertices
        self.ra0, self.dec0 = polygon_centre(vertices)
        self.px, self.py = gnomonic(self.ra0, self.dec0, vertices[:, 0], vertices[:, 1])

    @classmethod
    def from_s_region(cls, s: str) -> "Footprint":
        return cls(parse_polygon(s))

    def radius_deg(self) -> float:
        return float(np.max(angsep(self.ra0, self.dec0, self.vertices[:, 0], self.vertices[:, 1])))

    def contains(self, ra: ArrayLike, dec: ArrayLike) -> NDArray[np.bool_]:
        x, y = gnomonic(self.ra0, self.dec0, ra, dec)
        return _point_in_poly(x, y, self.px, self.py)

    def edge_distance_deg(self, ra: ArrayLike, dec: ArrayLike) -> NDArray[np.float64]:
        """Signed distance to the edge (degrees, tangent plane): > 0 inside, < 0 outside."""
        x, y = gnomonic(self.ra0, self.dec0, ra, dec)
        d = _dist_to_poly(x, y, self.px, self.py)
        return np.where(_point_in_poly(x, y, self.px, self.py), d, -d)


def _point_in_poly(x, y, px, py) -> NDArray[np.bool_]:
    x = np.asarray(x, np.float64)
    y = np.asarray(y, np.float64)
    inside = np.zeros(x.shape, dtype=bool)
    n = len(px)
    for k in range(n):
        x1, y1, x2, y2 = px[k], py[k], px[(k + 1) % n], py[(k + 1) % n]
        cond = (y1 > y) != (y2 > y)
        with np.errstate(divide="ignore", invalid="ignore"):
            xc = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
        inside ^= cond & (x < xc)
    return inside & np.isfinite(x)


def _dist_to_poly(x, y, px, py) -> NDArray[np.float64]:
    x = np.asarray(x, np.float64)
    y = np.asarray(y, np.float64)
    best = np.full(x.shape, np.inf)
    n = len(px)
    for k in range(n):
        x1, y1, x2, y2 = px[k], py[k], px[(k + 1) % n], py[(k + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        t = np.clip(((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy), 0.0, 1.0)
        best = np.minimum(best, np.hypot(x - (x1 + t * dx), y - (y1 + t * dy)))
    return best


# --- spherical helpers -----------------------------------------------------------------------
def radec_to_xyz(ra: ArrayLike, dec: ArrayLike) -> NDArray[np.float64]:
    a = np.deg2rad(np.asarray(ra, np.float64))
    d = np.deg2rad(np.asarray(dec, np.float64))
    return np.stack([np.cos(d) * np.cos(a), np.cos(d) * np.sin(a), np.sin(d)], axis=-1)


def angsep(ra1, dec1, ra2, dec2) -> NDArray[np.float64]:
    """Great-circle separation in degrees (haversine)."""
    a1, d1, a2, d2 = (np.deg2rad(np.asarray(v, np.float64)) for v in (ra1, dec1, ra2, dec2))
    h = np.sin((d2 - d1) / 2) ** 2 + np.cos(d1) * np.cos(d2) * np.sin((a2 - a1) / 2) ** 2
    return np.rad2deg(2 * np.arcsin(np.sqrt(np.clip(h, 0, 1))))


def chord(arcsec: float) -> float:
    return 2.0 * np.sin(np.deg2rad(arcsec / 3600.0) / 2.0)


class SkyTree:
    """KD-tree on unit vectors for radius queries in arcsec."""

    def __init__(self, ra: ArrayLike, dec: ArrayLike):
        self.n = int(np.asarray(ra).size)
        self.tree = cKDTree(radec_to_xyz(ra, dec).reshape(-1, 3)) if self.n else None

    def query_radius(self, ra, dec, arcsec: float) -> list[list[int]]:
        ra = np.atleast_1d(np.asarray(ra, np.float64))
        if self.tree is None:
            return [[] for _ in range(ra.size)]
        return self.tree.query_ball_point(radec_to_xyz(ra, np.atleast_1d(dec)), chord(arcsec))

    def nearest(self, ra, dec) -> tuple[NDArray[np.float64], NDArray[np.int64]]:
        """(separation arcsec, index) of the nearest point; inf / -1 if the tree is empty."""
        ra = np.atleast_1d(np.asarray(ra, np.float64))
        if self.tree is None:
            return np.full(ra.size, np.inf), np.full(ra.size, -1)
        d, i = self.tree.query(radec_to_xyz(ra, np.atleast_1d(dec)))
        sep = np.rad2deg(2 * np.arcsin(np.clip(d / 2, 0, 1))) * 3600.0
        return sep, i.astype(np.int64)


def offset_radec(ra, dec, east_arcsec, north_arcsec) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Small offsets (arcsec) on the sky; fine for the minutes-to-arcmin moves we need."""
    dec = np.asarray(dec, np.float64)
    ndec = dec + np.asarray(north_arcsec) / 3600.0
    nra = np.asarray(ra, np.float64) + np.asarray(east_arcsec) / 3600.0 / np.cos(np.deg2rad(dec))
    return nra % 360.0, ndec
