// Tiny SVG helpers shared by the spectrum and the light curve (strings, no library).

export const esc = (s: string): string =>
  s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

export const r1 = (n: number): string => (Math.round(n * 10) / 10).toString();

/** Visit colours (validated against the plate surface: CVD ΔE ≥ 9 adjacent) and shapes as a second channel. */
export const VISIT_COLOURS = ["#4f9cff", "#ff7a59", "#e0c341", "#c08cff", "#3fd0a0"];
export const VISIT_SHAPES = ["circle", "square", "triangle", "diamond", "cross"] as const;
export type Shape = (typeof VISIT_SHAPES)[number];

export function visitColour(i: number): string {
  return VISIT_COLOURS[i % VISIT_COLOURS.length];
}
export function visitShape(i: number): Shape {
  return VISIT_SHAPES[i % VISIT_SHAPES.length];
}

/** A marker of radius r at (x, y). Hollow markers carry the colour on the stroke. */
export function marker(shape: Shape, x: number, y: number, r: number, colour: string, hollow: boolean, extra = ""): string {
  const paint = hollow
    ? `fill="var(--plate-2)" stroke="${colour}" stroke-width="1.3"`
    : `fill="${colour}" stroke="var(--plate-2)" stroke-width="0.7"`;
  switch (shape) {
    case "square": {
      const s = r * 0.9;
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
      const s = r * 1.1;
      return `<path d="M${r1(x - s)} ${r1(y - s)}L${r1(x + s)} ${r1(y + s)}M${r1(x - s)} ${r1(y + s)}L${r1(x + s)} ${r1(y - s)}" fill="none" stroke="${colour}" stroke-width="1.6" ${extra}/>`;
    }
    default:
      return `<circle cx="${r1(x)}" cy="${r1(y)}" r="${r1(r)}" ${paint} ${extra}/>`;
  }
}

/** Small downward caret: an upper limit (not detected above 2σ). */
export function caret(x: number, y: number, colour: string): string {
  return `<path d="M${r1(x - 3)} ${r1(y - 1.5)}L${r1(x + 3)} ${r1(y - 1.5)}L${r1(x)} ${r1(y + 2.5)}Z" fill="none" stroke="${colour}" stroke-width="1" opacity="0.75"/>`;
}

/** Legend swatch as a 14×14 inline SVG. */
export function swatch(inner: string): string {
  return `<svg class="cd-swatch" width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">${inner}</svg>`;
}
