// Community numbers, computed from vote rows: consensus (DESIGN.md "Votes and consensus"),
// totals, activity over time, the most debated candidates, this browser's agreement rate and
// the list for astronomers with its CSV and JSON exports. Pure: no DOM, no Firebase.
import type { CandidateKind, Clock, Det } from "../api/types";
import { formatMjd } from "../app/astro";
import { ANSWERS, CONSENSUS, consensus, countVotes, csvCell, emptyCounts, toVoteRecord, total, type Answer, type Counts, type VoteRecord } from "../vote/model";

/** The rule lives in src/vote (CONSENSUS, consensus()): at least 5 votes and at least 70% on one answer. */
export { CONSENSUS };
/** The rule's share as a whole percentage, for sentences. */
export const CONSENSUS_PCT = Math.round(CONSENSUS.share * 100);

/** Answers that put a candidate on the list for astronomers. */
export const REAL_ANSWERS = ["mover", "changing"] as const satisfies readonly Answer[];
export type RealAnswer = (typeof REAL_ANSWERS)[number];

/** One vote as read from the store (src/vote VoteRecord): ts in epoch ms, or null while pending. */
export type VoteRow = VoteRecord;

const isAnswer = (a: unknown): a is Answer => (ANSWERS as readonly unknown[]).includes(a);

/**
 * Keep well-formed votes with a voter id only, and one per voter and candidate (the earliest):
 * the rules can't stop a second vote from the same browser, so it isn't counted twice here.
 */
