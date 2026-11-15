// Results as a one-screen overview and one page per section: the four cards and their links, each
// section's page showing only that section, Advanced adding its card and pages, show(param) opening a
// page and scrolling to one part of it, and the live feed polling only on #/results/progress.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import validationJson from "../../public/data/validation.json";
import distantJson from "../../public/data/distant.json";
import suspectsFx from "../../../fixtures/api/suspects.json";
import sample from "../live/fixtures/skyrun-sample.json";
import type { DistantResponse, SuspectsResponse, ValidationResponse } from "../api/types";
import type { PageContext } from "../app/modules";
import type { Snapshot } from "../community/source";
import { parseSkyrun, summarize, type SkyRun } from "../live/model";
import { _resetDetailsForTests, getDetails, setDetails } from "../prefs/details";
import { cleanVotes, type CandInfo } from "../community/stats";
import { SECTIONS, createResultsPage, mountResults, parseResultsParam, type ResultsDeps, type SectionId } from "./page";
import { completenessRows } from "./render";
import { RECALL_SCOPE } from "./checks";
import { distantPlain, distantVerdict } from "./plain";

const validation = validationJson as unknown as ValidationResponse;
const distant = distantJson as unknown as DistantResponse;
const suspects = suspectsFx as unknown as SuspectsResponse;
const wait = (ms = 0) => new Promise((r) => setTimeout(r, ms));
const text = (n: Element | null) => (n?.textContent ?? "").replace(/\s+/g, " ").trim();

function memoryStorage(): Storage {
  const m = new Map<string, string>();
  return {
    get length() {
      return m.size;
    },
    clear: () => m.clear(),
    getItem: (k) => (m.has(k) ? m.get(k)! : null),
    setItem: (k, v) => void m.set(k, String(v)),
    removeItem: (k) => void m.delete(k),
    key: (i) => [...m.keys()][i] ?? null,
  };
}

/** A vote snapshot where "pilot-m1" reached consensus as a mover (5 of 5); none with empty. */
function snapshot(empty = false): Snapshot {
  const info = new Map<string, CandInfo>([
    ["pilot-m1", { id: "pilot-m1", kind: "mover", ra: 122.5, dec: 17, mjd: 60974.09, clock: "hours", det: 3, why: "Seen in two exposures." }],
  ]);
  const votes = empty ? [] : cleanVotes([0, 1, 2, 3, 4].map((i) => ({ candidate_id: "pilot-m1", answer: "mover", ts: 1000 + i, voter: `v${i}` })));
  return { mode: "local", note: null, votes, info, listed: 1, dataRelease: "DR1", mine: {}, loadedAt: Date.now(), lookup: async () => new Map() };
}

/** A small candidates file in the contract's shape: zone ids, kinds, one grade A mover. */
const CANDS = {
  generated: "2026-11-15",
  data_release: "DR1+QR3",
  zones: ["hunt", "pilot", "ecl00", "ecl12n1"],
  counts: { mover: 3, high_pm: 2, spectral: 1, shape: 0 },
  candidates: [
    { kind: "mover", grade: "A" },
    { kind: "mover", grade: "B" },
    { kind: "mover", grade: "C" },
    { kind: "high_pm" },
    { kind: "high_pm" },
    { kind: "spectral" },
  ],
};

interface Setup {
  el: HTMLElement;
  page: ReturnType<typeof createResultsPage>;
  load: ReturnType<typeof vi.fn>;
  votes: ReturnType<typeof vi.fn>;
}

