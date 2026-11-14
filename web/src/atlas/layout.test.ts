// Geometry of the map's frame: the projection's edge, the whole-sky fit, the flat map's place,
// the mini blink's epochs and cut-out.
import { describe, expect, it } from "vitest";
import type { Epoch } from "../api/types";
import { fitWidth, limbEllipse } from "./aladin-map";
import { wholeGeometry } from "./fallback";
import { inView } from "./map";
import { cropValues, cropWindow, epochCaption, pickEpochs } from "./miniblink";
import { cand } from "./testdata";

describe("limbEllipse", () => {
  it("draws the globe as a disk the map's width across at 180°", () => {
    // A phone: 390 × 454. The disk is 390 px wide; the corners are outside it.
    expect(limbEllipse(false, 180, 390, 454)).toEqual({ cx: 195, cy: 227, rx: 195, ry: 195 });
  });

  it("draws the whole sky as a 2:1 ellipse at 360°", () => {
    expect(limbEllipse(true, 360, 1348, 694)).toEqual({ cx: 674, cy: 347, rx: 674, ry: 337 });
  });

  it("has nothing to cover once the sky fills the map", () => {
    expect(limbEllipse(false, 60, 1440, 700)).toBeNull();
    expect(limbEllipse(true, 120, 1440, 700)).toBeNull();
    expect(limbEllipse(true, 0, 1440, 700)).toBeNull();
  });
});

describe("fitWidth", () => {
  it("narrows a wide map to 2:1 for the whole-sky ellipse only", () => {
    expect(fitWidth(true, 360, 1440, 694)).toBe(2 * (694 - 20));
    expect(fitWidth(false, 180, 1440, 694)).toBe(1440);
    expect(fitWidth(true, 200, 1440, 694)).toBe(1440);
    // Already narrower than 2:1: nothing to do.
    expect(fitWidth(true, 360, 800, 694)).toBe(800);
  });

  it("holds the narrow width while the field shrinks a little", () => {
    expect(fitWidth(true, 350, 1440, 694, 10, true)).toBe(1348);
    expect(fitWidth(true, 350, 1440, 694, 10, false)).toBe(1440);
    expect(fitWidth(true, 300, 1440, 694, 10, true)).toBe(1440);
  });
});

describe("wholeGeometry", () => {
  it("centres the flat map on a wide stage", () => {
    const g = wholeGeometry(1440, 640);
    expect(g.cy).toBe(320);
    expect(4 * g.s).toBeLessThanOrEqual(1440 - 24);
    expect(2 * g.s).toBeLessThanOrEqual(640 - 24);
  });

  it("lifts it towards the top of a tall stage, inside the stage", () => {
    const g = wholeGeometry(390, 454);
    expect(g.s).toBeCloseTo((390 - 24) / 4);
    expect(g.cy).toBeLessThan(227);
    expect(g.cy - g.s).toBeGreaterThan(0);
  });
});

describe("inView", () => {
  it("keeps what lands on the map, nearest the centre first", () => {
    const cands = [cand({ id: "far", ra: 1 }), cand({ id: "mid", ra: 2 }), cand({ id: "off", ra: 3 })];
    const at: Record<string, { x: number; y: number } | null> = { far: { x: 5, y: 5 }, mid: { x: 50, y: 40 }, off: { x: 500, y: 5 } };
    const map = { screenOf: (p: { ra: number }) => at[cands.find((c) => c.ra === p.ra)!.id] };
    expect(inView(cands, map, { w: 100, h: 80 }).map((c) => c.id)).toEqual(["mid", "far"]);
  });
});

function epoch(i: number, det: number, mjd: number, kind: Epoch["kind"] = "visit"): Epoch {
  return { id: `e${i}`, kind, mjd_mid: mjd, mjd_start: mjd, mjd_end: mjd, det, data_url: "x.bin", scale: 1, zero: 0 } as unknown as Epoch;
}

describe("pickEpochs", () => {
  it("keeps the candidate's detector and spreads visits over the whole span", () => {
    const frames = { epochs: [0, 1, 2, 3, 4, 5, 6].map((i) => epoch(i, 3, 60000 + 180 * i)).concat([epoch(9, 1, 60100)]) };
    const got = pickEpochs(frames, 3, "months", 0, 4);
    expect(got.map((e) => e.id)).toEqual(["e0", "e2", "e4", "e6"]);
    expect(got.every((e) => e.det === 3)).toBe(true);
  });

  it("takes consecutive exposures round the candidate's moment on the hours clock", () => {
    const frames = { epochs: [0, 1, 2, 3, 4, 5, 6, 7].map((i) => epoch(i, 2, 60000 + i * 0.001, "exposure")) };
    expect(pickEpochs(frames, 2, "hours", 60000.0051, 4).map((e) => e.id)).toEqual(["e4", "e5", "e6", "e7"]);
    expect(pickEpochs(frames, 2, "hours", 60000.0021, 3).map((e) => e.id)).toEqual(["e1", "e2", "e3"]);
  });

  it("falls back to the best-covered detector, never mixing detectors", () => {
    const frames = { epochs: [epoch(0, 1, 60000), epoch(1, 1, 60180), epoch(2, 1, 60360), epoch(3, 4, 60000)] };
    const got = pickEpochs(frames, 6, "months", 0);
    expect(got.map((e) => e.det)).toEqual([1, 1, 1]);
  });
});

describe("the cut-out", () => {
  it("stays inside the frame", () => {
    expect(cropWindow(200, 200, 100, 100, 72)).toEqual({ x0: 64, y0: 64, w: 72, h: 72 });
    expect(cropWindow(200, 200, 3, 198, 72)).toEqual({ x0: 0, y0: 128, w: 72, h: 72 });
    expect(cropWindow(40, 30, 20, 15, 72)).toEqual({ x0: 0, y0: 0, w: 40, h: 30 });
  });

  it("copies the window's rows", () => {
    const nx = 4;
    const values = Float32Array.from({ length: 16 }, (_, i) => i);
    expect([...cropValues(values, nx, { x0: 1, y0: 2, w: 2, h: 2 })]).toEqual([9, 10, 13, 14]);
  });

  it("captions each frame", () => {
    expect(epochCaption({ kind: "visit", mjd_mid: 61117 }, 1, 3)).toBe("Visit 2 of 3, 18 Mar 2026");
    expect(epochCaption({ kind: "exposure", mjd_mid: 61117.5 }, 0, 4)).toMatch(/^Exposure 1 of 4, 18 Mar 2026, 12:00 UTC$/);
  });
});
