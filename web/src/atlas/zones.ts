// The searched zones as the map draws them: one area per group of overlapping zones, drawn as one
// outline (the union of the zones' boxes) with one faint fill and one count badge beside it. The
// pilot zone lies inside the hunt zone, so the two make one area: no box drawn on top of another.
// Pure: no DOM, no Aladin. Degrees in, degrees out.
import type { SkyPos } from "../api/types";
import type { ZoneBox } from "./heat";
import { countText, zoneName } from "./kinds";
import { wrap360 } from "./sphere";

/** An RA/Dec box with RA unwrapped (ra1 > ra0, either may pass 360 or go below 0). */
export interface RaDecBox {
  ra0: number;
  ra1: number;
  dec0: number;
  dec1: number;
}

export interface ZoneArea {
  /** Stable id: the zone names joined. */
  id: string;
  /** Zone names, the busiest first. */
  zones: string[];
  /** Objects to check in all of them. */
  count: number;
  /** The zones' boxes, RA unwrapped near the first one's. */
  boxes: RaDecBox[];
  /** The union's outline: closed rings of [ra, dec] corners, RA unwrapped like `boxes`. */
  rings: [number, number][][];
  /** The union's bounds and their middle. */
  bounds: RaDecBox;
  center: SkyPos;
}

/** Shift a box's RA by whole turns so it starts within half a turn of `ref`. */
function unwrapNear(b: RaDecBox, ref: number): RaDecBox {
  const k = Math.round((ref - b.ra0) / 360);
  return { ra0: b.ra0 + 360 * k, ra1: b.ra1 + 360 * k, dec0: b.dec0, dec1: b.dec1 };
}

/** True when two boxes share some sky (RA modulo 360). Touching edges count. */
export function boxesOverlap(a: RaDecBox, b: RaDecBox): boolean {
  if (a.dec1 < b.dec0 || b.dec1 < a.dec0) return false;
  if (a.ra1 - a.ra0 >= 360 || b.ra1 - b.ra0 >= 360) return true;
  const u = unwrapNear(b, a.ra0);
  // b may also sit one turn either side of where unwrapNear put it.
  return [-360, 0, 360].some((k) => u.ra0 + k <= a.ra1 && a.ra0 <= u.ra1 + k);
}

/** True when the box holds the position (RA modulo 360). */
export function boxContains(b: RaDecBox, p: SkyPos): boolean {
  if (p.dec < b.dec0 || p.dec > b.dec1) return false;
  if (b.ra1 - b.ra0 >= 360) return true;
  let ra = wrap360(p.ra);
  while (ra < b.ra0) ra += 360;
  return ra <= b.ra1;
}

/**
 * Outline of the union of boxes on a plane (x = RA, y = Dec): closed rings of corners, the inside
 * on the left (anticlockwise with y up). The boxes are cut on a grid of all their edges, the grid
 * cells inside any box kept, and the kept cells' outer sides chained into rings. A hole (boxes
 * round an empty middle) comes out as its own, clockwise ring.
 */
export function unionRings(boxes: readonly RaDecBox[]): [number, number][][] {
  const ok = boxes.filter((b) => b.ra1 > b.ra0 && b.dec1 > b.dec0);
  if (!ok.length) return [];
  const xs = [...new Set(ok.flatMap((b) => [b.ra0, b.ra1]))].sort((a, b) => a - b);
  const ys = [...new Set(ok.flatMap((b) => [b.dec0, b.dec1]))].sort((a, b) => a - b);
  const nx = xs.length - 1;
  const ny = ys.length - 1;
  const inside = (i: number, j: number): boolean => {
    if (i < 0 || j < 0 || i >= nx || j >= ny) return false;
    const x = (xs[i] + xs[i + 1]) / 2;
    const y = (ys[j] + ys[j + 1]) / 2;
    return ok.some((b) => x > b.ra0 && x < b.ra1 && y > b.dec0 && y < b.dec1);
  };
  // Directed sides on grid corners (i, j), the cell on their left.
  const out = new Map<string, [number, number][]>();
  const key = (i: number, j: number) => `${i},${j}`;
  let edges = 0;
  const add = (a: [number, number], b: [number, number]) => {
    const k = key(a[0], a[1]);
    const list = out.get(k) ?? [];
    list.push(b);
    out.set(k, list);
    edges++;
  };
  for (let i = 0; i < nx; i++) {
    for (let j = 0; j < ny; j++) {
      if (!inside(i, j)) continue;
      if (!inside(i, j - 1)) add([i, j], [i + 1, j]);
      if (!inside(i + 1, j)) add([i + 1, j], [i + 1, j + 1]);
      if (!inside(i, j + 1)) add([i + 1, j + 1], [i, j + 1]);
      if (!inside(i - 1, j)) add([i, j + 1], [i, j]);
    }
  }
  const rings: [number, number][][] = [];
  while (edges > 0) {
    // Start anywhere a side is left; follow sides end to start until the ring closes.
    const [startKey, list] = [...out.entries()].find(([, l]) => l.length > 0)!;
    const start = startKey.split(",").map(Number) as [number, number];
    const corners: [number, number][] = [start];
    let at = start;
    let next = list.shift()!;
    edges--;
    while (next[0] !== start[0] || next[1] !== start[1]) {
      corners.push(next);
      at = next;
      const from = out.get(key(at[0], at[1]));
      if (!from?.length) break; // can't happen on a closed union; stop rather than loop
      next = from.shift()!;
      edges--;
    }
    // Keep only the turns: drop corners in the middle of a straight run.
    const n = corners.length;
    const turns = corners.filter((c, k) => {
      const p = corners[(k - 1 + n) % n];
      const q = corners[(k + 1) % n];
      return !((p[0] === c[0] && c[0] === q[0]) || (p[1] === c[1] && c[1] === q[1]));
    });
    rings.push(turns.map(([i, j]) => [xs[i], ys[j]]));
  }
  return rings;
}

