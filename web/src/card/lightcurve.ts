// Light curve: one detector's flux against date, one median per visit over that detector's points.
// Inside a visit the points sample different wavelengths, so their spread is mostly colour, not time.
import type { Det, SpectrumPoint } from "../api/types";
import { DETECTORS, formatMjd, mjdToDate } from "../app/astro";
import { compactUjy, isUpperLimit, linearTicks, median } from "./format";
import { esc, marker, r1 } from "./svg";

export interface VisitFlux {
  visit: number;
  mjd: number;
  flux: number;
  err: number;
  n: number;
  saturated: boolean;
}

/** Per-visit median flux in one detector, with a robust error (1.4826 MAD / √n, at least the median point error / √n). */
export function lightCurve(spectrum: SpectrumPoint[], det: Det): VisitFlux[] {
  const byVisit = new Map<number, SpectrumPoint[]>();
  for (const p of spectrum) {
    if (p.det !== det || isUpperLimit(p)) continue;
    const list = byVisit.get(p.visit) ?? [];
    list.push(p);
    byVisit.set(p.visit, list);
  }
  const out: VisitFlux[] = [];
  for (const [visit, pts] of byVisit) {
    const fl = pts.map((p) => p.flux_ujy);
    const m = median(fl);
    const mad = median(fl.map((f) => Math.abs(f - m)));
    const n = pts.length;
    const err = Math.max((1.4826 * mad) / Math.sqrt(n), median(pts.map((p) => p.err_ujy)) / Math.sqrt(n));
    out.push({ visit, mjd: median(pts.map((p) => p.mjd)), flux: m, err, n, saturated: pts.filter((p) => p.saturated).length > n / 2 });
  }
  return out.sort((a, b) => a.mjd - b.mjd);
}

/** Detectors with data in at least two visits: the ones a light curve can show. */
export function lightCurveDets(spectrum: SpectrumPoint[]): Det[] {
  return ([1, 2, 3, 4, 5, 6] as Det[]).filter((d) => lightCurve(spectrum, d).length >= 2);
}

/** Month ticks (first of the month) every k months so that about 3–5 fit. */
export function monthTicks(mjd0: number, mjd1: number): number[] {
  const span = (mjd1 - mjd0) / 30.44;
  const k = [1, 2, 3, 6, 12, 24].find((s) => span / s <= 4) ?? 24;
  const start = mjdToDate(mjd0);
  let y = start.getUTCFullYear();
  let m = start.getUTCMonth() + 1;
  const ticks: number[] = [];
  for (let i = 0; i < 200; i++) {
    if (m > 11) {
      y += Math.floor(m / 12);
      m %= 12;
    }
    const mjd = Date.UTC(y, m, 1) / 86400000 + 40587;
    if (mjd > mjd1) break;
    if (m % k === 0 || k === 1) ticks.push(mjd);
    m += 1;
  }
  return ticks;
}

