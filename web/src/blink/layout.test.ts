// Circle and ring sizes (web/DESIGN.md, "Pencil marks"): each circle as big as its source looks,
// each Gaia ring a little bigger, one mark per bright star, one circle per blend.
import { describe, expect, it } from "vitest";
import {
  BLEND_ARCSEC,
  circleRadius,
  GAIA_SEEN_G,
  glowHost,
  planMarks,
  ringGap,
  SAME_OBJECT_ARCSEC,
  sourceGaia,
  type PlanDet,
  type PlanOptions,
  type PlanStar,
} from "./layout";
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

  it("a circle never swallows a neighbouring detection: neighbours' circles meet", () => {
    const p = planMarks([], [det(0, 100, 100, 30), det(1, 120, 100, 0)], O);
    expect(p.circles[0].r).toBe(10); // half of 20 px, not 32
  });
});

describe("one big star is never many small ones", () => {
  it("hides the fragments around a saturated star with no detection, and draws one dashed ring sized to its blob", () => {
    const stars = [star("barnard", 100, 100, 50, { g: 8.2, saturated: true, ringed: false })];
    const dets = [det(0, 130, 100, 3, { cls: "other" }), det(1, 100, 70, 2, { cls: "artifact" }), det(2, 200, 200, 4, { cls: "other" })];
    const p = planMarks(stars, dets, { ...O, hideGlow: true });
    expect([...p.hidden.keys()].sort()).toEqual([0, 1]);
    expect(p.circles.map((c) => c.i)).toEqual([2]);
    expect(p.big).toEqual([{ id: "barnard", X: 100, Y: 100, r: 52, saturated: true, fragments: 2 }]);
    // Advanced: the no-Gaia source in its blob is drawn greyed ("may not be real"); the artefact stays a piece.
    const adv = planMarks(stars, dets, O);
    expect(adv.circles.map((c) => [c.i, c.glow])).toEqual([
      [0, "barnard"],
      [2, undefined],
    ]);
    expect([...adv.hidden.keys()]).toEqual([1]);
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

  it("a saturated star always gets its dashed ring, its own detection (within 6″) drawn as that ring, and the blue ring around it", () => {
    const p = planMarks(
      [star("s", 100, 100, 40, { saturated: true })],
      [det(0, 104, 100, 8, { gaiaId: "s" }), det(1, 120, 100, 2, { cls: "other" })],
      { ...O, hideGlow: true },
    );
    // One dashed ring sized to its whole blob, holding the detection; no circle of its own on top.
    expect(p.big).toEqual([{ id: "s", X: 100, Y: 100, r: 42, saturated: true, fragments: 1, det: 0 }]);
    expect(p.circles).toEqual([]);
    expect(p.hidden.get(1)).toBe("s");
    // The Gaia ring hugs the dashed ring: a circle inside a ring, never two circles.
    expect(p.rings).toHaveLength(1);
    expect(p.rings[0].r).toBeCloseTo(42 + ringGap(42), 6);
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
    // Off the burnt core, a star SkyShift measured is a separate star: its own circle and ring.
    const q = planMarks(
      [star("barnard", 100, 100, 50, { g: 8.2, saturated: true, ringed: false }), star("f", 140, 110, 8, { g: 16.9, found: true })],
      [det(0, 141, 110, 8, { cls: "stack", snr: 460 })],
      { ...O, hideGlow: true },
    );
    expect(q.rings.map((r) => r.id)).toEqual(["f"]);
    expect(q.circles.map((c) => [c.i, c.star])).toEqual([[0, "f"]]);
    // The plain Viewer doesn't draw stack-only sources (no detection passed): the star keeps its ring.
    const plain = planMarks([star("barnard", 100, 100, 50, { g: 8.2, saturated: true, ringed: false }), star("f", 140, 110, 8, { g: 16.9, found: true })], [], {
      ...O,
      hideGlow: true,
    });
    expect(plain.rings.map((r) => r.id)).toEqual(["f"]);
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
    // The far fainter partner is lost in a's light: one ring, not a second one over the first.
    expect(faint.rings.map((r) => r.id)).toEqual(["a"]);
  });

  it("one ring per blob with the measured layer off too: a close pair nobody detected apart is one ring with two dots", () => {
    const off = { ...O, measured: false };
    const p = planMarks([star("a", 100, 100, 6, { g: 13 }), star("b", 100 + near, 100, 5, { g: 14.5 })], [], off);
    expect(p.rings).toHaveLength(1);
    expect(p.rings[0].blend!.map((b) => b.id)).toEqual(["a", "b"]);
    expect(p.rings[0].r).toBeGreaterThan(near);
    // Detected apart (each matched in some photo): two rings, each no wider than half the way to the other plus the gap.
    const two = planMarks(
      [star("a", 100, 100, 12, { g: 13, detected: true }), star("b", 124, 100, 12, { g: 14.5, detected: true })],
      [],
      off,
    );
    expect(two.rings).toHaveLength(2);
    for (const r of two.rings) expect(r.r).toBeLessThanOrEqual(12 + ringGap(O.floor) + 1e-9);
  });

  it("a faint star inside a bright star's blob gets no ring of its own", () => {
    // Barnard's months plate: faint Gaia stars in a bright star's halo got rings over its blob.
    const p = planMarks([star("big", 100, 100, 18, { g: 11 }), star("f1", 112, 96, 4, { g: 16.2 }), star("f2", 90, 110, 4, { g: 13 })], [], {
      ...O,
      measured: false,
    });
    expect(p.rings).toHaveLength(1);
    expect(p.rings[0].id).toBe("big");
    expect(p.rings[0].blend!.map((b) => b.id)).toEqual(["big", "f2"]); // within 3 mag: a dot in its ring; f1: lost
  });

  it("a stack-only dot in a bright star's glow: hidden in the plain Viewer, greyed in Advanced", () => {
    const stars = [star("big", 100, 100, 30, { g: 10 })];
    const dets = [det(0, 115, 100, 2, { cls: "stack" }), det(1, 200, 200, 2, { cls: "stack" })];
    const plain = planMarks(stars, dets, { ...O, hideGlow: true });
    expect(plain.hidden.get(0)).toBe("big");
    expect(plain.circles.map((c) => c.i)).toEqual([1]);
    const adv = planMarks(stars, dets, O);
    expect(adv.circles.map((c) => [c.i, c.glow])).toEqual([
      [0, "big"],
      [1, undefined],
    ]);
    expect(adv.glows.map((g) => g.id)).toEqual(["big"]);
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

// The site owner's review (16 Nov): one object, one set of marks.
describe("one object, one set of marks", () => {
  const nested = (p: ReturnType<typeof planMarks>) =>
    p.circles.some((a) => p.circles.some((c) => a !== c && Math.hypot(a.X - c.X, a.Y - c.Y) < Math.max(a.r, c.r)));

  it("a source the server didn't match, on two Gaia stars, is drawn as those two stars (RW Aurigae, months, visit 1)", () => {
    // `other` (S/N 10, reason blend) between two G 17.8 stars 9″ apart, each also found on the stacks.
    const stars = [star("a", 100, 92, 0, { g: 17.84 }), star("b", 98, 110, 0, { g: 17.85 })];
    const dets = [
      det(0, 100, 100, 2, { cls: "other", reasons: ["blend"], blendGaiaIds: ["a", "b"], snr: 10 }),
      det(1, 100, 92, 0, { cls: "stack", gaiaId: "a", snr: 63 }),
      det(2, 98, 110, 0, { cls: "stack", gaiaId: "b", snr: 49 }),
    ];
    const p = planMarks(stars, dets, O);
    // One circle, drawn as the Gaia stars, holding both; the stack-only circles on them are that source again.
    expect(p.circles).toHaveLength(1);
    expect(p.circles[0]).toMatchObject({ i: 0, blend: ["a", "b"], asGaia: true, also: [1, 2] });
    // One ring around it, a dot per star (drawn in Advanced only).
    expect(p.rings).toHaveLength(1);
    expect(p.rings[0].blend!.map((b) => b.id)).toEqual(["a", "b"]);
    expect(p.rings[0].r).toBeGreaterThan(p.circles[0].r);
  });

  it("names a source the server didn't match after the Gaia star on it, only when that star is near, bright enough, and not measured apart", () => {
    const px = O.pxPerArcsec;
    const near = (SAME_OBJECT_ARCSEC - 0.5) * px;
    const other = det(0, 100, 100, 2, { cls: "other" });
    expect(sourceGaia(other, [star("s", 100 + near, 100, 0, { g: 16 })], new Set(), px)).toEqual(["s"]);
    // Too far (a FWHM away: a neighbour), too faint, or another detection's own star: still "not in Gaia".
    expect(sourceGaia(other, [star("s", 100 + 2 * near, 100, 0, { g: 16 })], new Set(), px)).toEqual([]);
    expect(sourceGaia(other, [star("s", 100 + near, 100, 0, { g: GAIA_SEEN_G + 1 })], new Set(), px)).toEqual([]);
    expect(sourceGaia(other, [star("s", 100 + near, 100, 0, { g: 16 })], new Set(["s"]), px)).toEqual([]);
    // Only `other` (and stack-only sources in a glow): a Gaia star near a matched one is its business.
    expect(sourceGaia({ ...other, cls: "star" }, [star("s", 100 + near, 100, 0, { g: 16 })], new Set(), px)).toEqual([]);
    expect(sourceGaia({ ...other, cls: "stack" }, [star("s", 100 + near, 100, 0, { g: 16 })], new Set(), px)).toEqual([]);
    expect(sourceGaia({ ...other, cls: "stack" }, [star("s", 100 + near, 100, 0, { g: 16 })], new Set(), px, true)).toEqual(["s"]);
    const p = planMarks([star("s", 100 + near, 100, 0, { g: 16 })], [other], O);
    expect(p.circles[0]).toMatchObject({ star: "s", asGaia: true });
    expect(p.rings).toHaveLength(1);
  });

  it("a blend detection's own Gaia match joins its blend: one circle, one ring (RW Aurigae, the right-hand pair)", () => {
    // The server matched a G 19.4 star but lists two G 17 stars in the blend: one ring, not a ring in a ring.
    const stars = [star("m", 100, 103, 2, { g: 19.4 }), star("a", 100, 95, 3, { g: 17.5 }), star("b", 104, 112, 2, { g: 17.2 })];
    const p = planMarks(stars, [det(0, 100, 102, 6, { gaiaId: "m", reasons: ["blend"], blendGaiaIds: ["a", "b"] })], O);
    expect(p.circles).toHaveLength(1);
    expect(p.circles[0].blend).toEqual(["a", "b", "m"]);
    expect(p.rings).toHaveLength(1);
  });

  it("a blend loses a star another detection measures on its own, and partners over 3 mag fainter", () => {
    const stars = [star("a", 100, 100, 6, { g: 15.2 }), star("b", 117, 100, 3, { g: 16.7 }), star("c", 104, 104, 2, { g: 18.5 })];
    const p = planMarks(
      stars,
      [det(0, 100, 100, 6, { gaiaId: "a", reasons: ["blend"], blendGaiaIds: ["a", "b", "c"] }), det(1, 122, 100, 2, { gaiaId: "b" })],
      O,
    );
    expect(p.circles.find((c) => c.i === 0)!.blend).toBeUndefined();
    expect(p.circles.find((c) => c.i === 0)!.star).toBe("a");
    expect(p.circles.find((c) => c.i === 1)!.star).toBe("b");
    expect(nested(p)).toBe(false);
  });

  it("two measurements under half a FWHM apart are one circle, the stronger, naming the other", () => {
    const close = (SAME_OBJECT_ARCSEC - 1) * O.pxPerArcsec;
    const p = planMarks([], [det(0, 100, 100, 3, { cls: "other", snr: 12 }), det(1, 100 + close, 100, 3, { cls: "other", snr: 40 })], O);
    expect(p.circles.map((c) => [c.i, c.also])).toEqual([[1, [0]]]);
    // A mover is never folded away, whatever its S/N.
    const m = planMarks([], [det(0, 100, 100, 3, { cls: "mover", snr: 6 }), det(1, 100 + close, 100, 3, { cls: "star", snr: 40 })], O);
    expect(m.circles.map((c) => c.i)).toEqual([0]);
    expect(m.circles[0].also).toEqual([1]);
    const two = planMarks([], [det(0, 100, 100, 3, { cls: "suspect" }), det(1, 100 + close, 100, 3, { cls: "known" })], O);
    expect(two.circles).toHaveLength(2);
  });

  it("two dots a FWHM apart keep a circle each, side by side, never one inside the other", () => {
    const fwhm = 7.53 * O.pxPerArcsec;
    // A bright star's own circle (its whole blob, 20 px) and a separate star one FWHM from it.
    const p = planMarks(
      [star("big", 100, 100, 18, { g: 10.5 }), star("n", 100 + fwhm, 100, 3, { g: 15, peak: true })],
      [det(0, 100, 100, 18, { gaiaId: "big", snr: 900 }), det(1, 100 + fwhm, 100, 3, { gaiaId: "n", snr: 30 })],
      O,
    );
    expect(p.circles).toHaveLength(2);
    expect(nested(p)).toBe(false);
    expect(p.rings.map((r) => r.id).sort()).toEqual(["big", "n"]);
    // A stack-only source a FWHM away is its own object too; one on the star's core is the star again.
    const s = planMarks(
      [],
      [det(0, 100, 100, 3, { snr: 50 }), det(1, 100 + fwhm, 100, 0, { cls: "stack" }), det(2, 100, 100 + 0.4 * fwhm, 0, { cls: "stack" })],
      O,
    );
    expect(s.circles.map((c) => c.i)).toEqual([0, 1]);
    expect(s.circles[0].also).toEqual([2]);
    // Even when the drawn source seems to reach far (in a bright star's glow its reach is the glow's):
    // Barnard's months plate, a S/N 460 stack-only source 1.1 FWHM from a Gaia star's circle.
    const g = planMarks([], [det(0, 100, 100, 40, { snr: 69 }), det(1, 100 + 1.1 * fwhm, 100, 16, { cls: "stack", snr: 460 })], O);
    expect(g.circles.map((c) => c.i)).toEqual([0, 1]);
    expect(nested(g)).toBe(false);
  });

  it("a Gaia star whose own dot shows is never folded into a brighter star's ring", () => {
    const off = { ...O, measured: false };
    const near = BLEND_ARCSEC * O.pxPerArcsec - 2;
    const lost = planMarks([star("a", 100, 100, 6, { g: 12 }), star("b", 100 + near, 100, 2, { g: 16.5 })], [], off);
    expect(lost.rings.map((r) => r.id)).toEqual(["a"]);
    const kept = planMarks([star("a", 100, 100, 6, { g: 12 }), star("b", 100 + near, 100, 2, { g: 16.5, peak: true })], [], off);
    expect(kept.rings.map((r) => r.id)).toEqual(["a", "b"]);
    expect(kept.rings.every((r) => !r.blend)).toBe(true);
  });

  it("the same Gaia stars get the same rings with the measured layer on or off", () => {
    const stars = [
      star("a", 100, 92, 0, { g: 17.84 }),
      star("b", 98, 110, 0, { g: 17.85 }),
      star("c", 160, 100, 4, { g: 14 }),
      star("s", 40, 40, 30, { g: 8, saturated: true }),
    ];
    const dets = [
      det(0, 100, 100, 2, { cls: "other", reasons: ["blend"], blendGaiaIds: ["a", "b"] }),
      det(1, 161, 100, 4, { gaiaId: "c" }),
      det(2, 41, 40, 30, { gaiaId: "s" }),
    ];
    const on = planMarks(stars, dets, O);
    const off = planMarks(stars, dets, { ...O, measured: false });
    expect(off.rings).toEqual(on.rings);
    expect(off.circles).toEqual([]);
    expect(off.big).toEqual([]);
  });

  it("in a bright star's glow: Gaia-backed sources stay, movers are never hidden", () => {
    const stars = [star("big", 100, 100, 20, { g: 8.2, saturated: true, glow: 120 }), star("g", 190, 100, 0, { g: 16.9 })];
    const dets = [
      det(0, 101, 100, 20, { cls: "other", snr: 2000 }), // the bright star's own measurement
      det(1, 191, 100, 2, { cls: "stack", snr: 460 }), // on a G 16.9 star, 0.5″ away: real
      det(2, 100, 170, 2, { cls: "stack", snr: 8 }), // nothing in Gaia: may be the glow's own
      det(3, 30, 100, 2, { cls: "mover", snr: 6 }),
    ];
    const plain = planMarks(stars, dets, { ...O, hideGlow: true });
    expect(plain.big.map((b) => [b.id, b.det])).toEqual([["big", 0]]);
    expect(plain.hidden.get(2)).toBe("big");
    expect(plain.circles.map((c) => c.i).sort()).toEqual([1, 3]);
    expect(plain.circles.find((c) => c.i === 1)).toMatchObject({ star: "g", asGaia: true });
    const adv = planMarks(stars, dets, O);
    expect(adv.circles.find((c) => c.i === 2)!.glow).toBe("big");
    expect(adv.glows).toEqual([{ id: "big", X: 100, Y: 100, r: 120 }]);
  });

  it("a star sitting in a bright star's glow reads that glow as its blob: the glow is still the bright star's", () => {
    const sat = star("big", 100, 100, 20, { g: 8.2, saturated: true, glow: 120 });
    const inner = star("in", 60, 70, 30, { g: 14, peak: true });
    expect(glowHost({ X: 50, Y: 60 }, [sat, inner], O)?.id).toBe("big");
    const adv = planMarks([sat, inner], [det(0, 50, 60, 2, { cls: "stack", snr: 8 })], O);
    expect(adv.circles[0].glow).toBe("big");
    expect(adv.glows.map((g) => g.id)).toEqual(["big"]);
  });
});
