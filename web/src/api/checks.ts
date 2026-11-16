// What a candidate still has to be checked for, and the certain matches listed apart
// (contracts/api.md v3.23, candidates.json `status`, `known_name`, `checks`). Read defensively from
// any row: older files have none of these fields (status open, no name, no checks). Pure: no DOM.
//
//   status   open: to check · known: certainly a known body (known_name) · flaw: certainly the instrument
//   checks   [{ code, level, text, look, values }], shown strong, warn, then ok:
//            strong "Sign against", warn "Can't rule out", ok "Sign for"
import type { CandidateCheck, CandidateStatus, CheckLevel } from "./types";

export type { CandidateCheck, CandidateStatus, CheckLevel } from "./types";

export const STATUSES: readonly CandidateStatus[] = ["open", "known", "flaw"];
export const LEVELS: readonly CheckLevel[] = ["strong", "warn", "ok"];

/** A row's status; anything else (an older file, a value the site doesn't know) reads as open. */
export function statusOf(x: { status?: unknown } | null | undefined): CandidateStatus {
  const s = x?.status;
  return s === "known" || s === "flaw" ? s : "open";
}

/** The known body's name (status known only), or null. */
export function knownNameOf(x: { status?: unknown; known_name?: unknown } | null | undefined): string | null {
  const n = x?.known_name;
  return statusOf(x) === "known" && typeof n === "string" && n.trim() ? n.trim() : null;
}

const RANK: Record<CheckLevel, number> = { strong: 0, warn: 1, ok: 2 };
const str = (v: unknown): string => (typeof v === "string" ? v.trim() : "");

function readCheck(x: unknown): CandidateCheck | null {
  if (!x || typeof x !== "object") return null;
  const o = x as Record<string, unknown>;
  const code = str(o.code);
  const text = str(o.text);
  if (!code && !text) return null;
  // A level the site doesn't know is still something to look at: "can't rule out".
  const level: CheckLevel = o.level === "strong" || o.level === "ok" ? o.level : "warn";
  const values = o.values && typeof o.values === "object" && !Array.isArray(o.values) ? (o.values as Record<string, unknown>) : null;
  return { code, level, text: text || code, look: str(o.look), values };
}

const cache = new WeakMap<object, CandidateCheck[]>();

/** A row's checks, strong first, then warn, then ok (the file's order within a level). [] without any. */
export function checksOf(x: { checks?: unknown } | null | undefined): CandidateCheck[] {
  if (!x || typeof x !== "object") return [];
  const hit = cache.get(x);
  if (hit) return hit;
  const raw = Array.isArray(x.checks) ? x.checks : [];
  const out = raw
    .map(readCheck)
    .filter((c): c is CandidateCheck => c !== null)
    .map((c, i) => ({ c, i }))
    .sort((a, b) => RANK[a.c.level] - RANK[b.c.level] || a.i - b.i)
    .map(({ c }) => c);
  cache.set(x, out);
  return out;
}

/** Does the row carry a measured sign that it is not new or not real? */
export function hasStrong(x: { checks?: unknown } | null | undefined): boolean {
  return checksOf(x).some((c) => c.level === "strong");
}

/** Open, with a strong check: listed under "Less likely", after the rest of its kind. */
export function lessLikely(x: { status?: unknown; checks?: unknown } | null | undefined): boolean {
  return statusOf(x) === "open" && hasStrong(x);
}

/** Does any row of this list carry v3.23's fields? (Older files: nothing to list apart, no checks.) */
export function anyChecked(rows: readonly { status?: unknown; checks?: unknown }[]): boolean {
  return rows.some((r) => Array.isArray(r.checks) || typeof r.status === "string");
}

// ---- words ---------------------------------------------------------------------------------------

export interface LevelText {
  /** The mark in front of the check: "Sign against". */
  label: string;
  /** One sentence for its title attribute. */
  hint: string;
}

export const LEVEL_TEXT: Record<CheckLevel, LevelText> = {
  strong: { label: "Sign against", hint: "Measured: a sign it is not new or not real. It lowers the score; nothing is hidden for it." },
  warn: { label: "Can't rule out", hint: "Not ruled out by our measurements: look for yourself." },
  ok: { label: "Sign for", hint: "Measured: a sign it is real." },
};

export interface StatusText {
  /** The list's tab: "Known bodies". */
  tab: string;
  /** "known body" / "known bodies", for counts. */
  one: string;
  many: string;
  /** What the status means, one plain sentence. */
  plain: string;
}

export const STATUS_TEXT: Record<CandidateStatus, StatusText> = {
  open: { tab: "To check", one: "object to check", many: "objects to check", plain: "Nothing certain explains it yet: look and vote." },
  known: {
    tab: "Known bodies",
    one: "known body",
    many: "known bodies",
    plain: "Certainly a known asteroid, comet, planet or moon: predicted at its place every time it was seen, moving at its rate and in its direction. Listed apart, not deleted.",
  },
  flaw: {
    tab: "Instrument flaws",
    one: "instrument flaw",
    many: "instrument flaws",
    plain: "Certainly the camera, not the sky: for example, every photo put it on the same detector pixel while the sky moved under it. Listed apart, not deleted.",
  },
};

/** A check in one line of plain text: "Sign against: <text> Look: <look>". */
export function checkLine(c: CandidateCheck): string {
  const text = /[.!?]$/.test(c.text) ? c.text : `${c.text}.`;
  return `${LEVEL_TEXT[c.level].label}: ${text}${c.look ? ` Look: ${c.look}` : ""}`;
}
