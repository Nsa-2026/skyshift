// Results as one page in sections: the sections and their order, Advanced adding and removing
// Data and methods and Tools live, show(param) opening and scrolling to a section or one part of
// it, and the Search progress section's polling running only while it can be seen.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import validationJson from "../../public/data/validation.json";
import distantJson from "../../public/data/distant.json";
import suspectsFx from "../../../fixtures/api/suspects.json";
import sample from "../live/fixtures/skyrun-sample.json";
import type { DistantResponse, SuspectsResponse, ValidationResponse } from "../api/types";
import type { PageContext } from "../app/modules";
import type { Snapshot } from "../community/source";
import { parseSkyrun, type SkyRun } from "../live/model";
import { _resetDetailsForTests, getDetails, setDetails } from "../prefs/details";
import { cleanVotes, type CandInfo } from "../community/stats";
import { SECTIONS, createResultsPage, mountResults, parseResultsParam, type ResultsDeps } from "./page";
import { completenessRows } from "./render";
import { RECALL_SCOPE } from "./checks";
import { distantPlain } from "./plain";

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

/** A vote snapshot where "pilot-m1" reached consensus as a mover (5 of 5). */
function snapshot(): Snapshot {
  const info = new Map<string, CandInfo>([
    ["pilot-m1", { id: "pilot-m1", kind: "mover", ra: 122.5, dec: 17, mjd: 60974.09, clock: "hours", det: 3, why: "Seen in two exposures." }],
  ]);
  const votes = cleanVotes([0, 1, 2, 3, 4].map((i) => ({ candidate_id: "pilot-m1", answer: "mover", ts: 1000 + i, voter: `v${i}` })));
  return { mode: "local", note: null, votes, info, listed: 1, dataRelease: "DR1", mine: {}, loadedAt: Date.now(), lookup: async () => new Map() };
}

interface Setup {
  el: HTMLElement;
  page: ReturnType<typeof createResultsPage>;
  load: ReturnType<typeof vi.fn>;
}

function setup(deps: Partial<ResultsDeps> = {}, liveDeps: ResultsDeps["live"] = {}): Setup {
  const el = document.createElement("main");
  document.body.append(el);
  const ctx: PageContext = { navigate: vi.fn(), explore: vi.fn() };
  const load = vi.fn(async (): Promise<SkyRun> => parseSkyrun(sample));
  const page = createResultsPage(el, ctx, {
    loadValidation: async () => validation,
    loadSuspects: async () => suspects,
    loadDistant: async () => distant,
    loadCandidates: async () => null,
    community: { load: async () => snapshot() },
    observer: null,
    ...deps,
    live: { load, intervalMs: 30, now: () => Date.parse("2026-11-14T19:14:40Z"), ...liveDeps },
  });
  return { el, page, load };
}

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

