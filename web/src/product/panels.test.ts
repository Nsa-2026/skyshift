// @vitest-environment jsdom
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {LAYOUT_KEY, dashboardKey, moveKey, readLayout, spanFor} from './panels';

describe('moveKey', () => {
  it('moves a dashboard earlier or later and keeps the others in order', () => {
    expect(moveKey(['a', 'b', 'c', 'd'], 'c', 0)).toEqual(['c', 'a', 'b', 'd']);
    expect(moveKey(['a', 'b', 'c', 'd'], 'a', 2)).toEqual(['b', 'c', 'a', 'd']);
    expect(moveKey(['a', 'b'], 'b', 9)).toEqual(['a', 'b']);
    expect(moveKey(['a', 'b'], 'a', -3)).toEqual(['a', 'b']);
  });
});

describe('spanFor', () => {
  it('rounds a width to whole grid columns, at least one and at most all', () => {
    expect(spanFor(300, 320, 16, 2)).toBe(1);
    expect(spanFor(560, 320, 16, 2)).toBe(2);
    expect(spanFor(2000, 320, 16, 2)).toBe(2);
    expect(spanFor(10, 320, 16, 2)).toBe(1);
    expect(spanFor(500, 0, 16, 2)).toBe(1);
  });
});

describe('readLayout', () => {
  // Node 26 shadows jsdom's localStorage with an undefined getter: install a small one.
  beforeEach(() => {
    const m = new Map<string, string>();
    vi.stubGlobal('localStorage', {getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, String(v)), removeItem: (k: string) => void m.delete(k), clear: () => m.clear()});
  });
  afterEach(() => vi.unstubAllGlobals());
  it('is empty without a stored layout or with a broken one', () => {
    expect(readLayout()).toEqual({order: [], size: {}});
    localStorage.setItem(LAYOUT_KEY, '{not json');
    expect(readLayout()).toEqual({order: [], size: {}});
  });
  it('keeps only valid keys and sizes', () => {
    localStorage.setItem(LAYOUT_KEY, JSON.stringify({order: ['spectrum', 3, 'motion'], size: {spectrum: {cols: 2, h: 400.4}, motion: {cols: 0, h: -1}}}));
    expect(readLayout()).toEqual({order: ['spectrum', 'motion'], size: {spectrum: {cols: 2, h: 400}, motion: {}}});
  });
});

describe('dashboardKey', () => {
  it('reads the card part, the workbench section or the science section', () => {
    const el = document.createElement('section');
    el.dataset.science = 'lightcurve';
    expect(dashboardKey(el)).toBe('lightcurve');
    el.dataset.cardDashboard = 'motion';
    expect(dashboardKey(el)).toBe('motion');
  });
});
