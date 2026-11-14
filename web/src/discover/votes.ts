// Discover reads votes through src/vote's shared API (getVoteStore, never edited here):
// counts per candidate id, this browser's own votes, and whether the counts are this device only.
import type { Counts } from "../vote/model";
import { VOTED_EVENT, getVoteStore } from "../vote/store";

export type VoteMode = "firestore" | "local";

export interface VoteReader {
  mode: VoteMode;
  /** A plain sentence when the vote server failed to load, else null. */
  notice: string | null;
  counts(ids: string[]): Promise<Map<string, Counts>>;
  /** This browser already voted on it. */
  votedOn(id: string): boolean;
}

export async function openVoteReader(): Promise<VoteReader> {
  const api = await getVoteStore();
  return {
    mode: api.mode,
    notice: api.notice,
    counts: (ids) => api.countsFor(ids),
    votedOn: (id) => api.myVote(id) !== null,
  };
}

/** Call back after any vote cast in this tab (src/vote fires the event). Returns an unsubscribe. */
export function onVoted(cb: () => void): () => void {
  const h = () => cb();
  addEventListener(VOTED_EVENT, h);
  return () => removeEventListener(VOTED_EVENT, h);
}
