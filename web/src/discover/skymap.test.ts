import { describe, expect, it } from "vitest";
import type { Candidate } from "../api/types";
import { aitoff, eqToGal, galToEq, glyphPath, gratStep, latLabel, meanLon, mountSkyMap, raLabel, wrap180 } from "./skymap";

describe("projection maths", () => {
  it("wraps angles to [−180, 180)", () => {
    expect(wrap180(0)).toBe(0);
    expect(wrap180(190)).toBe(-170);
    expect(wrap180(-190)).toBe(170);
    expect(wrap180(540)).toBe(-180);
  });

  it("puts the centre at the origin and the edges of the Aitoff ellipse at ±π, ±π/2", () => {
    expect(aitoff(0, 0)).toEqual({ x: -0, y: 0 });
    const left = aitoff(179.999999, 0);
    expect(left.x).toBeCloseTo(-Math.PI, 4);
    expect(aitoff(0, 90).y).toBeCloseTo(Math.PI / 2, 9);
    expect(aitoff(0, -90).y).toBeCloseTo(-Math.PI / 2, 9);
    // On the equator Aitoff is x = 2 sin(λ/2)·α/sin α with α = λ/2, i.e. x = λ: 90° → π/2.
    expect(Math.abs(aitoff(90, 0).x)).toBeCloseTo(Math.PI / 2, 9);
  });

  it("draws the sky as seen from inside: east (larger longitude) to the left", () => {
    expect(aitoff(30, 0).x).toBeLessThan(0);
    expect(aitoff(-30, 0).x).toBeGreaterThan(0);
    expect(aitoff(10, 20).x).toBeCloseTo(-aitoff(-10, 20).x, 12);
  });

  it("recentres on a longitude", () => {
    expect(aitoff(122.5, 16, 122.5).x).toBeCloseTo(0, 12);
    expect(aitoff(123.5, 16, 122.5).x).toBeLessThan(0);
  });

  it("stays inside the ellipse (x/π)² + (2y/π)² ≤ 1", () => {
    for (let lon = -180; lon < 180; lon += 15)
      for (let lat = -90; lat <= 90; lat += 15) {
        const { x, y } = aitoff(lon, lat);
        expect((x / Math.PI) ** 2 + ((2 * y) / Math.PI) ** 2).toBeLessThanOrEqual(1 + 1e-9);
      }
  });

  it("converts ICRS to galactic: the Galactic centre and the north galactic pole", () => {
    const gc = eqToGal(266.40499, -28.93617);
    expect(Math.min(gc.l, 360 - gc.l)).toBeLessThan(0.01);
    expect(gc.b).toBeCloseTo(0, 2);
    expect(eqToGal(192.85948, 27.12825).b).toBeCloseTo(90, 3);
    // The pilot zone sits well off the plane (fixture galactic_b ≈ +25°).
    expect(eqToGal(122.55, 17.0).b).toBeCloseTo(24.85, 0);
  });

  it("round-trips galactic ↔ equatorial", () => {
    for (const [ra, dec] of [[10, 20], [122.5, 16], [269.45, 4.77], [350, -60]]) {
      const g = eqToGal(ra, dec);
      const e = galToEq(g.l, g.b);
      expect(e.ra).toBeCloseTo(ra, 8);
      expect(e.dec).toBeCloseTo(dec, 8);
    }
  });

  it("averages longitudes across 0°", () => {
    expect(meanLon([359, 1])).toBeCloseTo(0, 6);
    expect(meanLon([120, 124])).toBeCloseTo(122, 6);
  });

  it("chooses graticule steps and labels", () => {
    expect(gratStep(4)).toBe(1);
    expect(gratStep(1.5)).toBe(0.5);
    expect(gratStep(170)).toBe(30);
    expect(raLabel(123)).toBe("8h 12m");
    expect(raLabel(120)).toBe("8h");
    expect(latLabel(16, false)).toBe("+16°");
    expect(latLabel(-4.5, true)).toBe("−4°30′");
  });

  it("gives each kind its own glyph shape", () => {
    expect(glyphPath("high_pm", 5, 5, 3)).toContain("a3.0,3.0");
    expect(glyphPath("mover", 5, 5, 3).match(/L/g)).toHaveLength(2);
    expect(glyphPath("spectral", 5, 5, 3)).toContain("H");
  });
});

