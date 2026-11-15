// The filters on the blink's plate when NASA's archive (IRSA) fails, stalls or is slow: a plain
// message, Try again and Back to the blink; never a blank plate, an endless wait or "nothing there".
// IRSA returned 502s on 14 Nov, so these are normal states.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { View } from "../app/modules";
import { IrsaError, REQUEST_TIMEOUT_MS, type Fetcher } from "./cube";
import { describeFailure } from "./failure";
import { mountFilters, type FiltersHandle } from "./index";

const VIEW: View = { ra: 133.79, dec: -7.24, clock: "months", fov: 5, det: 6, slug: null, name: null };

/** Buttons the blink's root click handler acted on (it reads data-mode, data-retry, … on any button). */
let blinkSaw: string[] = [];

/** Just the blink DOM the filters attach to (web/src/blink), with a Play button that toggles. */
function blinkDom(): HTMLElement {
  const el = document.createElement("div");
  el.innerHTML = `
    <div class="bk"><div class="bk-col">
      <div class="bk-mat"><div class="bk-plate"><canvas class="bk-canvas"></canvas></div></div>
      <div class="bk-strip"><button class="bk-play" type="button" aria-pressed="true">Play</button></div>
    </div></div>`;
  const play = el.querySelector<HTMLButtonElement>(".bk-play")!;
  play.addEventListener("click", () => play.setAttribute("aria-pressed", String(play.getAttribute("aria-pressed") !== "true")));
  // Like the blink's delegated handler (web/src/blink/index.ts, root "click").
  blinkSaw = [];
  el.querySelector(".bk")!.addEventListener("click", (ev) => {
    const b = (ev.target as Element).closest<HTMLButtonElement>("button");
    const d = b?.dataset ?? {};
    if (d.mode || d.retry !== undefined || d.ov || d.item) blinkSaw.push(JSON.stringify(d));
  });
  document.body.appendChild(el);
  return el;
}

const $ = <T extends Element>(el: Element, sel: string) => el.querySelector(sel) as T;
const noteText = (el: Element) => $<HTMLElement>(el, ".flt-note").textContent!.replace(/\s+/g, " ").trim();
const choose = (el: Element, mode: string) => {
  $<HTMLButtonElement>(el, ".flt-add").click();
  $<HTMLButtonElement>(el, `.flt-opts [data-flt-mode='${mode}']`).click();
};

let el: HTMLElement;
let filters: FiltersHandle;
let calls: number;

function mount(answer: Fetcher) {
  calls = 0;
  el = blinkDom();
  filters = mountFilters(el, (u, i) => {
    calls++;
    return answer(u, i);
  });
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  filters.destroy();
  el.remove();
  vi.useRealTimers();
});

