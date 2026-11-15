// Composition (layer A3): which sources in a field show a molecule's band, measured from the
// SPHEREx all-sky cube. Per source, never per pixel: on empty sky a band ratio is noise ÷ noise.
// Every step and threshold, and the checks on real data behind them: docs/colour-layers.md (A3).
//
//  1. Each frame (cube frames 17–101, the blank ones left out): sigma-clipped background off.
//  2. Sources: peaks ≥ 5σ in any of five wide windows (medians of their frames), 3 × 3 local maxima.
//  3. Light per frame: the 3 × 3 pixels minus the median of the ring 3 pixels out. Its noise is
//     the scatter of the same measurement over the field (so correlated noise is included).
//  4. Frames left out, per source: lost exposures (NHITS, saturation) and single-frame glitches
//     (the frame's 3 × 3 shape disagrees with its neighbour frames' shape).
//  5. Errors: aperture noise ⊕ ε × flux (ε: frame-to-frame jitter of bright stars, measured in
//     the field), then × the source's own roughness when its spectrum is rougher than that.
//  6. Indices as the pipeline (indices.ts). Zero point and error scale per index from the field's
//     ordinary stars: their median ratio is "no feature", the spread of their pulls scales σ.
//  7. Found = ≥ 5σ and ≥ the pipeline's minimum depth, survives leaving any one frame out, and
//     passes the pipeline's look-alike guards. A source that can't be measured is "can't tell".
import { CUBE_UM } from "../sky/layers";
import { BLANK_FRAMES } from "./cube";
import {
  INDEX_IDS,
  INDEX_WINDOWS,
  J_WINDOW,
  M46_WINDOW,
  MIN_DEPTH,
  cubeChannels,
  indexOf,
  indexWeights,
  windowMean,
  windowWeights,
  type IndexDef,
  type IndexId,
  type IndexWeights,
} from "./indices";
import { clippedStats, medianPlanes } from "./rgb";

// ---- thresholds (docs/colour-layers.md, A3) -------------------------------------------------
/** D1 (frames 0–16) isn't used by any index, and its frames have field-wide artefacts. */
export const FIRST_FRAME = 17;
export const DETECT_SIGMA = 5;
/** Detection windows: the pipeline's MAG_WINDOWS. */
export const DETECT_WINDOWS: readonly [number, number][] = [
  [1.15, 1.33],
  [1.5, 1.78],
  [2.0, 2.3],
  [3.3, 3.7],
  [4.4, 4.8],
];
/** Pixels kept clear of the field's edge: the background ring's radius. */
export const MARGIN = 3;
/** pipeline INDEX_SIG and LABEL_SUPPORT_SIG. */
export const FOUND_SIGMA = 5;
export const SUPPORT_SIGMA = 3;
/** Continuum S/N below this: can't tell. */
export const CONT_MIN_SNR = 5;
/** Reference stars for the zero point: continuum S/N ≥ this (band ≥ half of it), at least MIN_REF. */
export const REF_MIN_SNR = 20;
export const MIN_REF = 8;
/** Leave-one-out: without any one frame the feature must stay ≥ 3σ and ≥ half the minimum depth. */
export const LOO_SIGMA = 3;
/**
 * Water vapour's two bands (1.4 and 1.9 µm) always come together, so they are also tested as a
 * pair: each ≥ 3σ and deep enough, together ≥ 5σ (root sum of squares); and without any one frame
 * of either band, each still ≥ 2σ and together ≥ 4σ. On 2,881 sources of eight ordinary fields
 * the mirror-image test (both bands too bright) fired 0 times, the real one 3 times.
 */
export const PAIR_EACH_SIGMA = 3;
export const PAIR_SIGMA = 5;
export const PAIR_LOO_EACH_SIGMA = 2;
export const PAIR_LOO_SIGMA = 4;
/** A frame is dropped when its brightest pixel kept < 70%, or a pixel holding light < 50%, of the usual exposures. */
export const KEPT_PEAK = 0.7;
export const KEPT_CORE = 0.5;
/** Too bright (saturating): ≥ 10% of frames lost at the peak, ≥ 4 frames with a hole, or a pixel ≥ 30 MJy/sr. */
export const BRIGHT_LOST_SHARE = 0.1;
export const BRIGHT_HOLE_FRAMES = 4;
export const BRIGHT_PEAK_MJY_SR = 30;
/** Glitch: a pixel off the neighbour frames' shape by > 6σ and > half the source's light in that frame. */
export const GLITCH_SIGMA = 6;
export const GLITCH_SHARE = 0.5;
/** Spectrum rougher than this × its errors: erratic (variable, moving or damaged), can't tell. */
export const ERRATIC_Q = 2.5;
/** A neighbour within 2 pixels with ≥ 40% of the source's peak in any window: blended. */
export const BLEND_RADIUS = 2;
export const BLEND_SHARE = 0.4;
/** ε when the field has too few bright stars to measure it (the value measured on 7 fields: 0.08–0.10). */
export const EPS_DEFAULT = 0.1;
/** J − [4.6] AB > −0.5 (pipeline BD_MIN_J_M46_AB) and ≥ 0 (LABEL_ICE_MIN_J_M46), as flux ratios. */
const RED_BD_RATIO = 10 ** (-0.4 * 0.5);
const RED_ICE_RATIO = 1;
/**
 * Dust (PAH) glow of the sky itself, measured in absolute brightness (see dustGlow). The map is cut
 * into cells this many pixels a side (7 px = 1.5′; a 30′ field has 20 × 20 of them).
 */
export const GLOW_CELL = 7;
/** Continuum either side of the 3.3 µm band, µm: three map frames each. The band is the frame nearest GLOW_PEAK_UM. */
export const GLOW_BLUE: [number, number] = [2.9, 3.16];
export const GLOW_RED: [number, number] = [3.52, 3.8];
export const GLOW_PEAK_UM = 3.29;
/**
 * Least excess at 3.3 µm, MJy/sr, for a cell to count as found, and as a hint. The map's zero level
 * differs from frame to frame: in 8 fields with almost no dust (IRAS 100 µm under 2.5 MJy/sr) the
 * whole sky's "excess" came out between −0.002 and +0.004. Found is twice that.
 */
export const GLOW_FOUND_MJY_SR = 0.008;
export const GLOW_HINT_MJY_SR = 0.004;
export const GLOW_FOUND_SIGMA = 5;
export const GLOW_HINT_SIGMA = 3;
/**
 * The band is one frame wide at its peak, but PAH also glows in the frames either side (3.2 and
 * 3.4 µm). Over the found cells those two wings together must hold at least this share of the peak:
 * real fields gave 0.4–1.1 (30 fields); a one-frame artefact gives 0.
 */
export const GLOW_WING_SHARE = 0.15;
/**
 * Pixels around each star left out of the glow (its light, with its frame-to-frame jitter). One
 * pixel: with two, half of the cells of a field were lost to stars, and with one the two no-band
 * frames still gave no found cell (0 of 19,062 cells over 31 fields).
 */
