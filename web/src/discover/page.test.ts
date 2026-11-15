import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import fixture from "../../../fixtures/api/candidates.json";
import type { CandidatesResponse } from "../api/types";
import type { Snapshot } from "../community/source";
import type { QueueOptions } from "../review";
import type { Answer } from "../vote/model";

const loadCandidates = vi.fn<() => Promise<CandidatesResponse | null>>();
vi.mock("../api/client", async (orig) => ({ ...(await orig<typeof import("../api/client")>()), loadCandidates: () => loadCandidates() }));
const AGREED = new Map([["pilot-pm-0000", { mover: 6, changing: 0, junk: 1, unsure: 0 }]]);
let voteCounts = AGREED;
/** The vote server can't be reached: openVoteReader fails. */
let votesDown = false;
vi.mock("./votes", () => ({
  openVoteReader: async () => {
    if (votesDown) throw new Error("offline");
    return {
      mode: "local",
      notice: null,
      counts: async () => voteCounts,
      votedOn: (id: string) => id === "pilot-pm-0000",
    };
  },
  onVoted: () => () => {},
}));
// The review queue (src/review) is the real one on the site; here a stand-in records what it's given.
const queue = { scope: vi.fn(), show: vi.fn(), hide: vi.fn(), destroy: vi.fn() };
const mountQueue = vi.fn((_el: HTMLElement, _opts: QueueOptions) => queue);
vi.mock("../review", () => ({ mountQueue: (el: HTMLElement, opts: QueueOptions) => mountQueue(el, opts) }));
const download = vi.fn();
vi.mock("./export", async (orig) => ({ ...(await orig<typeof import("./export")>()), download: (...a: unknown[]) => download(...a) }));
const loadSnapshot = vi.fn<(o: unknown) => Promise<Snapshot>>();
vi.mock("../community/source", async (orig) => ({ ...(await orig<typeof import("../community/source")>()), loadSnapshot: (o: unknown) => loadSnapshot(o) }));

const { mountDiscover } = await import("./index");
const { setDetails, _resetDetailsForTests, DETAILS_KEY } = await import("../prefs/details");
const { infoFromCatalogue } = await import("../community/source");
const data = fixture as unknown as CandidatesResponse;
const tick = () => new Promise((r) => setTimeout(r, 0));

