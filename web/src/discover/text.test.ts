import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { REASON_TEXT, reasonText } from "./text";

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
