// What one object to check did, in which detector, and what could make it an artefact: the few
// lines at the top of the suspect panel (#/c/<id>). Built only from the candidate's own fields and
// reasons (contracts/api.md, candidates.json) and, once loaded, the measured layer of its frames
// (the mover's track, a moving star's fitted shift, the partner detector's detections). Pure.
// A file with `checks` (contracts/api.md v3.23: measured, with what to look at) shows those in place
// of the "Watch for" line guessed here from reasons and flags.
import { checksOf } from "../api/checks";
import type { Candidate, CandidateCheck, FramesResponse, MeasuredTrack, SkyPos } from "../api/types";
import { SAME_MOMENT, sepArcsec } from "../app/astro";
import { paWord } from "../card/format";
import { arcsecH, arcsecYr } from "./text";

export interface About {
  /** At most three short sentences: what it did and what that means. */
  said: string[];
  /** "Watch for: …" (the cautions in plain words), or "" when the pipeline raised none or the file
   *  has checks (they replace it). */
  watch: string;
  /** The file's checks (v3.23), strong first, then warn, then ok; [] without any. */
  checks: CandidateCheck[];
}

/** Checks that say, measured, whether the partner detector saw a mover: they replace our guess. */
const PARTNER_CHECKS = new Set(["one_detector", "partner_seen"]);

/** Two dots this close in time (days, 30 s) are one moment: two detectors exposed together. */
const MOMENT_DAYS = 30 / 86400;
/** A partner detection this close (arcsec) to the track's dot of that moment is the same object. */
const PARTNER_ARCSEC = 6;
/** A fitted shift this close (arcsec) to a moving star's position is its own. */
const SHIFT_ARCSEC = 6;

const ok = (x: number | null | undefined): x is number => typeof x === "number" && Number.isFinite(x);

/** Arcsec with sensible digits: 0.42″, 3.1″, 11″. */
export function arcsecText(a: number): string {
  return `${a < 1 ? a.toFixed(2) : a < 10 ? a.toFixed(1) : Math.round(a).toLocaleString("en-US")}″`;
}

/** A short span in words: "6.4 minutes", "2.5 hours". */
export function spanText(days: number): string {
  const min = days * 1440;
  if (min < 60) return `${min < 10 ? min.toFixed(1) : Math.round(min)} minutes`;
  const h = min / 60;
  return `${h < 10 ? h.toFixed(1) : Math.round(h)} hours`;
}

/** The mover's track in these frames (contracts/api.md measured.tracks), or null. */
export function trackOf(c: Pick<Candidate, "id">, f: FramesResponse | null | undefined): MeasuredTrack | null {
  return f?.measured?.tracks?.find((t) => t.id === c.id || t.suspect_id === c.id) ?? null;
}

/** The track's moments in time order (dots of one instant, two detectors, counted once). */
function moments(t: MeasuredTrack): { mjd: number; at: SkyPos }[] {
  const out: { mjd: number; at: SkyPos }[] = [];
  for (const d of [...t.dots].sort((a, b) => a.mjd - b.mjd)) {
    const last = out[out.length - 1];
    if (!last || d.mjd - last.mjd > MOMENT_DAYS) out.push({ mjd: d.mjd, at: d });
  }
  return out;
}

/**
 * Did the simultaneous partner detector see the mover? "yes" (two_detectors, or a partner detection
 * at the track's place at one of its moments); "no" only when the partner's exposures are in the
 * frames and their measured layer has nothing there; "own" when the partner wasn't looked at but every
 * dot of the track is in the candidate's own detector (the pipeline pairs a partner's dot of the same
 * moment into the mover, so none of ≥ 5σ was linked); else "unknown". Evidence alone claims nothing.
 */
export function partnerSaw(
  c: Pick<Candidate, "id" | "det" | "reasons">,
  f: FramesResponse | null | undefined,
): "yes" | "no" | "own" | "unknown" {
  if (c.reasons.includes("two_detectors")) return "yes";
  const partner = SAME_MOMENT[c.det];
  const t = trackOf(c, f);
  if (f && t && partner && f.measured) {
    const pe = f.epochs.filter((e) => e.det === partner);
    const looked = moments(t).flatMap((m) => pe.filter((e) => Math.abs(e.mjd_mid - m.mjd) <= MOMENT_DAYS).map((e) => ({ e, m })));
    if (looked.length) {
      const seen = looked.some(({ e, m }) => f.measured!.detections.some((d) => d.epoch_id === e.id && sepArcsec(d, m.at) <= PARTNER_ARCSEC));
      return seen ? "yes" : "no";
    }
  }
  if (t?.dots.length && t.dots.every((d) => d.epoch_id?.startsWith(`d${c.det}-`))) return "own";
  return "unknown";
}

