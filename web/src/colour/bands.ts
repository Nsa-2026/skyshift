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
}

/** 0-based cube frames whose centre wavelength lies inside the window (blank frames left out). */
export function framesIn(w: Pick<Window, "lo" | "hi">): number[] {
  const out: number[] = [];
  CUBE_UM.forEach((um, i) => {
    if (um >= w.lo && um <= w.hi && !BLANK_FRAMES.has(i)) out.push(i);
  });
  return out;
}

export interface Preset {
  id: "cold" | "nir";
  label: string;
  red: Window;
  green: Window;
  blue: Window;
  /** What the colours mean, one sentence per hue. */
  reads: string[];
}

// Pipeline MAG_WINDOWS: j 1.15–1.33, h 1.50–1.78, k 2.00–2.30, m46 4.40–4.80 µm.
const J: Window = { lo: 1.15, hi: 1.33, name: "J 1.15–1.33 µm" };
const H: Window = { lo: 1.5, hi: 1.78, name: "H 1.50–1.78 µm" };
const K: Window = { lo: 2.0, hi: 2.3, name: "K 2.00–2.30 µm" };
const M46: Window = { lo: 4.4, hi: 4.8, name: "4.40–4.80 µm" };

export const PRESETS: readonly Preset[] = [
  {
    id: "cold",
    label: "Cold bodies",
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
    label: "2MASS-like",
    red: K,
    green: H,
    blue: J,
    reads: [
      "The same three bands as the 2MASS layer, so the two can be compared.",
      "Most stars look orange-white here; dust behind or around a star makes it redder.",
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

export const FEATURES: readonly Feature[] = [
  { um: 1.4, lo: 1.34, hi: 1.45, name: "water vapour", what: "M and L dwarfs, pulsating giants" },
  { um: 1.67, lo: 1.63, hi: 1.72, name: "methane", what: "T dwarfs" },
  { um: 1.9, lo: 1.84, hi: 1.95, name: "water vapour", what: "M and L dwarfs" },
  { um: 2.35, lo: 2.29, hi: 2.4, name: "carbon monoxide", what: "cool giant stars" },
  { um: 3.05, lo: 2.95, hi: 3.15, name: "water ice", what: "dark clouds in front of a star" },
  { um: 3.3, lo: 3.2, hi: 3.45, name: "methane / PAH", what: "coldest brown dwarfs (dip) or star-forming dust (bump)" },
  { um: 4.27, lo: 4.23, hi: 4.31, name: "CO₂ ice / gas", what: "icy clouds; comets" },
  { um: 4.67, lo: 4.64, hi: 4.7, name: "CO ice / gas", what: "icy clouds; cool stars" },
];

/** The feature whose window contains this wavelength, if any. */
export function featureAt(um: number): Feature | null {
  return FEATURES.find((f) => um >= f.lo && um <= f.hi) ?? null;
}

/** Detector (1–6) and channel (1–17) of a 0-based cube frame. */
export function detChannel(frame: number): { det: number; channel: number } {
  return { det: Math.floor(frame / 17) + 1, channel: (frame % 17) + 1 };
}
