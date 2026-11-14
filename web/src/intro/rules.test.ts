import { describe, expect, it } from "vitest";
import { isBareHome, openingMode, type OpeningEnv } from "./rules";

const env = (over: Partial<OpeningEnv> = {}): OpeningEnv => ({ hash: "", search: "", reducedMotion: false, ...over });

describe("the opening plays on the home page, every time", () => {
  it.each([
    ["", ""],
    ["#", ""],
    ["#/", ""],
    ["", "?"],
    ["", "?utm_source=spaceapps&utm_medium=social"],
    ["", "?fbclid=abc123"],
    ["", "?gclid=xyz"],
  ])("hash %j, query %j", (hash, search) => {
    expect(openingMode(env({ hash, search }))).toBe("play");
    // No memory: the next visit, a reload or another tab plays it again.
    expect(openingMode(env({ hash, search }))).toBe("play");
  });

  it("as a still card with reduced motion (still skippable)", () => {
    expect(openingMode(env({ reducedMotion: true }))).toBe("still");
  });
});

describe("the opening stays away from deep links", () => {
  it.each([
    "#/sky",
    "#/sky/266,-29,50",
    "#/explore",
    "#/c/abc",
    "#/candidates",
    "#/candidates/preset=promising",
    "#/review",
    "#/about",
    "#/learn",
    "#/results",
    "#/score",
    "#anything",
  ])("hash %s", (hash) => {
    expect(openingMode(env({ hash }))).toBeNull();
    expect(openingMode(env({ hash, reducedMotion: true }))).toBeNull();
  });

  it.each(["?t=barnard", "?ra=269.45&dec=4.69", "?ra=269.45&dec=4.69&clock=hours", "?embed=1", "?t=barnard&embed=1", "?target=wise0855", "?utm_source=x&t=barnard"])(
    "query %s",
    (search) => {
      expect(openingMode(env({ search }))).toBeNull();
    },
  );

  it("a deep link with a hash and a query", () => {
    expect(openingMode(env({ hash: "#/explore", search: "?t=barnard" }))).toBeNull();
  });
});

describe("isBareHome", () => {
  it("accepts only the home itself", () => {
    expect(isBareHome("", "")).toBe(true);
    expect(isBareHome("#/", "?")).toBe(true);
    expect(isBareHome("#/sky", "")).toBe(false);
    expect(isBareHome("", "?utm_campaign=launch")).toBe(true);
    expect(isBareHome("", "?ra=1")).toBe(false);
  });
});
