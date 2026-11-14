import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  DETAILS_EVENT,
  DETAILS_KEY,
  _resetDetailsForTests,
  getDetails,
  mountDetailsSwitch,
  onDetails,
  setDetails,
} from "./details";

/** Node 26 shadows jsdom's localStorage with an undefined getter: install a small one. */
function memoryStorage(): Storage {
  const m = new Map<string, string>();
  return {
    get length() {
      return m.size;
    },
    clear: () => m.clear(),
    getItem: (k) => (m.has(k) ? m.get(k)! : null),
    setItem: (k, v) => void m.set(k, String(v)),
    removeItem: (k) => void m.delete(k),
    key: (i) => [...m.keys()][i] ?? null,
  };
}

beforeEach(() => {
  vi.stubGlobal("localStorage", memoryStorage());
  _resetDetailsForTests();
  delete document.documentElement.dataset.details;
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("Details preference", () => {
  it("is off by default", () => {
    expect(getDetails()).toBe(false);
  });

  it("remembers the choice and mirrors it on <html>", () => {
    setDetails(true);
    expect(getDetails()).toBe(true);
    expect(localStorage.getItem(DETAILS_KEY)).toBe("on");
    expect(document.documentElement.dataset.details).toBe("on");
    setDetails(false);
    expect(localStorage.getItem(DETAILS_KEY)).toBe("off");
    expect(document.documentElement.dataset.details).toBe("off");
  });

  it("reads a choice stored by an earlier visit", () => {
    localStorage.setItem(DETAILS_KEY, "on");
    expect(getDetails()).toBe(true);
  });

  it("announces changes once, on window", () => {
    const seen: boolean[] = [];
    const off = onDetails((on) => seen.push(on));
    const raw = vi.fn();
    window.addEventListener(DETAILS_EVENT, raw);
    setDetails(true);
    setDetails(true); // no change, no event
    setDetails(false);
    off();
    setDetails(true); // unsubscribed
    window.removeEventListener(DETAILS_EVENT, raw);
    expect(seen).toEqual([true, false]);
    expect(raw).toHaveBeenCalledTimes(3);
    expect((raw.mock.calls[0][0] as CustomEvent).detail).toEqual({ on: true });
  });

  it("works when storage is blocked", () => {
    const blocked = () => {
      throw new Error("blocked");
    };
    vi.stubGlobal("localStorage", { ...memoryStorage(), getItem: blocked, setItem: blocked });
    expect(getDetails()).toBe(false);
    expect(() => setDetails(true)).not.toThrow();
    expect(getDetails()).toBe(true);
    expect(document.documentElement.dataset.details).toBe("on");
  });

  it("follows another tab", () => {
    const seen: boolean[] = [];
    const off = onDetails((on) => seen.push(on));
    localStorage.setItem(DETAILS_KEY, "on");
    window.dispatchEvent(new StorageEvent("storage", { key: DETAILS_KEY, newValue: "on" }));
    off();
    expect(seen).toEqual([true]);
    expect(document.documentElement.dataset.details).toBe("on");
  });
});

describe("mountDetailsSwitch", () => {
  it("renders a labelled switch that toggles and stays in step with others", () => {
    const a = document.createElement("div");
    const b = document.createElement("div");
    const ha = mountDetailsSwitch(a);
    const hb = mountDetailsSwitch(b);
    const sa = a.querySelector<HTMLButtonElement>("button")!;
    const sb = b.querySelector<HTMLButtonElement>("button")!;
    expect(sa.getAttribute("role")).toBe("switch");
    expect(sa.textContent?.trim()).toBe("Advanced");
    expect(sa.getAttribute("aria-checked")).toBe("false");
    sa.click();
    expect(getDetails()).toBe(true);
    expect(sa.getAttribute("aria-checked")).toBe("true");
    expect(sb.getAttribute("aria-checked")).toBe("true");
    sb.click();
    expect(sa.getAttribute("aria-checked")).toBe("false");
    ha.destroy();
    hb.destroy();
    expect(a.childElementCount).toBe(0);
  });

  it("starts checked when Details is already on", () => {
    setDetails(true);
    const el = document.createElement("div");
    mountDetailsSwitch(el);
    expect(el.querySelector("button")!.getAttribute("aria-checked")).toBe("true");
  });
});
