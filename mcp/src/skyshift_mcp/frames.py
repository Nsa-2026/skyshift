"""Turn /api/frames data into one PNG a model can look at (contracts/api.md, Frame data).

Frames are little-endian uint16, north up and east left; value = raw × scale + zero (MJy/sr);
raw 0 = no data. All panels share one asinh stretch so brightness compares between epochs; a last
panel shows last − first (white = brighter later, black = fainter). Optional overlays mark Gaia DR3
stars (moved to each epoch), known asteroids and comets, and SkyShift's measured detections, with a
legend strip under the panels; `animation` gives the same panels as a looping GIF.
Pure functions, no network.
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np
from PIL import Image, ImageDraw, ImageFont

PANEL_MIN = 260        # panels are upscaled (nearest) to at least this many pixels a side
LABEL_H = 30
GAP = 6
BG = (16, 18, 24)
NO_DATA = (60, 30, 60)  # purple hatch colour for raw = 0
TICK = (255, 190, 60)
TEXT = (230, 230, 230)

# Overlays. Colours chosen to read on black and on the grey stretch.
GAIA_EPOCH_MJD = 57388.5            # Gaia DR3 positions are at J2016.0
GAIA_COLOR = (0, 200, 255)          # thin cyan circles, G <= GAIA_BRIGHT_G
GAIA_FAINT_COLOR = (0, 120, 150)    # dimmer for fainter stars, so crowded fields stay readable
GAIA_BRIGHT_G = 16.0
KNOWN_COLOR = (80, 255, 100)        # green diamonds with a name
CLASS_COLOR = {                     # measured.detections[].class
    "suspect": (255, 45, 45),       # red, thick square: a lead
    "mover": (255, 225, 0),         # yellow, thick square: moved, vetted out
    "known": KNOWN_COLOR,           # green square: matched a known object
    "artifact": (255, 80, 255),     # magenta x
    "star": (80, 140, 255),         # blue +
    "other": (185, 185, 185),       # grey dot
}
CLASS_ORDER = ("other", "star", "artifact", "known", "mover", "suspect")  # drawn bottom to top
GAIA_MAX_MARKS = 150                # at most this many circles a frame, brightest first
LEGEND_ROW = 18
ARCSEC_PER_RAD = 180.0 / math.pi * 3600.0


def decode(raw: bytes, size: Sequence[int], scale: float, zero: float) -> np.ma.MaskedArray:
    nx, ny = int(size[0]), int(size[1])
    a = np.frombuffer(raw, dtype="<u2")
    if a.size != nx * ny:
        raise ValueError(f"frame has {a.size} values, expected {nx}×{ny}")
    a = a.reshape(ny, nx)
    return np.ma.masked_equal(a, 0).astype(np.float64) * scale + zero


def crop_window(shape: Sequence[int], pixel_arcsec: float,
                zoom_arcmin: float | None) -> tuple[int, int, int, int] | None:
    """(y0, y1, x0, x1), end exclusive, of a zoom_arcmin square centred on the frame centre
    ((n−1)/2: between two pixels when n is even, so the crop keeps the centre at its own centre).
    None for the whole frame: no zoom, or a zoom larger than the frame."""
    if not zoom_arcmin:
        return None
    ny, nx = int(shape[0]), int(shape[1])
    half = max(4, int(round(zoom_arcmin * 60.0 / pixel_arcsec / 2)))
    if 2 * half + 1 >= min(nx, ny):
        return None
    cy, cx = (ny - 1) / 2, (nx - 1) / 2
    return (math.ceil(cy - half), math.floor(cy + half) + 1,
            math.ceil(cx - half), math.floor(cx + half) + 1)


def crop(f: np.ma.MaskedArray, pixel_arcsec: float, zoom_arcmin: float | None) -> np.ma.MaskedArray:
    """A square of zoom_arcmin around the frame centre (pixel ((nx-1)/2, (ny-1)/2)); the whole frame
    if zoom is None or larger than the frame. The cropped frame's centre pixel is the same sky point."""
    w = crop_window(f.shape, pixel_arcsec, zoom_arcmin)
    if w is None:
        return f
    y0, y1, x0, x1 = w
    return f[y0:y1, x0:x1]


