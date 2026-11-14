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
