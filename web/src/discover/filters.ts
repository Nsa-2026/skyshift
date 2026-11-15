// The candidate filter model, as pure functions: filter state, the URL it lives in, filtering,
// facet counts, presets and sorting (DESIGN.md "Smart filters"). No DOM, so the Candidates page
// (src/discover) and the Sky page (src/atlas) share one meaning of "filtered".
//
//   const state = queryToState(routeQuery(param));     // from #/candidates/<query>
//   const rows = toRows(candidates, counts, votedOn);  // counts from src/vote's countsFor()
//   const f = filterRows(rows, state);                 // f.rows passes; f.without(key) for facet counts
//   const list = sortRows(f.rows, state.sort);
//   const link = candidatesHash(state);                // shareable link to the same list
import { gradeOf, gradeRank, leadRank } from "../api/grades";
import type { Candidate, CandidateKind } from "../api/types";
import { consensus as voteConsensus, type Answer, type Counts } from "../vote/model";
import { reasonText, type ReviewState } from "./text";

export type Tri = "any" | "yes" | "no";
export type SortKey = "promising" | "grade" | "fewest" | "most" | "brightest" | "fastest" | "newest";

/** Sorts only reorder the list, so their names say what they order by. "Most promising" is the
 *  preset (a narrowed set), not a sort: the default order is "Pipeline score". */
export const SORTS: { key: SortKey; label: string; hint?: string }[] = [
  {
    key: "promising",
    label: "Score",
    hint: "Grade A first and grade C last; then the highest score, with possible camera glitches and doubtful ones after the rest. Nothing is hidden.",
  },
  {
    key: "grade",
    label: "Discovery type",
    hint: "Fast movers by discovery type: A, then B, then C, then everything without a grade; each by S/N, highest first.",
  },
  { key: "fewest", label: "Fewest votes" },
  { key: "most", label: "Most votes" },
  { key: "brightest", label: "Brightest" },
  { key: "fastest", label: "Fastest" },
  { key: "newest", label: "Newest" },
];

/** A closed or half-open interval; null means no limit on that side. */
export interface Range {
  min: number | null;
  max: number | null;
}

export const RANGE_KEYS = ["score", "sig", "pm", "rate", "snr", "flux", "known", "b"] as const;
export type RangeKey = (typeof RANGE_KEYS)[number];

/** Multi-choice facets (OR within a facet, AND between facets). Values are strings, as in the URL.
 *  "grade" is last so links written before it keep their order. */
export const LIST_KEYS = ["kind", "review", "verdict", "label", "reason", "det", "zone", "grade"] as const;
export type ListKey = (typeof LIST_KEYS)[number];

export interface FilterState {
  kind: string[];
  review: string[];
  verdict: string[];
  label: string[];
  reason: string[];
  det: string[];
  zone: string[];
  /** Vetting grades: "A", "B", "C", or "none" (no grade). */
  grade: string[];
  gaia: Tri;
  catwise: Tri;
  /** Hide candidates with any reason the pipeline lists as a doubt. */
  hideDoubts: boolean;
  /** Artefact cautions (flags near it, or a caution reason such as outlier pixels or a repeat
   *  position): "no" hides them, "yes" shows only them (to check first), "any" both. */
  cautions: Tri;
  /** Hide candidates this browser already voted on. */
  hideMine: boolean;
  ranges: Partial<Record<RangeKey, Range>>;
  sort: SortKey;
}

export function defaultState(): FilterState {
  return {
    kind: [],
    review: [],
    verdict: [],
    label: [],
    reason: [],
    det: [],
    zone: [],
    grade: [],
    gaia: "any",
    catwise: "any",
    hideDoubts: false,
    cautions: "any",
    hideMine: false,
    ranges: {},
    sort: "promising",
  };
}

// ---- rows: a candidate with what the community said about it --------------------------------

export interface Row {
  c: Candidate;
  votes: Counts;
  total: number;
  review: ReviewState;
  /** The answer the community agreed on, or null. */
  verdict: Answer | null;
  /** This browser voted on it. */
  mine: boolean;
}

const ANSWER_KEYS: Answer[] = ["mover", "changing", "junk", "unsure"];
const zero = (): Counts => ({ mover: 0, changing: 0, junk: 0, unsure: 0 });
const sum = (c: Counts) => c.mover + c.changing + c.junk + c.unsure;

/** The answer the community agreed on (src/vote's rule: at least 5 votes, 70 % on one answer), or null. */
export function verdictOf(c: Counts): Answer | null {
  return voteConsensus(c)?.answer ?? null;
}

