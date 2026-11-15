import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { sceneAt } from './model';
import { diveTarget } from './planets';
import {
  coverLayout, decodeStars, drawStarField, flightAt, flightDirection, loadStars, paintStars, projectStar,
  earthCover, setStarLayer, skyToLayer, starBudget, toneOf,
} from './stars';

// Tests run in web/.
const shipped = readFileSync(resolve(process.cwd(), 'public/opening/stars-gaia.bin'));
const bytes = shipped.buffer.slice(shipped.byteOffset, shipped.byteOffset + shipped.byteLength) as ArrayBuffer;
const catalog = decodeStars(bytes);

/** The file layout of scripts/precompute/opening_stars.py, for small hand-made catalogues. */
function encode(stars: Array<[ra: number, dec: number, parallax: number, g: number, bpRp: number]>): ArrayBuffer {
  const n = stars.length, buf = new ArrayBuffer(64 + n * 8), view = new DataView(buf);
  'GDR3STAR'.split('').forEach((ch, i) => view.setUint8(i, ch.charCodeAt(0)));
  view.setUint32(8, 1, true); view.setUint32(12, n, true);
  const ranges = [240, 80, -40, 70, Math.log(.05), Math.log(1000 / .05), -1, 12, -.5, 6, 281, -4];
  ranges.forEach((v, i) => view.setFloat32(16 + i * 4, v, true));
  stars.forEach(([ra, dec, plx, g, c], i) => {
    view.setUint16(64 + i * 2, Math.round((ra - 240) / 80 * 65535), true);
    view.setUint16(64 + 2 * n + i * 2, Math.round((dec + 40) / 70 * 65535), true);
    view.setUint16(64 + 4 * n + i * 2, Math.round((Math.log(plx) - Math.log(.05)) / Math.log(1000 / .05) * 65535), true);
    view.setUint8(64 + 6 * n + i, Math.round((g + 1) / 12 * 255));
    view.setUint8(64 + 7 * n + i, Number.isNaN(c) ? 0 : 1 + Math.round((c + .5) / 6 * 254));
  });
  return buf;
}

/** A 2D context that records what is drawn, so frames can be compared. */
function recorder() {
  const log: unknown[] = [];
  const gradient = { addColorStop: () => {} };
  const target: Record<string, unknown> = {};
  const ctx = new Proxy(target, {
    get(_, key) {
      if (key === 'createRadialGradient' || key === 'createLinearGradient') return () => gradient;
      if (key === 'log') return log;
      if (typeof key === 'string' && key in target) return target[key];
      return (...args: unknown[]) => { log.push([key, ...args.map(a => typeof a === 'number' ? Math.round(a * 1000) / 1000 : typeof a)]); };
    },
    set(_, key, value) { target[key as string] = value; log.push([key, value]); return true; },
  });
  return ctx as unknown as CanvasRenderingContext2D & { log: unknown[] };
}
function stubCanvas() {
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(() => recorder() as never);
}
afterEach(() => { vi.restoreAllMocks(); setStarLayer(null); });

