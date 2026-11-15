import { describe, expect, it } from 'vitest';
import { lensWindow, portraitCamera, toScreen } from './portrait';
import { sceneAt } from './model';
import { GALILEO_HEIGHT, GALILEO_OBJECTIVE, objectiveAspect, SCOPE_AXIS } from '../opening/galileo';

const SIZES: [number, number][] = [[1440, 900], [390, 844]];

describe('Galileo camera', () => {
  it.each<[number, number]>([...SIZES, [844, 390]])('stands him on the ground line, telescope and all in view, at %i×%i', (w, h) => {
    const cam = portraitCamera(w, h, sceneAt(.45));
    expect(toScreen(cam, { x: 0, y: 0 }).y).toBeCloseTo(h * .86);
    expect(GALILEO_HEIGHT * cam.scale).toBeGreaterThan(h * .2);
    const lens = toScreen(cam, GALILEO_OBJECTIVE);
    expect(lens.x).toBeGreaterThan(0); expect(lens.x).toBeLessThan(w);
    expect(lens.y).toBeGreaterThan(0);
    expect(cam.roll).toBe(0);
  });

  it.each(SIZES)('centres the objective at the end of the glide along the tube, at %i×%i', (w, h) => {
    const cam = portraitCamera(w, h, sceneAt(.72));
    const lens = toScreen(cam, GALILEO_OBJECTIVE);
    expect(lens.x).toBeCloseTo(w / 2);
    expect(lens.y).toBeCloseTo(h / 2);
    // The camera has rolled with the tube, which now lies close to level.
    const tube = Math.atan2(SCOPE_AXIS.y, SCOPE_AXIS.x) + cam.roll;
    expect(Math.abs(tube)).toBeLessThan(10 * Math.PI / 180);
    // Halfway through the glide the focus is on the tube's line, between eyepiece and lens.
    const mid = portraitCamera(w, h, sceneAt(.66));
    expect(mid.focusX).toBeLessThan(GALILEO_OBJECTIVE.x);
    expect(mid.scale).toBeLessThan(cam.scale);
  });

  it.each(SIZES)('ends with a circular sky window covering the whole screen at %i×%i', (w, h) => {
    const s = sceneAt(.9);
    expect(s.aperture).toBe(1);
    const win = lensWindow(w, h, s);
    expect(win.open).toBe(1);
    expect(win.rx).toBeCloseTo(win.ry, 9);
    expect(objectiveAspect(portraitCamera(w, h, s).turn)).toBeCloseTo(1, 12);
    for (const [x, y] of [[0, 0], [w, 0], [0, h], [w, h]]) {
      expect(Math.hypot(x - win.x, y - win.y)).toBeLessThan(win.rx);
    }
  });

  it('opens the window only once the lens faces the camera', () => {
    for (const p of [.72, .74, .76]) expect(lensWindow(1440, 900, sceneAt(p)).open).toBe(0);
    const opening = lensWindow(1440, 900, sceneAt(.8));
    expect(opening.open).toBeGreaterThan(0);
    expect(opening.rx / opening.ry).toBeGreaterThan(.95);
  });

  it.each(SIZES)('gives exactly the same view after scrubbing back and forth, at %i×%i', (w, h) => {
    for (const p of [.5, .66, .8]) {
      const before = portraitCamera(w, h, sceneAt(p));
      const win = lensWindow(w, h, sceneAt(p));
      for (const q of [.9, .3, .72, .45, .99, .6]) { portraitCamera(w, h, sceneAt(q)); lensWindow(w, h, sceneAt(q)); }
      expect(portraitCamera(w, h, sceneAt(p))).toEqual(before);
      expect(lensWindow(w, h, sceneAt(p))).toEqual(win);
    }
  });
});
