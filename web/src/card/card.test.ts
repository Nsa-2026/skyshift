import { readFileSync, readdirSync, existsSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import type { FramesResponse, ObjectResponse } from "../api/types";
import type { View } from "../app/modules";
import { targetBySlug, viewForTarget } from "../app/state";
import { mountCard } from "./index";
import { lightCurve, lightCurveDets, renderLightCurve } from "./lightcurve";
import { moleculeRows, renderSpectrum, spectrumDomain, visitCurve } from "./spectrum";
import { visitColour } from "./svg";

const dataDir = join(__dirname, "../../public/data");
const slugs = readdirSync(dataDir).filter((s) => existsSync(join(dataDir, s, "object.json")));
const load = (slug: string): ObjectResponse => JSON.parse(readFileSync(join(dataDir, slug, "object.json"), "utf8"));

const spot: View = { ra: 10, dec: 10, clock: "months", fov: 5, det: 2, slug: null, name: null };

function viewFor(slug: string): View {
  const t = targetBySlug(slug);
  return t ? viewForTarget(t) : { ...spot, slug: null };
}

function cardFor(slug: string, frames: FramesResponse | null = null): HTMLElement {
  const el = document.createElement("section");
  mountCard(el).show(viewFor(slug), load(slug), frames);
  return el;
}

/** Hours frames whose epochs either carry their own centre (the frames follow the object) or not. */
function hoursFrames(follow: boolean): FramesResponse {
  const epoch = { id: "e1", kind: "exposure", det: 2, mjd_mid: 61000, ...(follow ? { center: { ra: 1, dec: 1 } } : {}) };
  return { clock: "hours", center: { ra: 1, dec: 1 }, epochs: [epoch] } as unknown as FramesResponse;
}

describe("every real object.json", () => {
  it("is present", () => {
    expect(slugs.length).toBeGreaterThanOrEqual(8);
  });

  for (const slug of slugs) {
    it(`renders ${slug} in both spectrum modes without throwing`, () => {
      const el = document.createElement("section");
      const card = mountCard(el);
      const obj = load(slug);
      card.status("loading");
      expect(el.dataset.state).toBe("loading");
      card.show(viewFor(slug), obj, null);
      expect(el.dataset.state).toBe("ready");
      expect(el.querySelector(".cd-name")?.textContent).toBe(obj.name);
      expect(el.querySelector(".cd-about")?.textContent?.length).toBeGreaterThan(10);
      expect(el.textContent).not.toMatch(/NaN|undefined|Infinity/);
      if (obj.measure) expect(el.querySelector(".cd-n")?.textContent).toBe(`${obj.measure.shift_arcsec.toFixed(2)}″`);
      else expect(el.querySelector(".cd-measure .cd-why")).not.toBeNull();
      if (obj.spectrum.length) {
        expect(el.querySelector(".cd-spec svg title")).not.toBeNull();
        expect(el.querySelector(".cd-spec table")).not.toBeNull();
        const byVisit = el.querySelector<HTMLButtonElement>('.cd-spec .cd-seg button[data-mode="visit"]');
        byVisit?.click();
        expect(el.textContent).not.toMatch(/NaN|undefined|Infinity/);
        if (byVisit) expect(el.querySelector('.cd-spec .cd-seg button[data-mode="visit"]')?.getAttribute("aria-pressed")).toBe("true");
        el.querySelectorAll<HTMLButtonElement>(".cd-dets button:not(:disabled)").forEach((b) => b.click());
        expect(el.innerHTML).not.toMatch(/NaN/);
      }
      // No boxes or alarm blocks: limits are footnotes with a numbered mark that points at them.
      el.querySelectorAll<HTMLAnchorElement>(".cd-ref a").forEach((a) => {
        expect(el.querySelector(a.getAttribute("href")!)).not.toBeNull();
      });
    });
  }

  it("reads Barnard's Star the way the demo quotes it", () => {
    const el = cardFor("barnard");
    expect(el.querySelector(".cd-about")?.textContent).toBe("A red dwarf 6 light-years away, the fastest-moving star in our sky.");
    expect(el.querySelector(".cd-n")?.textContent).toBe("10.83″");
    expect(el.querySelector(".cd-pm")?.textContent).toBe("± 0.36″");
    expect(el.querySelector(".cd-says")?.textContent).toBe(
      "moved between visits 1 and 3 (0.99 years). Gaia predicts 10.27″: they agree within 1.6σ.",
    );
    expect(el.querySelector(".cd-arrow")?.getAttribute("aria-label")).toMatch(/358° east of north \(north\)/);
    expect(el.querySelector(".cd-dl")?.textContent).toMatch(/1\.83 pc/);
  });

  it("names what Barnard's shift was measured on, whatever detector the player shows", () => {
    const row = (el: HTMLElement) =>
      [...el.querySelectorAll(".cd-dl > div")].find((d) => d.querySelector("dt")?.textContent === "Measured in")?.querySelector("dd")?.textContent;
    const obj = load("barnard");
    expect(obj.measure?.source?.det).toBe(2); // precompute wrote it (contracts/api.md v3.9)
    const el = document.createElement("section");
    const card = mountCard(el);
    card.show({ ...viewFor("barnard"), det: 2 }, obj, null);
    expect(row(el)).toBe("D2, visits 1 and 3 (18 and 25 exposures)");
    card.show({ ...viewFor("barnard"), det: 6, clock: "hours", fov: 20 }, obj, null);
    expect(row(el)).toBe("D2, visits 1 and 3 (18 and 25 exposures), not the D6 the player shows");
    expect(el.querySelector(".cd-n")?.textContent).toBe("10.83″"); // the player doesn't change the measurement
    // Without a source (older files, cached answers): no row, and nothing taken from the player.
    const old = { ...obj, measure: { ...obj.measure!, source: undefined } };
    card.show({ ...viewFor("barnard"), det: 6 }, old, null);
    expect(row(el)).toBeUndefined();
    expect(el.querySelector(".cd-says")?.textContent).toMatch(/^moved between visits 1 and 3 \(0\.99 years\)/);
    // The measured pair wins over the count guessed from the visits on screen.
    const later = { ...obj, measure: { ...obj.measure!, source: { ...obj.measure!.source!, visits: [2, 3] as [number, number] } } };
    card.show(viewFor("barnard"), later, null);
    expect(el.querySelector(".cd-says")?.textContent).toMatch(/^moved between visits 2 and 3 \(0\.99 years\)/);
  });

  it("puts Barnard's saturation on the number as note 1 and the parallax on the distance as note 2", () => {
    const el = cardFor("barnard");
    const heroRef = el.querySelector(".cd-hero .cd-ref a");
    expect(heroRef?.textContent).toBe("1");
    const satNote = el.querySelector(heroRef!.getAttribute("href")!);
    expect(satNote?.textContent).toMatch(/saturated in every detector \(353 of 353 spectrum points\)/);
    expect(satNote?.textContent).toMatch(/measured on the wings/);
    const distRef = el.querySelector(".cd-dl .cd-ref a");
    expect(distRef?.textContent).toBe("2");
    expect(el.querySelector(distRef!.getAttribute("href")!)?.textContent).toMatch(/doesn't measure parallax/);
    expect(el.querySelectorAll(".cd-spec circle[fill='var(--glass)']").length).toBeGreaterThan(50); // hollow
  });

  it("does not claim wing measurement when only a few points saturate", () => {
    const el = cardFor("epsindib"); // 2 of 416 points saturate, in D5 and D6
    expect(el.querySelector(".cd-hero .cd-ref")).toBeNull();
    const capRef = el.querySelector(".cd-spec .cd-cap .cd-ref a");
    expect(capRef?.textContent).toBe("2"); // after the distance mark
    const note = el.querySelector(capRef!.getAttribute("href")!);
    expect(note?.textContent).toMatch(/saturated in D5 and D6 \(2 of 416 spectrum points\)\./);
    expect(note?.textContent).not.toMatch(/wings/);
    expect(el.querySelector(".cd-says")?.textContent).toMatch(/^moved between visits 1 and 4 \(1\.41 years\)/);
  });

  it("follows a footnote mark without touching the URL hash", () => {
    const el = cardFor("barnard");
    document.body.append(el);
    const before = location.hash;
    el.querySelector<HTMLAnchorElement>(".cd-hero .cd-ref a")!.click();
    expect(location.hash).toBe(before);
    expect(document.activeElement?.id).toMatch(/-fn-sat$/);
    el.remove();
  });

  it("names the visit pair only when the detectors that fit the measure agree", () => {
    const months = (slug: string): FramesResponse => JSON.parse(readFileSync(join(dataDir, slug, "frames_months_5.json"), "utf8"));
    expect(cardFor("barnard", months("barnard")).querySelector(".cd-says")?.textContent).toMatch(/^moved between visits 1 and 3 \(0\.99 years\)/);
    expect(cardFor("epsindib", months("epsindib")).querySelector(".cd-says")?.textContent).toMatch(/^moved between visits 1 and 4/);
    // R Draconis: 3 visits in D2, 4 in D3, and both span the measured year.
    expect(cardFor("rdra", months("rdra")).querySelector(".cd-says")?.textContent).toMatch(/^between the first and last visits \(1 year\)/);
  });

  it("says R Draconis stays put, with a spectrum read from the wings", () => {
    const el = cardFor("rdra");
    expect(el.querySelector(".cd-hero")?.getAttribute("data-seen")).toBe("false");
    expect(el.querySelector(".cd-arrow")).toBeNull();
    expect(el.querySelector(".cd-says")?.textContent).toMatch(/so it stays put/);
    expect(el.querySelector(".cd-spec .cd-cap")?.textContent).toMatch(/^500 points from 0\.74 to 5\.00 µm/);
    // Every point saturates, yet each is drawn as a (hollow) detection, not as an upper limit.
    expect(el.querySelectorAll(".cd-spec circle[fill='var(--glass)']").length).toBeGreaterThan(50);
    expect(el.textContent).not.toMatch(/Upper limit, below 2σ/);
  });

  it("says why a spectrum is missing when every exposure saturated", () => {
    const el = document.createElement("section");
    mountCard(el).show(spot, { ...load("rdra"), spectrum: [], spectrum_reason: "saturated" } as ObjectResponse, null);
    expect(el.querySelector(".cd-spec .cd-cap")?.textContent).toMatch(/^No spectrum: it is so bright/);
  });

  it("says plainly when a spectrum is missing for no known reason", () => {
    const el = document.createElement("section");
    mountCard(el).show(spot, { ...load("rdra"), spectrum: [], spectrum_reason: null } as ObjectResponse, null);
    expect(el.querySelector(".cd-spec .cd-cap")?.textContent).toMatch(/^No spectrum measured here yet\./);
  });

  it("compares WISE 0855 with the published motion, since Gaia can't see it", () => {
    const el = cardFor("wise0855");
    expect(el.querySelector(".cd-says")?.textContent).toMatch(/The published proper motion predicts 8\.06″: they agree within 1\.1σ\./);
    expect(el.querySelector(".cd-dl")?.textContent).toMatch(/not in Gaia/);
  });

  it("explains why a comet has no shift, from the frames", () => {
    expect(cardFor("3i-atlas").querySelector(".cd-why")?.textContent).toMatch(/moves in hours/);
    expect(cardFor("3i-atlas", hoursFrames(true)).querySelector(".cd-why")?.textContent).toMatch(/frames follow it/);
    const asteroid = cardFor("elpis", hoursFrames(false)).querySelector(".cd-why")?.textContent;
    expect(asteroid).toMatch(/crosses the field/);
    expect(asteroid).toMatch(/violet diamond/);
    expect(asteroid).not.toMatch(/follow/);
  });

  it("reads a spectrum point and a visit with the keyboard", () => {
    const el = cardFor("rwaur");
    const sp = el.querySelector<SVGSVGElement>('[data-chart="spectrum"] svg')!;
    sp.dispatchEvent(new KeyboardEvent("keydown", { key: "Home", bubbles: true }));
    expect(el.querySelector(".cd-spec .cd-readout")?.textContent).toMatch(/^0\.\d\d µm: .* µJy \(D1, visit \d/);
    const lc = el.querySelector<SVGSVGElement>('[data-chart="lightcurve"] svg')!;
    lc.dispatchEvent(new KeyboardEvent("keydown", { key: "End", bubbles: true }));
    expect(el.querySelector(".cd-lc .cd-readout")?.textContent).toMatch(/^Visit 3, .* µJy in D2, median of \d+ points/);
  });
});

describe("states", () => {
  it("shows an empty spot with a generic hint", () => {
    const el = document.createElement("section");
    mountCard(el).show(spot, null, null);
    expect(el.querySelector(".cd-name")?.textContent).toBe("This spot");
    expect(el.querySelector(".cd-about")?.textContent).toMatch(/^The sky at 00h40m/);
    expect(el.textContent).toMatch(/D1\+D4, D2\+D5 and D3\+D6/);
    expect(el.textContent).toMatch(/Nothing has been measured/);
  });

  it("describes an unnamed Gaia star by its distance", () => {
    const el = document.createElement("section");
    mountCard(el).show(spot, { ...load("rwaur"), name: null }, null);
    expect(el.querySelector(".cd-about")?.textContent).toBe("A star in Gaia DR3, 510 light-years away.");
  });

  it("shows an error as one sentence with what to do", () => {
    const el = document.createElement("section");
    const card = mountCard(el);
    card.show(spot, null, null);
    card.status("error", "NASA's archive didn't answer. Try again in a minute, or pick a featured target.");
    expect(el.dataset.state).toBe("error");
    expect(el.querySelector("[role=alert]")?.textContent).toMatch(/archive didn't answer. Try again in a minute/);
    card.status("error");
    expect(el.querySelector("[role=alert]")?.textContent).toMatch(/pick a featured target above/);
  });

  it("shows a quiet skeleton while loading", () => {
    const el = document.createElement("section");
    mountCard(el).status("loading");
    expect(el.getAttribute("aria-busy")).toBe("true");
    expect(el.querySelector(".cd-sk-hero")).not.toBeNull();
    expect(el.querySelector("[role=status]")?.textContent).toMatch(/Reading the object/);
  });

  it("does not show the previous object while the next one loads", () => {
    const el = document.createElement("section");
    const card = mountCard(el);
    const barnard = load("barnard");
    card.show(viewFor("barnard"), barnard, null);
    card.status("loading");
    card.show(viewFor("rdra"), barnard, null); // frames arrived first, object is stale
    expect(el.querySelector(".cd-n")).toBeNull();
    expect(el.getAttribute("aria-busy")).toBe("true");
    expect(el.querySelector(".cd-name")?.textContent).toBe("R Draconis");
    card.show(viewFor("rdra"), load("rdra"), null);
    expect(el.querySelector(".cd-name")?.textContent).toBe("R Draconis");
    expect(el.getAttribute("aria-busy")).toBe("false");
  });

  it("says the spectrum is being measured when frames arrive before the object", () => {
    const el = document.createElement("section");
    const card = mountCard(el);
    card.status("loading");
    card.show(spot, null, null); // frames first, the live /api/object still measuring
    expect(el.querySelector(".cd-spec")?.textContent).toMatch(/Measuring the spectrum/);
    card.show(viewFor("rdra"), load("rdra"), null);
    expect(el.querySelector(".cd-spec")?.textContent).not.toMatch(/Measuring the spectrum/);
    card.status("loading");
    card.status("idle"); // the object failed (404): no endless "measuring"
    card.show(spot, null, null);
    expect(el.textContent).not.toMatch(/Measuring the spectrum/);
  });

  describe("why there is no shift (measure_reason, contracts v3.2)", () => {
    const barnard2016: View = { ...spot, ra: 269.4485, dec: 4.7394 };
    const moved = {
      name: null, gaia: null, known_ucd: null, spectrum: [], measure: null, measure_reason: "moved_away",
      measure_hint: { name: "Barnard's Star", ra: 269.446376, dec: 4.768684, mjd: 61104.25 },
    } as ObjectResponse;

    it("offers to open a fast star where it is now", () => {
      const el = document.createElement("section");
      const opened: { ra: number; dec: number }[] = [];
      mountCard(el, { onExplore: (at) => opened.push(at) }).show(barnard2016, moved, null);
      expect(el.querySelector(".cd-why")?.textContent).toMatch(/^This is a catalogue position of Barnard's Star, .* 106″ away/);
      const a = el.querySelector<HTMLAnchorElement>("a.cd-open");
      expect(a?.textContent).toBe("Open it where it is now");
      expect(a?.getAttribute("href")).toBe("?ra=269.44638&dec=4.76868&clock=months&fov=5&det=2#/explore");
      a!.click();
      expect(opened).toEqual([{ ra: 269.446376, dec: 4.768684 }]);
    });

    it("keeps a plain link without the app, and no link for other reasons", () => {
      const el = document.createElement("section");
      mountCard(el).show(barnard2016, moved, null);
      expect(el.querySelector("a.cd-open")?.getAttribute("href")).toMatch(/^\?ra=269\.44638&dec=4\.76868/);
      mountCard(el).show(spot, { ...moved, measure_reason: "not_detected", measure_hint: null }, null);
      expect(el.querySelector("a.cd-open")).toBeNull();
      expect(el.querySelector(".cd-why")?.textContent).toMatch(/^Nothing stands out clearly at this spot/);
      mountCard(el).show(spot, { ...moved, measure_hint: null }, null);
      expect(el.querySelector("a.cd-open")).toBeNull();
      expect(el.querySelector(".cd-why")?.textContent).toMatch(/Fast stars move/);
    });

    it("escapes a hint's name", () => {
      const el = document.createElement("section");
      const evil = { ...moved, measure_hint: { ...moved.measure_hint!, name: "<img src=x>" } };
      mountCard(el, { onExplore: () => {} }).show(barnard2016, evil, null);
      expect(el.querySelector("img")).toBeNull();
      expect(el.querySelector(".cd-why")?.textContent).toMatch(/<img src=x>/);
    });
  });

  it("escapes names", () => {
    const el = document.createElement("section");
    mountCard(el).show(spot, { name: "<img src=x>", gaia: null, known_ucd: null, spectrum: [], measure: null }, null);
    expect(el.querySelector("img")).toBeNull();
  });
});

describe("charts", () => {
  const rw = load("rwaur");
  it("keeps the log domain around the data", () => {
    const d = spectrumDomain(rw.spectrum);
    expect(d.min).toBeLessThanOrEqual(40229.3); // lowest flux − 1σ
    expect(d.max).toBeGreaterThanOrEqual(10206334.4); // highest flux + 1σ
  });
  it("draws one point per spectrum point inside the plot", () => {
    for (const width of [343, 420]) {
      const c = renderSpectrum(rw.spectrum, { width, mode: "det", id: "t" });
      expect(c.points.length).toBe(rw.spectrum.length);
      for (const p of c.points) {
        expect(p.x).toBeGreaterThanOrEqual(c.box.left);
        expect(p.x).toBeLessThanOrEqual(c.box.right);
        expect(p.y).toBeGreaterThanOrEqual(c.box.top - 0.01);
        expect(p.y).toBeLessThanOrEqual(c.box.bottom + 0.01);
      }
    }
  });
  it("draws on glass: no plot background, hairline grid, detector strip", () => {
    const { svg } = renderSpectrum(rw.spectrum, { width: 343, mode: "det", id: "t" });
    expect(svg).not.toMatch(/--plate|--sheet/);
    expect(svg).toMatch(/stroke="var\(--hair\)"/);
    for (const d of [1, 2, 3, 4, 5, 6]) expect(svg).toContain(`fill="var(--d${d})"`);
  });
  it("shades visits in graphite, never in detector colours", () => {
    const { svg } = renderSpectrum(rw.spectrum, { width: 343, mode: "visit", id: "t" });
    expect(svg).not.toMatch(/var\(--d\d\)" stroke="var\(--glass\)/); // no detector-filled points
    const ramp = [0, 1, 2].map((i) => visitColour(i, 3));
    expect(ramp[0]).toBe("#8b95a5");
    expect(ramp[2]).toBe("#151d2c");
    for (const c of ramp) expect(svg).toContain(c);
  });
  it("keeps molecule labels apart", () => {
    const rows = moleculeRows((um) => 36 + ((um - 0.7) / 4.35) * 293);
    expect(Math.max(...rows)).toBeLessThanOrEqual(2);
  });
  it("bins a visit into a median curve", () => {
    const c = visitCurve(rw.spectrum.filter((p) => p.visit === 1));
    expect(c.length).toBeGreaterThan(10);
    expect(c.every((q, i) => i === 0 || q.um >= c[i - 1].um)).toBe(true);
  });
  it("builds a light curve per visit", () => {
    const dets = lightCurveDets(rw.spectrum);
    expect(dets.length).toBe(6);
    const lc = lightCurve(rw.spectrum, 2);
    expect(lc.map((v) => v.visit)).toEqual([1, 2, 3]);
    const chart = renderLightCurve(rw.spectrum, 2, { width: 343, id: "l" });
    expect(chart.svg).not.toMatch(/NaN/);
    expect(chart.points.map((p) => p.v.visit)).toEqual([1, 2, 3]);
    expect(lightCurveDets(load("elpis").spectrum)).toEqual([]);
  });
});
