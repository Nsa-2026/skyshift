// Results in plain words: every number from the published files, a check left out rather than guessed.
import { describe, expect, it } from "vitest";
import validationJson from "../../public/data/validation.json";
import distantJson from "../../public/data/distant.json";
import type { DistantResponse, ValidationCheck, ValidationResponse } from "../api/types";
import { distantPlain, plainResults, plainTally, strongestMovers } from "./plain";

const validation = validationJson as unknown as ValidationResponse;
const distant = distantJson as unknown as DistantResponse;

const check = (id: string, over: Partial<ValidationCheck> = {}): ValidationCheck => ({
  id,
  claim: id,
  status: "pass",
  headline: "",
  value: null,
  unit: null,
  expected: "",
  note: null,
  rows: [],
  ...over,
});

describe("plainResults", () => {
  it("reads the published file: at most five, in plain words, each with its own status", () => {
    const r = plainResults(validation);
    expect(r.length).toBeGreaterThanOrEqual(3);
    expect(r.length).toBeLessThanOrEqual(5);
    for (const x of r) expect(x.text).not.toMatch(/\bV\d+\b|σ|S\/N|pipeline|recall/);
    const v5 = validation.checks.find((c) => c.id === "V5")!;
    expect(r.find((x) => x.id === "V5")?.text).toBe(
      `Found ${Math.round(Number(v5.value) * 100)}% of bright known asteroids without being told where to look.`,
    );
  });

  it("builds each sentence from the check's value and headline", () => {
    const v: ValidationResponse = {
      generated: "2026-11-15",
      data_release: "DR1",
      checks: [
        check("V4", { value: 58, unit: "asteroids", headline: "Re-found 58 of 60 asteroids where NASA predicted them" }),
        check("V2", { status: "partial", headline: "Barnard's Star moved 10.83\" at PA 357.8; Gaia predicts 10.27\" (+12.2%, 2.2 deg)" }),
        check("V1", { value: 4, unit: "targets", headline: "4 of 5 test objects found where predicted, in 18 of 20 visits" }),
        check("V8", { value: 0.99, unit: "ratio", headline: "SPHEREx / catalogue flux, Ks 0.990 (scatter 2.9%); W1 1.042 (scatter 1.9%)" }),
      ],
    };
    expect(plainResults(v)).toEqual([
      { id: "V4", status: "pass", text: "Found 58 of 60 known asteroids where NASA predicted them." },
      { id: "V2", status: "partial", text: "Barnard's Star moved as far as Gaia predicts, to within 13%." },
      { id: "V1", status: "pass", text: "Found 4 of 5 test stars and brown dwarfs where they should be, in 18 of 20 visits." },
      { id: "V8", status: "pass", text: "Star brightness agrees with older sky surveys to within 5%." },
    ]);
  });

  it("leaves out a check that hasn't run or whose number can't be read", () => {
    const v: ValidationResponse = {
      generated: "2026-11-15",
      data_release: "DR1",
      checks: [
        check("V5", { status: "pending", value: 0.9, unit: "recall" }),
        check("V4", { value: null }),
        check("V2", { headline: "Motion measured; see the rows" }),
      ],
    };
    expect(plainResults(v)).toEqual([]);
    expect(plainResults(null)).toEqual([]);
  });
});

describe("plainTally", () => {
  it("counts from the file, without codes", () => {
    expect(plainTally(validation.checks)).toBe("All 9 checks we ran passed; 2 more weren't run.");
    expect(plainTally([{ status: "pass" }, { status: "partial" }, { status: "fail" }])).toBe(
      "1 of the 3 checks we ran passed, 1 in part, 1 failed.",
    );
    expect(plainTally([{ status: "pass" }, { status: "pending" }])).toBe("The one check we ran passed; 1 more wasn't run.");
    expect(plainTally([{ status: "pending" }])).toBe("The checks haven't run yet.");
  });
});

describe("what we found", () => {
  it("counts the grade A fast movers only", () => {
    expect(strongestMovers(null)).toBe(0);
    const c = { candidates: [{ kind: "mover", grade: "A" }, { kind: "mover", grade: "B" }, { kind: "high_pm", grade: "A" }, { kind: "mover", grade: "a" }] };
    expect(strongestMovers(c as never)).toBe(2);
  });

  it("says the distant-planet search in a line, with how much it finds", () => {
    expect(distantPlain(null)).toBe("");
    // from the shipped distant.json (it changes with a data refresh), not pinned
    const inj = distant.injection!;
    const added = inj.distances.reduce((a, r) => a + r.injected, 0).toLocaleString("en-GB");
    const verdict = distant.control.beyond_chance
      ? "More possible pairs turned up than chance explains: worth a closer look, not yet a discovery."
      : "No sign of one in the patch of sky we searched.";
    expect(distantPlain(distant)).toBe(
      `${verdict} The same search found ${Math.round(inj.recovered_frac! * 100)}% of ${added} made-up planets we hid in the real photos.`,
    );
    const beyond = { ...distant, control: { ...distant.control, beyond_chance: true }, injection: null } as unknown as DistantResponse;
    expect(distantPlain(beyond)).toBe("More possible pairs turned up than chance explains: worth a closer look, not yet a discovery.");
  });
});
