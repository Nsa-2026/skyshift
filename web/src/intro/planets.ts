import { smooth, type SceneState } from './model';
import { noise, rasterSphere, sphereLookup, restoreSphereLighting, type Material } from './spheres';
import { earthMaterial, gasOrRockMaterial, type Kind } from './planet-materials';
import { createEarthMask } from './planet-mask';
import type { PreparedPlanet } from './planet-preparation';

const TAU = Math.PI * 2;
const materials = new Map<Kind, Material>();
const sprites = new Map<string, { canvas: HTMLCanvasElement; pixels: ImageData; map: ReturnType<typeof sphereLookup>; turns: number }>();
const pending = new Map<number, Set<symbol>>();

export const earthSpriteSize = (w: number, h: number) => Math.min(w, h) < 600 ? 384 : 768;
export function planetaryCacheReady(size: number): boolean {
  return ['saturn:224', 'jupiter:224', 'mars:160', `earth:${size}`].every(key => sprites.has(key));
}

/** Defer only the requested size, without blocking the portrait or the rest of the story. */
export function deferPlanetaryPreparation(size: number): () => void {
  const token = Symbol();
  const jobs = pending.get(size) ?? new Set<symbol>();
  jobs.add(token); pending.set(size, jobs);
  return () => { jobs.delete(token); if (!jobs.size) pending.delete(size); };
}

/** Install transferred pixels atomically, so a resource failure leaves fallback caches intact. */
export function installPreparedPlanets(prepared: PreparedPlanet[], earthSize: number): void {
  const expected = new Map<Kind, number>([['earth', earthSize], ['saturn', 224], ['jupiter', 224], ['mars', 160]]);
  if (prepared.length !== expected.size) throw new Error('Incomplete planetary preparation');
  const next: Array<{ planet: PreparedPlanet; sprite: NonNullable<ReturnType<typeof sprites.get>> }> = [];
  for (const planet of prepared) {
    const size = expected.get(planet.kind);
    if (!size || planet.map.size !== size || planet.pixels.length !== size * size * 4) throw new Error('Invalid planetary preparation');
    expected.delete(planet.kind);
    const canvas = document.createElement('canvas'); canvas.width = size; canvas.height = size;
    const c = canvas.getContext('2d');
    if (!c) throw new Error('Canvas resources unavailable');
    const pixels = c.createImageData(size, size);
    pixels.data.set(planet.pixels);
    c.putImageData(pixels, 0, 0);
    next.push({ planet, sprite: { canvas, pixels, map: planet.map, turns: planet.turns } });
  }
  for (const { planet, sprite } of next) {
    materials.set(planet.kind, planet.material);
    restoreSphereLighting(planet.map, planet.lighting);
    sprites.set(`${planet.kind}:${planet.map.size}`, sprite);
  }
}
function sphereSprite(kind: Kind, size: number, turns: number): HTMLCanvasElement {
  let material = materials.get(kind);
  if (!material) { material = kind === 'earth' ? earthMaterial(createEarthMask()) : gasOrRockMaterial(kind); materials.set(kind, material); }
  const key = `${kind}:${size}`;
  let sprite = sprites.get(key);
  if (!sprite) {
    const canvas = document.createElement('canvas'); canvas.width = size; canvas.height = size;
    sprite = { canvas, pixels: canvas.getContext('2d')!.createImageData(size, size), map: sphereLookup(size, kind === 'earth' ? .23 : .04), turns: NaN };
    sprites.set(key, sprite);
  }
  if (sprite.turns !== turns) {
    rasterSphere(sprite.map, material, turns, sprite.pixels.data);
    sprite.canvas.getContext('2d')!.putImageData(sprite.pixels, 0, 0);
    sprite.turns = turns;
  }
  return sprite.canvas;
}

/** Positions are scenic composition, not a representation of measured planetary positions. */
export function orbitalLayout(w: number, h: number, p: number) {
  const m = Math.min(w, h), dive = smooth(p, 0, .28), retreat = smooth(p, 0, .25);
  return {
    earth: { x: w * (.74 - .24 * dive), y: h * (.76 + .43 * dive), r: m * .19 * Math.exp(dive * 2.9) },
    planets: [
      { kind: 'saturn' as const, x: w * (.18 - retreat * .17), y: h * (.23 - retreat * .18), r: m * .058 * (1 + retreat * .3) },
      { kind: 'jupiter' as const, x: w * (.84 + retreat * .20), y: h * (.25 - retreat * .19), r: m * .066 * (1 + retreat * .2) },
      { kind: 'mars' as const, x: w * (.15 - retreat * .18), y: h * (.72 + retreat * .06), r: m * .033 * (1 + retreat * .45) },
    ],
    alpha: 1 - smooth(p, .10, .24), turns: 22 / 360 + dive * .07,
  };
}

function rings(c: CanvasRenderingContext2D, r: number, front: boolean) {
  c.save(); c.scale(1, .34);
  const light = c.createLinearGradient(-r * 2.3, 0, r * 2.3, 0);
  light.addColorStop(0, '#e8dec2'); light.addColorStop(.5, '#bcae8e'); light.addColorStop(1, '#766c57');
  c.strokeStyle = light;
  for (let i = 0; i < 70; i++) {
    const rho = 1.24 + i / 69 * 1.02;
    if (rho > 1.83 && rho < 1.92) continue; // Cassini-like gap; illustration, not ring measurements.
    const density = rho < 1.5 ? .28 : .6 + noise(i, 3, 7) * .3;
    c.globalAlpha *= (front ? .85 : .58) * density;
    c.lineWidth = r * .019;
    c.beginPath(); c.arc(0, 0, r * rho, front ? 0 : Math.PI, front ? Math.PI : TAU); c.stroke();
    c.globalAlpha /= (front ? .85 : .58) * density;
  }
  c.restore();
}

export function paintPlanetaryOpening(c: CanvasRenderingContext2D, w: number, h: number, s: SceneState) {
  const size = earthSpriteSize(w, h);
  if (pending.has(size) && !planetaryCacheReady(size)) return;
  const alpha = s.earth * (1 - smooth(s.p, .20, .30));
  if (alpha <= 0) return;
  const layout = orbitalLayout(w, h, s.p);
  c.save(); c.globalAlpha = alpha;
  c.imageSmoothingEnabled = true; c.imageSmoothingQuality = 'high';
  if (layout.alpha > 0) for (const planet of layout.planets) {
    c.save(); c.globalAlpha *= layout.alpha;
    c.translate(planet.x, planet.y); c.rotate(planet.kind === 'saturn' ? -.36 : -.16);
    if (planet.kind === 'saturn') rings(c, planet.r, false);
    const body = sphereSprite(planet.kind, planet.kind === 'mars' ? 160 : 224, 0);
    c.drawImage(body, -planet.r, -planet.r, planet.r * 2, planet.r * 2);
    if (planet.kind === 'saturn') rings(c, planet.r, true);
    c.restore();
  }
  const { x, y, r } = layout.earth;
  const atmosphere = c.createRadialGradient(x, y, r * .99, x, y, r * 1.10);
  atmosphere.addColorStop(0, '#60b5ff70'); atmosphere.addColorStop(.25, '#4a9cf82a'); atmosphere.addColorStop(1, '#357ad500');
  c.fillStyle = atmosphere; c.beginPath(); c.arc(x, y, r * 1.10, 0, TAU); c.fill();
  c.save(); c.translate(x, y); c.rotate(-.10);
  const body = sphereSprite('earth', size, layout.turns);
  c.drawImage(body, -r, -r, r * 2, r * 2);
  c.restore(); c.restore();
}
