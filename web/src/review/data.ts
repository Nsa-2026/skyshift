import { candidateFramesUrl } from "../api/client";
// Data for the review pages: the candidate list, each candidate's frames (static frames_url
// first, then the live API) with a small preload cache, and the vote store.
import { ApiFailure, epochsFor, loadCandidates, loadFrames } from "../api/client";
import type { Candidate, CandidateKind, CandidatesResponse, Det, FramesResponse, LoadedFrames } from "../api/types";
import type { View } from "../app/modules";
import { safeStorage, type KV } from "../vote/model";
import { getVoteStore, type VoteApi } from "../vote/store";

// ---- the candidate list ------------------------------------------------------------------------

export interface CandidateSet {
  data: CandidatesResponse;
  candidates: Candidate[];
  /** True when the list is the dev-only sample from fixtures/ (no /data/candidates.json yet). */
  sample: boolean;
}

const KINDS: CandidateKind[] = ["mover", "high_pm", "spectral"];
const finite = (x: unknown): x is number => typeof x === "number" && Number.isFinite(x);
const numOrNull = (x: unknown): number | null => (finite(x) ? x : null);
const strings = (x: unknown): string[] => (Array.isArray(x) ? x.filter((s): s is string => typeof s === "string") : []);

/** Read one candidate defensively: the file is written by another team and may grow. */
export function toCandidate(x: unknown): Candidate | null {
  if (!x || typeof x !== "object") return null;
  const o = x as Record<string, unknown>;
  if (typeof o.id !== "string" || !o.id || !finite(o.ra) || !finite(o.dec)) return null;
  if (!KINDS.includes(o.kind as CandidateKind)) return null;
  const clock = o.clock === "hours" || o.clock === "months" ? o.clock : o.kind === "mover" ? "hours" : "months";
  const det = Number(o.det);
  return {
    id: o.id,
    kind: o.kind as CandidateKind,
    ra: o.ra,
    dec: o.dec,
    mjd: finite(o.mjd) ? o.mjd : 0,
    clock,
    det: (Number.isInteger(det) && det >= 1 && det <= 6 ? det : 2) as Det,
    zone: typeof o.zone === "string" ? o.zone : "",
    score: numOrNull(o.score),
    snr: numOrNull(o.snr),
    flux_ujy: numOrNull(o.flux_ujy),
    rate_arcsec_h: numOrNull(o.rate_arcsec_h),
    pm_mas_yr: numOrNull(o.pm_mas_yr),
    pm_err_mas_yr: numOrNull(o.pm_err_mas_yr),
    pa_deg: numOrNull(o.pa_deg),
    nearest_known_arcsec: numOrNull(o.nearest_known_arcsec),
    gaia_match: o.gaia_match === true,
    catwise_match: o.catwise_match === true,
    galactic_b: finite(o.galactic_b) ? o.galactic_b : 0,
    labels: strings(o.labels),
    reasons: strings(o.reasons),
    why: typeof o.why === "string" ? o.why : "",
    flags: strings(o.flags),
    frames_url: typeof o.frames_url === "string" && o.frames_url ? o.frames_url : null,
  };
}

export function toCandidateSet(data: CandidatesResponse, sample: boolean): CandidateSet {
  const seen = new Set<string>();
  const candidates = (Array.isArray(data?.candidates) ? data.candidates : [])
    .map(toCandidate)
    .filter((c): c is Candidate => c !== null && !seen.has(c.id) && !!seen.add(c.id));
  return { data, candidates, sample };
}

let setPromise: Promise<CandidateSet | null> | null = null;

/**
 * /data/candidates.json (written by the pipeline export). In dev only, when it isn't there yet,
 * the real pilot sample from fixtures/api/ with sample = true. Production shows an empty state.
 */
export function candidateSet(): Promise<CandidateSet | null> {
  setPromise ??= (async () => {
    const live = await loadCandidates().catch(() => null);
    if (live && Array.isArray(live.candidates)) return toCandidateSet(live, false);
    if (import.meta.env.DEV) {
      try {
        const mod = await import("../../../fixtures/api/candidates.json");
        return toCandidateSet(mod.default as unknown as CandidatesResponse, true);
      } catch {
        return null;
      }
    }
    return null;
  })();
  const p = setPromise;
  // A failed read may succeed later (the export lands): don't keep "nothing" forever.
  p.then((s) => {
    if (!s && setPromise === p) setPromise = null;
  });
  return p;
}

// ---- frames ------------------------------------------------------------------------------------

/** The view the plate opens on: the candidate's position, clock and detector. */
export function candidateView(c: Candidate): View {
  return { ra: c.ra, dec: c.dec, clock: c.clock, fov: c.clock === "hours" ? 20 : 5, det: c.det, slug: null, name: null };
}

