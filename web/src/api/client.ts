// Static first: try /data/<slug>/ for featured targets, then /api/. Relative URLs only
// (Firebase Hosting rewrites /api/** to Cloud Run).
import type {
  Clock,
  Det,
  DistantResponse,
  Epoch,
  FramesResponse,
  LoadedFrames,
  ObjectResponse,
  ResolveResponse,
  SuspectsResponse,
  TargetIndex,
  ValidationResponse,
} from "./types";

export class ApiFailure extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string = "internal",
  ) {
    super(message);
  }
}

const base = (): string => (typeof location !== "undefined" ? location.href : "http://localhost/");

export async function getJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(url, { signal });
  if (!res.ok) {
    let code = "internal";
    let message = `${res.status} ${res.statusText} for ${url}`;
    try {
      const body = await res.json();
      if (body?.error) {
        code = body.error.code;
        message = body.error.message;
      }
    } catch {
      /* not JSON */
    }
    throw new ApiFailure(message, res.status, code);
  }
  return (await res.json()) as T;
}

/** getJson for observations (frames, objects): a synthetic example ("synthetic": true) is never
 *  shown as a real observation. Pages that label examples (Results, Tools) read them with getJson. */
async function getReal<T>(url: string, signal?: AbortSignal): Promise<T> {
  const data = await getJson<T>(url, signal);
  if ((data as { synthetic?: unknown } | null)?.synthetic === true)
    throw new ApiFailure("Synthetic examples cannot be loaded as real observations", 502, "upstream");
  return data;
}

export async function loadTargetIndex(slug: string, signal?: AbortSignal): Promise<TargetIndex | null> {
  try {
    return await getJson<TargetIndex>(`/data/${slug}/index.json`, signal);
  } catch {
    return null;
  }
}

/** Default field per clock (contracts/api.md): months 5′ (or 15′), hours 20′ (or 60′). */
export const DEFAULT_FOV: Record<Clock, number> = { months: 5, hours: 20 };
export const ALLOWED_FOV: Record<Clock, number[]> = { months: [5, 15], hours: [20, 60] };

export interface FramesQuery {
  ra: number;
  dec: number;
  clock: Clock;
  fov?: number;
  det?: Det;
  /** Featured target slug: tried first as static files. */
  slug?: string | null;
}

/**
 * Frames for a spot. For a featured slug, reads /data/<slug>/frames_<clock>_<fov>.json
 * (or frames.json when that is the target's own clock and field); otherwise calls /api/frames.
 * Static files may hold several detectors: callers filter epochs with epochsFor().
 */
export async function loadFrames(q: FramesQuery, signal?: AbortSignal): Promise<LoadedFrames> {
  const fov = q.fov ?? DEFAULT_FOV[q.clock];
  if (q.slug) {
    const idx = await loadTargetIndex(q.slug, signal);
    const file =
      idx?.frames.find((f) => f.clock === q.clock && f.fov === fov && (!q.det || f.dets.includes(q.det)))?.file ??
      idx?.frames.find((f) => f.clock === q.clock && f.fov === fov)?.file ??
      (idx ? null : "frames.json");
    if (file) {
      const jsonUrl = new URL(`/data/${q.slug}/${file}`, base()).href;
      try {
        const frames = await getReal<FramesResponse>(jsonUrl, signal);
        if (frames.clock === q.clock && (!q.det || frames.epochs.some(e=>e.det===q.det))) return { frames, jsonUrl, origin: "static" };
      } catch (e) {
        if ((e as Error).name === "AbortError") throw e;
      }
    }
  }
  const p = new URLSearchParams({ ra: String(q.ra), dec: String(q.dec), clock: q.clock, fov: String(fov) });
  if (q.det) p.set("det", String(q.det));
  const jsonUrl = new URL(`/api/frames?${p}`, base()).href;
  const frames = await getReal<FramesResponse>(jsonUrl, signal);
  return { frames, jsonUrl, origin: "api" };
}

export function epochsFor(frames: FramesResponse, det: Det): Epoch[] {
  return frames.epochs.filter((e) => e.det === det).sort((a, b) => a.mjd_mid - b.mjd_mid);
}

export function detectorsIn(frames: FramesResponse): Det[] {
  return [...new Set(frames.epochs.map((e) => e.det))].sort() as Det[];
}

/** Raw uint16 pixels of one epoch (little-endian, north up, east left). raw 0 = no data. */
export async function loadFrameData(epoch: Epoch, jsonUrl: string, signal?: AbortSignal): Promise<Uint16Array> {
  const url = new URL(epoch.data_url, jsonUrl).href;
  const res = await fetch(url, { signal });
  if (!res.ok) throw new ApiFailure(`${res.status} for frame ${epoch.id}`, res.status);
  return new Uint16Array(await res.arrayBuffer());
}

/** value = raw × scale + zero (MJy/sr); NaN where raw = 0 (no data). */
export function toMJySr(raw: Uint16Array, epoch: Pick<Epoch, "scale" | "zero">): Float32Array {
  const out = new Float32Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw[i] === 0 ? NaN : raw[i] * epoch.scale + epoch.zero;
  return out;
}

export async function loadObject(
  pos: { ra: number; dec: number; slug?: string | null },
  signal?: AbortSignal,
): Promise<ObjectResponse> {
  if (pos.slug) {
    try {
      return await getReal<ObjectResponse>(`/data/${pos.slug}/object.json`, signal);
    } catch (e) {
      if ((e as Error).name === "AbortError") throw e;
    }
  }
  return getReal<ObjectResponse>(`/api/object?ra=${pos.ra}&dec=${pos.dec}`, signal);
}

export function resolve(q: string, signal?: AbortSignal): Promise<ResolveResponse> {
  return getJson<ResolveResponse>(`/api/resolve?q=${encodeURIComponent(q)}`, signal);
}

export async function loadValidation(signal?: AbortSignal): Promise<ValidationResponse | null> {
  try {
    return await getJson<ValidationResponse>("/data/validation.json", signal);
  } catch {
    return null;
  }
}

export async function loadSuspects(signal?: AbortSignal): Promise<SuspectsResponse | null> {
  try {
    return await getJson<SuspectsResponse>("/data/suspects.json", signal);
  } catch {
    return null;
  }
}

/** The distant-planet search (contracts/api.md v3.6), written by scripts/validate; null when not published. */
export async function loadDistant(signal?: AbortSignal): Promise<DistantResponse | null> {
  try {
    return await getJson<DistantResponse>("/data/distant.json", signal);
  } catch {
    return null;
  }
}

/** Candidates for community review: /data/candidates.json (static, written by the pipeline export). */
export async function loadCandidates(signal?: AbortSignal): Promise<import("./types").CandidatesResponse | null> {
  try {
    return await getJson<import("./types").CandidatesResponse>("/data/candidates.json", signal);
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    return null;
  }
}

/** A candidate's static frames_url resolves against candidates.json (contracts/api.md), not the page. */
export function candidateFramesUrl(framesUrl: string): string {
  return new URL(framesUrl, new URL("/data/candidates.json", base())).href;
}