export const GLOW_MASK_GROW = 1;
/**
 * Fewest star-free pixels for a cell to be measured (of its 49). A cell with fewer pixels than the
 * typical one gets a larger error, by √(typical ÷ its own). With 12 instead of half the cell, 31
 * fields kept 350–399 of their 400 cells and the two no-band frames still gave no found cell
 * (0 of 23,452).
 */
export const GLOW_MIN_PIXELS = 12;
/**
 * A source is "in glare" when the light around it (the ring's median, over its 9 pixels) is at
 * least this share of its own light. Then what is left after taking the surroundings off is mostly
 * the error of that subtraction: next to R Draconis (a star with deep water bands) four faint
 * "sources" in its glare showed water at 5–11σ with the surroundings 3–13 times their own light,
 * and one beside Epsilon Indi A likewise (4 times). The 16 water detections away from bright stars
 * had −0.24 to +0.25.
 */
export const GLARE_SHARE = 0.5;
/**
 * From GLARE_SHARE up to this, a sign that passes every other check is kept as a hint ("possible"):
 * it is measured, but may belong to the light around the source (a star inside a bright cloud,
 * whose scattered light crosses the same dust). Beyond it nothing is said at all.
 */
export const GLARE_CANT = 2;
/** A cell holding a pixel this bright (MJy/sr above the sky) is left alone: a saturating star or core. */
export const BLOCK_BRIGHT_MJY_SR = 20;

/**
 * Water vapour's three bands with wide windows (A3's own, not the pipeline's): more map frames per
 * band, so less noise. The first two sit inside one detector each (D2, D3); the third takes its
 * blue continuum from D3 and the rest from D4, which were exposed at different times, so it only
 * adds to the other two and never carries a detection alone.
 */
export const WATER_WIDE: readonly IndexDef[] = [
  { kind: "abs", band: [1.33, 1.5], cont: [[1.22, 1.31], [1.55, 1.62]] },
  { kind: "abs", band: [1.78, 2.05], cont: [[1.64, 1.74], [2.1, 2.28]] },
  { kind: "abs", band: [2.45, 2.95], cont: [[2.1, 2.28], [3.5, 3.8]] },
];
/**
 * Water vapour, three wide bands together. Each band's depth ≥ JOINT_EACH_SIGMA; their weighted mean
 * ≥ JOINT_SIGMA and ≥ JOINT_MIN_DEPTH; the two one-detector bands alone ≥ JOINT_PAIR_SIGMA; and with
 * any one frame left out the mean stays ≥ JOINT_LOO_SIGMA, the pair ≥ JOINT_LOO_PAIR_SIGMA and each
 * band ≥ JOINT_LOO_EACH_SIGMA. On 31 real fields (6,654 measurable sources) the first three
 * conditions marked 23 sources; the mirror image (three "emission" bands, which nothing real makes) marked none.
 */
export const JOINT_EACH_SIGMA = 1.5;
export const JOINT_SIGMA = 5;
export const JOINT_PAIR_SIGMA = 4;
export const JOINT_MIN_DEPTH = 0.05;
export const JOINT_LOO_SIGMA = 4;
export const JOINT_LOO_PAIR_SIGMA = 3;
export const JOINT_LOO_EACH_SIGMA = 1;

export type FeatureState = "found" | "possible" | "none" | "cant";
/** Why a band is "can't tell" or only "possible". */
export type Reason =
  | "bright"
  | "blend"
  | "erratic"
  | "glare"
  | "faint"
  | "nodata"
  | "nocal"
  | "one_frame"
  | "not_red"
  | "no_second_band"
  | "vapour"
  | "co_gas"
  | "gaia_star"
  | "moving"
  | "variable"
  | "ice"
  | "ice_maybe";

export interface BandResult {
  state: FeatureState;
  /** Depth (absorption) or excess (emission), zero-pointed on the field's stars. NaN if unmeasured. */
  depth: number;
  err: number;
  /** depth ÷ err. */
  sig: number;
  contSnr: number;
  reasons: Reason[];
  /**
   * Set when a band one map frame wide (CO₂ or CO ice) is counted as found because water ice,
   * which spans several frames, is found in the same source.
   */
  confirmedBy?: IndexId;
  /**
   * Set on the water vapour bands when they count as found through the three wide bands together
   * (WATER_WIDE): the weighted mean depth of the three, its error and depth ÷ error.
   */
  joint?: { depth: number; err: number; sig: number };
}

export interface Source {
  /** Pixel of its peak: column (x, east left) and row (y, north up). */
  x: number;
  y: number;
  /** Peak S/N in its best detection window. */
  snr: number;
  bright: boolean;
  /** The light around it is at least GLARE_SHARE of its own: the glare of a brighter star, or nebula. */
  glare: boolean;
  blend: boolean;
  erratic: boolean;
  /** Roughness of its spectrum in units of its errors (NaN: too few frames). */
  roughness: number;
  /** Frames left out: lost exposures, glitches. */
  dropped: { exposures: number; glitches: number };
  /** Red enough for a cold brown dwarf / for a star behind an icy cloud (4.6 µm vs J). */
  redBd: boolean;
  redIce: boolean;
  bands: Record<IndexId, BandResult>;
}

export interface Calibration {
  ok: boolean;
  /** Reference stars used. */
  n: number;
  /** Their median band ÷ continuum: the "no feature" value. */
  r0: number;
  scatter: number;
  zpErr: number;
  /** Error scale: spread of the reference stars' pulls, at least 1. */
  k: number;
}

/** One cell of the dust-glow map. */
export interface Block {
  /** Top-left pixel and size (pixels a side). */
  x: number;
  y: number;
  size: number;
  state: FeatureState;
  /** Excess at 3.3 µm over the continuum either side, MJy/sr (NaN where it couldn't be measured). */
  depth: number;
  /** Noise of one cell, MJy/sr. */
  err: number;
  reasons: Reason[];
}

/** The dust glow of the picture as a whole. */
export interface GlowSummary {
  /** 3.3 µm excess of the picture's sky level (each frame's median) over its continuum, MJy/sr. */
  sky: number;
  /** Scatter of the sky's six continuum frames about a straight line, MJy/sr. */
  skyScatter: number;
  /** Noise of one cell, MJy/sr: the scatter of the cells of two frames that hold no band. */
  cellSigma: number;
  /** (3.2 µm + 3.4 µm) ÷ 3.3 µm excess over the cells that reached "found"; NaN when none did. */
  wingShare: number;
  /** The wings agree with the peak (wingShare ≥ GLOW_WING_SHARE). */
  wings: boolean;
}

export interface Composition {
  n: number;
  /** Frame-to-frame jitter of bright stars, as a fraction of flux. */
  eps: number;
  sources: Source[];
  cal: Record<IndexId, Calibration>;
  /** Dust (PAH) glow per cell of GLOW_CELL pixels, row by row. */
  blocks: Block[];
  glow: GlowSummary;
  /** Per pixel: strongest S/N among the detection windows (the grey picture). */
  detection: Float32Array;
}

/**
 * Exposure counts (NHITS) are read only near pixels this bright in any frame, MJy/sr above the
 * background, and this many pixels around them. Lost exposures mean saturation, which began at
 * 30–100 MJy/sr on real fields (below 30, the brightest pixel kept all its exposures), so 10
 * leaves a factor of three. Elsewhere no frame is dropped for its exposure count.
 */
