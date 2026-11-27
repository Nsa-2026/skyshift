// SPHEREx now: where NASA's SPHEREx is probably looking at a moment, and where it looked in the
// newest public photos (the Sky map's "SPHEREx now" switch). Pure, except loadNow().
//
// NASA publishes no pointing schedule, so "now" is an estimate built from two measured things:
//  - The survey's pattern is fixed relative to the Sun. It is measured on the newest public photos
//    as (ecliptic longitude minus the Sun's, ecliptic latitude); turned with the Sun it says where
//    the survey works on any day, lopsided sides included (in autumn 2026 the photos west of the Sun
//    sat 81–96° from it, east of it 81–114°).
//  - SPHEREx flies a dawn–dusk polar orbit (NORAD 63182) and points within 35° of the point straight
//    overhead. That point comes from CelesTrak's mean elements, propagated with SGP4's secular terms.
// The photos themselves come out in weekly batches, 4–6 weeks after they are taken
// (scripts/precompute/spherex_now.py writes /data/spherex-now.json from NASA's bucket).
import type { SkyPos } from "../api/types";
import { wrap360 } from "./sphere";

const R = Math.PI / 180;
const OBLIQUITY = 23.4393 * R;

/** One planning period (half a survey week) as IRSA published it. */
export interface NowBatch {
  period: string;
  files: number;
  /** When the batch came out (S3 time of its first file), ISO UTC. */
  published: string;
  first_mjd: number | null;
  last_mjd: number | null;
}

/** CelesTrak mean elements of SPHEREx. */
export interface NowOrbit {
  epoch_mjd: number;
  inclination_deg: number;
  raan_deg: number;
  eccentricity: number;
  arg_perigee_deg: number;
  mean_anomaly_deg: number;
  mean_motion_rev_day: number;
  /** TLE first derivative of mean motion over 2, rev/day². */
  mean_motion_dot: number;
}

export interface NowFile {
  generated: string;
  newest_mjd: number;
  batches: NowBatch[];
  orbit: NowOrbit | null;
  /** [ra°, dec°, MJD-AVG] of sampled real photos (field centres), any order. */
  photos: [number, number, number][];
}

export interface NowModel {
  file: NowFile;
  /** Real photos sorted by time. */
  photos: { ra: number; dec: number; mjd: number }[];
  newest: number;
  /** The last week of photos as (lon − Sun's lon, lat): one week covers the whole pattern. */
  week: [number, number][];
  /** Cells of 2° × 2° in (lon − Sun's lon, lat) the photos covered, widened by one cell. */
  grid: Uint8Array;
}

/** What the map draws: the model and the moment shown (MJD). */
export interface NowLayer {
  model: NowModel;
  t: number;
}

export interface Visit {
  start: number;
  end: number;
}

// ---- time ----------------------------------------------------------------------------------
export const mjdOf = (ms: number): number => ms / 864e5 + 40587;
export const msOf = (mjd: number): number => (mjd - 40587) * 864e5;

// ---- the Sun and ecliptic coordinates -------------------------------------------------------
/** The Sun's ecliptic longitude, degrees (Astronomical Almanac low-precision, 0.01°). */
export function sunLon(mjd: number): number {
  const n = mjd - 51544.5;
  const g = (357.528 + 0.9856003 * n) * R;
  return wrap360(280.46 + 0.9856474 * n + 1.915 * Math.sin(g) + 0.02 * Math.sin(2 * g));
}

export function eclToEq(lon: number, lat: number): SkyPos {
  const cl = Math.cos(lat * R);
  const x = cl * Math.cos(lon * R);
  const y0 = cl * Math.sin(lon * R);
  const z0 = Math.sin(lat * R);
  const y = y0 * Math.cos(OBLIQUITY) - z0 * Math.sin(OBLIQUITY);
  const z = y0 * Math.sin(OBLIQUITY) + z0 * Math.cos(OBLIQUITY);
  return { ra: wrap360(Math.atan2(y, x) / R), dec: Math.asin(Math.max(-1, Math.min(1, z))) / R };
}

