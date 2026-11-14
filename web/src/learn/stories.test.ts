// Stories in the sky: routing, loading only when opened, and data → text checked against the real
// files in public/guxing (records, remnant pictures, planet events).
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { learnHref, mountLearn, parseLearnRoute, type LearnRoute } from "./index";
import {
  GUXING_FILES,
  _resetGuxingLoad,
  blinkAt,
  blinkHref,
  colourText,
  directionWords,
  eventLine,
  eventsIn,
  guestChecks,
  loadGuxing,
  measuredPath,
  monthsBetween,
  pickMonth,
  remnantCaption,
  resolveSpot,
  sourceHtml,
  spotList,
  type GuxingApi,
  type GuxingWindow,
  type NasaBundle,
} from "./stories-data";
import { STORY_PARTS } from "./stories-route";
import { TARGETS } from "../app/state";

const here = dirname(fileURLToPath(import.meta.url));
const GUXING = resolve(here, "../../public/guxing");
const text = (html: string) => html.replace(/<[^>]+>/g, "");

let gx: GuxingApi;
let nasa: NasaBundle;
beforeAll(() => {
  // Run the library as the page does: four classic scripts that set globals.
  for (const f of GUXING_FILES) (0, eval)(readFileSync(`${GUXING}/${f}.js`, "utf8"));
  const w = globalThis as unknown as GuxingWindow;
  gx = new w.Guxing!();
  nasa = w.GUXING_NASA!;
});

