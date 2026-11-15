import { describe, expect, it } from "vitest";
import { aroundBox, aroundPoint, boundsOf, boxesHit, estimateSize, placeLabels, type Box, type LabelSpec } from "./labels";

const MAP: Box = { x0: 0, y0: 0, x1: 1000, y1: 600 };
/** 8 px a letter, 16 px tall: easy sums. */
const measure = (t: string) => ({ w: t.length * 8, h: 16 });

function target(id: string, x: number, y: number, texts = [id]): LabelSpec {
  return { id, texts, anchor: { x, y }, places: (s) => aroundPoint(x, y, s) };
}

describe("placeLabels", () => {
  it("puts a name right of its mark when the space is free", () => {
    const [p] = placeLabels([target("Barnard", 100, 100)], measure, [], MAP);
    expect(p.box).toEqual({ x0: 111, y0: 92, x1: 111 + 56, y1: 108 });
  });

  it("moves a name off a zone: WISE 0855 no longer sits under the hunt zone", () => {
    const zone: Box = { x0: 120, y0: 60, x1: 200, y1: 140 };
    const [p] = placeLabels([target("WISE 0855", 100, 100)], measure, [zone], MAP);
    expect(boxesHit(p.box, zone)).toBe(false);
    expect(p.box.x1).toBeLessThanOrEqual(100); // the left side
  });

  it("ignores an area the object itself sits in", () => {
    const zone: Box = { x0: 0, y0: 0, x1: 400, y1: 400 };
    const [p] = placeLabels([target("Inside", 100, 100)], measure, [zone], MAP);
    expect(p.box.x0).toBe(111);
  });

  it("keeps labels off each other: the later one moves", () => {
    const got = placeLabels([target("Elpis", 300, 300), target("RW Aur", 318, 300)], measure, [], MAP);
    expect(got).toHaveLength(2);
    expect(boxesHit(got[0].box, got[1].box, 3)).toBe(false);
  });

  it("stays on the map: a name near the right edge goes left", () => {
    const [p] = placeLabels([target("Eps Indi B", 990, 300)], measure, [], MAP);
    expect(p.box.x1).toBeLessThanOrEqual(1000);
    expect(p.box.x1).toBeLessThan(990);
  });

  it("shortens a label, then leaves it out, when nothing fits", () => {
    // A mark boxed in on every side but a short gap to its right.
    const walls: Box[] = [
      { x0: 0, y0: 0, x1: 1000, y1: 88 },
      { x0: 0, y0: 112, x1: 1000, y1: 600 },
      { x0: 0, y0: 0, x1: 95, y1: 600 },
      { x0: 160, y0: 0, x1: 1000, y1: 600 },
    ];
    const got = placeLabels([target("long", 100, 100, ["A long name", "Short"])], measure, walls, MAP);
    expect(got.map((p) => p.text)).toEqual(["Short"]);
    const none = placeLabels([target("x", 100, 100, ["A long name", "Still too long"])], measure, walls, MAP);
    expect(none).toEqual([]);
  });

  it("keeps a zone's badge beside its outline and off the controls", () => {
    const zone: Box = { x0: 500, y0: 280, x1: 540, y1: 320 };
    const panel: Box = { x0: 0, y0: 0, x1: 460, y1: 140 };
    const spec: LabelSpec = {
      id: "hunt",
      texts: ["Hunt and pilot zones, 272 to check"],
      anchor: { x: 520, y: 300 },
      places: (s) => aroundBox(zone, s, MAP),
    };
    const [p] = placeLabels([spec], measure, [zone, panel], MAP);
    expect(boxesHit(p.box, zone)).toBe(false);
    expect(boxesHit(p.box, panel)).toBe(false);
    expect(p.box.x1).toBeLessThanOrEqual(492); // east of it: screen left
  });
});

describe("aroundBox", () => {
  it("offers the visible part's corners when the area covers the map", () => {
    const places = aroundBox({ x0: -500, y0: -500, x1: 2000, y1: 2000 }, { w: 100, h: 20 }, MAP);
    const inside = places.filter((b) => b.x0 >= 0 && b.y0 >= 0 && b.x1 <= 1000 && b.y1 <= 600);
    expect(inside.length).toBeGreaterThan(0);
    expect(inside[0]).toEqual({ x0: 8, y0: 8, x1: 108, y1: 28 });
  });

  it("offers nothing inside an area too small for the badge", () => {
    const places = aroundBox({ x0: 10, y0: 10, x1: 40, y1: 40 }, { w: 100, h: 20 }, MAP);
    expect(places).toHaveLength(8);
  });
});

describe("helpers", () => {
  it("bounds points and sizes text without a canvas", () => {
    expect(boundsOf([{ x: 3, y: 9 }, { x: -1, y: 4 }])).toEqual({ x0: -1, y0: 4, x1: 3, y1: 9 });
    expect(boundsOf([])).toBeNull();
    expect(estimateSize("abcd", 10, 8, 22)).toEqual({ w: 38, h: 22 });
  });
});
