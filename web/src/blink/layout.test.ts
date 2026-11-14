// Circle and ring sizes (web/DESIGN.md, "Pencil marks"): each circle as big as its source looks,
// each Gaia ring a little bigger, one mark per bright star, one circle per blend.
import { describe, expect, it } from "vitest";
import { BLEND_ARCSEC, circleRadius, planMarks, ringGap, type PlanDet, type PlanOptions, type PlanStar } from "./layout";
import { psfExtent, unstretch, visibleExtent } from "./math";

/** A 61 × 61 sky of noise σ = 1 (deterministic) with Gaussian stars; `burnt` cores read NaN. */
function plate(stars: { x: number; y: number; peak: number; sat?: number }[], n = 61, fwhm = 3) {
  const s = fwhm / 2.3548;
  const vals = new Float32Array(n * n);
  const burnt = new Uint8Array(n * n);
  for (let y = 0; y < n; y++)
    for (let x = 0; x < n; x++) {
      let v = ((x * 7 + y * 13) % 5) * 0.4 - 0.8; // −0.8 … 0.8, mean 0
      for (const st of stars) v += st.peak * Math.exp(-((x - st.x) ** 2 + (y - st.y) ** 2) / (2 * s * s));
      const k = y * n + x;
      if (stars.some((st) => st.sat != null && v > st.sat)) {
        vals[k] = NaN;
        burnt[k] = 1;
      } else vals[k] = v;
    }
  return { vals, burnt, n };
}
const opts = { floor: 2, sigma: 1, fwhm: 3, maxR: 40 };

describe("how far a source visibly reaches", () => {
  it("is 0 on empty sky, small for a faint star and larger for a bright one", () => {
    const p = plate([
      { x: 15, y: 30, peak: 8 },
      { x: 45, y: 30, peak: 800 },
    ]);
    expect(visibleExtent(p.vals, p.burnt, p.n, p.n, 30, 10, opts)).toBe(0);
    const faint = visibleExtent(p.vals, p.burnt, p.n, p.n, 15, 30, opts);
    const bright = visibleExtent(p.vals, p.burnt, p.n, p.n, 45, 30, opts);
    expect(faint).toBeGreaterThan(0.5);
    expect(faint).toBeLessThan(3);
    // A Gaussian of FWHM 3 falls from 800 to 2 at r = σ√(2 ln 400) ≈ 4.4 px.
    expect(bright).toBeGreaterThan(3.8);
    expect(bright).toBeLessThan(5.2);
  });

  it("runs through a saturated (burnt) core to the edge of its wings", () => {
    const p = plate([{ x: 30, y: 30, peak: 1e5, sat: 2000 }]);
    expect(p.burnt[30 * 61 + 30]).toBe(1);
    const r = visibleExtent(p.vals, p.burnt, p.n, p.n, 30, 30, opts);
    // 1e5 → 2 at σ√(2 ln 5e4) ≈ 5.9 px.
    expect(r).toBeGreaterThan(5.2);
    expect(r).toBeLessThan(6.6);
  });

  it("doesn't swallow a neighbour: a ray stops where the profile rises again", () => {
    const alone = plate([{ x: 30, y: 30, peak: 300 }]);
    const pair = plate([
      { x: 30, y: 30, peak: 300 },
      { x: 37, y: 30, peak: 3000 },
    ]);
    const a = visibleExtent(alone.vals, alone.burnt, 61, 61, 30, 30, opts);
    const b = visibleExtent(pair.vals, pair.burnt, 61, 61, 30, 30, opts);
    expect(b).toBeLessThanOrEqual(a + 0.5);
  });

  it("models the reach from flux and the PSF when the plate can't be read", () => {
    expect(psfExtent(0, 7.53, 6.15, 0.01)).toBe(0);
    const faint = psfExtent(200, 7.53, 6.15, 0.05);
    const bright = psfExtent(200_000, 7.53, 6.15, 0.05);
    expect(bright).toBeGreaterThan(faint);
    // 200 mJy at FWHM 7.53″: peak ≈ 200000 / (2π 3.2²) / 23.5 ≈ 132 MJy/sr → 2.4 px at 0.05 MJy/sr.
    expect(bright).toBeCloseTo(2.04, 1);
  });

  it("inverts the stretch, so the display's visible grey is a level", () => {
    const lim = { lo: 1, hi: 11 };
    expect(unstretch(0, lim, "linear")).toBe(1);
    expect(unstretch(0.5, lim, "linear")).toBe(6);
    expect(unstretch(1, lim, "asinh")).toBeCloseTo(11, 6);
  });
});

