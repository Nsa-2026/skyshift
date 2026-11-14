// Spectrum chart: flux (µJy, log) against wavelength (µm, 0.70–5.05), inline SVG on glass.
// Colour = detector (redundant with x position and the D1–D6 strip, so never the only channel)
// or visit (graphite light → ink, plus shape).
import type { SpectrumPoint } from "../api/types";
import { DETECTORS, formatMjd } from "../app/astro";
import { compactUjy, isUpperLimit, logTicks, median, upperLimitValue, visitsOf } from "./format";
import { caret, esc, marker, r1, visitColour, visitShape } from "./svg";

export type SpectrumMode = "det" | "visit";

export const UM_MIN = 0.7;
export const UM_MAX = 5.05;
/** A visit line breaks where its bins are further apart than this (µm). */
const GAP_UM = 0.4;

/**
 * Main molecular bands (µm): centre and an approximate extent, drawn as faint bands.
 * They are a hint of where to look, not a fit.
 */
export const MOLECULES: { um: number; lo: number; hi: number; label: string; name: string }[] = [
  { um: 1.4, lo: 1.33, hi: 1.49, label: "H₂O", name: "water" },
  { um: 1.65, lo: 1.6, hi: 1.72, label: "CH₄", name: "methane" },
  { um: 1.9, lo: 1.8, hi: 1.98, label: "H₂O", name: "water" },
  { um: 2.3, lo: 2.2, hi: 2.4, label: "CH₄", name: "methane" },
  { um: 2.7, lo: 2.6, hi: 2.85, label: "H₂O", name: "water" },
  { um: 3.0, lo: 2.9, hi: 3.12, label: "ice", name: "water ice" },
  { um: 3.3, lo: 3.2, hi: 3.42, label: "CH₄", name: "methane" },
  { um: 4.27, lo: 4.2, hi: 4.34, label: "CO₂", name: "carbon dioxide" },
  { um: 4.67, lo: 4.55, hi: 4.8, label: "CO", name: "carbon monoxide" },
];

/** A drawn point, for hover and keyboard reading. */
export interface PlotPoint {
  x: number;
  y: number;
  p: SpectrumPoint;
  limit: boolean;
}

export interface Chart {
  svg: string;
  points: PlotPoint[];
  /** Plot box in SVG units (for the hover layer). */
  box: { left: number; top: number; right: number; bottom: number };
}

export function spectrumDomain(spectrum: SpectrumPoint[]): { min: number; max: number; ticks: number[] } {
  let lo = Infinity;
  let hi = -Infinity;
  for (const p of spectrum) {
    if (isUpperLimit(p)) {
      const v = upperLimitValue(p);
      if (v > 0) {
        lo = Math.min(lo, v);
        hi = Math.max(hi, v);
      }
      continue;
    }
    lo = Math.min(lo, p.flux_ujy - p.err_ujy > 0 ? p.flux_ujy - p.err_ujy : p.flux_ujy);
    hi = Math.max(hi, p.flux_ujy + p.err_ujy);
  }
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return logTicks(1, 10);
  return logTicks(lo, hi);
}

/** Labels in rows so neighbours don't collide (≈ 5.6 px per character at 9.5 px). */
export function moleculeRows(xOf: (um: number) => number): number[] {
  const rows: number[] = [];
  const lastEnd: number[] = [];
  for (const m of MOLECULES) {
    const w = m.label.length * 5.6 + 5;
    const x0 = xOf(m.um) - w / 2;
    let row = 0;
    while (lastEnd[row] != null && lastEnd[row] > x0) row++;
    lastEnd[row] = x0 + w;
    rows.push(row);
  }
  return rows;
}

/** A DR1 reference point (contracts/api.md, science.json `reference.spectrum`): µm and µJy. */
export interface ReferencePoint {
  um: number;
  flux_ujy: number;
}

/** Reference points that can be drawn: inside the chart's wavelengths, finite and above zero (log scale). */
export function drawableReference(reference: ReferencePoint[] | null | undefined): ReferencePoint[] {
  return (reference ?? []).filter(
    (p) => Number.isFinite(p.um) && Number.isFinite(p.flux_ujy) && p.flux_ujy > 0 && p.um >= UM_MIN && p.um <= UM_MAX,
  );
}

