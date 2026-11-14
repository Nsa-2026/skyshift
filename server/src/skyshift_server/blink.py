"""The blink engine's public interface (contracts/api.md).

The app (app.py) and scripts/precompute call only build_frames and build_object.

Frame values are IMAGE minus NASA's zodiacal-light model (the ZODI HDU), in MJy/sr: the zodiacal
glow differs between visits six months apart, and without this the blink's background jumps.

Months clock: every exposure of one detector at the position, one median stack per visit.
Hours clock: single exposures of one detector in time order. A position has 50-80 exposures per
detector over all visits, so the hours clock shows ONE visit: the one with the most exposures in
that detector (the latest on a tie), at most HOURS_MAX_FRAMES frames (whole sessions from its
start). `known` is asked from SkyBoT once per epoch (months: at the visit's mjd_mid, where it
only tells which asteroids crossed the field around then; the median removes them), at the times
the search gives, while the cutouts download (reconcile_known re-asks an epoch whose time moved).
The live months clock stacks at most LIVE_PER_VISIT exposures a visit; precompute stacks all.
"""

from __future__ import annotations

import logging
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
from skyshift_core import Level2

from . import cutouts, gaia, known, search, stack
from .cache import Cache
from .errors import ApiError, NotFoundError, bad_request
from .search import Image

log = logging.getLogger(__name__)

HOURS_MAX_FRAMES = 48
# Live path (GET /api/frames): at most this many exposures per visit go into a months stack, so a
# cold request stays well inside Cloud Run's 60 s. Measured on a laptop line (14 Nov, months 15'
# D2, IRSA mostly busy so S3 reads): every exposure (19 a visit) 84 s cold; 8 a visit 37 s; 6 a
# visit 24-27 s, warm 0.5 s. scripts/precompute passes per_visit=None (full stacks).
LIVE_PER_VISIT = 6


@dataclass
class Frames:
    """GET /api/frames: `meta` is the response JSON. Each epochs[].data_url is a relative name
    (e.g. "frames/m15_d2_v1.bin") whose bytes are in `blobs`: raw little-endian uint16,
    size[1] rows x size[0] columns, north up, east left; value = raw * scale + zero (MJy/sr);
    raw 0 = no data."""

    meta: dict[str, Any]
    blobs: dict[str, bytes] = field(default_factory=dict)
    notices: list[str] = field(default_factory=list)


@dataclass
class Stacked:
    """One months-clock epoch before encoding (also used by obj.measure)."""

    visit: int
    image: np.ndarray  # on the grid, MJy/sr, NaN = no data
    mjd_start: float
    mjd_end: float
    n_exposures: int
    flag_frac: float
    sat_frac: float
    um_min: float
    um_max: float
    mjd_median: float = float("nan")  # median MJD-AVG of the exposures in the stack

    @property
    def mjd_mid(self) -> float:
        return (self.mjd_start + self.mjd_end) / 2.0


def _check(clock: str, fov: float, det: int) -> None:
    if clock not in ("months", "hours"):
        raise bad_request("clock must be months or hours")
    if (clock, int(round(fov))) not in stack.SAMPLING or abs(fov - round(fov)) > 1e-6:
        raise bad_request("fov must be 5 or 15 (months), 20 or 60 (hours), in arcmin")
    if det not in (1, 2, 3, 4, 5, 6):
        raise bad_request("det must be 1 to 6")


def _r(v: float, digits: int) -> float | None:
    return round(float(v), digits) if v is not None and np.isfinite(v) else None


