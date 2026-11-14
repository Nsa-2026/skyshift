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
