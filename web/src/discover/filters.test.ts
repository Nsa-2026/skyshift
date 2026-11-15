import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import fixture from "../../../fixtures/api/candidates.json";
import type { Candidate, CandidatesResponse } from "../api/types";
import type { Counts } from "../vote/model";
import {
  PRESETS,
  RANGES,
  SET_ASIDE,
  SORTS,
  activeCount,
  activePreset,
  bestRelaxation,
  candidatesHash,
  countBy,
  defaultState,
  domainOf,
  FACET_VALUES,
  filterRows,
  inRange,
  isCautioned,
  isMapView,
  isOneAtATime,
  leading,
  motionArcsecYr,
  presetState,
  queryToState,
  routeQuery,
  sameState,
  setAsideOf,
  setAsideValues,
  setsAside,
  significance,
  sortRows,
  stateToQuery,
  toRows,
  verdictOf,
  withoutTest,
  type FilterState,
} from "./filters";

const data = fixture as unknown as CandidatesResponse;
const all = data.candidates;
/** The list the site publishes (scripts/precompute), for the set-aside rule's invariants. */
const published = JSON.parse(readFileSync(join(__dirname, "../../public/data/candidates.json"), "utf8")) as CandidatesResponse;

const base: Candidate = {
  id: "x", kind: "high_pm", ra: 122, dec: 16, mjd: 61000, clock: "months", det: 2, zone: "pilot",
  score: null, snr: null, flux_ujy: null, rate_arcsec_h: null, pm_mas_yr: null, pm_err_mas_yr: null, pa_deg: null,
  nearest_known_arcsec: null, gaia_match: false, catwise_match: false, galactic_b: 25,
  labels: [], reasons: [], why: "", flags: [], frames_url: null,
};
const c = (o: Partial<Candidate>): Candidate => ({ ...base, ...o });
const votes = (mover = 0, changing = 0, junk = 0, unsure = 0): Counts => ({ mover, changing, junk, unsure });
const st = (o: Partial<FilterState>): FilterState => ({ ...defaultState(), ...o });
/** The same, with every set-aside group shown (show=all): filters on the whole sample. */
const every = (o: Partial<FilterState> = {}): FilterState => st({ show: [...SET_ASIDE], ...o });
/** The pilot sample's 142 moving stars all have a Gaia match: 52 of its 194 are worth checking. */
const WORTH = all.filter((x) => !(x.kind === "high_pm" && x.gaia_match)).length;

describe("rows and consensus", () => {
  it("reads review state and the verdict from the counts (≥ 5 votes, ≥ 70 % on one answer)", () => {
    const rows = toRows(
      [c({ id: "a" }), c({ id: "b" }), c({ id: "c" }), c({ id: "d" })],
      new Map([
        ["b", votes(2, 1)],
        ["c", votes(4, 0, 1)],
        ["d", votes(3, 1, 1)],
      ]),
      (id) => id === "b",
    );
    expect(rows.map((r) => r.review)).toEqual(["none", "some", "consensus", "some"]);
    expect(rows.map((r) => r.verdict)).toEqual([null, null, "mover", null]);
    expect(rows.map((r) => r.total)).toEqual([0, 3, 5, 5]);
    expect(rows[1].mine).toBe(true);
    expect(verdictOf(votes(0, 0, 7, 3))).toBe("junk");
    expect(verdictOf(votes(0, 0, 6, 3))).toBeNull();
    expect(leading(votes(1, 3, 3))).toEqual({ answer: "changing", n: 3 });
    expect(leading(votes())).toBeNull();
  });
});

