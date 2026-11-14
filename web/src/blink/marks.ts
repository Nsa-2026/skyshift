// Grease-pencil marks and plate guides drawn on the blink canvas (web/DESIGN.md, "Pencil marks"):
// 1.5 px strokes, 0.85 alpha, round caps. Each helper draws one mark; the caller sets colours.
type Ctx = CanvasRenderingContext2D;

export const PENCIL_WIDTH = 1.5;
export const PENCIL_ALPHA = 0.85;

/** Stroke the current path as a pencil line. */
export function pencil(ctx: Ctx, color: string, width = PENCIL_WIDTH, alpha = PENCIL_ALPHA): void {
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.stroke();
  ctx.restore();
}

/** A Gaia ring. */
export function ring(ctx: Ctx, x: number, y: number, r: number): void {
  ctx.beginPath();
  ctx.arc(x, y, r, 0, Math.PI * 2);
}

/** The target: an open reticle, four ticks pointing in, with a gap so the dot stays visible. */
export function reticle(ctx: Ctx, x: number, y: number, gap: number, len: number): void {
  ctx.beginPath();
  for (const [dx, dy] of [
    [1, 0],
    [-1, 0],
    [0, 1],
    [0, -1],
  ]) {
    ctx.moveTo(x + dx * gap, y + dy * gap);
    ctx.lineTo(x + dx * (gap + len), y + dy * (gap + len));
  }
}

/** A known asteroid or comet: a diamond. */
export function diamond(ctx: Ctx, x: number, y: number, r: number): void {
  ctx.beginPath();
  ctx.moveTo(x, y - r);
  ctx.lineTo(x + r, y);
  ctx.lineTo(x, y + r);
  ctx.lineTo(x - r, y);
  ctx.closePath();
}

/** A flagged dot: a circle with a short tick off its upper right, like a margin note. */
export function flagMark(ctx: Ctx, x: number, y: number, r: number): void {
  ctx.beginPath();
  ctx.arc(x, y, r, 0, Math.PI * 2);
  const a = -Math.PI / 4;
  ctx.moveTo(x + Math.cos(a) * (r + 3), y + Math.sin(a) * (r + 3));
  ctx.lineTo(x + Math.cos(a) * (r + 10), y + Math.sin(a) * (r + 10));
}

// ---- What SkyShift measured: one circle per detection, its shape telling the class -------------
// (colour is never the only cue). Each circle is as big as its source looks on the plate (layout.ts),
// and a Gaia ring is always a little bigger than the circle it holds, so agreement reads as a circle
// inside a ring. A saturated star with no detection of its own gets one dashed ring (`bigStar`).

export type MeasuredShape = "star" | "known" | "mover" | "suspect" | "artifact" | "other";

/** Stroke per class: width, alpha, and a dash (dots for `other`). */
export const MEASURED_STROKE: Record<MeasuredShape, { width: number; alpha: number; dash: number[] }> = {
  star: { width: 1.1, alpha: 0.75, dash: [] },
  other: { width: 1.5, alpha: 0.8, dash: [0.01, 3] },
  known: { width: 1.4, alpha: 0.9, dash: [] },
  mover: { width: 1.6, alpha: 0.9, dash: [] },
  suspect: { width: 2.4, alpha: 0.95, dash: [] },
  artifact: { width: 1.25, alpha: 0.7, dash: [] },
};

/** The path of a measured mark: a circle, doubled for known, side ticks for a mover, struck through for an artefact. */
export function measuredPath(ctx: Ctx, cls: MeasuredShape, x: number, y: number, r: number): void {
  ctx.beginPath();
  ctx.arc(x, y, r, 0, Math.PI * 2);
  if (cls === "known") {
    ctx.moveTo(x + r + 2.5, y);
    ctx.arc(x, y, r + 2.5, 0, Math.PI * 2);
  } else if (cls === "mover") {
    ctx.moveTo(x - r - 2, y);
    ctx.lineTo(x - r - 6, y);
    ctx.moveTo(x + r + 2, y);
    ctx.lineTo(x + r + 6, y);
  } else if (cls === "artifact") {
    const k = r * Math.SQRT1_2;
    ctx.moveTo(x - k, y + k);
    ctx.lineTo(x + k, y - k);
  }
}

/**
 * Draw a measured mark. The rarer classes get a halo in the plate colour under the pencil, so they
 * read on a dark core; stars and other sources (hundreds of them) stay a plain thin line.
 */
export function measuredMark(ctx: Ctx, cls: MeasuredShape, x: number, y: number, r: number, color: string, halo: string): void {
  const s = MEASURED_STROKE[cls];
  measuredPath(ctx, cls, x, y, r);
  ctx.save();
  ctx.setLineDash(s.dash);
  if (cls !== "star" && cls !== "other") pencil(ctx, halo, s.width + 2, 0.5);
  pencil(ctx, color, s.width, s.alpha);
  ctx.restore();
}

