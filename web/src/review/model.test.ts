import { describe, expect, it } from "vitest";
import type { Candidate } from "../api/types";
import { cand } from "./testkit";
import { emptyCounts, type Answer, type Counts } from "../vote/model";
import {
  applyFilter,
  consensus,
  discoverQuery,
  forAstronomers,
  gradeCCount,
  inScope,
  isDiscoverUrl,
  keyAction,
  needsCounts,
  neighbours,
  parseCandidateParam,
  parseFilter,
  queueOrder,
  scoreOrder,
  votesToGo,
  withVote,
} from "./model";

const counts = (o: Partial<Counts>): Counts => ({ ...emptyCounts(), ...o });
const ids = (cs: Candidate[]) => cs.map((c) => c.id);

describe("consensus", () => {
  it("has no state without votes", () => {
    expect(consensus(emptyCounts())).toMatchObject({ state: "none", total: 0, top: null, answer: null });
  });

  it("is forming under 5 votes, even when they all agree", () => {
    const c = consensus(counts({ mover: 4 }));
    expect(c).toMatchObject({ state: "forming", total: 4, top: "mover", answer: null });
    expect(votesToGo(counts({ mover: 4 }))).toBe(1);
  });

  it("is reached at 5 votes with 70 % agreeing", () => {
    expect(consensus(counts({ mover: 4, junk: 1 }))).toMatchObject({ state: "reached", answer: "mover", share: 0.8 });
    expect(votesToGo(counts({ mover: 4, junk: 1 }))).toBe(0);
  });

  it("counts exactly 70 % as agreement (7 of 10)", () => {
    expect(consensus(counts({ changing: 7, junk: 2, unsure: 1 }))).toMatchObject({ state: "reached", answer: "changing", topCount: 7 });
  });

  it("is split just under 70 % (6 of 9)", () => {
    expect(consensus(counts({ junk: 6, mover: 3 }))).toMatchObject({ state: "split", top: "junk", answer: null });
  });

  it("breaks a tie for the top answer by answer order, and never reaches consensus on a tie", () => {
    const c = consensus(counts({ junk: 3, mover: 3 }));
    expect(c.state).toBe("split");
    expect(c.top).toBe("mover");
    expect(c.share).toBe(0.5);
  });

  it("sends only consensus movers and changing sources to astronomers", () => {
    expect(forAstronomers(consensus(counts({ mover: 5 })))).toBe(true);
    expect(forAstronomers(consensus(counts({ changing: 5 })))).toBe(true);
    expect(forAstronomers(consensus(counts({ junk: 5 })))).toBe(false);
    expect(forAstronomers(consensus(counts({ mover: 3, junk: 2 })))).toBe(false);
  });

  it("adds a vote to a copy", () => {
    const before = counts({ mover: 1 });
    expect(withVote(before, "mover").mover).toBe(2);
    expect(before.mover).toBe(1);
    expect(withVote(undefined, "unsure")).toEqual(counts({ unsure: 1 }));
  });
});

describe("queueOrder", () => {
  const a = cand("a", { score: 0.9 });
  const b = cand("b", { score: 0.5 });
  const c = cand("c", { score: null, snr: 80 });
  const d = cand("d", { score: 0.7 });
  const e = cand("e", { score: 0.99 });

  it("orders by fewest votes, then score high to low, no score last", () => {
    expect(ids(queueOrder([c, b, a, d], new Map()))).toEqual(["a", "d", "b", "c"]);
    const k = new Map([["a", counts({ mover: 2 })], ["d", counts({ junk: 1 })]]);
    expect(ids(queueOrder([c, b, a, d], k))).toEqual(["b", "c", "d", "a"]);
  });

  it("puts candidates at consensus last and leaves out what this browser voted on", () => {
    const k = new Map([["e", counts({ junk: 5 })]]);
    const mine = (id: string): Answer | null => (id === "b" ? "mover" : null);
    expect(ids(queueOrder([a, b, c, d, e], k, mine))).toEqual(["a", "d", "c", "e"]);
  });

  it("skips grade C, leads with grade A, and queues B and ungraded as before", () => {
    const gA = cand("gA", { kind: "mover", grade: "A", score: null, snr: 6 });
    const gB = cand("gB", { kind: "mover", grade: "B", score: null, snr: 30 });
    const gC = cand("gC", { kind: "mover", grade: "C", score: null, snr: 99, reasons: ["hot_pixel"] });
    const old = cand("old", { kind: "mover", score: null, snr: 20 });
    expect(ids(queueOrder([gC, old, gB, gA, a], new Map()))).toEqual(["gA", "a", "gB", "old"]);
    expect(gradeCCount([gC, old, gB, gA])).toBe(1);
    // Grade A leads until it reaches consensus, even with a vote already.
    const k = new Map([["gA", counts({ mover: 1 })]]);
    expect(ids(queueOrder([gB, gA], k))).toEqual(["gA", "gB"]);
    expect(ids(queueOrder([gA, gB], new Map([["gA", counts({ mover: 5 })]])))).toEqual(["gB", "gA"]);
  });

  it("runs inside To check's filtered list (scope), in its own order there", () => {
    const all = [a, b, c, d, e];
    expect(ids(inScope(all, null))).toEqual(["a", "b", "c", "d", "e"]);
    expect(ids(inScope(all, new Set(["e", "b", "nowhere"])))).toEqual(["b", "e"]);
    expect(ids(inScope(all, new Set()))).toEqual([]);
    // Fewest votes first inside the scope, whatever order the list was sorted in.
    const k = new Map([["e", counts({ mover: 1 })]]);
    expect(ids(queueOrder(inScope(all, new Set(["e", "b", "c"])), k))).toEqual(["b", "c", "e"]);
  });

  it("score order breaks ties by S/N, then id", () => {
    const x = cand("x", { score: 0.5, snr: 10 });
    const y = cand("y", { score: 0.5, snr: 20 });
    const z = cand("z", { score: 0.5, snr: 20 });
    expect(ids(scoreOrder([z, x, y]))).toEqual(["y", "z", "x"]);
  });
});

