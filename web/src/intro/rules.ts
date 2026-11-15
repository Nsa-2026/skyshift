// Show the opening on every home visit. Observation links, area routes and embeds keep
// their direct entry; reduced motion gets the still title card instead of the journey.

/** Attribution tags do not turn a home visit into an observation/deep link. */
const TRACKING = /^(utm_\w+|fbclid|gclid|msclkid)$/i;

export interface OpeningEnv {
  hash: string;
  search: string;
  reducedMotion: boolean;
}

/** The home itself, optionally carrying only attribution tags. */
export function isBareHome(hash: string, search: string): boolean {
  return (hash === "" || hash === "#" || hash === "#/") &&
    [...new URLSearchParams(search).keys()].every(key => TRACKING.test(key));
}

/** Explicit local-review/replay link. Observation links retain their normal entry. */
export function isOpeningPreview(hash: string, search: string): boolean {
  const query = new URLSearchParams(search);
  return query.get('intro') === '1' && [...query.keys()].every(key => key === 'intro' || TRACKING.test(key)) &&
    (isBareHome(hash, '') || hash === '#/sky' || hash.startsWith('#/sky/'));
}

export type OpeningMode = "play" | "still";

/** Visiting the home is sufficient; no persistent storage gate is read or written. */
export function openingMode(env: OpeningEnv): OpeningMode | null {
  if (!isBareHome(env.hash, env.search)) return null;
  return env.reducedMotion ? "still" : "play";
}

export function browserEnv(): OpeningEnv {
  let reducedMotion = true;
  try {
    reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    /* no matchMedia: keep the opening still */
  }
  return {
    hash: location.hash,
    search: location.search,
    reducedMotion,
  };
}
