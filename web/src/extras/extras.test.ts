import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiFailure } from "../api/client";
import type { Epoch, FramesResponse, ObjectResponse } from "../api/types";
import type { CardHandle, View } from "../app/modules";
import { targetBySlug, viewForTarget } from "../app/state";
import { bandPairs, bestPair, colourRead, colourSentence } from "./compare";
import {
  type AsteroidsAnswer,
  type ComoversAnswer,
  asteroidsHtml,
  catwiseHtml,
  comoversHtml,
  gaiaInFramesHtml,
  knownInFramesHtml,
  relayHtml,
} from "./crossmatch";
import { draftTracks, epochsCsv, itemsFor, scienceCsv, trackDraft } from "./downloads";
import { mountExtras, withExtras } from "./index";
import { failText, targetKey, umRange } from "./shared";

const pub = join(__dirname, "../../public");
const readJson = <T>(p: string): T => JSON.parse(readFileSync(join(pub, p), "utf8")) as T;
const object = (slug: string) => readJson<ObjectResponse>(`data/${slug}/object.json`);
const view = (slug: string): View => viewForTarget(targetBySlug(slug)!);
const spot: View = { ra: 10, dec: 20, clock: "months", fov: 5, det: 2, slug: null, name: null };
const ctxOf = (v: View, frames: FramesResponse | null = null, obj: ObjectResponse | null = null) => ({ view: v, frames, object: obj, key: targetKey(v) });

/** Real answers of /api/enrichment, 14 Nov 2026 (Barnard's Star, Epsilon Indi B). */
const BARNARD_ASTEROIDS: AsteroidsAnswer = {
  source: "Minor Planet Center orbits (kete); geocentric predictions, V <= 21",
  objects: [{ name: "(265513)", ra: 269.465318, dec: 4.727065, v: 20.54, visit: 2, mjd: 61122.657030103 }],
  errors: [],
  note: "All 73 observing sessions checked",
};
const EPSINDI_COMOVERS: ComoversAnswer = {
  source: "ESA Gaia DR3 co-moving companions",
  candidates: [
    { ra: 331.07645259656397, dec: -56.79381206688221, parallax: 270.658, pmra: 3981.97666296105, pmdec: -2466.8318147663504, phot_g_mean_mag: 18.030664, pm_chi: 0.873, sep_arcsec: 46.9, distance_pc: 3.6947, projected_sep_au: 173.28 },
    { ra: 330.8724078795965, dec: -56.79725466122902, parallax: 274.843, pmra: 3966.66, pmdec: -2536.19, phot_g_mean_mag: 4.322904, pm_chi: 2.64, sep_arcsec: 442.27, distance_pc: 3.6384, projected_sep_au: 1609.19 },
  ],
  luminosity: { L_sun: 1.4822691439192672e-5, note: "0.75-5 um only: a lower limit" },
};

function epoch(id: string, det: number, t0: number, t1: number, n = 10): Epoch {
  return { id, det, mjd_start: t0, mjd_end: t1, mjd_mid: (t0 + t1) / 2, n_exposures: n } as unknown as Epoch;
}

