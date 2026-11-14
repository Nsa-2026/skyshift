import { describe, expect, it, vi } from "vitest";
import { ALADIN_URLS, SkyUnavailable, loadAladin } from "./loader";

const fakeAladin = (init: Promise<void> = Promise.resolve()) => ({
  default: { init, aladin: () => ({}), HiPS: () => ({}) },
});

describe("loadAladin", () => {
  it("pins 3.8.2: jsDelivr first, unpkg second, nothing from CDS", () => {
    expect(ALADIN_URLS).toEqual([
      "https://cdn.jsdelivr.net/npm/aladin-lite@3.8.2/dist/aladin.js",
      "https://unpkg.com/aladin-lite@3.8.2/dist/aladin.js",
    ]);
    expect(ALADIN_URLS.join(" ")).not.toMatch(/unistra|latest/);
  });

  it("uses jsDelivr when it answers and never asks unpkg", async () => {
    const importer = vi.fn(async () => fakeAladin());
    const A = await loadAladin({ importer, hasWebGL2: () => true });
    expect(typeof A.aladin).toBe("function");
    expect(importer.mock.calls.map((c) => (c as unknown[])[0])).toEqual([ALADIN_URLS[0]]);
  });

  it("falls back to unpkg when jsDelivr fails", async () => {
    const importer = vi.fn(async (url: string) => {
      if (url.includes("jsdelivr")) throw new TypeError("Failed to fetch dynamically imported module");
      return fakeAladin();
    });
    const A = await loadAladin({ importer, hasWebGL2: () => true });
    expect(A).toBeTruthy();
    expect(importer.mock.calls.map((c) => c[0])).toEqual([...ALADIN_URLS]);
  });

  it("falls back when the first source hangs", async () => {
    const importer = vi.fn((url: string) => (url.includes("jsdelivr") ? new Promise(() => {}) : Promise.resolve(fakeAladin())));
    const A = await loadAladin({ importer, hasWebGL2: () => true, timeoutMs: 20 });
    expect(A).toBeTruthy();
    expect(importer).toHaveBeenCalledTimes(2);
  });

  it("falls back when a source returns something that isn't Aladin", async () => {
    const importer = vi.fn(async (url: string) => (url.includes("jsdelivr") ? { default: {} } : fakeAladin()));
    await expect(loadAladin({ importer, hasWebGL2: () => true })).resolves.toBeTruthy();
    expect(importer).toHaveBeenCalledTimes(2);
  });

  it("gives a plain reason when every source fails", async () => {
    const importer = vi.fn(async () => {
      throw new Error("offline");
    });
    const err = await loadAladin({ importer, hasWebGL2: () => true }).catch((e) => e);
    expect(err).toBeInstanceOf(SkyUnavailable);
    expect(err.reason).toBe("the Aladin sky viewer couldn't be downloaded");
    expect(importer).toHaveBeenCalledTimes(2);
  });

  it("doesn't download anything without WebGL2", async () => {
    const importer = vi.fn(async () => fakeAladin());
    const err = await loadAladin({ importer, hasWebGL2: () => false }).catch((e) => e);
    expect(err).toBeInstanceOf(SkyUnavailable);
    expect(err.reason).toMatch(/WebGL2/);
    expect(importer).not.toHaveBeenCalled();
  });

  it("reports a failed start-up (A.init rejects) without trying another source", async () => {
    const importer = vi.fn(async () => fakeAladin(Promise.reject("WebGL2 not supported by your browser")));
    const err = await loadAladin({ importer, hasWebGL2: () => true }).catch((e) => e);
    expect(err).toBeInstanceOf(SkyUnavailable);
    expect(err.message).toMatch(/^Sky view unavailable: WebGL2/);
    expect(importer).toHaveBeenCalledTimes(1);
  });
});
