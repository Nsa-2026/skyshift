import { afterEach, describe, expect, it, vi } from "vitest";
import publishedJson from "../../public/data/distant.json";
import type { DistantResponse } from "../api/types";
import { chanceText, dropText, renderDistant, verdictText, zoneText } from "./distant";
import { mountScore, renderScore } from "./index";

// A small hand-made file in the contract's shape (contracts/api.md v3.6). Used here only, never copied to public/data.
const DIST: DistantResponse = {
  generated: "2026-11-14T18:00:00Z",
  data_release: "SPHEREx DR1",
  zone: "hunt",
  zone_box: { ra: [120.5, 130.5], dec: [14.4, 24.4] },
  run_created: "2026-11-14T17:35:15Z",
  searched: { distance_au: [70, 1000], sep_arcsec: [300, 7200], rate_arcsec_h: [0, 1], pair_min_p: 0.01 },
  counts: {
    single_visit_sources: 5275,
    uncatalogued: 779,
    covered: 779,
    orphans: 447,
    dropped: { bright_fragment: 314, zone_edge: 26, saturated: 5 },
    drift_ok: 442,
    pairs_tested: 892,
    pairs: 1,
    known_orphans: 0,
    open_orphans: 103,
  },
  pairs: [
    {
      source_id_a: 64904007030800,
      source_id_b: 64940007013300,
      visit_a: 1,
      visit_b: 2,
      ra_a: 123.334145,
      dec_a: 18.174917,
      mjd_a: 60975.2943,
      ra_b: 123.069556,
      dec_b: 18.234311,
      mjd_b: 61144.9956,
      sep_arcsec: 929.8,
      dlon_arcsec: -929.7,
      dlat_arcsec: 11.4,
      distance_au: 404.1,
      distance_lo_au: 404.1,
      distance_hi_au: 445.9,
      flux_ratio: 0.814,
      n_common_det: 1,
      n_dots_a: 3,
      n_dots_b: 3,
      snr_a: 6.55,
      snr_b: 15.29,
      p: 0.6604,
      known_a: null,
      known_b: null,
    },
  ],
  control: {
    n: 4,
    passing_per_control: [1, 4, 0, 1],
    expected_pairs: 1.5,
    spread_pairs: 1.249,
    poisson_p: 0.7813,
    beyond_chance: false,
    method: "shifted",
  },
  injection: {
    method: "table-level",
    distances: [
      { distance_au: 70, injected: 63, recovered: 61, recovered_frac: 0.968, distance_fit_median_au: 70.4 },
      { distance_au: 150, injected: 63, recovered: 57, recovered_frac: 0.905, distance_fit_median_au: 147.4 },
      { distance_au: 1000, injected: 66, recovered: 66, recovered_frac: 1, distance_fit_median_au: 1004.2 },
    ],
    recovered_frac: 0.958,
    min_cell_recovered_frac: 0.826,
    distance_err_median_frac: 0.016,
  },
  limits: {
    depth_ab_snr5: { D2: 17.36, D1: 17.26, D4: 17.71, D3: 17.51 },
    reach_au: { "neptune_p0.4": 283, "earth_p0.4": 144 },
    notes: ["a note"],
  },
};

const published = publishedJson as unknown as DistantResponse;

function el(html = ""): HTMLElement {
  const d = document.createElement("div");
  d.innerHTML = html;
  document.body.appendChild(d);
  return d;
}

const text = (n: Element | null) => (n?.textContent ?? "").replace(/\s+/g, " ").trim();

