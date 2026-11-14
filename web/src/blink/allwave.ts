// "All wavelengths": every detector's frame of one moment combined into one grey plate. Each photo
// sees a star at one wavelength per detector (the linear variable filters), so one detector's frame
// can miss a star that another shows; the pipeline already links dots across all six. Pure: no DOM.
//
// Months: the same visit in every detector (their mid-times within a few weeks). Hours: the
// detectors exposed at the same moment (D1+D4, D2+D5, D3+D6); exposures at different times stay
// separate frames. Each detector is put in units of its own sky noise before the sum, so none
// dominates and faint stars gain depth.
import type { Clock, Det, Epoch, Measured, MeasuredDetection, MeasuredTrack } from "../api/types";

export interface WaveGroup {
  /** The combined epoch the player shows: id `all-<n>`, the members' time and wavelength span. */
  epoch: Epoch;
  /** The detector frames combined into it, by detector. */
  members: Epoch[];
}

/** Months: visits of different detectors closer than this (days) are one visit. */
const VISIT_DAYS = 45;
/** Hours: exposures closer than this (days, 30 s) are one moment. */
const MOMENT_DAYS = 30 / 86400;

/**
 * Group the epochs of every detector into moments, in time order. Hours keeps only the moments of
 * `det`'s visit (live answers may pick another visit for another detector).
 */
export function groupEpochs(epochs: Epoch[], clock: Clock, det: Det): WaveGroup[] {
  let list = [...epochs].sort((a, b) => a.mjd_mid - b.mjd_mid);
  const tol = clock === "months" ? VISIT_DAYS : MOMENT_DAYS;
  if (clock === "hours") {
    const mine = list.filter((e) => e.det === det);
    if (mine.length) {
      const lo = mine[0].mjd_mid - 0.5;
      const hi = mine[mine.length - 1].mjd_mid + 0.5;
      list = list.filter((e) => e.mjd_mid >= lo && e.mjd_mid <= hi);
    }
  }
  const groups: Epoch[][] = [];
  for (const e of list) {
    const g = groups[groups.length - 1];
    if (g && e.mjd_mid - g[0].mjd_mid <= tol && !g.some((x) => x.det === e.det)) g.push(e);
    else groups.push([e]);
  }
  return groups.map((members, k) => {
    members.sort((a, b) => a.det - b.det);
    const first = members[0];
    const mjd = clock === "months" ? members.reduce((s, e) => s + e.mjd_mid, 0) / members.length : first.mjd_mid;
    const epoch: Epoch = {
      ...first,
      id: `all-${k + 1}`,
      det,
      mjd_mid: mjd,
      mjd_start: Math.min(...members.map((e) => e.mjd_start)),
      mjd_end: Math.max(...members.map((e) => e.mjd_end)),
      um_min: Math.min(...members.map((e) => e.um_min)),
      um_max: Math.max(...members.map((e) => e.um_max)),
      n_exposures: members.reduce((s, e) => s + e.n_exposures, 0),
      flag_frac: members.reduce((s, e) => s + e.flag_frac, 0) / members.length,
      sat_frac: Math.max(...members.map((e) => e.sat_frac)),
    };
    return { epoch, members };
  });
}

export interface WavePart {
  /** MJy/sr, NaN where there is no data or the core is burnt. */
  vals: Float32Array;
  /** 1 where a NaN is a saturated (burnt) core, not missing data. */
  burnt: Uint8Array | null;
  /** Sky level and noise (robust σ) of this frame. */
  sky: number;
  sigma: number;
}

/**
 * The combined plate: each detector in units of its own noise, (value − sky) / σ, summed and divided
 * by √(detectors with data there), so the sky noise stays 1 everywhere. NaN where no detector has
 * data, and where any detector's core is burnt (so the plate still shows a saturated star as ink).
 */
export function combineFrames(parts: WavePart[]): Float32Array {
  const n = parts[0]?.vals.length ?? 0;
  const out = new Float32Array(n);
  const ok = parts.filter((p) => p.vals.length === n && p.sigma > 0);
  for (let i = 0; i < n; i++) {
    let sum = 0;
    let k = 0;
    let burnt = false;
    for (const p of ok) {
      const v = p.vals[i];
      if (v === v) {
        sum += (v - p.sky) / p.sigma;
        k++;
      } else if (p.burnt?.[i]) burnt = true;
    }
    out[i] = burnt || k === 0 ? NaN : sum / Math.sqrt(k);
  }
  return out;
}

