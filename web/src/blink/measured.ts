// What SkyShift measured (contracts/api.md v3.3, `measured` on /api/frames): the words and numbers
// behind the blink's Measured marks and its summary panel. Pure functions, no DOM, so they test
// without a canvas. Drawing lives in index.ts and marks.ts.
import type {
  FramesResponse,
  GaiaStar,
  Measured,
  MeasuredClass,
  MeasuredDetection,
  MeasuredReason,
  MeasuredShift,
  MeasuredTrack,
} from "../api/types";
import { gaiaAt } from "../app/astro";

/** Legend and drawing order: what matters most first (drawn last, so it sits on top). */
export const CLASS_ORDER: MeasuredClass[] = ["suspect", "known", "mover", "artifact", "star", "other", "stack"];

export interface ClassInfo {
  /** Plain name in the legend and the list. */
  name: string;
  /** One sentence: what the class means. */
  about: string;
  /** Colour token (tokens.css). */
  token: string;
}

export const CLASS_INFO: Record<MeasuredClass, ClassInfo> = {
  suspect: {
    name: "Suspect",
    about: "A mover nothing explains yet: no star, known asteroid or artefact. Our checks grade it A, B or C.",
    token: "--m-suspect",
  },
  known: {
    name: "Known asteroid or comet",
    about: "A mover where the Minor Planet Center's orbits put a known asteroid or comet.",
    token: "--m-known",
  },
  mover: {
    name: "Other mover",
    about: "Paired like a mover, but our checks set it aside, for example next to a catalogued star.",
    token: "--m-mover",
  },
  artifact: {
    name: "Artefact",
    about: "Not a real source: a ghost, crosstalk, a halo or a masked pixel.",
    token: "--m-artifact",
  },
  star: { name: "Gaia star", about: "A source that matches a star Gaia already knows.", token: "--m-star" },
  other: {
    name: "Other source",
    about: "No Gaia match: a galaxy, a faint or red star, or a lone dot.",
    token: "--m-other",
  },
  stack: {
    name: "Seen in the stack only",
    about:
      "Found only when a visit's photos are added up: fainter than one photo shows, the second star of a close pair, or a star in a bright star's glow.",
    token: "--m-stack",
  },
};

/** A class this page knows; anything newer than this code is drawn and counted as `other`. */
export const knownClass = (c: string | null | undefined): MeasuredClass =>
  c != null && Object.prototype.hasOwnProperty.call(CLASS_INFO, c) ? (c as MeasuredClass) : "other";
/** The legend entry for a class, `other` for one this page doesn't know. */
export const classInfo = (c: string | null | undefined): ClassInfo => CLASS_INFO[knownClass(c)];

/**
 * The measured layer with every class one this page knows (unknown → `other`, the contract's
 * catch-all), so a class added on the server never breaks the plate. The same object when nothing changes.
 */
export function normalizeMeasured(m: Measured): Measured {
  const fix = <T extends { class: MeasuredClass }>(xs: T[] | undefined): T[] | undefined =>
    xs?.some((x) => knownClass(x.class) !== x.class) ? xs.map((x) => ({ ...x, class: knownClass(x.class) })) : xs;
  const detections = fix(m.detections);
  const tracks = fix(m.tracks);
  const shifts = fix(m.shifts);
  if (detections === m.detections && tracks === m.tracks && shifts === m.shifts) return m;
  return { ...m, detections: detections ?? [], tracks: tracks ?? [], shifts: shifts ?? [] };
}

/** Reason codes in plain words. Codes not listed are shown as given (the contract asks for that). */
const REASON_WORDS: Record<string, string> = {
  ghost: "ghost",
  ghost_fpa: "focal-plane ghost",
  ghost_ext: "ghost from a star off the detector",
  crosstalk: "crosstalk",
  halo: "halo",
  bloom: "bloom",
  masked_peak: "masked pixel at its peak",
  elongated: "stretched, not round",
  short_span: "seen over too short a time",
  few_dots: "too few dots",
  bad_fit: "poor motion fit",
  over_pm_cap: "faster than any star could move",
  gaia_pm_disagrees: "Gaia measures a different motion",
  catwise_pm_disagrees: "CatWISE measures a different motion",
  at_control_level: "no better than chance",
  not_vetted: "not vetted yet",
  hot_pixel: "hot pixel",
  bad_column: "bad column",
  edge: "at the detector's edge",
  sharp_outlier: "sharper than a star",
  very_sharp: "very sharp, sharper than the optics allow",
  allwise: "an AllWISE source sits here",
  flux_ratio: "brightness jumps",
  red_photo: "photo failed the quality check",
  "2mass": "a 2MASS source sits here",
  near_gaia: "near a Gaia star",
  // Proposed codes (not in contracts/api.md yet): read when the server sends them.
  bright_fragment: "a piece of a bright star's glow",
  blend: "two Gaia stars blended in SPHEREx's 6″ pixels",
  split_by_fit: "a second object a two-object fit split off this source",
  stack_near_bright: "next to a much brighter source, where the star fit leaves pieces",
};

/**
 * Proposed detection fields, not in contracts/api.md yet: the bright star a `bright_fragment` belongs
 * to, and the Gaia stars of a `blend`. Read when present; the plate falls back on its own heuristics.
 */
export interface DetectionExtras {
  parent_gaia_id?: string | null;
  blend_gaia_ids?: string[] | null;
}
export const extras = (d: MeasuredDetection): DetectionExtras => d as MeasuredDetection & DetectionExtras;

export const reasonWords = (codes: string[]): string[] => codes.map((c) => REASON_WORDS[c] ?? c);

// ---- state ----------------------------------------------------------------------------------

export type MeasuredState =
  | { kind: "absent" }
  | { kind: "missing"; reason: MeasuredReason | string }
  | { kind: "measured"; m: Measured };

