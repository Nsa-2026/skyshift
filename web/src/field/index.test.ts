import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiFailure } from "../api/client";
import type { Analysis } from "../api/science-client";
import type { SkyPos } from "../api/types";
import type { View } from "../app/modules";
import epsAnalysis from "../../public/science-data/epsindib/analysis.json";
import barnardAnalysis from "../../public/science-data/barnard/analysis.json";
import rdraAnalysis from "../../public/science-data/rdra/analysis.json";
import wiseScience from "../../public/science-data/wise0855/science.json";
import { analysisDet, fieldKey, keepTogether, mountField, type FieldData } from "./index";
import type { ScienceDoc } from "./model";

const EPS = epsAnalysis as unknown as Analysis;
const BARNARD = barnardAnalysis as unknown as Analysis;
const SCIENCE = wiseScience as unknown as ScienceDoc;

const view = (slug: string | null, name: string | null, ra = 331.09668, dec = -56.80067, det: View["det"] = 2): View => ({
  ra,
  dec,
  slug,
  name,
  clock: "months",
  fov: 5,
  det,
});
const EPS_VIEW = view("epsindib", "Epsilon Indi B");
const BARNARD_VIEW = view("barnard", "Barnard's Star", 269.4462, 4.76883);
const OFF_VIEW = view(null, null, 10, 20);

/** A promise you settle by hand, to hold an answer back until a later view has started. */
function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}
const flush = () => new Promise((r) => setTimeout(r, 0));

function setup(data: Partial<FieldData>, visibleAtStart = true) {
  const el = document.createElement("div");
  document.body.append(el);
  const highlight = vi.fn<(p: SkyPos | null) => void>();
  let show: (on: boolean) => void = () => {};
  const handle = mountField(el, {
    highlight,
    data: { gaia: async () => null, science: async () => SCIENCE, analysis: async () => EPS, ...data },
    whenVisible(_el, cb) {
      show = cb;
      if (visibleAtStart) cb(true);
      return () => {};
    },
  });
  return { el, handle, highlight, reveal: () => show(true) };
}

afterEach(() => {
  document.body.innerHTML = "";
});