def visit_stacks(images: list[Image], grid: stack.Grid, size_deg: float, cache: Cache,
                 visits_wanted: set[int] | None = None, zodi: bool = True) -> list[Stacked]:
    """Median stack per visit of one detector's images on `grid` (sorted by time).
    zodi: subtract NASA's zodiacal-light model from each exposure first."""
    if visits_wanted is not None:
        images = [im for im in images if im.visit in visits_wanted]
    l2s = cutouts.fetch_many(images, grid.ra, grid.dec, size_deg, cache, zodi=zodi)
    sky = grid.sky()
    by_visit: dict[int, list[tuple[Level2, stack.Resampled]]] = {}
    for im, l2 in zip(images, l2s):
        if l2 is None:
            continue
        rs = stack.resample(l2, grid, sky)
        if not np.isfinite(rs.image).any():
            continue
        by_visit.setdefault(im.visit, []).append((l2, rs))
    out = []
    for v in sorted(by_visit):
        parts = by_visit[v]
        mjds = [l2.mjd_avg for l2, _ in parts]
        ff, sf = stack.fractions([rs for _, rs in parts])
        out.append(Stacked(
            visit=v, image=stack.median_stack([rs.image for _, rs in parts]),
            mjd_start=min(mjds), mjd_end=max(mjds), n_exposures=len(parts), flag_frac=ff, sat_frac=sf,
            um_min=float(np.nanmin([rs.um_min for _, rs in parts])),
            um_max=float(np.nanmax([rs.um_max for _, rs in parts])),
            mjd_median=float(np.median(mjds)),
        ))
    return out


def pick_hours_images(images: list[Image]) -> list[Image]:
    """The hours clock's exposures: the visit with the most exposures (latest on a tie),
    whole sessions from its start, at most HOURS_MAX_FRAMES."""
    if not images:
        return []
    counts = Counter(im.visit for im in images)
    best = max(counts, key=lambda v: (counts[v], v))
    chosen = sorted((im for im in images if im.visit == best), key=lambda im: im.mjd)
    if len(chosen) > HOURS_MAX_FRAMES:
        keep: list[Image] = []
        for s in sorted({im.session for im in chosen}):
            part = [im for im in chosen if im.session == s]
            if keep and len(keep) + len(part) > HOURS_MAX_FRAMES:
                break
            keep += part
        chosen = keep[:HOURS_MAX_FRAMES]
    return chosen


def pick_months_images(images: list[Image], per_visit: int | None) -> list[Image]:
    """At most `per_visit` exposures of each visit, spread evenly in time over the visit (so the
    median still sees several sessions and removes asteroids). None or 0: every exposure."""
    if not per_visit:
        return images
    out: list[Image] = []
    for v in sorted({im.visit for im in images}):
        part = sorted((im for im in images if im.visit == v), key=lambda im: im.mjd)
        if len(part) > per_visit:
            idx = np.unique(np.rint(np.linspace(0, len(part) - 1, per_visit)).astype(int))
            part = [part[i] for i in idx]
        out += part
    return out


def exposure_epochs(images: list[Image], ra: float, dec: float, fov: float, cache: Cache, prefix: str,
                    centers: list[tuple[float, float]] | None = None) -> tuple[list[dict], dict[str, bytes], stack.Grid]:
    """Hours-clock epochs for the given images (one detector). centers: a centre per image, for
    frames that follow a moving object; each such epoch carries its own `center`."""
    size_deg = cutouts.tier_for_field(fov)
    l2s = cutouts.fetch_many(images, ra, dec, size_deg, cache, centers=centers, zodi=True)
    grid0, b = stack.grid_for(ra, dec, "hours", fov)
    sky0 = None if centers else grid0.sky()
    epochs, blobs = [], {}
    for i, (im, l2) in enumerate(zip(images, l2s)):
        if l2 is None:
            continue
        if centers:
            g, _ = stack.grid_for(centers[i][0], centers[i][1], "hours", fov)
            rs = stack.resample(l2, g)
        else:
            rs = stack.resample(l2, grid0, sky0)
        if not np.isfinite(rs.image).any():
            continue
        data, scale, zero = stack.encode(stack.bin_mean(rs.image, b))
        ff, sf = stack.fractions([rs])
        name = f"frames/{prefix}_d{im.det}_{im.obs_id}.bin"
        blobs[name] = data
        ep = {
            "id": f"d{im.det}-{im.obs_id}", "kind": "exposure",
            "mjd_mid": _r(l2.mjd_avg, 6),
            "mjd_start": _r(l2.mjd_beg if l2.mjd_beg is not None else im.t_min, 6),
            "mjd_end": _r(l2.mjd_end if l2.mjd_end is not None else im.t_max, 6),
            "det": im.det, "um_min": _r(rs.um_min, 4), "um_max": _r(rs.um_max, 4),
            "n_exposures": 1, "flag_frac": _r(ff, 4), "sat_frac": _r(sf, 4),
            "data_url": name, "scale": float(scale), "zero": float(zero),
        }
        if centers:
            ep["center"] = {"ra": centers[i][0], "dec": centers[i][1]}
        epochs.append(ep)
    epochs.sort(key=lambda e: e["mjd_mid"])
    return epochs, blobs, grid0.binned(b)


