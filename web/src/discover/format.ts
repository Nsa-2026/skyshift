// Numbers with units for Discover (DESIGN.md "Numbers": units always, tabular figures).
import type { Range, RangeSpec } from "./model";

const nf = (d: number) => new Intl.NumberFormat("en-GB", { minimumFractionDigits: d, maximumFractionDigits: d });

/** A plain number with a sensible number of digits for its size. */
export function num(v: number, digits?: number): string {
  if (!Number.isFinite(v)) return "—";
  const d = digits ?? (Math.abs(v) >= 100 ? 0 : Math.abs(v) >= 10 ? 1 : 2);
  return nf(d).format(v);
}

/** Proper motion in mas/yr shown as ″/yr: 0.50″/yr, 12.3″/yr, 258″/yr. */
export function arcsecPerYr(masYr: number): string {
  const a = masYr / 1000;
  return `${num(a, a >= 100 ? 0 : a >= 10 ? 1 : 2)}″/yr`;
}

export function arcsecPerHour(v: number): string {
  return `${num(v, v >= 100 ? 0 : 1)}″/h`;
}

export function flux(v: number): string {
  return `${num(v, v >= 100 ? 0 : 1)} µJy`;
}

/** Values on a range input, in the range's own unit and digits. */
export function rangeValue(spec: Pick<RangeSpec, "digits" | "log">, v: number): string {
  if (!Number.isFinite(v)) return "";
  const d = spec.log ? (v >= 100 ? 0 : v >= 10 ? Math.min(spec.digits, 1) : Math.max(spec.digits, 1)) : spec.digits;
  return String(+v.toFixed(d));
}

/** "0.15–1", "≥ 60″", "≤ 500 µJy" for an active range. */
export function rangeText(spec: Pick<RangeSpec, "digits" | "log" | "unit">, r: Range): string {
  const u = spec.unit ? (spec.unit === "″" || spec.unit === "°" || spec.unit === "σ" ? spec.unit : ` ${spec.unit}`) : "";
  const a = r.min != null ? num(r.min, decimalsFor(spec, r.min)) : null;
  const b = r.max != null ? num(r.max, decimalsFor(spec, r.max)) : null;
  if (a != null && b != null) return `${a}–${b}${u}`;
  if (a != null) return `≥ ${a}${u}`;
  return `≤ ${b}${u}`;
}

function decimalsFor(spec: Pick<RangeSpec, "digits" | "log">, v: number): number {
  const s = rangeValue(spec, v);
  const i = s.indexOf(".");
  return i < 0 ? 0 : s.length - i - 1;
}

/** Slider position (0–1000) ↔ value, linear or logarithmic over a domain. */
export const SLIDER_MAX = 1000;
export function toSlider(v: number, dom: [number, number], log: boolean): number {
  const [a, b] = dom;
  const t = log ? (Math.log10(Math.max(v, a)) - Math.log10(a)) / (Math.log10(b) - Math.log10(a)) : (v - a) / (b - a);
  return Math.round(Math.min(1, Math.max(0, t)) * SLIDER_MAX);
}
export function fromSlider(pos: number, dom: [number, number], log: boolean): number {
  const [a, b] = dom;
  const t = pos / SLIDER_MAX;
  return log ? 10 ** (Math.log10(a) + t * (Math.log10(b) - Math.log10(a))) : a + t * (b - a);
}

/** Round a slider value to something a person would type. */
export function roundNice(v: number, spec: Pick<RangeSpec, "digits" | "log">): number {
  if (!spec.log) return +v.toFixed(spec.digits);
  const p = 10 ** (Math.floor(Math.log10(Math.abs(v) || 1)) - 1);
  return Math.round(v / p) * p;
}

export function plural(n: number, one: string, many: string): string {
  return `${n.toLocaleString("en-GB")} ${n === 1 ? one : many}`;
}
