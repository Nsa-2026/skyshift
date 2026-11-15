import { descentAt, smooth, type SceneState } from './model';
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

/** Earth on the title: lower right, sunlit from the upper left. */
export function earthAtStart(w: number, h: number) {
  return { x: w * .74, y: h * .76, r: Math.min(w, h) * .19 };
}
/**
 * The point of Earth the camera dives to, in Earth radii from its centre (screen axes, y down):
 * on the night side, just past the evening terminator. A straight dive keeps it still on screen.
 */
const TARGET = { x: .6, y: .48 };
/** Toward the Sun on screen: the sprite's light direction, turned with the sprite. */
const SUN = [-.873, -.487];
/** Night opacity across the dusk ramp, t = 0 (day side) to 1 (full night): 0 → .55 → .96. */
export const duskOpacity = (t: number) => t <= 0 ? 0 : t >= 1 ? .96 : t < .5 ? t * 1.1 : .55 + (t - .5) * .82;
/** Columns of the dusk strip across the disc, x = −DUSK_REACH r … +DUSK_REACH r. */
export const DUSK_COLUMNS = 512;
const DUSK_REACH = 1.02;
/**
 * The night's opacity (0–255) in each column of a strip laid across the disc, +x away from the
 * Sun: the ramp runs from x = r(edge − .45) to r(edge + .22). Computed pixel by pixel instead
 * of with a CanvasGradient: on one browser that gradient's dark end came out white (15 Nov).
 */
export function duskRow(edge: number, strength: number, columns = DUSK_COLUMNS): Uint8ClampedArray {
  const row = new Uint8ClampedArray(columns * 4), k = Math.max(0, Math.min(1, strength));
  for (let i = 0; i < columns; i++) {
    const x = -DUSK_REACH + 2 * DUSK_REACH * (i + .5) / columns;
    row[i * 4] = 3; row[i * 4 + 1] = 7; row[i * 4 + 2] = 16;
    row[i * 4 + 3] = Math.round(255 * k * duskOpacity((x - (edge - .45)) / .67));
  }
  return row;
}
let duskStrip: { canvas: HTMLCanvasElement; c: CanvasRenderingContext2D; pixels: ImageData } | null = null;
/**
 * Fill Earth's disc (centre x, y, radius r) with the dusk: the strip as a pattern, turned so its
 * +x points away from the Sun. A pattern fill, like the old gradient fill: no clip, which cost
 * frames on the GPU.
 */
export function drawDusk(c: CanvasRenderingContext2D, x: number, y: number, r: number, edge: number, strength: number): void {
  if (!(strength > 0) || !Number.isFinite(r) || r <= 0 || typeof DOMMatrix !== 'function') return;
  if (!duskStrip) {
    const canvas = document.createElement('canvas'); canvas.width = DUSK_COLUMNS; canvas.height = 1;
    const sc = canvas.getContext('2d');
    if (!sc) return;
    duskStrip = { canvas, c: sc, pixels: sc.createImageData(DUSK_COLUMNS, 1) };
  }
  duskStrip.pixels.data.set(duskRow(edge, strength));
  duskStrip.c.putImageData(duskStrip.pixels, 0, 0);
  const pattern = c.createPattern(duskStrip.canvas, 'no-repeat');
  if (!pattern) return;
  const side = 2 * DUSK_REACH * r;
  pattern.setTransform(new DOMMatrix()
    .translateSelf(x, y).rotateSelf(Math.atan2(-SUN[1], -SUN[0]) * 180 / Math.PI)
    .translateSelf(-DUSK_REACH * r, -DUSK_REACH * r).scaleSelf(side / DUSK_COLUMNS, side));
  c.save();
  c.imageSmoothingEnabled = true;
  c.fillStyle = pattern; c.beginPath(); c.arc(x, y, r * 1.001, 0, TAU); c.fill();
  c.restore();
}
export function diveTarget(w: number, h: number): [number, number] {
  const e = earthAtStart(w, h);
  return [e.x + TARGET.x * e.r, e.y + TARGET.y * e.r];
}
/** Earth's final size, in title radii, at the end of the dive (the clouds take over before). */
const ZOOM = 28;
/**
 * How far the camera has come toward Earth's surface: 0 on the title, toward 1 at the clouds.
 * It eases in, then accelerates; everything seen from the camera scales as depth / (depth − τ).
 */
