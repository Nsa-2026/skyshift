// The colour key next to the main image. Every swatch is drawn by the same algorithm as the picture
// (rgb.ts compose), from what that kind of object looks like in the picture's three colours, so the
// key can't drift from the picture. There is one key per colour set: the still pictures use the
// all-sky map's windows (J, H, K, 4.6 µm), the moving ones the blink's detectors (D1–D3, D6).
//
// Typical colours (Vega mags, J = 0; approximate, ±0.2–0.3 mag): A0 star 0 everywhere; Sun-like
// (G2) Y−J 0.2, J−H 0.30, J−K 0.36, J−W2 0.40; red dwarf (M4) 0.45, 0.60, 0.85, 1.05; L5 dwarf
// 1.0, 1.0, 1.7, 2.6; T1 1.0, 0.8, 1.1, 2.4; T6 1.1, −0.3, −0.1, 3.0; Y0 0.5, −0.3, −0.5, 6.0.
// Vega → AB: Y +0.634, J +0.91, H +1.39, K +1.85, W2 +3.34. A detector's colour is the mean of the
// bands it spans: D1 ≈ Y, D2 ≈ (J + H)/2, D3 ≈ (H + K)/2, D6 ≈ W2. A named example must look like
// its swatch on its own plate: Barnard's Star (saturated) and WISE 0855 (blended in the still
// picture) don't, so they aren't named.
import type { PresetId } from "./bands";
import { compose, type Channel } from "./rgb";

type Mags = Record<"Y" | "J" | "H" | "K" | "W2", number>;
const TYPES: Record<"A0" | "G2" | "M4" | "L5" | "T1" | "T6" | "Y0", Mags> = {
  A0: { Y: 0, J: 0, H: 0, K: 0, W2: 0 },
  G2: { Y: 0.2, J: 0, H: -0.3, K: -0.36, W2: -0.4 },
  M4: { Y: 0.45, J: 0, H: -0.6, K: -0.85, W2: -1.05 },
  L5: { Y: 1.0, J: 0, H: -1.0, K: -1.7, W2: -2.6 },
  T1: { Y: 1.0, J: 0, H: -0.8, K: -1.1, W2: -2.4 },
  T6: { Y: 1.1, J: 0, H: 0.3, K: 0.1, W2: -3.0 },
  Y0: { Y: 0.5, J: 0, H: 0.3, K: 0.5, W2: -6.0 },
};
type TypeId = keyof typeof TYPES;
const AB: Mags = { Y: 0.634, J: 0.91, H: 1.39, K: 1.85, W2: 3.34 };
/** Brightness per unit frequency relative to J. */
const flux = (t: Mags, b: keyof Mags) => 10 ** (-0.4 * (t[b] + AB[b] - AB.J));
type Band = "J" | "H" | "K" | "W2" | "D1" | "D2" | "D3" | "D6";
const BAND: Record<Band, (t: Mags) => number> = {
  J: (t) => flux(t, "J"),
  H: (t) => flux(t, "H"),
  K: (t) => flux(t, "K"),
  W2: (t) => flux(t, "W2"),
  D1: (t) => flux(t, "Y"),
  D2: (t) => (flux(t, "J") + flux(t, "H")) / 2,
  D3: (t) => (flux(t, "H") + flux(t, "K")) / 2,
  D6: (t) => flux(t, "W2"),
};

export type KeySet = "still-cold" | "still-nir" | "visit-cold" | "visit-nir";
const BANDS: Record<KeySet, [Band, Band, Band]> = {
  "still-cold": ["W2", "K", "J"],
  "still-nir": ["K", "H", "J"],
  "visit-cold": ["D6", "D3", "D2"],
  "visit-nir": ["D3", "D2", "D1"],
};

export interface KeyEntry {
  /** Ratios R : G : B per unit frequency. null for the special swatches (grey, no data, saturated). */
  rgb: [number, number, number] | null;
  swatch: "colour" | "grey" | "none" | "saturated";
  label: string;
  examples: string;
}

const COMMON: KeyEntry[] = [
  { rgb: null, swatch: "grey", label: "Grey", examples: "too faint to tell the colour" },
  { rgb: null, swatch: "none", label: "Striped", examples: "no data here" },
  { rgb: null, swatch: "saturated", label: "Red squares", examples: "too bright to measure, so the colour there is wrong" },
];

function entries(set: KeySet, rows: [TypeId, string, string][]): KeyEntry[] {
  const [r, g, b] = BANDS[set];
  return [
    ...rows.map(([t, label, examples]): KeyEntry => ({
      rgb: [BAND[r](TYPES[t]), BAND[g](TYPES[t]), BAND[b](TYPES[t])],
      swatch: "colour",
      label,
      examples,
    })),
    ...COMMON,
  ];
}

// Colour names follow the swatches each set really draws (checked in bands.test.ts).
const COLD: [TypeId, string, string][] = [
  ["G2", "Blue", "ordinary stars, like the Sun"],
  ["M4", "Light blue", "small, cool red stars"],
  ["L5", "Green", "warm brown dwarfs (\"failed stars\")"],
  ["T1", "White", "cooler brown dwarfs, such as Epsilon Indi B"],
  ["T6", "Pink", "colder brown dwarfs: methane gas makes them shine most in the reddest light"],
  ["Y0", "Red", "seen only in the reddest light: the coldest brown dwarfs, dusty galaxies, or stars hidden behind dust"],
];

export const KEY_SETS: Record<KeySet, KeyEntry[]> = {
  "still-cold": entries("still-cold", COLD),
  "visit-cold": entries("visit-cold", COLD),
  "still-nir": entries("still-nir", [
    ["A0", "Blue", "hot stars, and also cold brown dwarfs, which look the same here (use the Temperature filter to tell them apart)"],
    ["G2", "Pale blue", "stars like the Sun"],
    ["M4", "White", "small, cool red stars"],
    ["L5", "Orange", "warm brown dwarfs, and stars reddened by dust"],
  ]),
  "visit-nir": entries("visit-nir", [
    ["A0", "Blue", "hot stars"],
    ["G2", "Pale blue", "stars like the Sun"],
    ["M4", "Cream", "small, cool red stars"],
    ["L5", "Orange", "warm brown dwarfs, and stars reddened by dust"],
    ["T6", "Green", "brown dwarfs with methane gas (use the Temperature filter to see the coldest ones)"],
  ]),
};

/** The still pictures' keys by filter, as before (index.ts reads KEYS[mode]). */
export const KEYS: Record<PresetId, KeyEntry[]> = { cold: KEY_SETS["still-cold"], nir: KEY_SETS["still-nir"] };

export function keySet(moving: boolean, preset: PresetId): KeySet {
  return `${moving ? "visit" : "still"}-${preset}` as KeySet;
}

/** CSS colour of a swatch, drawn by the picture's own algorithm at high signal-to-noise. */
export function swatchColour(rgb: [number, number, number]): string {
  const ch = (v: number): Channel => ({ values: Float32Array.from([v * 1000]), stats: { median: 0, sigma: 1, n: 1 } });
  const c = compose(ch(rgb[0]), ch(rgb[1]), ch(rgb[2]));
  return `rgb(${c.rgba[0]}, ${c.rgba[1]}, ${c.rgba[2]})`;
}
