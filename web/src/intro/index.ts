// The opening's entry point, the only part in the main bundle: decide whether it plays (rules.ts),
// add "Replay intro" to the footer, and load the overlay itself only when it is shown.
import "./replay.css";
import { browserEnv, openingMode, type OpeningEnv } from "./rules";
import type { OpeningOptions } from "./opening";

type Loader = () => Promise<typeof import("./opening")>;
const loadOpening: Loader = () => import("./opening");

let playing = false;

/** Play the opening over `app` (no-op while one is already up). Resolves once it is on screen. */
export async function playOpeningLater(
  app: HTMLElement,
  opts: OpeningOptions & { auto?: boolean } = {},
  load: Loader = loadOpening,
): Promise<boolean> {
  if (playing) return false;
  playing = true;
  // A first visit or replay may navigate while the module loads (a nav link, Back):
  // then it stays away rather than opening over the newly selected page.
  // Only real navigations count: the Sky page writes its view with replaceState, which fires no
  // hashchange, and that must not cancel the opening.
  let navigated = false;
  const onHash = () => (navigated = true);
  addEventListener("hashchange", onHash);
  try {
    const { playOpening } = await load();
    if (navigated || !app.isConnected) {
      playing = false;
      return false;
    }
    playOpening(app, {
      ...opts,
      onClose(reason) {
        playing = false;
        opts.onClose?.(reason);
      },
    });
    return true;
  } catch {
    // The chunk didn't load (offline, a new deploy): the site simply shows without it.
    playing = false;
    return false;
  } finally {
    removeEventListener("hashchange", onHash);
  }
}

function prefersReducedMotion(): boolean {
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return true;
  }
}

/**
 * Mount the opening: a "Replay intro" button in the footer, and the opening itself on
 * every page load (rules.ts). Returns whether it is starting now.
 */
export function mountOpening(app: HTMLElement, env: OpeningEnv = browserEnv(), load: Loader = loadOpening): boolean {
  const links = app.querySelector(".foot-links");
  if (links) {
    const replay = document.createElement("button");
    replay.type = "button";
    replay.className = "foot-replay";
    replay.textContent = "Replay intro";
    replay.addEventListener("click", () => {
      void playOpeningLater(app, { still: prefersReducedMotion(), returnFocus: replay }, load);
    });
    links.append(" ", replay);
  }
  const mode = openingMode(env);
  if (mode) void playOpeningLater(app, { auto: true, still: mode === "still" }, load);
  return mode !== null;
}