describe("Results sections", () => {
  it("has found, sure, progress and astronomers in order, and Advanced's two hidden", async () => {
    const { el, page } = setup();
    await page.ready();
    const secs = [...el.querySelectorAll<HTMLElement>(".rs-sec")];
    expect(secs.map((s) => s.id)).toEqual(["rs-found", "rs-sure", "rs-progress", "rs-astronomers", "rs-methods", "rs-tools"]);
    expect(secs.map((s) => text(s.querySelector("h2")))).toEqual(SECTIONS.map((s) => s.title));
    expect(secs.filter((s) => !s.hidden).map((s) => s.dataset.section)).toEqual(["found", "sure", "progress", "astronomers"]);
    // the page's one h1, a contents line pointing at each shown section, the quiet line for Advanced
    expect([...el.querySelectorAll("h1")].map(text)).toEqual(["Results"]);
    const toc = [...el.querySelectorAll<HTMLAnchorElement>(".rs-toc li:not([hidden]) a")];
    expect(toc.map((a) => a.getAttribute("href"))).toEqual(["#/results/found", "#/results/sure", "#/results/progress", "#/results/astronomers"]);
    expect(el.querySelector<HTMLElement>(".rs-more")!.hidden).toBe(false);
    expect(text(el.querySelector(".rs-more"))).toMatch(/More for astronomers.*turn on Advanced/);
    page.destroy();
  });

  it("draws what we found and how sure we are from the files, one heading level down", async () => {
    const { el, page } = setup();
    await page.ready();
    const found = el.querySelector("#rs-found-body")!;
    expect(found.querySelector("h3#sc-suspects-h")).not.toBeNull();
    expect(found.querySelector("h3#sc-distant-h")).not.toBeNull();
    expect(found.querySelector("h2")).toBeNull();
    // the suspects' table waits behind a disclosure, closed
    const rows = found.querySelector<HTMLDetailsElement>(".sc-suspect-rows")!;
    expect(rows.open).toBe(false);
    expect(text(rows.querySelector(".sc-show"))).toBe(`Show the ${suspects.suspects.length} suspects`);
    const sure = el.querySelector("#rs-sure-body")!;
    expect(sure.querySelectorAll(".sc-check")).toHaveLength(11);
    expect(sure.querySelectorAll(".sc-check h3.sc-claim")).toHaveLength(11);
    expect(text(sure.querySelector(".sc-summary"))).toBe("9 of 11 checks pass and 2 have not run.");
    // what the search can find: V5 recall with what it covers, V4, and the made-up distant planets
    // V5 from the shipped validation.json (it changes with every data refresh), not pinned
    const v5 = validation.checks.find((c) => c.id === "V5")!;
    expect([...sure.querySelectorAll(".rs-fig")].map(text)).toEqual([`${Math.round(Number(v5.value) * 100)}%`, "60 of 60", expect.stringMatching(/^\d+%$/)]);
    expect(text(sure.querySelector(".rs-figures"))).toContain(RECALL_SCOPE);
    page.destroy();
  });

  it("counts the objects to check by kind, with the plain line next to the count and the strongest leads", async () => {
    const { el, page } = setup({
      loadCandidates: async () =>
        ({
          generated: "2026-11-14",
          data_release: "DR1",
          zones: ["hunt", "pilot"],
          counts: { mover: 2, high_pm: 1, spectral: 0, shape: 1 },
          candidates: [{ kind: "mover", grade: "A" }, { kind: "mover", grade: "C" }, { kind: "high_pm" }, { kind: "shape" }],
        }) as never,
    });
    await page.ready();
    expect(text(el.querySelector(".rs-big"))).toBe("4 objects to check");
    expect(text(el.querySelector(".rs-count"))).toBe("2 fast movers, 1 moving star and 1 that may be one object or two, from 2 areas of sky.");
    // the zone ids are for Advanced only
    expect(text(el.querySelector(".rs-objects .pro-only"))).toBe("Listed in the hunt and pilot zones.");
    expect(text(el.querySelector(".rs-objects"))).toContain("Most turn out to be detector glitches once people look.");
    const best = el.querySelector(".rs-best")!;
    expect(text(best)).toBe("Strongest leads: 1 fast mover seen on the same steady path at different times. See it");
    expect(best.querySelector("a")!.getAttribute("href")).toBe("#/candidates/kind=mover&grade=A");
    page.destroy();
  });

  it("shows the community's list for astronomers alone: the consensus table, next steps, the rule", async () => {
    const { el, page } = setup();
    await page.ready();
    const astro = el.querySelector("#rs-astronomers-body")!;
    expect(astro.querySelectorAll(".cm-table tbody tr")).toHaveLength(1);
    expect(text(astro.querySelector(".cm-verdict"))).toContain("5 of 5 agree (100%)");
    expect(astro.querySelector<HTMLButtonElement>("[data-act=csv]")!.disabled).toBe(false);
    expect(text(astro)).toContain("What happens next");
    expect(text(astro)).toContain("It reaches consensus once it has at least 5 votes and 70% or more agree on one answer.");
    // the Community page's other parts stay on that page
    expect(astro.querySelector("h1, .cm-activity, .cm-debated, .cm-you, .cm-nick")).toBeNull();
    page.destroy();
  });
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

describe("plain view (Advanced off)", () => {
  async function drawn() {
    const s = setup({ loadCandidates: async () => CANDS as never, loadDistant: async () => distant });
    await s.page.ready();
    await vi.waitFor(() => expect(s.el.querySelector("#rs-progress-body .lv-big")).not.toBeNull());
    return s;
  }

  it("shows no check codes and none of the jargon, and says each section's point", async () => {
    const { el, page } = await drawn();
    const seen = seenText(el, "pro-only");
    for (const re of JARGON) expect(seen, `plain view shows ${re}`).not.toMatch(re);
    // what we found: the count by kind, the strongest leads, the distant planet, where to look
    expect(seen).toContain("6 objects to check");
    expect(seen).toContain("3 fast movers, 2 moving stars and 1 odd spectrum, from 4 areas of sky.");
    expect(seen).toContain("Strongest leads: 1 fast mover");
    expect(seen).toContain(distantPlain(distant));
    // how sure we are: plain results from validation.json, then the tally
    expect(seen).toContain("Found 60 of 60 known asteroids where NASA predicted them.");
    expect(seen).toContain("All 9 checks we ran passed; 2 more weren't run.");
    // search progress: the big number in plain words
    expect(text(el.querySelector("#rs-progress-body .lv-big.plain-only"))).toMatch(/^\d+ of 333 areas of sky searched \d+(\.\d)?%$/);
    expect(seen).toMatch(/[\d,]+ photos read so far\./);
    // list for astronomers: one sentence, the count, the rule in one line, the downloads
    expect(seen).toContain("Objects that people agree move or change go on a list for astronomers");
    expect(seen).toContain("1 object is on the list so far.");
    expect(seen).toContain("An object joins the list once at least 5 people have voted and 70% or more of them agree.");
    expect(seen).toContain("Download CSV");
    page.destroy();
  });

  it("keeps the codes, tables, machines and zone ids for Advanced", async () => {
    const { el, page } = await drawn();
    setDetails(true);
    const seen = seenText(el, "plain-only");
    for (const id of ["V1", "V2", "V5", "V9", "V11"]) expect(seen).toMatch(new RegExp(`\\b${id}\\b`));
    expect(seen).toMatch(/9 of 11 checks pass and 2 have not run/);
    expect(seen).toContain("What the search can find");
    expect(seen).toContain("Unknown movers");
    expect(seen).toContain("Listed in the hunt, pilot, ecl00 and ecl12n1 zones.");
    expect(seen).toMatch(/\d+ of 333 zones combined/);
    expect(seen).toContain("Machines");
    expect(seen).toContain("What happens next");
    expect(seen).not.toContain("areas of sky searched");
    page.destroy();
  });

  it("lists a few plain results, each with its check's own mark", async () => {
    const { el, page } = await drawn();
    const items = [...el.querySelectorAll<HTMLElement>(".rs-tests li")];
    expect(items.length).toBeGreaterThanOrEqual(3);
    expect(items.length).toBeLessThanOrEqual(5);
    const v5 = validation.checks.find((c) => c.id === "V5")!;
    expect(text(items[0])).toBe(`Pass: Found ${Math.round(Number(v5.value) * 100)}% of bright known asteroids without being told where to look.`);
    for (const li of items) {
      expect(li.className).toMatch(/^sc-(pass|partial|fail)$/);
      expect(li.querySelector("svg.sc-mark")).not.toBeNull();
    }
    page.destroy();
  });

  it("turns Advanced on from 'See every check' and keeps that section in view", async () => {
    const { el, page } = await drawn();
    el.querySelector<HTMLButtonElement>("#rs-sure [data-advanced-on]")!.click();
    expect(getDetails()).toBe(true);
    const scroll = Element.prototype.scrollIntoView as ReturnType<typeof vi.fn>;
    expect(scroll.mock.contexts.at(-1)).toBe(el.querySelector("#rs-sure"));
    page.destroy();
  });

  it("turns Advanced on for a link to one check or to the full distant-planet search", async () => {
    const { el, page } = await drawn();
    page.show("found/distant");
    await wait(5);
    expect(getDetails()).toBe(true);
    const scroll = Element.prototype.scrollIntoView as ReturnType<typeof vi.fn>;
    expect(scroll.mock.contexts.at(-1)).toBe(el.querySelector("#sc-distant-h"));
    setDetails(false);
    page.show("sure/V2");
    await wait(5);
    expect(getDetails()).toBe(true);
    expect(scroll.mock.contexts.at(-1)).toBe(el.querySelector("#sc-V2"));
    // a section the plain view shows stays plain
    setDetails(false);
    page.show("progress");
    await wait(5);
    expect(getDetails()).toBe(false);
    page.destroy();
  });
});

describe("Advanced", () => {
  it("adds Data and methods and Tools live, and takes them away again", async () => {
    const { el, page } = setup();
    await page.ready();
    setDetails(true);
    const methods = el.querySelector<HTMLElement>("#rs-methods")!;
    const tools = el.querySelector<HTMLElement>("#rs-tools")!;
    expect(methods.hidden).toBe(false);
    expect(tools.hidden).toBe(false);
    expect(el.querySelector<HTMLElement>(".rs-more")!.hidden).toBe(true);
    expect(el.querySelectorAll(".rs-toc li:not([hidden])")).toHaveLength(6);
    // closed, with one line saying what is inside
    expect(methods.querySelector(".rs-toggle")!.getAttribute("aria-expanded")).toBe("false");
    expect(methods.querySelector<HTMLElement>(".rs-gist")!.hidden).toBe(false);
    // Data and methods: the reviewed text, with the checks line from validation.json
    expect(text(methods.querySelector(".tl-validation"))).toContain("9 of 11 checks pass and 2 have not run.");
    expect(methods.querySelector('.tl-tested a[href="#/results/sure/V2"]')).not.toBeNull();
    // Tools as a section: no page title, headings one level down, its links under #/results/tools
    expect(tools.querySelector("h1, h2.tl-h2")).toBeNull();
    expect([...tools.querySelectorAll("h3.tl-h2")].map(text)).toEqual(["Download the list", "Batch coordinates", "API", "Cite and acknowledge", "Data and versions"]);
    expect(tools.querySelector('.tl-toc a[href="#/results/tools/batch"]')).not.toBeNull();
    expect(text(tools)).toContain("claude mcp add skyshift -- uv run --project mcp skyshift-mcp");
    setDetails(false);
    expect(methods.hidden).toBe(true);
    expect(tools.hidden).toBe(true);
    expect(el.querySelector<HTMLElement>(".rs-more")!.hidden).toBe(false);
    expect(el.querySelectorAll(".rs-toc li:not([hidden])")).toHaveLength(4);
    page.destroy();
  });

  it("turns on from the quiet line", async () => {
    const { el, page } = setup();
    el.querySelector<HTMLButtonElement>("[data-advanced-on]")!.click();
    expect(getDetails()).toBe(true);
    expect(el.querySelector<HTMLElement>("#rs-tools")!.hidden).toBe(false);
    page.destroy();
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

  it("scrolls to the section and focuses its heading", async () => {
    const { el, page } = setup();
    await page.ready();
    page.show("astronomers");
    await wait(5);
    const scroll = Element.prototype.scrollIntoView as ReturnType<typeof vi.fn>;
    expect(scroll.mock.contexts.at(-1)).toBe(el.querySelector("#rs-astronomers"));
    expect(document.activeElement).toBe(el.querySelector("#rs-astronomers-h .rs-toggle"));
    page.destroy();
  });

  it("goes to one check under How sure we are, and reopens a section closed by hand", async () => {
    const { el, page } = setup();
    await page.ready();
    el.querySelector<HTMLButtonElement>("#rs-sure-h .rs-toggle")!.click();
    expect(el.querySelector<HTMLElement>("#rs-sure-body")!.hidden).toBe(true);
    page.show("sure/V5");
    await wait(5);
    expect(el.querySelector<HTMLElement>("#rs-sure-body")!.hidden).toBe(false);
    const scroll = Element.prototype.scrollIntoView as ReturnType<typeof vi.fn>;
    expect(scroll.mock.contexts.at(-1)).toBe(el.querySelector("#sc-V5"));
    page.destroy();
  });

  it("turns Advanced on for Data and methods or Tools, opens it, and finds a Tools part", async () => {
    const { el, page } = setup();
    await page.ready();
    expect(getDetails()).toBe(false);
    page.show("methods");
    await wait(5);
    expect(getDetails()).toBe(true);
    expect(el.querySelector<HTMLElement>("#rs-methods-body")!.hidden).toBe(false);
    page.show("tools/batch");
    await wait(5);
    expect(el.querySelector<HTMLElement>("#rs-tools-body")!.hidden).toBe(false);
    const scroll = Element.prototype.scrollIntoView as ReturnType<typeof vi.fn>;
    expect(scroll.mock.contexts.at(-1)).toBe(el.querySelector("#tl-batch-h"));
    expect(document.activeElement).toBe(el.querySelector("#tl-batch-h"));
    page.destroy();
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
      observer: null,
    });
    await wait(5);
    const scroll = Element.prototype.scrollIntoView as ReturnType<typeof vi.fn>;
    expect(scroll.mock.contexts.at(-1)).toBe(el.querySelector("#rs-sure"));
    h.destroy();
    expect(el.innerHTML).toBe("");
  });
});

describe("Search progress", () => {
  it("is the Live page without its own title, writing an open zone under #/results/progress", async () => {
    const { el, page, load } = setup();
    await wait(5);
    expect(load).toHaveBeenCalled();
    const prog = el.querySelector("#rs-progress-body")!;
    expect(prog.querySelector(".lv-title, h1")).toBeNull();
    expect(prog.querySelectorAll("svg.lv-svg g.z")).toHaveLength(333);
    page.show("progress/hunt");
    await wait(5);
    expect(location.hash).toBe("#/results/progress/hunt");
    expect(prog.querySelector<HTMLElement>("[data-slot=panel]")!.hidden).toBe(false);
    page.destroy();
  });

  it("stops polling on hide() and starts again on show()", async () => {
    const { page, load } = setup();
    // polling: a second load follows the first
    await vi.waitFor(() => expect(load.mock.calls.length).toBeGreaterThan(1), { timeout: 3000 });
    page.hide();
    const at = load.mock.calls.length;
    await wait(150);
    expect(load.mock.calls.length).toBe(at);
    page.show(null);
    await vi.waitFor(() => expect(load.mock.calls.length).toBeGreaterThan(at), { timeout: 3000 });
    page.destroy();
  });

  it("stops polling when the section is closed", async () => {
    const { el, page, load } = setup();
    await vi.waitFor(() => expect(load.mock.calls.length).toBeGreaterThan(1), { timeout: 3000 });
    el.querySelector<HTMLButtonElement>("#rs-progress-h .rs-toggle")!.click();
    const at = load.mock.calls.length;
    await wait(150);
    expect(load.mock.calls.length).toBe(at);
    page.destroy();
  });

  it("polls only while the section is near the screen", async () => {
    let fire: ((entries: { isIntersecting: boolean }[]) => void) | null = null;
    const observed: Element[] = [];
    class FakeObserver {
      constructor(cb: (entries: { isIntersecting: boolean }[]) => void) {
        fire = cb;
      }
      observe(t: Element) {
        observed.push(t);
      }
      disconnect() {}
    }
    const { el, page, load } = setup({ observer: FakeObserver as unknown as typeof IntersectionObserver });
    expect(observed).toEqual([el.querySelector("#rs-progress")]);
    await wait(60);
    expect(load).not.toHaveBeenCalled();
    fire!([{ isIntersecting: true }]);
    await vi.waitFor(() => expect(load.mock.calls.length).toBeGreaterThan(1), { timeout: 3000 });
    fire!([{ isIntersecting: false }]);
    const at = load.mock.calls.length;
    await wait(150);
    expect(load.mock.calls.length).toBe(at);
    page.destroy();
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