def visit_epochs(stacks: list[Stacked], det: int, prefix: str) -> tuple[list[dict], dict[str, bytes]]:
    epochs, blobs = [], {}
    for s in stacks:
        data, scale, zero = stack.encode(s.image)
        name = f"frames/{prefix}_d{det}_v{s.visit}.bin"
        blobs[name] = data
        epochs.append({
            "id": f"d{det}-v{s.visit}", "kind": "visit",
            "mjd_mid": _r(s.mjd_mid, 6), "mjd_start": _r(s.mjd_start, 6), "mjd_end": _r(s.mjd_end, 6),
            "det": det, "um_min": _r(s.um_min, 4), "um_max": _r(s.um_max, 4),
            "n_exposures": s.n_exposures, "flag_frac": _r(s.flag_frac, 4), "sat_frac": _r(s.sat_frac, 4),
            "data_url": name, "scale": float(scale), "zero": float(zero),
        })
    return epochs, blobs


def known_for_epochs(epochs: list[dict], ra: float, dec: float, fov: float, cache: Cache,
                     *, failed_epochs: list[str] | None = None) -> list[dict]:
    """One SkyBoT cone per epoch (at its own centre if it has one). A SkyBoT failure leaves that
    epoch without known objects and is logged; it never fails the frames."""
    r = fov / 2.0 * 2**0.5

    def one(ep: dict) -> list[dict]:
        c = ep.get("center") or {"ra": ra, "dec": dec}
        try:
            found = known.cone(c["ra"], c["dec"], r, ep["mjd_mid"], cache=cache)
        except ApiError as e:
            log.warning("SkyBoT failed for %s: %s", ep["id"], e)
            if failed_epochs is not None:
                failed_epochs.append(ep["id"])
            return []
        return [{"name": k["name"], "epoch_id": ep["id"], "ra": k["ra"], "dec": k["dec"],
                 "v": k["v"], "source": k["source"]} for k in found]

    if not epochs:
        return []
    with ThreadPoolExecutor(4) as ex:
        return [k for part in ex.map(one, epochs) for k in part]


def planned_epochs(images: list[Image], det: int, clock: str) -> list[dict]:
    """The epochs build_frames expects, from the search times alone ({id, mjd_mid}), so SkyBoT
    can be asked while the cutouts download. Search times are the middle of t_min and t_max,
    which is MJD-AVG to within a second."""
    if clock == "hours":
        return [{"id": f"d{det}-{im.obs_id}", "mjd_mid": im.mjd} for im in images]
    out = []
    for v in sorted({im.visit for im in images}):
        t = [im.mjd for im in images if im.visit == v]
        out.append({"id": f"d{det}-v{v}", "mjd_mid": (min(t) + max(t)) / 2.0})
    return out


KNOWN_MJD_TOLERANCE = 0.001  # days (86 s): a main-belt asteroid moves well under 1" in that time


