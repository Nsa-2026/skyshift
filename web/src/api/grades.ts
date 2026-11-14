// The pipeline's vetting grades (contracts/api.md, suspects.json and candidates.json, added 14 Nov)
// in plain words, and the one ranking every page uses. Pure: no DOM.
//
//   A  "candidate mover": passes every gate and is on a track over ≥ 2 sessions at ≥ 5σ (api v3.4: the
//      pipeline's tracklets step; the track is in n_sessions, rate_arcsec_h, pa_deg, rate_sigma).
//   B  "unexplained single-session source": passes every gate; its motion is not shown.
//   C  fails a gate or sits on a catalogued source: shown greyed with its reasons, never put to a vote.
//   no grade  a file (or zone) written before the vetting: "not vetted yet", never shown as B.
//
// Grades are read, never guessed: gradeOf() returns null unless the file says A, B or C.
import type { Grade } from "./types";

export type { Grade } from "./types";

export const GRADES: readonly Grade[] = ["A", "B", "C"];

/** The grade a row carries, or null when it has none (an older, unvetted file; or a kind that isn't graded). */
export function gradeOf(x: { grade?: unknown } | null | undefined): Grade | null {
  const g = x?.grade;
  if (typeof g !== "string") return null;
  const u = g.trim().toUpperCase();
  return u === "A" || u === "B" || u === "C" ? u : null;
}

/** A, B, C, then ungraded (contracts/api.md suspects.json: "ranked by grade, then S/N"). */
export function gradeRank(g: Grade | null): number {
  return g === "A" ? 0 : g === "B" ? 1 : g === "C" ? 2 : 3;
}

/** Grade order for lists that mix kinds (candidates.json: A, then B or no grade, then C): C never leads. */
export function leadRank(g: Grade | null): number {
  return g === "A" ? 0 : g === "C" ? 2 : 1;
}

const snrOf = (x: { snr?: unknown }) => (typeof x.snr === "number" && Number.isFinite(x.snr) ? x.snr : -Infinity);

/** Sort rows by grade (A, B, C, then ungraded), then S/N, highest first. Stable for equal keys. */
export function byGradeThenSnr<T extends { grade?: unknown; snr?: unknown }>(a: T, b: T): number {
  return gradeRank(gradeOf(a)) - gradeRank(gradeOf(b)) || snrOf(b) - snrOf(a);
}

export function rankByGrade<T extends { grade?: unknown; snr?: unknown }>(rows: readonly T[]): T[] {
  return [...rows].sort(byGradeThenSnr);
}

/** Grade C objects can be opened and blinked, but nobody is asked to vote on them. */
export function votable(x: { grade?: unknown }): boolean {
  return gradeOf(x) !== "C";
}

// ---- words ---------------------------------------------------------------------------------------

export interface GradeText {
  /** "Grade B". */
  short: string;
  /** What it means, as a name: "Unexplained single-session source". */
  name: string;
  /** One plain sentence. */
  plain: string;
}

export const GRADE_TEXT: Record<Grade, GradeText> = {
  A: {
    short: "Grade A",
    name: "Candidate mover",
    plain: "Passes every pipeline check and sits on a track over two or more sessions, moving at 5σ or more.",
  },
  B: {
    short: "Grade B",
    name: "Unexplained single-session source",
    plain: "Passes every pipeline check, but its motion isn't shown: most are two dots minutes apart in one session.",
  },
  C: {
    short: "Grade C",
    name: "Fails a check",
    plain: "Fails a pipeline check or sits on a catalogued source: most likely an artefact. It stays listed so anyone can see why.",
  },
};

/** For a mover without a grade: found before the vetting ran. Never shown as B. */
export const UNGRADED_TEXT: GradeText = {
  short: "Not vetted yet",
  name: "Not vetted yet",
  plain: "Found before the pipeline's vetting checks ran, so it has no grade yet.",
};

/** "Grade B: unexplained single-session source". */
export function gradeLabel(g: Grade): string {
  return `${GRADE_TEXT[g].short}: ${GRADE_TEXT[g].name.charAt(0).toLowerCase()}${GRADE_TEXT[g].name.slice(1)}`;
}

/** The vetting gates, in the contract's order (contracts/api.md suspects.json "reasons"). */
export const GATES = [
  "not_vetted",
  "hot_pixel",
  "bad_column",
  "edge",
  "masked_peak",
  "sharp_outlier",
  "very_sharp",
  "flux_ratio",
  "red_photo",
  "2mass",
  "allwise",
] as const;
export type Gate = (typeof GATES)[number];

export function isGate(code: string): code is Gate {
  return (GATES as readonly string[]).includes(code);
}

export interface GateText {
  /** The glossary's word for it. */
  term: string;
  /** Short chip text. */
  label: string;
  /** What it means, in one or two plain sentences (≤ 200 characters: the glossary uses them). */
  plain: string;
}

