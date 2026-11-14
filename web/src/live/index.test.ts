import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import real from "./fixtures/skyrun-real-1410Z.json";
import real2 from "./fixtures/skyrun-real-1440Z.json";
import sample from "./fixtures/skyrun-sample.json";
import huntSuspects from "./fixtures/suspects-hunt-top30.json";
import type { Suspect } from "../api/types";
import { FeedError, httpSource, loadSkyrun, loadZoneSuspects, SKYRUN_URL } from "./load";
import { parseSkyrun, type SkyRun } from "./model";
import { createLivePage, mountLive, zonesToPolygons } from "./index";
import type { PageContext } from "../app/modules";

const T = (iso: string) => Date.parse(iso);
const flush = () => new Promise((r) => setTimeout(r, 0));

function page(load: (s: AbortSignal) => Promise<SkyRun>, now = T("2026-11-14T19:14:40Z")) {
  const el = document.createElement("main");
  document.body.append(el);
  const ctx: PageContext = { navigate: vi.fn(), explore: vi.fn() };
  const handle = createLivePage(el, ctx, { load, now: () => now });
  return { el, ctx, handle };
}

describe("Live search page", () => {
  beforeEach(() => {
    history.replaceState(null, "", "/#/live");
    document.body.innerHTML = "";
  });
  afterEach(() => vi.useRealTimers());

  it("waits for show() before loading, then draws the head, counters, machines and 333 zones", async () => {
    const load = vi.fn(async () => parseSkyrun(sample));
    const { el, handle } = page(load);
    expect(load).not.toHaveBeenCalled();
    expect(el.textContent).toContain("Loading tonight's run");
    handle.show(null);
    await flush();
    expect(load).toHaveBeenCalledTimes(1);
    const text = el.textContent!.replace(/\s+/g, " ");
    // counted from the data: four machines in the sample
    expect(text).toContain("Four cloud machines are reading every SPHEREx photo along the ecliptic tonight, looking for things that move.");
    const combined = sample.zones.filter((z) => z.state === "combined").length;
    expect(el.querySelector(".lv-big")!.textContent!.replace(/\s+/g, " ")).toContain(`${combined} of 333 zones combined`);
    expect(text).toContain("Updated 2 min ago");
    expect(text).toContain("498,935");
    expect(text).toMatch(/until the run ends at .*\(05:00 UTC\)/);
    expect(text).toMatch(/until the deadline at .*\(09:00 UTC\)/);
    expect(text).toMatch(/Final pass \(zone edges joined, WISE check\) planned from .*\(05:00 UTC\); until then results are provisional\./);
    expect(text).toContain("224 CPUs in all");
    // the stacked bar, its key in %, the projection marker and the time bar, with honest copy
    const seg = (c: string) => Number(el.querySelector<HTMLElement>(`.lv-pbar .${c}`)!.style.width.replace("%", ""));
    expect(seg("lv-seg-comb")).toBeCloseTo(sample.progress.combined_pct);
    expect(seg("lv-seg-ext")).toBeCloseTo(sample.progress.extracted_pct - sample.progress.combined_pct);
    expect(el.querySelector(".lv-pkey")!.textContent).toMatch(/40% combined.*6% extracted, not yet combined.*53% not yet extracted/);
    expect(el.querySelector<HTMLElement>(".lv-proj")!.style.left).toBe(`${Number(sample.progress.projected_extracted_pct.toFixed(2))}%`);
    expect(el.querySelector(".lv-proj-label")!.textContent).toMatch(/expected by .*\(05:00 UTC\)/);
    expect(el.querySelector(".lv-tbar")!.textContent).toMatch(/28% of the time to the deadline at .*\(09:00 UTC\) has gone/);
    expect(el.querySelector(".lv-honest")!.textContent).toMatch(/^333 zones is the plan for the whole ecliptic band; tonight's run reaches only part of it\./);
    expect(el.querySelectorAll("svg.lv-svg g.z")).toHaveLength(333);
    expect(el.querySelector('g.z[data-zone="hunt"]')!.getAttribute("data-state")).toBe("combined");
    expect(el.querySelector(".lv-hunt")).not.toBeNull();
    expect(el.querySelector(".lv-ecl")!.getAttribute("d")).toMatch(/^M/);
    // roving tabindex: exactly one zone in the tab order, the hunt zone first
    const tabbable = el.querySelectorAll('g.z[tabindex="0"]');
    expect(tabbable).toHaveLength(1);
    expect((tabbable[0] as SVGGElement).dataset.zone).toBe("hunt");
    // every state has both a fill and a pattern hook, and a label for screen readers
    expect(el.querySelector('g.z[data-state="failed"] .z-x')).not.toBeNull();
    expect(el.querySelector('g.z[data-zone="hunt"]')!.getAttribute("aria-label")).toMatch(/^Hunt zone: combined, 228 suspects\. RA 120\.5° to 130\.5°/);
    expect(el.querySelector(".lv-legend")!.textContent).toMatch(/queued, empty/);
    handle.destroy();
  });

  it("opens a zone on click: numbers with units, provisional note, blink and sky links", async () => {
    const { el, ctx, handle } = page(async () => parseSkyrun(sample));
    handle.show(null);
    await flush();
    const name = sample.zones.find((z) => z.state === "combined" && z.name !== "hunt")!.name;
    el.querySelector<SVGGElement>(`g.z[data-zone="${name}"]`)!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    const panel = el.querySelector<HTMLElement>(".lv-panel")!;
    expect(panel.hidden).toBe(false);
    expect(panel.textContent).toContain(`Zone ${name}`);
    expect(panel.textContent).toMatch(/Provisional: zone edges and the CatWISE check pending\./);
    expect(panel.textContent).toMatch(/\d deg²/);
    expect(panel.textContent).toMatch(/[\d,.]+( million)? dots/);
    expect(panel.textContent).toMatch(/\(\d\d:\d\d UTC\)/);
    expect(location.hash).toBe(`#/live/${name}`);
    expect(el.querySelector(`g.z[data-zone="${name}"]`)!.getAttribute("aria-pressed")).toBe("true");
    expect(el.querySelector(".lv-sel")!.getAttribute("d")).toMatch(/^M/);
    const z = sample.zones.find((q) => q.name === name)!;
    expect(panel.querySelector<HTMLAnchorElement>('a[href^="#/sky/"]')!.getAttribute("href")).toBe(
      `#/sky/${(z.ra[0] + z.ra[1]) / 2},${Math.round(((z.dec[0] + z.dec[1]) / 2) * 100) / 100},12`,
    );
    panel.querySelector<HTMLButtonElement>("[data-blink]")!.click();
    expect(ctx.explore).toHaveBeenCalledWith({ ra: (z.ra[0] + z.ra[1]) / 2, dec: expect.closeTo((z.dec[0] + z.dec[1]) / 2, 6) });
    panel.querySelector<HTMLButtonElement>("[data-close]")!.click();
    expect(panel.hidden).toBe(true);
    expect(location.hash).toBe("#/live");
    handle.destroy();
  });

  it("the hunt zone is final: no provisional note; its suspects load on demand, listed and marked on the map", async () => {
    const loadSuspects = vi.fn(async () => [...huntSuspects.suspects].sort((a, b) => b.snr - a.snr) as Suspect[]);
    const el = document.createElement("main");
    document.body.append(el);
    const ctx: PageContext = { navigate: vi.fn(), explore: vi.fn() };
    const handle = createLivePage(el, ctx, { load: async () => parseSkyrun(real2), now: () => T("2026-11-14T14:41:00Z"), loadSuspects });
    handle.show("hunt");
    await flush();
    const panel = el.querySelector<HTMLElement>(".lv-panel")!;
    expect(panel.hidden).toBe(false);
    expect(panel.textContent).toContain("Hunt zone");
    expect(panel.textContent).not.toContain("Provisional");
    expect(loadSuspects).toHaveBeenCalledWith("https://storage.googleapis.com/nasa-glazing-live/skyrun/suspects/hunt.json", expect.any(AbortSignal));
    await flush();
    const rows = panel.querySelectorAll(".lv-sus-row");
    expect(rows).toHaveLength(8);
    // highest S/N first, with position, detector, S/N and flux in µJy
    expect(rows[0].textContent!.replace(/\s+/g, " ")).toMatch(/RA 125\.6665°, Dec \+14\.9737° D1, S\/N 290, 2,960 µJy Blink/);
    expect(panel.textContent).toMatch(/The 8 with the highest S\/N of 30, marked in red on the map\. All 30 as JSON/);
    expect(el.querySelectorAll(".lv-sus-marks circle")).toHaveLength(30);
    rows[0].querySelector<HTMLButtonElement>(".lv-sus-blink")!.click();
    expect(ctx.explore).toHaveBeenCalledWith({ ra: 125.666546, dec: 14.973674, clock: "hours", det: 1 });
    // cached: opening it again doesn't fetch again; closing clears the marks
    panel.querySelector<HTMLButtonElement>("[data-close]")!.click();
    expect(el.querySelectorAll(".lv-sus-marks circle")).toHaveLength(0);
    el.querySelector<SVGGElement>('g.z[data-zone="hunt"]')!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    expect(loadSuspects).toHaveBeenCalledTimes(1);
    expect(el.querySelectorAll(".lv-sus-marks circle")).toHaveLength(30);
    handle.destroy();
  });

  it("shows the combiner of a combining zone and says when suspects can't load", async () => {
    const el = document.createElement("main");
    document.body.append(el);
    const loadSuspects = vi.fn(async () => {
      throw new FeedError("404", "not_connected", 404);
    });
    const handle = createLivePage(el, { navigate: vi.fn(), explore: vi.fn() }, { load: async () => parseSkyrun(sample), now: () => T("2026-11-14T19:14:40Z"), loadSuspects });
    const z = sample.zones.find((q) => q.state === "combining")!;
    handle.show(z.name);
    await flush();
    const panel = el.querySelector<HTMLElement>(".lv-panel")!;
    const dl = [...panel.querySelectorAll(".lv-pdl > div")].map((d) => `${d.querySelector("dt")!.textContent}: ${d.querySelector("dd")!.textContent}`);
    expect(dl).toContain(`Extracted by: ${z.machine}`);
    expect(dl).toContain(`Combining on: ${z.combiner}`);
    expect(panel.querySelector(".lv-pstate")!.textContent).toContain(`Combining on ${z.combiner}:`);
    expect(el.querySelector(`g.z[data-zone="${z.name}"]`)!.getAttribute("aria-label")).toContain(`combining on ${z.combiner}`);
    const done = sample.zones.find((q) => q.state === "combined" && q.name !== "hunt")!;
    el.querySelector<SVGGElement>(`g.z[data-zone="${done.name}"]`)!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    await flush();
    expect(panel.textContent).toContain("This zone's suspects couldn't be loaded.");
    expect(el.querySelectorAll(".lv-sus-marks circle")).toHaveLength(0);
    handle.destroy();
  });

  it("moves between zones with the arrow keys and opens one with Enter", async () => {
    const { el, handle } = page(async () => parseSkyrun(sample));
    handle.show(null);
    await flush();
    const hunt = el.querySelector<SVGGElement>('g.z[data-zone="hunt"]')!;
    hunt.focus();
    hunt.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowUp", bubbles: true }));
    const up = el.querySelector<SVGGElement>('g.z[data-zone="ecl12n1"]')!;
    expect(up.getAttribute("tabindex")).toBe("0");
    expect(hunt.getAttribute("tabindex")).toBe("-1");
    up.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    expect(el.querySelector<HTMLElement>(".lv-panel")!.textContent).toContain("Zone ecl12n1");
    // with the panel open, arrows browse
    up.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true }));
    expect(el.querySelector<HTMLElement>(".lv-panel")!.textContent).toMatch(/Zone ecl13/);
    el.querySelector(".lv-panel")!.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    expect(el.querySelector<HTMLElement>(".lv-panel")!.hidden).toBe(true);
    handle.destroy();
  });

  it("outlines zones whose state changed at the next refresh, in place", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    let run = parseSkyrun(sample);
    const { el, handle } = page(async () => run);
    handle.show(null);
    await vi.advanceTimersByTimeAsync(0);
    const g = el.querySelector<SVGGElement>('g.z[data-state="queued"]')!;
    const name = g.dataset.zone!;
    const next = structuredClone(sample);
    next.zones.find((z) => z.name === name)!.state = "extracting";
    next.zones.find((z) => z.name === name)!.machine = "worker";
    run = parseSkyrun(next);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(el.querySelector(`g.z[data-zone="${name}"]`)).toBe(g); // the same element: focus would stay
    expect(g.getAttribute("data-state")).toBe("extracting");
    expect(el.querySelectorAll(".lv-changed path")).toHaveLength(1);
    handle.destroy();
  });

  it("stops refreshing when hidden (another route) and resumes on show", async () => {
    vi.useFakeTimers();
    const load = vi.fn(async () => parseSkyrun(sample));
    const { handle } = page(load);
    handle.show(null);
    await vi.advanceTimersByTimeAsync(0);
    handle.hide!();
    await vi.advanceTimersByTimeAsync(10 * 60_000);
    expect(load).toHaveBeenCalledTimes(1);
    handle.show(null);
    await vi.advanceTimersByTimeAsync(0);
    expect(load).toHaveBeenCalledTimes(2);
    handle.destroy();
  });

  it("says calmly when the feed isn't connected, and retries", async () => {
    const load = vi.fn().mockRejectedValueOnce(new FeedError("404", "not_connected", 404)).mockResolvedValue(parseSkyrun(real));
    const { el, handle } = page(load, T("2026-11-14T14:12:00Z"));
    handle.show(null);
    await flush();
    expect(el.textContent).toContain("The live feed isn't connected yet.");
    expect(el.querySelector<HTMLElement>(".lv-stage")!.hidden).toBe(true);
    el.querySelector<HTMLButtonElement>("[data-retry]")!.click();
    await flush();
    expect(el.querySelector(".lv-alert")).toBeNull();
    expect(el.querySelectorAll("g.z")).toHaveLength(333);
    expect(el.textContent).toContain("1 of 333 zones combined");
    handle.destroy();
  });

  it("keeps the map when a later refresh fails, and warns when the numbers are stale", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const load = vi.fn().mockResolvedValueOnce(parseSkyrun(real)).mockRejectedValue(new TypeError("Failed to fetch"));
    const { el, handle } = page(load, T("2026-11-14T14:25:00Z"));
    handle.show(null);
    await vi.advanceTimersByTimeAsync(0);
    expect(el.querySelector(".lv-stale")!.textContent).toMatch(/No news from the machines for 14 min/);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(el.querySelector(".lv-alert")!.textContent).toMatch(/couldn't be loaded\. Showing the numbers from/);
    expect(el.querySelectorAll("g.z")).toHaveLength(333);
    handle.destroy();
  });

  it("exports mountLive, loadSkyrun and zonesToPolygons for other pages", () => {
    expect(typeof mountLive).toBe("function");
    expect(typeof loadSkyrun).toBe("function");
    expect(zonesToPolygons(parseSkyrun(real).zones)).toHaveLength(333);
  });
});

