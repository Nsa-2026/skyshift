// A dot SkyShift circled in only one photo of the hours clock: a steady source just under the cut in
// the others, a cosmic ray, something that moved, or a one-photo flash? The same rule as core's
// skyshift_core.pixelcheck.one_photo (agent "pixel", 16 Nov; scratchpad reports/core-api-onephoto.md),
// measured here on the frames the browser already has: forced aperture photometry at the dot's sky
// place in every other photo of the same detector, an empirical error from random apertures, and the
// dot's size against the stars of similar S/N in its own photo. Never mixes detectors. Pure: no DOM.
//
// What differs from core, and why the verdicts still hold:
// - No FLAGS in the browser: NASA's TRANSIENT hits are already masked and filled by the server's
//   resampling (stack.resample), so `transient_px` is unknown (null) and only the size test finds a hit.
// - Frames are resampled bilinearly onto the field's grid. That widens every source by the same
//   kernel, so a cosmic ray stays narrower than the stars measured on the same frame (the test is a
//   ratio); on the 60′ field (binned 2×) the size test is off: a 12″ pixel can't tell a hit from a star.
// - The random apertures come from a seeded generator of our own, not numpy's: the error is the same
//   estimate (1.4826 × MAD of 200 apertures), not the same digits.
import type { Det, MeasuredDetection, SkyPos } from "../api/types";
import { skyToPixel } from "../app/astro";

/** Native SPHEREx pixel (″) and µJy per MJy/sr in one such pixel (core pixelcheck). */
export const NATIVE_ARCSEC = 6.152;
export const UJY_PER_MJYSR = 889.7;
/** core photometry: an r = 2 px aperture holds 96.8% of a star. */
export const APERTURE_CORRECTION = 1.033;
const R_PX = 2;
const ANNULUS_PX: [number, number] = [4, 7];
const NOISE_N = 200;
const NOISE_MIN_SEP_PX = 8;
const NOISE_EDGE_PX = 7;
const SHAPE_R_PX = 2.5;
const SHAPE_BKG_PX: [number, number] = [3.5, 5.5];
const SIZE_SNR_FACTOR = 2;
const SIZE_MIN_STARS = 8;
// The rule's thresholds (core pixelcheck, module constants).
export const STEADY_SNR = 3;
export const STEADY_RATIO = 0.3;
export const UNCLEAR_LIMIT = 0.5;
export const SHARP_RATIO = 0.7;
export const MIN_OTHERS = 2;
/** Detections closer than this (″, half the PSF FWHM) in two photos are one source seen twice. */
export const SAME_SPOT_ARCSEC = 3.75;
/** `moved`: a dot within reach of motion in the photo before or after (core-api-onephoto.md). */
export const MOVE_RATE_ARCSEC_H = 150;
export const MOVE_SLACK_ARCSEC = 6;
/** A session: photos under 10 minutes apart (contracts/api.md visit rule). */
const SESSION_GAP_DAYS = 10 / 1440;

export type Verdict = "too_few" | "steady" | "unclear" | "cosmic_ray" | "moved" | "flash";

export interface OnePhoto {
  verdict: Verdict;
  /** The pipeline linked it to a mover's track (or a known body): it moves; nothing measured here. */
  track: boolean;
  n_other: number;
  n_seen: number;
  own_flux_ujy: number;
  other_flux_ujy: number;
  other_err_ujy: number;
  other_snr: number;
  ratio: number;
  limit_ratio: number;
  size_ratio: number;
  moved: boolean | null;
  /** `moved`: the nearest new dot in the photo before or after (″ away, minutes later; negative: before). */
  step?: { arcsec: number; minutes: number };
}

/** One photo of one detector, on the field's grid, MJy/sr (NaN: no data). */
export interface OPFrame {
  id: string;
  det: Det;
  mjd: number;
  vals: Float32Array;
  /** The frame's own centre (frames that follow a mover), else the field's. */
  centre: SkyPos;
}
export interface OPGrid {
  size: [number, number];
  pixel_arcsec: number;
}

