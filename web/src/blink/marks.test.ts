// Which marks the plate draws: none over the no-data hatch, and only known objects SPHEREx can see.
import { describe, expect, it } from "vitest";
import { toMJySr } from "../api/client";
import type { KnownObject } from "../api/types";
import { SPHEREX_V_LIMIT, seenBySpherex } from "./known";
import { noDataAt } from "./math";

describe("no marks over the no-data area", () => {
  // A 6 × 4 frame whose right half is past the detector's edge (raw 0), as in the hours D6 frame
  // at RA 125.5, Dec 19.4 on 15 Apr 2026 (26% of it raw 0).
  const nx = 6;
  const ny = 4;
  const raw = Uint16Array.from({ length: nx * ny }, (_, i) => (i % nx < 3 ? 1000 : 0));
  const vals = toMJySr(raw, { scale: 0.001, zero: 0 });

  it("hides a position inside the hatch and keeps one inside the data", () => {
    expect(noDataAt(vals, null, nx, ny, 4.6, 1.2)).toBe(true);
    expect(noDataAt(vals, null, nx, ny, 0.8, 2.0)).toBe(false);
  });

  it("decides at the boundary by most of the 3 × 3 around the position", () => {
    expect(noDataAt(vals, null, nx, ny, 2.0, 1.0)).toBe(false); // on the last data column
    expect(noDataAt(vals, null, nx, ny, 3.0, 1.0)).toBe(true); // first column past the edge
  });

  it("ignores a lone masked pixel inside the data", () => {
    const v = toMJySr(Uint16Array.from({ length: 25 }, (_, i) => (i === 12 ? 0 : 500)), { scale: 0.001, zero: 0 });
    expect(noDataAt(v, null, 5, 5, 2, 2)).toBe(false);
  });

  it("treats a burnt (saturated) core as data, not as the hatch", () => {
    const v = toMJySr(Uint16Array.from({ length: 25 }, (_, i) => ([6, 7, 8, 11, 12, 13, 16, 17, 18].includes(i) ? 0 : 500)), {
      scale: 0.001,
      zero: 0,
    });
    const burnt = Uint8Array.from({ length: 25 }, (_, i) => (Number.isNaN(v[i]) ? 1 : 0));
    expect(noDataAt(v, null, 5, 5, 2, 2)).toBe(true);
    expect(noDataAt(v, burnt, 5, 5, 2, 2)).toBe(false);
  });

  it("judges a position just off the frame at the nearest edge", () => {
    expect(noDataAt(vals, null, nx, ny, -1.5, 1)).toBe(false); // west of the data
    expect(noDataAt(vals, null, nx, ny, 7.2, 1)).toBe(true); // east of the hatch
  });
});

describe("known objects SPHEREx can see", () => {
  const k = (name: string, v: number | null): KnownObject => ({ name, epoch_id: "e1", ra: 0, dec: 0, v, source: "mpc" });

  it("keeps V 18 and brighter, and comets (no V)", () => {
    expect(SPHEREX_V_LIMIT).toBe(18);
    expect(seenBySpherex(k("Tone", 15.6))).toBe(true);
    expect(seenBySpherex(k("edge", 18))).toBe(true);
    expect(seenBySpherex(k("3I/ATLAS", null))).toBe(true);
    expect(seenBySpherex(k("Tophergee", 18.97))).toBe(false);
    expect(seenBySpherex(k("(44439)", 20.73))).toBe(false);
  });

  it("drops the faint entries at RA 125.5, Dec 19.4 (hours, D6)", () => {
    // V of some live /api/frames known[] entries there (14 Nov): 4 of its 40 are V 18 or brighter.
    const vs = [18.97, 20.73, 15.97, 20.89, 15.6, 20.68, 20.49, 19.67];
    const list = vs.map((v, i) => k(`o${i}`, v)).filter(seenBySpherex);
    expect(list.map((o) => o.v)).toEqual([15.97, 15.6]);
  });
});
