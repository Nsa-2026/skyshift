// Firestore behind the VoteStore interface. index.ts imports this module dynamically, so the
// Firebase SDK is a separate chunk that loads after the first frame.
import { getApps, initializeApp } from "firebase/app";
import {
  addDoc,
  collection,
  getDocs,
  getFirestore,
  limit,
  query,
  serverTimestamp,
  where,
  type Firestore,
} from "firebase/firestore";
import type { SkyPos } from "../api/types";
import { sepArcsec } from "../app/astro";
import {
  candidateDoc,
  countVotes,
  toCandidate,
  toVoteRecord,
  voteDoc,
  type Answer,
  type Candidate,
  type CandidateFields,
  type Counts,
  type VoteRecord,
} from "./model";
import { toVoteError, withTimeout, type FirebaseConfig, type VoteStore } from "./store";

const TIMEOUT_MS = 12_000;
const MAX_NEAR = 500;
/** allVotes / allCandidates read at most this many documents (one read each). Firestore refuses
 * a query limit over 10,000 (INVALID_ARGUMENT), which left the Community page with no votes. */
const MAX_ALL = 10_000;
const IN_CHUNK = 30; // Firestore "in" takes at most 30 values
const COUNT_PARALLEL = 8; // "in" queries in flight at once while counting votes

export class FirestoreStore implements VoteStore {
  readonly mode = "firestore" as const;
  private db: Firestore;

  constructor(config: FirebaseConfig) {
    const app = getApps()[0] ?? initializeApp(config);
    this.db = getFirestore(app);
  }

  async near(center: SkyPos, radiusArcsec: number): Promise<Candidate[]> {
    // Firestore has no cone search: query a declination band, then cut the circle here.
    const r = radiusArcsec / 3600;
    const q = query(
      collection(this.db, "candidates"),
      where("dec", ">=", Math.max(-90, center.dec - r)),
      where("dec", "<=", Math.min(90, center.dec + r)),
      limit(MAX_NEAR),
    );
    try {
      const snap = await withTimeout(getDocs(q), TIMEOUT_MS);
      return snap.docs
        .map((d) => toCandidate(d.id, d.data()))
        .filter((c): c is Candidate => c !== null && sepArcsec(c, center) <= radiusArcsec);
    } catch (e) {
      throw toVoteError(e);
    }
  }

  async counts(ids: string[]): Promise<Map<string, Counts>> {
    const votes: { candidate_id: string; answer: Answer }[] = [];
    const chunks: string[][] = [];
    for (let i = 0; i < ids.length; i += IN_CHUNK) chunks.push(ids.slice(i, i + IN_CHUNK));
    // A few queries in flight at once: one after another, 1,333 candidates took 7–12 s.
    let next = 0;
    const worker = async () => {
      while (next < chunks.length) {
        const chunk = chunks[next++];
        const snap = await withTimeout(getDocs(query(collection(this.db, "votes"), where("candidate_id", "in", chunk))), TIMEOUT_MS);
        snap.forEach((d) => votes.push(d.data() as { candidate_id: string; answer: Answer }));
      }
    };
    try {
      await Promise.all(Array.from({ length: Math.min(COUNT_PARALLEL, chunks.length) }, worker));
    } catch (e) {
      throw toVoteError(e);
    }
    return countVotes(votes);
  }

  async flag(fields: CandidateFields): Promise<Candidate> {
    // created: serverTimestamp(); the rules require created == request.time (contracts/api.md v2.1).
    const doc = candidateDoc(fields, serverTimestamp());
    try {
      const ref = await withTimeout(addDoc(collection(this.db, "candidates"), doc), TIMEOUT_MS);
      // The server's time comes back on the next read; until then, this browser's clock.
      return { id: ref.id, ...doc, created: Date.now() };
    } catch (e) {
      throw toVoteError(e);
    }
  }

  async allVotes(): Promise<VoteRecord[]> {
    try {
      const snap = await withTimeout(getDocs(query(collection(this.db, "votes"), limit(MAX_ALL))), TIMEOUT_MS);
      return snap.docs.map((d) => toVoteRecord(d.data())).filter((v): v is VoteRecord => v !== null);
    } catch (e) {
      throw toVoteError(e);
    }
  }

  async allCandidates(): Promise<Candidate[]> {
    try {
      const snap = await withTimeout(getDocs(query(collection(this.db, "candidates"), limit(MAX_ALL))), TIMEOUT_MS);
      return snap.docs.map((d) => toCandidate(d.id, d.data())).filter((c): c is Candidate => c !== null);
    } catch (e) {
      throw toVoteError(e);
    }
  }

  async vote(candidateId: string, answer: Answer, voter: string): Promise<void> {
    const doc = voteDoc(candidateId, answer, voter, serverTimestamp());
    try {
      await withTimeout(addDoc(collection(this.db, "votes"), doc), TIMEOUT_MS);
    } catch (e) {
      throw toVoteError(e);
    }
  }
}
