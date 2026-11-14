"""SPHEREx spectra as an image the model can look at, with molecule bands marked.

`spectrum` reads the same object as `measure` (/data/<slug>/object.json for a featured target, else
/api/object) and draws its `spectrum[]` points (contracts/api.md, GET /api/object) with Pillow and
numpy only. `render_png` is pure, so it is tested without the network."""
from __future__ import annotations

import io
import math
from functools import lru_cache
from typing import Annotated, Any, Iterable, Literal

import numpy as np
from mcp.server.fastmcp import Image
from PIL import Image as PILImage, ImageDraw, ImageFont
from pydantic import Field

from . import candidates as cands
from .client import SkyShiftError
from .server import _json, _position, app, client

# Detectors (CLAUDE.md, Domain facts) and their colours: the dataviz categorical order, slots 1-6.
DET_UM = {1: (0.75, 1.1), 2: (1.1, 1.6), 3: (1.6, 2.4), 4: (2.4, 3.8), 5: (3.8, 4.4), 6: (4.4, 5.0)}
DET_RGB = {1: (42, 120, 214), 2: (235, 104, 52), 3: (27, 175, 122),
           4: (237, 161, 0), 5: (232, 123, 164), 6: (0, 131, 0)}

# The band windows SkyShift's classifier measures: the `band` µm of each entry of INDEX_WINDOWS in
# pipeline/src/skyshift_pipeline/config.py (labels from classify.band_labels; keep them in sync).
# (index name, short text, lo µm, hi µm, kind, candidates.json label)
BANDS: tuple[tuple[str, str, float, float, str, str], ...] = (
    ("h2o_14", "H2O", 1.34, 1.45, "gas", "h2o_vapour"),
    ("ch4_16", "CH4", 1.63, 1.72, "gas", "ch4"),
    ("h2o_19", "H2O", 1.84, 1.95, "gas", "h2o_vapour"),
    ("co_23", "CO", 2.29, 2.40, "gas", "co"),
    ("h2o_ice_305", "H2O ice", 2.95, 3.15, "ice", "h2o_ice"),
    ("ch4_33", "CH4", 3.20, 3.45, "gas", "ch4"),
    ("pah_33", "PAH", 3.24, 3.34, "emission", "pah"),
    ("co2_ice_427", "CO2 ice", 4.23, 4.31, "ice", "co2_ice"),
    ("co_ice_467", "CO ice", 4.64, 4.70, "ice", "co_ice"),
)
BAND_TINT = {"gas": (226, 225, 219), "ice": (210, 228, 248), "emission": (252, 222, 200)}
BAND_LINE = {"gas": (150, 148, 140), "ice": (95, 140, 200), "emission": (215, 130, 75)}

SURFACE, INK, INK2, MUTED = (252, 252, 251), (11, 11, 11), (82, 81, 78), (128, 127, 122)
GRID, AXIS = (234, 233, 229), (110, 109, 105)

W, H, SS = 1100, 660, 2                              # final size (px) and supersampling factor
LEFT, RIGHT, TOP, BOTTOM = 92, W - 26, 140, H - 112  # plot area
UM_LO, UM_HI = 0.70, 5.05

# ---------------------------------------------------------------- fonts and text

# Linux, macOS, Windows; then Pillow's own font. Glyphs a font lacks (µ, −, ₂ …) fall back to ASCII.
_FONT_NAMES = ("DejaVuSans.ttf", "LiberationSans-Regular.ttf", "Helvetica.ttc", "Arial.ttf",
               "arial.ttf", "Arial Unicode.ttf")
_ASCII = {"µ": "u", "−": "-", "–": "-", "—": "-", "₂": "2", "₄": "4", "″": '"', "×": "x",
          "·": "-", "σ": "sigma", "°": " deg", "≥": ">=", "≤": "<="}


@lru_cache(maxsize=None)
def _font(size: int) -> Any:
    for name in _FONT_NAMES:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)   # Pillow >= 10.1 (FreeType)
    except TypeError:
        return ImageFont.load_default()


@lru_cache(maxsize=None)
def _missing(size: int) -> frozenset[str]:
    """The special characters this font has no glyph for (they render as the missing-glyph box)."""
    f = _font(size)
    box = bytes(f.getmask("\U0010fffd"))
    return frozenset(ch for ch in _ASCII if bytes(f.getmask(ch)) == box)


def _fit(text: str, size: int) -> str:
    miss = _missing(size)
    return "".join(_ASCII[ch] if ch in miss else ch for ch in text) if miss else text


