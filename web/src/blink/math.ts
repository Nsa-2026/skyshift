// Pure pixel maths for the blink player: robust limits, stretch, subtract, add.
// Frame values are MJy/sr (Float32Array) with NaN where there is no data (raw 0).

export type StretchKind = "linear" | "asinh";

/** Up to `max` finite values sampled evenly from several frames (stride sampling, no randomness). */
export function sampleFinite(frames: ArrayLike<number>[], max = 200_000): Float32Array {
  const total = frames.reduce((n, f) => n + f.length, 0);
  if (total === 0) return new Float32Array(0);
  const stride = Math.max(1, Math.floor(total / max));
  const out = new Float32Array(Math.min(total, max + frames.length));
  let n = 0;
  for (const f of frames) {
    for (let i = 0; i < f.length && n < out.length; i += stride) {
      const v = f[i];
      if (Number.isFinite(v)) out[n++] = v;
    }
  }
  return out.subarray(0, n);
}

/** Percentile (0–100) of an ascending-sorted array, linear interpolation between ranks. */
export function percentileSorted(sorted: ArrayLike<number>, p: number): number {
  const n = sorted.length;
  if (n === 0) return NaN;
  if (n === 1) return sorted[0];
  const r = (Math.min(100, Math.max(0, p)) / 100) * (n - 1);
  const i = Math.floor(r);
  const f = r - i;
  return i + 1 < n ? sorted[i] * (1 - f) + sorted[i + 1] * f : sorted[i];
}

export function percentiles(values: ArrayLike<number>[], ps: number[], max?: number): number[] {
  const s = sampleFinite(values, max).sort();
  return ps.map((p) => percentileSorted(s, p));
}

/** Median of the finite values of one frame (the sky level, zodiacal light included). */
export function median(frame: ArrayLike<number>): number {
  const [m] = percentiles([frame], [50], 50_000);
  return Number.isFinite(m) ? m : 0;
}

/**
 * Contrast slider (0–1) → percentile pair. 0 keeps almost the whole range (0.5–99.95 %);
 * 1 clips hard (10–90 %) to pull faint dots out of the noise.
 */
export function contrastToPercentiles(c: number): [number, number] {
  const k = Math.min(1, Math.max(0, c));
  return [0.5 + 9.5 * k, 100 - 0.05 * 10 ** (2.3 * k)];
}

export interface Limits {
  lo: number;
  hi: number;
}

/**
 * Display limits shared by every frame of one detector, so a real brightness change between
 * epochs stays visible instead of being normalised away.
 */
export function robustLimits(frames: ArrayLike<number>[], contrast: number): Limits {
  const [pLo, pHi] = contrastToPercentiles(contrast);
  let [lo, hi] = percentiles(frames, [pLo, pHi]);
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return { lo: 0, hi: 1 };
  if (hi <= lo) hi = lo + (Math.abs(lo) || 1) * 1e-3;
  return { lo, hi };
}

/** Symmetric limits ±L for difference images: L is the high percentile of |difference|. */
export function symmetricLimits(diffs: ArrayLike<number>[], contrast: number): Limits {
  const [, pHi] = contrastToPercentiles(contrast);
  const abs = diffs.map((d) => {
    const a = new Float32Array(d.length);
    for (let i = 0; i < d.length; i++) a[i] = Math.abs(d[i]);
    return a;
  });
  let [L] = percentiles(abs, [pHi]);
  if (!Number.isFinite(L) || L <= 0) L = 1e-6;
  return { lo: -L, hi: L };
}

const ASINH_BETA = 10;
const ASINH_NORM = Math.asinh(ASINH_BETA);
/** Gentler for differences: a strong asinh turns the noise floor into confetti. */
const ASINH_BETA_SIGNED = 3;
const ASINH_NORM_SIGNED = Math.asinh(ASINH_BETA_SIGNED);

/** Map a value to 0–1 between lo and hi. NaN stays NaN. */
export function stretch(v: number, lim: Limits, kind: StretchKind): number {
  if (Number.isNaN(v)) return NaN;
  const t = Math.min(1, Math.max(0, (v - lim.lo) / (lim.hi - lim.lo)));
  return kind === "asinh" ? Math.asinh(t * ASINH_BETA) / ASINH_NORM : t;
}