export const HITS_MIN_MJY_SR = 10;
export const HITS_GROW = 5;

/** Pixels whose exposure counts the composition needs: within HITS_GROW of a bright pixel. */
export function brightMask(images: Map<number, Float32Array>, n: number): Uint8Array {
  const hot = new Uint8Array(n * n);
  for (const f of compositionFrames()) {
    const img = images.get(f);
    if (!img) continue;
    const limit = planeStats(img, n).median + HITS_MIN_MJY_SR;
    for (let k = 0; k < img.length; k++) if (img[k] >= limit) hot[k] = 1;
  }
  const out = new Uint8Array(n * n);
  for (let j = 0; j < n; j++) {
    for (let i = 0; i < n; i++) {
      if (!hot[j * n + i]) continue;
      for (let y = Math.max(0, j - HITS_GROW); y <= Math.min(n - 1, j + HITS_GROW); y++) {
        for (let x = Math.max(0, i - HITS_GROW); x <= Math.min(n - 1, i + HITS_GROW); x++) out[y * n + x] = 1;
      }
    }
  }
  return out;
}

/** The cube frames the composition reads. */
export function compositionFrames(): number[] {
  const out: number[] = [];
  for (let f = FIRST_FRAME; f < CUBE_UM.length; f++) if (!BLANK_FRAMES.has(f)) out.push(f);
  return out;
}

// ---- small statistics -------------------------------------------------------------------------
function medianInPlace(a: Float64Array, len: number): number {
  if (!len) return NaN;
  if (len <= 64) {
    // Insertion sort: these medians run a few million times on 9–49 values.
    for (let i = 1; i < len; i++) {
      const v = a[i];
      let k = i - 1;
      while (k >= 0 && a[k] > v) {
        a[k + 1] = a[k];
        k--;
      }
      a[k + 1] = v;
    }
  } else a.subarray(0, len).sort();
  const m = len >> 1;
  return len % 2 ? a[m] : (a[m - 1] + a[m]) / 2;
}

/** Median and 1.4826 × MAD of the finite values. */
function robust(values: number[]): { median: number; sigma: number } {
  const v = Float64Array.from(values.filter(Number.isFinite));
  if (!v.length) return { median: NaN, sigma: NaN };
  const median = medianInPlace(v, v.length);
  const dev = Float64Array.from(v, (x) => Math.abs(x - median));
  return { median, sigma: 1.4826 * medianInPlace(dev, dev.length) };
}

/** Most pixels used for a frame's background and noise: beyond this, an even sample of the frame. */
const STATS_SAMPLE = 20000;

/** Sigma-clipped median and σ of a plane, from an even sample of its pixels when it is large. */
function planeStats(plane: Float32Array, n: number): { median: number; sigma: number } {
  if (plane.length <= STATS_SAMPLE) return clippedStats(plane);
  let stride = Math.ceil(plane.length / STATS_SAMPLE);
  while (n % stride === 0) stride++; // never a whole column
  const sample = new Float32Array(Math.ceil(plane.length / stride));
  for (let k = 0, c = 0; k < plane.length; k += stride) sample[c++] = plane[k];
  return clippedStats(sample);
}

/** Neighbour frames (±2) in the same detector, blank frames and frames outside the set left out. */
function neighbourFrames(f: number): number[] {
  const det = Math.floor(f / 17);
  const out: number[] = [];
  for (let g = f - 2; g <= f + 2; g++) {
    if (g !== f && g >= FIRST_FRAME && g < CUBE_UM.length && Math.floor(g / 17) === det && !BLANK_FRAMES.has(g)) out.push(g);
  }
  return out;
}

/** Frames with both neighbours measured in the same detector (for second differences). */
function interiorFrames(): number[] {
  return compositionFrames().filter(
    (f) => f % 17 !== 0 && f % 17 !== 16 && !BLANK_FRAMES.has(f - 1) && !BLANK_FRAMES.has(f + 1) && f - 1 >= FIRST_FRAME,
  );
}

const RING: [number, number][] = [];
for (let dj = -MARGIN; dj <= MARGIN; dj++) {
  for (let di = -MARGIN; di <= MARGIN; di++) if (Math.max(Math.abs(dj), Math.abs(di)) === MARGIN) RING.push([dj, di]);
}
const ringBuf = new Float64Array(RING.length);

/**
 * Light of a source in one frame: its 3 × 3 pixels minus the median of the ring 3 pixels out.
 * Fills `box` (9 values, background off). Returns NaN when the ring has under half its pixels or
 * a box pixel has no data; `hole` says a box pixel had no data although the ring was fine; `bg` is
 * the ring's median (per pixel), the light around the source that was taken off.
 */
function aperture(plane: Float32Array, n: number, j: number, i: number, box: Float64Array): { flux: number; hole: boolean; bg: number } {
  let c = 0;
  for (const [dj, di] of RING) {
    const v = plane[(j + dj) * n + i + di];
    if (Number.isFinite(v)) ringBuf[c++] = v;
  }
  if (c < RING.length / 2) {
    box.fill(NaN);
    return { flux: NaN, hole: false, bg: NaN };
  }
  const bg = medianInPlace(ringBuf, c);
  let sum = 0;
  let k = 0;
  for (let dj = -1; dj <= 1; dj++) {
    for (let di = -1; di <= 1; di++) {
      const v = plane[(j + dj) * n + i + di] - bg;
      box[k++] = v;
      sum += v;
    }
  }
  return { flux: sum, hole: !Number.isFinite(sum), bg };
}

export interface CompositionInput {
  n: number;
  /** Frame → surface brightness plane (MJy/sr, n × n), for every frame of compositionFrames(). */
  images: Map<number, Float32Array>;
  /**
   * Frame → exposures per pixel (NHITS). −1 means "not read": only the pixels of brightMask() need
   * it, and a source whose 7 × 7 surroundings weren't all read has no frame dropped for lost exposures.
   */
  hits: Map<number, Int32Array>;
}