/**
 * A motion arrow through screen points in time order, the head on the last. Nothing is drawn
 * shorter than 1.5 px. Arrows are to scale unless stretched (measured.ts `motionArrow`), and then
 * they carry their factor: never shortened, so there is no break to explain.
 */
export function arrowPath(ctx: Ctx, pts: { X: number; Y: number }[], head = 6): boolean {
  if (pts.length < 2) return false;
  const a = pts[pts.length - 2];
  const b = pts[pts.length - 1];
  let len = 0;
  for (let i = 1; i < pts.length; i++) len += Math.hypot(pts[i].X - pts[i - 1].X, pts[i].Y - pts[i - 1].Y);
  if (len < 1.5) return false;
  ctx.beginPath();
  pts.forEach((p, i) => (i ? ctx.lineTo(p.X, p.Y) : ctx.moveTo(p.X, p.Y)));
  const ang = Math.atan2(b.Y - a.Y, b.X - a.X);
  const h = Math.min(head, len * 0.6);
  for (const s of [-1, 1]) {
    ctx.moveTo(b.X, b.Y);
    ctx.lineTo(b.X - h * Math.cos(ang + s * 0.45), b.Y - h * Math.sin(ang + s * 0.45));
  }
  return true;
}

/** Where a stretched arrow's factor ("×10") goes: beside its middle, on the side away from the head's turn. */
export function factorSpot(from: { X: number; Y: number }, to: { X: number; Y: number }, gap = 9): { X: number; Y: number } {
  const ang = Math.atan2(to.Y - from.Y, to.X - from.X);
  const mx = (from.X + to.X) / 2;
  const my = (from.Y + to.Y) / 2;
  // The normal that points up the plate (or right, for a vertical arrow), so labels read clear of the shaft.
  let nx = Math.sin(ang);
  let ny = -Math.cos(ang);
  if (ny > 0 || (ny === 0 && nx < 0)) {
    nx = -nx;
    ny = -ny;
  }
  return { X: mx + nx * gap, Y: my + ny * gap };
}

/** A saturated (or very bright) star without a detection of its own: one long-dashed ring around its blob. */
export function bigStar(ctx: Ctx, x: number, y: number, r: number, color: string, halo: string): void {
  ring(ctx, x, y, r);
  ctx.save();
  ctx.setLineDash([7, 4]);
  pencil(ctx, halo, 3.5, 0.5);
  pencil(ctx, color, 1.6, 0.9);
  ctx.restore();
}

/** Small text with a halo in the plate colour, so it reads over stars. */
export function label(ctx: Ctx, text: string, x: number, y: number, color: string, halo: string): void {
  ctx.save();
  ctx.lineJoin = "round";
  ctx.lineWidth = 3;
  ctx.strokeStyle = halo;
  ctx.strokeText(text, x, y);
  ctx.fillStyle = color;
  ctx.fillText(text, x, y);
  ctx.restore();
}

/** Scale bar: a line with end ticks, its length printed above. (x0, y0) is the left end. */
export function scaleBar(ctx: Ctx, x0: number, y0: number, len: number): void {
  ctx.beginPath();
  ctx.moveTo(x0, y0 - 4);
  ctx.lineTo(x0, y0);
  ctx.lineTo(x0 + len, y0);
  ctx.lineTo(x0 + len, y0 - 4);
}

/** Compass at its corner (cx, cy): north up, east left, arms of length L. */
export function compass(ctx: Ctx, cx: number, cy: number, L: number): void {
  ctx.beginPath();
  ctx.moveTo(cx - L, cy);
  ctx.lineTo(cx, cy);
  ctx.lineTo(cx, cy - L);
  ctx.moveTo(cx - 3, cy - L + 4);
  ctx.lineTo(cx, cy - L);
  ctx.lineTo(cx + 3, cy - L + 4);
  ctx.moveTo(cx - L + 4, cy - 3);
  ctx.lineTo(cx - L, cy);
  ctx.lineTo(cx - L + 4, cy + 3);
}

/** The no-data fill: flat sheet with a fine diagonal hatch, drawn at device resolution. */
export function hatchPattern(ctx: Ctx, dpr: number, bg: string, line: string): CanvasPattern | null {
  const step = Math.max(4, Math.round(6 * dpr));
  const tile = document.createElement("canvas");
  tile.width = step;
  tile.height = step;
  const t = tile.getContext("2d");
  if (!t) return null;
  t.fillStyle = bg;
  t.fillRect(0, 0, step, step);
  t.strokeStyle = line;
  t.lineWidth = Math.max(1, dpr * 0.9);
  t.beginPath();
  // One diagonal per tile, plus the corners, so tiles join without seams.
  t.moveTo(-1, step + 1);
  t.lineTo(step + 1, -1);
  t.moveTo(-1, 1);
  t.lineTo(1, -1);
  t.moveTo(step - 1, step + 1);
  t.lineTo(step + 1, step - 1);
  t.stroke();
  return ctx.createPattern(tile, "repeat");
}
