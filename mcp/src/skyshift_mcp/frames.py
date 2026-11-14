"""Turn /api/frames data into one PNG a model can look at (contracts/api.md, Frame data).

Frames are little-endian uint16, north up and east left; value = raw × scale + zero (MJy/sr);
raw 0 = no data. All panels share one asinh stretch so brightness compares between epochs; a last
panel shows last − first (white = brighter later, black = fainter). Pure functions, no network.
"""
from __future__ import annotations

import io
from typing import Any, Sequence

import numpy as np
from PIL import Image, ImageDraw, ImageFont

PANEL_MIN = 260        # panels are upscaled (nearest) to at least this many pixels a side
LABEL_H = 30
GAP = 6
BG = (16, 18, 24)
NO_DATA = (60, 30, 60)  # purple hatch colour for raw = 0
TICK = (255, 190, 60)


def decode(raw: bytes, size: Sequence[int], scale: float, zero: float) -> np.ma.MaskedArray:
    nx, ny = int(size[0]), int(size[1])
    a = np.frombuffer(raw, dtype="<u2")
    if a.size != nx * ny:
        raise ValueError(f"frame has {a.size} values, expected {nx}×{ny}")
    a = a.reshape(ny, nx)
    return np.ma.masked_equal(a, 0).astype(np.float64) * scale + zero


def crop(f: np.ma.MaskedArray, pixel_arcsec: float, zoom_arcmin: float | None) -> np.ma.MaskedArray:
    """A square of zoom_arcmin around the frame centre (pixel ((nx-1)/2, (ny-1)/2)); the whole frame
    if zoom is None or larger than the frame."""
    if not zoom_arcmin:
        return f
    ny, nx = f.shape
    half = max(4, int(round(zoom_arcmin * 60.0 / pixel_arcsec / 2)))
    cy, cx = (ny - 1) // 2, (nx - 1) // 2
    if 2 * half + 1 >= min(nx, ny):
        return f
    return f[cy - half:cy + half + 1, cx - half:cx + half + 1]


def pick_epochs(epochs: list[dict[str, Any]], det: int | None, max_frames: int) -> list[dict[str, Any]]:
    """Epochs of one detector, sorted by time; if there are more than max_frames, an even spread
    that keeps the first and last."""
    rows = [e for e in epochs if det is None or e.get("det") == det]
    rows.sort(key=lambda e: e["mjd_mid"])
    if len(rows) <= max_frames:
        return rows
    idx = np.unique(np.round(np.linspace(0, len(rows) - 1, max(2, max_frames))).astype(int))
    return [rows[i] for i in idx]


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


def _panel(rgb: np.ndarray, label: str, factor: int) -> Image.Image:
    img = Image.fromarray(rgb, "RGB").resize((rgb.shape[1] * factor, rgb.shape[0] * factor), Image.NEAREST)
    w, h = img.size
    d = ImageDraw.Draw(img)
    cx, cy, g, ln = w // 2, h // 2, max(8, w // 20), max(8, w // 14)
    for x0, y0, x1, y1 in ((cx - g - ln, cy, cx - g, cy), (cx + g, cy, cx + g + ln, cy),
                           (cx, cy - g - ln, cx, cy - g), (cx, cy + g, cx, cy + g + ln)):
        d.line((x0, y0, x1, y1), fill=TICK, width=2)
    out = Image.new("RGB", (w, h + LABEL_H), BG)
    out.paste(img, (0, LABEL_H))
    ImageDraw.Draw(out).text((4, 8), label, fill=(230, 230, 230), font=ImageFont.load_default())
    return out


def label_for(e: dict[str, Any]) -> str:
    from datetime import datetime, timedelta
    t = datetime(1858, 11, 17) + timedelta(days=float(e["mjd_mid"]))
    when = t.strftime("%Y-%m-%d") if e.get("kind") == "visit" else t.strftime("%Y-%m-%d %H:%M")
    return f"{e['id']}  {when}"


def contact_sheet(frames: list[np.ma.MaskedArray], labels: list[str], with_diff: bool = True) -> bytes:
    """One PNG: the frames left to right in time, plus last − first. Returns PNG bytes."""
    if not frames:
        raise ValueError("no frames to draw")
    lo, hi = _limits(frames)
    ny, nx = frames[0].shape
    factor = max(1, -(-PANEL_MIN // max(nx, ny)))
    panels = [_panel(_stretch(f, lo, hi), lab, factor) for f, lab in zip(frames, labels)]
    if with_diff and len(frames) >= 2:
        panels.append(_panel(_diff_rgb(frames[0], frames[-1]), "last - first", factor))
    w = sum(p.width for p in panels) + GAP * (len(panels) - 1)
    sheet = Image.new("RGB", (w, panels[0].height), BG)
    x = 0
    for p in panels:
        sheet.paste(p, (x, 0))
        x += p.width + GAP
    buf = io.BytesIO()
    sheet.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
