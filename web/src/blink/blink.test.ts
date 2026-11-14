import { describe, expect, it } from "vitest";
import { epochsFor, toMJySr } from "../api/client";
import type { Epoch, FramesResponse } from "../api/types";
import { buildAxis, formatGap, nearestIndex } from "./axis";
import {
  contrastToPercentiles,
  mean,
  median,
  niceArcsec,
  percentileSorted,
  robustLimits,
  sampleFinite,
  stretch,
  stretchSigned,
  subtract,
  symmetricLimits,
} from "./math";
import { buildLut, diffIndex, paint, plainIndex, type Palette } from "./paint";

const f32 = (a: number[]) => Float32Array.from(a);

describe("uint16 decode", () => {
  it("applies raw × scale + zero and turns raw 0 into no data", () => {
    const v = toMJySr(Uint16Array.from([0, 1, 1000, 65535]), { scale: 0.001, zero: -0.5 });
    expect(Number.isNaN(v[0])).toBe(true);
    expect(v[1]).toBeCloseTo(-0.499, 6);
    expect(v[2]).toBeCloseTo(0.5, 6);
    expect(v[3]).toBeCloseTo(65.035, 3);
  });
  it("decodes little-endian bytes as the frame files hold them", () => {
    const bytes = new Uint8Array([0x00, 0x00, 0x34, 0x12]);
    const raw = new Uint16Array(bytes.buffer);
    expect(Array.from(raw)).toEqual([0, 0x1234]);
  });
});

describe("percentiles and limits", () => {
  it("interpolates percentiles on sorted data", () => {
    const s = f32([0, 10, 20, 30, 40]);
    expect(percentileSorted(s, 0)).toBe(0);
    expect(percentileSorted(s, 50)).toBe(20);
    expect(percentileSorted(s, 100)).toBe(40);
    expect(percentileSorted(s, 62.5)).toBeCloseTo(25);
    expect(Number.isNaN(percentileSorted(f32([]), 50))).toBe(true);
  });
  it("samples only finite values", () => {
    const s = sampleFinite([f32([1, NaN, 3]), f32([NaN, 5])]);
    expect(Array.from(s)).toEqual([1, 3, 5]);
  });
  it("median ignores no-data pixels", () => {
    expect(median(f32([NaN, 1, 2, 3, NaN]))).toBe(2);
    expect(median(f32([NaN, NaN]))).toBe(0);
  });
  it("maps contrast to a widening-to-clipping percentile pair", () => {
    const [lo0, hi0] = contrastToPercentiles(0);
    const [lo1, hi1] = contrastToPercentiles(1);
    expect(lo0).toBeCloseTo(0.5);
    expect(hi0).toBeCloseTo(99.95);
    expect(lo1).toBeCloseTo(10);
    expect(hi1).toBeCloseTo(90, 1);
    expect(contrastToPercentiles(0.5)[1]).toBeLessThan(hi0);
  });
  it("shares limits across every frame of a detector", () => {
    const dim = f32(Array.from({ length: 100 }, (_, i) => i / 100)); // 0 … 0.99
    const bright = f32(Array.from({ length: 100 }, (_, i) => 10 + i / 100));
    const both = robustLimits([dim, bright], 0);
    expect(both.lo).toBeLessThan(0.1);
    expect(both.hi).toBeGreaterThan(10.5);
    // Per-frame limits would hide the brightness change; shared ones keep it.
    expect(stretch(dim[50], both, "linear")).toBeLessThan(0.1);
    expect(stretch(bright[50], both, "linear")).toBeGreaterThan(0.9);
  });
  it("never returns an empty range", () => {
    const flat = robustLimits([f32([2, 2, 2, 2])], 0.5);
    expect(flat.hi).toBeGreaterThan(flat.lo);
    expect(robustLimits([f32([NaN, NaN])], 0.5)).toEqual({ lo: 0, hi: 1 });
  });
  it("gives symmetric limits for differences", () => {
    const l = symmetricLimits([f32([-4, -1, 0, 1, 2])], 0);
    expect(l.lo).toBeCloseTo(-l.hi);
    expect(l.hi).toBeGreaterThan(3.9);
  });
});