// ---- measurements --------------------------------------------------------------------------------
/** A small seeded generator (mulberry32): the same frames give the same error estimate. */
function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** k-th smallest of a[0..n) (quickselect, reorders a). */
function select(a: Float64Array, n: number, k: number): number {
  let lo = 0;
  let hi = n - 1;
  while (lo < hi) {
    const p = a[(lo + hi) >> 1];
    let i = lo;
    let j = hi;
    while (i <= j) {
      while (a[i] < p) i++;
      while (a[j] > p) j--;
      if (i <= j) {
        const t = a[i];
        a[i] = a[j];
        a[j] = t;
        i++;
        j--;
      }
    }
    if (k <= j) hi = j;
    else if (k >= i) lo = i;
    else return a[k];
  }
  return a[k];
}
/** Median of a[0..n) (reorders a). */
function medianOf(a: Float64Array, n: number): number {
  if (!n) return NaN;
  const m = n >> 1;
  if (n % 2) return select(a, n, m);
  const hi = select(a, n, m);
  let lo = -Infinity;
  for (let i = 0; i < m; i++) if (a[i] > lo) lo = a[i];
  return (lo + hi) / 2;
}
function median(a: number[]): number {
  return medianOf(Float64Array.from(a), a.length);
}
/** The annulus' pixel values, reused (an annulus of 7 px holds under 200 pixels). */
let ringBuf = new Float64Array(512);

/** Background-subtracted aperture sum (MJy/sr summed) at (x, y): the good pixels' share inside an r px
 *  circle (5 × 5 sub-pixels on its edge, as sep), minus the annulus median × the good area. NaN
 *  without 8 good annulus pixels. Pixel i's centre is at x = i. */
export function apertureSum(v: Float32Array, nx: number, ny: number, x: number, y: number, r: number, ann: [number, number]): number {
  const h = Math.ceil(Math.max(r, ann[1]));
  const ix = Math.round(x);
  const iy = Math.round(y);
  let sum = 0;
  let area = 0;
  if (ringBuf.length < (2 * h + 1) ** 2) ringBuf = new Float64Array((2 * h + 1) ** 2);
  const ring = ringBuf;
  let nr = 0;
  const a0 = ann[0] * ann[0];
  const a1 = ann[1] * ann[1];
  for (let j = Math.max(0, iy - h); j <= Math.min(ny - 1, iy + h); j++) {
    for (let i = Math.max(0, ix - h); i <= Math.min(nx - 1, ix + h); i++) {
      const val = v[j * nx + i];
      if (val !== val) continue;
      const dx = i - x;
      const dy = j - y;
      const rr = dx * dx + dy * dy;
      if (rr >= a0 && rr <= a1) ring[nr++] = val;
      if (rr > (r + 0.71) * (r + 0.71)) continue;
      // the pixel's square against the circle (squared distances: Math.hypot is slow)
      const ax = Math.abs(dx);
      const ay = Math.abs(dy);
      const nx0 = Math.max(ax - 0.5, 0);
      const ny0 = Math.max(ay - 0.5, 0);
      if (nx0 * nx0 + ny0 * ny0 >= r * r) continue;
      let frac = 1;
      if ((ax + 0.5) * (ax + 0.5) + (ay + 0.5) * (ay + 0.5) > r * r) {
        let k = 0;
        for (let sy = 0; sy < 5; sy++)
          for (let sx = 0; sx < 5; sx++) {
            const px = dx - 0.4 + 0.2 * sx;
            const py = dy - 0.4 + 0.2 * sy;
            if (px * px + py * py <= r * r) k++;
          }
        frac = k / 25;
      }
      sum += val * frac;
      area += frac;
    }
  }
  if (nr < 8) return NaN;
  return sum - medianOf(ring, nr) * area;
}

/** core forced_flux: µJy at each place, with one empirical error for the frame (1.4826 × MAD of the
 *  same measurement at random places ≥ minSep from every target and edge px inside). `scale` is the
 *  native pixels per frame pixel (1 at 20′, 0.5 on the 60′ field binned 2×): radii shrink with it, µJy
 *  per pixel grow. */
