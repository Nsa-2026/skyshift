// Sky geometry for the Live search map: an equal-area Mollweide projection of RA/Dec, zone boxes as
// sampled sky polygons (so their edges curve as they should), and the ecliptic. Pure functions.
//
// Map convention: RA increases to the LEFT, as on the sky seen from inside. The map is centred on
// centreRa (default 180°, 12h), so its left and right edges are RA 0h and no ecliptic zone is cut.
import type { SkyZone, ZoneState } from "./model";

const D2R = Math.PI / 180;
const R2D = 180 / Math.PI;
const SQRT2 = Math.SQRT2;
/** Half-width and half-height of the Mollweide ellipse on the unit sphere. */
export const MOLL_HALF_W = 2 * SQRT2;
export const MOLL_HALF_H = SQRT2;
export const OBLIQUITY_DEG = 23.4393;

const wrap360 = (x: number) => ((x % 360) + 360) % 360;
/** Into (-180, 180]. */
const wrap180 = (x: number) => {
  const w = wrap360(x + 180) - 180;
  return w === -180 ? 180 : w;
};

/** Mollweide auxiliary angle θ: 2θ + sin 2θ = π sin φ (Newton). */
export function mollTheta(lat: number): number {
  if (Math.abs(lat) >= Math.PI / 2 - 1e-12) return Math.sign(lat) * (Math.PI / 2);
  const target = Math.PI * Math.sin(lat);
  let t = lat;
  for (let i = 0; i < 60; i++) {
    const f = 2 * t + Math.sin(2 * t) - target;
    const d = 2 + 2 * Math.cos(2 * t);
    if (d < 1e-12) break;
    const step = f / d;
    t -= step;
    if (Math.abs(step) < 1e-13) break;
  }
  return t;
}

/** Unit-sphere Mollweide: lon, lat in radians → x in [-2√2, 2√2], y in [-√2, √2] (y up). */
export function mollweide(lon: number, lat: number): { x: number; y: number } {
  const t = mollTheta(lat);
  return { x: ((2 * SQRT2) / Math.PI) * lon * Math.cos(t), y: SQRT2 * Math.sin(t) };
}

/** Inverse of mollweide(); null outside the ellipse. */
export function mollweideInverse(x: number, y: number): { lon: number; lat: number } | null {
  if ((x / MOLL_HALF_W) ** 2 + (y / MOLL_HALF_H) ** 2 > 1 + 1e-12) return null;
  const t = Math.asin(Math.max(-1, Math.min(1, y / SQRT2)));
  const lat = Math.asin(Math.max(-1, Math.min(1, (2 * t + Math.sin(2 * t)) / Math.PI)));
  const c = Math.cos(t);
  const lon = c < 1e-12 ? 0 : (Math.PI * x) / (2 * SQRT2 * c);
  if (Math.abs(lon) > Math.PI + 1e-9) return null;
  return { lon, lat };
}

// ---- the map frame (SVG user units, y down) ---------------------------------------------------
export interface MapFrame {
  /** Width of the ellipse in user units; height is half of it. */
  width: number;
  /** Margin around the ellipse in user units (strokes). */
  pad: number;
  centreRa: number;
}
export const DEFAULT_FRAME: MapFrame = { width: 1000, pad: 3, centreRa: 180 };

export const frameSize = (f: MapFrame) => ({ w: f.width + 2 * f.pad, h: f.width / 2 + 2 * f.pad });

/** Map longitude (degrees, + to the left... i.e. east) for an RA: λ = centreRa − RA, wrapped into (-180, 180]. */
export function lambdaOf(ra: number, frame: MapFrame = DEFAULT_FRAME): number {
  return wrap180(frame.centreRa - ra);
}

/** Map longitude λ (degrees, already chosen) and Dec → SVG point. */
export function lambdaToXY(lambda: number, dec: number, frame: MapFrame = DEFAULT_FRAME): [number, number] {
  const s = frame.width / (2 * MOLL_HALF_W);
  const p = mollweide(lambda * D2R, dec * D2R);
  // λ > 0 (RA below the centre) goes right; RA increasing goes left.
  return [frame.pad + frame.width / 2 + p.x * s, frame.pad + frame.width / 4 - p.y * s];
}

/** RA/Dec → SVG point. A point exactly on the map edge goes to the right edge. */
export function skyToXY(ra: number, dec: number, frame: MapFrame = DEFAULT_FRAME): [number, number] {
  return lambdaToXY(lambdaOf(ra, frame), dec, frame);
}