class _Canvas:
    """Draw in final-image pixels on an SS× canvas; png() downsamples (smooth lines and dots)."""

    def __init__(self) -> None:
        self.img = PILImage.new("RGB", (W * SS, H * SS), SURFACE)
        self.d = ImageDraw.Draw(self.img)

    @staticmethod
    def _s(*v: float) -> list[float]:
        return [x * SS for x in v]

    def line(self, x0, y0, x1, y1, fill, width=1.0) -> None:
        self.d.line(self._s(x0, y0, x1, y1), fill=fill, width=max(1, round(width * SS)))

    def rect(self, x0, y0, x1, y1, fill=None, outline=None, width=1.0) -> None:
        self.d.rectangle(self._s(x0, y0, x1, y1), fill=fill, outline=outline,
                         width=max(1, round(width * SS)))

    def dot(self, x, y, r, fill, outline, width=1.0) -> None:
        self.d.ellipse(self._s(x - r, y - r, x + r, y + r), fill=fill, outline=outline,
                       width=max(1, round(width * SS)))

    def tri(self, x, y, r, up: bool, fill) -> None:
        k = -1 if up else 1
        self.d.polygon(self._s(x - r, y - k * r * 0.8, x + r, y - k * r * 0.8, x, y + k * r * 0.9),
                       fill=fill, outline=SURFACE)

    def width(self, text: str, size: int) -> float:
        f = _font(size * SS)
        l, _, r, _ = self.d.textbbox((0, 0), _fit(text, size * SS), font=f)
        return (r - l) / SS

    def text(self, x, y, text, size, fill=INK, anchor="lt") -> None:
        """anchor: (l|m|r)(t|m|b); vertical from the font's cap-and-descender box, so rows align."""
        f = _font(size * SS)
        s = _fit(text, size * SS)
        l, _, r, _ = self.d.textbbox((0, 0), s, font=f)
        _, t, _, b = self.d.textbbox((0, 0), "Hg", font=f)
        dx = {"l": -l, "m": -(l + r) / 2, "r": -r}[anchor[0]]
        dy = {"t": -t, "m": -(t + b) / 2, "b": -b}[anchor[1]]
        self.d.text((x * SS + dx, y * SS + dy), s, font=f, fill=fill)

    def vtext(self, x, y, text, size, fill=INK2) -> None:
        """Text rotated 90° (reads bottom to top), centred on (x, y)."""
        f = _font(size * SS)
        s = _fit(text, size * SS)
        l, t, r, b = self.d.textbbox((0, 0), s, font=f)
        tile = PILImage.new("L", (r - l + 4, b - t + 4), 0)
        ImageDraw.Draw(tile).text((2 - l, 2 - t), s, font=f, fill=255)
        tile = tile.rotate(90, expand=True)
        ink = PILImage.new("RGB", tile.size, fill)
        self.img.paste(ink, (round(x * SS - tile.width / 2), round(y * SS - tile.height / 2)), tile)

    def png(self) -> bytes:
        out = self.img.resize((W, H), PILImage.LANCZOS)
        buf = io.BytesIO()
        out.save(buf, format="PNG", optimize=True)
        return buf.getvalue()


# ---------------------------------------------------------------- scales

def _unit(vmax: float) -> tuple[str, float]:
    """Axis unit for fluxes up to vmax µJy: µJy, mJy or Jy (tick labels stay short)."""
    if vmax >= 1e7:
        return "Jy", 1e6
    if vmax >= 1e4:
        return "mJy", 1e3
    return "µJy", 1.0


def _nice_ticks(lo: float, hi: float, n: int = 6) -> list[float]:
    span = hi - lo
    if not span > 0:
        return [lo]
    mag = 10 ** math.floor(math.log10(span / n))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if span / (m * mag) <= n)
    first = math.ceil(lo / step - 1e-9)
    return [round(k * step, 12) for k in range(first, math.floor(hi / step + 1e-9) + 1)]


def _num(v: float) -> str:
    if v == 0:
        return "0"
    s = f"{v:.6g}" if abs(v) >= 1e-3 else f"{v:.1e}"
    if "e" not in s and "." in s:
        s = s.rstrip("0").rstrip(".")
    return s.replace("-", "−")


