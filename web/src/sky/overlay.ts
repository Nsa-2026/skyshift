// Pure geometry for the sky view's overlays: no Aladin, no DOM, so it is tested directly.
// Degrees, arcmin (fields), MJD.
import { gaiaAt, pixelToSky } from "../app/astro";
import type { FramesResponse, GaiaStar, SkyPos } from "../api/types";
import type { View } from "../app/modules";

/** The sky view shows about this many blink fields across. */
export const FIELD_MULTIPLE = 3;

/** Side of the blink field in arcmin: from the frames when loaded (what the blink really shows), else the view's fov. */
export function blinkFieldArcmin(view: Pick<View, "fov">, frames: Pick<FramesResponse, "pixel_arcsec" | "size"> | null): number {
  if (frames && frames.pixel_arcsec > 0 && frames.size[0] > 0) {
    return (Math.max(frames.size[0], frames.size[1]) * frames.pixel_arcsec) / 60;
  }
  return view.fov;
}

/**
 * Aladin's field of view in degrees. Aladin's fov is the map's width, so on a wide map it grows
 * by width/height to keep three blink fields across the short side. Kept to a sensible range.
 */
export function skyFovDeg(fieldArcmin: number, aspect = 1): number {
  const across = Math.min(30, Math.max(0.05, (fieldArcmin * FIELD_MULTIPLE) / 60));
  const a = Number.isFinite(aspect) && aspect > 1 ? aspect : 1;
  return Math.min(60, across * a);
}

/**
 * Corners of the blink field as [ra, dec] pairs (NE, NW, SW, SE order around the square),
 * on the same tangent plane the blink uses, so the outline matches the frame's edge.
 */
export function fieldCorners(
  center: SkyPos,
  fieldArcmin: number,
  frames: Pick<FramesResponse, "pixel_arcsec" | "size"> | null,
): [number, number][] {
  const frame =
    frames && frames.pixel_arcsec > 0 && frames.size[0] > 0
      ? { pixel_arcsec: frames.pixel_arcsec, size: frames.size }
      : { pixel_arcsec: 1, size: [fieldArcmin * 60, fieldArcmin * 60] as [number, number] };
  const [nx, ny] = frame.size;
  const px = [
    { x: -0.5, y: -0.5 },
    { x: nx - 0.5, y: -0.5 },
    { x: nx - 0.5, y: ny - 0.5 },
    { x: -0.5, y: ny - 0.5 },
  ];
  return px.map((p) => {
    const s = pixelToSky(p, frame, center);
    return [s.ra, s.dec];
  });
}

export interface GaiaDot extends SkyPos {
  id: string;
  g: number | null;
}

/** Gaia stars moved from J2016.0 to `mjd` with their proper motion. Faint ones dropped when `maxG` is set. */
export function gaiaDots(gaia: readonly GaiaStar[], mjd: number, maxG?: number): GaiaDot[] {
  const out: GaiaDot[] = [];
  for (const s of gaia) {
    if (maxG != null && s.g != null && s.g > maxG) continue;
    const p = gaiaAt(s, mjd);
    if (!Number.isFinite(p.ra) || !Number.isFinite(p.dec)) continue;
    out.push({ id: s.id, g: s.g, ra: ((p.ra % 360) + 360) % 360, dec: p.dec });
  }
  return out;
}

/**
 * How many Gaia rings the map can show legibly: about one ring per 20 × 20 px, over twice the
 * blink field's area on screen (the Gaia list reaches a little past the field).
 */
export function gaiaBudget(fieldPx: number): number {
  if (!Number.isFinite(fieldPx) || fieldPx <= 0) return 100;
  return Math.round(Math.min(3000, Math.max(30, (2 * fieldPx * fieldPx) / 400)));
}

/** The `n` brightest dots (smallest G; stars without G last). */
export function brightest<T extends { g: number | null }>(dots: T[], n: number): T[] {
  if (dots.length <= n) return dots;
  return [...dots].sort((a, b) => (a.g ?? Infinity) - (b.g ?? Infinity)).slice(0, n);
}

/** The epoch the sky view shows before the blink reports one: the first of the view's detector. */
export function firstEpochMjd(frames: Pick<FramesResponse, "epochs"> | null, det: number): number | null {
  if (!frames || !frames.epochs.length) return null;
  const mine = frames.epochs.filter((e) => e.det === det);
  return (mine[0] ?? frames.epochs[0]).mjd_mid;
}

/**
 * Where the field sits at `mjd`. Featured targets that follow a moving object (comet, asteroids)
 * give each epoch its own centre; take the nearest epoch's. Otherwise the frames' centre.
 */
export function centerAt(frames: Pick<FramesResponse, "epochs" | "center"> | null, mjd: number | null, fallback: SkyPos): SkyPos {
  if (!frames) return fallback;
  if (mjd != null) {
    let best: { d: number; c: SkyPos } | null = null;
    for (const e of frames.epochs) {
      if (!e.center) continue;
      const d = Math.abs(e.mjd_mid - mjd);
      if (!best || d < best.d) best = { d, c: e.center };
    }
    if (best) return best.c;
  }
  return frames.center ?? fallback;
}
