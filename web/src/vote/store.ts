// Where flags and votes go. FirestoreStore (./firestore.ts, loaded lazily) when the Firebase
// config is present; LocalStore otherwise and in tests. Both write exactly the contract's shapes.
//
// ---- The vote API for other pages (src/review, src/discover, src/community) ---------------------
// Import from here and from ./model; don't edit src/vote (P4 owns it). Stable from 14 Nov 2026;
// change and take back from 15 Nov 2026 (contracts/api.md v3.16).
//
//   import { getVoteStore } from "../vote/store";
//   import { ANSWERS, ANSWER_TEXT, consensus, type Answer, type Counts } from "../vote/model";
//
//   const votes = await getVoteStore();      // one per page; Firestore if configured, else local mode
//   votes.mode                               // "firestore" | "local" (local: this browser only; say so)
//   votes.notice                             // plain sentence if Firestore failed and it fell back, else null
//   await votes.countsFor(ids)               // Map<id, Counts>, every id present (zeros when no votes);
//                                            //   each voter's latest vote only
//   await votes.castVote(id, "mover")        // this browser's vote: a first vote, or a change of mind
//                                            //   (a new doc; the latest counts). Same answer as now:
//                                            //   resolves without writing. Throws VoteError (plain
//                                            //   words, e.message is ready to show)
//   await votes.withdrawVote(id)             // take this browser's vote back (a `withdrawn` doc);
//                                            //   no vote here: resolves without writing
//   votes.myVote(id)                         // "mover" | … | null, sync (this browser's memory)
//   votes.voterId()                          // this browser's random voter id (never a name or email)
//   await votes.allVotes()                   // VoteRecord[] { candidate_id, answer, ts ms|null, voter }:
//                                            //   only votes that count (each voter's latest, never withdrawn)
//   await votes.allCandidates()              // Candidate[] flagged in Firestore (user and pipeline)
//   consensus(counts)                        // { answer, share, votes } at ≥ 5 votes and ≥ 70 %, else null
//
// candidate_id is the candidate's id: a /data/candidates.json id for pipeline candidates, or a
// Firestore `candidates` doc id for dots people flagged. After every castVote or withdrawVote that
// writes, the window gets a `skyshift:voted` CustomEvent, detail { candidateId, answer, previous }
// (VotedDetail): answer is the new vote, or null when it was taken back; previous is this
// browser's vote before, or null for a first vote. Other views refresh on it.
// Errors never throw raw Firebase errors: always VoteError with a message for people.
import { loadSuspects } from "../api/client";
import type { SkyPos } from "../api/types";
import { sepArcsec } from "../app/astro";
import {
  ANSWERS,
  BrowserMemory,
  KEYS,
  VoteError,
  WITHDRAWN,
  candidateDoc,
  countVotes,
  emptyCounts,
  latestVotes,
  readJson,
  safeStorage,
  toCandidate,
  toVoteRecord,
  voteDoc,
  type Answer,
  type Candidate,
  type CandidateDoc,
  type CandidateFields,
  type Counts,
  type KV,
  type StoredAnswer,
  type VoteDoc,
  type VoteRecord,
} from "./model";

/** Storage behind the vote API: Firestore or this browser. Pages use VoteApi (getVoteStore) instead. */
export interface VoteStore {
  readonly mode: "firestore" | "local";
  /** Candidates within radius (arcsec) of center, any order. */
  near(center: SkyPos, radiusArcsec: number): Promise<Candidate[]>;
  /** Vote counts per candidate id, each voter's latest vote only (ids without votes may be missing). */
  counts(ids: string[]): Promise<Map<string, Counts>>;
  /** Create a `candidates` doc (the store adds `created`); returns it with its new id. */
  flag(fields: CandidateFields): Promise<Candidate>;
  /** Create a `votes` doc: a vote, a change (a newer doc) or `withdrawn` to take it back. */
  vote(candidateId: string, answer: StoredAnswer, voter: string): Promise<void>;
  /** Every vote that counts: each voter's latest per candidate, never `withdrawn` (capped; see the store). */
  allVotes(): Promise<VoteRecord[]>;
  /** Every flagged candidate (capped; see the store). */
  allCandidates(): Promise<Candidate[]>;
}

/** The vote API every page uses. See the top of this file. */
export interface VoteApi {
  readonly mode: "firestore" | "local";
  /** A plain sentence when Firestore was configured but failed to load (local mode instead), else null. */
  readonly notice: string | null;
  countsFor(ids: string[]): Promise<Map<string, Counts>>;
  /** Vote, or change this browser's vote. The same answer as now resolves without writing. */
  castVote(candidateId: string, answer: Answer): Promise<void>;
  /** Take this browser's vote back. Without a vote here it resolves without writing. */
  withdrawVote(candidateId: string): Promise<void>;
  myVote(candidateId: string): Answer | null;
  voterId(): string;
  allVotes(): Promise<VoteRecord[]>;
  allCandidates(): Promise<Candidate[]>;
  /** For the Explore panel: flagged dots near a position, and flagging one. */
  near(center: SkyPos, radiusArcsec: number): Promise<Candidate[]>;
  flag(fields: CandidateFields): Promise<Candidate>;
  /** When this browser flagged the dot (epoch ms), or null. */
  flaggedAt(candidateId: string): number | null;
}