export function renderSpectrum(
  spectrum: SpectrumPoint[],
  opts: { width: number; mode: SpectrumMode; id: string; reference?: ReferencePoint[] | null },
): Chart {
  const W = Math.round(Math.max(280, Math.min(opts.width, 720)));
  const H = Math.round(Math.max(220, Math.min(W * 0.66, 300)));
  const left = 36;
  const right = W - 14;
  const xOf = (um: number) => left + ((um - UM_MIN) / (UM_MAX - UM_MIN)) * (right - left);
  const rows = moleculeRows(xOf);
  const nRows = Math.max(...rows) + 1;
  const box = { left, top: 6 + nRows * 11 + 4, right, bottom: H - 44 };
  const reference = drawableReference(opts.reference);
  // The reference shares the flux axis, so the domain covers it too.
  const dom = spectrumDomain([
    ...spectrum,
    ...reference.map((p) => ({ um: p.um, flux_ujy: p.flux_ujy, err_ujy: 0, det: 6 as const, mjd: 0, visit: 0, saturated: false })),
  ]);
  const lmin = Math.log10(dom.min);
  const lmax = Math.log10(dom.max);
  const yOf = (v: number) => box.bottom - ((Math.log10(Math.max(v, dom.min)) - lmin) / (lmax - lmin)) * (box.bottom - box.top);

  const visits = visitsOf(spectrum);
  const vIndex = new Map(visits.map((v, i) => [v, i]));
  const out: string[] = [];

  // Molecule bands: faint washes the full plot height, small labels on top.
  MOLECULES.forEach((m, i) => {
    const x0 = xOf(m.lo);
    const x1 = xOf(m.hi);
    const ly = 6 + rows[i] * 11 + 8;
    out.push(
      `<g class="cd-mol"><title>${esc(m.name)} band, near ${m.um} µm</title>` +
        `<rect x="${r1(x0)}" y="${box.top}" width="${r1(x1 - x0)}" height="${r1(box.bottom - box.top)}" fill="var(--ink)" opacity="0.024"/>` +
        `<text x="${r1(xOf(m.um))}" y="${r1(ly)}" class="cd-mol-t" text-anchor="middle">${m.label}</text></g>`,
    );
  });

  // Y grid only on labelled ticks: hairlines.
  for (const t of dom.ticks) {
    const y = yOf(t);
    out.push(`<line x1="${box.left}" x2="${box.right}" y1="${r1(y)}" y2="${r1(y)}" stroke="var(--hair)" stroke-width="1"/>`);
    out.push(`<text x="${box.left - 6}" y="${r1(y + 3.5)}" class="cd-ax" text-anchor="end">${compactUjy(t)}</text>`);
  }
  // X: wavelength labels, then the detector strip with its names.
  for (const um of [1, 2, 3, 4, 5]) {
    const x = xOf(um);
    out.push(`<text x="${r1(x)}" y="${box.bottom + 14}" class="cd-ax" text-anchor="middle">${um === 5 ? "5 µm" : um}</text>`);
  }
  for (const [d, r] of Object.entries(DETECTORS)) {
    const x0 = xOf(r.min);
    const x1 = xOf(r.max);
    out.push(`<rect x="${r1(x0 + 1)}" y="${box.bottom + 21}" width="${r1(x1 - x0 - 2)}" height="4" rx="2" fill="var(--d${d})"/>`);
    out.push(`<text x="${r1((x0 + x1) / 2)}" y="${box.bottom + 38}" class="cd-ax cd-ax-d" text-anchor="middle">D${d}</text>`);
  }

  // Points: error bars first, then marks.
  const points: PlotPoint[] = [];
  const bars: string[] = [];
  const marks: string[] = [];
  const byVisit = opts.mode === "visit";
  for (const p of spectrum) {
    const x = xOf(p.um);
    const vi = vIndex.get(p.visit) ?? 0;
    const colour = byVisit ? visitColour(vi, visits.length) : `var(--d${p.det})`;
    const shape = byVisit ? visitShape(vi) : "circle";
    if (isUpperLimit(p)) {
      const y = yOf(upperLimitValue(p));
      marks.push(caret(x, y, colour));
      points.push({ x, y, p, limit: true });
      continue;
    }
    const y = yOf(p.flux_ujy);
    const yHi = yOf(p.flux_ujy + p.err_ujy);
    const yLo = yOf(p.flux_ujy - p.err_ujy);
    if (yLo - yHi > 2) bars.push(`<line x1="${r1(x)}" x2="${r1(x)}" y1="${r1(yHi)}" y2="${r1(yLo)}" stroke="${colour}"/>`);
    marks.push(marker(shape, x, y, 2.5, colour, p.saturated, byVisit ? `opacity="0.5"` : ""));
    points.push({ x, y, p, limit: false });
  }
  out.push(`<g stroke-width="1" stroke-linecap="round" opacity="${byVisit ? 0.2 : 0.45}">${bars.join("")}</g>`);
  out.push(`<g>${marks.join("")}</g>`);

  // By visit: a median line per visit through wavelength bins, so changes between visits read at a glance.
  if (byVisit) {
    for (const v of visits) {
      const pts = visitCurve(spectrum.filter((p) => p.visit === v));
      if (pts.length < 2) continue;
      // Break the line across wavelength gaps: no data there, so no line either.
      const d = pts.map((q, i) => `${i && q.um - pts[i - 1].um < GAP_UM ? "L" : "M"}${r1(xOf(q.um))} ${r1(yOf(q.flux))}`).join("");
      const c = visitColour(vIndex.get(v) ?? 0, visits.length);
      out.push(`<path d="${d}" fill="none" stroke="var(--glass)" stroke-width="4" stroke-linejoin="round" stroke-linecap="round"/>`);
      out.push(`<path d="${d}" fill="none" stroke="${c}" stroke-width="1.75" stroke-linejoin="round" stroke-linecap="round"/>`);
    }
  }

  // DR1 reference: a dashed ink line with small dots, over the measured points and on the same
  // flux scale. Not rings: hollow marks already mean a saturated core.
  if (reference.length) {
    const pts = [...reference].sort((a, b) => a.um - b.um);
    const d = pts.map((q, i) => `${i && q.um - pts[i - 1].um < GAP_UM ? "L" : "M"}${r1(xOf(q.um))} ${r1(yOf(q.flux_ujy))}`).join("");
    const dots = pts
      .map(
        (p) =>
          `<circle cx="${r1(xOf(p.um))}" cy="${r1(yOf(p.flux_ujy))}" r="1.6"><title>DR1 reference: ${p.um.toFixed(2)} µm, ${Math.round(p.flux_ujy).toLocaleString("en-US")} µJy</title></circle>`,
      )
      .join("");
    out.push(
      `<g class="cd-refpts"><path d="${d}" fill="none" stroke="var(--glass)" stroke-width="3.5" stroke-linejoin="round" stroke-linecap="round"/>` +
        `<path d="${d}" fill="none" stroke="var(--ink)" stroke-width="1.25" stroke-dasharray="4 2.5" stroke-linejoin="round" stroke-linecap="round"/>` +
        `<g fill="var(--ink)">${dots}</g></g>`,
    );
  }

  // Hover ring (moved by the card).
  out.push(`<circle class="cd-hover" r="6" cx="0" cy="0" visibility="hidden" fill="none" stroke="var(--ink)" stroke-width="1.5"/>`);

  const dets = [...new Set(spectrum.map((p) => p.det))].sort();
  const title = `Spectrum, ${spectrum.length} points`;
  const desc =
    `Flux in microjansky on a log scale against wavelength in micrometres, ${UM_MIN} to 5 µm. ` +
    `Detectors ${dets.map((d) => `D${d}`).join(", ")}; ${visits.length} visit${visits.length === 1 ? "" : "s"}. ` +
    `Flux range ${compactUjy(dom.min)} to ${compactUjy(dom.max)} µJy.` +
    (byVisit ? " Shaded by visit, light to dark, with a median line per visit." : " Coloured by detector.") +
    (reference.length ? ` Dashed line: ${reference.length} points of the DR1 all-sky reference at this spot.` : "");
  const svg =
    `<svg class="cd-chart" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-labelledby="${opts.id}-t ${opts.id}-d" tabindex="0">` +
    `<title id="${opts.id}-t">${esc(title)}</title><desc id="${opts.id}-d">${esc(desc)}</desc>` +
    out.join("") +
    `</svg>`;
  return { svg, points, box };
}