describe("D3 beside D6", () => {
  it("pairs epochs whose times overlap, each D6 used once, in time order", () => {
    const short = [epoch("a2", 3, 200, 210), epoch("a1", 3, 100, 110, 4), epoch("a3", 3, 300, 301)];
    const long = [epoch("b1", 6, 100, 110, 9), epoch("b2", 6, 205, 215), epoch("bx", 6, 500, 510)];
    const pairs = bandPairs(short, long);
    expect(pairs.map((p) => [p.short.id, p.long.id])).toEqual([
      ["a1", "b1"],
      ["a2", "b2"],
    ]);
    // the deepest pair (fewest exposures of the two, highest), the latest on a tie
    expect(bestPair(pairs)).toBe(1);
    expect(bandPairs(short, [])).toEqual([]);
  });

  it("pairs the five featured fields' real visits", () => {
    for (const slug of ["barnard", "epsindib", "wise0855", "rwaur", "rdra"]) {
      const f = readJson<FramesResponse>(`data/${slug}/frames_months_5.json`);
      const pairs = bandPairs(f.epochs.filter((e) => e.det === 3), f.epochs.filter((e) => e.det === 6));
      expect(pairs.length, slug).toBeGreaterThanOrEqual(2);
    }
  });

  it("reads WISE 0855 as cold: D6 only, at least ten times D3", () => {
    const c = colourRead(object("wise0855").spectrum);
    expect(c.kind).toBe("d6only");
    if (c.kind === "d6only") expect(c.atLeast).toBeGreaterThan(10);
    expect(colourSentence(c)).toMatch(/D6 is at least \d+ times as bright/);
  });

  it("gives Epsilon Indi B a ratio near 1 and calls a saturated star unreadable", () => {
    const c = colourRead(object("epsindib").spectrum);
    expect(c.kind).toBe("ratio");
    if (c.kind === "ratio") expect(c.ratio).toBeGreaterThan(0.5);
    expect(colourRead(object("barnard").spectrum)).toEqual({ kind: "none", why: "saturated" });
    expect(colourSentence({ kind: "none", why: "saturated" })).toMatch(/saturated/);
    expect(colourSentence({ kind: "none", why: "missing" })).toBeNull();
  });
});

