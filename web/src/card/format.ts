// Pure helpers for the object card: numbers, wording and axis maths. No DOM here, so tests are cheap.
import type { Det, FramesResponse, GaiaStar, Measure, MeasurementSource, ObjectResponse, SpectrumPoint } from "../api/types";
import { sepArcsec } from "../app/astro";

export const LY_PER_PC = 3.26156;
/** Largest parallax shift between two visits six months apart for our targets (Barnard's Star). */
export const PARALLAX_BETWEEN_VISITS_ARCSEC = 1.1;

/** Gaia distance: 1000 / parallax(mas) in parsecs, or null when Gaia has no usable parallax. */
export function distancePc(parallaxMas: number | null | undefined): number | null {
  if (parallaxMas == null || !Number.isFinite(parallaxMas) || parallaxMas <= 0) return null;
  return 1000 / parallaxMas;
}

export function pcToLy(pc: number): number {
  return pc * LY_PER_PC;
}

/** Few significant figures for a distance: 1.83, 18.3, 183, 1,830. */
export function formatDistance(x: number): string {
  if (x < 10) return x.toFixed(2);
  if (x < 100) return x.toFixed(1);
  return Math.round(x).toLocaleString("en-US");
}

/** (measured − predicted) / σ, signed. Null without a prediction or a usable σ. */
export function sigmaDiff(m: Measure): number | null {
  if (m.predicted_arcsec == null || !(m.sigma_arcsec > 0)) return null;
  return (m.shift_arcsec - m.predicted_arcsec) / m.sigma_arcsec;
}

/** 3σ: the slowest shift the measurement can see. */
export function detectionLimit(m: Measure): number {
  return 3 * m.sigma_arcsec;
}

/** Only show explicit, internally consistent provenance; never infer it from player frames. */
export function measurementSource(m: Measure): MeasurementSource | null {
  const s = m.source;
  if (!s || !Number.isInteger(s.det) || s.det < 1 || s.det > 6) return null;
  const pair = (a: unknown): a is number[] => Array.isArray(a) && a.length === 2 &&
    a.every((x) => typeof x === "number" && Number.isFinite(x));
  if (!pair(s.visits) || s.visits.some((v) => !Number.isInteger(v) || v < 1) || s.visits[1] <= s.visits[0] ||
      !pair(s.mjd) || s.mjd[1] <= s.mjd[0] ||
      !pair(s.n_exposures) || s.n_exposures.some((n) => !Number.isInteger(n) || n < 1) ||
      !Number.isFinite(s.fov) || s.fov <= 0 || !Number.isFinite(s.pixel_arcsec) || s.pixel_arcsec <= 0 ||
      !Array.isArray(s.core_filled) || s.core_filled.length !== 2 ||
      !s.core_filled.every((v) => typeof v === "boolean") ||
      Math.abs((s.mjd[1] - s.mjd[0]) / 365.25 - m.dt_yr) > 0.0001) return null;
  return s;
}

export function isDetectedMotion(m: Measure): boolean {
  return m.shift_arcsec >= detectionLimit(m);
}

export type Agreement = "agrees" | "marginal" | "disagrees";
export function agreement(nSigma: number): Agreement {
  const a = Math.abs(nSigma);
  if (a < 2) return "agrees";
  if (a < 3) return "marginal";
  return "disagrees";
}

/** Arcsec with two decimals and a double prime: 10.83″. */
export function arcsec(x: number, digits = 2): string {
  return `${x.toFixed(digits)}″`;
}

/** Compass word for a position angle (east of north). */
export function paWord(pa: number): string {
  const names = ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"];
  const i = Math.round((((pa % 360) + 360) % 360) / 45) % 8;
  return names[i];
}

/** Position angle of a proper motion (east of north), degrees, or null without one. */
export function pmPa(g: GaiaStar | null): number | null {
  if (!g || g.pmra == null || g.pmdec == null || (g.pmra === 0 && g.pmdec === 0)) return null;
  return ((Math.atan2(g.pmra, g.pmdec) * 180) / Math.PI + 360) % 360;
}

/** Screen direction for a position angle with north up and east left: (dx, dy) with y down. */
export function paVector(pa: number): { dx: number; dy: number } {
  const r = (pa * Math.PI) / 180;
  return { dx: -Math.sin(r), dy: -Math.cos(r) };
}

// ---------------------------------------------------------------------------------------------
// Spectrum

export interface Saturation {
  dets: Det[];
  n: number;
  total: number;
}

export function saturation(spectrum: SpectrumPoint[]): Saturation | null {
  const sat = spectrum.filter((p) => p.saturated);
  if (!sat.length) return null;
  const dets = [...new Set(sat.map((p) => p.det))].sort((a, b) => a - b) as Det[];
  return { dets, n: sat.length, total: spectrum.length };
}