describe('the Gaia star file', () => {
  it('holds 20–40 thousand real stars, brightest first, with sane values', () => {
    expect(catalog.count).toBeGreaterThan(20000);
    expect(catalog.count).toBeLessThan(40000);
    expect(shipped.byteLength).toBeLessThan(500 * 1024);
    for (let i = 1; i < catalog.count; i++) expect(catalog.g[i]).toBeGreaterThanOrEqual(catalog.g[i - 1]);
    expect(catalog.g[catalog.count - 1]).toBeLessThan(10.3);
    expect(Math.min(...catalog.parallax)).toBeGreaterThan(0);
    const colours = Array.from(catalog.bpRp).filter(Number.isFinite);
    expect(colours.length).toBeGreaterThan(catalog.count * .99);
  });
  it("has Barnard's Star where Gaia DR3 puts it, 1.83 pc away", () => {
    const i = Array.from(catalog.parallax).findIndex(p => p > 500);
    expect(i).toBeGreaterThanOrEqual(0);
    expect(catalog.ra[i]).toBeCloseTo(269.449, 2);
    expect(catalog.dec[i]).toBeCloseTo(4.739, 2);
    expect(catalog.parallax[i]).toBeCloseTo(546.98, 0);
    expect(catalog.g[i]).toBeCloseTo(8.19, 1);
    expect(Math.hypot(catalog.x[i], catalog.y[i], catalog.z[i])).toBeCloseTo(1.828, 2);
  });
  it('decodes the layout the script writes, to within its quantisation', () => {
    const cat = decodeStars(encode([[269.4486, 4.7394, 546.98, 8.19, 2.83], [281, -4, 1, 9.5, NaN]]));
    expect(cat.count).toBe(2);
    expect(cat.ra[0]).toBeCloseTo(269.4486, 2); expect(cat.dec[0]).toBeCloseTo(4.7394, 2);
    expect(cat.parallax[0] / 546.98).toBeCloseTo(1, 3);
    expect(cat.g[0]).toBeCloseTo(8.19, 1); expect(cat.bpRp[0]).toBeCloseTo(2.83, 1);
    expect(cat.bpRp[1]).toBeNaN(); expect(cat.tone[1]).toBe(toneOf(NaN));
    // On the view axis, 1 kpc straight ahead.
    expect(cat.x[1]).toBeCloseTo(0, 0); expect(cat.y[1]).toBeCloseTo(0, 0); expect(cat.z[1]).toBeCloseTo(1000, -1);
  });
  it('refuses anything that is not a whole star file, such as the site\'s index.html', () => {
    const html = new TextEncoder().encode('<!doctype html><html><head><title>SkyShift</title></head><body></body></html>');
    expect(() => decodeStars(html.buffer as ArrayBuffer)).toThrow(/Gaia/);
    expect(() => decodeStars(new ArrayBuffer(10))).toThrow(/short/);
    expect(() => decodeStars(bytes.slice(0, bytes.byteLength - 1))).toThrow(/size/);
  });
  it('loads in the background; a missing or broken file is one warning and no layer', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const ok = await loadStars('/x.bin', (async () => new Response(bytes)) as typeof fetch);
    expect(ok?.count).toBe(catalog.count);
    expect(await loadStars('/x.bin', (async () => new Response('', { status: 404 })) as typeof fetch)).toBeNull();
    expect(await loadStars('/x.bin', (async () => new Response('<!doctype html>')) as typeof fetch)).toBeNull();
    expect(await loadStars('/x.bin', (async () => { throw new TypeError('offline'); }) as typeof fetch)).toBeNull();
    expect(warn).toHaveBeenCalledTimes(3);
    expect(String(warn.mock.calls[0][0])).toMatch(/Gaia star layer.*404/);
  });
});

describe('registration with the SPHEREx backdrop', () => {
  // Gaia DR3 stars and where they are in spherex-sky.webp (centroids measured on the image).
  const measured: Array<[ra: number, dec: number, u: number, v: number]> = [
    [265.86795, 4.56801, 1277.0, 231.4], [275.32506, -2.90194, 981.1, 465.6], [269.75660, -9.77415, 1152.3, 687.0],
    [302.82635, -.82144, 118.4, 407.4], [278.80170, -8.24547, 869.7, 635.7], [298.82851, 6.40463, 242.0, 174.9],
    [266.97306, 2.70695, 1244.1, 290.0], [290.41804, -17.84710, 514.4, 940.5],
  ];
  it.each([[1600, 1000], [1440, 900], [390, 844], [1920, 900]])('puts known stars on their image pixels at %i×%i', (w, h) => {
    const { k, ox, oy } = coverLayout(w, h);
    for (const [ra, dec, u, v] of measured) {
      const [x, y] = skyToLayer(ra, dec, w, h);
      expect(Math.hypot(x - (ox + k * u), y - (oy + k * v)) / k).toBeLessThan(.5);
    }
  });
  it('draws every star at p = 0 exactly where the backdrop has it (seen from the Sun)', () => {
    const dir = flightDirection(1440, 900);
    expect(flightAt(0)).toBe(0);
    for (const i of [0, 1, 100, 5000, catalog.count - 1]) {
      const at = projectStar(catalog, i, flightAt(0), dir, 1440, 900)!;
      const [x, y] = skyToLayer(catalog.ra[i], catalog.dec[i], 1440, 900);
      expect(at[0]).toBeCloseTo(x, 3); expect(at[1]).toBeCloseTo(y, 3);
    }
  });
});

