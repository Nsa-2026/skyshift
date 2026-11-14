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
    expect(el.querySelector(".dv-counts")?.textContent).toContain("194 candidates from the pilot zone.");
    expect(el.querySelector(".dv-counts")?.textContent).toContain("1 reviewed so far, with 7 votes cast.");
    expect(el.querySelector(".dv-count")?.textContent).toBe("194 candidates");
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
    expect(el.querySelector(".dv-count")?.textContent).toBe("27 of 194 candidates");
    expect(el.querySelector('[data-preset="fast-movers"]')?.getAttribute("aria-pressed")).toBe("true");
    const chips = [...el.querySelectorAll(".achip")].map((b) => b.textContent);
    expect(chips).toEqual(["Fast mover", "No known object within 60″"]);
    el.querySelector<HTMLButtonElement>(".achip")!.click();
    expect(location.hash).toBe("#/candidates/known=60-");
    expect(el.querySelector('[data-preset="fast-movers"]')?.getAttribute("aria-pressed")).toBe("false");
  });

  it("reads a shared link and keeps facet counts next to each option", async () => {
    const { el } = await mount("#/candidates/kind=high_pm&score=0.15-1&sort=fewest");
    expect(el.querySelector(".dv-count")?.textContent).toMatch(/^\d+ of 194 candidates$/);
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
    expect(el.querySelector(".dv-count")?.textContent).toBe("27 of 194 candidates");
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
    expect(el.querySelector(".dv-count")?.textContent).toBe("2 of 194 candidates");
    const tags = [...el.querySelectorAll(".dv-row .tag-caution")].map((t) => t.textContent);
    expect(tags.sort()).toEqual(["Outlier pixels (caution)", "Same spot days apart (caution)"]);
    expect(el.querySelector('[data-preset="check-first"]')).not.toBeNull();
    el.querySelector<HTMLButtonElement>('#fx-cautions-t + .seg button[data-v="no"]')!.click();
    expect(location.hash).toBe("#/candidates/cautions=hide");
    expect(el.querySelector(".dv-count")?.textContent).toBe("192 of 194 candidates");
    el.querySelector<HTMLButtonElement>('#fx-cautions-t + .seg button[data-v="any"]')!.click();
    const ids = [...el.querySelectorAll(".dv-row")].map((r) => r.getAttribute("data-id"));
    expect(ids).not.toContain(d.candidates[0].id); // 'Most promising' pushes cautions past the first page
  });

  it("says what to do when nothing matches", async () => {
    const { el } = await mount("#/candidates/kind=mover&gaia=yes");
    expect(el.querySelectorAll(".dv-row")).toHaveLength(0);
    expect(el.querySelector(".dv-state")?.textContent).toContain("No candidates match these filters.");
    expect(el.querySelector(".dv-state")?.textContent).toContain("Removing “Fast mover” would bring back 142 candidates.");
    el.querySelector<HTMLButtonElement>("[data-relax]")!.click();
    expect(location.hash).toBe("#/candidates/gaia=yes");
  });

  it("in production without a published list, explains and points to Explore (never the fixture)", async () => {
    vi.stubEnv("DEV", false);
    loadCandidates.mockResolvedValue(null);
    const { el } = await mount();
    expect(el.querySelector(".dv-state")?.textContent).toContain("The candidate list isn't published yet.");
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
