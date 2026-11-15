// Flag-a-dot and votes: the pure parts. Document shapes (contracts/api.md, "Firestore: flags
// and votes", mirrored by infra/firebase/firestore.rules), the browser's voter id and memory,
// counts, nearby filtering and the CSV export. No DOM, no Firebase.
//
// Changing a vote (contracts/api.md v3.16) writes a new document with the new answer; WITHDRAWN
// takes it back. Per candidate only each voter's latest document counts (latestVotes): every count
// here goes through it.
import type { Clock, SkyPos } from "../api/types";
import { formatMjd, sepArcsec } from "../app/astro";
import type { View } from "../app/modules";

/** The four answers people pick. */
export const ANSWERS = ["mover", "changing", "junk", "unsure"] as const;
export type Answer = (typeof ANSWERS)[number];

/** Stored only (v3.16): a document that takes this voter's vote back. Never shown as an answer. */
export const WITHDRAWN = "withdrawn";
/** Every answer a `votes` document may hold (the rules' list). */
export const STORED_ANSWERS = [...ANSWERS, WITHDRAWN] as const;
export type StoredAnswer = (typeof STORED_ANSWERS)[number];

export const isAnswer = (a: unknown): a is Answer => (ANSWERS as readonly unknown[]).includes(a);
const isStoredAnswer = (a: unknown): a is StoredAnswer => (STORED_ANSWERS as readonly unknown[]).includes(a);

export const ANSWER_TEXT: Record<Answer, { label: string; meaning: string }> = {
  mover: { label: "Mover", meaning: "It changes place." },
  changing: { label: "Changing", meaning: "Same place, brightness changes." },
  junk: { label: "Junk", meaning: "A camera glitch (cosmic ray, ghost, edge)." },
  unsure: { label: "Not sure", meaning: "Can't tell from these frames." },
};

export type Source = "user" | "pipeline";

/** Where and when a dot was flagged: a `candidates` document without its `created` time. */
export interface CandidateFields {
  ra: number;
  dec: number;
  mjd: number;
  clock: Clock;
  source: Source;
}

/**
 * A `candidates` document, exactly as the contract lists it (v2.1). created is serverTimestamp()
 * in Firestore (the rules require created == request.time), a local Date in local mode.
 */
export interface CandidateDoc<TS = unknown> extends CandidateFields {
  created: TS;
}

/** A `votes` document. ts is serverTimestamp() in Firestore, epoch ms in local mode. */
export interface VoteDoc<TS = unknown> {
  candidate_id: string;
  answer: StoredAnswer;
  ts: TS;
  voter: string;
}

