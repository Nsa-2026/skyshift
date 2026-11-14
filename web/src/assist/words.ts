// Plain words for every code a candidate carries: kinds, pipeline reason codes, FLAGS bit names,
// labels, and the checklist's statuses. One table each, so chips, the checklist and the drafts all
// say the same thing. Sources: contracts/flags.md (bits and policy), contracts/dots.md and
// pipeline/src/skyshift_pipeline/highpm.py (reasons), web/DESIGN.md (kinds, labels).
import type { CandidateKind } from "../api/types";
import type { Reply, StepStatus } from "./types";

/** A reason's weight for "this is real and new": good, caution or bad. */
export type Tone = "good" | "caution" | "bad";

export interface Words {
  /** Chip label, sentence case, a few words. */
  short: string;
  /** One plain sentence. */
  plain: string;
}

export const KIND_TEXT: Record<CandidateKind, Words> = {
  mover: { short: "Fast mover", plain: "Moves within hours: it could be an uncatalogued asteroid or comet." },
  high_pm: { short: "Moving star", plain: "Moves between visits: it could be a nearby star or brown dwarf." },
  spectral: { short: "Odd spectrum", plain: "Its colours or molecule bands are worth a look." },
};

/** Pipeline reason codes (Candidate.reasons). */
export const REASON_TEXT: Record<string, Words & { tone: Tone }> = {
  two_exposures: {
    short: "Two exposures",
    plain: "Seen in two exposures of one session at different places.",
    tone: "good",
  },
  two_detectors: {
    short: "Two detectors",
    plain: "Seen at the same moment in two detectors, so it's real light, not a detector flaw.",
    tone: "good",
  },
  gaia_pm_disagrees: {
    short: "Gaia motion differs",
    plain: "Gaia lists a star at this spot with a different motion, so our motion may be off.",
    tone: "caution",
  },
  catwise_pm_disagrees: {
    short: "CatWISE motion differs",
    plain: "CatWISE lists a source at this spot with a different motion, so our motion may be off.",
    tone: "caution",
  },
  bad_fit: {
    short: "Poor motion fit",
    plain: "Its positions scatter more than a steady motion allows (χ²/ν above 5).",
    tone: "caution",
  },
  few_dots: {
    short: "Few detections",
    plain: "The motion rests on fewer than 20 detections.",
    tone: "caution",
  },
  over_pm_cap: {
    short: "Too fast for a star",
    plain: "It moves faster than 10.5″ a year, quicker than any known star, so two different sources were probably paired.",
    tone: "bad",
  },
  at_control_level: {
    short: "No better than chance",
    plain: "Pairs this far apart turn up just as often by chance in the control fields.",
    tone: "caution",
  },
  short_span: {
    short: "Short time span",
    plain: "Its visits are less than 0.25 yr (3 months) apart, too close to measure a slow motion.",
    tone: "caution",
  },
};

/** What we do with a FLAGS bit (contracts/flags.md): mask = can't be trusted; record = a reason for care. */
export type FlagPolicy = "mask" | "record" | "never";

/** FLAGS bit names (contracts/flags.md; meanings from the SPHEREx Explanatory Supplement v2.1, Table 16),
 *  plus "EDGE" for a position near the photo's edge. */
