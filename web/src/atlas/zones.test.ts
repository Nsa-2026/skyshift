import { describe, expect, it } from "vitest";
import type { ZoneBox } from "./heat";
import { areaAt, areaFov, areaName, badgeTexts, boxContains, boxesOverlap, densifyRing, unionRings, zoneAreas } from "./zones";

const box = (zone: string, count: number, ra0: number, ra1: number, dec0: number, dec1: number): ZoneBox => ({
  zone,
  count,
  ra0,
  ra1,
  dec0,
  dec1,
  center: { ra: (ra0 + ra1) / 2, dec: (dec0 + dec1) / 2 },
});

/** A ring as a set of "x,y" corners, order- and start-free. */
const corners = (ring: [number, number][]) => new Set(ring.map(([x, y]) => `${x},${y}`));

/** Shoelace area, positive when anticlockwise (y up). */
const signedArea = (ring: [number, number][]) =>
  ring.reduce((s, [x0, y0], k) => {
    const [x1, y1] = ring[(k + 1) % ring.length];
    return s + (x0 * y1 - x1 * y0) / 2;
  }, 0);

describe("unionRings", () => {
  it("draws one box as its four corners, anticlockwise", () => {
    const rings = unionRings([{ ra0: 0, ra1: 2, dec0: 0, dec1: 1 }]);
    expect(rings).toHaveLength(1);
    expect(corners(rings[0])).toEqual(new Set(["0,0", "2,0", "2,1", "0,1"]));
    expect(signedArea(rings[0])).toBeCloseTo(2);
  });

  it("swallows a box inside another: one outline, no box on top", () => {
    const rings = unionRings([
      { ra0: 120.1, ra1: 130.9, dec0: 14.1, dec1: 24.7 },
      { ra0: 120.13, ra1: 124.5, dec0: 14.14, dec1: 18.3 },
    ]);
    expect(rings).toHaveLength(1);
    expect(corners(rings[0])).toEqual(new Set(["120.1,14.1", "130.9,14.1", "130.9,24.7", "120.1,24.7"]));
  });

  it("joins two overlapping boxes into one eight-cornered outline", () => {
    const rings = unionRings([
      { ra0: 0, ra1: 4, dec0: 0, dec1: 4 },
      { ra0: 2, ra1: 6, dec0: 2, dec1: 6 },
    ]);
    expect(rings).toHaveLength(1);
    expect(rings[0]).toHaveLength(8);
    expect(corners(rings[0])).toEqual(new Set(["0,0", "4,0", "4,2", "6,2", "6,6", "2,6", "2,4", "0,4"]));
    expect(signedArea(rings[0])).toBeCloseTo(16 + 16 - 4);
  });

  it("keeps boxes apart as separate rings", () => {
    const rings = unionRings([
      { ra0: 0, ra1: 1, dec0: 0, dec1: 1 },
      { ra0: 5, ra1: 6, dec0: 0, dec1: 1 },
    ]);
    expect(rings).toHaveLength(2);
  });

  it("traces a hole as its own clockwise ring", () => {
    const rings = unionRings([
      { ra0: 0, ra1: 3, dec0: 0, dec1: 1 },
      { ra0: 0, ra1: 3, dec0: 2, dec1: 3 },
      { ra0: 0, ra1: 1, dec0: 0, dec1: 3 },
      { ra0: 2, ra1: 3, dec0: 0, dec1: 3 },
    ]);
    expect(rings).toHaveLength(2);
    const areas = rings.map(signedArea).sort((a, b) => a - b);
    expect(areas[0]).toBeCloseTo(-1);
    expect(areas[1]).toBeCloseTo(9);
  });

  it("ignores empty boxes", () => {
    expect(unionRings([{ ra0: 1, ra1: 1, dec0: 0, dec1: 2 }])).toEqual([]);
  });
});

