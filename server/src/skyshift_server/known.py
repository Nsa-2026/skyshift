"""Known asteroids and comets near a position at given times: Minor Planet Center orbits moved with
kete (skyshift_core.known_index; design and numbers in docs/research/2026-11-14-live-known-mpc.md).

The index (~41 MB .npz, built from an MPC snapshot) is read once per instance from the Cache
(gs://nasa-glazing-data/cache/ on Cloud Run) under INDEX_KEY, then kept in memory. A call is a
numpy coarse cut over all orbits plus kete n-body on the short list: 0.2-2 s warm.

Entries have the contract's `known[]` shape: {name, epoch_id, ra, dec, v, source: "mpc"}, nearest
first per time. Predicted from the Earth's centre: SPHEREx is in low Earth orbit, so the parallax
against a main-belt asteroid is under 10" (under 2 native pixels). V <= 21; comets have no V.
"""

from __future__ import annotations

import io
import logging
import os
import threading
import time
from typing import Any, Sequence

from skyshift_core import known_index

from .cache import Cache
from .errors import UpstreamError, bad_request

log = logging.getLogger(__name__)

# Cache key of the index (a new snapshot gets a new name, so running instances keep a consistent file).
INDEX_KEY = os.environ.get("SKYSHIFT_KNOWN_INDEX") or "mpc/known_index/known_index_2026-11-14.npz"

_lock = threading.Lock()
_index: known_index.KnownIndex | None = None


def mpc_index(cache: Cache | None = None) -> known_index.KnownIndex:
    """The MPC index, read from the cache on first use and kept for the life of the process."""
    global _index
    if _index is not None:
        return _index
    with _lock:  # one download per instance, even when several requests start together
        if _index is None:
            t0 = time.perf_counter()
            raw = (cache or Cache()).get(INDEX_KEY)
            if raw is None:
                raise UpstreamError(f"known-object index {INDEX_KEY} is missing from the cache")
            _index = known_index.load_index(io.BytesIO(raw))
            log.info("known index %s loaded in %.1f s: %s", INDEX_KEY, time.perf_counter() - t0, _index.meta)
    return _index


def reset_index() -> None:
    """For tests: forget the loaded index."""
    global _index
    with _lock:
        _index = None


def near(ra: float, dec: float, r_arcmin: float, mjds: Sequence[float], *, epoch_ids: Sequence[Any] | None = None,
         cache: Cache | None = None) -> list[dict[str, Any]]:
    """Known objects within r_arcmin of (ra, dec) at each of `mjds` (UTC), one entry per object and
    time; epoch_id = epoch_ids[k] for mjds[k] (None without epoch_ids). Raises ValueError for times
    outside the index's range and UpstreamError when the index is missing."""
    return known_index.known_near(ra, dec, r_arcmin / 60.0, list(mjds), index=mpc_index(cache), epoch_ids=epoch_ids)


def cone(ra: float, dec: float, r_arcmin: float, mjd: float, *, cache: Cache | None = None) -> list[dict[str, Any]]:
    """GET /api/known: known objects within r_arcmin of (ra, dec) at `mjd` (UTC), nearest first."""
    idx = mpc_index(cache)
    if not idx.mjd_lo <= mjd <= idx.mjd_hi:
        raise bad_request(f"mjd must be within the known-object index's range {idx.mjd_lo:.0f}-{idx.mjd_hi:.0f}")
    return near(ra, dec, r_arcmin, [mjd], cache=cache)


def warm_up(cache: Cache | None = None) -> None:
    """Load the index and make one call (imports kete, reads its kernels), so the first user doesn't
    pay for it. Never raises."""
    try:
        t0 = time.perf_counter()
        idx = mpc_index(cache)
        near(269.4462, 4.76883, 3.5, [min(max(61045.0, idx.mjd_lo), idx.mjd_hi)], cache=cache)
        log.info("known warm-up done in %.1f s", time.perf_counter() - t0)
    except Exception:  # noqa: BLE001 - the API still serves frames without known objects
        log.exception("known warm-up failed")


def warm_up_in_background(cache: Cache | None = None) -> threading.Thread | None:
    """Start warm_up in a daemon thread, unless SKYSHIFT_KNOWN_WARMUP=0."""
    if os.environ.get("SKYSHIFT_KNOWN_WARMUP", "1") == "0":
        return None
    t = threading.Thread(target=warm_up, args=(cache,), name="known-warm-up", daemon=True)
    t.start()
    return t
