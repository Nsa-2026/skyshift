// Review and Candidate pages: the pure parts. Consensus (DESIGN.md "Votes and consensus"), the
// review queue's order, the Discover filter read from its URL, keyboard mapping and URL parsing.
// No DOM, no Firebase, so tests are cheap.
import { gradeOf, gradeRank, leadRank, votable } from "../api/grades";
import type { Candidate } from "../api/types";
import { ANSWERS, CONSENSUS, consensus as agreed, emptyCounts, total, type Answer, type Counts } from "../vote/model";

// ---- consensus ---------------------------------------------------------------------------------

/** A candidate reaches consensus with at least this many votes (src/vote's rule, DESIGN.md)… */
export const CONSENSUS_MIN_VOTES = CONSENSUS.minVotes;
/** …and at least this share agreeing on one answer (70 %). */
export const CONSENSUS_PERCENT = Math.round(CONSENSUS.share * 100);

export interface Consensus {
  /** none: no votes; forming: under 5 votes; split: 5+ but no answer at 70 %; reached: consensus. */
  state: "none" | "forming" | "split" | "reached";
  total: number;
  /** The most common answer (first in ANSWERS order on a tie), or null without votes. */
  top: Answer | null;
  topCount: number;
  /** topCount / total, 0 without votes. */
  share: number;
  /** The agreed answer when state is "reached", else null. */
  answer: Answer | null;
}

/** Where the votes stand: src/vote's consensus rule, plus the states before it (none, forming, split). */
export function consensus(c: Counts): Consensus {
  const n = total(c);
  let top: Answer | null = null;
  let topCount = 0;
  for (const a of ANSWERS) {
    if (c[a] > topCount) {
      top = a;
      topCount = c[a];
    }
  }
  const share = n ? topCount / n : 0;
  if (n === 0) return { state: "none", total: 0, top: null, topCount: 0, share: 0, answer: null };
  if (n < CONSENSUS_MIN_VOTES) return { state: "forming", total: n, top, topCount, share, answer: null };
  const a = agreed(c);
  if (a) return { state: "reached", total: n, top: a.answer, topCount: c[a.answer], share: a.share, answer: a.answer };
  return { state: "split", total: n, top, topCount, share, answer: null };
}

/** Consensus movers and changing sources form the list for astronomers. */
export function forAstronomers(c: Consensus): boolean {
  return c.state === "reached" && (c.answer === "mover" || c.answer === "changing");
}

/** Votes still needed before consensus is possible (0 once there are 5). */
export function votesToGo(c: Counts): number {
  return Math.max(0, CONSENSUS_MIN_VOTES - total(c));
}

/** Add one vote to a copy of the counts (optimistic update after a vote is saved). */
export function withVote(c: Counts | undefined, a: Answer): Counts {
  const out = { ...(c ?? emptyCounts()) };
  out[a] += 1;
  return out;
}

// ---- ordering ----------------------------------------------------------------------------------

const desc = (a: number | null, b: number | null): number => {
  if (a == null && b == null) return 0;
  if (a == null) return 1; // nulls last
  if (b == null) return -1;
  return b - a;
};

/** Pipeline promise: score high to low (no score last), then S/N high to low, then id. */
export function byPromise(a: Candidate, b: Candidate): number {
  return desc(a.score, b.score) || desc(a.snr, b.snr) || a.id.localeCompare(b.id);
}

export function scoreOrder(cands: Candidate[]): Candidate[] {
  return [...cands].sort(byPromise);
}

/**
 * The review queue: what needs votes first, then the pipeline's score.
 * Grade C (failed a vetting check) is never put to a vote, so it is left out, as are candidates
 * this browser already voted on; candidates at consensus go last; grade A leads the rest; then
 * fewest votes, score (no score last) and S/N. Ungraded candidates queue as they always did.
 */
export function queueOrder(
  cands: Candidate[],
  counts: Map<string, Counts>,
  votedFor: (id: string) => Answer | null = () => null,
): Candidate[] {
  const info = new Map<string, { done: number; n: number; lead: number }>();
  for (const c of cands) {
    const k = counts.get(c.id) ?? emptyCounts();
    info.set(c.id, { done: consensus(k).state === "reached" ? 1 : 0, n: total(k), lead: leadRank(gradeOf(c)) });
  }
  return cands
    .filter((c) => votable(c) && votedFor(c.id) == null)
    .sort((a, b) => {
      const A = info.get(a.id)!;
      const B = info.get(b.id)!;
      return A.done - B.done || A.lead - B.lead || A.n - B.n || byPromise(a, b);
    });
}

