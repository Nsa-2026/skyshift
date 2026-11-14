// Real examples for the lessons, read from the static files (/data/<slug>/) the way the Blink
// page reads them. Pure helpers (sessions, no-data blobs, crops) are exported for tests.
import { epochsFor, loadFrameData, loadFrames, toMJySr } from "../api/client";
import type { Clock, Det, Epoch, FramesResponse, KnownObject, SkyPos } from "../api/types";
import { gaiaAt, sepArcsec, skyToPixel } from "../app/astro";
import { TARGETS, targetBySlug as target, type Target } from "../app/state";

export { TARGETS, target, type Target };

export interface FrameSet {
  frames: FramesResponse;
  jsonUrl: string;
}

const setCache = new Map<string, Promise<FrameSet>>();

/** The static frame set of a featured target for a clock and field (the API only if no static file). */
export function frameSet(slug: string, clock: Clock, fov: number, det: Det, signal?: AbortSignal): Promise<FrameSet> {
  const key = `${slug}|${clock}|${fov}|${det}`;
  let p = setCache.get(key);
  if (!p) {
    const t = target(slug);
    p = loadFrames({ ra: t?.ra ?? 0, dec: t?.dec ?? 0, clock, fov, det, slug }, signal).then((l) => ({
      frames: l.frames,
      jsonUrl: l.jsonUrl,
    }));
    p.catch(() => setCache.delete(key));
    setCache.set(key, p);
  }
  return p;
}

const valueCache = new Map<string, Promise<Float32Array>>();

/** One epoch's pixels in MJy/sr (NaN = no data). */
export function epochValues(set: FrameSet, e: Epoch, signal?: AbortSignal): Promise<Float32Array> {
  const key = new URL(e.data_url, set.jsonUrl).href;
  let p = valueCache.get(key);
  if (!p) {
    p = loadFrameData(e, set.jsonUrl, signal).then((raw) => toMJySr(raw, e));
    p.catch(() => valueCache.delete(key));
    valueCache.set(key, p);
  }
  return p;
}

/** A frame's centre: the epoch's own when the frames follow a moving object (3I/ATLAS). */
export const centreOf = (set: FrameSet, e: Epoch): SkyPos => e.center ?? set.frames.center;

export function toPixel(set: FrameSet, e: Epoch, pos: SkyPos): { x: number; y: number } {
  return skyToPixel(pos, set.frames, centreOf(set, e));
}

/** Split exposures into sessions: runs with gaps under 10 minutes (CLAUDE.md, visit rule). */
export function sessions(epochs: Epoch[]): Epoch[][] {
  const sorted = [...epochs].sort((a, b) => a.mjd_mid - b.mjd_mid);
  const out: Epoch[][] = [];
  for (const e of sorted) {
    const last = out[out.length - 1];
    if (last && (e.mjd_mid - last[last.length - 1].mjd_mid) * 1440 < 10) last.push(e);
    else out.push([e]);
  }
  return out;
}

export function knownIn(set: FrameSet, e: Epoch): KnownObject | undefined {
  return set.frames.known.find((k) => k.epoch_id === e.id);
}

/** Share of a frame's pixels with no data. */
export function noDataShare(values: Float32Array): number {
  let n = 0;
  for (let i = 0; i < values.length; i++) if (Number.isNaN(values[i])) n++;
  return values.length ? n / values.length : 1;
}

/**
 * Pick one exposure per session of a moving object, keeping those where its known position
 * falls well inside the frame. At most `max`, spread over the visit.
 */
export function moverExposures(set: FrameSet, det: Det, max = 4, margin = 12): Epoch[] {
  const [nx, ny] = set.frames.size;
  const inside = (e: Epoch) => {
    const k = knownIn(set, e);
    if (!k) return false;
    const p = toPixel(set, e, k);
    return p.x >= margin && p.y >= margin && p.x <= nx - 1 - margin && p.y <= ny - 1 - margin;
  };
  const picks = sessions(epochsFor(set.frames, det))
    .map((s) => s.find(inside))
    .filter((e): e is Epoch => !!e);
  if (picks.length <= max) return picks;
  // Spread: first, last and evenly between.
  return Array.from({ length: max }, (_, i) => picks[Math.round((i * (picks.length - 1)) / (max - 1))]);
}

/** Apparent speed of a known mover between two exposures, arcsec per hour. */
export function rateArcsecPerHour(set: FrameSet, a: Epoch, b: Epoch): number | null {
  const ka = knownIn(set, a);
  const kb = knownIn(set, b);
  const hours = (b.mjd_mid - a.mjd_mid) * 24;
  if (!ka || !kb || hours <= 0) return null;
  return sepArcsec(ka, kb) / hours;
}

