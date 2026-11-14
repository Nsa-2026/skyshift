// Plate layers: where the derived layers can be offered, how their frames get the observations'
// marks, and the sentences under the control.
import { describe, expect, it } from "vitest";
import type { Epoch, FramesResponse, Measured } from "../api/types";
import { LAYERS, availability, isLayer, marksLine, percent, qualityLine, withMarks } from "./layers";
import { epoch, frames } from "./testkit";

const GAIA = [
  { id: "4472832130942575872", ra: 269.4486, dec: 4.6933, pmra: -801.6, pmdec: 10362.4, parallax: 546.98, g: 8.2 },
  { id: "4472832130942575873", ra: 269.45, dec: 4.77, pmra: 1, pmdec: 2, parallax: null, g: 14.1 },
];
const MEASURED = { zone: "t_barnard", stage: "final", detections: [], tracks: [], shifts: [] } as unknown as Measured;

/** The prepared files: same grid, nulls where the analysis cube keeps nothing, only the target in gaia. */
function derived(epochs: Epoch[]): FramesResponse {
  return frames(
    epochs.map((e) => ({ ...e, um_min: null, um_max: null, flag_frac: null, sat_frac: null, data_url: `views/resid/${e.data_url}` }) as unknown as Epoch),
    { center: { ra: 269.4462, dec: 4.769 }, gaia: [GAIA[0]] },
  );
}

describe("availability", () => {
  it("offers the derived layers for the five featured stars at 5′ on Months", () => {
    for (const slug of ["barnard", "epsindib", "wise0855", "rwaur", "rdra"])
      expect(availability({ slug, clock: "months", fov: 5 })).toEqual({ ok: true });
  });
  it("on another clock or field size, says so and offers Months at 5′", () => {
    for (const v of [{ clock: "hours", fov: 20 }, { clock: "months", fov: 15 }] as const) {
      const a = availability({ slug: "barnard", ...v });
      expect(a.ok).toBe(false);
      if (!a.ok) {
        expect(a.why).toMatch(/5′ on the Months clock/);
        expect(a.fix).toEqual({ clock: "months", fov: 5 });
      }
    }
  });
  it("anywhere else (coordinates, the comet, the asteroids), names the five stars and offers no switch", () => {
    for (const slug of [null, "elpis", "3i-atlas", "euphrosyne"]) {
      const a = availability({ slug, clock: "months", fov: 5 });
      expect(a.ok).toBe(false);
      if (!a.ok) {
        expect(a.fix).toBeNull();
        expect(a.why).toContain("Barnard's Star");
        expect(a.why).toContain("Pick one of them");
      }
    }
  });
});