function setup(deps: Partial<ResultsDeps> = {}, opts: { emptyVotes?: boolean } = {}): Setup {
  const el = document.createElement("main");
  document.body.append(el);
  const ctx: PageContext = { navigate: vi.fn(), explore: vi.fn() };
  const load = vi.fn(async (): Promise<SkyRun> => parseSkyrun(sample));
  const votes = vi.fn(async () => snapshot(opts.emptyVotes));
  const page = createResultsPage(el, ctx, {
    loadValidation: async () => validation,
    loadSuspects: async () => suspects,
    loadDistant: async () => distant,
    loadCandidates: async () => CANDS as never,
    community: { load: votes },
    ...deps,
    live: { load, intervalMs: 30, now: () => Date.parse("2026-11-14T19:14:40Z") },
  });
  return { el, page, load, votes };
}

/** Show a view the way the shell does, and wait for the files, the votes and the feed. */
async function open(s: Setup, param: string | null) {
  s.page.show(param);
  await s.page.ready();
  await wait(5);
}

const shown = (el: Element) => [...el.querySelectorAll<HTMLElement>(".rs-view")].filter((v) => !v.hidden).map((v) => v.dataset.view);

beforeEach(() => {
  vi.stubGlobal("localStorage", memoryStorage());
  _resetDetailsForTests();
  history.replaceState(null, "", "/#/results");
  document.body.innerHTML = "";
  // Tools reads the static files through src/api: none here.
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response("<!doctype html>", { status: 404, statusText: "Not Found" })),
  );
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

/**
 * The text a visitor sees: leaves out what `skip` marks (prefs.css hides .pro-only with Advanced off,
 * .plain-only with it on), anything hidden, the body of a closed <details>, and the map drawing (its
 * box labels are hidden in the plain view by live.css).
 */
function seenText(root: Element, skip: "pro-only" | "plain-only"): string {
  const out: string[] = [];
  const walk = (n: Node) => {
    for (const c of Array.from(n.childNodes)) {
      if (c.nodeType === Node.TEXT_NODE) out.push(c.textContent ?? "");
      if (!(c instanceof Element)) continue;
      if (c.classList.contains(skip) || c.hasAttribute("hidden") || c.localName === "svg") continue;
      if (c instanceof HTMLDetailsElement && !c.open) {
        const s = c.querySelector(":scope > summary");
        if (s) walk(s);
        continue;
      }
      walk(c);
    }
  };
  walk(root);
  return out.join(" ").replace(/\s+/g, " ").trim();
}

/** Words and codes the plain view never shows (the user: "no jargon like V2"). */
const JARGON: RegExp[] = [
  /\bV\d+\b/,
  /pipeline/i,
  /S\/N/,
  /σ/,
  /χ²/,
  /\bgates?\b/i,
  /\bstages?\b/i,
  /provisional/i,
  /extracted/i,
  /combined zones|zones combined/i,
  /\bgrade [ABC]\b/i,
  /\brecall\b/i,
  /\bMJD\b/,
  /\becl\d+/i,
  /\b(hunt|pilot) zone/i,
  /\borphans?\b/i,
];

