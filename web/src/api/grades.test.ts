import { describe, expect, it } from "vitest";
import suspectsFx from "../../../fixtures/api/suspects.json";
import {
  GATES,
  GATE_TEXT,
  ZERO_A,
  countGrades,
  failedGates,
  gateText,
  gateTotals,
  gatesPhrase,
  gradeALine,
  gradeBCLine,
  gradeCSentence,
  gradeCounts,
  gradeLabel,
  gradeOf,
  gradeRank,
  leadRank,
  nearSourceOf,
  nearSourceText,
  rankByGrade,
  trackOf,
  trackText,
  vetoesText,
  votable,
} from "./grades";
import type { SuspectsResponse } from "./types";

// Fixtures are used here only, never copied into public/data.
const sus = suspectsFx as unknown as SuspectsResponse;

describe("reading grades", () => {
  it("reads A, B and C from the file and never invents one", () => {
    expect(gradeOf({ grade: "A" })).toBe("A");
    expect(gradeOf({ grade: " b " })).toBe("B");
    expect(gradeOf({ grade: "C" })).toBe("C");
    expect(gradeOf({ grade: null })).toBeNull();
    expect(gradeOf({ grade: "D" })).toBeNull();
    expect(gradeOf({})).toBeNull(); // an older, unvetted file
    expect(gradeOf(null)).toBeNull();
  });

  it("puts grade C beyond the vote, and leaves ungraded and A/B votable", () => {
    expect(votable({ grade: "C" })).toBe(false);
    expect(votable({ grade: "B" })).toBe(true);
    expect(votable({ grade: "A" })).toBe(true);
    expect(votable({})).toBe(true);
  });
});

describe("ranking", () => {
  it("ranks A, B, C, then ungraded; C never leads a mixed list", () => {
    expect([gradeRank("A"), gradeRank("B"), gradeRank("C"), gradeRank(null)]).toEqual([0, 1, 2, 3]);
    expect(leadRank("A")).toBeLessThan(leadRank("B"));
    expect(leadRank("B")).toBe(leadRank(null));
    expect(leadRank("C")).toBeGreaterThan(leadRank(null));
  });

  it("sorts suspects by grade, then S/N, highest first", () => {
    const rows = [
      { id: "c-hi", grade: "C", snr: 40 },
      { id: "none", snr: 99 },
      { id: "b-lo", grade: "B", snr: 6 },
      { id: "b-hi", grade: "B", snr: 12 },
      { id: "a", grade: "A", snr: 5 },
      { id: "c-lo", grade: "C", snr: 8 },
    ];
    expect(rankByGrade(rows).map((r) => r.id)).toEqual(["a", "b-hi", "b-lo", "c-hi", "c-lo", "none"]);
    expect(rows[0].id).toBe("c-hi"); // a copy: the input keeps its order
  });
});

describe("words", () => {
  it("names the grades as the contract does", () => {
    expect(gradeLabel("A")).toBe("Grade A: candidate mover");
    expect(gradeLabel("B")).toBe("Grade B: unexplained single-session source");
    expect(gradeLabel("C")).toBe("Grade C: fails a check");
  });

  it("says every gate in plain words, without codes or underscores", () => {
    for (const code of GATES) {
      const t = GATE_TEXT[code];
      expect(t.label).not.toMatch(/_/);
      expect(t.plain).not.toMatch(/_/);
      expect(t.plain.endsWith(".")).toBe(true);
      expect(t.plain.length).toBeLessThanOrEqual(200);
    }
    expect(gateText("hot_pixel").label).toBe("On a hot pixel");
    expect(gateText("some_new_gate").label).toBe("Some new gate"); // a newer pipeline: readable, not hidden
  });

  it("explains a grade C row from its reasons", () => {
    const [, s2, s3] = sus.suspects;
    expect(failedGates(s2)).toEqual(["hot_pixel", "edge"]);
    expect(gatesPhrase(failedGates(s2))).toBe("on a hot pixel and at the detector edge");
    expect(gradeCSentence(failedGates(s3))).toBe("Grade C: a 2MASS source sits here.");
    expect(gradeCSentence([])).toBe("Grade C: it fails a pipeline check.");
    // Candidate reasons mix evidence codes with gates: only the gates are why it is C.
    expect(failedGates({ reasons: ["two_exposures", "very_sharp", "flux_ratio"] })).toEqual(["very_sharp", "flux_ratio"]);
  });

  it("gives near_source_arcsec as a quiet warning", () => {
    expect(nearSourceOf(sus.suspects[0])).toBe(8.4);
    expect(nearSourceOf(sus.suspects[1])).toBeNull();
    expect(nearSourceOf({})).toBeNull();
    expect(nearSourceText(4.1)).toBe("A catalogued source sits 4.1″ away.");
    expect(nearSourceText(4)).toBe("A catalogued source sits 4.0″ away.");
  });
});

describe("the track behind a grade A", () => {
  it("reads the track fields, and reads absent or null as no track", () => {
    const t = trackOf(sus.suspects[0]);
    expect(t).toEqual({ sessions: 4, rate: 31.25, pa: 252.4, sigma: 0.82 });
    expect(trackText(t!)).toBe("On a track over 4 sessions, moving 31.3″ an hour (38σ) toward 252°.");
    expect(trackOf(sus.suspects[1])).toBeNull(); // null: on no track
    expect(trackOf({})).toBeNull(); // absent: a file written without tracks
    expect(trackText({ sessions: 3, rate: null, pa: null, sigma: null })).toBe("On a track over 3 sessions.");
    expect(trackText({ sessions: 2, rate: 5, pa: -10, sigma: null })).toBe("On a track over 2 sessions, moving 5.0″ an hour toward 350°.");
  });
});

describe("totals", () => {
  it("says 0 grade A as the expected result, not a failure", () => {
    expect(gradeALine(0)).toBe(ZERO_A);
    expect(ZERO_A).toBe("0 candidate movers (grade A). Expected: proving motion needs a track over several sessions.");
    expect(gradeALine(1)).toBe("1 candidate mover (grade A).");
    expect(gradeBCLine({ A: 0, B: 22, C: 206 })).toBe(
      "22 unexplained single-session sources (grade B) and 206 that fail a check (grade C).",
    );
    expect(gradeBCLine({ A: 0, B: 1, C: 1 })).toBe("1 unexplained single-session source (grade B) and 1 that fails a check (grade C).");
  });

  it("reads the file's totals, or counts rows, and returns null for an ungraded file", () => {
    expect(gradeCounts(sus.grades)).toEqual({ A: 1, B: 0, C: 2 });
    expect(gradeCounts({ A: 0, B: "1", C: 2 })).toBeNull();
    expect(countGrades(sus.suspects)).toEqual({ A: 1, B: 0, C: 2 });
    expect(countGrades([{}, { grade: null }])).toBeNull();
  });

  it("lists the zone's checks and the gates that failed anything, most first", () => {
    expect(vetoesText(sus.vetoes)).toBe("known asteroids and comets (MPC), Gaia and CatWISE");
    expect(vetoesText(undefined)).toBeNull();
    expect(gateTotals(sus.gates)).toEqual([
      ["2mass", 1],
      ["edge", 1],
      ["hot_pixel", 1],
    ]);
    expect(gateTotals({ edge: 3, hot_pixel: 9, bad_column: 0 })).toEqual([
      ["hot_pixel", 9],
      ["edge", 3],
    ]);
  });
});
