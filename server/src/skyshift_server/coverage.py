"""How much of a requested field each photo covers, from its footprint alone (no pixels).

The search returns every image whose footprint holds the field's centre. When the centre lies
near a detector's edge, part of the field is off the detector: those frame pixels have no data
(raw 0, drawn white), so a single exposure (hours clock) or a stack of a few (live months clock)
shows a half-empty square. blink.py uses these numbers to prefer photos that cover the field.

Footprint: the search row's s_region ('POLYGON ICRS ra dec ...': the WCS at the four outer pixel
corners), from IRSA's SIA or SkyShift's photo lists (search_fallback.py). Field: the frame's square
on the tangent plane at its centre, north up (stack.grid_for), sampled at SAMPLES x SAMPLES points.
An image's coverage is the share of those points inside its footprint; a stack's is the share inside
at least one of its images. The test is a crossing test on the same tangent plane, where the
footprint's great-circle edges are straight lines.

An image without a usable s_region (a search saved before footprints were kept) has unknown
coverage, and the selection treats it as covering the whole field, as before.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable

import numpy as np
from numpy.typing import NDArray

from . import stack
from .search import Image
from .search_fallback import parse_polygon

SAMPLES = 32  # sample points a side: 1024 points, each 0.1% of the field
FULL = 0.9  # an image covering at least this share of the field counts as covering it
USABLE = 0.5  # hours clock: an exposure covering less is left out when its session has better ones


def field_side_deg(clock: str, fov: float) -> float:
    """Side of the frame's square in degrees (stack.grid_for: pixels x pixel size)."""
    grid, _ = stack.grid_for(0.0, 0.0, clock, fov)
    return grid.n * grid.pixel_arcsec / 3600.0


def tangent(ra0: float, dec0: float, ra, dec) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.bool_]]:
    """Gnomonic (TAN) coordinates in degrees of (ra, dec) on the plane touching the sky at
    (ra0, dec0): xi east, eta north. ok is False for points on the far side of the sky."""
    a0, d0 = math.radians(ra0), math.radians(dec0)
    a, d = np.deg2rad(np.asarray(ra, np.float64)), np.deg2rad(np.asarray(dec, np.float64))
    cosc = math.sin(d0) * np.sin(d) + math.cos(d0) * np.cos(d) * np.cos(a - a0)
    ok = cosc > 1e-6
    safe = np.where(ok, cosc, 1.0)
    xi = np.rad2deg(np.cos(d) * np.sin(a - a0) / safe)
    eta = np.rad2deg((math.cos(d0) * np.sin(d) - math.sin(d0) * np.cos(d) * np.cos(a - a0)) / safe)
    return xi, eta, ok


def field_points(side_deg: float, n: int = SAMPLES) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """(xi, eta) in degrees of n x n points at the centres of an n x n split of the square."""
    c = ((np.arange(n) + 0.5) / n - 0.5) * side_deg
    xx, yy = np.meshgrid(c, c)
    return xx.ravel(), yy.ravel()


def inside(vx: NDArray, vy: NDArray, px: NDArray, py: NDArray) -> NDArray[np.bool_]:
    """Even-odd test: which points (px, py) lie inside the polygon with vertices (vx, vy) (open or
    closed; a repeated vertex is a zero-length edge, which never crosses)."""
    x1, y1 = vx[:, None], vy[:, None]
    x2, y2 = np.roll(vx, -1)[:, None], np.roll(vy, -1)[:, None]
    py_, px_ = py[None, :], px[None, :]
    cross = (y1 > py_) != (y2 > py_)
    with np.errstate(divide="ignore", invalid="ignore"):
        xc = x1 + (py_ - y1) * (x2 - x1) / (y2 - y1)
    return np.logical_xor.reduce(cross & (px_ < xc), axis=0)


def footprint_mask(s_region: str | None, ra: float, dec: float, side_deg: float,
                   n: int = SAMPLES) -> NDArray[np.bool_] | None:
    """Which of the field's n x n sample points the footprint holds; None when it is unknown
    (no s_region, too few vertices, or a vertex 90 degrees or more away)."""
    if not s_region:
        return None
    v = parse_polygon(s_region)
    if len(v) < 3:
        return None
    vx, vy, ok = tangent(ra, dec, v[:, 0], v[:, 1])
    if not ok.all():
        return None
    px, py = field_points(side_deg, n)
    return inside(vx, vy, px, py)


@dataclass
class Coverage:
    """Coverage of one field by the images of one search (keyed by Image.key)."""

    masks: dict[str, NDArray[np.bool_]] = field(default_factory=dict)  # images with a known footprint

    def known(self, im: Image) -> bool:
        return im.key in self.masks

    def frac(self, im: Image) -> float:
        """Share of the field inside the image's footprint, 0-1 (1 when unknown)."""
        m = self.masks.get(im.key)
        return 1.0 if m is None else float(m.mean())

    def union(self, ims: Iterable[Image]) -> float:
        """Share of the field inside at least one of the images (1 when one of them is unknown)."""
        acc = None
        for im in ims:
            m = self.masks.get(im.key)
            if m is None:
                return 1.0
            acc = m.copy() if acc is None else (acc | m)
        return 0.0 if acc is None else float(acc.mean())

    def gain(self, have: Iterable[Image], im: Image) -> float:
        """Share of the field that `im` adds to what `have` already covers (0 if any is unknown)."""
        m = self.masks.get(im.key)
        if m is None:
            return 0.0
        acc = np.zeros_like(m)
        for h in have:
            hm = self.masks.get(h.key)
            if hm is None:
                return 0.0
            acc |= hm
        return float((m & ~acc).mean())


def of_images(images: Iterable[Image], ra: float, dec: float, side_deg: float) -> Coverage:
    """Coverage of the side_deg square centred on (ra, dec), north up, by each image's footprint."""
    out = Coverage()
    for im in images:
        m = footprint_mask(im.s_region, ra, dec, side_deg)
        if m is not None:
            out.masks[im.key] = m
    return out


def for_frames(images: Iterable[Image], ra: float, dec: float, clock: str, fov: float) -> Coverage:
    """Coverage of one frame field (clock, fov arcmin), as blink.build_frames shows it."""
    return of_images(images, ra, dec, field_side_deg(clock, fov))
