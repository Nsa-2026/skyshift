// Label placement on the sky map: zone badges and featured-target names never cover a zone, a
// mark or each other. Each label tries its places in order (right of a mark, then left, above,
// below); when none is free it tries a shorter text, and when nothing fits it is left out. The
// busiest zone's badge goes first, then the targets in their list order. Pure: screen boxes only.

export interface Box {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export interface Size {
  w: number;
  h: number;
}

export interface LabelSpec {
  id: string;
  /** Texts to try, the full one first, then shorter forms. */
  texts: readonly string[];
  /** The point the label belongs to. Obstacles that hold it are ignored: a label can't leave the area its object sits in. */
  anchor: { x: number; y: number };
  /** Places for a label of this size, in order of preference. */
  places(size: Size): Box[];
}

export interface Placed {
  id: string;
  text: string;
  box: Box;
}

/** Space kept between a label and anything else, px. */
export const LABEL_GAP = 3;

export function boxesHit(a: Box, b: Box, pad = 0): boolean {
  return a.x0 < b.x1 + pad && b.x0 < a.x1 + pad && a.y0 < b.y1 + pad && b.y0 < a.y1 + pad;
}

export function boxHolds(b: Box, p: { x: number; y: number }): boolean {
  return p.x >= b.x0 && p.x <= b.x1 && p.y >= b.y0 && p.y <= b.y1;
}

function within(b: Box, bounds: Box): boolean {
  return b.x0 >= bounds.x0 && b.y0 >= bounds.y0 && b.x1 <= bounds.x1 && b.y1 <= bounds.y1;
}

/**
 * Place labels in the order given. `measure` sizes a text; `obstacles` are zones, marks and the
 * controls floating over the map; `bounds` is the map. Returns the labels that found a place.
 */
export function placeLabels<S extends LabelSpec>(
  specs: readonly S[],
  measure: (text: string, spec: S) => Size,
  obstacles: readonly Box[],
  bounds: Box,
  gap = LABEL_GAP,
): Placed[] {
  const placed: Placed[] = [];
  for (const s of specs) {
    const blocking = obstacles.filter((o) => !boxHolds(o, s.anchor));
    let done = false;
    for (const text of s.texts) {
      const size = measure(text, s);
      for (const box of s.places(size)) {
        if (!within(box, bounds)) continue;
        if (blocking.some((o) => boxesHit(box, o, gap))) continue;
        if (placed.some((p) => boxesHit(box, p.box, gap))) continue;
        placed.push({ id: s.id, text, box });
        done = true;
        break;
      }
      if (done) break;
    }
  }
  return placed;
}

const at = (x: number, y: number, w: number, h: number): Box => ({ x0: x, y0: y, x1: x + w, y1: y + h });

/** Places round a mark at (x, y): right, left, above, below, `r` px from its centre. */
export function aroundPoint(x: number, y: number, size: Size, r = 11): Box[] {
  const { w, h } = size;
  return [at(x + r, y - h / 2, w, h), at(x - r - w, y - h / 2, w, h), at(x - w / 2, y - r - h, w, h), at(x - w / 2, y + r, w, h)];
}

/**
 * Places round an area's screen box: beside it (east first, screen left), then above or below it,
 * then its corners outside. When the area is larger than the map, inside the visible part's
 * corners last (the area's own box doesn't block its badge: the badge's anchor is inside it).
 */
export function aroundBox(b: Box, size: Size, bounds: Box, gap = 8): Box[] {
  const { w, h } = size;
  const cx = (b.x0 + b.x1) / 2;
  const cy = (b.y0 + b.y1) / 2;
  const out = [
    at(b.x0 - gap - w, cy - h / 2, w, h),
    at(b.x1 + gap, cy - h / 2, w, h),
    at(cx - w / 2, b.y0 - gap - h, w, h),
    at(cx - w / 2, b.y1 + gap, w, h),
    at(b.x0 - gap - w, b.y0 - h, w, h),
    at(b.x1 + gap, b.y0 - h, w, h),
    at(b.x0 - gap - w, b.y1, w, h),
    at(b.x1 + gap, b.y1, w, h),
  ];
  // Inside the part of the area on screen, when there's room for the badge there.
  const v = { x0: Math.max(b.x0, bounds.x0), y0: Math.max(b.y0, bounds.y0), x1: Math.min(b.x1, bounds.x1), y1: Math.min(b.y1, bounds.y1) };
  if (v.x1 - v.x0 >= w + 2 * gap && v.y1 - v.y0 >= h + 2 * gap) {
    out.push(
      at(v.x0 + gap, v.y0 + gap, w, h),
      at(v.x1 - gap - w, v.y0 + gap, w, h),
      at(v.x0 + gap, v.y1 - gap - h, w, h),
      at(v.x1 - gap - w, v.y1 - gap - h, w, h),
      at((v.x0 + v.x1) / 2 - w / 2, v.y0 + gap, w, h),
    );
  }
  return out;
}

/** Screen box of points, or null when there are none. */
export function boundsOf(points: readonly { x: number; y: number }[]): Box | null {
  if (!points.length) return null;
  let x0 = Infinity;
  let y0 = Infinity;
  let x1 = -Infinity;
  let y1 = -Infinity;
  for (const p of points) {
    if (p.x < x0) x0 = p.x;
    if (p.y < y0) y0 = p.y;
    if (p.x > x1) x1 = p.x;
    if (p.y > y1) y1 = p.y;
  }
  return { x0, y0, x1, y1 };
}

/** A label's size without a canvas to measure on (tests, jsdom): Archivo semi-condensed is about 0.55 em a letter. */
export function estimateSize(text: string, fontPx: number, padX = 0, h = Math.round(fontPx * 1.35)): Size {
  return { w: Math.ceil(text.length * fontPx * 0.55 + 2 * padX), h };
}

/** Badge style shared by the globe and the flat map: a pill this tall, text this size. */
export const BADGE = { h: 22, font: 12.5, padX: 8, weight: 650 } as const;
/** Featured-target names. */
export const NAME = { h: 16, font: 12.5, weight: 650 } as const;
