import { readFileSync, readdirSync, existsSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import type { ObjectResponse } from "../api/types";
import type { View } from "../app/modules";
import { targetBySlug, viewForTarget } from "../app/state";
import { mountCard } from "./index";
import { lightCurve, lightCurveDets, renderLightCurve } from "./lightcurve";
import { moleculeRows, renderSpectrum, spectrumDomain, visitCurve } from "./spectrum";

const dataDir = join(__dirname, "../../public/data");
const slugs = readdirSync(dataDir).filter((s) => existsSync(join(dataDir, s, "object.json")));
const load = (slug: string): ObjectResponse => JSON.parse(readFileSync(join(dataDir, slug, "object.json"), "utf8"));

const spot: View = { ra: 10, dec: 10, clock: "months", fov: 5, det: 2, slug: null, name: null };

function viewFor(slug: string): View {
  const t = targetBySlug(slug);
  return t ? viewForTarget(t) : { ...spot, slug: null };
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
      expect(el.textContent).not.toMatch(/NaN|undefined|Infinity/);
      if (obj.measure) expect(el.querySelector(".cd-big-n")?.textContent).toBe(`${obj.measure.shift_arcsec.toFixed(2)}″`);
      else expect(el.querySelector(".cd-measure .cd-none")).not.toBeNull();
      if (obj.spectrum.length) {
        expect(el.querySelector(".cd-spec svg title")).not.toBeNull();
        expect(el.querySelector(".cd-spec table")).not.toBeNull();
        const byVisit = el.querySelector<HTMLButtonElement>('.cd-seg-b[data-mode="visit"]');
        byVisit?.click();
        expect(el.textContent).not.toMatch(/NaN|undefined|Infinity/);
        if (byVisit) expect(el.querySelector('.cd-seg-b[data-mode="visit"]')?.getAttribute("aria-pressed")).toBe("true");
        el.querySelectorAll<HTMLButtonElement>(".cd-det:not(:disabled)").forEach((b) => b.click());
        expect(el.innerHTML).not.toMatch(/NaN/);
      }
    });
  }

  it("names the saturated detectors for Barnard's Star", () => {
    const el = document.createElement("section");
    mountCard(el).show(viewFor("barnard"), load("barnard"), null);
    expect(el.querySelector(".cd-limit-sat")?.textContent).toMatch(/Core saturated in D1, D3, D5 and D6/);
    expect(el.textContent).toMatch(/1\.83 pc/);
    expect(el.textContent).toMatch(/Gaia predicts 10\.27″/);
    expect(el.querySelectorAll(".cd-spec circle[fill='var(--plate-2)']").length).toBeGreaterThan(50); // hollow
  });

  it("says R Draconis stays put and has no spectrum yet", () => {
    const el = document.createElement("section");
    mountCard(el).show(viewFor("rdra"), load("rdra"), null);
    expect(el.querySelector(".cd-measure .cd-h")?.textContent).toBe("No shift between visits");
    expect(el.querySelector(".cd-spec .cd-none")).not.toBeNull();
  });

  it("explains why a comet has no shift", () => {
    const el = document.createElement("section");
    mountCard(el).show(viewFor("3i-atlas"), load("3i-atlas"), null);
    expect(el.querySelector(".cd-measure .cd-none")?.textContent).toMatch(/moves in hours/);
  });
});

describe("states", () => {
  it("identifies measurement inputs independently of the selected detector and clock", () => {
    const obj = load("barnard");
    const m = obj.measure!;
    m.source = {
      det: 2, visits: [1, 3], mjd: [61000, 61000 + m.dt_yr * 365.25],
      n_exposures: [19, 20], fov: 15, pixel_arcsec: 6.15, core_filled: [false, true],
    };
    const el = document.createElement("section");
    const card = mountCard(el);
    const v = { ...viewFor("barnard"), det: 6 as const };
    card.show(v, obj, null);
    expect(el.querySelector(".cd-measure-source")?.textContent).toContain("player shows D6");
    expect(el.querySelector(".cd-measure-source")?.textContent).toContain("measurement uses D2");
    expect(el.textContent).toContain("D2, visits 1 and 3");
    expect(el.textContent).toContain("19 and 20 exposures");
    expect(el.textContent).toContain("15′ at 6.15″/pixel");
    card.show({ ...v, clock: "hours", fov: 20 }, obj, null);
    expect(el.querySelector(".cd-measure-source")?.textContent).toContain("individual exposures");
    expect(el.querySelector(".cd-big-n")?.textContent).toBe(`${m.shift_arcsec.toFixed(2)}″`);
  });

  it("does not infer the source of legacy measurements from the selected view", () => {
    const el = document.createElement("section");
    mountCard(el).show(viewFor("barnard"), load("barnard"), null);
    expect(el.querySelector(".cd-measure-source")?.textContent).toContain("no verified detector or visit source");
    expect(el.querySelector(".cd-big-n")?.textContent).toBe("10.83″");
  });

  it("shows an empty spot with a generic hint", () => {
    const el = document.createElement("section");
    mountCard(el).show(spot, null, null);
    expect(el.querySelector(".cd-name")?.textContent).toBe("This spot");
    expect(el.textContent).toMatch(/D1\+D4, D2\+D5 and D3\+D6/);
    expect(el.textContent).toMatch(/Nothing has been measured/);
  });

  it("shows an error with an instruction", () => {
    const el = document.createElement("section");
    const card = mountCard(el);
    card.show(spot, null, null);
    card.status("error", "NASA's archive didn't answer.");
    expect(el.querySelector("[role=alert]")?.textContent).toMatch(/archive didn't answer/);
    expect(el.textContent).toMatch(/Pick a featured target/);
  });

  it("does not show the previous object while the next one loads", () => {
    const el = document.createElement("section");
    const card = mountCard(el);
    const barnard = load("barnard");
    card.show(viewFor("barnard"), barnard, null);
    card.status("loading");
    card.show(viewFor("rdra"), barnard, null); // frames arrived first, object is stale
    expect(el.querySelector(".cd-big-n")).toBeNull();
    expect(el.getAttribute("aria-busy")).toBe("true");
    card.show(viewFor("rdra"), load("rdra"), null);
    expect(el.querySelector(".cd-name")?.textContent).toBe("R Draconis");
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
    expect(d.min).toBeLessThanOrEqual(31604.8);
    expect(d.max).toBeGreaterThanOrEqual(1449474);
  });
  it("draws one point per spectrum point", () => {
    const c = renderSpectrum(rw.spectrum, { width: 360, mode: "det", id: "t" });
    expect(c.points.length).toBe(rw.spectrum.length);
    for (const p of c.points) {
      expect(p.x).toBeGreaterThanOrEqual(c.box.left);
      expect(p.x).toBeLessThanOrEqual(c.box.right);
      expect(p.y).toBeGreaterThanOrEqual(c.box.top - 0.01);
      expect(p.y).toBeLessThanOrEqual(c.box.bottom + 0.01);
    }
  });
  it("keeps molecule labels apart", () => {
    const rows = moleculeRows((um) => 38 + ((um - 0.7) / 4.35) * 300);
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
    expect(renderLightCurve(rw.spectrum, 2, { width: 360, id: "l" })).not.toMatch(/NaN/);
    expect(lightCurveDets(load("elpis").spectrum)).toEqual([]);
  });
});