describe("measured values", () => {
  it("puts motions on one scale and measures significance", () => {
    expect(significance(c({ pm_mas_yr: 500, pm_err_mas_yr: 50 }))).toBe(10);
    expect(significance(c({ pm_mas_yr: 500, pm_err_mas_yr: 0 }))).toBeNull();
    expect(motionArcsecYr(c({ pm_mas_yr: 500 }))).toBe(0.5);
    expect(motionArcsecYr(c({ rate_arcsec_h: 1 }))).toBeCloseTo(8766, 6);
    expect(motionArcsecYr(c({}))).toBeNull();
  });

  it("rounds slider domains out to 1–2–5 steps, fixed where the scale is fixed", () => {
    expect(domainOf(RANGES.flux, all)).toEqual([200, 5000]);
    expect(domainOf(RANGES.score, all)).toEqual([0, 1]);
    expect(domainOf(RANGES.b, all)).toEqual([0, 90]);
    expect(domainOf(RANGES.rate, all)).toBeNull(); // no fast mover has a rate yet
  });

  it("treats a missing value as excluded, except a missing nearest known object, which is far", () => {
    expect(inRange(null, { min: 1, max: null }, "excluded")).toBe(false);
    expect(inRange(null, { min: 60, max: null }, "far")).toBe(true);
    expect(inRange(null, { min: null, max: 60 }, "far")).toBe(false);
    expect(inRange(null, undefined, "excluded")).toBe(true);
    expect(inRange(5, { min: 5, max: 5 }, "excluded")).toBe(true);
  });
});