/** Several detectors' measured layers (live answers come one detector at a time) as one. */
export function mergeMeasured(list: (Measured | null | undefined)[]): Measured | null {
  const ms = list.filter((m): m is Measured => !!m && typeof m === "object");
  if (ms.length <= 1) return ms[0] ?? null;
  const [m0] = ms;
  const counts = { ...m0.counts };
  for (const m of ms.slice(1)) for (const k of Object.keys(counts) as (keyof typeof counts)[]) counts[k] += m.counts?.[k] ?? 0;
  const tracks = new Map<string, MeasuredTrack>();
  for (const m of ms)
    for (const t of m.tracks ?? []) {
      const had = tracks.get(t.id);
      tracks.set(t.id, had ? { ...had, dots: [...had.dots, ...t.dots].sort((a, b) => a.mjd - b.mjd) } : t);
    }
  const shifts = new Map(ms.flatMap((m) => (m.shifts ?? []).map((s) => [s.source_id, s] as const)));
  return {
    ...m0,
    photos: ms.reduce((s, m) => s + (m.photos ?? 0), 0),
    photos_missing: ms.reduce((s, m) => s + (m.photos_missing ?? 0), 0),
    truncated: ms.some((m) => m.truncated),
    counts,
    detections: ms.flatMap((m) => m.detections ?? []),
    tracks: [...tracks.values()],
    shifts: [...shifts.values()].sort((a, b) => b.pm_snr - a.pm_snr),
  };
}

/** Two detections of one moment closer than this (arcsec, half the PSF's FWHM) are one source. */
export const SAME_SOURCE_ARCSEC = 3.75;
/** From two different detectors, closer than one PSF FWHM (arcsec): one source seen twice. */
export const OTHER_DET_ARCSEC = 7.53;
const CLASS_RANK: Record<string, number> = { suspect: 0, known: 1, mover: 2, star: 3, other: 4, artifact: 5 };

/**
 * The measured layer on the combined plate: each detection moved to its moment's combined epoch,
 * one per source and moment, each track's dots one per moment (a D2 and a D5 dot of the same moment
 * are colour, not motion). One source is: the same pipeline source, the same Gaia star, dots under
 * 3.75″ apart, or dots of two detectors under one PSF FWHM apart (each detector's centroid, and its
 * Gaia match, can land on a different side of a blend). The one kept is the most
 * telling class (suspect, known, mover, star, other, artefact), then the best S/N. Shifts as they are.
 */
export function remapMeasured(m: Measured, groups: WaveGroup[]): Measured {
  const groupOf = new Map<string, string>();
  for (const g of groups) for (const e of g.members) groupOf.set(e.id, g.epoch.id);
  const detOf = new Map<MeasuredDetection, number>();
  const memberDet = new Map(groups.flatMap((g) => g.members.map((e) => [e.id, e.det] as const)));
  const best = new Map<string, MeasuredDetection>();
  const loose: MeasuredDetection[] = [];
  for (const d of m.detections ?? []) {
    const id = groupOf.get(d.epoch_id);
    if (!id) continue;
    const moved = { ...d, epoch_id: id };
    detOf.set(moved, memberDet.get(d.epoch_id) ?? 0);
    if (d.source_id == null) {
      loose.push(moved);
      continue;
    }
    const k = `${id}|${d.source_id}`;
    const had = best.get(k);
    if (!had || d.snr > had.snr) best.set(k, moved);
  }
  const ranked = [...best.values(), ...loose].sort(
    (a, b) => a.epoch_id.localeCompare(b.epoch_id) || (CLASS_RANK[a.class] ?? 9) - (CLASS_RANK[b.class] ?? 9) || b.snr - a.snr,
  );
  const detections: MeasuredDetection[] = [];
  let from = 0; // first kept detection of this moment
  for (const d of ranked) {
    if (detections[from]?.epoch_id !== d.epoch_id) from = detections.length;
    const cosd = Math.cos((d.dec * Math.PI) / 180);
    let dup = false;
    for (let k = from; k < detections.length && !dup; k++) {
      const o = detections[k];
      const r = Math.hypot((d.ra - o.ra) * cosd, d.dec - o.dec) * 3600;
      dup =
        (d.gaia_id != null && d.gaia_id === o.gaia_id) ||
        r < SAME_SOURCE_ARCSEC ||
        (r < OTHER_DET_ARCSEC && detOf.get(d) !== detOf.get(o));
    }
    if (!dup) detections.push(d);
  }
  detections.sort((a, b) => a.epoch_id.localeCompare(b.epoch_id) || b.snr - a.snr);
  const tracks = (m.tracks ?? []).map((t) => {
    const seen = new Set<string>();
    const dots = t.dots
      .map((d) => ({ ...d, epoch_id: groupOf.get(d.epoch_id) ?? "" }))
      .filter((d) => d.epoch_id && !seen.has(d.epoch_id) && seen.add(d.epoch_id))
      .sort((a, b) => a.mjd - b.mjd);
    return { ...t, dots };
  });
  return { ...m, detections, tracks, shifts: m.shifts ?? [] };
}

/**
 * The plate label's count, from the detectors really combined in a moment: "4 of 6 detectors" when
 * some are missing (months: all six; hours: the pair exposed together); "" when all are there.
 */
export function detectorsNote(n: number, clock: Clock): string {
  const of = clock === "months" ? 6 : 2;
  return n > 0 && n < of ? `${n} of ${of} detectors` : "";
}
