import { describe, expect, it } from "vitest";
import { openingMode, isBareHome, isOpeningPreview, type OpeningEnv } from "./rules";

function env(over: Partial<OpeningEnv> = {}): OpeningEnv {
  return { hash: "", search: "", reducedMotion: false, ...over };
}

describe("the opening plays", () => {
  it.each(["", "#", "#/"])("on every visit to the home URL (hash %j)", hash => {
    expect(openingMode(env({ hash }))).toBe("play");
    expect(openingMode(env({ hash }))).toBe("play");
  });
  it.each(["?utm_source=mail&utm_campaign=sky", "?fbclid=x", "?gclid=x&msclkid=y", "?UTM_SOURCE=link"])("with attribution tags %s", search => {
    expect(openingMode(env({ search }))).toBe("play");
  });
  it("uses a still title for reduced motion on every home visit", () => {
    expect(openingMode(env({ reducedMotion: true }))).toBe("still");
    expect(openingMode(env({ reducedMotion: true }))).toBe("still");
  });
});

describe("the opening stays away", () => {
  it.each(["#/explore", "#/sky", "#/sky/266,-29,50", "#/c/abc", "#/candidates", "#/about", "#/review", "#/research/data"])("for the deep link %s", hash => {
    expect(openingMode(env({ hash }))).toBeNull();
  });
  it.each(["?t=barnard", "?ra=269.45&dec=4.69", "?embed=1", "?target=wise0855", "?utm_source=x&t=barnard"])("for the query link %s", search => {
    expect(openingMode(env({ search }))).toBeNull();
  });
});

describe("isBareHome", () => {
  it("accepts the home itself and tracking tags, but no observation state", () => {
    expect(isBareHome("", "")).toBe(true);
    expect(isBareHome("#/", "?")).toBe(true);
    expect(isBareHome("", "?utm_source=x")).toBe(true);
    expect(isBareHome("#/sky", "")).toBe(false);
    expect(isBareHome("", "?utm_source=x&embed=1")).toBe(false);
  });
});

it("offers an explicit preview without intercepting observation, embed or area links", () => {
  expect(isOpeningPreview("", "?intro=1")).toBe(true);
  expect(isOpeningPreview("#/sky/125.6315,19.2541,360,mol", "?intro=1")).toBe(true);
  expect(isOpeningPreview("#/sky", "?intro=1&utm_source=review")).toBe(true);
  for (const search of ["?intro=0", "?intro=1&t=barnard", "?intro=1&ra=10&dec=20", "?intro=1&embed=1"])
    expect(isOpeningPreview("", search)).toBe(false);
  expect(isOpeningPreview("#/research/data", "?intro=1")).toBe(false);
});
