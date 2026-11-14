import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import fixture from "../../../fixtures/api/candidates.json";
import type { CandidatesResponse } from "../api/types";

const loadCandidates = vi.fn<() => Promise<CandidatesResponse | null>>();
vi.mock("../api/client", async (orig) => ({ ...(await orig<typeof import("../api/client")>()), loadCandidates: () => loadCandidates() }));
vi.mock("./votes", () => ({
  openVoteReader: async () => ({
    mode: "local",
    notice: null,
    counts: async () => new Map([["pilot-pm-0000", { mover: 6, changing: 0, junk: 1, unsure: 0 }]]),
    votedOn: (id: string) => id === "pilot-pm-0000",
  }),
  onVoted: () => () => {},
}));

const { mountDiscover } = await import("./index");
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
    expect(el.querySelector('a[href="#/review"]')?.textContent).toBe("Start reviewing");
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

  // ---- vetting grades (hand-made from the pilot sample; public/data is never written) ----------

  /** The pilot sample with its movers graded: the first B, the second C (hot pixel, 2MASS), the
   *  rest B, and `ungraded` of them left without a grade (a zone run before the vetting). */
  function gradedData(ungraded: string[] = []) {
    const d = structuredClone(data);
    const movers = d.candidates.filter((c) => c.kind === "mover");
    for (const m of movers) m.grade = "B";
    movers[1].grade = "C";
    movers[1].reasons = [...movers[1].reasons, "hot_pixel", "2mass"];
    for (const m of movers) if (ungraded.includes(m.id)) m.grade = null;
    for (const c of d.candidates) if (c.kind !== "mover") c.grade = null;
    return { d, movers };
  }
  const rowOf = (el: HTMLElement, id: string) => el.querySelector<HTMLElement>(`.dv-row[data-id="${id}"]`)!;
  /** No grade anywhere: rows, filters, active chips, the doubts toggle's hint. */
  function expectNoGradeUi(el: HTMLElement) {
    expect(el.querySelectorAll(".dv-grade, [data-grade], .dv-row.is-greyed")).toHaveLength(0);
    expect(el.querySelector('[data-facet="grade"]')).toBeNull();
    expect(el.querySelector(".dv-filters")?.textContent).not.toMatch(/grade|vetted/i);
    expect(el.querySelector(".dv-results")?.textContent).not.toMatch(/grade [ABC]|not vetted/i);
    const titles = [...el.querySelectorAll("[title]")].map((t) => t.getAttribute("title")).join("|");
    expect(titles).not.toMatch(/grade [ABC]|\(grade C\)|not vetted/i);
  }

  it("shows a graded mover's grade in words, greys grade C with its failed checks, never hides it", async () => {
    const { d, movers } = gradedData();
    loadCandidates.mockResolvedValue(d);
    const { el } = await mount("#/candidates/kind=mover");
    expect(el.querySelector(".dv-count")?.textContent).toBe("27 of 194 objects");

    const b = rowOf(el, movers[0].id);
    expect(b.dataset.grade).toBe("B");
    expect(b.classList.contains("is-greyed")).toBe(false);
    expect(b.querySelector(".dv-grade")?.textContent).toBe("Grade B. Passed every check, motion not shown yet");
    expect(b.querySelector(".grade-mark")?.textContent).toBe("Grade B"); // the letter in words, not a colour

    const sorted = [...el.querySelectorAll<HTMLElement>(".dv-row")].map((r) => r.dataset.id);
    expect(sorted).toHaveLength(27);
    expect(sorted[sorted.length - 1]).toBe(movers[1].id); // a doubt: last in 'Pipeline score', still listed
    const c = rowOf(el, movers[1].id);
    expect(c.dataset.grade).toBe("C");
    expect(c.classList.contains("is-greyed")).toBe(true);
    expect(c.querySelector(".dv-grade")?.textContent).toBe("Grade C. Failed: hot pixel, 2MASS source here");
    expect(c.querySelector(".dv-grade")?.getAttribute("title")).toContain("hot_pixel");
    // Named once, on the grade line, not again as chips; its other reasons stay chips.
    const tags = [...c.querySelectorAll(".dv-tags li")].map((t) => t.textContent);
    expect(tags.join("|")).not.toMatch(/Hot pixel|2MASS/);
    expect(tags).toContain("Seen in two exposures");
  });

  it("filters by grade, and 'Most promising' leaves grade C out", async () => {
    const { d, movers } = gradedData();
    loadCandidates.mockResolvedValue(d);
    const { el } = await mount();
    const optC = el.querySelector<HTMLInputElement>('[data-facet="grade"] input[value="C"]')!;
    expect(optC.closest("fieldset")?.querySelector("legend")?.textContent).toBe("Vetting grade (fast movers)");
    expect(optC.closest("label")?.textContent).toBe("Grade C: failed a check1");
    expect(el.querySelector('[data-facet="grade"] input[value="none"]')).toBeNull(); // every mover is graded
    optC.checked = true;
    optC.dispatchEvent(new Event("change", { bubbles: true }));
    expect(location.hash).toBe("#/candidates/grade=C");
    expect(el.querySelector(".dv-count")?.textContent).toBe("1 of 194 objects");
    expect([...el.querySelectorAll(".achip")].map((x) => x.textContent)).toEqual(["Grade C"]);

    el.querySelector<HTMLButtonElement>('[data-preset="promising"]')!.click();
    expect(el.querySelector('[data-preset="promising"]')?.getAttribute("aria-pressed")).toBe("true");
    const ids = [...el.querySelectorAll<HTMLElement>(".dv-row")].map((r) => r.dataset.id);
    expect(ids).toContain(movers[0].id);
    expect(ids).not.toContain(movers[1].id);
    expect(el.querySelector("[data-show-n]")?.textContent).toBe("Show 34 objects"); // 35 before the grades, less the C
  });

  it("in a list with grades, says quietly that a mover without one isn't vetted", async () => {
    const { d, movers } = gradedData([data.candidates.find((c) => c.kind === "mover")!.id]);
    loadCandidates.mockResolvedValue(d);
    const { el } = await mount("#/candidates/kind=mover");
    const u = rowOf(el, movers[0].id);
    expect(u.dataset.grade).toBeUndefined();
    expect(u.querySelector(".dv-grade")?.textContent).toBe("Not vetted yet");
    expect(u.querySelector(".dv-grade")?.classList.contains("is-none")).toBe(true);
    expect(u.querySelector(".grade-mark")).toBeNull();
    expect(el.querySelector('[data-facet="grade"] input[value="none"]')?.closest("label")?.textContent).toBe("Not vetted yet1");
    // Moving stars carry no grade and get no grade line.
    const box = el.querySelector<HTMLInputElement>('.fx input[value="high_pm"]')!;
    box.checked = true;
    box.dispatchEvent(new Event("change", { bubbles: true }));
    const star = el.querySelector<HTMLElement>('.dv-row[data-id^="pilot-pm-"]')!;
    expect(star.querySelector(".dv-grade")).toBeNull();
  });

  it("shows no grade at all for a list exported before the grades", async () => {
    const { el } = await mount();
    expect(el.querySelectorAll(".dv-row")).toHaveLength(50);
    expectNoGradeUi(el);
    const { el: movers } = await mount("#/candidates/kind=mover");
    expectNoGradeUi(movers);
  });

  it("renders the published list: no grade anywhere while it has none (until the re-export), grades once it has", async () => {
    const published = JSON.parse(readFileSync(join(__dirname, "../../public/data/candidates.json"), "utf8")) as CandidatesResponse;
    const hasGrades = published.candidates.some((c) => c.grade != null);
    loadCandidates.mockResolvedValue(published);
    const { el } = await mount();
    expect(el.querySelector(".dv-count")?.textContent).toBe(`${published.candidates.length.toLocaleString("en-GB")} objects`);
    const { el: movers } = await mount("#/candidates/kind=mover");
    expect(movers.querySelectorAll(".dv-row").length).toBeGreaterThan(0);
    if (!hasGrades) {
      // Today's file (0 of 1,333 rows graded): the page looks exactly as before the grades.
      expectNoGradeUi(el);
      expectNoGradeUi(movers);
    } else {
      // After the re-export every mover row says its grade, or that it isn't vetted.
      for (const r of movers.querySelectorAll(".dv-row")) expect(r.querySelector(".dv-grade"), r.getAttribute("data-id")!).not.toBeNull();
    }
  });

  it("in dev without a published list, uses the pilot sample and says so", async () => {
    loadCandidates.mockResolvedValue(null);
    const { el } = await mount();
    expect(el.querySelectorAll(".dv-row").length).toBeGreaterThan(0);
    expect(el.querySelector("[data-note]")?.textContent).toContain("Sample data from the pilot zone");
  });
});
