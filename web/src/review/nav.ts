// Remembers the last Discover URL (its filter lives there), so a candidate page can step through
// the same list and link back to it. Kept in sessionStorage: one tab, one browsing session.
import { LIST_HASH, discoverQuery, isDiscoverUrl } from "./model";

const KEY = "skyshift.discover.last";
let memo: { hash: string; search: string } | null = null;

function record(): void {
  if (typeof location === "undefined") return;
  if (!isDiscoverUrl(location.hash)) return;
  memo = { hash: location.hash, search: location.search };
  try {
    sessionStorage.setItem(KEY, JSON.stringify(memo));
  } catch {
    /* blocked: memory still has it */
  }
}

function read(): { hash: string; search: string } | null {
  if (memo) return memo;
  try {
    const v = JSON.parse(sessionStorage.getItem(KEY) ?? "null");
    if (v && typeof v.hash === "string" && typeof v.search === "string") return (memo = v);
  } catch {
    /* nothing kept */
  }
  return null;
}

let started = false;
/** Start listening (idempotent). Called when this module's pages load, at app start. */
export function trackDiscover(): void {
  if (started || typeof window === "undefined") return;
  started = true;
  record();
  window.addEventListener("hashchange", record);
}

/** Discover's filter query from the last Discover URL, or "" (every candidate, score order). */
export function lastDiscoverQuery(): string {
  const d = read();
  return d ? discoverQuery(d.hash, d.search) : "";
}

/** The hash to go back to the list: the last list URL (with its filter), else the list. */
export function lastDiscoverHash(): string {
  const d = read();
  return d?.hash && isDiscoverUrl(d.hash) ? d.hash : LIST_HASH;
}
