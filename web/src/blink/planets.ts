// Planets on the plate (P4). Near the ecliptic a planet can fill part of a field: Saturn beside the
// ecl35 candidates is a big black disc, and its moons show up as movers. The plate names it. The
// position comes from astronomy-engine (VSOP87, about 1″), loaded only when a plate near the
// ecliptic draws. Astrometric place: geocentric, corrected for light time, no aberration, because
// the photos' WCS is tied to catalogue stars, which carry no aberration either. SPHEREx flies about
// 700 km up; from there a planet sits at most a few arcseconds off its geocentric place (Mars at
// its closest: about 4″), under one pixel.
//
// Not the Moon: from SPHEREx's orbit its place shifts by up to about a degree from the geocentric
// one, so without the spacecraft's position it can't be put on a plate a few arcminutes wide.
// Not Mercury or Venus: SPHEREx looks about 90° from the Sun, and they never get that far from it.
import type { SkyPos } from "../api/types";

export const PLANETS = ["Mars", "Jupiter", "Saturn", "Uranus", "Neptune"] as const;
export type Planet = (typeof PLANETS)[number];

export interface PlanetSpot extends SkyPos {
  name: Planet;
}

/** Furthest these planets get from the ecliptic, degrees (Mars: about 7°). */
export const PLANET_LATITUDE_MAX = 8;
/** Obliquity of the ecliptic, J2000, degrees. */
const OBLIQUITY = 23.4393;
const D = Math.PI / 180;

/** Ecliptic latitude of an RA/Dec (J2000), degrees. */
export function eclipticLatitude(p: SkyPos): number {
  const e = OBLIQUITY * D;
  const s = Math.sin(p.dec * D) * Math.cos(e) - Math.cos(p.dec * D) * Math.sin(e) * Math.sin(p.ra * D);
  return Math.asin(Math.max(-1, Math.min(1, s))) / D;
}

/** Could a planet be within `radiusDeg` of this spot? Cheap: only then is the ephemeris loaded. */
export const nearEcliptic = (p: SkyPos, radiusDeg: number): boolean => Math.abs(eclipticLatitude(p)) <= PLANET_LATITUDE_MAX + radiusDeg;

/** A UTC modified Julian date as a JavaScript Date. */
export const mjdToDate = (mjd: number): Date => new Date((mjd - 40587) * 86_400_000);

/** Angle between two sky positions, degrees. */
export function separation(a: SkyPos, b: SkyPos): number {
  const h =
    Math.sin(((b.dec - a.dec) * D) / 2) ** 2 + Math.cos(a.dec * D) * Math.cos(b.dec * D) * Math.sin(((b.ra - a.ra) * D) / 2) ** 2;
  return (2 * Math.asin(Math.min(1, Math.sqrt(h)))) / D;
}

type Engine = typeof import("astronomy-engine");
let engine: Promise<Engine> | null = null;
let loaded: Engine | null = null;
/** astronomy-engine, imported once on first use (its own chunk, not in the page's bundle). */
export function loadEngine(): Promise<Engine> {
  engine ??= import("astronomy-engine").then(
    (A) => (loaded = A),
    (e) => {
      engine = null;
      throw e;
    },
  );
  return engine;
}
/** The engine if it has loaded already (a plate draws synchronously), else null. */
export const engineNow = (): Engine | null => loaded;

/** Where each planet is at `mjd` (astrometric RA/Dec, degrees), from a loaded engine. */
export function planetSpots(A: Engine, mjd: number): PlanetSpot[] {
  const t = A.MakeTime(mjdToDate(mjd));
  return PLANETS.map((name) => {
    const q = A.EquatorFromVector(A.GeoVector(A.Body[name], t, false));
    return { name, ra: (q.ra * 15 + 360) % 360, dec: q.dec };
  });
}

/** The planets within `radiusDeg` of `c` at `mjd`, nearest first, from a loaded engine. */
export function planetsWithin(A: Engine, c: SkyPos, radiusDeg: number, mjd: number): PlanetSpot[] {
  return planetSpots(A, mjd)
    .filter((p) => separation(p, c) <= radiusDeg)
    .sort((a, b) => separation(a, c) - separation(b, c));
}

/** The planets within `radiusDeg` of `c` at `mjd`, nearest first; far from the ecliptic, none (and nothing loaded). */
export async function planetsNear(c: SkyPos, radiusDeg: number, mjd: number): Promise<PlanetSpot[]> {
  if (!nearEcliptic(c, radiusDeg)) return [];
  return planetsWithin(await loadEngine(), c, radiusDeg, mjd);
}