/** Sources, their features and the diffuse glow of one field. */
export function measureComposition(input: CompositionInput): Composition {
  const { n } = input;
  const frames = compositionFrames();
  const NF = CUBE_UM.length;

  // 1. background off, noise per frame
  const sub = new Map<number, Float32Array>();
  const sigPx = new Float64Array(NF).fill(NaN);
  for (const f of frames) {
    const img = input.images.get(f);
    if (!img) throw new Error(`composition: frame ${f} is missing`);
    const st = planeStats(img, n);
    const out = new Float32Array(n * n);
    for (let k = 0; k < out.length; k++) out[k] = img[k] - st.median;
    sub.set(f, out);
    sigPx[f] = st.sigma;
  }

  // 2. detection: S/N image per window, the best of them per pixel
  const stacks: Float32Array[] = [];
  const detection = new Float32Array(n * n).fill(-Infinity);
  for (const [a, b] of DETECT_WINDOWS) {
    const fs = frames.filter((f) => CUBE_UM[f] >= a && CUBE_UM[f] <= b);
    const raw = medianPlanes(fs.map((f) => sub.get(f)!));
    const st = planeStats(raw, n);
    const snr = new Float32Array(n * n);
    for (let k = 0; k < snr.length; k++) {
      snr[k] = (raw[k] - st.median) / st.sigma;
      if (snr[k] > detection[k]) detection[k] = snr[k];
    }
    stacks.push(snr);
  }
  const peaks: { j: number; i: number }[] = [];
  for (let j = MARGIN; j < n - MARGIN; j++) {
    for (let i = MARGIN; i < n - MARGIN; i++) {
      const v = detection[j * n + i];
      if (!(v >= DETECT_SIGMA)) continue;
      let top = true;
      for (let dj = -1; dj <= 1 && top; dj++) {
        for (let di = -1; di <= 1; di++) {
          if (!dj && !di) continue;
          const w = detection[(j + dj) * n + i + di];
          // Equal neighbours: the first in reading order wins, so a flat top gives one source.
          if (w > v || (w === v && (dj < 0 || (dj === 0 && di < 0)))) {
            top = false;
            break;
          }
        }
      }
      if (top) peaks.push({ j, i });
    }
  }
  const ns = peaks.length;

  // 3. aperture noise per frame: the same measurement on a grid over the field
  const apNoise = new Float64Array(NF).fill(NaN);
  {
    const step = Math.max(1, Math.ceil((n - 2 * MARGIN) / 70));
    const box = new Float64Array(9);
    for (const f of frames) {
      const plane = sub.get(f)!;
      const vals: number[] = [];
      for (let j = MARGIN; j < n - MARGIN; j += step) {
        for (let i = MARGIN; i < n - MARGIN; i += step) vals.push(aperture(plane, n, j, i, box).flux);
      }
      apNoise[f] = clippedStats(vals).sigma;
    }
  }

  // 3–4. per source: light per frame, its 3 × 3 shape, lost exposures, glitches
  const raw: Float64Array[] = []; // light per frame, before any frame is dropped
  const flux: Float64Array[] = [];
  const stat: Float64Array[] = [];
  /** glare: light around the source ÷ its own light (0 when the surroundings aren't brighter than the sky). */
  const meta: { bright: boolean; glare: number; exposures: number; glitches: number }[] = [];
  const bgBuf = new Float64Array(NF);
  const flBuf = new Float64Array(NF);
  const box = new Float64Array(9);
  const hitBuf = new Float64Array(49);
  const medBuf = new Float64Array(NF);
  for (const { j, i } of peaks) {
    const fl = new Float64Array(NF).fill(NaN);
    const boxes = new Float64Array(NF * 9).fill(NaN);
    const hole = new Uint8Array(NF);
    let peak = -Infinity;
    let nb = 0;
    for (const f of frames) {
      const a = aperture(sub.get(f)!, n, j, i, box);
      fl[f] = a.flux;
      if (Number.isFinite(a.flux) && Number.isFinite(a.bg)) {
        bgBuf[nb] = 9 * a.bg;
        flBuf[nb++] = a.flux;
      }
      hole[f] = a.hole ? 1 : 0;
      boxes.set(box, f * 9);
      for (let p = 0; p < 9; p++) if (box[p] > peak) peak = box[p];
    }
    // The source's shape: median over frames of each pixel; `top` its brightest pixel, `core` those with light.
    const prof = new Float64Array(9);
    for (let p = 0; p < 9; p++) {
      let c = 0;
      for (const f of frames) {
        const v = boxes[f * 9 + p];
        if (Number.isFinite(v)) medBuf[c++] = v;
      }
      prof[p] = medianInPlace(medBuf, c);
    }
    let top = 0;
    let profMax = -Infinity;
    for (let p = 0; p < 9; p++) {
      if (prof[p] > profMax) {
        profMax = prof[p];
        top = p;
      }
    }
    // Lost exposures: NHITS against the median of the 7 × 7 box around the source.
    const lost = new Uint8Array(NF);
    let lostAtPeak = 0;
    let holes = 0;
    for (const f of frames) {
      const h = input.hits.get(f);
      if (!h) throw new Error(`composition: NHITS of frame ${f} is missing`);
      let c = 0;
      for (let dj = -3; dj <= 3; dj++) for (let di = -3; di <= 3; di++) hitBuf[c++] = h[(j + dj) * n + i + di];
      const med = medianInPlace(hitBuf, 49); // sorts: hitBuf[0] is now the smallest
      if (hole[f]) holes++;
      if (!(med > 0) || hitBuf[0] < 0) continue; // no exposures at all, or the counts weren't read around this source
      const atTop = h[(j + Math.floor(top / 3) - 1) * n + i + (top % 3) - 1];
      if (atTop < KEPT_PEAK * med) {
        lost[f] = 1;
        lostAtPeak++;
        continue;
      }
      for (let p = 0; p < 9; p++) {
        if (prof[p] >= 0.2 * profMax && h[(j + Math.floor(p / 3) - 1) * n + i + (p % 3) - 1] < KEPT_CORE * med) {
          lost[f] = 1;
          break;
        }
      }
    }
    const bright = lostAtPeak / frames.length >= BRIGHT_LOST_SHARE || holes >= BRIGHT_HOLE_FRAMES || peak >= BRIGHT_PEAK_MJY_SR;
    // Glitches: the frame's 3 × 3 pixels against the neighbour frames' shape, scaled to this frame's light.
    const glitch = new Uint8Array(NF);
    const shape = new Float64Array(9);
    for (const f of frames) {
      if (fl[f] < -5 * apNoise[f]) glitch[f] = 1;
      const nb = neighbourFrames(f);
      if (nb.length < 2) continue;
      let total = 0;
      for (let p = 0; p < 9; p++) {
        let c = 0;
        for (const g of nb) {
          const v = boxes[g * 9 + p];
          if (Number.isFinite(v)) medBuf[c++] = v;
        }
        shape[p] = medianInPlace(medBuf, c);
        total += shape[p];
      }
      const scale = total > 3 * apNoise[f] ? fl[f] / total : 1;
      let worst = 0;
      let judged = true; // a pixel without data: the shape can't be compared
      for (let p = 0; p < 9; p++) {
        const d = Math.abs(boxes[f * 9 + p] - scale * shape[p]);
        if (!Number.isFinite(d)) judged = false;
        else if (d > worst) worst = d;
      }
      if (judged && worst > GLITCH_SIGMA * sigPx[f] && worst > GLITCH_SHARE * Math.abs(scale * total)) glitch[f] = 1;
    }
    const kept = new Float64Array(NF).fill(NaN);
    const st = new Float64Array(NF).fill(NaN);
    let nLost = 0;
    let nGlitch = 0;
    for (const f of frames) {
      st[f] = apNoise[f];
      if (lost[f]) nLost++;
      else if (glitch[f]) nGlitch++;
      else kept[f] = fl[f];
    }
    raw.push(fl);
    flux.push(kept);
    stat.push(st);
    // Glare: the light around the source (what the ring took off, over the same 9 pixels) against its own.
    const around = nb ? medianInPlace(bgBuf, nb) : NaN;
    const own = nb ? medianInPlace(flBuf, nb) : NaN;
    const glare = around > 0 ? (own > 0 ? around / own : Infinity) : 0;
    meta.push({ bright, glare, exposures: nLost, glitches: nGlitch });
  }

  // 5. ε: relative second differences of bright stars; then errors, roughness
  const inner = interiorFrames();
  const diffs: number[] = [];
  for (let s = 0; s < ns; s++) {
    const fl = raw[s];
    for (const f of inner) {
      if (fl[f] > 50 * apNoise[f] && fl[f - 1] > 50 * apNoise[f - 1] && fl[f + 1] > 50 * apNoise[f + 1]) {
        diffs.push(fl[f] / ((fl[f - 1] + fl[f + 1]) / 2) - 1);
      }
    }
  }
  const eps = diffs.length >= 200 ? Math.min(0.3, Math.max(0.05, robust(diffs).sigma / Math.sqrt(1.5))) : EPS_DEFAULT;
  const err: Float64Array[] = [];
  const roughness = new Float64Array(ns).fill(NaN);
  for (let s = 0; s < ns; s++) {
    const fl = flux[s];
    const e = new Float64Array(NF).fill(NaN);
    for (const f of frames) if (Number.isFinite(fl[f])) e[f] = Math.hypot(apNoise[f], eps * fl[f]);
    const z: number[] = [];
    for (const f of inner) {
      const d = fl[f] - (fl[f - 1] + fl[f + 1]) / 2;
      const sd = Math.sqrt(e[f] ** 2 + (e[f - 1] ** 2 + e[f + 1] ** 2) / 4);
      if (Number.isFinite(d) && sd > 0) z.push(d / sd);
    }
    if (z.length >= 20) {
      roughness[s] = robust(z).sigma;
      const scale = Math.max(1, roughness[s]);
      if (scale > 1) for (const f of frames) e[f] *= scale;
    }
    err.push(e);
  }

  // blends: a comparable neighbour within 2 pixels in any window
  const blend = new Uint8Array(ns);
  for (let a = 0; a < ns; a++) {
    const ka = peaks[a].j * n + peaks[a].i;
    for (let o = 0; o < ns && !blend[a]; o++) {
      if (o === a) continue;
      const d = Math.max(Math.abs(peaks[o].j - peaks[a].j), Math.abs(peaks[o].i - peaks[a].i));
      if (d > BLEND_RADIUS) continue;
      const ko = peaks[o].j * n + peaks[o].i;
      for (const st of stacks) {
        if (st[ko] >= BLEND_SHARE * Math.max(st[ka], DETECT_SIGMA)) {
          blend[a] = 1;
          break;
        }
      }
    }
  }

  // colour guards: 4.6 µm against J (J's 2σ upper limit when J isn't seen)
  const ch = cubeChannels();
  const wJ = windowWeights(ch, J_WINDOW[0], J_WINDOW[1]);
  const w46 = windowWeights(ch, M46_WINDOW[0], M46_WINDOW[1]);
  const redBd = new Uint8Array(ns);
  const redIce = new Uint8Array(ns);
  for (let s = 0; s < ns; s++) {
    const j = windowMean(flux[s], err[s], wJ, ch.centre);
    const m = windowMean(flux[s], err[s], w46, ch.centre);
    const jUpper = Number.isFinite(j.mean) && Number.isFinite(j.sig) ? Math.max(j.mean, 0) + 2 * j.sig : Infinity;
    const seen = m.mean >= 5 * m.sig;
    redBd[s] = seen && m.mean > RED_BD_RATIO * jUpper ? 1 : 0;
    redIce[s] = seen && m.mean >= RED_ICE_RATIO * jUpper ? 1 : 0;
  }

  // 6–7. indices, zero point from the field's stars, states
  const weights = {} as Record<IndexId, IndexWeights>;
  for (const id of INDEX_IDS) weights[id] = indexWeights(INDEX_WINDOWS[id], ch);
  const erratic = (s: number) => roughness[s] > ERRATIC_Q;
  const cal = {} as Record<IndexId, Calibration>;
  const res = {} as Record<IndexId, { depth: Float64Array; err: Float64Array; sig: Float64Array; contSnr: Float64Array; loo: Uint8Array }>;
  /** Per index: depth and its error from a band ÷ continuum ratio, with the field's zero point. */
  const zeroPointed = {} as Record<IndexId, (ratio: number, ratioSig: number) => { d: number; e: number }>;
  /** One index over every source: the band ÷ continuum ratio, and the field's zero point for it. */
  const measureIndex = (w: IndexWeights) => {
    const abs = w.def.kind === "abs";
    const ratio = new Float64Array(ns).fill(NaN);
    const ratioSig = new Float64Array(ns).fill(NaN);
    const contSnr = new Float64Array(ns).fill(NaN);
    const refRatio: number[] = [];
    const refSig: number[] = [];
    for (let s = 0; s < ns; s++) {
      const v = indexOf(flux[s], err[s], w);
      ratio[s] = v.ratio;
      ratioSig[s] = v.ratioSig;
      contSnr[s] = v.cont / v.contSig;
      if (blend[s] || meta[s].bright || meta[s].glare >= GLARE_SHARE || erratic(s) || !Number.isFinite(v.ratio) || !Number.isFinite(v.ratioSig)) continue;
      const plain = indexOf(flux[s], stat[s], w); // photon and sky noise only: "is it a bright star?"
      if (plain.cont / plain.contSig >= REF_MIN_SNR && plain.band / plain.bandSig >= REF_MIN_SNR / 2) {
        refRatio.push(v.ratio);
        refSig.push(v.ratioSig);
      }
    }
    const c: Calibration = { ok: false, n: refRatio.length, r0: NaN, scatter: NaN, zpErr: NaN, k: NaN };
    if (refRatio.length >= MIN_REF) {
      let keep = refRatio.slice();
      for (let pass = 0; pass < 5; pass++) {
        const r = robust(keep);
        const next = refRatio.filter((x) => Math.abs(x - r.median) <= 3 * r.sigma);
        if (next.length === keep.length || next.length < MIN_REF) break;
        keep = next;
      }
      const r = robust(keep);
      c.r0 = r.median;
      c.scatter = r.sigma;
      c.zpErr = (1.2533 * r.sigma) / Math.sqrt(keep.length);
      c.k = Math.max(1, robust(refRatio.map((x, q) => (x - r.median) / refSig[q])).sigma);
      c.ok = c.r0 > 0 && Number.isFinite(c.zpErr) && Number.isFinite(c.k);
    }
    /** Depth and its error from a ratio, with the zero point. */
    const calibrated = (rat: number, ratSig: number) => {
      const rc = rat / c.r0;
      const d = abs ? 1 - rc : rc - 1;
      return { d, e: Math.hypot((c.k * ratSig) / c.r0, (rc * c.zpErr) / c.r0) };
    };
    return { c, ratio, ratioSig, contSnr, calibrated };
  };
  for (const id of INDEX_IDS) {
    const w = weights[id];
    const { c, ratio, ratioSig, contSnr, calibrated } = measureIndex(w);
    cal[id] = c;
    const depth = new Float64Array(ns).fill(NaN);
    const dErr = new Float64Array(ns).fill(NaN);
    const sig = new Float64Array(ns).fill(NaN);
    const loo = new Uint8Array(ns).fill(1);
    zeroPointed[id] = calibrated;
    if (c.ok) {
      for (let s = 0; s < ns; s++) {
        const { d, e } = calibrated(ratio[s], ratioSig[s]);
        depth[s] = d;
        dErr[s] = e;
        sig[s] = d / e;
        if (!(sig[s] >= FOUND_SIGMA && d >= MIN_DEPTH[id])) continue;
        // Leave each frame out in turn: a feature carried by one frame doesn't count.
        // A band that can't spare a frame (indexOf gives NaN) rests on that frame: it fails too.
        for (const f of w.frames) {
          if (!Number.isFinite(flux[s][f])) continue;
          const v = indexOf(flux[s], err[s], w, f);
          const o = calibrated(v.ratio, v.ratioSig);
          if (!(o.d / o.e >= LOO_SIGMA && o.d >= 0.5 * MIN_DEPTH[id])) {
            loo[s] = 0;
            break;
          }
        }
      }
    }
    res[id] = { depth, err: dErr, sig, contSnr, loo };
  }

  const support = (id: IndexId, s: number) => res[id].sig[s] >= SUPPORT_SIGMA;

  // Water vapour as a pair of bands (PAIR_* above).
  const waterPair = new Uint8Array(ns);
  if (cal.h2o_14.ok && cal.h2o_19.ok) {
    const pair = ["h2o_14", "h2o_19"] as const;
    const frames2 = [...new Set([...weights.h2o_14.frames, ...weights.h2o_19.frames])];
    for (let s = 0; s < ns; s++) {
      const [a, b] = pair.map((id) => ({ sig: res[id].sig[s], d: res[id].depth[s] }));
      if (!(a.sig >= PAIR_EACH_SIGMA && b.sig >= PAIR_EACH_SIGMA && Math.hypot(a.sig, b.sig) >= PAIR_SIGMA)) continue;
      if (!(a.d >= MIN_DEPTH.h2o_14 && b.d >= MIN_DEPTH.h2o_19)) continue;
      let ok = true;
      for (const f of frames2) {
        if (!Number.isFinite(flux[s][f])) continue;
        const left = pair.map((id) => {
          if (!weights[id].frames.includes(f)) return { sig: res[id].sig[s], d: res[id].depth[s] };
          const v = indexOf(flux[s], err[s], weights[id], f);
          const o = zeroPointed[id](v.ratio, v.ratioSig);
          return { sig: o.d / o.e, d: o.d };
        });
        const each = left.every((o, k) => o.sig >= PAIR_LOO_EACH_SIGMA && o.d >= 0.5 * MIN_DEPTH[pair[k]]);
        if (!(each && Math.hypot(left[0].sig, left[1].sig) >= PAIR_LOO_SIGMA)) {
          ok = false;
          break;
        }
      }
      if (ok) waterPair[s] = 1;
    }
  }

  // Water vapour through three wide bands together (WATER_WIDE, JOINT_* above).
  const waterJoint: (BandResult["joint"] | null)[] = new Array(ns).fill(null);
  const wide = WATER_WIDE.map((def) => {
    const w = indexWeights(def, ch);
    return { w, ...measureIndex(w) };
  });
  if (wide.every((m) => m.c.ok)) {
    const combine = (parts: { d: number; e: number }[]) => {
      let sw = 0;
      let swd = 0;
      for (const p of parts) {
        sw += 1 / (p.e * p.e);
        swd += p.d / (p.e * p.e);
      }
      return { depth: swd / sw, err: 1 / Math.sqrt(sw) };
    };
    /** The three depths (one frame left out), their weighted mean, and the mean of the first two. */
    const jointOf = (s: number, skip = -1) => {
      const parts = wide.map((m) => {
        if (skip >= 0 && m.w.frames.includes(skip)) {
          const v = indexOf(flux[s], err[s], m.w, skip);
          return m.calibrated(v.ratio, v.ratioSig);
        }
        return m.calibrated(m.ratio[s], m.ratioSig[s]);
      });
      if (!parts.every((p) => Number.isFinite(p.d) && Number.isFinite(p.e) && p.e > 0)) return null;
      return { parts, all: combine(parts), pair: combine(parts.slice(0, 2)) };
    };
    const frames3 = [...new Set(wide.flatMap((m) => m.w.frames))];
    for (let s = 0; s < ns; s++) {
      if (blend[s] || meta[s].bright || meta[s].glare >= GLARE_CANT || erratic(s)) continue;
      if (!wide.every((m) => m.contSnr[s] >= CONT_MIN_SNR)) continue;
      const j = jointOf(s);
      if (!j) continue;
      if (!j.parts.every((p) => p.d / p.e >= JOINT_EACH_SIGMA)) continue;
      if (!(j.all.depth / j.all.err >= JOINT_SIGMA && j.all.depth >= JOINT_MIN_DEPTH && j.pair.depth / j.pair.err >= JOINT_PAIR_SIGMA)) continue;
      let ok = true;
      for (const f of frames3) {
        if (!Number.isFinite(flux[s][f])) continue;
        const o = jointOf(s, f);
        if (
          !o ||
          !(o.all.depth / o.all.err >= JOINT_LOO_SIGMA && o.pair.depth / o.pair.err >= JOINT_LOO_PAIR_SIGMA) ||
          !o.parts.every((p) => p.d / p.e >= JOINT_LOO_EACH_SIGMA)
        ) {
          ok = false;
          break;
        }
      }
      if (ok) waterJoint[s] = { depth: j.all.depth, err: j.all.err, sig: j.all.depth / j.all.err };
    }
  }

  const sources: Source[] = [];
  for (let s = 0; s < ns; s++) {
    const vapour = support("h2o_14", s) || support("h2o_19", s) || !!waterJoint[s];
    // Water ice's broad band (2.8–3.6 µm) dips the methane window at 3.2–3.45 µm too. Seen on real
    // clouds (15 Nov): every ice source also "fired" methane at 3.3 µm, always with the ice band
    // the deeper of the two (ice 71–114%, the methane window 30–105%); in Epsilon Indi B, a real
    // methane dwarf, the ice band is flat (−10 ± 14%) under a 68% methane dip. So methane at 3.3 µm
    // counts only where the ice band was measured and is under 3σ. Where the ice band is at least
    // as deep as the dip, the dip is ice ("measured, not there", reason "ice"); otherwise it stays
    // possible ("ice_maybe").
    const iceLike = !(res.h2o_ice_305.sig[s] < SUPPORT_SIGMA);
    const iceDeeper = iceLike && res.h2o_ice_305.depth[s] >= res.ch4_33.depth[s];
    const bands = {} as Record<IndexId, BandResult>;
    for (const id of INDEX_IDS) {
      const r = res[id];
      const reasons: Reason[] = [];
      let state: FeatureState;
      const water = id === "h2o_14" || id === "h2o_19";
      if (!cal[id].ok) reasons.push("nocal");
      if (meta[s].bright) reasons.push("bright");
      if (meta[s].glare >= GLARE_CANT) reasons.push("glare");
      if (blend[s]) reasons.push("blend");
      if (erratic(s)) reasons.push("erratic");
      if (!reasons.length && !(Number.isFinite(r.depth[s]) && Number.isFinite(r.err[s]))) reasons.push("nodata");
      if (!reasons.length && !(r.contSnr[s] >= CONT_MIN_SNR)) reasons.push("faint");
      if (water && waterJoint[s]) {
        // The three wide bands together: this band may be weak, or unmeasured, on its own.
        state = "found";
        reasons.length = 0;
      } else if (reasons.length) state = "cant";
      else if (water && waterPair[s]) state = "found";
      else if (!(r.sig[s] >= FOUND_SIGMA && r.depth[s] >= MIN_DEPTH[id])) state = "none";
      else {
        // The pipeline's look-alike guards (classify.band_labels), with what the cube can test.
        if (!r.loo[s]) reasons.push("one_frame");
        if (id === "h2o_ice_305" || id === "co2_ice_427" || id === "co_ice_467") {
          if (vapour) reasons.push("vapour");
          if (!redIce[s]) reasons.push("not_red");
          if (id === "co_ice_467" && support("co_23", s)) reasons.push("co_gas");
        } else if (id === "ch4_33") {
          if (!redBd[s]) reasons.push("not_red");
          if (!(support("ch4_16", s) || vapour)) reasons.push("no_second_band");
          if (iceLike) reasons.push("ice_maybe");
        } else if (id === "ch4_16") {
          if (!redBd[s]) reasons.push("not_red");
          if (!((support("ch4_33", s) && !iceLike) || vapour)) reasons.push("no_second_band");
        } else if (water) {
          // Water vapour absorbs at 1.4 and 1.9 µm together: one band alone isn't water.
          if (!support(id === "h2o_14" ? "h2o_19" : "h2o_14", s)) reasons.push("no_second_band");
        }
        state = reasons.length ? "possible" : "found";
        if (id === "ch4_33" && iceDeeper) {
          state = "none";
          reasons.length = 0;
          reasons.push("ice");
        }
      }
      // In moderate glare a sign is kept, but only as a hint: it may belong to the light around the source.
      if (meta[s].glare >= GLARE_SHARE && (state === "found" || state === "possible")) {
        state = "possible";
        if (!reasons.includes("glare")) reasons.push("glare");
      }
      bands[id] = { state, depth: r.depth[s], err: r.err[s], sig: r.sig[s], contSnr: r.contSnr[s], reasons };
      if (water && waterJoint[s]) bands[id].joint = waterJoint[s]!;
    }
    // CO₂ and CO ice are one map frame wide, so leave-one-out can never pass for them. Where water
    // ice (several frames wide, confirmed on its own) is found in the same source, a one-frame ice
    // band that passed every other check counts. A chance one-frame dip of this size was seen on
    // about 0.1% of ordinary sources (3 in 2,983 band measurements), and never on an ice source.
    if (bands.h2o_ice_305.state === "found") {
      for (const id of ["co2_ice_427", "co_ice_467"] as const) {
        const b = bands[id];
        if (b.state === "possible" && b.reasons.length === 1 && b.reasons[0] === "one_frame") {
          bands[id] = { ...b, state: "found", reasons: [], confirmedBy: "h2o_ice_305" };
        }
      }
    }
    sources.push({
      x: peaks[s].i,
      y: peaks[s].j,
      snr: detection[peaks[s].j * n + peaks[s].i],
      bright: meta[s].bright,
      glare: meta[s].glare >= GLARE_SHARE,
      blend: !!blend[s],
      erratic: erratic(s),
      roughness: roughness[s],
      dropped: { exposures: meta[s].exposures, glitches: meta[s].glitches },
      redBd: !!redBd[s],
      redIce: !!redIce[s],
      bands,
    });
  }

  return { n, eps, sources, cal, ...dustGlow(n, input.images), detection };
}

