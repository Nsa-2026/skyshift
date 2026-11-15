import { describe, expect, it } from "vitest";
import { frameText } from "./bands";
import { skipBlank } from "./index";
import { neighbourFrames } from "./engine";
import { KEY_SETS, swatchColour } from "./legend";

describe("single-wavelength filter (layer A1)", () => {
  it("names wavelength, detector, channel and any feature", () => {
    expect(frameText(0)).toBe("0.75 micrometres");
    expect(frameText(62)).toMatch(/^3\.29 micrometres: methane gas.+The coldest brown dwarfs go dark/);
    expect(frameText(80)).toMatch(/carbon dioxide/);
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
  const rgbOf = (x: string) => x.match(/\d+/g)!.map(Number);
  // Each entry's colour name must describe the swatch the picture's algorithm really draws.
  const hue: Record<string, (c: number[]) => boolean> = {
    Blue: ([r, , b]) => b === 255 && r < 120,
    "Light blue": ([r, g, b]) => b === 255 && g > 200 && r < 120,
    "Pale blue": ([r, g, b]) => b === 255 && r > 120 && g > 160,
    Green: ([r, g, b]) => g === 255 && r < 220 && b < 240,
    White: ([r, g, b]) => Math.min(r, g, b) > 180,
    Cream: ([r, g, b]) => Math.min(r, g) > 230 && b > 150,
    Pink: ([r, g, b]) => r === 255 && b > g,
    Red: ([r, g, b]) => r === 255 && g < 30 && b < 30,
    Orange: ([r, g, b]) => r === 255 && g > 120 && g < 230 && b < 140,
  };
  for (const [set, list] of Object.entries(KEY_SETS)) {
    for (const e of list.filter((x) => x.rgb)) {
      it(`${set}: "${e.label}" looks ${e.label.toLowerCase()}`, () => {
        const c = rgbOf(swatchColour(e.rgb!));
        expect(hue[e.label], e.label).toBeDefined();
        expect(hue[e.label](c), `${e.label} ${c}`).toBe(true);
      });
    }
  }
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

import { filterField, level } from "./index";
describe("filter field and plain words", () => {
  it("shows a wider area than the blink so the map's coarse pixels don't look like a zoom", () => {
    expect(filterField(5)).toBe(15);
    expect(filterField(10)).toBe(30);
  });
  it("reads at most 30′: a 60′ field took 64 s from IRSA; a 60′ plate shows its middle", () => {
    expect(filterField(15)).toBe(30);
    expect(filterField(20)).toBe(30);
    expect(filterField(60)).toBe(30);
  });
  it("says signal in words", () => {
    expect([1, 5, 50, 500, NaN].map(level)).toEqual(["none", "faint", "clear", "bright", "no data"]);
  });
});
