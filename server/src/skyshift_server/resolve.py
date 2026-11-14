"""GET /api/resolve?q= (contracts/api.md): any coordinate format, or a built-in name. No SIMBAD.

Coordinates: `08h55m10.831s -7d14m42.53s`, `08:55:10.8 -07:14:42`, `08 55 10.8 -07 14 42`,
`133.795 -7.245`, `133.795°, −7.245°`, `133d47m42s -7d14m42s`, `J085510.83-071442.5`.
RA written as sexagesimal without units (colons or spaces) or with `h` is in hours; a single number,
or one with `d`/`°`, is in degrees. Dec is always degrees. Commas, degree signs, primes and the
Unicode minus are accepted.

Names: the five targets in scripts/validate/data/targets.json (moved from Gaia's epoch 2016.0 to
the present with their proper motion) and comet 3I/ATLAS (position at the middle of its SPHEREx
sequence of 14 Dec 2025), each with a few aliases.
"""

from __future__ import annotations

import json
import math
import os
import re
import unicodedata
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from .errors import ApiError

REF_JYEAR = 2016.0  # Gaia DR3 epoch of targets.json

# Comet 3I/ATLAS: mid-point of NASA's per-exposure comet_ra/comet_dec over SPHEREx's 14 Dec 2025
# sequence (MJD 61023.19-61024.00, scripts/validate/data/photolists/3i-atlas.csv). It moves
# ~176"/h, so this is where the Hours blink is, not where the comet is today.
COMET = {"slug": "3i-atlas", "name": "3I/ATLAS", "ra": 166.82745, "dec": 5.27872,
         "pmra": None, "pmdec": None}

ALIASES: dict[str, list[str]] = {
    "barnard": ["Barnard", "Barnard's Star", "Barnards Star", "Barnard Star", "GJ 699", "Gl 699",
                "V2500 Oph", "HIP 87937"],
    "epsindib": ["Epsilon Indi B", "Eps Indi B", "ε Indi B", "Epsilon Indi Ba", "Eps Ind B",
                 "Epsilon Indi Bab", "eps Indi Bab"],
    "wise0855": ["WISE 0855", "WISE 0855-0714", "WISE J0855-0714", "WISE J085510.83-071442.5",
                 "WISEA J085510.74-071442.5", "W0855"],
    "rwaur": ["RW Aurigae", "RW Aur", "RW Aur A"],
    "rdra": ["R Draconis", "R Dra"],
    "3i-atlas": ["3I/ATLAS", "3I", "3I ATLAS", "ATLAS", "C/2025 N1", "C/2025 N1 (ATLAS)",
                 "3I/2025 N1", "interstellar comet"],
}

_MINUS = "−‒–—‐‑﹣－"
_PLUS = "＋"


def _targets_path() -> Path:
    env = os.environ.get("SKYSHIFT_TARGETS")
    if env:
        return Path(env)
    root = Path(os.environ.get("SKYSHIFT_REPO_ROOT") or Path(__file__).resolve().parents[3])
    return root / "scripts" / "validate" / "data" / "targets.json"


def norm_name(s: str) -> str:
    """Lowercase, Greek epsilon spelled out, only letters and digits kept."""
    s = unicodedata.normalize("NFKC", s).lower().replace("ε", "eps").replace("epsilon", "eps")
    return re.sub(r"[^a-z0-9]", "", s)


@lru_cache(maxsize=1)
def builtins() -> dict[str, dict[str, Any]]:
    """slug -> {slug, name, ra, dec, pmra, pmdec} at epoch 2016.0 (comet: fixed position)."""
    out: dict[str, dict[str, Any]] = {}
    for t in json.loads(_targets_path().read_text())["targets"]:
        out[t["slug"]] = {k: t.get(k) for k in ("slug", "name", "ra", "dec", "pmra", "pmdec")}
    out[COMET["slug"]] = dict(COMET)
    return out


