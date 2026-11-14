import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { GRADE_TEXT, GRADES, REASON_TEXT, VET_GATES, failedChecks, gradeSentence, gradeWords, isVetGate, lcFirst, reasonText } from "./text";

const published = JSON.parse(
  readFileSync(join(__dirname, "../../public/data/candidates.json"), "utf8"),
) as { candidates: { reasons: string[] }[] };

// contracts/api.md, suspects.json "reasons" (pipeline config VET_GATES).
const ARTEFACT_GATES = ["hot_pixel", "bad_column", "edge", "masked_peak", "sharp_outlier", "very_sharp", "flux_ratio", "red_photo"];
const OTHER_GATES = ["2mass", "allwise", "not_vetted"];

describe("REASON_TEXT", () => {
  it("has text for every reason code in the published list", () => {
    const codes = new Set(published.candidates.flatMap((c) => c.reasons));
    expect(codes.size).toBeGreaterThan(0);
    expect([...codes].filter((code) => !REASON_TEXT[code])).toEqual([]);
  });

  it("names every vetting gate, artefact gates as cautions", () => {
    for (const code of ARTEFACT_GATES) expect(REASON_TEXT[code]?.tone, code).toBe("caution");
    for (const code of OTHER_GATES) expect(REASON_TEXT[code]?.tone, code).toBe("doubt");
  });

  it("still treats an unknown code as a doubt", () => {
    expect(reasonText("some_new_gate").tone).toBe("doubt");
  });
});

describe("grades in words", () => {
  it("lists every gate the contract names, and only those", () => {
    expect([...VET_GATES].sort()).toEqual([...ARTEFACT_GATES, ...OTHER_GATES].sort());
    expect(isVetGate("hot_pixel")).toBe(true);
    expect(isVetGate("two_exposures")).toBe(false);
  });

  it("says what each grade means, and names a grade C's failed checks", () => {
    expect(gradeWords("B", [])).toBe("Passed every check, motion not shown yet");
    expect(gradeWords("A", [])).toBe("Moves along a track");
    expect(gradeWords("C", ["two_exposures", "hot_pixel", "2mass", "allwise"])).toBe("Failed: hot pixel, 2MASS source here, AllWISE source here");
    expect(gradeWords("C", ["two_exposures"])).toBe("Failed a check"); // no gate named: still says it failed
    expect(gradeSentence("B", [])).toBe("Grade B: passed every check, motion not shown yet.");
    expect(gradeSentence("C", ["edge"])).toBe("Grade C, failed: at the detector edge.");
    expect(failedChecks(["sharp_outlier", "very_sharp", "not_vetted"])).toBe("sharper than a star, far too sharp, not vetted");
    for (const g of GRADES) expect(GRADE_TEXT[g].label).toBe(`Grade ${g}`);
  });

  it("keeps acronyms and names as written when lower-casing", () => {
    expect(lcFirst("Hot pixel")).toBe("hot pixel");
    expect(lcFirst("2MASS source here")).toBe("2MASS source here");
    expect(lcFirst("AllWISE source here")).toBe("AllWISE source here");
    expect(lcFirst("Failed: edge")).toBe("failed: edge");
  });
});
