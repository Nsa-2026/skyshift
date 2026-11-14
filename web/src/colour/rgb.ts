// False-colour composite (layer A2). Every step is a view of the data; nothing is removed.
//
// 1. Each colour is the median of the cube frames inside its window (NaN-aware). Not the mean:
//    the cube has single-frame glitches (one frame at −1,300 to −4,800σ while the other 11 are
//    normal, 14 Nov), and a mean carries them into the picture as fake dots.
// 2. Background: sigma-clipped median of the field (3σ, up to 5 passes, σ = 1.4826 × MAD),
//    subtracted per colour. The same clipped σ is that colour's noise.
// 3. No per-colour rescaling: equal surface brightness per unit frequency (MJy/sr) gives equal
//    R, G and B, so a flat spectrum in AB is white and the hue is the spectrum's slope.
// 4. Lupton et al. (2004, PASP 116, 133) stretch: one asinh on the total I = (R+G+B)/3, applied
//    to all three colours by the same factor, so bright sources keep their hue instead of
//    turning white; a colour over 1 scales all three down together.
// 5. A pixel gets colour only when its brightest colour is significant in its own band: grey
//    (its brightness only) below 3σ, full colour from 5σ, linear in between. Testing I instead
//    let one noisy band paint single pixels pure red (seen on real data, 14 Nov); a 5σ spike in
//    one of three bands happens about once in 10⁶ pixels. NaN in any colour is "no data".

export interface Stats {
  median: number;
  sigma: number;
  n: number;
}

