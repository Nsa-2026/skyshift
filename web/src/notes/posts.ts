// Notes people leave on an object to check: short messages written beside a vote.
//
// They are written on the object's own page (#/c/<id>, src/notes/panel.ts) and read back in two
// places: that same panel, and the roundup in the Community page's right column.
//
// Where they live: this browser, in localStorage (KEY below), the same way votes do before the
// vote server is configured (src/vote/store LocalStore). That means a note is kept for the person
// who wrote it and is not sent anywhere. Until a notes collection exists in the contract
// (contracts/api.md) with Firestore rules to match, there is nothing to send it to, and the panel
// says so rather than implying an audience.
//
// No DOM here: this file is the model, so it can be tested without a page.
import type { KV } from "../vote/model";

/** A note. `about` is an object id when the note is about one, else null. */
export interface Post {
  id: string;
  about: string | null;
  /** What the author called themselves on this device. "" reads as Anonymous. */
  name: string;
  body: string;
  /** Epoch ms. */
  ts: number;
}

export const KEY = "skyshift.local.posts";
/** The name this browser signs notes with, set on Community. Here so src/notes owns no cycle. */
export const NICKNAME_KEY = "skyshift.nickname";
export const NAME_MAX = 40;
export const BODY_MAX = 600;
/** Most notes kept in this browser; the oldest fall off the end. */
export const KEEP = 200;

const trim = (s: string) => s.replace(/\s+/g, " ").trim();

/** Read a stored note back defensively: anything malformed is dropped, never thrown. */
export function toPost(d: unknown): Post | null {
  if (!d || typeof d !== "object") return null;
  const o = d as Record<string, unknown>;
  if (typeof o.id !== "string" || !o.id) return null;
  if (typeof o.body !== "string") return null;
  const body = trim(o.body).slice(0, BODY_MAX);
  if (!body) return null;
  const ts = typeof o.ts === "number" && Number.isFinite(o.ts) ? o.ts : null;
  if (ts === null) return null;
  return {
    id: o.id,
    about: typeof o.about === "string" && o.about ? o.about : null,
    name: typeof o.name === "string" ? trim(o.name).slice(0, NAME_MAX) : "",
    body,
    ts,
  };
}

/** Stored notes, newest first, one per id. */
export function cleanPosts(raw: readonly unknown[]): Post[] {
  const seen = new Set<string>();
  const out: Post[] = [];
  for (const d of raw) {
    const p = toPost(d);
    if (!p || seen.has(p.id)) continue;
    seen.add(p.id);
    out.push(p);
  }
  return out.sort((a, b) => b.ts - a.ts || a.id.localeCompare(b.id));
}

export type PostProblem = "empty" | "too_long";

/** What is wrong with a note someone is writing, or null when it can be kept. */
export function checkBody(body: string): PostProblem | null {
  const t = trim(body);
  if (!t) return "empty";
  if (t.length > BODY_MAX) return "too_long";
  return null;
}

export const PROBLEM_TEXT: Record<PostProblem, string> = {
  empty: "Write something first.",
  too_long: `Keep a note under ${BODY_MAX} characters.`,
};

/** A new note. The id is random: notes are never counted, so it needs no meaning. */
export function newPost(input: { about?: string | null; name?: string; body: string }, now: number): Post {
  return {
    id: `p-${now.toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
    about: input.about ?? null,
    name: trim(input.name ?? "").slice(0, NAME_MAX),
    body: trim(input.body).slice(0, BODY_MAX),
    ts: now,
  };
}

/** Notes about one object, newest first. */
export function postsAbout(posts: readonly Post[], id: string): Post[] {
  return posts.filter((p) => p.about === id);
}

/** How many objects the notes talk about. */
export function objectsDiscussed(posts: readonly Post[]): number {
  return new Set(posts.filter((p) => p.about).map((p) => p.about!)).size;
}

const MIN = 60_000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

/** "just now", "12 min ago", "3 hours ago", "2 days ago", then a plain date. */
export function timeAgo(ts: number, now: number): string {
  const d = now - ts;
  if (d < 0 || d < MIN) return "just now";
  if (d < HOUR) {
    const m = Math.floor(d / MIN);
    return `${m} min ago`;
  }
  if (d < DAY) {
    const h = Math.floor(d / HOUR);
    return `${h} ${h === 1 ? "hour" : "hours"} ago`;
  }
  if (d < 7 * DAY) {
    const n = Math.floor(d / DAY);
    return `${n} ${n === 1 ? "day" : "days"} ago`;
  }
  return new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short" }).format(new Date(ts));
}

/** The author's name as it should read. */
export const authorName = (p: Post): string => p.name || "Anonymous";

// ---- storage ---------------------------------------------------------------------------------

/** Notes kept in this browser. Never throws: unreadable storage reads as no notes. */
export function readPosts(kv: KV): Post[] {
  try {
    const raw = kv.get(KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    return Array.isArray(parsed) ? cleanPosts(parsed) : [];
  } catch {
    return [];
  }
}

/** Keep a note. Returns the list as it now stands (newest first, capped at KEEP). */
export function addPost(kv: KV, post: Post): Post[] {
  const all = cleanPosts([post, ...readPosts(kv)]).slice(0, KEEP);
  try {
    kv.set(KEY, JSON.stringify(all));
  } catch {
    // Storage full or blocked: the note still shows this session.
  }
  return all;
}

/** Remove one note (only the author's own browser has it). */
export function removePost(kv: KV, id: string): Post[] {
  const all = readPosts(kv).filter((p) => p.id !== id);
  try {
    kv.set(KEY, JSON.stringify(all));
  } catch {
    // ignore
  }
  return all;
}