/**
 * What the panel shows. `measured` set: the summary. `measured` null with a reason: one line saying
 * why. Neither field (files and answers older than v3.3), or null without a reason: nothing.
 */
export function measuredState(f: Pick<FramesResponse, "measured" | "measured_reason"> | null | undefined): MeasuredState {
  if (f?.measured && typeof f.measured === "object") return { kind: "measured", m: normalizeMeasured(f.measured) };
  if (typeof f?.measured_reason === "string" && f.measured_reason) return { kind: "missing", reason: f.measured_reason };
  return { kind: "absent" };
}

/** The line for `measured: null`, as HTML (links to the live run), and whether asking again helps. */
export function reasonLine(reason: string): { html: string; retry: boolean } {
  const live = `<a href="#/live">tonight's run</a>`;
  switch (reason) {
    case "not_processed":
      return { html: `Not processed yet: this spot isn't in ${live}.`, retry: false };
    case "processing":
      return {
        html: `Being processed: ${live} covers this spot, but its results aren't combined yet. Check back later.`,
        retry: false,
      };
    case "no_dots":
      return {
        html: "Not measured in these photos: the run covers this spot, but none of these photos has been searched for dots yet.",
        retry: false,
      };
    case "unavailable":
      return { html: "SkyShift's measurements didn't load in time. Asking again usually works.", retry: true };
    default:
      return { html: "SkyShift hasn't measured this spot.", retry: false };
  }
}

// ---- words ----------------------------------------------------------------------------------

const plural = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString("en-GB")} ${n === 1 ? one : many}`;

/** "a", "a and b", "a, b and c". */
export function listJoin(items: string[]): string {
  if (items.length <= 1) return items[0] ?? "";
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

/** Comet designations: 3I/ATLAS, C/2025 N1, 12P/…, P/2019 LD2. */
const isComet = (name: string) => /^(\d+[PIDC]\/|[PCDXAI]\/)/.test(name.trim());

function knownPhrase(n: number, names: (string | null)[]): string {
  const named = names.filter((s): s is string => !!s);
  const comets = named.filter(isComet).length;
  if (named.length && comets === named.length) return plural(n, "known comet");
  if (comets) return `${n} known ${n === 1 ? "asteroid or comet" : "asteroids and comets"}`;
  return plural(n, "known asteroid");
}

/** "grade B", "1 grade B, 2 grade C", "not graded yet". */
function gradePhrase(grades: (string | null)[]): string {
  if (!grades.length) return "";
  const by = new Map<string, number>();
  for (const g of grades) by.set(g ?? "", (by.get(g ?? "") ?? 0) + 1);
  const keys = [...by.keys()].sort((a, b) => (a === "" ? 1 : b === "" ? -1 : a.localeCompare(b)));
  if (keys.length === 1) return keys[0] ? `grade ${keys[0]}` : "not graded yet";
  return keys.map((k) => (k ? `${by.get(k)} grade ${k}` : `${by.get(k)} not graded yet`)).join(", ");
}

const total = (c: Measured["counts"]) => CLASS_ORDER.reduce((s, k) => s + (c?.[k] ?? 0), 0);

/** A track's grade: from its detections (tracks carry none themselves). */
function trackGrades(m: Measured): Map<string, MeasuredDetection["grade"]> {
  const out = new Map<string, MeasuredDetection["grade"]>();
  for (const d of m.detections)
    if (d.track_id && (!out.has(d.track_id) || (out.get(d.track_id) == null && d.grade))) out.set(d.track_id, d.grade);
  return out;
}

/** Months: distinct linked sources per class (a source keeps its class in every visit). */
export function sourcesByClass(m: Measured): Record<MeasuredClass, number> {
  const seen = new Map<string, MeasuredClass>();
  m.detections.forEach((d, i) => {
    const k = d.source_id != null ? `s${d.source_id}` : `d${i}`;
    if (!seen.has(k)) seen.set(k, knownClass(d.class));
  });
  const out = { star: 0, known: 0, mover: 0, suspect: 0, artifact: 0, other: 0, stack: 0 } as Record<MeasuredClass, number>;
  for (const c of seen.values()) out[c] = (out[c] ?? 0) + 1;
  return out;
}

export interface Summary {
  /** The headline sentence, HTML (numbers in <b>). */
  lead: string;
  /** The rest of the field in one sentence, or "". */
  rest: string;
  /** Where the numbers come from and their limits: zone and stage, missing photos, the cap. */
  notes: string[];
}

/** The stage as a short tag: Final, Provisional, Extracted, or a new stage as given. */
export function stageLabel(stage: string | null | undefined): string {
  const s = String(stage ?? "").replace(/_/g, " ").trim();
  return s ? s[0].toUpperCase() + s.slice(1) : "";
}

/** One sentence: which zone, and how far its results have come. */
export function stageNote(m: Pick<Measured, "zone" | "stage">): string {
  const zone = `the ${m.zone} zone`;
  switch (m.stage) {
    case "final":
      return `Final results from ${zone}.`;
    case "provisional":
      return `Provisional results from ${zone}, checked against Gaia only: expect more suspects than the final pass keeps.`;
    case "extracted":
      return `Early results from ${zone}: each photo's detections classed against Gaia only, before the zone is combined.`;
    default:
      return m.stage ? `Results from ${zone}, at the stage it calls ${String(m.stage).replace(/_/g, " ")}.` : `Results from ${zone}.`;
  }
}