describe("the overview (#/results)", () => {
  it("shows the overview alone: the title, four cards in order, each with one link to its own page", async () => {
    const s = setup();
    await open(s, null);
    const { el, page } = s;
    expect(shown(el)).toEqual(["overview"]);
    expect([...el.querySelectorAll("h1")].filter((h) => !h.closest("[hidden]")).map(text)).toEqual(["Results"]);
    const cards = [...el.querySelectorAll<HTMLElement>(".rs-card")];
    expect(cards.map((c) => c.dataset.card)).toEqual(["found", "sure", "progress", "astronomers"]);
    expect(cards.map((c) => text(c.querySelector("h2")))).toEqual(["What we found", "How sure we are", "Search progress", "List for astronomers"]);
    for (const c of cards) {
      const links = [...c.querySelectorAll<HTMLAnchorElement>(".rs-card-more")];
      expect(links).toHaveLength(1);
      expect(links[0].getAttribute("href")).toBe(`#/results/${c.dataset.card}`);
      expect(text(links[0])).toMatch(/^More on .+ →$/);
      // one figure, at most one sentence
      expect(c.querySelectorAll(".rs-card-big")).toHaveLength(1);
      expect(c.querySelectorAll(".rs-card-line").length).toBeLessThanOrEqual(1);
    }
    // none of the sections' full content shows here
    expect(seenText(el, "pro-only")).not.toContain("We first tested the search");
    page.destroy();
  });

  it("puts one real figure and one sentence on each card", async () => {
    const s = setup();
    await open(s, null);
    const card = (id: string) => s.el.querySelector(`[data-card="${id}"]`)!;
    expect(text(card("found").querySelector(".rs-card-big"))).toBe("6 objects to check");
    expect(text(card("found").querySelector(".rs-card-line"))).toBe("Strongest lead: 1 fast mover on a steady path.");
    // from the shipped validation.json: every check that ran passed
    const ran = validation.checks.filter((c) => c.status !== "pending").length;
    expect(text(card("sure").querySelector(".rs-card-big"))).toBe(`${ran} of ${ran} checks we ran passed`);
    expect(card("sure").querySelector(".rs-card-big.sc-pass svg.sc-mark")).not.toBeNull();
    expect(text(card("sure").querySelector(".rs-card-line"))).toBe("60 of 60 known asteroids found; Barnard's Star matches Gaia.");
    const combined = sample.zones.filter((z) => z.state === "combined").length;
    expect(text(card("progress").querySelector(".rs-card-big"))).toBe(`${combined} of 333 areas of sky searched`);
    const sum = summarize(parseSkyrun(sample), Date.parse("2026-11-14T19:14:40Z"));
    expect(card("progress").querySelector<HTMLElement>(".rs-bar > span")!.style.width).toBe(`${sum.combinedPct.toFixed(2)}%`);
    expect(text(card("progress").querySelector(".rs-card-line"))).toBe(`${sample.totals.photos.toLocaleString("en-US")} photos read.`);
    expect(text(card("progress").querySelector(".rs-card-meta"))).toBe("Updated 2 min ago");
    // no map, no machines on the overview
    expect(card("progress").querySelector("svg, .lv-mach")).toBeNull();
    expect(text(card("astronomers").querySelector(".rs-card-big"))).toBe("1 object on the list");
    expect(card("astronomers").querySelector("[data-astro-csv]")).not.toBeNull();
    // the distant planet in one line, with the way to its part of What we found
    const strip = s.el.querySelector<HTMLElement>(".rs-planet-strip")!;
    expect(strip.hidden).toBe(false);
    expect(text(strip)).toBe(`A distant planet? ${distantVerdict(distant)} More on the distant planet →`);
    expect(strip.querySelector("a")!.getAttribute("href")).toBe("#/results/found/planet");
    s.page.destroy();
  });

  it("keeps each card's sentence to ten words or fewer, and its links to one", async () => {
    const s = setup();
    await open(s, null);
    const lines = [...s.el.querySelectorAll(".rs-card-line, .rs-strip-text")].map(text);
    expect(lines).toHaveLength(5);
    for (const l of lines) expect(l.split(" ").length, l).toBeLessThanOrEqual(10);
    // no lede restating the title
    expect(s.el.querySelector(".rs-overview .sc-lede")).toBeNull();
    s.page.destroy();
  });

  it("shows no check codes and none of the jargon", async () => {
    const s = setup();
    await open(s, null);
    const seen = seenText(s.el, "pro-only");
    for (const re of JARGON) expect(seen, `overview shows ${re}`).not.toMatch(re);
    expect(seen).toContain("turn on Advanced");
    s.page.destroy();
  });

  it("says when nothing is on the list yet, and offers a way to add one", async () => {
    const s = setup({}, { emptyVotes: true });
    await open(s, null);
    const card = s.el.querySelector('[data-card="astronomers"]')!;
    expect(text(card.querySelector(".rs-card-big"))).toBe("0 objects on the list so far");
    expect(card.querySelector('a[href="#/candidates/one=1"]')).not.toBeNull();
    expect(card.querySelector("[data-astro-csv]")).toBeNull();
    s.page.destroy();
  });

  it("downloads the list for astronomers as CSV from its card", async () => {
    const s = setup();
    await open(s, null);
    const blobs: Blob[] = [];
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn((b: Blob) => (blobs.push(b), "blob:x")), revokeObjectURL: vi.fn() }));
    const saved = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {});
    s.el.querySelector<HTMLButtonElement>("[data-astro-csv]")!.click();
    expect(blobs).toHaveLength(1);
    expect((saved.mock.contexts[0] as HTMLAnchorElement).download).toMatch(/^skyshift-list-for-astronomers-\d{4}-\d\d-\d\d\.csv$/);
    const csv = await new Promise<string>((done) => {
      const r = new FileReader();
      r.onload = () => done(String(r.result));
      r.readAsText(blobs[0]);
    });
    expect(csv).toContain("pilot-m1");
    s.page.destroy();
  });

  it("says each file that isn't there in a sentence, never a made-up number", async () => {
    const s = setup({ loadValidation: async () => null, loadCandidates: async () => null, loadDistant: async () => null });
    s.load.mockImplementation(async () => {
      throw new Error("offline");
    });
    await open(s, null);
    const card = (id: string) => s.el.querySelector(`[data-card="${id}"]`)!;
    expect(card("found").querySelector(".rs-card-big")).toBeNull();
    expect(text(card("found"))).toContain("published once the search has run");
    expect(card("sure").querySelector(".rs-card-big")).toBeNull();
    expect(text(card("sure"))).toContain("haven't run yet");
    expect(card("progress").querySelector(".rs-card-big")).toBeNull();
    expect(text(card("progress"))).toContain("The live feed couldn't be loaded.");
    expect(s.el.querySelector<HTMLElement>(".rs-planet-strip")!.hidden).toBe(true);
    s.page.destroy();
  });

  it("with Advanced on, adds one line for data, methods and tools", async () => {
    const s = setup();
    await open(s, null);
    expect(seenText(s.el, "pro-only")).not.toContain("Data, methods and tools");
    setDetails(true);
    const seen = seenText(s.el, "plain-only");
    expect(seen).toContain("Data, methods and tools");
    expect(seen).not.toContain("turn on Advanced");
    const links = [...s.el.querySelectorAll<HTMLAnchorElement>(".rs-adv-strip a")].map((a) => a.getAttribute("href"));
    expect(links).toEqual(["#/results/methods", "#/results/tools"]);
    s.page.destroy();
  });

  it("turns Advanced on from its quiet line", async () => {
    const s = setup();
    await open(s, null);
    s.el.querySelector<HTMLButtonElement>(".rs-overview [data-advanced-on]")!.click();
    expect(getDetails()).toBe(true);
    s.page.destroy();
  });
});