/** How many candidates the queue leaves out for being grade C. */
export function gradeCCount(cands: readonly Candidate[]): number {
  return cands.filter((c) => !votable(c)).length;
}

// ---- the Candidates page's filter, read from its URL ------------------------------------------
// The same keys and meaning as src/discover (DESIGN.md "Smart filters"): #/candidates/<query>, e.g.
// "kind=mover&known=60-&sort=fewest". Lists are comma-separated (OR inside a key, AND between
// keys); ranges are "min-max" with either side open; gaia/catwise are yes/no; doubts, cautions and
// mine take "hide". Tolerant: unknown keys and bad values are ignored.

export type Sort = "promising" | "grade" | "fewest" | "most" | "brightest" | "fastest" | "newest";
const SORTS: Sort[] = ["promising", "grade", "fewest", "most", "brightest", "fastest", "newest"];

export interface Range {
  min: number | null;
  max: number | null;
}
const RANGE_KEYS = ["score", "sig", "pm", "rate", "snr", "flux", "known", "b"] as const;
type RangeKey = (typeof RANGE_KEYS)[number];
const LIST_KEYS = ["kind", "review", "verdict", "label", "reason", "det", "zone", "grade"] as const;
type ListKey = (typeof LIST_KEYS)[number];
type Tri = "any" | "yes" | "no";

export interface ListFilter {
  lists: Record<ListKey, string[]>;
  gaia: Tri;
  catwise: Tri;
  hideDoubts: boolean;
  hideCautions: boolean;
  hideMine: boolean;
  ranges: Partial<Record<RangeKey, Range>>;
  sort: Sort;
}

function parseRange(v: string): Range | null {
  const m = /^\s*([0-9.eE+]*)\s*-\s*([0-9.eE+]*)\s*$/.exec(v);
  if (!m) return null;
  const n = (x: string) => (x === "" ? null : Number.isFinite(Number(x)) ? Number(x) : NaN);
  let min = n(m[1]);
  let max = n(m[2]);
  if (Number.isNaN(min) || Number.isNaN(max)) return null;
  if (min != null && max != null && min > max) [min, max] = [max, min];
  return min == null && max == null ? null : { min, max };
}

