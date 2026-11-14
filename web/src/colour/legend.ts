// The colour key next to the main image. Every swatch is drawn by the same algorithm as the picture
// (rgb.ts compose), from what that kind of object looks like in these three windows, so the key
// can't drift from the picture.
//
// Brightness ratios per unit frequency (AB), relative to J: X/J = 10^(−0.4 (X_AB − J_AB)), with
// Vega → AB offsets J +0.91, H +1.39, K +1.85, W2 (4.6 µm) +3.34 mag, and typical Vega colours:
// A0 star J−H = J−K = J−W2 = 0; Sun-like (G2) J−H 0.30, J−K 0.36, J−W2 0.40; red dwarf (M4)
// J−H 0.60, J−K 0.85, J−W2 1.05; L5 dwarf J−H 1.0, J−K 1.7; T6 dwarf J−H −0.3, J−K −0.1,
// J−W2 3.0. Epsilon Indi B is measured here (colour view, 14 Nov). Values are typical, ±0.1–0.3 mag.
// A named example must look like its swatch on its own plate: Barnard's Star (saturated) and
// WISE 0855 (blended with a blue neighbour) don't, so they aren't named.
import type { PresetId } from "./bands";
import { compose, type Channel } from "./rgb";

export interface KeyEntry {
  /** Ratios R : G : B per unit frequency. null for the special swatches (grey, no data, saturated). */
  rgb: [number, number, number] | null;
  swatch: "colour" | "grey" | "none" | "saturated";
  label: string;
  examples: string;
}

const ab = (vegaJminusX: number, offX: number) => 10 ** (-0.4 * (-vegaJminusX + (offX - 0.91)));
const J = 1;
const H = (jh: number) => ab(jh, 1.39);
const K = (jk: number) => ab(jk, 1.85);
const W2 = (jw: number) => ab(jw, 3.34);

const COMMON: KeyEntry[] = [
  { rgb: null, swatch: "grey", label: "Grey", examples: "too faint to tell the colour" },
  { rgb: null, swatch: "none", label: "Striped", examples: "no data here" },
  { rgb: null, swatch: "saturated", label: "Red squares", examples: "too bright to measure, so the colour there is wrong" },
];

export const KEYS: Record<PresetId, KeyEntry[]> = {
  cold: [
    { rgb: [W2(0.4), K(0.36), J], swatch: "colour", label: "Blue", examples: "ordinary stars, like the Sun" },
    { rgb: [W2(1.05), K(0.85), J], swatch: "colour", label: "Light blue", examples: "small, cool red stars" },
    { rgb: [1.0, 0.9, 1.0], swatch: "colour", label: "White", examples: "warmer brown dwarfs (\"failed stars\"), such as Epsilon Indi B" },
    { rgb: [W2(3.0), K(-0.1), J], swatch: "colour", label: "Pink", examples: "cooler brown dwarfs: methane gas makes them shine most in the reddest light" },
    { rgb: [1, 0.02, 0.005], swatch: "colour", label: "Red", examples: "seen only in the reddest light: the coldest brown dwarfs, dusty galaxies, or stars hidden behind dust" },
    ...COMMON,
  ],
  nir: [
    { rgb: [K(0), H(0), J], swatch: "colour", label: "Blue", examples: "hot stars, and also cold brown dwarfs, which look the same here (use Find cold objects to tell them apart)" },
    { rgb: [K(0.36), H(0.3), J], swatch: "colour", label: "Pale blue", examples: "stars like the Sun" },
    { rgb: [K(0.85), H(0.6), J], swatch: "colour", label: "White", examples: "small, cool red stars" },
    { rgb: [K(1.7), H(1.0), J], swatch: "colour", label: "Orange", examples: "warm brown dwarfs, and stars reddened by dust" },
    ...COMMON,
  ],
};

/** CSS colour of a swatch, drawn by the picture's own algorithm at high signal-to-noise. */
export function swatchColour(rgb: [number, number, number]): string {
  const ch = (v: number): Channel => ({ values: Float32Array.from([v * 1000]), stats: { median: 0, sigma: 1, n: 1 } });
  const c = compose(ch(rgb[0]), ch(rgb[1]), ch(rgb[2]));
  return `rgb(${c.rgba[0]}, ${c.rgba[1]}, ${c.rgba[2]})`;
}