export function cleanVotes(raw: readonly unknown[]): VoteRow[] {
  const rows = raw.map(toVoteRecord).filter((v): v is VoteRecord => v !== null && v.voter !== "");
  rows.sort((a, b) => (a.ts ?? Infinity) - (b.ts ?? Infinity));
  const seen = new Set<string>();
  return rows.filter((v) => {
    const key = `${v.voter}\u0000${v.candidate_id}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

/** Vote counts per candidate id. */
export function tally(votes: readonly VoteRow[]): Map<string, Counts> {
  return countVotes(votes as VoteRow[]);
}

export type ReviewState = "unreviewed" | "in_review" | "consensus";

export interface Verdict {
  state: ReviewState;
  /** The agreed answer once at consensus, else null. */
  answer: Answer | null;
  /** The most chosen answer; null without votes or when two answers tie at the top. */
  leading: Answer | null;
  /** Votes for the top answer. */
  top: number;
  /** top ÷ votes, 0–1 (0 without votes). */
  share: number;
  votes: number;
}

export function verdict(c: Counts | undefined): Verdict {
  const n = c ? total(c) : 0;
  if (!c || n === 0) return { state: "unreviewed", answer: null, leading: null, top: 0, share: 0, votes: 0 };
  let top = 0;
  let leading: Answer | null = null;
  let tie = false;
  for (const a of ANSWERS) {
    if (c[a] > top) {
      top = c[a];
      leading = a;
      tie = false;
    } else if (c[a] === top && top > 0) tie = true;
  }
  if (tie) leading = null;
  // The decision itself is src/vote's consensus(), so every page agrees on it.
  const agreed = consensus(c);
  return { state: agreed ? "consensus" : "in_review", answer: agreed?.answer ?? null, leading, top, share: top / n, votes: n };
}

export const isReal = (a: Answer | null): a is RealAnswer => a === "mover" || a === "changing";

// ---- totals ----------------------------------------------------------------------------------

export interface Summary {
  votes: number;
  /** Candidates with at least one vote. */
  reviewed: number;
  /** Distinct voter ids: browsers, not verified people. */
  people: number;
  inReview: number;
  consensus: number;
  /** Consensus candidates by agreed answer. */
  consensusBy: Counts;
  /** Consensus movers and changing sources. */
  forAstronomers: number;
  first: number | null;
  last: number | null;
}

export function summarize(votes: readonly VoteRow[], counts: Map<string, Counts> = tally(votes)): Summary {
  const consensusBy = emptyCounts();
  let consensus = 0;
  let inReview = 0;
  for (const c of counts.values()) {
    const v = verdict(c);
    if (v.state === "consensus" && v.answer) {
      consensus++;
      consensusBy[v.answer]++;
    } else if (v.state === "in_review") inReview++;
  }
  const times = votes.map((v) => v.ts).filter((t): t is number => t != null);
  return {
    votes: votes.length,
    reviewed: counts.size,
    people: new Set(votes.map((v) => v.voter)).size,
    inReview,
    consensus,
    consensusBy,
    forAstronomers: consensusBy.mover + consensusBy.changing,
    first: times.length ? Math.min(...times) : null,
    last: times.length ? Math.max(...times) : null,
  };
}

// ---- activity --------------------------------------------------------------------------------

export type BinUnit = "hour" | "day";
export interface Bin {
  start: number;
  end: number;
  count: number;
}
export interface Activity {
  unit: BinUnit;
  bins: Bin[];
  /** Votes without a time (Firestore writes not yet confirmed). */
  undated: number;
}

const HOUR = 3_600_000;
/** Most bins shown: two days by the hour, or two months by the day. */
export const MAX_BINS: Record<BinUnit, number> = { hour: 48, day: 60 };
/** Fewest bins shown, so a quiet start still reads as a timeline. */
export const MIN_BINS = 12;

/** Start of the hour or the (local) day that holds t. */
export function floorTo(t: number, unit: BinUnit): number {
  const d = new Date(t);
  if (unit === "hour") d.setMinutes(0, 0, 0);
  else d.setHours(0, 0, 0, 0);
  return d.getTime();
}

function nextBin(t: number, unit: BinUnit): number {
  if (unit === "hour") return t + HOUR;
  const d = new Date(t);
  d.setDate(d.getDate() + 1);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

function prevBin(t: number, unit: BinUnit): number {
  if (unit === "hour") return t - HOUR;
  const d = new Date(t);
  d.setDate(d.getDate() - 1);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

/**
 * Votes per hour when everything fits in two days, otherwise per day. The timeline ends at
 * now (quiet hours show as zero), or at the last vote if now is earlier, as with a clock skew.
 */
export function activity(votes: readonly VoteRow[], now: number): Activity {
  const times = votes.map((v) => v.ts).filter((t): t is number => t != null);
  const undated = votes.length - times.length;
  if (!times.length) return { unit: "hour", bins: [], undated };
  const first = Math.min(...times);
  const last = Math.max(...times, now);
  const unit: BinUnit = last - first <= MAX_BINS.hour * HOUR ? "hour" : "day";
  const end = floorTo(last, unit);
  let start = floorTo(first, unit);
  const starts: number[] = [end];
  while (starts.length < MAX_BINS[unit] && (starts[0] > start || starts.length < MIN_BINS)) starts.unshift(prevBin(starts[0], unit));
  start = starts[0];
  const bins: Bin[] = starts.map((s) => ({ start: s, end: nextBin(s, unit), count: 0 }));
  for (const t of times) {
    if (t < start) continue;
    // Bins are in time order; find by the floor, which is exact for hours and local days.
    const f = floorTo(t, unit);
    const b = bins.find((x) => x.start === f);
    if (b) b.count++;
  }
  return { unit, bins, undated };
}

// ---- most debated ----------------------------------------------------------------------------

export interface Debate {
  id: string;
  counts: Counts;
  verdict: Verdict;
}

/** Candidates not at consensus, with votes split across two or more answers: most evenly split first. */
export function mostDebated(counts: Map<string, Counts>, opts: { minVotes?: number; limit?: number } = {}): Debate[] {
  const { minVotes = 3, limit = 6 } = opts;
  const out: Debate[] = [];
  for (const [id, c] of counts) {
    const v = verdict(c);
    if (v.state === "consensus" || v.votes < minVotes) continue;
    if (ANSWERS.filter((a) => c[a] > 0).length < 2) continue;
    out.push({ id, counts: c, verdict: v });
  }
  return out
    .sort((a, b) => a.verdict.share - b.verdict.share || b.verdict.votes - a.verdict.votes || a.id.localeCompare(b.id))
    .slice(0, limit);
}

// ---- this browser -----------------------------------------------------------------------------

export interface Agreement {
  /** Candidates this browser voted on. */
  voted: number;
  /** Of those, how many reached consensus. */
  settled: number;
  /** Of the settled ones, how many with the same answer as this browser. */
  agreed: number;
  /** agreed ÷ settled, or null before any of them settles. */
  rate: number | null;
  /** This browser's answers, counted. */
  answers: Counts;
}

export function agreement(mine: Record<string, Answer>, counts: Map<string, Counts>): Agreement {
  const answers = emptyCounts();
  let voted = 0;
  let settled = 0;
  let agreed = 0;
  for (const [id, a] of Object.entries(mine)) {
    if (!isAnswer(a)) continue;
    voted++;
    answers[a]++;
    const v = verdict(counts.get(id));
    if (v.state !== "consensus") continue;
    settled++;
    if (v.answer === a) agreed++;
  }
  return { voted, settled, agreed, rate: settled ? agreed / settled : null, answers };
}

// ---- the list for astronomers ----------------------------------------------------------------

/** What the page knows about a candidate: from candidates.json, or a dot someone flagged. */
export interface CandInfo {
  id: string;
  /** "flag": a dot a visitor flagged in Explore (no pipeline kind). */
  kind: CandidateKind | "flag";
  ra: number;
  dec: number;
  mjd: number;
  clock: Clock;
  det: Det | null;
  why: string | null;
}

export type NextStep = "mpc" | "backyard_worlds";

/** Fast movers on the Hours clock may be solar-system objects: the MPC. Everything else: Backyard Worlds. */
export function nextStep(info: Pick<CandInfo, "kind" | "clock"> | undefined): NextStep {
  if (!info) return "backyard_worlds";
  if (info.kind === "mover") return "mpc";
  if (info.kind === "flag" && info.clock === "hours") return "mpc";
  return "backyard_worlds";
}

export interface AstroRow {
  id: string;
  kind: CandInfo["kind"] | null;
  answer: RealAnswer;
  votes: number;
  agree: number;
  share: number;
  counts: Counts;
  ra: number | null;
  dec: number | null;
  mjd: number | null;
  clock: Clock | null;
  det: Det | null;
  why: string | null;
  next: NextStep;
}

/** Consensus movers and changing sources, strongest agreement first. */
export function astronomersList(counts: Map<string, Counts>, info: (id: string) => CandInfo | undefined): AstroRow[] {
  const rows: AstroRow[] = [];
  for (const [id, c] of counts) {
    const v = verdict(c);
    if (v.state !== "consensus" || !isReal(v.answer)) continue;
    const i = info(id);
    rows.push({
      id,
      kind: i?.kind ?? null,
      answer: v.answer,
      votes: v.votes,
      agree: v.top,
      share: v.share,
      counts: c,
      ra: i?.ra ?? null,
      dec: i?.dec ?? null,
      mjd: i?.mjd ?? null,
      clock: i?.clock ?? null,
      det: i?.det ?? null,
      why: i?.why ?? null,
      next: nextStep(i),
    });
  }
  return rows.sort((a, b) => b.share - a.share || b.votes - a.votes || a.id.localeCompare(b.id));
}

export const ASTRO_CSV_COLUMNS = [
  "id",
  "kind",
  "consensus",
  "votes",
  "agree",
  "agree_pct",
  "mover",
  "changing",
  "junk",
  "unsure",
  "ra_deg",
  "dec_deg",
  "mjd",
  "date_utc",
  "clock",
  "det",
  "next_step",
  "link",
] as const;

const NEXT_NAME: Record<NextStep, string> = { mpc: "Minor Planet Center", backyard_worlds: "Backyard Worlds" };

/** Link to the candidate's page on the site, from the page's own address. */
export function candidateLink(base: string, id: string): string {
  const u = new URL(base);
  u.search = "";
  u.hash = `#/c/${encodeURIComponent(id)}`;
  return u.href;
}

const round = (x: number | null, digits: number) => (x == null ? null : +x.toFixed(digits));

export function toAstroCsv(rows: readonly AstroRow[], base: string): string {
  const lines: string[] = [ASTRO_CSV_COLUMNS.join(",")];
  for (const r of rows) {
    lines.push(
      [
        r.id,
        r.kind,
        r.answer,
        r.votes,
        r.agree,
        Math.round(r.share * 100),
        r.counts.mover,
        r.counts.changing,
        r.counts.junk,
        r.counts.unsure,
        round(r.ra, 6),
        round(r.dec, 6),
        round(r.mjd, 6),
        r.mjd == null ? null : formatMjd(r.mjd, true),
        r.clock,
        r.det,
        NEXT_NAME[r.next],
        candidateLink(base, r.id),
      ]
        .map(csvCell)
        .join(","),
    );
  }
  return lines.join("\r\n") + "\r\n";
}

export interface AstroJsonMeta {
  generated: string;
  /** Where the votes came from: everyone's (firestore) or this browser's only (local). */
  votes_from: "firestore" | "local";
  data_release: string | null;
}

export function toAstroJson(rows: readonly AstroRow[], meta: AstroJsonMeta, base: string): string {
  return (
    JSON.stringify(
      {
        ...meta,
        rule: { min_votes: CONSENSUS.minVotes, min_agree_pct: CONSENSUS_PCT, answers: [...REAL_ANSWERS] },
        note: "Leads from SkyShift's community votes on NASA SPHEREx data, not confirmed discoveries. Positions in degrees (ICRS), times in MJD (UTC).",
        count: rows.length,
        candidates: rows.map((r) => ({
          id: r.id,
          kind: r.kind,
          consensus: r.answer,
          votes: r.votes,
          agree: r.agree,
          agree_pct: Math.round(r.share * 100),
          counts: { ...r.counts },
          ra_deg: round(r.ra, 6),
          dec_deg: round(r.dec, 6),
          mjd: round(r.mjd, 6),
          clock: r.clock,
          det: r.det,
          why: r.why,
          next_step: r.next,
          link: candidateLink(base, r.id),
        })),
      },
      null,
      2,
    ) + "\n"
  );
}