/** Hours candidates: exposures within this many days of the find are shown first. */
export const FOCUS_DAYS = 1;

/**
 * Hours candidates were found in one session: blink the exposures within a day of it (the
 * session and its neighbours), so the mover is there, then gone. Months: every visit. Falls back
 * to all epochs when fewer than 3 of the view's detector fall in the window.
 */
export function focusFrames(frames: FramesResponse, c: Pick<Candidate, "mjd" | "clock">, det: Det): { frames: FramesResponse; focused: boolean; all: number; shown: number } {
  const all = epochsFor(frames, det).length;
  if (frames.clock !== "hours" || c.clock !== "hours" || !(c.mjd > 0)) return { frames, focused: false, all, shown: all };
  const keep = frames.epochs.filter((e) => Math.abs(e.mjd_mid - c.mjd) <= FOCUS_DAYS);
  const shown = keep.filter((e) => e.det === det).length;
  if (shown < 3 || shown === all) return { frames, focused: false, all, shown: all };
  const ids = new Set(keep.map((e) => e.id));
  return {
    frames: { ...frames, epochs: keep, known: frames.known.filter((k) => !k.epoch_id || ids.has(k.epoch_id)) },
    focused: true,
    all,
    shown,
  };
}

async function getFrames(url: string, signal?: AbortSignal): Promise<FramesResponse> {
  const res = await fetch(url, { signal });
  if (!res.ok) throw new ApiFailure(`${res.status} for ${url}`, res.status, res.status === 404 ? "not_found" : "internal");
  return (await res.json()) as FramesResponse;
}

/** Will these frames come from the live service (slow), not a static file? */
export function isLive(c: Candidate, view: View): boolean {
  return !c.frames_url || view.clock !== c.clock;
}

/**
 * Frames for a candidate's view: its static frames_url (relative URLs resolve against it) when
 * the view is the candidate's own clock, else the live API.
 */
export async function candidateFrames(c: Candidate, view: View, signal?: AbortSignal): Promise<LoadedFrames> {
  if (c.frames_url && view.clock === c.clock) {
    const jsonUrl = candidateFramesUrl(c.frames_url);
    try {
      const frames = await getFrames(jsonUrl, signal);
      if (frames.clock === view.clock && frames.epochs.some((e) => e.det === view.det)) return { frames, jsonUrl, origin: "static" };
    } catch (e) {
      if ((e as Error).name === "AbortError") throw e;
    }
  }
  return loadFrames({ ra: view.ra, dec: view.dec, clock: view.clock, fov: view.fov, det: view.det }, signal);
}

/** Frame data warms the HTTP cache (immutable on the server), so the blink starts at once. */
async function warm(loaded: LoadedFrames, det: Det, c: Candidate): Promise<void> {
  const eps = epochsFor(focusFrames(loaded.frames, c, det).frames, det).slice(0, 40);
  let next = 0;
  const worker = async () => {
    while (next < eps.length) {
      const e = eps[next++];
      try {
        await fetch(new URL(e.data_url, loaded.jsonUrl).href);
      } catch {
        /* the blink will try again */
      }
    }
  };
  await Promise.all([worker(), worker(), worker()]);
}

const cache = new Map<string, Promise<LoadedFrames>>();
const key = (c: Candidate, v: View) => `${c.id}|${v.clock}|${v.fov}|${v.det}`;

/** Frames for a candidate, shared between the page and the preloader. Failures aren't kept. */
export function framesFor(c: Candidate, view: View = candidateView(c)): Promise<LoadedFrames> {
  const k = key(c, view);
  let p = cache.get(k);
  if (!p) {
    p = candidateFrames(c, view);
    cache.set(k, p);
    p.catch(() => cache.delete(k));
    if (cache.size > 12) cache.delete(cache.keys().next().value as string);
  }
  return p;
}

/** Is this candidate's view already loaded (or loading)? */
export function hasFrames(c: Candidate, view: View = candidateView(c)): boolean {
  return cache.has(key(c, view));
}

/** Start loading a candidate's frames and their pixels in the background. */
export function preload(c: Candidate): void {
  const view = candidateView(c);
  framesFor(c, view)
    .then((loaded) => warm(loaded, view.det, c))
    .catch(() => {
      /* shown when the person gets there */
    });
}

// ---- votes -------------------------------------------------------------------------------------

export interface Votes {
  /** src/vote's API: Firestore when configured, else this browser only (mode "local"). */
  api: VoteApi;
  /** This browser's storage, for the practice round's "done" mark. */
  kv: KV;
}

let votesPromise: Promise<Votes> | null = null;

/** The vote API and browser storage, shared by both pages. Never rejects. */
export function votes(): Promise<Votes> {
  votesPromise ??= getVoteStore().then((api) => ({ api, kv: safeStorage() }));
  return votesPromise;
}
