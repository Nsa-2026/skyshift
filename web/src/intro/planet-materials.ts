// Shared by the interactive fallback and the preparation worker; no DOM dependencies.
import { clamp01, fbm, type Material } from './spheres';
export type Kind = 'earth' | 'jupiter' | 'saturn' | 'mars';
const TAU = Math.PI * 2;
const mix = (a: number, b: number, t: number) => a + (b - a) * t;
const bell = (x: number, center: number, spread: number) => Math.exp(-(((x - center) / spread) ** 2));

/** Natural Earth public-domain land vectors; surface colours/weather are authored illustration. */
export function earthMaterial(mask: Uint8ClampedArray, width = 1024, height = 512): Material {
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

export function gasOrRockMaterial(kind: Exclude<Kind, 'earth'>): Material {
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