describe("withMarks", () => {
  const obs = [epoch("d2-v1", 2, 60913.3286), epoch("d2-v2", 2, 61117.63), epoch("d6-v1", 6, 60908.675, { um_min: 4.4, um_max: 5.0 })];

  it("draws the observations' Gaia stars, known objects and measurements when the visits are the same", () => {
    const o = frames(obs, {
      gaia: GAIA,
      known: [{ name: "(59) Elpis", epoch_id: "d2-v1", ra: 1, dec: 2, v: 12, source: "mpc" }],
      measured: MEASURED,
      measured_reason: null,
    });
    const m = withMarks(derived(obs), o);
    expect(m.dropped).toBe(false);
    expect(m.frames.gaia).toBe(o.gaia);
    expect(m.frames.known).toEqual(o.known);
    expect(m.frames.measured).toBe(MEASURED);
    // The grid and the data stay the derived layer's own.
    expect(m.frames.center).toEqual({ ra: 269.4462, dec: 4.769 });
    expect(m.frames.epochs[0].data_url).toBe("views/resid/frames/d2-v1.bin");
    // Wavelengths from the same visit; quality fractions stay unknown (no flags in the cube).
    expect(m.frames.epochs[2].um_min).toBe(4.4);
    expect(m.frames.epochs[0].flag_frac).toBeNull();
  });

  it("leaves measurements and known objects out when the layer splits the visits differently (R Dra)", () => {
    const o = frames(obs, { gaia: GAIA, measured: MEASURED, measured_reason: null, known: [{ name: "x", epoch_id: "d2-v1", ra: 0, dec: 0, v: null, source: "mpc" }] });
    const other = [epoch("d2-v2", 2, 60838.12), epoch("d2-v3", 2, 60855.24), epoch("d6-v8", 6, 61008.81)];
    const m = withMarks(derived(other), o);
    expect(m.dropped).toBe(true);
    expect("measured" in m.frames).toBe(false);
    expect("measured_reason" in m.frames).toBe(false);
    expect(m.frames.known).toEqual([]);
    // Gaia stars are sky positions: still drawn.
    expect(m.frames.gaia).toBe(o.gaia);
    // No twin: the detector's nominal range instead of null (the combined label would read 0.00 µm).
    expect(m.frames.epochs[0].um_min).toBe(1.1);
    expect(m.frames.epochs[2].um_max).toBe(5.0);
  });

  it("a visit with the same id but another time is not the same visit", () => {
    const o = frames(obs, { measured: MEASURED });
    const shifted = [epoch("d2-v1", 2, 60913.3286 + 2), obs[1], obs[2]];
    expect(withMarks(derived(shifted), o).dropped).toBe(true);
  });

  it("says nothing was dropped when the observations had no measurements", () => {
    const o = frames(obs, { measured: null, measured_reason: "not_processed" });
    const m = withMarks(derived(obs), o);
    expect(m.dropped).toBe(false);
    expect(m.frames.measured).toBeNull();
    expect(m.frames.measured_reason).toBe("not_processed");
    expect(withMarks(derived([obs[0]]), frames(obs)).dropped).toBe(false);
  });

  it("keeps the layer's own Gaia list when the observations have none", () => {
    expect(withMarks(derived(obs), frames(obs)).frames.gaia).toEqual([GAIA[0]]);
    expect(withMarks(derived(obs), null).frames.gaia).toEqual([GAIA[0]]);
  });
});

describe("words", () => {
  it("has one sentence for each derived layer and none for Observed", () => {
    expect(LAYERS.map((l) => l.label)).toEqual(["Observed", "Sky model", "Residual", "DR1 difference"]);
    expect(LAYERS[0].says).toBe("");
    for (const l of LAYERS.slice(1)) {
      expect(l.says).toMatch(/^[A-Z].*\.$/);
      expect(l.says.split(". ").length).toBe(1);
      expect(l.says).not.toMatch(/ · /);
    }
    expect(isLayer("resid")).toBe(true);
    expect(isLayer("diff")).toBe(false);
    expect(isLayer(undefined)).toBe(false);
  });

  it("writes shares as short percentages", () => {
    expect(percent(0)).toBe("0%");
    expect(percent(0.1545)).toBe("15%");
    expect(percent(0.025)).toBe("2.5%");
    expect(percent(0.0023)).toBe("0.23%");
    expect(percent(0.00002)).toBe("under 0.01%");
  });

  it("says the shown frame's quality in a sentence, or nothing when it isn't recorded", () => {
    expect(qualityLine(epoch("d2-v1", 2, 1), "raw")).toBe(
      "In D2, this visit has 2.5% of its pixels masked and 0.23% saturated, counted over the whole frame.",
    );
    expect(qualityLine(epoch("d2-x", 2, 1, { kind: "exposure", flag_frac: 0.0093, sat_frac: 0.0005 }), "raw")).toMatch(
      /^In D2, this exposure has 0.93% of its pixels masked and 0.05% saturated/,
    );
    expect(qualityLine(epoch("d2-v1", 2, 1, { flag_frac: null as unknown as number }), "raw")).toBe("");
    expect(qualityLine(null, "raw")).toBe("");
  });

  it("on a derived layer, warns that it has no flags and covers only the middle 4′", () => {
    const q = qualityLine(epoch("d2-v1", 2, 1), "resid");
    expect(q).toContain("middle 4′");
    expect(q).toContain("without SPHEREx's flags");
    expect(q).toContain("Observed");
  });

  it("explains the rings on derived layers, and says when SkyShift's measurements are left out", () => {
    expect(marksLine("raw", false)).toBe("");
    expect(marksLine("model", false)).toBe("Blue rings still mark Gaia's stars.");
    expect(marksLine("resid", false)).toMatch(/leftover on a ring is a star that moved/);
    expect(marksLine("refdiff", true)).toMatch(/measurements aren't drawn/);
  });
});