describe("filtering the pilot sample", () => {
  const rows = toRows(all, new Map());

  it("keeps everything worth checking with no filters, and everything with show=all", () => {
    expect(WORTH).toBe(52);
    expect(filterRows(rows, defaultState()).rows).toHaveLength(WORTH);
    expect(filterRows(rows, every()).rows).toHaveLength(194);
  });

  it("ORs within a facet and ANDs between facets", () => {
    expect(filterRows(rows, st({ kind: ["mover"] })).rows).toHaveLength(27);
    expect(filterRows(rows, every({ kind: ["mover", "high_pm"] })).rows).toHaveLength(194);
    const f = filterRows(rows, st({ kind: ["high_pm"], gaia: "no" }));
    expect(f.rows.every((r) => r.c.kind === "high_pm" && !r.c.gaia_match)).toBe(true);
    expect(f.rows.length).toBe(all.filter((x) => x.kind === "high_pm" && !x.gaia_match).length);
  });

  it("counts facet options without their own facet, so other options stay visible", () => {
    const f = filterRows(rows, st({ kind: ["mover"], det: ["3"] }));
    const kinds = countBy(f.without("kind"), FACET_VALUES.kind);
    expect(kinds.get("mover")).toBe(f.rows.length);
    expect(kinds.get("high_pm") ?? 0).toBe(all.filter((x) => x.kind === "high_pm" && x.det === 3).length);
    const dets = countBy(f.without("det"), FACET_VALUES.det);
    expect(dets.get("2") ?? 0).toBe(all.filter((x) => x.kind === "mover" && x.det === 2).length);
  });

  it("hides the pipeline's doubts, artefact cautions and my own votes", () => {
    const doubtless = filterRows(rows, every({ hideDoubts: true })).rows;
    expect(doubtless).toHaveLength(35); // 27 movers (support reasons only) + 8 vetted moving stars
    const flagged = toRows([c({ id: "f", flags: ["ghost"] }), c({ id: "g" })], new Map(), (id) => id === "g");
    expect(filterRows(flagged, st({ cautions: "no" })).rows.map((r) => r.c.id)).toEqual(["g"]);
    expect(filterRows(flagged, st({ cautions: "yes" })).rows.map((r) => r.c.id)).toEqual(["f"]);
    expect(filterRows(flagged, st({ hideMine: true })).rows.map((r) => r.c.id)).toEqual(["f"]);
  });

  it("counts outlier pixels and a repeat position as artefact cautions, as a flag or a reason", () => {
    expect(isCautioned(c({ flags: ["OUTLIER"] }))).toBe(true);
    expect(isCautioned(c({ reasons: ["outlier"] }))).toBe(true);
    expect(isCautioned(c({ reasons: ["repeat_position"] }))).toBe(true);
    expect(isCautioned(c({ reasons: ["near_fixed_position"] }))).toBe(true); // the exporter's name may differ
    expect(isCautioned(c({ reasons: ["same_spot_days_apart"] }))).toBe(true);
    expect(isCautioned(c({ reasons: ["two_exposures", "gaia_pm_disagrees"] }))).toBe(false);
    const rows = toRows([c({ id: "o", reasons: ["outlier"] }), c({ id: "r", reasons: ["repeat_position"] }), c({ id: "k" })], new Map());
    expect(filterRows(rows, st({ cautions: "no" })).rows.map((r) => r.c.id)).toEqual(["k"]);
    expect(filterRows(rows, st({ hideDoubts: true })).rows).toHaveLength(3); // a caution is not a doubt
  });

  it("treats a reason code it doesn't know as a doubt, not as support", () => {
    const rows = toRows([c({ id: "u", reasons: ["new_check_failed"] }), c({ id: "k" })], new Map());
    expect(filterRows(rows, st({ hideDoubts: true })).rows.map((r) => r.c.id)).toEqual(["k"]);
  });

  it("narrows by ranges, |b| and labels", () => {
    const bright = filterRows(rows, st({ ranges: { flux: { min: 2000, max: null } } })).rows;
    expect(bright.length).toBeGreaterThan(0);
    expect(bright.every((r) => (r.c.flux_ujy ?? 0) >= 2000)).toBe(true);
    expect(filterRows(rows, every({ ranges: { b: { min: 10, max: null } } })).rows).toHaveLength(194); // pilot is at b ≈ +25°
    expect(filterRows(rows, every({ ranges: { b: { min: 30, max: null } } })).rows).toHaveLength(0);
    expect(filterRows(rows, every({ label: ["red"] })).rows).toHaveLength(16);
  });

  it("filters by verdict only where there is one", () => {
    const voted = toRows([c({ id: "a" }), c({ id: "b" })], new Map([["a", votes(5)], ["b", votes(1)]]));
    expect(filterRows(voted, st({ verdict: ["mover"] })).rows.map((r) => r.c.id)).toEqual(["a"]);
    expect(filterRows(voted, st({ review: ["some"] })).rows.map((r) => r.c.id)).toEqual(["b"]);
  });

  it("suggests the one filter whose removal brings back the most", () => {
    const s = every({ kind: ["mover"], gaia: "yes" });
    const f = filterRows(rows, s);
    expect(f.rows).toHaveLength(0);
    const hint = bestRelaxation(f);
    expect(hint).toEqual({ key: "kind", n: 142 }); // the 142 Gaia matches are all moving stars
    expect(stateToQuery(withoutTest(s, hint!.key))).toBe("gaia=yes&show=all");
    expect(filterRows(rows, withoutTest(s, "gaia")).rows).toHaveLength(27);
    expect(withoutTest(st({ ranges: { score: { min: 0.1, max: null } } }), "score").ranges).toEqual({});
    // Without show=all, those 142 are set aside: the hint is the Gaia filter, and the set-aside rule is a test of its own.
    const d = filterRows(rows, st({ kind: ["mover"], gaia: "yes" }));
    expect(bestRelaxation(d)).toEqual({ key: "gaia", n: 27 });
    const known = filterRows(rows, st({ kind: ["high_pm"], gaia: "yes" }));
    expect(known.rows).toHaveLength(0);
    expect(bestRelaxation(known)).toEqual({ key: "scope", n: 142 });
    expect(stateToQuery(withoutTest(st({ kind: ["high_pm"], gaia: "yes" }), "scope"))).toBe("kind=high_pm&gaia=yes&show=all");
  });
});

