// Review and Candidate pages: the pure parts. Consensus (DESIGN.md "Votes and consensus"), the
// review queue's order, the Discover filter read from its URL, keyboard mapping and URL parsing.
// No DOM, no Firebase, so tests are cheap.
import type { Candidate, CandidateKind, Det } from "../api/types";
import { ANSWERS, emptyCounts, total, type Answer, type Counts } from "../vote/model";

// ---- consensus ---------------------------------------------------------------------------------

/** A candidate reaches consensus with at least this many votes… */
export const CONSENSUS_MIN_VOTES = 5;
/** …and at least this share agreeing on one answer (70 %). */
export const CONSENSUS_PERCENT = 70;

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
  // Integer test, so 7 of 10 is exactly 70 %.
  if (topCount * 100 >= n * CONSENSUS_PERCENT) return { state: "reached", total: n, top, topCount, share, answer: top };
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
 * Candidates this browser already voted on are left out; candidates at consensus go last;
 * the rest by fewest votes, then score (no score last), then S/N.
 */
export function queueOrder(
  cands: Candidate[],
  counts: Map<string, Counts>,
  votedFor: (id: string) => Answer | null = () => null,
): Candidate[] {
  const info = new Map<string, { done: number; n: number }>();
  for (const c of cands) {
    const k = counts.get(c.id) ?? emptyCounts();
    info.set(c.id, { done: consensus(k).state === "reached" ? 1 : 0, n: total(k) });
  }
  return cands
    .filter((c) => votedFor(c.id) == null)
    .sort((a, b) => {
      const A = info.get(a.id)!;
      const B = info.get(b.id)!;
      return A.done - B.done || A.n - B.n || byPromise(a, b);
    });
}

// ---- Discover's filter, read from its URL ----------------------------------------------------

export type Preset = "promising" | "needs-votes" | "brown-dwarfs" | "unknown-movers" | "community-real";
export type Sort = "promising" | "fewest" | "most" | "brightest" | "fastest" | "newest";

export interface ListFilter {
  preset: Preset | null;
  kinds: CandidateKind[];
  zones: string[];
  dets: Det[];
  labels: string[];
  match: "none" | "gaia" | "catwise" | null;
  hideFlagged: boolean;
  minScore: number | null;
  minSnr: number | null;
  minAbsB: number | null;
  sort: Sort;
  q: string;
}

const PRESETS: Record<string, Preset> = {
  promising: "promising",
  "most-promising": "promising",
  "needs-votes": "needs-votes",
  "needs-your-vote": "needs-votes",
  "brown-dwarfs": "brown-dwarfs",
  "possible-brown-dwarfs": "brown-dwarfs",
  "unknown-movers": "unknown-movers",
  "unknown-fast-movers": "unknown-movers",
  "community-real": "community-real",
  "community-says-real": "community-real",
};
const SORTS: Record<string, Sort> = {
  promising: "promising",
  score: "promising",
  "most-promising": "promising",
  fewest: "fewest",
  "fewest-votes": "fewest",
  "votes-asc": "fewest",
  most: "most",
  "most-votes": "most",
  "votes-desc": "most",
  votes: "most",
  brightest: "brightest",
  bright: "brightest",
  flux: "brightest",
  fastest: "fastest",
  fast: "fastest",
  motion: "fastest",
  newest: "newest",
  new: "newest",
  date: "newest",
};
const KINDS: CandidateKind[] = ["mover", "high_pm", "spectral"];
const slug = (s: string) => s.trim().toLowerCase().replace(/[\s_]+/g, "-");
const list = (p: URLSearchParams, ...keys: string[]) =>
  keys
    .flatMap((k) => p.getAll(k))
    .flatMap((v) => v.split(","))
    .map((v) => v.trim())
    .filter(Boolean);
const num = (p: URLSearchParams, ...keys: string[]): number | null => {
  for (const k of keys) {
    const v = p.get(k);
    if (v != null && v.trim() !== "" && Number.isFinite(Number(v))) return Number(v);
  }
  return null;
};

/**
 * Read Discover's filter from a query string ("kind=mover&sort=fewest", with or without "?").
 * Tolerant: unknown keys and values are ignored, several spellings are accepted, and an empty
 * query means "every candidate, most promising first".
 */
