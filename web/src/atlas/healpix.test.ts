import { describe, expect, it } from "vitest";
import { ang2pixNest, boundariesNest, cellSideDeg, nest2xyf, npixOf, parentNest, pix2angNest } from "./healpix";

const D2R = Math.PI / 180;

/** Seeded uniform points on the sphere, so the test is the same every run. */
function* spherePoints(n: number, seed = 7): Generator<[number, number]> {
  let s = seed;
  const rnd = () => ((s = (s * 1664525 + 1013904223) >>> 0) / 2 ** 32);
  for (let i = 0; i < n; i++) yield [rnd() * 360, Math.asin(2 * rnd() - 1) / D2R];
}

describe("HEALPix nested", () => {
  it("knows the base pixels (healpy, nside 1)", () => {
    expect(ang2pixNest(0, 0, 0)).toBe(4); // equator at RA 0: face 4
    expect(ang2pixNest(0, 45, 60)).toBe(0);
    expect(ang2pixNest(0, 135, 60)).toBe(1);
    expect(ang2pixNest(0, 315, -60)).toBe(11);
    expect(ang2pixNest(0, 180, 0)).toBe(6);
    // healpy.pix2ang(1, 0, nest=True) → θ = acos(2/3), φ = π/4
    const [ra, dec] = pix2angNest(0, 0);
    expect(ra).toBeCloseTo(45, 9);
    expect(dec).toBeCloseTo(90 - Math.acos(2 / 3) / D2R, 9);
  });

  it("sizes cells as expected", () => {
    expect(npixOf(3)).toBe(768);
    expect(cellSideDeg(3)).toBeCloseTo(7.33, 2);
    expect(cellSideDeg(5)).toBeCloseTo(1.83, 2);
  });

  it("round-trips every cell centre at orders 0–4", () => {
    for (let order = 0; order <= 4; order++) {
      for (let pix = 0; pix < npixOf(order); pix++) {
        const [ra, dec] = pix2angNest(order, pix);
        expect(ang2pixNest(order, ra, dec)).toBe(pix);
      }
    }
  });

  it("round-trips sampled cells at order 13", () => {
    const n = npixOf(13);
    for (let k = 0; k < 2000; k++) {
      const pix = Math.floor((k * 7919 * 104729) % n);
      const [ra, dec] = pix2angNest(13, pix);
      expect(ang2pixNest(13, ra, dec)).toBe(pix);
    }
  });

  it("has equal-area cells: uniform points fill them evenly", () => {
    const order = 1;
    const counts = new Array(npixOf(order)).fill(0);
    const N = 96_000;
    for (const [ra, dec] of spherePoints(N)) counts[ang2pixNest(order, ra, dec)]++;
    const mean = N / counts.length; // 2000
    for (const c of counts) expect(Math.abs(c - mean)).toBeLessThan(6 * Math.sqrt(mean));
  });

  it("puts boundary points on the cell's edge", () => {
    for (const order of [2, 3, 5]) {
      for (let pix = 0; pix < npixOf(order); pix += 37) {
        const [cra, cdec] = pix2angNest(order, pix);
        for (const [ra, dec] of boundariesNest(order, pix, 3)) {
          // A point 2 % of the way from the edge towards the centre is inside the cell.
          let dra = cra - ra;
          if (dra > 180) dra -= 360;
          if (dra < -180) dra += 360;
          // Near the poles RA is meaningless: skip the corner that sits on the pole.
          if (Math.abs(dec) > 89.99) continue;
          const ira = ra + 0.02 * dra;
          const idec = dec + 0.02 * (cdec - dec);
          expect(ang2pixNest(order, ira, idec)).toBe(pix);
        }
      }
    }
  });

  it("gives 4 × step boundary points", () => {
    expect(boundariesNest(3, 100, 1)).toHaveLength(4);
    expect(boundariesNest(3, 100, 4)).toHaveLength(16);
  });

  it("nests: a child's parent holds the child's centre", () => {
    for (let pix = 0; pix < npixOf(5); pix += 11) {
      const [ra, dec] = pix2angNest(5, pix);
      expect(ang2pixNest(3, ra, dec)).toBe(parentNest(pix, 2));
    }
    expect(nest2xyf(npixOf(2) - 1, 2)).toEqual({ ix: 3, iy: 3, face: 11 });
  });

  it("handles the poles and RA wrap", () => {
    expect(() => ang2pixNest(5, 0, 90)).not.toThrow();
    expect(ang2pixNest(5, 360, 10)).toBe(ang2pixNest(5, 0, 10));
    expect(ang2pixNest(5, -10, 10)).toBe(ang2pixNest(5, 350, 10));
  });
});
