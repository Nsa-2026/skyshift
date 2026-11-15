// Grease-pencil marks for single signals (web/DESIGN.md "Sky" and "Pencil marks"):
//   fast mover   red open circle with a short tick pointing along its motion
//   moving star  red ring with a motion arrow: where it will be in 100 years
//   odd spectrum violet diamond
// 1.5 px strokes, 0.85 opacity, round caps. The geometry is pure and tested; draw* paint a canvas.
import type { CandidateKind } from "../api/types";

export interface Vec {
  x: number;
  y: number;
}

/** Years of motion the arrow shows: ×100 per year. */
export const ARROW_YEARS = 100;
export const ARROW_MIN_PX = 11;
export const ARROW_MAX_PX = 44;
export const MARK_R = 6.5;
export const HIT_PX = 14;

function unit(v: Vec): Vec | null {
  const n = Math.hypot(v.x, v.y);
  return n > 1e-9 ? { x: v.x / n, y: v.y / n } : null;
}

/**
 * Screen direction of a position angle (east of north). `north` and `east` are the screen
 * directions (any length) of a small step north and east at the mark; y grows downward.
 */
export function paToScreen(paDeg: number, north: Vec, east: Vec): Vec | null {
  const n = unit(north);
  const e = unit(east);
  if (!n || !e || !Number.isFinite(paDeg)) return null;
  const t = (paDeg * Math.PI) / 180;
  return unit({ x: Math.cos(t) * n.x + Math.sin(t) * e.x, y: Math.cos(t) * n.y + Math.sin(t) * e.y });
}

/** Screen direction of a PA on a plain north-up, east-left map (the fallback map and tests). */
export function paToScreenNorthUp(paDeg: number): Vec {
  return paToScreen(paDeg, { x: 0, y: -1 }, { x: -1, y: 0 })!;
}

/** Arrow length on screen: proper motion × 100 years, at least long enough to read, never a spear. */
export function arrowLengthPx(pmMasYr: number | null, pxPerArcsec: number): number {
  if (pmMasYr == null || !Number.isFinite(pmMasYr) || !(pxPerArcsec > 0)) return ARROW_MIN_PX;
  const px = (pmMasYr / 1000) * ARROW_YEARS * pxPerArcsec;
  return Math.max(ARROW_MIN_PX, Math.min(ARROW_MAX_PX, px));
}

/** A fast mover's tick: from just outside the circle, 6 px along the motion (or up-right when unknown). */
export function moverTick(x: number, y: number, dir: Vec | null, r = MARK_R): [Vec, Vec] {
  const d = dir ?? { x: Math.SQRT1_2, y: -Math.SQRT1_2 };
  return [
    { x: x + d.x * (r + 1.5), y: y + d.y * (r + 1.5) },
    { x: x + d.x * (r + 7.5), y: y + d.y * (r + 7.5) },
  ];
}

/** A moving star's arrow: shaft from the ring's edge to the tip, and the two barbs. */
export function starArrow(x: number, y: number, dir: Vec, lengthPx: number, r = MARK_R): { shaft: [Vec, Vec]; barbs: [Vec, Vec] } {
  const tip = { x: x + dir.x * (r + lengthPx), y: y + dir.y * (r + lengthPx) };
  const start = { x: x + dir.x * (r + 1), y: y + dir.y * (r + 1) };
  const b = 4.5;
  const back = { x: tip.x - dir.x * b, y: tip.y - dir.y * b };
  const perp = { x: -dir.y, y: dir.x };
  return {
    shaft: [start, tip],
    barbs: [
      { x: back.x + perp.x * b * 0.7, y: back.y + perp.y * b * 0.7 },
      { x: back.x - perp.x * b * 0.7, y: back.y - perp.y * b * 0.7 },
    ],
  };
}

/** The four corners of an odd-spectrum diamond. */
export function diamond(x: number, y: number, r = MARK_R + 1): Vec[] {
  return [
    { x, y: y - r },
    { x: x + r, y },
    { x, y: y + r },
    { x: x - r, y },
  ];
}

export interface HitPoint {
  id: string;
  x: number;
  y: number;
}