describe("each section's own page", () => {
  it("shows only that section, with a way back and its title as the one h1", async () => {
    const s = setup();
    for (const sec of ["found", "sure", "progress", "astronomers"] as SectionId[]) {
      await open(s, sec);
      expect(shown(s.el)).toEqual([sec]);
      const view = s.el.querySelector<HTMLElement>(`[data-view="${sec}"]`)!;
      expect([...view.querySelectorAll("h1")].map(text)).toEqual([SECTIONS.find((x) => x.id === sec)!.title]);
      expect(view.querySelector<HTMLAnchorElement>(".rs-back")!.getAttribute("href")).toBe("#/results");
      expect(text(view.querySelector(".rs-back"))).toBe("← Results");
    }
    s.page.destroy();
  });

  it("What we found: the objects, the strongest leads and the distant planet; suspects with Advanced", async () => {
    const s = setup();
    await open(s, "found");
    const found = s.el.querySelector("#rs-found-body")!;
    expect(text(found.querySelector(".rs-big"))).toBe("6 objects to check");
    expect(text(found.querySelector(".rs-count"))).toBe("3 fast movers, 2 moving stars and 1 odd spectrum, from 4 areas of sky.");
    expect(text(found.querySelector(".rs-objects .pro-only"))).toBe("Listed in the hunt, pilot, ecl00 and ecl12n1 zones.");
    expect(text(found.querySelector(".rs-best"))).toBe("Strongest leads: 1 fast mover on a steady path. See it");
    expect([...found.querySelectorAll(".rs-links a")].map(text)).toEqual(["See the list", "Review them"]);
    expect(found.querySelector(".rs-best a")!.getAttribute("href")).toBe("#/candidates/kind=mover&grade=A");
    expect(seenText(found, "pro-only")).toContain(distantPlain(distant));
    expect(found.querySelector("h3#sc-suspects-h")).not.toBeNull();
    expect(found.querySelector("h3#sc-distant-h")).not.toBeNull();
    // the suspects' table waits behind a disclosure, closed
    const rows = found.querySelector<HTMLDetailsElement>(".sc-suspect-rows")!;
    expect(rows.open).toBe(false);
    expect(text(rows.querySelector(".sc-show"))).toBe(`Show the ${suspects.suspects.length} suspects`);
    s.page.destroy();
  });

  it("How sure we are: a few plain results and the tally; every check with Advanced", async () => {
    const s = setup();
    await open(s, "sure");
    const sure = s.el.querySelector("#rs-sure-body")!;
    // the figure first, then the results, then one footnote
    expect(text(sure.querySelector(".rs-plain .rs-big"))).toBe("9 of 9 checks we ran passed");
    const items = [...sure.querySelectorAll<HTMLElement>(".rs-tests li")];
    expect(items.length).toBeGreaterThanOrEqual(3);
    expect(items.length).toBeLessThanOrEqual(5);
    const v5 = validation.checks.find((c) => c.id === "V5")!;
    expect(text(items[0])).toBe(`Pass: Found ${Math.round(Number(v5.value) * 100)}% of bright known asteroids unaided.`);
    for (const li of items) {
      expect(li.className).toMatch(/^sc-(pass|partial|fail)$/);
      expect(li.querySelector("svg.sc-mark")).not.toBeNull();
    }
    expect(text(sure.querySelector(".rs-tally"))).toBe("2 not run yet. See every check");
    expect(sure.querySelectorAll(".sc-check")).toHaveLength(11);
    expect(sure.querySelectorAll(".sc-check h3.sc-claim")).toHaveLength(11);
    expect(text(sure.querySelector(".sc-summary"))).toBe("9 of 11 checks pass and 2 have not run.");
    // what the search can find: V5 recall with what it covers, V4, and the made-up distant planets
    expect([...sure.querySelectorAll(".rs-fig")].map(text)).toEqual([`${Math.round(Number(v5.value) * 100)}%`, "60 of 60", expect.stringMatching(/^\d+%$/)]);
    expect(text(sure.querySelector(".rs-figures"))).toContain(RECALL_SCOPE);
    s.page.destroy();
  });

  it("Search progress: the live map and status, only on its page", async () => {
    const s = setup();
    await open(s, null);
    expect(s.el.querySelector("[data-view=overview] svg.lv-svg")).toBeNull();
    await open(s, "progress");
    const prog = s.el.querySelector("#rs-progress-body")!;
    await vi.waitFor(() => expect(prog.querySelectorAll("svg.lv-svg g.z")).toHaveLength(333));
    expect(prog.querySelector(".lv-title, h1")).toBeNull();
    expect(text(prog.querySelector(".lv-big.plain-only"))).toMatch(/^\d+ of 333 areas of sky searched \d+(\.\d)?%$/);
    const seen = seenText(prog, "pro-only");
    expect(seen).toMatch(/[\d,]+ photos read\./);
    // the lede is Advanced's: the number leads
    expect(seen).not.toContain("cloud machines");
    s.page.destroy();
  });

  it("List for astronomers: the community's list alone, with the rule and the downloads", async () => {
    const s = setup();
    await open(s, "astronomers");
    const astro = s.el.querySelector("#rs-astronomers-body")!;
    expect(astro.querySelectorAll(".cm-table tbody tr")).toHaveLength(1);
    expect(text(astro.querySelector(".cm-verdict"))).toContain("5 of 5 agree (100%)");
    expect(astro.querySelector<HTMLButtonElement>("[data-act=csv]")!.disabled).toBe(false);
    expect(text(astro)).toContain("What happens next");
    expect(text(astro)).toContain("It reaches consensus once it has at least 5 votes and 70% or more agree on one answer.");
    expect(astro.querySelector("h1, .cm-activity, .cm-debated, .cm-you, .cm-nick")).toBeNull();
    // plain: the count leads, then the rule in one line, then the downloads
    expect(seenText(astro, "pro-only")).toBe("1 object is on the list. See it An object joins at 5 votes, 70% agreeing. Download CSV Download JSON");
    // one read of the votes for the card and the page
    s.page.show(null);
    await s.page.ready();
    expect(s.votes).toHaveBeenCalledTimes(1);
    s.page.destroy();
  });

  it("shows no check codes and none of the jargon on any page, Advanced off", async () => {
    const s = setup();
    for (const sec of ["found", "sure", "progress", "astronomers"]) {
      await open(s, sec);
      if (sec === "progress") await vi.waitFor(() => expect(s.el.querySelector("#rs-progress-body .lv-big")).not.toBeNull());
      const seen = seenText(s.el, "pro-only");
      for (const re of JARGON) expect(seen, `${sec} shows ${re}`).not.toMatch(re);
    }
    s.page.destroy();
  });

  it("keeps the codes, tables, machines and zone ids for Advanced, each on its page", async () => {
    const s = setup();
    setDetails(true);
    await open(s, "sure");
    let seen = seenText(s.el, "plain-only");
    for (const id of ["V1", "V2", "V5", "V9", "V11"]) expect(seen).toMatch(new RegExp(`\\b${id}\\b`));
    expect(seen).toMatch(/9 of 11 checks pass and 2 have not run/);
    expect(seen).toContain("What the search can find");
    await open(s, "found");
    seen = seenText(s.el, "plain-only");
    expect(seen).toContain("Unknown movers");
    expect(seen).toContain("Listed in the hunt, pilot, ecl00 and ecl12n1 zones.");
    await open(s, "progress");
    await vi.waitFor(() => expect(s.el.querySelector("#rs-progress-body .lv-big")).not.toBeNull());
    seen = seenText(s.el, "plain-only");
    expect(seen).toMatch(/\d+ of 333 zones combined/);
    expect(seen).toContain("Machines");
    expect(seen).not.toContain("areas of sky searched");
    await open(s, "astronomers");
    expect(seenText(s.el, "plain-only")).toContain("What happens next");
    s.page.destroy();
  });

  it("turns Advanced on from 'See every check' and keeps the page's top in view", async () => {
    const s = setup();
    await open(s, "sure");
    s.el.querySelector<HTMLButtonElement>("#rs-sure [data-advanced-on]")!.click();
    expect(getDetails()).toBe(true);
    const scroll = Element.prototype.scrollIntoView as ReturnType<typeof vi.fn>;
    expect(scroll.mock.contexts.at(-1)).toBe(s.el.querySelector("#rs-sure"));
    s.page.destroy();
  });
});

