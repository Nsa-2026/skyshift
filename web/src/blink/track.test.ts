// The candidate's red circles on the mounted player (BlinkHandle.track): one per frame, solid where
// measured and dashed where predicted, walking with the object; every frame's faintly in Stack.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Det, Epoch, FramesResponse } from "../api/types";
import type { BlinkHandle, TrackMarks, View } from "../app/modules";
import { mountBlink } from "./index";

const N = 40;
const RED = "#ff0000";
const epoch = (id: string, det: Det, mjd: number): Epoch => ({
  id,
  kind: "exposure",
  mjd_mid: mjd,
  mjd_start: mjd - 0.0006,
  mjd_end: mjd + 0.0006,
  det,
  um_min: 3.8,
  um_max: 4.4,
  n_exposures: 1,
  flag_frac: 0,
  sat_frac: 0,
  data_url: `frames/${id}.bin`,
  scale: 0.001,
  zero: 0,
});
const MJD = [61139.924191, 61139.925683, 61139.927175, 61139.928668];
const centre = { ra: 121.58158, dec: 17.599035 };
const frames = (dets: Det[] = [5]): FramesResponse => ({
  clock: "hours",
  center: centre,
  pixel_arcsec: 6.15,
  size: [N, N],
  epochs: dets.flatMap((d) => MJD.map((m, i) => epoch(`d${d}-x_${i + 1}`, d, m))),
  gaia: [],
  known: [],
  data_release: "test",
  archive_checked: "2026-11-15",
});
const view: View = { ...centre, clock: "hours", fov: 20, det: 5, slug: null, name: null };
// pilot-645590069534-88041494: measured in exposures 1 and 4, predicted in 2 and 3 (moving west).
const marks: TrackMarks = {
  label: "the fast mover",
  spots: [
    { epoch_id: "d5-x_1", mjd: MJD[0], ra: 121.582754, dec: 17.599178, measured: true },
    { epoch_id: "d5-x_2", mjd: MJD[1], ra: 121.581971, dec: 17.599083, measured: false },
    { epoch_id: "d5-x_3", mjd: MJD[2], ra: 121.581189, dec: 17.598987, measured: false },
    { epoch_id: "d5-x_4", mjd: MJD[3], ra: 121.580406, dec: 17.598892, measured: true },
  ],
};

interface Stroke {
  arcs: { x: number; y: number; r: number }[];
  color: string;
  dash: number[];
  alpha: number;
}
/** A 2D context that records each stroke: its arcs, colour, dash and alpha. Everything else is a no-op. */
function recorder() {
  const strokes: Stroke[] = [];
  let path: Stroke["arcs"] = [];
  const state: Record<string, unknown> = { strokeStyle: "", globalAlpha: 1, dash: [] as number[] };
  const stack: Record<string, unknown>[] = [];
  const own: Record<string, unknown> = {
    save: () => stack.push({ ...state }),
    restore: () => Object.assign(state, stack.pop() ?? {}),
    beginPath: () => (path = []),
    arc: (x: number, y: number, r: number) => path.push({ x, y, r }),
    stroke: () => strokes.push({ arcs: [...path], color: String(state.strokeStyle), dash: [...(state.dash as number[])], alpha: Number(state.globalAlpha) }),
    setLineDash: (d: number[]) => (state.dash = [...d]),
    getLineDash: () => state.dash,
    measureText: (s: string) => ({ width: s.length * 6 }),
    createImageData: (w: number, h: number) => ({ data: new Uint8ClampedArray(w * h * 4), width: w, height: h }),
    createPattern: () => null,
  };
  const ctx = new Proxy(own, {
    get: (t, k: string) => (k in t ? t[k] : k in state ? state[k] : () => undefined),
    set: (_t, k: string, v) => ((state[k] = v), true),
  });
  /** The red circles of the last draws: one arc per stroke. */
  const red = () => strokes.filter((s) => s.color === RED && s.arcs.length === 1).map((s) => ({ ...s.arcs[0], dashed: s.dash.length > 0, alpha: s.alpha }));
  return { ctx, strokes, red };
}

