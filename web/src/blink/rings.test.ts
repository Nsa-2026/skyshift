// Which Gaia stars get a ring: the plate's own depth, measured on its own pixels (rings.ts).
import { describe, expect, it } from "vitest";
import { getsRing, MIN_SEEN, ringLimitG, standsOutAt, type SeenStar } from "./rings";

/** Stars every 0.1 mag from g0 to g1, each covered by `frames` photos, shown in `hitsAt(g)` of them. */
function stars(g0: number, g1: number, frames: number, hitsAt: (g: number) => number): SeenStar[] {
  const out: SeenStar[] = [];
  for (let k = 0; g0 + k * 0.1 <= g1 + 1e-9; k++) {
    const g = +(g0 + k * 0.1).toFixed(2);
    out.push({ g, frames, hits: hitsAt(g) });
  }
  return out;
}

describe("the plate's ring limit", () => {
  it("is where half the Gaia stars show a dot", () => {
    // Shown to G 15, never past it.
    expect(ringLimitG(stars(10, 20, 1, (g) => (g <= 15 ? 1 : 0)))!).toBeCloseTo(15.05, 6);
    // A shallower plate (a long-wavelength detector, or six summed) finds its own, brighter, limit.
    expect(ringLimitG(stars(10, 20, 1, (g) => (g <= 13.2 ? 1 : 0)))!).toBeCloseTo(13.25, 6);
  });

  it("follows the half-way point of a gradual fall-off, counting each star by how often it shows", () => {
    // Hours clock, 6 photos: every photo to G 14, then the share falls linearly to none at G 16.
    const L = ringLimitG(stars(10, 20, 6, (g) => Math.round(6 * Math.min(1, Math.max(0, (16 - g) / 2)))))!;
    expect(L).toBeGreaterThan(14.7);
    expect(L).toBeLessThan(15.3);
  });

  it("is not thrown by a few misses among bright stars or a few faint dots", () => {
    const list = stars(10, 20, 1, (g) => (g <= 15 ? 1 : 0));
    // Two bright stars on a bad pixel, two faint ones on a neighbour's wing.
    for (const s of list) if (s.g === 10.2 || s.g === 12.5) s.hits = 0;
    for (const s of list) if (s.g === 18.3 || s.g === 19.1) s.hits = 1;
    expect(ringLimitG(list)!).toBeCloseTo(15.05, 6);
  });

  it("gives no limit (the old cut is used) when too few stars show, or none has data", () => {
    expect(ringLimitG(stars(10, 20, 1, (g) => (g < 10 + (MIN_SEEN - 1) * 0.1 - 0.05 ? 1 : 0)))).toBeNull();
    expect(ringLimitG(stars(10, 20, 0, () => 1))).toBeNull();
    expect(ringLimitG([])).toBeNull();
  });
});

describe("does the plate show a dot", () => {
  // A 21 × 21 sky of noise about 1 (deterministic), one star of peak `peak` at (10, 10).
  const sky = (peak: number, burntCore = false) => {
    const n = 21;
    const vals = new Float32Array(n * n);
    const burnt = new Uint8Array(n * n);
    for (let y = 0; y < n; y++)
      for (let x = 0; x < n; x++) {
        const k = y * n + x;
        vals[k] = ((x * 7 + y * 13) % 5) * 0.5 - 1 + peak * Math.exp(-((x - 10) ** 2 + (y - 10) ** 2) / 2);
        if (burntCore && x === 10 && y === 10) {
          vals[k] = NaN;
          burnt[k] = 1;
        }
      }
    return { vals, burnt, n };
  };
  it("over 6σ of its local sky, yes; under, no; a burnt core, yes", () => {
    const bright = sky(20);
    expect(standsOutAt(bright.vals, bright.burnt, 21, 21, 10, 10, 5, 1)).toBe(true);
    const faint = sky(3);
    expect(standsOutAt(faint.vals, faint.burnt, 21, 21, 10, 10, 5, 1)).toBe(false);
    const sat = sky(0, true);
    expect(standsOutAt(sat.vals, sat.burnt, 21, 21, 10, 10, 5, 1)).toBe(true);
    expect(standsOutAt(bright.vals, bright.burnt, 21, 21, 3, 3, 5, 1)).toBe(false); // empty sky
  });
});

describe("a Gaia star's ring", () => {
  const no = () => false;
  it("always, when SkyShift found it in this photo", () => {
    expect(getsRing(19, true, 15, 16.5, no)).toBe(true);
  });
  it("else only brighter than the limit, or where the plate shows a dot", () => {
    expect(getsRing(14, false, 15, 16.5, no)).toBe(true);
    expect(getsRing(16, false, 15, 16.5, no)).toBe(false);
    expect(getsRing(16, false, 15, 16.5, () => true)).toBe(true);
  });
  it("falls back to the old cut without a limit", () => {
    expect(getsRing(16, false, null, 16.5, no)).toBe(true);
    expect(getsRing(17, false, null, 16.5, no)).toBe(false);
  });
});
