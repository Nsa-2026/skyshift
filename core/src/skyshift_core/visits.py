"""The visit rule and the session rule of contracts/api.md.

    sort the exposure times at one position
    new visit where the gap exceeds 60 days
    cut any visit longer than 75 days into equal parts of at most 30 days   (near the ecliptic poles)
    session = run of exposures with gaps under 10 minutes

Ids are 1-based, numbered from the earliest, and returned in the order of the input.
Times are MJD-AVG (UTC exposure midpoints).

Implementation detail: a visit of length L days is cut into n = ceil(L / 30) equal parts counted
from its first exposure, and each session is binned by its own start time, so a session never
straddles two visits. Empty parts are skipped, so visit ids are always consecutive.

Why 75 and equal parts (14 Nov, on the real photo lists): mid-latitude seasons last 45-55 days.
The first rule (cut above 45 days into 30-day bins from the start) split them into a 30-day visit
plus a 4-16-exposure leftover with only one detector pair (Barnard's Star, Epsilon Indi B).
Near the pole (R Draconis, seasons of 67-88 days) equal parts keep every visit at 19+ exposures.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray

VISIT_GAP_DAYS = 60.0
MAX_VISIT_DAYS = 75.0
VISIT_BIN_DAYS = 30.0  # longest part when a visit is cut
SESSION_GAP_DAYS = 10.0 / 1440.0  # 10 minutes


def _as_mjds(mjds: ArrayLike) -> NDArray[np.float64]:
    t = np.asarray(mjds, dtype=np.float64).ravel()
    if not np.all(np.isfinite(t)):
        raise ValueError("mjds must be finite")
    return t


def _runs(sorted_t: NDArray[np.float64], new_run: NDArray[np.bool_]) -> NDArray[np.int64]:
    """1-based run ids for sorted times, given a 'starts a new run' flag per gap."""
    ids = np.ones(sorted_t.size, dtype=np.int64)
    if sorted_t.size > 1:
        ids[1:] += np.cumsum(new_run)
    return ids


def sessions(mjds: ArrayLike) -> NDArray[np.int64]:
    """Session id per exposure: a run of exposures with gaps under 10 minutes. 1 = earliest."""
    t = _as_mjds(mjds)
    if t.size == 0:
        return np.zeros(0, dtype=np.int64)
    order = np.argsort(t, kind="stable")
    ts = t[order]
    sid = _runs(ts, np.diff(ts) >= SESSION_GAP_DAYS)
    out = np.empty_like(sid)
    out[order] = sid
    return out


def visits(mjds: ArrayLike) -> NDArray[np.int64]:
    """Visit id per exposure, by the visit rule. 1 = earliest."""
    t = _as_mjds(mjds)
    if t.size == 0:
        return np.zeros(0, dtype=np.int64)
    order = np.argsort(t, kind="stable")
    ts = t[order]
    raw = _runs(ts, np.diff(ts) > VISIT_GAP_DAYS)
    sess = _runs(ts, np.diff(ts) >= SESSION_GAP_DAYS)

    # sub-bin per exposure: 0 unless its raw visit is longer than MAX_VISIT_DAYS
    sub = np.zeros(ts.size, dtype=np.int64)
    for v in np.unique(raw):
        sel = raw == v
        tv = ts[sel]
        length = tv[-1] - tv[0]
        if length <= MAX_VISIT_DAYS:
            continue
        n_parts = int(np.ceil(length / VISIT_BIN_DAYS))
        width = length / n_parts
        sv = sess[sel]
        # bin each session by its own start time so a session is never split
        first_of_session = {s: tv[sv == s][0] for s in np.unique(sv)}
        start = np.array([first_of_session[s] for s in sv])
        sub[sel] = np.minimum(np.floor((start - tv[0]) / width), n_parts - 1).astype(np.int64)

    # dense, time-ordered numbering of (raw visit, bin)
    key = raw * 1_000_000 + sub
    change = np.diff(key) != 0
    vid = _runs(ts, change)
    out = np.empty_like(vid)
    out[order] = vid
    return out


@dataclass(frozen=True)
class Visit:
    """One visit's summary, in the shape of a months-clock epoch (contracts/api.md)."""

    visit: int
    mjd_start: float  # MJD-AVG of the first exposure
    mjd_end: float  # MJD-AVG of the last exposure
    mjd_mid: float  # (mjd_start + mjd_end) / 2
    n_exposures: int
    n_sessions: int


def summarize_visits(mjds: ArrayLike) -> list[Visit]:
    """One Visit per visit id, sorted by time."""
    t = _as_mjds(mjds)
    v = visits(t)
    s = sessions(t)
    out: list[Visit] = []
    for vid in np.unique(v):
        tv = t[v == vid]
        a, b = float(tv.min()), float(tv.max())
        out.append(Visit(int(vid), a, b, (a + b) / 2.0, int(tv.size), int(np.unique(s[v == vid]).size)))
    return out