export const VOTED_EVENT = "skyshift:voted";
export interface VotedDetail {
  candidateId: string;
  /** This browser's vote now, or null when it was taken back. */
  answer: Answer | null;
  /** This browser's vote before this action, or null for a first vote. */
  previous: Answer | null;
}

function announce(detail: VotedDetail): void {
  try {
    window.dispatchEvent(new CustomEvent<VotedDetail>(VOTED_EVENT, { detail }));
  } catch {
    /* no window (tests, workers): nothing to tell */
  }
}

/** Wrap a store with this browser's memory (its voter id, its votes, its flags). */
export function voteApi(store: VoteStore, kv: KV = safeStorage(), notice: string | null = null): VoteApi {
  const memory = new BrowserMemory(kv);
  return {
    mode: store.mode,
    notice,
    async countsFor(ids) {
      const unique = [...new Set(ids.filter((id) => typeof id === "string" && id))];
      const found = unique.length ? await store.counts(unique) : new Map<string, Counts>();
      return new Map(unique.map((id) => [id, found.get(id) ?? emptyCounts()]));
    },
    async castVote(candidateId, answer) {
      // Only the four answers here: taking a vote back is withdrawVote.
      if (!(ANSWERS as readonly unknown[]).includes(answer)) throw new VoteError("Pick one of the four answers.", "invalid");
      const previous = memory.votedFor(candidateId);
      if (previous === answer) return; // nothing changes: no new document
      try {
        await store.vote(candidateId, answer, memory.voter);
      } catch (e) {
        throw toVoteError(e);
      }
      memory.rememberVote(candidateId, answer);
      announce({ candidateId, answer, previous });
    },
    async withdrawVote(candidateId) {
      const previous = memory.votedFor(candidateId);
      if (!previous) return; // no vote from this browser: nothing to take back
      try {
        await store.vote(candidateId, WITHDRAWN, memory.voter);
      } catch (e) {
        throw toVoteError(e);
      }
      memory.forgetVote(candidateId);
      announce({ candidateId, answer: null, previous });
    },
    myVote: (candidateId) => memory.votedFor(candidateId),
    voterId: () => memory.voter,
    allVotes: () => store.allVotes().catch((e) => Promise.reject(toVoteError(e))),
    allCandidates: () => store.allCandidates().catch((e) => Promise.reject(toVoteError(e))),
    near: (center, radiusArcsec) => store.near(center, radiusArcsec).catch((e) => Promise.reject(toVoteError(e))),
    async flag(fields) {
      try {
        const c = await store.flag(fields);
        memory.rememberFlag(c.id);
        return c;
      } catch (e) {
        throw toVoteError(e);
      }
    },
    flaggedAt: (candidateId) => memory.flaggedAt(candidateId),
  };
}

/** Pipeline suspects from the static export, as read-only candidates (local mode only). */
async function staticSuspects(): Promise<Candidate[]> {
  const r = await loadSuspects();
  return (r?.suspects ?? [])
    .map((s) =>
      toCandidate(`pipeline-${s.id}`, { ra: s.ra, dec: s.dec, mjd: s.mjd, clock: s.clock, source: "pipeline", created: r?.generated }),
    )
    .filter((c): c is Candidate => c !== null);
}

/** Firestore if configured and the SDK loads; LocalStore otherwise. */
export async function openStore(kv: KV, env?: Record<string, unknown>): Promise<{ store: VoteStore; notice: string | null }> {
  const local = () => new LocalStore(kv, staticSuspects);
  const config = firebaseConfig(env);
  if (!config) return { store: local(), notice: null };
  try {
    const { FirestoreStore } = await import("./firestore");
    return { store: new FirestoreStore(config), notice: null };
  } catch {
    return { store: local(), notice: "The vote server didn't load, so votes stay in this browser for now." };
  }
}

let shared: Promise<VoteApi> | null = null;

/** The page's one vote API (Firestore or local mode). Never rejects. */
export function getVoteStore(): Promise<VoteApi> {
  if (!shared) {
    const kv = safeStorage();
    shared = openStore(kv).then(({ store, notice }) => voteApi(store, kv, notice));
  }
  return shared;
}

/** This browser only: candidates and votes kept in localStorage (falls back to memory). */
export class LocalStore implements VoteStore {
  readonly mode = "local" as const;
  /** The documents exactly as they would be written, for tests and inspection. */
  readonly written: { candidates: CandidateDoc<Date>[]; votes: VoteDoc<number>[] } = { candidates: [], votes: [] };

