import { describe, expect, it } from "vitest";
import { ang2pixNest } from "./healpix";
import { binCandidates, cellOutline, rampAlpha, rampIndex, rampLabels, rampSteps, zoneBoxes, zoneLabelAnchor, zoneOutline } from "./heat";
import { sepDeg } from "./sphere";
import { cand } from "./testdata";

describe("binCandidates", () => {
  const cands = [
    cand({ id: "a", ra: 122.5, dec: 17.0 }),
    cand({ id: "b", ra: 122.6, dec: 17.1, kind: "high_pm" }),
    cand({ id: "c", ra: 122.55, dec: 17.05, kind: "high_pm" }),
    cand({ id: "d", ra: 300, dec: -40 }),
    cand({ id: "e", ra: NaN, dec: 0 }),
  ];

  it("counts candidates per cell, busiest first, and skips unplaceable ones", () => {
    const cells = binCandidates(cands, 3);
    expect(cells.map((c) => c.count)).toEqual([3, 1]);
    expect(cells[0].pix).toBe(ang2pixNest(3, 122.5, 17.0));
    expect(cells[0].byKind).toEqual({ mover: 1, high_pm: 2, spectral: 0 });
    expect(cells.reduce((n, c) => n + c.count, 0)).toBe(4);
  });

  it("puts each cell's centre inside the cell", () => {
    for (const c of binCandidates(cands, 5)) expect(ang2pixNest(5, c.center.ra, c.center.dec)).toBe(c.pix);
  });

  it("counts only the kinds shown", () => {
    expect(binCandidates(cands, 3, ["high_pm"]).map((c) => c.count)).toEqual([2]);
    expect(binCandidates(cands, 3, [])).toEqual([]);
  });

  it("gives a closed outline round the centre", () => {
    const [cell] = binCandidates(cands, 4);
    const ring = cellOutline(cell, 3);
    expect(ring).toHaveLength(12);
    for (const [ra, dec] of ring) expect(sepDeg({ ra, dec }, cell.center)).toBeLessThan(4);
  });
});

describe("the ramp", () => {
  it("steps 1–3–10–30–100 up to the largest count", () => {
    expect(rampSteps(0)).toEqual([1]);
    expect(rampSteps(1)).toEqual([1]);
    expect(rampSteps(2)).toEqual([1]);
    expect(rampSteps(3)).toEqual([1, 3]);
    expect(rampSteps(48)).toEqual([1, 3, 10, 30]);
    expect(rampSteps(1000)).toEqual([1, 3, 10, 30, 100, 300, 1000]);
  });

  it("puts each count on its step", () => {
    const s = rampSteps(48);
    expect(rampIndex(0, s)).toBe(-1);
    expect(rampIndex(1, s)).toBe(0);
    expect(rampIndex(2, s)).toBe(0);
    expect(rampIndex(3, s)).toBe(1);
    expect(rampIndex(29, s)).toBe(2);
    expect(rampIndex(48, s)).toBe(3);
  });

  it("darkens with the step, from a wash to a smudge", () => {
    expect(rampAlpha(-1, 4)).toBe(0);
    expect(rampAlpha(0, 4)).toBeCloseTo(0.16);
    expect(rampAlpha(3, 4)).toBeCloseTo(0.78);
    expect(rampAlpha(1, 4)).toBeGreaterThan(rampAlpha(0, 4));
    expect(rampAlpha(0, 1)).toBeGreaterThan(0.3);
  });

  it("labels each step in words", () => {
    expect(rampLabels([1, 3, 10, 30])).toEqual(["1–2", "3–9", "10–29", "30 or more"]);
    expect(rampLabels([1])).toEqual(["1 or more"]);
    expect(rampLabels([1, 2])).toEqual(["1", "2 or more"]);
  });
});

describe("zoneBoxes", () => {
  it("draws a padded box round each zone, busiest first", () => {
    const boxes = zoneBoxes(
      [
        cand({ id: "a", ra: 120.5, dec: 14.4 }),
        cand({ id: "b", ra: 124.1, dec: 17.9 }),
        cand({ id: "c", ra: 122, dec: 16 }),
        cand({ id: "h", ra: 10, dec: -30, zone: "hunt" }),
      ],
      0.4,
    );
    expect(boxes.map((b) => [b.zone, b.count])).toEqual([
      ["pilot", 3],
      ["hunt", 1],
    ]);
    const p = boxes[0];
    expect(p.dec0).toBeCloseTo(14.0);
    expect(p.dec1).toBeCloseTo(18.3);
    expect(p.ra0).toBeLessThan(120.5);
    expect(p.ra1).toBeGreaterThan(124.1);
    expect(p.ra1 - p.ra0).toBeLessThan(5);
  });

  it("keeps a zone across RA 0 narrow", () => {
    const [b] = zoneBoxes([cand({ id: "a", ra: 359, dec: 0 }), cand({ id: "b", ra: 1, dec: 1 }), cand({ id: "c", ra: 0.2, dec: 0.5 })], 0.4);
    expect(b.ra1 - b.ra0).toBeLessThan(4);
    expect(b.ra0).toBeLessThan(359);
    expect(b.ra1).toBeGreaterThan(360);
  });

  it("outlines the box as a ring that follows its edges", () => {
    const [b] = zoneBoxes([cand({ id: "a", ra: 120.5, dec: 14.4 }), cand({ id: "b", ra: 124.1, dec: 17.9 })], 0.4);
    const ring = zoneOutline(b, 0.5);
    expect(ring.length).toBeGreaterThan(20);
    const decs = ring.map(([, d]) => d);
    expect(Math.min(...decs)).toBeCloseTo(b.dec0);
    expect(Math.max(...decs)).toBeCloseTo(b.dec1);
    for (const [ra] of ring) expect(ra >= 0 && ra < 360).toBe(true);
    // The label sits on the south-east corner: largest RA, lowest Dec.
    expect(zoneLabelAnchor(b)).toEqual({ ra: b.ra1, dec: b.dec0 });
  });
});
