// Spectral indices: how deep a molecule's band is against the continuum either side of it.
// A port of the pipeline's indices.py (window_mean, continuum, index), so the site and the
// pipeline compute the same thing; indices.test.ts checks it against the Python on reference
// spectra (indices_ref.json, written by make_indices_ref.py).
//
// A window [a, b] µm is the mean of the channels it overlaps, each weighted by the fraction of the
// channel inside the window (channels without a value left out). A window with less than
// MIN_COVER of its channel weight measured is NaN. The continuum at the band is a power law
// through two side windows (a straight line when a side window is ≤ 0 or below S/N 3).
import { CUBE_UM } from "../sky/layers";

/** Wavelength channels: lower edge, upper edge and centre of each, µm. */
export interface Channels {
  lo: Float64Array;
  hi: Float64Array;
  centre: Float64Array;
}

export type IndexKind = "abs" | "emis";
export interface IndexDef {
  kind: IndexKind;
  band: [number, number];
  cont: [[number, number], [number, number]];
}

/** pipeline config.INDEX_WINDOWS. */
export const INDEX_WINDOWS = {
  h2o_ice_305: { kind: "abs", band: [2.95, 3.15], cont: [[2.45, 2.7], [3.65, 3.8]] },
  co2_ice_427: { kind: "abs", band: [4.23, 4.31], cont: [[4.05, 4.17], [4.4, 4.5]] },
  co_ice_467: { kind: "abs", band: [4.64, 4.7], cont: [[4.5, 4.58], [4.76, 4.9]] },
  ch4_33: { kind: "abs", band: [3.2, 3.45], cont: [[2.4, 2.65], [3.9, 4.1]] },
  ch4_16: { kind: "abs", band: [1.63, 1.72], cont: [[1.53, 1.59], [2.06, 2.14]] },
  co_23: { kind: "abs", band: [2.29, 2.4], cont: [[2.06, 2.14], [2.19, 2.28]] },
  h2o_14: { kind: "abs", band: [1.34, 1.45], cont: [[1.22, 1.3], [1.55, 1.65]] },
  h2o_19: { kind: "abs", band: [1.84, 1.95], cont: [[1.55, 1.65], [2.06, 2.14]] },
  pah_33: { kind: "emis", band: [3.24, 3.34], cont: [[3.0, 3.15], [3.5, 3.65]] },
} as const satisfies Record<string, IndexDef>;

export type IndexId = keyof typeof INDEX_WINDOWS;
export const INDEX_IDS = Object.keys(INDEX_WINDOWS) as IndexId[];

/** pipeline config.LABEL_MIN_DEPTH: the depth (or PAH excess) a feature needs. */
export const MIN_DEPTH: Record<IndexId, number> = {
  h2o_ice_305: 0.1,
  co2_ice_427: 0.1,
  co_ice_467: 0.1,
  ch4_33: 0.25,
  ch4_16: 0.15,
  h2o_14: 0.05,
  h2o_19: 0.05,
  co_23: 0.05,
  pah_33: 0.1,
};

/** pipeline config.MAG_WINDOWS (the two used for the colour guards). */
export const J_WINDOW: [number, number] = [1.15, 1.33];
export const M46_WINDOW: [number, number] = [4.4, 4.8];

/** pipeline INDEX_MIN_COVER, INDEX_LOG_CONT_MIN_SNR. */
export const MIN_COVER = 0.5;
export const LOG_CONT_MIN_SNR = 3;

/**
 * Channels of the all-sky cube. Each frame's edges are the geometric midpoints to its neighbours
 * in the same detector (the frames are close to evenly spaced in log wavelength, not exactly);
 * a detector's first and last frame take their one neighbour's spacing on the open side.
 */
export function cubeChannels(): Channels {
  const n = CUBE_UM.length;
  const lo = new Float64Array(n);
  const hi = new Float64Array(n);
  const centre = Float64Array.from(CUBE_UM);
  for (let i = 0; i < n; i++) {
    const first = i % 17 === 0;
    const last = i % 17 === 16;
    const down = first ? CUBE_UM[i + 1] / CUBE_UM[i] : CUBE_UM[i] / CUBE_UM[i - 1];
    const up = last ? CUBE_UM[i] / CUBE_UM[i - 1] : CUBE_UM[i + 1] / CUBE_UM[i];
    lo[i] = CUBE_UM[i] / Math.sqrt(down);
    hi[i] = CUBE_UM[i] * Math.sqrt(up);
  }
  return { lo, hi, centre };
}

/** Fraction of each channel inside [a, b]. */
export function windowWeights(ch: Channels, a: number, b: number): Float64Array {
  const w = new Float64Array(ch.lo.length);
  for (let i = 0; i < w.length; i++) {
    w[i] = Math.max(0, Math.min(ch.hi[i], b) - Math.max(ch.lo[i], a)) / (ch.hi[i] - ch.lo[i]);
  }
  return w;
}

export interface WindowValue {
  mean: number;
  sig: number;
  /** Weighted log-mean wavelength of the channels used, µm. */
  lam: number;
}

