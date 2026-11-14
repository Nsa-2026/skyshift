// Small spherical geometry for the sky page: frames (ICRS ↔ galactic), map projections for the
// fallback map (Aitoff for the whole sky, gnomonic for a close look), offsets along a position
// angle. Degrees everywhere. Pure.
import type { SkyPos } from "../api/types";

const D2R = Math.PI / 180;
const R2D = 180 / Math.PI;

export function wrap360(a: number): number {
  const r = a % 360;
  return r < 0 ? r + 360 : r;
}

/** Wrap to (−180, 180]. */
export function wrap180(a: number): number {
  const r = wrap360(a);
  return r > 180 ? r - 360 : r;
}

/** ICRS → galactic rotation (Hipparcos, ESA 1997, vol. 1 §1.5.3). */
const EQ2GAL = [
  [-0.0548755604162154, -0.873437090234885, -0.4838350155487132],
  [0.4941094278755837, -0.4448296299600112, 0.7469822444972189],
  [-0.8676661490190047, -0.1980763734312015, 0.4559837761750669],
];

function toVec(lon: number, lat: number): [number, number, number] {
  const cl = Math.cos(lat * D2R);
  return [cl * Math.cos(lon * D2R), cl * Math.sin(lon * D2R), Math.sin(lat * D2R)];
}

function fromVec(v: [number, number, number]): [number, number] {
  const lon = Math.atan2(v[1], v[0]) * R2D;
  const lat = Math.asin(Math.max(-1, Math.min(1, v[2]))) * R2D;
  return [wrap360(lon), lat];
}

export function toGalactic(p: SkyPos): { l: number; b: number } {
  const v = toVec(p.ra, p.dec);
  const g = EQ2GAL.map((r) => r[0] * v[0] + r[1] * v[1] + r[2] * v[2]) as [number, number, number];
  const [l, b] = fromVec(g);
  return { l, b };
}

export function fromGalactic(l: number, b: number): SkyPos {
  const g = toVec(l, b);
  // The matrix is orthogonal: its transpose is its inverse.
  const v = [0, 1, 2].map((i) => EQ2GAL[0][i] * g[0] + EQ2GAL[1][i] * g[1] + EQ2GAL[2][i] * g[2]) as [number, number, number];
  const [ra, dec] = fromVec(v);
  return { ra, dec };
}

/** Longitude and latitude of a position in the chosen frame. */
export function lonLat(p: SkyPos, galactic: boolean): [number, number] {
  if (!galactic) return [p.ra, p.dec];
  const g = toGalactic(p);
  return [g.l, g.b];
}

/** Angular separation in degrees (haversine; exact for small angles). */
export function sepDeg(a: SkyPos, b: SkyPos): number {
  const d1 = a.dec * D2R;
  const d2 = b.dec * D2R;
  const s = Math.sin((d2 - d1) / 2) ** 2 + Math.cos(d1) * Math.cos(d2) * Math.sin(((b.ra - a.ra) * D2R) / 2) ** 2;
  return 2 * Math.asin(Math.min(1, Math.sqrt(s))) * R2D;
}

/** Position angle (east of north) from a to b, degrees in [0, 360). */
export function paDeg(a: SkyPos, b: SkyPos): number {
  const d1 = a.dec * D2R;
  const d2 = b.dec * D2R;
  const dra = (b.ra - a.ra) * D2R;
  const y = Math.sin(dra) * Math.cos(d2);
  const x = Math.cos(d1) * Math.sin(d2) - Math.sin(d1) * Math.cos(d2) * Math.cos(dra);
  return wrap360(Math.atan2(y, x) * R2D);
}

/** The point `distDeg` away from p along position angle `pa` (east of north). */
export function offsetPa(p: SkyPos, pa: number, distDeg: number): SkyPos {
  const d = distDeg * D2R;
  const t = pa * D2R;
  const dec1 = p.dec * D2R;
  const dec2 = Math.asin(Math.sin(dec1) * Math.cos(d) + Math.cos(dec1) * Math.sin(d) * Math.cos(t));
  const ra2 = p.ra * D2R + Math.atan2(Math.sin(t) * Math.sin(d) * Math.cos(dec1), Math.cos(d) - Math.sin(dec1) * Math.sin(dec2));
  return { ra: wrap360(ra2 * R2D), dec: dec2 * R2D };
}

/**
 * Aitoff projection with east to the left (the sky as seen from inside), centred on lon0.
 * Returns x in [−2, 2] and y in [−1, 1] (units of π/2), so a 2:1 box holds the whole sky.
 */
