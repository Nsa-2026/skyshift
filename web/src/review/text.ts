// Plain words and numbers for candidates: kind names and icons, reason chips, caution footnotes,
// measurements with units. Pure (strings only), so the pages and the tests share one wording.
import type { Candidate, CandidateKind } from "../api/types";
import { DETECTORS, formatMjd } from "../app/astro";
import { paWord } from "../card/format";
import { ANSWER_TEXT, type Answer, type Counts } from "../vote/model";
import { CONSENSUS_MIN_VOTES, CONSENSUS_PERCENT, consensus, type Consensus } from "./model";

export const SITE = "https://skyshift.web.app";
export const FORUM_URL = "https://www.zooniverse.org/projects/marckuchner/backyard-worlds-planet-9/talk";
export const MPC_URL = "https://minorplanetcenter.net/";

export const esc = (s: string): string =>
  s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

// ---- kinds ---------------------------------------------------------------------------------

/** The honest caution on fast movers: most rest on two dots in one session (hunt zone: 211 of 228). */
export const MOVER_CAUTION =
  "Most fast movers are seen in only two exposures of one session, which can't show motion by itself: blink it and look for a steady step.";

export const KIND_TEXT: Record<CandidateKind, { name: string; could: string }> = {
  mover: { name: "Fast mover", could: `Seems to move within hours: it could be an asteroid or comet no one has catalogued. ${MOVER_CAUTION}` },
  high_pm: { name: "Moving star", could: "Moves between visits months apart: it could be a nearby star or a brown dwarf." },
  spectral: { name: "Odd spectrum", could: "Its colours or molecule bands are worth a closer look." },
};

/** Colour-free icons (currentColor), one per kind. 20 × 20, 1.5 px strokes like the pencil marks. */
export const KIND_ICON: Record<CandidateKind, string> = {
  // A dot with a short trail of earlier positions.
  mover: `<svg viewBox="0 0 20 20" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><circle cx="14.5" cy="5.5" r="2.6" fill="currentColor" stroke="none"/><circle cx="9.5" cy="10.5" r="1.5"/><circle cx="5.2" cy="14.8" r="1"/><path d="M2.5 17.5 3.4 16.6" /></svg>`,
  // A star with the step it takes between visits.
  high_pm: `<svg viewBox="0 0 20 20" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6 6.2v6.6M2.7 9.5h6.6"/><circle cx="6" cy="9.5" r="1.3" fill="currentColor" stroke="none"/><path d="M10.5 9.5h6M14 7l2.5 2.5L14 12" /></svg>`,
  // A spectrum with an absorption band.
  spectral: `<svg viewBox="0 0 20 20" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M2 13c2-4 4-6 6-6s2 6 3.5 6S13 5 15 5s2.5 3 3 5"/><path d="M2 17h16" opacity=".5"/></svg>`,
};

export function kindName(k: CandidateKind): string {
  return KIND_TEXT[k]?.name ?? "Candidate";
}

// ---- reasons and cautions --------------------------------------------------------------------

/** Pipeline reason codes as plain chips. tone "for" supports the candidate; "doubt" weakens it. */
export const REASON_TEXT: Record<string, { chip: string; tone: "for" | "doubt" | "info"; tip: string }> = {
  two_exposures: {
    chip: "Seen in two exposures",
    tone: "for",
    tip: "Two exposures minutes apart show it at slightly different places.",
  },
  two_detectors: {
    chip: "Seen in two detectors at once",
    tone: "for",
    tip: "Two detectors exposed at the same moment both see it, so it's real light, not a detector flaw.",
  },
  gaia_pm_disagrees: {
    chip: "Gaia lists a different motion",
    tone: "info",
    tip: "Gaia DR3 has a star here, but its catalogued motion differs from what SPHEREx measured.",
  },
  catwise_pm_disagrees: {
    chip: "CatWISE lists a different motion",
    tone: "info",
    tip: "CatWISE2020 (infrared, from NASA's WISE) has a source here with a different motion.",
  },
  few_dots: {
    chip: "Few detections",
    tone: "doubt",
    tip: "Fewer than 20 detections went into the motion fit, so it's less certain.",
  },
  bad_fit: {
    chip: "Positions scatter",
    tone: "doubt",
    tip: "The positions don't line up well on a straight path (χ²/ν above 5).",
  },
  over_pm_cap: {
    chip: "Faster than any known star",
    tone: "doubt",
    tip: "Over 10.5″ a year, faster than Barnard's Star. Usually two different sources paired by mistake.",
  },
  at_control_level: {
    chip: "No better than chance",
    tone: "doubt",
    tip: "Pairs this far apart turn up as often in shifted control fields, so this may be a chance match.",
  },
  short_span: {
    chip: "Short time span",
    tone: "doubt",
    tip: "The detections cover too little time to pin the motion down.",
  },
};

export function reasonText(code: string): { chip: string; tone: "for" | "doubt" | "info"; tip: string } {
  return (
    REASON_TEXT[code] ?? {
      chip: code.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase()),
      tone: "info",
      tip: `Pipeline reason “${code}”.`,
    }
  );
}

