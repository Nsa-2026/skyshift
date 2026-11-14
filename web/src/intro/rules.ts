// When the opening plays. It must never get in the way: once per browser, on the bare home
// URL only, never for a deep link or an embed, never with reduced motion, and never when
// storage can't remember that it has played.

export const SEEN_KEY = "skyshift.opening.v1";

/** The part of Storage the rules use (localStorage in the browser, a fake in tests). */
export interface SeenStore {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export interface OpeningEnv {
  hash: string;
  search: string;
  reducedMotion: boolean;
  /** Returns the store, or throws when the browser denies storage. */
  store(): SeenStore | null;
}

/** The bare home URL: no query (deep links, embeds) and no route beyond the home itself. */
export function isBareHome(hash: string, search: string): boolean {
  return (search === "" || search === "?") && (hash === "" || hash === "#" || hash === "#/");
}

/**
 * True when the opening should play now. It also records the play, so a reload or a second
 * tab never shows it again. Any storage error means "don't show": better to skip it for
 * someone than to show it on every visit.
 */
export function claimOpening(env: OpeningEnv): boolean {
  if (!isBareHome(env.hash, env.search) || env.reducedMotion) return false;
  try {
    const store = env.store();
    if (!store || store.getItem(SEEN_KEY) !== null) return false;
    store.setItem(SEEN_KEY, "1");
    return store.getItem(SEEN_KEY) === "1";
  } catch {
    return false;
  }
}

export function browserEnv(): OpeningEnv {
  let reducedMotion = true;
  try {
    reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    /* no matchMedia: treat as reduced, so the opening stays out of the way */
  }
  return {
    hash: location.hash,
    search: location.search,
    reducedMotion,
    store: () => window.localStorage,
  };
}