/** The leading answer and its count, or null with no votes. Ties go to the first in answer order. */
export function leading(c: Counts): { answer: Answer; n: number } | null {
  let best: Answer | null = null;
  for (const a of ANSWER_KEYS) if (c[a] > 0 && (best === null || c[a] > c[best])) best = a;
  return best ? { answer: best, n: c[best] } : null;
}

export function toRows(cands: Candidate[], counts: Map<string, Counts>, votedOn: (id: string) => boolean = () => false): Row[] {
  return cands.map((c) => {
    const votes = counts.get(c.id) ?? zero();
    const total = sum(votes);
    const verdict = verdictOf(votes);
    return { c, votes, total, verdict, review: verdict ? "consensus" : total > 0 ? "some" : "none", mine: votedOn(c.id) };
  });
}

// ---- measured values ---------------------------------------------------------------------------

export const ARCSEC_H_TO_YR = 24 * 365.25;

/** Motion significance: proper motion over its 1σ error. */
export function significance(c: Candidate): number | null {
  return c.pm_mas_yr != null && c.pm_err_mas_yr != null && c.pm_err_mas_yr > 0 ? c.pm_mas_yr / c.pm_err_mas_yr : null;
}

/** Any motion as arcsec per year, so fast movers and moving stars sort on one scale. */
export function motionArcsecYr(c: Candidate): number | null {
  if (c.rate_arcsec_h != null) return c.rate_arcsec_h * ARCSEC_H_TO_YR;
  if (c.pm_mas_yr != null) return c.pm_mas_yr / 1000;
  return null;
}

export interface RangeSpec {
  key: RangeKey;
  label: string;
  /** The pipeline field(s), for title attributes. */
  code: string;
  unit: string;
  log: boolean;
  digits: number;
  /** Fixed domain, or null to take it from the data. */
  fixed: [number, number] | null;
  value(c: Candidate): number | null;
  /** What a missing value means when this range is narrowed. */
  missing: "excluded" | "far";
}

export const RANGES: Record<RangeKey, RangeSpec> = {
  score: { key: "score", label: "Score", code: "score", unit: "", log: false, digits: 2, fixed: [0, 1], value: (c) => c.score, missing: "excluded" },
  sig: { key: "sig", label: "How sure the motion is", code: "pm_mas_yr / pm_err_mas_yr", unit: "σ", log: true, digits: 1, fixed: null, value: significance, missing: "excluded" },
  pm: { key: "pm", label: "Motion between visits", code: "pm_mas_yr", unit: "mas/yr", log: true, digits: 0, fixed: null, value: (c) => c.pm_mas_yr, missing: "excluded" },
  rate: { key: "rate", label: "Motion within hours", code: "rate_arcsec_h", unit: "″/h", log: true, digits: 1, fixed: null, value: (c) => c.rate_arcsec_h, missing: "excluded" },
  snr: { key: "snr", label: "Signal to noise", code: "snr", unit: "", log: true, digits: 1, fixed: null, value: (c) => c.snr, missing: "excluded" },
  flux: { key: "flux", label: "Brightness", code: "flux_ujy", unit: "µJy", log: true, digits: 0, fixed: null, value: (c) => c.flux_ujy, missing: "excluded" },
  known: {
    key: "known",
    label: "Nearest known asteroid or comet",
    code: "nearest_known_arcsec",
    unit: "″",
    log: false,
    digits: 0,
    fixed: null,
    value: (c) => c.nearest_known_arcsec,
    // The pipeline writes null when nothing known came within its search radius: that is "far".
    missing: "far",
  },
  b: { key: "b", label: "Distance from the Milky Way plane", code: "|galactic_b|", unit: "°", log: false, digits: 0, fixed: [0, 90], value: (c) => Math.abs(c.galactic_b), missing: "excluded" },
};

/** |b| below this is the crowded Milky Way plane. */
export const PLANE_B = 10;

function nice(x: number, dir: "down" | "up"): number {
  if (x <= 0) return 0;
  const p = 10 ** Math.floor(Math.log10(x));
  const steps = [1, 2, 5, 10];
  if (dir === "down") {
    for (let i = steps.length - 1; i >= 0; i--) if (steps[i] * p <= x * (1 + 1e-9)) return steps[i] * p;
    return p;
  }
  for (const s of steps) if (s * p >= x * (1 - 1e-9)) return s * p;
  return 10 * p;
}

