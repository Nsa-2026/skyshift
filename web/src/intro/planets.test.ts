import { describe, expect, it, vi } from 'vitest';
import { diveDepth, diveTarget, drawDusk, duskOpacity, duskRow, orbitalLayout, paintPlanetaryOpening } from './planets';
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
  it('keeps the title composition: Earth lower right, Saturn and Jupiter above, Mars lower left', () => {
    const { earth, planets } = orbitalLayout(1440, 900, 0);
    expect(earth).toEqual({ x: 1440 * .74, y: 900 * .76, r: 900 * .19 });
    expect(planets.map(p => [p.x / 1440, p.y / 900, p.r / 900].map(v => +v.toFixed(3)))).toEqual([[.18, .23, .058], [.84, .25, .066], [.15, .72, .033]]);
  });
  it('dives in one straight move: the target on Earth stays put while everything grows away from it', () => {
    const [tx, ty] = diveTarget(1440, 900);
    for (const p of [.05, .12, .2, .25]) {
      const a = orbitalLayout(1440, 900, 0), b = orbitalLayout(1440, 900, p), grow = b.earth.r / a.earth.r;
      // The target is the same point of the disc at every step.
      expect((tx - b.earth.x) / b.earth.r).toBeCloseTo((tx - a.earth.x) / a.earth.r, 9);
      expect((ty - b.earth.y) / b.earth.r).toBeCloseTo((ty - a.earth.y) / a.earth.r, 9);
      expect(grow).toBeGreaterThanOrEqual(1);
    }
  });
  it('eases in, then accelerates, until Earth fills the screen', () => {
    const tau = [0, .03, .08, .14, .2].map(diveDepth);
    expect(tau[1]).toBeLessThan(.01);
    for (let i = 1; i < tau.length; i++) expect(tau[i] - tau[i - 1]).toBeGreaterThan(i > 1 ? tau[i - 1] - tau[i - 2] : 0);
    for (const [w, h] of [[1440, 900], [390, 844], [844, 390]]) {
      const { x, y, r } = orbitalLayout(w, h, .24).earth;
      for (const [cx, cy] of [[0, 0], [w, 0], [0, h], [w, h]]) expect(Math.hypot(cx - x, cy - y)).toBeLessThan(r);
    }
  });
  it('slides the planets out past the edges, nearer ones sooner, growing as they pass', () => {
    const at = (p: number) => orbitalLayout(1440, 900, p).planets;
    const off = (q: { x: number; y: number; r: number }) => q.x + q.r * 2.3 < 0 || q.x - q.r * 2.3 > 1440 || q.y + q.r * 2.3 < 0 || q.y - q.r * 2.3 > 900;
    const gone = (i: number) => [.04, .06, .08, .1, .12, .14, .16, .18].find(p => off(at(p)[i]) || !Number.isFinite(at(p)[i].r))!;
    expect(gone(2)).toBeLessThanOrEqual(gone(0)); // Mars (nearest) before Saturn
    expect(gone(0)).toBeLessThanOrEqual(gone(1)); // Saturn before Jupiter
    expect(gone(1)).toBeLessThanOrEqual(.18);
    const before = at(0), passing = at(.09);
    passing.forEach((q, i) => { if (Number.isFinite(q.r)) expect(q.r).toBeGreaterThan(before[i].r); });
  });
});

describe('dusk over Earth', () => {
  it('is a dark strip computed per pixel with the same ramp as the old gradient', () => {
    const row = duskRow(.2, 1, 512);
    const colour = (i: number) => [...row.slice(i * 4, i * 4 + 3)];
    const alphaAt = (x: number) => row[Math.min(511, Math.max(0, Math.floor((x + 1.02) / 2.04 * 512))) * 4 + 3] / 255;
    for (let i = 0; i < 512; i++) expect(colour(i)).toEqual([3, 7, 16]);
    const from = .2 - .45;
    expect(alphaAt(from - .05)).toBe(0);
    for (const t of [.1, .3, .5, .7, .9]) expect(alphaAt(from + .67 * t)).toBeCloseTo(duskOpacity(t), 1);
    expect(alphaAt(1)).toBeCloseTo(.96, 2);
    expect(duskRow(.2, .5, 512)[511 * 4 + 3]).toBe(Math.round(255 * .5 * .96));
  });
  it('draws nothing at zero strength or a broken radius', () => {
    const fill = vi.fn();
    const c = { fill } as unknown as CanvasRenderingContext2D;
    drawDusk(c, 0, 0, 100, .2, 0); drawDusk(c, 0, 0, NaN, .2, 1); drawDusk(c, 0, 0, 0, .2, 1);
    expect(fill).not.toHaveBeenCalled();
  });
});