@lru_cache(maxsize=1)
def alias_index() -> dict[str, str]:
    idx: dict[str, str] = {}
    for slug, t in builtins().items():
        for a in [slug, t["name"], *ALIASES.get(slug, [])]:
            idx[norm_name(a)] = slug
    return idx


def jyear(now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    jd = now.timestamp() / 86400.0 + 2440587.5
    return 2000.0 + (jd - 2451545.0) / 365.25


def position_now(t: dict[str, Any], now: datetime | None = None) -> tuple[float, float]:
    """Move a built-in from 2016.0 to `now` with its proper motion (parallax ignored, < 0.6")."""
    ra, dec = float(t["ra"]), float(t["dec"])
    if t.get("pmra") is None or t.get("pmdec") is None:
        return ra, dec
    dt = jyear(now) - REF_JYEAR
    dec2 = dec + t["pmdec"] * dt / 3.6e6
    ra2 = (ra + t["pmra"] * dt / 3.6e6 / math.cos(math.radians(dec))) % 360.0
    return ra2, dec2


# ---------------------------------------------------------------- coordinates

class CoordError(ValueError):
    pass


_COMPACT = re.compile(
    r"^(?:[a-z0-9]+\s*)?j?(\d{2})(\d{2})(\d{2}(?:\.\d+)?)([+-])(\d{2})(\d{2})(\d{2}(?:\.\d+)?)$")
_TOKEN = re.compile(r"^([+-]?)(\d+(?:\.\d*)?|\.\d+)([hdms]?)$")


def _normalize_coords(q: str) -> str:
    s = unicodedata.normalize("NFKC", q).strip().lower()
    for ch in _MINUS:
        s = s.replace(ch, "-")
    s = s.replace(_PLUS, "+")
    # unit symbols -> h d m s
    s = s.replace("°", "d").replace("deg", "d").replace("º", "d")
    s = s.replace("′′", "s").replace("''", "s").replace("″", "s").replace('"', "s")
    s = s.replace("′", "m").replace("'", "m")
    s = s.replace("ʰ", "h").replace("ᵐ", "m").replace("ˢ", "s")
    return re.sub(r"(?<=[\d.])\s+([hdms])(?![a-z])", r"\1", s)  # "133.8 d" -> "133.8d"


def _part_value(tokens: list[tuple[str, str, str]], *, is_ra: bool) -> float:
    if not 1 <= len(tokens) <= 3:
        raise CoordError("expected 1 to 3 parts per coordinate")
    sign = -1.0 if tokens[0][0] == "-" else 1.0
    if any(t[0] for t in tokens[1:]):
        raise CoordError("sign inside a coordinate")
    nums = [float(t[1]) for t in tokens]
    units = [t[2] for t in tokens]
    first = units[0]
    if is_ra:
        hours = first == "h" or (first == "" and len(tokens) > 1)
        if first not in ("", "h", "d"):
            raise CoordError("RA must start with hours or degrees")
        if sign < 0:
            raise CoordError("RA cannot be negative")
    else:
        hours = False
        if first not in ("", "d"):
            raise CoordError("Dec must start with degrees")
    expected = ["h" if hours else "d", "m", "s"]
    for i, u in enumerate(units):
        if u and u != expected[i]:
            raise CoordError(f"unexpected unit {u!r}")
    for i in (1, 2):
        if i < len(nums) and nums[i] >= 60:
            raise CoordError("minutes and seconds must be under 60")
    for i in range(len(nums) - 1):  # only the last part may have a fraction
        if nums[i] != int(nums[i]):
            raise CoordError("only the last part may have decimals")
    val = nums[0] + (nums[1] / 60 if len(nums) > 1 else 0) + (nums[2] / 3600 if len(nums) > 2 else 0)
    if hours:
        if nums[0] >= 24:
            raise CoordError("hours must be under 24")
        val *= 15.0
    return sign * val


def parse_coords(q: str) -> tuple[float, float]:
    """(ra, dec) in degrees, or CoordError."""
    s = _normalize_coords(q)
    if not s:
        raise CoordError("empty query")
    m = _COMPACT.match(s.replace(" ", "")) if re.search(r"\d{6}", s) else None
    if m:
        hh, mm, ss, sg, dd, dm, ds = m.groups()
        ra = _part_value([("", hh, "h"), ("", mm, "m"), ("", ss, "s")], is_ra=True)
        dec = _part_value([(sg, dd, "d"), ("", dm, "m"), ("", ds, "s")], is_ra=False)
        return _check(ra, dec)

    if s.count(",") > 1:
        raise CoordError("too many commas")
    halves = s.split(",") if "," in s else None

    def tokenize(text: str) -> list[tuple[str, str, str]]:
        text = text.replace(":", " ")
        text = re.sub(r"([hdms])", r"\1 ", text)              # a space after each unit
        text = re.sub(r"(?<=[\d.hdms])\s*([+-])", r" \1", text)  # split "...-7.2" at the sign
        text = re.sub(r"([+-])\s+", r"\1", text)               # "- 7" -> "-7"
        out = []
        for tok in text.split():
            mt = _TOKEN.match(tok)
            if not mt:
                raise CoordError(f"can't read {tok!r}")
            out.append(mt.groups())
        return out

    if halves is not None:
        ra_t, dec_t = tokenize(halves[0]), tokenize(halves[1])
    else:
        toks = tokenize(s)
        split = None
        for i, t in enumerate(toks[1:], 1):
            if t[0]:  # first signed token after the start opens Dec
                split = i
                break
        if split is None:
            units = [t[2] for t in toks]
            if "h" in units and "d" in units[units.index("h") + 1:]:
                split = units.index("d", units.index("h") + 1)
            elif units[0] == "d" and "d" in units[1:]:
                split = units.index("d", 1)
            elif len(toks) % 2 == 0:
                split = len(toks) // 2
            else:
                raise CoordError("can't tell where RA ends and Dec starts")
        ra_t, dec_t = toks[:split], toks[split:]
    if not ra_t or not dec_t:
        raise CoordError("need both RA and Dec")
    return _check(_part_value(ra_t, is_ra=True), _part_value(dec_t, is_ra=False))


def _check(ra: float, dec: float) -> tuple[float, float]:
    if not (math.isfinite(ra) and math.isfinite(dec)):
        raise CoordError("not a number")
    if not 0 <= ra <= 360:
        raise CoordError("RA must be 0-360 degrees (0-24 h)")
    if not -90 <= dec <= 90:
        raise CoordError("Dec must be -90 to +90 degrees")
    return ra % 360.0, dec


def _looks_like_name(q: str) -> bool:
    """True when the query has letters that no coordinate format uses."""
    s = _normalize_coords(q)
    if re.match(r"[a-ik-z]", s):  # starts with a letter (J is a coordinate prefix)
        return True
    letters = re.sub(r"[^a-z]", "", s)
    return bool(re.sub(r"[hdmsj]", "", letters))


def resolve(q: str | None, now: datetime | None = None) -> dict[str, Any]:
    q = (q or "").strip()
    if not q:
        raise ApiError(400, "bad_request", "q is empty: give coordinates or a name")
    if len(q) > 200:
        raise ApiError(400, "bad_request", "q is too long")
    slug = alias_index().get(norm_name(q))
    if slug:
        t = builtins()[slug]
        ra, dec = position_now(t, now)
        return {"q": q, "name": t["name"], "ra": round(ra, 7), "dec": round(dec, 7),
                "slug": slug, "source": "builtin"}
    try:
        ra, dec = parse_coords(q)
    except CoordError as e:
        if _looks_like_name(q):
            raise ApiError(404, "not_found",
                           f"unknown name {q!r}: give coordinates, or one of the built-in targets "
                           f"({', '.join(t['name'] for t in builtins().values())})") from None
        raise ApiError(400, "bad_request", f"can't read coordinates {q!r}: {e}") from None
    return {"q": q, "name": None, "ra": round(ra, 7), "dec": round(dec, 7), "slug": None,
            "source": "coords"}