/** A candidate read back. created in epoch ms, or null for docs made before contract v2.1. */
export interface Candidate extends CandidateFields {
  id: string;
  created: number | null;
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

/** The fields a browser may flag (rules: source "user", ra 0–360, dec ±90, mjd 60000–80000). */
export function candidateFields(p: SkyPos & { mjd: number; clock: Clock }): CandidateFields {
  if (!isNum(p.ra) || !isNum(p.dec) || !isNum(p.mjd)) throw new VoteError("This dot has no usable position or time.", "invalid");
  const ra = ((p.ra % 360) + 360) % 360;
  if (p.dec < -90 || p.dec > 90) throw new VoteError("This dot's declination is off the sky.", "invalid");
  if (p.mjd < 60000 || p.mjd > 80000) throw new VoteError("This frame's date is outside the SPHEREx mission.", "invalid");
  if (p.clock !== "months" && p.clock !== "hours") throw new VoteError("Pick the months or hours clock first.", "invalid");
  return { ra, dec: p.dec, mjd: p.mjd, clock: p.clock, source: "user" };
}

/** A `candidates` document. Pass serverTimestamp() as created for Firestore, a Date in local mode. */
export function candidateDoc<TS>(p: SkyPos & { mjd: number; clock: Clock }, created: TS): CandidateDoc<TS> {
  return { ...candidateFields(p), created };
}

/** A stored time as epoch ms: Firestore Timestamp (toMillis), Date, ms number or ISO string. */
export function toMillis(t: unknown): number | null {
  if (t == null) return null;
  if (typeof t === "number") return Number.isFinite(t) ? t : null;
  if (typeof t === "string") {
    const ms = Date.parse(t);
    return Number.isFinite(ms) ? ms : null;
  }
  if (t instanceof Date) return Number.isFinite(t.getTime()) ? t.getTime() : null;
  if (typeof t === "object" && typeof (t as { toMillis?: unknown }).toMillis === "function") {
    try {
      const ms = (t as { toMillis(): unknown }).toMillis();
      return typeof ms === "number" && Number.isFinite(ms) ? ms : null;
    } catch {
      return null;
    }
  }
  return null;
}

export const VOTER_RE = /^[A-Za-z0-9_-]{8,64}$/;

/** A vote document (an answer, or WITHDRAWN to take the vote back). Pass serverTimestamp() as ts for Firestore. */
export function voteDoc<TS>(candidate_id: string, answer: StoredAnswer, voter: string, ts: TS): VoteDoc<TS> {
  if (typeof candidate_id !== "string" || candidate_id.length < 1 || candidate_id.length > 128)
    throw new VoteError("This candidate has no usable id.", "invalid");
  if (!isStoredAnswer(answer)) throw new VoteError("Pick one of the four answers.", "invalid");
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
  return { id, ra: o.ra, dec: o.dec, mjd: o.mjd, clock: o.clock, source: o.source, created: toMillis(o.created) };
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

/** What this browser has done: its latest answer per candidate and dots it flagged, with when. */
export class BrowserMemory {
  readonly voter: string;
  constructor(private kv: KV) {
    this.voter = voterId(kv);
  }
  votedFor(id: string): Answer | null {
    const v = readJson<Record<string, string>>(this.kv, KEYS.voted, {})[id];
    return isAnswer(v) ? v : null;
  }
  rememberVote(id: string, answer: Answer): void {
    const all = readJson<Record<string, string>>(this.kv, KEYS.voted, {});
    all[id] = answer;
    this.kv.set(KEYS.voted, JSON.stringify(all));
  }
  /** The vote was taken back: this browser has no answer on it any more. */
  forgetVote(id: string): void {
    const all = readJson<Record<string, string>>(this.kv, KEYS.voted, {});
    if (!(id in all)) return;
    delete all[id];
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

/** A vote as any store holds it: ts may be a Firestore Timestamp, ms, null (pending) or missing. */
export interface VoteLike {
  candidate_id: string;
  answer: string;
  voter?: unknown;
  ts?: unknown;
}

/** Sort key for "latest": epoch ms, and a document whose ts isn't set yet (pending) is the latest. */
const when = (ts: unknown): number => toMillis(ts) ?? Infinity;

/**
 * The votes that count (contracts/api.md v3.16): per candidate, each voter's latest document only
 * (largest ts; a pending ts is the latest; a tie goes to the later one in the list), and nothing
 * when that latest document is WITHDRAWN. Unknown answers are ignored; a document without a voter
 * id counts on its own. Keeps the input order.
 */
export function latestVotes<T extends VoteLike>(votes: readonly T[]): (T & { answer: Answer })[] {
  const latest = new Map<string, number>();
  votes.forEach((v, i) => {
    if (!v || typeof v.candidate_id !== "string" || !v.candidate_id || !isStoredAnswer(v.answer)) return;
    const key = typeof v.voter === "string" && v.voter ? `${v.candidate_id}\u0000${v.voter}` : `\u0001${i}`;
    const have = latest.get(key);
    if (have === undefined || when(v.ts) >= when(votes[have].ts)) latest.set(key, i);
  });
  const keep = new Set(latest.values());
  return votes.filter((v, i): v is T & { answer: Answer } => keep.has(i) && isAnswer(v.answer));
}

/** Counts per candidate and answer, from each voter's latest vote (latestVotes). */
export function countVotes(votes: readonly VoteLike[]): Map<string, Counts> {
  const out = new Map<string, Counts>();
  for (const v of latestVotes(votes)) {
    const c = out.get(v.candidate_id) ?? emptyCounts();
    c[v.answer] += 1;
    out.set(v.candidate_id, c);
  }
  return out;
}

export const total = (c: Counts): number => c.mover + c.changing + c.junk + c.unsure;

/**
 * Counts after one browser's vote changes (an optimistic update before a fresh read): its previous
 * answer −1 (never below 0), its new one +1. null for either side: a first vote, or a take-back.
 */
export function shiftCounts(c: Counts | null | undefined, previous: Answer | null, answer: Answer | null): Counts {
  const out = { ...(c ?? emptyCounts()) };
  if (previous) out[previous] = Math.max(0, out[previous] - 1);
  if (answer) out[answer] += 1;
  return out;
}

/** A vote document read back, WITHDRAWN included. ts in epoch ms (null while a fresh serverTimestamp() is pending). */
export interface StoredVote {
  candidate_id: string;
  answer: StoredAnswer;
  ts: number | null;
  voter: string;
}

/** A vote that is an answer (one of the four). latestVotes() turns StoredVote[] into the ones that count. */
export interface VoteRecord extends StoredVote {
  answer: Answer;
}

/** Read a stored vote document back defensively, WITHDRAWN included. */
export function toStoredVote(d: unknown): StoredVote | null {
  if (!d || typeof d !== "object") return null;
  const o = d as Record<string, unknown>;
  if (typeof o.candidate_id !== "string" || !o.candidate_id) return null;
  if (!isStoredAnswer(o.answer)) return null;
  return { candidate_id: o.candidate_id, answer: o.answer, ts: toMillis(o.ts), voter: typeof o.voter === "string" ? o.voter : "" };
}

/** Read one stored answer back defensively (null for WITHDRAWN: it is not an answer). Counting
 *  needs every document instead: toStoredVote, then latestVotes. */
export function toVoteRecord(d: unknown): VoteRecord | null {
  const v = toStoredVote(d);
  return v && isAnswer(v.answer) ? (v as VoteRecord) : null;
}

/** Consensus (web/DESIGN.md "Votes and consensus"): at least 5 votes and at least 70 % on one answer. */
export const CONSENSUS = { minVotes: 5, share: 0.7 } as const;

export function consensus(c: Counts): { answer: Answer; share: number; votes: number } | null {
  const votes = total(c);
  if (votes < CONSENSUS.minVotes) return null;
  const answer = ANSWERS.reduce((best, a) => (c[a] > c[best] ? a : best), ANSWERS[0]);
  const share = c[answer] / votes;
  return share >= CONSENSUS.share ? { answer, share, votes } : null;
}

/** The view's field: half the diagonal of the fov × fov square (fov in arcmin), in arcsec. */
export function fieldRadiusArcsec(view: Pick<View, "fov">): number {
  return (view.fov * 60) / Math.SQRT2;
}

/**
 * Candidates inside the view's field, newest first by `created` (contracts/api.md v2.1).
 * Docs made before v2.1 have no `created`: a dot this browser flagged uses the time it was
 * flagged here; the rest go last, by the clicked frame's date, latest first.
 */
export function nearby(cands: Candidate[], view: SkyPos & Pick<View, "fov">, flaggedAt: (id: string) => number | null = () => null): Candidate[] {
  const r = fieldRadiusArcsec(view);
  return cands
    .filter((c) => sepArcsec(c, view) <= r)
    .map((c) => ({ c, t: c.created ?? flaggedAt(c.id) }))
    .sort((a, b) => {
      if (a.t != null || b.t != null) return (b.t ?? -Infinity) - (a.t ?? -Infinity) || a.c.id.localeCompare(b.c.id);
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

export const CSV_COLUMNS = ["id", "source", "ra_deg", "dec_deg", "mjd", "date_utc", "clock", "mover", "changing", "junk", "unsure", "votes", "created_utc"] as const;

export function toCsv(cands: Candidate[], counts: Map<string, Counts>): string {
  const rows: string[] = [CSV_COLUMNS.join(",")];
  for (const c of cands) {
    const n = counts.get(c.id) ?? emptyCounts();
    rows.push(
      [
        c.id, c.source, +c.ra.toFixed(6), +c.dec.toFixed(6), +c.mjd.toFixed(6), formatMjd(c.mjd, true), c.clock,
        n.mover, n.changing, n.junk, n.unsure, total(n), c.created != null ? new Date(c.created).toISOString() : null,
      ]
        .map(csvCell)
        .join(","),
    );
  }
  return rows.join("\r\n") + "\r\n";
}