def _limits(f: np.ndarray, log: bool) -> tuple[float, float]:
    """Robust flux limits (µJy): the 5–95% range (log: 2–98% in dex, so a brown dwarf's steep fall
    at 1 µm stays on) widened by its span on each side, never past the data, so only real outliers
    fall off the scale (they are drawn as triangles at the edge)."""
    v = np.log10(f[f > 0]) if log else f
    if len(v) == 0:
        return (1.0, 10.0) if log else (-1.0, 1.0)
    plo, phi = (np.percentile(v, [2, 98] if log else [5, 95]) if len(v) >= 20 else (v.min(), v.max()))
    span = max(phi - plo, 1e-9)
    lo, hi = float(max(v.min(), plo - span)), float(min(v.max(), phi + span))
    if log:
        span = max(hi - lo, 0.3)
        return 10 ** (lo - 0.08 * span), 10 ** (hi + 0.08 * span)
    if 0 < lo < 0.3 * hi:
        lo = 0.0                      # a positive spectrum near zero: show the zero line
    if hi <= 0:
        hi = 0.0
    pad = 0.06 * (hi - lo) if hi > lo else (abs(hi) * 0.1 or 1.0)
    return (lo if lo == 0 and f.min() >= 0 else lo - pad), hi + pad


def choose_scale(flux: Iterable[float]) -> str:
    """log when nearly every point is positive and the fluxes span more than ~1.5 decades, else linear
    (faint objects scatter around zero; a log axis would hide that)."""
    f = np.asarray(list(flux), dtype=float)
    pos = f[f > 0]
    if len(f) < 3 or len(pos) < 0.95 * len(f):
        return "linear"
    lo, hi = np.percentile(pos, [2, 98])
    return "log" if hi / lo > 30 else "linear"


# ---------------------------------------------------------------- the picture

def _band_labels(c: _Canvas, x_of) -> None:
    """Band labels in rows above the plot (row 0 nearest), never overlapping: absorption bands first,
    so PAH (inside CH₄ 3.3 µm) goes one row up. A leader line joins a label to its band unless it
    would cross a label below."""
    rows: list[list[tuple[float, float]]] = []
    kind_order = {"gas": 0, "ice": 1, "emission": 2}
    for b in sorted(BANDS, key=lambda b: (kind_order[b[4]], b[2])):
        cx = x_of((b[2] + b[3]) / 2)
        half = c.width(b[1], 12) / 2 + 4
        row = next((i for i, used in enumerate(rows)
                    if all(cx + half <= a or cx - half >= z for a, z in used)), len(rows))
        if row == len(rows):
            rows.append([])
        rows[row].append((cx - half, cx + half))
        ty = TOP - 10 - row * 17
        if not any(a <= cx <= z for lower in rows[:row] for a, z in lower):
            c.line(cx, ty + 2, cx, TOP, BAND_LINE[b[4]], 1)
        c.text(cx, ty, b[1], 12, INK2, "mb")


