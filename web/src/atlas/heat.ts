// The heat map: candidates binned into HEALPix cells, a single-hue ramp by count, and the
// searched zones as boxes round their candidates. Pure.
import type { Candidate, CandidateKind, SkyPos } from "../api/types";
import { ang2pixNest, boundariesNest, pix2angNest } from "./healpix";
import { meanPosition, wrap360 } from "./sphere";

export interface Cell {
  order: number;
  pix: number;
  count: number;
  center: SkyPos;
  byKind: Record<CandidateKind, number>;
}

/** Bin candidates of the shown kinds into cells of one order; only cells with candidates appear. */
export function binCandidates(cands: readonly Candidate[], order: number, kinds?: readonly CandidateKind[]): Cell[] {
  const shown = kinds ? new Set(kinds) : null;
  const cells = new Map<number, Cell>();
  for (const c of cands) {
    if (shown && !shown.has(c.kind)) continue;
    if (!Number.isFinite(c.ra) || !Number.isFinite(c.dec)) continue;
    const pix = ang2pixNest(order, c.ra, c.dec);
    let cell = cells.get(pix);
    if (!cell) {
      const [ra, dec] = pix2angNest(order, pix);
      cell = { order, pix, count: 0, center: { ra, dec }, byKind: { mover: 0, high_pm: 0, spectral: 0, shape: 0 } };
      cells.set(pix, cell);
    }
    cell.count++;
    if (c.kind in cell.byKind) cell.byKind[c.kind]++;
  }
  return [...cells.values()].sort((a, b) => b.count - a.count || a.pix - b.pix);
}

/** Outline of a cell, smooth enough to draw: `step` points per edge. */
export function cellOutline(cell: Pick<Cell, "order" | "pix">, step = 3): [number, number][] {
  return boundariesNest(cell.order, cell.pix, step);
}

/**
 * Ramp steps: counts at which the fill gets one step darker, 1–3–10–30–100… up to the largest
 * count. Always starts at 1 and has at least one step.
 */
export function rampSteps(max: number): number[] {
  const steps = [1];
  const seq = [3, 10];
  let k = 0;
  for (;;) {
    const next = seq[k % 2] * 10 ** Math.floor(k / 2);
    if (next > max) break;
    steps.push(next);
    k++;
  }
  return steps;
}

/** Which step a count falls on (0 = the faintest), or −1 for no candidates. */
export function rampIndex(count: number, steps: readonly number[]): number {
  if (!(count >= 1)) return -1;
  let i = 0;
  while (i + 1 < steps.length && count >= steps[i + 1]) i++;
  return i;
}

/** Fill opacity for a step: from a light wash (0.16) to a dense smudge (0.78). */
export function rampAlpha(index: number, nSteps: number): number {
  if (index < 0) return 0;
  if (nSteps <= 1) return 0.55;
  return 0.16 + (0.62 * index) / (nSteps - 1);
}

/** Legend text for each step: "1–2", "3–9", "10 or more". */
export function rampLabels(steps: readonly number[]): string[] {
  return steps.map((s, i) => {
    const next = steps[i + 1];
    if (next == null) return s === 1 ? "1 or more" : `${s} or more`;
    return next - 1 === s ? `${s}` : `${s}–${next - 1}`;
  });
}

export interface ZoneBox {
  zone: string;
  count: number;
  center: SkyPos;
  /** RA range, degrees, going east from ra0 to ra1 (ra1 may pass 360). */
  ra0: number;
  ra1: number;
  dec0: number;
  dec1: number;
}

/**
 * A box round each zone's candidates, padded by `padDeg`. RA wraps: the box spans the complement
 * of the widest empty gap in RA, so a zone across RA 0 gets a narrow box, not a 360° band.
 */