/** What the partner's silence means for a mover seen in `det` alone. Real asteroids glow by their own
 *  heat in D5–D6 and can show there alone; seen in D4 alone, they are rare (hunt, ecl04, ecl08: 1, 8
 *  and 1 known asteroids against ~200 junk movers a zone), so a flaw of D4 is more likely. */
function aloneMeans(det: number): string {
  if (det >= 5) return `typical of an asteroid or comet glowing by its own heat, but it can also be a flaw of D${det}`;
  if (det === 4) return "rare for a real asteroid (their heat glow starts in D5), so a flaw of D4 is more likely";
  return "";
}

// ---- cautions ----------------------------------------------------------------------------------

/** Codes that say why it was listed or support it: never a "watch for". */
const NOT_A_CAUTION = new Set([
  "two_exposures",
  "two_detectors",
  "pm_fit",
  "pm_pair",
  "vetted",
  "catwise_pm_agrees",
  "parallax_fit",
  "no_gaia",
  "gaia_nearby",
  "class_star",
  "class_unsure",
  "variable",
  "elongated",
  "steady_pa",
  "one_catalogue_source",
]);

const minutesAway = (a: number) => (a >= 60 ? `${(a / 60).toFixed(1)}′` : `${Math.round(a)}″`);

/** One caution code in plain words (lower case, no full stop), for the "Watch for" line. */
export function cautionText(code: string, c: Pick<Candidate, "det" | "nearest_known_arcsec">): string {
  const D = `D${c.det}`;
  switch (code) {
    case "outlier_flag":
      return "NASA marked these pixels as unusual (OUTLIER)";
    case "detector_excess":
      return `${D} had more unexplained movers than usual in that visit`;
    case "repeat_position":
      return "another unexplained mover was seen within 30″ of here in another session, so it may be a fixed source";
    case "known_object_near":
      return ok(c.nearest_known_arcsec)
        ? `a known asteroid, comet, planet or moon was predicted ${minutesAway(c.nearest_known_arcsec)} away: it may be that object`
        : "a known asteroid, comet, planet or moon was predicted within 2′: it may be that object";
    case "motion_not_significant":
      return "the shift is within its position errors, so it may not move at all";
    case "bad_pixels_near":
      return "a masked pixel (cosmic ray, hot or dead pixel) touches one of its dots";
    case "gaia_unchecked":
      return "Gaia could not be checked here";
    case "catwise_unchecked":
      return "CatWISE could not be checked here";
    case "not_vetted":
      return "there was no dot data to vet it with";
    case "hot_pixel":
      return "a dot sits on a pixel where dots keep turning up (a hot pixel)";
    case "bad_column":
      return "a dot sits on a faulty detector column";
    case "edge":
      return "a dot lies within 6 pixels of the detector's edge";
    case "masked_peak":
      return "NASA flags the pixel at a dot's peak as untrustworthy";
    case "sharp_outlier":
      return "a dot is sharper than a star and flagged OUTLIER: usually a cosmic ray";
    case "very_sharp":
      return "a dot is far too sharp for light from the sky";
    case "flux_ratio":
      return "its brightness jumps more than 4× between photos";
    case "red_photo":
      return "every dot comes from photos our check graded bad";
    case "2mass":
      return "2MASS lists a source within 6″ that stays put";
    case "allwise":
      return "AllWISE lists a source within 6″ that stays put";
    case "short_span":
      return "its visits are close in time, too close to pin a motion down";
    case "few_dots":
      return "fewer than 20 detections went into the fit";
    case "bad_fit":
      return "its positions don't line up on a straight path (a blend or a bad fit)";
    case "unsteady":
      return "its visits go out and back instead of along a line";
    case "over_pm_cap":
      return "it is faster than any known star (over 10.5″ a year)";
    case "gaia_pm_disagrees":
      return "Gaia lists a different motion for the star here: likely a mismatch";
    case "catwise_pm_disagrees":
      return "CatWISE lists a different motion for the source here: likely a mismatch";
    case "at_control_level":
      return "pairs this far apart turn up as often by chance";
    case "implausible_pm":
      return "no star moves that fast: almost certainly two different sources";
    case "blend":
      return "two or more Gaia stars sit within 8″, so the light of a pair may be merged";
    case "one_visit":
      return "only one visit shows the stretch";
    case "class_galaxy":
      return "it looks like a galaxy, and galaxies are extended by nature";
    default:
      return `pipeline check “${code}”`;
  }
}