describe("Advanced's pages", () => {
  it("Data and methods turns Advanced on and shows the methods with the checks line", async () => {
    const s = setup();
    expect(getDetails()).toBe(false);
    await open(s, "methods");
    expect(getDetails()).toBe(true);
    expect(shown(s.el)).toEqual(["methods"]);
    const methods = s.el.querySelector("#rs-methods-body")!;
    expect(text(methods.querySelector(".tl-validation"))).toContain("9 of 11 checks pass and 2 have not run.");
    expect(methods.querySelector('.tl-tested a[href="#/results/sure/V2"]')).not.toBeNull();
    // Advanced turned off on the page: one line says what it needs, and the content hides
    setDetails(false);
    const seen = seenText(s.el, "pro-only");
    expect(seen).toContain("Part of Advanced. Turn it on");
    expect(seen).not.toContain("The checks, in one line");
    s.page.destroy();
  });

  it("Tools is drawn the first time its page opens, and a part of it is scrolled to", async () => {
    const s = setup();
    await open(s, null);
    expect(s.el.querySelector("#rs-tools-body")!.firstChild).toBeNull();
    s.page.show("tools/batch");
    await wait(5);
    expect(getDetails()).toBe(true);
    expect(shown(s.el)).toEqual(["tools"]);
    const tools = s.el.querySelector("#rs-tools-body")!;
    expect(tools.querySelector("h1, h2.tl-h2")).toBeNull();
    expect([...tools.querySelectorAll("h3.tl-h2")].map(text)).toEqual(["Download the list", "Batch coordinates", "API", "Cite and acknowledge", "Data and versions"]);
    expect(tools.querySelector('.tl-toc a[href="#/results/tools/batch"]')).not.toBeNull();
    expect(text(tools)).toContain("claude mcp add skyshift -- uv run --project mcp skyshift-mcp");
    const scroll = Element.prototype.scrollIntoView as ReturnType<typeof vi.fn>;
    expect(scroll.mock.contexts.at(-1)).toBe(s.el.querySelector("#tl-batch-h"));
    expect(document.activeElement).toBe(s.el.querySelector("#tl-batch-h"));
    s.page.destroy();
  });
});