/** "D1, D3 and D6" */
export function listDets(dets: number[]): string {
  const names = dets.map((d) => `D${d}`);
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/** A point below 2σ is not a detection: it is drawn as an upper limit at flux + 2σ (floored at 2σ). */
export const DETECT_SIGMA = 2;
export function isUpperLimit(p: Pick<SpectrumPoint, "flux_ujy" | "err_ujy">): boolean {
  return !(p.flux_ujy > DETECT_SIGMA * p.err_ujy) || p.flux_ujy <= 0;
}
export function upperLimitValue(p: Pick<SpectrumPoint, "flux_ujy" | "err_ujy">): number {
  return Math.max(p.flux_ujy, 0) + DETECT_SIGMA * p.err_ujy;
}

/** Distinct visit numbers in the spectrum, in order. */
export function visitsOf(spectrum: SpectrumPoint[]): number[] {
  return [...new Set(spectrum.map((p) => p.visit))].sort((a, b) => a - b);
}

/** Median of a list (NaN for an empty list). */
export function median(xs: number[]): number {
  if (!xs.length) return NaN;
  const s = [...xs].sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

/** Log-axis domain and ticks. Decades always; 2 and 5 inside when the range spans under ~2 decades. */
export function logTicks(lo: number, hi: number): { min: number; max: number; ticks: number[] } {
  if (!(lo > 0) || !(hi > 0)) return { min: 1, max: 10, ticks: [1, 10] };
  if (hi < lo) [lo, hi] = [hi, lo];
  if (hi / lo < 1.5) {
    lo /= 1.25;
    hi *= 1.25;
  }
  const d0 = Math.floor(Math.log10(lo));
  const d1 = Math.ceil(Math.log10(hi));
  const span = Math.log10(hi) - Math.log10(lo);
  const mults = span < 1.3 ? [1, 2, 5] : span < 2.3 ? [1, 3] : [1];
  const ticks: number[] = [];
  for (let d = d0; d <= d1; d++) {
    for (const m of mults) {
      const v = m * 10 ** d;
      if (v >= lo * 0.999 && v <= hi * 1.001) ticks.push(Number(v.toPrecision(3)));
    }
  }
  // Too few labels inside the data range: widen to the enclosing decades.
  if (ticks.length < 2) {
    const min = 10 ** Math.floor(Math.log10(lo));
    const max = 10 ** Math.ceil(Math.log10(hi));
    return { min, max, ticks: max / min > 10 ? decadesBetween(min, max) : [min, max] };
  }
  return { min: lo, max: hi, ticks };
}

function decadesBetween(min: number, max: number): number[] {
  const out: number[] = [];
  for (let v = min; v <= max * 1.001; v *= 10) out.push(Number(v.toPrecision(3)));
  return out;
}

/** Nice linear ticks (1, 2, 5 steps). */
export function linearTicks(lo: number, hi: number, target = 4): { min: number; max: number; ticks: number[] } {
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return { min: 0, max: 1, ticks: [0, 1] };
  if (hi === lo) {
    const pad = Math.abs(hi) * 0.1 || 1;
    lo -= pad;
    hi += pad;
  }
  const raw = (hi - lo) / target;
  const p = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * p).find((s) => s >= raw) ?? 10 * p;
  const min = Math.floor(lo / step) * step;
  const max = Math.ceil(hi / step) * step;
  const ticks: number[] = [];
  for (let v = min; v <= max + step / 2; v += step) ticks.push(Number(v.toPrecision(6)));
  return { min, max, ticks };
}

/** Compact µJy for axes: 300, 1k, 30k, 1M. */
export function compactUjy(v: number): string {
  const a = Math.abs(v);
  const f = (x: number, s: string) => `${Number(x.toPrecision(3))}${s}`;
  if (a >= 1e6) return f(v / 1e6, "M");
  if (a >= 1e3) return f(v / 1e3, "k");
  return f(v, "");
}

/** Full µJy for readouts: 127,811. */
export function fullUjy(v: number): string {
  return Math.round(v).toLocaleString("en-US");
}

// ---------------------------------------------------------------------------------------------
// Visits and the reason a measure is missing

/** Number of distinct visits in frames, or null when the frames don't count visits (hours clock, other spot). */
export function visitCountFromFrames(frames: FramesResponse | null, near: { ra: number; dec: number }, fovArcmin: number): number | null {
  if (!frames || frames.clock !== "months") return null;
  if (sepArcsec(frames.center, near) > fovArcmin * 60) return null; // stale frames from the last view
  // Epochs repeat per detector (d2-v1, d5-v1, …): count visits within each detector, keep the most.
  const perDet = new Map<number, number>();
  for (const e of frames.epochs) if (e.kind === "visit") perDet.set(e.det, (perDet.get(e.det) ?? 0) + 1);
  return perDet.size ? Math.max(...perDet.values()) : 0;
}

export function noMeasureReason(opts: {
  object: ObjectResponse | null;
  kind: string | null;
  visits: number | null;
}): string {
  const { object, kind, visits } = opts;
  if (kind && /asteroid|comet/i.test(kind)) {
    return "It moves in hours, not months: the frames follow it along its known orbit, so there is no shift between visits to measure. Blink the exposures and watch the stars stream past it.";
  }
  const spectrumVisits = object ? visitsOf(object.spectrum).length : 0;
  const n = visits ?? (spectrumVisits || null);
  if (n != null && n < 2) {
    return `SPHEREx has seen this spot in ${n === 0 ? "no visits" : "one visit"} so far. A shift needs two visits, about six months apart.`;
  }
  return "Nothing has been measured at this spot yet. Blink the visits and flag anything that steps.";
}