/** Slider domain for a range from the data: fixed, or rounded out to 1–2–5 steps. null when no values. */
export function domainOf(spec: RangeSpec, cands: Candidate[]): [number, number] | null {
  const vals = cands.map(spec.value).filter((v): v is number => v != null && Number.isFinite(v));
  if (!vals.length) return null;
  if (spec.fixed) return spec.fixed;
  let lo = Math.min(...vals);
  let hi = Math.max(...vals);
  if (spec.log) {
    lo = nice(Math.max(lo, 1e-3), "down");
    hi = nice(Math.max(hi, lo * 10), "up");
  } else {
    lo = spec.key === "known" ? 0 : nice(lo, "down");
    hi = nice(Math.max(hi, lo + 1), "up");
  }
  return [lo, hi];
}

export function inRange(v: number | null, r: Range | undefined, missing: RangeSpec["missing"]): boolean {
  if (!r || (r.min == null && r.max == null)) return true;
  const x = v == null || !Number.isFinite(v) ? (missing === "far" ? Infinity : null) : v;
  if (x == null) return false;
  if (r.min != null && x < r.min) return false;
  if (r.max != null && x > r.max) return false;
  return true;
}

export const isActiveRange = (r: Range | undefined): r is Range => !!r && (r.min != null || r.max != null);

// ---- filtering --------------------------------------------------------------------------------

export const isDoubtCode = (code: string): boolean => reasonText(code).tone === "doubt";
export const isCautionCode = (code: string): boolean => reasonText(code).tone === "caution";

/** An artefact may be behind it: flags near it, or a caution reason (outlier pixels, repeat position). */
export function isCautioned(c: Candidate): boolean {
  return c.flags.length > 0 || c.reasons.some(isCautionCode);
}

export function isDoubted(c: Candidate, doubts: (code: string) => boolean = isDoubtCode): boolean {
  return c.reasons.some(doubts);
}

/** Keys of the independent tests: facet counts leave their own key out. */
export type TestKey = ListKey | "gaia" | "catwise" | "doubts" | "cautions" | "mine" | RangeKey;

interface Test {
  key: TestKey;
  pass(r: Row): boolean;
}

const has = (list: string[], v: string) => list.includes(v);
const triPass = (t: Tri, v: boolean) => t === "any" || (t === "yes") === v;

function tests(s: FilterState, doubts: (code: string) => boolean): Test[] {
  const out: Test[] = [];
  if (s.kind.length) out.push({ key: "kind", pass: (r) => has(s.kind, r.c.kind) });
  if (s.review.length) out.push({ key: "review", pass: (r) => has(s.review, r.review) });
  if (s.verdict.length) out.push({ key: "verdict", pass: (r) => r.verdict != null && has(s.verdict, r.verdict) });
  if (s.label.length) out.push({ key: "label", pass: (r) => r.c.labels.some((l) => has(s.label, l)) });
  if (s.reason.length) out.push({ key: "reason", pass: (r) => r.c.reasons.some((l) => has(s.reason, l)) });
  if (s.det.length) out.push({ key: "det", pass: (r) => has(s.det, String(r.c.det)) });
  if (s.zone.length) out.push({ key: "zone", pass: (r) => has(s.zone, r.c.zone) });
  if (s.grade.length) out.push({ key: "grade", pass: (r) => has(s.grade, gradeKey(r.c)) });
  if (s.gaia !== "any") out.push({ key: "gaia", pass: (r) => triPass(s.gaia, r.c.gaia_match) });
  if (s.catwise !== "any") out.push({ key: "catwise", pass: (r) => triPass(s.catwise, r.c.catwise_match) });
  if (s.hideDoubts) out.push({ key: "doubts", pass: (r) => !isDoubted(r.c, doubts) });
  if (s.cautions !== "any") out.push({ key: "cautions", pass: (r) => triPass(s.cautions, isCautioned(r.c)) });
  if (s.hideMine) out.push({ key: "mine", pass: (r) => !r.mine });
  for (const k of RANGE_KEYS) {
    const range = s.ranges[k];
    if (isActiveRange(range)) {
      const spec = RANGES[k];
      out.push({ key: k, pass: (r) => inRange(spec.value(r.c), range, spec.missing) });
    }
  }
  return out;
}

export interface Filtered {
  rows: Row[];
  /** Rows that pass every test except the one keyed (for facet counts and histograms). */
  without(key: TestKey): Row[];
  activeKeys: TestKey[];
}