describe("what's worth checking (the set-aside rule)", () => {
  const mover = (o: Partial<Candidate>) => c({ kind: "mover", ...o });
  const ns = ["two_exposures", "motion_not_significant"];

  it("sets aside moving stars Gaia knows, grade C movers, and movers below A whose motion isn't significant: from fields only", () => {
    expect(setAsideOf(c({ kind: "high_pm", gaia_match: true }))).toBe("gaia_known");
    expect(setAsideOf(c({ kind: "high_pm", gaia_match: false }))).toBeNull();
    expect(setAsideOf(c({ kind: "high_pm", gaia_match: false, reasons: ["pm_pair", "implausible_pm"] }))).toBeNull();
    expect(setAsideOf(mover({ grade: "A" }))).toBeNull();
    expect(setAsideOf(mover({ grade: "A", reasons: ns }))).toBeNull(); // grade A is always worth checking
    expect(setAsideOf(mover({ grade: "B" }))).toBeNull();
    expect(setAsideOf(mover({ grade: "B", reasons: ns }))).toBe("not_significant");
    expect(setAsideOf(mover({ grade: "C", reasons: ["hot_pixel"] }))).toBe("grade_c");
    expect(setAsideOf(mover({ grade: "C", reasons: ns }))).toBe("grade_c"); // one group each: C wins
    expect(setAsideOf(mover({}))).toBeNull(); // not vetted yet: nothing says to set it aside
    expect(setAsideOf(mover({ reasons: ns }))).toBe("not_significant");
    // Odd spectra and "one or two?" stay as they are, whatever their fields say.
    expect(setAsideOf(c({ kind: "spectral", gaia_match: true, reasons: ns }))).toBeNull();
    expect(setAsideOf(c({ kind: "shape", gaia_match: true, grade: "C" }))).toBeNull();
  });

  // A small list shaped like the published one: kinds, grades and reasons, not ids.
  const mix = [
    ...Array.from({ length: 6 }, (_, i) => c({ id: `pm-known-${i}`, kind: "high_pm", gaia_match: true })),
    ...Array.from({ length: 2 }, (_, i) => c({ id: `pm-new-${i}`, kind: "high_pm", gaia_match: false })),
    ...Array.from({ length: 3 }, (_, i) => mover({ id: `a-${i}`, grade: "A" })),
    ...Array.from({ length: 4 }, (_, i) => mover({ id: `b-${i}`, grade: "B" })),
    ...Array.from({ length: 5 }, (_, i) => mover({ id: `b-ns-${i}`, grade: "B", reasons: ns })),
    ...Array.from({ length: 7 }, (_, i) => mover({ id: `c-${i}`, grade: "C", reasons: i % 2 ? ns : ["bad_column"] })),
    ...Array.from({ length: 2 }, (_, i) => c({ id: `sp-${i}`, kind: "spectral" })),
    c({ id: "sh", kind: "shape" }),
  ];
  const rows = toRows(mix, new Map());
  const byKind = (rs: ReturnType<typeof toRows>) => Object.fromEntries(countBy(rs, FACET_VALUES.kind));

  it("lists by default only what's worth checking, and counts what it sets aside", () => {
    const f = filterRows(rows, defaultState());
    expect(byKind(f.rows)).toEqual({ high_pm: 2, mover: 7, spectral: 2, shape: 1 }); // 3 A + 4 B
    expect(f.activeKeys).toEqual(["scope"]);
    expect(f.without("scope")).toHaveLength(mix.length);
    expect(Object.fromEntries(countBy(f.without("scope"), setAsideValues))).toEqual({ gaia_known: 6, not_significant: 5, grade_c: 7 });
    expect(f.rows.length + 6 + 5 + 7).toBe(mix.length);
    expect(activeCount(defaultState())).toBe(0); // a baseline, not a filter
  });

  it("adds back the groups asked for, and everything with show=all", () => {
    expect(filterRows(rows, st({ show: ["gaia_known"] })).rows).toHaveLength(12 + 6);
    expect(filterRows(rows, st({ show: ["not_significant", "grade_c"] })).rows).toHaveLength(12 + 5 + 7);
    const f = filterRows(rows, every());
    expect(f.rows).toHaveLength(mix.length);
    expect(f.activeKeys).toEqual([]); // nothing set aside: no test at all
    expect(setsAside(every())).toBe(false);
    expect(activeCount(every())).toBe(0);
  });

  it("keeps facet counts to what's listed, so a grade C link finds nothing until the group is shown", () => {
    const f = filterRows(rows, st({ grade: ["C"] }));
    expect(f.rows).toHaveLength(0);
    expect(countBy(f.without("grade"), FACET_VALUES.grade).get("C") ?? 0).toBe(0);
    // The set-aside ones that match (the page offers these first: they're what the link asked for).
    expect(f.without("scope")).toHaveLength(7);
    expect(bestRelaxation(f)).toEqual({ key: "grade", n: 12 });
    expect(filterRows(rows, withoutTest(st({ grade: ["C"] }), "scope")).rows).toHaveLength(7);
    expect(filterRows(rows, st({ grade: ["C"], show: ["grade_c"] })).rows).toHaveLength(7);
  });

  it("sets nothing aside when the filter asks for a community verdict: what people agreed on outranks our checks", () => {
    const voted = toRows(mix, new Map([["pm-known-0", votes(6)], ["c-0", votes(5)], ["a-0", votes(5)]]));
    const real = presetState(PRESETS.find((p) => p.id === "community-real")!);
    expect(setsAside(real)).toBe(false);
    expect(filterRows(voted, real).rows.map((r) => r.c.id).sort()).toEqual(["a-0", "c-0", "pm-known-0"]);
    // The Community page's own link (verdict only) too.
    expect(filterRows(voted, queryToState("verdict=mover,changing")).rows).toHaveLength(3);
    // "Community verdict" alone (review=consensus) is a vote filter like the others: the rule applies.
    expect(filterRows(voted, st({ review: ["consensus"] })).rows.map((r) => r.c.id)).toEqual(["a-0"]);
  });

  it("starts every other preset from what's worth checking", () => {
    for (const p of PRESETS.filter((x) => x.id !== "community-real")) {
      const s = presetState(p);
      expect(setsAside(s), p.id).toBe(true);
      expect(filterRows(rows, s).rows.every((r) => setAsideOf(r.c) === null), p.id).toBe(true);
    }
  });

  it("holds on the published list: the groups split it, and what's left is what's worth checking", () => {
    const cands = published.candidates;
    const f = filterRows(toRows(cands, new Map()), defaultState());
    const n = countBy(f.without("scope"), setAsideValues);
    const aside = SET_ASIDE.reduce((a, g) => a + (n.get(g) ?? 0), 0);
    expect(f.rows.length + aside).toBe(cands.length);
    for (const r of f.rows) {
      const x = r.c;
      if (x.kind === "high_pm") expect(x.gaia_match).toBe(false);
      if (x.kind === "mover") expect(x.grade === "A" || (x.grade !== "C" && !x.reasons.includes("motion_not_significant"))).toBe(true);
    }
    // Odd spectra and "one or two?" are all listed, as before.
    for (const k of ["spectral", "shape"] as const) expect(f.rows.filter((r) => r.c.kind === k).length).toBe(cands.filter((x) => x.kind === k).length);
    expect(n.get("gaia_known") ?? 0).toBe(cands.filter((x) => x.kind === "high_pm" && x.gaia_match).length);
    expect(n.get("grade_c") ?? 0).toBe(cands.filter((x) => x.kind === "mover" && x.grade === "C").length);
  });
});