/** Weights that give a straight line through `frames` (by wavelength), evaluated at `um`. */
function lineWeights(frames: number[], um: number): number[] {
  const x = frames.map((f) => CUBE_UM[f]);
  const mean = x.reduce((p, q) => p + q, 0) / x.length;
  const sxx = x.reduce((p, q) => p + (q - mean) ** 2, 0);
  return x.map((q) => 1 / x.length + (sxx > 0 ? ((um - mean) * (q - mean)) / sxx : 0));
}

/**
 * Dust (PAH) glow of the sky, per cell, in absolute brightness.
 *
 * The 3.3 µm band of PAH dust adds light to the sky itself wherever the Milky Way's dust is, so it
 * is measured on the map's own surface brightness, not on sources and not after taking the frame's
 * background off (that would remove a glow that fills the picture). Per pixel:
 *
 *   excess = I(3.29 µm) − max( straight line through the six continuum frames at 3.29 µm,
 *                              mean of the three red continuum frames )
 *
 * The second term keeps a continuum whose blue side is lowered by water ice (3.05 µm, in scattered
 * light) from being read as a bump. Pixels within GLOW_MASK_GROW of anything ≥ 5σ in the continuum
 * frames are left out (a star's light jumps ~9% from frame to frame); a cell's value is the median
 * of the rest (at least GLOW_MIN_PIXELS of them). Its
 * noise is measured, not assumed: the same sum on two frames that hold no band (the middle blue and
 * the middle red continuum frame, each against the other continuum frames), scatter over the cells.
 *
 * Found: excess ≥ 5 × noise and ≥ GLOW_FOUND_MJY_SR, with a neighbouring cell at hint level or more,
 * and, over all such cells, the 3.2 and 3.4 µm wings agreeing (GLOW_WING_SHARE). Hint: ≥ 3 × noise
 * and ≥ GLOW_HINT_MJY_SR. Checked on 31 real fields against the IRAS 100 µm dust map: no cell found
 * in the 9 fields under 2.5 MJy/sr except one (WISE 0855, 17 cells), cells found in all 19 fields
 * above 3 MJy/sr, and none in 19,062 cells of the two no-band frames (docs/colour-layers.md).
 */
