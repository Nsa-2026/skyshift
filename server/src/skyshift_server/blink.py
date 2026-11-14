"""The blink engine's public interface (contracts/api.md). Implemented by the engine build.

The app (app.py) and scripts/precompute call only these two functions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .cache import Cache


@dataclass
class Frames:
    """GET /api/frames: `meta` is the response JSON. Each epochs[].data_url is a relative name
    (e.g. "frames/m15_d2_v1.bin") whose bytes are in `blobs`: raw little-endian uint16,
    size[1] rows x size[0] columns, north up, east left; value = raw * scale + zero (MJy/sr);
    raw 0 = no data."""

    meta: dict[str, Any]
    blobs: dict[str, bytes] = field(default_factory=dict)


def build_frames(ra: float, dec: float, fov: float, clock: Literal["months", "hours"], det: int,
                 *, cache: Cache | None = None) -> Frames:
    """Blink frames for one position, field (arcmin: months 5 or 15, hours 20 or 60) and detector."""
    raise NotImplementedError


def build_object(ra: float, dec: float, *, cache: Cache | None = None) -> dict[str, Any]:
    """GET /api/object: name, gaia, known_ucd, spectrum[], measure (contracts/api.md)."""
    raise NotImplementedError