export function forcedFlux(
  v: Float32Array,
  nx: number,
  ny: number,
  targets: { x: number; y: number }[],
  scale = 1,
  seed = 0,
): { flux: number[]; err: number; masked: boolean[] } {
  const r = R_PX * scale;
  const ann: [number, number] = [ANNULUS_PX[0] * scale, ANNULUS_PX[1] * scale];
  const conv = (UJY_PER_MJYSR / (scale * scale)) * APERTURE_CORRECTION;
  const flux = targets.map((t) => apertureSum(v, nx, ny, t.x, t.y, r, ann) * conv);
  const masked = targets.map((t) => {
    const ix = Math.round(t.x);
    const iy = Math.round(t.y);
    if (ix < 0 || iy < 0 || ix >= nx || iy >= ny) return true;
    for (let j = iy - 1; j <= iy + 1; j++)
      for (let i = ix - 1; i <= ix + 1; i++) {
        if (i < 0 || j < 0 || i >= nx || j >= ny) continue;
        const val = v[j * nx + i];
        if (val !== val) return true;
      }
    return false;
  });
  let err = NaN;
  const e = NOISE_EDGE_PX * scale;
  const sep = NOISE_MIN_SEP_PX * scale;
  if (nx > 2 * e + 1 && ny > 2 * e + 1) {
    const rand = rng(seed + 1);
    const got: number[] = [];
    let kept = 0;
    // The targets by cell of `sep` px: each random place looks at the 3 × 3 cells around it only.
    const cw = Math.ceil(nx / sep) + 1;
    const cells = new Map<number, { x: number; y: number }[]>();
    for (const t of targets) {
      if (!Number.isFinite(t.x) || !Number.isFinite(t.y)) continue;
      const c = Math.floor(t.y / sep) * cw + Math.floor(t.x / sep);
      const l = cells.get(c);
      if (l) l.push(t);
      else cells.set(c, [t]);
    }
    const near = (x: number, y: number) => {
      const cx = Math.floor(x / sep);
      const cy = Math.floor(y / sep);
      for (let j = cy - 1; j <= cy + 1; j++)
        for (let i = cx - 1; i <= cx + 1; i++)
          for (const t of cells.get(j * cw + i) ?? []) if ((x - t.x) ** 2 + (y - t.y) ** 2 < sep * sep) return true;
      return false;
    };
    for (let k = 0; k < 4 * NOISE_N && kept < NOISE_N; k++) {
      const x = e + rand() * (nx - 1 - 2 * e);
      const y = e + rand() * (ny - 1 - 2 * e);
      if (near(x, y)) continue;
      kept++;
      const c = v[Math.round(y) * nx + Math.round(x)];
      if (c !== c) continue;
      const f = apertureSum(v, nx, ny, x, y, r, ann) * conv;
      if (Number.isFinite(f)) got.push(f);
    }
    if (got.length >= 20) {
      const m = median(got);
      err = 1.4826 * median(got.map((g) => Math.abs(g - m)));
    }
  }
  return { flux, err, masked };
}

/** core moment_shape's size: √(a·b) (px) from the second moments of the good pixels within r of
 *  (x, y) above the annulus median, negatives clipped. NaN near the edge or without signal. */
export function momentSize(v: Float32Array, nx: number, ny: number, x: number, y: number): number {
  const h = Math.ceil(SHAPE_BKG_PX[1]);
  const ix = Math.round(x);
  const iy = Math.round(y);
  if (ix - h < 0 || iy - h < 0 || ix + h + 1 > nx || iy + h + 1 > ny) return NaN;
  const ring: number[] = [];
  for (let j = -h; j <= h; j++)
    for (let i = -h; i <= h; i++) {
      const val = v[(iy + j) * nx + ix + i];
      if (val !== val) continue;
      const dx = i - (x - ix);
      const dy = j - (y - iy);
      const rr = dx * dx + dy * dy;
      if (rr >= SHAPE_BKG_PX[0] ** 2 && rr <= SHAPE_BKG_PX[1] ** 2) ring.push(val);
    }
  if (ring.length < 5) return NaN;
  const med = median(ring);
  let tot = 0;
  let cxx = 0;
  let cyy = 0;
  let cxy = 0;
  for (let j = -h; j <= h; j++)
    for (let i = -h; i <= h; i++) {
      const val = v[(iy + j) * nx + ix + i];
      if (val !== val) continue;
      const dx = i - (x - ix);
      const dy = j - (y - iy);
      if (dx * dx + dy * dy > SHAPE_R_PX * SHAPE_R_PX) continue;
      const w = Math.max(val - med, 0);
      tot += w;
      cxx += w * dx * dx;
      cyy += w * dy * dy;
      cxy += w * dx * dy;
    }
  if (!(tot > 0)) return NaN;
  cxx /= tot;
  cyy /= tot;
  cxy /= tot;
  const tr = cxx + cyy;
  const det = cxx * cyy - cxy * cxy;
  const disc = Math.sqrt(Math.max((tr * tr) / 4 - det, 0));
  const l1 = tr / 2 + disc;
  const l2 = tr / 2 - disc;
  return Math.sqrt(Math.sqrt(Math.max(l1, 0)) * Math.sqrt(Math.max(l2, 0)));
}