def crop_origin(shape: Sequence[int], pixel_arcsec: float, zoom_arcmin: float | None) -> tuple[int, int]:
    """(x0, y0): the full-frame pixel at the cropped frame's (0, 0)."""
    w = crop_window(shape, pixel_arcsec, zoom_arcmin)
    return (0, 0) if w is None else (w[2], w[0])


def pick_epochs(epochs: list[dict[str, Any]], det: int | None, max_frames: int) -> list[dict[str, Any]]:
    """Epochs of one detector, sorted by time; if there are more than max_frames, an even spread
    that keeps the first and last."""
    rows = [e for e in epochs if det is None or e.get("det") == det]
    rows.sort(key=lambda e: e["mjd_mid"])
    if len(rows) <= max_frames:
        return rows
    idx = np.unique(np.round(np.linspace(0, len(rows) - 1, max(2, max_frames))).astype(int))
    return [rows[i] for i in idx]


# ---------------------------------------------------------------- sky → pixel

def propagate(ra, dec, pmra, pmdec, mjd: float, epoch_mjd: float = GAIA_EPOCH_MJD):
    """Move positions (deg) from epoch_mjd to mjd with proper motion in mas/yr (pmra already
    × cos dec). Linear, no parallax (≤ 1″ even for Barnard's Star). Null motions count as 0."""
    ra = np.asarray(ra, dtype=float)
    dec = np.asarray(dec, dtype=float)
    pmra = np.nan_to_num(np.asarray(pmra, dtype=float))
    pmdec = np.nan_to_num(np.asarray(pmdec, dtype=float))
    dt = (mjd - epoch_mjd) / 365.25
    dec2 = dec + pmdec * dt / 3.6e6
    ra2 = ra + pmra * dt / 3.6e6 / np.cos(np.radians(dec))
    return ra2, dec2


def project(ra, dec, ra0: float, dec0: float, pixel_arcsec: float, size: Sequence[int]):
    """Gnomonic projection onto a frame's tangent plane at (ra0, dec0) → pixel (x, y): x the column
    from the east (left) edge, y the row from the north (top) edge, the centre at
    ((nx−1)/2, (ny−1)/2). East is left, so x falls as RA grows. Points 90° or more away: nan."""
    a = np.radians(np.asarray(ra, dtype=float))
    d = np.radians(np.asarray(dec, dtype=float))
    a0, d0 = math.radians(ra0), math.radians(dec0)
    cosc = np.sin(d0) * np.sin(d) + np.cos(d0) * np.cos(d) * np.cos(a - a0)
    with np.errstate(divide="ignore", invalid="ignore"):
        xi = np.cos(d) * np.sin(a - a0) / cosc
        eta = (np.cos(d0) * np.sin(d) - np.sin(d0) * np.cos(d) * np.cos(a - a0)) / cosc
    xi = np.where(cosc > 0, xi, np.nan)
    eta = np.where(cosc > 0, eta, np.nan)
    nx, ny = int(size[0]), int(size[1])
    x = (nx - 1) / 2 - xi * ARCSEC_PER_RAD / pixel_arcsec
    y = (ny - 1) / 2 - eta * ARCSEC_PER_RAD / pixel_arcsec
    return x, y


def gaia_radius_arcsec(g: float) -> float:
    """Circle radius: bigger for brighter stars, 4″ (G 19) to 14″ (G ≤ 6.5)."""
    return float(np.clip(4.0 + 0.8 * (19.0 - g), 4.0, 14.0))


def short_name(name: str | None, n: int = 14) -> str:
    s = (name or "?").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


@dataclass
class Marks:
    """Overlay marks of one frame, in that frame's (cropped) pixel coordinates."""
    pixel_arcsec: float
    gaia: list[tuple[float, float, float]] = field(default_factory=list)       # x, y, G (bright first)
    known: list[tuple[float, float, str]] = field(default_factory=list)        # x, y, label
    measured: list[tuple[float, float, str, str]] = field(default_factory=list)  # x, y, class, label
    gaia_fainter: int = 0      # in the frame but fainter than the G limit (or no G)
    gaia_capped: int = 0       # in the frame, bright enough, beyond GAIA_MAX_MARKS
    known_fainter: int = 0     # in the frame but fainter than the V limit

    def counts(self) -> dict[str, Any]:
        by = {c: 0 for c in CLASS_COLOR}
        for _, _, c, _ in self.measured:
            by[c] = by.get(c, 0) + 1
        return {"gaia": len(self.gaia), "gaia_fainter": self.gaia_fainter, "gaia_capped": self.gaia_capped,
                "known": len(self.known), "known_fainter": self.known_fainter, "measured": by}