/** "N dots in P photos, and M movers: K known asteroids, J suspects (grades), L artefacts." */
export function summarize(m: Measured, gaia?: GaiaStar[], opts: { artefacts?: boolean } = {}): Summary {
  const photos = plural(m.photos ?? 0, "photo");
  const counts = Object.fromEntries(CLASS_ORDER.map((k) => [k, m.counts?.[k] ?? 0])) as Record<MeasuredClass, number>;
  m = { ...m, counts, tracks: m.tracks ?? [], shifts: m.shifts ?? [], detections: m.detections ?? [] };
  let lead: string;
  let rest = "";
  // Extracted: each photo classed against Gaia, nothing linked or paired yet. "No movers" would be
  // wrong there: say they aren't linked yet.
  const early = m.stage === "extracted" && !m.tracks.length && !m.shifts.length;
  if (early) {
    const n = total(counts);
    lead =
      `<b>${n.toLocaleString("en-GB")}</b> ${m.clock === "hours" ? (n === 1 ? "dot" : "dots") : n === 1 ? "detection" : "detections"} in ${photos}, ` +
      (m.clock === "hours"
        ? "classed against Gaia only: movers aren't linked yet."
        : "classed against Gaia only: sources aren't linked and motions aren't fitted yet.");
    const r: string[] = [];
    if (counts.star) r.push(`${counts.star.toLocaleString("en-GB")} ${counts.star === 1 ? "matches a Gaia star" : "match Gaia stars"}`);
    if (counts.other) r.push(`${counts.other.toLocaleString("en-GB")} ${counts.other === 1 ? "has" : "have"} no Gaia match`);
    if (counts.artifact) r.push(`${counts.artifact.toLocaleString("en-GB")} ${counts.artifact === 1 ? "looks" : "look"} like ${counts.artifact === 1 ? "an artefact" : "artefacts"}`);
    if (r.length) rest = `${listJoin(r)}.`;
  } else if (m.clock === "hours") {
    const dots = total(m.counts);
    const by = (c: MeasuredTrack["class"]) => m.tracks.filter((t) => t.class === c);
    const grades = trackGrades(m);
    const parts: string[] = [];
    // The pipeline pairs a mover per session: one known asteroid crossing the field in six sessions
    // is six tracks but one object. Count objects by name.
    const known = by("known");
    const knownObjects = new Set(known.map((t, i) => t.known_name ?? `#${i}`)).size;
    if (known.length)
      parts.push(
        knownPhrase(knownObjects, known.map((t) => t.known_name)) +
          (known.length > knownObjects ? ` (in ${known.length} sessions)` : ""),
      );
    const sus = by("suspect");
    if (sus.length) {
      // Until the vetting grades a zone, every grade is null: then no parenthesis at all.
      const g = sus.map((t) => grades.get(t.id) ?? null);
      parts.push(g.some((x) => x) ? `${plural(sus.length, "suspect")} (${gradePhrase(g)})` : plural(sus.length, "suspect"));
    }
    const art = by("artifact");
    if (art.length) parts.push(plural(art.length, "artefact"));
    const mov = by("mover");
    if (mov.length) parts.push(plural(mov.length, "other mover"));
    lead = `<b>${dots.toLocaleString("en-GB")}</b> ${dots === 1 ? "dot" : "dots"} in ${photos}`;
    const movers = m.tracks.length - known.length + knownObjects;
    lead += movers
      ? `, and <b>${movers}</b> ${movers === 1 ? "mover" : "movers"}: ${listJoin(parts)}.`
      : ", and no movers.";
    const r: string[] = [];
    if (m.counts.star) r.push(`${plural(m.counts.star, "dot")} ${m.counts.star === 1 ? "matches a Gaia star" : "match Gaia stars"}`);
    // A lone dot where a known asteroid should be (no mover paired it): known dots always survive the cap.
    const loneKnown = m.detections.filter((d) => d.class === "known" && !d.track_id);
    if (loneKnown.length) {
      const names = [...new Set(loneKnown.map((d) => d.known_name).filter((x): x is string => !!x))];
      const who = names.length === 1 ? names[0] : names.length ? "known asteroids" : "a known asteroid";
      r.push(`${loneKnown.length} ${loneKnown.length === 1 ? "sits" : "sit"} where ${who} should be`);
    }
    if (m.counts.other) r.push(`${m.counts.other.toLocaleString("en-GB")} ${m.counts.other === 1 ? "has" : "have"} no Gaia match`);
    if (m.counts.artifact) r.push(`${m.counts.artifact.toLocaleString("en-GB")} ${m.counts.artifact === 1 ? "looks" : "look"} like ${m.counts.artifact === 1 ? "an artefact" : "artefacts"}`);
    if (r.length) rest = `${listJoin(r)}.`;
  } else {
    const src = sourcesByClass(m);
    const n = total(src);
    const atLeast = m.truncated ? "at least " : "";
    const parts: string[] = [];
    // Only the shifts the plate draws an arrow for "moved": the rest are weak or Gaia disagrees.
    const G = gaiaById(gaia);
    const moved = m.shifts.filter((s) => shiftVerdict(s, G).draw);
    const weak = m.shifts.length - moved.length;
    const by = (c: MeasuredClass) => moved.filter((s) => s.class === c).length;
    if (by("star")) parts.push(plural(by("star"), "Gaia star"));
    if (by("suspect")) {
      const g = new Map(m.detections.filter((d) => d.class === "suspect" && d.source_id != null).map((d) => [d.source_id, d.grade]));
      const grades = moved.filter((s) => s.class === "suspect").map((s) => g.get(s.source_id) ?? null);
      parts.push(grades.some((x) => x) ? `${plural(by("suspect"), "suspect")} (${gradePhrase(grades)})` : plural(by("suspect"), "suspect"));
    }
    if (by("artifact")) parts.push(plural(by("artifact"), "artefact"));
    const others = moved.length - by("star") - by("suspect") - by("artifact");
    if (others) parts.push(plural(others, "source without Gaia", "sources without Gaia"));
    lead = `${atLeast ? "At least " : ""}<b>${n.toLocaleString("en-GB")}</b> ${n === 1 ? "source" : "sources"} in ${photos}`;
    lead += moved.length
      ? `, and <b>${moved.length}</b> moved between visits: ${listJoin(parts)}.`
      : ", and none moved measurably between visits.";
    const r: string[] = [];
    if (src.star) r.push(`${atLeast}${src.star.toLocaleString("en-GB")} ${src.star === 1 ? "matches a Gaia star" : "match Gaia stars"}`);
    if (src.other) r.push(`${atLeast}${src.other.toLocaleString("en-GB")} ${src.other === 1 ? "has" : "have"} no Gaia match`);
    if (src.artifact) r.push(`${src.artifact.toLocaleString("en-GB")} ${src.artifact === 1 ? "looks" : "look"} like ${src.artifact === 1 ? "an artefact" : "artefacts"}`);
    if (src.suspect && !by("suspect")) r.push(`${src.suspect} ${src.suspect === 1 ? "is a suspect" : "are suspects"}`);
    if (src.stack) r.push(`${src.stack.toLocaleString("en-GB")} ${src.stack === 1 ? "shows" : "show"} only in the visit stacks, fainter than one photo`);
    const weakNote = weak
      ? `${weak.toLocaleString("en-GB")} weaker ${weak === 1 ? "shift is" : "shifts are"} listed but not drawn: under ${SHIFT_MIN_SNR}σ, or Gaia measures a different motion.`
      : "";
    rest = [weakNote, r.length ? `Of the sources, ${listJoin(r)}.` : ""].filter(Boolean).join(" ");
  }

  const notes: string[] = [stageNote(m)];
  if (m.photos_missing > 0) {
    const all = m.photos + m.photos_missing;
    notes.push(
      `${m.photos_missing} of these ${all} photos ${m.photos_missing === 1 ? "has" : "have"} no dots yet, so ${m.photos_missing === 1 ? "it shows" : "they show"} no marks.`,
    );
  }
  if (m.truncated) {
    const unit = m.clock === "hours" ? "photo" : "visit";
    notes.push(
      `A busy field: each ${unit} shows at most 250 marks, every mover, suspect${opts.artefacts === false ? "" : " and artefact"} first, then the brightest of the rest.`,
    );
  }
  return { lead, rest, notes };
}