describe("failures say which service and never read as empty", () => {
  it("names the service and what to do next", () => {
    const up = failText(new ApiFailure("x", 502, "upstream"), "NASA/IPAC IRSA's CatWISE2020 catalogue");
    expect(up).toContain("CatWISE2020");
    expect(up).toContain("not the same as nothing there");
    expect(up).toMatch(/[Tt]ry again/);
    expect(failText(new TypeError("Failed to fetch"), "The ESA Gaia archive")).toMatch(/can't be reached.*not the same as nothing there/);
    expect(failText(new ApiFailure("x", 500), "S")).toContain("failed (500)");
  });
});

describe("catalogue answers in words", () => {
  const barnard = view("barnard");

  it("lists known asteroids with V, distance from the centre and date", () => {
    const html = asteroidsHtml(BARNARD_ASTEROIDS, barnard);
    expect(html).toContain("1 known asteroid passed");
    expect(html).toContain("(265513)");
    expect(html).toContain("V 20.5");
    expect(html).toContain("(visit 2)");
    expect(html).not.toContain("incomplete");
    const empty = asteroidsHtml({ ...BARNARD_ASTEROIDS, objects: [], errors: [{ visit: 1, mjd: 60000, error: "outside" }] }, barnard);
    expect(empty).toContain("No known asteroid");
    expect(empty).toContain("incomplete");
  });

  it("picks out the target itself among Gaia co-movers and names its companion", () => {
    const eps = view("epsindib");
    const html = comoversHtml(EPSINDI_COMOVERS, eps, 61000, "Epsilon Indi B");
    expect(html).toContain("1 Gaia DR3 star nearby moves with it");
    expect(html).toContain("G 4.3");
    expect(html).toContain("1,609 au");
    expect(html).toContain("10<sup>−5</sup>");
    const selfOnly = comoversHtml({ ...EPSINDI_COMOVERS, candidates: [EPSINDI_COMOVERS.candidates[0]], luminosity: null }, eps, 61000, "Epsilon Indi B");
    expect(selfOnly).toContain("No other Gaia DR3 star");
    const none = comoversHtml({ source: "ESA Gaia DR3", candidates: [] }, eps, 61000, "X");
    expect(none).toContain("No Gaia DR3 star nearby moves with X");
    const noMotion = comoversHtml({ source: "ESA Gaia DR3", candidates: [], note: "No significant target motion available" }, eps, 61000, "X");
    expect(noMotion).toContain("no clear motion");
  });

  it("sorts CatWISE2020 sources by distance from the centre, with W1 − W2", () => {
    const html = catwiseHtml(
      {
        source: "CatWISE2020",
        sources: [
          { name: "far", ra: 10.01, dec: 20, w1mpro: 12, w2mpro: 11, pmra: 0, pmdec: 0 },
          { name: "near", ra: 10.0001, dec: 20, w1mpro: 15.2, w2mpro: 13.1, pmra: -0.3, pmdec: 0.4 },
          { name: "no position", ra: null, dec: null, w1mpro: 1, w2mpro: 1, pmra: null, pmdec: null },
        ],
      },
      spot,
    );
    expect(html).toContain("2 CatWISE2020 sources");
    expect(html.indexOf("near")).toBeLessThan(html.indexOf("far"));
    expect(html).toContain("W1 − W2 2.10 mag");
    expect(html).toContain("0.50"); // motion ″/yr
    expect(html).toContain('class="pro-only"');
  });

  it("draws ten years of W1 and W2 and compares SPHEREx with NEOWISE", () => {
    const points = [
      { mjd: 56800, W1: 12, W1_err: 0.05, W2: 10, W2_err: 0.05, n: 12, source: "NEOWISE" },
      { mjd: 60200, W2: 10.2, n: 12, source: "NEOWISE" },
      { mjd: 61000, W1: 12.3, W2: 10.5, W2_err: 0.02, source: "SPHEREx (synthetic)" },
    ];
    const html = relayHtml({ source: "x", points, error: null }, "t");
    expect(html).toContain("2 visits from 2014 to 2023");
    expect(html).toContain("0.40 mag fainter");
    expect(html).toContain("<svg");
    expect(html).not.toContain("NaN");
    expect(relayHtml({ source: "x", points: [], error: null }, "t")).toContain("no clean measurement");
  });

  it("reads Gaia stars and known objects straight from real frames", () => {
    const f = readJson<FramesResponse>("data/barnard/frames_months_5.json");
    const gaia = gaiaInFramesHtml(f, barnard);
    expect(gaia).toMatch(/Gaia DR3 stars in this field/);
    expect(gaia).toContain("10.39″/yr"); // Barnard's Star itself sits at the centre
    const elpis = readJson<FramesResponse>("data/elpis/frames_hours_20.json");
    expect(knownInFramesHtml(elpis)).toContain("Elpis");
    expect(knownInFramesHtml({ ...elpis, known: [] })).toContain("No known asteroid");
    expect(knownInFramesHtml(null)).toContain("once the plate's frames have loaded");
  });
});

describe("downloads", () => {
  it("writes the exposure table and the epoch list as CSV", () => {
    const sci = readJson<{ frames: { mjd: number; det: number; um: number; visit: number; quality: string; flags: Record<string, number> }[] }>(
      "science-data/barnard/science.json",
    );
    const text = scienceCsv(sci.frames);
    const lines = text.trim().split("\n");
    expect(lines[0]).toBe("mjd,det,um,visit,quality,flags");
    expect(lines.length).toBe(sci.frames.length + 1);
    expect(lines[1]).toMatch(/TRANSIENT=\d+;/);
    const f = readJson<FramesResponse>("data/elpis/frames_hours_20.json");
    const csv = epochsCsv(f).trim().split("\n");
    expect(csv[0]).toBe("id,kind,det,mjd_mid,mjd_start,mjd_end,n_exposures,um_min,um_max,flag_frac,sat_frac");
    expect(csv.length).toBe(f.epochs.length + 1);
  });

  it("offers static files first, and the server's GIF only where it has the field", () => {
    const signal = () => new AbortController().signal;
    const b = itemsFor(ctxOf(view("barnard")), signal);
    expect(b.find((i) => i.label.startsWith("Analysis"))?.href).toBe("/science-data/barnard/science.json");
    expect(b.find((i) => i.label.startsWith("Animated GIF"))?.href).toContain("clock=months&format=gif");
    const elpisFrames = readJson<FramesResponse>("data/elpis/frames_hours_20.json");
    const e = itemsFor(ctxOf(view("elpis"), elpisFrames), signal);
    expect(e.find((i) => i.label.startsWith("Animated GIF"))?.href).toContain("clock=hours&format=gif");
    const any = itemsFor(ctxOf(spot, elpisFrames), signal);
    expect(any.some((i) => i.label.startsWith("Animated GIF"))).toBe(false);
    expect(any.map((i) => i.label)).toEqual(["Frames and object, JSON", "Exposure list, CSV"]);
    expect(itemsFor(ctxOf(spot), signal)).toEqual([]);
  });

  it("drafts ADES only for unexplained movers, marked as a draft with SPHEREx's code", () => {
    const f = readJson<FramesResponse>("data/barnard/frames_hours_60.json");
    expect(draftTracks(f)).toEqual([]); // only "other movers" and artefacts here
    const track = { ...f.measured!.tracks.find((t) => t.dots.length >= 2)!, class: "suspect" as const, suspect_id: "hunt-mv-1" };
    const frames = { ...f, measured: { ...f.measured!, tracks: [track] } };
    expect(draftTracks(frames)).toHaveLength(1);
    const d = trackDraft(track, frames, 2);
    expect(d.text).toContain("DRAFT - NOT SUBMITTED");
    expect(d.text).toContain("! mpcCode 288");
    expect(d.lines).toBe(track.dots.length);
    expect(d.fromMean).toBe(false);
  });

  it("writes detector ranges as CLAUDE.md does", () => {
    expect(umRange(6)).toBe("4.4–5.0");
    expect(umRange(1)).toBe("0.75–1.1");
  });
});

// ---- the sections on the page ---------------------------------------------------------------

type Route = (url: string) => { status: number; body: unknown } | undefined;
function stubFetch(route: Route) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init?: { signal?: AbortSignal }) => {
      const url = String(input);
      calls.push(url);
      if (init?.signal?.aborted) throw new DOMException("aborted", "AbortError");
      const r = route(url);
      const path = url.replace(/^https?:\/\/[^/]+/, "").split("?")[0];
      if (!r && existsSync(join(pub, path)) && !path.endsWith("/")) {
        const buf = readFileSync(join(pub, path));
        return { ok: true, status: 200, json: async () => JSON.parse(buf.toString("utf8")), arrayBuffer: async () => buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength) };
      }
      const { status, body } = r ?? { status: 404, body: { error: { code: "not_found", message: "none" } } };
      return { ok: status < 400, status, statusText: "", json: async () => body };
    }),
  );
  return calls;
}