async function mount(hash = "#/candidates") {
  history.replaceState(null, "", `/${hash}`);
  const el = document.createElement("main");
  document.body.append(el);
  const navigate = vi.fn();
  const page = mountDiscover(el, { navigate, explore: vi.fn() });
  for (let i = 0; i < 5; i++) await tick();
  page.show(hash.replace(/^#\/candidates\/?/, "") || null);
  return { el, page, navigate };
}

describe("the Candidates page", () => {
  beforeEach(() => loadCandidates.mockResolvedValue(structuredClone(data)));
  afterEach(() => {
    document.body.innerHTML = "";
    vi.unstubAllEnvs();
  });

  it("shows the intro, counts, list and presets, and a link to the map instead of the map", async () => {
    const { el } = await mount();
    expect(el.querySelector(".dv-title")?.textContent).toContain("SPHEREx");
    expect(el.querySelector(".dv-count")?.textContent).toBe("194 objects");
    expect(el.querySelectorAll(".dv-row")).toHaveLength(50);
    expect(el.querySelector(".dv-more-n")?.textContent).toBe("Showing 50 of 194.");
    expect(el.querySelectorAll("[data-preset]")).toHaveLength(6);
    expect(el.querySelector(".dv-map, .skymap")).toBeNull(); // the map is a page of its own, drawn when opened
    expect(el.querySelector<HTMLElement>(".dv-sky")!.hidden).toBe(true);
    expect(el.querySelector(".dv-toolbar .dv-skylink")?.textContent).toBe("See them on the sky");
    expect(el.querySelector("[data-one-on]")?.textContent).toBe("Check them individually");
  });

  it("keeps the intro to its title, one plain sentence and the two buttons: no counts, grades or device note", async () => {
    const { el } = await mount();
    const intro = el.querySelector<HTMLElement>(".dv-intro")!;
    expect(intro.querySelector(".dv-sub")?.textContent).toBe(
      "Spots in NASA's SPHEREx pictures that known stars and asteroids don't explain. Many are just camera glitches, so people need to look: blink each one, say what you see, and the best go to astronomers.",
    );
    expect(intro.querySelector(".dv-counts, [data-grades]")).toBeNull();
    expect(intro.querySelector<HTMLElement>("[data-note]")!.hidden).toBe(true); // votes on this device only: nothing to say
    expect(intro.textContent).not.toMatch(/objects to check from|reviewed so far|Counting votes|Votes stay on this device|grade A|pipeline|artefact/i);
    expect([...intro.querySelectorAll(".dv-actions .btn")].map((b) => b.textContent)).toEqual(["Check them individually", "How it works"]);
  });

  it("says so in the intro's note when vote counts can't be reached", async () => {
    votesDown = true;
    try {
      const { el } = await mount();
      const note = el.querySelector<HTMLElement>("[data-note]")!;
      expect(note.hidden).toBe(false);
      expect(note.textContent).toBe("Vote counts can't be reached right now; everything else works.");
    } finally {
      votesDown = false;
    }
  });

  it("applies a preset, writes it to the URL and shows it as removable chips", async () => {
    const { el } = await mount();
    el.querySelector<HTMLButtonElement>('[data-preset="fast-movers"]')!.click();
    expect(location.hash).toBe("#/candidates/kind=mover&known=60-");
    expect(el.querySelector(".dv-count")?.textContent).toBe("27 of 194 objects");
    expect(el.querySelector('[data-preset="fast-movers"]')?.getAttribute("aria-pressed")).toBe("true");
    const chips = [...el.querySelectorAll(".achip")].map((b) => b.textContent);
    expect(chips).toEqual(["Fast mover", "No known object within 60″"]);
    el.querySelector<HTMLButtonElement>(".achip")!.click();
    expect(location.hash).toBe("#/candidates/known=60-");
    expect(el.querySelector('[data-preset="fast-movers"]')?.getAttribute("aria-pressed")).toBe("false");
  });

  it("reads a shared link and keeps facet counts next to each option", async () => {
    const { el } = await mount("#/candidates/kind=high_pm&score=0.15-1&sort=fewest");
    expect(el.querySelector(".dv-count")?.textContent).toMatch(/^\d+ of 194 objects$/);
    expect(el.querySelector<HTMLSelectElement>(".dv-select")!.value).toBe("fewest");
    const mover = el.querySelector<HTMLInputElement>('.fx input[value="mover"]')!;
    expect(mover.checked).toBe(false);
    expect(mover.closest("label")?.querySelector(".fx-n")?.textContent).toBe("0"); // movers have no score
    const pm = el.querySelector<HTMLInputElement>('.fx input[value="high_pm"]')!;
    expect(pm.checked).toBe(true);
  });

  it("filters with a checkbox and opens a candidate from its row", async () => {
    const { el } = await mount();
    const box = el.querySelector<HTMLInputElement>('.fx input[value="mover"]')!;
    box.checked = true;
    box.dispatchEvent(new Event("change", { bubbles: true }));
    expect(el.querySelector(".dv-count")?.textContent).toBe("27 of 194 objects");
    const link = el.querySelector<HTMLAnchorElement>(".dv-link")!;
    expect(link.getAttribute("href")).toMatch(/^#\/c\/pilot-/);
    expect(link.textContent).toContain("Fast mover");
  });

  it("shows votes on rows: a verdict, and this browser's own vote", async () => {
    const { el } = await mount("#/candidates/sort=most");
    const first = el.querySelector(".dv-row")!;
    expect(first.getAttribute("data-id")).toBe("pilot-pm-0000");
    expect(first.querySelector(".dv-votes")?.textContent).toContain("Verdict: mover");
    expect(first.querySelector(".dv-mine")?.textContent).toBe("You voted");
  });

  it("marks artefact cautions on rows, sorts them last and filters them with one control", async () => {
    const d = structuredClone(data);
    d.candidates[0].flags = ["OUTLIER"];
    d.candidates[0].reasons = ["outlier", "two_exposures"];
    d.candidates[1].reasons = ["repeat_position"];
    loadCandidates.mockResolvedValue(d);
    const { el } = await mount("#/candidates/cautions=only");
    expect(el.querySelector(".dv-count")?.textContent).toBe("2 of 194 objects");
    const tags = [...el.querySelectorAll(".dv-row .tag-caution")].map((t) => t.textContent);
    expect(tags.sort()).toEqual(["Outlier pixels (caution)", "Same spot days apart (caution)"]);
    expect(el.querySelector('[data-preset="check-first"]')).not.toBeNull();
    el.querySelector<HTMLButtonElement>('#fx-cautions-t + .seg button[data-v="no"]')!.click();
    expect(location.hash).toBe("#/candidates/cautions=hide");
    expect(el.querySelector(".dv-count")?.textContent).toBe("192 of 194 objects");
    expect([...el.querySelectorAll(".achip")].map((b) => b.textContent)).toEqual(["No possible glitches"]);
    el.querySelector<HTMLButtonElement>('#fx-cautions-t + .seg button[data-v="any"]')!.click();
    const ids = [...el.querySelectorAll(".dv-row")].map((r) => r.getAttribute("data-id"));
    expect(ids).not.toContain(d.candidates[0].id); // the 'Pipeline score' sort pushes cautions past the first page
  });

  it("says what to do when nothing matches", async () => {
    const { el } = await mount("#/candidates/kind=mover&gaia=yes");
    expect(el.querySelectorAll(".dv-row")).toHaveLength(0);
    expect(el.querySelector(".dv-state")?.textContent).toContain("Nothing matches these filters.");
    expect(el.querySelector(".dv-state")?.textContent).toContain("Removing “Fast mover” would bring back 142 objects.");
    el.querySelector<HTMLButtonElement>("[data-relax]")!.click();
    expect(location.hash).toBe("#/candidates/gaia=yes");
  });

  it("applies a link's filter whose control is hidden (Advanced off), shows its chip and removes it", async () => {
    const { el } = await mount("#/candidates/gaia=no");
    expect(el.querySelector<HTMLElement>(".fx-more")!.hidden).toBe(true);
    const n = data.candidates.filter((c) => !c.gaia_match).length;
    expect(n).toBeLessThan(194);
    expect(el.querySelector(".dv-count")?.textContent).toBe(`${n} of 194 objects`);
    expect(el.querySelector(".dv-filters-btn")?.textContent).toBe("Filters (1)");
    const chip = el.querySelector<HTMLButtonElement>(".achip")!;
    expect(chip.textContent).toBe("Not in Gaia");
    chip.click();
    expect(location.hash).toBe("#/candidates");
    expect(el.querySelector(".dv-count")?.textContent).toBe("194 objects");
    expect(el.querySelector(".achip")).toBeNull();
  });

  it("offers to remove a hidden filter when nothing matches", async () => {
    const { el } = await mount("#/candidates/snr=100000-");
    expect(el.querySelector<HTMLElement>(".fx-more")!.hidden).toBe(true);
    expect(el.querySelectorAll(".dv-row")).toHaveLength(0);
    expect(el.querySelector(".dv-state")?.textContent).toMatch(/Removing “Signal to noise [^”]+” would bring back 194 objects\./);
    el.querySelector<HTMLButtonElement>("[data-relax]")!.click();
    expect(location.hash).toBe("#/candidates");
  });

  it("in production without a published list, explains and points to Explore (never the fixture)", async () => {
    vi.stubEnv("DEV", false);
    loadCandidates.mockResolvedValue(null);
    const { el } = await mount();
    expect(el.querySelector(".dv-state")?.textContent).toContain("The list of objects to check isn't published yet.");
    expect(el.querySelector('.dv-state a[href="#/explore"]')).not.toBeNull();
    expect(el.querySelectorAll(".dv-row")).toHaveLength(0);
    expect(el.querySelector<HTMLElement>(".dv-filters")!.hidden).toBe(true);
  });

  it("in dev without a published list, uses the pilot sample and says so", async () => {
    loadCandidates.mockResolvedValue(null);
    const { el } = await mount();
    expect(el.querySelectorAll(".dv-row").length).toBeGreaterThan(0);
    expect(el.querySelector("[data-note]")?.textContent).toContain("Sample data from the pilot zone");
  });
});

describe("the Candidates page with vetting grades", () => {
  /** The fixture's 27 fast movers graded: one C (hot pixel, edge), one left ungraded, the rest B; 0 A. */
  function graded(): CandidatesResponse {
    const d = structuredClone(data);
    const movers = d.candidates.filter((c) => c.kind === "mover");
    movers.forEach((m, i) => {
      if (i === 1) {
        m.grade = "C";
        m.reasons = ["two_exposures", "hot_pixel", "edge"];
      } else if (i !== 2) m.grade = "B";
    });
    return d;
  }
  const ids = (el: HTMLElement) => [...el.querySelectorAll(".dv-row")].map((r) => r.getAttribute("data-id"));

  beforeEach(() => loadCandidates.mockResolvedValue(graded()));
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("leaves the grade totals out of the intro: grades are on each row and in the filters", async () => {
    const { el } = await mount();
    expect(el.querySelector(".dv-intro")?.textContent).not.toMatch(/grade [ABC]|vetted/i);
    const grade = el.querySelector<HTMLElement>('.fx-main .fx[data-facet="grade"]')!;
    expect(grade.hidden).toBe(false);
    expect(grade.querySelector("legend")?.textContent).toBe("Discovery type");
    expect([...grade.querySelectorAll("input")].map((i) => i.value)).toEqual(["A", "B", "C", "none"]);
  });

  it("greys a grade C row, says what it failed in words, and asks no vote", async () => {
    const { el } = await mount("#/candidates/grade=C");
    expect(el.querySelector(".dv-count")?.textContent).toBe("1 of 194 objects");
    const row = el.querySelector(".dv-row")!;
    expect(row.classList.contains("is-gradec")).toBe(true);
    expect(row.querySelector(".tag-grade")?.textContent).toBe("Grade C");
    expect(row.querySelector(".dv-gradec")?.textContent).toBe(
      "Fails a check: on a hot pixel and at the detector edge. Open it to blink; it isn't put to a vote.",
    );
    expect(row.querySelector(".tag-grade")?.getAttribute("title")).toBe(
      "Grade C: Fails one of our checks (most likely a camera glitch) or sits on a catalogued source. It stays listed so anyone can see why.",
    );
    expect(row.textContent).not.toMatch(/hot_pixel/);
    expect(row.querySelector(".dv-votes")?.textContent).toContain("Not put to a vote");
    expect([...el.querySelectorAll(".achip")].map((b) => b.textContent)).toEqual(["Grade C: fails a check"]);
  });

  it("filters by grade, with counts, and keeps 'No grade' for everything not vetted", async () => {
    const { el } = await mount();
    const b = el.querySelector<HTMLInputElement>('.fx input[value="B"]')!;
    expect(b.closest("label")?.querySelector(".fx-n")?.textContent).toBe("25");
    expect(el.querySelector('.fx[data-facet="grade"] input[value="none"]')?.closest("label")?.querySelector(".fx-n")?.textContent).toBe("168");
    b.checked = true;
    b.dispatchEvent(new Event("change", { bubbles: true }));
    expect(location.hash).toBe("#/candidates/grade=B");
    expect(el.querySelector(".dv-count")?.textContent).toBe("25 of 194 objects");
    expect(el.querySelector(".dv-row .tag-grade")?.textContent).toBe("Grade B: unexplained single-session source");
  });

  it("puts grade C last under 'Pipeline score' and orders A, B, C, then ungraded under 'Vetting grade'", async () => {
    const d = graded();
    const movers = d.candidates.filter((c) => c.kind === "mover");
    const [c, none] = [movers[1].id, movers[2].id];
    let { el } = await mount("#/candidates/kind=mover");
    expect(el.querySelector<HTMLSelectElement>(".dv-select")!.value).toBe("promising");
    expect(ids(el).at(-1)).toBe(c);
    document.body.innerHTML = "";
    ({ el } = await mount("#/candidates/kind=mover&sort=grade"));
    expect(el.querySelector<HTMLSelectElement>(".dv-select")!.value).toBe("grade");
    expect(ids(el).slice(-2)).toEqual([c, none]);
    const snr = (id: string | null) => d.candidates.find((x) => x.id === id)!.snr ?? 0;
    const bs = ids(el).slice(0, 25).map(snr);
    expect(bs).toEqual([...bs].sort((x, y) => y - x));
  });

  it("without grades in the list, shows no grade filter, chip or greyed row (today's behaviour)", async () => {
    loadCandidates.mockResolvedValue(structuredClone(data));
    const { el } = await mount();
    expect(el.querySelector<HTMLElement>('.fx[data-facet="grade"]')!.hidden).toBe(true);
    expect(el.querySelectorAll(".tag-grade, .is-gradec, .dv-gradec")).toHaveLength(0);
    expect(el.querySelector('.fx input[value="B"]')).toBeNull();
  });
});

describe("Check them individually", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    loadCandidates.mockResolvedValue(structuredClone(data));
  });
  afterEach(() => {
    document.body.innerHTML = "";
  });
  const on = (el: HTMLElement) => el.querySelector<HTMLButtonElement>("[data-one-on]")!;
  const back = (el: HTMLElement) => el.querySelector<HTMLButtonElement>("[data-one-off]")!;
  const view = (el: HTMLElement) => el.querySelector<HTMLElement>(".dv")!.dataset.view;
  const lastScope = () => queue.scope.mock.calls.at(-1)?.[0] as string[] | null;
  const movers = data.candidates.filter((c) => c.kind === "mover").map((c) => c.id);

  it("is off by default: the list shows, with the intro's button and no switch in the toolbar", async () => {
    const { el } = await mount();
    expect(on(el).textContent).toBe("Check them individually");
    expect(on(el).title).toContain("individually");
    expect(el.querySelector("[data-one], .dv-one, .dv-toolbar [role='switch']")).toBeNull();
    expect(back(el).hidden).toBe(true);
    expect(view(el)).toBe("list");
    expect(el.querySelector<HTMLElement>(".dv-queue")!.hidden).toBe(true);
    expect(el.querySelectorAll(".dv-row")).toHaveLength(50);
    expect(mountQueue).not.toHaveBeenCalled();
  });

  it("puts the review queue in the list's place, on the filtered set, and writes one=1 to the URL", async () => {
    const { el } = await mount("#/candidates/kind=mover");
    on(el).click();
    expect(location.hash).toBe("#/candidates/kind=mover&one=1");
    expect(view(el)).toBe("one");
    expect(back(el).hidden).toBe(false);
    expect(back(el).textContent).toBe("Back to the list");
    expect(document.activeElement).toBe(back(el));
    expect(el.querySelector<HTMLElement>(".dv-queue")!.hidden).toBe(false);
    expect(mountQueue).toHaveBeenCalledTimes(1);
    expect(mountQueue.mock.calls[0][0]).toBe(el.querySelector(".dv-queue"));
    expect([...lastScope()!].sort()).toEqual([...movers].sort());
    expect(queue.show).toHaveBeenCalled();
    // Its end links: back to this list, and to what the community says is real.
    const opts = mountQueue.mock.calls[0][1];
    expect(opts.embedded?.listHref()).toBe("#/candidates/kind=mover");
    expect(opts.embedded?.realHref()).toBe("#/candidates/review=consensus&verdict=mover,changing&sort=most");
  });

  it("leaves with 'Back to the list': the list again, same filters, focus on its count", async () => {
    const { el } = await mount("#/candidates/kind=mover");
    on(el).click();
    back(el).click();
    expect(location.hash).toBe("#/candidates/kind=mover");
    expect(view(el)).toBe("list");
    expect(back(el).hidden).toBe(true);
    expect(queue.hide).toHaveBeenCalled();
    expect(el.querySelector(".dv-count")?.textContent).toBe("27 of 194 objects");
    expect(document.activeElement).toBe(el.querySelector(".dv-count"));
  });

  it("leaves with the browser's Back: turning it on is a new history entry", async () => {
    const { el, page } = await mount("#/candidates/kind=mover");
    const before = history.length;
    on(el).click();
    expect(history.length).toBe(before + 1);
    const popped = new Promise((r) => addEventListener("popstate", r, { once: true }));
    history.back();
    await popped;
    expect(location.hash).toBe("#/candidates/kind=mover");
    page.show("kind=mover"); // what the shell's router does on the hashchange
    expect(view(el)).toBe("list");
    expect(back(el).hidden).toBe(true);
    expect(el.querySelectorAll(".dv-row")).toHaveLength(27);
  });

  it("opens from a shared link and follows the filters: a preset narrows the queue's set", async () => {
    const { el } = await mount("#/candidates/one=1");
    expect(view(el)).toBe("one");
    expect(back(el).hidden).toBe(false);
    expect(lastScope()).toHaveLength(194);
    el.querySelector<HTMLButtonElement>('[data-preset="fast-movers"]')!.click();
    expect(location.hash).toBe("#/candidates/kind=mover&known=60-&one=1");
    expect(lastScope()).toHaveLength(27);
    expect(mountQueue).toHaveBeenCalledTimes(1); // one queue, new scope
  });

  it("follows the router (the old #/review link maps to #/candidates/one=1) and hides with the page", async () => {
    const { el, page } = await mount();
    page.show("kind=mover&one=1");
    expect(view(el)).toBe("one");
    expect(lastScope()).toHaveLength(27);
    expect(queue.show).toHaveBeenCalled();
    page.hide?.();
    expect(queue.hide).toHaveBeenCalled();
    queue.hide.mockClear();
    page.show("kind=mover");
    expect(view(el)).toBe("list");
    expect(back(el).hidden).toBe(true);
    expect(queue.hide).toHaveBeenCalled();
    expect(el.querySelectorAll(".dv-row")).toHaveLength(27);
  });
});