/** Classes worth a legend entry here. */
export function classesPresent(m: Measured): MeasuredClass[] {
  const src = m.clock === "months" ? sourcesByClass(m) : null;
  return CLASS_ORDER.filter(
    (c) =>
      (m.counts?.[c] ?? 0) > 0 ||
      (src?.[c] ?? 0) > 0 ||
      (m.tracks ?? []).some((t) => t.class === c) ||
      (m.shifts ?? []).some((s) => s.class === c),
  );
}

// ---- per epoch -------------------------------------------------------------------------------

export function detectionsAt(m: Measured, epochId: string): MeasuredDetection[] {
  return m.detections.filter((d) => d.epoch_id === epochId);
}

const NOTABLE: MeasuredClass[] = ["suspect", "known", "mover", "artifact"];

/** The detections of one epoch worth listing (and reaching by keyboard): suspects first, by S/N. */
export function notableAt(m: Measured, epochId: string): MeasuredDetection[] {
  return detectionsAt(m, epochId)
    .filter((d) => NOTABLE.includes(d.class))
    .sort((a, b) => NOTABLE.indexOf(a.class) - NOTABLE.indexOf(b.class) || b.snr - a.snr);
}

/** Fixed decimals, with thousands separators like the rest of the site (48,159 µJy). */
const num = (x: number, d = 0) =>
  Number.isFinite(x) ? x.toLocaleString("en-GB", { minimumFractionDigits: d, maximumFractionDigits: d }) : "?";
const arcsec = (x: number) => `${num(x, x >= 10 ? 1 : 2)}″`;

/** "Suspect, grade B", "Artefact: ghost, halo", "Known: (433) Eros". */
export function describeDetection(d: MeasuredDetection): string {
  const reasons = reasonWords(d.reasons ?? []);
  const tail = reasons.length ? `: ${reasons.join(", ")}` : "";
  switch (d.class) {
    case "suspect":
      return `Suspect${d.grade ? `, grade ${d.grade}` : ""}${tail}`;
    case "known":
      return d.known_name ? `Known: ${d.known_name}` : "Known asteroid or comet";
    case "mover":
      return `Other mover${tail}`;
    case "artifact":
      return `Artefact${tail || ", reason not given"}`;
    case "star":
      return (d.reasons ?? []).includes("blend") ? blendWords(Math.max(2, extras(d).blend_gaia_ids?.length ?? 2)) : "Gaia star";
    case "stack":
      return "Seen in the stack only";
    default:
      return `Other source${tail}`;
  }
}

/** "S/N 23, 997 µJy" (and the dot count on the months clock). */
export function detectionNumbers(d: MeasuredDetection): string {
  const flux = Math.abs(d.flux_ujy) >= 100 ? num(d.flux_ujy) : num(d.flux_ujy, 1);
  const dots = d.n_dots > 1 ? `, from ${d.n_dots} dots` : "";
  return `S/N ${num(d.snr, d.snr >= 10 ? 0 : 1)}, ${flux} µJy${dots}`;
}

/** "Gaia star moved 0.42″ ± 0.08″ in 1.0 yr". */
export function describeShift(s: MeasuredShift, grade?: string | null): string {
  const name = classInfo(s.class).name;
  const who = s.class === "star" ? "Gaia star" : s.class === "other" ? "Source without Gaia" : grade ? `${name}, grade ${grade},` : name;
  return `${who} moved ${arcsec(s.shift_arcsec)} ± ${arcsec(s.err_arcsec)} in ${num(s.dt_yr, 1)} yr`;
}