/** SVG point → RA/Dec, or null off the ellipse. */
export function xyToSky(x: number, y: number, frame: MapFrame = DEFAULT_FRAME): { ra: number; dec: number } | null {
  const s = frame.width / (2 * MOLL_HALF_W);
  const p = mollweideInverse((x - frame.pad - frame.width / 2) / s, -(y - frame.pad - frame.width / 4) / s);
  if (!p) return null;
  return { ra: wrap360(frame.centreRa - p.lon * R2D), dec: p.lat * R2D };
}

// ---- zone polygons on the sky -----------------------------------------------------------------
export type Ring = Array<[number, number]>;

export interface ZonePolygon {
  name: string;
  state: ZoneState;
  zone: SkyZone;
  /** Closed rings of [RA, Dec] in degrees (first point not repeated). RA in [0, 360]. */
  rings: Ring[];
}

function sampled(a: number, b: number, step: number): number[] {
  const n = Math.max(1, Math.ceil(Math.abs(b - a) / step - 1e-9));
  return Array.from({ length: n + 1 }, (_, i) => a + ((b - a) * i) / n);
}

/** RA intervals of a box, [lo, hi] with lo < hi in [0, 360]; a box through RA 0 gives two. */
export function raPieces(ra: [number, number], splitAtRa: number | null = null): Array<[number, number]> {
  const [r0, r1] = ra;
  let pieces: Array<[number, number]> = r1 > r0 ? [[r0, r1]] : r1 === 0 ? [[r0, 360]] : [[r0, 360], [0, r1]];
  if (splitAtRa !== null) {
    const e = wrap360(splitAtRa);
    pieces = pieces.flatMap(([lo, hi]) =>
      e > lo && e < hi ? ([[lo, e], [e, hi]] as Array<[number, number]>) : [[lo, hi] as [number, number]],
    );
    // A box through RA 0 isn't cut at 0 unless the map edge is there.
    if (e !== 0 && r1 < r0 && r1 !== 0) {
      const a = pieces.findIndex(([, hi]) => hi === 360);
      const b = pieces.findIndex(([lo]) => lo === 0);
      if (a >= 0 && b >= 0) {
        const [lo] = pieces[a];
        const [, hi] = pieces[b];
        pieces = pieces.filter((_, i) => i !== a && i !== b).concat([[lo, hi + 360]]);
      }
    }
  }
  return pieces.filter(([lo, hi]) => hi > lo);
}

/**
 * A box as a closed ring on the sky with edges sampled every stepDeg: bottom edge (RA up), the high-RA
 * edge (Dec up), top edge (RA down), the low-RA edge (Dec down). RA may run past 360 within one piece.
 */
export function boxRing(lo: number, hi: number, d0: number, d1: number, stepDeg = 1): Ring {
  const ras = sampled(lo, hi, stepDeg);
  const decs = sampled(d0, d1, stepDeg);
  const ring: Ring = [];
  for (const r of ras) ring.push([r, d0]);
  for (const d of decs.slice(1)) ring.push([hi, d]);
  for (const r of ras.slice(0, -1).reverse()) ring.push([r, d1]);
  for (const d of decs.slice(1, -1).reverse()) ring.push([lo, d]);
  return ring;
}

export interface PolygonOptions {
  /** Edge sampling in degrees (default 1). */
  stepDeg?: number;
  /** Cut boxes where they cross this RA (a map's edge). Default null: one ring per box (two if it wraps through 0). */
  splitAtRa?: number | null;
}

/**
 * Zones as sky polygons with sampled edges, for any map or globe (the Sky page's "Tonight's search" layer).
 * RA values stay in [0, 360] (a ring of a box through RA 0 is split there unless splitAtRa says otherwise).
 */
export function zonesToPolygons(zones: SkyZone[], opts: PolygonOptions = {}): ZonePolygon[] {
  const step = opts.stepDeg ?? 1;
  return zones.map((z) => ({
    name: z.name,
    state: z.state,
    zone: z,
    rings: raPieces(z.ra, opts.splitAtRa ?? null).map(([lo, hi]) =>
      boxRing(lo, hi, z.dec[0], z.dec[1], step).map(([r, d]) => [r === 360 ? 360 : wrap360(r), d] as [number, number]),
    ),
  }));
}

/**
 * One RA piece of a box (lo < hi, not crossing the map edge) → SVG path. λ is taken continuously from the
 * piece's middle so an edge at the map's seam lands on the correct side.
 */
export function piecePath(lo: number, hi: number, d0: number, d1: number, frame: MapFrame = DEFAULT_FRAME, stepDeg = 1): string {
  const mid = (lo + hi) / 2;
  const lMid = lambdaOf(mid, frame);
  const pts = boxRing(lo, hi, d0, d1, stepDeg).map(([r, d]) => lambdaToXY(clampL(lMid - (r - mid)), d, frame));
  return pathOf(pts, true);
}