function open(el: HTMLElement, sec: string) {
  const d = el.querySelector<HTMLDetailsElement>(`[data-sec="${sec}"]`)!;
  d.open = true;
  d.dispatchEvent(new Event("toggle"));
}
const flush = () => new Promise((r) => setTimeout(r, 0));
async function settle() {
  for (let i = 0; i < 20; i++) await flush();
}

describe("the extras under the card", () => {
  beforeEach(() => {
    // jsdom has no canvas: the plates paint nothing, everything else runs.
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("asks nothing until a section is opened", async () => {
    const calls = stubFetch(() => undefined);
    const el = document.createElement("div");
    const ex = mountExtras(el);
    ex.show(view("barnard"), object("barnard"), readJson("data/barnard/frames_months_5.json"));
    await settle();
    expect(calls).toEqual([]);
    expect(el.querySelectorAll("details.ex-sec")).toHaveLength(3);
    expect([...el.querySelectorAll(".ex-title")].map((t) => t.textContent)).toEqual([
      "Compare short and long wavelengths",
      "Known objects and long-term observations",
      "Downloads",
    ]);
  });

  it("asks the four catalogues once opened, and says which one failed", async () => {
    const calls = stubFetch((url) => {
      if (!url.includes("/api/enrichment")) return undefined;
      if (url.includes("kind=catwise"))
        return { status: 502, body: { error: { code: "upstream", message: "NASA/IPAC IRSA (CatWISE2020) did not answer" } } };
      if (url.includes("kind=asteroids")) return { status: 200, body: BARNARD_ASTEROIDS };
      if (url.includes("kind=comovers")) return { status: 200, body: { source: "ESA Gaia DR3", candidates: [] } };
      return { status: 502, body: { error: { code: "upstream", message: "NEOWISE" } } };
    });
    const el = document.createElement("div");
    const ex = mountExtras(el);
    ex.show(view("barnard"), object("barnard"), readJson("data/barnard/frames_months_5.json"));
    open(el, "known");
    await settle();
    const kinds = calls.filter((u) => u.includes("/api/enrichment")).map((u) => new URL(u, "http://x").searchParams.get("kind"));
    expect(kinds.sort()).toEqual(["asteroids", "catwise", "comovers", "relay"]);
    const text = el.textContent!;
    expect(text).toContain("(265513)");
    expect(text).toContain("CatWISE2020 catalogue didn't answer");
    expect(text).toContain("NEOWISE archive didn't answer");
    expect(text).toContain("not the same as nothing there");
    expect(el.querySelectorAll('[data-retry]')).toHaveLength(2);
    // MPC only: no other small-body service is ever asked
    expect(calls.join(" ")).not.toMatch(/skybot|ssd\.jpl|horizons/i);
  });

  it("never shows the previous target's answers under a new target", async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => (release = r));
    stubFetch(() => undefined);
    const fetchMock = vi.mocked(fetch);
    const base = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (input, init) => {
      if (String(input).includes("/api/enrichment")) {
        await gate;
        if ((init as RequestInit | undefined)?.signal?.aborted) throw new DOMException("aborted", "AbortError");
        return { ok: true, status: 200, json: async () => BARNARD_ASTEROIDS } as Response;
      }
      return base(input as string, init as never);
    });
    const el = document.createElement("div");
    const ex = mountExtras(el);
    ex.show(view("barnard"), object("barnard"), null);
    open(el, "known");
    await settle();
    expect(el.textContent).toContain("Moving every known asteroid");
    // The page moves to another spot: the section empties at once.
    ex.status("loading");
    expect(el.querySelector('[data-sec="known"] [data-body]')!.textContent).toBe("");
    ex.show(spot, null, null);
    release();
    await settle();
    expect(el.textContent).not.toContain("(265513)");
    // A spot without science products: no catalogue calls, and it says why.
    expect(el.textContent).toContain("run for the five featured stars");
  });

  it("loads the D3 and D6 stacks from static files when opened, and explains colour against motion", async () => {
    const calls = stubFetch(() => undefined);
    const el = document.createElement("div");
    const ex = mountExtras(el);
    ex.show(view("wise0855"), object("wise0855"), null);
    open(el, "compare");
    await settle();
    expect(calls.some((u) => u.includes("/data/wise0855/frames_months_5.json"))).toBe(true);
    expect(calls.some((u) => u.includes("/api/"))).toBe(false);
    expect(calls.some((u) => u.includes("m5_d3_"))).toBe(true);
    expect(calls.some((u) => u.includes("m5_d6_"))).toBe(true);
    const text = el.textContent!;
    expect(text).toContain("same moment");
    expect(text).toContain("colour, not motion");
    expect(text).toMatch(/D6 is at least \d+ times as bright/);
    expect(el.querySelectorAll(".ex-select option").length).toBeGreaterThanOrEqual(2);
    expect(el.querySelector('.ex-fig[data-det="6"] [data-cap]')!.textContent).toMatch(/\d{4}, \d+ exposures?/);
  });

  it("says what failed when the D3 and D6 stacks can't be built, with a way to try again", async () => {
    stubFetch((url) => (url.includes("/api/frames") ? { status: 502, body: { error: { code: "upstream", message: "IRSA" } } } : undefined));
    const el = document.createElement("div");
    const ex = mountExtras(el);
    ex.show(spot, null, null);
    open(el, "compare");
    await settle();
    expect(el.textContent).toContain("NASA's archive didn't answer");
    expect(el.querySelector(".ex-retry")).not.toBeNull();
  });

  it("feeds the card and the extras from one handle", () => {
    const seen: string[] = [];
    const card: CardHandle = { show: () => seen.push("card"), status: (k) => seen.push(`card:${k}`) };
    const el = document.createElement("div");
    const both = withExtras(card, el);
    both.status("loading");
    both.show(spot, null, null);
    expect(seen).toEqual(["card:loading", "card"]);
    expect(el.classList.contains("ex")).toBe(true);
    expect(withExtras(card, null)).toBe(card);
  });
});