  constructor(
    private kv: KV,
    /** Extra read-only candidates, e.g. pipeline suspects from /data/suspects.json. */
    private extra: () => Promise<Candidate[]> = async () => [],
  ) {}

  private candidates(): Candidate[] {
    const raw = readJson<Record<string, unknown>>(this.kv, KEYS.localCandidates, {});
    return Object.entries(raw)
      .map(([id, d]) => toCandidate(id, d))
      .filter((c): c is Candidate => c !== null);
  }

  /** Every stored vote document, in the order written (changes and take-backs included). */
  private votes(): VoteDoc<number>[] {
    const raw = readJson<unknown[]>(this.kv, KEYS.localVotes, []);
    return Array.isArray(raw) ? (raw.filter((d) => d && typeof d === "object") as VoteDoc<number>[]) : [];
  }

  async near(center: SkyPos, radiusArcsec: number): Promise<Candidate[]> {
    let extra: Candidate[] = [];
    try {
      extra = await this.extra();
    } catch {
      extra = [];
    }
    return [...this.candidates(), ...extra].filter((c) => sepArcsec(c, center) <= radiusArcsec);
  }

  async counts(ids: string[]): Promise<Map<string, Counts>> {
    const want = new Set(ids);
    return countVotes(this.votes().filter((v) => want.has(v.candidate_id)));
  }

  async flag(fields: CandidateFields): Promise<Candidate> {
    const clean = candidateDoc(fields, new Date()); // same checks as the rules: source "user" only
    const id = `local-${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
    const raw = readJson<Record<string, unknown>>(this.kv, KEYS.localCandidates, {});
    raw[id] = clean; // created is stored as an ISO string and read back as ms
    this.kv.set(KEYS.localCandidates, JSON.stringify(raw));
    this.written.candidates.push(clean);
    return { id, ...clean, created: clean.created.getTime() };
  }

  /** Create-only, like the rules: a second vote from the same voter is a change of mind, kept as a new doc. */
  async vote(candidateId: string, answer: StoredAnswer, voter: string): Promise<void> {
    const doc = voteDoc(candidateId, answer, voter, Date.now());
    const all = this.votes();
    all.push(doc);
    this.kv.set(KEYS.localVotes, JSON.stringify(all));
    this.written.votes.push(doc);
  }

  async allVotes(): Promise<VoteRecord[]> {
    return latestVotes(this.votes())
      .map(toVoteRecord)
      .filter((v): v is VoteRecord => v !== null);
  }

  async allCandidates(): Promise<Candidate[]> {
    let extra: Candidate[] = [];
    try {
      extra = await this.extra();
    } catch {
      extra = [];
    }
    return [...this.candidates(), ...extra];
  }
}

/** Turn anything a store throws into plain words. */
export function toVoteError(e: unknown): VoteError {
  if (e instanceof VoteError) return e;
  const code = (e as { code?: unknown } | null)?.code;
  const offline = typeof navigator !== "undefined" && navigator.onLine === false;
  if (offline || code === "unavailable" || code === "deadline-exceeded")
    return new VoteError("Can't reach the vote server. Check your connection, then try again.", "offline");
  if (code === "permission-denied")
    return new VoteError("The vote server refused this. Each flag and vote needs all its fields.", "denied");
  if (code === "resource-exhausted")
    return new VoteError("The vote server is over its limit for now. Try again in a few minutes.", "busy");
  if (code === "failed-precondition" || code === "not-found" || code === "unauthenticated" || code === "invalid-argument")
    return new VoteError("The vote server isn't set up for this yet. Your flags can't be saved right now.", "setup");
  return new VoteError("Something went wrong talking to the vote server. Try again.", "unknown");
}

/** Reject after ms: Firestore keeps offline writes pending forever instead of failing. */
export function withTimeout<T>(p: Promise<T>, ms: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const t = setTimeout(() => reject(new VoteError("No answer from the vote server. Check your connection, then try again.", "offline")), ms);
    p.then(
      (v) => {
        clearTimeout(t);
        resolve(v);
      },
      (e) => {
        clearTimeout(t);
        reject(e);
      },
    );
  });
}

/** Firebase web config from Vite env, or null for local mode. */
export interface FirebaseConfig {
  apiKey: string;
  authDomain?: string;
  projectId: string;
  appId?: string;
}

export function firebaseConfig(env: Record<string, unknown> = import.meta.env): FirebaseConfig | null {
  const s = (k: string) => (typeof env[k] === "string" && (env[k] as string).trim() ? (env[k] as string).trim() : undefined);
  const apiKey = s("VITE_FIREBASE_API_KEY");
  const projectId = s("VITE_FIREBASE_PROJECT_ID");
  if (!apiKey || !projectId) return null;
  return { apiKey, projectId, authDomain: s("VITE_FIREBASE_AUTH_DOMAIN"), appId: s("VITE_FIREBASE_APP_ID") };
}