describe("show(param)", () => {
  it("reads the section and the part after it", () => {
    expect(parseResultsParam("sure/V5")).toEqual({ section: "sure", more: "V5" });
    expect(parseResultsParam("progress/ecl%2026")).toEqual({ section: "progress", more: "ecl 26" });
    expect(parseResultsParam("found")).toEqual({ section: "found", more: null });
    expect(parseResultsParam("elsewhere")).toBeNull();
    expect(parseResultsParam(null)).toBeNull();
  });

  it("goes to one check on How sure we are, turning Advanced on", async () => {
    const s = setup();
    await s.page.ready();
    s.page.show("sure/V5");
    await wait(5);
    expect(shown(s.el)).toEqual(["sure"]);
    expect(getDetails()).toBe(true);
    const scroll = Element.prototype.scrollIntoView as ReturnType<typeof vi.fn>;
    expect(scroll.mock.contexts.at(-1)).toBe(s.el.querySelector("#sc-V5"));
    s.page.destroy();
  });

  it("goes to the full distant-planet search with Advanced, and to its plain line without", async () => {
    const s = setup();
    await s.page.ready();
    const scroll = Element.prototype.scrollIntoView as ReturnType<typeof vi.fn>;
    s.page.show("found/planet");
    await wait(5);
    expect(getDetails()).toBe(false);
    expect(scroll.mock.contexts.at(-1)).toBe(s.el.querySelector("#rs-planet-h"));
    s.page.show("found/distant");
    await wait(5);
    expect(getDetails()).toBe(true);
    expect(scroll.mock.contexts.at(-1)).toBe(s.el.querySelector("#sc-distant-h"));
    // a section the plain view shows stays plain
    setDetails(false);
    s.page.show("progress");
    await wait(5);
    expect(getDetails()).toBe(false);
    s.page.destroy();
  });

  it("moves focus to the new page's title, and back to the overview's", async () => {
    const s = setup();
    await open(s, null);
    await open(s, "astronomers");
    expect(document.activeElement).toBe(s.el.querySelector("#rs-astronomers-h"));
    await open(s, null);
    expect(document.activeElement).toBe(s.el.querySelector("#rs-overview-h"));
    s.page.destroy();
  });

  it("opens a zone on the progress page, written under #/results/progress", async () => {
    const s = setup();
    s.page.show("progress/hunt");
    expect(shown(s.el)).toEqual(["progress"]);
    await vi.waitFor(() => expect(location.hash).toBe("#/results/progress/hunt"), { timeout: 3000 });
    expect(s.el.querySelector<HTMLElement>("#rs-progress-body [data-slot=panel]")!.hidden).toBe(false);
    s.page.destroy();
  });

  it("follows #/results/<section> by itself until the shell calls show()", async () => {
    history.replaceState(null, "", "/#/results/sure");
    const el = document.createElement("main");
    document.body.append(el);
    const h = mountResults(el, undefined, {
      loadValidation: async () => validation,
      loadSuspects: async () => null,
      loadDistant: async () => null,
      loadCandidates: async () => null,
      community: { load: async () => snapshot() },
      live: { load: async () => parseSkyrun(sample) },
    });
    await wait(5);
    expect(shown(el)).toEqual(["sure"]);
    history.replaceState(null, "", "/#/results");
    dispatchEvent(new HashChangeEvent("hashchange"));
    await wait(5);
    expect(shown(el)).toEqual(["overview"]);
    h.destroy();
    expect(el.innerHTML).toBe("");
  });
});

