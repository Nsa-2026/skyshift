import { describe, expect, it } from "vitest";
import type { Measure, SpectrumPoint } from "../api/types";
import {
  agreement,
  compactUjy,
  detectionLimit,
  distancePc,
  isDetectedMotion,
  isUpperLimit,
  linearTicks,
  listDets,
  logTicks,
  noMeasureReason,
  paVector,
  paWord,
  pcToLy,
  pmPa,
  saturation,
  sigmaDiff,
  upperLimitValue,
  visitCountFromFrames,
} from "./format";

const barnard: Measure = {
  shift_arcsec: 10.8336,
  pa_deg: 357.7703,
  dt_yr: 0.986,
  rate_arcsec_yr: 10.987,
  sigma_arcsec: 0.3637,
  n_ref_stars: 344,
  predicted_arcsec: 10.2663,
};

const pt = (o: Partial<SpectrumPoint>): SpectrumPoint => ({
  um: 1,
  flux_ujy: 1000,
  err_ujy: 10,
  det: 1,
  mjd: 61000,
  visit: 1,
  saturated: false,
  ...o,
});

describe("distance", () => {
  it("is 1000 / parallax in parsecs", () => {
    expect(distancePc(546.976)).toBeCloseTo(1.8282, 3); // Barnard's Star
    expect(pcToLy(distancePc(546.976)!)).toBeCloseTo(5.963, 2);
  });
  it("is null without a usable parallax", () => {
    expect(distancePc(null)).toBeNull();
    expect(distancePc(undefined)).toBeNull();
    expect(distancePc(0)).toBeNull();
    expect(distancePc(-0.3)).toBeNull();
  });
});

describe("measure", () => {
  it("gives the signed difference from the prediction in σ", () => {
    expect(sigmaDiff(barnard)).toBeCloseTo((10.8336 - 10.2663) / 0.3637, 6);
    expect(agreement(sigmaDiff(barnard)!)).toBe("agrees");
    expect(sigmaDiff({ ...barnard, predicted_arcsec: 12 })!).toBeLessThan(0);
    expect(agreement(-2.5)).toBe("marginal");
    expect(agreement(4)).toBe("disagrees");
  });
  it("is null without a prediction or σ", () => {
    expect(sigmaDiff({ ...barnard, predicted_arcsec: null })).toBeNull();
    expect(sigmaDiff({ ...barnard, sigma_arcsec: 0 })).toBeNull();
  });
  it("uses 3σ as the detection limit", () => {
    expect(detectionLimit(barnard)).toBeCloseTo(1.0911, 4);
    expect(isDetectedMotion(barnard)).toBe(true);
    expect(isDetectedMotion({ ...barnard, shift_arcsec: 0.0427, sigma_arcsec: 0.3555 })).toBe(false);
  });
  it("points north up, east left", () => {
    expect(paVector(0).dy).toBeCloseTo(-1);
    expect(paVector(90).dx).toBeCloseTo(-1); // east is left
    expect(paVector(270).dx).toBeCloseTo(1);
    expect(paWord(357.8)).toBe("north");
    expect(paWord(278)).toBe("west");
    expect(pmPa({ id: "1", ra: 0, dec: 0, pmra: -801.551, pmdec: 10362.394, parallax: 1, g: 1 })).toBeCloseTo(355.58, 1);
    expect(pmPa(null)).toBeNull();
  });
});

describe("log axis ticks", () => {
  it("uses decades over a wide range", () => {
    const t = logTicks(80, 2_000_000);
    expect(t.ticks).toEqual([100, 1000, 10000, 100000, 1000000]);
    expect(t.min).toBe(80);
  });
  it("adds 2 and 5 inside a narrow range", () => {
    const t = logTicks(120, 900);
    expect(t.ticks).toEqual([200, 500]);
  });
  it("adds 3 at one to two decades", () => {
    expect(logTicks(9000, 400000).ticks).toEqual([10000, 30000, 100000, 300000]);
  });
  it("widens to decades when no tick would fall inside", () => {
    const t = logTicks(1010, 1090);
    expect(t.ticks.length).toBeGreaterThanOrEqual(2);
    expect(t.min).toBeLessThanOrEqual(1010);
    expect(t.max).toBeGreaterThanOrEqual(1090);
  });
  it("survives bad input", () => {
    expect(logTicks(0, 10).ticks).toEqual([1, 10]);
    expect(logTicks(NaN, NaN).ticks).toEqual([1, 10]);
  });
  it("labels compactly", () => {
    expect(compactUjy(1000)).toBe("1k");
    expect(compactUjy(30000)).toBe("30k");
    expect(compactUjy(2_000_000)).toBe("2M");
    expect(compactUjy(500)).toBe("500");
  });
  it("makes nice linear ticks", () => {
    const t = linearTicks(31604, 140000);
    expect(t.min).toBeLessThanOrEqual(31604);
    expect(t.max).toBeGreaterThanOrEqual(140000);
    expect(t.ticks.length).toBeGreaterThanOrEqual(3);
  });
});

describe("saturated and undetected points", () => {
  it("lists saturated detectors", () => {
    const s = saturation([pt({ det: 3, saturated: true }), pt({ det: 1, saturated: true }), pt({ det: 2 })]);
    expect(s).toEqual({ dets: [1, 3], n: 2, total: 3 });
    expect(listDets(s!.dets)).toBe("D1 and D3");
    expect(listDets([1, 3, 5, 6])).toBe("D1, D3, D5 and D6");
    expect(saturation([pt({})])).toBeNull();
    expect(saturation([])).toBeNull();
  });
  it("draws points under 2σ as upper limits", () => {
    expect(isUpperLimit(pt({ flux_ujy: -82, err_ujy: 74 }))).toBe(true);
    expect(upperLimitValue(pt({ flux_ujy: -82, err_ujy: 74 }))).toBe(148);
    expect(isUpperLimit(pt({ flux_ujy: 100, err_ujy: 60 }))).toBe(true);
    expect(upperLimitValue(pt({ flux_ujy: 100, err_ujy: 60 }))).toBe(220);
    expect(isUpperLimit(pt({ flux_ujy: 1000, err_ujy: 60 }))).toBe(false);
  });
});

describe("why there is no measure", () => {
  it("explains movers, single visits and empty spots", () => {
    expect(noMeasureReason({ object: null, kind: "asteroid", visits: null })).toMatch(/hours/);
    expect(noMeasureReason({ object: null, kind: null, visits: 1 })).toMatch(/one visit/);
    expect(noMeasureReason({ object: null, kind: null, visits: null })).toMatch(/Nothing has been measured/);
  });
  it("counts visits per detector in frames near the view", () => {
    const frames = {
      clock: "months",
      center: { ra: 10, dec: 10 },
      epochs: [
        { id: "d2-v1", det: 2, kind: "visit" },
        { id: "d5-v1", det: 5, kind: "visit" },
      ],
    } as never;
    expect(visitCountFromFrames(frames, { ra: 10, dec: 10 }, 5)).toBe(1);
    expect(visitCountFromFrames(frames, { ra: 20, dec: 10 }, 5)).toBeNull();
    expect(visitCountFromFrames(null, { ra: 10, dec: 10 }, 5)).toBeNull();
  });
});
