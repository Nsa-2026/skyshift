// Wavelength windows and spectral features, shared by the colour view (A2) and the sky view's
// wavelength slider (A1). Windows match the pipeline's MAG_WINDOWS and INDEX_WINDOWS
// (pipeline/src/skyshift_pipeline/config.py), so the site and the pipeline mean the same thing.
import { CUBE_UM } from "../sky/layers";
import { BLANK_FRAMES } from "./cube";

export interface Window {
  lo: number;
  hi: number;
  /** Short name for the legend, e.g. "J". */
  name: string;
  /** Plain words: "1.2 micrometres". */
  plain: string;
}

/** 0-based cube frames whose centre wavelength lies inside the window (blank frames left out). */
export function framesIn(w: Pick<Window, "lo" | "hi">): number[] {
  const out: number[] = [];
  CUBE_UM.forEach((um, i) => {
    if (um >= w.lo && um <= w.hi && !BLANK_FRAMES.has(i)) out.push(i);
  });
  return out;
}

export type PresetId = "cold" | "nir";

export interface Preset {
  id: PresetId;
  label: string;
  red: Window;
  green: Window;
  blue: Window;
  /** What the colours mean, one sentence per hue. */
  reads: string[];
}

// Pipeline MAG_WINDOWS: j 1.15–1.33, h 1.50–1.78, k 2.00–2.30, m46 4.40–4.80 µm.
const J: Window = { lo: 1.15, hi: 1.33, name: "J 1.15–1.33 µm", plain: "1.2 micrometres" };
const H: Window = { lo: 1.5, hi: 1.78, name: "H 1.50–1.78 µm", plain: "1.6 micrometres" };
const K: Window = { lo: 2.0, hi: 2.3, name: "K 2.00–2.30 µm", plain: "2.2 micrometres" };
const M46: Window = { lo: 4.4, hi: 4.8, name: "4.40–4.80 µm", plain: "4.6 micrometres" };

export const PRESETS: readonly Preset[] = [
  {
    id: "cold",
    label: "Find cold objects",
    red: M46,
    green: K,
    blue: J,
    reads: [
      "Ordinary stars look blue: they are much fainter at 4.6 µm than at 1.2 µm.",
      "Red means brighter at 4.6 µm: cold brown dwarfs, stars behind thick dust, or warm dust. The pipeline calls a source red when J − [4.6] > −0.5 AB.",
    ],
  },
  {
    id: "nir",
    label: "Compare with 2MASS",
    red: K,
    green: H,
    blue: J,
    reads: [
      "The same three bands as the 2MASS layer, so the two can be compared.",
      "Hot and Sun-like stars look blue-white, red dwarfs white; dust behind or around a star makes it orange.",
    ],
  },
];

/** Molecular features SPHEREx can see (pipeline INDEX_WINDOWS), for the slider's ticks. */
export interface Feature {
  um: number;
  lo: number;
  hi: number;
  name: string;
  what: string;
}

// Plain words for visitors; the science behind each is in pipeline/CLAUDE.md ("Spectral science").
export const FEATURES: readonly Feature[] = [
  { um: 1.4, lo: 1.34, hi: 1.45, name: "water vapour", what: "cool red stars and brown dwarfs look darker here" },
  { um: 1.67, lo: 1.63, hi: 1.72, name: "methane gas", what: "cold brown dwarfs look darker here" },
  { um: 1.9, lo: 1.84, hi: 1.95, name: "water vapour", what: "cool red stars look darker here" },
  { um: 2.35, lo: 2.29, hi: 2.4, name: "carbon monoxide gas", what: "giant red stars look darker here" },
  { um: 3.05, lo: 2.95, hi: 3.15, name: "water ice", what: "stars behind icy dark clouds look darker here" },
  { um: 3.3, lo: 3.2, hi: 3.45, name: "methane gas, and sooty dust", what: "the coldest brown dwarfs go dark; galaxies making new stars glow" },
  { um: 4.27, lo: 4.23, hi: 4.31, name: "carbon dioxide", what: "icy clouds look darker; some comets glow" },
  { um: 4.67, lo: 4.64, hi: 4.7, name: "carbon monoxide", what: "icy clouds and cool stars look darker here" },
];

/** The feature whose window contains this wavelength, if any. */
export function featureAt(um: number): Feature | null {
  return FEATURES.find((f) => um >= f.lo && um <= f.hi) ?? null;
}

/** Detector (1–6) and channel (1–17) of a 0-based cube frame. */
export function detChannel(frame: number): { det: number; channel: number } {
  return { det: Math.floor(frame / 17) + 1, channel: (frame % 17) + 1 };
}

/** Plain words for one cube frame: the wavelength, and what absorbs or glows there, if anything. */
export function frameText(frame: number): string {
  const um = CUBE_UM[frame];
  const f = featureAt(um);
  return `${um.toFixed(2)} micrometres${f ? `: ${f.name}. ${f.what.charAt(0).toUpperCase()}${f.what.slice(1)}.` : ""}`;
}