/** core size_ratio: size ÷ the median size of the stars whose S/N is within ×/÷ 2 of snr (NaN under 8). */
export function sizeRatio(size: number, snr: number, stars: { size: number; snr: number }[]): number {
  const ok = stars.filter((s) => Number.isFinite(s.size) && Number.isFinite(s.snr) && s.snr >= snr / SIZE_SNR_FACTOR && s.snr <= snr * SIZE_SNR_FACTOR);
  if (ok.length < SIZE_MIN_STARS || !Number.isFinite(size)) return NaN;
  return size / median(ok.map((s) => s.size));
}

/** Inverse-variance mean: [flux, err, n used] (finite, positive errors only). */
export function combine(flux: number[], err: number[]): [number, number, number] {
  let sw = 0;
  let swf = 0;
  let n = 0;
  flux.forEach((f, i) => {
    const e = err[i];
    if (!Number.isFinite(f) || !Number.isFinite(e) || !(e > 0)) return;
    const w = 1 / (e * e);
    sw += w;
    swf += w * f;
    n++;
  });
  return n ? [swf / sw, 1 / Math.sqrt(sw), n] : [NaN, NaN, 0];
}

/** core one_photo: the verdict, first match: too_few, steady, unclear, cosmic_ray, moved, flash. */
export function onePhoto(
  ownFlux: number,
  otherFlux: number[],
  otherErr: number[],
  opts: { sizeRatio?: number; transientPx?: number | null; moved?: boolean | null } = {},
): OnePhoto {
  const [f, e, n] = combine(otherFlux, otherErr);
  const nSeen = otherFlux.filter((x, i) => Number.isFinite(x) && otherErr[i] > 0 && x >= 3 * otherErr[i]).length;
  const sr = opts.sizeRatio ?? NaN;
  const res: OnePhoto = {
    verdict: "flash", track: false, n_other: n, n_seen: nSeen, own_flux_ujy: ownFlux,
    other_flux_ujy: f, other_err_ujy: e, other_snr: n ? f / e : NaN,
    ratio: n && ownFlux > 0 ? f / ownFlux : NaN,
    limit_ratio: n && ownFlux > 0 ? (Math.max(f, 0) + 3 * e) / ownFlux : NaN,
    size_ratio: sr, moved: opts.moved ?? null,
  };
  if (n < MIN_OTHERS || !(ownFlux > 0)) res.verdict = "too_few";
  else if (res.other_snr >= STEADY_SNR && res.ratio >= STEADY_RATIO) res.verdict = "steady";
  else if (res.limit_ratio >= UNCLEAR_LIMIT) res.verdict = "unclear";
  else if ((Number.isFinite(sr) && sr < SHARP_RATIO) || (opts.transientPx ?? 0) >= 1) res.verdict = "cosmic_ray";
  else if (opts.moved) res.verdict = "moved";
  return res;
}

// ---- the dots of one photo ----------------------------------------------------------------------
const sep = (a: SkyPos, b: SkyPos) => {
  const x = (a.ra - b.ra) * Math.cos((b.dec * Math.PI) / 180);
  const y = a.dec - b.dec;
  return Math.sqrt(x * x + y * y) * 3600;
};

/** `near(p, r)`: is any of `list` within r ″ (≤ `cell`) of p? Looks at the 3 × 3 sky cells around p
 *  only (a field is under 2°: one cos δ for all; across RA 0 = 360 a match can be missed). */