/** "420 ± 80 mas/yr, 5.3σ, towards 172°". */
export function shiftNumbers(s: MeasuredShift): string {
  const pm = Math.hypot(s.pmra, s.pmdec);
  return `${num(pm)} ± ${num(s.pm_err)} mas/yr, ${num(s.pm_snr, 1)}σ, towards ${num(s.pa_deg)}° east of north`;
}

// ---- arrows ---------------------------------------------------------------------------------

/** An arrow at least this long (screen pixels) is drawn to scale. */
export const MIN_ARROW_PX = 3;
/** A shorter one is stretched to about this length, by a plain factor printed on it. */
export const STRETCHED_ARROW_PX = 16;
/** Stretch factors: plain numbers people can multiply in their head. */
const STRETCH = [2, 5, 10, 20, 50, 100, 200, 500, 1000];

/**
 * How many times longer than the motion an arrow is drawn: 1 (to scale) when it is long enough to
 * see; else the smallest plain factor that makes it about 16 px; 0 when nothing would show.
 */
export function arrowFactor(lengthPx: number): number {
  if (!(lengthPx > 0.001)) return 0;
  if (lengthPx >= MIN_ARROW_PX) return 1;
  for (const k of STRETCH) if (lengthPx * k >= STRETCHED_ARROW_PX) return k;
  return 0;
}

type Pt = { X: number; Y: number };

export interface Arrow {
  /** The shaft through these points, the head on the last. */
  pts: Pt[];
  /** 1: to scale. More: stretched that many times, printed on the arrow ("×10"). */
  factor: number;
  /** Where the motion really ends (the last point when to scale). */
  end: Pt;
}

/**
 * A motion arrow through screen points in time order (first to last measured position). To scale
 * when it spans at least 3 px; else stretched about its first point by `arrowFactor`, keeping the
 * true end. `along`, when given, replaces the direction and length of a stretched arrow: the fitted
 * motion, steadier than two positions a fraction of a pixel apart. null when nothing would show.
 */
export function motionArrow(pts: Pt[], along?: Pt): Arrow | null {
  if (pts.length < 2) return null;
  const p0 = pts[0];
  const last = pts[pts.length - 1];
  const span = Math.hypot(last.X - p0.X, last.Y - p0.Y);
  if (span >= MIN_ARROW_PX) return { pts, factor: 1, end: last };
  if (along) {
    const k = arrowFactor(Math.hypot(along.X, along.Y));
    if (!k) return null;
    if (k === 1) return { pts: [p0, { X: p0.X + along.X, Y: p0.Y + along.Y }], factor: 1, end: { X: p0.X + along.X, Y: p0.Y + along.Y } };
    return { pts: [p0, { X: p0.X + along.X * k, Y: p0.Y + along.Y * k }], factor: k, end: { X: p0.X + along.X, Y: p0.Y + along.Y } };
  }
  const k = arrowFactor(span);
  if (!k) return null;
  return { pts: pts.map((p) => ({ X: p0.X + (p.X - p0.X) * k, Y: p0.Y + (p.Y - p0.Y) * k })), factor: k, end: last };
}

/** Where a fitted shift puts its source at `mjd`: its position at mjd_from moved by the proper motion. */
export function shiftAt(s: Pick<MeasuredShift, "ra" | "dec" | "pmra" | "pmdec" | "mjd_from">, mjd: number): { ra: number; dec: number } {
  const yr = (mjd - s.mjd_from) / 365.25;
  const dec = s.dec + (s.pmdec * yr) / 3.6e6;
  return { ra: s.ra + (s.pmra * yr) / 3.6e6 / Math.cos((s.dec * Math.PI) / 180), dec };
}

/** A shift is drawn at 5σ or more… */
export const SHIFT_MIN_SNR = 5;
/** …with our errors scaled up like this before comparing with Gaia (pos_err is 1.4–1.8× too small, CLAUDE.md). */
export const ERR_SCALE = 1.6;

export type ShiftWhy = "gaia_agrees" | "significant" | "gaia_disagrees" | "weak" | "artifact";

export interface ShiftVerdict {
  draw: boolean;
  why: ShiftWhy;
  /** Gaia's proper motion for the star, mas/yr, when known. */
  gaiaPm?: number;
}

/**
 * Is a shift worth an arrow on the plate? A Gaia star's motion is Gaia's to confirm, since Gaia is
 * far more precise: drawn when Gaia measures a motion we could see (3σ of ours) and ours agrees
 * within 3σ (errors × 1.6); not drawn when it disagrees, as a saturated star's fake westward shift
 * does. Without a Gaia motion: drawn at 5σ or more, unless the checks called it an artefact.
 */
export function shiftVerdict(s: MeasuredShift, gaia?: Map<string, GaiaStar>): ShiftVerdict {
  const g = s.gaia_id ? gaia?.get(s.gaia_id) : undefined;
  if (g && g.pmra != null && g.pmdec != null) {
    const gaiaPm = Math.hypot(g.pmra, g.pmdec);
    const diff = Math.hypot(s.pmra - g.pmra, s.pmdec - g.pmdec);
    if (diff <= 3 * ERR_SCALE * s.pm_err && gaiaPm >= 3 * s.pm_err) return { draw: true, why: "gaia_agrees", gaiaPm };
    return { draw: false, why: "gaia_disagrees", gaiaPm };
  }
  if (s.class === "artifact") return { draw: false, why: "artifact" };
  if (s.pm_snr >= SHIFT_MIN_SNR) return { draw: true, why: "significant" };
  return { draw: false, why: "weak" };
}

/** Why a shift has no arrow, one sentence; "" when it has one. */
export function verdictWords(v: ShiftVerdict): string {
  if (v.draw) return "";
  switch (v.why) {
    case "gaia_disagrees":
      return `Not on the plate: Gaia measures ${num(v.gaiaPm ?? 0)} mas/yr for this star.`;
    case "artifact":
      return "Not on the plate: our checks set it aside.";
    default:
      return `Not on the plate: under ${SHIFT_MIN_SNR}σ.`;
  }
}

