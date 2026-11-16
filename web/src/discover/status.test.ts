// contracts/api.md v3.23 on the To check list's model: certain matches (status known, flaw) are
// listed apart, and in score order the open ones with a strong check come last, kind by kind.
import { describe, expect, it } from "vitest";
import pilot from "../../../fixtures/api/candidates.json";
import sample from "../review/checks.fixture.json";
import type { Candidate, CandidatesResponse } from "../api/types";
import {
  SET_ASIDE,
  activeCount,
  bestRelaxation,
  candidatesHash,
  defaultState,
  filterRows,
  isCautioned,
  queryToState,
  setsAside,
  sortRows,
  splitsLessLikely,
  stateToQuery,
  toRows,
  type FilterState,
  type SortKey,
} from "./filters";

const checked = (sample as unknown as CandidatesResponse).candidates;
const old = (pilot as unknown as CandidatesResponse).candidates;
const st = (o: Partial<FilterState> = {}): FilterState => ({ ...defaultState(), ...o });
const ids = (cands: Candidate[], s: FilterState) => sortRows(filterRows(toRows(cands, new Map()), s).rows, s.sort).map((r) => r.c.id);

describe("which list: to check, known bodies, instrument flaws", () => {
  it("leaves the certain matches out of the list to check and lists each apart, nothing hidden", () => {
    const open = ids(checked, st({ show: [...SET_ASIDE] }));
    const known = ids(checked, st({ status: "known" }));
    const flaw = ids(checked, st({ status: "flaw" }));
    expect(known).toEqual(["ecl04-701100229999-88001190", "ecl04-701100231111-88001201"]);
    expect(flaw).toEqual(["hunt-664250099999-87801700"]);
    expect(open).toHaveLength(checked.length - 3);
    expect(new Set([...open, ...known, ...flaw]).size).toBe(checked.length);
  });

  it("sets nothing aside among the certain matches", () => {
    expect(setsAside(st({ status: "known" }))).toBe(false);
    expect(setsAside(st())).toBe(true);
  });

  it("keeps the list in the URL as status=…, a view and not a filter", () => {
    expect(stateToQuery(st({ status: "known" }))).toBe("status=known");
    expect(stateToQuery(st({ status: "flaw", kind: ["mover"], sort: "newest" }))).toBe("kind=mover&status=flaw&sort=newest");
    expect(queryToState("status=flaw").status).toBe("flaw");
    expect(queryToState("status=nonsense").status).toBe("open");
    expect(queryToState("").status).toBe("open");
    expect(candidatesHash(st({ status: "known" }), { one: true })).toBe("#/candidates/status=known&one=1");
    expect(activeCount(st({ status: "known" }))).toBe(0);
  });

  it("never suggests switching lists as a way to loosen a filter", () => {
    const f = filterRows(toRows(checked, new Map()), st({ status: "known", kind: ["spectral"] }));
    expect(f.rows).toHaveLength(0);
    expect(bestRelaxation(f)?.key).toBe("kind");
  });

  it("adds no test for older files, which have no status: they filter as they always did", () => {
    expect(filterRows(toRows(old, new Map()), st({ show: [...SET_ASIDE] })).activeKeys).toEqual([]);
    expect(filterRows(toRows(checked, new Map()), st({ show: [...SET_ASIDE] })).activeKeys).toEqual(["status"]);
  });
});

describe("score order with checks: less likely last, kind by kind", () => {
  it("keeps the file's score order and puts open ones with a strong check after the rest, movers first", () => {
    expect(ids(checked, st({ show: [...SET_ASIDE] }))).toEqual([
      // grade A, then the rest by score as the file gives it (a strong check already lowered it)
      "hunt-664250069368-87801613",
      "hunt-pm-65039007407911",
      "hunt-664250077777-87801640",
      "hunt-pm-65039007600033",
      "hunt-sp-65039007700044",
      "hunt-664250055555-87801599",
      // less likely: movers, then moving stars, then "one or two?", each by score
      "ecl04-701100223344-88001122",
      "hunt-664250081234-87801655",
      "hunt-pm-65039007500022",
      "hunt-sh-65039007800055",
    ]);
  });

  it("ranks a file with checks by its score, not by the site's own guesses from flags and codes", () => {
    // hunt-…640 carries an OUTLIER flag (a warn check): with checks it keeps its place by score.
    const order = ids(checked, st());
    expect(order.indexOf("hunt-664250077777-87801640")).toBeLessThan(order.indexOf("hunt-pm-65039007600033"));
  });

  it("draws the divider only in score order on the list to check", () => {
    expect(splitsLessLikely(st())).toBe(true);
    for (const sort of ["grade", "fewest", "most", "brightest", "fastest", "newest"] as SortKey[]) expect(splitsLessLikely(st({ sort }))).toBe(false);
    expect(splitsLessLikely(st({ status: "known" }))).toBe(false);
  });

  it("orders an older file as before (cautions, then doubts, after the rest), and a file with checks by score", () => {
    const row = (id: string, score: number, o: Partial<Candidate> = {}): Candidate => ({ ...old[0], id, kind: "mover", score, reasons: [], flags: [], ...o });
    const before = [row("glitch", 0.9, { flags: ["outlier"] }), row("doubt", 0.8, { reasons: ["few_dots"] }), row("clean", 0.1)];
    expect(isCautioned(before[0])).toBe(true);
    expect(ids(before, st())).toEqual(["clean", "doubt", "glitch"]);
    const after = before.map((c) => ({ ...c, status: "open" as const, checks: [] }));
    expect(ids(after, st())).toEqual(["glitch", "doubt", "clean"]);
  });
});
