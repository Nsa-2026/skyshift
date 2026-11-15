// Every puzzle is derived from the same real frames and photometry used by the Viewer.
import type { GaiaStar, ObjectResponse, SpectrumPoint } from '../api/types';
import { gaiaAt, sepArcsec, skyToPixel } from '../app/astro';
import type { Observation } from '../product/observations';
import { motionDirection } from '../product/evidence';
import { cropAround } from './data';

export type Pixel = { x: number; y: number };
export type SkyCase = {
  target: Pixel; reference: Pixel; referenceStar: GaiaStar;
  crop: { x: number; y: number; w: number; h: number };
  direction: string; arcmin: number; tolerance: number;
};
export function directionFromOffset(east: number, north: number): string {
  const angle = (Math.atan2(east, north) * 180 / Math.PI + 360) % 360;
  return ['north', 'northeast', 'east', 'southeast', 'south', 'southwest', 'west', 'northwest'][Math.round(angle / 45) % 8];
}
export function skyCase(first: Observation, object: ObjectResponse): SkyCase {
  if (!object.gaia) throw new Error('A catalogued target position is required for this field.');
  const f = first.set.frames, centre = first.epoch.center ?? f.center;
  const at = gaiaAt(object.gaia, first.epoch.mjd_mid);
  const target = skyToPixel(at, f, centre);
  const inside = (p: Pixel) => p.x >= 8 && p.y >= 8 && p.x < f.size[0] - 8 && p.y < f.size[1] - 8;
  if (!inside(target)) throw new Error('The target is outside the usable part of this observation.');
  const neighbours = f.gaia.filter(s => s.id !== object.gaia!.id && s.g != null)
    .map(s => ({ star: s, at: gaiaAt(s, first.epoch.mjd_mid), pixel: skyToPixel(gaiaAt(s, first.epoch.mjd_mid), f, centre) }))
    .filter(s => inside(s.pixel) && Math.hypot(s.pixel.x - target.x, s.pixel.y - target.y) > 12)
    .filter(s => {
      // A catalogue position alone is not a visible reference: require finite image pixels nearby.
      const x = Math.round(s.pixel.x), y = Math.round(s.pixel.y);
      return [-1, 0, 1].some(dy => [-1, 0, 1].some(dx => Number.isFinite(first.values[(y + dy) * f.size[0] + x + dx])));
    }).sort((a, b) => a.star.g! - b.star.g!);
  const chosen = neighbours[0];
  if (!chosen) throw new Error('No suitable reference star is available in this observation.');
  return {
    target, reference: chosen.pixel, referenceStar: chosen.star,
    crop: cropAround([target, chosen.pixel], f.size, 14, 64),
    direction: directionFromOffset(chosen.pixel.x - target.x, chosen.pixel.y - target.y),
    arcmin: sepArcsec(at, chosen.at) / 60,
    tolerance: Math.max(2.5, 15 / f.pixel_arcsec),
  };
}
export const matchesTarget = (pick: Pixel, field: SkyCase): boolean => Math.hypot(pick.x - field.target.x, pick.y - field.target.y) <= field.tolerance;
export function measuredDirection(object: ObjectResponse, detector: number): string {
  const m = object.measure;
  if (!m || !Number.isFinite(m.shift_arcsec) || !Number.isFinite(m.pa_deg) || !Number.isFinite(m.sigma_arcsec) || m.sigma_arcsec <= 0 || (m.source && m.source.det !== detector)) return 'Not enough evidence';
  const direction = motionDirection(m);
  return direction === 'No significant motion measured' ? 'Not enough evidence' : direction;
}

/** Usable detections, never invented/interpolated samples or saturated/low-significance points. */
export function usableSpectrum(points: readonly SpectrumPoint[], visit: number | null = null): SpectrumPoint[] {
  return points.filter(p => (visit == null || p.visit === visit) && !p.saturated && Number.isFinite(p.um) && p.um > 0 &&
    Number.isFinite(p.flux_ujy) && Number.isFinite(p.err_ujy) && p.err_ujy > 0 && p.flux_ujy / p.err_ujy >= 3 &&
    Number.isInteger(p.det) && p.det >= 1 && p.det <= 6 && Number.isFinite(p.mjd))
    .sort((a, b) => a.um - b.um || a.mjd - b.mjd);
}
function median(values: number[]): number {
  const a = [...values].sort((a, b) => a - b), mid = Math.floor(a.length / 2);
  return a.length % 2 ? a[mid] : (a[mid - 1] + a[mid]) / 2;
}
export function bandEvidence(points: readonly SpectrumPoint[], visit: number) {
  const valid = usableSpectrum(points, visit);
  const summary = (det: number) => {
    const p = valid.filter(p => p.det === det);
    return p.length >= 3 ? { count: p.length, flux: median(p.map(p => p.flux_ujy)) } : null;
  };
  const short = summary(3), long = summary(6);
  // This is a descriptive comparison of measured medians, not a fitted colour/temperature test.
  const winner = !short || !long || short.flux === long.flux ? 'Not enough evidence' : short.flux > long.flux ? 'D3' : 'D6';
  return { short, long, winner };
}
export function nearestSample(points: readonly SpectrumPoint[], um: number): number {
  let best = 0;
  for (let i = 1; i < points.length; i++) if (Math.abs(points[i].um - um) < Math.abs(points[best].um - um)) best = i;
  return best;
}