export function diveDepth(p: number): number {
  return 1 - Math.exp(-Math.log(ZOOM) * Math.pow(descentAt(p), 2.6));
}
/** Depths along the dive, Earth = 1: nearer planets slide out sooner and faster (parallax). */
const PLANETS = [
  { kind: 'saturn' as const, x: .18, y: .23, size: .058, depth: .6 },
  { kind: 'jupiter' as const, x: .84, y: .25, size: .066, depth: .74 },
  { kind: 'mars' as const, x: .15, y: .72, size: .033, depth: .5 },
];

/**
 * Positions are scenic composition, not measured planetary positions. One straight camera move:
 * everything expands from the dive target, each body by depth / (depth − τ).
 */
export function orbitalLayout(w: number, h: number, p: number) {
  const m = Math.min(w, h), tau = diveDepth(p), [tx, ty] = diveTarget(w, h), e = earthAtStart(w, h);
  const grow = 1 / (1 - tau);
  return {
    earth: { x: tx + (e.x - tx) * grow, y: ty + (e.y - ty) * grow, r: e.r * grow },
    planets: PLANETS.map(({ kind, x, y, size, depth }) => {
      // Past the camera: gone (never drawn behind it).
      const f = tau < depth * .97 ? depth / (depth - tau) : Infinity;
      return { kind, x: tx + (w * x - tx) * f, y: ty + (h * y - ty) * f, r: m * size * f };
    }),
    alpha: 1 - smooth(p, .17, .24), turns: 22 / 360, tau,
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
  const alpha = s.earth;
  if (alpha <= 0) return;
  const layout = orbitalLayout(w, h, s.p);
  c.save(); c.globalAlpha = alpha;
  c.imageSmoothingEnabled = true; c.imageSmoothingQuality = 'high';
  if (layout.alpha > 0) for (const planet of layout.planets) {
    const reach = planet.r * (planet.kind === 'saturn' ? 2.3 : 1);
    if (!Number.isFinite(planet.r) || planet.x + reach < 0 || planet.x - reach > w || planet.y + reach < 0 || planet.y - reach > h) continue;
    c.save(); c.globalAlpha *= layout.alpha;
    c.translate(planet.x, planet.y); c.rotate(planet.kind === 'saturn' ? -.36 : -.16);
    if (planet.kind === 'saturn') rings(c, planet.r, false);
    const body = sphereSprite(planet.kind, planet.kind === 'mars' ? 160 : 224, 0);
    c.drawImage(body, -planet.r, -planet.r, planet.r * 2, planet.r * 2);
    if (planet.kind === 'saturn') rings(c, planet.r, true);
    c.restore();
  }
  const { x, y, r } = layout.earth, tau = layout.tau;
  // The air glows thicker as the camera comes down through it.
  const air = 1.10 + .14 * smooth(tau, .3, .97);
  const atmosphere = c.createRadialGradient(x, y, r * .985, x, y, r * air);
  atmosphere.addColorStop(0, '#6cbcff80'); atmosphere.addColorStop(.22, '#4a9cf838'); atmosphere.addColorStop(1, '#357ad500');
  c.fillStyle = atmosphere; c.beginPath(); c.arc(x, y, r * air, 0, TAU); c.fill();
  c.save(); c.translate(x, y); c.rotate(-.10);
  const body = sphereSprite('earth', size, layout.turns);
  c.drawImage(body, -r, -r, r * 2, r * 2);
  c.restore();
  // Dusk falls as the camera comes down: the night side widens over the disc until the screen is
  // night (an illustration; it keeps the low-resolution day side from filling the screen).
  const dusk = smooth(s.p, .15, .235);
  if (dusk > 0) {
    drawDusk(c, x, y, r, .594 - dusk * 1.75, alpha * Math.min(1, dusk * 1.6));
  }
  // Inside the atmosphere: night air closes in before the clouds.
  const haze = smooth(tau, .8, .975);
  if (haze > 0) {
    const veil = c.createLinearGradient(0, 0, 0, h);
    veil.addColorStop(0, `rgba(22,44,82,${(.75 * haze).toFixed(3)})`);
    veil.addColorStop(.6, `rgba(10,24,48,${(.62 * haze).toFixed(3)})`);
    veil.addColorStop(1, `rgba(5,12,26,${(.55 * haze).toFixed(3)})`);
    c.fillStyle = veil; c.fillRect(0, 0, w, h);
  }
  c.restore();
}