def render_png(points: list[dict[str, Any]], title: str, subtitle: str = "",
               scale: str = "auto", bands: bool = True) -> tuple[bytes, dict[str, Any]]:
    """One PNG of flux against wavelength. Returns (png, info: scale, unit, off-scale counts)."""
    pts = [p for p in points if p.get("um") is not None and p.get("flux_ujy") is not None
           and math.isfinite(p["um"]) and math.isfinite(p["flux_ujy"])]
    flux = np.array([p["flux_ujy"] for p in pts], dtype=float)
    used = choose_scale(flux) if scale == "auto" else scale
    log = used == "log"
    lo, hi = _limits(flux, log)
    unit, div = _unit(max(abs(lo), abs(hi)))

    def x_of(um: float) -> float:
        return LEFT + (um - UM_LO) / (UM_HI - UM_LO) * (RIGHT - LEFT)

    def y_of(v: float) -> float:
        if log:
            t = (math.log10(max(v, lo * 1e-3)) - math.log10(lo)) / (math.log10(hi) - math.log10(lo))
        else:
            t = (v - lo) / (hi - lo)
        return BOTTOM - t * (BOTTOM - TOP)

    def clamp(y: float) -> float:
        return min(BOTTOM, max(TOP, y))

    c = _Canvas()
    c.text(LEFT - 70, 14, title, 21, INK)
    if subtitle:
        c.text(LEFT - 70, 44, subtitle, 13, INK2)

    # legend row
    x, ly = LEFT - 70, 74
    c.dot(x + 5, ly, 4.5, INK2, SURFACE)
    c.text(x + 14, ly, "measured", 12, INK2, "lm")
    x += 14 + c.width("measured", 12) + 18
    c.dot(x + 5, ly, 4, SURFACE, INK2, 1.6)
    c.text(x + 14, ly, "saturated core (flux unreliable)", 12, INK2, "lm")
    x += 14 + c.width("saturated core (flux unreliable)", 12) + 18
    c.line(x + 5, ly - 7, x + 5, ly + 7, INK2, 1)
    c.text(x + 12, ly, "1σ error", 12, INK2, "lm")
    x += 12 + c.width("1σ error", 12) + 18
    c.tri(x + 5, ly, 5, True, INK2)
    c.text(x + 14, ly, "off scale", 12, INK2, "lm")
    x += 14 + c.width("off scale", 12) + 24
    if bands:
        for kind, word in (("gas", "gas absorption"), ("ice", "ice absorption"), ("emission", "PAH emission")):
            c.rect(x, ly - 6, x + 16, ly + 6, BAND_TINT[kind], BAND_LINE[kind], 1)
            c.text(x + 22, ly, word, 12, INK2, "lm")
            x += 22 + c.width(word, 12) + 16

    # bands: shading in the plot, labels in the strip above it
    if bands:
        for _, _, b_lo, b_hi, kind, _ in sorted(BANDS, key=lambda b: -(b[3] - b[2])):
            c.rect(x_of(b_lo), TOP, x_of(b_hi), BOTTOM, BAND_TINT[kind])
        _band_labels(c, x_of)

    # y grid and ticks
    if log:
        k0, k1 = math.floor(math.log10(lo)), math.ceil(math.log10(hi))
        mult = (1, 2, 5) if math.log10(hi / lo) < 2.2 else (1,)
        major = [m * 10.0 ** k for k in range(k0, k1 + 1) for m in mult if lo <= m * 10.0 ** k <= hi]
        minor = [m * 10.0 ** k for k in range(k0, k1 + 1) for m in range(2, 10)
                 if m not in mult and lo <= m * 10.0 ** k <= hi]
        for v in minor:
            c.line(LEFT - 3, y_of(v), LEFT, y_of(v), AXIS, 1)
    else:
        major = [t * div for t in _nice_ticks(lo / div, hi / div)]
    for v in major:
        y = y_of(v)
        c.line(LEFT, y, RIGHT, y, GRID, 1)
        c.line(LEFT - 5, y, LEFT, y, AXIS, 1)
        c.text(LEFT - 8, y, _num(v / div), 13, INK2, "rm")
    if not log and lo < 0 < hi:
        c.line(LEFT, y_of(0), RIGHT, y_of(0), MUTED, 1.2)
    c.vtext(20, (TOP + BOTTOM) / 2, f"flux density ({unit}){', log scale' if log else ''}", 14)

    # error bars, then dots on top (unsaturated last so they stay visible)
    def light(rgb: tuple[int, int, int], a: float = 0.55) -> tuple[int, ...]:
        return tuple(round(a * v + (1 - a) * s) for v, s in zip(rgb, SURFACE))

    above = below = 0
    order = sorted(pts, key=lambda p: (not p.get("saturated"), p["um"]))
    for p in order:
        e = p.get("err_ujy")
        if e is None or not math.isfinite(e) or e <= 0 or not (lo <= p["flux_ujy"] <= hi):
            continue
        xp = x_of(p["um"])
        y0, y1 = clamp(y_of(p["flux_ujy"] - e)), clamp(y_of(p["flux_ujy"] + e))
        c.line(xp, y0, xp, y1, light(DET_RGB.get(p.get("det"), INK2)), 1)
    for p in order:
        xp, v = x_of(p["um"]), p["flux_ujy"]
        rgb = DET_RGB.get(p.get("det"), INK2)
        if v > hi:
            above += 1
            c.tri(xp, TOP + 6, 5, True, rgb)
        elif v < lo or (log and v <= 0):
            below += 1
            c.tri(xp, BOTTOM - 6, 5, False, rgb)
        elif p.get("saturated"):
            c.dot(xp, y_of(v), 3.6, SURFACE, rgb, 1.6)
        else:
            c.dot(xp, y_of(v), 4.2, rgb, SURFACE, 0.8)

    # axes, wavelength ticks, the detector bar (a direct-label legend for the colours)
    c.line(LEFT, TOP, LEFT, BOTTOM, AXIS, 1)
    c.line(LEFT, BOTTOM, RIGHT, BOTTOM, AXIS, 1)
    for k in range(math.ceil(UM_LO * 10), math.floor(UM_HI * 10) + 1):
        um = k / 10
        major_tick = k % 5 == 0
        c.line(x_of(um), BOTTOM, x_of(um), BOTTOM + (6 if major_tick else 3), AXIS, 1)
        if major_tick:
            c.text(x_of(um), BOTTOM + 9, _num(um), 13, INK2, "mt")
    by = BOTTOM + 30
    for det, (a, b) in DET_UM.items():
        c.rect(x_of(a) + 1, by, x_of(b) - 1, by + 6, DET_RGB[det])
        c.text((x_of(a) + x_of(b)) / 2, by + 10, f"D{det}", 12, INK2, "mt")
    c.text((LEFT + RIGHT) / 2, by + 30, "wavelength (µm)", 14, INK2, "mt")
    c.text(LEFT - 70, H - 10,
           "Shaded: the windows SkyShift's classifier measures. D1+D4, D2+D5, D3+D6 are exposed at "
           "the same moment: a step between them is colour, not change.", 11, MUTED, "lb")
    return c.png(), {"y_scale": used, "plot_flux_unit": unit,
                     "plot_flux_range_ujy": [round(float(lo), 4), round(float(hi), 4)],
                     "off_scale": {"above": above, "below": below}}