/** Read the Candidates page's filter from its query ("kind=mover&sort=fewest", "?…" or "/…"). */
export function parseFilter(query: string): ListFilter {
  const p = new URLSearchParams(query.replace(/^[#/?]+/, ""));
  const lists = Object.fromEntries(
    LIST_KEYS.map((k) => [
      k,
      [...new Set((p.get(k) ?? "").split(",").map((x) => x.trim()).filter((x) => /^[A-Za-z0-9_.-]{1,64}$/.test(x)))],
    ]),
  ) as Record<ListKey, string[]>;
  const tri = (v: string | null): Tri => (v === "yes" || v === "no" ? v : "any");
  const ranges: Partial<Record<RangeKey, Range>> = {};
  for (const k of RANGE_KEYS) {
    const r = parseRange(p.get(k) ?? "");
    if (r) ranges[k] = r;
  }
  const sort = p.get("sort") as Sort | null;
  return {
    lists,
    gaia: tri(p.get("gaia")),
    catwise: tri(p.get("catwise")),
    hideDoubts: p.get("doubts") === "hide",
    hideCautions: p.get("cautions") === "hide",
    hideMine: p.get("mine") === "hide",
    ranges,
    sort: sort && SORTS.includes(sort) ? sort : "promising",
  };
}

/** Motion in arcsec per year, comparable across kinds (movers in ″/h are far faster). */
export function motionArcsecYr(c: Candidate): number | null {
  if (c.rate_arcsec_h != null) return c.rate_arcsec_h * 24 * 365.25;
  if (c.pm_mas_yr != null) return c.pm_mas_yr / 1000;
  return null;
}

/** Motion significance: proper motion over its 1σ error. */
export function significance(c: Candidate): number | null {
  return c.pm_mas_yr != null && c.pm_err_mas_yr != null && c.pm_err_mas_yr > 0 ? c.pm_mas_yr / c.pm_err_mas_yr : null;
}

const RANGE_VALUE: Record<RangeKey, (c: Candidate) => number | null> = {
  score: (c) => c.score,
  sig: significance,
  pm: (c) => c.pm_mas_yr,
  rate: (c) => c.rate_arcsec_h,
  snr: (c) => c.snr,
  flux: (c) => c.flux_ujy,
  // null = nothing known within the search radius: far.
  known: (c) => c.nearest_known_arcsec ?? Infinity,
  b: (c) => Math.abs(c.galactic_b),
};

/** Reason codes that weaken a candidate (the dashed chips). */
export const DOUBT_CODES = ["few_dots", "bad_fit", "over_pm_cap", "at_control_level", "short_span"];

/** none: no votes; some: votes but no agreement; consensus: agreed (src/vote's rule). */
export function reviewState(k: Counts): "none" | "some" | "consensus" {
  if (agreed(k)) return "consensus";
  return total(k) > 0 ? "some" : "none";
}

/** Pipeline promise: grade A first and grade C last, then score high to low, motion significance,
 *  S/N, then id (nulls last). */
export function byPromiseList(a: Candidate, b: Candidate): number {
  return (
    leadRank(gradeOf(a)) - leadRank(gradeOf(b)) ||
    desc(a.score, b.score) ||
    desc(significance(a), significance(b)) ||
    desc(a.snr, b.snr) ||
    a.id.localeCompare(b.id)
  );
}

/** The candidates the Candidates page shows for this filter, in its order. */
export function applyFilter(
  cands: Candidate[],
  f: ListFilter,
  counts: Map<string, Counts> = new Map(),
  votedFor: (id: string) => Answer | null = () => null,
): Candidate[] {
  const k = (c: Candidate) => counts.get(c.id) ?? emptyCounts();
  const has = (list: string[], v: string) => !list.length || list.includes(v);
  const tri = (t: Tri, v: boolean) => t === "any" || (t === "yes") === v;
  const out = cands.filter((c) => {
    const L = f.lists;
    if (!has(L.kind, c.kind) || !has(L.det, String(c.det)) || !has(L.zone, c.zone)) return false;
    if (!has(L.grade, gradeOf(c) ?? "none")) return false;
    if (L.review.length && !L.review.includes(reviewState(k(c)))) return false;
    if (L.verdict.length) {
      const v = agreed(k(c))?.answer;
      if (!v || !L.verdict.includes(v)) return false;
    }
    if (L.label.length && !c.labels.some((l) => L.label.includes(l))) return false;
    if (L.reason.length && !c.reasons.some((r) => L.reason.includes(r))) return false;
    if (!tri(f.gaia, c.gaia_match) || !tri(f.catwise, c.catwise_match)) return false;
    if (f.hideDoubts && c.reasons.some((r) => DOUBT_CODES.includes(r))) return false;
    if (f.hideCautions && c.flags.length) return false;
    if (f.hideMine && votedFor(c.id)) return false;
    for (const key of RANGE_KEYS) {
      const r = f.ranges[key];
      if (!r) continue;
      const v = RANGE_VALUE[key](c);
      if (v == null || Number.isNaN(v)) return false;
      if ((r.min != null && v < r.min) || (r.max != null && v > r.max)) return false;
    }
    return true;
  });
  const n = (c: Candidate) => total(k(c));
  const by: Record<Sort, (a: Candidate, b: Candidate) => number> = {
    promising: byPromiseList,
    grade: (a, b) => gradeRank(gradeOf(a)) - gradeRank(gradeOf(b)) || desc(a.snr, b.snr) || byPromiseList(a, b),
    fewest: (a, b) => n(a) - n(b) || byPromiseList(a, b),
    most: (a, b) => n(b) - n(a) || byPromiseList(a, b),
    brightest: (a, b) => desc(a.flux_ujy, b.flux_ujy) || byPromiseList(a, b),
    fastest: (a, b) => desc(motionArcsecYr(a), motionArcsecYr(b)) || byPromiseList(a, b),
    newest: (a, b) => b.mjd - a.mjd || byPromiseList(a, b),
  };
  return out.sort(by[f.sort]);
}

/** Does the filter need vote counts (review state, verdict, vote sorts)? */
export function needsCounts(f: ListFilter): boolean {
  return f.lists.review.length > 0 || f.lists.verdict.length > 0 || f.sort === "fewest" || f.sort === "most";
}

/** Previous and next candidate around id in an ordered list (null at the ends or when absent). */
export function neighbours<T extends { id: string }>(items: T[], id: string): { prev: T | null; next: T | null; index: number } {
  const i = items.findIndex((c) => c.id === id);
  if (i < 0) return { prev: null, next: null, index: -1 };
  return { prev: items[i - 1] ?? null, next: items[i + 1] ?? null, index: i };
}

// ---- URLs ----------------------------------------------------------------------------------

/** #/c/<id> may carry Discover's query: "<id>?kind=mover". */
export function parseCandidateParam(param: string | null): { id: string; query: string } {
  if (!param) return { id: "", query: "" };
  const q = param.indexOf("?");
  const id = (q < 0 ? param : param.slice(0, q)).replace(/\/+$/, "").trim();
  return { id, query: q < 0 ? "" : param.slice(q + 1) };
}

/** Explore deep-link keys (src/app/state.ts) that are not Discover filters. */
const VIEW_KEYS = ["t", "ra", "dec", "clock", "fov", "det", "embed"];

/** The candidate list's route (DESIGN.md "Pages"; "#/discover" is its old name). */
export const LIST_HASH = "#/candidates";

/** Is this URL the candidate list? Mirrors src/app/app.ts parseRoute: "#/candidates…", "#/discover…". */
export function isDiscoverUrl(hash: string): boolean {
  const head = hash.replace(/^#\/?/, "").split(/[/?]/)[0];
  return head === "candidates" || head === "discover";
}

/**
 * The list's filter query from its URL: what follows the route in the hash
 * ("#/candidates/kind=mover", "#/candidates/?kind=mover", "#/candidates?kind=mover"),
 * else the page query minus the Explore view keys.
 */
export function discoverQuery(hash: string, search: string): string {
  const rest = hash.replace(/^#\/?(candidates|discover)/, "").replace(/^[/?]+/, "");
  if (rest) return rest;
  const s = new URLSearchParams(search);
  for (const k of VIEW_KEYS) s.delete(k);
  return s.toString();
}

// ---- keyboard ------------------------------------------------------------------------------

export type KeyAction =
  | { kind: "vote"; answer: Answer }
  | { kind: "skip" }
  | { kind: "back" }
  | { kind: "next" }
  | { kind: "confirm" };

export interface KeyLike {
  key: string;
  metaKey?: boolean;
  ctrlKey?: boolean;
  altKey?: boolean;
  repeat?: boolean;
  target?: EventTarget | null;
}

const isTyping = (t: EventTarget | null | undefined): boolean => {
  const el = t as { closest?: (s: string) => unknown } | null | undefined;
  return !!el && typeof el.closest === "function" && !!el.closest("input, textarea, select, [contenteditable='true']");
};
const isControl = (t: EventTarget | null | undefined): boolean => {
  const el = t as { closest?: (s: string) => unknown } | null | undefined;
  return !!el && typeof el.closest === "function" && !!el.closest("button, a[href], summary");
};

/**
 * What a key press means on the review pages. 1–4 vote (mover, changing, junk, not sure);
 * with queue keys: S skips, ← goes back, → goes on, Enter confirms. Nothing while typing,
 * with a modifier, or on auto-repeat.
 */
export function keyAction(ev: KeyLike, opts: { queue: boolean }): KeyAction | null {
  if (ev.metaKey || ev.ctrlKey || ev.altKey || ev.repeat || isTyping(ev.target)) return null;
  const i = ["1", "2", "3", "4"].indexOf(ev.key);
  if (i >= 0) return { kind: "vote", answer: ANSWERS[i] };
  if (!opts.queue) return null;
  if (ev.key === "s" || ev.key === "S") return { kind: "skip" };
  if (ev.key === "ArrowLeft") return { kind: "back" };
  if (ev.key === "ArrowRight") return { kind: "next" };
  // Enter on a focused button or link already clicks it.
  if (ev.key === "Enter" && !isControl(ev.target)) return { kind: "confirm" };
  return null;
}
