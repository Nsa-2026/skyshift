// Course progress, kept in this browser only (localStorage, never required). Storage that
// throws (private windows, blocked site data, previews) falls back to memory for the visit.
import { safeStorage, type KV } from "../vote/model";

export const PROGRESS_KEY = "skyshift.learn.v1";

export interface Progress {
  /** Lessons finished (1-based numbers), ascending. */
  done: number[];
  /** The lesson last opened, or null before the first. */
  last: number | null;
}

const empty = (): Progress => ({ done: [], last: null });

export function readProgress(kv: KV, count: number): Progress {
  try {
    const raw = kv.get(PROGRESS_KEY);
    if (!raw) return empty();
    const p = JSON.parse(raw) as Partial<Progress>;
    const ok = (n: unknown): n is number => Number.isInteger(n) && (n as number) >= 1 && (n as number) <= count;
    const done = Array.isArray(p.done) ? [...new Set(p.done.filter(ok))].sort((a, b) => a - b) : [];
    return { done, last: ok(p.last) ? p.last : null };
  } catch {
    return empty();
  }
}

function write(kv: KV, p: Progress): Progress {
  try {
    kv.set(PROGRESS_KEY, JSON.stringify(p));
  } catch {
    /* memory only */
  }
  return p;
}

export function markOpened(kv: KV, count: number, n: number): Progress {
  const p = readProgress(kv, count);
  if (n < 1 || n > count) return p;
  return write(kv, { ...p, last: n });
}

export function markDone(kv: KV, count: number, n: number): Progress {
  const p = readProgress(kv, count);
  if (n < 1 || n > count || p.done.includes(n)) return p;
  return write(kv, { ...p, done: [...p.done, n].sort((a, b) => a - b) });
}

export function resetProgress(kv: KV): Progress {
  return write(kv, empty());
}

/** Where "Continue" should take someone: the first lesson not done after the last opened, else the first not done. */
export function nextLesson(p: Progress, count: number): number {
  const notDone = (n: number) => !p.done.includes(n);
  if (p.last) {
    for (let n = p.last; n <= count; n++) if (notDone(n)) return n;
  }
  for (let n = 1; n <= count; n++) if (notDone(n)) return n;
  return 1;
}

export { safeStorage };
export type { KV };
