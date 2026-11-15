// Colour maps and painting a frame into RGBA. Colours come from the design tokens (web/DESIGN.md):
// a plate negative by default (sky --glass, stars --ink), a positive on request, and differences
// in grease pencil (--pencil-red: light arrived, --pencil-blue: light left) on glass.
import { sigmaStretch, stretch, stretchSigned, type Limits, type StretchKind } from "./math";

export type RGB = [number, number, number];

export interface Palette {
  /** The lightbox glass: sky in a negative, zero in a difference. */
  glass: RGB;
  /** Ink: stars in a negative, sky in a positive. */
  ink: RGB;
  /** Difference: light arrived (positive) and light left (negative). */
  arrived: RGB;
  left: RGB;
}

export function parseHex(s: string, fallback: RGB): RGB {
  const m = /^#?([0-9a-f]{6})$/i.exec(s.trim());
  if (!m) return fallback;
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

export function readPalette(el: Element = document.documentElement): Palette {
  const cs = getComputedStyle(el);
  const v = (name: string, fb: RGB) => parseHex(cs.getPropertyValue(name), fb);
  return {
    glass: v("--glass", [237, 240, 239]),
    ink: v("--ink", [24, 32, 46]),
    arrived: v("--pencil-red", [207, 35, 56]),
    left: v("--pencil-blue", [37, 86, 200]),
  };
}

export const lerp = (a: RGB, b: RGB, t: number): RGB => [
  Math.round(a[0] + (b[0] - a[0]) * t),
  Math.round(a[1] + (b[1] - a[1]) * t),
  Math.round(a[2] + (b[2] - a[2]) * t),
];

/**
 * 256-entry RGB lookup. Plain: index 0 is the sky, 255 the brightest star (ink on glass in a
 * negative, glass on ink in a positive). Signed: index 128 is zero (glass), 0 light left, 255 light
 * arrived. Differences always sit on glass.
 */
export function buildLut(p: Palette, signed: boolean, positive: boolean): Uint8ClampedArray {
  const lut = new Uint8ClampedArray(256 * 3);
  for (let i = 0; i < 256; i++) {
    let c: RGB;
    if (signed) {
      const s = (i - 128) / 127;
      c = lerp(p.glass, s < 0 ? p.left : p.arrived, Math.min(1, Math.abs(s)));
    } else {
      const t = i / 255;
      c = positive ? lerp(p.ink, p.glass, t) : lerp(p.glass, p.ink, t);
    }
    lut.set(c, i * 3);
  }
  return lut;
}

/** Value → LUT index for a plain image: 0 sky … 255 brightest. */
export function plainIndex(lim: Limits, kind: StretchKind): (v: number) => number {
  return (v) => Math.round(stretch(v, lim, kind) * 255);
}

/**
 * Value → LUT index for a difference, 128 = no change. With a noise sigma, colour starts at 1σ
 * (below that is noise) and is full at nσ; without one, a plain linear signed stretch.
 */
export function diffIndex(lim: Limits & { sigma?: number; n?: number }): (v: number) => number {
  const { sigma, n } = lim;
  if (sigma && sigma > 0 && n && n > 1) return (v) => Math.round(128 + sigmaStretch(v, sigma, n) * 127);
  return (v) => Math.round(128 + stretchSigned(v, lim, "linear") * 127);
}

/**
 * Paint values (row 0 = north, col 0 = east) into RGBA through `index` and the LUT. No-data
 * pixels become transparent, so the hatch drawn under the image shows through; pixels marked in
 * `burnt` (saturated cores) take the brightest colour instead, a burnt plate.
 */
export function paint(
  values: Float32Array,
  out: Uint8ClampedArray,
  lut: Uint8ClampedArray,
  index: (v: number) => number,
  burnt: Uint8Array | null = null,
): void {
  for (let i = 0; i < values.length; i++) {
    const v = values[i];
    const o = i * 4;
    let k: number;
    if (Number.isNaN(v)) {
      if (!burnt?.[i]) {
        out[o] = out[o + 1] = out[o + 2] = out[o + 3] = 0;
        continue;
      }
      k = 255;
    } else k = index(v);
    out[o] = lut[k * 3];
    out[o + 1] = lut[k * 3 + 1];
    out[o + 2] = lut[k * 3 + 2];
    out[o + 3] = 255;
  }
}

/**
 * Lighten painted pixels marked in `mask` toward `toward` by `t` (0–1): the part of a photo filled
 * from its other photos is drawn paler, so it never passes for what this photo saw.
 */
export function fade(out: Uint8ClampedArray, mask: Uint8Array, toward: RGB, t: number): void {
  for (let i = 0; i < mask.length; i++) {
    if (!mask[i]) continue;
    const o = i * 4;
    if (!out[o + 3]) continue;
    out[o] = Math.round(out[o] + (toward[0] - out[o]) * t);
    out[o + 1] = Math.round(out[o + 1] + (toward[1] - out[o + 1]) * t);
    out[o + 2] = Math.round(out[o + 2] + (toward[2] - out[o + 2]) * t);
  }
}
