import { describe, expect, it } from "vitest";
import { CUBE_UM } from "../sky/layers";
import { HITS_GROW, HITS_MIN_MJY_SR, brightMask, compositionFrames, measureComposition, type CompositionInput, type Source } from "./composition";
import { INDEX_IDS } from "./indices";

// A synthetic field: Gaussian sky noise, stars with a fixed 3 × 3 shape, 5% frame-to-frame jitter.
const N = 72;
const SIGMA = 0.01;
const FRAMES = compositionFrames();
const SHAPE = [0.02, 0.08, 0.02, 0.08, 0.6, 0.08, 0.02, 0.08, 0.02];

function rng(seed: number) {
  let s = seed;
  const u = () => ((s = (s * 1664525 + 1013904223) % 2 ** 32) + 1) / (2 ** 32 + 1);
  return () => Math.sqrt(-2 * Math.log(u())) * Math.cos(2 * Math.PI * u());
}

interface Star {
  x: number;
  y: number;
  /** Total light per frame, MJy/sr summed over its pixels. */
  spectrum: (um: number, frame: number) => number;
  jitter?: number;
}

/** An ordinary star: light falling towards the red (4.6 µm at 14% of 1.25 µm), no bands. */
const flat = (level: number) => (um: number) => level * (um / 1.25) ** -1.5;
const dip = (um: number, lo: number, hi: number, depth: number) => (um >= lo && um <= hi ? 1 - depth : 1);

function field(stars: Star[], seed = 3, edit?: (images: Map<number, Float32Array>, hits: Map<number, Int32Array>) => void): CompositionInput {
  const g = rng(seed);
  const images = new Map<number, Float32Array>();
  const hits = new Map<number, Int32Array>();
  for (const f of FRAMES) {
    const img = new Float32Array(N * N);
    for (let k = 0; k < img.length; k++) img[k] = 1 + SIGMA * g(); // 1 MJy/sr of sky
    for (const s of stars) {
      const total = s.spectrum(CUBE_UM[f], f) * (1 + (s.jitter ?? 0.05) * g());
      for (let p = 0; p < 9; p++) img[(s.y + Math.floor(p / 3) - 1) * N + s.x + (p % 3) - 1] += total * SHAPE[p];
    }
    images.set(f, img);
    hits.set(f, new Int32Array(N * N).fill(10));
  }
  edit?.(images, hits);
  return { n: N, images, hits };
}

/** 36 ordinary stars on a grid, 10 pixels apart, brightness 6–24 at 1.25 µm. */
function ordinary(): Star[] {
  const out: Star[] = [];
  for (let a = 0; a < 6; a++) for (let b = 0; b < 6; b++) out.push({ x: 10 + 10 * b, y: 10 + 10 * a, spectrum: flat(6 + 2 * ((a * 6 + b) % 10)) });
  return out;
}

const at = (sources: Source[], x: number, y: number) => {
  const s = sources.find((q) => q.x === x && q.y === y);
  if (!s) throw new Error(`no source at ${x},${y}`);
  return s;
};

