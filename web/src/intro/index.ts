// The opening's entry point: decide whether it plays (rules.ts), add "Replay intro" to the
// footer, and show the overlay. It plays every time someone opens the home page, over the Sky
// map, which keeps loading underneath; Skip, Escape, scrolling to the end or the still card's
// own scroll all land on the map. It is in the main bundle on purpose: loaded on demand, the
// Sky page flashed up before the overlay covered it.
import "./replay.css";
import { playOpening, type OpeningHandle, type OpeningOptions } from "./opening";
import { browserEnv, openingMode, prefersReducedMotion, type OpeningEnv } from "./rules";

type Play = typeof playOpening;

let current: OpeningHandle | null = null;

/** Show the opening over `app`, unless one is already up. */
export function showOpening(app: HTMLElement, opts: OpeningOptions = {}, play: Play = playOpening): OpeningHandle | null {
  if (current) return null;
  current = play(app, {
    ...opts,
    onClose(reason) {
      current = null;
      opts.onClose?.(reason);
    },
  });
  return current;
}

/** Where the keyboard lands after the opening on the home page: the Sky map, when it is showing. */
export function skyMap(app: HTMLElement): HTMLElement | null {
  return app.querySelector<HTMLElement>("main:not([hidden]) [data-map]");
}

/**
 * Mount the opening: a "Replay intro" button in the footer, and the opening itself when this is
 * the home page (rules.ts). Returns whether it is showing now.
 */
export function mountOpening(app: HTMLElement, env: OpeningEnv = browserEnv(), play: Play = playOpening): boolean {
  const links = app.querySelector(".foot-links");
  if (links) {
    const replay = document.createElement("button");
    replay.type = "button";
    replay.className = "foot-replay";
    replay.textContent = "Replay intro";
    replay.addEventListener("click", () => {
      showOpening(app, { still: prefersReducedMotion(), returnFocus: () => replay }, play);
    });
    links.append(" ", replay);
  }
  const mode = openingMode(env);
  if (!mode) return false;
  return !!showOpening(app, { still: mode === "still", returnFocus: () => skyMap(app) }, play);
}