def epoch_marks(data: dict[str, Any], epoch: dict[str, Any], shape: Sequence[int],
                origin: Sequence[int] = (0, 0), max_gaia_g: float | None = 19.0,
                max_known_v: float | None = 18.0, max_gaia: int = GAIA_MAX_MARKS) -> Marks:
    """Marks for one epoch of a frames response. `shape` is the (ny, nx) of the frame as drawn and
    `origin` its (x0, y0) in the full frame (crop_origin). The tangent plane is the epoch's own
    `center` when present (frames that follow a moving target), else the response's `center`.
    Gaia stars are moved from J2016.0 to the epoch's mjd_mid; known[] and measured.detections[]
    are matched by epoch_id. A null V counts as shown; None limits show everything."""
    c = epoch.get("center") or data["center"]
    p = float(data["pixel_arcsec"])
    size = data["size"]
    ny, nx = int(shape[0]), int(shape[1])
    x0, y0 = origin
    m = Marks(pixel_arcsec=p)

    def place(ra, dec):
        x, y = project(ra, dec, c["ra"], c["dec"], p, size)
        x, y = np.atleast_1d(x) - x0, np.atleast_1d(y) - y0
        pad = 1.0
        inside = np.isfinite(x) & np.isfinite(y) & (x >= -pad) & (x <= nx - 1 + pad) \
            & (y >= -pad) & (y <= ny - 1 + pad)
        return x, y, inside

    stars = [s for s in (data.get("gaia") or []) if s.get("ra") is not None and s.get("dec") is not None]
    if stars:
        ra, dec = propagate([s["ra"] for s in stars], [s["dec"] for s in stars],
                            [s.get("pmra") if s.get("pmra") is not None else 0.0 for s in stars],
                            [s.get("pmdec") if s.get("pmdec") is not None else 0.0 for s in stars],
                            float(epoch["mjd_mid"]))
        x, y, inside = place(ra, dec)
        rows = []
        for i in np.flatnonzero(inside):
            g = stars[i].get("g")
            if g is None or (max_gaia_g is not None and g > max_gaia_g):
                m.gaia_fainter += 1
                continue
            rows.append((float(g), float(x[i]), float(y[i])))
        rows.sort()
        m.gaia_capped = max(0, len(rows) - max_gaia)
        m.gaia = [(xx, yy, g) for g, xx, yy in rows[:max_gaia]]

    known = [k for k in (data.get("known") or []) if k.get("epoch_id") == epoch["id"]]
    if known:
        x, y, inside = place([k["ra"] for k in known], [k["dec"] for k in known])
        for i in np.flatnonzero(inside):
            v = known[i].get("v")
            if v is not None and max_known_v is not None and v > max_known_v:
                m.known_fainter += 1
                continue
            m.known.append((float(x[i]), float(y[i]), short_name(known[i].get("name"))))

    dets = [d for d in ((data.get("measured") or {}).get("detections") or [])
            if d.get("epoch_id") == epoch["id"] and d.get("class") in CLASS_COLOR]
    if dets:
        x, y, inside = place([d["ra"] for d in dets], [d["dec"] for d in dets])
        m.measured = [(float(x[i]), float(y[i]), dets[i]["class"], _det_label(dets[i]))
                      for i in np.flatnonzero(inside)]
    return m


def _det_label(d: dict[str, Any]) -> str:
    """Text beside a measured mark: suspects (with their grade) and movers only."""
    if d["class"] == "suspect":
        return "suspect" + (f" {d['grade']}" if d.get("grade") else "")
    return "mover" if d["class"] == "mover" else ""


# ---------------------------------------------------------------- drawing

def _font(size: int = 11):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1: fixed bitmap font
        return ImageFont.load_default()


def _limits(frames: list[np.ma.MaskedArray]) -> tuple[float, float]:
    vals = np.concatenate([f.compressed() for f in frames]) if frames else np.array([])
    if vals.size == 0:
        return 0.0, 1.0
    lo, hi = np.percentile(vals, [5.0, 99.7])
    return float(lo), float(hi if hi > lo else lo + 1e-6)