const NAN_WINDOW: WindowValue = { mean: NaN, sig: NaN, lam: NaN };

/**
 * Weighted mean of one spectrum in a window. `skip` leaves one channel out (the leave-one-out
 * check), and `minCover` 0 keeps a window that has any channel left.
 */
export function windowMean(
  flux: ArrayLike<number>,
  err: ArrayLike<number>,
  w: Float64Array,
  centre: Float64Array,
  skip = -1,
  minCover = MIN_COVER,
): WindowValue {
  let sw = 0;
  let tot = 0;
  let sf = 0;
  let se = 0;
  let sl = 0;
  for (let i = 0; i < w.length; i++) {
    const wi = w[i];
    if (wi <= 0) continue;
    tot += wi;
    const f = flux[i];
    const e = err[i];
    if (i === skip || !Number.isFinite(f) || !Number.isFinite(e) || !(e > 0)) continue;
    sw += wi;
    sf += wi * f;
    se += wi * wi * e * e;
    sl += wi * Math.log(centre[i]);
  }
  if (!(sw > 0) || sw < minCover * tot) return NAN_WINDOW;
  return { mean: sf / sw, sig: Math.sqrt(se) / sw, lam: Math.exp(sl / sw) };
}

/** Continuum at `lam` from two windows: power law if both are positive and significant, else linear. */
export function continuum(a: WindowValue, b: WindowValue, lam: number): { mean: number; sig: number } {
  const useLog =
    a.mean > 0 && b.mean > 0 && a.mean >= LOG_CONT_MIN_SNR * a.sig && b.mean >= LOG_CONT_MIN_SNR * b.sig;
  if (useLog) {
    const t = Math.log(lam / a.lam) / Math.log(b.lam / a.lam);
    const fc = Math.exp(Math.log(a.mean) + t * (Math.log(b.mean) - Math.log(a.mean)));
    return { mean: fc, sig: fc * Math.hypot(((1 - t) * a.sig) / a.mean, (t * b.sig) / b.mean) };
  }
  const t = (lam - a.lam) / (b.lam - a.lam);
  return { mean: a.mean + t * (b.mean - a.mean), sig: Math.hypot((1 - t) * a.sig, t * b.sig) };
}

/** Weights of an index's three windows on a channel grid, computed once. */
export interface IndexWeights {
  def: IndexDef;
  band: Float64Array;
  c1: Float64Array;
  c2: Float64Array;
  centre: Float64Array;
  /** Channels with any weight in the index. */
  frames: number[];
}

export function indexWeights(def: IndexDef, ch: Channels): IndexWeights {
  const band = windowWeights(ch, def.band[0], def.band[1]);
  const c1 = windowWeights(ch, def.cont[0][0], def.cont[0][1]);
  const c2 = windowWeights(ch, def.cont[1][0], def.cont[1][1]);
  const frames: number[] = [];
  for (let i = 0; i < band.length; i++) if (band[i] > 0 || c1[i] > 0 || c2[i] > 0) frames.push(i);
  return { def, band, c1, c2, centre: ch.centre, frames };
}

export interface IndexValue {
  /** Band ÷ continuum (NaN when a window is under-covered or the continuum is ≤ 0). */
  ratio: number;
  /** 1σ of the ratio: √(σ_band² + (ratio · σ_cont)²) / continuum. */
  ratioSig: number;
  band: number;
  bandSig: number;
  cont: number;
  contSig: number;
  /** The pipeline's depth (absorption) or excess (emission) and its significance. */
  value: number;
  sig: number;
}

/** With one channel left out, the band still needs this share of its weight (a quarter). */
export const SKIP_BAND_COVER = 0.25;

/**
 * One index of one spectrum. `skip` leaves that channel out: the continuum windows may then be
 * as thin as one channel, and the band needs SKIP_BAND_COVER of its weight (NaN below that: the
 * band rests on the channel left out).
 */
export function indexOf(flux: ArrayLike<number>, err: ArrayLike<number>, w: IndexWeights, skip = -1): IndexValue {
  const b = windowMean(flux, err, w.band, w.centre, skip, skip >= 0 ? SKIP_BAND_COVER : MIN_COVER);
  const thin = skip >= 0 ? 0 : MIN_COVER;
  const a1 = windowMean(flux, err, w.c1, w.centre, skip, thin);
  const a2 = windowMean(flux, err, w.c2, w.centre, skip, thin);
  const c = continuum(a1, a2, b.lam);
  const ok = c.mean > 0 && Number.isFinite(b.mean);
  const ratio = ok ? b.mean / c.mean : NaN;
  const ratioSig = ok ? Math.hypot(b.sig, ratio * c.sig) / c.mean : NaN;
  const tot = Math.hypot(b.sig, c.sig);
  const abs = w.def.kind === "abs";
  return {
    ratio,
    ratioSig,
    band: b.mean,
    bandSig: b.sig,
    cont: c.mean,
    contSig: c.sig,
    value: ok ? (abs ? 1 - ratio : ratio - 1) : NaN,
    sig: abs ? (c.mean - b.mean) / tot : (b.mean - c.mean) / tot,
  };
}