describe("the Candidates filter from its URL", () => {
  it("reads lists, ranges, yes/no, hides and sort, ignoring junk", () => {
    const f = parseFilter("kind=mover,high_pm&known=60-&pm=-500&gaia=no&doubts=hide&sort=fewest&bogus=1&det=2,<x>");
    expect(f.lists.kind).toEqual(["mover", "high_pm"]);
    expect(f.lists.det).toEqual(["2"]);
    expect(f.ranges.known).toEqual({ min: 60, max: null });
    expect(f.ranges.pm).toEqual({ min: null, max: 500 });
    expect(f.gaia).toBe("no");
    expect(f.catwise).toBe("any");
    expect(f.hideDoubts).toBe(true);
    expect(f.sort).toBe("fewest");
    expect(needsCounts(f)).toBe(true);
  });

  it("defaults to every candidate in promising order", () => {
    const f = parseFilter("");
    expect(f.sort).toBe("promising");
    expect(f.ranges).toEqual({});
    expect(needsCounts(f)).toBe(false);
    expect(parseFilter("sort=sideways&score=abc").sort).toBe("promising");
  });

  it("swaps a reversed range and accepts a leading ? or /", () => {
    expect(parseFilter("?snr=50-10").ranges.snr).toEqual({ min: 10, max: 50 });
    expect(parseFilter("/kind=mover").lists.kind).toEqual(["mover"]);
  });

  it("filters and sorts like the Candidates page", () => {
    const m = cand("m", { kind: "mover", clock: "hours", score: 0.4, nearest_known_arcsec: null });
    const p = cand("p", { score: 0.8, pm_mas_yr: 400, nearest_known_arcsec: 20 });
    const q = cand("q", { score: 0.6, pm_mas_yr: 900, reasons: ["few_dots"], nearest_known_arcsec: 100 });
    const all = [m, p, q];
    // null nearest-known counts as far away
    expect(ids(applyFilter(all, parseFilter("known=60-")))).toEqual(["q", "m"]);
    expect(ids(applyFilter(all, parseFilter("kind=high_pm&doubts=hide")))).toEqual(["p"]);
    // no measured motion sorts last
    expect(ids(applyFilter(all, parseFilter("sort=fastest")))).toEqual(["q", "p", "m"]);
    const k = new Map([["p", counts({ mover: 5 })], ["q", counts({ junk: 1 })]]);
    expect(ids(applyFilter(all, parseFilter("review=none"), k))).toEqual(["m"]);
    expect(ids(applyFilter(all, parseFilter("verdict=mover"), k))).toEqual(["p"]);
    expect(ids(applyFilter(all, parseFilter("sort=fewest"), k))).toEqual(["m", "q", "p"]);
    const mine = (id: string): Answer | null => (id === "p" ? "mover" : null);
    expect(ids(applyFilter(all, parseFilter("mine=hide"), k, mine))).toEqual(["q", "m"]);
  });

  it("reads the grade filter and both grade orders like the Candidates page", () => {
    const b = cand("b", { kind: "mover", grade: "B", snr: 8 });
    const c = cand("c", { kind: "mover", grade: "C", snr: 50, score: 0.99 });
    const n = cand("n", { kind: "mover", snr: 40 });
    const p = cand("p", { score: 0.5, snr: 9 });
    const all = [c, n, b, p];
    expect(parseFilter("grade=C,none").lists.grade).toEqual(["C", "none"]);
    expect(ids(applyFilter(all, parseFilter("grade=C,none")))).toEqual(["p", "n", "c"]);
    // Pipeline score: C last, even with the highest score.
    expect(ids(applyFilter(all, parseFilter("")))).toEqual(["p", "n", "b", "c"]);
    // Vetting grade: A, B, C, then no grade; S/N within each.
    expect(ids(applyFilter(all, parseFilter("sort=grade")))).toEqual(["b", "c", "n", "p"]);
  });

  it("finds the neighbours in a list", () => {
    const list = [cand("a"), cand("b"), cand("c")];
    expect(neighbours(list, "b")).toMatchObject({ prev: { id: "a" }, next: { id: "c" }, index: 1 });
    expect(neighbours(list, "a")).toMatchObject({ prev: null, next: { id: "b" }, index: 0 });
    expect(neighbours(list, "zz")).toEqual({ prev: null, next: null, index: -1 });
  });
});

