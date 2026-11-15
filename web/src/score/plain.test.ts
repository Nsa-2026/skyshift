// Results in plain words: every number from the published files, a check left out rather than guessed.
import { describe, expect, it } from "vitest";
import validationJson from "../../public/data/validation.json";
import distantJson from "../../public/data/distant.json";
import type { DistantResponse, ValidationCheck, ValidationResponse } from "../api/types";
import { distantPlain, distantVerdict, foundLine, knownMovingStars, plainResults, plainTally, strongestMovers, sureFigure, sureLine } from "./plain";

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
      `Found ${Math.round(Number(v5.value) * 100)}% of bright known asteroids unaided.`,
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
      { id: "V2", status: "partial", text: "Barnard's Star moved as Gaia predicts, within 13%." },
      { id: "V1", status: "pass", text: "Found 4 of 5 test stars, in 18 of 20 visits." },
      { id: "V8", status: "pass", text: "Star brightness matches older surveys within 5%." },
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
    expect(plainTally(validation.checks)).toBe("");
    expect(plainTally([{ status: "pass" }, { status: "partial" }, { status: "fail" }])).toBe(
      "1 passed in part, 1 failed.",
    );
    expect(plainTally([{ status: "pass" }, { status: "pending" }])).toBe("1 not run yet.");
    expect(plainTally([{ status: "pass" }])).toBe("");
    expect(plainTally([{ status: "pending" }])).toBe("The checks haven't run yet.");
  });
});

describe("what we found", () => {
  it("counts the grade A fast movers only", () => {
    expect(strongestMovers(null)).toBe(0);
    const c = { candidates: [{ kind: "mover", grade: "A" }, { kind: "mover", grade: "B" }, { kind: "high_pm", grade: "A" }, { kind: "mover", grade: "a" }] };
    expect(strongestMovers(c as never)).toBe(2);
  });

  it("counts the moving stars Gaia already lists, from candidates.json's own fields", () => {
    expect(knownMovingStars(null)).toBe(0);
    const c = {
      candidates: [
        { kind: "high_pm", gaia_match: true },
        { kind: "high_pm", gaia_match: true },
        { kind: "high_pm", gaia_match: false },
        { kind: "mover", gaia_match: true },
        { kind: "spectral", gaia_match: true },
      ],
    };
    expect(knownMovingStars(c as never)).toBe(2);
  });

  it("says the distant-planet search in a line, with how much it finds", () => {
    expect(distantPlain(null)).toBe("");
    // from the shipped distant.json (it changes with a data refresh), not pinned
    const inj = distant.injection!;
    const added = inj.distances.reduce((a, r) => a + r.injected, 0).toLocaleString("en-GB");
    const verdict = distant.control.beyond_chance
      ? "More pairs than chance explains: worth a closer look."
      : "No sign of one in the area searched.";
    expect(distantPlain(distant)).toBe(
      `${verdict} The search found ${Math.round(inj.recovered_frac! * 100)}% of ${added} test planets hidden in the photos.`,
    );
    const beyond = { ...distant, control: { ...distant.control, beyond_chance: true }, injection: null } as unknown as DistantResponse;
    expect(distantPlain(beyond)).toBe("More pairs than chance explains: worth a closer look.");
  });
});

describe("the overview's lines", () => {
  it("counts the checks that ran, with the mark of the weakest", () => {
    expect(sureFigure(validation.checks)).toEqual({ num: "11 of 11", label: "checks we ran passed", status: "pass" });
    expect(sureFigure([{ status: "pass" }, { status: "partial" }, { status: "pending" }])).toEqual({ num: "1 of 2", label: "checks we ran passed", status: "partial" });
    expect(sureFigure([{ status: "fail" }])).toEqual({ num: "0 of 1", label: "check we ran passed", status: "fail" });
    expect(sureFigure([{ status: "pending" }])).toBeNull();
  });

  it("says the asteroids and Barnard's Star from the file, and Barnard's Star only when its check passed", () => {
    expect(sureLine(validation)).toBe("60 of 60 known asteroids found; Barnard's Star matches Gaia.");
    const v = (over: Partial<ValidationCheck>): ValidationResponse => ({
      generated: "2026-11-15",
      data_release: "DR1",
      checks: [
        check("V4", { value: 58, unit: "asteroids", headline: "Re-found 58 of 60 asteroids where NASA predicted them" }),
        check("V2", { headline: "Barnard's Star moved 10.83\"; Gaia predicts 10.27\" (+12.2%)", ...over }),
      ],
    });
    expect(sureLine(v({ status: "partial" }))).toBe("58 of 60 known asteroids found.");
    expect(sureLine(v({}))).toBe("58 of 60 known asteroids found; Barnard's Star matches Gaia.");
    expect(sureLine(null)).toBe("");
  });

  it("names the strongest leads, or what the objects are", () => {
    expect(foundLine(null)).toBe("Things Gaia, WISE and known asteroids don't explain.");
    expect(foundLine({ candidates: [{ kind: "mover", grade: "A" }, { kind: "mover", grade: "A" }] } as never)).toBe(
      "Strongest leads: 2 fast movers on steady paths.",
    );
  });

  it("gives the distant-planet verdict alone", () => {
    expect(distantVerdict(null)).toBe("");
    expect(distantPlain(distant).startsWith(distantVerdict(distant))).toBe(true);
  });
});
