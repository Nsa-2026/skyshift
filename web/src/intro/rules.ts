// The opening plays once per tab. sessionStorage survives refreshes and is cleared when
// the tab closes, so reloading keeps the current URL and a new tab gets the journey again.
// Embeds and `?intro=0` skip it; reduced motion gets the still title card.

const TAB_VISIT_KEY = "skyshift.opening.tab.v1";

export interface OpeningEnv {
  hash: string;
  search: string;
  reducedMotion: boolean;
  visitedInTab?: boolean;
}

export type OpeningMode = "play" | "still";

/** Embedded views (the MCP and other sites) and an explicit `?intro=0` never show the opening. */
export function skipsOpening(search: string): boolean {
  const query = new URLSearchParams(search);
  return query.get("embed") === "1" || query.get("intro") === "0";
}

/** Refreshing an existing tab leaves its current page visible. */
export function openingMode(env: OpeningEnv): OpeningMode | null {
  if (env.visitedInTab || skipsOpening(env.search)) return null;
  return env.reducedMotion ? "still" : "play";
}

export function browserEnv(): OpeningEnv {
  let reducedMotion = true;
  let visitedInTab = false;
  try {
    reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    /* no matchMedia: keep the opening still */
  }
  try {
    visitedInTab = window.sessionStorage.getItem(TAB_VISIT_KEY) === "1";
  } catch {
    /* blocked storage: the site and opening still work */
  }
  return {
    hash: location.hash,
    search: location.search,
    reducedMotion,
    visitedInTab,
  };
}

/** Record entry before the lazy opening loads, including visits with `?intro=0`. */
export function rememberTabVisit(): void {
  try {
    window.sessionStorage.setItem(TAB_VISIT_KEY, "1");
  } catch {
    /* storage is optional */
  }
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
