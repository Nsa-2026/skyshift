// "Follow its motion": shift-and-add along a known proper motion. Each epoch is shifted so the
// moving star lands where it was at the middle of the time span; averaging then stacks the mover
// into one dot while the still background smears into short trails.
import type { FramesResponse, GaiaStar, SkyPos } from "../api/types";
import { DAYS_PER_YEAR, GAIA_EPOCH_MJD, gaiaAt, sepArcsec, skyToPixel } from "../app/astro";

/** Same threshold as the fast-star tracks on the image. */
export const FAST_STAR_MAS_YR = 300;
/** The mover must sit this close to the frame centre (featured targets are centred). */
export const MOVER_MAX_SEP_ARCSEC = 60;
/** Below this total drift the shifts are invisible: don't offer the option. */
export const MIN_DRIFT_PX = 0.5;

export interface Mover {
  /** Position and motion in Gaia's form (epoch J2016.0, mas/yr with cos dec). */
  star: GaiaStar;
  source: "gaia" | "published";
  /** Total proper motion, arcsec per year. */
  rate: number;
  /** Where the motion comes from, for the label. */
  ref: string;
}

/**
 * Featured targets Gaia cannot see (too cold to shine in visible light). Motion only: the anchor
 * position is put on the target at the frames' mid-time, since only the shifts between epochs matter.
 */
export const PUBLISHED_MOTION: Record<string, { pmra: number; pmdec: number; ref: string }> = {
  wise0855: { pmra: -8123.7, pmdec: 673.5, ref: "Luhman & Esplin 2016" },
};

/** A pseudo Gaia star that is at `pos` on `mjd` and moves at pmra/pmdec (mas/yr). */
export function starThrough(pos: SkyPos, mjd: number, pmra: number, pmdec: number): GaiaStar {
  const dtYr = (mjd - GAIA_EPOCH_MJD) / DAYS_PER_YEAR;
  const dec = pos.dec - (pmdec * dtYr) / 3.6e6;
  const ra = pos.ra - (pmra * dtYr) / 3.6e6 / Math.cos((dec * Math.PI) / 180);
  return { id: "", ra, dec, pmra, pmdec, parallax: null, g: null };
}

/**
 * The mover to follow: the Gaia star nearest the frame centre (at `mjd`) moving faster than
 * 300 mas/yr, or the published motion of a featured target Gaia lacks. Null if neither.
 */
export function findMover(
  F: Pick<FramesResponse, "gaia" | "center">,
  mjd: number,
  slug: string | null,
  target: SkyPos = F.center,
): Mover | null {
  let best: GaiaStar | null = null;
  let bestSep = MOVER_MAX_SEP_ARCSEC;
  for (const s of F.gaia) {
    const pm = Math.hypot(s.pmra ?? 0, s.pmdec ?? 0);
    if (pm <= FAST_STAR_MAS_YR) continue;
    const sep = sepArcsec(gaiaAt(s, mjd), F.center);
    if (sep < bestSep) {
      best = s;
      bestSep = sep;
    }
  }
  if (best) {
    const rate = Math.hypot(best.pmra ?? 0, best.pmdec ?? 0) / 1000;
    return { star: best, source: "gaia", rate, ref: "Gaia DR3" };
  }
  const pub = slug ? PUBLISHED_MOTION[slug] : undefined;
  if (pub) {
    return {
      star: starThrough(target, mjd, pub.pmra, pub.pmdec),
      source: "published",
      rate: Math.hypot(pub.pmra, pub.pmdec) / 1000,
      ref: pub.ref,
    };
  }
  return null;
}

/**
 * Per-epoch pixel shifts (dx, dy) such that resampling out(x, y) = in(x + dx, y + dy) moves the
 * star from where it was at each epoch to where it was at `alignMjd`.
 */
export function followShifts(
  star: GaiaStar,
  mjds: number[],
  F: Pick<FramesResponse, "pixel_arcsec" | "size">,
  centre: SkyPos,
  alignMjd: number,
): { dx: number; dy: number }[] {
  const a = skyToPixel(gaiaAt(star, alignMjd), F, centre);
  return mjds.map((m) => {
    const p = skyToPixel(gaiaAt(star, m), F, centre);
    return { dx: p.x - a.x, dy: p.y - a.y };
  });
}

/** Largest distance between any two shifts, in pixels. */
export function driftPx(shifts: { dx: number; dy: number }[]): number {
  let d = 0;
  for (const a of shifts) for (const b of shifts) d = Math.max(d, Math.hypot(a.dx - b.dx, a.dy - b.dy));
  return d;
}
