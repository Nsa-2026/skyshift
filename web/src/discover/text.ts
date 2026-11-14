// Plain words for the codes the pipeline writes (DESIGN.md "Candidates", "Smart filters").
// Codes stay in title attributes and exports; people read these.
import type { CandidateKind } from "../api/types";

export interface KindText {
  name: string;
  plural: string;
  /** One line: what it is and what it could be. */
  gist: string;
}

export const KINDS: CandidateKind[] = ["mover", "high_pm", "spectral"];

export const KIND_TEXT: Record<CandidateKind, KindText> = {
  mover: { name: "Fast mover", plural: "Fast movers", gist: "Moves within hours: maybe an uncatalogued asteroid or comet." },
  high_pm: { name: "Moving star", plural: "Moving stars", gist: "Moves between visits: maybe a nearby star or brown dwarf." },
  spectral: { name: "Odd spectrum", plural: "Odd spectra", gist: "Molecule bands or colours worth a look." },
};

/** Why a candidate is listed (supports it) or why the pipeline is unsure (a doubt). */
export interface ReasonText {
  label: string;
  tone: "support" | "doubt";
  detail: string;
}

export const REASON_TEXT: Record<string, ReasonText> = {
  two_exposures: { label: "Seen in two exposures", tone: "support", detail: "The same dot sits at two places in one session." },
  two_detectors: { label: "Seen in two detectors at once", tone: "support", detail: "Two detectors exposed at the same moment both caught it, so it's real light." },
  gaia_pm_disagrees: { label: "Gaia lists a different motion", tone: "doubt", detail: "Gaia has a star here that moves differently: the match may be a slower star." },
  catwise_pm_disagrees: { label: "CatWISE lists a different motion", tone: "doubt", detail: "CatWISE has a source here that moves differently." },
  bad_fit: { label: "Motion fit is poor", tone: "doubt", detail: "The straight-line motion fit doesn't match the positions well." },
  few_dots: { label: "Few detections", tone: "doubt", detail: "Fewer than 20 detections went into the motion." },
  at_control_level: { label: "As common as chance pairs", tone: "doubt", detail: "Pairs this far apart turn up as often in a shuffled control." },
  over_pm_cap: { label: "Faster than the motion limit", tone: "doubt", detail: "Faster than any star the fit is trusted for." },
  short_span: { label: "Seen over a short time", tone: "doubt", detail: "Its detections cover too little time to trust the motion." },
};

export function reasonText(code: string): ReasonText {
  return REASON_TEXT[code] ?? { label: humanize(code), tone: "support", detail: code };
}

/** Spectral and colour labels (pipeline `labels`). */
export const LABEL_TEXT: Record<string, string> = {
  red: "Very red",
  ch4: "Methane",
  h2o_ice: "Water ice",
  co2_ice: "CO₂ ice",
  co_ice: "CO ice",
  h2o_vapour: "Water vapour",
  brown_dwarf_like: "Brown-dwarf-like",
  co_23: "CO bandhead",
  pah_33: "PAH glow",
};

export const labelText = (code: string): string => LABEL_TEXT[code] ?? humanize(code);

/** Artefact flags near a candidate (contracts/flags.md, "record on the dot"). */
export const FLAG_TEXT: Record<string, string> = {
  crosstalk: "Crosstalk nearby",
  ghost: "Near a ghost",
  ghost_fpa: "Near a ghost",
  ghost_ext: "Near a ghost",
  bloom: "Bloom nearby",
  halo: "In a bright star's halo",
  outlier: "Outlier pixels",
};

export const flagText = (code: string): string => FLAG_TEXT[code.toLowerCase()] ?? humanize(code);

export type ReviewState = "none" | "some" | "consensus";
export const REVIEW_TEXT: Record<ReviewState, string> = {
  none: "Not reviewed yet",
  some: "In review",
  consensus: "Community verdict",
};

export function humanize(code: string): string {
  const s = code.replace(/[_-]+/g, " ").trim();
  return s ? s[0].toUpperCase() + s.slice(1) : code;
}

/** Zones as a phrase: "the pilot zone", "the pilot and hunt zones". */
export function zonesPhrase(zones: string[]): string {
  if (!zones.length) return "the survey";
  if (zones.length === 1) return `the ${zones[0]} zone`;
  return `the ${zones.slice(0, -1).join(", ")} and ${zones[zones.length - 1]} zones`;
}