export function eqToEcl(p: SkyPos): { lon: number; lat: number } {
  const cd = Math.cos(p.dec * R);
  const x = cd * Math.cos(p.ra * R);
  const y0 = cd * Math.sin(p.ra * R);
  const z0 = Math.sin(p.dec * R);
  const y = y0 * Math.cos(OBLIQUITY) + z0 * Math.sin(OBLIQUITY);
  const z = -y0 * Math.sin(OBLIQUITY) + z0 * Math.cos(OBLIQUITY);
  return { lon: wrap360(Math.atan2(y, x) / R), lat: Math.asin(Math.max(-1, Math.min(1, z))) / R };
}

export const sunAt = (mjd: number): SkyPos => eclToEq(sunLon(mjd), 0);

// ---- the measured pattern -------------------------------------------------------------------
const cell = (dLon: number, lat: number): number =>
  Math.floor(wrap360(dLon) / 2) * 90 + Math.min(89, Math.max(0, Math.floor((lat + 90) / 2)));

export function buildModel(file: NowFile): NowModel {
  const photos = file.photos
    .filter((p) => Array.isArray(p) && p.length >= 3 && p.every(Number.isFinite) && Math.abs(p[1]) <= 90)
    .map(([ra, dec, mjd]) => ({ ra: wrap360(ra), dec, mjd }))
    .sort((a, b) => a.mjd - b.mjd);
  const newest = photos.length ? photos[photos.length - 1].mjd : file.newest_mjd;
  const grid = new Uint8Array(180 * 90);
  const week: [number, number][] = [];
  for (const p of photos) {
    const e = eqToEcl(p);
    const d = wrap360(e.lon - sunLon(p.mjd));
    if (p.mjd > newest - 7) week.push([d, e.lat]);
    const i = Math.floor(d / 2);
    const j = Math.min(89, Math.max(0, Math.floor((e.lat + 90) / 2)));
    for (let di = -1; di <= 1; di++) {
      for (let dj = -1; dj <= 1; dj++) {
        const jj = j + dj;
        if (jj >= 0 && jj < 90) grid[((i + di + 180) % 180) * 90 + jj] = 1;
      }
    }
  }
  return { file, photos, newest, week, grid };
}

/** Where the survey works on a day: the measured week, turned with the Sun. `step` thins it. */
export function patternAt(m: NowModel, mjd: number, step = 1): SkyPos[] {
  const sl = sunLon(mjd);
  const out: SkyPos[] = [];
  for (let i = 0; i < m.week.length; i += step) out.push(eclToEq(m.week[i][0] + sl, m.week[i][1]));
  return out;
}

/** One 2° cell of the band: its centre and its four corners (shared with the neighbouring cells). */
export interface BandCell {
  centre: SkyPos;
  corners: [SkyPos, SkyPos, SkyPos, SkyPos];
}

/** The band the survey works in on a day: the measured 2° cells, turned with the Sun. */
export function bandAt(m: NowModel, mjd: number): BandCell[] {
  const sl = sunLon(mjd);
  const verts = new Map<number, SkyPos>();
  const vert = (i: number, j: number): SkyPos => {
    const k = (i % 180) * 91 + j;
    let p = verts.get(k);
    if (!p) verts.set(k, (p = eclToEq(i * 2 + sl, j * 2 - 90)));
    return p;
  };
  const out: BandCell[] = [];
  for (let k = 0; k < m.grid.length; k++) {
    if (!m.grid[k]) continue;
    const i = Math.floor(k / 90);
    const j = k % 90;
    out.push({ centre: eclToEq(i * 2 + 1 + sl, j * 2 - 89), corners: [vert(i, j), vert(i + 1, j), vert(i + 1, j + 1), vert(i, j + 1)] });
  }
  return out;
}

