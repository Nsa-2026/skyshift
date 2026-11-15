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
vi.mock("./votes", () => ({
  openVoteReader: async () => ({
    mode: "local",
    notice: null,
    counts: async () => voteCounts,
    votedOn: (id: string) => id === "pilot-pm-0000",
  }),
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

  it("shows the intro, counts, list, presets and the map", async () => {
    const { el } = await mount();
    expect(el.querySelector(".dv-title")?.textContent).toContain("SPHEREx");
    expect(el.querySelector(".dv-counts")?.textContent).toContain("194 objects to check from the pilot zone.");
    expect(el.querySelector(".dv-counts")?.textContent).toContain("1 reviewed so far, with 7 votes cast.");
    expect(el.querySelector(".dv-count")?.textContent).toBe("194 objects");
    expect(el.querySelectorAll(".dv-row")).toHaveLength(50);
    expect(el.querySelector(".dv-more-n")?.textContent).toBe("Showing 50 of 194.");
    expect(el.querySelectorAll("[data-preset]")).toHaveLength(6);
    expect(el.querySelector(".skymap-legend")?.textContent).toContain("Moving stars167 of 167");
    expect(el.querySelector("[data-one-on]")?.textContent).toBe("Check them one at a time");
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

  it("says 0 grade A as the expected result, with the B and C totals", async () => {
    const { el } = await mount();
    const g = el.querySelector<HTMLElement>("[data-grades]")!;
    expect(g.hidden).toBe(false);
    expect(g.querySelector(".dv-grades-a")?.textContent).toBe(
      "0 candidate movers (grade A). Expected: proving motion needs a track over several sessions.",
    );
    expect(g.textContent).toContain("25 unexplained single-session sources (grade B) and 1 that fails a check (grade C), shown greyed and not put to a vote.");
    expect(g.textContent).toContain("1 fast mover isn't vetted yet.");
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

  it("without grades in the list, shows no grade line, chip or greyed row (today's behaviour)", async () => {
    loadCandidates.mockResolvedValue(structuredClone(data));
    const { el } = await mount();
    expect(el.querySelector<HTMLElement>("[data-grades]")!.hidden).toBe(true);
    expect(el.querySelectorAll(".tag-grade, .is-gradec, .dv-gradec")).toHaveLength(0);
    expect(el.querySelector('.fx input[value="B"]')).toBeNull();
  });
});

describe("One at a time", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    loadCandidates.mockResolvedValue(structuredClone(data));
  });
  afterEach(() => {
    document.body.innerHTML = "";
  });
  const sw = (el: HTMLElement) => el.querySelector<HTMLButtonElement>("[data-one]")!;
  const view = (el: HTMLElement) => el.querySelector<HTMLElement>(".dv")!.dataset.view;
  const lastScope = () => queue.scope.mock.calls.at(-1)?.[0] as string[] | null;
  const movers = data.candidates.filter((c) => c.kind === "mover").map((c) => c.id);

  it("is off by default: the list shows, with a switch in the toolbar and a button in the intro", async () => {
    const { el } = await mount();
    expect(sw(el).getAttribute("role")).toBe("switch");
    expect(sw(el).getAttribute("aria-checked")).toBe("false");
    expect(sw(el).textContent).toBe("One at a time");
    expect(view(el)).toBe("list");
    expect(el.querySelector<HTMLElement>(".dv-queue")!.hidden).toBe(true);
    expect(el.querySelectorAll(".dv-row")).toHaveLength(50);
    expect(mountQueue).not.toHaveBeenCalled();
  });

  it("puts the review queue in the list's place, on the filtered set, and writes one=1 to the URL", async () => {
    const { el } = await mount("#/candidates/kind=mover");
    sw(el).click();
    expect(sw(el).getAttribute("aria-checked")).toBe("true");
    expect(location.hash).toBe("#/candidates/kind=mover&one=1");
    expect(view(el)).toBe("one");
    expect(el.querySelector<HTMLElement>(".dv-queue")!.hidden).toBe(false);
    expect(mountQueue).toHaveBeenCalledTimes(1);
    expect(mountQueue.mock.calls[0][0]).toBe(el.querySelector(".dv-queue"));
    expect([...lastScope()!].sort()).toEqual([...movers].sort());
    expect(queue.show).toHaveBeenCalled();
    // Its end links: back to this list, and to what the community says is real.
    const opts = mountQueue.mock.calls[0][1];
    expect(opts.embedded?.listHref()).toBe("#/candidates/kind=mover");
    expect(opts.embedded?.realHref()).toBe("#/candidates/review=consensus&verdict=mover,changing&sort=most");
    // Off: the list again, same filters.
    sw(el).click();
    expect(sw(el).getAttribute("aria-checked")).toBe("false");
    expect(location.hash).toBe("#/candidates/kind=mover");
    expect(view(el)).toBe("list");
    expect(queue.hide).toHaveBeenCalled();
    expect(el.querySelector(".dv-count")?.textContent).toBe("27 of 194 objects");
  });

  it("opens from a shared link and follows the filters: a preset narrows the queue's set", async () => {
    const { el } = await mount("#/candidates/one=1");
    expect(sw(el).getAttribute("aria-checked")).toBe("true");
    expect(lastScope()).toHaveLength(194);
    el.querySelector<HTMLButtonElement>('[data-preset="fast-movers"]')!.click();
    expect(location.hash).toBe("#/candidates/kind=mover&known=60-&one=1");
    expect(lastScope()).toHaveLength(27);
    expect(mountQueue).toHaveBeenCalledTimes(1); // one queue, new scope
  });

  it("follows the router (the old #/review link maps to #/candidates/one=1) and hides with the page", async () => {
    const { el, page } = await mount();
    page.show("kind=mover&one=1");
    expect(sw(el).getAttribute("aria-checked")).toBe("true");
    expect(lastScope()).toHaveLength(27);
    expect(queue.show).toHaveBeenCalled();
    page.hide?.();
    expect(queue.hide).toHaveBeenCalled();
    queue.hide.mockClear();
    page.show("kind=mover");
    expect(sw(el).getAttribute("aria-checked")).toBe("false");
    expect(queue.hide).toHaveBeenCalled();
    expect(el.querySelectorAll(".dv-row")).toHaveLength(27);
  });

  it("turns on from the intro's button", async () => {
    const { el } = await mount();
    el.querySelector<HTMLButtonElement>("[data-one-on]")!.click();
    expect(location.hash).toBe("#/candidates/one=1");
    expect(sw(el).getAttribute("aria-checked")).toBe("true");
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
