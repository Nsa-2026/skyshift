import { describe, expect, it } from "vitest";
import { landingUnderOpening, openingMode, skipsOpening, type OpeningEnv } from "./rules";

function env(over: Partial<OpeningEnv> = {}): OpeningEnv {
  return { hash: "", search: "", reducedMotion: false, ...over };
}

describe("the opening plays on the first visit in a tab", () => {
  it.each(["", "#", "#/", "#/sky", "#/sky/266,-29,50", "#/sky/42,10,360,mol", "#/explore", "#/c/abc", "#/about"])(
    "whatever the hash (%j)",
    hash => {
      expect(openingMode(env({ hash }))).toBe("play");
    },
  );
  it.each(["?utm_source=mail&utm_campaign=sky", "?t=barnard", "?ra=269.45&dec=4.69", "?intro=1"])("with the query %s", search => {
    expect(openingMode(env({ search }))).toBe("play");
  });
  it("uses a still title for reduced motion", () => {
    expect(openingMode(env({ reducedMotion: true }))).toBe("still");
    expect(openingMode(env({ hash: "#/sky/1,2,30", reducedMotion: true }))).toBe("still");
  });
});

describe("the opening stays away", () => {
  it.each(["", "#/sky/42,10,360,mol", "#/explore/spectrum", "#/candidates/kind=mover", "#/learn"])(
    "keeps the current page on refresh (%j)", hash => {
      expect(openingMode(env({ hash, visitedInTab: true }))).toBeNull();
      expect(openingMode(env({ hash, visitedInTab: true, reducedMotion: true }))).toBeNull();
    },
  );
  it.each(["?embed=1", "?t=barnard&embed=1", "?intro=0", "?utm_source=x&intro=0"])("for %s", search => {
    expect(skipsOpening(search)).toBe(true);
    expect(openingMode(env({ search, hash: "#/sky" }))).toBeNull();
  });
});

describe("where the opening lands", () => {
  const at = (hash: string, search = "", reducedMotion = false): OpeningEnv => ({ hash, search, reducedMotion });
  it("sends Viewer addresses to the Sky", () => {
    expect(landingUnderOpening(at("", "?t=barnard"))).toBe("#/sky");
    expect(landingUnderOpening(at("#/", "?ra=10&dec=20"))).toBe("#/sky");
    expect(landingUnderOpening(at("#/explore/spectrum", "?t=barnard", true))).toBe("#/sky");
  });
  it("keeps every other page, the bare home, and skipped openings", () => {
    for (const h of ["", "#/sky/1,2,3", "#/c/hunt-1", "#/candidates", "#/results", "#/learn/practice"])
      expect(landingUnderOpening(at(h)), h).toBeNull();
    expect(landingUnderOpening(at("#/explore", "?intro=0"))).toBeNull();
    expect(landingUnderOpening(at("#/explore", "?embed=1"))).toBeNull();
  });
  it("preserves Viewer coordinates and sections when refreshing the same tab", () => {
    for (const hash of ["", "#/explore", "#/explore/motion+spectrum", "#/explore/processing"])
      expect(landingUnderOpening({ ...at(hash, "?ra=10&dec=20&det=3"), visitedInTab: true })).toBeNull();
  });
});
