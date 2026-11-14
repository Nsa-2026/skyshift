import { describe, expect, it } from "vitest";
import fixture from "../../../fixtures/api/candidates.json";
import flagsMd from "../../../contracts/flags.md?raw";
import { FLAG_TEXT, KIND_TEXT, LABEL_TEXT, REASON_TEXT, STATUS_TEXT, flagWords, labelWords, listWords, reasonWords } from "./words";

const cands = fixture.candidates;

/** Bit names per policy, parsed from contracts/flags.md's policy table. */
function contractFlags(): Record<"mask" | "record" | "never", string[]> {
  const md = flagsMd;
  const row = (label: string) => {
    const line = md.split("\n").find((l) => l.startsWith(`| **${label}**`));
    if (!line) throw new Error(`no ${label} row in contracts/flags.md`);
    return [...line.split("|")[2].matchAll(/([A-Z_]+) \d+/g)].map((m) => m[1]);
  };
  return { mask: row("Mask"), record: row("Record"), never: row("Never mask") };
}

describe("reason codes", () => {
  // pipeline/src/skyshift_pipeline/highpm.py REASONS, plus the suspects' evidence codes.
  const PIPELINE = ["short_span", "few_dots", "bad_fit", "over_pm_cap", "gaia_pm_disagrees", "catwise_pm_disagrees", "at_control_level"];
  const EVIDENCE = ["two_exposures", "two_detectors"];

  it("has plain words for every pipeline reason and every evidence code", () => {
    for (const code of [...PIPELINE, ...EVIDENCE]) {
      expect(REASON_TEXT[code], code).toBeDefined();
      expect(REASON_TEXT[code].plain).toMatch(/^[A-Z].*\.$/);
      expect(REASON_TEXT[code].short.length).toBeLessThan(30);
    }
  });

  it("covers every reason in the pilot sample", () => {
    for (const code of new Set(cands.flatMap((c) => c.reasons))) expect(REASON_TEXT[code], code).toBeDefined();
  });

  it("weighs evidence as good, disputes as caution and the speed cap as bad", () => {
    expect(REASON_TEXT.two_detectors.tone).toBe("good");
    expect(REASON_TEXT.two_exposures.tone).toBe("good");
    expect(REASON_TEXT.gaia_pm_disagrees.tone).toBe("caution");
    expect(REASON_TEXT.over_pm_cap.tone).toBe("bad");
  });

  it("states the pipeline's numbers in the words", () => {
    expect(REASON_TEXT.few_dots.plain).toContain("20");
    expect(REASON_TEXT.bad_fit.plain).toContain("5");
    expect(REASON_TEXT.over_pm_cap.plain).toContain("10.5″");
    expect(REASON_TEXT.short_span.plain).toContain("0.25 yr");
  });

  it("falls back to readable words for an unknown code", () => {
    const w = reasonWords("new_check_2027");
    expect(w.short).toBe("New check 2027");
    expect(w.plain).toBe("Pipeline note: new check 2027.");
    expect(w.tone).toBe("caution");
  });
});

describe("FLAGS bits", () => {
  const contract = contractFlags();

  it("has every bit of contracts/flags.md, with the contract's policy", () => {
    expect(contract.mask.length).toBe(13);
    expect(contract.record.length).toBe(7);
    for (const policy of ["mask", "record", "never"] as const)
      for (const name of contract[policy]) {
        expect(FLAG_TEXT[name], name).toBeDefined();
        expect(FLAG_TEXT[name].policy, name).toBe(policy);
      }
  });

  it("matches the masks in contracts/flags.md bit for bit", () => {
    const bits: Record<string, number> = {
      TRANSIENT: 0, OVERFLOW: 1, SUR_ERROR: 2, NONFUNC: 6, DICHROIC: 7, MISSING_DATA: 9, HOT: 10, COLD: 11, FULLSAMPLE: 12,
      PHANMISS: 14, NONLINEAR: 15, PERSIST: 17, OUTLIER: 19, CROSSTALK: 20, SOURCE: 21, GHOST: 22, GHOST_FPA: 23,
      GHOST_EXT: 24, STREAK: 25, BLOOM: 26, SNOWBALL: 27, HALO: 28,
    };
    const mask = (p: string) => Object.entries(bits).filter(([n]) => FLAG_TEXT[n].policy === p).reduce((m, [, b]) => m | (1 << b), 0) >>> 0;
    expect(mask("mask")).toBe(0x0a02cec7);
    expect(mask("record")).toBe(0x15d80000);
    expect(mask("never")).toBe(0x00201000);
  });

  it("reads names in any case and spacing", () => {
    expect(flagWords("ghost")).toBe(FLAG_TEXT.GHOST);
    expect(flagWords(" Ghost-Ext ").plain).toBe(FLAG_TEXT.GHOST_EXT.plain);
    expect(flagWords("missing data").policy).toBe("mask");
  });

  it("treats an unknown flag as a reason for care, in readable words", () => {
    const w = flagWords("WEIRD_GLOW");
    expect(w.policy).toBe("record");
    expect(w.plain).toBe("weird glow");
  });

  it("never says 'artefact' in caps or leaves a code in the plain words", () => {
    for (const [name, w] of Object.entries(FLAG_TEXT)) {
      expect(w.plain, name).not.toMatch(/[A-Z]{3,}_|_[A-Z]{3,}/);
      expect(w.plain.charAt(0), name).toBe(w.plain.charAt(0).toLowerCase());
    }
  });
});

describe("labels, kinds and statuses", () => {
  it("covers every label in the pilot sample and the ones types.ts lists", () => {
    const listed = ["red", "ch4", "h2o_ice", "co2_ice", "co_ice", "h2o_vapour", "brown_dwarf_like"];
    for (const l of new Set([...listed, ...cands.flatMap((c) => c.labels)])) expect(LABEL_TEXT[l], l).toBeDefined();
  });

  it("gives every label its wavelength in µm where it has one", () => {
    for (const l of ["ch4", "h2o_ice", "co2_ice", "co_ice", "h2o_vapour", "co", "pah"]) expect(LABEL_TEXT[l].plain).toMatch(/\d µm/);
  });

  it("falls back for unknown labels", () => {
    expect(labelWords("nh3_band")).toEqual({ short: "Nh3 band", plain: "nh3 band" });
  });

  it("uses DESIGN.md's kind names", () => {
    expect(KIND_TEXT.mover.short).toBe("Fast mover");
    expect(KIND_TEXT.high_pm.short).toBe("Moving star");
    expect(KIND_TEXT.spectral.short).toBe("Odd spectrum");
  });

  it("has a word for every status, in sentence case", () => {
    expect(Object.keys(STATUS_TEXT).sort()).toEqual(["ask", "caution", "fail", "pass", "unknown"]);
    for (const w of Object.values(STATUS_TEXT)) expect(w).toMatch(/^[A-Z][a-z' ]+$/);
  });

  it("joins lists in plain English", () => {
    expect(listWords([])).toBe("");
    expect(listWords(["a"])).toBe("a");
    expect(listWords(["a", "b"])).toBe("a and b");
    expect(listWords(["a", "b", "c"])).toBe("a, b and c");
  });
});