/** Artefact flags (contracts/flags.md, recorded on the dot) as plain footnotes. */
export const FLAG_TEXT: Record<string, string> = {
  crosstalk: "Crosstalk: a bright star elsewhere on the detector can echo into this spot.",
  ghost: "Ghost: light reflected inside the telescope can make a faint copy of a bright star.",
  ghost_fpa: "Ghost from the focal plane: a reflection off the detectors can make a false dot.",
  ghost_ext: "Ghost from outside the field: a bright star beyond the edge can leave a reflection here.",
  bloom: "Bloom: charge spilling from a saturated star can streak along a column.",
  halo: "Halo: the glow around a bright star can hide or fake faint dots.",
  outlier: "Outlier: one or more pixels here disagree with the rest of the exposures.",
};

export function flagText(flag: string): string {
  return FLAG_TEXT[flag.trim().toLowerCase()] ?? `${flag.replace(/_/g, " ")}: an artefact flag sits near this dot.`;
}

// ---- numbers -------------------------------------------------------------------------------

const fixed = (x: number, d: number) => x.toFixed(d);

/** Arcsec per year from mas/yr, with sensible digits: 0.50, 5.0, 180. */
export function arcsecYr(mas: number): string {
  const a = mas / 1000;
  return a >= 100 ? Math.round(a).toLocaleString("en-US") : a >= 10 ? fixed(a, 1) : fixed(a, 2);
}

/** Rate in ″/h with sensible digits. */
export function arcsecH(r: number): string {
  return r >= 100 ? Math.round(r).toLocaleString("en-US") : r >= 10 ? fixed(r, 1) : fixed(r, 2);
}

/** AB magnitude of a flux in µJy (AB 23.9 = 1 µJy). */
export function abMag(ujy: number): number {
  return 23.9 - 2.5 * Math.log10(ujy);
}

export function ujy(v: number): string {
  return v >= 100 ? Math.round(v).toLocaleString("en-US") : v.toPrecision(3);
}

export function detText(det: number): string {
  const d = DETECTORS[det as 1];
  return d ? `D${det}, ${d.min}–${d.max} µm` : `D${det}`;
}

/** The headline motion of a candidate: value, unit, ± and significance, or null. */
export function motion(c: Candidate): { value: string; unit: string; plusMinus: string | null; sigma: number | null; toward: string | null } | null {
  if (c.rate_arcsec_h != null && Number.isFinite(c.rate_arcsec_h)) {
    return { value: arcsecH(c.rate_arcsec_h), unit: "″/h", plusMinus: null, sigma: null, toward: c.pa_deg != null ? paWord(c.pa_deg) : null };
  }
  if (c.pm_mas_yr != null && Number.isFinite(c.pm_mas_yr)) {
    const err = c.pm_err_mas_yr != null && c.pm_err_mas_yr > 0 ? c.pm_err_mas_yr : null;
    return {
      value: arcsecYr(c.pm_mas_yr),
      unit: "″/yr",
      plusMinus: err != null ? arcsecYr(err) : null,
      sigma: err != null ? c.pm_mas_yr / err : null,
      toward: c.pa_deg != null ? paWord(c.pa_deg) : null,
    };
  }
  return null;
}

/** "08 Nov 2025, 02:14 UTC" for hours candidates, the date for months. */
export function seenText(c: Candidate): string {
  return formatMjd(c.mjd, c.clock === "hours");
}

/** Nearest known asteroid or comet, in words. */
export function nearestKnownText(c: Candidate): string {
  const d = c.nearest_known_arcsec;
  if (d == null) return c.kind === "mover" ? "None predicted within 600″ at that moment." : "Not checked for this kind.";
  const where = `${Math.round(d).toLocaleString("en-US")}″ away`;
  if (d <= 30) return `${where}: it may be that object.`;
  if (d <= 60) return `${where}: close, but not on it.`;
  return `${where}: too far to be it.`;
}

export function galacticText(b: number): string {
  const s = `${b >= 0 ? "+" : "−"}${Math.abs(b).toFixed(1)}°`;
  return Math.abs(b) < 10 ? `${s}, in the crowded Milky Way plane` : `${s}, away from the crowded Milky Way plane`;
}

// ---- votes ---------------------------------------------------------------------------------

export const answerLabel = (a: Answer): string => ANSWER_TEXT[a].label;

/** One sentence on where the votes stand (DESIGN.md: ≥ 5 votes, ≥ 70 % agreeing). */
export function consensusText(c: Consensus): string {
  const pct = Math.round(c.share * 100);
  switch (c.state) {
    case "none":
      return "No votes yet.";
    case "forming": {
      const togo = CONSENSUS_MIN_VOTES - c.total;
      return `${c.total} ${c.total === 1 ? "vote" : "votes"} so far. ${togo} more ${togo === 1 ? "is" : "are"} needed before the community can agree.`;
    }
    case "split":
      return `${c.total} votes, no agreement yet: the top answer, ${answerLabel(c.top!)}, has ${pct}%. Agreement needs ${CONSENSUS_PERCENT}%.`;
    case "reached":
      return `The community agrees: ${answerLabel(c.answer!)}, ${c.topCount} of ${c.total} votes (${pct}%).`;
  }
}

/** Counts as a sentence, so colour is never the only cue: "3 mover, 1 changing, 0 junk, 1 not sure". */
export function countsText(k: Counts): string {
  return `${k.mover} mover, ${k.changing} changing, ${k.junk} junk, ${k.unsure} not sure`;
}

export { consensus };