export const FLAG_TEXT: Record<string, Words & { policy: FlagPolicy }> = {
  TRANSIENT: { short: "Cosmic-ray hit", plain: "a one-off spike in the readout, such as a cosmic-ray hit", policy: "mask" },
  OVERFLOW: { short: "Saturated", plain: "saturated pixels, too bright to measure", policy: "mask" },
  SUR_ERROR: { short: "Readout error", plain: "pixels whose on-board readout fit failed", policy: "mask" },
  NONFUNC: { short: "Dead pixels", plain: "pixels that never work", policy: "mask" },
  DICHROIC: { short: "Dark corner", plain: "the dark corners of detectors 3 and 4, where too little light gets through to calibrate", policy: "mask" },
  MISSING_DATA: { short: "Missing data", plain: "pixels whose data was lost on the way down from the spacecraft", policy: "mask" },
  HOT: { short: "Hot pixel", plain: "hot, noisy pixels", policy: "mask" },
  COLD: { short: "Cold pixel", plain: "cold pixels that hardly respond to light", policy: "mask" },
  PHANMISS: { short: "Uncorrected", plain: "pixels left uncorrected because their phantom pixels were missing", policy: "mask" },
  NONLINEAR: { short: "Near saturation", plain: "pixels without a reliable non-linearity correction, usually near saturation", policy: "mask" },
  PERSIST: { short: "Persistence", plain: "persistence, a fading after-image of a bright source from an earlier photo", policy: "mask" },
  STREAK: { short: "Streak", plain: "a streak, most likely from a satellite in Earth orbit", policy: "mask" },
  SNOWBALL: { short: "Snowball", plain: "a snowball, a round burst of charge on the detector", policy: "mask" },
  OUTLIER: { short: "Outlier", plain: "pixels the pipeline's outlier check flagged", policy: "record" },
  CROSSTALK: { short: "Crosstalk", plain: "crosstalk, an electronic echo of a bright source on the same detector", policy: "record" },
  GHOST: { short: "Ghost", plain: "an optical ghost of a bright source in the same photo", policy: "record" },
  GHOST_FPA: { short: "Ghost", plain: "an optical ghost of a bright source seen on another detector", policy: "record" },
  GHOST_EXT: { short: "Ghost", plain: "an optical ghost of a bright source outside the field of view", policy: "record" },
  BLOOM: { short: "Bloom", plain: "blooming, charge spilling out of a bright source", policy: "record" },
  HALO: { short: "Halo", plain: "the halo of a cosmic-ray hit or other transient", policy: "record" },
  SATELLITE_HALO: { short: "Satellite halo", plain: "the halo of a satellite streak", policy: "record" },
  EDGE: { short: "Edge", plain: "the edge of the photo, where part of the light falls off", policy: "record" },
  SOURCE: { short: "Catalogued source", plain: "a known source", policy: "never" },
  FULLSAMPLE: { short: "Full sample", plain: "pixels with their full readout kept (information only)", policy: "never" },
};

/** Spectrum and colour labels (Candidate.labels). */
export const LABEL_TEXT: Record<string, Words> = {
  red: { short: "Red", plain: "much brighter at 4–5 µm than at 1–1.6 µm, as cold brown dwarfs are" },
  ch4: { short: "Methane", plain: "a methane dip near 3.3 µm, as in T and Y dwarfs" },
  h2o_ice: { short: "Water ice", plain: "a water-ice dip near 3.05 µm" },
  co2_ice: { short: "CO₂ ice", plain: "a carbon-dioxide-ice dip near 4.27 µm" },
  co_ice: { short: "CO ice", plain: "a carbon-monoxide-ice dip near 4.67 µm" },
  h2o_vapour: { short: "Water vapour", plain: "water-vapour dips near 1.4 and 1.9 µm, as in cool stars and brown dwarfs" },
  co: { short: "CO", plain: "a carbon-monoxide dip near 2.3 µm, as in cool stars" },
  pah: { short: "PAH", plain: "a bump near 3.3 µm from PAH molecules, as in star-forming galaxies" },
  brown_dwarf_like: { short: "Brown-dwarf-like", plain: "colours like a brown dwarf's" },
};

/** Labels that point towards a cold, nearby brown dwarf. */
export const COLD_LABELS = new Set(["red", "ch4", "h2o_vapour", "brown_dwarf_like"]);

export const STATUS_TEXT: Record<StepStatus, string> = {
  pass: "Good sign",
  caution: "Caution",
  fail: "Bad sign",
  unknown: "Unknown",
  ask: "Your check",
};

export const REPLY_TEXT: Record<Reply, string> = {
  yes: "Yes",
  no: "No",
  unsure: "Can't tell",
};

/** "gaia_pm_disagrees" → "gaia pm disagrees": readable fallback for codes no table knows yet. */
export function humanize(code: string): string {
  return code.replace(/[_-]+/g, " ").trim();
}

/** A reason in plain words; unknown codes are shown as they are, softened, with tone "caution". */
export function reasonWords(code: string): Words & { tone: Tone } {
  return REASON_TEXT[code] ?? { short: cap(humanize(code)), plain: `Pipeline note: ${humanize(code)}.`, tone: "caution" };
}

/** A FLAGS bit name in plain words, any case ("ghost", "GHOST"). Unknown names count as "record". */
export function flagWords(name: string): Words & { policy: FlagPolicy } {
  const key = name.trim().toUpperCase().replace(/[\s-]+/g, "_");
  return FLAG_TEXT[key] ?? { short: cap(humanize(name.toLowerCase())), plain: humanize(name.toLowerCase()), policy: "record" };
}

/** A label in plain words; unknown labels fall back to the code, readable. */
export function labelWords(code: string): Words {
  return LABEL_TEXT[code] ?? { short: cap(humanize(code)), plain: humanize(code) };
}

export function cap(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** "a, b and c". */
export function listWords(items: string[]): string {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}
