// The lesson's spectrum: real SPHEREx points (detector colours, faint) with a median line in ink,
// and the water, methane and carbon monoxide bands named in words. Inline SVG on glass.
import type { SpectrumPoint } from "../api/types";
import { DETECTORS } from "../app/astro";
import { compactUjy, isUpperLimit, logTicks } from "../card/format";
import { visitCurve } from "../card/spectrum";
import { esc } from "./glossary";

export const UM_MIN = 0.7;
export const UM_MAX = 5.05;

/** Bands a brown dwarf shows clearly in SPHEREx; centres and extents in µm. Row 0 water/CO, row 1 methane. */
export const LESSON_BANDS: { lo: number; hi: number; um: number; word: string; row: 0 | 1 }[] = [
  { um: 1.4, lo: 1.33, hi: 1.49, word: "water", row: 0 },
  { um: 1.9, lo: 1.8, hi: 1.98, word: "water", row: 0 },
  { um: 2.3, lo: 2.2, hi: 2.4, word: "methane", row: 1 },
  { um: 2.7, lo: 2.6, hi: 2.85, word: "water", row: 0 },
  { um: 3.3, lo: 3.2, hi: 3.42, word: "methane", row: 1 },
  { um: 4.67, lo: 4.55, hi: 4.8, word: "CO", row: 0 },
];

const r1 = (n: number) => (Math.round(n * 10) / 10).toString();

export function lessonSpectrumSvg(points: SpectrumPoint[], opts: { width: number; id: string; name: string }): string {
  const W = Math.round(Math.max(300, Math.min(opts.width, 720)));
  const H = Math.round(Math.max(230, Math.min(W * 0.58, 340)));
  const left = 40;
  const right = W - 10;
  const top = 40;
  const bottom = H - 44;
  const xOf = (um: number) => left + ((um - UM_MIN) / (UM_MAX - UM_MIN)) * (right - left);

  const det = points.filter((p) => !isUpperLimit(p));
  const lo = Math.min(...det.map((p) => Math.max(p.flux_ujy - p.err_ujy, p.flux_ujy * 0.5)));
  const hi = Math.max(...det.map((p) => p.flux_ujy + p.err_ujy));
  const dom = det.length ? logTicks(lo, hi) : logTicks(1, 10);
  const l0 = Math.log10(dom.min);
  const l1 = Math.log10(dom.max);
  const yOf = (v: number) => bottom - ((Math.log10(Math.max(v, dom.min)) - l0) / (l1 - l0)) * (bottom - top);

  const out: string[] = [];
  for (const b of LESSON_BANDS) {
    const x0 = xOf(b.lo);
    const x1 = xOf(b.hi);
    out.push(
      `<rect x="${r1(x0)}" y="${top}" width="${r1(x1 - x0)}" height="${r1(bottom - top)}" class="ls-band"/>` +
        `<text x="${r1(xOf(b.um))}" y="${b.row ? 30 : 16}" class="ls-word" text-anchor="middle">${b.word}</text>`,
    );
  }
  for (const tk of dom.ticks) {
    const y = yOf(tk);
    out.push(`<line x1="${left}" x2="${right}" y1="${r1(y)}" y2="${r1(y)}" class="ls-grid"/>`);
    out.push(`<text x="${left - 6}" y="${r1(y + 4)}" class="ls-ax" text-anchor="end">${compactUjy(tk)}</text>`);
  }
  for (const um of [1, 2, 3, 4, 5]) {
    out.push(`<text x="${r1(xOf(um))}" y="${bottom + 15}" class="ls-ax" text-anchor="middle">${um === 5 ? "5 µm" : um}</text>`);
  }
  for (const [d, r] of Object.entries(DETECTORS)) {
    const x0 = xOf(r.min);
    const x1 = xOf(r.max);
    out.push(`<rect x="${r1(x0 + 1)}" y="${bottom + 22}" width="${r1(x1 - x0 - 2)}" height="4" rx="2" fill="var(--d${d})"/>`);
    out.push(`<text x="${r1((x0 + x1) / 2)}" y="${bottom + 39}" class="ls-ax" text-anchor="middle">D${d}</text>`);
  }
  // Points, faint; the median line carries the shape.
  const dots = det
    .map(
      (p) =>
        `<circle cx="${r1(xOf(p.um))}" cy="${r1(yOf(p.flux_ujy))}" r="2.2" ${
          p.saturated ? `fill="none" stroke="var(--d${p.det})"` : `fill="var(--d${p.det})"`
        }/>`,
    )
    .join("");
  out.push(`<g opacity="0.5">${dots}</g>`);
  const line = visitCurve(det);
  if (line.length > 1) {
    const d = line.map((q, i) => `${i && q.um - line[i - 1].um < 0.4 ? "L" : "M"}${r1(xOf(q.um))} ${r1(yOf(q.flux))}`).join("");
    out.push(`<path d="${d}" class="ls-halo"/><path d="${d}" class="ls-line"/>`);
  }
  const desc =
    `Brightness in microjansky (log scale) against wavelength from 0.75 to 5 µm, ${det.length} points with a median line. ` +
    `Shaded bands: water near 1.4, 1.9 and 2.7 µm, methane near 2.3 and 3.3 µm, carbon monoxide near 4.7 µm.`;
  return (
    `<svg class="ls-chart" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-labelledby="${opts.id}-t ${opts.id}-d">` +
    `<title id="${opts.id}-t">${esc(`Spectrum of ${opts.name}`)}</title><desc id="${opts.id}-d">${esc(desc)}</desc>` +
    out.join("") +
    `</svg>`
  );
}