/** Diverging stretch for differences: −1 … 0 … +1, zero difference maps to 0. */
export function stretchSigned(v: number, lim: Limits, kind: StretchKind): number {
  if (Number.isNaN(v)) return NaN;
  const L = Math.max(Math.abs(lim.lo), Math.abs(lim.hi));
  const t = Math.min(1, Math.abs(v) / L);
  const y = kind === "asinh" ? Math.asinh(t * ASINH_BETA_SIGNED) / ASINH_NORM_SIGNED : t;
  return v < 0 ? -y : y;
}

/** Subtract a constant (the sky level) from a frame. */
export function offset(frame: Float32Array, by: number): Float32Array {
  const out = new Float32Array(frame.length);
  for (let i = 0; i < frame.length; i++) out[i] = frame[i] - by;
  return out;
}

/** a − b per pixel; NaN where either has no data. */
export function subtract(a: Float32Array, b: Float32Array): Float32Array {
  const out = new Float32Array(a.length);
  for (let i = 0; i < a.length; i++) out[i] = a[i] - b[i];
  return out;
}

/** Per-pixel mean of the frames, skipping no-data pixels; NaN only where every frame is empty. */
export function mean(frames: Float32Array[]): Float32Array {
  const n = frames[0]?.length ?? 0;
  const sum = new Float64Array(n);
  const count = new Uint16Array(n);
  for (const f of frames) {
    for (let i = 0; i < n; i++) {
      const v = f[i];
      if (!Number.isNaN(v)) {
        sum[i] += v;
        count[i]++;
      }
    }
  }
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) out[i] = count[i] ? sum[i] / count[i] : NaN;
  return out;
}

/** Nice scale-bar length (arcsec) close to `targetArcsec`. */
export function niceArcsec(targetArcsec: number): { arcsec: number; label: string } {
  const steps = [1, 2, 5, 10, 15, 20, 30, 60, 120, 300, 600, 900, 1200, 1800, 3600];
  let best = steps[0];
  for (const s of steps) if (s <= targetArcsec) best = s;
  const label = best >= 60 ? `${best / 60}′` : `${best}″`;
  return { arcsec: best, label };
}

// ---- noise: PSF smoothing and σ-scaled differences ------------------------------------------

/** Gaussian FWHM → sigma. */
export const FWHM_TO_SIGMA = 1 / (2 * Math.sqrt(2 * Math.LN2));

/** Sigma in pixels of a Gaussian matched to the PSF (FWHM in arcsec) at this pixel scale. */
export function psfSigmaPx(fwhmArcsec: number, pixelArcsec: number): number {
  return (fwhmArcsec * FWHM_TO_SIGMA) / pixelArcsec;
}

/** Normalised 1-D Gaussian, radius ceil(3σ). */
export function gaussianKernel(sigma: number): Float32Array {
  const r = Math.max(1, Math.ceil(3 * sigma));
  const k = new Float32Array(2 * r + 1);
  let s = 0;
  for (let i = -r; i <= r; i++) s += k[i + r] = Math.exp((-i * i) / (2 * sigma * sigma));
  for (let i = 0; i < k.length; i++) k[i] /= s;
  return k;
}

/**
 * Gaussian smoothing that ignores no-data pixels (normalised convolution): each output is the
 * weighted mean of its finite neighbours. No-data pixels stay no data. Separable, two passes.
 */