function cellIndex(list: SkyPos[], cell = SAME_SPOT_ARCSEC): (p: SkyPos, r: number) => boolean {
  const cosd = Math.cos(((list[0]?.dec ?? 0) * Math.PI) / 180);
  const key = (i: number, j: number) => `${i}|${j}`;
  const ci = (p: SkyPos) => Math.floor((p.ra * cosd * 3600) / cell);
  const cj = (p: SkyPos) => Math.floor((p.dec * 3600) / cell);
  const cells = new Map<string, SkyPos[]>();
  for (const p of list) {
    const k = key(ci(p), cj(p));
    const l = cells.get(k);
    if (l) l.push(p);
    else cells.set(k, [p]);
  }
  return (p, r) => {
    const i0 = ci(p);
    const j0 = cj(p);
    for (let j = j0 - 1; j <= j0 + 1; j++)
      for (let i = i0 - 1; i <= i0 + 1; i++) for (const o of cells.get(key(i, j)) ?? []) if (sep(o, p) < r) return true;
    return false;
  };
}

/** A detection's key, the same after allwave.ts moves it onto a combined moment. */
export const detKey = (d: Pick<MeasuredDetection, "ra" | "dec" | "flux_ujy" | "snr">) => `${d.ra}|${d.dec}|${d.flux_ujy}|${d.snr}`;

const MOVERS = new Set(["known", "mover", "suspect"]);

/**
 * The verdict for every dot of photo `own` (detections of one detector) that no other photo of that
 * detector shows within 3.75″ (nor as the same pipeline source or Gaia star). `frames`: every photo of
 * own's detector here, own included, on one grid. `dets`: their detections. Dots on a mover's track
 * (or a known body) are `moved` without measuring; artefacts are left out.
 */
export function onePhotoChecks(grid: OPGrid, frames: OPFrame[], own: OPFrame, dets: MeasuredDetection[]): Map<string, OnePhoto> {
  const it = onePhotoSteps(grid, frames, own, dets);
  let r = it.next();
  while (!r.done) r = it.next();
  return r.value;
}

/** onePhotoChecks a photo at a time, waiting for `pause()` between them (a millisecond or two each at
 *  20′), so a visit of 24 photos never holds the page; `pause` may throw to give up. */
export async function onePhotoChecksAsync(
  grid: OPGrid,
  frames: OPFrame[],
  own: OPFrame,
  dets: MeasuredDetection[],
  pause: () => Promise<void> = () => new Promise((ok) => setTimeout(ok, 0)),
): Promise<Map<string, OnePhoto>> {
  const it = onePhotoSteps(grid, frames, own, dets);
  let r = it.next();
  while (!r.done) {
    await pause();
    r = it.next();
  }
  return r.value;
}

