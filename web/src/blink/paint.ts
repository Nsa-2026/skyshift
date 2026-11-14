// Colour maps and painting a frame into RGBA. Colours come from the design tokens.
import { stretch, stretchSigned, type Limits, type StretchKind } from "./math";

export type RGB = [number, number, number];

export interface Palette {
  /** Faint end of the plain map (the sky). */
  plate: RGB;
  /** Bright end (stars). */
  emulsion: RGB;
  /** Difference map: negative (where light was), positive (where light is now). */
  neg: RGB;
  pos: RGB;
  /** No data: a two-tone checker, so it never reads as dark sky. */
  nodataA: RGB;
  nodataB: RGB;
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
    plate: v("--plate", [15, 26, 44]),
    emulsion: v("--emulsion", [233, 228, 214]),
    neg: v("--d2", [75, 159, 242]),
    pos: v("--mover", [255, 155, 61]),
    nodataA: v("--rule-strong", [52, 69, 106]),
    nodataB: v("--plate-3", [27, 44, 72]),
  };
}

const lerp = (a: RGB, b: RGB, t: number): RGB => [
  Math.round(a[0] + (b[0] - a[0]) * t),
  Math.round(a[1] + (b[1] - a[1]) * t),
  Math.round(a[2] + (b[2] - a[2]) * t),
];

/** 256-entry RGB lookup for 0–1 (plain) or −1…+1 (signed, entry 128 = zero). */
export function buildLut(p: Palette, signed: boolean, invert: boolean): Uint8ClampedArray {
  const lut = new Uint8ClampedArray(256 * 3);
  for (let i = 0; i < 256; i++) {
    let c: RGB;
    if (signed) {
      const s = (i - 128) / 127;
      const t = Math.min(1, Math.abs(s));
      const zero = invert ? p.emulsion : p.plate;
      c = lerp(zero, s < 0 ? p.neg : p.pos, t);
    } else {
      const t = invert ? 1 - i / 255 : i / 255;
      c = lerp(p.plate, p.emulsion, t);
    }
    lut.set(c, i * 3);
  }
  return lut;
}

/** Paint values (row 0 = north, col 0 = east) into RGBA. */
export function paint(
  values: Float32Array,
  nx: number,
  out: Uint8ClampedArray,
  lim: Limits,
  kind: StretchKind,
  signed: boolean,
  lut: Uint8ClampedArray,
  p: Palette,
): void {
  for (let i = 0; i < values.length; i++) {
    const v = values[i];
    const o = i * 4;
    if (Number.isNaN(v)) {
      const x = i % nx;
      const y = (i - x) / nx;
      const c = (x + y) & 1 ? p.nodataA : p.nodataB;
      out[o] = c[0];
      out[o + 1] = c[1];
      out[o + 2] = c[2];
    } else {
      const k = signed
        ? Math.round(128 + stretchSigned(v, lim, kind) * 127)
        : Math.round(stretch(v, lim, kind) * 255);
      out[o] = lut[k * 3];
      out[o + 1] = lut[k * 3 + 1];
      out[o + 2] = lut[k * 3 + 2];
    }
    out[o + 3] = 255;
  }
}
