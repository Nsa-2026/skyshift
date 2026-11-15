import { describe, expect, it } from "vitest";
import ref from "./indices_ref.json";
import { CUBE_UM } from "../sky/layers";
import {
  INDEX_IDS,
  INDEX_WINDOWS,
  LOG_CONT_MIN_SNR,
  MIN_COVER,
  MIN_DEPTH,
  cubeChannels,
  indexOf,
  indexWeights,
  windowMean,
  windowWeights,
  type Channels,
} from "./indices";

const pipeline: Channels = {
  lo: Float64Array.from(ref.channels.lo),
  hi: Float64Array.from(ref.channels.hi),
  centre: Float64Array.from(ref.channels.centre),
};
const num = (v: number | null) => (v == null ? NaN : v);

describe("indices match the pipeline (indices_ref.json, from pipeline/indices.py)", () => {
  it("uses the pipeline's windows and thresholds", () => {
    expect(MIN_COVER).toBe(ref.min_cover);
    expect(LOG_CONT_MIN_SNR).toBe(ref.log_cont_min_snr);
    for (const id of INDEX_IDS) {
      const [kind, band, cont] = ref.windows[id] as [string, number[], number[][]];
      expect(INDEX_WINDOWS[id].kind).toBe(kind);
      expect([...INDEX_WINDOWS[id].band]).toEqual(band);
      expect(INDEX_WINDOWS[id].cont.map((w) => [...w])).toEqual(cont);
      expect(MIN_DEPTH[id]).toBe((ref.label_min_depth as Record<string, number>)[id]);
    }
    expect(Object.keys(ref.windows).sort()).toEqual([...INDEX_IDS].sort());
  });

  for (const s of ref.spectra) {
    it(`every index of "${s.name}"`, () => {
      const flux = s.flux.map(num);
      const err = s.err.map(num);
      const want = s.indices as Record<string, number | null>;
      for (const id of INDEX_IDS) {
        const w = indexWeights(INDEX_WINDOWS[id], pipeline);
        const got = indexOf(flux, err, w);
        const name = INDEX_WINDOWS[id].kind === "abs" ? "depth" : "excess";
        const value = want[`${id}_${name}`];
        const sig = want[`${id}_sig`];
        if (value == null) expect(got.value, `${id} value`).toBeNaN();
        else expect(Math.abs(got.value - value), `${id} value`).toBeLessThan(2e-5 * Math.max(1, Math.abs(value)));
        if (sig == null) expect(got.sig, `${id} sig`).toBeNaN();
        else expect(Math.abs(got.sig - sig), `${id} sig`).toBeLessThan(2e-4 * Math.max(1, Math.abs(sig)));
      }
    });
  }
});

describe("cube channels", () => {
  const ch = cubeChannels();
  it("tile the detectors without gaps inside a detector", () => {
    for (let i = 0; i < CUBE_UM.length - 1; i++) {
      if (Math.floor(i / 17) !== Math.floor((i + 1) / 17)) continue;
      expect(ch.hi[i]).toBeCloseTo(ch.lo[i + 1], 9);
    }
    for (let i = 0; i < CUBE_UM.length; i++) {
      expect(ch.lo[i]).toBeLessThan(CUBE_UM[i]);
      expect(ch.hi[i]).toBeGreaterThan(CUBE_UM[i]);
    }
  });
  it("every index window is covered by at least two frames", () => {
    for (const id of INDEX_IDS) {
      const w = indexWeights(INDEX_WINDOWS[id], ch);
      for (const part of [w.band, w.c1, w.c2]) expect(Array.from(part).filter((v) => v > 0).length).toBeGreaterThanOrEqual(2);
    }
  });
});

describe("windowMean", () => {
  const ch = cubeChannels();
  it("is NaN when less than half the window is measured", () => {
    const w = windowWeights(ch, 3.2, 3.45);
    const flux = new Float64Array(102).fill(1);
    const err = new Float64Array(102).fill(0.1);
    expect(windowMean(flux, err, w, ch.centre).mean).toBeCloseTo(1, 9);
    flux[62] = NaN;
    flux[63] = NaN;
    expect(windowMean(flux, err, w, ch.centre).mean).toBeNaN();
  });
  it("leave-one-out skips one frame", () => {
    const w = windowWeights(ch, 3.2, 3.45);
    const flux = new Float64Array(102).fill(1);
    flux[62] = 100;
    const err = new Float64Array(102).fill(0.1);
    expect(windowMean(flux, err, w, ch.centre).mean).toBeGreaterThan(10);
    expect(windowMean(flux, err, w, ch.centre, 62).mean).toBeCloseTo(1, 9);
  });
});
