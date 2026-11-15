// One list of notes to show, from two sources that must never be confused for each other: the
// notes this browser wrote (real, kept locally) and the sample comments (made up, from sample.ts).
// Every row carries `sample`, and both renderers tag it, so no made-up line can read as someone's.
//
// No DOM here, so the ordering and the mixing can be tested on their own.
import { type Post, authorName, postsAbout } from "./posts";
import { type SampleNote, sampleNotes } from "./sample";

/** A note as a list shows it. */
export interface Shown {
  /** Stable within one render; the key for the remove button on a real note. */
  key: string;
  about: string | null;
  who: string;
  body: string;
  ts: number;
  /** True for a made-up comment from sample.ts. Always shown as such. */
  sample: boolean;
}

const MIN = 60_000;

const fromPost = (p: Post): Shown => ({
  key: p.id,
  about: p.about,
  who: authorName(p),
  body: p.body,
  ts: p.ts,
  sample: false,
});

const fromSample = (about: string, s: SampleNote, i: number, now: number): Shown => ({
  key: `s-${about}-${i}`,
  about,
  who: s.who,
  body: s.body,
  ts: now - s.agoMin * MIN,
  sample: true,
});

const newestFirst = (a: Shown, b: Shown) => b.ts - a.ts || a.key.localeCompare(b.key);

/** What the panel on one object shows: your own notes on it first by time, then the samples. */
export function feedFor(about: string, posts: readonly Post[], now: number): Shown[] {
  return [
    ...postsAbout(posts, about).map(fromPost),
    ...sampleNotes(about).map((s, i) => fromSample(about, s, i, now)),
  ].sort(newestFirst);
}

/**
 * The Community roundup: notes from across objects, newest first, capped.
 *
 * `ids` are the objects worth sampling (the ones with votes in the snapshot). Your own notes are
 * always in, including ones on objects not in `ids` and ones written about nothing in particular,
 * because leaving a person's own note out of their own roundup would read as it having been lost.
 *
 * There are fewer sample lines than there are objects, so without this a line would come round
 * twice under two handles and the column would read as an echo. One line each, newest kept; your
 * own notes are never folded away, whatever anyone else wrote.
 */
export function roundup(ids: readonly string[], posts: readonly Post[], now: number, limit: number): Shown[] {
  const rows = posts.map(fromPost);
  for (const id of ids) rows.push(...sampleNotes(id).map((s, i) => fromSample(id, s, i, now)));
  const keys = new Set<string>();
  const bodies = new Set<string>();
  return rows
    .sort(newestFirst)
    .filter((r) => {
      if (keys.has(r.key)) return false;
      if (r.sample && bodies.has(r.body)) return false;
      keys.add(r.key);
      bodies.add(r.body);
      return true;
    })
    .slice(0, Math.max(0, limit));
}

/** How many distinct objects a list of rows talks about. */
export const objectsIn = (rows: readonly Shown[]): number =>
  new Set(rows.filter((r) => r.about).map((r) => r.about!)).size;
