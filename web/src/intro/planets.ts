import landPolygons from './data/earth-land.json';
import { smooth, type SceneState } from './model';
import { clamp01, fbm, noise, rasterSphere, sphereLookup, type Material } from './spheres';

type Kind = 'earth' | 'jupiter' | 'saturn' | 'mars';
const TAU = Math.PI * 2;
const materials = new Map<Kind, Material>();
const sprites = new Map<string, { canvas: HTMLCanvasElement; pixels: ImageData; map: ReturnType<typeof sphereLookup>; turns: number }>();
const mix = (a: number, b: number, t: number) => a + (b - a) * t;
const bell = (x: number, center: number, spread: number) => Math.exp(-(((x - center) / spread) ** 2));

/** Natural Earth public-domain land vectors; surface colours/weather are authored illustration. */
function earthMaterial(): Material {
  const width = 1024, height = 512;
  const maskCanvas = document.createElement('canvas'); maskCanvas.width = width; maskCanvas.height = height;
  const c = maskCanvas.getContext('2d')!;
  c.fillStyle = '#fff';
  for (const polygon of landPolygons) {
    c.beginPath();
    for (const ring of polygon) {
      ring.forEach(([lon, lat], i) => {
        const x = (lon + 180) / 360 * width, y = (90 - lat) / 180 * height;
        if (i) c.lineTo(x, y); else c.moveTo(x, y);
      });
      c.closePath();
    }
    c.fill('evenodd');
  }
  const mask = c.getImageData(0, 0, width, height).data;
  const rgb = new Uint8ClampedArray(width * height * 3), clouds = new Uint8Array(width * height), water = new Uint8Array(width * height);
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const i = y * width + x, at = i * 3, u = x / width, v = y / height;
    const lon = u * 360 - 180, lat = 90 - v * 180, land = mask[i * 4 + 3] / 255;
    const terrain = fbm(u * 75, v * 60, 14), broad = fbm(u * 14, v * 12, 6);
    const green = clamp01((Math.abs(lat) - 4) / 55 + broad * .3);
    const desert = clamp01(bell(lat, 24, 13) * bell(lon, 28, 48) + bell(lat, -25, 10) * bell(lon, 134, 22));
    const ice = clamp01((Math.abs(lat) - 65 + broad * 7) / 13);
    const relief = .83 + terrain * .35;
    let lr = mix(36, 105, green), lg = mix(82, 112, green), lb = mix(48, 66, green);
    lr = mix(lr, 190, desert); lg = mix(lg, 159, desert); lb = mix(lb, 107, desert);
    lr = mix(lr * relief, 225, ice); lg = mix(lg * relief, 235, ice); lb = mix(lb * relief, 238, ice);
    const coast = Math.max(mask[(y * width + (x + 3) % width) * 4 + 3], mask[(y * width + (x + width - 3) % width) * 4 + 3], mask[(Math.max(0, y - 3) * width + x) * 4 + 3], mask[(Math.min(height - 1, y + 3) * width + x) * 4 + 3]) / 255;
    rgb[at] = mix(7 + broad * 5 + coast * 8, lr, land);
    rgb[at + 1] = mix(39 + broad * 11 + coast * 34, lg, land);
    rgb[at + 2] = mix(77 + broad * 14 + coast * 26, lb, land);
    water[i] = Math.round((1 - land) * 255);
    const warp = fbm(u * 9 + 3, v * 11, 21);
    const weather = fbm(u * 28 + warp * 2.7, v * 32 + Math.sin(u * 28) * .6, 41);
    const wisps = fbm(u * 73 + warp, v * 66, 16);
    const cloud = clamp01((weather - .49) * 3.6 + (wisps - .5) * .5);
    clouds[i] = Math.round(cloud * .90 * 255);
  }
  return { width, height, rgb, clouds, water };
}

function gasOrRockMaterial(kind: Exclude<Kind, 'earth'>): Material {
  const width = 512, height = 256, rgb = new Uint8ClampedArray(width * height * 3);
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const i = (y * width + x) * 3, u = x / width, v = y / height;
    const lat = (.5 - v) * Math.PI, lon = (u - .5) * TAU;
    const n = fbm(u * 22, v * 19, 11);
    let r: number, g: number, b: number;
    if (kind === 'mars') {
      const plain = fbm(u * 8, v * 10, 3), dark = clamp01((plain - .48) * 3);
      r = (186 - dark * 75) * (.8 + n * .4); g = (94 - dark * 33) * (.8 + n * .4); b = 53 - dark * 13 + n * 12;
      const cap = clamp01((Math.abs(lat) - 1.35 + n * .08) * 10);
      r = mix(r, 221, cap); g = mix(g, 222, cap); b = mix(b, 211, cap);
    } else {
      const bands = Math.sin(lat * 27 + (n - .5) * 1.4 + Math.sin(lon * 7 + lat * 13) * .12);
      const fine = Math.sin(lat * 99 + n * .8) * .05;
      const zone = clamp01(.5 + bands * .4 + fine);
      r = mix(kind === 'jupiter' ? 155 : 177, 225, zone);
      g = mix(kind === 'jupiter' ? 113 : 153, 208, zone);
      b = mix(kind === 'jupiter' ? 82 : 111, 174, zone);
      if (kind === 'jupiter') {
        const storm = ((lon + .32) / .30) ** 2 + ((lat + .34) / .115) ** 2;
        const edge = clamp01((1.15 - storm) * 6), whorl = .75 + .18 * Math.sin(storm * 25 + n * 3);
        r = mix(r, 185 * whorl, edge); g = mix(g, 100 * whorl, edge); b = mix(b, 70 * whorl, edge);
      }
    }
    rgb[i] = r; rgb[i + 1] = g; rgb[i + 2] = b;
  }
  return { width, height, rgb };
}

function sphereSprite(kind: Kind, size: number, turns: number): HTMLCanvasElement {
  let material = materials.get(kind);
  if (!material) { material = kind === 'earth' ? earthMaterial() : gasOrRockMaterial(kind); materials.set(kind, material); }
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
  if (s.earth <= 0) return;
  const layout = orbitalLayout(w, h, s.p);
  c.save(); c.globalAlpha = s.earth * (1 - smooth(s.p, .20, .30));
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
  const body = sphereSprite('earth', Math.min(w, h) < 600 ? 384 : 768, layout.turns);
  c.drawImage(body, -r, -r, r * 2, r * 2);
  c.restore(); c.restore();
}