describe("stretch", () => {
  const lim = { lo: 0, hi: 10 };
  it("linear maps lo → 0, hi → 1 and clips", () => {
    expect(stretch(0, lim, "linear")).toBe(0);
    expect(stretch(5, lim, "linear")).toBeCloseTo(0.5);
    expect(stretch(20, lim, "linear")).toBe(1);
    expect(stretch(-3, lim, "linear")).toBe(0);
    expect(Number.isNaN(stretch(NaN, lim, "linear"))).toBe(true);
  });
  it("asinh lifts faint values but keeps the ends", () => {
    expect(stretch(0, lim, "asinh")).toBe(0);
    expect(stretch(10, lim, "asinh")).toBeCloseTo(1);
    expect(stretch(1, lim, "asinh")).toBeGreaterThan(stretch(1, lim, "linear"));
  });
  it("signed stretch keeps zero at zero and the sign", () => {
    const d = { lo: -2, hi: 2 };
    expect(stretchSigned(0, d, "linear")).toBe(0);
    expect(stretchSigned(-1, d, "linear")).toBeCloseTo(-0.5);
    expect(stretchSigned(5, d, "asinh")).toBe(1);
  });
});

describe("subtract and add", () => {
  it("subtracts per pixel and keeps no data as no data", () => {
    const d = subtract(f32([5, 3, NaN, 1]), f32([1, 3, 2, NaN]));
    expect(d[0]).toBe(4);
    expect(d[1]).toBe(0);
    expect(Number.isNaN(d[2])).toBe(true);
    expect(Number.isNaN(d[3])).toBe(true);
  });
  it("means frames, skipping no-data pixels", () => {
    const m = mean([f32([1, NaN, NaN]), f32([3, 4, NaN]), f32([5, 8, NaN])]);
    expect(m[0]).toBe(3);
    expect(m[1]).toBe(6);
    expect(Number.isNaN(m[2])).toBe(true);
  });
});

describe("paint", () => {
  const p: Palette = {
    glass: [240, 240, 240],
    ink: [20, 30, 40],
    arrived: [200, 30, 50],
    left: [30, 80, 200],
  };
  const lin = { lo: 0, hi: 10 };
  it("is a negative by default: sky on glass, bright stars in ink", () => {
    const out = new Uint8ClampedArray(4 * 2);
    paint(f32([0, 10]), out, buildLut(p, false, false), plainIndex(lin, "linear"));
    expect(Array.from(out.slice(0, 4))).toEqual([240, 240, 240, 255]);
    expect(Array.from(out.slice(4, 8))).toEqual([20, 30, 40, 255]);
  });
  it("flips to a positive on request", () => {
    const out = new Uint8ClampedArray(4 * 2);
    paint(f32([0, 10]), out, buildLut(p, false, true), plainIndex(lin, "linear"));
    expect(Array.from(out.slice(0, 3))).toEqual([20, 30, 40]);
    expect(Array.from(out.slice(4, 7))).toEqual([240, 240, 240]);
  });
  it("leaves no data transparent (the hatch shows through) and burns saturated cores to ink", () => {
    const out = new Uint8ClampedArray(4 * 3);
    paint(f32([NaN, NaN, 5]), out, buildLut(p, false, false), plainIndex(lin, "linear"), Uint8Array.from([0, 1, 0]));
    expect(out[3]).toBe(0);
    expect(Array.from(out.slice(4, 8))).toEqual([20, 30, 40, 255]);
    expect(out[11]).toBe(255);
  });
  it("paints differences on glass: blue for light that left, red for light that arrived", () => {
    const out = new Uint8ClampedArray(4 * 3);
    paint(f32([-10, 0, 10]), out, buildLut(p, true, false), diffIndex({ lo: -10, hi: 10 }));
    expect(Array.from(out.slice(0, 3))).toEqual([30, 80, 200]);
    expect(Array.from(out.slice(4, 7))).toEqual([240, 240, 240]);
    expect(Array.from(out.slice(8, 11))).toEqual([200, 30, 50]);
    // A positive plate does not change the difference map.
    expect(Array.from(buildLut(p, true, true))).toEqual(Array.from(buildLut(p, true, false)));
  });
  it("scales differences in sigma, keeping noise on glass", () => {
    const idx = diffIndex({ lo: -5, hi: 5, sigma: 1, n: 5 });
    expect(idx(0.8)).toBe(128);
    expect(idx(-1.2)).toBe(128);
    expect(idx(5)).toBe(255);
    expect(idx(-9)).toBe(1);
    expect(idx(3)).toBeGreaterThan(128);
    expect(idx(3)).toBeLessThan(255);
  });
});

