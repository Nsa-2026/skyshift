// The epoch timeline's time axis. Months: a plain linear MJD axis (visits sit six months apart).
// Hours: exposures 2 minutes apart, sessions hours apart and revisits days apart all on one strip,
// so every gap is drawn log-compressed and gaps over an hour are labelled with their real length.
import { mjdToDate } from "../app/astro";

export type AxisKind = "linear" | "gaps";

export interface AxisTick {
  x: number;
  label: string;
  major: boolean;
}

export interface AxisGap {
  /** Middle of the gap on the strip, 0–1. */
  x: number;
  days: number;
  label: string;
}

export interface Axis {
  kind: AxisKind;
  /** MJD → position on the strip, 0–1 (monotonic; clamps outside the range). */
  x(mjd: number): number;
  /** Position 0–1 → MJD (inverse of x). */
  mjd(x: number): number;
  ticks: AxisTick[];
  gaps: AxisGap[];
}

const PAD = 0.035;
/** Gaps shorter than this keep their linear look; 1.44 min ≈ one exposure step. */
const GAP_UNIT_DAYS = 0.001;
/** Gaps longer than this get a label with their real length. */
const LABEL_GAP_DAYS = 1 / 24;

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function formatGap(days: number): string {
  const min = days * 1440;
  if (min < 90) return `${Math.round(min)} min`;
  const h = days * 24;
  if (h < 36) return `${h < 10 ? h.toFixed(1).replace(/\.0$/, "") : Math.round(h)} h`;
  if (days < 75) return `${Math.round(days)} d`;
  return `${Math.round(days / 30.44)} mo`;
}

/** Piecewise-linear interpolation through knots (ascending in both). Clamps at the ends. */
function interp(knotsA: number[], knotsB: number[], a: number): number {
  const n = knotsA.length;
  if (a <= knotsA[0]) return knotsB[0];
  if (a >= knotsA[n - 1]) return knotsB[n - 1];
  let lo = 0;
  let hi = n - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (knotsA[mid] <= a) lo = mid;
    else hi = mid;
  }
  const span = knotsA[hi] - knotsA[lo];
  const f = span > 0 ? (a - knotsA[lo]) / span : 0;
  return knotsB[lo] + f * (knotsB[hi] - knotsB[lo]);
}

/**
 * Build the axis for a set of epoch times. `spans` (optional) widens a linear axis to cover
 * visit start/end so the visit bars fit.
 */
export function buildAxis(mjds: number[], kind: AxisKind, spans: [number, number][] = [], maxLabels = 6): Axis {
  const t = [...new Set(mjds.filter(Number.isFinite))].sort((a, b) => a - b);
  if (t.length === 0) {
    return { kind, x: () => 0.5, mjd: () => NaN, ticks: [], gaps: [] };
  }

  if (kind === "linear") {
    let a = Math.min(t[0], ...spans.map((s) => s[0]));
    let b = Math.max(t[t.length - 1], ...spans.map((s) => s[1]));
    const pad = Math.max((b - a) * 0.04, 8);
    a -= pad;
    b += pad;
    const x = (m: number) => Math.min(1, Math.max(0, (m - a) / (b - a)));
    const mjd = (p: number) => a + p * (b - a);
    return { kind, x, mjd, ticks: monthTicks(a, b, x, maxLabels), gaps: [] };
  }

  // gaps: knots at each epoch time; each gap's width grows with log(1 + gap / unit).
  if (t.length === 1) {
    return { kind, x: () => 0.5, mjd: () => t[0], ticks: dayTicks(t, () => 0.5), gaps: [] };
  }
  const v = [0];
  for (let i = 1; i < t.length; i++) v.push(v[i - 1] + Math.log10(1 + (t[i] - t[i - 1]) / GAP_UNIT_DAYS));
  const total = v[v.length - 1];
  const pos = v.map((u) => PAD + (u / total) * (1 - 2 * PAD));
  const x = (m: number) => interp(t, pos, m);
  const mjd = (p: number) => interp(pos, t, p);
  const gaps: AxisGap[] = [];
  for (let i = 1; i < t.length; i++) {
    const d = t[i] - t[i - 1];
    if (d >= LABEL_GAP_DAYS) gaps.push({ x: (pos[i] + pos[i - 1]) / 2, days: d, label: formatGap(d) });
  }
  return { kind, x, mjd, ticks: dayTicks(t, x), gaps };
}

/** Month ticks; labels every 1, 2, 3, 6 or 12 months so at most maxLabels show. */
function monthTicks(a: number, b: number, x: (m: number) => number, maxLabels: number): AxisTick[] {
  const start = mjdToDate(a);
  let y = start.getUTCFullYear();
  let mo = start.getUTCMonth() + 1;
  if (mo > 11) {
    mo = 0;
    y++;
  }
  const months: { y: number; m: number; mjd: number }[] = [];
  for (;;) {
    const mjd = Date.UTC(y, mo, 1) / 86400000 + 40587;
    if (mjd > b) break;
    months.push({ y, m: mo, mjd });
    mo++;
    if (mo > 11) {
      mo = 0;
      y++;
    }
  }
  const step = [1, 2, 3, 6, 12].find((s) => months.filter((m) => m.m % s === 0).length <= maxLabels) ?? 12;
  let lastYear = -1;
  return months.map((m) => {
    const major = m.m % step === 0;
    let label = "";
    if (major) {
      label = m.y !== lastYear || m.m === 0 ? `${MONTHS[m.m]} ${m.y}` : MONTHS[m.m];
      lastYear = m.y;
    }
    return { x: x(m.mjd), label, major };
  });
}

/** One labelled tick at the first epoch of each UTC day. */
function dayTicks(t: number[], x: (m: number) => number): AxisTick[] {
  const out: AxisTick[] = [];
  let last = "";
  for (const m of t) {
    const d = mjdToDate(m);
    const key = `${d.getUTCFullYear()}-${d.getUTCMonth()}-${d.getUTCDate()}`;
    if (key === last) continue;
    last = key;
    out.push({ x: x(m), label: `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`, major: true });
  }
  return out;
}

/** Index of the epoch whose position is nearest to p (0–1). */
export function nearestIndex(positions: number[], p: number): number {
  let best = 0;
  let bestD = Infinity;
  positions.forEach((q, i) => {
    const d = Math.abs(q - p);
    if (d < bestD) {
      bestD = d;
      best = i;
    }
  });
  return best;
}
