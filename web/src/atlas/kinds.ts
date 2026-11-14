// The three kinds of candidate in plain words (web/DESIGN.md "Candidates"), the reason codes as
// short phrases, and the one number that matters for each kind, with units. Pure.
import type { Candidate, CandidateKind } from "../api/types";
import { DETECTORS, formatMjd } from "../app/astro";

export const KINDS: readonly CandidateKind[] = ["mover", "high_pm", "spectral"];

export interface KindText {
  name: string;
  plural: string;
  /** One letter in the URL (#/sky/…,mp). */
  code: string;
  /** One sentence: what it could be. */
  meaning: string;
}

export const KIND_TEXT: Record<CandidateKind, KindText> = {
  mover: {
    name: "Fast mover",
    plural: "Fast movers",
    code: "m",
    meaning:
      "Seems to move within hours. Could be an asteroid or comet no one has catalogued. Most fast movers are seen in only two exposures of one session, which can't show motion by itself: blink it and look for a steady step.",
  },
  high_pm: {
    name: "Moving star",
    plural: "Moving stars",
    code: "p",
    meaning: "Moves between visits six months apart. Could be a nearby star or brown dwarf.",
  },
  spectral: {
    name: "Odd spectrum",
    plural: "Odd spectra",
    code: "s",
    meaning: "Its colours or molecule bands stand out: methane, water ice or CO₂.",
  },
};

export function isKind(k: unknown): k is CandidateKind {
  return k === "mover" || k === "high_pm" || k === "spectral";
}

/**
 * Reason codes (contracts/api.md, candidates.json "reasons") as short phrases. Unknown codes fall
 * back to the code in words.
 */
const REASON_TEXT: Record<string, string> = {
  // mover
  two_exposures: "Seen in two exposures",
  two_detectors: "Seen in two detectors at once",
  known_object_near: "A known asteroid is predicted near",
  motion_not_significant: "Motion not significant",
  bad_pixels_near: "Bad pixels nearby",
  outlier_flag: "Outlier pixels",
  detector_excess: "Detector with many artefacts",
  repeat_position: "Seen here before",
  gaia_unchecked: "Gaia not checked",
  catwise_unchecked: "CatWISE not checked",
  // high_pm
  pm_fit: "Motion fitted over every visit",
  pm_pair: "Paired across two visits",
  vetted: "Passed every check",
  catwise_pm_agrees: "CatWISE's motion agrees",
  short_span: "Short time span",
  few_dots: "Few detections",
  bad_fit: "Poor straight-line fit",
  over_pm_cap: "Faster than any known star",
  gaia_pm_disagrees: "Gaia's motion differs",
  catwise_pm_disagrees: "CatWISE's motion differs",
  at_control_level: "Could be chance",
  implausible_pm: "Too fast to be one star",
  // spectral
  no_gaia: "Not in Gaia",
  gaia_nearby: "Gaia: within 50 pc",
  class_star: "Looks like a star",
  class_galaxy: "Looks like a galaxy",
  class_unsure: "Star or galaxy unclear",
  variable: "Brightness changes",
};

/** The "check first" codes of the contract: shown as cautions, not as evidence. */
const CAUTION_CODES = new Set([
  "known_object_near",
  "motion_not_significant",
  "bad_pixels_near",
  "outlier_flag",
  "detector_excess",
  "repeat_position",
  "gaia_unchecked",
  "catwise_unchecked",
  "short_span",
  "few_dots",
  "bad_fit",
  "over_pm_cap",
  "gaia_pm_disagrees",
  "catwise_pm_disagrees",
  "at_control_level",
  "implausible_pm",
]);

export function isCaution(code: string): boolean {
  return CAUTION_CODES.has(code);
}

/**
 * A "moving star" faster than any star can move (`implausible_pm`): almost certainly two different
 * sources paired by mistake (the export scores them × 0.01). The map leaves them out unless asked.
 */
export function isLikelyMismatch(c: Pick<Candidate, "reasons">): boolean {
  return c.reasons.includes("implausible_pm");
}