/** True when the measured pattern, turned to this day, covers the position. */
export function covered(m: NowModel, pos: SkyPos, mjd: number): boolean {
  const e = eqToEcl(pos);
  return m.grid[cell(e.lon - sunLon(mjd), e.lat)] === 1;
}

/** Days the pattern covers the position, joined into visits (gaps up to 3 days), half-day steps. */
export function visitsAt(m: NowModel, pos: SkyPos, from: number, to: number): Visit[] {
  const e = eqToEcl(pos);
  const out: Visit[] = [];
  let cur: Visit | null = null;
  for (let t = from; t <= to; t += 0.5) {
    if (m.grid[cell(e.lon - sunLon(t), e.lat)] !== 1) continue;
    if (cur && t - cur.end <= 3) cur.end = t;
    else {
      cur = { start: t, end: t };
      out.push(cur);
    }
  }
  return out;
}

// ---- the orbit --------------------------------------------------------------------------------
// SGP4's constants (WGS-72) and its secular J2/J4 rates: enough for the point overhead to about a
// degree within days of the elements' epoch (the drag term is a few hundredths of a degree a week).
const XKE = 0.0743669161331734; // sqrt(GM) in Earth radii^1.5 per minute
const J2 = 0.001082616;
const J4 = -0.00000165597;

/** The point straight overhead SPHEREx (geocentric zenith) at a moment, in RA and Dec. */
export function overheadAt(o: NowOrbit, mjd: number): SkyPos {
  const inc = o.inclination_deg * R;
  const e = o.eccentricity;
  const cosi = Math.cos(inc);
  const cosi2 = cosi * cosi;
  const omeosq = 1 - e * e;
  const rteosq = Math.sqrt(omeosq);
  const noKozai = (o.mean_motion_rev_day * 2 * Math.PI) / 1440; // rad/min
  // Un-Kozai the mean motion as SGP4 does.
  const ak = Math.pow(XKE / noKozai, 2 / 3);
  const d1 = (0.75 * J2 * (3 * cosi2 - 1)) / (rteosq * omeosq);
  let del = d1 / (ak * ak);
  const adel = ak * (1 - del * del - del * (1 / 3 + (134 * del * del) / 81));
  del = d1 / (adel * adel);
  const no = noKozai / (1 + del);
  const ao = Math.pow(XKE / no, 2 / 3);
  const pinvsq = 1 / (ao * ao * omeosq * omeosq);
  const temp1 = 1.5 * J2 * pinvsq * no;
  const temp2 = 0.5 * temp1 * J2 * pinvsq;
  const temp3 = -0.46875 * J4 * pinvsq * pinvsq * no;
  const cosi4 = cosi2 * cosi2;
  const mdot = no + 0.5 * temp1 * rteosq * (3 * cosi2 - 1) + 0.0625 * temp2 * rteosq * (13 - 78 * cosi2 + 137 * cosi4);
  const argpdot = -0.5 * temp1 * (1 - 5 * cosi2) + 0.0625 * temp2 * (7 - 114 * cosi2 + 395 * cosi4) + temp3 * (3 - 36 * cosi2 + 49 * cosi4);
  const nodedot = -temp1 * cosi + (0.5 * temp2 * (4 - 19 * cosi2) + 2 * temp3 * (3 - 7 * cosi2)) * cosi;
  const tMin = (mjd - o.epoch_mjd) * 1440;
  const tDay = mjd - o.epoch_mjd;
  const M = o.mean_anomaly_deg * R + mdot * tMin + 2 * Math.PI * o.mean_motion_dot * tDay * tDay;
  const w = o.arg_perigee_deg * R + argpdot * tMin;
  const node = o.raan_deg * R + nodedot * tMin;
  const u = w + M + 2 * e * Math.sin(M); // argument of latitude, to first order in e
  const x = Math.cos(node) * Math.cos(u) - Math.sin(node) * Math.sin(u) * cosi;
  const y = Math.sin(node) * Math.cos(u) + Math.cos(node) * Math.sin(u) * cosi;
  const z = Math.sin(u) * Math.sin(inc);
  return { ra: wrap360(Math.atan2(y, x) / R), dec: Math.asin(Math.max(-1, Math.min(1, z))) / R };
}