/** Recorded flags (contracts/flags.md) the reasons don't already name, in plain words. */
const FLAG_WORDS: Record<string, string> = {
  outlier: "NASA marked these pixels as unusual (OUTLIER)",
  crosstalk: "NASA flagged crosstalk from a bright star on these pixels (CROSSTALK)",
  ghost: "NASA flagged a ghost of a bright star here (GHOST)",
  ghost_fpa: "NASA flagged a reflection off the detectors here (GHOST_FPA)",
  ghost_ext: "NASA flagged a reflection from a star outside the field here (GHOST_EXT)",
  bloom: "NASA flagged charge spilling from a saturated star here (BLOOM)",
  halo: "NASA flagged a bright star's halo here (HALO)",
};

function watchLine(c: Candidate): string {
  const parts: string[] = [];
  for (const r of c.reasons) if (!NOT_A_CAUTION.has(r)) parts.push(cautionText(r, c));
  for (const f of c.flags) {
    const k = f.trim().toLowerCase();
    if (k === "outlier" && c.reasons.includes("outlier_flag")) continue;
    parts.push(FLAG_WORDS[k] ?? `NASA flagged ${k.toUpperCase()} on these pixels`);
  }
  const uniq = [...new Set(parts)];
  if (!uniq.length) return "";
  return `Watch for: ${uniq.join("; ")}.`;
}

// ---- what it did, per kind ---------------------------------------------------------------------

function mover(c: Candidate, f: FramesResponse | null | undefined, checks: CandidateCheck[]): string[] {
  const D = `D${c.det}`;
  const P = SAME_MOMENT[c.det];
  const out: string[] = [];
  const rate = ok(c.rate_arcsec_h) ? `about ${arcsecH(c.rate_arcsec_h)}″ an hour` : "";
  if (c.reasons.includes("two_detectors")) {
    out.push(`Seen once, by ${D} and D${P} at the same moment, so its motion was not measured.`);
    out.push(`Two detectors seeing it at once means real light, not a flaw of one detector.`);
    return out;
  }
  const t = trackOf(c, f);
  const m = t ? moments(t) : [];
  if (m.length >= 2) {
    const moved = sepArcsec(m[0].at, m[m.length - 1].at);
    const verb = c.reasons.includes("motion_not_significant") ? "Shifted" : "Moved";
    out.push(`${verb} ${arcsecText(moved)} in ${D} over ${spanText(m[m.length - 1].mjd - m[0].mjd)} (${m.length} photos)${rate ? `, ${rate}` : ""}.`);
  } else if (rate) out.push(`Moves ${rate} in ${D}.`);
  else out.push(`Seen at different places in ${D} photos minutes apart.`);
  // The file measured the partner (one_detector, partner_seen): its check says so, with numbers.
  if (checks.some((k) => PARTNER_CHECKS.has(k.code))) return out;
  const saw = partnerSaw(c, f);
  const means = aloneMeans(c.det);
  if (saw === "no" && P) {
    out.push(`D${P} photographed the same spot at the same moments and saw nothing there${means ? `: seen in ${D} alone is ${means}` : ""}.`);
  } else if (saw === "own" && P) {
    out.push(`All its dots are in ${D}; D${P} sees the same sky at the same moments${means ? `. Seen in ${D} alone is ${means}` : ""}.`);
  } else if (saw === "yes" && P) out.push(`D${P} saw it too at the same moment: real light, not a flaw of one detector.`);
  return out;
}

