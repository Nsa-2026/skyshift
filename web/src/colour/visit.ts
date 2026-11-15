// Colour pictures that move with the blink: one picture per visit, built from the blink's own
// visit stacks of three detectors (the same frames the blink shows, 2.05″ or 3.08″ pixels).
// Each detector's frame is levelled and its noise measured like a cube window (rgb.ts toChannel),
// then composed with the same Lupton algorithm. Checked 14 Nov on the featured targets' Months 5′
// frames: no pixel at −5σ in any frame (they are already medians over the visit's exposures).
//
// What differs from the still picture: each visit's frame of a detector covers a slightly different
// range of wavelengths (e.g. WISE 0855's D6 saw 4.85–5.01 µm in visit 1 and 4.41–4.99 µm in visit 2),
// so colour changes between visits can come from that rather than from the object. The key shows
// each visit's ranges and says to compare motion, not small colour changes.
import { DEFAULT_FOV, epochsFor, loadFrameData, loadFrames, toMJySr } from "../api/client";
import type { Det, Epoch, FramesResponse } from "../api/types";
import type { View } from "../app/modules";
import type { PresetId } from "./bands";
import { toChannel, type Channel } from "./rgb";

/** Detectors behind red, green and blue, per filter. */
export const VISIT_DETS: Record<PresetId, [Det, Det, Det]> = {
  cold: [6, 3, 2], // 4.4–5.0, 1.6–2.4, 1.1–1.6 µm
  nir: [3, 2, 1], // 1.6–2.4, 1.1–1.6, 0.75–1.1 µm
};

/** Visit number of a Months epoch id ("d6-v2" → 2), or null for an Hours exposure. */
export function visitOf(id: string): number | null {
  const m = /-v(\d+)$/.exec(id);
  return m ? Number(m[1]) : null;
}

export interface VisitPicture {
  visit: number;
  mjd: number;
  /** Red, green, blue; null when SPHEREx has no frame of that detector for this visit. */
  channels: (Channel | null)[];
  /** Wavelength range each colour saw in this visit, µm; null with its channel. */
  ranges: ({ min: number; max: number } | null)[];
}

export interface VisitSet {
  preset: PresetId;
  size: [number, number];
  pixelArcsec: number;
  visits: Map<number, VisitPicture>;
}

/**
 * Read the three detectors' Months frames for this view and build a channel per visit and colour.
 * Throws when the frames can't be had (Hours clock, or a detector the source doesn't have).
 */
export async function loadVisits(
  view: View,
  preset: PresetId,
  signal: AbortSignal,
  progress: (done: number, total: number) => void,
): Promise<VisitSet> {
  if (view.clock !== "months") throw new Error("colours that move need the Months view");
  const dets = VISIT_DETS[preset];
  const load = (slug: string | null | undefined) =>
    Promise.all(dets.map((det) => loadFrames({ ra: view.ra, dec: view.dec, clock: "months", fov: view.fov, det, slug }, signal)));
  let sets = await load(view.slug);
  // A featured target's saved file may lack a detector at this field (most 15′ files hold only D2
  // and D5): the static loader then hands back that file anyway. Ask the server for all three, so
  // they share one grid.
  const lacking = () => dets.filter((d, i) => epochsFor(sets[i].frames, d).length === 0);
  if (view.slug && lacking().length) {
    const saved = lacking();
    try {
      sets = await load(null);
    } catch (e) {
      if ((e as Error).name === "AbortError") throw e;
      throw new Error(
        `the saved ${view.fov === DEFAULT_FOV.months ? "close-up" : "wide"} view has no ${saved.map((d) => `D${d}`).join(" or ")} frames and the server couldn't make them`,
      );
    }
  }
  const first = sets[0].frames;
  const perDet: { epochs: Epoch[]; frames: FramesResponse; jsonUrl: string }[] = sets.map((s, i) => {
    if (s.frames.size[0] !== first.size[0] || s.frames.pixel_arcsec !== first.pixel_arcsec) {
      throw new Error("the detectors' frames don't share one grid");
    }
    return { epochs: epochsFor(s.frames, dets[i]), frames: s.frames, jsonUrl: s.jsonUrl };
  });
  const missing = dets.filter((_, i) => perDet[i].epochs.length === 0);
  if (missing.length) throw new Error(`this view has no frames from detector ${missing.map((d) => `D${d}`).join(" or ")}`);
  const visitNumbers = [...new Set(perDet.flatMap((d) => d.epochs.map((e) => visitOf(e.id))).filter((v): v is number => v != null))].sort(
    (a, b) => a - b,
  );
  const jobs = visitNumbers.flatMap((v) => perDet.map((d, c) => ({ v, c, e: d.epochs.find((e) => visitOf(e.id) === v), jsonUrl: d.jsonUrl })));
  let done = 0;
  progress(0, jobs.length);
  const data = await Promise.all(
    jobs.map(async (j) => {
      const ch = j.e ? toChannel(toMJySr(await loadFrameData(j.e, j.jsonUrl, signal), j.e)) : null;
      progress(++done, jobs.length);
      return { ...j, ch };
    }),
  );
  const visits = new Map<number, VisitPicture>();
  for (const v of visitNumbers) {
    const mine = data.filter((d) => d.v === v);
    const byC = [0, 1, 2].map((c) => mine.find((d) => d.c === c));
    const mjds = byC.map((d) => d?.e?.mjd_mid).filter((m): m is number => m != null);
    visits.set(v, {
      visit: v,
      mjd: mjds.reduce((a, b) => a + b, 0) / Math.max(1, mjds.length),
      channels: byC.map((d) => d?.ch ?? null),
      ranges: byC.map((d) => (d?.e ? { min: d.e.um_min, max: d.e.um_max } : null)),
    });
  }
  return { preset, size: first.size, pixelArcsec: first.pixel_arcsec, visits };
}