describe("zoneAreas", () => {
  it("makes the hunt zone and the pilot inside it one area with one count", () => {
    const areas = zoneAreas([box("pilot", 42, 120.13, 124.5, 14.14, 18.3), box("hunt", 230, 120.1, 130.9, 14.1, 24.7)]);
    expect(areas).toHaveLength(1);
    expect(areas[0].zones).toEqual(["hunt", "pilot"]);
    expect(areas[0].count).toBe(272);
    expect(areas[0].rings).toHaveLength(1);
    expect(areas[0].rings[0]).toHaveLength(4);
    expect(areas[0].center.ra).toBeCloseTo(125.5);
    expect(areas[0].center.dec).toBeCloseTo(19.4);
  });

  it("keeps zones far apart as separate areas, the busiest first", () => {
    const areas = zoneAreas([box("a", 3, 10, 12, 0, 2), box("b", 9, 200, 203, -5, -2)]);
    expect(areas.map((a) => a.id)).toEqual(["b", "a"]);
  });

  it("joins zones across RA 0", () => {
    const areas = zoneAreas([box("west", 5, 355, 361, 0, 3), box("east", 4, 0.5, 4, 1, 4)]);
    expect(areas).toHaveLength(1);
    expect(areas[0].bounds.ra0).toBe(355);
    expect(areas[0].bounds.ra1).toBe(364);
    expect(areas[0].center.ra).toBeCloseTo(359.5);
  });
});

describe("zone geometry", () => {
  it("tests overlap and containment modulo 360", () => {
    expect(boxesOverlap({ ra0: 358, ra1: 362, dec0: 0, dec1: 1 }, { ra0: 1, ra1: 3, dec0: 0.5, dec1: 2 })).toBe(true);
    expect(boxesOverlap({ ra0: 10, ra1: 12, dec0: 0, dec1: 1 }, { ra0: 10, ra1: 12, dec0: 2, dec1: 3 })).toBe(false);
    expect(boxContains({ ra0: 358, ra1: 362, dec0: 0, dec1: 1 }, { ra: 1, dec: 0.5 })).toBe(true);
    expect(boxContains({ ra0: 358, ra1: 362, dec0: 0, dec1: 1 }, { ra: 5, dec: 0.5 })).toBe(false);
  });

  it("finds the area under a point", () => {
    const areas = zoneAreas([box("hunt", 230, 120.1, 130.9, 14.1, 24.7)]);
    expect(areaAt(areas, { ra: 125, dec: 19 })?.id).toBe("hunt");
    expect(areaAt(areas, { ra: 133.8, dec: -7.2 })).toBeNull();
  });

  it("densifies a ring so its sides follow the sky, RA wrapped", () => {
    const pts = densifyRing([
      [359, 0],
      [361, 0],
      [361, 1],
      [359, 1],
    ], 0.5);
    expect(pts).toHaveLength(4 + 2 + 4 + 2);
    expect(pts.every(([ra]) => ra >= 0 && ra < 360)).toBe(true);
    expect(pts[2]).toEqual([0, 0]);
  });

  it("names areas and writes the badge in words", () => {
    expect(areaName({ zones: ["hunt"] })).toBe("Hunt zone");
    expect(areaName({ zones: ["hunt", "pilot"] })).toBe("Hunt and pilot zones");
    expect(areaName({ zones: ["hunt", "pilot", "ecl13"] })).toBe("Hunt, pilot and ecl13 zones");
    expect(badgeTexts({ zones: ["hunt"], count: 230 })).toEqual(["Hunt zone, 230 to check", "230 to check"]);
    expect(badgeTexts({ zones: ["hunt", "pilot"], count: 1272 })[0]).toBe("Hunt and pilot zones, 1,272 to check");
  });

  it("opens an area with room round it, within limits", () => {
    const hunt = { bounds: { ra0: 120.1, ra1: 130.9, dec0: 14.1, dec1: 24.7 } };
    // A phone-shaped map: the width sets the field.
    expect(areaFov(hunt, 0.5)).toBeCloseTo(14.3, 0);
    // A wide map (1440 × 823): the height does, so the zone's top and bottom stay in view.
    expect(areaFov(hunt, 1440 / 823)).toBeCloseTo(23.2, 0);
    expect(areaFov({ bounds: { ra0: 0, ra1: 0.1, dec0: 0, dec1: 0.1 } })).toBe(2);
    expect(areaFov({ bounds: { ra0: 0, ra1: 90, dec0: 0, dec1: 60 } })).toBe(45);
  });
});