describe("the live feed", () => {
  it("is read once for the overview, not again within a minute, and never polled there", async () => {
    const s = setup();
    await open(s, null);
    await wait(100);
    expect(s.load).toHaveBeenCalledTimes(1);
    await open(s, "found");
    await open(s, null);
    await wait(60);
    expect(s.load).toHaveBeenCalledTimes(1);
    s.page.destroy();
  });

  it("polls only while #/results/progress is shown", async () => {
    const s = setup();
    await open(s, null);
    const first = s.load.mock.calls.length;
    s.page.show("progress");
    await vi.waitFor(() => expect(s.load.mock.calls.length).toBeGreaterThan(first + 1), { timeout: 3000 });
    // leaving for the overview stops it
    s.page.show(null);
    await wait(5);
    const at = s.load.mock.calls.length;
    await wait(150);
    expect(s.load.mock.calls.length).toBe(at);
    // and for another section
    s.page.show("progress");
    await vi.waitFor(() => expect(s.load.mock.calls.length).toBeGreaterThan(at), { timeout: 3000 });
    s.page.show("sure");
    await wait(5);
    const at2 = s.load.mock.calls.length;
    await wait(150);
    expect(s.load.mock.calls.length).toBe(at2);
    s.page.destroy();
  });

  it("stops polling on hide() and starts again on show()", async () => {
    const s = setup();
    s.page.show("progress");
    await vi.waitFor(() => expect(s.load.mock.calls.length).toBeGreaterThan(1), { timeout: 3000 });
    s.page.hide();
    const at = s.load.mock.calls.length;
    await wait(150);
    expect(s.load.mock.calls.length).toBe(at);
    s.page.show("progress");
    await vi.waitFor(() => expect(s.load.mock.calls.length).toBeGreaterThan(at), { timeout: 3000 });
    s.page.destroy();
  });
});

describe("completeness", () => {
  it("leaves out a figure whose check hasn't run or whose file is missing", () => {
    expect(completenessRows(null, null)).toEqual([]);
    const pending = { ...validation, checks: validation.checks.map((c) => ({ ...c, status: "pending" as const })) };
    expect(completenessRows(pending, null)).toEqual([]);
    const rows = completenessRows(validation, distant);
    expect(rows.map((r) => r.href)).toEqual(["#/results/sure/V5", "#/results/sure/V4", "#/results/found/distant"]);
  });
});
