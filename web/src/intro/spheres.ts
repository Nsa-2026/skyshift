/** Inverse sphere projection and directional lighting; no WebGL or image textures needed. */
const PI = Math.PI, TAU = PI * 2;
export const clamp01 = (v: number) => Math.max(0, Math.min(1, v));
export function noise(x: number, y: number, seed = 0): number {
  const hash = (a: number, b: number) => {
    let n = Math.imul(a, 374761393) + Math.imul(b, 668265263) + seed * 1013;
    n = Math.imul(n ^ (n >>> 13), 1274126177);
    return ((n ^ (n >>> 16)) >>> 0) / 4294967295;
  };
  const a = Math.floor(x), b = Math.floor(y), fx = x - a, fy = y - b;
  const u = fx * fx * (3 - 2 * fx), v = fy * fy * (3 - 2 * fy);
  const top = hash(a, b) * (1 - u) + hash(a + 1, b) * u;
  const bottom = hash(a, b + 1) * (1 - u) + hash(a + 1, b + 1) * u;
  return top * (1 - v) + bottom * v;
}
export function fbm(x: number, y: number, seed = 0): number {
  return noise(x, y, seed) * .55 + noise(x * 2.03, y * 2.03, seed + 7) * .28
    + noise(x * 4.1, y * 4.1, seed + 19) * .17;
}
export interface Material {
  width: number; height: number;
  rgb: Uint8ClampedArray;
  water?: Uint8Array;
  clouds?: Uint8Array;
}
export interface SphereLookup {
  size: number;
  u: Float32Array; v: Float32Array;
  light: Float32Array; specular: Float32Array; rim: Float32Array;
  coverage: Float32Array;
}

// Lighting is fixed in view space. Keep the derived double-precision values private,
// preserving the public lookup shape and the exact arithmetic used by the rasterizer.
export interface SphereLighting { day: Float64Array; cloud: Float64Array }
const lightingCache = new WeakMap<SphereLookup, SphereLighting>();
export function lightingFor(map: SphereLookup): SphereLighting {
  const cached = lightingCache.get(map);
  if (cached) return cached;
  const day = new Float64Array(map.light.length), cloud = new Float64Array(map.light.length);
  for (let i = 0; i < map.light.length; i++) {
    if (!map.coverage[i]) continue;
    const lit = Math.max(0, map.light[i]);
    day[i] = .016 + .984 * Math.pow(lit, .74);
    cloud[i] = .035 + .965 * Math.pow(lit, .65);
  }
  const value = { day, cloud };
  lightingCache.set(map, value);
  return value;
}

/** Reuse the worker's exact derived values, avoiding first-scroll recalculation. */
export function restoreSphereLighting(map: SphereLookup, lighting: SphereLighting): void {
  lightingCache.set(map, lighting);
}

/** Cache view-space normals and spherical UVs, so scrolling only samples the material. */
export function sphereLookup(size: number, latitude = 0): SphereLookup {
  const count = size * size;
  const map: SphereLookup = {
    size, u: new Float32Array(count), v: new Float32Array(count),
    light: new Float32Array(count), specular: new Float32Array(count),
    rim: new Float32Array(count), coverage: new Float32Array(count),
  };
  const co = Math.cos(latitude), si = Math.sin(latitude);
  for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
    const i = y * size + x;
    const nx = (x + .5) * 2 / size - 1, ny = 1 - (y + .5) * 2 / size;
    const rr = nx * nx + ny * ny;
    if (rr >= 1) continue;
    const z = Math.sqrt(1 - rr), worldY = ny * co + z * si, worldZ = z * co - ny * si;
    map.u[i] = Math.atan2(nx, worldZ) / TAU + .5;
    map.v[i] = .5 - Math.asin(Math.max(-1, Math.min(1, worldY))) / PI;
    map.light[i] = nx * -.66 + ny * .46 + z * .594;
    map.specular[i] = Math.pow(Math.max(0, nx * -.370 + ny * .258 + z * .893), 85) * .55;
    map.rim[i] = Math.pow(1 - z, 3.5);
    map.coverage[i] = clamp01((1 - Math.sqrt(rr)) * size / 2);
  }
  lightingFor(map);
  return map;
}

/** A pure rasterizer. The same material/rotation always produces the same pixels. */
export function rasterSphere(map: SphereLookup, material: Material, turns: number, out: Uint8ClampedArray): void {
  const { width: w, height: h, rgb, water, clouds } = material;
  const lighting = lightingFor(map);
  for (let i = 0; i < map.coverage.length; i++) {
    const at = i * 4;
    if (!map.coverage[i]) { out[at + 3] = 0; continue; }
    const u = ((map.u[i] + turns) % 1 + 1) % 1;
    const tx = u * w, ty = Math.min(h - 1, map.v[i] * h);
    const col = Math.floor(tx), row = Math.floor(ty), fx = tx - col, fy = ty - row;
    const a = (1 - fx) * (1 - fy), b0 = fx * (1 - fy), c0 = (1 - fx) * fy, d = fx * fy;
    const pixel = row * w + col, right = row * w + (col + 1) % w;
    const below = Math.min(h - 1, row + 1) * w + col, diagonal = Math.min(h - 1, row + 1) * w + (col + 1) % w;
    const p0 = pixel * 3, p1 = right * 3, p2 = below * 3, p3 = diagonal * 3, dot = map.light[i];
    const day = lighting.day[i];
    const cloud = clouds ? (clouds[pixel] * a + clouds[right] * b0 + clouds[below] * c0 + clouds[diagonal] * d) / 255 : 0;
    const shadow = clouds ? clouds[Math.max(0, row - 2) * w + (col + 3) % w] / 255 * .25 : 0;
    const shine = water ? map.specular[i] * (water[pixel] * a + water[right] * b0 + water[below] * c0 + water[diagonal] * d) / 255 * (1 - cloud) : 0;
    // Bilinear sampling prevents pixel blocks when the scroll camera approaches the surface.
    let r = (rgb[p0] * a + rgb[p1] * b0 + rgb[p2] * c0 + rgb[p3] * d) * day * (1 - shadow);
    let g = (rgb[p0 + 1] * a + rgb[p1 + 1] * b0 + rgb[p2 + 1] * c0 + rgb[p3 + 1] * d) * day * (1 - shadow);
    let b = (rgb[p0 + 2] * a + rgb[p1 + 2] * b0 + rgb[p2 + 2] * c0 + rgb[p3 + 2] * d) * day * (1 - shadow);
    const cloudLight = lighting.cloud[i];
    r = r * (1 - cloud) + 239 * cloudLight * cloud;
    g = g * (1 - cloud) + 246 * cloudLight * cloud;
    b = b * (1 - cloud) + 253 * cloudLight * cloud;
    if (water) {
      const air = map.rim[i] * (.08 + .74 * clamp01(dot + .2));
      r = r * (1 - air) + 55 * air + shine * 140;
      g = g * (1 - air) + 145 * air + shine * 165;
      b = b * (1 - air) + 245 * air + shine * 185;
    }
    out[at] = r; out[at + 1] = g; out[at + 2] = b;
    out[at + 3] = Math.round(map.coverage[i] * 255);
  }
}