export function smooth(frame: Float32Array, nx: number, ny: number, sigma: number): Float32Array {
  if (!(sigma > 0.05)) return frame.slice();
  const k = gaussianKernel(sigma);
  const r = (k.length - 1) / 2;
  const n = nx * ny;
  // Pass 1 (rows): weighted value sum and weight sum. Pass 2 (columns) divides at the end.
  const sv = new Float32Array(n);
  const sw = new Float32Array(n);
  for (let y = 0; y < ny; y++) {
    const row = y * nx;
    for (let x = 0; x < nx; x++) {
      let a = 0;
      let w = 0;
      const lo = Math.max(0, x - r);
      const hi = Math.min(nx - 1, x + r);
      for (let j = lo; j <= hi; j++) {
        const v = frame[row + j];
        if (v === v) {
          const kk = k[j - x + r];
          a += kk * v;
          w += kk;
        }
      }
      sv[row + x] = a;
      sw[row + x] = w;
    }
  }
  const out = new Float32Array(n);
  for (let y = 0; y < ny; y++) {
    const lo = Math.max(0, y - r);
    const hi = Math.min(ny - 1, y + r);
    for (let x = 0; x < nx; x++) {
      const i = y * nx + x;
      if (frame[i] !== frame[i]) {
        out[i] = NaN;
        continue;
      }
      let a = 0;
      let w = 0;
      for (let j = lo; j <= hi; j++) {
        const kk = k[j - y + r];
        a += kk * sv[j * nx + x];
        w += kk * sw[j * nx + x];
      }
      out[i] = w > 0 ? a / w : NaN;
    }
  }
  return out;
}

/** Robust noise of the finite values: 1.4826 × median absolute deviation (a Gaussian's σ). */
export function robustSigma(values: ArrayLike<number>[], max = 200_000): number {
  const s = sampleFinite(values, max).sort();
  if (!s.length) return NaN;
  const med = percentileSorted(s, 50);
  const dev = new Float32Array(s.length);
  for (let i = 0; i < s.length; i++) dev[i] = Math.abs(s[i] - med);
  return 1.4826 * percentileSorted(dev.sort(), 50);
}

/**
 * Contrast slider (0–1) → the difference image's colour range in σ. 0 shows ±10σ (only strong
 * changes), the default 0.45 about ±5σ, 1 shows ±2σ (the noise starts to show).
 */
export function contrastToSigmas(c: number): number {
  const k = Math.min(1, Math.max(0, c));
  const n = 10 * 0.2 ** k;
  return n >= 4 ? Math.round(n) : Math.round(n * 2) / 2;
}

/** Differences smaller than this many sigma are noise: they stay glass. */
export const NOISE_FLOOR_SIGMA = 1.5;

/**
 * A difference in noise units → −1 … +1 for the diverging colour map. Below the noise floor
 * (1.5σ) it stays at 0 (glass); colour grows linearly to full at nσ. NaN stays NaN.
 */
export function sigmaStretch(v: number, sigma: number, n: number): number {
  if (Number.isNaN(v)) return NaN;
  const z = Math.abs(v) / sigma;
  const f = Math.min(NOISE_FLOOR_SIGMA, n / 2);
  const y = Math.min(1, Math.max(0, (z - f) / Math.max(1e-6, n - f)));
  return v < 0 ? -y : y;
}

/** ±N σ limits for difference images; falls back to percentile limits when σ is unusable. */
export function sigmaLimits(diffs: ArrayLike<number>[], contrast: number): Limits & { sigma: number; n: number } {
  const sigma = robustSigma(diffs);
  const n = contrastToSigmas(contrast);
  if (!(sigma > 0)) return { ...symmetricLimits(diffs, contrast), sigma: NaN, n };
  return { lo: -n * sigma, hi: n * sigma, sigma, n };
}

// ---- shift-and-add ---------------------------------------------------------------------------

/**
 * Resample a frame so that out(x, y) = in(x + dx, y + dy), bilinear between pixel centres.
 * A pixel is no data if it falls outside the frame or any neighbour it needs is no data.
 */
export function shiftBilinear(frame: Float32Array, nx: number, ny: number, dx: number, dy: number): Float32Array {
  const out = new Float32Array(nx * ny);
  const ix = Math.floor(dx);
  const iy = Math.floor(dy);
  const fx = dx - ix;
  const fy = dy - iy;
  const W = [(1 - fx) * (1 - fy), fx * (1 - fy), (1 - fx) * fy, fx * fy];
  const OX = [0, 1, 0, 1];
  const OY = [0, 0, 1, 1];
  for (let y = 0; y < ny; y++) {
    for (let x = 0; x < nx; x++) {
      let s = 0;
      let bad = false;
      for (let q = 0; q < 4 && !bad; q++) {
        const w = W[q];
        if (w <= 1e-9) continue;
        const xx = x + ix + OX[q];
        const yy = y + iy + OY[q];
        if (xx < 0 || yy < 0 || xx >= nx || yy >= ny) bad = true;
        else {
          const v = frame[yy * nx + xx];
          if (v !== v) bad = true;
          else s += w * v;
        }
      }
      out[y * nx + x] = bad ? NaN : s;
    }
  }
  return out;
}

