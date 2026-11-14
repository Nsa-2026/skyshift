// Flag-a-dot and votes: the pure parts. Document shapes (contracts/api.md, "Firestore: flags
// and votes", mirrored by infra/firebase/firestore.rules), the browser's voter id and memory,
// counts, nearby filtering and the CSV export. No DOM, no Firebase.
import type { Clock, SkyPos } from "../api/types";
import { formatMjd, sepArcsec } from "../app/astro";
import type { View } from "../app/modules";

export const ANSWERS = ["mover", "changing", "junk", "unsure"] as const;
export type Answer = (typeof ANSWERS)[number];

export const ANSWER_TEXT: Record<Answer, { label: string; meaning: string }> = {
  mover: { label: "Mover", meaning: "It changes place between frames." },
  changing: { label: "Changing", meaning: "Same place, but its brightness changes." },
  junk: { label: "Junk", meaning: "An artefact: a cosmic ray, a ghost, an edge." },
  unsure: { label: "Not sure", meaning: "Can't tell from these frames." },
};

export type Source = "user" | "pipeline";

/** A `candidates` document, exactly as the contract lists it. */
export interface CandidateDoc {
  ra: number;
  dec: number;
  mjd: number;
  clock: Clock;
  source: Source;
}

/** A `votes` document. ts is serverTimestamp() in Firestore, epoch ms in local mode. */
export interface VoteDoc<TS = unknown> {
  candidate_id: string;
  answer: Answer;
  ts: TS;
  voter: string;
}

export interface Candidate extends CandidateDoc {
  id: string;
}

export type Counts = Record<Answer, number>;

/** Plain-words failure the panel can print. kind lets the panel choose what to offer. */
export class VoteError extends Error {
  constructor(
    message: string,
    readonly kind: "offline" | "denied" | "busy" | "invalid" | "already" | "setup" | "unknown" = "unknown",
  ) {
    super(message);
    this.name = "VoteError";
  }
}

const isNum = (x: unknown): x is number => typeof x === "number" && Number.isFinite(x);

/** The candidate a browser may create (rules: source "user", ra 0–360, dec ±90, mjd 60000–80000). */
export function candidateDoc(p: SkyPos & { mjd: number; clock: Clock }): CandidateDoc {
  if (!isNum(p.ra) || !isNum(p.dec) || !isNum(p.mjd)) throw new VoteError("This dot has no usable position or time.", "invalid");
  const ra = ((p.ra % 360) + 360) % 360;
  if (p.dec < -90 || p.dec > 90) throw new VoteError("This dot's declination is off the sky.", "invalid");
  if (p.mjd < 60000 || p.mjd > 80000) throw new VoteError("This frame's date is outside the SPHEREx mission.", "invalid");
  if (p.clock !== "months" && p.clock !== "hours") throw new VoteError("Pick the months or hours clock first.", "invalid");
  return { ra, dec: p.dec, mjd: p.mjd, clock: p.clock, source: "user" };
}

export const VOTER_RE = /^[A-Za-z0-9_-]{8,64}$/;

/** A vote document. Pass serverTimestamp() as ts for Firestore. */
export function voteDoc<TS>(candidate_id: string, answer: Answer, voter: string, ts: TS): VoteDoc<TS> {
  if (typeof candidate_id !== "string" || candidate_id.length < 1 || candidate_id.length > 128)
    throw new VoteError("This candidate has no usable id.", "invalid");
  if (!(ANSWERS as readonly string[]).includes(answer)) throw new VoteError("Pick one of the four answers.", "invalid");
  if (!VOTER_RE.test(voter)) throw new VoteError("This browser's voter id is broken. Reload the page.", "invalid");
  return { candidate_id, answer, ts, voter };
}

/** Read a stored candidate back defensively (Firestore data or localStorage can be anything). */
export function toCandidate(id: string, d: unknown): Candidate | null {
  if (!d || typeof d !== "object") return null;
  const o = d as Record<string, unknown>;
  if (!isNum(o.ra) || !isNum(o.dec) || !isNum(o.mjd)) return null;
  if (o.clock !== "months" && o.clock !== "hours") return null;
  if (o.source !== "user" && o.source !== "pipeline") return null;
  return { id, ra: o.ra, dec: o.dec, mjd: o.mjd, clock: o.clock, source: o.source };
}

// ---- browser memory --------------------------------------------------------------------

/** localStorage that never throws (private windows, blocked storage, previews). */
export interface KV {
  get(key: string): string | null;
  set(key: string, value: string): void;
}

export function safeStorage(): KV {
  const mem = new Map<string, string>();
  let ls: Storage | null = null;
  try {
    ls = globalThis.localStorage ?? null;
  } catch {
    ls = null;
  }
  return {
    get(key) {
      try {
        const v = ls?.getItem(key);
        if (v != null) return v;
      } catch {
        /* blocked */
      }
      return mem.get(key) ?? null;
    },
    set(key, value) {
      mem.set(key, value);
      try {
        ls?.setItem(key, value);
      } catch {
        /* full or blocked: memory still has it for this page */
      }
    },
  };
}

