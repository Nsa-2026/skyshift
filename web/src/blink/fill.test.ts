// Filling a photo's no-data part from the visit's other photos (math.ts fillHoles, paint.ts fade).
import { describe, expect, it } from "vitest";
import { fillHoles, type FillSource } from "./math";
import { fade } from "./paint";

const NX = 8;
const NY = 6;

/** A flat frame at `sky` with a star of `amp` at (sx, sy). */
function frame(sky: number, sx = 2, sy = 2, amp = 10): Float32Array {
  const f = new Float32Array(NX * NY).fill(sky);
  f[sy * NX + sx] += amp;
  return f;
}
/** Columns from `x0` on have no data (past the detector's edge). */
function cutRight(f: Float32Array, x0: number): Float32Array {
  const g = f.slice();
  for (let y = 0; y < NY; y++) for (let x = x0; x < NX; x++) g[y * NX + x] = NaN;
  return g;
}
const src = (vals: Float32Array, sky: number, dx = 0, dy = 0, burnt: Uint8Array | null = null): FillSource => ({ vals, sky, burnt, dx, dy });

describe("fillHoles", () => {
  it("fills the empty part from the median of the others, at this photo's sky level", () => {
    const me = cutRight(frame(5, 6, 3), 5); // the star at x 6 is past the edge here
    const others = [src(frame(1, 6, 3), 1), src(frame(2, 6, 3), 2), src(frame(3, 6, 3), 3)];
    const fl = fillHoles(me, 5, null, others, NX, NY);
    expect(fl.count).toBe(3 * NY);
    expect(fl.vals[3 * NX + 6]).toBeCloseTo(15); // the star, at this photo's sky
    expect(fl.vals[0 * NX + 7]).toBeCloseTo(5);
    expect(fl.filled[3 * NX + 6]).toBe(1);
    // What this photo saw is untouched and not marked.
    expect(fl.vals[0]).toBe(me[0]);
    expect(fl.filled[0]).toBe(0);
  });

  it("drops what is in only one of the others (an asteroid), keeps what is in most", () => {
    const me = cutRight(frame(0, 0, 0, 0), 4);
    const withAsteroid = frame(0, 6, 1, 50);
    const plain = [frame(0, 0, 0, 0), frame(0, 0, 0, 0)];
    const fl = fillHoles(me, 0, null, [src(withAsteroid, 0), ...plain.map((p) => src(p, 0))], NX, NY);
    expect(fl.vals[1 * NX + 6]).toBe(0);
  });

  it("leaves pixels no other photo covers as no data", () => {
    const me = cutRight(frame(0), 4);
    const other = cutRight(frame(0), 6);
    const fl = fillHoles(me, 0, null, [src(other, 0)], NX, NY);
    expect(fl.filled[0 * NX + 5]).toBe(1);
    expect(Number.isNaN(fl.vals[0 * NX + 6])).toBe(true);
    expect(fl.filled[0 * NX + 6]).toBe(0);
    expect(fl.count).toBe(2 * NY);
  });

  it("does not touch this photo's burnt cores, and marks a fill burnt where the others mostly are", () => {
    const me = cutRight(frame(0), 5);
    me[1 * NX + 1] = NaN;
    const myBurnt = new Uint8Array(NX * NY);
    myBurnt[1 * NX + 1] = 1;
    const o = frame(0);
    o[2 * NX + 6] = NaN;
    const oBurnt = new Uint8Array(NX * NY);
    oBurnt[2 * NX + 6] = 1;
    const fl = fillHoles(me, 0, myBurnt, [src(o, 0, 0, 0, oBurnt)], NX, NY);
    expect(fl.filled[1 * NX + 1]).toBe(0);
    expect(fl.burnt[1 * NX + 1]).toBe(1);
    expect(fl.burnt[2 * NX + 6]).toBe(1);
    expect(fl.filled[2 * NX + 6]).toBe(1);
  });

  it("lines up photos with other centres on the sky (frames that follow a mover)", () => {
    // The other photo's centre is at x + 2 of this one's (dx = 2): its pixel x is this photo's
    // x + 2, so its star at x 4 is at x 6 here, and this photo's x 7 is its x 5.
    const me = cutRight(frame(0, 0, 0, 0), 5);
    const other = frame(0, 4, 3, 7);
    const fl = fillHoles(me, 0, null, [src(other, 0, 2, 0)], NX, NY);
    expect(fl.vals[3 * NX + 6]).toBe(7);
    expect(fl.vals[3 * NX + 5]).toBe(0);
    // x 7 here is x 5 there: covered.
    expect(fl.filled[3 * NX + 7]).toBe(1);
  });
});

describe("fade", () => {
  it("lightens only the marked, painted pixels toward the glass", () => {
    const out = new Uint8ClampedArray([0, 0, 0, 255, 0, 0, 0, 255, 0, 0, 0, 0]);
    fade(out, new Uint8Array([1, 0, 1]), [200, 100, 0], 0.5);
    expect(Array.from(out.slice(0, 4))).toEqual([100, 50, 0, 255]);
    expect(Array.from(out.slice(4, 8))).toEqual([0, 0, 0, 255]);
    expect(Array.from(out.slice(8, 12))).toEqual([0, 0, 0, 0]); // transparent stays transparent
  });
});
