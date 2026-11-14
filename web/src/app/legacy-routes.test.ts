import { describe, expect, it } from "vitest";
import { legacyHash } from "./legacy-routes";

describe("legacyHash", () => {
  it("leaves this shell's own links alone", () => {
    for (const h of ["", "#/", "#/explore", "#/c/hunt-1", "#/candidates/kind=mover", "#/sky/1,2,3", "#/review", "#/learn/glossary"])
      expect(legacyHash(h)).toBeNull();
  });
  it("maps Explore links", () => {
    expect(legacyHash("#/explore/sky/125.6,19.2,360,mol")).toBe("#/sky/125.6,19.2,360,mol");
    expect(legacyHash("#/explore/sky")).toBe("#/sky");
    expect(legacyHash("#/explore/learn/stories")).toBe("#/learn/stories");
    expect(legacyHash("#/explore/community")).toBe("#/community");
    expect(legacyHash("#/explore/play")).toBe("#/review");
  });
  it("maps Analyze links, keeping candidate filters and ids", () => {
    expect(legacyHash("#/analyze/candidates/kind=mover&grade=A")).toBe("#/candidates/kind=mover&grade=A");
    expect(legacyHash("#/analyze/candidates")).toBe("#/candidates");
    expect(legacyHash("#/analyze/review")).toBe("#/review");
    expect(legacyHash("#/analyze/c/hunt-665180071278-87799171")).toBe("#/c/hunt-665180071278-87799171");
    expect(legacyHash("#/analyze")).toBe("#/explore");
    expect(legacyHash("#/analyze/spectrum")).toBe("#/explore");
  });
  it("maps Research links", () => {
    expect(legacyHash("#/research/live")).toBe("#/live");
    expect(legacyHash("#/research/results")).toBe("#/results");
    expect(legacyHash("#/research/tools")).toBe("#/tools");
    expect(legacyHash("#/research")).toBe("#/tools");
    expect(legacyHash("#/research/validation")).toBe("#/tools");
  });
});
