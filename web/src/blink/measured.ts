// What SkyShift measured (contracts/api.md v3.3, `measured` on /api/frames): the words and numbers
// behind the blink's Measured marks and its summary panel. Pure functions, no DOM, so they test
// without a canvas. Drawing lives in index.ts and marks.ts.
import type {
  FramesResponse,
  Measured,
  MeasuredClass,
  MeasuredDetection,
  MeasuredReason,
  MeasuredShift,
  MeasuredTrack,
} from "../api/types";

/** Legend and drawing order: what matters most first (drawn last, so it sits on top). */
export const CLASS_ORDER: MeasuredClass[] = ["suspect", "known", "mover", "artifact", "star", "other"];

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
};

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
  flux_ratio: "brightness jumps",
  red_photo: "photo failed the quality check",
  "2mass": "a 2MASS source sits here",
  near_gaia: "near a Gaia star",
};

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
  if (f?.measured && typeof f.measured === "object") return { kind: "measured", m: f.measured };
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
    if (!seen.has(k)) seen.set(k, d.class);
  });
  const out = { star: 0, known: 0, mover: 0, suspect: 0, artifact: 0, other: 0 } as Record<MeasuredClass, number>;
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

/** "Here SkyShift found N dots in P photos, and M movers: K known asteroids, J suspects (grades), L artefacts." */
export function summarize(m: Measured): Summary {
  const photos = plural(m.photos, "photo");
  let lead: string;
  let rest = "";
  if (m.clock === "hours") {
    const dots = total(m.counts);
    const by = (c: MeasuredTrack["class"]) => m.tracks.filter((t) => t.class === c);
    const grades = trackGrades(m);
    const parts: string[] = [];
    const known = by("known");
    if (known.length) parts.push(knownPhrase(known.length, known.map((t) => t.known_name)));
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
    lead = `Here SkyShift found <b>${dots.toLocaleString("en-GB")}</b> ${dots === 1 ? "dot" : "dots"} in ${photos}`;
    lead += m.tracks.length
      ? `, and <b>${m.tracks.length}</b> ${m.tracks.length === 1 ? "mover" : "movers"}: ${listJoin(parts)}.`
      : ", and no movers.";
    const r: string[] = [];
    if (m.counts.star) r.push(`${plural(m.counts.star, "dot")} ${m.counts.star === 1 ? "matches a Gaia star" : "match Gaia stars"}`);
    if (m.counts.other) r.push(`${m.counts.other.toLocaleString("en-GB")} ${m.counts.other === 1 ? "has" : "have"} no Gaia match`);
    if (m.counts.artifact) r.push(`${m.counts.artifact.toLocaleString("en-GB")} ${m.counts.artifact === 1 ? "looks" : "look"} like ${m.counts.artifact === 1 ? "an artefact" : "artefacts"}`);
    if (r.length) rest = `${listJoin(r)}.`;
  } else {
    const src = sourcesByClass(m);
    const n = total(src);
    const atLeast = m.truncated ? "at least " : "";
    const parts: string[] = [];
    const by = (c: MeasuredClass) => m.shifts.filter((s) => s.class === c).length;
    if (by("star")) parts.push(plural(by("star"), "Gaia star"));
    if (by("suspect")) {
      const g = new Map(m.detections.filter((d) => d.class === "suspect" && d.source_id != null).map((d) => [d.source_id, d.grade]));
      const grades = m.shifts.filter((s) => s.class === "suspect").map((s) => g.get(s.source_id) ?? null);
      parts.push(grades.some((x) => x) ? `${plural(by("suspect"), "suspect")} (${gradePhrase(grades)})` : plural(by("suspect"), "suspect"));
    }
    if (by("artifact")) parts.push(plural(by("artifact"), "artefact"));
    const others = m.shifts.length - by("star") - by("suspect") - by("artifact");
    if (others) parts.push(plural(others, "source without Gaia", "sources without Gaia"));
    lead = `Here SkyShift found ${atLeast}<b>${n.toLocaleString("en-GB")}</b> ${n === 1 ? "source" : "sources"} in ${photos}`;
    lead += m.shifts.length
      ? `, and <b>${m.shifts.length}</b> moved between visits: ${listJoin(parts)}.`
      : ", and none moved measurably between visits.";
    const r: string[] = [];
    if (src.star) r.push(`${atLeast}${src.star.toLocaleString("en-GB")} ${src.star === 1 ? "matches a Gaia star" : "match Gaia stars"}`);
    if (src.other) r.push(`${atLeast}${src.other.toLocaleString("en-GB")} ${src.other === 1 ? "has" : "have"} no Gaia match`);
    if (src.artifact) r.push(`${src.artifact.toLocaleString("en-GB")} ${src.artifact === 1 ? "looks" : "look"} like ${src.artifact === 1 ? "an artefact" : "artefacts"}`);
    if (src.suspect && !by("suspect")) r.push(`${src.suspect} ${src.suspect === 1 ? "is a suspect" : "are suspects"}`);
    if (r.length) rest = `Of the sources, ${listJoin(r)}.`;
  }

  const notes: string[] = [];
  notes.push(
    m.stage === "final"
      ? `Final results from the ${m.zone} zone.`
      : `Provisional results from the ${m.zone} zone, checked against Gaia only: expect more suspects than the final pass keeps.`,
  );
  if (m.photos_missing > 0) {
    const all = m.photos + m.photos_missing;
    notes.push(
      `${m.photos_missing} of these ${all} photos ${m.photos_missing === 1 ? "has" : "have"} no dots yet, so ${m.photos_missing === 1 ? "it shows" : "they show"} no marks.`,
    );
  }
  if (m.truncated) {
    const unit = m.clock === "hours" ? "photo" : "visit";
    notes.push(
      `A busy field: each ${unit} shows at most 250 marks, every mover, suspect and artefact first, then the brightest of the rest.`,
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
      m.tracks.some((t) => t.class === c) ||
      m.shifts.some((s) => s.class === c),
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

const num = (x: number, d = 0) => (Number.isFinite(x) ? x.toFixed(d) : "?");
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
      return "Gaia star";
    default:
      return "Other source";
  }
}

/** "S/N 23, 997 µJy" (and the dot count on the months clock). */
export function detectionNumbers(d: MeasuredDetection): string {
  const flux = Math.abs(d.flux_ujy) >= 100 ? num(d.flux_ujy) : num(d.flux_ujy, 1);
  const dots = d.n_dots > 1 ? `, from ${d.n_dots} dots` : "";
  return `S/N ${num(d.snr, d.snr >= 10 ? 0 : 1)}, ${flux} µJy${dots}`;
}

/** "Gaia star moved 0.42″ ± 0.08″ in 1.0 yr". */
export function describeShift(s: MeasuredShift): string {
  const who = s.class === "star" ? "Gaia star" : s.class === "other" ? "Source without Gaia" : CLASS_INFO[s.class].name;
  return `${who} moved ${arcsec(s.shift_arcsec)} ± ${arcsec(s.err_arcsec)} in ${num(s.dt_yr, 1)} yr`;
}

/** "420 ± 80 mas/yr, 5.3σ, towards 172°". */
export function shiftNumbers(s: MeasuredShift): string {
  const pm = Math.hypot(s.pmra, s.pmdec);
  return `${num(pm)} ± ${num(s.pm_err)} mas/yr, ${num(s.pm_snr, 1)}σ, towards ${num(s.pa_deg)}° east of north`;
}

// ---- arrows ---------------------------------------------------------------------------------

/** Exaggeration factors: plain numbers people can multiply in their head. */
const NICE = [1, 2, 5, 10, 20, 50, 100, 200, 500];

/**
 * How much to stretch motion arrows so a typical one is visible: the largest nice factor that keeps
 * the median motion (arcsec) at or under `target` arcsec (a share of the field). 1 when nothing moves
 * or motion is already that large.
 */
export function exaggeration(lengthsArcsec: number[], targetArcsec: number): number {
  const ls = lengthsArcsec.filter((x) => Number.isFinite(x) && x > 0.005).sort((a, b) => a - b);
  if (!ls.length) return 1;
  const med = ls[ls.length >> 1];
  let f = 1;
  for (const k of NICE) if (med * k <= targetArcsec) f = k;
  return f;
}

/** Sentence for the legend: how arrows are drawn. */
export function arrowNote(clock: "hours" | "months", factor: number): string {
  const how = factor > 1 ? `drawn ${factor} times longer than ${clock === "hours" ? "the motion" : "it is"}` : "drawn to scale";
  return clock === "hours"
    ? `Arrows join each mover's dots in time order, ${how}.`
    : `Arrows show each measured shift from the first visit to the last, ${how}. The circle at the tip is its uncertainty (1σ).`;
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
}

const ITEM_ORDER: MeasuredClass[] = ["suspect", "known", "mover", "artifact", "star", "other"];
const byClass = (a: MeasuredItem, b: MeasuredItem) => ITEM_ORDER.indexOf(a.cls) - ITEM_ORDER.indexOf(b.cls);

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
export function listItems(m: Measured, epochDet: Map<string, number>, det: number): MeasuredItem[] {
  const onDet = (id: string) => epochDet.get(id) === det;
  const items: MeasuredItem[] = [];
  if (m.clock === "hours") {
    for (const t of m.tracks) {
      const dots = trackDotsFor(t, epochDet, det);
      if (!dots.length) continue;
      const ds = m.detections.filter((d) => d.track_id === t.id);
      const d = ds.find((x) => onDet(x.epoch_id)) ?? ds[0];
      const label =
        t.class === "known"
          ? `Known: ${t.known_name ?? d?.known_name ?? "asteroid or comet"}`
          : d
            ? describeDetection({ ...d, class: t.class })
            : CLASS_INFO[t.class].name;
      const apart = dots.length > 1 ? `, ${span(dots[dots.length - 1].mjd - dots[0].mjd)} apart` : "";
      const how = t.evidence === "two_detectors" ? ". Seen in two detectors at once" : "";
      items.push({
        key: `t:${t.id}`,
        cls: t.class,
        label,
        numbers: `${plural(dots.length, "dot")}${apart}${how}${d ? `. ${detectionNumbers(d)}` : ""}`,
        at: Object.fromEntries(dots.map((x) => [x.epoch_id, { ra: x.ra, dec: x.dec }])),
        pos: { ra: dots[0].ra, dec: dots[0].dec },
        epochs: dots.map((x) => x.epoch_id),
        track: t.id,
      });
    }
    // Lone dots: known ones grouped by name, the rest one by one.
    const lone = m.detections.filter((d) => !d.track_id && onDet(d.epoch_id) && NOTABLE.includes(d.class));
    const groups = new Map<string, MeasuredDetection[]>();
    lone.forEach((d, i) => {
      const k = d.class === "known" && d.known_name ? `k:${d.known_name}` : `d:${d.epoch_id}:${i}`;
      groups.set(k, [...(groups.get(k) ?? []), d]);
    });
    for (const [k, ds] of groups) {
      const d = ds[0];
      items.push({
        key: k,
        cls: d.class,
        label: describeDetection(d),
        numbers: ds.length > 1 ? plural(ds.length, "dot") : detectionNumbers(d),
        at: Object.fromEntries(ds.map((x) => [x.epoch_id, { ra: x.ra, dec: x.dec }])),
        pos: { ra: d.ra, dec: d.dec },
        epochs: ds.map((x) => x.epoch_id),
      });
    }
  } else {
    const withShift = new Set<number>();
    m.shifts.forEach((s, i) => {
      withShift.add(s.source_id);
      items.push({
        key: `s:${s.source_id}`,
        cls: s.class,
        label: describeShift(s),
        numbers: shiftNumbers(s),
        at: {},
        pos: { ra: s.ra, dec: s.dec },
        epochs: [],
        shift: i,
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
        numbers: `In ${plural(new Set(ds.map((x) => x.epoch_id)).size, "visit")}. ${detectionNumbers(d)}`,
        at: Object.fromEntries(mine.map((x) => [x.epoch_id, { ra: x.ra, dec: x.dec }])),
        pos: { ra: d.ra, dec: d.dec },
        epochs: mine.map((x) => x.epoch_id),
      });
    }
  }
  // Class first, then as listed (tracks in the server's order, shifts by pm_snr).
  return items
    .map((it, i) => ({ it, i }))
    .sort((a, b) => byClass(a.it, b.it) || a.i - b.i)
    .map((x) => x.it);
}
