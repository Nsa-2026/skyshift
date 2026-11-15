import { describe, expect, it } from 'vitest';
import { portraitCamera, PORTRAIT_EYE } from './portrait';
import { sceneAt } from './model';
import { GODDESS_HEIGHT, GODDESS_EYE_WIDTH, GODDESS_PUPIL } from '../opening/goddess';

describe('supplied goddess camera', () => {
  it.each([[1440, 1000], [390, 844], [844, 390]])('fits the wide figure and grounds its feet at %i×%i', (w, h) => {
    const cam = portraitCamera(w, h, sceneAt(.43));
    expect(cam.y - cam.focusY * cam.scale).toBeCloseTo(h * .86);
    expect(GODDESS_HEIGHT * cam.scale).toBeLessThanOrEqual(h * .38 + 1e-8);
    expect(GODDESS_HEIGHT * cam.scale).toBeGreaterThan(h * .25);
    expect(75 * cam.scale).toBeLessThanOrEqual(w * .46 + 1e-8);
  });
  it.each([[1440, 1000], [390, 844]])('centres the model pupil, rather than the old face coordinates, at %i×%i', (w, h) => {
    const cam = portraitCamera(w, h, sceneAt(.72));
    expect(cam.x + (GODDESS_PUPIL.x - cam.focusX) * cam.scale).toBeCloseTo(w / 2);
    expect(cam.y + (GODDESS_PUPIL.y - cam.focusY) * cam.scale).toBeCloseTo(h / 2);
    expect(GODDESS_EYE_WIDTH * cam.scale).toBeCloseTo(Math.min(w, h) * .6);
    const end = portraitCamera(w, h, sceneAt(.90));
    expect(PORTRAIT_EYE.radius * end.scale).toBeGreaterThan(Math.hypot(w, h) / 2);
  });
  it('reverses to exactly the same view after scrubbing through the pupil', () => {
    const before = portraitCamera(390, 844, sceneAt(.57));
    for (const p of [.8, .3, .7, .5, .99]) portraitCamera(390, 844, sceneAt(p));
    expect(portraitCamera(390, 844, sceneAt(.57))).toEqual(before);
  });
});
