import { describe, expect, it } from "vitest";
import fixture from "../../../fixtures/api/candidates.json";
import type { Candidate, CandidatesResponse } from "../api/types";
import type { Counts } from "../vote/model";
import {
  PRESETS,
  RANGES,
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
  leading,
  motionArcsecYr,
  presetState,
  queryToState,
  routeQuery,
  sameState,
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

const base: Candidate = {
  id: "x", kind: "high_pm", ra: 122, dec: 16, mjd: 61000, clock: "months", det: 2, zone: "pilot",
  score: null, snr: null, flux_ujy: null, rate_arcsec_h: null, pm_mas_yr: null, pm_err_mas_yr: null, pa_deg: null,
  nearest_known_arcsec: null, gaia_match: false, catwise_match: false, galactic_b: 25,
  labels: [], reasons: [], why: "", flags: [], frames_url: null,
};
const c = (o: Partial<Candidate>): Candidate => ({ ...base, ...o });
const votes = (mover = 0, changing = 0, junk = 0, unsure = 0): Counts => ({ mover, changing, junk, unsure });
const st = (o: Partial<FilterState>): FilterState => ({ ...defaultState(), ...o });

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

  it("keeps everything with no filters", () => {
    expect(filterRows(rows, defaultState()).rows).toHaveLength(194);
  });

  it("ORs within a facet and ANDs between facets", () => {
    expect(filterRows(rows, st({ kind: ["mover"] })).rows).toHaveLength(27);
    expect(filterRows(rows, st({ kind: ["mover", "high_pm"] })).rows).toHaveLength(194);
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
    const doubtless = filterRows(rows, st({ hideDoubts: true })).rows;
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
    expect(filterRows(rows, st({ ranges: { b: { min: 10, max: null } } })).rows).toHaveLength(194); // pilot is at b ≈ +25°
    expect(filterRows(rows, st({ ranges: { b: { min: 30, max: null } } })).rows).toHaveLength(0);
    expect(filterRows(rows, st({ label: ["red"] })).rows).toHaveLength(16);
  });

  it("filters by verdict only where there is one", () => {
    const voted = toRows([c({ id: "a" }), c({ id: "b" })], new Map([["a", votes(5)], ["b", votes(1)]]));
    expect(filterRows(voted, st({ verdict: ["mover"] })).rows.map((r) => r.c.id)).toEqual(["a"]);
    expect(filterRows(voted, st({ review: ["some"] })).rows.map((r) => r.c.id)).toEqual(["b"]);
  });

  it("suggests the one filter whose removal brings back the most", () => {
    const s = st({ kind: ["mover"], gaia: "yes" });
    const f = filterRows(rows, s);
    expect(f.rows).toHaveLength(0);
    const hint = bestRelaxation(f);
    expect(hint).toEqual({ key: "kind", n: 142 }); // the 142 Gaia matches are all moving stars
    expect(stateToQuery(withoutTest(s, hint!.key))).toBe("gaia=yes");
    expect(filterRows(rows, withoutTest(s, "gaia")).rows).toHaveLength(27);
    expect(withoutTest(st({ ranges: { score: { min: 0.1, max: null } } }), "score").ranges).toEqual({});
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
    expect(n("promising")).toBe(35);
    expect(n("needs-vote")).toBe(194);
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
  it("puts artefact cautions, then the pipeline's doubts, below everything else in 'Most promising'", () => {
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