export function filterRows(all: Row[], s: FilterState, doubts: (code: string) => boolean = isDoubtCode): Filtered {
  const ts = tests(s, doubts);
  const rows = all.filter((r) => ts.every((t) => t.pass(r)));
  const cache = new Map<TestKey, Row[]>();
  return {
    rows,
    activeKeys: ts.map((t) => t.key),
    without(key) {
      if (!ts.some((t) => t.key === key)) return rows;
      let got = cache.get(key);
      if (!got) {
        const others = ts.filter((t) => t.key !== key);
        got = all.filter((r) => others.every((t) => t.pass(r)));
        cache.set(key, got);
      }
      return got;
    },
  };
}

/** Count rows per facet value (a row can count for several values, e.g. labels). */
export function countBy(rows: Row[], valuesOf: (r: Row) => string[]): Map<string, number> {
  const m = new Map<string, number>();
  for (const r of rows) for (const v of new Set(valuesOf(r))) m.set(v, (m.get(v) ?? 0) + 1);
  return m;
}

export const FACET_VALUES: Record<ListKey, (r: Row) => string[]> = {
  kind: (r) => [r.c.kind],
  review: (r) => [r.review],
  verdict: (r) => (r.verdict ? [r.verdict] : []),
  label: (r) => r.c.labels,
  reason: (r) => r.c.reasons,
  det: (r) => [String(r.c.det)],
  zone: (r) => [r.c.zone],
  grade: (r) => [gradeKey(r.c)],
};

/** The grade facet's value for a candidate: its grade, or "none" (read from the file, never guessed). */
export function gradeKey(c: Candidate): string {
  return gradeOf(c) ?? "none";
}

/** Does any candidate carry a grade? (An older export carries none: then the grade filter says so.) */
export function anyGraded(cands: readonly Candidate[]): boolean {
  return cands.some((c) => gradeOf(c) !== null);
}

/** How many filters are on (sort is not a filter). */
export function activeCount(s: FilterState): number {
  let n = 0;
  for (const k of LIST_KEYS) if (s[k].length) n++;
  if (s.gaia !== "any") n++;
  if (s.catwise !== "any") n++;
  if (s.hideDoubts) n++;
  if (s.cautions !== "any") n++;
  if (s.hideMine) n++;
  for (const k of RANGE_KEYS) if (isActiveRange(s.ranges[k])) n++;
  return n;
}

// ---- sorting ----------------------------------------------------------------------------------

/** Descending with nulls last. */
const desc = (a: number | null, b: number | null) => (a == null ? (b == null ? 0 : 1) : b == null ? -1 : b - a);

/** The "Pipeline score" order: grade A first and grade C last (candidates.json: A, then B or no
 *  grade, then C); then anything an artefact may be behind goes last, then the pipeline's doubts;
 *  then the score, how sure the motion is, and S/N. Without grades it is the order it always was. */
function promising(a: Row, b: Row): number {
  return (
    leadRank(gradeOf(a.c)) - leadRank(gradeOf(b.c)) ||
    Number(isCautioned(a.c)) - Number(isCautioned(b.c)) ||
    Number(isDoubted(a.c)) - Number(isDoubted(b.c)) ||
    desc(a.c.score, b.c.score) ||
    desc(significance(a.c), significance(b.c)) ||
    desc(a.c.snr, b.c.snr) ||
    a.c.id.localeCompare(b.c.id)
  );
}

/** The "Vetting grade" order: A, B, C, then no grade; then S/N, highest first. */
function byGrade(a: Row, b: Row): number {
  return gradeRank(gradeOf(a.c)) - gradeRank(gradeOf(b.c)) || desc(a.c.snr, b.c.snr) || promising(a, b);
}

const SORTERS: Record<SortKey, (a: Row, b: Row) => number> = {
  promising,
  grade: byGrade,
  fewest: (a, b) => a.total - b.total || promising(a, b),
  most: (a, b) => b.total - a.total || promising(a, b),
  brightest: (a, b) => desc(a.c.flux_ujy, b.c.flux_ujy) || promising(a, b),
  fastest: (a, b) => desc(motionArcsecYr(a.c), motionArcsecYr(b.c)) || promising(a, b),
  newest: (a, b) => b.c.mjd - a.c.mjd || promising(a, b),
};

export function sortRows(rows: Row[], key: SortKey): Row[] {
  return [...rows].sort(SORTERS[key] ?? promising);
}

// ---- URL: everything after "#/?" ---------------------------------------------------------------

