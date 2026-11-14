import { describe, expect, it } from "vitest";
import { ARROW_MAX_PX, ARROW_MIN_PX, MARK_R, arrowLengthPx, diamond, moverTick, nearestHit, paToScreen, paToScreenNorthUp, starArrow } from "./marks";

const near = (a: { x: number; y: number }, x: number, y: number) => {
  expect(a.x).toBeCloseTo(x, 6);
  expect(a.y).toBeCloseTo(y, 6);
};

describe("paToScreen", () => {
  it("points north up and east left on a plain sky map", () => {
    near(paToScreenNorthUp(0), 0, -1);
    near(paToScreenNorthUp(90), -1, 0);
    near(paToScreenNorthUp(180), 0, 1);
    near(paToScreenNorthUp(270), 1, 0);
    near(paToScreenNorthUp(45), -Math.SQRT1_2, -Math.SQRT1_2);
  });

  it("follows a rotated, mirrored or stretched frame", () => {
    // Galactic-style rotation: north to the right, east up.
    near(paToScreen(0, { x: 1, y: 0 }, { x: 0, y: -1 })!, 1, 0);
    near(paToScreen(90, { x: 1, y: 0 }, { x: 0, y: -1 })!, 0, -1);
    // East to the right (mirrored), axes of any length.
    near(paToScreen(90, { x: 0, y: -3 }, { x: 5, y: 0 })!, 1, 0);
  });

  it("gives up on a degenerate frame or a broken angle", () => {
    expect(paToScreen(10, { x: 0, y: 0 }, { x: 1, y: 0 })).toBeNull();
    expect(paToScreen(NaN, { x: 0, y: -1 }, { x: -1, y: 0 })).toBeNull();
  });
});

describe("arrowLengthPx", () => {
  it("is the motion over 100 years, kept readable", () => {
    // 1″/yr × 100 yr = 100″; at 0.2 px per arcsec that is 20 px.
    expect(arrowLengthPx(1000, 0.2)).toBeCloseTo(20);
    expect(arrowLengthPx(10, 0.2)).toBe(ARROW_MIN_PX);
    expect(arrowLengthPx(10_000, 0.2)).toBe(ARROW_MAX_PX);
    expect(arrowLengthPx(null, 0.2)).toBe(ARROW_MIN_PX);
    expect(arrowLengthPx(1000, 0)).toBe(ARROW_MIN_PX);
  });
});

describe("mark geometry", () => {
  it("starts a mover's tick just outside its circle, along the motion", () => {
    const [a, b] = moverTick(100, 100, { x: 1, y: 0 });
    near(a, 100 + MARK_R + 1.5, 100);
    near(b, 100 + MARK_R + 7.5, 100);
    // Unknown motion: a tick to the upper right.
    const [c] = moverTick(0, 0, null);
    expect(c.x).toBeGreaterThan(0);
    expect(c.y).toBeLessThan(0);
  });

  it("draws a moving star's arrow from the ring to its tip, barbs behind the tip", () => {
    const { shaft, barbs } = starArrow(50, 50, { x: 0, y: -1 }, 20);
    near(shaft[0], 50, 50 - (MARK_R + 1));
    near(shaft[1], 50, 50 - (MARK_R + 20));
    for (const b of barbs) expect(b.y).toBeGreaterThan(shaft[1].y);
    expect(barbs[0].x + barbs[1].x).toBeCloseTo(100);
  });

  it("puts a diamond's corners round its centre", () => {
    const d = diamond(10, 20, 5);
    expect(d).toHaveLength(4);
    near(d[0], 10, 15);
    near(d[2], 10, 25);
  });
});

describe("nearestHit", () => {
  const pts = [
    { id: "a", x: 10, y: 10 },
    { id: "b", x: 30, y: 10 },
  ];

  it("picks the closest point within reach", () => {
    expect(nearestHit(pts, 12, 11)?.id).toBe("a");
    expect(nearestHit(pts, 26, 10)?.id).toBe("b");
    expect(nearestHit(pts, 20, 40)).toBeNull();
    expect(nearestHit(pts, 20, 10, 5)).toBeNull();
    expect(nearestHit([], 0, 0)).toBeNull();
  });
});