/** The point nearest (x, y) within `radius` px, or null. */
export function nearestHit<T extends HitPoint>(points: readonly T[], x: number, y: number, radius = HIT_PX): T | null {
  let best: T | null = null;
  let bestD = radius * radius;
  for (const p of points) {
    const d = (p.x - x) ** 2 + (p.y - y) ** 2;
    if (d <= bestD) {
      bestD = d;
      best = p;
    }
  }
  return best;
}

// ---- painting ------------------------------------------------------------------------------

export interface Ink {
  red: string;
  violet: string;
  ink: string;
  ink2: string;
  glass: string;
  sheet: string;
}

export interface MarkStyle {
  selected?: boolean;
  hovered?: boolean;
  /** Grade C (failed a vetting check): the same mark in grey pencil, fainter. */
  muted?: boolean;
}

/** Cool, readable annotation colours over real survey imagery; the plate negative keeps its ink. */
export function signalInk(ink: Ink, negative: boolean): Ink {
  return negative ? ink : { ...ink, red: "#8bc8db", violet: "#baa9e6", ink2: "#8898a8", glass: "#07121e", ink: "#e5f3fc" };
}

function focusCorners(ctx: CanvasRenderingContext2D, ink: Ink, x: number, y: number, r: number) {
  for (const [color, width] of [[ink.glass, 3.5], [ink.ink, 1.25]] as const) {
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.globalAlpha = 1;
    ctx.beginPath();
    for (const dx of [-1, 1]) for (const dy of [-1, 1]) {
      ctx.moveTo(x + dx * (r - 4), y + dy * r);
      ctx.lineTo(x + dx * r, y + dy * r);
      ctx.lineTo(x + dx * r, y + dy * (r - 4));
    }
    ctx.stroke();
  }
}

/** Compact, hollow overview marks leave the observed source visible at their centre. */
export function drawSignalDot(ctx: CanvasRenderingContext2D, ink: Ink, kind: CandidateKind, x: number, y: number, style: MarkStyle = {}): void {
  ctx.save();
  ctx.lineJoin = "round";
  const active = style.hovered || style.selected;
  const r = active ? 4.5 : 3;
  const color = style.muted ? ink.ink2 : kind === "spectral" ? ink.violet : ink.red;
  for (const [stroke, width, alpha] of [[ink.glass, 3.75, 1], [color, active ? 1.65 : 1.25, active ? 1 : style.muted ? 0.65 : 0.88]] as const) {
    ctx.strokeStyle = stroke;
    ctx.lineWidth = width;
    ctx.globalAlpha = alpha;
    ctx.beginPath();
    if (kind === "spectral") {
      const points = diamond(x, y, r + 0.5);
      ctx.moveTo(points[0].x, points[0].y);
      for (const p of points.slice(1)) ctx.lineTo(p.x, p.y);
      ctx.closePath();
    } else ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.stroke();
  }
  if (active) focusCorners(ctx, ink, x, y, style.selected ? 12 : 10);
  ctx.restore();
}

function line(ctx: CanvasRenderingContext2D, a: Vec, b: Vec) {
  ctx.moveTo(a.x, a.y);
  ctx.lineTo(b.x, b.y);
}