const bad502: Fetcher = async () => new Response("Bad Gateway", { status: 502 });
/** Never answers; rejects only when its request is cancelled, like fetch. */
const hang: Fetcher = (_u, init) =>
  new Promise((_, reject) => init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError"))));

describe("filters and NASA's archive", () => {
  it("downloads nothing until a filter is chosen", async () => {
    mount(bad502);
    filters.loading(VIEW);
    filters.goto(VIEW, null);
    await vi.advanceTimersByTimeAsync(5000);
    expect(calls).toBe(0);
    expect($<HTMLElement>(el, ".flt-layer").hidden).toBe(true);
  });

  it("says in plain words that IRSA is failing (502), with Try again and Back to the blink", async () => {
    mount(bad502);
    filters.goto(VIEW, null);
    choose(el, "cold");
    expect(noteText(el)).toMatch(/Reading from NASA's SPHEREx map/);
    expect($(el, ".flt-note [data-flt-back]")).not.toBeNull(); // a way back even while reading
    await vi.advanceTimersByTimeAsync(10_000); // three retries with backoff
    const note = $<HTMLElement>(el, ".flt-note");
    expect(note.hidden).toBe(false);
    expect(note.dataset.kind).toBe("fail");
    expect(noteText(el)).toMatch(/Can't reach NASA's archive right now/);
    expect(noteText(el)).toMatch(/error 502/);
    expect(noteText(el)).toMatch(/still works/);
    expect(noteText(el)).not.toMatch(/nothing|no data/i);
    expect($<HTMLElement>(el, ".flt-key").hidden).toBe(true); // no key for a picture that isn't there
    expect($<HTMLCanvasElement>(el, ".flt-canvas").width).toBe(0); // no stale picture
    // It stops asking once it has failed.
    const after = calls;
    await vi.advanceTimersByTimeAsync(10_000);
    expect(calls).toBe(after);
    // Try again asks again.
    $<HTMLButtonElement>(el, ".flt-note [data-flt-retry]").click();
    await vi.advanceTimersByTimeAsync(10);
    expect(calls).toBeGreaterThan(after);
  });

  it("goes back to the blink: filter off, blink playing again, focus on the filter button", async () => {
    mount(bad502);
    filters.goto(VIEW, null);
    const play = $<HTMLButtonElement>(el, ".bk-play");
    choose(el, "nir");
    expect(play.getAttribute("aria-pressed")).toBe("false"); // paused under the filter
    await vi.advanceTimersByTimeAsync(10_000);
    $<HTMLButtonElement>(el, ".flt-note [data-flt-back]").click();
    expect($<HTMLElement>(el, ".flt-layer").hidden).toBe(true);
    expect($<HTMLElement>(el, ".flt-key").hidden).toBe(true);
    expect(el.querySelector(".bk")!.classList.contains("flt-on")).toBe(false);
    expect(play.getAttribute("aria-pressed")).toBe("true");
    expect(document.activeElement).toBe($(el, ".flt-add"));
    expect($(el, "[data-flt-mode='none']").getAttribute("aria-pressed")).toBe("true");
  });

  it("keeps its clicks from the blink's own buttons handler (no blink mode 'cold', no blink reload)", async () => {
    mount(bad502);
    filters.goto(VIEW, null);
    for (const m of ["cold", "nir", "wave", "none"]) choose(el, m);
    choose(el, "cold");
    await vi.advanceTimersByTimeAsync(10_000);
    $<HTMLButtonElement>(el, ".flt-note [data-flt-retry]").click();
    $<HTMLButtonElement>(el, ".flt-note [data-flt-back]").click();
    expect(blinkSaw).toEqual([]);
  });

  it("never waits forever: slow after 12 s, a plain failure when IRSA doesn't answer in time", async () => {
    mount(hang);
    filters.goto(VIEW, null);
    choose(el, "wave");
    await vi.advanceTimersByTimeAsync(13_000);
    expect(noteText(el)).toMatch(/slow right now; still trying/);
    await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS);
    expect($<HTMLElement>(el, ".flt-note").dataset.kind).toBe("fail");
    expect(noteText(el)).toMatch(/too slow right now/);
    expect($(el, ".flt-note [data-flt-retry]")).not.toBeNull();
  });

  it("switching filters during the first read doesn't fail the next one", async () => {
    mount(hang);
    filters.goto(VIEW, null);
    choose(el, "cold");
    await vi.advanceTimersByTimeAsync(100);
    choose(el, "nir"); // cancels the cold read; the shared tile reads must go on
    await vi.advanceTimersByTimeAsync(100);
    expect($<HTMLElement>(el, ".flt-note").dataset.kind).toBe("info");
    expect(noteText(el)).toMatch(/Reading from NASA's SPHEREx map/);
  });

  it("never shows one spot's picture while the blink loads another", async () => {
    mount(hang);
    filters.goto(VIEW, null);
    choose(el, "cold");
    filters.loading({ ...VIEW, ra: VIEW.ra + 1 });
    expect(noteText(el)).toMatch(/Waiting for the new spot/);
    expect($<HTMLCanvasElement>(el, ".flt-canvas").width).toBe(0);
  });

  it("disabling filters cancels pending reads and slider work, and returns to the blink without taking focus", async () => {
    const requests: AbortSignal[] = [];
    mount((url, init) => {
      requests.push(init.signal!);
      return hang(url, init);
    });
    filters.goto(VIEW, null);
    choose(el, "wave");
    await vi.advanceTimersByTimeAsync(100);
    expect(calls).toBeGreaterThan(0);
    const slider = $<HTMLInputElement>(el, ".flt-frame");
    slider.value = "63";
    slider.dispatchEvent(new Event("input", { bubbles: true }));
    $<HTMLButtonElement>(el, ".flt-note [data-flt-back]").focus();
    const addFocus = vi.spyOn($<HTMLButtonElement>(el, ".flt-add"), "focus");

    filters.active(false);
    expect(requests.every(signal => signal.aborted)).toBe(true);
    expect($<HTMLElement>(el, ".flt-bar").hidden).toBe(true);
    expect($<HTMLElement>(el, ".flt-layer").hidden).toBe(true);
    expect($<HTMLElement>(el, ".flt-key").hidden).toBe(true);
    expect($<HTMLElement>(el, ".flt-key").childElementCount).toBe(0);
    expect($<HTMLElement>(el, ".flt-note").hidden).toBe(true);
    expect(noteText(el)).toBe("");
    expect($<HTMLElement>(el, ".flt-menu").hidden).toBe(true);
    expect($<HTMLElement>(el, ".flt-wave").hidden).toBe(true);
    expect($<HTMLCanvasElement>(el, ".flt-canvas").width).toBe(0);
    expect($<HTMLElement>(el, ".flt-overlay").innerHTML).toBe("");
    expect(el.querySelector(".bk")!.classList.contains("flt-on")).toBe(false);
    expect(el.querySelector(".bk-col")!.classList.contains("has-flt")).toBe(false);
    expect($<HTMLButtonElement>(el, ".bk-play").getAttribute("aria-pressed")).toBe("true");
    expect($(el, "[data-flt-mode='none']").getAttribute("aria-pressed")).toBe("true");
    expect(addFocus).not.toHaveBeenCalled();

    const stoppedAt = calls;
    // Loading a new view, the old slider timer, and hidden controls cannot restart a read.
    filters.loading({ ...VIEW, ra: VIEW.ra + 1 });
    filters.goto(VIEW, null);
    choose(el, "cold");
    await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS + 13_000);
    expect(calls).toBe(stoppedAt);
    expect($<HTMLElement>(el, ".flt-note").hidden).toBe(true);

    filters.active(true);
    expect($<HTMLElement>(el, ".flt-bar").hidden).toBe(false);
    expect($<HTMLButtonElement>(el, ".flt-add").textContent).toContain("Add filters");
    await vi.advanceTimersByTimeAsync(5000);
    expect(calls).toBe(stoppedAt); // availability alone downloads nothing
    choose(el, "cold");
    await vi.advanceTimersByTimeAsync(10);
    expect(calls).toBeGreaterThan(stoppedAt);
  });

  // A tile-header read is shared by every filter of the field, so it has no abort signal: when IRSA
  // answers 5xx it waits and retries (cube.ts). Ending the filters must also end that wait. A
  // destroyed instance's retries used to land in the next test and break its call count.
  /** A 502 without a body: the retry's wait starts within fake time, nothing waits on a stream. */
  const bare502: Fetcher = async () => new Response(null, { status: 502 });

  it("a destroyed filter never asks IRSA again, even a header read waiting to retry", async () => {
    mount(bare502);
    filters.goto(VIEW, null);
    choose(el, "cold");
    await vi.advanceTimersByTimeAsync(10); // first answers are 502; the retries wait 400 ms
    expect(calls).toBeGreaterThan(0);
    const stoppedAt = calls;
    filters.destroy();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(calls).toBe(stoppedAt);
  });

  it("disabling filters ends a header read waiting to retry, even if they are enabled again at once", async () => {
    mount(bare502);
    filters.goto(VIEW, null);
    choose(el, "cold");
    await vi.advanceTimersByTimeAsync(10);
    expect(calls).toBeGreaterThan(0);
    const stoppedAt = calls;
    filters.active(false);
    filters.active(true);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(calls).toBe(stoppedAt); // the old read stays ended; availability alone downloads nothing
  });

  it("supports availability changes when the blink DOM is absent", () => {
    el = document.createElement("div");
    filters = mountFilters(el, bad502);
    expect(() => {
      filters.active(false);
      filters.goto(VIEW, null);
      filters.active(true);
    }).not.toThrow();
    expect(el.childElementCount).toBe(0);
  });
});

describe("failure words", () => {
  it("names each way IRSA can fail, without jargon in the title", () => {
    expect(describeFailure(new IrsaError("down", "IRSA answered 502", 502, true)).title).toBe("Can't reach NASA's archive right now");
    expect(describeFailure(new TypeError("Failed to fetch")).text).toMatch(/couldn't reach NASA's archive/);
    expect(describeFailure(new IrsaError("slow", "x")).title).toMatch(/too slow/);
    expect(describeFailure(new IrsaError("range", "x")).text).toMatch(/whole 320 MB/);
    expect(describeFailure(new Error("no IMAGE header in tile 7")).title).toBe("Can't show this filter right now");
  });
});
