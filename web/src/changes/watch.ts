// The browser's watch list: the stars someone follows, kept in localStorage only (no account, nothing sent).
// Storage can be off (private window, blocked site data) or throw: the list then lives in memory for this page.

export const WATCH_KEY = 'skyshift:following';

export interface WatchList {
  has(key: string): boolean;
  /** Follow or stop following; returns whether it is followed now. */
  toggle(key: string): boolean;
  keys(): ReadonlySet<string>;
  /** false: the browser would not keep it (the list lasts until the page closes). */
  readonly saved: boolean;
}

function storage(): Storage | null {
  try {
    const s = globalThis.localStorage;
    if (!s) return null;
    const probe = `${WATCH_KEY}:probe`;
    s.setItem(probe, '1');
    s.removeItem(probe);
    return s;
  } catch {
    return null;
  }
}

function read(s: Storage | null): Set<string> {
  if (!s) return new Set();
  try {
    const v = JSON.parse(s.getItem(WATCH_KEY) ?? '[]');
    return new Set(Array.isArray(v) ? v.filter((k): k is string => typeof k === 'string') : []);
  } catch {
    return new Set();
  }
}

export function openWatchList(): WatchList {
  const s = storage();
  const keys = read(s);
  let saved = s != null;
  const write = () => {
    if (!s) return;
    try {
      s.setItem(WATCH_KEY, JSON.stringify([...keys].sort()));
    } catch {
      saved = false;
    }
  };
  return {
    has: (k) => keys.has(k),
    toggle(k) {
      if (keys.has(k)) keys.delete(k);
      else keys.add(k);
      write();
      return keys.has(k);
    },
    keys: () => keys,
    get saved() {
      return saved;
    },
  };
}
