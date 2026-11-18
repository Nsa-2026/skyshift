import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Epoch } from "../api/types";
import { CONSENSUS, emptyCounts, type KV } from "../vote/model";
import { centreCrop, classifyFakes, cropAround, noDataBlobs, noDataShare, sessions } from "./data";
import { learnHref, mountLearn, parseLearnRoute, type LearnRoute } from "./index";
import { LESSONS, LESSON_COUNT } from "./lessons";
import { _markSvg, paintNegative, skySubtracted } from "./plate";
import { PROGRESS_KEY, markDone, markOpened, nextLesson, readProgress, resetProgress } from "./progress";
import { BARNARD_PM, keyNumbers } from "./teachers";
import { verdict } from "./votes";
import targets from "../data/targets.json";
import { legacyHash } from "../app/legacy-routes";
import { siteRoute } from "../app/navigation";
import { MODES } from "../product/workspace-state";

const memKV = (init: Record<string, string> = {}): KV & { data: Map<string, string> } => {
  const data = new Map(Object.entries(init));
  return { data, get: (k) => data.get(k) ?? null, set: (k, v) => void data.set(k, v) };
};

describe("lesson routes", () => {
  it("reads the route argument the shell passes", () => {
    expect(parseLearnRoute(null)).toEqual({ view: "home" });
    expect(parseLearnRoute("")).toEqual({ view: "home" });
    expect(parseLearnRoute("3")).toEqual({ view: "lesson", n: 3 });
    expect(parseLearnRoute("7/")).toEqual({ view: "lesson", n: 7 });
    expect(parseLearnRoute("0")).toEqual({ view: "home" });
    expect(parseLearnRoute(String(LESSON_COUNT + 1))).toEqual({ view: "home" });
    expect(parseLearnRoute("glossary")).toEqual({ view: "glossary", slug: null });
    expect(parseLearnRoute("glossary/parallax")).toEqual({ view: "glossary", slug: "parallax" });
    expect(parseLearnRoute("teachers")).toEqual({ view: "teachers" });
    expect(parseLearnRoute("whatever")).toEqual({ view: "home" });
  });
  it("round-trips through learnHref", () => {
    const all: LearnRoute[] = [
      { view: "home" },
      { view: "first-star" },
      { view: "comet" },
      { view: "asteroid" },
      { view: "lesson", n: 4 },
      { view: "glossary", slug: null },
      { view: "glossary", slug: "ghost" },
      { view: "teachers" },
    ];
    for (const r of all) expect(parseLearnRoute(learnHref(r).replace(/^#\/learn\/?/, ""))).toEqual(r);
  });
});

describe("the course", () => {
  it("has seven numbered lessons, each ending in an action that goes somewhere real", () => {
    expect(LESSONS.map((l) => l.n)).toEqual([1, 2, 3, 4, 5, 6, 7]);
    const slugs = new Set(targets.targets.map((t) => t.slug));
    const sections = [...MODES.analyze.tabs, ...MODES.research.tabs].map(([k]) => k as string);
    for (const l of LESSONS) {
      expect(l.tryIt.length).toBeGreaterThan(0);
      for (const a of l.tryIt) {
        // Every action is one of today's routes: none that only an old-link rewrite saves.
        expect(legacyHash(a.href.replace(/^\?[^#]*/, "")), a.href).toBeNull();
        const m = /^\?t=([a-z0-9-]+)#\/explore(?:\/([a-z]+))?$/.exec(a.href);
        if (m) {
          expect(slugs.has(m[1]), a.href).toBe(true);
          if (m[2]) expect(sections, a.href).toContain(m[2]);
          expect(siteRoute(`#/explore${m[2] ? `/${m[2]}` : ""}`).screen).toBe("viewer");
        } else {
          expect(["sky", "check", "community", "results", "learn"], a.href).toContain(siteRoute(a.href).screen);
        }
      }
    }
    // Lessons that open the Viewer say so.
    expect(LESSONS.filter((l) => l.tryIt[0].href.includes("#/explore")).every((l) => l.tryTitle === "Try it in the Viewer")).toBe(true);
    expect(LESSONS[1].tryIt.map((a) => a.href)).toEqual(["?t=barnard#/explore", "?t=barnard#/explore/motion"]);
    expect(LESSONS[3].tryIt[0].href).toBe("#/learn/practice");
    expect(LESSONS[6].tryIt[0].href).toBe("?t=wise0855#/explore/sensitivity");
  });
  it("states the facts with their units", () => {
    const text = LESSONS.map((l) => l.lede() + l.body()).join(" ");
    expect(text).toContain("113 seconds");
    expect(text).toContain("6.15″");
    expect(text).toContain("0.75 to 2.4");
    expect(text).toContain("10.39″ a year");
    expect(text).toContain("2.1 minutes");
    expect(text).toContain(`at least ${CONSENSUS.minVotes} votes and at least ${CONSENSUS.share * 100}%`);
    expect(text).toMatch(/D1 and D4, D2 and D5, D3 and D6/);
  });
});

describe("progress", () => {
  it("survives garbage and keeps only real lesson numbers", () => {
    expect(readProgress(memKV({ [PROGRESS_KEY]: "{not json" }), 7)).toEqual({ done: [], last: null });
    expect(readProgress(memKV({ [PROGRESS_KEY]: JSON.stringify({ done: [3, 3, 9, "2", 1], last: 12 }) }), 7)).toEqual({
      done: [1, 3],
      last: null,
    });
  });
  it("remembers the last lesson opened and the lessons done", () => {
    const kv = memKV();
    markOpened(kv, 7, 2);
    markDone(kv, 7, 2);
    markDone(kv, 7, 1);
    markDone(kv, 7, 2);
    expect(readProgress(kv, 7)).toEqual({ done: [1, 2], last: 2 });
    expect(nextLesson(readProgress(kv, 7), 7)).toBe(3);
    resetProgress(kv);
    expect(readProgress(kv, 7)).toEqual({ done: [], last: null });
  });
  it("continues at the first unfinished lesson", () => {
    expect(nextLesson({ done: [], last: null }, 7)).toBe(1);
    expect(nextLesson({ done: [1, 2, 3], last: 5 }, 7)).toBe(5);
    expect(nextLesson({ done: [1, 2, 3, 5, 6, 7], last: 7 }, 7)).toBe(4);
    expect(nextLesson({ done: [1, 2, 3, 4, 5, 6, 7], last: 7 }, 7)).toBe(1);
  });
  it("never throws when storage does", () => {
    const broken: KV = {
      get: () => {
        throw new Error("blocked");
      },
      set: () => {
        throw new Error("blocked");
      },
    };
    expect(() => markDone(broken, 7, 1)).not.toThrow();
    expect(readProgress(broken, 7)).toEqual({ done: [], last: null });
  });
});

describe("the vote demo's verdict", () => {
  const c = (o: Partial<ReturnType<typeof emptyCounts>>) => ({ ...emptyCounts(), ...o });
  it("waits for five votes, then applies the 70% rule", () => {
    expect(verdict(c({}))).toMatch(/No votes yet/);
    expect(verdict(c({ mover: 4 }))).toMatch(/4 votes\. 1 more/);
    expect(verdict(c({ mover: 4, junk: 1 }))).toBe("Consensus: Mover, 4 of 5 (80%). It goes on the list for astronomers.");
    expect(verdict(c({ mover: 3, junk: 2 }))).toMatch(/No consensus: the most common answer, Mover, has 60%, short of 70%/);
    expect(verdict(c({ junk: 5 }))).toMatch(/^Consensus: Junk.*stays off the list/);
  });
});

describe("frames for lessons", () => {
  const ep = (id: string, mjd: number): Epoch =>
    ({ id, kind: "exposure", mjd_mid: mjd, mjd_start: mjd, mjd_end: mjd, det: 2, um_min: 1.1, um_max: 1.6, n_exposures: 1, flag_frac: 0, sat_frac: 0, data_url: "", scale: 1, zero: 0 }) as Epoch;
  it("groups exposures into sessions at gaps of 10 minutes", () => {
    const min = 1 / 1440;
    const s = sessions([ep("c", 1 + 30 * min), ep("a", 1), ep("b", 1 + 2.1 * min), ep("d", 1 + 32.1 * min), ep("e", 2)]);
    expect(s.map((x) => x.map((e) => e.id))).toEqual([["a", "b"], ["c", "d"], ["e"]]);
  });
  it("finds no-data regions and tells edges, one-photo spots and steady cores apart", () => {
    const nx = 10;
    const ny = 10;
    const blank = () => new Float32Array(nx * ny).fill(1);
    const a = blank();
    const b = blank();
    for (let x = 0; x < nx; x++) a[x] = a[nx + x] = NaN; // an edge band, 20 px
    for (const i of [44, 45, 54, 55, 46]) a[i] = NaN; // a one-photo spot, 5 px
    for (const i of [77, 78, 87, 88]) a[i] = b[i] = NaN; // a steady core, in both
    expect(noDataBlobs(a, nx, ny).map((x) => x.area)).toEqual([20, 5, 4]);
    expect(noDataShare(a)).toBeCloseTo(0.29);
    const k = classifyFakes(a, [b], nx, ny, 4);
    expect(k.edges.map((x) => x.area)).toEqual([20]);
    expect(k.oneOff.map((x) => x.area)).toEqual([5]);
    expect(k.steady.map((x) => x.area)).toEqual([4]);
    expect(k.oneOff[0].x).toBeCloseTo(4.8);
    expect(k.oneOff[0].y).toBeCloseTo(4.4);
  });
  it("crops squares that stay inside the frame", () => {
    expect(cropAround([{ x: 5, y: 5 }], [100, 100], 10, 40)).toEqual({ x: 0, y: 0, w: 40, h: 40 });
    expect(cropAround([{ x: 50, y: 40 }, { x: 70, y: 44 }], [100, 100], 10, 20)).toEqual({ x: 40, y: 22, w: 40, h: 40 });
    expect(cropAround([], [30, 30], 5, 10)).toEqual({ x: 0, y: 0, w: 30, h: 30 });
    expect(centreCrop([146, 146], 56)).toEqual({ x: 45, y: 45, w: 56, h: 56 });
  });
});

describe("the lesson plate", () => {
  it("paints a negative: sky to glass, stars to ink, no data transparent", () => {
    const glass: [number, number, number] = [237, 240, 239];
    const ink: [number, number, number] = [24, 32, 46];
    const px = paintNegative(Float32Array.from([0, 10, NaN, 5]), 2, { x: 0, y: 0, w: 2, h: 2 }, { lo: 0, hi: 10 }, { glass, ink });
    expect(Array.from(px.slice(0, 4))).toEqual([237, 240, 239, 255]);
    expect(Array.from(px.slice(4, 8))).toEqual([24, 32, 46, 255]);
    expect(px[11]).toBe(0);
  });
  it("crops before painting", () => {
    const v = Float32Array.from([0, 0, 0, 0, 10, 0, 0, 0, 0]);
    const px = paintNegative(v, 3, { x: 1, y: 1, w: 1, h: 1 }, { lo: 0, hi: 10 });
    expect(px.length).toBe(4);
    expect(px[0]).toBe(24);
  });
  it("levels each frame's sky", () => {
    const [a] = skySubtracted([Float32Array.from([5, 5, 5, 9, NaN])]);
    expect(Array.from(a.slice(0, 4))).toEqual([0, 0, 0, 4]);
    expect(Number.isNaN(a[4])).toBe(true);
  });
  it("draws grease-pencil marks by kind", () => {
    expect(_markSvg({ kind: "gaia", x: 1, y: 2, r: 3 })).toContain('class="lp-m lp-m-gaia"');
    expect(_markSvg({ kind: "known", x: 1, y: 2, r: 3 })).toContain("lp-m-known");
    expect(_markSvg({ kind: "flag", x: 1, y: 2, r: 3 }).match(/lp-m-flag/g)).toHaveLength(2);
    expect(_markSvg({ kind: "reticle", x: 1, y: 2, r: 3 }).match(/<line/g)).toHaveLength(4);
  });
});

describe("teacher answer key", () => {
  it("turns two dates into years and Gaia's prediction", () => {
    const k = keyNumbers(60913.3286, 61288.25, null);
    expect(k.years).toBeCloseTo(1.0265, 3);
    expect(k.predicted).toBeCloseTo(BARNARD_PM * k.years, 6);
    expect(BARNARD_PM).toBe(10.39);
  });
});

describe("the Learn page", () => {
  let el: HTMLElement;
  const ctx = { navigate: vi.fn(), explore: vi.fn() };
  let store: Map<string, string>;
  beforeEach(() => {
    // jsdom under Node 26 has no localStorage here; give the page a small one.
    store = new Map();
    vi.stubGlobal("localStorage", {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => void store.set(k, String(v)),
      removeItem: (k: string) => void store.delete(k),
    });
    // No network in tests: every example reports that it didn't load.
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("offline"))));
    vi.spyOn(console, "warn").mockImplementation(() => {});
    el = document.createElement("main");
    document.body.appendChild(el);
  });
  afterEach(() => {
    el.remove();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("shows the course as a path, a start button and a card for everything else", () => {
    const page = mountLearn(el, ctx);
    page.show(null);
    expect(el.querySelector("h1")!.textContent).toBe("Learn to read the SPHEREx sky");
    expect(document.title).toBe("Learn: SkyShift");
    expect(el.querySelectorAll(".ln-course li")).toHaveLength(LESSON_COUNT);
    expect(el.querySelector(".ln-home-cta .btn-primary")!.getAttribute("href")).toBe("#/learn/1");
    expect(el.querySelector(".ln-course li.is-next a")!.getAttribute("href")).toBe("#/learn/1");
    expect(el.querySelector(".ln-course .ln-tag")!.textContent).toBe("Start here");
    const cards = [...el.querySelectorAll<HTMLAnchorElement>(".ln-card a")];
    expect(cards.map((a) => a.getAttribute("href"))).toEqual(["#/learn/hunt", "#/learn/practice", "#/learn/stories", "#/learn/glossary", "#/learn/teachers"]);
    expect(cards.map((a) => a.querySelector(".ln-card-title")!.textContent)).toEqual(["Sky hunt", "Guided practice", "Stories in the sky", "Glossary", "For teachers"]);
    // Each card shows something real: SPHEREx pictures, a glossary entry, the worksheet.
    expect(el.querySelector<HTMLImageElement>(".ln-card-stories img")!.getAttribute("src")).toBe("/guxing/data/nasa/spherex_remnants/SN1054.png");
    expect(el.querySelector(".ln-card-glossary .ln-card-def")!.textContent).toMatch(/arcseconds per year/);
    expect(el.querySelector(".ln-card-practice .ln-card-meta")!.textContent).toBe("3 real examples");
    page.destroy();
  });

  it("shows guided practice progress on its card", () => {
    store.set("skyshift.explore.practice.v1", JSON.stringify([{ slug: "elpis", answer: "mover" }]));
    const page = mountLearn(el, ctx);
    page.show(null);
    expect(el.querySelector(".ln-card-practice .ln-card-meta")!.textContent).toBe("1 of 3 done");
    page.destroy();
  });

  it("shows one lesson with the course nav, glossary words and a pager", async () => {
    const page = mountLearn(el, ctx);
    page.show("2");
    expect(el.querySelector("h1")!.textContent).toBe("Blinking: how Pluto was found");
    expect(el.querySelector(".ln-head .ln-eyebrow")!.textContent).toBe("Lesson 2 of 7");
    expect(document.title).toBe("Blinking: how Pluto was found: SkyShift");
    expect(el.querySelector(".ln-try h2")!.textContent).toBe("Try it in the Viewer");
    expect(el.querySelector('.ln-toc a[aria-current="page"]')!.getAttribute("href")).toBe("#/learn/2");
    expect(el.querySelectorAll(".ln-body .gl-term").length).toBeGreaterThan(2);
    expect(el.querySelector(".ln-next")!.getAttribute("href")).toBe("#/learn/3");
    expect(el.querySelector(".ln-try a")!.getAttribute("href")).toBe("?t=barnard#/explore");
    await vi.waitFor(() => expect(el.querySelector(".ln-fail")).not.toBeNull());
    expect(el.querySelector(".ln-fail a")!.getAttribute("href")).toBe("?t=barnard#/explore");
    expect(el.querySelector(".ln-fail")!.textContent).toContain("Open it in the Viewer");
    page.destroy();
  });

  it("remembers progress in localStorage and offers to continue", () => {
    const page = mountLearn(el, ctx);
    page.show("3");
    el.querySelector<HTMLAnchorElement>(".ln-next")!.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    const saved = JSON.parse(store.get(PROGRESS_KEY)!);
    expect(saved).toEqual({ done: [3], last: 3 });
    page.show("");
    expect(el.querySelector(".ln-home-cta .btn-primary")!.getAttribute("href")).toBe("#/learn/4");
    expect(el.querySelector(".ln-course li:nth-child(3)")!.classList.contains("is-done")).toBe(true);
    expect(el.querySelector(".ln-course li.is-next a")!.getAttribute("href")).toBe("#/learn/4");
    expect(el.querySelector(".ln-course .ln-tag")!.textContent).toBe("Up next");
    expect(el.querySelector(".ln-home-progress")!.textContent).toContain("1 of 7 lessons done.");
    el.querySelector<HTMLButtonElement>(".ln-reset")!.click();
    expect(JSON.parse(store.get(PROGRESS_KEY)!)).toEqual({ done: [], last: null });
    page.destroy();
  });

  it("renders the glossary with an anchor per word", () => {
    const page = mountLearn(el, ctx);
    page.show("glossary/parallax");
    expect(el.querySelectorAll(".ln-gloss-item").length).toBeGreaterThanOrEqual(25);
    expect(el.querySelector("#g-parallax dt a")!.getAttribute("href")).toBe("#/learn/glossary/parallax");
    // One button per first letter, each jumping to the first word with it.
    const letters = [...el.querySelectorAll<HTMLButtonElement>(".ln-az button")].map((b) => b.textContent);
    expect(letters).toContain("P");
    expect(el.querySelectorAll(".ln-gloss-item[data-first]")).toHaveLength(letters.length);
    const scroll = vi.fn();
    el.querySelector<HTMLElement>('[data-first="P"]')!.scrollIntoView = scroll;
    el.querySelector<HTMLButtonElement>('.ln-az button[data-letter="P"]')!.click();
    expect(scroll).toHaveBeenCalled();
    page.destroy();
  });

  it("renders the teacher corner with a plan, a worksheet and a print button", () => {
    const page = mountLearn(el, ctx);
    page.show("teachers");
    expect(el.querySelectorAll(".ln-plan li")).toHaveLength(5);
    expect(el.querySelectorAll(".ln-sheet tbody tr").length).toBeGreaterThanOrEqual(5);
    expect(el.querySelector(".ln-print")).not.toBeNull();
    expect(el.querySelector('a[href="#/results/tools"]')).not.toBeNull();
    expect(el.querySelector('a[href="#/candidates/one=1"]')).not.toBeNull();
    expect(el.textContent).not.toMatch(/Blink page|Review queue/);
    page.destroy();
  });

  it("falls back to the course for unknown routes", () => {
    const page = mountLearn(el, ctx);
    page.show("99");
    expect(el.querySelector(".ln-home")).not.toBeNull();
    page.destroy();
  });
});
