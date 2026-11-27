// The mounted player through its seam (src/app/modules.ts): epochs, keyboard, modes, picks, status.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Det, Epoch, FramesResponse } from "../api/types";
import type { View } from "../app/modules";
import { mountBlink } from "./index";
import { PAGE_RETRY_MS, PAGE_TRIES } from "./pages";

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

  describe("the whole hours visit (contracts v3.23 page)", () => {
    const hv: View = { ...view, clock: "hours", fov: 20 };
    const exp = (id: string, det: Det, mjd: number): Epoch => ({
      ...epoch(id, det, mjd), kind: "exposure", mjd_start: mjd - 0.0007, mjd_end: mjd + 0.0007, n_exposures: 1,
      data_url: `/api/data/k/frames/h20_${id}.bin`,
    });
    const T = 61021.2;
    const page = (ids: [string, number][], more: boolean, det: Det = 2, total = 9): FramesResponse => ({
      ...frames, clock: "hours", epochs: ids.map(([id, dt]) => exp(`d${det}-${id}`, det, T + dt)), more, visit_exposures: total,
    });
    const p0 = page([["a", 0], ["b", 0.0015], ["g", 10]], true);
    const p1 = page([["c", 2], ["d", 2.0015], ["e", 6]], true);
    const p2 = page([["f", 8], ["h", 12], ["i", 12.0015]], false);
    const LIVE = "https://example.test/api/frames?ra=269.4462&dec=4.76883&clock=hours&fov=20&det=2";
    /** /api/frames answers by detector, page and (static files) mjd; anything else is a frame's pixels. */
    const serve = (pages: Record<string, FramesResponse | number>) =>
      vi.mocked(fetch).mockImplementation(async (input) => {
        const u = new URL(String(input), "https://example.test");
        if (u.pathname === "/api/frames") {
          const got = pages[`${u.searchParams.get("det")}:${u.searchParams.get("page") ?? 0}`] ?? 404;
          return typeof got === "number"
            ? new Response(JSON.stringify({ error: { code: got === 404 ? "not_found" : "upstream", message: "no" } }), { status: got })
            : new Response(JSON.stringify(got), { headers: { "Content-Type": "application/json" } });
        }
        return new Response(new Uint16Array(N * N).fill(1000).buffer);
      });
    const asked = () =>
      vi.mocked(fetch).mock.calls.map(([u]) => String(u)).filter((u) => u.includes("/api/frames?")).map((u) => new URL(u));
    const text = () => el.querySelector(".bk-pages-n")!.textContent;
    const sub = () => el.querySelector(".bk-when-sub")!.textContent;
    const main = () => el.querySelector(".bk-when-main")!.textContent;

    it("shows page 0 at once, adds the rest of the visit in time order by itself and keeps its photo", async () => {
      serve({ "2:1": p1, "2:2": p2 });
      const onFrames = vi.fn();
      const h = mountBlink(el, { onViewChange: vi.fn(), onPick: vi.fn(), onFrames });
      await h.show(hv, p0, LIVE, { singleDetector: true, pages: true });
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
      const at = main();
      expect(sub()).toMatch(/^exposure 2 of 3$/);
      expect(text()).toBe("3 of 9 photos of this visit loaded · loading…");
      await vi.waitFor(() => expect(text()).toBe("9 of 9 photos of this visit"));
      expect(sub()).toBe("exposure 2 of 9"); // the same photo, among nine now
      expect(main()).toBe(at);
      expect(main()).toMatch(/^12 Dec 2025, \d\d:\d\d UTC$/); // a photo: its own date and time
      expect(el.querySelector(".bk-pages button")).toBeNull(); // no button: it loads by itself
      expect(el.querySelectorAll(".bk-ep")).toHaveLength(9);
      expect(onFrames.mock.lastCall![0].epochs.map((e: Epoch) => e.id)).toEqual(
        ["a", "b", "c", "d", "e", "f", "g", "h", "i"].map((x) => `d2-${x}`));
      expect(asked().filter((u) => u.searchParams.has("page")).map((u) => u.searchParams.get("page"))).toEqual(["1", "2"]);
      h.destroy();
    });

    it("asks a few pages at once when the visit is long", async () => {
      // 3 photos shown, 21 in the visit: pages 1, 2 and 3 go together
      const big = (k: number, more: boolean) =>
        page([[`p${k}a`, k], [`p${k}b`, k + 0.0015], [`p${k}c`, k + 0.003], [`p${k}d`, k + 0.1], [`p${k}e`, k + 0.1015], [`p${k}f`, k + 0.103]], more, 2, 21);
      const gate: (() => void)[] = [];
      serve({ "2:1": big(1, true), "2:2": big(2, true), "2:3": big(3, false) });
      const ok = vi.mocked(fetch).getMockImplementation()!;
      vi.mocked(fetch).mockImplementation(async (input, init) => {
        if (String(input).includes("page=")) await new Promise<void>((go) => gate.push(go));
        return ok(input, init);
      });
      const h = mountBlink(el, { onViewChange: vi.fn(), onPick: vi.fn() });
      await h.show(hv, { ...p0, visit_exposures: 21 }, LIVE, { singleDetector: true, pages: true });
      await vi.waitFor(() => expect(gate).toHaveLength(3));
      expect(asked().map((u) => u.searchParams.get("page"))).toEqual(["1", "2", "3"]);
      gate.forEach((go) => go());
      await vi.waitFor(() => expect(text()).toBe("21 of 21 photos of this visit"));
      h.destroy();
    });

    /** Retries a few ms apart instead of 3, 6 and 12 s. */
    const quickRetries = () => {
      const real = globalThis.setTimeout;
      return vi.spyOn(globalThis, "setTimeout").mockImplementation(((fn: () => void, ms?: number) =>
        real(fn, ms != null && ms >= PAGE_RETRY_MS ? 5 : ms)) as typeof setTimeout);
    };

    it("asks a failed page again (a busy service), up to PAGE_TRIES times, then says plainly how many photos could not be loaded", async () => {
      quickRetries();
      let busy = 0;
      serve({ "2:1": p1, "2:2": p2 });
      const ok = vi.mocked(fetch).getMockImplementation()!;
      vi.mocked(fetch).mockImplementation(async (input, init) =>
        String(input).includes("page=2") && busy++ < 2 ? new Response(JSON.stringify({ error: { code: "upstream", message: "busy" } }), { status: 503 }) : ok(input, init));
      const h = mountBlink(el, { onViewChange: vi.fn(), onPick: vi.fn() });
      await h.show(hv, p0, LIVE, { singleDetector: true, pages: true });
      await vi.waitFor(() => expect(text()).toBe("9 of 9 photos of this visit"));
      expect(busy).toBe(3); // two 503s, then the page
      // a page that keeps failing: asked PAGE_TRIES times, the rest goes on, and the line says how many are missing
      serve({ "2:1": { ...p1, center: { ra: 10, dec: 4.76883 } }, "2:2": 502 });
      await h.show({ ...hv, ra: 10 }, { ...p0, center: { ra: 10, dec: 4.76883 } }, LIVE.replace("ra=269.4462", "ra=10"), { singleDetector: true, pages: true });
      await vi.waitFor(() =>
        expect(text()).toBe("6 of 9 photos of this visit loaded; 3 could not be loaded (SkyShift's live service did not answer for them)"));
      expect(asked().filter((u) => u.searchParams.get("ra") === "10" && u.searchParams.get("page") === "2")).toHaveLength(PAGE_TRIES);
      h.destroy();
    });

    it("asks a partial page again only when it lacks photos (cutouts too late), not when Gaia failed", async () => {
      quickRetries();
      serve({ "2:1": p1, "2:2": p2 });
      const ok = vi.mocked(fetch).getMockImplementation()!;
      const why: Record<string, string> = { "1": "Gaia failed", "2": "2 of 3 cutouts not in time; Gaia failed" };
      vi.mocked(fetch).mockImplementation(async (input, init) => {
        const res = await ok(input, init);
        const k = new URL(String(input), "https://example.test").searchParams.get("page");
        if (!k || !why[k]) return res;
        return new Response(await res.text(), { headers: { "Content-Type": "application/json", "X-SkyShift-Partial": why[k] } });
      });
      const h = mountBlink(el, { onViewChange: vi.fn(), onPick: vi.fn() });
      await h.show(hv, p0, LIVE, { singleDetector: true, pages: true });
      await vi.waitFor(() => expect(text()).toBe("9 of 9 photos of this visit"));
      await vi.waitFor(() => expect(asked().filter((u) => u.searchParams.get("page") === "2")).toHaveLength(2));
      expect(asked().filter((u) => u.searchParams.get("page") === "1")).toHaveLength(1);
      h.destroy();
    });

    it("keeps the pages on their way when the same plate is shown again (Show all), and aborts them for another plate", async () => {
      const gate: (() => void)[] = [];
      const signals: AbortSignal[] = [];
      let open = false;
      serve({ "2:1": p1, "2:2": p2 });
      const ok = vi.mocked(fetch).getMockImplementation()!;
      vi.mocked(fetch).mockImplementation(async (input, init) => {
        if (String(input).includes("page=") && !open) {
          if (init?.signal) signals.push(init.signal);
          await new Promise<void>((go) => gate.push(go));
        }
        return ok(input, init);
      });
      const h = mountBlink(el, { onViewChange: vi.fn(), onPick: vi.fn() });
      await h.show(hv, p0, LIVE, { singleDetector: true, pages: true });
      await vi.waitFor(() => expect(gate).toHaveLength(1)); // page 1 on its way
      // "Show all": the app says it is loading, then shows the same plate again
      h.status("loading", "Loading frames…");
      await h.show(hv, p0, LIVE, { singleDetector: true, pages: true });
      h.status("idle");
      open = true;
      gate.forEach((go) => go());
      await vi.waitFor(() => expect(text()).toBe("9 of 9 photos of this visit"));
      expect(asked().filter((u) => u.searchParams.has("page")).map((u) => u.searchParams.get("page"))).toEqual(["1", "2"]); // page 1 not asked again
      // another plate: the requests of the one before still on their way are aborted
      open = false;
      gate.length = 0;
      signals.length = 0;
      await h.show({ ...hv, ra: 10 }, { ...p0, center: { ra: 10, dec: 4.76883 } }, LIVE.replace("ra=269.4462", "ra=10"), { singleDetector: true, pages: true });
      await vi.waitFor(() => expect(gate).toHaveLength(1));
      expect(signals[0].aborted).toBe(false);
      await h.show({ ...hv, ra: 20 }, { ...p0, center: { ra: 20, dec: 4.76883 } }, LIVE.replace("ra=269.4462", "ra=20"), { singleDetector: true, pages: true });
      expect(signals[0].aborted).toBe(true);
      gate.forEach((go) => go());
      h.destroy();
    });

    it("continues a saved file live at the same visit, and an object's day of the find stays on show", async () => {
      // a saved file (an object's frames): the find's session, two photos
      const saved: FramesResponse = { ...frames, clock: "hours", epochs: [exp("d2-a", 2, T), exp("d2-b", 2, T + 0.0015)].map((e) => ({ ...e, data_url: `frames/${e.id}.bin` })) };
      serve({ "2:0": p0, "2:1": p1, "2:2": p2 });
      const onFrames = vi.fn();
      const h = mountBlink(el, { onViewChange: vi.fn(), onPick: vi.fn(), onFrames });
      // the plate shows the photos within a day of the find (T)
      const filter = (f: FramesResponse) => ({ ...f, epochs: f.epochs.filter((e) => Math.abs(e.mjd_mid - T) <= 1 || e.id === "d2-c") });
      await h.show(hv, saved, "https://example.test/data/cand/x/frames.json", { singleDetector: true, pages: true, visitMjd: T + 0.001, filter });
      expect(sub()).toBe("exposure 1 of 2");
      // the line says the plate shows fewer than the visit: else the visit looks short of photos
      await vi.waitFor(() => expect(text()).toBe("9 of 9 photos of this visit · the plate shows the 3 within a day of the find"));
      // page 0 asked at the find's time, the rest of the same visit after it
      expect(asked().map((u) => [u.searchParams.get("page"), u.searchParams.get("mjd")])).toEqual([[null, String(T + 0.001)], ["1", String(T + 0.001)], ["2", String(T + 0.001)]]);
      // the whole visit to the page, the find's day (and what the filter keeps) on the plate
      expect(onFrames.mock.lastCall![0].epochs).toHaveLength(9);
      expect(sub()).toMatch(/ of 3$/);
      // the saved photos' frame files stay the saved ones (a and b came from both)
      const files = vi.mocked(fetch).mock.calls.map(([u]) => String(u)).filter((u) => u.endsWith(".bin"));
      expect(files.some((u) => u.endsWith("/data/cand/x/frames/d2-a.bin"))).toBe(true);
      expect(files.some((u) => u.includes("/api/data/k/frames/h20_d2-a.bin"))).toBe(false);
      h.destroy();
    });

    it("a saved file that holds the whole visit: one look, then the count", async () => {
      const saved: FramesResponse = { ...frames, clock: "hours", epochs: [...p0.epochs, ...p1.epochs, ...p2.epochs].map((e) => ({ ...e, data_url: `frames/${e.id}.bin` })) };
      serve({ "2:0": p0 });
      const h = mountBlink(el, { onViewChange: vi.fn(), onPick: vi.fn() });
      await h.show(hv, saved, "https://example.test/data/barnard/frames_hours_20.json", { singleDetector: true, pages: true });
      expect(text()).toBe("9 photos of this visit loaded · loading…");
      await vi.waitFor(() => expect(text()).toBe("9 of 9 photos of this visit"));
      expect(asked()).toHaveLength(1);
      h.destroy();
    });

    it("gives the partner detector the same pages on All wavelengths", async () => {
      const q0 = page([["a", 0], ["b", 0.0015], ["g", 10]], true, 5, 6);
      const q1 = page([["c", 2], ["d", 2.0015], ["e", 6]], false, 5, 6);
      serve({ "2:1": { ...p1, more: false, visit_exposures: 6 }, "5:0": q0, "5:1": q1 });
      const h = mountBlink(el, { onViewChange: vi.fn(), onPick: vi.fn() });
      await h.show(hv, { ...p0, visit_exposures: 6 }, LIVE, { pages: true });
      await vi.waitFor(() => expect(text()).toBe("6 of 6 photos of this visit"));
      await vi.waitFor(() => expect(el.querySelector(".bk-det-label")!.textContent).toContain("D2 and D5"));
      // every moment of the six combines both detectors (a lone D2 moment would blink colour as change)
      for (let i = 0; i < 6; i++) {
        expect(el.querySelector(".bk-det-label")!.textContent).toContain("D2 and D5");
        document.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
      }
      expect(sub()).toMatch(/ of 6$/);
      // the detector's page 1 waited for its partner, and both were asked together
      const pages = asked().filter((u) => u.searchParams.has("page")).map((u) => `${u.searchParams.get("det")}:${u.searchParams.get("page")}`);
      expect(pages.sort()).toEqual(["2:1", "5:1"]);
      h.destroy();
    });

    it("asks the partner again at the plate's visit when its own fullest visit is another one (near the poles)", async () => {
      const far = page([["x", 180], ["y", 180.0015], ["z", 190]], false, 5, 3); // D5's June visit
      const q0 = page([["a", 0], ["b", 0.0015], ["g", 10]], false, 5, 3);
      vi.mocked(fetch).mockImplementation(async (input) => {
        const u = new URL(String(input), "https://example.test");
        if (u.pathname === "/api/frames") {
          const got = u.searchParams.get("det") === "5" && !u.searchParams.has("page") ? (u.searchParams.has("mjd") ? q0 : far) : 404;
          return typeof got === "number"
            ? new Response(JSON.stringify({ error: { code: "not_found", message: "no" } }), { status: 404 })
            : new Response(JSON.stringify(got), { headers: { "Content-Type": "application/json" } });
        }
        return new Response(new Uint16Array(N * N).fill(1000).buffer);
      });
      const h = mountBlink(el, { onViewChange: vi.fn(), onPick: vi.fn() });
      await h.show(hv, { ...p0, more: false, visit_exposures: 3 }, LIVE, { pages: true });
      await vi.waitFor(() => expect(el.querySelector(".bk-det-label")!.textContent).toContain("D2 and D5"));
      const d5 = asked().filter((u) => u.searchParams.get("det") === "5");
      expect(d5.map((u) => u.searchParams.get("mjd"))).toEqual([null, String(T + 0.0015)]); // the median of D2's photos
      h.destroy();
    });

    it("stops the partner's pages when All is turned off, and shows this detector's photos without waiting for them", async () => {
      const q0 = page([["a", 0], ["b", 0.0015], ["g", 10]], true, 5, 9);
      serve({ "2:1": p1, "2:2": p2, "5:0": q0 });
      const ok = vi.mocked(fetch).getMockImplementation()!;
      vi.mocked(fetch).mockImplementation(async (input, init) => {
        const u = new URL(String(input), "https://example.test");
        if (u.searchParams.get("det") === "5" && u.searchParams.has("page")) await new Promise(() => {}); // the partner's pages never land
        return ok(input, init);
      });
      const h = mountBlink(el, { onViewChange: vi.fn(), onPick: vi.fn() });
      await h.show(hv, p0, LIVE, { pages: true });
      await vi.waitFor(() => expect(asked().some((u) => u.searchParams.get("det") === "2" && u.searchParams.get("page") === "1")).toBe(true));
      await new Promise((r) => setTimeout(r, 50));
      expect(sub()).toMatch(/ of 3$/); // D2's page 1 waits for D5's
      el.querySelector<HTMLButtonElement>('.bk-det[data-det="2"]')!.click(); // All off: D2 alone
      await vi.waitFor(() => expect(text()).toBe("9 of 9 photos of this visit"));
      await vi.waitFor(() => expect(sub()).toMatch(/ of 9$/));
      expect(asked().filter((u) => u.searchParams.get("det") === "5" && u.searchParams.get("page") === "2")).toHaveLength(0);
      h.destroy();
    });

    it("dates a months visit by its weeks", async () => {
      const h = mountBlink(el, { onViewChange: vi.fn(), onPick: vi.fn() });
      await h.show(view, frames, "https://example.test/data/x/frames.json", { singleDetector: true });
      // visit 1: MJD 60902-60914, 15-27 Aug 2025
      expect(main()).toBe("15–27 Aug 2025");
      expect(el.querySelector<HTMLElement>(".bk-when")!.title).toContain("median stack of 18 photos");
      h.destroy();
    });
  });
});