def reconcile_known(found: list[dict], planned: list[dict], epochs: list[dict], ra: float, dec: float,
                    fov: float, cache: Cache, *, failed_epochs: list[str] | None = None) -> list[dict]:
    """Known objects asked at the planned times -> those of the epochs that were built. An epoch
    whose mjd_mid moved (an exposure at the start or end of a visit had no pixels) is asked again."""
    plan = {p["id"]: p["mjd_mid"] for p in planned}
    ok = {e["id"] for e in epochs if e["id"] in plan and abs(plan[e["id"]] - e["mjd_mid"]) <= KNOWN_MJD_TOLERANCE}
    again = [e for e in epochs if e["id"] not in ok]
    if failed_epochs is not None:
        failed_epochs[:] = [i for i in failed_epochs if i in ok]
    return [k for k in found if k["epoch_id"] in ok] + known_for_epochs(
        again, ra, dec, fov, cache, failed_epochs=failed_epochs)


def frames_meta(clock: str, grid: stack.Grid, epochs: list[dict], stars: list[dict], known_: list[dict],
                archive_checked: str) -> dict[str, Any]:
    return {
        "clock": clock,
        "center": {"ra": grid.ra, "dec": grid.dec},
        "pixel_arcsec": round(grid.pixel_arcsec, 4),
        "size": [grid.n, grid.n],
        "epochs": epochs,
        "gaia": stars,
        "known": known_,
        "data_release": search.DATA_RELEASE,
        "archive_checked": archive_checked,
    }


def build_frames(ra: float, dec: float, fov: float, clock: Literal["months", "hours"], det: int,
                 *, cache: Cache | None = None, per_visit: int | None = LIVE_PER_VISIT) -> Frames:
    """Blink frames for one position, field (arcmin: months 5 or 15, hours 20 or 60) and detector.
    per_visit: months clock, at most this many exposures per visit stack (None: all of them)."""
    _check(clock, fov, det)
    cache = cache or Cache()
    fov_i = int(round(fov))
    found = search.search(ra, dec, cache=cache)
    images = found.of_detector(det)
    if not images:
        raise NotFoundError(f"no SPHEREx images of detector {det} at this position"
                            if found.images else "no SPHEREx images at this position")
    prefix = f"{clock[0]}{fov_i}"
    chosen = pick_months_images(images, per_visit) if clock == "months" else pick_hours_images(images)
    planned = planned_epochs(chosen, det, clock)
    failed_known: list[str] = []
    notices: list[str] = []
    with ThreadPoolExecutor(2) as side:  # Gaia and SkyBoT run while the cutouts download
        stars_f = side.submit(gaia.cone, ra, dec, fov / 2.0 * 2**0.5, cache=cache)
        known_f = side.submit(known_for_epochs, planned, ra, dec, fov, cache, failed_epochs=failed_known)
        if clock == "months":
            grid, _ = stack.grid_for(ra, dec, clock, fov)
            stacks = visit_stacks(chosen, grid, cutouts.tier_for_field(fov), cache)
            epochs, blobs = visit_epochs(stacks, det, prefix)
        else:
            epochs, blobs, grid = exposure_epochs(chosen, ra, dec, fov, cache, prefix)
        if not epochs:
            raise NotFoundError("no usable SPHEREx pixels at this position")
        known_ = reconcile_known(known_f.result(), planned, epochs, ra, dec, fov, cache,
                                 failed_epochs=failed_known)
        if failed_known:
            notices.append("known_lookup_failed")
        try:
            stars = stars_f.result()
        except ApiError as e:
            log.warning("Gaia failed: %s", e)
            stars = []
            notices.append("gaia_lookup_failed")
    return Frames(frames_meta(clock, grid, epochs, stars, known_, found.archive_checked), blobs, notices)


def build_object(ra: float, dec: float, *, cache: Cache | None = None) -> dict[str, Any]:
    """GET /api/object: name, gaia, known_ucd, spectrum[], measure (contracts/api.md)."""
    from .obj import build_object as _build

    return _build(ra, dec, cache=cache)