export const gaiaById = (gaia: GaiaStar[] | null | undefined): Map<string, GaiaStar> => new Map((gaia ?? []).map((g) => [g.id, g]));

/** "measured 2.1″ from Gaia's position", when a circle sits more than 1.5″ off its ring; else "". */
export const OFFSET_NOTE_ARCSEC = 1.5;
export function offsetWords(arcsec: number | undefined): string {
  return arcsec != null && arcsec > OFFSET_NOTE_ARCSEC ? `measured ${arcsec.toFixed(1)}″ from Gaia's position` : "";
}

/** The words for a blend of n Gaia stars. */
export const blendWords = (n: number): string => `${n} Gaia stars, blended in SPHEREx's 6″ pixels`;

/** Sentence for the legend: how arrows are drawn. */
export function arrowNote(clock: "hours" | "months"): string {
  const stretched = "One too short to see is drawn longer, with its factor (×10) beside it and a small dot where it really ends.";
  return clock === "hours"
    ? `Arrows join each mover's dots in time order, to scale. ${stretched}`
    : `Arrows run from where we measured a source in the first visit to where we measured it in the last, to scale. ` +
        `Only shifts of ${SHIFT_MIN_SNR}σ or more, or that Gaia's own motion confirms, get one; the rest are in the list. ${stretched}`;
}

/** Offset (arcsec, east and north) between two positions, small-angle. */
export function offsetArcsec(a: { ra: number; dec: number }, b: { ra: number; dec: number }): { east: number; north: number } {
  let dra = b.ra - a.ra;
  if (dra > 180) dra -= 360;
  if (dra < -180) dra += 360;
  return { east: dra * Math.cos(((a.dec + b.dec) / 2) * (Math.PI / 180)) * 3600, north: (b.dec - a.dec) * 3600 };
}

/**
 * A track's dots that belong on one detector's plate, in time order. Dots of the partner detector
 * (D2 with D5, …) are the same moment in another colour, never motion: they stay off the arrow.
 */
export function trackDotsFor(t: MeasuredTrack, epochDet: Map<string, number>, det: number): MeasuredTrack["dots"] {
  return t.dots.filter((d) => epochDet.get(d.epoch_id) === det).sort((a, b) => a.mjd - b.mjd);
}

/** How far a track moved on a plate: first to last of its dots there, arcsec. */
export function trackSpanArcsec(dots: MeasuredTrack["dots"]): number {
  if (dots.length < 2) return 0;
  const o = offsetArcsec(dots[0], dots[dots.length - 1]);
  return Math.hypot(o.east, o.north);
}

// ---- the list under the summary: what can be reached by keyboard ------------------------------

/** One entry of the list: a mover (hours), a fitted shift (months) or a lone notable detection. */
export interface MeasuredItem {
  key: string;
  cls: MeasuredClass;
  label: string;
  numbers: string;
  /** Where to mark it, per epoch id of this detector; `pos` when the epoch has none. */
  at: Record<string, { ra: number; dec: number }>;
  pos: { ra: number; dec: number };
  /** Its epochs on this detector, in time order (empty for a shift: it spans them all). */
  epochs: string[];
  track?: string;
  shift?: number;
  /** A suspect's grade, when graded. */
  grade?: MeasuredDetection["grade"];
  /** A shift without an arrow on the plate (under 5σ, or Gaia disagrees). */
  undrawn?: boolean;
}

/** Grade C suspects fail a check: drawn greyed like artefacts, still labelled "Suspect, grade C". */
export const greyed = (cls: MeasuredClass, grade?: string | null): boolean =>
  cls === "artifact" || (cls === "suspect" && grade === "C");

/**
 * The measured layer without artefacts (camera glitches): no dots, movers or shifts of that class,
 * and a zero count. The Viewer and the To check plates (BlinkOptions.hideArtefacts) show this, so
 * the plate, legend, summary and list all leave them out. A new object: the response is untouched.
 */
export function withoutArtefacts(m: Measured): Measured {
  const keep = <T extends { class: MeasuredClass }>(xs: T[] | undefined) => (xs ?? []).filter((x) => x.class !== "artifact");
  return {
    ...m,
    counts: { ...m.counts, artifact: 0 },
    detections: keep(m.detections),
    tracks: keep(m.tracks),
    shifts: keep(m.shifts),
  };
}

// ---- the Viewer's plain version (BlinkOptions.viewer) -----------------------------------------
// The Viewer draws only marks a visitor can act on, and says them in plain words: no grey circles
// (artefacts, sources without a Gaia match, stack-only sources, grade C suspects), no σ, mas/yr,
// S/N or µJy. The To check and Learn plates keep the full version above.

/** Classes the Viewer neither circles nor puts in its legend. */
export const VIEWER_HIDDEN: ReadonlySet<MeasuredClass> = new Set<MeasuredClass>(["artifact", "other", "stack"]);
/** Does the Viewer circle this detection? */
export const viewerShows = (cls: MeasuredClass, grade?: string | null): boolean => !VIEWER_HIDDEN.has(cls) && !greyed(cls, grade);

const COMPASS = [
  "north", "north-northeast", "northeast", "east-northeast", "east", "east-southeast", "southeast", "south-southeast",
  "south", "south-southwest", "southwest", "west-southwest", "west", "west-northwest", "northwest", "north-northwest",
];
/** A position angle (degrees east of north) as a compass direction: 163 → "south-southeast". */
export const compass = (deg: number): string => COMPASS[Math.round((((deg % 360) + 360) % 360) / 22.5) % 16];

