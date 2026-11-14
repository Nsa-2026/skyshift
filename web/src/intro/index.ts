// The opening's entry point, the only part in the main bundle: decide whether it plays (rules.ts),
// add "Replay the opening" to the footer, and load the overlay itself only when it is shown.
import "./replay.css";
import { browserEnv, claimOpening, isBareHome, type OpeningEnv } from "./rules";
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
  try {
    const { playOpening } = await load();
    // A first visit may have moved on while the module loaded: then it stays away.
    if (opts.auto && !isBareHome(location.hash, location.search)) {
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
 * Mount the opening: a "Replay the opening" button in the footer, and the opening itself on a
 * first visit to the bare home URL. Returns whether it is starting now.
 */
export function mountOpening(app: HTMLElement, env: OpeningEnv = browserEnv(), load: Loader = loadOpening): boolean {
  const links = app.querySelector(".foot-links");
  if (links) {
    const replay = document.createElement("button");
    replay.type = "button";
    replay.className = "foot-replay";
    replay.textContent = "Replay the opening";
    replay.addEventListener("click", () => {
      void playOpeningLater(app, { still: prefersReducedMotion(), returnFocus: replay }, load);
    });
    links.append(" ", replay);
  }
  const show = claimOpening(env);
  if (show) void playOpeningLater(app, { auto: true }, load);
  return show;
}
