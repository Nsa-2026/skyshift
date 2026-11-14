// Painting "Tonight's search" on a canvas over the globe: each zone faint, by state.
//   queued      a hairline
//   extracting  blue hatch (a machine is reading its photos)
//   combining   blue hatch, denser (its detections are being joined)
//   extracted   a hairline, a little darker (read, waiting to be joined)
//   combined    a light red wash, redder with more suspects
//   failed      a cross
//   deferred    grey stipple (a crowded bulge zone, left for last)
// The heat map and the marks are drawn by Aladin above this canvas, so they stay on top.
import type { Vec } from "./marks";
import { suspectAlpha, type SearchLayer, type SearchState } from "./search";

export interface SearchInk {
  ink: string;
  red: string;
  blue: string;
  grey: string;
  warn: string;
}

/** Stroke and fill of a state, in words a test can check. */
export interface ZoneLook {
  stroke: string | null;
  lineWidth: number;
  fill: "none" | "hatch" | "hatch-dense" | "wash" | "stipple";
  cross: boolean;
}

export function zoneLook(state: SearchState): ZoneLook {
  switch (state) {
    case "extracting":
      return { stroke: "blue", lineWidth: 1, fill: "hatch", cross: false };
    case "combining":
      return { stroke: "blue", lineWidth: 1, fill: "hatch-dense", cross: false };
    case "combined":
      return { stroke: "red", lineWidth: 1, fill: "wash", cross: false };
    case "failed":
      return { stroke: "warn", lineWidth: 1, fill: "none", cross: true };
    case "deferred":
      return { stroke: "grey", lineWidth: 0.75, fill: "stipple", cross: false };
    case "extracted":
      return { stroke: "ink", lineWidth: 0.75, fill: "none", cross: false };
    default:
      return { stroke: "ink", lineWidth: 0.5, fill: "none", cross: false };
  }
}

function rgba(hex: string, a: number): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return hex;
  const n = parseInt(m[1], 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a.toFixed(3)})`;
}

function pattern(ctx: CanvasRenderingContext2D, size: number, paint: (p: CanvasRenderingContext2D) => void): CanvasPattern | null {
  if (typeof document === "undefined") return null;
  const c = document.createElement("canvas");
  c.width = size;
  c.height = size;
  const p = c.getContext("2d");
  if (!p) return null;
  paint(p);
  return ctx.createPattern(c, "repeat");
}

/** Screen points of a ring, or null when part of it can't be drawn or it jumps across the map's seam. */
function ringPoints(ring: readonly [number, number][], project: (ra: number, dec: number) => Vec | null, maxJump: number): Vec[] | null {
  const pts: Vec[] = [];
  for (const [ra, dec] of ring) {
    const q = project(ra, dec);
    if (!q) return null;
    if (pts.length && Math.hypot(q.x - pts[pts.length - 1].x, q.y - pts[pts.length - 1].y) > maxJump) return null;
    pts.push(q);
  }
  return pts.length >= 3 ? pts : null;
}

export function paintSearch(
  ctx: CanvasRenderingContext2D,
  layer: SearchLayer,
  project: (ra: number, dec: number) => Vec | null,
  ink: SearchInk,
  size: { w: number; h: number },
): void {
  const color = { ink: rgba(ink.ink, 0.28), blue: rgba(ink.blue, 0.7), red: rgba(ink.red, 0.35), warn: rgba(ink.warn, 0.8), grey: rgba(ink.grey, 0.55) };
  const hatch = pattern(ctx, 8, (p) => {
    p.strokeStyle = rgba(ink.blue, 0.45);
    p.lineWidth = 1;
    p.beginPath();
    p.moveTo(0, 8);
    p.lineTo(8, 0);
    p.stroke();
  });
  const hatchDense = pattern(ctx, 5, (p) => {
    p.strokeStyle = rgba(ink.blue, 0.5);
    p.lineWidth = 1;
    p.beginPath();
    p.moveTo(0, 5);
    p.lineTo(5, 0);
    p.stroke();
  });
  const stipple = pattern(ctx, 5, (p) => {
    p.fillStyle = rgba(ink.grey, 0.6);
    p.fillRect(1, 1, 1.2, 1.2);
  });
  const maxJump = Math.max(size.w, size.h) * 0.35;
  ctx.save();
  ctx.lineJoin = "round";
  for (const z of layer.zones) {
    const look = zoneLook(z.state);
    for (const ring of z.rings) {
      const pts = ringPoints(ring, project, maxJump);
      if (!pts) continue;
      ctx.beginPath();
      pts.forEach((q, i) => (i ? ctx.lineTo(q.x, q.y) : ctx.moveTo(q.x, q.y)));
      ctx.closePath();
      if (look.fill === "wash") {
        ctx.fillStyle = rgba(ink.red, suspectAlpha(z.suspects, layer.maxSuspects));
        ctx.fill();
      } else if (look.fill === "hatch" && hatch) {
        ctx.fillStyle = hatch;
        ctx.fill();
      } else if (look.fill === "hatch-dense" && hatchDense) {
        ctx.fillStyle = hatchDense;
        ctx.fill();
      } else if (look.fill === "stipple" && stipple) {
        ctx.fillStyle = stipple;
        ctx.fill();
      }
      if (look.stroke) {
        ctx.strokeStyle = color[look.stroke as keyof typeof color];
        ctx.lineWidth = look.lineWidth;
        ctx.stroke();
      }
      if (look.cross) {
        // Corner to corner of the ring's screen box.
        const xs = pts.map((q) => q.x);
        const ys = pts.map((q) => q.y);
        const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
        ctx.beginPath();
        ctx.moveTo(x0, y0);
        ctx.lineTo(x1, y1);
        ctx.moveTo(x1, y0);
        ctx.lineTo(x0, y1);
        ctx.strokeStyle = color.warn;
        ctx.lineWidth = 1;
        ctx.stroke();
      }
    }
  }
  ctx.restore();
}
