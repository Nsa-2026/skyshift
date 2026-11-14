"""Filter, sort and summarise /data/candidates.json (contracts/api.md, candidates.json). Pure functions."""
from __future__ import annotations

import math
from typing import Any, Iterable

KINDS = ("mover", "high_pm", "spectral", "shape")
GRADE_ORDER = {"A": 0, "B": 1, None: 2, "C": 3}

# Fields kept in list views; get_candidate returns the full record.
SUMMARY_FIELDS = (
    "id", "kind", "grade", "score", "snr", "ra", "dec", "mjd", "clock", "det", "zone",
    "rate_arcsec_h", "pm_mas_yr", "pm_err_mas_yr", "pa_deg", "nearest_known_arcsec",
    "gaia_match", "catwise_match", "labels", "reasons", "flags", "why",
)


def separation_arcmin(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """Great-circle distance in arcmin (haversine; positions in degrees)."""
    r1, d1, r2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    h = math.sin((d2 - d1) / 2) ** 2 + math.cos(d1) * math.cos(d2) * math.sin((r2 - r1) / 2) ** 2
    return math.degrees(2 * math.asin(min(1.0, math.sqrt(h)))) * 60.0


def summarise(c: dict[str, Any]) -> dict[str, Any]:
    out = {k: c.get(k) for k in SUMMARY_FIELDS}
    out["has_static_frames"] = bool(c.get("frames_url"))
    return out


def select(
    candidates: Iterable[dict[str, Any]],
    *,
    kind: str | None = None,
    zone: str | None = None,
    det: int | None = None,
    min_score: float | None = None,
    min_snr: float | None = None,
    grade: str | None = None,
    exclude_gaia: bool = False,
    exclude_flagged: bool = False,
    min_known_arcsec: float | None = None,
    near: tuple[float, float, float] | None = None,
) -> list[dict[str, Any]]:
    """Candidates passing every filter given. `near` = (ra, dec, radius_arcmin); each row then
    carries `distance_arcmin`. `min_known_arcsec` keeps rows whose nearest known object is at
    least that far, or none was found within 600″ (null)."""
    if kind is not None and kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    if grade is not None and grade not in ("A", "B", "C"):
        raise ValueError("grade must be A, B or C")
    out = []
    for c in candidates:
        if kind and c.get("kind") != kind:
            continue
        if zone and zone != c.get("zone") and zone not in (c.get("zones") or []):
            continue
        if det is not None and c.get("det") != det:
            continue
        if min_score is not None and (c.get("score") or 0.0) < min_score:
            continue
        if min_snr is not None and (c.get("snr") or 0.0) < min_snr:
            continue
        if grade and c.get("grade") != grade:
            continue
        if exclude_gaia and c.get("gaia_match"):
            continue
        if exclude_flagged and c.get("flags"):
            continue
        if min_known_arcsec is not None:
            nk = c.get("nearest_known_arcsec")
            if nk is not None and nk < min_known_arcsec:
                continue
        if near is not None:
            d = separation_arcmin(near[0], near[1], c["ra"], c["dec"])
            if d > near[2]:
                continue
            c = {**c, "distance_arcmin": round(d, 3)}
        out.append(c)
    return out


def rank(rows: list[dict[str, Any]], sort: str = "score") -> list[dict[str, Any]]:
    """Sort: `score` (grade A, B, ungraded, C; then score), `snr`, `rate` (movers' ″/h, else proper
    motion), `distance` (needs `near`). Highest first, except distance."""
    if sort == "score":
        key = lambda c: (GRADE_ORDER.get(c.get("grade"), 2), -(c.get("score") or 0.0))
    elif sort == "snr":
        key = lambda c: -(c.get("snr") or 0.0)
    elif sort == "rate":
        key = lambda c: -(c.get("rate_arcsec_h") or (c.get("pm_mas_yr") or 0.0) / 1000.0)
    elif sort == "distance":
        key = lambda c: c.get("distance_arcmin", math.inf)
    else:
        raise ValueError("sort must be score, snr, rate or distance")
    return sorted(rows, key=key)


def find(candidates: Iterable[dict[str, Any]], cid: str) -> dict[str, Any] | None:
    return next((c for c in candidates if c.get("id") == cid), None)