export const GATE_TEXT: Record<Gate, GateText> = {
  not_vetted: {
    term: "Not vetted",
    label: "No dot data to vet",
    plain: "The pipeline had no dot data to vet it with: an older movers table, or a dot without a pixel position.",
  },
  hot_pixel: {
    term: "Hot pixel",
    label: "On a hot pixel",
    plain: "A dot sits on a detector pixel where dots keep turning up in many exposures: a hot pixel that NASA's HOT flag missed.",
  },
  bad_column: {
    term: "Bad column",
    label: "On a bad column",
    plain: "Three or more loose dots of the same photo line up in one pixel column: a faulty column of the detector, not a source.",
  },
  edge: {
    term: "Detector edge",
    label: "At the detector edge",
    plain: "A dot lies within 6 pixels of the detector's edge, where measurements can't be trusted.",
  },
  masked_peak: {
    term: "Masked peak",
    label: "Bad pixel at its peak",
    plain: "NASA's pipeline flags the pixel at a dot's peak as untrustworthy, for example a cosmic ray, persistence or a cold pixel.",
  },
  sharp_outlier: {
    term: "Sharp outlier",
    label: "Sharper than a star",
    plain: "A dot is much sharper than a star of the same brightness and its peak is flagged as an outlier: usually a cosmic ray.",
  },
  very_sharp: {
    term: "Very sharp",
    label: "Far too sharp",
    plain: "A bright dot (S/N 15 or more) is less than 0.4 times a star's width. Light from the sky is never that sharp: it's a pixel event.",
  },
  flux_ratio: {
    term: "Brightness jump",
    label: "Brightness jumps",
    plain: "Its brightest dot is over 4 times its faintest in the same detector: one object doesn't change that much in minutes.",
  },
  red_photo: {
    term: "Red-graded photo",
    label: "Only in bad photos",
    plain: "Every dot comes from photos our photo check graded red, the ones it can't trust.",
  },
  "2mass": {
    term: "2MASS",
    label: "A 2MASS source sits here",
    plain: "2MASS, an older infrared survey of the whole sky, lists a source within 6″: a star or galaxy that stays put.",
  },
  allwise: {
    term: "AllWISE",
    label: "An AllWISE source sits here",
    plain: "AllWISE, from NASA's WISE telescope, lists a source within 6″: a star or galaxy that stays put.",
  },
};

/** A gate code in words; unknown codes in their own words, so a newer pipeline never breaks a page. */
export function gateText(code: string): GateText {
  if (isGate(code)) return GATE_TEXT[code];
  const w = code.replace(/[_-]+/g, " ").trim();
  const label = w ? `${w.charAt(0).toUpperCase()}${w.slice(1)}` : code;
  return { term: label, label, plain: `Pipeline check “${code}”.` };
}

/** The gates a row failed, in its own order, as codes (suspects: `reasons`; candidates: the gate codes among `reasons`). */
export function failedGates(x: { reasons?: unknown }): string[] {
  const r = Array.isArray(x.reasons) ? x.reasons.filter((s): s is string => typeof s === "string") : [];
  return r.filter(isGate);
}

/** "On a hot pixel" → "on a hot pixel"; names and codes ("AllWISE", "2MASS") keep their case. */
const lowerFirst = (s: string) => (/^[A-Z][a-z ]/.test(s) ? s.charAt(0).toLowerCase() + s.slice(1) : s);

/** Why a grade C row is C, in a short phrase: "on a hot pixel and at the detector edge". Empty when no gate is named. */
export function gatesPhrase(codes: readonly string[]): string {
  const words = [...new Set(codes.map((c) => gateText(c).label))].map(lowerFirst);
  if (!words.length) return "";
  return words.length === 1 ? words[0] : `${words.slice(0, -1).join(", ")} and ${words[words.length - 1]}`;
}

/** One sentence for a grade C row: "Grade C: on a hot pixel and at the detector edge." */
export function gradeCSentence(codes: readonly string[]): string {
  const why = gatesPhrase(codes);
  return why ? `Grade C: ${why}.` : "Grade C: it fails a pipeline check.";
}

// ---- near_source_arcsec: a warning, not a gate ------------------------------------------------------

/** A finite distance to the nearest catalogued or fixed source within 10″, else null. */
export function nearSourceOf(x: { near_source_arcsec?: unknown } | null | undefined): number | null {
  const v = x?.near_source_arcsec;
  return typeof v === "number" && Number.isFinite(v) && v >= 0 ? v : null;
}

/** "A catalogued source sits 4.1″ away." */
export function nearSourceText(arcsec: number): string {
  const d = arcsec >= 10 ? arcsec.toFixed(0) : arcsec.toFixed(1);
  return `A catalogued source sits ${d}″ away.`;
}

export const NEAR_SOURCE_HINT =
  "Gaia, CatWISE, 2MASS or a fixed source within 10″: it may blend with it or sit in its glow. A warning, not a check it failed.";

// ---- the track (api v3.4): what makes a grade A -------------------------------------------------------