describe("epochs per detector", () => {
  const ep = (id: string, det: 1 | 2 | 5, mjd: number): Epoch => ({
    id,
    kind: "visit",
    mjd_mid: mjd,
    mjd_start: mjd - 5,
    mjd_end: mjd + 5,
    det,
    um_min: 1,
    um_max: 2,
    n_exposures: 10,
    flag_frac: 0,
    sat_frac: 0,
    data_url: `${id}.bin`,
    scale: 1,
    zero: 0,
  });
  const frames = {
    epochs: [ep("d5-v1", 5, 100), ep("d2-v2", 2, 300), ep("d2-v1", 2, 100), ep("d1-v1", 1, 101)],
  } as FramesResponse;
  it("keeps one detector only, in time order", () => {
    expect(epochsFor(frames, 2).map((e) => e.id)).toEqual(["d2-v1", "d2-v2"]);
    expect(epochsFor(frames, 5).map((e) => e.id)).toEqual(["d5-v1"]);
    expect(epochsFor(frames, 3)).toEqual([]);
  });
});

describe("timeline axis", () => {
  it("months: linear in time, padded, visits six months apart stay six months apart", () => {
    const m = [60900, 61083, 61266];
    const a = buildAxis(m, "linear");
    const x = m.map((t) => a.x(t));
    expect(x[0]).toBeGreaterThan(0);
    expect(x[2]).toBeLessThan(1);
    expect(x[1] - x[0]).toBeCloseTo(x[2] - x[1], 6);
    expect(a.mjd(a.x(61000))).toBeCloseTo(61000, 6);
    expect(a.ticks.some((t) => t.label)).toBe(true);
    expect(a.ticks.filter((t) => t.label).length).toBeLessThanOrEqual(6);
  });
  it("months: covers the visit spans", () => {
    const a = buildAxis([61000], "linear", [[60990, 61010]]);
    expect(a.x(60990)).toBeGreaterThan(0);
    expect(a.x(61010)).toBeLessThan(1);
  });
  it("hours: compresses long gaps but keeps order, and labels gaps over an hour", () => {
    const min = 1 / 1440;
    const t = [61000, 61000 + 2.1 * min, 61000 + 4.2 * min, 61000.25, 61025];
    const a = buildAxis(t, "gaps");
    const x = t.map((m) => a.x(m));
    for (let i = 1; i < x.length; i++) expect(x[i]).toBeGreaterThan(x[i - 1]);
    // A 25-day gap is drawn wider than a 6-hour one, but far less than 100× wider.
    const w6h = x[3] - x[2];
    const w25d = x[4] - x[3];
    expect(w25d).toBeGreaterThan(w6h);
    expect(w25d / w6h).toBeLessThan(3);
    // Exposures 2 minutes apart are still visibly apart.
    expect(x[1] - x[0]).toBeGreaterThan(0.02);
    expect(a.gaps.map((g) => g.label)).toEqual(["5.9 h", "25 d"]);
    expect(a.mjd(a.x(61000.1))).toBeCloseTo(61000.1, 6);
  });
  it("handles one epoch", () => {
    expect(buildAxis([61000], "gaps").x(61000)).toBe(0.5);
    expect(buildAxis([], "linear").ticks).toEqual([]);
  });
  it("finds the nearest mark", () => {
    expect(nearestIndex([0.1, 0.5, 0.9], 0.62)).toBe(1);
    expect(nearestIndex([0.1, 0.5, 0.9], 0.99)).toBe(2);
  });
  it("formats gaps in plain units", () => {
    expect(formatGap(2.1 / 1440)).toBe("2 min");
    expect(formatGap(0.125)).toBe("3 h");
    expect(formatGap(25)).toBe("25 d");
    expect(formatGap(183)).toBe("6 mo");
  });
});

describe("scale bar", () => {
  it("picks a round length not longer than asked", () => {
    expect(niceArcsec(45)).toEqual({ arcsec: 30, label: "30″" });
    expect(niceArcsec(130)).toEqual({ arcsec: 120, label: "2′" });
  });
});
