"""Known asteroids and comets near a position at given times: Minor Planet Center orbits moved with
kete (skyshift_core.known_index; design and numbers in docs/research/2026-11-14-live-known-mpc.md).

The index (~41 MB .npz, built from an MPC snapshot) is read once per instance from the Cache
(gs://nasa-glazing-data/cache/ on Cloud Run) under INDEX_KEY, then kept in memory. A call is a
numpy coarse cut over all orbits plus kete n-body on the short list: 0.2-2 s warm.

Entries have the contract's `known[]` shape: {name, epoch_id, ra, dec, v, source: "mpc"}, nearest
first per time. Predicted from the Earth's centre: SPHEREx is in low Earth orbit, so the parallax
against a main-belt asteroid is under 10" (under 2 native pixels). V <= 21; comets have no V.
position(name, mjd): one object's place by its MPC name, the same way (GET /api/resolve for the
comet and the asteroids among the built-in targets).
"""

from __future__ import annotations

import io
import logging
import os
import threading
import time
from typing import Any, Sequence

import numpy as np
from skyshift_core import known_index

from .cache import Cache
from .errors import UpstreamError, bad_request

log = logging.getLogger(__name__)

# Cache key of the index (a new snapshot gets a new name, so running instances keep a consistent file).
INDEX_KEY = os.environ.get("SKYSHIFT_KNOWN_INDEX") or "mpc/known_index/known_index_2026-11-16.npz"

_lock = threading.Lock()
_index: known_index.KnownIndex | None = None
# At most this many known_near calls at once per process: each one's coarse cut makes ~60 MB of
# float32 temporaries over all orbits, and 8 at once (Cloud Run's concurrency) took a worker from
# 0.46 to 0.98 GB peak (14 Nov). The CPU is shared anyway, so waiting costs little time.
_slots = threading.BoundedSemaphore(int(os.environ.get("SKYSHIFT_KNOWN_SLOTS") or 2))


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
    idx = mpc_index(cache)
    with _slots:
        return known_index.known_near(ra, dec, r_arcmin / 60.0, list(mjds), index=idx, epoch_ids=epoch_ids)


def row_of(idx: known_index.KnownIndex, name: str) -> int | None:
    """Index row of the object called exactly `name` (the index's names: "Elpis", "3I/ATLAS", "(12345)"),
    or None."""
    blob, off = idx.data["name_blob"].tobytes(), idx.data["name_off"]
    want = name.encode("utf-8")
    k = blob.find(want)
    while k >= 0:
        row = int(np.searchsorted(off, k, side="right")) - 1
        if off[row] == k and off[row + 1] == k + len(want):
            return row
        k = blob.find(want, k + 1)
    return None


def position(name: str, mjd: float, *, cache: Cache | None = None) -> tuple[float, float]:
    """(ra, dec) in degrees of the MPC object `name` (as the index names it) at `mjd` (UTC), seen from
    the Earth's centre with light time: kete n-body from the MPC orbit, the computation known.near
    makes. Raises KeyError for a name not in the index, ValueError for a time outside its range or an
    orbit kete can't move, UpstreamError when the index is missing."""
    idx = mpc_index(cache)
    if not idx.mjd_lo <= mjd <= idx.mjd_hi:
        raise ValueError(f"MJD {mjd} outside the known index's range {idx.mjd_lo:.0f}-{idx.mjd_hi:.0f}")
    row = row_of(idx, name)
    if row is None:
        raise KeyError(f"{name!r} is not in the known-object index {INDEX_KEY}")
    kete = known_index._kete()
    with _slots:
        obs = known_index.observer_state(kete, mjd)
        states = kete.propagate_n_body(known_index.kete_states(kete, idx, np.array([row])), obs.jd,
                                       suppress_errors=True)
        seen = kete.fov_state_check([s for s in states if s.is_finite], [kete.OmniDirectionalFOV(obs)])
    if not seen or not len(seen[0].states):
        raise ValueError(f"kete could not move {name!r} to MJD {mjd}")
    ra, dec = np.asarray(seen[0].ra_dec_with_rates, float).reshape(-1, 4)[0, :2]
    return float(ra) % 360.0, float(dec)


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
