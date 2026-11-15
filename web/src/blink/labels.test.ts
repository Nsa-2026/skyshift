// Where the plate's small words go (labels.ts): never on another word, an arrow or off the plate.
import { describe, expect, it } from "vitest";
import { besideArrow, boxAround, discCrossesBox, LabelSpace, segHitsBox, type Box } from "./labels";

const plate: Box = { x: 0, y: 0, w: 400, h: 400 };
const overlaps = (a: Box, b: Box) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

describe("geometry", () => {
  it("sets a word outside its mark, on the side asked", () => {
    const at = { X: 100, Y: 100, r: 10 };
    const right = boxAround(at, 0, 30, 10, 3);
    expect(right.x).toBeCloseTo(113, 6);
    expect(right.y).toBeCloseTo(95, 6);
    const below = boxAround(at, Math.PI / 2, 30, 10, 3);
    expect(below.y).toBeCloseTo(113, 6);
    expect(below.x).toBeCloseTo(85, 6);
    const upLeft = boxAround(at, (-3 * Math.PI) / 4, 30, 10, 3);
    expect(upLeft.x + upLeft.w).toBeLessThan(100);
    expect(upLeft.y + upLeft.h).toBeLessThan(100);
  });

  it("knows when a line crosses a box and when a circle's outline does", () => {
    const b = { x: 10, y: 10, w: 20, h: 10 };
    expect(segHitsBox({ x1: 0, y1: 15, x2: 40, y2: 15 }, b)).toBe(true);
    expect(segHitsBox({ x1: 0, y1: 0, x2: 40, y2: 0 }, b)).toBe(false);
    expect(segHitsBox({ x1: 20, y1: 12, x2: 22, y2: 14 }, b)).toBe(true); // wholly inside
    expect(discCrossesBox({ X: 20, Y: 15, r: 100 }, b)).toBe(false); // the box sits inside the circle
    expect(discCrossesBox({ X: 20, Y: 15, r: 8 }, b)).toBe(true);
    expect(discCrossesBox({ X: 200, Y: 15, r: 8 }, b)).toBe(false);
  });
});

describe("placing words", () => {
  it("puts two saturated labels of neighbouring stars side by side, not on top of each other", () => {
    // Two dashed rings close together: the old fixed spot (lower right) put both words in one place.
    const s = new LabelSpace(plate);
    s.addDisc({ X: 150, Y: 150, r: 20 });
    s.addDisc({ X: 165, Y: 160, r: 20 });
    const a = s.place({ X: 150, Y: 150, r: 20 }, 50, 12)!;
    const b = s.place({ X: 165, Y: 160, r: 20 }, 50, 12)!;
    expect(a).not.toBeNull();
    expect(b).not.toBeNull();
    expect(overlaps(a, b)).toBe(false);
  });

  it("keeps a grade letter off an arrow and its ×20", () => {
    // The ecl35 mover: a short arrow through its circle, its factor up and right.
    const s = new LabelSpace(plate);
    const at = { X: 200, Y: 200, r: 12 };
    s.addPath([
      { X: 196, Y: 204 },
      { X: 186, Y: 186 },
    ]);
    const factor = s.place({ X: 191, Y: 195, r: 0 }, 22, 11, 4, besideArrow({ X: 196, Y: 204 }, { X: 186, Y: 186 }))!;
    const grade = s.place(at, 9, 11, 2, [-Math.PI / 4, Math.PI / 4, (-3 * Math.PI) / 4, (3 * Math.PI) / 4])!;
    expect(factor).not.toBeNull();
    expect(grade).not.toBeNull();
    expect(overlaps(factor, grade)).toBe(false);
    for (const seg of s.segs) expect(segHitsBox(seg, grade, 0)).toBe(false);
  });

  it("prefers a spot that crosses no circle, and leaves the word out when nothing is free", () => {
    const s = new LabelSpace(plate);
    s.addDisc({ X: 140, Y: 120, r: 15 }); // a neighbour's ring just right of the mark
    const b = s.place({ X: 100, Y: 100, r: 10 }, 30, 10, 3, [0, Math.PI])!;
    expect(b.x + b.w).toBeLessThan(100); // went left
    const tight = new LabelSpace({ x: 0, y: 0, w: 40, h: 40 });
    expect(tight.place({ X: 20, Y: 20, r: 15 }, 30, 10)).toBeNull();
  });

  it("stays on the plate", () => {
    const s = new LabelSpace(plate);
    const b = s.place({ X: 390, Y: 390, r: 5 }, 40, 10)!;
    expect(b.x + b.w).toBeLessThanOrEqual(400);
    expect(b.y + b.h).toBeLessThanOrEqual(400);
  });

  it("writes an arrow's factor on the side of the shaft that faces up the plate", () => {
    const [first] = besideArrow({ X: 0, Y: 0 }, { X: 10, Y: 0 });
    expect(Math.sin(first)).toBeLessThan(0);
  });
});
