import { describe, expect, it } from "vitest";
import { openingMode, skipsOpening, type OpeningEnv } from "./rules";

function env(over: Partial<OpeningEnv> = {}): OpeningEnv {
  return { hash: "", search: "", reducedMotion: false, ...over };
}

describe("the opening plays on every page load", () => {
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
  it.each(["?embed=1", "?t=barnard&embed=1", "?intro=0", "?utm_source=x&intro=0"])("for %s", search => {
    expect(skipsOpening(search)).toBe(true);
    expect(openingMode(env({ search, hash: "#/sky" }))).toBeNull();
  });
});
