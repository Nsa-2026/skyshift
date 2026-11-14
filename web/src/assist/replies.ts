// The person's replies to the checklist's "your check" steps, kept per candidate in this browser
// (localStorage through src/vote's safe wrapper, which never throws and falls back to memory).
import { safeStorage, type KV } from "../vote/model";
import type { Reply, StepId } from "./types";

export const REPLIES_KEY = "skyshift.assist.replies";
/** Fired on window after a reply changes, detail { candidateId }, so drafts can refresh. */
export const REPLY_EVENT = "skyshift:assist-reply";

const STEPS: StepId[] = ["motion", "known", "artefact", "same", "colour", "call"];
const REPLIES: Reply[] = ["yes", "no", "unsure"];

let shared: KV | null = null;
const store = (kv?: KV) => kv ?? (shared ??= safeStorage());

type All = Record<string, Partial<Record<StepId, Reply>>>;

function readAll(kv: KV): All {
  try {
    const v = kv.get(REPLIES_KEY);
    const parsed: unknown = v ? JSON.parse(v) : null;
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? (parsed as All) : {};
  } catch {
    return {};
  }
}

/** This browser's replies for one candidate. Unknown keys and values are dropped. */
export function loadReplies(candidateId: string, kv?: KV): Partial<Record<StepId, Reply>> {
  const all = readAll(store(kv));
  const mine = all[candidateId];
  const out: Partial<Record<StepId, Reply>> = {};
  if (!mine || typeof mine !== "object") return out;
  for (const s of STEPS) if (REPLIES.includes(mine[s] as Reply)) out[s] = mine[s];
  return out;
}

/** Save (or with null, clear) one reply, then tell the page. */
export function saveReply(candidateId: string, step: StepId, reply: Reply | null, kv?: KV): void {
  const k = store(kv);
  const all = readAll(k);
  const mine = { ...(all[candidateId] ?? {}) };
  if (reply) mine[step] = reply;
  else delete mine[step];
  if (Object.keys(mine).length) all[candidateId] = mine;
  else delete all[candidateId];
  k.set(REPLIES_KEY, JSON.stringify(all));
  try {
    window.dispatchEvent(new CustomEvent(REPLY_EVENT, { detail: { candidateId } }));
  } catch {
    /* no window */
  }
}
