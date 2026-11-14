// Read-only view of the community's votes for Discover, through src/vote's exported store and
// memory (never edited here). Firestore when configured, this browser's local store otherwise.
import { BrowserMemory, safeStorage, type Counts, type KV } from "../vote/model";
import { LocalStore, firebaseConfig, type VoteStore } from "../vote/store";

export type VoteMode = "firestore" | "local";

export interface VoteReader {
  mode: VoteMode;
  counts(ids: string[]): Promise<Map<string, Counts>>;
  /** This browser already voted on it. */
  votedOn(id: string): boolean;
}

export async function openVoteReader(kv: KV = safeStorage()): Promise<VoteReader> {
  const memory = new BrowserMemory(kv);
  const votedOn = (id: string) => memory.votedFor(id) !== null;
  let store: VoteStore = new LocalStore(kv);
  const config = firebaseConfig();
  if (config) {
    try {
      const { FirestoreStore } = await import("../vote/firestore");
      store = new FirestoreStore(config);
    } catch {
      /* the SDK didn't load: local counts */
    }
  }
  return { mode: store.mode, counts: (ids) => store.counts(ids), votedOn };
}
