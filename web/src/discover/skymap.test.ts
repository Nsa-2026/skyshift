import { describe, expect, it, vi } from "vitest";
import type { Candidate } from "../api/types";
import { aitoff, eqToGal, galToEq, glyphPath, gratStep, latLabel, meanLon, mountSkyMap, raLabel, wrap180, zoneLabel } from "./skymap";

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

  it("offers one close-up per zone when there are several, in a 'Search zone' menu", () => {
    const el = document.createElement("div");
    const map = mountSkyMap(el, { onOpen: () => {} });
    map.setData([cand("a", 122, 16), cand("b", 269, 5, "high_pm", "hunt"), cand("c", 269.1, 5, "high_pm", "hunt")]);
    map.setFiltered(["a", "b", "c"]);
    const zones = el.querySelector<HTMLElement>(".skymap-zones")!;
    expect(zones.hidden).toBe(false);
    expect(zones.querySelector("span")?.textContent).toBe("Search zone");
    expect(zones.title).toBe("The sky patches we searched: 10° boxes along the ecliptic, numbered from RA 0° (n1 is the box north of it).");
    const select = zones.querySelector<HTMLSelectElement>("select")!;
    expect([...select.options].map((o) => o.textContent)).toEqual(["Hunt", "Pilot"]);
    expect(select.value).toBe("hunt");
    expect(el.querySelector(".skymap-caption")?.textContent).toContain("Search zone Hunt:");
    select.value = "pilot";
    select.dispatchEvent(new Event("change"));
    expect(el.querySelector(".skymap-caption")?.textContent).toContain("Search zone Pilot:");
    map.destroy();
  });

  it("opens fullscreen only on request, and Escape restores the map, selection and keyboard focus", () => {
    const el = document.createElement("div");
    document.body.append(el);
    const opened = vi.fn();
    const map = mountSkyMap(el, { onOpen: opened });
    map.setData([cand("a", 122, 16), cand("b", 269, 5, "mover", "hunt")]);
    map.setFiltered(["a"]);
    const select = el.querySelector<HTMLSelectElement>(".skymap-zones select")!;
    select.value = "hunt";
    select.dispatchEvent(new Event("change"));
    el.querySelector<HTMLButtonElement>('[data-frame="gal"]')!.click();
    const button = el.querySelector<HTMLButtonElement>("[data-fullscreen]")!;
    expect(document.querySelector(".skymap-dialog")).toBeNull();

    // jsdom has no top-layer implementation; exercise the same cancel event a browser emits.
    const proto = HTMLDialogElement.prototype;
    const show = Object.getOwnPropertyDescriptor(proto, "showModal");
    const close = Object.getOwnPropertyDescriptor(proto, "close");
    Object.defineProperty(proto, "showModal", { configurable: true, value(this: HTMLDialogElement) { this.open = true; } });
    Object.defineProperty(proto, "close", { configurable: true, value(this: HTMLDialogElement) { this.open = false; } });
    try {
      button.click();
      const dialog = document.querySelector<HTMLDialogElement>(".skymap-dialog")!;
      expect(dialog.open).toBe(true);
      expect(button.textContent).toBe("Exit full screen");
      expect(dialog.contains(el)).toBe(true);
      dialog.dispatchEvent(new Event("cancel", { cancelable: true }));
      expect(document.querySelector(".skymap-dialog")).toBeNull();
      expect(el.parentElement).toBe(document.body);
      expect(document.activeElement).toBe(button);
      expect(button.textContent).toBe("Full screen");
      expect(document.body.classList.contains("skymap-fullscreen-open")).toBe(false);
      expect(select.value).toBe("hunt");
      expect(el.querySelector('[data-frame="gal"]')?.getAttribute("aria-pressed")).toBe("true");
      const plot = el.querySelector<HTMLElement>(".skymap-plot")!;
      plot.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight" }));
      plot.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter" }));
      expect(opened).toHaveBeenCalledWith("a");

      button.click();
      plot.focus();
      plot.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight" }));
      plot.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter" }));
      expect(opened).toHaveBeenLastCalledWith("a");
      expect(document.querySelector(".skymap-dialog")).toBeNull();

      button.click();
      map.closeFullscreen();
      expect(document.querySelector(".skymap-dialog")).toBeNull();
      expect(el.parentElement).toBe(document.body);
      button.click();
      map.destroy();
      expect(document.querySelector(".skymap-dialog")).toBeNull();
      expect(document.body.classList.contains("skymap-fullscreen-open")).toBe(false);
    } finally {
      map.destroy();
      el.remove();
      if (show) Object.defineProperty(proto, "showModal", show);
      else Reflect.deleteProperty(proto, "showModal");
      if (close) Object.defineProperty(proto, "close", close);
      else Reflect.deleteProperty(proto, "close");
    }
  });

  it("names the ecliptic zones plainly and leaves a long run of them unringed on the whole sky", () => {
    expect(zoneLabel("ecl12n1")).toBe("Ecl12 n1");
    expect(zoneLabel("ecl03s2")).toBe("Ecl03 s2");
    expect(zoneLabel("ecl00")).toBe("Ecl00");
    expect(zoneLabel("hunt")).toBe("Hunt");
    const el = document.createElement("div");
    const map = mountSkyMap(el, { onOpen: () => {}, zoom: "sky" });
    const zones = ["ecl00", "ecl01", "ecl02", "ecl03", "ecl04", "ecl05", "ecl06", "ecl12n1"];
    map.setData(zones.map((z, i) => cand(`c${i}`, 5 + 10 * i, 2, "high_pm", z)));
    map.setFiltered(zones.map((_, i) => `c${i}`));
    expect(el.querySelectorAll(".sm-zone")).toHaveLength(0);
    expect(el.querySelectorAll(".sm-pt")).toHaveLength(8);
    el.querySelector<HTMLButtonElement>('[data-zoom="fit"]')!.click();
    expect([...el.querySelectorAll(".skymap-zones option")].map((b) => b.textContent)).toContain("Ecl12 n1");
    map.destroy();
  });
});
