import { describe, expect, it } from 'vitest';
import { hasSeen, rememberIntro, sceneAt, scrollProgress, SEEN_KEY } from './model';
import { entryRoute } from './routing';
import { readView } from '../app/state';

describe('scroll narrative', () => {
  it('maps native scrolling to the entire timeline, clamping overscroll and short pages', () => {
    expect(scrollProgress(0, 800, 5200)).toBe(0);
    expect(scrollProgress(2200, 800, 5200)).toBe(.5);
    expect(scrollProgress(4600, 800, 5200)).toBe(1);
    expect(scrollProgress(-90, 800, 5200)).toBe(0);
    expect(scrollProgress(0, 800, 800)).toBe(0);
  });
  it('can scrub in both directions without accumulated state, overshoot, or jumps at cuts', () => {
    const before = sceneAt(.64);
    [.9, .1, 1, .32, .7].forEach(sceneAt);
    expect(sceneAt(.64)).toEqual(before);
    for (const boundary of [0, .25, .45, .70, .90, .925, .95, .975, 1]) {
      const a = sceneAt(boundary - 1e-7), b = sceneAt(boundary + 1e-7);
      for (const key of ['earth', 'ground', 'girl', 'face', 'eye', 'pupil', 'clouds'] as const) {
        expect(Math.abs(a[key] - b[key])).toBeLessThan(.001);
        expect(b[key]).toBeGreaterThanOrEqual(0); expect(b[key]).toBeLessThanOrEqual(1);
      }
    }
  });
  it('reveals panels only at the end and completes every panel', () => {
    expect(sceneAt(.89).panels).toEqual([0, 0, 0, 0]);
    expect(sceneAt(.94).panels[0]).toBe(1);
    expect(sceneAt(.94).panels[1]).toBeGreaterThan(0);
    expect(sceneAt(.94).panels.slice(2)).toEqual([0, 0]);
    expect(sceneAt(1).panels).toEqual([1, 1, 1, 1]);
    expect(sceneAt(1).girl).toBe(0);
  });
});

describe('entry routing and remembered completion', () => {
  it.each(['', '#', '#/'])('shows the intro on a fresh home visit: %s', hash => {
    expect(entryRoute(hash, '', false, false).page).toBe('intro');
  });
  it.each(['?target=wise0855', '?t=barnard', '?ra=12&dec=-20', '?embed=1'])('opens shared views immediately: %s', query => {
    expect(entryRoute('#/', query, false, false).page).toBe('explore');
  });
  it.each(['explore', 'learn', 'play', 'tools', 'live', 'review'])('keeps the explicit %s route', route => {
    expect(entryRoute(`#/${route}`, '?target=wise0855', true, true).page).toBe(route);
  });
  it('keeps sky and candidate permalink arguments', () => {
    expect(entryRoute('#/sky/125,19,360,mol', '', false, false)).toEqual({ page: 'atlas', param: '125,19,360,mol' });
    expect(entryRoute('#/c/a%2Fb', '', false, false)).toEqual({ page: 'candidate', param: 'a/b' });
    expect(entryRoute('#/c/%ZZ', '', false, false).page).toBe('discover');
  });
  it('skips seen visits and reduced motion without requiring storage access', () => {
    expect(entryRoute('#/', '', true, false).page).toBe('explore');
    expect(entryRoute('#/', '', false, true).page).toBe('explore');
    const denied = { getItem() { throw Error('denied'); }, setItem() { throw Error('denied'); } };
    expect(hasSeen(denied)).toBe(false);
    expect(() => rememberIntro(denied)).not.toThrow();
  });
  it('records only an explicit completion call and supports target aliases without losing view options', () => {
    const values = new Map<string, string>();
    const store = { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => { values.set(key, value); } };
    expect(hasSeen(store)).toBe(false); expect(values.size).toBe(0);
    rememberIntro(store); expect(values.get(SEEN_KEY)).toBe('1'); expect(hasSeen(store)).toBe(true);
    expect(readView('?target=wise0855&det=5&clock=hours&fov=20')).toMatchObject({ slug: 'wise0855', det: 5, clock: 'hours', fov: 20 });
    expect(readView('?t=barnard')?.slug).toBe('barnard');
    expect(readView('?target=missing&t=barnard')?.slug).toBe('barnard');
  });
});
