import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_SLOTS, type Slots } from "./manifest";
import { paintScene } from "./scene";
import { playOpening, type OpeningHandle } from "./opening";

vi.mock("./scene", () => ({ paintScene: vi.fn() }));

/** Stands in for `new Image()` (Earth, hills): jsdom loads no images. */
class FakeImage {
  static made: FakeImage[] = [];
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  decoding = "";
  src = "";
  naturalWidth = 2400;
  naturalHeight = 800;
  constructor() {
    FakeImage.made.push(this);
  }
}

describe("the opening overlay", () => {
  let app: HTMLElement;
  let handle: OpeningHandle | null = null;
  const onClose = vi.fn();

  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    FakeImage.made = [];
    vi.stubGlobal("requestAnimationFrame", (fn: () => void) => setTimeout(fn, 16));
    vi.stubGlobal("cancelAnimationFrame", (id: number) => clearTimeout(id));
    vi.stubGlobal("Image", FakeImage);
    vi.spyOn(console, "warn").mockImplementation(() => {});
    app = document.createElement("div");
    app.id = "app";
    app.innerHTML = `<div id="map" tabindex="0"></div><p class="foot-links"><button id="replay">Replay intro</button></p>`;
    document.body.append(app);
  });
  afterEach(() => {
    handle?.close();
    handle = null;
    document.body.replaceChildren();
    document.documentElement.classList.remove("op-open");
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  function open(opts: Parameters<typeof playOpening>[1] = {}) {
    handle = playOpening(app, { onClose, slots: DEFAULT_SLOTS, ...opts });
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
  const skyImg = (el: HTMLElement) => el.querySelector<HTMLImageElement>(".op-sky img")!;
  const creditOf = (el: HTMLElement) => el.querySelector(".op-credit")!.textContent!;

  it("is a labelled modal dialog over an inert page, with focus on Skip", () => {
    const el = open();
    expect(el.getAttribute("role")).toBe("dialog");
    expect(el.getAttribute("aria-modal")).toBe("true");
    expect(el.getAttribute("aria-label")).toBe("SkyShift opening. Scroll down to explore, or skip the intro.");
    expect(document.getElementById(el.getAttribute("aria-describedby")!)!.textContent).toMatch(/Escape/);
    expect(el.querySelector("h2")!.textContent).toMatch(/The sky is\s+never still\./);
    const skip = el.querySelector<HTMLButtonElement>(".op-skip")!;
    expect(skip.textContent!.replace(/\s+/g, " ").trim()).toMatch(/^Skip intro/);
    expect(document.activeElement).toBe(skip);
    expect(app.hasAttribute("inert")).toBe(true);
    expect(document.documentElement.classList.contains("op-open")).toBe(true);
    expect(el.querySelector("canvas")!.getAttribute("aria-hidden")).toBe("true");
  });

  it("describes and credits the SPHEREx sky once it is on screen, and says the rest is drawn in code", () => {
    const el = open();
    expect(creditOf(el)).toBe("Illustration: Earth, hills and figure drawn in code.");
    const img = skyImg(el);
    expect(img.getAttribute("src")).toBe("/opening/spherex-sky.webp");
    expect(img.hidden).toBe(true);
    img.dispatchEvent(new Event("load"));
    expect(img.hidden).toBe(false);
    expect(img.alt).toMatch(/Milky Way/);
    expect(creditOf(el)).toBe(
      "Sky: SPHEREx DR1 at 1.35 µm, NASA/JPL-Caltech/IPAC (IRSA). Illustration: Earth, hills and figure drawn in code.",
    );
  });

  it("keeps the credit on screen as long as the sky, while the brand steps back", () => {
    const el = open();
    scroll(el, 0.91 * 4400);
    expect(el.querySelector<HTMLElement>(".op-brand")!.style.opacity).toBe("0");
    expect(el.querySelector<HTMLElement>(".op-credit")!.style.opacity).toBe("");
    expect(el.style.opacity).toBe("1");
  });

  it("drops the sky and its credit when the image fails, and keeps going", () => {
    const el = open();
    skyImg(el).dispatchEvent(new Event("error"));
    expect(el.querySelector(".op-sky")).toBeNull();
    expect(creditOf(el)).not.toContain("SPHEREx");
    scroll(el, 2200);
    expect(paintScene).toHaveBeenLastCalledWith(expect.any(HTMLCanvasElement), 0.5, {});
  });

  it("falls back to today's sky when the manifest's file is missing", () => {
    const slots: Slots = { ...DEFAULT_SLOTS, sky: { ...DEFAULT_SLOTS.sky, file: "sky-new.webp", credit: "Sky: SPHEREx QR3 at 2 µm, NASA/JPL-Caltech/IPAC (IRSA)." } };
    const el = open({ slots });
    const img = skyImg(el);
    expect(img.getAttribute("src")).toBe("/opening/sky-new.webp");
    img.dispatchEvent(new Event("error"));
    expect(img.getAttribute("src")).toBe("/opening/spherex-sky.webp");
    img.dispatchEvent(new Event("load"));
    // The credit follows the file on screen, not the manifest's.
    expect(creditOf(el)).toContain("SPHEREx DR1 at 1.35 µm");
  });

  it("draws a manifest's Earth and hills, and labels them as illustration", () => {
    const slots: Slots = {
      ...DEFAULT_SLOTS,
      earth: { ...DEFAULT_SLOTS.earth, file: "earth.webp", credit: "Earth, made with Higgsfield" },
      horizon: { ...DEFAULT_SLOTS.horizon, file: "hills.webp", credit: "hills, made with Higgsfield." },
    };
    const el = open({ slots });
    expect(FakeImage.made.map((i) => i.src)).toEqual(["/opening/earth.webp", "/opening/hills.webp"]);
    FakeImage.made[0].onload!();
    expect(creditOf(el)).toBe("Illustration: Earth, made with Higgsfield; hills and figure drawn in code.");
    FakeImage.made[1].onload!();
    expect(creditOf(el)).toBe("Illustration: Earth, made with Higgsfield; hills, made with Higgsfield; figure drawn in code.");
    vi.advanceTimersByTime(20);
    expect(paintScene).toHaveBeenLastCalledWith(expect.any(HTMLCanvasElement), 0, { earth: FakeImage.made[0], horizon: FakeImage.made[1] });
  });

  it("keeps the drawn Earth when its image fails", () => {
    const slots: Slots = { ...DEFAULT_SLOTS, earth: { ...DEFAULT_SLOTS.earth, file: "earth.webp", credit: "Earth, made with Higgsfield" } };
    const el = open({ slots });
    FakeImage.made[0].onerror!();
    expect(creditOf(el)).toBe("Illustration: Earth, hills and figure drawn in code.");
  });

  it("waits for the manifest without holding anything up", async () => {
    let resolve!: (s: Slots) => void;
    const el = open({ slots: new Promise<Slots>((r) => (resolve = r)) });
    expect(skyImg(el).getAttribute("src")).toBeNull();
    expect(document.activeElement).toBe(el.querySelector(".op-skip"));
    resolve(DEFAULT_SLOTS);
    await Promise.resolve();
    expect(skyImg(el).getAttribute("src")).toBe("/opening/spherex-sky.webp");
  });

  it("has no Chinese text left", () => {
    const el = open();
    expect(el.textContent).not.toMatch(/[　-鿿＀-￯]/);
  });

  it("Skip closes it, restores the page, lands on the map and calls back once", () => {
    const map = app.querySelector<HTMLElement>("#map")!;
    const el = open({ returnFocus: () => map });
    el.querySelector<HTMLButtonElement>(".op-skip")!.click();
    expect(el.isConnected).toBe(false);
    expect(app.hasAttribute("inert")).toBe(false);
    expect(document.documentElement.classList.contains("op-open")).toBe(false);
    expect(document.activeElement).toBe(map);
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledWith("skip");
    handle!.close();
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("Escape skips it at once, wherever focus is", () => {
    const map = app.querySelector<HTMLElement>("#map")!;
    const el = open({ returnFocus: () => map });
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    expect(el.isConnected).toBe(false);
    expect(onClose).toHaveBeenCalledWith("skip");
    expect(document.activeElement).toBe(map);
  });

  it("a hash change (Back, a link) closes it and leaves focus to the new page", () => {
    const map = app.querySelector<HTMLElement>("#map")!;
    const el = open({ returnFocus: () => map });
    window.dispatchEvent(new HashChangeEvent("hashchange"));
    expect(el.isConnected).toBe(false);
    expect(onClose).toHaveBeenCalledWith("navigate");
    expect(document.activeElement).not.toBe(map);
  });

  it("does not move on with time; scrolling back redraws the same frame", () => {
    const el = open();
    vi.advanceTimersByTime(5000);
    expect(paintScene).toHaveBeenLastCalledWith(expect.any(HTMLCanvasElement), 0, {});
    scroll(el, 2200);
    scroll(el, 3300);
    scroll(el, 2200);
    expect(paintScene).toHaveBeenLastCalledWith(expect.any(HTMLCanvasElement), 0.5, {});
    expect(onClose).not.toHaveBeenCalled();
  });

  it("ends by itself at the bottom, once, even after a fling, on the map", () => {
    const map = app.querySelector<HTMLElement>("#map")!;
    const el = open({ returnFocus: () => map });
    scroll(el, 4400);
    scroll(el, 4400);
    expect(el.isConnected).toBe(false);
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledWith("end");
    expect(app.hasAttribute("inert")).toBe(false);
    expect(document.activeElement).toBe(map);
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
    const el = open({ returnFocus: () => replay });
    el.querySelector<HTMLButtonElement>(".op-skip")!.click();
    expect(document.activeElement).toBe(replay);
  });

  describe("still version (reduced motion)", () => {
    it("is one frame with the same Skip intro button, focused", () => {
      const el = open({ still: true });
      expect(el.classList.contains("is-still")).toBe(true);
      const skip = el.querySelector<HTMLButtonElement>(".op-skip")!;
      expect(skip.textContent).toMatch(/Skip intro/);
      expect(document.activeElement).toBe(skip);
      scroll(el, 2200);
      expect(paintScene).toHaveBeenLastCalledWith(expect.any(HTMLCanvasElement), 0, {});
      expect(el.isConnected).toBe(true);
    });

    it("scrolling down with the wheel moves on to the site", () => {
      const el = open({ still: true });
      el.dispatchEvent(new WheelEvent("wheel", { deltaY: -40 }));
      expect(el.isConnected).toBe(true);
      el.dispatchEvent(new WheelEvent("wheel", { deltaY: 40 }));
      expect(el.isConnected).toBe(false);
      expect(onClose).toHaveBeenCalledWith("end");
    });

    it("Page Down or the down arrow moves on too", () => {
      const el = open({ still: true });
      el.querySelector(".op-skip")!.dispatchEvent(new KeyboardEvent("keydown", { key: "PageDown", bubbles: true }));
      expect(el.isConnected).toBe(false);
    });

    it("Escape skips it", () => {
      const el = open({ still: true });
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
      expect(el.isConnected).toBe(false);
    });
  });

  it("the arrow keys, Page Down, Home and End scroll the story with focus on Skip", () => {
    const el = open();
    const scrollTo = vi.fn((o: ScrollToOptions) => void (el.scrollTop = o.top!));
    el.scrollTo = scrollTo as never;
    const press = (key: string) => el.querySelector(".op-skip")!.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));
    press("ArrowDown");
    expect(scrollTo).toHaveBeenLastCalledWith({ top: 80, behavior: "smooth" });
    press("PageDown");
    expect(scrollTo).toHaveBeenLastCalledWith({ top: 800, behavior: "smooth" });
    press("ArrowUp");
    press("Home");
    expect(scrollTo).toHaveBeenLastCalledWith({ top: 0, behavior: "smooth" });
    press("ArrowUp");
    expect(scrollTo).toHaveBeenLastCalledWith({ top: 0, behavior: "smooth" });
    press("End");
    expect(scrollTo).toHaveBeenLastCalledWith({ top: 4400, behavior: "smooth" });
    // Other keys (Space, Enter, Tab) keep their usual job.
    expect(press("Tab")).toBe(true);
    expect(press(" ")).toBe(true);
    expect(scrollTo).toHaveBeenCalledTimes(6);
  });

  it("the moving version ignores the wheel (it scrolls the story instead)", () => {
    const el = open();
    el.dispatchEvent(new WheelEvent("wheel", { deltaY: 40 }));
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