export function aitoff(lon: number, lat: number, lon0 = 0): { x: number; y: number } {
  const lam = -wrap180(lon - lon0) * D2R;
  const phi = lat * D2R;
  const alpha = Math.acos(Math.cos(phi) * Math.cos(lam / 2));
  const sinc = alpha < 1e-12 ? 1 : Math.sin(alpha) / alpha;
  const x = (2 * Math.cos(phi) * Math.sin(lam / 2)) / sinc;
  const y = Math.sin(phi) / sinc;
  return { x: x / (Math.PI / 2), y: y / (Math.PI / 2) };
}

/**
 * Gnomonic (tangent-plane) projection about `center`, east to the left, north up, in degrees on
 * the tangent plane. null for points on the far hemisphere.
 */
export function gnomonic(p: SkyPos, center: SkyPos): { x: number; y: number } | null {
  const ra = p.ra * D2R;
  const dec = p.dec * D2R;
  const ra0 = center.ra * D2R;
  const dec0 = center.dec * D2R;
  const cosc = Math.sin(dec0) * Math.sin(dec) + Math.cos(dec0) * Math.cos(dec) * Math.cos(ra - ra0);
  if (cosc <= 1e-6) return null;
  const xi = (Math.cos(dec) * Math.sin(ra - ra0)) / cosc;
  const eta = (Math.cos(dec0) * Math.sin(dec) - Math.sin(dec0) * Math.cos(dec) * Math.cos(ra - ra0)) / cosc;
  return { x: -xi * R2D, y: eta * R2D };
}

/** Inverse of gnomonic: tangent-plane degrees (east left) back to the sky. */
export function gnomonicInverse(x: number, y: number, center: SkyPos): SkyPos {
  const xi = -x * D2R;
  const eta = y * D2R;
  const ra0 = center.ra * D2R;
  const dec0 = center.dec * D2R;
  const rho = Math.hypot(xi, eta);
  if (rho === 0) return { ...center };
  const c = Math.atan(rho);
  const dec = Math.asin(Math.cos(c) * Math.sin(dec0) + (eta * Math.sin(c) * Math.cos(dec0)) / rho);
  const ra = ra0 + Math.atan2(xi * Math.sin(c), rho * Math.cos(dec0) * Math.cos(c) - eta * Math.sin(dec0) * Math.sin(c));
  return { ra: wrap360(ra * R2D), dec: dec * R2D };
}

/**
 * Inverse of aitoff(): the sky position under map point (x, y) (units of π/2, east left), or null
 * outside the map's ellipse. Newton's method from a good guess; a few steps reach 1e-9.
 */
export function aitoffInverse(x: number, y: number, lon0 = 0): { lon: number; lat: number } | null {
  if ((x / 2) ** 2 + y ** 2 > 1 + 1e-9) return null;
  let lam = -x * 90; // degrees, east left → minus
  let phi = y * 90;
  for (let i = 0; i < 40; i++) {
    const p = aitoff(lon0 + lam, phi, lon0);
    const fx = p.x - x;
    const fy = p.y - y;
    if (Math.abs(fx) < 1e-10 && Math.abs(fy) < 1e-10) break;
    const h = 1e-6;
    const px = aitoff(lon0 + lam + h, phi, lon0);
    const py = aitoff(lon0 + lam, phi + h, lon0);
    const a = (px.x - p.x) / h;
    const b = (py.x - p.x) / h;
    const c = (px.y - p.y) / h;
    const d = (py.y - p.y) / h;
    const det = a * d - b * c;
    if (Math.abs(det) < 1e-14) break;
    lam -= (d * fx - b * fy) / det;
    phi -= (-c * fx + a * fy) / det;
    lam = Math.max(-179.999, Math.min(179.999, lam));
    phi = Math.max(-89.999, Math.min(89.999, phi));
  }
  const back = aitoff(lon0 + lam, phi, lon0);
  if (Math.abs(back.x - x) > 1e-4 || Math.abs(back.y - y) > 1e-4) return null;
  return { lon: wrap360(lon0 + lam), lat: phi };
}

/** Mean position of a set of points (unit-vector mean), or null when empty. */
export function meanPosition(points: readonly SkyPos[]): SkyPos | null {
  if (!points.length) return null;
  let x = 0;
  let y = 0;
  let z = 0;
  for (const p of points) {
    const v = toVec(p.ra, p.dec);
    x += v[0];
    y += v[1];
    z += v[2];
  }
  const n = Math.hypot(x, y, z);
  if (n < 1e-12) return { ...points[0] };
  const [ra, dec] = fromVec([x / n, y / n, z / n]);
  return { ra, dec };
}