/** A square crop around some points, padded and kept inside the frame. */
export function cropAround(
  pts: { x: number; y: number }[],
  size: [number, number],
  pad: number,
  min: number,
): { x: number; y: number; w: number; h: number } {
  const [nx, ny] = size;
  if (!pts.length) return { x: 0, y: 0, w: nx, h: ny };
  const xs = pts.map((p) => p.x);
  const ys = pts.map((p) => p.y);
  const cx = (Math.min(...xs) + Math.max(...xs)) / 2;
  const cy = (Math.min(...ys) + Math.max(...ys)) / 2;
  let s = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)) + 2 * pad;
  s = Math.round(Math.min(Math.max(s, min), nx, ny));
  const x = Math.round(Math.min(Math.max(cx - s / 2, 0), nx - s));
  const y = Math.round(Math.min(Math.max(cy - s / 2, 0), ny - s));
  return { x, y, w: s, h: s };
}

/** A centred square crop of side s. */
export function centreCrop(size: [number, number], s: number): { x: number; y: number; w: number; h: number } {
  const [nx, ny] = size;
  const w = Math.min(s, nx, ny);
  return { x: Math.floor((nx - w) / 2), y: Math.floor((ny - w) / 2), w, h: w };
}

export interface Blob {
  /** Pixel count. */
  area: number;
  /** Centre (frame pixels). */
  x: number;
  y: number;
  /** Touches the frame border. */
  edge: boolean;
  /** Pixel indices (for overlap tests). */
  pixels: number[];
}

/** Connected regions of no-data pixels (4-neighbour), largest first. */
export function noDataBlobs(values: Float32Array, nx: number, ny: number): Blob[] {
  const seen = new Uint8Array(values.length);
  const out: Blob[] = [];
  const stack: number[] = [];
  for (let start = 0; start < values.length; start++) {
    if (seen[start] || !Number.isNaN(values[start])) continue;
    const pixels: number[] = [];
    let sx = 0;
    let sy = 0;
    let edge = false;
    stack.push(start);
    seen[start] = 1;
    while (stack.length) {
      const i = stack.pop()!;
      pixels.push(i);
      const x = i % nx;
      const y = (i - x) / nx;
      sx += x;
      sy += y;
      if (x === 0 || y === 0 || x === nx - 1 || y === ny - 1) edge = true;
      const nb = [x > 0 ? i - 1 : -1, x < nx - 1 ? i + 1 : -1, y > 0 ? i - nx : -1, y < ny - 1 ? i + nx : -1];
      for (const j of nb) {
        if (j >= 0 && !seen[j] && Number.isNaN(values[j])) {
          seen[j] = 1;
          stack.push(j);
        }
      }
    }
    out.push({ area: pixels.length, x: sx / pixels.length, y: sy / pixels.length, edge, pixels });
  }
  return out.sort((a, b) => b.area - a.area);
}

export interface FakeSpots {
  /** Flagged in this photo only (cosmic rays, afterimages, ghosts …): circled. */
  oneOff: Blob[];
  /** Edges of the photo. */
  edges: Blob[];
  /** Blank in every photo shown: overflowing cores of bright stars. */
  steady: Blob[];
}

/**
 * Sort one frame's no-data regions into the kinds the fakes lesson names. A region is "steady"
 * when most of its pixels are blank in every other frame too; "edge" when it touches the border
 * and covers at least 1% of the frame; otherwise "one-off" if it has at least minArea pixels.
 */
export function classifyFakes(frame: Float32Array, others: Float32Array[], nx: number, ny: number, minArea = 4): FakeSpots {
  const res: FakeSpots = { oneOff: [], edges: [], steady: [] };
  for (const b of noDataBlobs(frame, nx, ny)) {
    if (b.edge && b.area >= 0.01 * nx * ny) {
      res.edges.push(b);
      continue;
    }
    const blankElsewhere = others.length
      ? b.pixels.filter((i) => others.every((o) => Number.isNaN(o[i]))).length / b.area
      : 0;
    if (blankElsewhere >= 0.5) res.steady.push(b);
    else if (b.area >= minArea && !b.edge) res.oneOff.push(b);
  }
  return res;
}

/** Gaia stars inside a crop at an epoch's time, brightest first. */
export function gaiaInCrop(
  set: FrameSet,
  e: Epoch,
  crop: { x: number; y: number; w: number; h: number },
  maxG = 19.5,
): { x: number; y: number; g: number | null; id: string }[] {
  return set.frames.gaia
    .filter((s) => s.g == null || s.g <= maxG)
    .map((s) => ({ ...toPixel(set, e, gaiaAt(s, e.mjd_mid)), g: s.g, id: s.id }))
    .filter((p) => p.x >= crop.x - 1 && p.y >= crop.y - 1 && p.x <= crop.x + crop.w && p.y <= crop.y + crop.h)
    .sort((a, b) => (a.g ?? 99) - (b.g ?? 99));
}