/** "2 Gaia stars too close together for SPHEREx to tell apart". */
export const plainBlendWords = (n: number): string => `${n} Gaia stars too close together for SPHEREx to tell apart`;

/** Why a shift has no arrow, in plain words ("" when it has one). */
export function plainVerdict(v: ShiftVerdict): string {
  if (v.draw) return "";
  switch (v.why) {
    case "gaia_disagrees": {
      const pm = (v.gaiaPm ?? 0) / 1000;
      return `No arrow on the plate: Gaia measures only ${num(pm, pm < 0.1 ? 3 : 2)}″ a year for this star.`;
    }
    case "artifact":
      return "No arrow on the plate: our checks set it aside.";
    default:
      return "No arrow on the plate: too small to be sure.";
  }
}

/** A shift in plain words: its direction, and why it has no arrow when it has none. */
export function plainShiftWords(s: MeasuredShift, v?: ShiftVerdict): string {
  return [`Towards the ${compass(s.pa_deg)}.`, v ? plainVerdict(v) : ""].filter(Boolean).join(" ");
}

const ITEM_ORDER: MeasuredClass[] = ["suspect", "known", "mover", "artifact", "star", "other"];
/** Suspects A, B, ungraded, then C (after the other movers, before artefacts). */
const rank = (it: MeasuredItem) => (it.cls === "suspect" && it.grade === "C" ? 2.5 : ITEM_ORDER.indexOf(it.cls));
const byClass = (a: MeasuredItem, b: MeasuredItem) => rank(a) - rank(b) || (a.grade ?? "Z").localeCompare(b.grade ?? "Z");

function span(days: number): string {
  const m = days * 1440;
  if (m < 90) return `${m < 10 ? m.toFixed(1) : m.toFixed(0)} min`;
  const h = m / 60;
  return h < 48 ? `${h.toFixed(h < 10 ? 1 : 0)} h` : `${(h / 24).toFixed(1)} days`;
}

/**
 * The movers, shifts and notable detections on one detector's plate, most important first.
 * Hours: each track with a dot on this detector, then lone known/suspect/mover/artefact dots.
 * Months: each fitted shift, then suspect or artefact sources without one.
 */
