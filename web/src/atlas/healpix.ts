// HEALPix, NESTED scheme only: what the sky heat map needs (Górski et al. 2005, ApJ 622, 759;
// the formulas follow healpix_base.cc). Pure: no DOM, no Aladin. Degrees in, degrees out.
// Orders up to 13 (nside 8192) keep every index inside 32-bit bit operations.

const D2R = Math.PI / 180;
const R2D = 180 / Math.PI;
const HALF_PI = Math.PI / 2;
const TWO_THIRDS = 2 / 3;
/** Ring number of each base face's southern corner (in units of nside), and its longitude index. */
const JRLL = [2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4];
const JPLL = [1, 3, 5, 7, 0, 2, 4, 6, 1, 3, 5, 7];

export const MAX_ORDER = 13;

export function nsideOf(order: number): number {
  if (!Number.isInteger(order) || order < 0 || order > MAX_ORDER) throw new RangeError(`HEALPix order ${order} is outside 0–${MAX_ORDER}`);
  return 2 ** order;
}

export function npixOf(order: number): number {
  return 12 * nsideOf(order) ** 2;
}

/** Mean cell side in degrees: √(4π / npix). Order 3 ≈ 7.3°, order 5 ≈ 1.8°. */
export function cellSideDeg(order: number): number {
  return Math.sqrt((4 * Math.PI) / npixOf(order)) * R2D;
}

/** Interleave the bits of v (< 2^16) with zeros: b → 0b0b. */
function spread(v: number): number {
  let r = 0;
  for (let i = 0; i < 16; i++) if (v & (1 << i)) r |= 1 << (2 * i);
  return r >>> 0;
}

/** Inverse of spread: keep the even bits. */
function compress(v: number): number {
  let r = 0;
  for (let i = 0; i < 16; i++) if (v & (1 << (2 * i))) r |= 1 << i;
  return r;
}

function xyf2nest(ix: number, iy: number, face: number, order: number): number {
  return face * 4 ** order + spread(ix) + 2 * spread(iy);
}

/** Face number and the cell's (x, y) inside it. */
export function nest2xyf(pix: number, order: number): { ix: number; iy: number; face: number } {
  const npface = 4 ** order;
  const face = Math.floor(pix / npface);
  const p = pix - face * npface;
  return { ix: compress(p), iy: compress(Math.floor(p / 2)), face };
}

function wrap360(ra: number): number {
  const r = ra % 360;
  return r < 0 ? r + 360 : r;
}

/** Cell index (NESTED) holding the position. */
export function ang2pixNest(order: number, raDeg: number, decDeg: number): number {
  const ns = nsideOf(order);
  const dec = Math.max(-90, Math.min(90, decDeg)) * D2R;
  const z = Math.sin(dec);
  const za = Math.abs(z);
  let tt = ((wrap360(raDeg) * D2R) / HALF_PI) % 4; // in [0, 4)
  if (tt < 0) tt += 4;

  if (za <= TWO_THIRDS) {
    // Equatorial belt: two families of edge lines.
    const temp1 = ns * (0.5 + tt);
    const temp2 = ns * (z * 0.75);
    const jp = Math.floor(temp1 - temp2); // ascending edge line
    const jm = Math.floor(temp1 + temp2); // descending edge line
    const ifp = Math.floor(jp / ns);
    const ifm = Math.floor(jm / ns);
    const face = ifp === ifm ? ifp | 4 : ifp < ifm ? ifp : ifm + 8;
    const ix = jm % ns;
    const iy = ns - (jp % ns) - 1;
    return xyf2nest(ix, iy, face, order);
  }
  // Polar caps.
  const ntt = Math.min(3, Math.floor(tt));
  const tp = tt - ntt;
  const tmp = za < 0.99 ? ns * Math.sqrt(3 * (1 - za)) : (ns * Math.cos(dec)) / Math.sqrt((1 + za) / 3);
  const jp = Math.min(ns - 1, Math.floor(tp * tmp));
  const jm = Math.min(ns - 1, Math.floor((1 - tp) * tmp));
  return z >= 0 ? xyf2nest(ns - jm - 1, ns - jp - 1, ntt, order) : xyf2nest(jp, jm, ntt + 8, order);
}

/** A point inside face `face` at fractional face coordinates (x, y) in [0, 1], as [ra, dec]. */
function xyf2radec(x: number, y: number, face: number): [number, number] {
  const jr = JRLL[face] - x - y;
  let nr: number;
  let z: number;
  let sth: number | null = null;
  if (jr < 1) {
    nr = jr;
    const tmp = (nr * nr) / 3;
    z = 1 - tmp;
    if (z > 0.99) sth = Math.sqrt(tmp * (2 - tmp));
  } else if (jr > 3) {
    nr = 4 - jr;
    const tmp = (nr * nr) / 3;
    z = tmp - 1;
    if (z < -0.99) sth = Math.sqrt(tmp * (2 - tmp));
  } else {
    nr = 1;
    z = ((2 - jr) * 2) / 3;
  }
  let t = JPLL[face] * nr + x - y;
  if (t < 0) t += 8;
  if (t >= 8) t -= 8;
  const phi = nr < 1e-15 ? 0 : (0.5 * HALF_PI * t) / nr;
  const dec = sth != null ? Math.atan2(z, sth) : Math.asin(Math.max(-1, Math.min(1, z)));
  return [wrap360(phi * R2D), dec * R2D];
}

/** Centre of a cell as [ra, dec]. */
export function pix2angNest(order: number, pix: number): [number, number] {
  const ns = nsideOf(order);
  const { ix, iy, face } = nest2xyf(pix, order);
  return xyf2radec((ix + 0.5) / ns, (iy + 0.5) / ns, face);
}

/**
 * The cell's outline as [ra, dec] points, `step` points per edge (4 × step in all), going round
 * the cell from its east corner. step 1 gives the four corners.
 */
export function boundariesNest(order: number, pix: number, step = 1): [number, number][] {
  const ns = nsideOf(order);
  const { ix, iy, face } = nest2xyf(pix, order);
  const s = Math.max(1, Math.floor(step));
  const dc = 0.5 / ns;
  const xc = (ix + 0.5) / ns;
  const yc = (iy + 0.5) / ns;
  const d = 1 / (s * ns);
  const out: [number, number][] = new Array(4 * s);
  for (let i = 0; i < s; i++) {
    out[i] = xyf2radec(xc + dc - i * d, yc + dc, face);
    out[i + s] = xyf2radec(xc - dc, yc + dc - i * d, face);
    out[i + 2 * s] = xyf2radec(xc - dc + i * d, yc - dc, face);
    out[i + 3 * s] = xyf2radec(xc + dc, yc - dc + i * d, face);
  }
  return out;
}

/** The cell one order coarser that holds this cell (NESTED: four children per parent). */
export function parentNest(pix: number, levels = 1): number {
  return Math.floor(pix / 4 ** levels);
}
