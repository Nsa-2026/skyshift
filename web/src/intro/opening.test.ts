import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { paintScene } from "./scene";
import { playOpening, type OpeningHandle } from "./opening";
import { preparePlanetaryOpening, type PreparationResult } from './planet-worker-client';

vi.mock("./scene", () => ({ paintScene: vi.fn() }));
vi.mock('./planet-worker-client', () => ({ preparePlanetaryOpening: vi.fn(() => ({ pending: false, ready: new Promise(() => {}), cancel: vi.fn() })) }));

describe("the opening overlay", () => {
  let app: HTMLElement;
  let handle: OpeningHandle | null = null;
  const onClose = vi.fn();

  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    vi.stubGlobal("requestAnimationFrame", (fn: () => void) => setTimeout(fn, 16));
    vi.stubGlobal("cancelAnimationFrame", (id: number) => clearTimeout(id));
    app = document.createElement("div");
    app.id = "app";
    app.innerHTML = `<p class="foot-links"><button id="replay">Replay intro</button></p>`;
    document.body.append(app);
  });
  afterEach(() => {
    handle?.close();
    handle = null;
    document.body.replaceChildren();
    document.documentElement.classList.remove("op-open");
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  function open(opts: Parameters<typeof playOpening>[1] = {}) {
    handle = playOpening(app, { onClose, skyUrl: "data:,", ...opts });
    const el = handle.element;
    Object.defineProperties(el, {
      clientHeight: { value: 800, configurable: true },
      scrollHeight: { value: 5200, configurable: true },
    });
    return el;
  }
  function scroll(el: HTMLElement, top: number) {
    el.scrollTop = top;
    el.dispatchEvent(new Event("scroll"));
    vi.advanceTimersByTime(20);
  }

  it("is a labelled modal dialog over an inert page, focused, with Skip reachable at once", () => {
    const el = open();
    expect(el.getAttribute("role")).toBe("dialog");
    expect(el.getAttribute("aria-modal")).toBe("true");
    expect(el.getAttribute("aria-label")).toBe("SkyShift opening. Scroll to follow the story, or choose Explore the sky.");
    expect(document.getElementById(el.getAttribute("aria-describedby")!)!.textContent).toMatch(/Escape/);
    expect(el.querySelector("h2")!.textContent).toMatch(/The sky is\s+never still\./);
    expect(document.activeElement).toBe(el);
    expect(app.hasAttribute("inert")).toBe(true);
    expect(document.documentElement.classList.contains("op-open")).toBe(true);
    const skip = el.querySelector<HTMLButtonElement>(".op-skip")!;
    expect(skip.textContent!.replace(/\s+/g, " ").trim()).toMatch(/^Explore the sky/);
    expect(skip.tabIndex).toBe(0);
    expect(el.querySelector("canvas")!.getAttribute("aria-hidden")).toBe("true");
    expect(el.querySelector(".op-sky")!.getAttribute("aria-hidden")).toBe("true");
  });

  it("credits the SPHEREx sky and says the rest is drawn in code", () => {
    const el = open();
    const credit = el.querySelector(".op-credit")!.textContent!;
    expect(credit).toContain("SPHEREx DR1");
    expect(credit).toContain("NASA/JPL-Caltech/IPAC (IRSA)");
    expect(credit).toContain("drawn in code");
  });

  it("drops the sky credit when the image fails, and keeps going", () => {
    const el = open();
    el.querySelector("img")!.dispatchEvent(new Event("error"));
    expect(el.querySelector(".op-sky")).toBeNull();
    expect(el.querySelector(".op-credit")!.textContent).not.toContain("SPHEREx");
    scroll(el, 2200);
    expect(paintScene).toHaveBeenLastCalledWith(expect.any(HTMLCanvasElement), 0.5);
  });

  it("keeps the entry action usable after the canvas renderer fails", () => {
    vi.mocked(paintScene).mockImplementationOnce(() => { throw new Error("canvas resource unavailable"); });
    const el = open();
    expect(el.querySelector("canvas")!.hidden).toBe(true);
    expect(document.activeElement).toBe(el);
    scroll(el, 2200);
    expect(paintScene).toHaveBeenCalledTimes(1);
    el.querySelector<HTMLButtonElement>(".op-skip")!.click();
    expect(el.isConnected).toBe(false);
    expect(app.hasAttribute("inert")).toBe(false);
    expect(onClose).toHaveBeenCalledWith("skip");
  });

  it("finishes the journey even if a later canvas redraw fails", () => {
    const el = open();
    vi.mocked(paintScene).mockImplementationOnce(() => { throw new Error("context lost"); });
    expect(() => scroll(el, 2200)).not.toThrow();
    expect(el.querySelector("canvas")!.hidden).toBe(true);
    scroll(el, 4400);
    vi.advanceTimersByTime(800);
    expect(onClose).toHaveBeenCalledWith("end");
    expect(app.hasAttribute("inert")).toBe(false);
  });

  it("has no Chinese text left", () => {
    const el = open();
    expect(el.textContent).not.toMatch(/[　-鿿＀-￯]/);
  });

  it("Skip closes it, restores the page and calls back once", () => {
    const el = open();
    el.querySelector<HTMLButtonElement>(".op-skip")!.click();
    expect(el.isConnected).toBe(false);
    expect(app.hasAttribute("inert")).toBe(false);
    expect(document.documentElement.classList.contains("op-open")).toBe(false);
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledWith("skip");
    handle!.close();
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("Escape skips it", () => {
    const el = open();
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    expect(el.isConnected).toBe(false);
    expect(onClose).toHaveBeenCalledWith("skip");
  });

  it("a hash change (Back, a link) closes it", () => {
    const el = open();
    window.dispatchEvent(new HashChangeEvent("hashchange"));
    expect(el.isConnected).toBe(false);
    expect(onClose).toHaveBeenCalledWith("navigate");
  });

  it("does not move on with time; scrolling back redraws the same frame", () => {
    const el = open();
    vi.advanceTimersByTime(5000);
    expect(paintScene).toHaveBeenLastCalledWith(expect.any(HTMLCanvasElement), 0);
    scroll(el, 2200);
    scroll(el, 3300);
    scroll(el, 2200);
    expect(paintScene).toHaveBeenLastCalledWith(expect.any(HTMLCanvasElement), 0.5);
    expect(onClose).not.toHaveBeenCalled();
  });

  it("does not rasterize an unchanged scene again on duplicate scroll or resize events", () => {
    const el = open();
    scroll(el, 2200);
    const paints = vi.mocked(paintScene).mock.calls.length;
    scroll(el, 2200);
    window.dispatchEvent(new Event("resize"));
    vi.advanceTimersByTime(20);
    expect(paintScene).toHaveBeenCalledTimes(paints);
    vi.stubGlobal("innerWidth", window.innerWidth + 100);
    window.dispatchEvent(new Event("resize"));
    vi.advanceTimersByTime(20);
    expect(paintScene).toHaveBeenCalledTimes(paints + 1);
  });

  it('redraws the current story once off-thread planets are ready, without moving scroll', async () => {
    let ready!: (result: PreparationResult) => void;
    vi.mocked(preparePlanetaryOpening).mockReturnValueOnce({ pending: true, ready: new Promise(r => { ready = r; }), cancel: vi.fn() });
    const el = open(); scroll(el, 2200);
    expect(el.dataset.planetPreparation).toBe('pending');
    const paints = vi.mocked(paintScene).mock.calls.length;
    ready('ready'); await Promise.resolve(); vi.advanceTimersByTime(20);
    expect(el.dataset.planetPreparation).toBe('ready');
    expect(el.scrollTop).toBe(2200);
    expect(paintScene).toHaveBeenCalledTimes(paints + 1);
    expect(paintScene).toHaveBeenLastCalledWith(expect.any(HTMLCanvasElement), .5);
  });

  it('cancels preparation on close and does not draw a late completion', async () => {
    let ready!: (result: PreparationResult) => void;
    const cancel = vi.fn();
    vi.mocked(preparePlanetaryOpening).mockReturnValueOnce({ pending: true, ready: new Promise(r => { ready = r; }), cancel });
    const el = open(), paints = vi.mocked(paintScene).mock.calls.length;
    handle!.close(); ready('ready'); await Promise.resolve(); vi.advanceTimersByTime(20);
    expect(cancel).toHaveBeenCalledOnce();
    expect(el.isConnected).toBe(false);
    expect(paintScene).toHaveBeenCalledTimes(paints);
  });

  it("dissolves into the page at the bottom, once, even after a fling", () => {
    const el = open();
    scroll(el, 4400);
    scroll(el, 4400);
    expect(el.classList.contains('is-revealing')).toBe(true);
    expect(el.isConnected).toBe(true);
    expect(app.hasAttribute('inert')).toBe(true);
    vi.advanceTimersByTime(800);
    expect(el.isConnected).toBe(false);
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledWith("end");
    expect(app.hasAttribute("inert")).toBe(false);
  });

  it('pauses the independent sky drift off the title and in hidden tabs, without a canvas idle loop',()=>{
    const hidden=vi.spyOn(document,'hidden','get').mockReturnValue(false);
    try {
      const el=open();
      expect(el.classList.contains('is-drifting')).toBe(true);
      const paints=vi.mocked(paintScene).mock.calls.length;
      vi.advanceTimersByTime(5000);
      expect(paintScene).toHaveBeenCalledTimes(paints);
      hidden.mockReturnValue(true);document.dispatchEvent(new Event('visibilitychange'));
      expect(el.classList.contains('is-background-paused')).toBe(true);
      scroll(el,2200);
      expect(el.classList.contains('is-drifting')).toBe(false);
      hidden.mockReturnValue(false);document.dispatchEvent(new Event('visibilitychange'));
      scroll(el,0);
      expect(el.classList.contains('is-background-paused')).toBe(false);
      expect(el.classList.contains('is-drifting')).toBe(true);
    } finally { hidden.mockRestore(); }
  });

  it('cancels a pending dissolve immediately on navigation and removes its later callback',()=>{
    const el=open();scroll(el,4400);
    window.dispatchEvent(new HashChangeEvent('hashchange'));
    expect(el.isConnected).toBe(false);
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledWith('navigate');
    vi.advanceTimersByTime(1000);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("keeps its place when the window resizes", () => {
    const el = open();
    scroll(el, 2200);
    Object.defineProperties(el, { clientHeight: { value: 600 }, scrollHeight: { value: 3900 } });
    window.dispatchEvent(new Event("resize"));
    vi.advanceTimersByTime(20);
    expect(el.scrollTop).toBe(1650);
    expect(onClose).not.toHaveBeenCalled();
  });

  it("returns focus to the replay button", () => {
    const replay = app.querySelector<HTMLButtonElement>("#replay")!;
    const el = open({ returnFocus: replay });
    el.querySelector<HTMLButtonElement>(".op-skip")!.click();
    expect(document.activeElement).toBe(replay);
  });

  it("still version: one frame, no scroll journey, the same Explore the sky action", () => {
    const el = open({ still: true });
    expect(el.classList.contains("is-still")).toBe(true);
    expect(el.querySelector(".op-skip")!.textContent).toMatch(/Explore the sky/);
    scroll(el, 2200);
    expect(paintScene).toHaveBeenLastCalledWith(expect.any(HTMLCanvasElement), 0);
    expect(el.isConnected).toBe(true);
  });

  it("closes when reduced motion is switched on mid-way", () => {
    const motion = Object.assign(new EventTarget(), { matches: false });
    vi.stubGlobal("matchMedia", () => motion);
    const el = open();
    motion.matches = true;
    motion.dispatchEvent(new Event("change"));
    expect(el.isConnected).toBe(false);
    expect(onClose).toHaveBeenCalledWith("skip");
  });

  it("honours reduced motion switched on while its module was loading", () => {
    const motion = Object.assign(new EventTarget(), { matches: true });
    vi.stubGlobal("matchMedia", () => motion);
    const el = open({ still: false });
    expect(el.classList.contains("is-still")).toBe(true);
    scroll(el, 2200);
    expect(paintScene).toHaveBeenLastCalledWith(expect.any(HTMLCanvasElement), 0);
    expect(onClose).not.toHaveBeenCalled();
  });
});