describe('the flight', () => {
  it('heads for the point on Earth the dive is aimed at', () => {
    for (const [w, h] of [[1440, 900], [390, 844]]) {
      const dir = flightDirection(w, h);
      expect(Math.hypot(...dir)).toBeCloseTo(1, 6);
      // A star straight down the flight path stays put on screen (before the sky's CSS turn).
      const cat = decodeStars(encode([[281, -4, 1, 9, 1]]));
      Object.assign(cat, { x: Float32Array.of(dir[0] * 800), y: Float32Array.of(dir[1] * 800), z: Float32Array.of(dir[2] * 800) });
      const a = projectStar(cat, 0, 0, dir, w, h)!, b = projectStar(cat, 0, flightAt(.25), dir, w, h)!;
      expect(Math.hypot(a[0] - b[0], a[1] - b[1])).toBeLessThan(.01);
      expect(diveTarget(w, h).every(Number.isFinite)).toBe(true);
    }
  });
  it('eases in, then accelerates, over a few tens of parsecs', () => {
    const at = [0, .03, .06, .1, .15, .2, .25].map(flightAt);
    expect(at[1]).toBeLessThan(.5);
    for (let i = 1; i < at.length; i++) expect(at[i]).toBeGreaterThan(at[i - 1]);
    const steps = at.slice(1).map((v, i) => v - at[i]);
    for (let i = 1; i < 5; i++) expect(steps[i] / ([.03, .03, .04, .05, .05][i])).toBeGreaterThan(steps[i - 1] / [.03, .03, .04, .05, .05][i - 1]);
    expect(flightAt(.3)).toBeGreaterThan(20); expect(flightAt(.3)).toBeLessThan(100);
  });
  it('moves near stars far more than distant ones in the same direction (real depth)', () => {
    const cat = decodeStars(encode([[290, 5, 100, 6, 1], [290, 5, 1, 9, 1]])); // 10 pc and 1 kpc
    const dir = flightDirection(1440, 900), moved = (i: number) => {
      const a = projectStar(cat, i, 0, dir, 1440, 900)!, b = projectStar(cat, i, flightAt(.15), dir, 1440, 900)!;
      return Math.hypot(a[0] - b[0], a[1] - b[1]);
    };
    expect(moved(0)).toBeGreaterThan(50 * moved(1));
    expect(moved(1)).toBeLessThan(12);
  });
  it('draws fewer stars on a phone than on a laptop', () => {
    expect(starBudget(390, 844, catalog.count)).toBeLessThan(starBudget(1440, 900, catalog.count));
    expect(starBudget(1440, 900, catalog.count)).toBeLessThanOrEqual(catalog.count);
  });
});

describe('drawing', () => {
  it('is a pure function of scroll: scrubbing away and back draws the same frame', () => {
    stubCanvas();
    const frame = (p: number) => { const c = recorder(); drawStarField(c, catalog, 1440, 900, sceneAt(p)); return c.log; };
    const first = frame(.15);
    expect(first.length).toBeGreaterThan(1000);
    frame(.05); frame(.3); frame(0);
    expect(frame(.15)).toEqual(first);
  });
  it('streaks only once the camera moves, and is gone once the clouds close', () => {
    stubCanvas();
    const strokes = (p: number) => { const c = recorder(); drawStarField(c, catalog, 1440, 900, sceneAt(p)); return c.log.filter(e => (e as unknown[])[0] === 'stroke').length; };
    expect(strokes(0)).toBe(0);
    expect(strokes(.18)).toBeGreaterThan(0);
    const c = recorder();
    expect(drawStarField(c, catalog, 1440, 900, sceneAt(.3))).toBe(0);
  });
  it('skips the stars Earth hides, and all of them once it fills the screen', () => {
    const at0 = earthCover(1440, 900, sceneAt(0));
    expect(Array.isArray(at0)).toBe(true);
    const [x, y, r2] = at0 as number[];
    expect(x).toBeGreaterThan(720); expect(y).toBeGreaterThan(450); expect(Math.sqrt(r2)).toBeLessThan(1440 * .19);
    expect(earthCover(1440, 900, sceneAt(.26))).toBe('all');
    expect(earthCover(390, 844, sceneAt(.26))).toBe('all');
    expect(earthCover(1440, 900, sceneAt(.28))).toBeNull(); // Earth is fading: stars show through
  });
  it('a failing canvas hides the layer with one warning, and never throws into the opening', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const canvas = document.createElement('canvas');
    vi.spyOn(canvas, 'getContext').mockImplementation(() => { throw new Error('context lost'); });
    setStarLayer(canvas, catalog);
    expect(() => paintStars(1440, 900, 1.75, sceneAt(.1))).not.toThrow();
    expect(() => paintStars(1440, 900, 1.75, sceneAt(.2))).not.toThrow();
    expect(canvas.hidden).toBe(true);
    expect(warn).toHaveBeenCalledTimes(1);
  });
});