def _stretch(f: np.ma.MaskedArray, lo: float, hi: float) -> np.ndarray:
    x = np.clip((f.filled(lo) - lo) / (hi - lo), 0.0, 1.0)
    y = np.arcsinh(10.0 * x) / np.arcsinh(10.0)
    rgb = np.repeat((y * 255).astype(np.uint8)[..., None], 3, axis=2)
    rgb[np.ma.getmaskarray(f)] = NO_DATA
    return rgb


def _diff_rgb(a: np.ma.MaskedArray, b: np.ma.MaskedArray) -> np.ndarray:
    d = b - a
    vals = np.abs(d.compressed())
    s = float(np.percentile(vals, 99.5)) if vals.size else 1.0
    s = s if s > 0 else 1.0
    y = np.clip(0.5 + 0.5 * d.filled(0.0) / s, 0.0, 1.0)
    rgb = np.repeat((y * 255).astype(np.uint8)[..., None], 3, axis=2)
    rgb[np.ma.getmaskarray(d)] = NO_DATA
    return rgb


def _glyph(d: ImageDraw.ImageDraw, kind: str, x: float, y: float, r: float, color, width: int = 1) -> None:
    """One mark centred on (x, y) with half-size r (display pixels)."""
    if kind == "circle":
        d.ellipse((x - r, y - r, x + r, y + r), outline=color, width=width)
    elif kind == "diamond":
        d.polygon([(x, y - r), (x + r, y), (x, y + r), (x - r, y)], outline=color, width=width)
    elif kind == "square":
        d.rectangle((x - r, y - r, x + r, y + r), outline=color, width=width)
    elif kind == "plus":
        d.line((x - r, y, x + r, y), fill=color, width=width)
        d.line((x, y - r, x, y + r), fill=color, width=width)
    elif kind == "x":
        d.line((x - r, y - r, x + r, y + r), fill=color, width=width)
        d.line((x - r, y + r, x + r, y - r), fill=color, width=width)
    elif kind == "dot":
        d.ellipse((x - r, y - r, x + r, y + r), fill=color)
    elif kind == "tick":
        d.line((x - r, y, x - r / 3, y), fill=color, width=2)
        d.line((x + r / 3, y, x + r, y), fill=color, width=2)
    elif kind == "swatch":
        d.rectangle((x - r, y - r, x + r, y + r), fill=color, outline=(120, 120, 120))


# measured class → (glyph, half-size ″, minimum half-size px, line width)
_CLASS_GLYPH = {
    "other": ("dot", 1.0, 1.0, 1),
    "star": ("plus", 2.5, 2.0, 1),
    "artifact": ("x", 4.0, 3.0, 1),
    "known": ("square", 6.0, 5.0, 1),
    "mover": ("square", 8.0, 7.0, 2),
    "suspect": ("square", 9.0, 8.0, 2),
}


def _draw_marks(img: Image.Image, marks: Marks, factor: int) -> None:
    d = ImageDraw.Draw(img)
    scale = factor / marks.pixel_arcsec          # display pixels per arcsec
    w, h = img.size

    def at(x: float, y: float) -> tuple[float, float]:
        return (x + 0.5) * factor, (y + 0.5) * factor

    def size(arcsec: float, lo: float) -> float:
        return float(np.clip(arcsec * scale, lo, 40.0))

    def label(X: float, Y: float, r: float, text: str, color) -> None:
        tw = d.textlength(text, font=font)
        tx = X + r + 3 if X + r + 3 + tw <= w - 2 else X - r - 3 - tw
        ty = min(max(Y - 6, 1), h - 13)
        d.text((tx, ty), text, fill=color, font=font, stroke_width=1, stroke_fill=(0, 0, 0))

    font = _font(11)
    by_class: dict[str, list[tuple[float, float, str]]] = {}
    for x, y, c, text in marks.measured:
        by_class.setdefault(c, []).append((x, y, text))
    for c in ("other", "star"):
        kind, arc, lo, width = _CLASS_GLYPH[c]
        for x, y, _ in by_class.get(c, []):
            _glyph(d, kind, *at(x, y), size(arc, lo), CLASS_COLOR[c], width)
    for x, y, g in reversed(marks.gaia):  # faint first, bright on top
        color = GAIA_COLOR if g <= GAIA_BRIGHT_G else GAIA_FAINT_COLOR
        _glyph(d, "circle", *at(x, y), size(gaia_radius_arcsec(g), 4.0), color, 1)
    for x, y, name in marks.known:
        X, Y = at(x, y)
        r = size(9.0, 6.0)
        _glyph(d, "diamond", X, Y, r, KNOWN_COLOR, 2)
        label(X, Y, r, name, KNOWN_COLOR)
    for c in ("artifact", "known", "mover", "suspect"):
        kind, arc, lo, width = _CLASS_GLYPH[c]
        for x, y, text in by_class.get(c, []):
            X, Y = at(x, y)
            r = size(arc, lo)
            _glyph(d, kind, X, Y, r, CLASS_COLOR[c], width)
            if text:
                label(X, Y, r, text, CLASS_COLOR[c])