function movingStar(c: Candidate, f: FramesResponse | null | undefined): string[] {
  const D = `D${c.det}`;
  const out: string[] = [];
  const at = { ra: c.ra, dec: c.dec };
  const shift = f?.measured?.shifts
    ?.filter((s) => ok(s.shift_arcsec) && ok(s.dt_yr) && s.dt_yr > 0 && sepArcsec(s, at) <= SHIFT_ARCSEC)
    .sort((a, b) => sepArcsec(a, at) - sepArcsec(b, at))[0];
  const year = ok(c.pm_mas_yr) ? `${arcsecYr(c.pm_mas_yr)}″ a year` : "";
  const toward = ok(c.pa_deg) ? ` toward the ${paWord(c.pa_deg)}` : "";
  if (shift) {
    const months = Math.round(shift.dt_yr * 12);
    out.push(`Moved ${arcsecText(shift.shift_arcsec)}${toward} between visits ${months} months apart${year ? ` (${year})` : ""}; clearest in ${D}.`);
  } else if (year) out.push(`Moves ${year}${toward} between visits; clearest in ${D}.`);
  else out.push(`Moves between visits; clearest in ${D}.`);
  const red = c.labels.includes("brown_dwarf_like") ? "its colours look like a brown dwarf's" : c.labels.includes("red") ? "it is red" : "";
  if (c.gaia_match && c.reasons.includes("catwise_pm_agrees")) out.push(`Gaia lists a star here and CatWISE gives the same motion: most likely a known nearby star${red ? `, and ${red}` : ""}.`);
  else if (c.gaia_match) out.push(`Gaia lists a star here${red ? `, and ${red}` : ""}.`);
  else out.push(`Gaia lists no star here${red ? `, and ${red}` : ""}: a faint nearby star or brown dwarf, if the motion is real.`);
  return out;
}

const BAND_WORDS: Record<string, string> = {
  ch4: "methane (1.6 and 3.3 µm)",
  h2o_vapour: "water vapour (1.4 and 1.9 µm)",
  co_23: "carbon monoxide (2.3 µm)",
  h2o_ice: "water ice (3.05 µm)",
  co2_ice: "CO₂ ice (4.27 µm)",
  co_ice: "CO ice (4.67 µm)",
  pah_33: "the 3.3 µm glow of a star-forming galaxy (PAH)",
};

const list = (w: string[]) => (w.length < 2 ? (w[0] ?? "") : `${w.slice(0, -1).join(", ")} and ${w[w.length - 1]}`);

function spectral(c: Candidate): string[] {
  const D = `D${c.det}`;
  const bands = c.labels.filter((l) => BAND_WORDS[l]).map((l) => BAND_WORDS[l]);
  const snr = ok(c.snr) ? ` (S/N ${Math.round(c.snr)})` : "";
  const out: string[] = [];
  if (bands.length) out.push(`Its spectrum shows ${list(bands)}; its strongest signal is in ${D}${snr}.`);
  else if (c.labels.includes("brown_dwarf_like")) out.push(`Its colours look like a brown dwarf's; its strongest signal is in ${D}${snr}.`);
  else out.push(`Its spectrum stands out; its strongest signal is in ${D}${snr}.`);
  const what: string[] = [];
  if (c.reasons.includes("gaia_nearby")) what.push("Gaia puts it within 50 parsecs");
  else if (c.reasons.includes("no_gaia")) what.push("Gaia lists no star here");
  if (c.reasons.includes("class_galaxy")) what.push("it looks like a galaxy");
  else if (c.reasons.includes("class_unsure")) what.push("it is point-like");
  if (c.labels.includes("red") && !c.labels.includes("brown_dwarf_like")) what.push("it is red");
  if (c.labels.includes("brown_dwarf_like") && bands.length) what.push("with its red colour that is what a brown dwarf would look like");
  if (c.reasons.includes("variable")) what.push("its brightness changes between visits");
  if (what.length) {
    const s = list(what);
    out.push(`${s.charAt(0).toUpperCase()}${s.slice(1)}.`);
  }
  return out;
}

function shape(c: Candidate): string[] {
  const out: string[] = [];
  const axis = ok(c.pa_deg) ? ` along PA ${Math.round(c.pa_deg)}° (${paWord(c.pa_deg)}–${paWord(c.pa_deg + 180)})` : "";
  out.push(`Its image is stretched${axis} like two objects side by side, but Gaia and CatWISE list only one; clearest in D${c.det}.`);
  if (c.reasons.includes("steady_pa")) out.push("D1–D3 and D4–D6 show the stretch along the same axis, which a flaw of one detector would not do.");
  return out;
}

/** The panel's lines for a candidate; `frames` (its own, when loaded) make them more precise. */
export function aboutCandidate(c: Candidate, frames?: FramesResponse | null): About {
  const checks = checksOf(c);
  const said = c.kind === "mover" ? mover(c, frames, checks) : c.kind === "high_pm" ? movingStar(c, frames) : c.kind === "spectral" ? spectral(c) : shape(c);
  return { said: said.slice(0, 3), watch: checks.length ? "" : watchLine(c), checks };
}