export function zoneBoxes(cands: readonly Candidate[], padDeg = 0.4): ZoneBox[] {
  const byZone = new Map<string, Candidate[]>();
  for (const c of cands) {
    if (!Number.isFinite(c.ra) || !Number.isFinite(c.dec)) continue;
    const list = byZone.get(c.zone) ?? [];
    list.push(c);
    byZone.set(c.zone, list);
  }
  const out: ZoneBox[] = [];
  for (const [zone, list] of byZone) {
    const ras = list.map((c) => wrap360(c.ra)).sort((a, b) => a - b);
    let gap = -1;
    let at = 0;
    for (let i = 0; i < ras.length; i++) {
      const next = i + 1 < ras.length ? ras[i + 1] : ras[0] + 360;
      if (next - ras[i] > gap) {
        gap = next - ras[i];
        at = i;
      }
    }
    // The box starts after the widest gap and runs east to just before it.
    let ra0 = ras[(at + 1) % ras.length];
    let ra1 = ras[at] < ra0 ? ras[at] + 360 : ras[at];
    const decs = list.map((c) => c.dec);
    const dec0 = Math.max(-90, Math.min(...decs) - padDeg);
    const dec1 = Math.min(90, Math.max(...decs) + padDeg);
    const midDec = Math.max(Math.abs(dec0), Math.abs(dec1));
    const raPad = padDeg / Math.max(0.05, Math.cos((Math.min(89, midDec) * Math.PI) / 180));
    ra0 -= raPad;
    ra1 += raPad;
    if (ra1 - ra0 >= 360) {
      ra0 = 0;
      ra1 = 360;
    }
    out.push({ zone, count: list.length, center: meanPosition(list) ?? list[0], ra0, ra1, dec0, dec1 });
  }
  return out.sort((a, b) => b.count - a.count);
}

/** Outline of a zone box: its four edges, with points every `stepDeg` so it bends with the sky. */
export function zoneOutline(b: ZoneBox, stepDeg = 1): [number, number][] {
  const pts: [number, number][] = [];
  const nRa = Math.max(1, Math.ceil((b.ra1 - b.ra0) / stepDeg));
  const nDec = Math.max(1, Math.ceil((b.dec1 - b.dec0) / stepDeg));
  for (let i = 0; i <= nRa; i++) pts.push([wrap360(b.ra0 + ((b.ra1 - b.ra0) * i) / nRa), b.dec1]);
  for (let i = 1; i <= nDec; i++) pts.push([wrap360(b.ra1), b.dec1 - ((b.dec1 - b.dec0) * i) / nDec]);
  for (let i = 1; i <= nRa; i++) pts.push([wrap360(b.ra1 - ((b.ra1 - b.ra0) * i) / nRa), b.dec0]);
  for (let i = 1; i < nDec; i++) pts.push([wrap360(b.ra0), b.dec0 + ((b.dec1 - b.dec0) * i) / nDec]);
  return pts;
}

/**
 * The zone label's anchor: the box's south-east corner (bottom left on a sky map, east is left).
 * Cell counts sit at cell centres, which crowd the busy middle; under the box is usually quiet.
 */
export function zoneLabelAnchor(b: ZoneBox): SkyPos {
  return { ra: wrap360(b.ra1), dec: b.dec0 };
}

/**
 * On the whole sky a zone is a small blot of order-3 cells (~7°): its label goes beside it, on the
 * east side (screen left), clear of the cells that stick out of the box by up to half a cell.
 */
export function zoneSideAnchor(b: ZoneBox, clearDeg = 3.5): SkyPos {
  const dec = (b.dec0 + b.dec1) / 2;
  const cos = Math.max(0.05, Math.cos((dec * Math.PI) / 180));
  return { ra: wrap360(b.ra1 + clearDeg / cos), dec };
}

/** True when the zone's centre lies in the box of a busier zone (the pilot inside the hunt). */
export function isNested(b: ZoneBox, all: readonly ZoneBox[]): boolean {
  return all.some((o) => {
    if (o === b || o.count < b.count) return false;
    if (b.center.dec < o.dec0 || b.center.dec > o.dec1) return false;
    let ra = wrap360(b.center.ra);
    if (ra < o.ra0) ra += 360;
    return ra <= o.ra1;
  });
}