# ---------------------------------------------------------------- summary

def _mjd_range(ps: list[dict[str, Any]]) -> list[float] | None:
    m = [p["mjd"] for p in ps if p.get("mjd") is not None]
    return [round(min(m), 3), round(max(m), 3)] if m else None


def _snr(ps: list[dict[str, Any]]) -> float | None:
    s = [p["flux_ujy"] / p["err_ujy"] for p in ps if (p.get("err_ujy") or 0) > 0]
    return round(float(np.median(s)), 2) if s else None


def summarise_points(ps: list[dict[str, Any]]) -> dict[str, Any]:
    dets = {}
    for d in sorted({p.get("det") for p in ps if p.get("det") is not None}):
        q = [p for p in ps if p.get("det") == d]
        dets[f"D{d}"] = {"n": len(q), "um": [min(p["um"] for p in q), max(p["um"] for p in q)],
                         "median_flux_ujy": round(float(np.median([p["flux_ujy"] for p in q])), 2),
                         "median_snr": _snr(q), "saturated": sum(bool(p.get("saturated")) for p in q)}
    visits = {}
    for v in sorted({p.get("visit") for p in ps if p.get("visit") is not None}):
        q = [p for p in ps if p.get("visit") == v]
        visits[str(v)] = {"n": len(q), "mjd": _mjd_range(q),
                          "saturated": sum(bool(p.get("saturated")) for p in q)}
    return {"n_points": len(ps), "um_range": [min(p["um"] for p in ps), max(p["um"] for p in ps)],
            "median_snr": _snr(ps), "n_saturated": sum(bool(p.get("saturated")) for p in ps),
            "per_detector": dets, "per_visit": visits}


def _subtitle(ps: list[dict[str, Any]], visit: int | None, all_visits: list[int], source: str) -> str:
    mjd = _mjd_range(ps)
    when = f"MJD {mjd[0]:.0f}–{mjd[1]:.0f}" if mjd else ""
    if visit is not None:
        head = f"visit {visit} of {len(all_visits)} only: {len(ps)} points, {when}"
    else:
        head = f"{len(ps)} points from {len(all_visits)} visit{'s' if len(all_visits) != 1 else ''}, {when}"
    sat = sum(bool(p.get("saturated")) for p in ps)
    tail = (" · all saturated" if sat == len(ps) else f" · {sat} saturated" if sat else "")
    return f"{head}{tail} · {source}"


# ---------------------------------------------------------------- the tool