export function parseFilter(query: string): ListFilter {
  const p = new URLSearchParams(query.replace(/^[?#]/, ""));
  const preset = PRESETS[slug(p.get("preset") ?? p.get("p") ?? "")] ?? null;
  const kinds = list(p, "kind", "kinds")
    .map((k) => slug(k).replace(/-/g, "_"))
    .map((k) => (k === "pm" || k === "highpm" || k === "moving_star" ? "high_pm" : k === "fast_mover" ? "mover" : k))
    .filter((k): k is CandidateKind => (KINDS as string[]).includes(k));
  const dets = list(p, "det", "dets", "detector")
    .map((d) => Number(d.replace(/^d/i, "")))
    .filter((d): d is Det => Number.isInteger(d) && d >= 1 && d <= 6);
  const m = slug(p.get("match") ?? p.get("catalog") ?? p.get("catalogue") ?? "");
  const hide = slug(p.get("flags") ?? p.get("cautions") ?? p.get("hide_flagged") ?? p.get("clean") ?? "");
  let sort = SORTS[slug(p.get("sort") ?? "")] ?? null;
  if (!sort) sort = preset === "needs-votes" ? "fewest" : "promising";
  return {
    preset,
    kinds,
    zones: list(p, "zone", "zones"),
    dets,
    labels: list(p, "label", "labels").map((l) => l.toLowerCase()),
    match: m === "none" || m === "gaia" || m === "catwise" ? m : null,
    hideFlagged: ["hide", "1", "true", "yes", "none", "clean"].includes(hide),
    minScore: num(p, "min_score", "score_min", "score"),
    minSnr: num(p, "min_snr", "snr_min", "snr"),
    minAbsB: num(p, "min_b", "b_min", "abs_b", "b"),
    sort,
    q: (p.get("q") ?? "").trim().toLowerCase(),
  };
}

/** Motion in arcsec per year, comparable across kinds (movers in ″/h are far faster). */
export function motionArcsecYr(c: Candidate): number | null {
  if (c.rate_arcsec_h != null) return c.rate_arcsec_h * 24 * 365.25;
  if (c.pm_mas_yr != null) return c.pm_mas_yr / 1000;
  return null;
}

const BROWN_DWARF_LABELS = ["red", "ch4", "brown_dwarf_like"];

/** The candidates Discover shows for this filter, in its order. */
export function applyFilter(cands: Candidate[], f: ListFilter, counts: Map<string, Counts> = new Map()): Candidate[] {
  const n = (c: Candidate) => total(counts.get(c.id) ?? emptyCounts());
  const out = cands.filter((c) => {
    if (f.kinds.length && !f.kinds.includes(c.kind)) return false;
    if (f.zones.length && !f.zones.includes(c.zone)) return false;
    if (f.dets.length && !f.dets.includes(c.det)) return false;
    if (f.labels.length && !f.labels.some((l) => c.labels.includes(l))) return false;
    if (f.match === "none" && (c.gaia_match || c.catwise_match)) return false;
    if (f.match === "gaia" && !c.gaia_match) return false;
    if (f.match === "catwise" && !c.catwise_match) return false;
    if (f.hideFlagged && c.flags.length) return false;
    if (f.minScore != null && (c.score == null || c.score < f.minScore)) return false;
    if (f.minSnr != null && (c.snr == null || c.snr < f.minSnr)) return false;
    if (f.minAbsB != null && Math.abs(c.galactic_b) < f.minAbsB) return false;
    if (f.q && !`${c.id} ${c.why}`.toLowerCase().includes(f.q)) return false;
    switch (f.preset) {
      case "brown-dwarfs":
        return c.kind === "high_pm" && !c.gaia_match && BROWN_DWARF_LABELS.some((l) => c.labels.includes(l));
      case "unknown-movers":
        return c.kind === "mover" && (c.nearest_known_arcsec == null || c.nearest_known_arcsec > 60);
      case "community-real":
        return forAstronomers(consensus(counts.get(c.id) ?? emptyCounts()));
      default:
        return true;
    }
  });
  const by: Record<Sort, (a: Candidate, b: Candidate) => number> = {
    promising: byPromise,
    fewest: (a, b) => n(a) - n(b) || byPromise(a, b),
    most: (a, b) => n(b) - n(a) || byPromise(a, b),
    brightest: (a, b) => desc(a.flux_ujy, b.flux_ujy) || byPromise(a, b),
    fastest: (a, b) => desc(motionArcsecYr(a), motionArcsecYr(b)) || byPromise(a, b),
    newest: (a, b) => b.mjd - a.mjd || byPromise(a, b),
  };
  return out.sort(by[f.sort]);
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

/** Is this URL the Discover page? Mirrors src/app/app.ts parseRoute: "", "#/", "#/?…". */
export function isDiscoverUrl(hash: string, search: string): boolean {
  const h = hash.replace(/^#\/?/, "");
  const head = h.split(/[/?]/)[0];
  if (head !== "" && head !== "discover") return false;
  if (!h) {
    const s = new URLSearchParams(search);
    if (s.has("t") || s.has("ra")) return false; // deep link: opens Explore
  }
  return true;
}

/** Discover's filter query from its URL: the hash's query, else the page query minus view keys. */
export function discoverQuery(hash: string, search: string): string {
  const i = hash.indexOf("?");
  if (i >= 0) return hash.slice(i + 1);
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
