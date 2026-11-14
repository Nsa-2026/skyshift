import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mountOpening, skyMap } from "./index";
import type { OpeningOptions } from "./opening";
import type { OpeningEnv } from "./rules";

describe("mountOpening", () => {
  let app: HTMLElement;
  let lastOpts: OpeningOptions = {};
  const playOpening = vi.fn((_app: HTMLElement, opts: OpeningOptions = {}) => {
    lastOpts = opts;
    return { element: document.createElement("div"), close: () => opts.onClose?.("skip") };
  });
  const closeLast = () => lastOpts.onClose?.("skip");

  beforeEach(() => {
    vi.clearAllMocks();
    lastOpts = {};
    app = document.createElement("div");
    app.innerHTML = `
      <main class="page" data-page="atlas"><div data-map tabindex="0"></div></main>
      <main class="page" data-page="about" hidden><div data-map tabindex="0" id="hidden-map"></div></main>
      <footer class="foot"><p class="foot-links"><a href="#/about">About</a></p></footer>`;
    document.body.append(app);
  });
  afterEach(() => {
    closeLast();
    document.body.replaceChildren();
  });

  const env = (over: Partial<OpeningEnv> = {}): OpeningEnv => ({ hash: "", search: "", reducedMotion: false, ...over });

  it("adds a Replay intro button to the footer", () => {
    mountOpening(app, env({ hash: "#/about" }), playOpening);
    const replay = app.querySelector<HTMLButtonElement>(".foot-links .foot-replay")!;
    expect(replay.textContent).toBe("Replay intro");
    expect(replay.type).toBe("button");
  });

  it("plays at once on the home page, and lands on the Sky map", () => {
    expect(mountOpening(app, env(), playOpening)).toBe(true);
    expect(playOpening).toHaveBeenCalledTimes(1);
    expect(lastOpts.still).toBe(false);
    expect(lastOpts.returnFocus?.()).toBe(app.querySelector('[data-page="atlas"] [data-map]'));
  });

  it("plays every time, not once per browser", () => {
    expect(mountOpening(app, env(), playOpening)).toBe(true);
    closeLast();
    expect(mountOpening(app, env(), playOpening)).toBe(true);
    expect(playOpening).toHaveBeenCalledTimes(2);
  });

  it("is a still card with reduced motion", () => {
    expect(mountOpening(app, env({ reducedMotion: true }), playOpening)).toBe(true);
    expect(lastOpts.still).toBe(true);
  });

  it.each([{ hash: "#/explore" }, { hash: "#/c/abc" }, { hash: "#/sky" }, { search: "?t=barnard" }, { search: "?ra=1&dec=2" }, { search: "?embed=1" }])(
    "stays away for the deep link %o",
    (over) => {
      expect(mountOpening(app, env(over), playOpening)).toBe(false);
      expect(playOpening).not.toHaveBeenCalled();
    },
  );

  it("replays on request, one at a time, and gives focus back to the button", () => {
    mountOpening(app, env({ hash: "#/about" }), playOpening);
    const replay = app.querySelector<HTMLButtonElement>(".foot-replay")!;
    replay.click();
    replay.click();
    expect(playOpening).toHaveBeenCalledTimes(1);
    expect(lastOpts.returnFocus?.()).toBe(replay);
    closeLast();
    replay.click();
    expect(playOpening).toHaveBeenCalledTimes(2);
  });

  it("finds the Sky map only on the page that shows", () => {
    app.querySelector<HTMLElement>('[data-page="atlas"]')!.hidden = true;
    expect(skyMap(app)).toBeNull();
  });
});