@app.tool()
async def spectrum(
    target: Annotated[str | None, Field(description="Name or coordinates in any format, e.g. 'WISE 0855-0714' or '17h57m48s +04d41m36s'")] = None,
    ra: float | None = None,
    dec: float | None = None,
    candidate_id: Annotated[str | None, Field(description="A candidates.json id: the spectrum at that candidate's position")] = None,
    visit: Annotated[int | None, Field(ge=1, description="Only this visit's points (numbered 1, 2, … from the earliest visit at the position). Variable stars such as R Draconis and RW Aurigae change between visits")] = None,
    scale: Annotated[Literal["auto", "linear", "log"], Field(description="Flux axis. auto = log when every point is positive and fluxes span more than ~1.5 decades, else linear")] = "auto",
    bands: Annotated[bool, Field(description="Shade the molecule and ice bands SkyShift's classifier measures")] = True,
) -> list:
    """SkyShift's SPHEREx spectrum (0.75–5.0 µm, every exposure's point) of an object as ONE image,
    plus a JSON summary (points per detector and per visit, median S/N, source).

    Reading the plot: flux density against wavelength. Dots are coloured by detector (the coloured
    bar under the x axis, D1–D6); bars are 1σ errors; hollow dots have a saturated core (OVERFLOW or
    NONLINEAR: the flux is unreliable, usually too low); triangles at the top or bottom edge are
    points off the scale. Shaded bands are the windows SkyShift's classifier measures: H₂O 1.4 and
    1.9 µm (water vapour: M/L dwarfs, cool giants such as R Draconis), CH₄ 1.65 and 3.3 µm (methane:
    only T/Y brown dwarfs), CO 2.3 µm (cool giants), H₂O ice 3.05, CO₂ ice 4.27, CO ice 4.67 µm (blue:
    ices in a dark cloud in front of a star), PAH 3.3 µm (orange: emission from star-forming galaxies).
    An absorption band shows as points below the continuum on both sides; PAH as a bump.
    A normal star falls smoothly from 1 to 5 µm. A cold brown dwarf (WISE 0855−0714, Epsilon Indi B)
    is faint short of ~2.5 µm, shows a deep CH₄ dip at 3.3 µm and rises steeply to 4–5 µm; the
    coldest are near zero in D1–D3 (points scatter around 0 within their errors).
    D1+D4, D2+D5, D3+D6 are exposed at the same moment, so a step between those pairs is colour, not
    change; points at one wavelength from different visits can differ for a variable star (use
    `visit`). Judge every dip against the error bars and the median S/N; say what is uncertain.
    An empty spectrum returns text only, with `spectrum_reason` (e.g. saturated, no_data)."""
    c = client()
    note: dict[str, Any] = {}
    if candidate_id:
        data = await c.candidates()
        cand = cands.find(data["candidates"], candidate_id)
        if cand is None:
            raise SkyShiftError(f"no candidate {candidate_id!r} in candidates.json (generated {data.get('generated')})")
        note["candidate"] = cands.summarise(cand)
        if cand.get("kind") == "mover":
            note["note"] = ("a mover sits at this position only around its mjd: the spectrum is of "
                            "whatever SkyShift measures here over all visits, not the mover's own")
        pos = {"ra": cand["ra"], "dec": cand["dec"], "name": None, "slug": None}
    else:
        pos = await _position(target, ra, dec)
    if pos.get("slug"):
        obj = await c.get_json(f"/data/{pos['slug']}/object.json")
        source = f"static /data/{pos['slug']}/object.json"
    else:
        obj = await c.api("/api/object", ra=pos["ra"], dec=pos["dec"])
        source = "live /api/object"

    name = obj.get("name") or pos.get("name") or candidate_id or f"RA {pos['ra']:.5f}, Dec {pos['dec']:+.5f}"
    pts = sorted([p for p in (obj.get("spectrum") or []) if p.get("um") is not None
                  and p.get("flux_ujy") is not None], key=lambda p: p["um"])
    all_visits = sorted({p["visit"] for p in pts if p.get("visit") is not None})
    out: dict[str, Any] = {"name": name, "position": pos, "source": source, **note,
                           "known_ucd": obj.get("known_ucd"), "gaia": obj.get("gaia"),
                           "visits_with_points": all_visits}
    if not pts:
        out["spectrum_points"] = 0
        out["spectrum_reason"] = obj.get("spectrum_reason") or "no_data"
        out["why"] = {"saturated": "the star saturated so deeply that no exposure had a usable "
                                   "aperture and background annulus",
                      "no_data": "SPHEREx gave no usable point here (no exposure, or no good pixels "
                                 "in the aperture or background annulus)"}.get(out["spectrum_reason"])
        return [_json(out)]
    shown = [p for p in pts if visit is None or p.get("visit") == visit]
    if not shown:
        out["spectrum_points"] = 0
        out["spectrum_reason"] = f"no points in visit {visit}"
        out["why"] = f"this position has points in visits {all_visits}; pick one of those or leave visit out"
        return [_json(out)]
    if visit is not None:
        out["visit"] = visit
    out.update(summarise_points(shown))
    title = f"{name} · SPHEREx spectrum" + (f" · visit {visit}" if visit is not None else "")
    png, info = render_png(shown, title, _subtitle(shown, visit, all_visits, source), scale, bands)
    out.update(info)
    if bands:
        out["bands_um"] = {b[0]: [b[2], b[3]] for b in BANDS}
    return [_json(out), Image(data=png, format="png")]