const O: PlanOptions = { pxPerArcsec: 2, floor: 5, brightExtent: 20, measured: true };
const star = (id: string, X: number, Y: number, extent: number, more: Partial<PlanStar> = {}): PlanStar => ({
  id,
  X,
  Y,
  g: 14,
  extent,
  saturated: false,
  ringed: true,
  ...more,
});
const det = (i: number, X: number, Y: number, extent: number, more: Partial<PlanDet> = {}): PlanDet => ({
  i,
  X,
  Y,
  cls: "star",
  extent,
  gaiaId: null,
  reasons: [],
  ...more,
});

describe("circle and ring sizes", () => {
  it("a circle sits just outside its source, never under the floor (half the PSF FWHM)", () => {
    expect(circleRadius(0, 5)).toBe(5);
    expect(circleRadius(10, 5)).toBe(12);
    const p = planMarks([], [det(0, 50, 50, 0), det(1, 150, 50, 16)], O);
    expect(p.circles.map((c) => c.r)).toEqual([5, 18]);
  });

  it("a Gaia ring is always a little bigger than the circle it holds, offset included", () => {
    const p = planMarks([star("g", 100, 100, 6)], [det(0, 103, 100, 10, { gaiaId: "g" })], O);
    const c = p.circles[0];
    const r = p.rings[0];
    expect(c.star).toBe("g");
    expect(c.offsetArcsec).toBeCloseTo(1.5, 6);
    expect(r.r).toBeGreaterThanOrEqual(c.r + 3 + ringGap(c.r) - 1e-9);
    // An unmatched ring is sized like the circle it would hold, plus the gap.
    const lone = planMarks([star("h", 10, 10, 6)], [], O).rings[0];
    expect(lone.r).toBe(circleRadius(6, 5) + ringGap(circleRadius(6, 5)));
  });

  it("a circle never swallows a neighbouring detection", () => {
    const p = planMarks([], [det(0, 100, 100, 30), det(1, 120, 100, 0)], O);
    expect(p.circles[0].r).toBe(15); // 0.75 × 20 px, not 32
  });
});

