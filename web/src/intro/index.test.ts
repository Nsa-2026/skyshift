import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mountOpening } from "./index";
import { browserEnv, type OpeningEnv } from "./rules";

const flush = () => new Promise((r) => setTimeout(r, 0));

/** Node 26 shadows jsdom's localStorage with an undefined getter: install a small one per test. */
function memoryStorage(): Storage {
  const m = new Map<string, string>();
  return {
    get length() {
      return m.size;
    },
    clear: () => m.clear(),
    getItem: (k) => (m.has(k) ? m.get(k)! : null),
    setItem: (k, v) => void m.set(k, String(v)),
    removeItem: (k) => void m.delete(k),
    key: (i) => [...m.keys()][i] ?? null,
  };
}

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
    const store = memoryStorage();
    Object.defineProperty(window, "localStorage", { configurable: true, get: () => store });
    vi.spyOn(window, "sessionStorage", "get").mockReturnValue(memoryStorage());
    app = document.createElement("div");
    app.innerHTML = `<footer class="foot"><p class="foot-links"><a href="#/about">About</a></p></footer>`;
    document.body.append(app);
  });
  afterEach(() => {
    closeLast();
    document.body.replaceChildren();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    history.replaceState(null, "", "/");
  });

  const env = (over: Partial<OpeningEnv> = {}): OpeningEnv => ({
    hash: "",
    search: "",
    reducedMotion: false,
    ...over,
  });

  it("adds a Replay intro button to the footer", () => {
    mountOpening(app, env({ hash: "#/about", search: "?intro=0" }), load);
    const replay = app.querySelector<HTMLButtonElement>(".foot-links .foot-replay")!;
    expect(replay.textContent).toBe("Replay intro");
    expect(replay.type).toBe("button");
  });

  it("plays once in a tab, skips refreshes, and plays again in a new tab", async () => {
    expect(mountOpening(app, undefined, load)).toBe(true);
    await flush();
    expect(playOpening).toHaveBeenCalledTimes(1);
    closeLast();
    expect(mountOpening(app, undefined, load)).toBe(false);
    await flush();
    expect(load).toHaveBeenCalledTimes(1);
    // Closing a tab discards its sessionStorage; reopening has a fresh store.
    vi.spyOn(window, "sessionStorage", "get").mockReturnValue(memoryStorage());
    expect(mountOpening(app, undefined, load)).toBe(true);
    await flush();
    expect(load).toHaveBeenCalledTimes(2);
    expect(playOpening).toHaveBeenCalledTimes(2);
  });

  it("records the visit before the animation loads or closes", async () => {
    let resolve!: () => void;
    const slow = vi.fn(() => new Promise<never>(r => (resolve = () => r({ playOpening } as never))));
    expect(mountOpening(app, undefined, slow)).toBe(true);
    expect(browserEnv().visitedInTab).toBe(true);
    resolve();
    await flush();
  });

  it("allows manual replay after a refresh", async () => {
    mountOpening(app, undefined, load);
    await flush();
    closeLast();
    const refreshed = document.createElement("div");
    refreshed.innerHTML = '<p class="foot-links"></p>';
    document.body.append(refreshed);
    expect(mountOpening(refreshed, undefined, load)).toBe(false);
    refreshed.querySelector<HTMLButtonElement>(".foot-replay")!.click();
    await flush();
    expect(playOpening).toHaveBeenCalledTimes(2);
  });

  it("remembers an explicit skip even if the Viewer later rewrites the query", () => {
    history.replaceState(null, "", "/?intro=0");
    expect(mountOpening(app, undefined, load)).toBe(false);
    history.replaceState(null, "", "/?t=barnard#/explore");
    expect(mountOpening(app, undefined, load)).toBe(false);
    expect(load).not.toHaveBeenCalled();
  });

  it("does not consume the opening on an embedded visit", () => {
    expect(mountOpening(app, env({ search: "?embed=1" }), load)).toBe(false);
    expect(browserEnv().visitedInTab).toBe(false);
  });

  it("ignores an old seen marker without reading, rewriting or deleting it", async () => {
    history.replaceState(null, "", "/");
    vi.stubGlobal("matchMedia", () => ({ matches: false }));
    window.localStorage.setItem("skyshift.opening.v1", "1");
    const read = vi.spyOn(window.localStorage, "getItem");
    const write = vi.spyOn(window.localStorage, "setItem");
    const remove = vi.spyOn(window.localStorage, "removeItem");
    expect(mountOpening(app, undefined, load)).toBe(true);
    await flush();
    expect(playOpening).toHaveBeenCalledTimes(1);
    expect(read).not.toHaveBeenCalled();
    expect(write).not.toHaveBeenCalled();
    expect(remove).not.toHaveBeenCalled();
  });

  it("plays the home opening when access to browser storage throws", async () => {
    history.replaceState(null, "", "/");
    vi.stubGlobal("matchMedia", () => ({ matches: false }));
    const storage = vi.spyOn(window, "localStorage", "get").mockImplementation(() => { throw new Error("denied"); });
    expect(mountOpening(app, undefined, load)).toBe(true);
    await flush();
    expect(playOpening).toHaveBeenCalledTimes(1);
    expect(storage).not.toHaveBeenCalled();
  });

  it("keeps the site usable when tab storage is blocked or full", async () => {
    const blocked = () => { throw new Error("denied"); };
    vi.spyOn(window, "sessionStorage", "get").mockImplementation(blocked);
    expect(mountOpening(app, undefined, load)).toBe(true);
    await flush();
    closeLast();
    vi.spyOn(window, "sessionStorage", "get").mockReturnValue({ ...memoryStorage(), setItem: blocked });
    expect(mountOpening(app, undefined, load)).toBe(true);
    await flush();
    expect(playOpening).toHaveBeenCalledTimes(2);
  });

  it("shows a still title on reduced-motion page loads", async () => {
    expect(mountOpening(app, env({ reducedMotion: true }), load)).toBe(true);
    await flush();
    expect(playOpening.mock.calls[0][1]).toMatchObject({ still: true });
  });

  it("plays when the browser reopens a sky view the map wrote into the address", async () => {
    expect(mountOpening(app, env({ hash: "#/sky/42,10,360,mol", search: "?utm_source=mail" }), load)).toBe(true);
    await flush();
    expect(playOpening).toHaveBeenCalledTimes(1);
  });

  it("plays on deep links too", async () => {
    expect(mountOpening(app, env({ hash: "#/explore", search: "?t=barnard" }), load)).toBe(true);
    await flush();
    expect(playOpening).toHaveBeenCalledTimes(1);
  });

  it("doesn't load the module for embeds or ?intro=0", async () => {
    expect(mountOpening(app, env({ search: "?embed=1" }), load)).toBe(false);
    expect(mountOpening(app, env({ search: "?intro=0", hash: "#/sky" }), load)).toBe(false);
    await flush();
    expect(load).not.toHaveBeenCalled();
  });

  it("replays from its explicit link with the still reduced-motion option", async () => {
    expect(mountOpening(app, env({ search: "?intro=1", hash: "#/sky", reducedMotion: true }), load)).toBe(true);
    await flush();
    expect(playOpening.mock.calls[0][1]).toMatchObject({ still: true });
  });

  it("replays on request, and only one at a time", async () => {
    mountOpening(app, env({ hash: "#/about", search: "?intro=0" }), load);
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
    window.dispatchEvent(new HashChangeEvent("hashchange"));
    resolve();
    await flush();
    expect(playOpening).not.toHaveBeenCalled();
  });

  it("cancels a delayed replay when the visitor navigates before it loads", async () => {
    let resolve!: () => void;
    const slow = vi.fn(() => new Promise<never>((r) => (resolve = () => r({ playOpening } as never))));
    mountOpening(app, env({ hash: "#/about", search: "?intro=0" }), slow);
    app.querySelector<HTMLButtonElement>(".foot-replay")!.click();
    window.dispatchEvent(new HashChangeEvent("hashchange"));
    resolve();
    await flush();
    expect(playOpening).not.toHaveBeenCalled();
  });

  it("does not mount an overlay over an app removed while the module loads", async () => {
    let resolve!: () => void;
    const slow = vi.fn(() => new Promise<never>((r) => (resolve = () => r({ playOpening } as never))));
    mountOpening(app, env({}), slow);
    app.remove();
    resolve();
    await flush();
    expect(playOpening).not.toHaveBeenCalled();
  });

  it("still plays when the Sky page writes its view into the URL meanwhile (replaceState)", async () => {
    let resolve!: () => void;
    const slow = vi.fn(() => new Promise<never>((r) => (resolve = () => r({ playOpening } as never))));
    mountOpening(app, env({}), slow);
    history.replaceState(null, "", "#/sky/0,0,180");
    resolve();
    await flush();
    expect(playOpening).toHaveBeenCalledTimes(1);
    history.replaceState(null, "", "/");
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