afterEach(() => {
  document.body.innerHTML = "";
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("distant-planet search section", () => {
  it("says what was searched, what was found against chance, the sensitivity and the limits", () => {
    const d = el(renderDistant(DIST));
    const s = d.querySelector(".sc-distant")!;
    expect(text(s.querySelector("h2"))).toBe("A distant planet?");
    expect([...s.querySelectorAll("h3")].map(text)).toEqual([
      "What we tested",
      "What we found",
      "How sensitive it is",
      "What it can't see",
    ]);
    expect(text(s.querySelector(".sc-intro"))).toContain("390″ away at 1000 AU, up to 4,800″ at 70 AU");
    // the funnel, every count from the file
    const funnel = [...s.querySelectorAll(".sc-distant-funnel tr")].map((r) => text(r.querySelector("td")));
    expect(funnel).toEqual(["5,275", "779", "779", "447", "442", "892", "1"]);
    expect(text(s.querySelector(".sc-distant-funnel"))).toContain(
      "pieces of bright stars (314), sources near the zone's edge (26) and saturated sources (5)",
    );
    expect(text(s.querySelector(".sc-distant-funnel"))).toContain("300″–7,200″ apart");
    // the result against chance
    expect(text(s.querySelector(".sc-num"))).toBe("1pair passed, of 892 tested");
    const vs = text(s.querySelector(".sc-num + .sc-vs"));
    expect(vs).toContain("Nothing beyond chance");
    expect(vs).toContain("Chance alone gives 1.5 ± 1.2 passing pairs");
    expect(vs).toContain("The probability of 1 or more by chance: 0.78.");
    expect(text(s)).toContain("930″ apart");
    expect(text(s)).toContain("about 404 AU from the Sun");
    expect(text(s)).toContain("it is what chance gives, not a planet");
    const pair = s.querySelector(".sc-distant-pairs tbody tr")!;
    expect(text(pair)).toContain("Visit 1, 27 Oct 2025");
    expect(text(pair)).toContain("Visit 2, 14 Apr 2026");
    expect(text(pair)).toContain("404 AU404–446 AU");
    // sensitivity
    expect(text(s)).toContain("We added 192 made-up planets at 70–1,000 AU");
    expect(text(s)).toContain("The search found 184 of them (96%). At every distance it found at least 91%, and 83% in the hardest single setting.");
    expect(text(s)).toContain("typically 1.6% off the true one");
    const inj = [...s.querySelectorAll(".sc-distant-injection tbody tr")].map((r) => [...r.children].map(text));
    expect(inj[0]).toEqual(["70 AU", "63", "61 97%", "70 AU"]);
    expect(inj).toHaveLength(3);
    // limits
    const limits = [...s.querySelectorAll(".sc-distant-limits li")].map(text);
    expect(limits[0]).toContain("about AB 17.3 (D1) to 17.7 (D4)");
    expect(limits[0]).toContain("out to about 283 AU; an Earth-size one to about 144 AU");
    expect(limits.some((t) => t.startsWith("103 orphans sit where their partner could lie outside this zone"))).toBe(true);
    expect(limits.some((t) => t.includes("more than 1″ an hour"))).toBe(true);
    expect(text(s.querySelector(".sc-zone"))).toContain("14 Nov 2026, 17:35 UTC");
    expect(s.querySelector<HTMLAnchorElement>('a[href="/data/distant.json"]')).not.toBeNull();
  });

  it("follows the file's own verdict when a search finds more than chance", () => {
    const more: DistantResponse = {
      ...DIST,
      counts: { ...DIST.counts, pairs: 6 },
      control: { ...DIST.control, poisson_p: 0.0004, beyond_chance: true },
    };
    expect(verdictText(more)).toMatch(/^More pairs passed than chance explains: worth a closer look, not yet a discovery/);
    expect(chanceText(more)).toContain("The probability of 6 or more by chance: 4.0e-4.");
    expect(text(el(renderDistant(more)))).not.toContain("not a planet");
  });

  it("says so when no pair passed and the sensitivity test is missing", () => {
    const none: DistantResponse = { ...DIST, counts: { ...DIST.counts, pairs: 0 }, pairs: [], injection: null };
    const d = el(renderDistant(none));
    expect(text(d)).toContain("No pair passed");
    expect(d.querySelector(".sc-distant-pairs")).toBeNull();
    expect(text(d)).toContain("The sensitivity test has not been run for this zone yet.");
    expect(chanceText(none)).toBe("Chance alone gives 1.5 ± 1.2 passing pairs (the same search on 4 shuffled copies of the data).");
  });

  it("shows a pending note when the file is not published", () => {
    const d = el(renderDistant(null));
    expect(text(d.querySelector(".sc-wait"))).toBe("The distant-planet search is published after its run.");
    expect(d.querySelector(".sc-num")).toBeNull();
  });

  it("escapes the data", () => {
    const bad: DistantResponse = { ...DIST, zone: "<img src=x onerror=alert(1)>", zone_box: null };
    const d = el(renderDistant(bad));
    expect(d.querySelector("img")).toBeNull();
    expect(text(d)).toContain("the <img src=x onerror=alert(1)> zone");
  });

  it("words the zone and the drops", () => {
    expect(zoneText(DIST)).toBe("the hunt zone (RA 120.5–130.5°, Dec +14.4° to +24.4°)");
    expect(zoneText({ zone: "pilot", zone_box: null })).toBe("the pilot zone");
    expect(dropText({ bright_fragment: 2, odd_code: 1, zone_edge: 0 })).toBe("pieces of bright stars (2) and odd code (1)");
  });

  it("sits after the suspects on the page, and mountScore loads /data/distant.json", async () => {
    const d = el();
    renderScore(d, null, null, DIST);
    const sections = [...d.querySelectorAll(".rs-found > section")].map((s) => s.className);
    expect(sections.slice(-2)).toEqual(["sc-suspects", "sc-distant"]);
    document.body.innerHTML = "";
    const urls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (u: string) => {
        urls.push(String(u));
        return String(u).endsWith("/data/distant.json")
          ? new Response(JSON.stringify(DIST), { status: 200 })
          : new Response("<!doctype html>", { status: 404, statusText: "Not Found" });
      }),
    );
    const m = el();
    const h = mountScore(m);
    await vi.waitFor(() => expect(m.querySelector(".sc-distant .sc-num")).not.toBeNull());
    expect(urls).toContain("/data/distant.json");
    h.destroy();
  });

  it("renders the published file, with numbers derived from it", () => {
    const d = el(renderDistant(published));
    const funnel = [...d.querySelectorAll(".sc-distant-funnel td")].map(text);
    expect(funnel.at(-1)).toBe(published.counts.pairs.toLocaleString("en-GB"));
    expect(funnel.at(-2)).toBe(published.counts.pairs_tested.toLocaleString("en-GB"));
    expect(text(d)).toContain(verdictText(published));
    expect(d.querySelectorAll(".sc-distant-pairs tbody tr")).toHaveLength(published.pairs.length);
  });
});
