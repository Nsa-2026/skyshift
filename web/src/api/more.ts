// The Less likely files (contracts/api.md v3.24): each zone's open grade C movers live in
// /data/candidates-more/<zone>.json, indexed by candidates.json `more`, so the list stays light. A
// zone's file is read only when someone opens it ("Less likely" on To check, or a link to one of its
// candidates), once per page load, and shared by every page.
//
//   const entries = moreOf(candidatesJson);   // [] for older files
//   const rows = await loadMore(entries[0]);  // that zone's candidates (raw, as in the file)
//   loadedMore(entries)                       // what's already in, synchronously
//   isFromMore(id)                            // did this candidate come from a Less likely file?
//   setMoreOpen(e, true); openedMore(entries) // which read files are on the list (To check and the Viewer)
import { candidateFramesUrl } from "./client";
import { gradeOf } from "./grades";
import type { Candidate, CandidateKind, CandidatesMore, CandidatesMoreFile } from "./types";

const KINDS: readonly CandidateKind[] = ["mover", "high_pm", "spectral", "shape"];

/** candidates.json `more`, read defensively; [] when absent (older files) or unreadable. */
export function moreOf(x: { more?: unknown } | null | undefined): CandidatesMore[] {
  const raw = Array.isArray(x?.more) ? x!.more : [];
  const out: CandidatesMore[] = [];
  for (const m of raw) {
    if (!m || typeof m !== "object") continue;
    const o = m as Record<string, unknown>;
    const zone = typeof o.zone === "string" ? o.zone.trim() : "";
    const url = typeof o.url === "string" ? o.url.trim() : "";
    const count = Number(o.count);
    if (!zone || !url || !Number.isFinite(count) || count <= 0) continue;
    const kind = KINDS.includes(o.kind as CandidateKind) ? (o.kind as CandidateKind) : "mover";
    if (out.some((e) => e.url === url)) continue;
    out.push({ zone, kind, grade: gradeOf(o), count: Math.round(count), url });
  }
  return out;
}

/** How many candidates the Less likely files hold in all. */
export const moreTotal = (entries: readonly CandidatesMore[]): number => entries.reduce((n, e) => n + e.count, 0);

/** The file's address: `url` resolves against candidates.json, like `frames_url`. */
export const moreUrl = (e: Pick<CandidatesMore, "url">): string => candidateFramesUrl(e.url);

const pending = new Map<string, Promise<Candidate[]>>();
const loaded = new Map<string, Candidate[]>();
const ids = new Set<string>();

/** One zone's Less likely file: its candidates, as the file gives them. Read once; a failure isn't kept. */
export function loadMore(e: Pick<CandidatesMore, "url">, signal?: AbortSignal): Promise<Candidate[]> {
  const url = moreUrl(e);
  const hit = loaded.get(url);
  if (hit) return Promise.resolve(hit);
  let p = pending.get(url);
  if (!p) {
    p = (async () => {
      const res = await fetch(url, { signal });
      if (!res.ok) throw new Error(`${res.status} for ${url}`);
      const doc = (await res.json()) as Partial<CandidatesMoreFile>;
      const rows = (Array.isArray(doc?.candidates) ? doc.candidates : []).filter(
        (c): c is Candidate => !!c && typeof c === "object" && typeof (c as Candidate).id === "string" && !!(c as Candidate).id,
      );
      for (const c of rows) ids.add(c.id);
      loaded.set(url, rows);
      return rows;
    })();
    pending.set(url, p);
    p.catch(() => pending.delete(url)).finally(() => {
      if (loaded.has(url)) pending.delete(url);
    });
  }
  return p;
}

/** Is this zone's file already read? */
export const isMoreLoaded = (e: Pick<CandidatesMore, "url">): boolean => loaded.has(moreUrl(e));

/** The candidates of the given files that are already read (nothing is fetched). */
export function loadedMore(entries: readonly CandidatesMore[]): Candidate[] {
  return entries.flatMap((e) => loaded.get(moreUrl(e)) ?? []);
}

/** Did this candidate come from a Less likely file? (It was asked for, so the list never sets it aside.) */
export const isFromMore = (id: string): boolean => ids.has(id);

// Which read files are on the list, shared by To check and the Viewer: a zone opened under "Less
// likely" (or by a link to one of its candidates) stays open on both until it is taken off.
const opened = new Set<string>();
let version = 0;

/** Is this zone's file read and on the list? */
export const isMoreOpen = (e: Pick<CandidatesMore, "url">): boolean => opened.has(moreUrl(e));

/** Put a read file on the list, or take it off. A file not read yet can't be put on. */
export function setMoreOpen(e: Pick<CandidatesMore, "url">, on: boolean): void {
  const url = moreUrl(e);
  if (on === opened.has(url) || (on && !loaded.has(url))) return;
  if (on) opened.add(url);
  else opened.delete(url);
  version++;
}

/** The given files that are on the list. */
export const openedMore = (entries: readonly CandidatesMore[]): CandidatesMore[] => entries.filter(isMoreOpen);

/** Changes each time a file goes on or off the list, so a page shown again can tell. */
export const moreVersion = (): number => version;

/** Tests only: forget every file read. */
export function _resetMoreForTests(): void {
  pending.clear();
  loaded.clear();
  ids.clear();
  opened.clear();
  version++;
}
