// Where flags and votes go. FirestoreStore (./firestore.ts, loaded lazily) when the Firebase
// config is present; LocalStore otherwise and in tests. Both write exactly the contract's shapes.
import type { SkyPos } from "../api/types";
import { sepArcsec } from "../app/astro";
import {
  KEYS,
  VoteError,
  candidateDoc,
  countVotes,
  readJson,
  toCandidate,
  voteDoc,
  type Answer,
  type Candidate,
  type CandidateDoc,
  type Counts,
  type KV,
  type VoteDoc,
} from "./model";

export interface VoteStore {
  readonly mode: "firestore" | "local";
  /** Candidates within radius (arcsec) of center, any order. */
  near(center: SkyPos, radiusArcsec: number): Promise<Candidate[]>;
  /** Vote counts per candidate id (ids without votes may be missing). */
  counts(ids: string[]): Promise<Map<string, Counts>>;
  /** Create a `candidates` doc; returns it with its new id. */
  flag(doc: CandidateDoc): Promise<Candidate>;
  /** Create a `votes` doc. */
  vote(candidateId: string, answer: Answer, voter: string): Promise<void>;
}

/** This browser only: candidates and votes kept in localStorage (falls back to memory). */
export class LocalStore implements VoteStore {
  readonly mode = "local" as const;
  /** The documents exactly as they would be written, for tests and inspection. */
  readonly written: { candidates: CandidateDoc[]; votes: VoteDoc<number>[] } = { candidates: [], votes: [] };

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

  private votes(): VoteDoc<number>[] {
    const raw = readJson<unknown[]>(this.kv, KEYS.localVotes, []);
    return Array.isArray(raw) ? (raw as VoteDoc<number>[]) : [];
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

  async flag(doc: CandidateDoc): Promise<Candidate> {
    const clean = candidateDoc(doc); // same checks as the rules: source "user" only
    const id = `local-${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
    const raw = readJson<Record<string, unknown>>(this.kv, KEYS.localCandidates, {});
    raw[id] = clean;
    this.kv.set(KEYS.localCandidates, JSON.stringify(raw));
    this.written.candidates.push(clean);
    return { id, ...clean };
  }

  async vote(candidateId: string, answer: Answer, voter: string): Promise<void> {
    const doc = voteDoc(candidateId, answer, voter, Date.now());
    const all = this.votes();
    if (all.some((v) => v.candidate_id === candidateId && v.voter === voter))
      throw new VoteError("This browser already voted on this dot.", "already");
    all.push(doc);
    this.kv.set(KEYS.localVotes, JSON.stringify(all));
    this.written.votes.push(doc);
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
    return new VoteError("The vote server refused this. Votes can't be changed once cast, and each one needs all its fields.", "denied");
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
