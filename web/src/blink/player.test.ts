// The mounted player through its seam (src/app/modules.ts): epochs, keyboard, modes, picks, status.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Det, Epoch, FramesResponse } from "../api/types";
import type { View } from "../app/modules";
import { mountBlink } from "./index";

const N = 8;
const epoch = (id: string, det: Det, mjd: number): Epoch => ({
  id,
  kind: "visit",
  mjd_mid: mjd,
  mjd_start: mjd - 6,
  mjd_end: mjd + 6,
  det,
  um_min: 1.1,
  um_max: 1.6,
  n_exposures: 18,
  flag_frac: 0.02,
  sat_frac: 0.001,
  data_url: `frames/${id}.bin`,
  scale: 0.001,
  zero: 0,
});
const frames: FramesResponse = {
  clock: "months",
  center: { ra: 269.4462, dec: 4.76883 },
  pixel_arcsec: 2.05,
  size: [N, N],
  epochs: [
    epoch("d2-v1", 2, 60908),
    epoch("d5-v1", 5, 60908),
    epoch("d2-v2", 2, 61090),
    epoch("d2-v3", 2, 61288),
    epoch("d5-v2", 5, 61090),
  ],
  gaia: [],
  known: [],
  data_release: "test",
  archive_checked: "2026-11-14",
};
const view: View = { ra: 269.4462, dec: 4.76883, clock: "months", fov: 5, det: 2, slug: null, name: null };

describe("blink player", () => {
  let el: HTMLDivElement;
  beforeEach(() => {
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        disconnect() {}
      },
    );
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(new Uint16Array(N * N).fill(1000).buffer)),
    );
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    el = document.createElement("div");
    document.body.append(el);
    // jsdom has no layout; the keyboard only acts on a visible player.
    vi.spyOn(el, "getClientRects").mockReturnValue({ length: 1 } as unknown as DOMRectList);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    el.remove();
  });

  it("blinks one detector, steps with the keyboard and reports each epoch", async () => {
    const onEpoch = vi.fn();
    const onPick = vi.fn();
    const onViewChange = vi.fn();
    const h = mountBlink(el, { onEpoch, onPick, onViewChange });
    const sub = () => el.querySelector(".bk-when-sub")!.textContent;

    h.status("loading", "Loading frames…");
    expect(sub()).toBe("Loading frames…");
    expect(el.querySelector<HTMLElement>(".bk-progress")!.hidden).toBe(false);

    await h.show(view, frames, "https://example.test/data/x/frames.json");
    // Never blink different detectors: three D2 visits, no D5.
    expect(sub()).toBe("visit 1 of 3");
    expect(onEpoch).toHaveBeenLastCalledWith({ id: "d2-v1", mjd_mid: 60908, det: 2 });
    expect(el.querySelector(".bk-det-label")!.textContent).toContain("D2");
    expect(el.querySelector(".bk")!.getAttribute("data-ready")).toBe("true");

    document.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
    expect(sub()).toBe("visit 2 of 3");
    expect(onEpoch).toHaveBeenLastCalledWith({ id: "d2-v2", mjd_mid: 61090, det: 2 });
    expect(el.querySelector(".bk-nowl")!.textContent).toMatch(/2026/);

    el.querySelector<HTMLButtonElement>("button[data-mode='difference']")!.click();
    expect(el.querySelector("button[data-mode='difference']")!.getAttribute("aria-pressed")).toBe("true");
    expect(el.querySelector(".bk")!.hasAttribute("aria-pressed")).toBe(false);
    expect(sub()).toBe("visit 2 minus visit 1");
    expect(el.querySelector(".bk-say")!.textContent).toBe("Each visit minus the first. Red: light arrived. Blue: light left.");

    el.querySelector<HTMLButtonElement>("button[data-mode='stack']")!.click();
    expect(sub()).toBe("mean of 3 visits");
    expect(el.querySelector<HTMLButtonElement>(".bk-play")!.disabled).toBe(true);

    el.querySelector<HTMLButtonElement>("[data-det='5']")!.click();
    expect(onViewChange).toHaveBeenLastCalledWith({ det: 5 });
    el.querySelector<HTMLButtonElement>("[data-clock='hours']")!.click();
    expect(onViewChange).toHaveBeenLastCalledWith({ clock: "hours", fov: 20 });
    expect(el.querySelector<HTMLButtonElement>("[data-det='3']")!.disabled).toBe(true);

    const adjust = el.querySelector<HTMLButtonElement>(".bk-adjust-btn")!;
    adjust.click();
    expect(adjust.getAttribute("aria-expanded")).toBe("true");
    expect(el.querySelector<HTMLElement>(".bk-adjust")!.hidden).toBe(false);
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    expect(el.querySelector<HTMLElement>(".bk-adjust")!.hidden).toBe(true);

    h.status("error", "NASA's archive didn't answer. Try again in a minute.");
    const msg = el.querySelector<HTMLElement>(".bk-msg")!;
    expect(msg.hidden).toBe(false);
    expect(msg.textContent).toContain("Try again in a minute");
    h.destroy();
    expect(el.innerHTML).toBe("");
    expect(onPick).not.toHaveBeenCalled();
  });

  it("offers the detectors that have frames when the chosen one has none", async () => {
    const onViewChange = vi.fn();
    const h = mountBlink(el, { onViewChange, onPick: vi.fn() });
    await h.show({ ...view, det: 3 }, frames, "https://example.test/data/x/frames.json");
    const msg = el.querySelector<HTMLElement>(".bk-msg")!;
    expect(msg.hidden).toBe(false);
    const offers = [...msg.querySelectorAll<HTMLButtonElement>("[data-goto-det]")].map((b) => b.dataset.gotoDet);
    expect(offers).toEqual(["2", "5"]);
    msg.querySelector<HTMLButtonElement>("[data-goto-det='5']")!.click();
    expect(onViewChange).toHaveBeenLastCalledWith({ det: 5 });
    h.destroy();
  });

  it("keeps every detector requestable after a live answer, which holds only the one asked for", async () => {
    // /api/frames?det=2 returns D2 epochs only; D6 at this spot is one more request away (#57).
    const onViewChange = vi.fn();
    const h = mountBlink(el, { onViewChange, onPick: vi.fn() });
    const live = { ...frames, epochs: frames.epochs.filter((e) => e.det === 2) };
    await h.show(view, live, "https://example.test/api/frames?ra=269.4462&dec=4.76883&clock=months&fov=5&det=2");
    const off = [...el.querySelectorAll<HTMLButtonElement>("[data-det]")].filter((b) => b.disabled);
    expect(off.map((b) => b.dataset.det)).toEqual([]);
    el.querySelector<HTMLButtonElement>("[data-det='6']")!.click();
    expect(onViewChange).toHaveBeenLastCalledWith({ det: 6 });
    // A static file holds every detector prepared: the others stay off there.
    await h.show(view, frames, "https://example.test/data/x/frames.json");
    expect(el.querySelector<HTMLButtonElement>("[data-det='6']")!.disabled).toBe(true);
    h.destroy();
  });
});
