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