def _panel(rgb: np.ndarray, label: str, factor: int, marks: Marks | None = None) -> Image.Image:
    img = Image.fromarray(rgb, "RGB").resize((rgb.shape[1] * factor, rgb.shape[0] * factor), Image.NEAREST)
    w, h = img.size
    if marks is not None:
        _draw_marks(img, marks, factor)
    d = ImageDraw.Draw(img)
    cx, cy, g, ln = w // 2, h // 2, max(8, w // 20), max(8, w // 14)
    for x0, y0, x1, y1 in ((cx - g - ln, cy, cx - g, cy), (cx + g, cy, cx + g + ln, cy),
                           (cx, cy - g - ln, cx, cy - g), (cx, cy + g, cx, cy + g + ln)):
        d.line((x0, y0, x1, y1), fill=TICK, width=2)
    out = Image.new("RGB", (w, h + LABEL_H), BG)
    out.paste(img, (0, LABEL_H))
    ImageDraw.Draw(out).text((4, 8), label, fill=TEXT, font=_font(11))
    return out


def label_for(e: dict[str, Any]) -> str:
    from datetime import datetime, timedelta
    t = datetime(1858, 11, 17) + timedelta(days=float(e["mjd_mid"]))
    when = t.strftime("%Y-%m-%d") if e.get("kind") == "visit" else t.strftime("%Y-%m-%d %H:%M")
    return f"{e['id']}  {when}"


# ---------------------------------------------------------------- legend

LegendItem = tuple[str, tuple[int, int, int] | None, str]  # glyph ("" = text only), colour, text


def legend(*, gaia: bool = True, known: bool = True, max_gaia_g: float | None = 19.0,
           max_known_v: float | None = 18.0, gaia_capped: bool = False, measured: bool = False,
           measured_reason: str | None = None, with_diff: bool = True) -> list[LegendItem]:
    """What each mark means, for the strip under the panels."""
    items: list[LegendItem] = [("tick", TICK, "field centre")]
    if gaia:
        g = f" G<={max_gaia_g:g}" if max_gaia_g is not None else ""
        items.append(("circle", GAIA_COLOR, f"Gaia DR3 star{g} at the frame's date (dim: G>{GAIA_BRIGHT_G:g})"))
        if gaia_capped:
            items.append(("", None, f"(brightest {GAIA_MAX_MARKS} a frame; zoom in for all)"))
    if known:
        v = f", V<={max_known_v:g} or unknown" if max_known_v is not None else ""
        items.append(("diamond", KNOWN_COLOR, f"known asteroid/comet, predicted position{v}"))
    if measured:
        items.append(("", None, "SkyShift measured:"))
        items += [("square2", CLASS_COLOR["suspect"], "suspect (lead)"),
                  ("square2", CLASS_COLOR["mover"], "mover (vetted out)"),
                  ("square", CLASS_COLOR["known"], "known object"),
                  ("x", CLASS_COLOR["artifact"], "artifact"),
                  ("plus", CLASS_COLOR["star"], "Gaia star"),
                  ("dot", CLASS_COLOR["other"], "other")]
    else:
        why = f" ({measured_reason})" if measured_reason else ""
        items.append(("", None, f"measured layer: none here{why}"))
    items.append(("swatch", NO_DATA, "no data"))
    if with_diff:
        items.append(("", None, "last - first: white brighter later, black fainter, no marks"))
    return items