describe("URLs", () => {
  it("splits a candidate route into id and list query", () => {
    expect(parseCandidateParam("pilot-pm-0043")).toEqual({ id: "pilot-pm-0043", query: "" });
    expect(parseCandidateParam("pilot-pm-0043?kind=mover&sort=fewest")).toEqual({ id: "pilot-pm-0043", query: "kind=mover&sort=fewest" });
    expect(parseCandidateParam("abc/")).toEqual({ id: "abc", query: "" });
    expect(parseCandidateParam(null)).toEqual({ id: "", query: "" });
  });

  it("knows the candidate list's routes, not the home globe", () => {
    expect(isDiscoverUrl("#/candidates")).toBe(true);
    expect(isDiscoverUrl("#/candidates/kind=mover")).toBe(true);
    expect(isDiscoverUrl("#/discover?kind=mover")).toBe(true);
    expect(isDiscoverUrl("#/")).toBe(false);
    expect(isDiscoverUrl("")).toBe(false);
    expect(isDiscoverUrl("#/c/abc")).toBe(false);
    expect(isDiscoverUrl("#/candidatesx")).toBe(false);
  });

  it("reads the list query from the hash, else from the page query minus view keys", () => {
    expect(discoverQuery("#/candidates/kind=mover", "")).toBe("kind=mover");
    expect(discoverQuery("#/candidates/?kind=mover", "")).toBe("kind=mover");
    expect(discoverQuery("#/candidates?sort=fewest", "")).toBe("sort=fewest");
    expect(discoverQuery("#/candidates", "?ra=1&dec=2&kind=mover")).toBe("kind=mover");
    expect(discoverQuery("#/candidates", "")).toBe("");
  });

  it("leaves out 'One at a time' (one=1): a view of the list, not part of its filter", () => {
    expect(discoverQuery("#/candidates/kind=mover&one=1", "")).toBe("kind=mover");
    expect(discoverQuery("#/candidates/one=1&sort=fewest", "")).toBe("sort=fewest");
    expect(discoverQuery("#/candidates/one=1", "")).toBe("");
    expect(isDiscoverUrl("#/candidates/one=1")).toBe(true);
  });

  it("leaves out the map view (map=1) too: the same filter on the sky map", () => {
    expect(discoverQuery("#/candidates/kind=mover&map=1", "")).toBe("kind=mover");
    expect(discoverQuery("#/candidates/map=1", "")).toBe("");
    expect(isDiscoverUrl("#/candidates/map=1")).toBe(true);
  });
});

describe("keyAction", () => {
  const button = document.createElement("button");
  const input = document.createElement("input");
  const div = document.createElement("div");

  it("maps 1–4 to the four answers on both pages", () => {
    expect(keyAction({ key: "1" }, { queue: false })).toEqual({ kind: "vote", answer: "mover" });
    expect(keyAction({ key: "2" }, { queue: true })).toEqual({ kind: "vote", answer: "changing" });
    expect(keyAction({ key: "3" }, { queue: true })).toEqual({ kind: "vote", answer: "junk" });
    expect(keyAction({ key: "4" }, { queue: false })).toEqual({ kind: "vote", answer: "unsure" });
    expect(keyAction({ key: "5" }, { queue: true })).toBeNull();
  });

  it("has skip, back, next and confirm only in the queue", () => {
    expect(keyAction({ key: "s" }, { queue: true })).toEqual({ kind: "skip" });
    expect(keyAction({ key: "S" }, { queue: true })).toEqual({ kind: "skip" });
    expect(keyAction({ key: "ArrowLeft" }, { queue: true })).toEqual({ kind: "back" });
    expect(keyAction({ key: "ArrowRight" }, { queue: true })).toEqual({ kind: "next" });
    expect(keyAction({ key: "Enter", target: div }, { queue: true })).toEqual({ kind: "confirm" });
    expect(keyAction({ key: "s" }, { queue: false })).toBeNull();
    expect(keyAction({ key: "ArrowLeft" }, { queue: false })).toBeNull();
  });

  it("leaves Enter on a button to the button", () => {
    expect(keyAction({ key: "Enter", target: button }, { queue: true })).toBeNull();
  });

  it("ignores typing, modifiers and auto-repeat", () => {
    expect(keyAction({ key: "1", target: input }, { queue: true })).toBeNull();
    expect(keyAction({ key: "1", metaKey: true }, { queue: true })).toBeNull();
    expect(keyAction({ key: "s", ctrlKey: true }, { queue: true })).toBeNull();
    expect(keyAction({ key: "2", altKey: true }, { queue: true })).toBeNull();
    expect(keyAction({ key: "3", repeat: true }, { queue: true })).toBeNull();
  });
});
