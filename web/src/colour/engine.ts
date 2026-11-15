// Reads what the filters need for one field of the SPHEREx all-sky cube and keeps it, so switching
// filters on the same field reads nothing twice. Algorithms: rgb.ts; checks: docs/colour-layers.md.
import { CUBE_UM } from "../sky/layers";
import { framesIn, type Preset } from "./bands";
import { BLANK_FRAMES, maskPlan, planField, pool, readTileInfo, sampleInt, samplePlane, type FieldPlan, type Fetcher, type TileInfo } from "./cube";
import { brightMask, compositionFrames, measureComposition, type Composition } from "./composition";
import { deficitPlanes, medianPlanes, saturatedMask, toChannel, type Channel } from "./rgb";

/** Requests in flight at once. */
const PARALLEL = 8;

export interface ColourPicture {
  plan: FieldPlan;
  r: Channel;
  g: Channel;
  b: Channel;
  saturated: Uint8Array;
  /** Exposures per colour window, shortest wavelength first, for "why the colour isn't reliable". */
  byWindow: { um: number; hits: Int32Array[] }[];
  dates: { beg: string | null; end: string | null };
}

export interface WavelengthPicture {
  plan: FieldPlan;
  /** Background-subtracted median of the frames used, MJy/sr. */
  values: Float32Array;
  /** The frames used: the chosen one and its neighbours in the same detector. */
  frames: number[];
  /** Fixed for the field (from the 2.0–2.3 µm window), so brightness changes along the slider are real. */
  scale: { sigma: number; max: number };
  dates: { beg: string | null; end: string | null };
}

export interface CompositionPicture {
  plan: FieldPlan;
  result: Composition;
  dates: { beg: string | null; end: string | null };
}

/** The chosen frame and its neighbours on each side within the same detector, never a blank frame. */
export function neighbourFrames(frame: number): number[] {
  const det = Math.floor(frame / 17);
  return [frame - 1, frame, frame + 1].filter(
    (f) => f >= 0 && f < CUBE_UM.length && Math.floor(f / 17) === det && !BLANK_FRAMES.has(f),
  );
}

const REFERENCE = { lo: 2.0, hi: 2.3 };

export class FieldReader {
  private plan: Promise<FieldPlan> | null = null;
  private images = new Map<number, Float32Array>();
  private hits = new Map<number, Int32Array>();
  private reference: { sigma: number; max: number } | null = null;
  private compositionResult: CompositionPicture | null = null;

  constructor(
    readonly center: { ra: number; dec: number },
    readonly fovArcmin: number,
    private toSky: (x: number, y: number, n: number, pixelArcsec: number) => { ra: number; dec: number },
    private fetcher: Fetcher,
    private infos: Map<number, Promise<TileInfo>>,
  ) {}

  private info(t: number, signal?: AbortSignal): Promise<TileInfo> {
    let p = this.infos.get(t);
    if (!p) {
      p = readTileInfo(t, this.fetcher, signal);
      p.catch(() => this.infos.delete(t));
      this.infos.set(t, p);
    }
    return p;
  }

  private getPlan(signal?: AbortSignal): Promise<FieldPlan> {
    if (!this.plan) {
      this.plan = planField(this.fovArcmin, this.toSky, (t) => this.info(t, signal), true);
      this.plan.catch(() => (this.plan = null));
    }
    return this.plan;
  }

  /** Read every frame (and optionally its exposure counts) not read yet. */
  private async read(frames: number[], withHits: boolean, signal: AbortSignal, progress: (done: number, total: number) => void) {
    const plan = await this.getPlan(signal);
    const jobs: (() => Promise<void>)[] = [];
    for (const f of frames) {
      if (!this.images.has(f)) {
        jobs.push(async () => {
          const v = await samplePlane(plan, f, this.fetcher, signal);
          if (!signal.aborted) this.images.set(f, v);
        });
      }
      if (withHits && !this.hits.has(f)) {
        jobs.push(async () => {
          const v = await sampleInt(plan, "NHITS", f, this.fetcher, signal);
          if (!signal.aborted) this.hits.set(f, v);
        });
      }
    }
    let done = 0;
    progress(0, jobs.length);
    await pool(jobs, PARALLEL, () => progress(++done, jobs.length));
    return plan;
  }