describe("field sections", () => {
  it("loads nothing until the sections scroll into view", async () => {
    const analysis = vi.fn(async () => EPS);
    const science = vi.fn(async () => SCIENCE);
    const { el, handle, reveal } = setup({ analysis, science }, false);
    handle.view(EPS_VIEW);
    await flush();
    expect(analysis).not.toHaveBeenCalled();
    expect(science).not.toHaveBeenCalled();
    expect(el.textContent).toMatch(/Signals found around Epsilon Indi B load here/);
    reveal();
    await flush();
    expect(analysis).toHaveBeenCalledTimes(1);
    expect(science).toHaveBeenCalledTimes(1);
    expect(el.textContent).toMatch(/It found 1 slow mover, 4 fast movers and \d+ cold sources/);
  });

  it("renders groups, chips, the false-alarm key and the Planet X answer", async () => {
    const { el, handle } = setup({});
    handle.view(EPS_VIEW);
    await flush();
    const chips = [...el.querySelectorAll<HTMLElement>(".fd-item .fd-chip")];
    expect(chips[0].dataset.tone).toBe("risk");
    expect(chips[0].textContent).toBe("12 % false alarms");
    expect(el.querySelector(".fd-key")!.textContent).toMatch(/Not the chance that the object is real/);
    expect([...el.querySelectorAll("h3")].map((h) => h.textContent?.replace(/\s+/g, " ").trim())).toEqual([
      "Slow movers (1)",
      "Fast movers (4)",
      `Cold sources (${EPS.cold_candidates.filter((c) => (c.n_visits ?? 0) >= 2).length})`,
      "Smallest planet SPHEREx could see here",
      "Faintest dot it shows",
    ]);
    // Three rows per group show; the rest wait behind "Show N more".
    const fast = el.querySelector('[aria-labelledby="fd-fast-h"]')!;
    expect(fast.querySelectorAll(".fd-item:not([hidden])")).toHaveLength(3);
    const more = fast.querySelector<HTMLButtonElement>(".fd-more")!;
    expect(more.textContent).toBe("Show 1 more");
    more.click();
    expect(fast.querySelectorAll(".fd-item:not([hidden])")).toHaveLength(4);
    expect(more.getAttribute("aria-expanded")).toBe("true");
    expect(el.querySelector(".fd-answer")!.textContent).toMatch(/^Yes\. A planet like the Planet Nine models/);
    expect(el.querySelector(".fd-assume")!.textContent).toMatch(/model estimate, not a search/);
    // Units stay with their numbers.
    expect(el.querySelector(".fd-dl")!.textContent).toContain("0.25 R⊕");
  });

  it("marks a signal on the plate, toggles it off, and follows a slow mover's motion", async () => {
    const { el, handle, highlight } = setup({});
    handle.view(EPS_VIEW);
    await flush();
    const btn = el.querySelector<HTMLButtonElement>('[aria-labelledby="fd-slow-h"] .fd-show')!;
    btn.click();
    expect(btn.getAttribute("aria-pressed")).toBe("true");
    expect(btn.getAttribute("aria-label")).toBe("Show on plate: slow mover, 4.53″/yr");
    const s = EPS.slow_movers[0];
    expect(highlight).toHaveBeenLastCalledWith({ ra: s.ra, dec: s.dec });
    handle.epoch(s.mjd_ref! + 365.25);
    const moved = highlight.mock.lastCall![0]!;
    expect((moved.dec - s.dec) * 3600).toBeCloseTo(s.pmdec_arcsec_yr!, 6);
    btn.click();
    expect(highlight).toHaveBeenLastCalledWith(null);
    expect(btn.getAttribute("aria-pressed")).toBe("false");
  });

  it("lets a click on the plate replace our mark without clearing it", async () => {
    const { el, handle, highlight } = setup({});
    handle.view(EPS_VIEW);
    await flush();
    const btn = el.querySelector<HTMLButtonElement>(".fd-show")!;
    btn.click();
    highlight.mockClear();
    handle.picked();
    expect(btn.getAttribute("aria-pressed")).toBe("false");
    handle.epoch(61500);
    expect(highlight).not.toHaveBeenCalled();
  });

  it("never shows the previous target's signals under a new name", async () => {
    const first = deferred<Analysis>();
    const signals: AbortSignal[] = [];
    const analysis = vi.fn((v: View, signal: AbortSignal) => {
      signals.push(signal);
      return v.slug === "epsindib" ? first.promise : Promise.resolve(BARNARD);
    });
    const { el, handle, highlight } = setup({ analysis });
    handle.view(EPS_VIEW);
    await flush();
    handle.view(BARNARD_VIEW);
    expect(signals[0].aborted).toBe(true);
    await flush();
    expect(el.textContent).toMatch(/around Barnard's Star/);
    // Epsilon Indi B's answer arrives late: it is dropped.
    first.resolve(EPS);
    await flush();
    expect(el.textContent).toMatch(/around Barnard's Star/);
    expect(el.textContent).not.toMatch(/Epsilon Indi B/);
    expect(el.textContent).toMatch(/It found no slow movers, 2 fast movers and no cold sources/);
    expect(highlight).not.toHaveBeenCalled();
  });

  it("clears the old field at once, and the plate mark with it", async () => {
    const pending = deferred<Analysis>();
    const analysis = vi.fn((v: View) => (v.slug ? Promise.resolve(EPS) : pending.promise));
    const { el, handle, highlight } = setup({ analysis });
    handle.view(EPS_VIEW);
    await flush();
    el.querySelector<HTMLButtonElement>(".fd-show")!.click();
    handle.view(OFF_VIEW);
    expect(highlight).toHaveBeenLastCalledWith(null);
    // While the new field loads, nothing of the old one is left.
    expect(el.querySelector('[data-part="signals"]')!.textContent!.trim()).toBe("Loading the analysis…");
    expect(el.querySelector('[data-part="signals"]')!.getAttribute("aria-busy")).toBe("true");
    expect(el.textContent).not.toMatch(/Epsilon Indi B|slow mover/);
  });

  it("keeps the same field when only the detector or clock changes", async () => {
    const analysis = vi.fn(async () => EPS);
    const { handle } = setup({ analysis });
    handle.view(EPS_VIEW);
    await flush();
    handle.view({ ...EPS_VIEW, det: 5, clock: "hours", fov: 20 });
    await flush();
    expect(analysis).toHaveBeenCalledTimes(1);
    expect(fieldKey(EPS_VIEW)).toBe(fieldKey({ ...EPS_VIEW, ra: EPS_VIEW.ra + 0.01 }));
    expect(fieldKey(OFF_VIEW)).toBe("10.0000,20.0000");
    expect(analysisDet(view("wise0855", "WISE 0855−0714", 133.75969, -7.24216, 2))).toBe(6);
    expect(analysisDet({ ...OFF_VIEW, det: 4 })).toBe(4);
  });

  it("says plainly that a spot has no prepared analysis, and what to do next", async () => {
    const nf = () => Promise.reject(new ApiFailure("No precomputed field here. Choose one of the five featured targets.", 404, "not_found"));
    const { el, handle } = setup({ analysis: nf, science: nf });
    handle.view(OFF_VIEW);
    await flush();
    const text = el.textContent!.replace(/\s+/g, " ");
    expect(text).toMatch(/No prepared analysis for 00h40m00\.0s \+20°00′00″\. Signals are worked out ahead of time for five fields only/);
    expect(text).toMatch(/flag anything that jumps in Flag what moved/);
    expect([...el.querySelectorAll("a")].map((a) => a.getAttribute("href"))).toEqual([
      "?t=barnard#/explore",
      "?t=epsindib#/explore",
      "?t=wise0855#/explore",
      "?t=rwaur#/explore",
      "?t=rdra#/explore",
    ]);
    expect(text).toMatch(/No depth measured for 00h40m00\.0s/);
    expect(el.querySelector(".fd-retry")).toBeNull();
  });

  it("offers a retry when the service fails, and the retry loads", async () => {
    let calls = 0;
    const analysis = vi.fn(async () => {
      calls++;
      if (calls === 1) throw new ApiFailure("bad gateway", 502, "upstream");
      return BARNARD;
    });
    const { el, handle } = setup({ analysis });
    handle.view(BARNARD_VIEW);
    await flush();
    expect(el.querySelector(".fd-err")!.textContent).toBe(
      "The analysis service didn't answer (502). Try again in a minute; the five prepared fields load without it.",
    );
    el.querySelector<HTMLButtonElement>(".fd-retry")!.click();
    await flush();
    expect(analysis).toHaveBeenCalledTimes(2);
    expect(el.textContent).toMatch(/2 fast movers/);
  });

  it("says an empty field isn't proof of nothing", async () => {
    const { el, handle } = setup({ analysis: async () => rdraAnalysis as unknown as Analysis });
    handle.view(view("rdra", "R Draconis", 248.16723, 66.75502));
    await flush();
    expect(el.querySelector(".fd-found")!.textContent).toMatch(/^Nothing passed the checks here\. That doesn't mean the field is empty/);
    expect(el.querySelectorAll(".fd-none")).toHaveLength(3);
  });

  it("uses Gaia when the field's list loads, and says so", async () => {
    const { el, handle } = setup({
      gaia: async () => ({
        stars: [{ id: "1", ra: 331.07645259656397, dec: -56.79381206688221, pmra: 3981.977, pmdec: -2466.832, parallax: 270.658, g: 18.031 }],
        mjdMid: 61062,
      }),
    });
    handle.view(EPS_VIEW);
    await flush();
    expect(el.textContent).toMatch(/compared each signal with Gaia DR3/);
    expect(el.querySelector('[aria-labelledby="fd-slow-h"] .fd-facts')!.textContent).toMatch(/Gaia DR3 has a star 0\.\d″ away/);
  });
});

describe("keepTogether", () => {
  it("binds units and ranges to their numbers", () => {
    expect(keepTogether("D2 (1.1–1.6 µm), 4.5 pc, 83 µJy, 12 %")).toBe("D2 (1.1–⁠1.6 µm), 4.5 pc, 83 µJy, 12 %");
    expect(keepTogether('<span data-tone="good">Under 2 %</span>')).toBe('<span data-tone="good">Under 2 %</span>');
  });
});