export function listItems(m: Measured, epochDet: Map<string, number>, det: number, gaia?: GaiaStar[], plain = false): MeasuredItem[] {
  const onDet = (id: string) => epochDet.get(id) === det;
  const items: MeasuredItem[] = [];
  /** The Viewer's version: photos instead of dots, no S/N or µJy. */
  const seen = (n: number) => `Seen in ${plural(n, "photo")}`;
  if (m.clock === "hours") {
    // One entry per known object, however many sessions paired it.
    const knownBy = new Map<string, MeasuredTrack[]>();
    for (const t of m.tracks)
      if (t.class === "known" && t.known_name && trackDotsFor(t, epochDet, det).length)
        knownBy.set(t.known_name, [...(knownBy.get(t.known_name) ?? []), t]);
    for (const [name, ts] of knownBy) {
      if (ts.length < 2) continue;
      const dots = ts.flatMap((t) => trackDotsFor(t, epochDet, det)).sort((a, b) => a.mjd - b.mjd);
      const d = m.detections.find((x) => x.track_id === ts[0].id && onDet(x.epoch_id));
      items.push({
        key: `k:${name}`,
        cls: "known",
        label: `Known: ${name}`,
        numbers: plain
          ? `${seen(dots.length)}, over ${ts.length} sessions.`
          : `${plural(dots.length, "dot")} in ${ts.length} sessions${d ? `. ${detectionNumbers(d)}` : ""}`,
        at: Object.fromEntries(dots.map((x) => [x.epoch_id, { ra: x.ra, dec: x.dec }])),
        pos: { ra: dots[0].ra, dec: dots[0].dec },
        epochs: dots.map((x) => x.epoch_id),
        track: ts[0].id,
      });
    }
    for (const t of m.tracks) {
      if (t.class === "known" && t.known_name && (knownBy.get(t.known_name)?.length ?? 0) > 1) continue;
      const dots = trackDotsFor(t, epochDet, det);
      if (!dots.length) continue;
      const ds = m.detections.filter((d) => d.track_id === t.id);
      const d = ds.find((x) => onDet(x.epoch_id)) ?? ds[0];
      const label =
        t.class === "known"
          ? `Known: ${t.known_name ?? d?.known_name ?? "asteroid or comet"}`
          : d
            ? describeDetection({ ...d, class: t.class })
            : classInfo(t.class).name;
      const apart = dots.length > 1 ? `, ${span(dots[dots.length - 1].mjd - dots[0].mjd)} apart` : "";
      const how = t.evidence === "two_detectors" ? ". Seen in two detectors at once" : "";
      items.push({
        key: `t:${t.id}`,
        cls: t.class,
        label,
        numbers: plain
          ? `${seen(dots.length)}${apart}${how}.`
          : `${plural(dots.length, "dot")}${apart}${how}${d ? `. ${detectionNumbers(d)}` : ""}`,
        at: Object.fromEntries(dots.map((x) => [x.epoch_id, { ra: x.ra, dec: x.dec }])),
        pos: { ra: dots[0].ra, dec: dots[0].dec },
        epochs: dots.map((x) => x.epoch_id),
        track: t.id,
        grade: t.class === "suspect" ? (ds.find((x) => x.grade)?.grade ?? null) : undefined,
      });
    }
    // Lone dots: known ones grouped by name; the rest grouped when the same class sits within 4″
    // in several exposures (a ghost stays put), so one artefact is one entry.
    const lone = m.detections.filter((d) => !d.track_id && onDet(d.epoch_id) && NOTABLE.includes(d.class));
    const groups = new Map<string, MeasuredDetection[]>();
    lone.forEach((d, i) => {
      let k = d.class === "known" && d.known_name ? `k:${d.known_name}` : "";
      if (!k)
        for (const [gk, g] of groups) {
          const o = offsetArcsec(g[0], d);
          if (gk.startsWith("d:") && g[0].class === d.class && Math.hypot(o.east, o.north) <= 4) k = gk;
        }
      k ||= `d:${d.epoch_id}:${i}`;
      groups.set(k, [...(groups.get(k) ?? []), d]);
    });
    for (const [k, ds] of groups) {
      const d = ds[0];
      const n = new Set(ds.map((x) => x.epoch_id)).size;
      items.push({
        key: k,
        cls: d.class,
        label: describeDetection(d),
        numbers: plain ? `${seen(n)}.` : n > 1 ? `In ${n} exposures. ${detectionNumbers(d)}` : detectionNumbers(d),
        at: Object.fromEntries(ds.map((x) => [x.epoch_id, { ra: x.ra, dec: x.dec }])),
        pos: { ra: d.ra, dec: d.dec },
        epochs: ds.map((x) => x.epoch_id),
        grade: d.grade,
      });
    }
  } else {
    const withShift = new Set<number>();
    const gradeOf = new Map(m.detections.filter((d) => d.grade && d.source_id != null).map((d) => [d.source_id, d.grade]));
    const G = gaiaById(gaia);
    m.shifts.forEach((s, i) => {
      withShift.add(s.source_id);
      const grade = s.class === "suspect" ? (gradeOf.get(s.source_id) ?? null) : undefined;
      const v = shiftVerdict(s, G);
      const g = s.gaia_id ? G.get(s.gaia_id) : undefined;
      const notes: string[] = [];
      const n = blendCount(s, m, gaia ?? []);
      if (n > 1) notes.push(`${plain ? plainBlendWords(n) : blendWords(n)}.`);
      if (g && n < 2) {
        const o = offsetArcsec(gaiaAt(g, s.mjd_from), s);
        const w = offsetWords(Math.hypot(o.east, o.north));
        if (w) notes.push(`${w[0].toUpperCase()}${w.slice(1)}.`);
      }
      if (!v.draw) notes.push(verdictWords(v));
      items.push({
        key: `s:${s.source_id}`,
        cls: s.class,
        grade,
        label: describeShift(s, grade),
        numbers: plain
          ? [`Towards the ${compass(s.pa_deg)}.`, ...notes.filter((x) => !x.startsWith("Not on the plate")), plainVerdict(v)].filter(Boolean).join(" ")
          : [shiftNumbers(s) + (notes.length ? "." : ""), ...notes].join(" "),
        at: {},
        pos: { ra: s.ra, dec: s.dec },
        epochs: [],
        shift: i,
        undrawn: !v.draw || undefined,
      });
    });
    const src = new Map<number, MeasuredDetection[]>();
    for (const d of m.detections)
      if ((d.class === "suspect" || d.class === "artifact") && d.source_id != null && !withShift.has(d.source_id))
        src.set(d.source_id, [...(src.get(d.source_id) ?? []), d]);
    for (const [id, ds] of src) {
      const mine = ds.filter((d) => onDet(d.epoch_id));
      const d = mine[0] ?? ds[0];
      items.push({
        key: `src:${id}`,
        cls: d.class,
        label: describeDetection(d),
        numbers: plain
          ? `Seen in ${plural(new Set(ds.map((x) => x.epoch_id)).size, "visit")}.`
          : `In ${plural(new Set(ds.map((x) => x.epoch_id)).size, "visit")}. ${detectionNumbers(d)}`,
        at: Object.fromEntries(mine.map((x) => [x.epoch_id, { ra: x.ra, dec: x.dec }])),
        pos: { ra: d.ra, dec: d.dec },
        epochs: mine.map((x) => x.epoch_id),
        grade: d.grade,
      });
    }
  }
  // Shifts with an arrow first, then class, then as listed (tracks in the server's order, shifts by pm_snr).
  // The Viewer leaves artefacts out (it doesn't draw them either).
  return items
    .filter((it) => !plain || it.cls !== "artifact")
    .map((it, i) => ({ it, i }))
    .sort((a, b) => Number(!!a.it.undrawn) - Number(!!b.it.undrawn) || byClass(a.it, b.it) || a.i - b.i)
    .map((x) => x.it);
}

/**
 * How many Gaia stars a shift's source blends (1: none). From its detections' `blend` reason when
 * the server sends one; else Gaia stars within 9″ of its own at mjd_from, at most 3 mag fainter,
 * with no other source measured within 4″ of them.
 */
export function blendCount(s: MeasuredShift, m: Measured, gaia: GaiaStar[]): number {
  const mine = m.detections.filter((d) => d.source_id === s.source_id);
  const told = mine.find((d) => (d.reasons ?? []).includes("blend"));
  if (told) return Math.max(2, extras(told).blend_gaia_ids?.length ?? 2);
  const g0 = s.gaia_id ? gaia.find((g) => g.id === s.gaia_id) : undefined;
  if (!g0) return 1;
  const p0 = gaiaAt(g0, s.mjd_from);
  const near = (a: { ra: number; dec: number }, b: { ra: number; dec: number }, r: number) => {
    const o = offsetArcsec(a, b);
    return Math.hypot(o.east, o.north) <= r;
  };
  const others = m.detections.filter((d) => d.source_id !== s.source_id);
  let n = 1;
  for (const g of gaia) {
    // A partner SPHEREx could see (the plate rings G 16.5 and brighter), at most 3 mag fainter.
    if (g.id === g0.id || g.g == null || g.g > 16.5 || (g0.g != null && g.g - g0.g > 3)) continue;
    const p = gaiaAt(g, s.mjd_from);
    if (!near(p0, p, 9)) continue;
    if (others.some((d) => near(d, p, 4))) continue;
    n++;
  }
  return n;
}