describe("stories routes", () => {
  it("reads the parts and their argument", () => {
    expect(parseLearnRoute("stories")).toEqual({ view: "stories", part: "guests", arg: null });
    expect(parseLearnRoute("stories/guests")).toEqual({ view: "stories", part: "guests", arg: null });
    expect(parseLearnRoute("stories/planets/2027-03")).toEqual({ view: "stories", part: "planets", arg: "2027-03" });
    expect(parseLearnRoute("stories/asterisms/83.6330,22.0145")).toEqual({ view: "stories", part: "asterisms", arg: "83.6330,22.0145" });
    expect(parseLearnRoute("stories/asterisms/SN1054")).toEqual({ view: "stories", part: "asterisms", arg: "SN1054" });
    expect(parseLearnRoute("stories/whatever")).toEqual({ view: "stories", part: "guests", arg: null });
  });
  it("round-trips through learnHref, and #214's tab links still land", () => {
    const all: LearnRoute[] = [
      { view: "stories", part: "guests", arg: null },
      { view: "stories", part: "asterisms", arg: null },
      { view: "stories", part: "asterisms", arg: "83.6330,-22.0145" },
      { view: "stories", part: "planets", arg: "2026-11" },
    ];
    for (const r of all) expect(parseLearnRoute(learnHref(r).replace(/^#\/learn\/?/, ""))).toEqual(r);
    expect(learnHref({ view: "stories", part: "guests", arg: null })).toBe("#/learn/stories");
    expect(STORY_PARTS.map((p) => p.id)).toEqual(["guests", "asterisms", "planets"]);
  });
});

describe("loading the library only when the page opens", () => {
  afterEach(() => {
    document.head.querySelectorAll("script[data-guxing]").forEach((s) => s.remove());
    _resetGuxingLoad();
  });
  it("adds the four scripts once, in order, and resolves when they ran", async () => {
    const w: GuxingWindow & { customElements: { get(n: string): unknown } } = { customElements: { get: () => undefined } };
    const p = loadGuxing(document, "/guxing/", w);
    expect(loadGuxing(document, "/guxing/", w)).toBe(p);
    const scripts = [...document.head.querySelectorAll<HTMLScriptElement>("script[data-guxing]")];
    expect(scripts.map((s) => s.getAttribute("src"))).toEqual(GUXING_FILES.map((f) => `/guxing/${f}.js`));
    expect(scripts.every((s) => s.async === false)).toBe(true);
    const g = globalThis as unknown as GuxingWindow;
    Object.assign(w, { GUXING_DATA: g.GUXING_DATA, Guxing: g.Guxing, GUXING_NASA: g.GUXING_NASA });
    w.customElements.get = () => class {};
    scripts.forEach((s) => s.dispatchEvent(new Event("load")));
    const libs = await p;
    expect(libs.nasa.guest_stars.items).toHaveLength(6);
  });
  it("names the file that failed and tries again next time", async () => {
    const w: GuxingWindow = {};
    const p = loadGuxing(document, "/guxing/", w);
    document.head.querySelector<HTMLScriptElement>('script[data-guxing="guxing-nasa"]')!.dispatchEvent(new Event("error"));
    await expect(p).rejects.toThrow("Couldn't download the old star maps (/guxing/guxing-nasa.js). Check your connection, then try again.");
    const again = loadGuxing(document, "/guxing/", w);
    expect(again).not.toBe(p);
    again.catch(() => {});
  });
  it("says so when a script answered but the library didn't start", async () => {
    const p = loadGuxing(document, "/guxing/", {});
    document.head.querySelectorAll("script[data-guxing]").forEach((s) => s.dispatchEvent(new Event("load")));
    await expect(p).rejects.toThrow("didn't start");
  });
});

describe("guest stars: records against positions", () => {
  const guest = (id: string) => nasa.guest_stars.items.find((g) => g.id === id)!;
  const checks = (id: string) => guestChecks(gx, guest(id)).map((c) => ({ ok: c.ok, text: text(c.html) }));

  it("has the six records, each with its remnant picture at the same position", () => {
    expect(nasa.guest_stars.items.map((g) => [g.id, g.year])).toEqual([
      ["SN185", 185],
      ["SN1006", 1006],
      ["SN1054", 1054],
      ["SN1181", 1181],
      ["SN1572", 1572],
      ["SN1604", 1604],
    ]);
    for (const g of nasa.guest_stars.items) {
      const r = nasa.spherex_remnants.items.find((x) => x.id === g.id)!;
      expect(r, g.id).toBeDefined();
      expect([r.ra, r.dec]).toEqual([g.ra, g.dec]);
      expect(existsSync(`${GUXING}/data/nasa/${r.image}`), r.image).toBe(true);
    }
    // The intro names the secondary sources and says nothing was checked against the originals.
    expect(nasa.guest_stars.about).toContain("Stephenson & Green 2002");
    expect(nasa.guest_stars.about).toContain("Xu, Pankenier & Jiang 2000");
    expect(nasa.guest_stars.items.every((g) => g.source_checked === false)).toBe(true);
  });

  it("SN 1006: lodge and asterism fit, the direction doesn't", () => {
    expect(checks("SN1006")).toEqual([
      { ok: true, text: "The record names the Root Lodge (氐宿), and in AD 1006 the remnant's position lay in the Root Lodge (氐宿)." },
      { ok: true, text: "The record names Imperial Guards (骑官): its nearest star, Imperial Guards III (骑官三), is 0.7° from the remnant." },
      { ok: false, text: "The record says west; the remnant lies east of Imperial Guards III (骑官三)." },
    ]);
  });
  it("SN 1054: near Celestial Pass, but north-west of it where the record says south-east", () => {
    const c = checks("SN1054");
    expect(c[0]).toEqual({ ok: null, text: "In AD 1054 the remnant's position lay in the Net Lodge (毕宿)." });
    expect(c[1]).toEqual({ ok: true, text: "The record names Celestial Pass (天关), a single star 1.1° from the remnant." });
    expect(c[2]).toEqual({ ok: false, text: "The record says south-east; the remnant lies north-west of Celestial Pass (天关)." });
  });
  it("SN 1572: the named asterism is far in this map; the closest one is said", () => {
    expect(checks("SN1572")[1]).toEqual({
      ok: false,
      text: "The record names Flying Corridor (阁道), but its nearest star, Flying Corridor III (阁道三), is 8.0° away. The closest asterism in this map is Wang Liang (王良), 1.5° away.",
    });
  });
  it("SN 185, 1181, 1604: fits", () => {
    expect(checks("SN185").map((c) => c.ok)).toEqual([null, true]);
    expect(checks("SN1181").map((c) => c.ok)).toEqual([true, true]);
    expect(checks("SN1604")).toEqual([{ ok: true, text: "The record names the Tail Lodge (尾宿), and in AD 1604 the remnant's position lay in the Tail Lodge (尾宿)." }]);
  });

  it("captions each picture from its own fields", () => {
    const crab = nasa.spherex_remnants.items.find((r) => r.id === "SN1054")!;
    expect(remnantCaption(crab)).toBe(
      "SPHEREx, 30′ across. North up, east left. Red 3.8–5.0 µm, green 1.5–2.4 µm, blue 0.75–1.1 µm. The circle shows the remnant's size, 7′ across.",
    );
    expect(remnantCaption(nasa.spherex_remnants.items.find((r) => r.id === "SN185")!)).toMatch(/^SPHEREx, 1.6° across\..*42′ across\.$/);
    expect(colourText("something else")).toBe("something else");
  });

  it("names the histories in English with the Chinese title", () => {
    expect(text(sourceHtml("《宋会要》"))).toBe("Song Huiyao (宋会要)");
    expect(text(sourceHtml("《宋史》《文献通考》 (via arXiv:2301.04807)"))).toBe("History of Song and Wenxian Tongkao (宋史, 文献通考), via arXiv:2301.04807");
    expect(text(sourceHtml("《未知》"))).toBe("《未知》");
    for (const g of nasa.guest_stars.items) expect(text(sourceHtml(g.source)), g.source).toMatch(/^[A-Z]/);
  });

  it("links each remnant to the Blink page at its spot, Months clock, 15′ field", () => {
    const crab = nasa.spherex_remnants.items.find((r) => r.id === "SN1054")!;
    expect(blinkHref(blinkAt(crab))).toBe("?ra=83.63300&dec=22.01450&clock=months&fov=15&det=2#/explore");
    const barnard = TARGETS.find((t) => t.slug === "barnard")!;
    expect(blinkHref(blinkAt(barnard))).toBe("?t=barnard#/explore");
  });

  it("words directions the English way", () => {
    expect(directionWords("东南")).toBe("south-east");
    expect(directionWords("西北")).toBe("north-west");
    expect(directionWords("东")).toBe("east");
  });
});

describe("around a spot", () => {
  const list = () => spotList(TARGETS, nasa.spherex_remnants.items, nasa.guest_stars.items);
  it("offers the featured fixed targets and the six remnants", () => {
    const l = list();
    expect(l.filter((s) => s.group === "featured").map((s) => s.key)).toEqual(TARGETS.filter((t) => t.clock === "months").map((t) => t.slug));
    expect(l.filter((s) => s.group === "guest").map((s) => s.label)).toContain("SN 1054: Crab Nebula (M1)");
  });
  it("resolves slugs, remnant ids, coordinates and the spot last blinked", () => {
    const l = list();
    expect(resolveSpot("barnard", l, null)!.group).toBe("featured");
    expect(resolveSpot("SN1054", l, null)).toMatchObject({ group: "guest", year: 1054, ra: 83.633 });
    expect(resolveSpot("83.6330,22.0145", l, null)).toMatchObject({ group: "typed", ra: 83.633, dec: 22.0145 });
    expect(resolveSpot("nonsense", l, null)).toBeNull();
    expect(resolveSpot("400,0", l, null)).toBeNull();
    expect(resolveSpot(null, l, { ra: 1, dec: 2, slug: "rdra", name: "R Draconis" })!.key).toBe("rdra");
    expect(resolveSpot(null, l, { ra: 10, dec: -20, slug: null, name: null })).toMatchObject({ group: "here", key: "10.0000,-20.0000" });
    expect(resolveSpot(null, l, null)!.key).toBe(l[0].key);
  });
  it("draws a path only for a shift SkyShift measured at three sigma or more", () => {
    const obj = (slug: string) => JSON.parse(readFileSync(resolve(here, `../../public/data/${slug}/object.json`), "utf8"));
    const b = measuredPath(obj("barnard").measure)!;
    expect(b.rate).toBeCloseTo(obj("barnard").measure.rate_arcsec_yr, 6);
    expect(b.pmdec).toBeGreaterThan(10000); // north
    expect(Math.hypot(b.pmra, b.pmdec)).toBeCloseTo(b.rate * 1000, 3);
    expect(measuredPath(obj("rwaur").measure)).toBeNull();
    expect(measuredPath(null)).toBeNull();
  });
});

describe("planets 2026–27", () => {
  it("covers the two years the label says", () => {
    expect(nasa.planets.start).toBe("2026-01-01");
    expect(nasa.planets.end).toBe("2027-12-31");
    const m = monthsBetween(nasa.planets.start, nasa.planets.end);
    expect(m).toHaveLength(24);
    expect([m[0], m[23]]).toEqual(["2026-01", "2027-12"]);
    // The glossary of terms on the page restates these conventions.
    expect((nasa.planets as unknown as { conventions: string }).conventions).toContain("犯 ≤ 1°");
    expect((nasa.planets as unknown as { conventions: string }).conventions).toContain("Moon ≤ 0.26°");
  });
  it("opens on this month, clamped into the data", () => {
    const m = monthsBetween("2026-01-01", "2027-12-31");
    expect(pickMonth(m, null, new Date("2026-11-14T22:00:00Z"))).toBe("2026-11");
    expect(pickMonth(m, "2027-03", new Date("2026-11-14T22:00:00Z"))).toBe("2027-03");
    expect(pickMonth(m, "1999-01", new Date("2025-05-01T00:00:00Z"))).toBe("2026-01");
    expect(pickMonth(m, null, new Date("2031-05-01T00:00:00Z"))).toBe("2027-12");
  });
  it("writes each event from its fields", () => {
    const nov = eventsIn(nasa.planets.events, "2026-11").map((e) => eventLine(gx, e));
    expect(nov).toHaveLength(14);
    const lines = nov.map((l) => `${l.date} | ${l.term.en} | ${text(l.html)}`);
    expect(lines).toContain("2 Nov | entering a lodge | Mercury (辰星) enters the Neck Lodge (亢宿) from the Root Lodge (氐宿).");
    expect(lines).toContain("11 Nov | trespassing | The Moon (月) passes 0.32° from Heart II (心宿二).");
    expect(lines).toContain("11 Nov | standing still | Venus (太白) stands still in the Chariot Lodge (轸宿), then turns prograde (eastward).");
    expect(lines).toContain("13 Nov | occultation | The Moon (月) covers Dipper IV (斗宿四), seen from the centre of the Earth.");
    // Every event of both years turns into a sentence without leftover codes.
    for (const e of nasa.planets.events) expect(text(eventLine(gx, e).html), e.time).not.toMatch(/undefined|NaN|: [犯掩留]/);
  });
});

describe("the Stories page", () => {
  let el: HTMLElement;
  const ctx = { navigate: vi.fn(), explore: vi.fn() };
  beforeEach(() => {
    vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {}, removeItem: () => {} });
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("offline"))));
    vi.spyOn(console, "warn").mockImplementation(() => {});
    ctx.explore.mockReset();
    el = document.createElement("main");
    document.body.appendChild(el);
  });
  afterEach(() => {
    el.remove();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("isn't loaded by the rest of Learn", () => {
    const page = mountLearn(el, ctx);
    page.show(null);
    page.show("2");
    page.show("glossary");
    expect(document.querySelector("script[data-guxing]")).toBeNull();
    expect(el.querySelector(".st-guest")).toBeNull();
    page.show(null);
    expect(el.querySelector('.ln-home-more a[href="#/learn/stories"]')).not.toBeNull();
    page.destroy();
    // Learn reaches the page's code only through a dynamic import, so it builds as its own chunk.
    const src = readFileSync(resolve(here, "index.ts"), "utf8");
    expect(src).toContain('import("./stories")');
    expect(src).not.toMatch(/from "\.\/stories(-data)?"/);
  });

  it("shows the guest stars with SPHEREx pictures and Blink links", async () => {
    const page = mountLearn(el, ctx);
    page.show("stories");
    expect(el.querySelector("h1")!.textContent).toBe("Stories in the sky");
    expect(document.title).toBe("Stories in the sky: SkyShift");
    expect(el.querySelector('.ln-side-more a[aria-current="page"]')!.getAttribute("href")).toBe("#/learn/stories");
    expect(el.querySelector('.ln-st-parts a[aria-current="page"]')!.textContent).toBe("Guest stars");
    await vi.waitFor(() => expect(el.querySelectorAll(".st-guest")).toHaveLength(6));
    const crab = el.querySelector("#st-SN1054")!;
    expect(crab.querySelector("h2")!.textContent).toBe("SN 1054 Crab Nebula (M1)");
    expect(crab.querySelector("img")!.getAttribute("src")).toBe("/guxing/data/nasa/spherex_remnants/SN1054.png");
    expect(crab.querySelector(".st-record [lang=zh]")!.textContent).toBe("客星出天关东南可数寸");
    expect(crab.querySelector(".st-differs")!.textContent).toContain("north-west");
    expect(crab.querySelector('a[href="#/learn/stories/asterisms/SN1054"]')).not.toBeNull();
    const blink = crab.querySelector<HTMLAnchorElement>("a[data-blink]")!;
    expect(blink.getAttribute("href")).toBe("?ra=83.63300&dec=22.01450&clock=months&fov=15&det=2#/explore");
    const scroll = vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    blink.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, button: 0 }));
    expect(ctx.explore).toHaveBeenCalledWith({ ra: 83.633, dec: 22.0145, clock: "months", fov: 15, det: 2 });
    expect(scroll).toHaveBeenCalledWith({ top: 0 });
    expect(el.querySelector(".st-credits")!.textContent).toContain("春。 and Codex");
    expect(el.querySelector(".st-credits")!.textContent).toContain("CC BY-SA");
    page.destroy();
  });

  it("lists a month of planet events and steps between months", async () => {
    const page = mountLearn(el, ctx);
    page.show("stories/planets/2026-11");
    await vi.waitFor(() => expect(el.querySelectorAll(".st-events li")).toHaveLength(14));
    expect(el.querySelector(".st-count")!.textContent).toBe("14 events in November 2026.");
    el.querySelector<HTMLButtonElement>('[data-step="1"]')!.click();
    expect(el.querySelector<HTMLSelectElement>("#st-month")!.value).toBe("2026-12");
    expect(location.hash).toBe("#/learn/stories/planets/2026-12");
    expect(el.querySelector(".st-count")!.textContent).toMatch(/in December 2026\.$/);
    page.destroy();
  });

  it("draws the old star map around a remnant and around typed coordinates", async () => {
    const page = mountLearn(el, ctx);
    page.show("stories/asterisms/SN1054");
    await vi.waitFor(() => expect(el.querySelector("guxing-card")).not.toBeNull());
    const card = el.querySelector("guxing-card")!;
    expect([card.getAttribute("ra"), card.getAttribute("dec"), card.getAttribute("epoch"), card.getAttribute("lang")]).toEqual(["83.633", "22.0145", "1054", "en"]);
    expect(card.shadowRoot!.textContent).toContain("Net Lodge");
    expect(el.querySelector<HTMLSelectElement>("#st-spot")!.value).toBe("SN1054");
    el.querySelector<HTMLInputElement>("#st-coords")!.value = "08h55m10.831s -7d14m42.53s";
    el.querySelector("form")!.dispatchEvent(new Event("submit", { cancelable: true }));
    const typed = el.querySelector("guxing-card")!;
    expect(Number(typed.getAttribute("ra"))).toBeCloseTo(133.795, 3);
    expect(location.hash).toMatch(/^#\/learn\/stories\/asterisms\/133\.79\d+,-7\.24\d+$/);
    el.querySelector<HTMLInputElement>("#st-coords")!.value = "not a place";
    el.querySelector("form")!.dispatchEvent(new Event("submit", { cancelable: true }));
    expect(el.querySelector(".st-msg")!.textContent).toMatch(/^Couldn't read that\./);
    page.destroy();
  });

  it("says what failed and offers to try again", async () => {
    const { renderStories } = await import("./stories");
    const body = document.createElement("div");
    el.append(body);
    const load = vi.fn().mockRejectedValueOnce(new Error("Couldn't download the old star maps (/guxing/guxing.js). Check your connection, then try again."));
    load.mockResolvedValueOnce({ gx, nasa });
    await renderStories(body, "guests", null, { signal: new AbortController().signal, explore: ctx.explore, load });
    expect(body.querySelector("[role=alert]")!.textContent).toContain("/guxing/guxing.js");
    body.querySelector<HTMLButtonElement>(".st-retry")!.click();
    await vi.waitFor(() => expect(body.querySelectorAll(".st-guest")).toHaveLength(6));
  });
});