function* onePhotoSteps(grid: OPGrid, frames: OPFrame[], own: OPFrame, dets: MeasuredDetection[]): Generator<void, Map<string, OnePhoto>, void> {
  const out = new Map<string, OnePhoto>();
  const same = frames.filter((f) => f.det === own.det).sort((a, b) => a.mjd - b.mjd);
  const at = new Map<string, MeasuredDetection[]>();
  for (const d of dets) {
    const l = at.get(d.epoch_id);
    if (l) l.push(d);
    else at.set(d.epoch_id, [d]);
  }
  const mine = (at.get(own.id) ?? []).filter((d) => d.class !== "artifact" && d.class !== "stack");
  const elsewhere = same.filter((f) => f.id !== own.id).flatMap((f) => at.get(f.id) ?? []);
  const sources = new Set(elsewhere.map((o) => o.source_id).filter((x) => x != null));
  const gaiaIds = new Set(elsewhere.map((o) => o.gaia_id).filter((x) => x != null));
  const near = cellIndex(elsewhere);
  const lone: MeasuredDetection[] = [];
  for (const d of mine) {
    if (MOVERS.has(d.class) || d.track_id) {
      out.set(detKey(d), { ...onePhoto(NaN, [], []), verdict: "moved", track: true });
      continue;
    }
    const seen = (d.source_id != null && sources.has(d.source_id)) || (d.gaia_id != null && gaiaIds.has(d.gaia_id)) || near(d, SAME_SPOT_ARCSEC);
    if (!seen) lone.push(d);
  }
  if (!lone.length) return out;
  const [nx, ny] = grid.size;
  const scale = NATIVE_ARCSEC / grid.pixel_arcsec;
  const native = Math.abs(scale - 1) < 0.1;
  const px = (f: OPFrame, p: SkyPos) => skyToPixel(p, grid, f.centre);
  // One forced pass per photo: every lone dot's place, the noise kept clear of all of them.
  const forced = new Map<string, ReturnType<typeof forcedFlux>>();
  for (const f of same) {
    if (f.vals.length !== nx * ny) continue;
    yield;
    forced.set(f.id, forcedFlux(f.vals, nx, ny, lone.map((d) => px(f, d)), scale));
  }
  yield;
  // The stars of similar S/N in its own photo, for the size test (native sampling only).
  const stars = native
    ? (at.get(own.id) ?? []).filter((d) => d.class === "star").map((d) => {
        const p = px(own, d);
        return { size: momentSize(own.vals, nx, ny, p.x, p.y), snr: d.snr };
      })
    : [];
  const i0 = same.findIndex((f) => f.id === own.id);
  const neighbours = [same[i0 - 1], same[i0 + 1]].filter((f): f is OPFrame => !!f && Math.abs(f.mjd - own.mjd) < SESSION_GAP_DAYS);
  // The neighbours' dots that are new (at no place of this photo's dots): where a mover went.
  const atMine = cellIndex(mine);
  const fresh = new Map(neighbours.map((f) => [f.id, (at.get(f.id) ?? []).filter((o) => !atMine(o, SAME_SPOT_ARCSEC))]));
  lone.forEach((d, k) => {
    const mineF = forced.get(own.id);
    const ownFlux = mineF && !mineF.masked[k] ? mineF.flux[k] : NaN;
    const oFlux: number[] = [];
    const oErr: number[] = [];
    for (const f of same) {
      if (f.id === own.id) continue;
      const m = forced.get(f.id);
      if (!m || m.masked[k] || !Number.isFinite(m.flux[k])) continue;
      oFlux.push(m.flux[k]);
      oErr.push(m.err);
    }
    let sr = NaN;
    if (native) {
      const p = px(own, d);
      sr = sizeRatio(momentSize(own.vals, nx, ny, p.x, p.y), d.snr, stars);
    }
    // A new dot within reach of motion in the photo before or after, at no place of this photo's dots.
    let moved: boolean | null = neighbours.length ? false : null;
    let step: { arcsec: number; minutes: number } | null = null;
    for (const f of neighbours) {
      const reach = MOVE_RATE_ARCSEC_H * Math.abs(f.mjd - own.mjd) * 24 + MOVE_SLACK_ARCSEC;
      for (const o of fresh.get(f.id) ?? []) {
        const s = sep(o, d);
        if (s <= SAME_SPOT_ARCSEC || s > reach) continue;
        moved = true;
        // The nearest such dot, for the words ("12″ away in the photo 2 minutes later").
        if (!step || s < step.arcsec) step = { arcsec: s, minutes: (f.mjd - own.mjd) * 1440 };
      }
    }
    const v = onePhoto(ownFlux, oFlux, oErr, { sizeRatio: sr, transientPx: null, moved });
    if (step) v.step = step;
    out.set(detKey(d), v);
  });
  return out;
}

// ---- the words ----------------------------------------------------------------------------------
/** µJy in plain digits: whole numbers from 10 up, one decimal below. */
const uj = (x: number) => (Math.abs(x) >= 10 ? Math.round(x).toLocaleString("en-GB") : x.toFixed(1));
const photos = (n: number) => `${n} photo${n === 1 ? "" : "s"}`;

/** What the hint means, in one group a visitor can count: the panel's rows and the circle's badge. */
export type OnePhotoKind = "stays" | "cosmic" | "moving" | "once" | "unknown";
export function onePhotoKind(v: OnePhoto): OnePhotoKind {
  if (v.verdict === "steady") return "stays";
  if (v.verdict === "cosmic_ray") return "cosmic";
  if (v.verdict === "moved") return "moving";
  if (v.verdict === "flash") return "once";
  return "unknown";
}

/**
 * What the other photos say about a dot circled in this photo only, in one or two plain sentences with
 * the numbers. `loading`: more photos of the visit are still on their way (the hint may change).
 */
