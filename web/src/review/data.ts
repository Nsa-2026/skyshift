import { candidateFramesUrl } from "../api/client";
// Data for the review pages: the candidate list, each candidate's frames (static frames_url
// first, then the live API) with a small preload cache, and the vote store.
import { ApiFailure, epochsFor, loadCandidates, loadFrames, loadSuspects } from "../api/client";
import { checksOf, knownNameOf, statusOf } from "../api/checks";
import { gradeOf } from "../api/grades";
import { loadMore, loadedMore, moreOf, moreTotal, openedMore, setMoreOpen } from "../api/more";
import type { Candidate, CandidateKind, CandidatesResponse, Det, FramesResponse, LoadedFrames, Suspect } from "../api/types";
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

const KINDS: CandidateKind[] = ["mover", "high_pm", "spectral", "shape"];
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
    // The vetting grade, only when the file has one (older exports: not vetted yet, never B).
    ...(gradeOf(o) ? { grade: gradeOf(o) } : {}),
    // v3.23, only when the file has them (older files: open, no name, no checks).
    ...(o.status !== undefined ? { status: statusOf(o), known_name: knownNameOf(o) } : {}),
    ...(Array.isArray(o.checks) ? { checks: checksOf(o) } : {}),
  };
}

export function toCandidateSet(data: CandidatesResponse, sample: boolean): CandidateSet {
  const seen = new Set<string>();
  const candidates = (Array.isArray(data?.candidates) ? data.candidates : [])
    .map(toCandidate)
    .filter((c): c is Candidate => c !== null && !seen.has(c.id) && !!seen.add(c.id));
  return { data, candidates, sample };
}

// ---- the Less likely files (contracts/api.md v3.24 `more`) --------------------------------------

const setIds = new WeakMap<CandidateSet, Set<string>>();
const converted = new WeakMap<object, Candidate | null>();

/**
 * The candidates of the Less likely files on the list (opened on To check, or by a link to one of
 * theirs), read like candidates.json's. An id is in one file only; one already in the set is skipped.
 */
export function moreCandidates(set: CandidateSet): Candidate[] {
  let have = setIds.get(set);
  if (!have) setIds.set(set, (have = new Set(set.candidates.map((c) => c.id))));
  const seen = new Set<string>();
  const out: Candidate[] = [];
  for (const raw of loadedMore(openedMore(moreOf(set.data)))) {
    let c = converted.get(raw);
    if (c === undefined) converted.set(raw, (c = toCandidate(raw)));
    if (c && !have.has(c.id) && !seen.has(c.id)) {
      seen.add(c.id);
      out.push(c);
    }
  }
  return out;
}

/** Over this many Less likely candidates in all, an id that names no zone isn't looked for in every file. */
export const MORE_SEARCH_MAX = 5000;

/**
 * A candidate by id: in candidates.json, else in a Less likely file. Only the files of the zone its
 * id starts with are read (every file only when the id names none and they are small), and the file
 * that holds it goes on the list. null when no file has it, or the file can't be read.
 */
export async function findCandidate(set: CandidateSet, id: string): Promise<Candidate | null> {
  const hit = set.candidates.find((c) => c.id === id) ?? moreCandidates(set).find((c) => c.id === id);
  if (hit) return hit;
  const entries = moreOf(set.data);
  const zone = entries.filter((e) => id.startsWith(`${e.zone}-`));
  const order = zone.length ? zone : moreTotal(entries) <= MORE_SEARCH_MAX ? entries : [];
  for (const e of order) {
    const rows = await loadMore(e).catch(() => null);
    if (!rows?.some((r) => r.id === id)) continue;
    setMoreOpen(e, true);
    return moreCandidates(set).find((c) => c.id === id) ?? null;
  }
  return null;
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

// ---- the suspect behind a fast mover -------------------------------------------------------------

let suspectsPromise: Promise<Map<string, Suspect>> | null = null;

/**
 * The suspects.json row of a fast mover (suspects and mover candidates share the id), for what the
 * candidate list doesn't carry: near_source_arcsec. Read once; null when it isn't there.
 */
export function suspectFor(id: string): Promise<Suspect | null> {
  suspectsPromise ??= loadSuspects()
    .then((r) => new Map((Array.isArray(r?.suspects) ? r!.suspects : []).filter((s) => s && typeof s.id === "string").map((s) => [s.id, s])))
    .catch(() => new Map<string, Suspect>());
  return suspectsPromise.then((m) => m.get(id) ?? null);
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

/**
 * Over this gap (days) between the find and the nearest frame, the find's own exposures aren't
 * shown. A mover's `mjd` is the middle of the exposures it was found in, 2.1 minutes apart, so its
 * own sit about a minute from it; the session's next exposure is 3 minutes away, and isn't the find's.
 */
export const OWN_GAP_DAYS = 2 / 1440;

/**
 * Hours candidates: days from the find's mjd to the nearest frame of `det`. Live hours frames are
 * asked at the find's mjd (candidateFrames), so its session is there; a static file, or a detector
 * that saw the spot at another moment, may still show other exposures. null for months, an
 * unknown mjd or no frames of `det`.
 */
export function findGap(frames: FramesResponse, c: Pick<Candidate, "mjd" | "clock">, det: Det): number | null {
  if (frames.clock !== "hours" || c.clock !== "hours" || !(c.mjd > 0)) return null;
  const eps = epochsFor(frames, det);
  return eps.length ? Math.min(...eps.map((e) => Math.abs(e.mjd_mid - c.mjd))) : null;
}

/** A time gap in plain words: "40 minutes", "9.8 hours", "3.2 days". */
export function gapText(days: number): string {
  const h = Math.abs(days) * 24;
  const n = (v: number) => (v < 10 ? v.toFixed(1) : String(Math.round(v)));
  if (h < 1) return `${Math.round(h * 60)} minutes`;
  return h < 48 ? `${n(h)} hours` : `${n(h / 24)} days`;
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
 * The time an hours candidate's live frames are asked at (contracts/api.md v3.19 `mjd`): its find,
 * so the API shows the visit that holds it and the session it was found in, whole, and the measured
 * layer can link its track. undefined for months (either side) or an unknown mjd.
 */
export function liveMjd(c: Pick<Candidate, "mjd" | "clock">, view: Pick<View, "clock">): number | undefined {
  return c.clock === "hours" && view.clock === "hours" && c.mjd > 0 ? c.mjd : undefined;
}

/**
 * Frames for a candidate's view: its static frames_url (relative URLs resolve against it) when
 * the view is the candidate's own clock, else the live API (hours: at the find's time, liveMjd).
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
  const mjd = liveMjd(c, view);
  return loadFrames({ ra: view.ra, dec: view.dec, clock: view.clock, fov: view.fov, det: view.det, ...(mjd ? { mjd } : {}) }, signal);
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