const TRI: Tri[] = ["any", "yes", "no"];
const SORT_KEYS = SORTS.map((s) => s.key);
const TOKEN = /^[A-Za-z0-9_.-]{1,64}$/;

function fmtNum(n: number): string {
  return String(+n.toPrecision(6));
}

function parseRange(v: string): Range | null {
  const m = /^\s*([0-9.eE+]*)\s*-\s*([0-9.eE+]*)\s*$/.exec(v);
  if (!m) return null;
  const num = (s: string) => (s === "" ? null : Number.isFinite(Number(s)) ? Number(s) : NaN);
  let min = num(m[1]);
  let max = num(m[2]);
  if (Number.isNaN(min) || Number.isNaN(max)) return null;
  if (min != null && max != null && min > max) [min, max] = [max, min];
  return min == null && max == null ? null : { min, max };
}

/** Only what differs from the default, in a stable order, commas left readable. */
export function stateToQuery(s: FilterState): string {
  const parts: string[] = [];
  const add = (k: string, v: string) => parts.push(`${k}=${encodeURIComponent(v).replace(/%2C/gi, ",")}`);
  for (const k of LIST_KEYS) if (s[k].length) add(k, [...new Set(s[k])].join(","));
  if (s.gaia !== "any") add("gaia", s.gaia);
  if (s.catwise !== "any") add("catwise", s.catwise);
  if (s.hideDoubts) add("doubts", "hide");
  if (s.cautions !== "any") add("cautions", s.cautions === "no" ? "hide" : "only");
  if (s.hideMine) add("mine", "hide");
  for (const k of RANGE_KEYS) {
    const r = s.ranges[k];
    if (isActiveRange(r)) add(k, `${r.min == null ? "" : fmtNum(r.min)}-${r.max == null ? "" : fmtNum(r.max)}`);
  }
  if (s.sort !== "promising") add("sort", s.sort);
  return parts.join("&");
}

