// Tiny SVG helpers shared by the spectrum and the light curve (strings, no library).

export const esc = (s: string): string =>
  s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

export const r1 = (n: number): string => (Math.round(n * 10) / 10).toString();

/**
 * Visits are ordered in time, so they take one graphite hue from light (first visit) to ink (last),
 * never a detector colour (those mean wavelength only). Ends validated as an ordinal ramp on
 * --glass (#edf0ef): monotone lightness, adjacent ΔL ≥ 0.06 up to five visits, light end 2.6:1.
 * Shapes are the second channel.
 */
const GRAPHITE_LIGHT = [0x8b, 0x95, 0xa5];
const GRAPHITE_DARK = [0x15, 0x1d, 0x2c];
export const VISIT_SHAPES = ["circle", "square", "triangle", "diamond", "cross"] as const;
export type Shape = (typeof VISIT_SHAPES)[number];

/** Graphite for visit i of n (0-based): the first visit lightest, the last darkest. */
export function visitColour(i: number, n: number): string {
  const t = n <= 1 ? 1 : Math.min(1, Math.max(0, i / (n - 1)));
  const c = GRAPHITE_LIGHT.map((a, k) => Math.round(a + (GRAPHITE_DARK[k] - a) * t));
  return `#${c.map((v) => v.toString(16).padStart(2, "0")).join("")}`;
}
export function visitShape(i: number): Shape {
  return VISIT_SHAPES[i % VISIT_SHAPES.length];
}

/** A marker of radius r at (x, y). Filled marks wear a thin glass ring; hollow ones carry the colour on the stroke. */
export function marker(shape: Shape, x: number, y: number, r: number, colour: string, hollow: boolean, extra = "", strokeWidth?: number): string {
  const paint = hollow
    ? `fill="var(--glass)" stroke="${colour}" stroke-width="${strokeWidth ?? 1.2}"`
    : `fill="${colour}" stroke="var(--glass)" stroke-width="${strokeWidth ?? 0.8}"`;
  switch (shape) {
    case "square": {
      const s = r * 0.88;
      return `<rect x="${r1(x - s)}" y="${r1(y - s)}" width="${r1(2 * s)}" height="${r1(2 * s)}" ${paint} ${extra}/>`;
    }
    case "triangle": {
      const s = r * 1.2;
      return `<path d="M${r1(x)} ${r1(y - s)}L${r1(x + s)} ${r1(y + s * 0.8)}L${r1(x - s)} ${r1(y + s * 0.8)}Z" ${paint} ${extra}/>`;
    }
    case "diamond": {
      const s = r * 1.25;
      return `<path d="M${r1(x)} ${r1(y - s)}L${r1(x + s)} ${r1(y)}L${r1(x)} ${r1(y + s)}L${r1(x - s)} ${r1(y)}Z" ${paint} ${extra}/>`;
    }
    case "cross": {
      const s = r * 1.05;
      return `<path d="M${r1(x - s)} ${r1(y - s)}L${r1(x + s)} ${r1(y + s)}M${r1(x - s)} ${r1(y + s)}L${r1(x + s)} ${r1(y - s)}" fill="none" stroke="${colour}" stroke-width="1.5" stroke-linecap="round" ${extra}/>`;
    }
    default:
      return `<circle cx="${r1(x)}" cy="${r1(y)}" r="${r1(r)}" ${paint} ${extra}/>`;
  }
}

/** Small open downward caret: an upper limit (not detected above 2σ). */
export function caret(x: number, y: number, colour: string): string {
  return `<path d="M${r1(x - 3)} ${r1(y - 1.5)}L${r1(x + 3)} ${r1(y - 1.5)}L${r1(x)} ${r1(y + 2.5)}Z" fill="none" stroke="${colour}" stroke-width="1" stroke-linejoin="round" opacity="0.8"/>`;
}

/** Legend key as a 14×14 inline SVG. */
export function swatch(inner: string): string {
  return `<svg class="cd-swatch" width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">${inner}</svg>`;
}
