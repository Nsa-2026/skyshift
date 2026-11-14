import { describe, expect, it } from 'vitest';
import { orbitalLayout, paintPlanetaryOpening } from './planets';
import { sceneAt } from './model';
import { rasterSphere, sphereLookup, type Material } from './spheres';

describe('code-drawn planetary surfaces', () => {
  const solid: Material = { width: 8, height: 4, rgb: new Uint8ClampedArray(8 * 4 * 3).fill(200) };
  it('shades the sphere from the shared sun direction and leaves space outside the limb transparent', () => {
    const map = sphereLookup(64), pixels = new Uint8ClampedArray(64 * 64 * 4);
    rasterSphere(map, solid, 0, pixels);
    const at = (x: number, y: number, channel = 0) => pixels[(y * 64 + x) * 4 + channel];
    expect(at(18, 18)).toBeGreaterThan(150);
    expect(at(58, 35)).toBeLessThan(15);
    expect(at(32, 32, 3)).toBe(255);
    expect(at(0, 0, 3)).toBe(0);
    expect(pixels.some((v, i) => i % 4 === 3 && v > 0 && v < 255)).toBe(true);
  });
  it('wraps longitude exactly once around and reproduces the image when scrolling back', () => {
    const material: Material = { width: 8, height: 4, rgb: Uint8ClampedArray.from({ length: 96 }, (_, i) => i * 13 % 255) };
    const map = sphereLookup(48, .23), render = (turns: number) => {
      const pixels = new Uint8ClampedArray(48 * 48 * 4); rasterSphere(map, material, turns, pixels); return pixels;
    };
    const first = render(.073);
    expect(render(.4)).not.toEqual(first);
    expect(render(1.073)).toEqual(first);
    expect(render(.073)).toEqual(first);
  });
  it('keeps clouds opaque on the lit hemisphere while preserving the curved terminator', () => {
    const map = sphereLookup(64), clear = new Uint8ClampedArray(64 * 64 * 4), cloudy = new Uint8ClampedArray(clear.length);
    rasterSphere(map, solid, 0, clear);
    rasterSphere(map, { ...solid, clouds: new Uint8Array(32).fill(255) }, 0, cloudy);
    const sun = (18 * 64 + 18) * 4, night = (35 * 64 + 58) * 4;
    expect(cloudy[sun]).toBeGreaterThan(clear[sun]);
    expect(cloudy[night]).toBeLessThan(15);
  });
});

describe('planet composition and scroll', () => {
  it('does not allocate or draw planetary surfaces once their opacity is zero', () => {
    const unusable = new Proxy({}, { get() { throw new Error('an invisible planet must not draw'); } }) as CanvasRenderingContext2D;
    expect(() => paintPlanetaryOpening(unusable, 1440, 1000, sceneAt(.31))).not.toThrow();
  });
  it.each([[1440, 1000], [390, 844], [844, 390]])('keeps the first Earth fully visible at %i×%i', (w, h) => {
    const layout = orbitalLayout(w, h, 0), { x, y, r } = layout.earth;
    expect(x - r * 1.1).toBeGreaterThan(0); expect(x + r * 1.1).toBeLessThan(w);
    expect(y - r * 1.1).toBeGreaterThan(0); expect(y + r * 1.1).toBeLessThan(h);
    expect(layout.planets.map(p => p.kind)).toEqual(['saturn', 'jupiter', 'mars']);
    expect(layout.planets.every(p => p.r > 0)).toBe(true);
  });
  it('enlarges Earth with scroll while the other planets retreat and disappear before the clouds', () => {
    const start = orbitalLayout(1440, 1000, 0), dive = orbitalLayout(1440, 1000, .20);
    expect(dive.earth.r).toBeGreaterThan(start.earth.r * 3);
    expect(dive.planets[0].x).toBeLessThan(start.planets[0].x);
    expect(dive.planets[1].x).toBeGreaterThan(start.planets[1].x);
    expect(dive.alpha).toBeGreaterThanOrEqual(0); expect(dive.alpha).toBeLessThan(1);
    expect(orbitalLayout(1440, 1000, .24).alpha).toBe(0);
    expect(orbitalLayout(1440, 1000, 1).alpha).toBe(0);
    expect(orbitalLayout(1440, 1000, 0)).toEqual(start);
  });
});