describe("the map view", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    loadCandidates.mockResolvedValue(structuredClone(data));
  });
  afterEach(() => {
    document.body.innerHTML = "";
  });
  const view = (el: HTMLElement) => el.querySelector<HTMLElement>(".dv")!.dataset.view;
  const link = (el: HTMLElement) => el.querySelector<HTMLAnchorElement>(".dv-skylink")!;
  const sky = (el: HTMLElement) => el.querySelector<HTMLElement>(".dv-sky")!;

  it("is a link in the list's toolbar that carries the current filter plus map=1", async () => {
    const { el } = await mount("#/candidates/kind=mover");
    expect(view(el)).toBe("list");
    expect(link(el).closest(".dv-toolbar")).not.toBeNull();
    expect(link(el).getAttribute("href")).toBe("#/candidates/kind=mover&map=1");
    el.querySelector<HTMLButtonElement>('[data-preset="fast-movers"]')!.click();
    expect(location.hash).toBe("#/candidates/kind=mover&known=60-");
    expect(link(el).getAttribute("href")).toBe("#/candidates/kind=mover&known=60-&map=1");
    el.querySelector<HTMLSelectElement>(".dv-select")!.value = "fewest";
    el.querySelector<HTMLSelectElement>(".dv-select")!.dispatchEvent(new Event("change"));
    expect(link(el).getAttribute("href")).toBe("#/candidates/kind=mover&known=60-&sort=fewest&map=1");
    expect(el.querySelector(".skymap")).toBeNull();
  });

  it("opens from its URL as a page of its own: heading, what's shown and filtered, the map, the way back", async () => {
    const { el, navigate } = await mount("#/candidates/kind=mover&map=1");
    expect(view(el)).toBe("map");
    expect(sky(el).hidden).toBe(false);
    expect(sky(el).querySelector("h1")?.textContent).toBe("Objects to check on the sky");
    expect(sky(el).querySelector(".dv-sky-count")?.textContent).toBe("27 of 194 objects, filtered:");
    expect([...sky(el).querySelectorAll(".dv-sky-chips li")].map((li) => li.textContent)).toEqual(["Fast mover"]);
    const back = sky(el).querySelector<HTMLAnchorElement>(".dv-sky-back")!;
    expect(back.textContent).toBe("Back to the list");
    expect(back.getAttribute("href")).toBe("#/candidates/kind=mover");
    expect(sky(el).querySelector("[data-sky-change]")?.getAttribute("href")).toBe("#/candidates/kind=mover");
    // The map draws the same filtered set; a point opens its object.
    expect(sky(el).querySelector(".skymap-legend")?.textContent).toContain("Fast movers27 of 27");
    const plot = sky(el).querySelector<HTMLElement>(".skymap-plot")!;
    plot.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight" }));
    plot.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter" }));
    expect(navigate).toHaveBeenCalledWith(expect.stringMatching(/^#\/c\/pilot-/));
    // The map key isn't a filter: the URL keeps it, the preset still matches, the list's count is the same.
    expect(location.hash).toBe("#/candidates/kind=mover&map=1");
    expect(el.querySelector(".dv-count")?.textContent).toBe("27 of 194 objects");
  });

  it("keeps a preset matched under map=1, and says when nothing is filtered", async () => {
    let { el } = await mount("#/candidates/kind=mover&known=60-&map=1");
    expect(el.querySelector('[data-preset="fast-movers"]')?.getAttribute("aria-pressed")).toBe("true");
    expect([...sky(el).querySelectorAll(".dv-sky-chips li")].map((li) => li.textContent)).toEqual(["Fast mover", "No known object within 60″"]);
    document.body.innerHTML = "";
    ({ el } = await mount("#/candidates/map=1"));
    expect(sky(el).querySelector(".dv-sky-count")?.textContent).toBe("All 194 objects, no filters.");
    expect(sky(el).querySelector<HTMLElement>(".dv-sky-chips")!.hidden).toBe(true);
    const change = sky(el).querySelector<HTMLAnchorElement>("[data-sky-change]")!;
    expect(change.textContent).toBe("Filter them");
    expect(change.getAttribute("href")).toBe("#/candidates");
  });

  it("follows the router: list, map (drawn on first open), back to the list with the same filter", async () => {
    const { el, page } = await mount("#/candidates/kind=mover");
    expect(el.querySelector(".skymap")).toBeNull();
    page.show("kind=mover&map=1");
    expect(view(el)).toBe("map");
    expect(el.querySelector(".skymap")).not.toBeNull();
    expect(document.activeElement).toBe(sky(el).querySelector("h1"));
    page.show("kind=mover");
    expect(view(el)).toBe("list");
    expect(sky(el).hidden).toBe(true);
    expect(el.querySelectorAll(".dv-row")).toHaveLength(27);
    page.show("kind=mover&map=1");
    expect(el.querySelectorAll(".skymap")).toHaveLength(1); // one map, kept
  });

  it("wins over one=1: no review queue behind the map", async () => {
    const { el } = await mount("#/candidates/kind=mover&one=1&map=1");
    expect(view(el)).toBe("map");
    expect(el.querySelector<HTMLElement>(".dv-queue")!.hidden).toBe(true);
    expect(mountQueue).not.toHaveBeenCalled();
  });
});

