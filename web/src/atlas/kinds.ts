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
    meaning: "Moves within hours. Could be an asteroid or comet no one has catalogued.",
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

/** Pipeline reason codes (contracts/dots.md) as short phrases. Unknown codes fall back to the code in words. */
const REASON_TEXT: Record<string, string> = {
  two_exposures: "Seen in two exposures",
  two_detectors: "Seen in two detectors at once",
  gaia_pm_disagrees: "Gaia's motion differs",
  catwise_pm_disagrees: "CatWISE's motion differs",
  few_dots: "Few detections",
  bad_fit: "Poor straight-line fit",
  short_span: "Short time span",
  over_pm_cap: "Faster than any known star",
  at_control_level: "Could be chance",
};

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

/** "194 candidates in 1 zone", "1 candidate in 2 zones". */
export function countSentence(n: number, zones: number): string {
  const c = `${new Intl.NumberFormat("en-GB").format(n)} candidate${n === 1 ? "" : "s"}`;
  return zones > 0 ? `${c} in ${zones} zone${zones === 1 ? "" : "s"}` : c;
}

export function plural(n: number, one: string, many: string): string {
  return `${new Intl.NumberFormat("en-GB").format(n)} ${n === 1 ? one : many}`;
}
