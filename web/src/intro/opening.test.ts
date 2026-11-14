import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { paintScene } from "./scene";
import { playOpening, type OpeningHandle } from "./opening";

vi.mock("./scene", () => ({ paintScene: vi.fn() }));

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
    app.innerHTML = `<p class="foot-links"><button id="replay">Replay the opening</button></p>`;
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
    expect(document.getElementById(el.getAttribute("aria-labelledby")!)!.textContent).toMatch(/The sky is\s+never still\./);
    expect(document.getElementById(el.getAttribute("aria-describedby")!)!.textContent).toMatch(/Escape/);
    expect(document.activeElement).toBe(el);
    expect(app.hasAttribute("inert")).toBe(true);
    expect(document.documentElement.classList.contains("op-open")).toBe(true);
    const skip = el.querySelector<HTMLButtonElement>(".op-skip")!;
    expect(skip.textContent!.replace(/\s+/g, " ").trim()).toMatch(/^Skip the opening/);
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

  it("ends by itself at the bottom, once, even after a fling", () => {
    const el = open();
    scroll(el, 4400);
    scroll(el, 4400);
    expect(el.isConnected).toBe(false);
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledWith("end");
    expect(app.hasAttribute("inert")).toBe(false);
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

  it("still version: one frame, no scroll journey, an Enter button", () => {
    const el = open({ still: true });
    expect(el.classList.contains("is-still")).toBe(true);
    expect(el.querySelector(".op-skip")!.textContent).toMatch(/Enter/);
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
});