function dustGlow(n: number, images: Map<number, Float32Array>): { blocks: Block[]; glow: GlowSummary } {
  const frames = compositionFrames();
  const inside = (f: number, w: [number, number]) => CUBE_UM[f] >= w[0] && CUBE_UM[f] <= w[1];
  const blue = frames.filter((f) => inside(f, GLOW_BLUE));
  const red = frames.filter((f) => inside(f, GLOW_RED));
  const cont = [...blue, ...red];
  let peak = frames[0];
  for (const f of frames) if (Math.abs(CUBE_UM[f] - GLOW_PEAK_UM) < Math.abs(CUBE_UM[peak] - GLOW_PEAK_UM)) peak = f;
  const size = GLOW_CELL;
  const nb = Math.floor(n / size);
  const off = Math.floor((n - nb * size) / 2);
  const blocks: Block[] = [];
  const none: GlowSummary = { sky: NaN, skyScatter: NaN, cellSigma: NaN, wingShare: NaN, wings: false };
  const plane = (f: number) => images.get(f)!;
  if (nb < 3 || blue.length < 2 || red.length < 2 || ![peak - 1, peak, peak + 1, ...cont].every((f) => images.has(f))) return { blocks, glow: none };

  // Pixels left out: whatever stands out at these wavelengths (the median of the continuum frames,
  // ≥ 5σ above the sky), and the pixels around it. Stars are far fainter here than at 1–2 µm, so
  // this leaves much more sky to measure than the sources' own detection picture would.
  const stack = medianPlanes(cont.map(plane));
  const st = planeStats(stack, n);
  const masked = new Uint8Array(n * n);
  for (let j = 0; j < n; j++) {
    for (let i = 0; i < n; i++) {
      if (!((stack[j * n + i] - st.median) / st.sigma >= DETECT_SIGMA)) continue;
      for (let dj = -GLOW_MASK_GROW; dj <= GLOW_MASK_GROW; dj++) {
        for (let di = -GLOW_MASK_GROW + Math.abs(dj); di <= GLOW_MASK_GROW - Math.abs(dj); di++) {
          const y = j + dj;
          const x = i + di;
          if (y >= 0 && x >= 0 && y < n && x < n) masked[y * n + x] = 1;
        }
      }
    }
  }

  /** Per cell: median over its unmasked pixels of band − max(line through `line`, mean of `side`). */
  const buf = new Float64Array(size * size);
  const used = new Float64Array(nb * nb); // star-free pixels per cell, from the last call
  const cells = (band: number, line: number[], side: number[]): Float64Array => {
    const w = lineWeights(line, CUBE_UM[band]);
    const out = new Float64Array(nb * nb).fill(NaN);
    const b = plane(band);
    const lp = line.map(plane);
    const sp = side.map(plane);
    for (let bj = 0; bj < nb; bj++) {
      for (let bi = 0; bi < nb; bi++) {
        let cnt = 0;
        for (let dj = 0; dj < size; dj++) {
          for (let di = 0; di < size; di++) {
            const k = (off + bj * size + dj) * n + off + bi * size + di;
            if (masked[k]) continue;
            let fit = 0;
            for (let q = 0; q < lp.length; q++) fit += w[q] * lp[q][k];
            let mean = 0;
            for (let q = 0; q < sp.length; q++) mean += sp[q][k];
            const v = b[k] - Math.max(fit, mean / sp.length);
            if (Number.isFinite(v)) buf[cnt++] = v;
          }
        }
        used[bj * nb + bi] = cnt;
        if (cnt >= GLOW_MIN_PIXELS) out[bj * nb + bi] = medianInPlace(buf, cnt);
      }
    }
    return out;
  };

  const wingLo = cells(peak - 1, cont, red);
  const wingHi = cells(peak + 1, cont, red);
  const value = cells(peak, cont, red);
  const pixels = Float64Array.from(used);
  // A cell with fewer star-free pixels than the typical cell is noisier: √(typical ÷ its own).
  const full = [...pixels].filter((c) => c >= (size * size) / 2);
  const typical = full.length ? robust(full).median : size * size;
  // Noise of a cell: two frames with no band, each against the other continuum frames.
  const nullBlue = blue[Math.floor(blue.length / 2)];
  const nullRed = red[Math.floor(red.length / 2)];
  const sigma = Math.max(
    robust([...cells(nullBlue, cont.filter((f) => f !== nullBlue), red)]).sigma,
    robust([...cells(nullRed, cont.filter((f) => f !== nullRed), red.filter((f) => f !== nullRed))]).sigma,
  );

  // A cell with a saturating star or core is left alone.
  const bright = new Uint8Array(nb * nb);
  const level = planeStats(plane(peak), n).median + BLOCK_BRIGHT_MJY_SR;
  const pk = plane(peak);
  for (let bj = 0; bj < nb; bj++) {
    for (let bi = 0; bi < nb; bi++) {
      for (let dj = 0; dj < size && !bright[bj * nb + bi]; dj++) {
        for (let di = 0; di < size; di++) {
          const v = pk[(off + bj * size + dj) * n + off + bi * size + di];
          if (!Number.isFinite(v) || v >= level) {
            bright[bj * nb + bi] = 1;
            break;
          }
        }
      }
    }
  }

  const errOf = (b: number) => sigma * Math.sqrt(Math.max(1, typical / Math.max(1, pixels[b])));
  const foundAt = (b: number) => Math.max(GLOW_FOUND_SIGMA * errOf(b), GLOW_FOUND_MJY_SR);
  const hintAt = (b: number) => Math.max(GLOW_HINT_SIGMA * errOf(b), GLOW_HINT_MJY_SR);
  const usable = (b: number) => !bright[b] && Number.isFinite(value[b]) && Number.isFinite(sigma);
  const hint = (b: number) => usable(b) && value[b] >= hintAt(b);
  const state: FeatureState[] = [];
  const reasons: Reason[][] = [];
  for (let b = 0; b < nb * nb; b++) {
    if (bright[b]) {
      state.push("cant");
      reasons.push(["bright"]);
    } else if (!usable(b)) {
      state.push("cant");
      reasons.push(["blend"]); // nearly all of the cell is taken by stars
    } else if (value[b] >= foundAt(b)) {
      // One frame wide: a second cell beside it has to agree.
      let backed = false;
      for (let dj = -1; dj <= 1; dj++) {
        for (let di = -1; di <= 1; di++) {
          const bj = Math.floor(b / nb) + dj;
          const bi = (b % nb) + di;
          if ((dj || di) && bj >= 0 && bi >= 0 && bj < nb && bi < nb && hint(bj * nb + bi)) backed = true;
        }
      }
      state.push(backed ? "found" : "possible");
      reasons.push(backed ? [] : ["one_frame"]);
    } else {
      state.push(hint(b) ? "possible" : "none");
      reasons.push([]);
    }
  }
  // The band's wings (3.2 and 3.4 µm) must show over the found cells taken together.
  let sumPeak = 0;
  let sumWings = 0;
  for (let b = 0; b < nb * nb; b++) {
    if (state[b] !== "found") continue;
    sumPeak += value[b];
    if (Number.isFinite(wingLo[b])) sumWings += wingLo[b];
    if (Number.isFinite(wingHi[b])) sumWings += wingHi[b];
  }
  const wingShare = sumPeak > 0 ? sumWings / sumPeak : NaN;
  const wings = wingShare >= GLOW_WING_SHARE;
  for (let b = 0; b < nb * nb; b++) {
    if (state[b] === "found" && !wings) {
      state[b] = "possible";
      reasons[b] = ["one_frame"];
    }
    blocks.push({ x: off + (b % nb) * size, y: off + Math.floor(b / nb) * size, size, state: state[b], depth: value[b], err: errOf(b), reasons: reasons[b] });
  }

  // The picture's sky level: each frame's median, the same formula.
  const sky = (f: number) => planeStats(plane(f), n).median;
  const levels = new Map(cont.map((f) => [f, sky(f)]));
  const line = (um: number) => lineWeights(cont, um).reduce((p, w, q) => p + w * levels.get(cont[q])!, 0);
  const redMean = red.reduce((p, f) => p + levels.get(f)!, 0) / red.length;
  let ss = 0;
  for (const f of cont) ss += (levels.get(f)! - line(CUBE_UM[f])) ** 2;
  return {
    blocks,
    glow: {
      sky: sky(peak) - Math.max(line(CUBE_UM[peak]), redMean),
      skyScatter: cont.length > 2 ? Math.sqrt(ss / (cont.length - 2)) : NaN,
      cellSigma: sigma,
      wingShare,
      wings,
    },
  };
}
