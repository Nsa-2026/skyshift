// The opening plays every time the site is opened (a full page load), whatever the address:
// the home, a sky view the map wrote into the URL, a target link, a restored browser tab.
// In-app navigation never replays it. Embeds and `?intro=0` skip it; reduced motion gets the
// still title card instead of the journey.

export interface OpeningEnv {
  hash: string;
  search: string;
  reducedMotion: boolean;
}

export type OpeningMode = "play" | "still";

/** Embedded views (the MCP and other sites) and an explicit `?intro=0` never show the opening. */
export function skipsOpening(search: string): boolean {
  const query = new URLSearchParams(search);
  return query.get("embed") === "1" || query.get("intro") === "0";
}

/** Every page load plays the opening; no persistent storage gate is read or written. */
export function openingMode(env: OpeningEnv): OpeningMode | null {
  if (skipsOpening(env.search)) return null;
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

/**
 * Where the site lands under the opening: the Sky, never the Viewer (user, 15 Nov). A Viewer
 * address (the bare home with ?t= or ?ra=, or #/explore…) is replaced by "#/sky"; every other
 * page (a candidate, To check, Results, Learn, practice) keeps its address. null: leave it as is.
 */
export function landingUnderOpening(env: OpeningEnv): string | null {
  if (!openingMode(env)) return null;
  const hash = env.hash.replace(/^#\/?/, "");
  const query = new URLSearchParams(env.search);
  const target = query.has("t") || query.has("ra");
  if (hash === "" ? target : hash === "explore" || hash.startsWith("explore/")) return "#/sky";
  return null;
}
