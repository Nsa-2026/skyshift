// Plain words for the codes the pipeline writes (DESIGN.md "Candidates", "Smart filters").
// Codes stay in title attributes and exports; people read these.
import { GRADE_TEXT, gradeLabel } from "../api/grades";
import type { CandidateKind } from "../api/types";

export interface KindText {
  name: string;
  plural: string;
  /** One line: what it is and what it could be. */
  gist: string;
}

export const KINDS: CandidateKind[] = ["mover", "high_pm", "spectral", "shape"];

export const KIND_TEXT: Record<CandidateKind, KindText> = {
  mover: { name: "Fast mover", plural: "Fast movers", gist: "Seems to move within hours: maybe an uncatalogued asteroid or comet. Most fast movers are seen in only two exposures of one session, which can't show motion by itself: blink it and look for a steady step." },
  high_pm: { name: "Moving star", plural: "Moving stars", gist: "Moves between visits: maybe a nearby star or brown dwarf." },
  spectral: { name: "Odd spectrum", plural: "Odd spectra", gist: "Molecule bands or colours worth a look." },
  shape: { name: "One or two?", plural: "One or two?", gist: "Wider or longer than a single star, where Gaia and CatWISE list one object: maybe a passing object or a companion." },
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

  // Codes the exporter sends that had no text, so they read as "Pm fit" and counted as doubt.
  // Wording from contracts/api.md, candidates.json "reasons".
  pm_fit: { label: "Motion fitted across visits", tone: "support", detail: "Its motion was fitted over every detection in every visit." },
  vetted: { label: "Passed every check", tone: "support", detail: "It came through all of the pipeline's checks with nothing against it." },
  catwise_pm_agrees: { label: "CatWISE lists the same motion", tone: "support", detail: "CatWISE lists the same motion for the source here." },
  no_gaia: { label: "Not in Gaia (G < 20)", tone: "support", detail: "Gaia DR3 lists nothing brighter than G 20 here." },
  gaia_nearby: { label: "Within 50 pc", tone: "support", detail: "Gaia has it with a parallax of 20 mas or more, so it is closer than 50 parsecs." },
  class_star: { label: "Point-like", tone: "support", detail: "The classifier calls it a star: a point source, not an extended one." },
  variable: { label: "Brightness changes", tone: "support", detail: "Its brightness changes between visits." },

  pm_pair: { label: "Paired across visits", tone: "doubt", detail: "Two sources, each seen in one visit only, paired across visits: it may be two different objects." },
  implausible_pm: { label: "Too fast for a star", tone: "doubt", detail: "Faster than 12″ a year. Barnard's Star, the fastest known, does 10.4″, so this is almost certainly two different sources." },
  motion_not_significant: { label: "Motion under 3σ", tone: "doubt", detail: "The motion is smaller than 3σ of its position errors: it may not move at all." },
  known_object_near: { label: "Known object predicted nearby", tone: "doubt", detail: "A known asteroid or comet was predicted within 2′ at that time: this may be that object, slightly off its prediction." },
  class_galaxy: { label: "Looks extended", tone: "doubt", detail: "The classifier calls it a galaxy, which would explain the spectrum or the shape without anything new: galaxies look extended by nature." },
  class_unsure: { label: "Shape unclear", tone: "doubt", detail: "Point-like but with no Gaia star, so the classifier could not decide what it is." },
  gaia_unchecked: { label: "Gaia not checked", tone: "doubt", detail: "Gaia could not be checked when the pipeline ran." },
  catwise_unchecked: { label: "CatWISE not checked", tone: "doubt", detail: "CatWISE could not be checked when the pipeline ran." },

  outlier_flag: { label: "Outlier pixels", tone: "caution", detail: "The OUTLIER flag is set on its pixels: often a cosmic ray or a bad pixel." },
  bad_pixels_near: { label: "Bad pixels nearby", tone: "caution", detail: "A masked pixel (cosmic ray, hot, dead or persistent) touches one of its detections." },
  detector_excess: { label: "From a detector with excess movers", tone: "caution", detail: "Seen only in detectors where this zone has at least 1.5 times its usual share of unexplained movers: those are likely artefacts." },

  // The suspects' failed vetting gates: a mover with any of them is grade C. Wording from
  // contracts/api.md, suspects.json "reasons". Artefact gates are cautions, so "hide artefact
  // cautions" and "Check first" catch them; the catalogue gates and not_vetted are doubts.
  hot_pixel: { label: "Hot pixel", tone: "caution", detail: "It sits on a detector pixel where the zone's movers turn up again and again: a hot pixel that NASA's HOT flag misses." },
  bad_column: { label: "Bad column", tone: "caution", detail: "Three or more unpaired dots line up in one pixel column of the same photo: a detector column, not a source." },
  edge: { label: "At the detector edge", tone: "caution", detail: "One of its detections is within 6 pixels of the detector edge, where false sources are common." },
  masked_peak: { label: "Bad pixel at its peak", tone: "caution", detail: "A pixel flagged as bad sits at the peak of one of its detections." },
  sharp_outlier: { label: "Sharper than a star", tone: "caution", detail: "One detection is narrower than a star of its brightness and has the OUTLIER flag at its peak: likely a cosmic ray." },
  very_sharp: { label: "Far too sharp", tone: "caution", detail: "One detection is less than 0.4 times as wide as a star of its brightness: no real source is that narrow." },
  flux_ratio: { label: "Brightness jumps", tone: "caution", detail: "Its brightest detection is more than 4 times its faintest in the same detector: likely not one steady source." },
  red_photo: { label: "Only in bad photos", tone: "caution", detail: "Every detection comes from a photo the quality check graded red." },
  "2mass": { label: "2MASS source here", tone: "doubt", detail: "2MASS lists a source within 6″: probably a catalogued star, not something new." },
  allwise: { label: "AllWISE source here", tone: "doubt", detail: "AllWISE lists a source within 6″: probably a catalogued source, not something new." },
  // shape: one object or two? (contracts/api.md, candidates.json "reasons")
  elongated: { label: "Stretched shape", tone: "support", detail: "Its image is more stretched than single stars of the same brightness on the same detector." },
  steady_pa: { label: "Same direction every time", tone: "support", detail: "Its photos show the stretch pointing the same way on the sky, in both visits, although the detector turns between visits. Noise points anywhere." },
  one_catalogue_source: { label: "Catalogues list one object", tone: "support", detail: "Gaia DR3 and CatWISE2020 list a single object within 15″, so a second one would be new." },
  one_visit: { label: "Stretched in one visit only", tone: "doubt", detail: "Only one of the two visits shows the stretch: something may have passed by, or one visit is noisier." },
  not_vetted: { label: "Not vetted", tone: "doubt", detail: "There was no pixel data to run the artefact checks on, so nothing was ruled out." },
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

/** The grade facet's values: the three grades, then "none" (not vetted yet, or a kind that isn't graded). */
export const GRADE_VALUES = ["A", "B", "C", "none"] as const;

/** A grade facet value in words, for the filter, the chips in use and their titles. */
export function gradeFacetText(v: string): { label: string; title: string } {
  if (v === "A" || v === "B" || v === "C") return { label: gradeLabel(v), title: `grade ${v}: ${GRADE_TEXT[v].plain}` };
  return {
    label: "No grade",
    title: "No grade: fast movers found before the vetting ran, and every moving star and odd spectrum (only fast movers are graded).",
  };
}

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