/** Tolerant: unknown keys and bad values are dropped, never thrown. */
export function queryToState(q: string): FilterState {
  const s = defaultState();
  const p = new URLSearchParams(q.replace(/^[#/?]+/, ""));
  for (const k of LIST_KEYS) {
    const v = p.get(k);
    if (v) s[k] = [...new Set(v.split(",").map((x) => x.trim()).filter((x) => TOKEN.test(x)))];
  }
  const tri = (v: string | null): Tri => (TRI.includes(v as Tri) ? (v as Tri) : "any");
  s.gaia = tri(p.get("gaia"));
  s.catwise = tri(p.get("catwise"));
  s.hideDoubts = p.get("doubts") === "hide";
  const cau = p.get("cautions");
  s.cautions = cau === "hide" ? "no" : cau === "only" ? "yes" : "any";
  s.hideMine = p.get("mine") === "hide";
  for (const k of RANGE_KEYS) {
    const v = p.get(k);
    const r = v ? parseRange(v) : null;
    if (r) s.ranges[k] = r;
  }
  const sort = p.get("sort") as SortKey | null;
  if (sort && SORT_KEYS.includes(sort)) s.sort = sort;
  return s;
}

/** The page's route. Filters follow it: #/candidates/kind=mover&sort=fewest. */
export const CANDIDATES_ROUTE = "#/candidates";

/** The filter query from the router's param ("kind=mover", "?kind=mover") or a whole hash. */
export function routeQuery(paramOrHash: string | null | undefined): string {
  if (!paramOrHash) return "";
  let s = paramOrHash;
  if (s.startsWith("#")) {
    const m = /^#\/?(?:(?:analyze\/)?candidates|discover)(?:[/?](.*))?$/.exec(s);
    s = m ? m[1] ?? "" : "";
  }
  return s.replace(/^[/?]+/, "");
}

/** The "One at a time" view's key: the same filter, the review queue in place of the list.
 *  It is not a filter (queryToState ignores it), so presets and counts never see it. */
export const ONE_KEY = "one";

/** Is "One at a time" on in this query or hash? ("one=1", "kind=mover&one=1", "#/candidates/one=1") */
export function isOneAtATime(paramOrHash: string | null | undefined): boolean {
  const q = paramOrHash?.startsWith("#") ? routeQuery(paramOrHash) : (paramOrHash ?? "");
  return new URLSearchParams(q.replace(/^[#/?]+/, "")).get(ONE_KEY) === "1";
}

/** The map view's key: the same filter on a page of its own, the sky map in place of the list.
 *  Like "one", a view and not a filter (queryToState ignores it). */
export const MAP_KEY = "map";

/** Is the map view on in this query or hash? ("map=1", "kind=mover&map=1", "#/candidates/map=1") */
export function isMapView(paramOrHash: string | null | undefined): boolean {
  const q = paramOrHash?.startsWith("#") ? routeQuery(paramOrHash) : (paramOrHash ?? "");
  return new URLSearchParams(q.replace(/^[#/?]+/, "")).get(MAP_KEY) === "1";
}

/** The shareable hash for a filter state: "#/candidates" or "#/candidates/kind=mover&sort=fewest";
 *  with one: true, the "One at a time" view of it: "#/candidates/one=1", "#/candidates/kind=mover&one=1";
 *  with map: true, the map view of it (which wins over one): "#/candidates/kind=mover&map=1". */
export function candidatesHash(s: FilterState, opts: { one?: boolean; map?: boolean } = {}): string {
  const view = opts.map ? `${MAP_KEY}=1` : opts.one ? `${ONE_KEY}=1` : "";
  const q = [stateToQuery(s), view].filter(Boolean).join("&");
  return q ? `${CANDIDATES_ROUTE}/${q}` : CANDIDATES_ROUTE;
}

export const sameState = (a: FilterState, b: FilterState) => stateToQuery(a) === stateToQuery(b);

// ---- presets (DESIGN.md "Smart filters") -------------------------------------------------------

export interface Preset {
  id: string;
  label: string;
  /** One sentence for the title attribute and the results line. */
  hint: string;
  state: Partial<FilterState>;
}

export const PRESETS: Preset[] = [
  {
    id: "promising",
    label: "Most promising",
    hint: "Highest score first, without doubtful ones or possible camera glitches.",
    state: { sort: "promising", hideDoubts: true, cautions: "no" },
  },
  {
    id: "needs-vote",
    label: "Needs your vote",
    hint: "Fewest votes first, without a verdict yet, and none you've already voted on.",
    state: { sort: "fewest", review: ["none", "some"], hideMine: true },
  },
  {
    id: "brown-dwarfs",
    label: "Possible brown dwarfs",
    hint: "Moving stars that are very red or show methane, with no Gaia match.",
    state: { kind: ["high_pm"], label: ["red", "ch4", "brown_dwarf_like"], gaia: "no" },
  },
  {
    id: "fast-movers",
    label: "Unknown fast movers",
    hint: "Fast movers with no known asteroid or comet within 60″.",
    state: { kind: ["mover"], ranges: { known: { min: 60, max: null } } },
  },
  {
    id: "community-real",
    label: "Community says real",
    hint: "The community agreed it moves or changes: these go to astronomers.",
    state: { review: ["consensus"], verdict: ["mover", "changing"], sort: "most" },
  },
  {
    id: "check-first",
    label: "Check first",
    hint: "Ones that may be camera glitches (odd pixels, ghosts, the same spot days apart): a quick look settles most.",
    state: { cautions: "yes", review: ["none", "some"], sort: "fewest" },
  },
];

export function presetState(p: Preset): FilterState {
  return { ...defaultState(), ...structuredCloneSafe(p.state) };
}

export function activePreset(s: FilterState): Preset | null {
  return PRESETS.find((p) => sameState(presetState(p), s)) ?? null;
}

function structuredCloneSafe<T>(x: T): T {
  return JSON.parse(JSON.stringify(x)) as T;
}

// ---- suggestions for an empty result -----------------------------------------------------------

/** The single filter whose removal brings back the most candidates. */
export function bestRelaxation(f: Filtered): { key: TestKey; n: number } | null {
  let best: { key: TestKey; n: number } | null = null;
  for (const k of f.activeKeys) {
    const n = f.without(k).length;
    if (n > 0 && (!best || n > best.n)) best = { key: k, n };
  }
  return best;
}

/** Remove one filter by its test key. */
export function withoutTest(s: FilterState, key: TestKey): FilterState {
  const next: FilterState = { ...s, ranges: { ...s.ranges } };
  if ((LIST_KEYS as readonly string[]).includes(key)) next[key as ListKey] = [];
  else if (key === "gaia" || key === "catwise") next[key] = "any";
  else if (key === "doubts") next.hideDoubts = false;
  else if (key === "cautions") next.cautions = "any";
  else if (key === "mine") next.hideMine = false;
  else delete next.ranges[key as RangeKey];
  return next;
}

export const KIND_ORDER: CandidateKind[] = ["mover", "high_pm", "spectral", "shape"];
