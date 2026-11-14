import { describe, expect, it } from "vitest";
import { frameText } from "./bands";
import { skipBlank } from "./index";
import { neighbourFrames } from "./engine";
import { KEYS, swatchColour } from "./legend";

describe("single-wavelength filter (layer A1)", () => {
  it("names wavelength, detector, channel and any feature", () => {
    expect(frameText(0)).toBe("0.75 µm · D1 channel 1");
    expect(frameText(62)).toMatch(/^3\.29 µm · D4 channel 12 · methane/);
    expect(frameText(80)).toMatch(/CO₂/);
  });
  it("steps over the blank dichroic frames in the direction of travel", () => {
    expect(skipBlank(50, 49)).toBe(52);
    expect(skipBlank(51, 52)).toBe(49);
    expect(skipBlank(40, 39)).toBe(40);
  });
  it("uses the frame and its neighbours in the same detector, never a blank one", () => {
    expect(neighbourFrames(62)).toEqual([61, 62, 63]);
    expect(neighbourFrames(16)).toEqual([15, 16]); // last frame of D1: no D2 neighbour
    expect(neighbourFrames(49)).toEqual([48, 49]); // 50 is blank
  });
});

describe("colour key", () => {
  const rgbOf = (s: string) => s.match(/\d+/g)!.map(Number);
  it("draws ordinary stars blue and Y dwarfs red in Cold bodies, with the picture's own algorithm", () => {
    const [blue, , , , red] = KEYS.cold;
    const b = rgbOf(swatchColour(blue.rgb!));
    const r = rgbOf(swatchColour(red.rgb!));
    expect(b[2]).toBeGreaterThan(b[0]);
    expect(r[0]).toBeGreaterThan(200);
    expect(r[2]).toBeLessThan(20);
  });
  it("draws L dwarfs orange in 2MASS-like", () => {
    const [r, g, b] = rgbOf(swatchColour(KEYS.nir[3].rgb!));
    expect(r).toBeGreaterThan(g);
    expect(g).toBeGreaterThan(b);
  });
});

import { blendPeaks, coreHoles } from "./index";
describe("checks at the target", () => {
  const n = 5;
  const ch = (vals: number[]) => ({ values: Float32Array.from(vals), stats: { median: 0, sigma: 1, n: vals.length } });
  it("finds a hole in the core with data around it", () => {
    const v = new Float32Array(25).fill(1);
    v[12] = NaN;
    expect(coreHoles(v, n, 2)).toBe(1);
    const edge = new Float32Array(25).fill(NaN);
    expect(coreHoles(edge, n, 2)).toBe(0); // no data anywhere: not a hole
  });
  it("calls a blend when short and long light peak in different pixels", () => {
    const short = new Array(25).fill(0);
    const long = new Array(25).fill(0);
    long[12] = 10; // centre: 4.6 µm source
    short[12] = 6;
    short[8] = 23; // neighbour: blue star
    long[8] = 5;
    expect(blendPeaks(ch(short), ch(long), n, 2)).toBe(true);
  });
  it("doesn't call a blend for one source, even off-centre by a pixel in one band", () => {
    const short = new Array(25).fill(0);
    const long = new Array(25).fill(0);
    short[12] = 20;
    short[13] = 19;
    long[13] = 10;
    long[12] = 9.5;
    expect(blendPeaks(ch(short), ch(long), n, 2)).toBe(false);
  });
});
