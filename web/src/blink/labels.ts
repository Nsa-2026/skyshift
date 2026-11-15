// Where the plate's small words go (P4): "saturated", a suspect's grade, an arrow's "×20", a known
// asteroid's name, "Saturn". Each is tried at eight spots around the mark it names and goes to the
// first that covers no other word, no arrow and nothing off the plate, preferring a spot that
// crosses the fewest circles. No spot free: the word is left out (the hover still says it). Pure.

/** A text box in screen pixels: top-left corner, width and height. */
export interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}
/** A circle on the plate (a mark's outline, or a point when r is 0). */
export interface Disc {
  X: number;
  Y: number;
  r: number;
}
/** A straight piece of an arrow. */
export interface Seg {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

/** Spots around a mark, as angles (radians, screen y down): lower right first, as before, then the other diagonals and sides. */
const ANGLES = [45, -45, 135, -135, 0, 180, 90, -90].map((d) => (d * Math.PI) / 180);
/** Clear space kept around each word, px. */
const PAD = 2;
/** How much further out the next round of spots is tried, px. */
const STEP = 7;

const overlap = (a: Box, b: Box, pad = PAD) =>
  a.x < b.x + b.w + pad && b.x < a.x + a.w + pad && a.y < b.y + b.h + pad && b.y < a.y + a.h + pad;

/** Does a segment pass through a box (grown by `pad`)? Liang–Barsky clipping. */
export function segHitsBox(s: Seg, b: Box, pad = PAD): boolean {
  const x0 = b.x - pad;
  const y0 = b.y - pad;
  const x1 = b.x + b.w + pad;
  const y1 = b.y + b.h + pad;
  const dx = s.x2 - s.x1;
  const dy = s.y2 - s.y1;
  let t0 = 0;
  let t1 = 1;
  for (const [p, q] of [
    [-dx, s.x1 - x0],
    [dx, x1 - s.x1],
    [-dy, s.y1 - y0],
    [dy, y1 - s.y1],
  ]) {
    if (p === 0) {
      if (q < 0) return false;
    } else {
      const t = q / p;
      if (p < 0) t0 = Math.max(t0, t);
      else t1 = Math.min(t1, t);
      if (t0 > t1) return false;
    }
  }
  return true;
}

/** Does a circle's outline cross a box? (Not when the box sits wholly inside or wholly outside it.) */
export function discCrossesBox(d: Disc, b: Box): boolean {
  if (d.r <= 0) return false;
  const nx = Math.min(Math.max(d.X, b.x), b.x + b.w);
  const ny = Math.min(Math.max(d.Y, b.y), b.y + b.h);
  const near = Math.hypot(nx - d.X, ny - d.Y);
  const far = Math.max(...[b.x, b.x + b.w].flatMap((x) => [b.y, b.y + b.h].map((y) => Math.hypot(x - d.X, y - d.Y))));
  return near <= d.r && d.r <= far;
}

/** The box of a w × h word set `gap` px outside the circle `at`, in the direction `a`. */
export function boxAround(at: Disc, a: number, w: number, h: number, gap: number): Box {
  const R = at.r + gap;
  const px = at.X + R * Math.cos(a);
  const py = at.Y + R * Math.sin(a);
  const c = Math.cos(a);
  const s = Math.sin(a);
  // Right of the mark: the word starts there; left: it ends there; above or below: centred.
  const x = c > 0.3 ? px : c < -0.3 ? px - w : px - w / 2;
  const y = s > 0.3 ? py : s < -0.3 ? py - h : py - h / 2;
  return { x, y, w, h };
}

/** The words and arrows already on the plate, and the circles words should rather not cross. */
export class LabelSpace {
  readonly boxes: Box[] = [];
  readonly segs: Seg[] = [];
  readonly discs: Disc[] = [];
  constructor(readonly bounds: Box) {}

  /** Keep words off an arrow: its points in order, as drawn. */
  addPath(pts: { X: number; Y: number }[]): void {
    for (let i = 1; i < pts.length; i++) this.segs.push({ x1: pts[i - 1].X, y1: pts[i - 1].Y, x2: pts[i].X, y2: pts[i].Y });
  }
  addDisc(d: Disc): void {
    if (d.r > 0) this.discs.push(d);
  }
  /** A word already drawn where this space can't choose (it still keeps others off it). */
  addBox(b: Box): void {
    this.boxes.push(b);
  }

  /** Is this box free of other words, arrows and the plate's edge? */
  free(b: Box): boolean {
    const B = this.bounds;
    if (b.x < B.x || b.y < B.y || b.x + b.w > B.x + B.w || b.y + b.h > B.y + B.h) return false;
    return !this.boxes.some((o) => overlap(o, b)) && !this.segs.some((s) => segHitsBox(s, b));
  }

  /**
   * The spot for a w × h word beside the mark `at`: the first of `angles` (default: lower right,
   * then round) that is free, the one crossing the fewest circles when several are. Nothing free
   * right beside the mark: the same round a step (`STEP` px) further out, twice. The word is then
   * taken (`boxes`). null when no spot is free.
   */
  place(at: Disc, w: number, h: number, gap = 3, angles: number[] = ANGLES): Box | null {
    let best: Box | null = null;
    for (let ring = 0; ring < 3 && !best; ring++) {
      let cost = Infinity;
      for (const a of angles) {
        const b = boxAround(at, a, w, h, gap + ring * STEP);
        if (!this.free(b)) continue;
        const k = this.discs.filter((d) => !(d.X === at.X && d.Y === at.Y && d.r === at.r) && discCrossesBox(d, b)).length;
        if (k < cost) {
          cost = k;
          best = b;
          if (k === 0) break;
        }
      }
    }
    if (best) this.boxes.push(best);
    return best;
  }
}

/** The angles to try first for a word beside an arrow's middle: its normal pointing up the plate, then the other side. */
export function besideArrow(from: { X: number; Y: number }, to: { X: number; Y: number }): number[] {
  const ang = Math.atan2(to.Y - from.Y, to.X - from.X);
  let n = ang - Math.PI / 2;
  if (Math.sin(n) > 0 || (Math.sin(n) === 0 && Math.cos(n) < 0)) n += Math.PI;
  return [n, n + Math.PI, ...ANGLES];
}