/** One candidate's pencil mark at (x, y). `dir` is its motion on screen, if known. */
export function drawSignal(
  ctx: CanvasRenderingContext2D,
  ink: Ink,
  kind: CandidateKind,
  x: number,
  y: number,
  dir: Vec | null,
  arrowPx: number,
  style: MarkStyle = {},
): void {
  ctx.save();
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  const color = style.muted ? ink.ink2 : kind === "spectral" ? ink.violet : ink.red;
  const alpha = style.hovered || style.selected ? 1 : style.muted ? 0.6 : 0.85;
  // A narrow contrasting under-stroke separates the annotation from the survey, without bloom.
  const passes: [string, number, number][] = [
    [ink.glass, style.selected ? 4.5 : 3.75, 1],
    [color, style.selected || style.hovered ? 1.9 : 1.4, alpha],
  ];
  for (const [stroke, width, alpha] of passes) {
    ctx.strokeStyle = stroke;
    ctx.lineWidth = width;
    ctx.globalAlpha = alpha;
    ctx.beginPath();
    if (kind === "spectral") {
      const d = diamond(x, y);
      ctx.moveTo(d[0].x, d[0].y);
      for (const p of d.slice(1)) ctx.lineTo(p.x, p.y);
      ctx.closePath();
    } else {
      ctx.moveTo(x + MARK_R, y);
      ctx.arc(x, y, MARK_R, 0, Math.PI * 2);
      if (kind === "mover") {
        const [a, b] = moverTick(x, y, dir);
        line(ctx, a, b);
      } else if (dir) {
        const { shaft, barbs } = starArrow(x, y, dir, arrowPx);
        line(ctx, shaft[0], shaft[1]);
        line(ctx, barbs[0], shaft[1]);
        line(ctx, barbs[1], shaft[1]);
      }
    }
    ctx.stroke();
  }
  if (style.selected || style.hovered) focusCorners(ctx, ink, x, y, style.selected ? 18 : 14);
  ctx.restore();
}

/** A soft look for marks and names on the sky: a colour and a blurred shadow under it, no hard halo. */
export interface SoftLook {
  color: string;
  shadow: string;
}

/**
 * A featured target: a small reticle and its short name. With `soft`, the reticle is drawn in that
 * colour on a soft shadow (the map's own look); without it, ink on a glass halo.
 */
export function drawTarget(
  ctx: CanvasRenderingContext2D,
  ink: Ink,
  x: number,
  y: number,
  label: string | null,
  side: "right" | "left" = "right",
  soft?: SoftLook,
): void {
  ctx.save();
  ctx.lineCap = "round";
  ctx.globalAlpha = 0.9;
  const passes: (readonly [string, number])[] = soft
    ? [[soft.color, 1.5]]
    : [
        [ink.glass, 4],
        [ink.ink, 1.5],
      ];
  if (soft) {
    ctx.shadowColor = soft.shadow;
    ctx.shadowBlur = 3;
  }
  for (const [stroke, width] of passes) {
    ctx.strokeStyle = stroke;
    ctx.lineWidth = width;
    ctx.beginPath();
    for (const [dx, dy] of [
      [1, 0],
      [-1, 0],
      [0, 1],
      [0, -1],
    ]) {
      ctx.moveTo(x + dx * 3.5, y + dy * 3.5);
      ctx.lineTo(x + dx * 8, y + dy * 8);
    }
    ctx.stroke();
  }
  if (label) {
    const left = side === "left";
    drawLabel(ctx, ink, left ? x - 11 : x + 11, y, label, { color: ink.ink, align: left ? "right" : "left", weight: 600 });
  }
  ctx.restore();
}

export interface LabelOpts {
  color: string;
  align?: CanvasTextAlign;
  weight?: number;
  size?: number;
  /** Halo colour behind the letters. */
  halo?: string;
  /** A soft blurred shadow of this colour instead of the halo (names on the sky map). */
  shadow?: string;
}

/** Text with a glass halo so it reads on any sky. Archivo, semi-condensed for labels and numbers. */
export function drawLabel(ctx: CanvasRenderingContext2D, ink: Ink, x: number, y: number, text: string, o: LabelOpts): void {
  ctx.save();
  ctx.font = `${o.weight ?? 600} ${o.size ?? 12}px Archivo, system-ui, sans-serif`;
  try {
    (ctx as CanvasRenderingContext2D & { fontStretch?: string }).fontStretch = "semi-condensed";
  } catch {
    /* older canvas: normal width */
  }
  ctx.textAlign = o.align ?? "center";
  ctx.textBaseline = "middle";
  ctx.lineJoin = "round";
  ctx.globalAlpha = 1;
  if (o.shadow) {
    ctx.shadowColor = o.shadow;
    ctx.shadowBlur = 4;
  } else {
    ctx.strokeStyle = o.halo ?? ink.glass;
    ctx.lineWidth = 3.5;
    ctx.strokeText(text, x, y);
  }
  ctx.fillStyle = o.color;
  ctx.fillText(text, x, y);
  ctx.restore();
}