export function reasonText(code: string): string {
  if (REASON_TEXT[code]) return REASON_TEXT[code];
  const words = code.replace(/[_-]+/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

const LABEL_TEXT: Record<string, string> = {
  red: "Red",
  ch4: "Methane",
  h2o_ice: "Water ice",
  co2_ice: "CO₂ ice",
  co_ice: "CO ice",
  h2o_vapour: "Water vapour",
  co_23: "CO at 2.3 µm",
  pah_33: "PAH at 3.3 µm",
  brown_dwarf_like: "Brown-dwarf colours",
};

export function labelText(code: string): string {
  return LABEL_TEXT[code] ?? reasonText(code);
}

const FLAG_TEXT: Record<string, string> = {
  GHOST: "a ghost reflection",
  GHOST_FPA: "a ghost reflection",
  GHOST_EXT: "a ghost reflection",
  CROSSTALK: "crosstalk from a bright star",
  HALO: "a bright star's halo",
  BLOOM: "a bright star's bloom",
  OUTLIER: "an outlier pixel",
  PERSIST: "persistence from an earlier exposure",
};

/** Artefact flags as one plain sentence, or null when there are none. */
export function flagsSentence(flags: readonly string[]): string | null {
  if (!flags.length) return null;
  const words = [...new Set(flags.map((f) => FLAG_TEXT[f.toUpperCase()] ?? f.toLowerCase().replace(/_/g, " ")))];
  const list = words.length === 1 ? words[0] : `${words.slice(0, -1).join(", ")} and ${words[words.length - 1]}`;
  return `Careful: it sits on ${list}.`;
}

const nf = (digits: number) => new Intl.NumberFormat("en-GB", { maximumFractionDigits: digits, minimumFractionDigits: digits });

/** The one number that matters for the candidate's kind, with its unit and a short gloss. */
export interface KeyNumber {
  value: string;
  unit: string;
  /** Short phrase after the number, e.g. "± 0.06″/yr, toward 151°". */
  detail: string;
}

export function keyNumber(c: Candidate): KeyNumber | null {
  if (c.kind === "high_pm" && c.pm_mas_yr != null && Number.isFinite(c.pm_mas_yr)) {
    const v = c.pm_mas_yr / 1000;
    const digits = v >= 10 ? 1 : 2;
    const parts: string[] = [];
    if (c.pm_err_mas_yr != null) parts.push(`± ${nf(digits).format(c.pm_err_mas_yr / 1000)}″/yr`);
    if (c.pa_deg != null) parts.push(`toward ${Math.round(c.pa_deg)}° east of north`);
    return { value: nf(digits).format(v), unit: "″/yr", detail: parts.join(", ") };
  }
  if (c.kind === "mover" && c.rate_arcsec_h != null && Number.isFinite(c.rate_arcsec_h)) {
    const digits = c.rate_arcsec_h >= 100 ? 0 : 1;
    const parts: string[] = [];
    if (c.pa_deg != null) parts.push(`toward ${Math.round(c.pa_deg)}° east of north`);
    return { value: nf(digits).format(c.rate_arcsec_h), unit: "″/h", detail: parts.join(", ") };
  }
  if (c.flux_ujy != null && Number.isFinite(c.flux_ujy)) {
    const digits = c.flux_ujy >= 100 ? 0 : 1;
    const parts: string[] = [];
    if (c.snr != null && Number.isFinite(c.snr)) parts.push(`signal-to-noise ${Math.round(c.snr)}`);
    return { value: nf(digits).format(c.flux_ujy), unit: "µJy", detail: parts.join(", ") };
  }
  if (c.score != null) return { value: nf(2).format(c.score), unit: "", detail: "pipeline score" };
  return null;
}

/** One line for the hover tooltip and the list of signals in view. */
export function oneLine(c: Candidate): string {
  const k = keyNumber(c);
  const name = KIND_TEXT[c.kind]?.name ?? "Candidate";
  if (!k) return name;
  const unit = k.unit ? (k.unit.startsWith("″") ? k.unit : ` ${k.unit}`) : "";
  const pa = c.pa_deg != null && c.kind !== "spectral" ? `, toward ${Math.round(c.pa_deg)}°` : "";
  return `${name}, ${k.value}${unit}${pa}`;
}

export function detectorText(det: number): string {
  const d = DETECTORS[det as 1 | 2 | 3 | 4 | 5 | 6];
  return d ? `D${det}, ${d.min}–${d.max} µm` : `D${det}`;
}

export function seenText(c: Pick<Candidate, "mjd" | "clock">): string {
  return Number.isFinite(c.mjd) ? formatMjd(c.mjd, c.clock === "hours") : "Unknown date";
}

export function zoneName(zone: string): string {
  const z = zone.replace(/[_-]+/g, " ").trim();
  return `${z.charAt(0).toUpperCase()}${z.slice(1)} zone`;
}

/** A count with thousands separators: "1,008". */
export function countText(n: number): string {
  return new Intl.NumberFormat("en-GB").format(n);
}

/** "194 objects to check in 1 zone", "1 object to check in 2 zones". */
export function countSentence(n: number, zones: number): string {
  const c = `${countText(n)} object${n === 1 ? "" : "s"} to check`;
  return zones > 0 ? `${c} in ${zones} zone${zones === 1 ? "" : "s"}` : c;
}

/** What the list is, next to its count: honest about how many turn out to be artefacts. */
export const LIST_NOTE =
  "Things our pipeline couldn't explain with Gaia, WISE or a known asteroid. Most are detector artefacts until people check them.";

export function plural(n: number, one: string, many: string): string {
  return `${new Intl.NumberFormat("en-GB").format(n)} ${n === 1 ? one : many}`;
}

/**
 * A kind chip's tooltip: what the kind is and, when likely mismatches are hidden, why its count is
 * lower than on To check: "… 925 shown; 83 likely mismatches hidden …".
 */
export function kindChipTitle(kind: CandidateKind, shown: number, hidden: number): string {
  const m = KIND_TEXT[kind].meaning;
  if (!(hidden > 0)) return m;
  return `${m} ${countText(shown)} shown; ${plural(hidden, "likely mismatch", "likely mismatches")} hidden: too fast to be one star. Turn on Likely mismatches under Adjust to show them.`;
}

/**
 * The whole-sky caption: what the map shows, and, when likely mismatches are hidden, why that's
 * fewer than the total in the intro: "1,250 shown; 83 likely mismatches hidden."
 */
export function shownSentence(shown: number, hidden: number, zones: number): string {
  if (!(hidden > 0)) return `${countSentence(shown, zones)}.`;
  return `${new Intl.NumberFormat("en-GB").format(shown)} shown; ${plural(hidden, "likely mismatch", "likely mismatches")} hidden.`;
}