describe("red circles where the candidate should be", () => {
  let el: HTMLDivElement;
  let h: BlinkHandle;
  let rec: ReturnType<typeof recorder>;
  const sub = () => el.querySelector(".bk-when-sub")!.textContent;
  /** Clear the record, act, and return the red circles drawn. */
  const after = (act: () => void) => {
    rec.strokes.length = 0;
    act();
    return rec.red();
  };

  beforeEach(() => {
    rec = recorder();
    vi.stubGlobal(
      "ResizeObserver",
      class {
        constructor(private cb: () => void) {}
        observe() {
          this.cb();
        }
        disconnect() {}
      },
    );
    // A 400 px plate: 10 screen pixels per image pixel.
    Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, get: () => 400 });
    vi.stubGlobal(
      "fetch",
      // Sky with a little noise (all wavelengths divides by each frame's noise).
      vi.fn(async () => new Response(Uint16Array.from({ length: N * N }, (_, i) => 1000 + ((i * 7919) % 41)).buffer)),
    );
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(rec.ctx as unknown as CanvasRenderingContext2D);
    el = document.createElement("div");
    el.style.setProperty("--pencil-red", RED);
    document.body.append(el);
    vi.spyOn(el, "getClientRects").mockReturnValue({ length: 1 } as unknown as DOMRectList);
    h = mountBlink(el, { onViewChange: vi.fn(), onPick: vi.fn() });
  });
  afterEach(() => {
    h.destroy();
    delete (HTMLElement.prototype as unknown as Record<string, unknown>).clientWidth;
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    el.remove();
  });

  it("draws one circle per exposure: solid where measured, dashed where predicted, walking west", async () => {
    await h.show(view, frames(), "https://example.test/data/cand/x/frames.json");
    const seen = [after(() => h.track!(marks))];
    expect(sub()).toBe("exposure 1 of 4");
    for (let i = 2; i <= 4; i++) {
      seen.push(after(() => h.step!()));
      expect(sub()).toBe(`exposure ${i} of 4`);
    }
    expect(seen.map((c) => c.length)).toEqual([1, 1, 1, 1]);
    expect(seen.map((c) => c[0].dashed)).toEqual([false, true, true, false]);
    // West is to the right on the plate (east left): x grows, ~1.3 image px (13 screen px) in all.
    const xs = seen.map((c) => c[0].x);
    for (let i = 1; i < 4; i++) expect(xs[i]).toBeGreaterThan(xs[i - 1]);
    expect(xs[3] - xs[0]).toBeGreaterThan(10);
    expect(xs[3] - xs[0]).toBeLessThan(16);
    // PSF-sized at least, never a dot: radius ≥ 8 screen px.
    expect(Math.min(...seen.map((c) => c[0].r))).toBeGreaterThanOrEqual(8);
    // The key under the plate names both strokes.
    expect(el.querySelector(".bk-key .bk-trk")!.textContent).toBe("measuredpredicted");
  });

  it("shows every frame's circle faintly in Stack, the shown one in Difference, none with Target off", async () => {
    await h.show(view, frames(), "https://example.test/data/cand/x/frames.json");
    h.track!(marks);
    const stack = after(() => el.querySelector<HTMLButtonElement>("button[data-mode='stack']")!.click());
    expect(stack.map((c) => c.dashed)).toEqual([false, true, true, false]);
    expect(stack.every((c) => c.alpha < 0.6)).toBe(true);
    const diff = after(() => el.querySelector<HTMLButtonElement>("button[data-mode='difference']")!.click());
    expect(diff).toHaveLength(1);
    expect(diff[0].alpha).toBeGreaterThan(0.9);
    const off = after(() => el.querySelector<HTMLButtonElement>("button[data-ov='target']")!.click());
    expect(off).toHaveLength(0);
    expect(el.querySelector(".bk-key .bk-trk")).toBeNull();
  });

  it("finds the spot through a combined moment's detectors (all wavelengths)", async () => {
    // D2 and D5 exposed together: the plate combines them into one moment, id all-<n>.
    await h.show(view, frames([2, 5]), "https://example.test/data/cand/x/frames.json");
    expect(el.querySelector(".bk-det-label")!.textContent).toContain("D2 and D5");
    const first = after(() => h.track!(marks));
    expect(first).toHaveLength(1);
    expect(first[0].dashed).toBe(false);
    expect(after(() => h.step!())[0].dashed).toBe(true);
  });

  it("clears with null, and show() drops circles made for other frames", async () => {
    await h.show(view, frames(), "https://example.test/data/cand/x/frames.json");
    expect(after(() => h.track!(marks))).toHaveLength(1);
    expect(after(() => h.track!(null))).toHaveLength(0);
    h.track!(marks);
    const other = frames();
    other.epochs = other.epochs.map((e) => ({ ...e, id: e.id.replace("x_", "y_") }));
    rec.strokes.length = 0;
    await h.show(view, other, "https://example.test/data/cand/y/frames.json");
    expect(rec.red()).toHaveLength(0);
  });
});
