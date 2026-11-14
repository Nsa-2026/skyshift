// Spectrum chart: flux (µJy, log) against wavelength (µm, 0.70–5.05), inline SVG.
// Colour = detector (redundant with x position, so never the only channel) or visit (+ shape).
import type { SpectrumPoint } from "../api/types";
import { DETECTORS, formatMjd } from "../app/astro";
import { compactUjy, isUpperLimit, logTicks, median, upperLimitValue, visitsOf } from "./format";
import { caret, esc, marker, r1, visitColour, visitShape } from "./svg";

export type SpectrumMode = "det" | "visit";

export const UM_MIN = 0.7;
export const UM_MAX = 5.05;
/** A visit line breaks where its bins are further apart than this (µm). */
const GAP_UM = 0.4;

/** Main molecular bands (µm). Labels stay small; they are a hint, not a fit. */
export const MOLECULES: { um: number; label: string; name: string }[] = [
  { um: 1.4, label: "H₂O", name: "water" },
  { um: 1.65, label: "CH₄", name: "methane" },
  { um: 1.9, label: "H₂O", name: "water" },
  { um: 2.3, label: "CH₄", name: "methane" },
  { um: 2.7, label: "H₂O", name: "water" },
  { um: 3.0, label: "ice", name: "water ice" },
  { um: 3.3, label: "CH₄", name: "methane" },
  { um: 4.27, label: "CO₂", name: "carbon dioxide" },
  { um: 4.67, label: "CO", name: "carbon monoxide" },
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

/** Labels in rows so neighbours don't collide (≈ 5.5 px per character at 10 px). */
export function moleculeRows(xOf: (um: number) => number): number[] {
  const rows: number[] = [];
  const lastEnd: number[] = [];
  for (const m of MOLECULES) {
    const w = m.label.length * 5.5 + 4;
    const x0 = xOf(m.um) - w / 2;
    let row = 0;
    while (lastEnd[row] != null && lastEnd[row] > x0) row++;
    lastEnd[row] = x0 + w;
    rows.push(row);
  }
  return rows;
}

export function renderSpectrum(spectrum: SpectrumPoint[], opts: { width: number; mode: SpectrumMode; id: string }): Chart {
  const W = Math.max(280, Math.min(opts.width, 720));
  const H = Math.round(Math.max(220, Math.min(W * 0.7, 320)));
  const pad = { left: 38, right: 16, top: 34, bottom: 48 };
  const box = { left: pad.left, top: pad.top, right: W - pad.right, bottom: H - pad.bottom };
  const xOf = (um: number) => box.left + ((um - UM_MIN) / (UM_MAX - UM_MIN)) * (box.right - box.left);
  const dom = spectrumDomain(spectrum);
  const lmin = Math.log10(dom.min);
  const lmax = Math.log10(dom.max);
  const yOf = (v: number) => box.bottom - ((Math.log10(Math.max(v, dom.min)) - lmin) / (lmax - lmin)) * (box.bottom - box.top);

  const visits = visitsOf(spectrum);
  const vIndex = new Map(visits.map((v, i) => [v, i]));
  const out: string[] = [];

  // Detector bands: faint washes in the plot, a solid strip under the axis.
  for (const [d, r] of Object.entries(DETECTORS)) {
    const x0 = xOf(r.min);
    const x1 = xOf(r.max);
    if (Number(d) % 2 === 0) out.push(`<rect x="${r1(x0)}" y="${box.top}" width="${r1(x1 - x0)}" height="${box.bottom - box.top}" fill="var(--plate-3)" opacity="0.35"/>`);
    out.push(`<rect x="${r1(x0 + 0.5)}" y="${box.bottom + 20}" width="${r1(x1 - x0 - 1)}" height="5" rx="1" fill="var(--d${d})"/>`);
    out.push(`<text x="${r1((x0 + x1) / 2)}" y="${box.bottom + 38}" class="cd-ax" text-anchor="middle">D${d}</text>`);
  }

  // Y grid and ticks.
  for (const t of dom.ticks) {
    const y = yOf(t);
    out.push(`<line x1="${box.left}" x2="${box.right}" y1="${r1(y)}" y2="${r1(y)}" stroke="var(--rule)" stroke-width="1"/>`);
    out.push(`<text x="${box.left - 5}" y="${r1(y + 3.5)}" class="cd-ax" text-anchor="end">${compactUjy(t)}</text>`);
  }
  // X ticks.
  for (const um of [1, 2, 3, 4, 5]) {
    const x = xOf(um);
    out.push(`<line x1="${r1(x)}" x2="${r1(x)}" y1="${box.bottom}" y2="${box.bottom + 4}" stroke="var(--rule-strong)"/>`);
    out.push(`<text x="${r1(x)}" y="${box.bottom + 15}" class="cd-ax" text-anchor="middle">${um === 5 ? "5 µm" : um}</text>`);
  }
  out.push(`<line x1="${box.left}" x2="${box.right}" y1="${box.bottom}" y2="${box.bottom}" stroke="var(--rule-strong)"/>`);

  // Molecule marks along the top.
  const rows = moleculeRows(xOf);
  MOLECULES.forEach((m, i) => {
    const x = xOf(m.um);
    const ly = box.top - 6 - rows[i] * 11;
    out.push(`<g class="cd-mol"><title>${esc(m.name)} band, ${m.um} µm</title>`);
    out.push(`<line x1="${r1(x)}" x2="${r1(x)}" y1="${r1(ly + 2)}" y2="${box.bottom}" stroke="var(--muted)" stroke-width="1" opacity="0.16"/>`);
    out.push(`<text x="${r1(x)}" y="${r1(ly)}" class="cd-mol-t" text-anchor="middle">${m.label}</text></g>`);
  });

  // Points: error bars first, then marks.
  const points: PlotPoint[] = [];
  const bars: string[] = [];
  const marks: string[] = [];
  const faded = opts.mode === "visit";
  for (const p of spectrum) {
    const x = xOf(p.um);
    const vi = vIndex.get(p.visit) ?? 0;
    const colour = opts.mode === "det" ? `var(--d${p.det})` : visitColour(vi);
    const shape = opts.mode === "det" ? "circle" : visitShape(vi);
    if (isUpperLimit(p)) {
      const y = yOf(upperLimitValue(p));
      marks.push(caret(x, y, colour));
      points.push({ x, y, p, limit: true });
      continue;
    }
    const y = yOf(p.flux_ujy);
    const yHi = yOf(p.flux_ujy + p.err_ujy);
    const yLo = yOf(p.flux_ujy - p.err_ujy);
    if (yLo - yHi > 1.5) bars.push(`<line x1="${r1(x)}" x2="${r1(x)}" y1="${r1(yHi)}" y2="${r1(yLo)}" stroke="${colour}"/>`);
    marks.push(marker(shape, x, y, p.saturated ? 2.7 : 2.4, colour, p.saturated, faded ? `opacity="0.55"` : ""));
    points.push({ x, y, p, limit: false });
  }
  out.push(`<g stroke-width="1" opacity="${faded ? 0.25 : 0.45}">${bars.join("")}</g>`);
  out.push(`<g>${marks.join("")}</g>`);

  // By visit: a median line per visit through wavelength bins, so changes between visits read at a glance.
  if (opts.mode === "visit") {
    for (const v of visits) {
      const pts = visitCurve(spectrum.filter((p) => p.visit === v));
      if (pts.length < 2) continue;
      // Break the line across wavelength gaps: no data there, so no line either.
      const d = pts.map((q, i) => `${i && q.um - pts[i - 1].um < GAP_UM ? "L" : "M"}${r1(xOf(q.um))} ${r1(yOf(q.flux))}`).join("");
      const c = visitColour(vIndex.get(v) ?? 0);
      out.push(`<path d="${d}" fill="none" stroke="var(--plate-2)" stroke-width="4" stroke-linejoin="round" stroke-linecap="round"/>`);
      out.push(`<path d="${d}" fill="none" stroke="${c}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`);
    }
  }

  // Hover ring (moved by the card).
  out.push(`<circle class="cd-hover" r="6" cx="0" cy="0" visibility="hidden" fill="none" stroke="var(--emulsion)" stroke-width="1.5"/>`);

  const dets = [...new Set(spectrum.map((p) => p.det))].sort();
  const title = `Spectrum, ${spectrum.length} points`;
  const desc =
    `Flux in microjansky on a log scale against wavelength in micrometres, ${UM_MIN} to 5 µm. ` +
    `Detectors ${dets.map((d) => `D${d}`).join(", ")}; ${visits.length} visit${visits.length === 1 ? "" : "s"}. ` +
    `Flux range ${compactUjy(dom.min)} to ${compactUjy(dom.max)} µJy.` +
    (opts.mode === "visit" ? " Coloured by visit, with a median line per visit." : " Coloured by detector.");
  const svg =
    `<svg class="cd-chart" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-labelledby="${opts.id}-t ${opts.id}-d" tabindex="0">` +
    `<title id="${opts.id}-t">${esc(title)}</title><desc id="${opts.id}-d">${esc(desc)}</desc>` +
    out.join("") +
    `</svg>`;
  return { svg, points, box };
}

/** Median flux of one visit in wavelength bins (about 8 bins per detector), skipping upper limits. */
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

/** Text for one point, for the readout line and the keyboard. */
export function describePoint(p: SpectrumPoint, limit: boolean): string {
  const flux = limit
    ? `not detected, below ${Math.round(upperLimitValue(p)).toLocaleString("en-US")} µJy`
    : `${Math.round(p.flux_ujy).toLocaleString("en-US")} ± ${Math.round(p.err_ujy).toLocaleString("en-US")} µJy`;
  const visit = p.visit > 0 ? `, visit ${p.visit}` : "";
  return `${p.um.toFixed(3)} µm: ${flux}. D${p.det}${visit}, ${formatMjd(p.mjd)}${p.saturated ? ". Core saturated" : ""}.`;
}
