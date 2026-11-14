// The Candidates page's list for a filter query, so a candidate page can step to its neighbours
// in the same order. Uses src/discover's own filter functions when that module exists (one
// meaning of "filtered"), else the same rules from ./model.
import type { Candidate } from "../api/types";
import type { Answer, Counts } from "../vote/model";
import { applyFilter, needsCounts, parseFilter } from "./model";

interface DiscoverFilters {
  queryToState(q: string): { sort: string };
  toRows(c: Candidate[], counts: Map<string, Counts>, votedOn: (id: string) => boolean): { c: Candidate }[];
  filterRows(rows: { c: Candidate }[], s: unknown): { rows: { c: Candidate }[] };
  sortRows(rows: { c: Candidate }[], key: string): { c: Candidate }[];
}

const modules = import.meta.glob("../discover/filters.ts");

async function discoverFilters(): Promise<DiscoverFilters | null> {
  const load = modules["../discover/filters.ts"];
  if (!load) return null;
  try {
    const m = (await load()) as Partial<DiscoverFilters>;
    return typeof m.queryToState === "function" &&
      typeof m.toRows === "function" &&
      typeof m.filterRows === "function" &&
      typeof m.sortRows === "function"
      ? (m as DiscoverFilters)
      : null;
  } catch {
    return null;
  }
}

/** Does this query need vote counts to be applied? */
export function queryNeedsCounts(query: string): boolean {
  return needsCounts(parseFilter(query));
}

export async function listFor(
  cands: Candidate[],
  query: string,
  counts: Map<string, Counts>,
  votedFor: (id: string) => Answer | null,
): Promise<Candidate[]> {
  const d = await discoverFilters();
  if (d) {
    try {
      const s = d.queryToState(query);
      const rows = d.toRows(cands, counts, (id) => votedFor(id) != null);
      return d.sortRows(d.filterRows(rows, s).rows, s.sort).map((r) => r.c);
    } catch {
      /* their API moved: fall back to ours */
    }
  }
  return applyFilter(cands, parseFilter(query), counts, votedFor);
}
