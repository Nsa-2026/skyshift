import { earthMaterial, gasOrRockMaterial, type Kind } from './planet-materials';
import { lightingFor, rasterSphere, sphereLookup, type Material, type SphereLighting, type SphereLookup } from './spheres';

export interface PreparedPlanet {
  kind: Kind;
  material: Material;
  map: SphereLookup;
  lighting: SphereLighting;
  pixels: Uint8ClampedArray;
  turns: number;
}
export interface PlanetPreparationRequest { mask: Uint8ClampedArray; earthSize: number }
export type PlanetPreparationReply = { ok: true; planets: PreparedPlanet[] } | { ok: false };

/** The same deterministic materials and rasterizer as the synchronous fallback. */
export function buildPlanetaryPreparation({ mask, earthSize }: PlanetPreparationRequest): PreparedPlanet[] {
  return (['saturn', 'jupiter', 'mars', 'earth'] as const).map(kind => {
    const material = kind === 'earth' ? earthMaterial(mask) : gasOrRockMaterial(kind);
    const map = sphereLookup(kind === 'earth' ? earthSize : kind === 'mars' ? 160 : 224, kind === 'earth' ? .23 : .04);
    const pixels = new Uint8ClampedArray(map.size * map.size * 4);
    const turns = kind === 'earth' ? 22 / 360 : 0;
    rasterSphere(map, material, turns, pixels);
    return { kind, material, map, lighting: lightingFor(map), pixels, turns };
  });
}

export function preparationTransfers(planets: PreparedPlanet[]): ArrayBuffer[] {
  const buffers = new Set<ArrayBuffer>();
  for (const { material, map, lighting, pixels } of planets) {
    for (const array of [material.rgb, material.water, material.clouds, map.u, map.v, map.light,
      map.specular, map.rim, map.coverage, lighting.day, lighting.cloud, pixels]) {
      if (array) buffers.add(array.buffer as ArrayBuffer);
    }
  }
  return [...buffers];
}