describe("one big star is never many small ones", () => {
  it("hides the fragments around a saturated star with no detection, and draws one dashed ring sized to its blob", () => {
    const p = planMarks(
      [star("barnard", 100, 100, 50, { g: 8.2, saturated: true, ringed: false })],
      [det(0, 130, 100, 3, { cls: "other" }), det(1, 100, 70, 2, { cls: "artifact" }), det(2, 200, 200, 4, { cls: "other" })],
      O,
    );
    expect([...p.hidden.keys()].sort()).toEqual([0, 1]);
    expect(p.circles.map((c) => c.i)).toEqual([2]);
    expect(p.big).toEqual([{ id: "barnard", X: 100, Y: 100, r: 52, saturated: true, fragments: 2 }]);
  });

  it("uses the server's bright_fragment reason and parent when given", () => {
    const p = planMarks(
      [star("a", 100, 100, 50, { saturated: true }), star("b", 400, 400, 2)],
      [det(0, 300, 300, 3, { cls: "artifact", reasons: ["bright_fragment"], parentGaiaId: "a" })],
      O,
    );
    expect(p.hidden.get(0)).toBe("a");
    expect(p.big[0].fragments).toBe(1);
  });

  it("sizes a saturated star's own detection (within 6″) to its whole blob, with no dashed ring", () => {
    const p = planMarks(
      [star("s", 100, 100, 40, { saturated: true })],
      [det(0, 104, 100, 8, { gaiaId: "s" }), det(1, 120, 100, 2, { cls: "other" })],
      O,
    );
    expect(p.big).toEqual([]);
    expect(p.circles).toHaveLength(1);
    expect(p.circles[0]).toMatchObject({ i: 0, r: 42, bright: "s", fragments: 1 });
    expect(p.rings[0].r).toBeGreaterThan(p.circles[0].r + 4);
  });

  it("keeps a real Gaia star inside a bright (unsaturated) star's glow", () => {
    const p = planMarks([star("big", 100, 100, 50, { g: 10 }), star("f", 130, 100, 2)], [det(0, 130, 100, 2, { gaiaId: "f" })], O);
    expect(p.hidden.size).toBe(0);
    expect(p.circles[0].star).toBe("f");
  });

  it("a fainter star inside a saturated star's blob is lost in its glow: no ring, no second dashed ring, its dot hidden", () => {
    // Barnard's Star in D4: faint Gaia stars under the burnt blob also see burnt pixels around them.
    const p = planMarks(
      [star("barnard", 100, 100, 50, { g: 8.2, saturated: true, ringed: false }), star("f", 120, 110, 45, { g: 15.5, saturated: true })],
      [det(0, 121, 110, 4, { gaiaId: "f" })],
      O,
    );
    expect(p.big.map((b) => b.id)).toEqual(["barnard"]);
    expect(p.rings).toEqual([]);
    expect(p.hidden.get(0)).toBe("barnard");
    expect(p.circles).toEqual([]);
  });

  it("draws Gaia rings only when the measured layer is off", () => {
    const p = planMarks([star("s", 100, 100, 40, { saturated: true })], [det(0, 130, 100, 2, { cls: "other" })], { ...O, measured: false });
    expect(p.big).toEqual([]);
    expect(p.circles).toEqual([]);
    expect(p.rings).toHaveLength(1);
  });
});

describe("blends", () => {
  const near = BLEND_ARCSEC * O.pxPerArcsec - 2; // just under 9″ apart
  it("one detection on two Gaia stars under 9″ apart: one circle around both, one ring with two dots", () => {
    const p = planMarks([star("a", 100, 100, 6), star("b", 100 + near, 100, 5, { g: 15 })], [det(0, 100 + near / 2, 100, 7, { gaiaId: "a" })], O);
    expect(p.circles).toHaveLength(1);
    const c = p.circles[0];
    expect(c.blend).toEqual(["a", "b"]);
    expect(c.r).toBeGreaterThanOrEqual(near / 2 + O.floor * 0.75);
    expect(p.rings).toHaveLength(1);
    expect(p.rings[0].blend!.map((b) => b.id)).toEqual(["a", "b"]);
    expect(p.rings[0].r).toBeGreaterThan(c.r);
  });

  it("is no blend when each star has its own detection, or the partner is far fainter", () => {
    const two = planMarks(
      [star("a", 100, 100, 6), star("b", 100 + near, 100, 5)],
      [det(0, 100, 100, 6, { gaiaId: "a" }), det(1, 100 + near, 100, 5, { gaiaId: "b" })],
      O,
    );
    expect(two.circles.every((c) => !c.blend)).toBe(true);
    expect(two.rings).toHaveLength(2);
    const faint = planMarks([star("a", 100, 100, 6, { g: 12 }), star("b", 100 + near, 100, 2, { g: 16.5 })], [det(0, 100, 100, 6, { gaiaId: "a" })], O);
    expect(faint.circles[0].blend).toBeUndefined();
  });

  it("reads the server's blend_gaia_ids when present", () => {
    const p = planMarks(
      [star("a", 100, 100, 6), star("b", 160, 100, 5)],
      [det(0, 100, 100, 6, { reasons: ["blend"], blendGaiaIds: ["a", "b"] })],
      O,
    );
    expect(p.circles[0].blend).toEqual(["a", "b"]);
  });
});
