// "All wavelengths": grouping each moment's detectors, the noise-weighted sum and its no-data rule,
// and the measured layer moved onto the combined moments.
import { describe, expect, it } from "vitest";
import type { Det, Epoch, Measured, MeasuredDetection } from "../api/types";
import { combineFrames, detectorsNote, groupEpochs, mergeMeasured, remapMeasured, type WavePart } from "./allwave";
import { noDataSpot } from "./math";

const ep = (id: string, det: Det, mjd: number, um: [number, number] = [1, 2]): Epoch => ({
  id,
  kind: "visit",
  mjd_mid: mjd,
  mjd_start: mjd - 5,
  mjd_end: mjd + 5,
  det,
  um_min: um[0],
  um_max: um[1],
  n_exposures: 10,
  flag_frac: 0.02,
  sat_frac: 0.001,
  data_url: `frames/${id}.bin`,
  scale: 0.001,
  zero: 0,
});

describe("grouping each moment's detectors", () => {
  it("months: the same visit in every detector is one epoch, its span and exposures summed", () => {
    const g = groupEpochs(
      [ep("d1-v1", 1, 60900, [0.75, 1.1]), ep("d4-v1", 4, 60912, [2.4, 3.8]), ep("d2-v2", 2, 61090), ep("d1-v2", 1, 61085, [0.75, 1.1])],
      "months",
      2,
    );
    expect(g.map((x) => x.epoch.id)).toEqual(["all-1", "all-2"]);
    expect(g.map((x) => x.members.map((m) => m.det))).toEqual([
      [1, 4],
      [1, 2],
    ]);
    expect(g[0].epoch).toMatchObject({ det: 2, mjd_mid: 60906, um_min: 0.75, um_max: 3.8, n_exposures: 20 });
  });

  it("never puts two frames of one detector in a moment", () => {
    const g = groupEpochs([ep("d2-a", 2, 60900), ep("d2-b", 2, 60910)], "months", 2);
    expect(g).toHaveLength(2);
  });

  it("hours: only simultaneous exposures combine; other times stay separate; other visits drop out", () => {
    const s = 1 / 86400;
    const g = groupEpochs(
      [
        ep("d2-a", 2, 60965.4),
        ep("d5-a", 5, 60965.4 + 2 * s),
        ep("d3-b", 3, 60965.401),
        ep("d6-b", 6, 60965.401),
        ep("d2-c", 2, 60965.5),
        ep("d5-far", 5, 61150), // another visit picked for D5
      ],
      "hours",
      2,
    );
    expect(g.map((x) => x.members.map((m) => m.id))).toEqual([["d2-a", "d5-a"], ["d3-b", "d6-b"], ["d2-c"]]);
    expect(g[0].epoch.mjd_mid).toBe(60965.4);
  });
});

describe("the combined plate", () => {
  const part = (vals: number[], sky: number, sigma: number, burnt?: number[]): WavePart => ({
    vals: Float32Array.from(vals),
    burnt: burnt ? Uint8Array.from(burnt) : null,
    sky,
    sigma,
  });

  it("puts each detector in units of its own noise, so none dominates, sky noise stays 1", () => {
    // Pixel 0: 3σ in both. Pixel 1: sky in both.
    const out = combineFrames([part([13, 10], 10, 1), part([1030, 1000], 1000, 10)]);
    expect(out[0]).toBeCloseTo(6 / Math.SQRT2, 6);
    expect(out[1]).toBeCloseTo(0, 6);
  });

  it("a pixel is no data only when every detector has none there", () => {
    const out = combineFrames([part([NaN, NaN, 12], 10, 1), part([4, NaN, 2], 0, 2)]);
    expect(out[0]).toBeCloseTo(2, 6); // one detector: its own σ units, √1
    expect(Number.isNaN(out[1])).toBe(true);
    expect(out[2]).toBeCloseTo(3 / Math.SQRT2, 6);
  });

  it("keeps a burnt core burnt (ink), even where another detector has a value", () => {
    const out = combineFrames([part([NaN, 5], 0, 1, [1, 0]), part([50, 5], 0, 1)]);
    expect(Number.isNaN(out[0])).toBe(true);
    expect(out[1]).toBeCloseTo(10 / Math.SQRT2, 6);
  });

  it("finds where to say 'no data here': inside the empty strip, with the share of the window", () => {
    const nx = 40;
    const vals = new Float32Array(nx * nx).fill(1);
    for (let y = 0; y < nx; y++) for (let x = 30; x < nx; x++) vals[y * nx + x] = NaN;
    const s = noDataSpot(vals, null, nx, nx, { u0: 0, v0: 0, vw: nx, vh: nx }, 20)!;
    expect(s.x).toBeGreaterThan(30);
    expect(s.frac).toBeCloseTo(0.25, 6);
    expect(noDataSpot(new Float32Array(16).fill(1), null, 4, 4, { u0: 0, v0: 0, vw: 4, vh: 4 })).toBeNull();
    // A burnt core is a saturated star, not missing data.
    const burnt = new Uint8Array(nx * nx).fill(1);
    expect(noDataSpot(vals, burnt, nx, nx, { u0: 0, v0: 0, vw: nx, vh: nx })).toBeNull();
  });
});