const clampL = (l: number) => Math.max(-180, Math.min(180, l));

export function pathOf(pts: Array<[number, number]>, close: boolean): string {
  return pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`).join("") + (close ? "Z" : "");
}

/** SVG path of a zone on the map (several sub-paths if the map edge cuts it). */
export function zonePath(z: Pick<SkyZone, "ra" | "dec">, frame: MapFrame = DEFAULT_FRAME, stepDeg = 1): string {
  return raPieces(z.ra, wrap360(frame.centreRa + 180))
    .map(([lo, hi]) => piecePath(lo, hi, z.dec[0], z.dec[1], frame, stepDeg))
    .join("");
}

/** The four corners of the largest piece of a zone in SVG units: [low-RA bottom, high-RA bottom, high-RA top, low-RA top]. */
export function zoneCorners(z: Pick<SkyZone, "ra" | "dec">, frame: MapFrame = DEFAULT_FRAME): Array<[number, number]> {
  const [lo, hi] = raPieces(z.ra, wrap360(frame.centreRa + 180)).sort((a, b) => b[1] - b[0] - (a[1] - a[0]))[0];
  const mid = (lo + hi) / 2;
  const lMid = lambdaOf(mid, frame);
  const at = (r: number, d: number) => lambdaToXY(clampL(lMid - (r - mid)), d, frame);
  return [at(lo, z.dec[0]), at(hi, z.dec[0]), at(hi, z.dec[1]), at(lo, z.dec[1])];
}

// ---- lines on the sky -------------------------------------------------------------------------
/** Ecliptic longitude → RA/Dec (degrees), latitude 0. */
export function eclipticToEq(lon: number, obliquity = OBLIQUITY_DEG): { ra: number; dec: number } {
  const l = lon * D2R;
  const e = obliquity * D2R;
  return { ra: wrap360(Math.atan2(Math.sin(l) * Math.cos(e), Math.cos(l)) * R2D), dec: Math.asin(Math.sin(l) * Math.sin(e)) * R2D };
}

/** A polyline on the sky → SVG path, broken where it jumps across the map edge. */
export function skyLinePath(points: Array<{ ra: number; dec: number }>, frame: MapFrame = DEFAULT_FRAME): string {
  const runs: Array<Array<[number, number]>> = [];
  let run: Array<[number, number]> = [];
  let prev: number | null = null;
  for (const p of points) {
    const l = lambdaOf(p.ra, frame);
    if (prev !== null && Math.abs(l - prev) > 180) {
      // Close the run on its own edge, start the next on the other.
      const edge = prev > 0 ? 180 : -180;
      run.push(lambdaToXY(edge, p.dec, frame));
      runs.push(run);
      run = [lambdaToXY(-edge, p.dec, frame)];
    }
    run.push(lambdaToXY(l, p.dec, frame));
    prev = l;
  }
  if (run.length) runs.push(run);
  return runs.filter((r) => r.length > 1).map((r) => pathOf(r, false)).join("");
}

export function eclipticPath(frame: MapFrame = DEFAULT_FRAME, stepDeg = 1): string {
  // Longitude 360 would wrap back to RA 0: end just short of it, on the other edge.
  const pts = sampled(0, 360, stepDeg).map((l) => (l >= 360 ? { ra: 360 - 1e-9, dec: 0 } : eclipticToEq(l)));
  return skyLinePath(pts, frame);
}

/** A parallel (constant Dec) across the whole map: a straight line in Mollweide. */
export function parallelPath(dec: number, frame: MapFrame = DEFAULT_FRAME): string {
  return pathOf([lambdaToXY(-180, dec, frame), lambdaToXY(180, dec, frame)], false);
}

/** A meridian (constant RA) from pole to pole. */
export function meridianPath(ra: number, frame: MapFrame = DEFAULT_FRAME, stepDeg = 2): string {
  const l = lambdaOf(ra, frame);
  return pathOf(sampled(-90, 90, stepDeg).map((d) => lambdaToXY(l, d, frame)), false);
}

/** The outline of the whole sky. */
export function outlinePath(frame: MapFrame = DEFAULT_FRAME): string {
  const right = sampled(-90, 90, 2).map((d) => lambdaToXY(180, d, frame));
  const left = sampled(90, -90, 2).map((d) => lambdaToXY(-180, d, frame));
  return pathOf([...right, ...left.slice(1, -1)], true);
}
