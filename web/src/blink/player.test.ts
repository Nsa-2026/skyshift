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
    vi.useRealTimers();
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
    // The default: all wavelengths, each visit's D2 and D5 combined into one plate.
    const label = () => el.querySelector(".bk-det-label")!.textContent;
    expect(el.querySelector("[data-det='all']")!.getAttribute("aria-pressed")).toBe("true");
    expect(sub()).toBe("visit 1 of 3");
    expect(label()).toContain("All wavelengths");
    expect(label()).toContain("2 of 6 detectors");
    // The app hears of a real epoch, never the combined one.
    expect(onEpoch).toHaveBeenLastCalledWith(expect.objectContaining({ id: "d2-v1", mjd_mid: 60908, det: 2 }));
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
    expect(label()).toContain("1 of 6 detectors");

    // A detector button filters: D2 alone. Never blink different detectors: three D2 visits, no D5.
    el.querySelector<HTMLButtonElement>("[data-det='2']")!.click();
    await vi.waitFor(() => expect(sub()).toMatch(/^visit \d of 3$/));
    expect(label()).toContain("D2");
    expect(el.querySelector("[data-det='all']")!.getAttribute("aria-pressed")).toBe("false");
    expect(el.querySelector("[data-det='2']")!.getAttribute("aria-pressed")).toBe("true");
    expect(onViewChange).not.toHaveBeenCalled();
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true }));
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true }));
    expect(sub()).toBe("visit 1 of 3");
    expect(onEpoch).toHaveBeenLastCalledWith(expect.objectContaining({ id: "d2-v1", mjd_mid: 60908, det: 2 }));
    expect(el.querySelector(".bk")!.getAttribute("data-ready")).toBe("true");

    document.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
    expect(sub()).toBe("visit 2 of 3");
    expect(onEpoch).toHaveBeenLastCalledWith(expect.objectContaining({ id: "d2-v2", mjd_mid: 61090, det: 2 }));
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

  it("ignores detector metadata that arrives after the player is destroyed", async () => {
    let release!: () => void;
    const wait = new Promise<void>(resolve => { release = resolve; });
    let metadataRead = 0;
    vi.mocked(fetch).mockImplementation(async input => {
      const url = String(input);
      if (url.includes('/api/frames?')) {
        await wait;
        const det = Number(new URL(url).searchParams.get('det')) as Det;
        metadataRead++;
        return new Response(JSON.stringify({...frames, epochs: frames.epochs.filter(e => e.det === 2)
          .map(e => ({...e, id: `late-d${det}-${e.id}`, det}))}));
      }
      return new Response(new Uint16Array(N * N).fill(1000).buffer);
    });
    const onEpoch = vi.fn(), h = mountBlink(el, { onViewChange: vi.fn(), onPick: vi.fn(), onEpoch });
    await h.show(view, frames, 'https://example.test/disposed-detectors/frames.json');
    h.destroy();
    const emitted = onEpoch.mock.calls.length;
    release();
    await vi.waitFor(() => expect(metadataRead).toBe(4));
    // Let all detector continuations run. Vitest also fails on a detached render's rejection.
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(el.innerHTML).toBe('');
    expect(onEpoch).toHaveBeenCalledTimes(emitted);
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

  it("does not start the next batch of pixels for an invalidated view", async () => {
    let release!: () => void;
    const wait = new Promise<void>(resolve => { release = resolve; });
    vi.mocked(fetch).mockImplementation(async () => { await wait; return new Response(new Uint16Array(N * N).fill(1000).buffer); });
    const many = {...frames, epochs: Array.from({length: 18}, (_, i) => epoch(`stale-pool-${i}`, 2, 60900 + i * 100))};
    const onEpoch = vi.fn(), h = mountBlink(el, { onViewChange: vi.fn(), onPick: vi.fn(), onEpoch });
    const loading = h.show(view, many, 'https://example.test/stale-pool/frames.json', {singleDetector:true});
    expect(fetch).toHaveBeenCalledTimes(6);
    h.status('loading', 'Loading the next target');
    release(); await loading;
    expect(fetch).toHaveBeenCalledTimes(6);
    expect(onEpoch).not.toHaveBeenCalled();
    h.destroy();
  });

  it("suspends hidden-tab playback and resumes only an active player that was playing", async () => {
    vi.useFakeTimers();
    const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(false);
    const onEpoch = vi.fn(), h = mountBlink(el, { onViewChange: vi.fn(), onPick: vi.fn(), onEpoch });
    const visibility = (value:boolean) => { hidden.mockReturnValue(value); document.dispatchEvent(new Event('visibilitychange')); };
    const play = () => el.querySelector<HTMLButtonElement>('.bk-play')!;
    try {
      await h.show(view, frames, 'https://example.test/tab-visibility/frames.json', {singleDetector:true});
      expect(play().getAttribute('aria-pressed')).toBe('true');
      visibility(true);
      const before = onEpoch.mock.calls.length;
      vi.advanceTimersByTime(2000);
      expect(onEpoch).toHaveBeenCalledTimes(before);
      expect(play().getAttribute('aria-pressed')).toBe('false');
      visibility(false);
      vi.advanceTimersByTime(500);
      expect(onEpoch).toHaveBeenCalledTimes(before + 1);
      play().click(); // The user pauses before changing tabs.
      visibility(true); visibility(false);
      vi.advanceTimersByTime(1000);
      expect(play().getAttribute('aria-pressed')).toBe('false');
      expect(onEpoch).toHaveBeenCalledTimes(before + 1);
      play().click(); visibility(true); h.active?.(false); visibility(false);
      vi.advanceTimersByTime(1000);
      expect(play().getAttribute('aria-pressed')).toBe('false');
      expect(onEpoch).toHaveBeenCalledTimes(before + 1);
    } finally { h.destroy(); }
  });

  it("offers the detectors that have frames when the chosen one has none", async () => {
    const onViewChange = vi.fn();
    const h = mountBlink(el, { onViewChange, onPick: vi.fn() });
    await h.show({ ...view, det: 3 }, frames, "https://example.test/data/x/frames.json");
    const msg = el.querySelector<HTMLElement>(".bk-msg")!;
    // All wavelengths shows whatever detectors there are.
    expect(msg.hidden).toBe(true);
    // D3 alone has nothing.
    el.querySelector<HTMLButtonElement>("[data-det='3']")!.click();
    await vi.waitFor(() => expect(msg.hidden).toBe(false));
    const offers = [...msg.querySelectorAll<HTMLButtonElement>("[data-goto-det]")].map((b) => b.dataset.gotoDet);
    expect(offers).toEqual(["2", "5"]);
    msg.querySelector<HTMLButtonElement>("[data-goto-det='5']")!.click();
    expect(onViewChange).toHaveBeenLastCalledWith({ det: 5 });
    h.destroy();
  });

  it("opens an object to check on its own detector, lets All back in, and restores the preference", async () => {
    const h = mountBlink(el, { onViewChange: vi.fn(), onPick: vi.fn() });
    const pressed = (d: string) => el.querySelector(`[data-det='${d}']`)!.getAttribute("aria-pressed");
    const label = () => el.querySelector(".bk-det-label")!.textContent;
    // The suspect panel sets the preference before its frames are shown: D2 alone, not All.
    expect(h.preferAllWave!(false)).toBe(true);
    await h.show(view, frames, "https://example.test/data/cand/x/frames.json", { singleDetector: false });
    expect(pressed("2")).toBe("true");
    expect(pressed("all")).toBe("false");
    expect(label()).toContain("D2");
    // All stays available (not locked as in practice).
    const all = el.querySelector<HTMLButtonElement>("[data-det='all']")!;
    expect(all.disabled).toBe(false);
    all.click();
    expect(pressed("all")).toBe("true");
    await vi.waitFor(() => expect(label()).toContain("All wavelengths"));
    // Leaving the object puts the person's choice back; the next view opens with it.
    expect(h.preferAllWave!(false)).toBe(true);
    h.preferAllWave!(true);
    await h.show({ ...view, ra: 270 }, frames, "https://example.test/data/x/frames.json");
    expect(pressed("all")).toBe("true");
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

  it("keeps practice on one detector and restores the normal wavelength preference afterwards", async () => {
    const onEpoch = vi.fn();
    const h = mountBlink(el, { onViewChange:vi.fn(), onPick:vi.fn(), onEpoch });
    const url = "https://example.test/practice-lock/frames.json";
    await h.show(view,frames,url,{singleDetector:true});
    const all = el.querySelector<HTMLButtonElement>("[data-det='all']")!;
    expect(all.disabled).toBe(true);
    expect(all.getAttribute('aria-pressed')).toBe('false');
    expect(el.querySelector("[data-det='2']")!.getAttribute('aria-pressed')).toBe('true');
    expect(onEpoch.mock.lastCall![0].presentation).toEqual({
      mode:'single-detector',detectors:[2],units:'MJy/sr',
      members:[{id:'d2-v1',det:2,mjd_mid:60908,data_url:'https://example.test/practice-lock/frames/d2-v1.bin'}],
    });
    expect(vi.mocked(fetch).mock.calls.every(([u])=>String(u).includes('/practice-lock/frames/d2-'))).toBe(true);
    // A route change can release the lock using cached frames, without an app-level fetch.
    h.singleDetector?.(false);
    await vi.waitFor(()=>expect(onEpoch.mock.lastCall![0].presentation.mode).toBe('all-wavelengths'));
    expect(all.disabled).toBe(false);
    expect(all.getAttribute('aria-pressed')).toBe('true');
    expect(onEpoch.mock.lastCall![0].presentation.detectors).toEqual([2,5]);
    // A person's single-detector preference survives a later temporary lock as well.
    el.querySelector<HTMLButtonElement>("[data-det='2']")!.click();
    await vi.waitFor(()=>expect(onEpoch.mock.lastCall![0].presentation.mode).toBe('single-detector'));
    await h.show(view,frames,url,{singleDetector:true});
    await h.show(view,frames,url);
    expect(all.disabled).toBe(false);
    expect(all.getAttribute('aria-pressed')).toBe('false');
    h.destroy();
  });

  it("reports only the successfully loaded constituents of a noise-normalized composite", async () => {
    const onEpoch = vi.fn();
    const full = {...frames,epochs:([1,2,3,4,5,6] as Det[]).map(d=>epoch(`d${d}-v1`,d,60908))};
    vi.mocked(fetch).mockImplementation(async u=>String(u).endsWith('/d2-v1.bin')
      ?new Response('',{status:503}):new Response(new Uint16Array(N*N).fill(1000).buffer));
    const h = mountBlink(el,{onViewChange:vi.fn(),onPick:vi.fn(),onEpoch});
    await h.show(view,full,'https://example.test/partial-composite/frames.json');
    const shown = onEpoch.mock.lastCall![0];
    expect(shown).toMatchObject({id:'d1-v1',det:1,presentation:{mode:'all-wavelengths',detectors:[1,3,4,5,6],units:'sky-noise'}});
    expect(shown.presentation.members).toHaveLength(5);
    expect(shown.presentation.members.every((m:{det:Det;data_url:string})=>m.det!==2&&m.data_url.startsWith('https://example.test/partial-composite/frames/'))).toBe(true);
    h.destroy();
  });

  it("does not let pending raw detector requests enter a locked derived plate on the same grid", async () => {
    let release!:()=>void;
    const pending = new Promise<void>(resolve=>{release=resolve});
    vi.mocked(fetch).mockImplementation(async input=>{
      const text = String(input);
      if(text.includes('/api/frames?')){
        await pending;
        const det=Number(new URL(text,'https://example.test').searchParams.get('det')) as Det;
        return new Response(JSON.stringify({...frames,epochs:frames.epochs.filter(e=>e.det===2).map(e=>({...e,id:`live-${det}-${e.id}`,det}))}),{headers:{'Content-Type':'application/json'}});
      }
      return new Response(new Uint16Array(N*N).fill(1000).buffer);
    });
    const onEpoch = vi.fn();
    const h = mountBlink(el,{onViewChange:vi.fn(),onPick:vi.fn(),onEpoch});
    const one = {...frames,epochs:frames.epochs.filter(e=>e.det===2)};
    await h.show(view,one,'https://example.test/pending-raw/frames.json');
    const apiCalls = ()=>vi.mocked(fetch).mock.calls.filter(([u])=>String(u).includes('/api/frames?')).length;
    expect(apiCalls()).toBe(5);
    await h.show(view,one,'https://example.test/derived-locked/frames.json',{singleDetector:true});
    const events = onEpoch.mock.calls.length;
    release();
    await new Promise(resolve=>setTimeout(resolve,0));
    expect(apiCalls()).toBe(5);
    expect(onEpoch).toHaveBeenCalledTimes(events);
    expect(onEpoch.mock.lastCall![0].presentation).toMatchObject({mode:'single-detector',detectors:[2],units:'MJy/sr'});
    expect(onEpoch.mock.lastCall![0].presentation.members[0].data_url).toContain('/derived-locked/');
    h.destroy();
  });
});
