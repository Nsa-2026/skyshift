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
  mover: { name: "Fast mover", plural: "Fast movers", gist: "Seems to move within hours: maybe an uncatalogued asteroid or comet. Most fast movers are seen in only two exposures of one session, which can't show motion by itself: blink it and look for a steady step." },
  high_pm: { name: "Moving star", plural: "Moving stars", gist: "Moves between visits: maybe a nearby star or brown dwarf." },
  spectral: { name: "Odd spectrum", plural: "Odd spectra", gist: "Molecule bands or colours worth a look." },
};

/**
 * Why a candidate is listed (support), why the pipeline is unsure of it (doubt), or a sign it may
 * be an artefact (caution: outlier pixels, the same spot seen days apart). Cautions count with the
 * artefact flags: "hide artefact cautions" and "Check first" use both.
 */
export interface ReasonText {
  label: string;
  tone: "support" | "doubt" | "caution";
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
  outlier: { label: "Outlier pixels", tone: "caution", detail: "The pipeline marked pixels here as outliers: often a cosmic ray or a bad pixel." },
  repeat_position: { label: "Same spot days apart", tone: "caution", detail: "Something sits at nearly this spot in sessions days apart: a fixed artefact, not a mover." },
};

/** Codes the exporter may use for the same two cautions (exact names still settling). */
const CAUTION_CODE = /outlier|repeat|same_?(spot|pos)|near_?fixed|fixed_?pos|stationary|persist|ghost|halo|crosstalk|bloom|artefact|artifact/i;

export function reasonText(code: string): ReasonText {
  const known = REASON_TEXT[code] ?? REASON_TEXT[code.toLowerCase()];
  if (known) return known;
  if (/repeat|same_?(spot|pos)|near_?fixed|fixed_?pos|stationary/i.test(code)) return { ...REASON_TEXT.repeat_position, detail: code };
  if (CAUTION_CODE.test(code)) return { label: flagText(code), tone: "caution", detail: code };
  // Unknown codes from a newer exporter: treat as a doubt, so "Most promising" and the pipeline-score
  // order stay conservative.
  return { label: humanize(code), tone: "doubt", detail: code };
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
  persist: "Persistence nearby",
  repeat_position: "Same spot days apart",
};

export const flagText = (code: string): string =>
  FLAG_TEXT[code.toLowerCase()] ?? (/repeat|same_?(spot|pos)|near_?fixed|fixed_?pos|stationary/i.test(code) ? FLAG_TEXT.repeat_position : humanize(code.toLowerCase()));

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