describe("the measured layer on the combined plate", () => {
  const det = (epoch_id: string, source_id: number | null, snr: number, more: Partial<MeasuredDetection> = {}): MeasuredDetection =>
    ({ epoch_id, ra: 10, dec: 0, snr, flux_ujy: 100, n_dots: 1, class: "star", source_id, gaia_id: null, reasons: [], ...more }) as unknown as MeasuredDetection;
  const arcsec = 1 / 3600;
  const meas = (d: MeasuredDetection[], shifts: { source_id: string; pm_snr: number }[] = []): Measured =>
    ({
      clock: "months",
      stage: "final",
      photos: 2,
      photos_missing: 0,
      truncated: false,
      counts: { detections: d.length, tracks: 0, shifts: shifts.length },
      detections: d,
      tracks: [],
      shifts,
    }) as unknown as Measured;

  it("merges one-detector answers: counts summed, shifts once per source", () => {
    const m = mergeMeasured([meas([det("d2-v1", 1, 5)], [{ source_id: "1", pm_snr: 3 }]), meas([det("d5-v1", 1, 9)], [{ source_id: "1", pm_snr: 3 }]), null])!;
    expect(m.detections).toHaveLength(2);
    expect(m.photos).toBe(4);
    expect(m.shifts).toHaveLength(1);
    expect(mergeMeasured([null, undefined])).toBeNull();
  });

  it("one dot per source and moment: same source, same Gaia star, under 3.75″ apart, or two detectors under one FWHM", () => {
    const groups = groupEpochs([ep("d2-v1", 2, 60900), ep("d5-v1", 5, 60900), ep("d2-v2", 2, 61090)], "months", 2);
    const m = remapMeasured(
      meas([
        det("d2-v1", 1, 5),
        det("d5-v1", 1, 9, { ra: 10 + 5 * arcsec }), // same source, the best S/N wins
        det("d2-v2", 1, 6),
        det("d5-v1", null, 4, { ra: 10 + 60 * arcsec }), // a loose dot far from the rest
        det("d2-v1", null, 4, { ra: 10 + 62 * arcsec }), // 2″ from it: the same source in D2
        det("d2-v1", 7, 8, { ra: 10 + 30 * arcsec, gaia_id: "g" }),
        det("d5-v1", 8, 3, { ra: 10 + 34 * arcsec, gaia_id: "g", class: "mover" }), // same Gaia star: the mover is kept
        det("d9-x", 2, 7), // no moment: dropped
        det("d2-v2", 3, 5, { ra: 10 + 40 * arcsec }),
        det("d2-v2", 4, 4, { ra: 10 + 46 * arcsec }), // 6″ apart in one detector: two sources
        det("d5-v1", 5, 2, { ra: 10 + 96 * arcsec }),
        det("d2-v1", 6, 3, { ra: 10 + 102 * arcsec }), // 6″ apart in two detectors: one source
      ]),
      groups,
    );
    const ids = m.detections.map((d) => `${d.epoch_id}:${d.source_id}:${d.snr}`);
    expect(ids).toEqual(["all-1:1:9", "all-1:null:4", "all-1:8:3", "all-1:6:3", "all-2:1:6", "all-2:3:5", "all-2:4:4"]);
  });

  it("says how many detectors a moment really has", () => {
    expect(detectorsNote(4, "months")).toBe("4 of 6 detectors");
    expect(detectorsNote(6, "months")).toBe("");
    expect(detectorsNote(1, "hours")).toBe("1 of 2 detectors");
    expect(detectorsNote(2, "hours")).toBe("");
  });
});
