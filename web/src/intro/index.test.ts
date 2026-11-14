import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mountOpening } from "./index";
import { SEEN_KEY, type OpeningEnv, type SeenStore } from "./rules";

function memory(): SeenStore {
  const values = new Map<string, string>();
  return { getItem: (k) => values.get(k) ?? null, setItem: (k, v) => void values.set(k, v) };
}
const flush = () => new Promise((r) => setTimeout(r, 0));

describe("mountOpening", () => {
  let app: HTMLElement;
  const playOpening = vi.fn((_app: HTMLElement, opts: { onClose?(r: string): void }) => {
    closeLast = () => opts.onClose?.("skip");
    return { element: document.createElement("div"), close() {} };
  });
  let closeLast: () => void = () => {};
  const load = vi.fn(async () => ({ playOpening }) as never);

  beforeEach(() => {
    vi.clearAllMocks();
    app = document.createElement("div");
    app.innerHTML = `<footer class="foot"><p class="foot-links"><a href="#/about">About</a></p></footer>`;
    document.body.append(app);
  });
  afterEach(() => {
    closeLast();
    document.body.replaceChildren();
  });

  const env = (over: Partial<OpeningEnv>, store: SeenStore = memory()): OpeningEnv => ({
    hash: "",
    search: "",
    reducedMotion: false,
    store: () => store,
    ...over,
  });

  it("adds a Replay the opening button to the footer", () => {
    mountOpening(app, env({ hash: "#/about" }), load);
    const replay = app.querySelector<HTMLButtonElement>(".foot-links .foot-replay")!;
    expect(replay.textContent).toBe("Replay the opening");
    expect(replay.type).toBe("button");
  });

  it("loads and plays the opening on a first visit to the bare home URL", async () => {
    const store = memory();
    expect(mountOpening(app, env({}, store), load)).toBe(true);
    await flush();
    expect(load).toHaveBeenCalledTimes(1);
    expect(playOpening).toHaveBeenCalledTimes(1);
    expect(store.getItem(SEEN_KEY)).toBe("1");
  });

  it("doesn't even load the module for a deep link, a second visit or reduced motion", async () => {
    const seen = memory();
    seen.setItem(SEEN_KEY, "1");
    expect(mountOpening(app, env({ hash: "#/explore" }), load)).toBe(false);
    expect(mountOpening(app, env({}, seen), load)).toBe(false);
    expect(mountOpening(app, env({ reducedMotion: true }), load)).toBe(false);
    expect(mountOpening(app, env({ search: "?t=barnard" }), load)).toBe(false);
    await flush();
    expect(load).not.toHaveBeenCalled();
  });

  it("doesn't play when storage throws", async () => {
    const shown = mountOpening(app, { ...env({}), store: () => { throw new Error("denied"); } }, load);
    await flush();
    expect(shown).toBe(false);
    expect(load).not.toHaveBeenCalled();
  });

  it("replays on request, and only one at a time", async () => {
    mountOpening(app, env({ hash: "#/about" }), load);
    const replay = app.querySelector<HTMLButtonElement>(".foot-replay")!;
    replay.click();
    replay.click();
    await flush();
    expect(playOpening).toHaveBeenCalledTimes(1);
    expect(playOpening.mock.calls[0][1]).toMatchObject({ returnFocus: replay });
    closeLast();
    replay.click();
    await flush();
    expect(playOpening).toHaveBeenCalledTimes(2);
  });

  it("stays away if the visitor navigated while the module loaded", async () => {
    let resolve!: () => void;
    const slow = vi.fn(() => new Promise<never>((r) => (resolve = () => r({ playOpening } as never))));
    mountOpening(app, env({}), slow);
    location.hash = "#/explore";
    resolve();
    await flush();
    expect(playOpening).not.toHaveBeenCalled();
    location.hash = "";
  });

  it("shows the site as normal when the module fails to load", async () => {
    const broken = vi.fn(() => Promise.reject(new Error("chunk failed")));
    expect(() => mountOpening(app, env({}), broken)).not.toThrow();
    await flush();
    // And a later replay still works.
    app.querySelector<HTMLButtonElement>(".foot-replay")!.click();
    await flush();
    expect(broken).toHaveBeenCalledTimes(2);
  });
});
