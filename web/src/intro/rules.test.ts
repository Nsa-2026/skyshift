import { describe, expect, it } from "vitest";
import { SEEN_KEY, claimOpening, isBareHome, type OpeningEnv, type SeenStore } from "./rules";

function memory(): SeenStore & { values: Map<string, string> } {
  const values = new Map<string, string>();
  return {
    values,
    getItem: (k) => values.get(k) ?? null,
    setItem: (k, v) => void values.set(k, v),
  };
}

function env(over: Partial<OpeningEnv> = {}, store: SeenStore | null = memory()): OpeningEnv {
  return { hash: "", search: "", reducedMotion: false, store: () => store, ...over };
}

describe("the opening plays", () => {
  it.each(["", "#", "#/"])("on a first visit to the bare home URL (hash %j), once", (hash) => {
    const store = memory();
    expect(claimOpening(env({ hash }, store))).toBe(true);
    expect(store.values.get(SEEN_KEY)).toBe("1");
    // Second visit, a reload or another tab: never again.
    expect(claimOpening(env({ hash }, store))).toBe(false);
  });
});

describe("the opening stays away", () => {
  it.each(["#/explore", "#/sky/266,-29,50", "#/c/abc", "#/candidates", "#/about", "#/review"])(
    "for the deep link %s, and doesn't use up the first visit",
    (hash) => {
      const store = memory();
      expect(claimOpening(env({ hash }, store))).toBe(false);
      expect(store.values.size).toBe(0);
    },
  );
  it.each(["?t=barnard", "?ra=269.45&dec=4.69", "?embed=1", "?target=wise0855"])("for the query link %s", (search) => {
    const store = memory();
    expect(claimOpening(env({ search }, store))).toBe(false);
    expect(store.values.size).toBe(0);
  });
  it("when it has been seen", () => {
    const store = memory();
    store.setItem(SEEN_KEY, "1");
    expect(claimOpening(env({}, store))).toBe(false);
  });
  it("with reduced motion, and leaves the first visit unused", () => {
    const store = memory();
    expect(claimOpening(env({ reducedMotion: true }, store))).toBe(false);
    expect(store.values.size).toBe(0);
  });
  it("when the browser denies storage", () => {
    expect(claimOpening({ ...env(), store: () => { throw new DOMException("denied", "SecurityError"); } })).toBe(false);
  });
  it("when reading storage throws", () => {
    const store = { getItem: () => { throw new Error("denied"); }, setItem: () => {} };
    expect(claimOpening(env({}, store))).toBe(false);
  });
  it("when writing storage throws (quota, Safari private mode)", () => {
    const store = { getItem: () => null, setItem: () => { throw new Error("QuotaExceededError"); } };
    expect(claimOpening(env({}, store))).toBe(false);
  });
  it("when storage silently drops the write", () => {
    const store = { getItem: () => null, setItem: () => {} };
    expect(claimOpening(env({}, store))).toBe(false);
  });
  it("when there is no storage at all", () => {
    expect(claimOpening(env({}, null))).toBe(false);
  });
});

describe("isBareHome", () => {
  it("accepts only the home itself", () => {
    expect(isBareHome("", "")).toBe(true);
    expect(isBareHome("#/", "?")).toBe(true);
    expect(isBareHome("#/sky", "")).toBe(false);
    expect(isBareHome("", "?utm_source=x")).toBe(false);
  });
});