describe("presets", () => {
  const rows = toRows(all, new Map());
  const n = (id: string) => filterRows(rows, presetState(PRESETS.find((p) => p.id === id)!)).rows.length;

  it("match DESIGN.md's five choices on the pilot sample", () => {
    expect(PRESETS.map((p) => p.label)).toEqual([
      "Most promising",
      "Needs your vote",
      "Possible brown dwarfs",
      "Unknown fast movers",
      "Community says real",
      "Check first",
    ]);
    // Each starts from what's worth checking: the pilot's 142 moving stars Gaia knows are set aside.
    expect(n("promising")).toBe(27); // 35 with show=all: the 8 vetted moving stars all have a Gaia match
    expect(n("needs-vote")).toBe(WORTH);
    expect(n("brown-dwarfs")).toBe(7);
    expect(n("fast-movers")).toBe(27);
    expect(n("community-real")).toBe(0);
    expect(n("check-first")).toBe(0); // the pilot sample has no artefact cautions
  });

  it("'Check first' lists the cautioned ones nobody has settled; 'Most promising' hides them", () => {
    const rows = toRows(
      [c({ id: "o", flags: ["OUTLIER"] }), c({ id: "r", reasons: ["repeat_position"] }), c({ id: "k", score: 0.5 })],
      new Map([["r", votes(5)]]),
    );
    const pick = (id: string) => sortRows(filterRows(rows, presetState(PRESETS.find((p) => p.id === id)!)).rows, presetState(PRESETS.find((p) => p.id === id)!).sort).map((r) => r.c.id);
    expect(pick("check-first")).toEqual(["o"]); // r already has a verdict
    expect(pick("promising")).toEqual(["k"]);
  });

  it("never share a name with a sort: a preset narrows the list, a sort only reorders it", () => {
    const sorts = SORTS.map((s) => s.label);
    for (const p of PRESETS) expect(sorts).not.toContain(p.label);
    expect(SORTS.find((s) => s.key === "promising")?.label).toBe("Score");
    expect(PRESETS.find((p) => p.id === "promising")?.label).toBe("Most promising");
  });

  it("is recognised again from the URL, and only when nothing else changed", () => {
    const p = PRESETS[2];
    const s = queryToState(stateToQuery(presetState(p)));
    expect(activePreset(s)?.id).toBe("brown-dwarfs");
    expect(activePreset({ ...s, det: ["2"] })).toBeNull();
    expect(activePreset(defaultState())).toBeNull();
  });
});