export const KEYS = {
  voter: "skyshift.voter",
  voted: "skyshift.voted",
  flagged: "skyshift.flagged",
  localCandidates: "skyshift.local.candidates",
  localVotes: "skyshift.local.votes",
} as const;

function randomId(): string {
  const bytes = new Uint8Array(16);
  try {
    crypto.getRandomValues(bytes);
  } catch {
    for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
  }
  const abc = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
  return Array.from(bytes, (b) => abc[b & 63]).join("") + "v";
}

/** A random id made once per browser. Never a name or an email. */
export function voterId(kv: KV): string {
  const have = kv.get(KEYS.voter);
  if (have && VOTER_RE.test(have)) return have;
  const id = randomId();
  kv.set(KEYS.voter, id);
  return id;
}

function readJson<T>(kv: KV, key: string, fallback: T): T {
  try {
    const v = kv.get(key);
    const parsed = v ? JSON.parse(v) : null;
    return parsed && typeof parsed === "object" ? (parsed as T) : fallback;
  } catch {
    return fallback;
  }
}

/** What this browser has done: votes cast (one per candidate) and dots it flagged, with when. */
export class BrowserMemory {
  readonly voter: string;
  constructor(private kv: KV) {
    this.voter = voterId(kv);
  }
  votedFor(id: string): Answer | null {
    const v = readJson<Record<string, string>>(this.kv, KEYS.voted, {})[id];
    return (ANSWERS as readonly string[]).includes(v) ? (v as Answer) : null;
  }
  rememberVote(id: string, answer: Answer): void {
    const all = readJson<Record<string, string>>(this.kv, KEYS.voted, {});
    all[id] = answer;
    this.kv.set(KEYS.voted, JSON.stringify(all));
  }
  flaggedAt(id: string): number | null {
    const t = readJson<Record<string, number>>(this.kv, KEYS.flagged, {})[id];
    return isNum(t) ? t : null;
  }
  rememberFlag(id: string, at = Date.now()): void {
    const all = readJson<Record<string, number>>(this.kv, KEYS.flagged, {});
    all[id] = at;
    this.kv.set(KEYS.flagged, JSON.stringify(all));
  }
}

export { readJson };

// ---- counts, nearby, CSV ------------------------------------------------------------------

export const emptyCounts = (): Counts => ({ mover: 0, changing: 0, junk: 0, unsure: 0 });

export function countVotes(votes: Pick<VoteDoc, "candidate_id" | "answer">[]): Map<string, Counts> {
  const out = new Map<string, Counts>();
  for (const v of votes) {
    if (!(ANSWERS as readonly string[]).includes(v.answer)) continue;
    const c = out.get(v.candidate_id) ?? emptyCounts();
    c[v.answer] += 1;
    out.set(v.candidate_id, c);
  }
  return out;
}

export const total = (c: Counts): number => c.mover + c.changing + c.junk + c.unsure;

/** The view's field: half the diagonal of the fov × fov square (fov in arcmin), in arcsec. */
export function fieldRadiusArcsec(view: Pick<View, "fov">): number {
  return (view.fov * 60) / Math.SQRT2;
}

/**
 * Candidates inside the view's field, newest first. Firestore keeps no creation time
 * (the contract has none), so "newest" is: dots this browser flagged, latest first, then
 * the rest by the clicked frame's date, latest first.
 */
export function nearby(cands: Candidate[], view: SkyPos & Pick<View, "fov">, flaggedAt: (id: string) => number | null = () => null): Candidate[] {
  const r = fieldRadiusArcsec(view);
  return cands
    .filter((c) => sepArcsec(c, view) <= r)
    .map((c) => ({ c, t: flaggedAt(c.id) }))
    .sort((a, b) => {
      if (a.t != null || b.t != null) return (b.t ?? -Infinity) - (a.t ?? -Infinity);
      return b.c.mjd - a.c.mjd || a.c.id.localeCompare(b.c.id);
    })
    .map((x) => x.c);
}

/** One CSV cell (RFC 4180), with spreadsheet formula injection defused for text. */
export function csvCell(v: string | number | null | undefined): string {
  if (v == null) return "";
  if (typeof v === "number") return Number.isFinite(v) ? String(v) : "";
  let s = String(v);
  if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export const CSV_COLUMNS = ["id", "source", "ra_deg", "dec_deg", "mjd", "date_utc", "clock", "mover", "changing", "junk", "unsure", "votes"] as const;

export function toCsv(cands: Candidate[], counts: Map<string, Counts>): string {
  const rows: string[] = [CSV_COLUMNS.join(",")];
  for (const c of cands) {
    const n = counts.get(c.id) ?? emptyCounts();
    rows.push(
      [c.id, c.source, +c.ra.toFixed(6), +c.dec.toFixed(6), +c.mjd.toFixed(6), formatMjd(c.mjd, true), c.clock, n.mover, n.changing, n.junk, n.unsure, total(n)]
        .map(csvCell)
        .join(","),
    );
  }
  return rows.join("\r\n") + "\r\n";
}