describe("the feed", () => {
  const ok = (body: unknown, type = "application/json") =>
    new Response(JSON.stringify(body), { status: 200, headers: { "content-type": type } });

  it("reads the public progress file by default, with a plain no-store GET", async () => {
    expect(SKYRUN_URL).toBe("https://storage.googleapis.com/nasa-glazing-live/skyrun/progress.json");
    const fetchFn = vi.fn(async () => ok(real));
    const run = await loadSkyrun(undefined, httpSource(SKYRUN_URL, fetchFn as never));
    expect(run.zones).toHaveLength(333);
    expect(fetchFn).toHaveBeenCalledWith(SKYRUN_URL, expect.objectContaining({ cache: "no-store", credentials: "omit" }));
  });

  it("tells 'not connected' from other failures", async () => {
    const kinds = async (fetchFn: () => Promise<Response>) =>
      httpSource("/x", fetchFn as never)().then(
        () => "ok",
        (e) => (e instanceof FeedError ? e.kind : e.name),
      );
    expect(await kinds(async () => new Response("nope", { status: 404 }))).toBe("not_connected");
    expect(await kinds(async () => new Response("<!doctype html>", { status: 200, headers: { "content-type": "text/html" } }))).toBe("not_connected");
    expect(await kinds(async () => new Response("{", { status: 200, headers: { "content-type": "application/json" } }))).toBe("not_connected");
    expect(await kinds(async () => Promise.reject(new TypeError("Failed to fetch")))).toBe("unreachable");
    expect(await kinds(async () => new Response("x", { status: 500 }))).toBe("server");
    const abort = new DOMException("aborted", "AbortError");
    expect(await kinds(async () => Promise.reject(abort))).toBe("AbortError");
  });

  it("reads a zone's suspects, brightest S/N first, dropping rows without a position", async () => {
    const body = { ...huntSuspects, suspects: [{ ...huntSuspects.suspects[3], ra: null }, ...[...huntSuspects.suspects].reverse()] };
    const fetchFn = vi.fn(async () => ok(body));
    const rows = await loadZoneSuspects("https://example.test/hunt.json", undefined, fetchFn as never);
    expect(rows).toHaveLength(30);
    expect(rows[0].snr).toBe(289.7);
    expect(rows.every((r, i) => i === 0 || rows[i - 1].snr >= r.snr)).toBe(true);
    expect(fetchFn).toHaveBeenCalledWith("https://example.test/hunt.json", expect.objectContaining({ cache: "no-store" }));
  });

  it("refuses a file that isn't schema 1 shaped", async () => {
    await expect(loadSkyrun(undefined, async () => ({ hello: 1 }))).rejects.toThrow(/schema/);
  });
});