  private dates(plan: FieldPlan) {
    const infos = plan.tiles.map((t) => t.info);
    const begs = infos.map((i) => i.dateBeg).filter((d): d is string => !!d).sort();
    const ends = infos.map((i) => i.dateEnd).filter((d): d is string => !!d).sort();
    return { beg: begs[0] ?? null, end: ends.at(-1) ?? null };
  }

  async colour(preset: Preset, signal: AbortSignal, progress: (done: number, total: number) => void): Promise<ColourPicture> {
    const windows = [preset.red, preset.green, preset.blue];
    const wanted = windows.map(framesIn);
    const plan = await this.read([...new Set(wanted.flat())], true, signal, progress);
    const [r, g, b] = wanted.map((fs) => toChannel(medianPlanes(fs.map((f) => this.images.get(f)!))));
    const all = [...new Set(wanted.flat())];
    const sig = Math.sqrt(r.stats.sigma ** 2 + g.stats.sigma ** 2 + b.stats.sigma ** 2) / 3;
    const saturated = saturatedMask(deficitPlanes(all.map((f) => this.hits.get(f)!), plan.n), r, g, b, sig, plan.n);
    const byWindow = [preset.blue, preset.green, preset.red].map((w, i) => ({
      um: (w.lo + w.hi) / 2,
      hits: wanted[2 - i].map((f) => this.hits.get(f)!),
    }));
    return { plan, r, g, b, saturated, byWindow, dates: this.dates(plan) };
  }

  /**
   * Layer A3: read the 83 frames the indices use, then the exposure counts around bright stars
   * only (brightMask: that is where exposures are lost, and it halves the download), then measure.
   * `measuring` is called once the reading is done, before the (1–2 s) measurement blocks the page.
   */
  async composition(
    signal: AbortSignal,
    progress: (done: number, total: number) => void,
    measuring?: () => Promise<void>,
  ): Promise<CompositionPicture> {
    if (!this.compositionResult) {
      const frames = compositionFrames();
      const plan = await this.read(frames, false, signal, (done, total) => progress(done, total + 1));
      if (signal.aborted) throw new DOMException("aborted", "AbortError");
      const images = new Map(frames.map((f) => [f, this.images.get(f)!]));
      const keep = brightMask(images, plan.n);
      const hits = new Map<number, Int32Array>();
      if (keep.some((v) => v)) {
        const near = maskPlan(plan, keep);
        await pool(
          frames.map((f) => async () => {
            const h = await sampleInt(near, "NHITS", f, this.fetcher, signal);
            for (let k = 0; k < h.length; k++) if (!keep[k]) h[k] = -1; // not read
            hits.set(f, h);
          }),
          PARALLEL,
        );
      } else {
        const none = new Int32Array(plan.n * plan.n).fill(-1);
        for (const f of frames) hits.set(f, none);
      }
      if (signal.aborted) throw new DOMException("aborted", "AbortError");
      progress(frames.length + 1, frames.length + 1);
      await measuring?.();
      this.compositionResult = { plan, result: measureComposition({ n: plan.n, images, hits }), dates: this.dates(plan) };
    }
    return this.compositionResult;
  }

  async wavelength(frame: number, signal: AbortSignal, progress: (done: number, total: number) => void): Promise<WavelengthPicture> {
    const use = neighbourFrames(frame);
    const ref = framesIn(REFERENCE);
    const plan = await this.read([...new Set([...use, ...ref])], false, signal, progress);
    if (!this.reference) {
      const k = toChannel(medianPlanes(ref.map((f) => this.images.get(f)!)));
      const sorted = Array.from(k.values).filter(Number.isFinite).sort((a, b) => a - b);
      const p995 = sorted[Math.floor(0.995 * (sorted.length - 1))] ?? 1;
      this.reference = { sigma: k.stats.sigma, max: Math.max(p995, 5 * k.stats.sigma) };
    }
    const ch = toChannel(medianPlanes(use.map((f) => this.images.get(f)!)));
    return { plan, values: ch.values, frames: use, scale: this.reference, dates: this.dates(plan) };
  }
}