export function onePhotoSay(v: OnePhoto, loading = false): string {
  if (v.track) return "SkyShift linked it to a moving object: see its arrow.";
  const n = v.n_other;
  const others = `the other ${photos(n)} of this visit`;
  const own = `${uj(v.own_flux_ujy)} µJy here`;
  const limit = `at most ${uj(Math.max(v.other_flux_ujy, 0) + 3 * v.other_err_ujy)} µJy there, against ${own}`;
  const more = loading ? " More photos of this visit are loading, so this may change." : "";
  switch (v.verdict) {
    case "steady": {
      const pct = Math.round(100 * v.ratio);
      const faint = pct < 90;
      return (
        `Circled in this photo only. At the same spot ${others} show ${faint ? "fainter " : ""}light too ` +
        `(${uj(v.other_flux_ujy)} ± ${uj(v.other_err_ujy)} µJy on average, ${pct}% of its ${own}): ` +
        `${faint ? "too faint to circle each time, but it is there" : "it is there each time, only not circled"}. A real object that stays put.`
      );
    }
    case "cosmic_ray":
      return (
        `Circled in this photo only, and nothing shows at its spot in ${others} (${limit}). ` +
        `It is also sharper than a star (${v.size_ratio.toFixed(2)} × a star's width): most likely a cosmic ray, ` +
        "a particle hitting the camera, not something in the sky."
      );
    case "moved": {
      const s = v.step;
      const where = s
        ? `${Math.round(s.arcsec)}″ away in the photo ${Math.abs(s.minutes).toFixed(1)} minutes ${s.minutes < 0 ? "earlier" : "later"}`
        : "a little further along in the photo before or after";
      return `Circled in this photo only, nothing shows at its spot in ${others}, and something similar sits ${where}: it may be moving. Blink to check.`;
    }
    case "flash":
      return (
        `Circled in this photo only, and nothing shows at its spot in ${others} (${limit}). ` +
        "It is as wide as a star, so not a particle hit: something that shone in this photo only, such as a fast mover already gone from the next one, or a glint." +
        more
      );
    case "unclear":
      return (
        `Circled in this photo only. At its spot ${others} are too noisy to tell whether it is there too ` +
        `(${uj(v.other_flux_ujy)} ± ${uj(v.other_err_ujy)} µJy on average, against ${own}).` +
        more
      );
    case "too_few":
      // also when its own light can't be measured: then the other photos are there, but there is nothing to compare
      if (n >= MIN_OTHERS)
        return `Circled in this photo only, but its own light can't be measured here (flagged pixels, or nothing above the background), so ${others} can't be compared with it.`;
      return (
        `Circled in this photo only. ${n ? `Only ${photos(n)} of this visit with data at its spot ${n === 1 ? "is" : "are"}` : "No other photo of this visit with data at its spot is"} ` +
        `loaded: too few to tell whether it is there in them.${more}`
      );
  }
}

/** The panel's rows: how many of each kind, and what that kind means. */
export const KIND_WORDS: Record<OnePhotoKind, (n: number) => string> = {
  stays: (n) => `${n} ${n === 1 ? "stays" : "stay"} put: fainter light shows at the same spot in the other photos, too faint to circle each time. Real objects.`,
  cosmic: (n) => `${n} ${n === 1 ? "is" : "are"} most likely ${n === 1 ? "a cosmic ray" : "cosmic rays"}: nothing there in the other photos, and sharper than a star. A particle hitting the camera, not something in the sky.`,
  moving: (n) => `${n} may be moving: nothing at the spot in the other photos, but something similar a little further along in the photo before or after. Blink to check.`,
  once: (n) => `${n} shone once: nothing there in the other photos, yet as wide as a star. A fast mover already gone, or a glint.`,
  unknown: (n) => `${n} can't be told: too few photos with data there, too much noise, or flagged pixels of its own.`,
};
export const KIND_ORDER: OnePhotoKind[] = ["stays", "cosmic", "moving", "once", "unknown"];

/** Counts by kind of the one-photo hints given so far (dots the pipeline linked to a mover left out). */
export function onePhotoCounts(list: Iterable<OnePhoto>): Map<OnePhotoKind, number> {
  const out = new Map<OnePhotoKind, number>();
  for (const v of list) if (!v.track) out.set(onePhotoKind(v), (out.get(onePhotoKind(v)) ?? 0) + 1);
  return out;
}