describe("sorting", () => {
  const rows = toRows(
    [
      c({ id: "a", score: 0.1, flux_ujy: 900, mjd: 61001, pm_mas_yr: 300 }),
      c({ id: "b", score: 0.3, flux_ujy: null, mjd: 61003 }),
      c({ id: "m", kind: "mover", score: null, snr: 40, flux_ujy: 2000, mjd: 61002, rate_arcsec_h: 2 }),
      c({ id: "n", kind: "mover", score: null, snr: 9, flux_ujy: 400, mjd: 61000 }),
    ],
    new Map([["a", votes(1)], ["m", votes(2, 1)]]),
  );
  const ids = (k: Parameters<typeof sortRows>[1]) => sortRows(rows, k).map((r) => r.c.id);

  it("puts scored candidates first, then by S/N", () => expect(ids("promising")).toEqual(["b", "a", "m", "n"]));
  it("puts artefact cautions, then the pipeline's doubts, below everything else in 'Pipeline score'", () => {
    const r = toRows(
      [
        c({ id: "best-but-outlier", score: 0.9, reasons: ["outlier"] }),
        c({ id: "flagged", score: 0.8, flags: ["GHOST"] }),
        c({ id: "doubted", score: 0.7, reasons: ["bad_fit"] }),
        c({ id: "clean", score: 0.1 }),
      ],
      new Map(),
    );
    expect(sortRows(r, "promising").map((x) => x.c.id)).toEqual(["clean", "doubted", "best-but-outlier", "flagged"]);
  });
  it("sorts by votes both ways", () => {
    expect(ids("fewest")).toEqual(["b", "n", "a", "m"]);
    expect(ids("most")).toEqual(["m", "a", "b", "n"]);
  });
  it("sorts by brightness with unknowns last", () => expect(ids("brightest")).toEqual(["m", "a", "n", "b"]));
  it("sorts movers and moving stars on one motion scale", () => expect(ids("fastest")).toEqual(["m", "a", "b", "n"]));
  it("sorts newest first", () => expect(ids("newest")).toEqual(["b", "m", "a", "n"]));
});