export function renderLightCurve(spectrum: SpectrumPoint[], det: Det, opts: { width: number; id: string }): string {
  const W = Math.max(280, Math.min(opts.width, 720));
  const H = Math.round(Math.max(170, Math.min(W * 0.5, 240)));
  const pad = { left: 44, right: 14, top: 12, bottom: 28 };
  const box = { left: pad.left, top: pad.top, right: W - pad.right, bottom: H - pad.bottom };
  const curve = lightCurve(spectrum, det);
  const raw = spectrum.filter((p) => p.det === det && !isUpperLimit(p));
  const mjds = raw.map((p) => p.mjd);
  const t0 = Math.min(...mjds) - 12;
  const t1 = Math.max(...mjds) + 12;
  const lo = Math.min(...raw.map((p) => p.flux_ujy), ...curve.map((c) => c.flux - c.err));
  const hi = Math.max(...raw.map((p) => p.flux_ujy), ...curve.map((c) => c.flux + c.err));
  const yt = linearTicks(lo, hi, 4);
  const xOf = (mjd: number) => box.left + ((mjd - t0) / (t1 - t0)) * (box.right - box.left);
  const yOf = (v: number) => box.bottom - ((v - yt.min) / (yt.max - yt.min)) * (box.bottom - box.top);
  const colour = `var(--d${det})`;
  const out: string[] = [];

  for (const t of yt.ticks) {
    const y = yOf(t);
    out.push(`<line x1="${box.left}" x2="${box.right}" y1="${r1(y)}" y2="${r1(y)}" stroke="var(--rule)"/>`);
    out.push(`<text x="${box.left - 5}" y="${r1(y + 3.5)}" class="cd-ax" text-anchor="end">${compactUjy(t)}</text>`);
  }
  for (const t of monthTicks(t0, t1)) {
    const x = xOf(t);
    if (x < box.left + 10 || x > box.right - 10) continue;
    const d = mjdToDate(t);
    const label = `${d.toLocaleString("en-GB", { month: "short", timeZone: "UTC" })} ${d.getUTCFullYear()}`;
    out.push(`<line x1="${r1(x)}" x2="${r1(x)}" y1="${box.bottom}" y2="${box.bottom + 4}" stroke="var(--rule-strong)"/>`);
    out.push(`<text x="${r1(x)}" y="${box.bottom + 16}" class="cd-ax" text-anchor="middle">${label}</text>`);
  }
  out.push(`<line x1="${box.left}" x2="${box.right}" y1="${box.bottom}" y2="${box.bottom}" stroke="var(--rule-strong)"/>`);

  // Every point of the detector, faint: the spread inside a visit is the spectrum's slope.
  out.push(
    `<g opacity="0.35">${raw
      .map((p) => `<circle cx="${r1(xOf(p.mjd))}" cy="${r1(yOf(p.flux_ujy))}" r="1.6" fill="${p.saturated ? "none" : colour}" stroke="${colour}" stroke-width="${p.saturated ? 0.8 : 0}"/>`)
      .join("")}</g>`,
  );
  // Medians joined.
  const path = curve.map((c, i) => `${i ? "L" : "M"}${r1(xOf(c.mjd))} ${r1(yOf(c.flux))}`).join("");
  out.push(`<path d="${path}" fill="none" stroke="var(--emulsion)" stroke-width="2" stroke-linejoin="round" opacity="0.85"/>`);
  for (const c of curve) {
    const x = xOf(c.mjd);
    out.push(`<line x1="${r1(x)}" x2="${r1(x)}" y1="${r1(yOf(c.flux + c.err))}" y2="${r1(yOf(c.flux - c.err))}" stroke="var(--emulsion)" stroke-width="1.5"/>`);
    out.push(`<g><title>${esc(`Visit ${c.visit}, ${formatMjd(c.mjd)}: ${Math.round(c.flux).toLocaleString("en-US")} ± ${Math.round(c.err).toLocaleString("en-US")} µJy, median of ${c.n} points${c.saturated ? ", mostly saturated" : ""}`)}</title>`);
    out.push(marker("circle", x, yOf(c.flux), 4.5, colour, c.saturated) + `</g>`);
  }

  const r = DETECTORS[det];
  const first = curve[0];
  const last = curve[curve.length - 1];
  const change = last.flux / first.flux;
  const title = `Light curve, D${det} (${r.min}–${r.max} µm)`;
  const desc = `Median flux per visit in D${det}: ${curve
    .map((c) => `visit ${c.visit} ${formatMjd(c.mjd)} ${Math.round(c.flux).toLocaleString("en-US")} µJy`)
    .join("; ")}. Last visit is ${change.toFixed(2)} times the first.`;
  return (
    `<svg class="cd-chart" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-labelledby="${opts.id}-t ${opts.id}-d">` +
    `<title id="${opts.id}-t">${esc(title)}</title><desc id="${opts.id}-d">${esc(desc)}</desc>${out.join("")}</svg>`
  );
}
