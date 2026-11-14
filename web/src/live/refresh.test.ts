import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createMinuteTicker, createRefresher } from "./refresh";

function fakeDoc() {
  const listeners = new Set<() => void>();
  return {
    hidden: false,
    addEventListener: (_: string, fn: () => void) => listeners.add(fn),
    removeEventListener: (_: string, fn: () => void) => listeners.delete(fn),
    set(hidden: boolean) {
      this.hidden = hidden;
      listeners.forEach((fn) => fn());
    },
    listeners,
  };
}

describe("createRefresher", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-11-14T18:00:00Z"));
  });
  afterEach(() => vi.useRealTimers());

  function setup(load = vi.fn(async (_s: AbortSignal) => ({ n: 1 }))) {
    const doc = fakeDoc();
    const onData = vi.fn();
    const onError = vi.fn();
    const onLoading = vi.fn();
    const r = createRefresher({ load, onData, onError, onLoading, intervalMs: 60e3, doc: doc as unknown as Document });
    return { r, doc, load, onData, onError, onLoading };
  }

  it("loads at once, then every 60 s after each load ends", async () => {
    const { r, load, onData } = setup();
    r.start();
    expect(load).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(0);
    expect(onData).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(59_000);
    expect(load).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1_000);
    expect(load).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(load).toHaveBeenCalledTimes(3);
    r.destroy();
  });

  it("never runs two loads at once", async () => {
    let resolve: (v: unknown) => void = () => {};
    const load = vi.fn(() => new Promise((res) => (resolve = res)));
    const { r } = setup(load as never);
    r.start();
    await vi.advanceTimersByTimeAsync(300_000);
    expect(load).toHaveBeenCalledTimes(1); // still waiting: no timer runs while a load is in flight
    resolve({});
    await vi.advanceTimersByTimeAsync(60_000);
    expect(load).toHaveBeenCalledTimes(2);
    r.destroy();
  });

  it("stop() (another route) aborts the fetch and stops the timer; start() resumes", async () => {
    const signals: AbortSignal[] = [];
    const load = vi.fn((s: AbortSignal) => {
      signals.push(s);
      return new Promise(() => {});
    });
    const { r, onData, onError, onLoading } = setup(load as never);
    r.start();
    expect(r.loading).toBe(true);
    r.stop();
    expect(signals[0].aborted).toBe(true);
    expect(r.loading).toBe(false);
    expect(onLoading).toHaveBeenLastCalledWith(false);
    await vi.advanceTimersByTimeAsync(600_000);
    expect(load).toHaveBeenCalledTimes(1);
    expect(onData).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
    r.start();
    expect(load).toHaveBeenCalledTimes(2);
    r.destroy();
  });

  it("on resume, waits out the rest of the minute when the data is fresh", async () => {
    const { r, load } = setup();
    r.start();
    await vi.advanceTimersByTimeAsync(20_000);
    r.stop();
    r.start();
    expect(load).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(39_000);
    expect(load).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1_000);
    expect(load).toHaveBeenCalledTimes(2);
    r.destroy();
  });

  it("pauses while the page is hidden and catches up when it's visible", async () => {
    const { r, doc, load } = setup();
    r.start();
    await vi.advanceTimersByTimeAsync(10_000);
    doc.set(true);
    await vi.advanceTimersByTimeAsync(600_000);
    expect(load).toHaveBeenCalledTimes(1);
    doc.set(false);
    expect(load).toHaveBeenCalledTimes(2); // the data is older than a minute: load now
    r.destroy();
    expect(doc.listeners.size).toBe(0);
  });

  it("hiding aborts a load in flight", async () => {
    const signals: AbortSignal[] = [];
    const load = vi.fn((s: AbortSignal) => {
      signals.push(s);
      return new Promise(() => {});
    });
    const { r, doc } = setup(load as never);
    r.start();
    doc.set(true);
    expect(signals[0].aborted).toBe(true);
    r.destroy();
  });

  it("reports errors and tries again a minute later; refreshNow() retries at once", async () => {
    const load = vi.fn().mockRejectedValueOnce(new Error("offline")).mockResolvedValue({ ok: true });
    const { r, onError, onData } = setup(load);
    r.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(onError).toHaveBeenCalledTimes(1);
    r.refreshNow();
    await vi.advanceTimersByTimeAsync(0);
    expect(onData).toHaveBeenCalledWith({ ok: true });
    expect(load).toHaveBeenCalledTimes(2);
    r.destroy();
  });

  it("a stopped refresher started while hidden waits for the page to show", async () => {
    const { r, doc, load } = setup();
    doc.hidden = true;
    r.start();
    expect(load).not.toHaveBeenCalled();
    doc.set(false);
    expect(load).toHaveBeenCalledTimes(1);
    r.destroy();
  });
});

describe("createMinuteTicker", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("ticks on minute boundaries, not every second, until stopped", async () => {
    vi.setSystemTime(new Date("2026-11-14T18:00:30Z"));
    const fn = vi.fn();
    const t = createMinuteTicker(fn);
    t.start();
    await vi.advanceTimersByTimeAsync(29_000);
    expect(fn).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1_100);
    expect(fn).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(fn).toHaveBeenCalledTimes(2);
    t.stop();
    await vi.advanceTimersByTimeAsync(600_000);
    expect(fn).toHaveBeenCalledTimes(2);
  });
});