describe("measureComposition on a synthetic field", () => {
  it("finds every star and no feature on ordinary stars", () => {
    const c = measureComposition(field(ordinary()));
    expect(c.sources.length).toBe(36);
    for (const id of INDEX_IDS) {
      expect(c.cal[id].ok, id).toBe(true);
      expect(c.cal[id].r0).toBeGreaterThan(0.9);
      expect(c.cal[id].r0).toBeLessThan(1.1);
      expect(c.cal[id].k).toBeGreaterThanOrEqual(1);
    }
    for (const s of c.sources) for (const id of INDEX_IDS) expect(["none", "cant"], `${id} at ${s.x},${s.y}`).toContain(s.bands[id].state);
    expect(c.eps).toBeGreaterThan(0.03);
    expect(c.eps).toBeLessThan(0.08);
    expect(c.blocks.filter((b) => b.state === "found" || b.state === "possible").length).toBe(0);
  });

  it("ordinary stars' significances are not overconfident (spread ≤ 1.3)", () => {
    const c = measureComposition(field(ordinary(), 9));
    for (const id of INDEX_IDS) {
      const sig = c.sources.map((s) => s.bands[id].sig).filter(Number.isFinite);
      const mean = sig.reduce((a, b) => a + b, 0) / sig.length;
      const sd = Math.sqrt(sig.reduce((a, b) => a + (b - mean) ** 2, 0) / sig.length);
      expect(sd, id).toBeLessThan(1.3);
      expect(Math.max(...sig.map(Math.abs)), id).toBeLessThan(5);
    }
  });

  it("finds methane and water on a red dwarf with deep bands (a T dwarf)", () => {
    const stars = ordinary();
    const t = stars[14];
    t.spectrum = (um) => 0.5 * um ** 2 * dip(um, 3.2, 3.45, 0.7) * dip(um, 1.63, 1.72, 0.4) * dip(um, 1.34, 1.45, 0.6) * dip(um, 1.84, 1.95, 0.6);
    const s = at(measureComposition(field(stars)).sources, t.x, t.y);
    expect(s.redBd).toBe(true);
    expect(s.bands.ch4_33.state).toBe("found");
    expect(s.bands.ch4_33.depth).toBeGreaterThan(0.55);
    expect(s.bands.ch4_33.depth).toBeLessThan(0.8);
    expect(s.bands.h2o_14.state).toBe("found");
    expect(s.bands.h2o_19.state).toBe("found");
    expect(s.bands.pah_33.state).toBe("none");
  });

  it("water ice's wide band is found as ice, and its wing at 3.3 µm is not called methane", () => {
    const stars = ordinary();
    // A reddened star behind a cloud: the ice band is deepest near 3.05 µm, with a wing to 3.5 µm.
    stars[14].spectrum = (um) => 0.5 * um ** 2 * dip(um, 2.85, 3.25, 0.8) * dip(um, 3.25, 3.5, 0.45);
    // The same star with water vapour too (as one real source in Chamaeleon had): methane's
    // "second band" is then there, and only the ice check stops a false methane.
    stars[20].spectrum = (um) => 0.5 * um ** 2 * dip(um, 2.85, 3.25, 0.8) * dip(um, 3.25, 3.5, 0.45) * dip(um, 1.34, 1.45, 0.5) * dip(um, 1.84, 1.95, 0.5);
    const c = measureComposition(field(stars));
    const ice = at(c.sources, stars[14].x, stars[14].y);
    expect(ice.bands.h2o_ice_305.state).toBe("found");
    expect(ice.bands.ch4_33.sig).toBeGreaterThan(5);
    expect(ice.bands.ch4_33.state).toBe("none"); // a real dip, and it is ice
    expect(ice.bands.ch4_33.reasons).toEqual(["ice"]);
    const both = at(c.sources, stars[20].x, stars[20].y);
    expect(both.bands.ch4_33.sig).toBeGreaterThan(5);
    expect(both.bands.ch4_33.state).toBe("none");
    expect(both.bands.ch4_33.reasons).toEqual(["ice"]);
    expect(both.bands.h2o_ice_305.state).toBe("possible"); // water vapour: could be a cool star's own water
  });

  it("an ice band shallower than the 3.3 µm dip leaves methane possible, not found", () => {
    const stars = ordinary();
    // Deep methane-like dip, some ice: can't be told apart from this map alone.
    stars[14].spectrum = (um) =>
      0.5 * um ** 2 * dip(um, 2.9, 3.2, 0.3) * dip(um, 3.2, 3.45, 0.7) * dip(um, 1.63, 1.72, 0.4) * dip(um, 1.34, 1.45, 0.6) * dip(um, 1.84, 1.95, 0.6);
    const b = at(measureComposition(field(stars)).sources, stars[14].x, stars[14].y).bands.ch4_33;
    expect(b.state).toBe("possible");
    expect(b.reasons).toEqual(["ice_maybe"]);
  });

  it("a one-frame CO₂ ice band counts only where water ice is found in the same source", () => {
    const stars = ordinary();
    const co2 = (f: number) => (f === 80 ? 0.3 : 1);
    stars[14].spectrum = (um, f) => 0.5 * um ** 2 * dip(um, 2.85, 3.25, 0.8) * co2(f);
    stars[20].spectrum = (um, f) => 0.5 * um ** 2 * co2(f);
    const c = measureComposition(field(stars));
    const withIce = at(c.sources, stars[14].x, stars[14].y).bands.co2_ice_427;
    expect(withIce.state).toBe("found");
    expect(withIce.confirmedBy).toBe("h2o_ice_305");
    const alone = at(c.sources, stars[20].x, stars[20].y).bands.co2_ice_427;
    expect(alone.state).toBe("possible");
    expect(alone.reasons).toEqual(["one_frame"]);
    expect(alone.confirmedBy).toBeUndefined();
  });

  it("two shallow water bands together are found; one alone is not", () => {
    const stars = ordinary();
    stars[14].jitter = 0.03;
    stars[20].jitter = 0.03;
    stars[14].spectrum = (um) => flat(24)(um) * dip(um, 1.34, 1.45, 0.2) * dip(um, 1.84, 1.95, 0.2);
    stars[20].spectrum = (um) => flat(24)(um) * dip(um, 1.34, 1.45, 0.2);
    const c = measureComposition(field(stars));
    const pair = at(c.sources, stars[14].x, stars[14].y);
    expect(pair.bands.h2o_14.sig).toBeGreaterThan(3);
    expect(pair.bands.h2o_19.sig).toBeGreaterThan(3);
    expect(pair.bands.h2o_14.state).toBe("found");
    expect(pair.bands.h2o_19.state).toBe("found");
    const one = at(c.sources, stars[20].x, stars[20].y);
    expect(one.bands.h2o_14.state).not.toBe("found");
    expect(one.bands.h2o_19.state).toBe("none");
  });

  it("a dip on a star that isn't red is only possible", () => {
    const stars = ordinary();
    stars[14].spectrum = (um) => flat(20)(um) * dip(um, 3.2, 3.45, 0.6);
    const b = at(measureComposition(field(stars)).sources, stars[14].x, stars[14].y).bands.ch4_33;
    expect(b.state).toBe("possible");
    expect(b.reasons).toContain("not_red");
    expect(b.reasons).toContain("no_second_band");
  });

  it("water vapour needs both of its bands", () => {
    const stars = ordinary();
    stars[14].spectrum = (um) => flat(20)(um) * dip(um, 1.34, 1.45, 0.5);
    const s = at(measureComposition(field(stars)).sources, stars[14].x, stars[14].y);
    expect(s.bands.h2o_14.state).toBe("possible");
    expect(s.bands.h2o_14.reasons).toContain("no_second_band");
  });

  it("a single-pixel glitch in one frame is left out and makes no feature", () => {
    const stars = ordinary();
    const s0 = stars[14];
    const input = field(stars, 3, (images) => {
      images.get(62)![s0.y * N + s0.x] -= 1.5 * s0.spectrum(CUBE_UM[62], 62) * SHAPE[4]; // the centre pixel goes negative
    });
    const s = at(measureComposition(input).sources, s0.x, s0.y);
    expect(s.dropped.glitches).toBeGreaterThanOrEqual(1);
    expect(s.bands.ch4_33.state).toBe("none");
    // The PAH band is that one frame: without it the band can't be measured, and says so.
    expect(s.bands.pah_33.state).toBe("cant");
    expect(s.bands.pah_33.reasons).toEqual(["nodata"]);
  });

  it("a milder glitch (half a frame's light, either way) is never 'found'", () => {
    for (const sign of [-1, 1]) {
      for (const f of [62, 80, 92, 49, 34, 40]) {
        const stars = ordinary();
        const s0 = stars[19];
        const input = field(stars, 5, (images) => {
          images.get(f)![s0.y * N + s0.x] += sign * 0.8 * s0.spectrum(CUBE_UM[f], f) * SHAPE[4];
        });
        const s = at(measureComposition(input).sources, s0.x, s0.y);
        for (const id of INDEX_IDS) expect(s.bands[id].state, `${id}, frame ${f}, sign ${sign}`).not.toBe("found");
      }
    }
  });

  it("a whole-star drop in a single frame is never 'found'", () => {
    const stars = ordinary();
    stars[14].spectrum = (um, f) => 0.5 * um ** 2 * (f === 62 ? 0.1 : 1); // red, so the colour guard passes
    const s = at(measureComposition(field(stars)).sources, stars[14].x, stars[14].y);
    expect(s.bands.ch4_33.state).not.toBe("found");
    expect(s.bands.pah_33.state).not.toBe("found");
  });

  it("a bump in the one PAH frame on a star is possible, never found", () => {
    const stars = ordinary();
    stars[14].spectrum = (um, f) => flat(20)(um) * (f === 62 ? 2 : 1);
    const b = at(measureComposition(field(stars)).sources, stars[14].x, stars[14].y).bands.pah_33;
    expect(b.state).toBe("possible");
    expect(b.reasons).toContain("one_frame");
  });

  it("a star that lost exposures at its peak is too bright to tell", () => {
    const stars = ordinary();
    const s0 = stars[14];
    const input = field(stars, 3, (_images, hits) => {
      for (const f of FRAMES) if (f % 3 === 0) hits.get(f)![s0.y * N + s0.x] = 2;
    });
    const s = at(measureComposition(input).sources, s0.x, s0.y);
    expect(s.bright).toBe(true);
    for (const id of INDEX_IDS) {
      expect(s.bands[id].state).toBe("cant");
      expect(s.bands[id].reasons).toContain("bright");
    }
  });

  it("exposure counts are needed only around bright stars", () => {
    // 24 MJy/sr in the bright star's centre pixel at 1.25 µm, 3 in the faint one's.
    const stars: Star[] = [
      { x: 30, y: 30, spectrum: flat(40) },
      { x: 50, y: 50, spectrum: flat(5) },
    ];
    const input = field(stars);
    const keep = brightMask(input.images, N);
    const s0 = stars[0];
    expect(keep[50 * N + 50]).toBe(0);
    expect(HITS_MIN_MJY_SR).toBe(10);
    let count = 0;
    for (let k = 0; k < keep.length; k++) count += keep[k];
    expect(count).toBe((2 * HITS_GROW + 1) ** 2); // one bright pixel, grown by 5
    expect(keep[s0.y * N + s0.x]).toBe(1);
    expect(keep[(s0.y + HITS_GROW) * N + s0.x - HITS_GROW]).toBe(1);
    expect(keep[(s0.y + HITS_GROW + 1) * N + s0.x]).toBe(0);
  });

  it("counts marked 'not read' (−1) drop no frame, and lost exposures are still seen where they were read", () => {
    const stars = ordinary();
    const s0 = stars[14];
    const far = stars[30];
    const input = field(stars, 3, (_images, hits) => {
      for (const f of FRAMES) {
        const h = hits.get(f)!;
        if (f % 3 === 0) {
          h[s0.y * N + s0.x] = 2;
          h[far.y * N + far.x] = 2;
        }
        // Not read around `far`.
        for (let dy = -5; dy <= 5; dy++) for (let dx = -5; dx <= 5; dx++) h[(far.y + dy) * N + far.x + dx] = -1;
      }
    });
    const c = measureComposition(input);
    expect(at(c.sources, s0.x, s0.y).bright).toBe(true);
    const f = at(c.sources, far.x, far.y);
    expect(f.bright).toBe(false);
    expect(f.dropped.exposures).toBe(0);
  });

  it("two stars 2 pixels apart are blended: can't tell", () => {
    const stars = ordinary();
    stars.push({ x: stars[14].x + 2, y: stars[14].y, spectrum: flat(8) });
    const c = measureComposition(field(stars));
    const s = at(c.sources, stars[14].x, stars[14].y);
    expect(s.blend).toBe(true);
    expect(s.bands.ch4_33.state).toBe("cant");
  });

  it("a star that flickers between frames is erratic: can't tell", () => {
    const stars = ordinary();
    stars[14].jitter = 0.5;
    const s = at(measureComposition(field(stars)).sources, stars[14].x, stars[14].y);
    expect(s.erratic).toBe(true);
    expect(s.bands.h2o_14.state).toBe("cant");
  });

  it("too few ordinary stars: not calibrated, so can't tell", () => {
    const c = measureComposition(field(ordinary().slice(0, 4)));
    expect(c.cal.ch4_33.ok).toBe(false);
    for (const s of c.sources) expect(s.bands.ch4_33.reasons).toContain("nocal");
  });

  // Dust glow: light added to the sky itself at 3.29 µm (frame 62), with PAH's wings in the frames either side.
  const glowing = (peak: number, lo: number, hi: number, where: (x: number, y: number) => boolean) =>
    field(ordinary(), 3, (images) => {
      for (const [f, add] of [[61, lo], [62, peak], [63, hi]] as const) {
        const img = images.get(f)!;
        for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) if (where(x, y)) img[y * N + x] += add;
      }
    });
  const patch = (x: number, y: number) => x >= 2 && x < 34 && y >= 36 && y < 68;

  it("finds a patch of dust glow, in absolute brightness, and only there", () => {
    const c = measureComposition(glowing(0.15, 0.04, 0.05, patch));
    const found = c.blocks.filter((b) => b.state === "found");
    expect(found.length).toBeGreaterThan(5);
    for (const b of found) {
      expect(patch(b.x + b.size / 2, b.y + b.size / 2)).toBe(true);
      expect(Math.abs(b.depth - 0.15)).toBeLessThan(0.03); // MJy/sr
      expect(b.depth / b.err).toBeGreaterThan(5);
    }
    expect(c.glow.wings).toBe(true);
    expect(c.glow.wingShare).toBeGreaterThan(0.4);
    expect(c.glow.wingShare).toBeLessThan(0.8);
    // Outside the patch nothing is found or hinted.
    for (const b of c.blocks) if (!patch(b.x + b.size / 2, b.y + b.size / 2) && (b.x >= 36 || b.y + b.size <= 34)) expect(["none", "cant"]).toContain(b.state);
    for (const s of c.sources) expect(s.bands.pah_33.state).not.toBe("found");
  });

  it("a glow that fills the picture is found too (it isn't taken off as background)", () => {
    const c = measureComposition(glowing(0.05, 0.012, 0.017, () => true));
    const usable = c.blocks.filter((b) => b.state !== "cant");
    expect(usable.length).toBeGreaterThan(20);
    expect(usable.filter((b) => b.state === "found").length).toBeGreaterThan(0.9 * usable.length);
    expect(Math.abs(c.glow.sky - 0.05)).toBeLessThan(0.005);
    expect(c.glow.wings).toBe(true);
  });

  it("a bump in one frame only, without PAH's wings, is never found", () => {
    const c = measureComposition(glowing(0.15, 0, 0, patch));
    expect(c.glow.wings).toBe(false);
    expect(c.blocks.some((b) => b.state === "found")).toBe(false);
    expect(c.blocks.filter((b) => b.state === "possible").length).toBeGreaterThan(5);
    expect(c.blocks.filter((b) => b.state === "possible" && b.reasons.includes("one_frame")).length).toBeGreaterThan(5);
  });

  it("a glow under the brightness floor is not found, however quiet the sky", () => {
    const c = measureComposition(glowing(0.006, 0.002, 0.002, () => true));
    expect(c.blocks.some((b) => b.state === "found")).toBe(false);
  });

  it("a continuum lowered on the blue side (water ice in scattered light) is not read as a glow", () => {
    const c = measureComposition(
      field(ordinary(), 3, (images) => {
        for (const f of FRAMES) {
          if (!(CUBE_UM[f] >= 2.9 && CUBE_UM[f] <= 3.16)) continue;
          const img = images.get(f)!;
          for (let y = 36; y < 68; y++) for (let x = 2; x < 34; x++) img[y * N + x] -= 0.1;
        }
      }),
    );
    expect(c.blocks.some((b) => b.state === "found" || b.state === "possible")).toBe(false);
  });

  it("stars are left out of the glow: a field of ordinary stars shows none", () => {
    const c = measureComposition(field(ordinary(), 11));
    expect(c.blocks.some((b) => b.state === "found" || b.state === "possible")).toBe(false);
    expect(Math.abs(c.glow.sky)).toBeLessThan(0.004);
    expect(c.blocks.filter((b) => b.state === "none").length).toBeGreaterThan(20);
  });

  // Water vapour through its three wide bands together.
  const wet = (depth: number) => (um: number) =>
    flat(24)(um) * dip(um, 1.33, 1.5, depth) * dip(um, 1.78, 2.05, depth) * dip(um, 2.45, 2.95, depth);

  it("water vapour too shallow for any one band is found when its three wide bands agree", () => {
    const stars = ordinary();
    stars[14].spectrum = wet(0.13);
    const s = at(measureComposition(field(stars)).sources, stars[14].x, stars[14].y);
    expect(s.bands.h2o_14.sig).toBeLessThan(5);
    expect(s.bands.h2o_19.sig).toBeLessThan(5);
    expect(s.bands.h2o_14.state).toBe("found");
    expect(s.bands.h2o_19.state).toBe("found");
    const j = s.bands.h2o_14.joint!;
    expect(j.sig).toBeGreaterThan(5);
    expect(Math.abs(j.depth - 0.13)).toBeLessThan(0.05);
    expect(s.bands.h2o_19.joint).toEqual(j);
  });

  it("the mirror image (three bumps) and a single wide dip are not water", () => {
    const stars = ordinary();
    stars[14].spectrum = wet(-0.13);
    stars[20].spectrum = (um) => flat(24)(um) * dip(um, 2.45, 2.95, 0.3); // only the 2.7 µm band, which spans two detectors
    const c = measureComposition(field(stars));
    for (const k of [14, 20]) {
      const s = at(c.sources, stars[k].x, stars[k].y);
      expect(s.bands.h2o_14.joint).toBeUndefined();
      expect(s.bands.h2o_14.state).not.toBe("found");
      expect(s.bands.h2o_19.state).not.toBe("found");
    }
  });

  // Glare: a wide, smooth pool of light around a source (a much brighter star's halo), with its own bands.
  const glareField = (pool: number, starSpectrum: Star["spectrum"]) => {
    const stars = ordinary();
    const t = stars[14];
    t.spectrum = starSpectrum;
    const input = field(stars, 3, (images) => {
      for (const f of FRAMES) {
        const um = CUBE_UM[f];
        const img = images.get(f)!;
        const level = pool * (um / 1.25) ** -1.5 * dip(um, 1.33, 1.5, 0.4) * dip(um, 1.78, 2.05, 0.4) * dip(um, 2.45, 2.95, 0.4);
        // Falls off across the source, as a halo does: the ring's median can't take all of it off.
        for (let y = t.y - 4; y <= t.y + 4; y++) for (let x = t.x - 4; x <= t.x + 4; x++) img[y * N + x] += level * (1 + 0.15 * (x - t.x));
      }
    });
    return { input, t };
  };

  it("a faint source in the glare of a bright star is can't tell, whatever its spectrum seems to show", () => {
    // The pool is 3 MJy/sr per pixel at 1.25 µm: 27 over the source's 9 pixels, against the source's own 6.
    const { input, t } = glareField(3, flat(6));
    const s = at(measureComposition(input).sources, t.x, t.y);
    expect(s.glare).toBe(true);
    for (const id of INDEX_IDS) {
      expect(s.bands[id].state, id).toBe("cant");
      expect(s.bands[id].reasons).toContain("glare");
    }
  });

  it("in moderate glare a real sign is kept, but only as a hint", () => {
    // Pool of 1 per pixel (9 over the source) against a source of 12 with deep water bands of its own.
    const { input, t } = glareField(1, (um) => flat(12)(um) * dip(um, 1.34, 1.45, 0.6) * dip(um, 1.84, 1.95, 0.6));
    const s = at(measureComposition(input).sources, t.x, t.y);
    expect(s.glare).toBe(true);
    expect(s.bands.h2o_14.state).toBe("possible");
    expect(s.bands.h2o_14.reasons).toContain("glare");
    expect(s.bands.h2o_19.state).toBe("possible");
  });

  it("ordinary stars are not in glare", () => {
    for (const s of measureComposition(field(ordinary())).sources) expect(s.glare).toBe(false);
  });

  it("empty sky gives no sources and no glow", () => {
    const c = measureComposition(field([]));
    expect(c.sources.length).toBeLessThanOrEqual(1); // 5σ over 5 windows × 4,356 pixels: a chance peak is rare
    expect(c.blocks.some((b) => b.state === "found" || b.state === "possible")).toBe(false);
  });

  it("missing data under a star is can't tell, not a feature", () => {
    const stars = ordinary();
    const s0 = stars[14];
    const input = field(stars, 3, (images) => {
      for (const f of [59, 60, 61, 62, 63, 64]) images.get(f)![s0.y * N + s0.x] = NaN;
    });
    const s = at(measureComposition(input).sources, s0.x, s0.y);
    expect(s.bright).toBe(true); // holes in the core: saturation
    expect(s.bands.ch4_33.state).toBe("cant");
  });
});