describe("Community says real, and the list for astronomers", () => {
  /** n votes, one voter each, on one candidate. */
  const votesOn = (id: string, answers: Answer[]) => answers.map((answer, i) => ({ candidate_id: id, answer, ts: 1_763_000_000_000 + i, voter: `voter_${i}_${id}` }));
  const snapshot = (votes: Snapshot["votes"]): Snapshot => ({
    mode: "firestore",
    note: null,
    votes,
    info: infoFromCatalogue(data),
    listed: data.candidates.length,
    dataRelease: data.data_release ?? null,
    mine: {},
    loadedAt: 0,
    lookup: async () => new Map(),
  });
  beforeEach(() => {
    vi.clearAllMocks();
    voteCounts = AGREED;
    loadCandidates.mockResolvedValue(structuredClone(data));
  });
  afterEach(() => {
    document.body.innerHTML = "";
    voteCounts = AGREED;
  });

  it("is a preset that finds what the community agreed moves or changes, and offers the list for astronomers", async () => {
    const { el } = await mount();
    const b = el.querySelector<HTMLButtonElement>('[data-preset="community-real"]')!;
    expect(b.textContent).toBe("Community says real");
    expect(el.querySelector<HTMLElement>(".dv-real")!.hidden).toBe(true);
    b.click();
    expect(location.hash).toBe("#/candidates/review=consensus&verdict=mover,changing&sort=most");
    expect(el.querySelector(".dv-count")?.textContent).toBe("1 of 194 objects");
    expect(el.querySelector(".dv-row")?.getAttribute("data-id")).toBe("pilot-pm-0000");
    const real = el.querySelector<HTMLElement>(".dv-real")!;
    expect(real.hidden).toBe(false);
    expect(real.textContent).toContain("They go to astronomers as leads to check, not discoveries.");
    expect(real.querySelector("[data-astro]")).not.toBeNull();
  });

  it("says what to do before the community has agreed on anything, and sends you to vote", async () => {
    voteCounts = new Map();
    const { el } = await mount("#/candidates/review=consensus&verdict=mover,changing&sort=most");
    expect(el.querySelector(".dv-state")?.textContent).toContain("Nothing the community agreed on yet.");
    el.querySelector<HTMLButtonElement>("[data-vote-now]")!.click();
    expect(location.hash).toBe("#/candidates/review=none,some&mine=hide&sort=fewest&one=1");
    expect(el.querySelector('[data-preset="needs-vote"]')?.getAttribute("aria-pressed")).toBe("true");
  });

  it("downloads the Community page's list for astronomers (everyone's votes, the same rule) as CSV or JSON", async () => {
    loadSnapshot.mockResolvedValue(
      snapshot([...votesOn("pilot-pm-0000", ["mover", "mover", "mover", "mover", "junk"]), ...votesOn("pilot-pm-0001", ["junk", "junk", "junk", "junk", "junk"])]),
    );
    const { el } = await mount();
    const menu = el.querySelector(".dv-dl")!;
    expect(menu.querySelector("summary")?.textContent).toBe("Download");
    expect(menu.textContent).toContain("List for astronomers");
    menu.querySelector<HTMLButtonElement>('[data-astro="csv"]')!.click();
    for (let i = 0; i < 4; i++) await tick();
    expect(download).toHaveBeenCalledTimes(1);
    const [name, text, type] = download.mock.calls[0] as [string, string, string];
    expect(name).toMatch(/^skyshift-list-for-astronomers-\d{4}-\d\d-\d\d\.csv$/);
    expect(type).toBe("text/csv;charset=utf-8");
    const lines = text.trimEnd().split("\r\n");
    expect(lines).toHaveLength(2);
    expect(lines[1]).toMatch(/^pilot-pm-0000,high_pm,mover,5,4,80,/);
    expect(menu.querySelector("[data-dl-status]")?.textContent).toMatch(/^Saved skyshift-list-for-astronomers-.*\.csv: 1 object\.$/);
    menu.querySelector<HTMLButtonElement>('[data-astro="json"]')!.click();
    for (let i = 0; i < 4; i++) await tick();
    const json = JSON.parse(download.mock.calls[1][1] as string);
    expect(json).toMatchObject({ votes_from: "firestore", count: 1, rule: { min_votes: 5, min_agree_pct: 70 } });
  });

  it("downloads nothing when nothing has reached consensus, and says why", async () => {
    loadSnapshot.mockResolvedValue(snapshot(votesOn("pilot-pm-0000", ["mover", "mover", "mover"])));
    const { el } = await mount();
    el.querySelector<HTMLButtonElement>('.dv-dl [data-astro="json"]')!.click();
    for (let i = 0; i < 4; i++) await tick();
    expect(download).not.toHaveBeenCalled();
    expect(el.querySelector(".dv-dl [data-dl-status]")?.textContent).toContain("Nothing has reached consensus as a mover or changing source yet");
  });
});

