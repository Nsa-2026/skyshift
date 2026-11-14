// When the opening plays: every time someone opens the home page itself. Never for a deep link
// (a route in the hash, ?t=, ?ra=&dec= or any other query) and never in an embed. With reduced
// motion it is a still title card instead of the scroll journey; Skip works the same.

/** Tags that links pick up on the way (social posts, newsletters): they don't make a deep link. */
const TRACKING = /^(utm_\w+|fbclid|gclid|msclkid)$/i;

export interface OpeningEnv {
  hash: string;
  search: string;
  reducedMotion: boolean;
}

/** The bare home URL: no route in the hash, no query beyond tracking tags. */
export function isBareHome(hash: string, search: string): boolean {
  if (hash !== "" && hash !== "#" && hash !== "#/") return false;
  for (const key of new URLSearchParams(search).keys()) if (!TRACKING.test(key)) return false;
  return true;
}

export type OpeningMode = "play" | "still";

/** How the opening shows for this URL, or null when it stays away. */
export function openingMode(env: OpeningEnv): OpeningMode | null {
  if (!isBareHome(env.hash, env.search)) return null;
  return env.reducedMotion ? "still" : "play";
}

export function prefersReducedMotion(): boolean {
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return true; // no matchMedia: keep it still
  }
}

// The URL as the person opened it, read when this module loads (before the app starts). By the
// time the app mounts the opening, the router may have rewritten a legacy hash and the Sky page
// may have written its view into the hash: neither makes a deep link.
const OPENED = typeof location === "undefined" ? { hash: "", search: "" } : { hash: location.hash, search: location.search };

export function browserEnv(): OpeningEnv {
  return { ...OPENED, reducedMotion: prefersReducedMotion() };
}
