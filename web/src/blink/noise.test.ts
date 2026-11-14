// Smoothing before Difference, σ-scaled differences, shift-and-add and saturated cores (math.ts).
import { describe, expect, it } from "vitest";
import {
  burntCores,
  contrastToSigmas,
  gaussianKernel,
  psfSigmaPx,
  robustSigma,
  shiftBilinear,
  sigmaLimits,
  sigmaStretch,
  smooth,
} from "./math";

/** Deterministic pseudo-random normal noise (Box–Muller on a small LCG). */
function noise(n: number, sd: number, seed = 7): Float32Array {
  let s = seed;
  const u = () => ((s = (s * 1664525 + 1013904223) >>> 0) + 0.5) / 2 ** 32;
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) out[i] = sd * Math.sqrt(-2 * Math.log(u())) * Math.cos(2 * Math.PI * u());
  return out;
}

/** A Gaussian star of peak `amp` at (cx, cy) on an nx × ny frame. */
function star(nx: number, ny: number, cx: number, cy: number, amp: number, sig = 1.5, base?: Float32Array) {
  const f = base ? base.slice() : new Float32Array(nx * ny);
  for (let y = 0; y < ny; y++)
    for (let x = 0; x < nx; x++) f[y * nx + x] += amp * Math.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2 * sig * sig));
  return f;
}

describe("PSF-matched smoothing", () => {
  it("matches SPHEREx's PSF: 7.53″ FWHM is 1.56 px at 2.05″ and 0.52 px at 6.15″", () => {
    expect(psfSigmaPx(7.53, 2.05)).toBeCloseTo(1.56, 2);
    expect(psfSigmaPx(7.53, 6.15)).toBeCloseTo(0.52, 2);
  });
  it("builds a normalised kernel out to 3σ", () => {
    const k = gaussianKernel(1.5);
    expect(k.length).toBe(11);
    expect(k.reduce((a, b) => a + b, 0)).toBeCloseTo(1, 6);
    expect(k[5]).toBeGreaterThan(k[4]);
  });
  it("cuts pixel noise and keeps flux, without spreading no-data holes", () => {
    const nx = 64;
    const f = noise(nx * nx, 1);
    f[10 * nx + 10] = NaN;
    const s = smooth(f, nx, nx, 1.56);
    expect(Number.isNaN(s[10 * nx + 10])).toBe(true);
    expect(Number.isNaN(s[10 * nx + 11])).toBe(false);
    expect(robustSigma([s])).toBeLessThan(0.5 * robustSigma([f]));
    // A flat frame stays flat (normalised convolution near holes and edges).
    const flat = new Float32Array(nx * nx).fill(3);
    flat[5] = NaN;
    const sf = smooth(flat, nx, nx, 1.56);
    expect(sf[0]).toBeCloseTo(3, 5);
    expect(sf[nx * nx - 1]).toBeCloseTo(3, 5);
  });
});

describe("differences in sigma", () => {
  it("measures noise robustly, ignoring a few bright stars", () => {
    const nx = 80;
    const f = star(nx, nx, 40, 40, 500, 1.5, noise(nx * nx, 2));
    expect(robustSigma([f])).toBeGreaterThan(1.7);
    expect(robustSigma([f])).toBeLessThan(2.3);
  });
  it("maps contrast to a ±Nσ range: 10σ at 0, about 5σ by default, 2σ at 1", () => {
    expect(contrastToSigmas(0)).toBe(10);
    expect(contrastToSigmas(0.45)).toBe(5);
    expect(contrastToSigmas(1)).toBe(2);
  });
  it("gives limits of ±Nσ with σ and N for the key", () => {
    const l = sigmaLimits([noise(10_000, 0.5)], 0.45);
    expect(l.n).toBe(5);
    expect(l.sigma).toBeGreaterThan(0.45);
    expect(l.sigma).toBeLessThan(0.55);
    expect(l.hi).toBeCloseTo(5 * l.sigma, 6);
    expect(l.lo).toBeCloseTo(-l.hi, 6);
  });
  it("falls back to percentile limits on a frame with no noise", () => {
    const l = sigmaLimits([new Float32Array([0, 0, 0, 0, 0, 4])], 0.45);
    expect(Number.isNaN(l.sigma)).toBe(true);
    expect(l.hi).toBeGreaterThan(0);
  });
  it("keeps noise on glass, then grows to full colour at Nσ", () => {
    expect(sigmaStretch(1.4, 1, 5)).toBe(0);
    expect(sigmaStretch(-1.4, 1, 5)).toBe(-0);
    expect(sigmaStretch(5, 1, 5)).toBe(1);
    expect(sigmaStretch(-20, 1, 5)).toBe(-1);
    expect(sigmaStretch(3.25, 1, 5)).toBeCloseTo(0.5, 6);
    expect(Number.isNaN(sigmaStretch(NaN, 1, 5))).toBe(true);
  });
});

describe("shift-and-add", () => {
  it("moves a frame by whole and fractional pixels", () => {
    const nx = 20;
    const f = star(nx, nx, 10, 10, 100, 1);
    const s = shiftBilinear(f, nx, nx, 2, -3); // out(x, y) = in(x + 2, y − 3): the star lands at (8, 13)
    expect(s[13 * nx + 8]).toBeCloseTo(100, 4);
    const h = shiftBilinear(f, nx, nx, 0.5, 0);
    expect(h[10 * nx + 10]).toBeCloseTo(h[10 * nx + 9], 4);
  });
  it("marks pixels that fall off the frame or touch no data as no data", () => {
    const nx = 6;
    const f = new Float32Array(nx * nx).fill(1);
    f[2 * nx + 2] = NaN;
    const s = shiftBilinear(f, nx, nx, 1, 0);
    expect(Number.isNaN(s[0 * nx + 5])).toBe(true);
    expect(Number.isNaN(s[2 * nx + 1])).toBe(true);
    expect(s[0]).toBe(1);
  });
});

describe("saturated cores", () => {
  const nx = 40;
  const sky = 0;
  it("burns a masked core on a star, not missing data on empty sky", () => {
    const f = star(nx, nx, 20, 20, 1000, 1.6, noise(nx * nx, 1));
    // The core is masked (OVERFLOW / NONLINEAR) …
    for (const [x, y] of [
      [20, 20],
      [21, 20],
      [20, 21],
      [19, 20],
      [20, 19],
    ])
      f[y * nx + x] = NaN;
    // … and so is a pixel on empty sky and a strip along the edge.
    f[5 * nx + 5] = NaN;
    for (let x = 0; x < nx; x++) f[x] = NaN;
    const b = burntCores(f, nx, nx, sky, 1);
    expect(b[20 * nx + 20]).toBe(1);
    expect(b[20 * nx + 21]).toBe(1);
    expect(b[5 * nx + 5]).toBe(0);
    expect(b[3]).toBe(0);
  });
  it("leaves a lone masked pixel in a bright star's wings as missing data", () => {
    const f = star(nx, nx, 20, 20, 5000, 6, noise(nx * nx, 1));
    f[20 * nx + 28] = NaN; // in the wings, 8 px from the peak: no peak here
    const b = burntCores(f, nx, nx, sky, 1);
    expect(b[20 * nx + 28]).toBe(0);
  });
});