/** The overhead point's path from `mjd` on, every `stepMin` minutes. */
export function overheadTrack(o: NowOrbit, mjd: number, minutes: number, stepMin = 1): SkyPos[] {
  const out: SkyPos[] = [];
  for (let k = 0; k <= minutes; k += stepMin) out.push(overheadAt(o, mjd + k / 1440));
  return out;
}

/** Minutes per orbit from the elements. */
export const orbitMinutes = (o: NowOrbit): number => 1440 / o.mean_motion_rev_day;

/** Points on a small circle of `radiusDeg` round a position. */
export function smallCircle(c: SkyPos, radiusDeg: number, n = 96): SkyPos[] {
  const cx = [Math.cos(c.dec * R) * Math.cos(c.ra * R), Math.cos(c.dec * R) * Math.sin(c.ra * R), Math.sin(c.dec * R)];
  const ref = Math.abs(cx[2]) < 0.9 ? [0, 0, 1] : [1, 0, 0];
  let u = [cx[1] * ref[2] - cx[2] * ref[1], cx[2] * ref[0] - cx[0] * ref[2], cx[0] * ref[1] - cx[1] * ref[0]];
  const l = Math.hypot(u[0], u[1], u[2]);
  u = u.map((v) => v / l);
  const w = [cx[1] * u[2] - cx[2] * u[1], cx[2] * u[0] - cx[0] * u[2], cx[0] * u[1] - cx[1] * u[0]];
  const cr = Math.cos(radiusDeg * R);
  const sr = Math.sin(radiusDeg * R);
  const out: SkyPos[] = [];
  for (let i = 0; i <= n; i++) {
    const t = (i / n) * 2 * Math.PI;
    const p = [0, 1, 2].map((k) => cx[k] * cr + sr * (Math.cos(t) * u[k] + Math.sin(t) * w[k]));
    out.push({ ra: wrap360(Math.atan2(p[1], p[0]) / R), dec: Math.asin(Math.max(-1, Math.min(1, p[2]))) / R });
  }
  return out;
}

/** The telescope points within this of the point overhead (local zenith angle under 35°). */
export const CONE_DEG = 35;

/** Typical days from a photo to its public release, from the newest batch (taken → published). */
export function releaseLagDays(m: NowModel): number {
  const b = [...m.file.batches].reverse().find((x) => x.last_mjd != null && Number.isFinite(Date.parse(x.published)));
  if (!b || b.last_mjd == null) return 38;
  return Math.max(0, mjdOf(Date.parse(b.published)) - b.last_mjd);
}

/** The newest batch: when it came out and which days its photos cover. */
export function newestBatch(m: NowModel): { published: number; first: number; last: number } | null {
  const ok = m.file.batches.filter((b) => b.first_mjd != null && b.last_mjd != null && Number.isFinite(Date.parse(b.published)));
  if (!ok.length) return null;
  const published = Math.max(...ok.map((b) => mjdOf(Date.parse(b.published))));
  const same = ok.filter((b) => Math.abs(mjdOf(Date.parse(b.published)) - published) < 2);
  return { published, first: Math.min(...same.map((b) => b.first_mjd!)), last: Math.max(...same.map((b) => b.last_mjd!)) };
}

/** Load /data/spherex-now.json (static first). Throws when it isn't there or is malformed. */
export async function loadNow(signal?: AbortSignal): Promise<NowModel> {
  const r = await fetch("/data/spherex-now.json", { signal, cache: "no-cache" });
  if (!r.ok) throw new Error(`spherex-now.json: HTTP ${r.status}`);
  const file = (await r.json()) as NowFile;
  if (!file || !Array.isArray(file.photos) || !Array.isArray(file.batches)) throw new Error("spherex-now.json: malformed");
  return buildModel(file);
}