const cand = (id: string, ra: number, dec: number, kind: Candidate["kind"] = "high_pm", zone = "pilot"): Candidate => ({
  id, kind, ra, dec, mjd: 61000, clock: "months", det: 2, zone, score: 0.1, snr: null, flux_ujy: null, rate_arcsec_h: null,
  pm_mas_yr: 500, pm_err_mas_yr: 50, pa_deg: 0, nearest_known_arcsec: null, gaia_match: true, catwise_match: false,
  galactic_b: 25, labels: [], reasons: [], why: "Moves.", flags: [], frames_url: null,
});

describe("the map", () => {
  it("draws the filtered set by kind, the rest as context, and opens a candidate with the keyboard", () => {
    const el = document.createElement("div");
    document.body.append(el);
    const opened: string[] = [];
    const map = mountSkyMap(el, { onOpen: (id) => opened.push(id) });
    map.setData([cand("a", 122, 16), cand("b", 123, 17, "mover"), cand("c", 124, 15)]);
    map.setFiltered(["b", "a"]);
    expect(el.querySelector(".sm-pt.kind-mover")).not.toBeNull();
    expect(el.querySelector(".sm-pt.kind-high_pm")).not.toBeNull();
    expect(el.querySelector(".sm-ctx")?.getAttribute("d")).toMatch(/^M/);
    expect(el.querySelector(".skymap-legend")?.textContent).toContain("Moving stars1 of 2");
    const plot = el.querySelector<HTMLElement>(".skymap-plot")!;
    expect(plot.getAttribute("aria-label")).toContain("2 objects to check");
    plot.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight" }));
    expect(el.querySelector(".skymap-tip")?.textContent).toContain("Fast mover");
    plot.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter" }));
    expect(opened).toEqual(["b"]);
    map.destroy();
    el.remove();
  });

  it("names a mover's grade in its tip once the list has grades, and nothing before", () => {
    const tipFor = (cands: Candidate[], id: string) => {
      const el = document.createElement("div");
      document.body.append(el);
      const map = mountSkyMap(el, { onOpen: () => {} });
      map.setData(cands);
      map.setFiltered([id]);
      el.querySelector<HTMLElement>(".skymap-plot")!.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight" }));
      const tip = el.querySelector(".skymap-tip");
      const out = { grade: tip?.querySelector(".sm-tip-grade")?.textContent ?? null, live: el.querySelector("[aria-live]")?.textContent ?? "" };
      map.destroy();
      el.remove();
      return out;
    };
    const m = (id: string, grade: Candidate["grade"], reasons: string[] = []) => ({ ...cand(id, 122, 16, "mover"), grade, reasons });
    const graded = [m("b", "B"), m("c", "C", ["two_exposures", "edge", "allwise"]), m("u", null), cand("s", 123, 17)];
    expect(tipFor(graded, "b").grade).toBe("Grade B: passed every check, motion not shown yet.");
    expect(tipFor(graded, "c").grade).toBe("Grade C, failed: at the detector edge, AllWISE source here.");
    expect(tipFor(graded, "u").grade).toBe("Not vetted yet.");
    expect(tipFor(graded, "s").grade).toBeNull(); // moving stars have no grade
    expect(tipFor([m("u", null), cand("s", 123, 17)], "u").grade).toBeNull(); // an ungraded list says nothing
  });

  it("offers one close-up per zone when there are several", () => {
    const el = document.createElement("div");
    const map = mountSkyMap(el, { onOpen: () => {} });
    map.setData([cand("a", 122, 16), cand("b", 269, 5, "high_pm", "hunt"), cand("c", 269.1, 5, "high_pm", "hunt")]);
    map.setFiltered(["a", "b", "c"]);
    const zones = el.querySelector<HTMLElement>(".skymap-zones")!;
    expect(zones.hidden).toBe(false);
    expect(zones.querySelector('[aria-pressed="true"]')?.textContent).toBe("Hunt");
    expect(el.querySelector(".skymap-caption")?.textContent).toContain("hunt zone");
    zones.querySelector<HTMLButtonElement>('[data-zone="pilot"]')!.click();
    expect(el.querySelector(".skymap-caption")?.textContent).toContain("pilot zone");
    map.destroy();
  });
});