/**
 * Group zones whose boxes overlap into areas, the busiest first. Each area's outline is the union
 * of its boxes, so overlapping zones draw one outline and one fill, never stacked boxes.
 */
export function zoneAreas(zones: readonly ZoneBox[]): ZoneArea[] {
  const sorted = [...zones].sort((a, b) => b.count - a.count || a.zone.localeCompare(b.zone));
  const parent = sorted.map((_, i) => i);
  const find = (i: number): number => (parent[i] === i ? i : (parent[i] = find(parent[i])));
  for (let i = 0; i < sorted.length; i++) {
    for (let j = i + 1; j < sorted.length; j++) {
      if (boxesOverlap(sorted[i], sorted[j])) parent[find(j)] = find(i);
    }
  }
  const groups = new Map<number, ZoneBox[]>();
  sorted.forEach((z, i) => {
    const g = groups.get(find(i)) ?? [];
    g.push(z);
    groups.set(find(i), g);
  });
  const areas: ZoneArea[] = [];
  for (const g of groups.values()) {
    const ref = g[0].ra0;
    const boxes = g.map((z) => unwrapNear(z, ref));
    const bounds: RaDecBox = {
      ra0: Math.min(...boxes.map((b) => b.ra0)),
      ra1: Math.max(...boxes.map((b) => b.ra1)),
      dec0: Math.min(...boxes.map((b) => b.dec0)),
      dec1: Math.max(...boxes.map((b) => b.dec1)),
    };
    areas.push({
      id: g.map((z) => z.zone).join("+"),
      zones: g.map((z) => z.zone),
      count: g.reduce((n, z) => n + z.count, 0),
      boxes,
      rings: unionRings(boxes),
      bounds,
      center: { ra: wrap360((bounds.ra0 + bounds.ra1) / 2), dec: (bounds.dec0 + bounds.dec1) / 2 },
    });
  }
  return areas.sort((a, b) => b.count - a.count);
}

/** The area holding the position, the busiest first, or null. */
export function areaAt(areas: readonly ZoneArea[], p: SkyPos): ZoneArea | null {
  return areas.find((a) => a.boxes.some((b) => boxContains(b, p))) ?? null;
}

/** A ring with points every `stepDeg` along each side, so it bends with the sky; RA wrapped. */
export function densifyRing(ring: readonly [number, number][], stepDeg = 0.5): [number, number][] {
  const pts: [number, number][] = [];
  const n = ring.length;
  for (let k = 0; k < n; k++) {
    const [x0, y0] = ring[k];
    const [x1, y1] = ring[(k + 1) % n];
    const steps = Math.max(1, Math.ceil(Math.max(Math.abs(x1 - x0), Math.abs(y1 - y0)) / stepDeg));
    for (let s = 0; s < steps; s++) pts.push([wrap360(x0 + ((x1 - x0) * s) / steps), y0 + ((y1 - y0) * s) / steps]);
  }
  return pts;
}

/** "Hunt zone"; "Hunt and pilot zones"; "Hunt, pilot and ecl13 zones". */
export function areaName(a: Pick<ZoneArea, "zones">): string {
  const names = a.zones.map((z) => zoneName(z).replace(/ zone$/, ""));
  if (names.length === 1) return `${names[0]} zone`;
  const rest = names.slice(1).map((s) => s.charAt(0).toLowerCase() + s.slice(1));
  const all = [names[0], ...rest];
  return `${all.slice(0, -1).join(", ")} and ${all[all.length - 1]} zones`;
}

/** The badge's text, longest first: "Hunt and pilot zones, 272 to check", then "272 to check". */
export function badgeTexts(a: Pick<ZoneArea, "zones" | "count">): string[] {
  const n = `${countText(a.count)} to check`;
  return [`${areaName(a)}, ${n}`, n];
}

/**
 * Field of view (across the map) that shows the whole area with some sky round it. `aspect` is the
 * map's width over its height: on a wide map the area's height sets the field.
 */
export function areaFov(a: Pick<ZoneArea, "bounds">, aspect = 1, min = 2, max = 45): number {
  const b = a.bounds;
  const mid = (b.dec0 + b.dec1) / 2;
  const w = (b.ra1 - b.ra0) * Math.max(0.05, Math.cos((mid * Math.PI) / 180));
  const h = (b.dec1 - b.dec0) * (Number.isFinite(aspect) && aspect > 0 ? aspect : 1);
  return Math.max(min, Math.min(max, Math.max(w * 1.4, h * 1.25)));
}