// ---- holes: saturated cores versus missing data ----------------------------------------------

/**
 * Classify the no-data pixels of a frame. A small hole that sits on a peak (its rim far brighter
 * than the sky, and clearly brighter than the ring 3 pixels further out) is a saturated core: the
 * pipeline masks OVERFLOW and NONLINEAR pixels. It gets 1, so it can be drawn as solid ink, a
 * burnt plate. Every other no-data pixel stays 0 (drawn as missing data), including a lone masked
 * pixel in a bright star's wings. `sky` and `sigma` are the frame's sky level and noise; `k` is
 * the rim threshold in sigma.
 */
export function burntCores(
  frame: Float32Array,
  nx: number,
  ny: number,
  sky: number,
  sigma: number,
  k = 8,
  maxArea = Math.max(16, Math.round(0.15 * nx * ny)),
): Uint8Array {
  const n = nx * ny;
  const out = new Uint8Array(n);
  if (!(sigma > 0)) return out;
  const seen = new Uint8Array(n);
  const stack: number[] = [];
  const comp: number[] = [];
  const rim: number[] = [];
  const limit = sky + k * sigma;
  for (let s = 0; s < n; s++) {
    if (seen[s] || frame[s] === frame[s]) continue;
    // Flood one 4-connected hole; its rim is every finite 8-neighbour.
    comp.length = 0;
    rim.length = 0;
    stack.push(s);
    seen[s] = 1;
    while (stack.length) {
      const i = stack.pop()!;
      comp.push(i);
      const x = i % nx;
      const y = (i - x) / nx;
      for (let dy = -1; dy <= 1; dy++) {
        const yy = y + dy;
        if (yy < 0 || yy >= ny) continue;
        for (let dx = -1; dx <= 1; dx++) {
          const xx = x + dx;
          if ((dx === 0 && dy === 0) || xx < 0 || xx >= nx) continue;
          const j = yy * nx + xx;
          const v = frame[j];
          if (v === v) rim.push(v);
          else if (!seen[j] && (dx === 0 || dy === 0)) {
            seen[j] = 1;
            stack.push(j);
          }
        }
      }
    }
    if (comp.length > maxArea || rim.length === 0) continue;
    rim.sort((a, b) => a - b);
    const rimMed = rim[rim.length >> 1];
    if (rimMed <= limit) continue;
    // The ring 3 pixels outside the hole's bounding box: a peak falls off toward it.
    let x0 = nx;
    let x1 = 0;
    let y0 = ny;
    let y1 = 0;
    for (const i of comp) {
      const x = i % nx;
      const y = (i - x) / nx;
      x0 = Math.min(x0, x);
      x1 = Math.max(x1, x);
      y0 = Math.min(y0, y);
      y1 = Math.max(y1, y);
    }
    const outer: number[] = [];
    const take = (x: number, y: number) => {
      if (x < 0 || y < 0 || x >= nx || y >= ny) return;
      const v = frame[y * nx + x];
      if (v === v) outer.push(v);
    };
    for (let x = x0 - 3; x <= x1 + 3; x++) {
      take(x, y0 - 3);
      take(x, y1 + 3);
    }
    for (let y = y0 - 2; y <= y1 + 2; y++) {
      take(x0 - 3, y);
      take(x1 + 3, y);
    }
    outer.sort((a, b) => a - b);
    const outerMed = outer.length ? outer[outer.length >> 1] : sky;
    // A core of a few pixels or more is a saturated star; a lone pixel must also sit on a peak.
    if (comp.length >= 3 || rimMed - sky > 1.6 * (outerMed - sky)) for (const i of comp) out[i] = 1;
  }
  return out;
}
