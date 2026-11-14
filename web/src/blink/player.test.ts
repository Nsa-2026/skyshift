// The mounted player through its seam (src/app/modules.ts): epochs, keyboard, modes, picks, status.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Det, Epoch, FramesResponse } from "../api/types";
import type { View } from "../app/modules";
import { mountBlink } from "./index";
import { mountObservatory } from "../app/observatory";

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

    el.querySelector<HTMLButtonElement>("[data-mode='difference']")!.click();
    expect(el.querySelector("[data-mode='difference']")!.getAttribute("aria-pressed")).toBe("true");
    expect(sub()).toBe("visit 2 minus visit 1");
    expect(el.querySelector(".bk-say")!.textContent).toBe("Each visit minus the first. Red: light arrived. Blue: light left.");

    el.querySelector<HTMLButtonElement>("[data-mode='stack']")!.click();
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

  it("does not start playback when a pending load finishes on a hidden page", async () => {
    let release!: () => void;
    const wait = new Promise<void>(resolve => { release = resolve; });
    vi.mocked(fetch).mockImplementation(async () => { await wait; return new Response(new Uint16Array(N * N).fill(1000).buffer); });
    const h = mountBlink(el, { onViewChange: vi.fn(), onPick: vi.fn() });
    const loading = h.show(view, frames, "https://example.test/hidden/frames.json");
    h.active?.(false);
    release(); await loading;
    expect(el.querySelector('.bk-play')?.getAttribute('aria-pressed')).toBe('false');
    expect(el.querySelector('.bk')?.getAttribute('data-ready')).toBe('true');
    h.destroy();
  });

  it("discards old pixel loads as soon as another view starts loading", async () => {
    let release!: () => void;
    const wait = new Promise<void>(resolve => { release = resolve; });
    vi.mocked(fetch).mockImplementation(async () => { await wait; return new Response(new Uint16Array(N * N).fill(1000).buffer); });
    const onEpoch = vi.fn(), h = mountBlink(el, { onViewChange: vi.fn(), onPick: vi.fn(), onEpoch });
    const loading = h.show(view, frames, "https://example.test/stale/frames.json");
    h.status('loading', 'Loading the next target');
    release(); await loading;
    expect(onEpoch).not.toHaveBeenCalled();
    expect(el.querySelector('.bk')?.getAttribute('data-ready')).toBe('false');
    h.destroy();
  });

  function shell() {
    const app = document.createElement("div");
    app.innerHTML = `<header class="top"><input id="q"></header><div class="featured"></div>
      <p class="find-msg"></p><section id="card"><button data-mode="visit" aria-pressed="true">By visit</button>
      <button data-det="6" aria-pressed="true">Chart D6</button></section>
      <section id="science-workbench"><details class="wb-main"></details></section>
      <section id="vote"></section><section id="sky"></section>`;
    document.body.append(app); app.append(el);
    return app;
  }

  it("keeps chart controls independent after docking them inside the player", async () => {
    const app = shell(), onViewChange = vi.fn(), onEpoch = vi.fn();
    const h = mountBlink(el, { immersive: true, onViewChange, onEpoch, onPick: vi.fn() });
    const obs = mountObservatory(app, el, { target: vi.fn(), analysis: vi.fn() });
    await h.show(view, frames, "https://example.test/docked/frames.json");
    obs.active(true);
    const chart = app.querySelector<HTMLButtonElement>('#card [data-mode]')!;
    chart.click();
    expect(el.querySelector('.bk')!.getAttribute('data-mode')).toBe('blink');
    expect(chart.getAttribute('aria-pressed')).toBe('true');
    const chartBand = app.querySelector<HTMLButtonElement>('#card [data-det]')!;
    expect(chartBand.disabled).toBe(false);
    chartBand.click(); expect(onViewChange).not.toHaveBeenCalled();
    app.querySelector<HTMLButtonElement>('.obs-next')!.click();
    expect(onEpoch).toHaveBeenLastCalledWith({ id: 'd2-v2', mjd_mid: 61090, det: 2 });
    expect(el.querySelector('.bk-play')!.getAttribute('aria-pressed')).toBe('false');
    app.querySelector<HTMLButtonElement>('[data-mode="stack"]')!.click();
    expect(app.querySelector<HTMLButtonElement>('.obs-next')!.disabled).toBe(true);
    const invert = app.querySelector<HTMLInputElement>('.obs-invert input')!;
    invert.checked = true; invert.dispatchEvent(new Event('change', { bubbles: true }));
    expect(el.querySelector('.bk')!.getAttribute('data-positive')).toBe('true');
    const speed = app.querySelector<HTMLSelectElement>('.obs-speed')!;
    speed.value = '0.5'; speed.dispatchEvent(new Event('change', { bubbles: true }));
    expect(app.querySelector('[data-fps="0.5"]')!.getAttribute('aria-pressed')).toBe('true');
    obs.destroy(); h.destroy(); app.remove();
  });

  it("stops analysis when its pane is hidden and restores navigation across routes", () => {
    const app = shell(), analysis = vi.fn(), target = vi.fn();
    const header = app.querySelector('.top')!;
    const h = mountBlink(el, { immersive: true, onViewChange: vi.fn(), onPick: vi.fn() });
    const obs = mountObservatory(app, el, { target, analysis });
    obs.active(true);
    expect(header.parentElement?.className).toBe('obs-navigation-content');
    obs.panel('analysis');
    expect(analysis).toHaveBeenLastCalledWith(true);
    expect(app.querySelector<HTMLDetailsElement>('.wb-main')!.open).toBe(true);
    app.querySelector<HTMLButtonElement>('[data-panel="analysis"]')!.dispatchEvent(new KeyboardEvent('keydown', {key:'ArrowLeft', bubbles:true}));
    expect(app.querySelector('[data-panel="view"]')!.getAttribute('aria-selected')).toBe('true');
    expect(analysis).toHaveBeenLastCalledWith(false);
    obs.panel('analysis');
    app.querySelector<HTMLButtonElement>('.obs-close')!.click();
    expect(analysis).toHaveBeenLastCalledWith(false);
    expect(app.querySelector<HTMLElement>('.obs-details')!.hidden).toBe(true);
    app.querySelector<HTMLButtonElement>('.obs-menu')!.click();
    expect(app.querySelector<HTMLElement>('.obs-navigation')!.hidden).toBe(false);
    obs.view(view, null);
    expect(app.querySelector<HTMLElement>('.obs-navigation')!.hidden).toBe(true);
    const targets = app.querySelector<HTMLSelectElement>('.obs-target')!;
    targets.value = 'barnard'; targets.dispatchEvent(new Event('change', {bubbles:true}));
    expect(target).toHaveBeenCalledWith('barnard');
    obs.active(false);
    expect(header.parentElement).toBe(app);
    expect(analysis).toHaveBeenLastCalledWith(false);
    obs.active(true);
    expect(app.querySelectorAll('.top')).toHaveLength(1);
    obs.destroy(); h.destroy(); app.remove();
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
});
