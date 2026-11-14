import { describe, expect, it } from "vitest";
import { framesIn, featureAt, detChannel, PRESETS } from "./bands";
import { clippedStats, colourWeight, compose, deficitPlanes, medianPlanes, saturatedMask, toChannel, type Channel } from "./rgb";

function noise(n: number, sigma: number, seed = 1): Float32Array {
  // Deterministic Gaussian noise (Box–Muller on a small LCG).
  let s = seed;
  const u = () => ((s = (s * 1664525 + 1013904223) % 2 ** 32) + 1) / (2 ** 32 + 1);
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) out[i] = sigma * Math.sqrt(-2 * Math.log(u())) * Math.cos(2 * Math.PI * u());
  return out;
}

describe("clippedStats", () => {
  it("recovers background level and noise despite bright stars", () => {
    const v = noise(4000, 2);
    for (let i = 0; i < 4000; i++) v[i] += 10;
    for (let i = 0; i < 100; i++) v[i * 40] += 500; // 2.5% stars
    const s = clippedStats(v);
    expect(s.median).toBeCloseTo(10, 0);
    expect(s.sigma).toBeGreaterThan(1.7);
    expect(s.sigma).toBeLessThan(2.3);
  });
  it("ignores NaN", () => {
    expect(clippedStats([1, NaN, 3, 2]).median).toBe(2);
  });
});

describe("medianPlanes", () => {
  it("takes the median of finite values and keeps NaN only where all are NaN", () => {
    const m = medianPlanes([Float32Array.from([1, NaN, NaN]), Float32Array.from([3, 4, NaN])]);
    expect(m[0]).toBe(2);
    expect(m[1]).toBe(4);
    expect(Number.isNaN(m[2])).toBe(true);
  });
  it("ignores a single glitched frame (as seen in the cube at 4.6 µm)", () => {
    const frames = [1, 2, 1.5, -4000, 1.2, 0.9].map((v) => Float32Array.from([v]));
    expect(medianPlanes(frames)[0]).toBeGreaterThan(0.9);
    expect(medianPlanes(frames)[0]).toBeLessThan(1.5);
  });
});

const chan = (vals: number[], sigma = 1): Channel => ({ values: Float32Array.from(vals), stats: { median: 0, sigma, n: vals.length } });

describe("compose (Lupton)", () => {
  it("draws a flat spectrum as white", () => {
    const c = compose(chan([100, 0]), chan([100, 0]), chan([100, 0]));
    expect(c.rgba[0]).toBe(c.rgba[1]);
    expect(c.rgba[1]).toBe(c.rgba[2]);
    expect(c.rgba[0]).toBeGreaterThan(0);
  });
  it("keeps the hue of a bright source instead of saturating to white", () => {
    // R:G:B = 4:2:1, far above the noise.
    const c = compose(chan([4000, 0, 1]), chan([2000, 0, 1]), chan([1000, 0, 1]));
    const [r, g, b] = [c.rgba[0], c.rgba[1], c.rgba[2]];
    expect(r).toBe(255);
    expect(g / r).toBeCloseTo(0.5, 1);
    expect(b / r).toBeCloseTo(0.25, 1);
  });
  it("draws a single-band noise spike grey, never coloured", () => {
    // 3σ in red only: I is 1.7σ_I, but no band is significant, so no hue.
    const c = compose(chan([3, 0, 0, 200]), chan([0, 0, 0, 200]), chan([0, 0, 0, 200]));
    expect(c.rgba[0]).toBe(c.rgba[1]);
    expect(c.rgba[1]).toBe(c.rgba[2]);
  });
  it("colours a source seen only in red when red is significant (a Y dwarf)", () => {
    const c = compose(chan([20, 0, 0, 200]), chan([0, 0, 0, 200]), chan([0, 0, 0, 200]));
    expect(c.rgba[0]).toBeGreaterThan(0);
    expect(c.rgba[1]).toBe(0);
    expect(c.rgba[2]).toBe(0);
  });
  it("leaves no-data pixels transparent", () => {
    const c = compose(chan([NaN, 5]), chan([1, 5]), chan([1, 5]));
    expect(c.rgba[3]).toBe(0);
    expect(c.rgba[7]).toBe(255);
  });
  it("colour weight ramps from 2σ to 4σ", () => {
    expect(colourWeight(2)).toBe(0);
    expect(colourWeight(4)).toBeCloseTo(0.5);
    expect(colourWeight(9)).toBe(1);
  });
  it("toChannel subtracts the clipped background", () => {
    const v = noise(2000, 1);
    for (let i = 0; i < v.length; i++) v[i] += 7;
    const ch = toChannel(v);
    expect(clippedStats(ch.values).median).toBeCloseTo(0, 1);
  });
});

describe("saturation from exposure deficits", () => {
  const n = 15;
  const flat = () => new Int32Array(n * n).fill(12);
  it("flags a bright core that lost exposures in many planes, grown by one pixel", () => {
    const planes = Array.from({ length: 6 }, () => {
      const h = flat();
      h[7 * n + 7] = 0; // the core: every exposure rejected
      return h;
    });
    const d = deficitPlanes(planes, n);
    expect(d[7 * n + 7]).toBe(6);
    expect(d[0]).toBe(0);
    const bright = chan(Array.from({ length: n * n }, (_, k) => (k === 7 * n + 7 ? 1000 : 0)));
    const m = saturatedMask(d, bright, bright, bright, 1, n);
    expect(m[7 * n + 7]).toBe(1);
    expect(m[6 * n + 6]).toBe(1); // grown
    expect(m.reduce((a, v) => a + v, 0)).toBe(9);
  });
  it("ignores chance deficits (< 4 planes) and faint pixels", () => {
    const planes = Array.from({ length: 6 }, (_, p) => {
      const h = flat();
      if (p < 3) h[3 * n + 3] = 2; // 3 planes only
      h[10 * n + 10] = 0; // every plane, but faint
      return h;
    });
    const d = deficitPlanes(planes, n);
    const vals = Array.from({ length: n * n }, (_, k) => (k === 3 * n + 3 ? 1000 : k === 10 * n + 10 ? 3 : 0));
    const c = chan(vals);
    expect(saturatedMask(d, c, c, c, 1, n).reduce((a, v) => a + v, 0)).toBe(0);
  });
  it("doesn't treat a smooth coverage step as a deficit", () => {
    const h = flat();
    for (let k = 0; k < n * n; k++) if (k % n > 7) h[k] = 7; // 12 → 7 across a footprint edge
    expect(deficitPlanes([h, h, h, h, h], n).reduce((a, v) => a + v, 0)).toBe(0);
  });
});

describe("bands", () => {
  it("windows pick the cube frames inside them and skip the blank dichroic frames", () => {
    expect(framesIn({ lo: 1.15, hi: 1.33 }).length).toBe(6);
    expect(framesIn({ lo: 2.39, hi: 2.45 })).toEqual([]); // frames 50 and 51 are blank
    for (const p of PRESETS) for (const w of [p.red, p.green, p.blue]) expect(framesIn(w).length).toBeGreaterThan(3);
  });
  it("names features and detectors", () => {
    expect(featureAt(3.29)?.name).toMatch(/methane/);
    expect(featureAt(2.0)).toBeNull();
    expect(detChannel(0)).toEqual({ det: 1, channel: 1 });
    expect(detChannel(101)).toEqual({ det: 6, channel: 17 });
  });
});