describe("the URL", () => {
  it("round-trips every kind of filter", () => {
    const s = st({
      kind: ["high_pm", "mover"],
      review: ["none"],
      verdict: ["mover"],
      label: ["red", "ch4"],
      reason: ["two_exposures"],
      det: ["2", "5"],
      zone: ["pilot"],
      gaia: "no",
      catwise: "yes",
      hideDoubts: true,
      cautions: "no",
      hideMine: true,
      ranges: { score: { min: 0.15, max: 1 }, flux: { min: 500, max: null }, known: { min: 60, max: null }, b: { min: null, max: 45 } },
      sort: "fewest",
    });
    const q = stateToQuery(s);
    expect(q).toBe(
      "kind=high_pm,mover&review=none&verdict=mover&label=red,ch4&reason=two_exposures&det=2,5&zone=pilot&gaia=no&catwise=yes" +
        "&doubts=hide&cautions=hide&mine=hide&score=0.15-1&flux=500-&known=60-&b=-45&sort=fewest",
    );
    expect(sameState(queryToState(q), s)).toBe(true);
    expect(queryToState(q)).toEqual(s);
  });

  it("writes the set-aside groups shown as show=…, show=all for all three, and nothing for none", () => {
    expect(stateToQuery(st({ show: ["grade_c", "gaia_known"] }))).toBe("show=gaia_known,grade_c"); // in SET_ASIDE order
    expect(stateToQuery(every())).toBe("show=all");
    expect(stateToQuery(every({ kind: ["high_pm"], gaia: "yes", sort: "fewest" }))).toBe("kind=high_pm&gaia=yes&show=all&sort=fewest");
    expect(queryToState("show=all").show).toEqual([...SET_ASIDE]);
    expect(queryToState("show=grade_c,nope,gaia_known").show).toEqual(["gaia_known", "grade_c"]);
    expect(queryToState("show=nope").show).toEqual([]);
    expect(queryToState("kind=mover").show).toEqual([]);
    expect(candidatesHash(queryToState("show=all"), { map: true })).toBe("#/candidates/show=all&map=1");
    // Not a filter: no count, and a preset with show=all is a different list (not the preset).
    expect(activeCount(queryToState("show=all"))).toBe(0);
    expect(activePreset(queryToState("kind=mover&known=60-&show=all"))).toBeNull();
    expect(sameState(queryToState("show=gaia_known,not_significant,grade_c"), every())).toBe(true);
  });

  it("writes 'only artefact cautions' as cautions=only", () => {
    const q = stateToQuery(st({ cautions: "yes" }));
    expect(q).toBe("cautions=only");
    expect(queryToState(q).cautions).toBe("yes");
    expect(queryToState("cautions=maybe").cautions).toBe("any");
  });

  it("writes nothing for the default state and reads the brief's example", () => {
    expect(stateToQuery(defaultState())).toBe("");
    expect(candidatesHash(defaultState())).toBe("#/candidates");
    const s = queryToState("kind=high_pm&score=0.15-1&sort=fewest");
    expect(s.kind).toEqual(["high_pm"]);
    expect(s.ranges.score).toEqual({ min: 0.15, max: 1 });
    expect(s.sort).toBe("fewest");
    expect(candidatesHash(s)).toBe("#/candidates/kind=high_pm&score=0.15-1&sort=fewest");
  });

  it("takes the router's param or a whole hash", () => {
    expect(routeQuery("kind=mover")).toBe("kind=mover");
    expect(routeQuery("?kind=mover")).toBe("kind=mover");
    expect(routeQuery("#/candidates/kind=mover&sort=most")).toBe("kind=mover&sort=most");
    expect(routeQuery("#/candidates?kind=mover")).toBe("kind=mover");
    expect(routeQuery("#/candidates")).toBe("");
    expect(routeQuery("#/review")).toBe("");
    expect(routeQuery(null)).toBe("");
  });

  it("keeps 'One at a time' in the URL as one=1, after the filter, and never as a filter", () => {
    expect(candidatesHash(defaultState(), { one: true })).toBe("#/candidates/one=1");
    expect(candidatesHash(defaultState(), { one: false })).toBe("#/candidates");
    const movers = queryToState("kind=mover&sort=fewest");
    expect(candidatesHash(movers, { one: true })).toBe("#/candidates/kind=mover&sort=fewest&one=1");
    // The parser reads it from the router's param or a whole hash; anything but "1" is off.
    expect(isOneAtATime("one=1")).toBe(true);
    expect(isOneAtATime("kind=mover&one=1")).toBe(true);
    expect(isOneAtATime("?kind=mover&one=1")).toBe(true);
    expect(isOneAtATime("#/candidates/kind=mover&one=1")).toBe(true);
    expect(isOneAtATime("#/candidates/one=1")).toBe(true);
    expect(isOneAtATime("#/candidates/kind=mover")).toBe(false);
    expect(isOneAtATime("one=0")).toBe(false);
    expect(isOneAtATime("#/review")).toBe(false);
    expect(isOneAtATime(null)).toBe(false);
    // Not a filter: same state, same preset, same count of filters on.
    const q = "kind=mover&known=60-&one=1";
    expect(sameState(queryToState(q), queryToState("kind=mover&known=60-"))).toBe(true);
    expect(activePreset(queryToState(q))?.id).toBe("fast-movers");
    expect(activeCount(queryToState("one=1"))).toBe(0);
    // Round trip through the page's reader and writer.
    expect(candidatesHash(queryToState(q), { one: isOneAtATime(q) })).toBe("#/candidates/kind=mover&known=60-&one=1");
  });

  it("keeps the map view in the URL as map=1, after the filter, and never as a filter", () => {
    expect(candidatesHash(defaultState(), { map: true })).toBe("#/candidates/map=1");
    expect(candidatesHash(defaultState(), { map: false })).toBe("#/candidates");
    const movers = queryToState("kind=mover&sort=fewest");
    expect(candidatesHash(movers, { map: true })).toBe("#/candidates/kind=mover&sort=fewest&map=1");
    // One view at a time: the map wins over "One at a time".
    expect(candidatesHash(movers, { map: true, one: true })).toBe("#/candidates/kind=mover&sort=fewest&map=1");
    expect(isMapView("map=1")).toBe(true);
    expect(isMapView("?kind=mover&map=1")).toBe(true);
    expect(isMapView("#/candidates/kind=mover&map=1")).toBe(true);
    expect(isMapView("#/candidates/map=1")).toBe(true);
    expect(isMapView("#/candidates/kind=mover")).toBe(false);
    expect(isMapView("map=0")).toBe(false);
    expect(isMapView("#/sky/map=1")).toBe(false);
    expect(isMapView(null)).toBe(false);
    // Not a filter: same state, same preset, same count of filters on.
    const q = "kind=mover&known=60-&map=1";
    expect(sameState(queryToState(q), queryToState("kind=mover&known=60-"))).toBe(true);
    expect(activePreset(queryToState(q))?.id).toBe("fast-movers");
    expect(activeCount(queryToState("map=1"))).toBe(0);
    expect(isOneAtATime(q)).toBe(false);
    expect(candidatesHash(queryToState(q), { map: isMapView(q) })).toBe("#/candidates/kind=mover&known=60-&map=1");
  });

  it("drops what it can't read instead of failing", () => {
    const s = queryToState("kind=<script>,mover&gaia=maybe&score=a-b&sort=random&b=40-10&unknown=1");
    expect(s.kind).toEqual(["mover"]);
    expect(s.gaia).toBe("any");
    expect(s.ranges.score).toBeUndefined();
    expect(s.ranges.b).toEqual({ min: 10, max: 40 }); // swapped into order
    expect(s.sort).toBe("promising");
    expect(activeCount(s)).toBe(2);
  });
});
