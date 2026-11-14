// Semantic zoom (web/DESIGN.md "Sky"): the field of view decides what the map draws.
//   > 60°      whole sky: heat map of order-3 cells (~7°), zones outlined
//   8°–60°     finer heat cells (order 5, ~1.8°) with their counts, featured targets labelled
//   0.5°–8°    single signals as pencil marks
//   < 0.5°     the same marks with their numbers written beside them
// Pure.
import { cellSideDeg } from "./healpix";

export type Level = "whole" | "mid" | "close" | "detail";

export const LEVEL_FOV = { whole: 60, mid: 8, close: 0.5 } as const;

export function levelFor(fovDeg: number): Level {
  if (!Number.isFinite(fovDeg) || fovDeg > LEVEL_FOV.whole) return "whole";
  if (fovDeg > LEVEL_FOV.mid) return "mid";
  if (fovDeg > LEVEL_FOV.close) return "close";
  return "detail";
}

export const showsHeat = (l: Level): boolean => l === "whole" || l === "mid";
export const showsSignals = (l: Level): boolean => l === "close" || l === "detail";

/** Screen size of one cell, px, for a map `widthPx` wide showing `fovDeg` across. */
export function cellPx(order: number, fovDeg: number, widthPx: number): number {
  if (!(fovDeg > 0) || !(widthPx > 0)) return 0;
  return (cellSideDeg(order) / fovDeg) * widthPx;
}

/**
 * HEALPix order of the heat cells. Order 3 for the whole sky and order 5 in the middle range,
 * as the design says; one order coarser when the finer cells would be smaller than `minPx`
 * (a phone showing 60°), so a patch never shrinks to a speck.
 */
export function heatOrder(fovDeg: number, widthPx: number, minPx = 12): number {
  const level = levelFor(fovDeg);
  if (level === "whole") return 3;
  if (cellPx(5, fovDeg, widthPx) >= minPx) return 5;
  return 4;
}

/** Counts are written in a cell only when it is this wide on screen. */
export const LABEL_MIN_PX = 26;

/** Field of view that shows a patch `spanDeg` across with some sky around it, landing on the next level down. */
export function fovToOpen(spanDeg: number, from: Level): number {
  const want = Math.max(spanDeg * 1.6, 0.25);
  if (from === "whole") return Math.min(LEVEL_FOV.whole * 0.75, Math.max(LEVEL_FOV.mid * 1.5, want));
  if (from === "mid") return Math.min(LEVEL_FOV.mid * 0.9, Math.max(1, want));
  return Math.max(0.1, Math.min(LEVEL_FOV.close * 0.8, want));
}

/** Field of view for looking at one signal: close enough for its marks and numbers. */
export const SIGNAL_FOV = 1.2;
/** Field of view for a searched position or a featured target. */
export const SPOT_FOV = 2;
/** The globe at rest. */
export const WHOLE_FOV = 180;

/** Field of view that shows the whole sky: the globe (SIN) is 180° across, the Mollweide ellipse 360°. */
export function wholeFov(mol: boolean): number {
  return mol ? 360 : WHOLE_FOV;
}

/**
 * Open on the whole-sky ellipse when the map is wide enough to hold it (it is 2:1), and on the
 * globe when the map is tall, as on a phone, where the disk fills it.
 */
export function defaultMol(widthPx: number, heightPx: number): boolean {
  if (!(widthPx > 0) || !(heightPx > 0)) return false;
  return widthPx / heightPx >= 1.3;
}
