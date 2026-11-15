// loadVisits: a featured target's saved file that lacks a detector at this field (most 15′ files
// hold only D2 and D5) must not freeze the colour pictures: ask the server for all three detectors.
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Det, FramesResponse } from "../api/types";

const loadFrames = vi.fn();
vi.mock("../api/client", async (orig) => ({
  ...(await orig<typeof import("../api/client")>()),
  loadFrames: (...a: unknown[]) => loadFrames(...a),
  loadFrameData: async () => ({ raw: new Uint16Array(4), scale: 1, zero: 0 }),
  toMJySr: () => new Float32Array(4),
}));
vi.mock("./rgb", async (orig) => ({ ...(await orig<typeof import("./rgb")>()), toChannel: () => ({}) }));

const { loadVisits } = await import("./visit");

function frames(dets: Det[]): FramesResponse {
  return {
    clock: "months",
    size: [2, 2],
    pixel_arcsec: 3.075,
    epochs: dets.flatMap((det) =>
      [1, 2].map((v) => ({ id: `d${det}-v${v}`, det, mjd_mid: 61000 + 180 * v, um_min: 1, um_max: 2 })),
    ),
  } as unknown as FramesResponse;
}

const view = { ra: 133.8, dec: -7.2, clock: "months", fov: 15, det: 2, slug: "wise0855", name: "WISE 0855" } as const;

describe("loadVisits", () => {
  beforeEach(() => {
    loadFrames.mockReset();
  });

  it("asks the server when the saved 15′ file lacks a detector, and then moves with the dates", async () => {
    loadFrames.mockImplementation(async (q: { slug?: string | null }) =>
      q.slug ? { frames: frames([2, 5]), jsonUrl: "s", origin: "static" } : { frames: frames([2, 3, 6]), jsonUrl: "a", origin: "api" },
    );
    const set = await loadVisits(view as never, "cold", new AbortController().signal, () => {});
    expect(loadFrames.mock.calls.filter(([q]) => !q.slug)).toHaveLength(3);
    expect([...set.visits.keys()]).toEqual([1, 2]);
  });

  it("says why when the server can't make them either", async () => {
    loadFrames.mockImplementation(async (q: { slug?: string | null }) => {
      if (q.slug) return { frames: frames([2, 5]), jsonUrl: "s", origin: "static" };
      throw new Error("HTTP 502");
    });
    await expect(loadVisits(view as never, "cold", new AbortController().signal, () => {})).rejects.toThrow(
      "the saved wide view has no D6 or D3 frames and the server couldn't make them",
    );
  });

  it("uses the saved file alone when it has every detector", async () => {
    loadFrames.mockResolvedValue({ frames: frames([1, 2, 3, 4, 5, 6]), jsonUrl: "s", origin: "static" });
    await loadVisits(view as never, "cold", new AbortController().signal, () => {});
    expect(loadFrames.mock.calls.every(([q]) => q.slug === "wise0855")).toBe(true);
  });
});