export interface Track {
  /** Sessions of the visit with a mover on the track. */
  sessions: number;
  /** ″/h at this suspect's time, or null. */
  rate: number | null;
  /** Degrees east of north, or null. */
  pa: number | null;
  /** 1σ error of the rate, ″/h, or null. */
  sigma: number | null;
}

const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : null);

/** The track a suspect is on (n_sessions, rate_arcsec_h, pa_deg, rate_sigma); null off a track or in a file without tracks. */
export function trackOf(x: { n_sessions?: unknown; rate_arcsec_h?: unknown; pa_deg?: unknown; rate_sigma?: unknown }): Track | null {
  const n = num(x.n_sessions);
  if (n == null || n < 1) return null;
  const sigma = num(x.rate_sigma);
  return { sessions: Math.round(n), rate: num(x.rate_arcsec_h), pa: num(x.pa_deg), sigma: sigma != null && sigma > 0 ? sigma : null };
}

/** "On a track over 4 sessions, moving 31.3″ an hour (38σ) toward 252°." */
export function trackText(t: Track): string {
  const rate = t.rate != null ? `, moving ${t.rate.toFixed(1)}″ an hour` : "";
  const sig = t.rate != null && t.sigma != null ? ` (${Math.round(Math.abs(t.rate) / t.sigma)}σ)` : "";
  const pa = t.pa != null ? ` toward ${Math.round(((t.pa % 360) + 360) % 360)}°` : "";
  return `On a track over ${t.sessions} ${t.sessions === 1 ? "session" : "sessions"}${rate}${sig}${pa}.`;
}

// ---- totals --------------------------------------------------------------------------------------

export type GradeCounts = Record<Grade, number>;

/** Count grades over rows; null when no row carries one (an ungraded file). */
export function countGrades(rows: readonly { grade?: unknown }[]): GradeCounts | null {
  const n: GradeCounts = { A: 0, B: 0, C: 0 };
  let any = false;
  for (const r of rows) {
    const g = gradeOf(r);
    if (g) {
      n[g]++;
      any = true;
    }
  }
  return any ? n : null;
}

/** A file's own `grades` object when it is well formed, else null. */
export function gradeCounts(x: unknown): GradeCounts | null {
  if (!x || typeof x !== "object") return null;
  const o = x as Record<string, unknown>;
  const n = (v: unknown) => (typeof v === "number" && Number.isInteger(v) && v >= 0 ? v : null);
  const a = n(o.A);
  const b = n(o.B);
  const c = n(o.C);
  return a == null || b == null || c == null ? null : { A: a, B: b, C: c };
}

/** Zero grade A is the expected result tonight, not a failure: say why, in one line. */
export const ZERO_A =
  "0 candidate movers (grade A). Expected: proving motion needs a track over several sessions.";

const nf = (n: number) => n.toLocaleString("en-GB");

/** The A line: ZERO_A for none, else "3 candidate movers (grade A)." */
export function gradeALine(a: number): string {
  if (a === 0) return ZERO_A;
  return `${nf(a)} ${a === 1 ? "candidate mover" : "candidate movers"} (grade A).`;
}

/** The B and C line: "22 unexplained single-session sources (grade B) and 206 that fail a check (grade C)." */
export function gradeBCLine(n: GradeCounts): string {
  const b = `${nf(n.B)} unexplained single-session ${n.B === 1 ? "source" : "sources"} (grade B)`;
  const c = `${nf(n.C)} that ${n.C === 1 ? "fails" : "fail"} a check (grade C)`;
  return `${b} and ${c}.`;
}

/** Both lines, A first. */
export function gradeTotalsText(n: GradeCounts): string {
  return `${gradeALine(n.A)} ${gradeBCLine(n)}`;
}

// ---- zone-level fields of a suspects file ------------------------------------------------------------

const VETO_TEXT: Record<string, string> = {
  mpc: "known asteroids and comets (MPC)",
  gaia: "Gaia",
  catwise: "CatWISE",
  allwise: "AllWISE",
};

/** "known asteroids and comets (MPC), Gaia and CatWISE", or null without vetoes. */
export function vetoesText(vetoes: unknown): string | null {
  if (!Array.isArray(vetoes)) return null;
  const words = vetoes.filter((v): v is string => typeof v === "string" && !!v).map((v) => VETO_TEXT[v.toLowerCase()] ?? v);
  if (!words.length) return null;
  return words.length === 1 ? words[0] : `${words.slice(0, -1).join(", ")} and ${words[words.length - 1]}`;
}

/** A suspects file's `provisional`: Gaia or every WISE catalogue could not be checked. */
export const FILE_PROVISIONAL_TEXT =
  "Provisional: Gaia or the WISE catalogues couldn't be checked, so very red sources only WISE sees may still be listed.";

/** A file's `gates` counts as [code, n] for gates that failed anything, most first. */
export function gateTotals(x: unknown): [string, number][] {
  if (!x || typeof x !== "object") return [];
  return Object.entries(x as Record<string, unknown>)
    .filter((e): e is [string, number] => typeof e[1] === "number" && Number.isFinite(e[1]) && e[1] > 0)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
}