function medianOf(a: Float64Array): number {
  if (!a.length) return NaN;
  const s = Float64Array.from(a).sort();
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

/** Sigma-clipped median and robust σ of the finite values. */
export function clippedStats(values: ArrayLike<number>, k = 3, passes = 5): Stats {
  let cur = Float64Array.from(Array.prototype.filter.call(values, Number.isFinite) as number[]);
  let median = NaN;
  let sigma = NaN;
  for (let p = 0; p < passes && cur.length; p++) {
    median = medianOf(cur);
    const dev = cur.map((v) => Math.abs(v - median));
    sigma = 1.4826 * medianOf(dev);
    if (!(sigma > 0)) break;
    const kept = cur.filter((v) => Math.abs(v - median) <= k * sigma);
    if (kept.length === cur.length) break;
    cur = kept;
  }
  return { median, sigma, n: cur.length };
}

/** Median of several planes, pixel by pixel, ignoring NaN (NaN only where every plane is NaN). */
export function medianPlanes(planes: Float32Array[]): Float32Array {
  const n = planes[0]?.length ?? 0;
  const out = new Float32Array(n);
  const vals = new Float64Array(planes.length);
  for (let i = 0; i < n; i++) {
    let c = 0;
    for (const p of planes) {
      const v = p[i];
      if (Number.isFinite(v)) vals[c++] = v;
    }
    if (!c) {
      out[i] = NaN;
      continue;
    }
    const s = vals.subarray(0, c).sort();
    const m = c >> 1;
    out[i] = c % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
  }
  return out;
}

export interface Channel {
  /** Background-subtracted values, MJy/sr. */
  values: Float32Array;
  stats: Stats;
}

export function toChannel(mean: Float32Array): Channel {
  const stats = clippedStats(mean);
  const values = new Float32Array(mean.length);
  for (let i = 0; i < mean.length; i++) values[i] = mean[i] - stats.median;
  return { values, stats };
}

export interface Composite {
  rgba: Uint8ClampedArray<ArrayBuffer>;
  /** Noise of I = (R+G+B)/3. */
  sigmaI: number;
  /** I that maps to full brightness (99.5th percentile, at least 5σ). */
  iMax: number;
}

export const GREY_BELOW_SIGMA = 3;
export const FULL_COLOUR_SIGMA = 5;

function percentile(values: number[], q: number): number {
  if (!values.length) return NaN;
  const s = values.slice().sort((a, b) => a - b);
  return s[Math.min(s.length - 1, Math.max(0, Math.floor(q * (s.length - 1))))];
}

/**
 * Lupton composite. `knee` sets where the asinh turns from linear to logarithmic, in units of
 * σ_I (1 = the noise level: faint sources show, bright ones compress).
 */
export function compose(r: Channel, g: Channel, b: Channel, knee = 1): Composite {
  const n = r.values.length;
  const sigmaI = Math.sqrt(r.stats.sigma ** 2 + g.stats.sigma ** 2 + b.stats.sigma ** 2) / 3;
  const iValues: number[] = [];
  for (let k = 0; k < n; k++) {
    const I = (r.values[k] + g.values[k] + b.values[k]) / 3;
    if (Number.isFinite(I)) iValues.push(I);
  }
  const iMax = Math.max(percentile(iValues, 0.995), 5 * sigmaI);
  const beta = Math.max(knee * sigmaI, 1e-12);
  const norm = Math.asinh(iMax / beta);
  const rgba = new Uint8ClampedArray(4 * n);
  for (let k = 0; k < n; k++) {
    const R = r.values[k];
    const G = g.values[k];
    const B = b.values[k];
    if (!Number.isFinite(R) || !Number.isFinite(G) || !Number.isFinite(B)) continue; // no data: transparent
    const I = (R + G + B) / 3;
    let out: [number, number, number] = [0, 0, 0];
    if (I > 0) {
      const f = Math.asinh(I / beta) / norm; // stretched brightness, 1 at iMax
      const s = f / I;
      let c: [number, number, number] = [Math.max(0, R * s), Math.max(0, G * s), Math.max(0, B * s)];
      const top = Math.max(...c);
      if (top > 1) c = [c[0] / top, c[1] / top, c[2] / top];
      const grey = Math.min(1, f);
      const w = colourWeight(Math.max(R / r.stats.sigma, G / g.stats.sigma, B / b.stats.sigma));
      out = [w * c[0] + (1 - w) * grey, w * c[1] + (1 - w) * grey, w * c[2] + (1 - w) * grey];
    }
    rgba[4 * k] = Math.round(out[0] * 255);
    rgba[4 * k + 1] = Math.round(out[1] * 255);
    rgba[4 * k + 2] = Math.round(out[2] * 255);
    rgba[4 * k + 3] = 255;
  }
  return { rgba, sigmaI, iMax };
}

// ---- saturation ---------------------------------------------------------------------------
// The cube leaves saturated readings out, so a bright star's core has fewer exposures (NHITS)
// than the sky around it, or none, and its colour is biased (Barnard's Star, J = 5.2, comes out
// red). The FLAGS plane doesn't mark this (checked on Barnard's Star and R Draconis, 14 Nov).
// Rule, calibrated on 4 fields (19,600 px): a plane has a deficit at a pixel when its NHITS is
// below half the median of the 7 × 7 box around it. Chance deficits on faint pixels reached at
// most 3 of the ~24 planes; saturated cores had 4–24. So: ≥ 4 deficit planes and I > 10σ_I,
// grown by one pixel to cover the core. The pixel is marked, never hidden.
export const DEFICIT_FRACTION = 0.5;
export const DEFICIT_BOX = 3; // half-width: 7 × 7
export const SATURATED_MIN_PLANES = 4;
export const SATURATED_MIN_SIGMA = 10;

/** Per pixel: how many planes have an exposure deficit against their 7 × 7 neighbourhood. */
export function deficitPlanes(hits: Int32Array[], n: number): Uint8Array {
  const count = new Uint8Array(n * n);
  const box: number[] = [];
  for (const h of hits) {
    for (let j = 0; j < n; j++) {
      for (let i = 0; i < n; i++) {
        box.length = 0;
        for (let dj = -DEFICIT_BOX; dj <= DEFICIT_BOX; dj++) {
          const jj = j + dj;
          if (jj < 0 || jj >= n) continue;
          for (let di = -DEFICIT_BOX; di <= DEFICIT_BOX; di++) {
            const ii = i + di;
            if (ii >= 0 && ii < n) box.push(h[jj * n + ii]);
          }
        }
        box.sort((a, b) => a - b);
        const m = box[box.length >> 1];
        const k = j * n + i;
        if (m > 0 && h[k] < DEFICIT_FRACTION * m && count[k] < 255) count[k]++;
      }
    }
  }
  return count;
}

/** Saturated pixels (1) from the deficit counts and brightness, grown by one pixel. */
export function saturatedMask(deficits: Uint8Array, r: Channel, g: Channel, b: Channel, sigmaI: number, n: number): Uint8Array {
  const core = new Uint8Array(n * n);
  for (let k = 0; k < n * n; k++) {
    const I = (r.values[k] + g.values[k] + b.values[k]) / 3;
    if (deficits[k] >= SATURATED_MIN_PLANES && I > SATURATED_MIN_SIGMA * sigmaI) core[k] = 1;
  }
  const out = new Uint8Array(n * n);
  for (let j = 0; j < n; j++) {
    for (let i = 0; i < n; i++) {
      if (!core[j * n + i]) continue;
      for (let dj = -1; dj <= 1; dj++) {
        for (let di = -1; di <= 1; di++) {
          const jj = j + dj;
          const ii = i + di;
          if (jj >= 0 && jj < n && ii >= 0 && ii < n) out[jj * n + ii] = 1;
        }
      }
    }
  }
  return out;
}

/** 0 below 3σ (grey), 1 from 5σ (full colour), linear in between. */
export function colourWeight(snr: number): number {
  return Math.min(1, Math.max(0, (snr - GREY_BELOW_SIGMA) / (FULL_COLOUR_SIGMA - GREY_BELOW_SIGMA)));
}