const r = (n: number) => Number(n.toFixed(2));
export const spectrumX = (um: number, width = 760) => 60 + (um - .7) / 4.4 * (width - 80);
export function spectrumSvg(points: SpectrumPoint[], allPoints: SpectrumPoint[], selected: number, errors: boolean, bands: boolean, width = 760): string {
  const W = Math.round(Math.max(340, Math.min(760, width))), X0 = 60, X1 = W - 20, Y0 = 60, Y1 = W < 500 ? 260 : 302;
  const xOf = (um: number) => spectrumX(um, W);
  const lo = Math.min(...allPoints.map(p => Math.max(p.flux_ujy - p.err_ujy, p.flux_ujy * .5)));
  const hi = Math.max(...allPoints.map(p => p.flux_ujy + p.err_ujy));
  const floor = Math.floor(Math.log10(lo)), ceil = Math.max(floor + 1, Math.ceil(Math.log10(hi)));
  const yOf = (v: number) => Y1 - (Math.log10(Math.max(v, 10 ** floor)) - floor) / (ceil - floor) * (Y1 - Y0);
  const grid = Array.from({ length: ceil - floor + 1 }, (_, i) => {
    const v = 10 ** (floor + i), y = r(yOf(v));
    return `<line x1="${X0}" x2="${X1}" y1="${y}" y2="${y}" class="sd-grid-line"/><text x="50" y="${y + 4}" text-anchor="end">${v >= 1000 ? `${v / 1000}k` : v}</text>`;
  }).join('');
  const labels = [1, 2, 3, 4, 5].map(um => `<text x="${r(xOf(um))}" y="${Y1+23}" text-anchor="middle">${um}</text>`).join('');
  const reference = bands ? [[1.33,1.49,'Water region'],[1.8,1.98,'Water region'],[3.2,3.42,'Methane region'],[4.55,4.8,'CO region']].map(([lo,hi,label]) =>
    `<g><rect class="sd-molecule" x="${r(xOf(Number(lo)))}" y="${Y0}" width="${r(xOf(Number(hi)) - xOf(Number(lo)))}" height="${Y1 - Y0}"/><text class="sd-band-word" x="${r(xOf((Number(lo) + Number(hi)) / 2))}" y="43" text-anchor="middle">${W < 500 ? String(label).replace('Water region','H₂O').replace('Methane region','CH₄').replace('CO region','CO') : label}</text></g>`).join('') : '';
  const dots = points.map((p,i) => {
    const x = r(xOf(p.um)), y = r(yOf(p.flux_ujy));
    return `<g style="color:var(--d${p.det})">${errors ? `<line x1="${x}" x2="${x}" y1="${r(yOf(p.flux_ujy - p.err_ujy))}" y2="${r(yOf(p.flux_ujy + p.err_ujy))}" class="sd-errorbar"/>` : ''}<circle cx="${x}" cy="${y}" r="${i === selected ? 5 : 2.7}" class="sd-sample ${i === selected ? 'is-selected' : ''}"/></g>`;
  }).join('');
  const p = points[selected], cursor = p ? `<line x1="${r(xOf(p.um))}" x2="${r(xOf(p.um))}" y1="${Y0}" y2="${Y1}" class="sd-cursor-line"/>` : '';
  return `<svg class="sd-spectrum" data-chart-width="${W}" viewBox="0 0 ${W} ${Y1+63}" role="img" aria-labelledby="sd-spectrum-title sd-spectrum-desc"><title id="sd-spectrum-title">Measured spectrum of Epsilon Indi B</title><desc id="sd-spectrum-desc">${points.length} real NASA SPHEREx photometry points. Horizontal axis: wavelength in micrometres. Vertical axis: flux density in microjansky on a logarithmic scale. No model curve is added. Use the wavelength slider to inspect each measurement.</desc>${reference}${grid}${cursor}${dots}${labels}<text x="${W/2}" y="${Y1+51}" text-anchor="middle">Wavelength (µm)</text><text x="60" y="17">Flux density (µJy · log scale)</text></svg>`;
}
