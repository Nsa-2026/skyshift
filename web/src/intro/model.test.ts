import { describe, expect, it } from "vitest";
import { CHAPTERS, chapterAt, sceneAt, scrollProgress } from "./model";

describe("scroll drives the opening", () => {
  it("maps native scrolling to the whole timeline, clamping overscroll and short pages", () => {
    expect(scrollProgress(0, 800, 5200)).toBe(0);
    expect(scrollProgress(2200, 800, 5200)).toBe(0.5);
    expect(scrollProgress(4400, 800, 5200)).toBe(1);
    expect(scrollProgress(-90, 800, 5200)).toBe(0);
    expect(scrollProgress(9000, 800, 5200)).toBe(1);
    expect(scrollProgress(0, 800, 800)).toBe(0);
  });
  it("scrubs both ways with no hidden state and no jumps at the cuts", () => {
    const before = sceneAt(0.64);
    [0.9, 0.1, 1, 0.32, 0.7].forEach(sceneAt);
    expect(sceneAt(0.64)).toEqual(before);
    for (const cut of [0, 0.25, 0.45, 0.7, 0.9, 1]) {
      const a = sceneAt(cut - 1e-7);
      const b = sceneAt(cut + 1e-7);
      for (const key of ["earth", "ground", "figure", "face", "scope", "aperture", "clouds", "stars", "descent", "chrome", "veil"] as const) {
        expect(Math.abs(a[key] - b[key])).toBeLessThan(0.001);
        expect(b[key]).toBeGreaterThanOrEqual(0);
        expect(b[key]).toBeLessThanOrEqual(1);
      }
    }
  });
  it("starts on the title and ends faded out, figure gone", () => {
    expect(sceneAt(0)).toMatchObject({ title: 1, earth: 1, figure: 0, veil: 1 });
    expect(sceneAt(1)).toMatchObject({ title: 0, earth: 0, figure: 0, veil: 0 });
    expect(sceneAt(0.91).veil).toBe(1);
    expect(sceneAt(0.91).chrome).toBe(0);
  });
  it("captions each chapter in order, none over the title or the fade", () => {
    expect(chapterAt(0)).toBe(-1);
    expect(chapterAt(0.2)).toBe(0);
    expect(chapterAt(0.3)).toBe(1);
    expect(chapterAt(0.5)).toBe(2);
    expect(chapterAt(0.8)).toBe(3);
    expect(chapterAt(0.95)).toBe(-1);
    expect(CHAPTERS).toHaveLength(4);
  });
  it("is in English only", () => {
    for (const line of CHAPTERS) expect(line).not.toMatch(/[　-鿿＀-￯]/);
  });
});