/** Median flux of one visit in wavelength bins (six bins per detector), skipping upper limits. */
export function visitCurve(points: SpectrumPoint[]): { um: number; flux: number }[] {
  const out: { um: number; flux: number }[] = [];
  for (const r of Object.values(DETECTORS)) {
    const nb = 6;
    const w = (r.max - r.min) / nb;
    for (let b = 0; b < nb; b++) {
      const lo = r.min + b * w;
      const inBin = points.filter((p) => !isUpperLimit(p) && p.um >= lo && p.um < lo + w);
      if (inBin.length) out.push({ um: median(inBin.map((p) => p.um)), flux: median(inBin.map((p) => p.flux_ujy)) });
    }
  }
  return out.sort((a, b) => a.um - b.um);
}

/** One line for a point, for the readout under the chart and the keyboard. */
export function describePoint(p: SpectrumPoint, limit: boolean): string {
  const n = (v: number) => Math.round(v).toLocaleString("en-US");
  const flux = limit ? `not detected, under ${n(upperLimitValue(p))} µJy` : `${n(p.flux_ujy)} ± ${n(p.err_ujy)} µJy`;
  const where = [`D${p.det}`, p.visit > 0 ? `visit ${p.visit}` : "", formatMjd(p.mjd), p.saturated ? "core saturated" : ""].filter(Boolean).join(", ");
  return `${p.um.toFixed(2)} µm: ${flux} (${where})`;
}