describe("plain words first", () => {
  beforeEach(() => loadCandidates.mockResolvedValue(structuredClone(data)));
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("keeps the pipeline's numbers (score, σ, S/N) behind Advanced: pro-only", async () => {
    const { el } = await mount();
    const rows = el.querySelectorAll(".dv-row");
    expect(rows).toHaveLength(50);
    expect(el.querySelectorAll(".dv-row .dv-score.pro-only")).toHaveLength(50);
    expect(el.querySelector(".dv-cols")?.textContent).toBe("ObjectMeasuredVotes");
    const plain = el.querySelector(".dv-list")!.cloneNode(true) as HTMLElement;
    plain.querySelectorAll(".pro-only").forEach((n) => n.remove());
    expect(plain.textContent).not.toMatch(/σ|S\/N|Score|No score/);
    // The measurements with units stay for everyone.
    expect(plain.textContent).toMatch(/″\/yr/);
    expect(plain.textContent).toMatch(/µJy/);
  });
});

describe("the filters panel", () => {
  beforeEach(() => loadCandidates.mockResolvedValue(structuredClone(data)));
  afterEach(() => {
    document.body.innerHTML = "";
    setDetails(false);
    _resetDetailsForTests();
    localStorage.removeItem(DETAILS_KEY);
  });
  const names = (root: Element) =>
    [...root.querySelectorAll(":scope > .fx-group-body > .fx:not([hidden]) > legend, :scope > .fx-group-body > .fx-set > .fx > legend, :scope > .fx-group-body > .fx-tri .fx-name")].map((n) => n.textContent);

  it("shows only the essentials with Advanced off: kind, votes, possible camera glitches", async () => {
    const { el } = await mount();
    const main = el.querySelector(".fx-main")!;
    expect(names(main)).toEqual(["Kind", "Votes", "Possible camera glitches"]);
    expect([...main.querySelectorAll('.fx[data-facet="kind"] .fx-opt')].map((l) => l.querySelector(".fx-name")?.textContent)).toEqual(["Fast mover", "Moving star", "Odd spectrum", "One or two?"]);
    expect(main.querySelectorAll('.fx[data-facet="kind"] .kind-glyph').length).toBeGreaterThan(0);
    expect([...main.querySelectorAll('.fx[data-facet="review"] .fx-name')].map((n) => n.textContent)).toEqual(["Not reviewed yet", "In review", "Community verdict"]);
    expect(main.querySelector(".fx-set .fx-toggle")?.textContent).toContain("Hide ones I voted on");
    expect([...main.querySelectorAll("#fx-cautions-t + .seg button")].map((b) => b.firstChild?.textContent?.trim())).toEqual(["Any", "With", "Without"]);
    expect(main.querySelector("details")).toBeNull(); // nothing to open
    const more = el.querySelector<HTMLElement>(".fx-more")!;
    expect(more.hidden).toBe(true);
    expect(more.classList.contains("pro-only")).toBe(true);
    expect(el.querySelectorAll(".fx-body > .fx-group")).toHaveLength(2);
  });

  it("adds the vetting grade to the essentials when the list carries grades", async () => {
    const d = structuredClone(data);
    d.candidates.filter((c) => c.kind === "mover").forEach((m) => (m.grade = "B"));
    loadCandidates.mockResolvedValue(d);
    const { el } = await mount();
    expect(names(el.querySelector(".fx-main")!)).toEqual(["Kind", "Discovery type", "Votes", "Possible camera glitches"]);
  });

  it("shows 'More filters', closed, only with Advanced on, and follows the switch", async () => {
    setDetails(true);
    const { el } = await mount();
    const more = el.querySelector<HTMLDetailsElement>(".fx-more")!;
    expect(more.hidden).toBe(false);
    expect(more.open).toBe(false);
    expect(more.querySelector("summary .fx-group-title")?.textContent).toBe("More filters");
    expect([...more.querySelectorAll(".fx-sub")].map((h) => h.textContent)).toEqual(["Evidence", "Measurements", "Sky and data"]);
    expect([...more.querySelectorAll(".fx-toggle .fx-name")].map((n) => n.textContent)).toEqual(["Hide doubtful ones", "Hide the Milky Way plane (|b| < 10°)"]);
    expect(more.querySelector("#fx-gaia-t")?.textContent).toBe("In Gaia");
    expect(more.querySelector("#fx-catwise-t")?.textContent).toBe("In CatWISE");
    expect(more.querySelector('.fx[data-facet="det"]')).not.toBeNull();
    expect(more.querySelector('.fx[data-facet="zone"]')).not.toBeNull();
    expect(more.querySelector("#fx-r-score")?.textContent).toBe("Score");
    // Dropped: the verdict (the "Community says real" preset and "Community verdict" cover it) and the |b| slider (the plane switch).
    expect(el.querySelector('.fx[data-facet="verdict"], #fx-r-b')).toBeNull();
    setDetails(false);
    expect(more.hidden).toBe(true);
    setDetails(true);
    expect(more.hidden).toBe(false);
  });

  it("opens 'More filters' when a link sets one of its filters, and counts it on the summary", async () => {
    setDetails(true);
    const { el } = await mount("#/candidates/gaia=no&det=2");
    const more = el.querySelector<HTMLDetailsElement>(".fx-more")!;
    expect(more.open).toBe(true);
    expect(more.querySelector(".fx-badge")?.textContent).toBe("2 on");
    expect(more.querySelector<HTMLButtonElement>('#fx-gaia-t + .seg button[data-v="no"]')?.getAttribute("aria-pressed")).toBe("true");
  });

  it("keeps a preset's hidden filters working with Advanced off: they show as chips", async () => {
    const { el } = await mount();
    el.querySelector<HTMLButtonElement>('[data-preset="brown-dwarfs"]')!.click();
    expect(el.querySelector('[data-preset="brown-dwarfs"]')?.getAttribute("aria-pressed")).toBe("true");
    expect([...el.querySelectorAll(".achip")].map((b) => b.textContent)).toEqual(["Moving star", "Very red", "Methane", "Brown-dwarf-like", "Not in Gaia"]);
    expect(el.querySelector<HTMLInputElement>('.fx-main input[value="high_pm"]')!.checked).toBe(true);
  });
});