def _legend_strip(width: int, items: list[LegendItem]) -> Image.Image:
    font = _font(11)
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    placed, x, row = [], 6, 0
    for glyph, color, text in items:
        w = (18 if glyph else 0) + int(probe.textlength(text, font=font)) + 16
        if x > 6 and x + w > width - 4:
            x, row = 6, row + 1
        placed.append((x, row, glyph, color, text))
        x += w
    img = Image.new("RGB", (width, (row + 1) * LEGEND_ROW + 8), (8, 9, 12))
    d = ImageDraw.Draw(img)
    for x, row, glyph, color, text in placed:
        y = 4 + row * LEGEND_ROW + LEGEND_ROW // 2
        if glyph:
            kind, width_ = ("square", 2) if glyph == "square2" else (glyph, 1 if glyph in ("circle", "square") else 2)
            r = {"dot": 1.5, "plus": 4, "x": 4, "swatch": 5, "tick": 7}.get(kind, 6)
            _glyph(d, kind, x + 7, y, r, color, width_)
            x += 18
        d.text((x, y - 7), text, fill=TEXT, font=font)
    return img


# ---------------------------------------------------------------- outputs

def _panels(frames: list[np.ma.MaskedArray], labels: list[str],
            marks: list[Marks] | None) -> tuple[list[Image.Image], int]:
    if not frames:
        raise ValueError("no frames to draw")
    if marks is not None and len(marks) != len(frames):
        raise ValueError("one Marks per frame")
    lo, hi = _limits(frames)
    ny, nx = frames[0].shape
    factor = max(1, -(-PANEL_MIN // max(nx, ny)))
    panels = [_panel(_stretch(f, lo, hi), lab, factor, marks[i] if marks else None)
              for i, (f, lab) in enumerate(zip(frames, labels))]
    return panels, factor


def contact_sheet(frames: list[np.ma.MaskedArray], labels: list[str], with_diff: bool = True,
                  marks: list[Marks] | None = None, legend_items: list[LegendItem] | None = None) -> bytes:
    """One PNG: the frames left to right in time, plus last − first (no marks on it). With `marks`
    (one per frame) the overlays are drawn on the frames; with `legend_items` a legend strip goes
    under the panels. Returns PNG bytes."""
    panels, factor = _panels(frames, labels, marks)
    if with_diff and len(frames) >= 2:
        panels.append(_panel(_diff_rgb(frames[0], frames[-1]), "last - first", factor))
    w = sum(p.width for p in panels) + GAP * (len(panels) - 1)
    strip = _legend_strip(w, legend_items) if legend_items else None
    sheet = Image.new("RGB", (w, panels[0].height + (strip.height if strip else 0)), BG)
    x = 0
    for p in panels:
        sheet.paste(p, (x, 0))
        x += p.width + GAP
    if strip:
        sheet.paste(strip, (0, panels[0].height))
    buf = io.BytesIO()
    sheet.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _gif_palette() -> Image.Image:
    """The mark colours exactly, then greys to fill 256 entries (an adaptive palette merges thin
    marks into the greys)."""
    fixed = [BG, (8, 9, 12), NO_DATA, TICK, TEXT, GAIA_COLOR, GAIA_FAINT_COLOR, (120, 120, 120),
             *CLASS_COLOR.values()]
    fixed = list(dict.fromkeys(fixed))
    greys = [(int(v),) * 3 for v in np.linspace(0, 255, 256 - len(fixed))]
    pal = Image.new("P", (1, 1))
    pal.putpalette([c for rgb in fixed + greys for c in rgb])
    return pal


def animation(frames: list[np.ma.MaskedArray], labels: list[str], marks: list[Marks] | None = None,
              legend_items: list[LegendItem] | None = None, ms: int = 700) -> bytes:
    """A looping GIF of the frames in time order: the same stretch, marks and legend as the
    contact sheet, `ms` milliseconds a frame. Returns GIF bytes."""
    panels, _ = _panels(frames, labels, marks)
    strip = _legend_strip(panels[0].width, legend_items) if legend_items else None
    out = []
    for p in panels:
        im = Image.new("RGB", (p.width, p.height + (strip.height if strip else 0)), BG)
        im.paste(p, (0, 0))
        if strip:
            im.paste(strip, (0, p.height))
        out.append(im.quantize(palette=_gif_palette(), dither=Image.Dither.NONE))
    buf = io.BytesIO()
    out[0].save(buf, format="GIF", save_all=True, append_images=out[1:], duration=ms, loop=0,
                disposal=1, optimize=False)
    return buf.getvalue()
