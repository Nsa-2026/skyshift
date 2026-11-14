import { describe, expect, it } from "vitest";
import type { Measure, SpectrumPoint } from "../api/types";
import {
  aboutSentence,
  agreement,
  compactUjy,
  framesFollow,
  measureSentence,
  proseLy,
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
  it("never draws a saturated point as an upper limit (its error is the ±50% wing estimate)", () => {
    expect(isUpperLimit(pt({ flux_ujy: 13e6, err_ujy: 6.6e6, saturated: true }))).toBe(false);
    expect(isUpperLimit(pt({ flux_ujy: 13e6, err_ujy: 6.6e6 }))).toBe(true);
    expect(isUpperLimit(pt({ flux_ujy: 0, err_ujy: 10, saturated: true }))).toBe(true);
  });
});

describe("sentences", () => {
  it("rounds light-years for prose", () => {
    expect(proseLy(5.963)).toBe("6");
    expect(proseLy(12.05)).toBe("12");
    expect(proseLy(509.1)).toBe("510");
    expect(proseLy(2607)).toBe("2,600");
    expect(proseLy(1.2)).toBe("1.2");
    expect(proseLy(NaN)).toBe("");
  });
  it("says what a featured target is, with Gaia's distance", () => {
    const gaia = { id: "1", ra: 0, dec: 0, pmra: 0, pmdec: 0, parallax: 546.976, g: 8 };
    const obj = { name: "x", gaia, known_ucd: null, spectrum: [], measure: null };
    expect(aboutSentence({ slug: "barnard", kind: "star", object: obj, where: "" })).toBe(
      "A red dwarf 6 light-years away, the fastest-moving star in our sky.",
    );
    expect(aboutSentence({ slug: "barnard", kind: "star", object: null, where: "" })).toBe("A red dwarf, the fastest-moving star in our sky.");
    expect(aboutSentence({ slug: null, kind: "star in Gaia DR3", object: obj, where: "" })).toBe("A star in Gaia DR3, 6 light-years away.");
    expect(aboutSentence({ slug: null, kind: "ultracool dwarf", object: { ...obj, known_ucd: { name: "u", spt: "T8", ref: "r" } }, where: "" })).toBe(
      "An ultracool dwarf of spectral type T8, 6 light-years away.",
    );
    expect(aboutSentence({ slug: null, kind: null, object: null, where: "00h40m00.0s +10°00′00″" })).toBe("The sky at 00h40m00.0s +10°00′00″.");
  });
  it("words the measurement against the prediction", () => {
    expect(measureSentence(barnard, { visits: 3, who: "Gaia" })).toBe(
      "moved between visits 1 and 3 (0.99 years). Gaia predicts 10.27″: they agree within 1.6σ.",
    );
    expect(measureSentence({ ...barnard, predicted_arcsec: 9.9 }, { visits: null, who: "Gaia" })).toMatch(
      /^moved between the first and last visits .* 2\.6σ apart, close but not a clean match\.$/,
    );
    expect(measureSentence({ ...barnard, predicted_arcsec: 12 }, { visits: 4, who: "Gaia" })).toMatch(/3\.2σ apart and disagree\.$/);
    expect(measureSentence({ ...barnard, predicted_arcsec: null }, { visits: 3, who: "Gaia" })).toMatch(/no catalogue prediction/);
    const still = measureSentence({ ...barnard, shift_arcsec: 0.0427, sigma_arcsec: 0.3555, predicted_arcsec: 0.0214, dt_yr: 1 }, { visits: 3, who: "Gaia" });
    expect(still).toBe("between visits 1 and 3 (1 year): below the 1.07″ we can see (3σ), so it stays put. Gaia predicts 0.02″: they agree within 0.1σ.");
  });
  it("knows whether the frames follow the object", () => {
    expect(framesFollow(null)).toBeNull();
    expect(framesFollow({ epochs: [{ center: { ra: 1, dec: 1 } }] } as never)).toBe(true);
    expect(framesFollow({ epochs: [{}] } as never)).toBe(false);
  });
});

describe("why there is no measure", () => {
  it("explains movers, single visits and empty spots", () => {
    expect(noMeasureReason({ object: null, kind: "asteroid", visits: null })).toMatch(/hours/);
    expect(noMeasureReason({ object: null, kind: "asteroid", visits: null, follows: false })).toMatch(/crosses the field.*violet diamond/);
    expect(noMeasureReason({ object: null, kind: "interstellar comet", visits: null, follows: true })).toMatch(/frames follow it/);
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
