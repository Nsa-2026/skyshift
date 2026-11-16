import { describe, expect, it } from 'vitest';
import sample from './fixtures/changes.sample.json';
import {
  falseAlarmRange,
  followedElsewhere,
  numbers,
  parseChange,
  parseChangesFile,
  roundedFollows,
  sortChanges,
  starKey,
  variableLabel,
  viewerHref,
  visible,
  watchedStars,
  when,
} from './model';

const file = parseChangesFile(sample);

describe('changes.json', () => {
  it('reads the real sample', () => {
    expect(file.changes).toHaveLength(8);
    expect(file.zones).toEqual(['pilot', 't_rdra', 't_rwaur']);
    expect(watchedStars(file)).toBe(45559 + 38438 + 98583);
    expect(falseAlarmRange(file)).toBe('0.12–0.33 per 1,000 stars');
    const c = file.changes[0];
    expect(c.name).toBe('Gaia DR3 181458215025292160');
    expect(c.det).toBe(5);
    expect(c.checks.every((k) => ['strong', 'warn', 'ok'].includes(k.level))).toBe(true);
  });

  it('puts the stars Gaia already lists as variable after the unexpected ones', () => {
    const v = file.changes.map((c) => c.gaia_variable === true);
    expect(v.indexOf(true)).toBeGreaterThan(v.lastIndexOf(false));
    const shuffled = sortChanges([...file.changes].reverse());
    expect(shuffled.map((c) => c.id)).toEqual(file.changes.map((c) => c.id));
  });

  it('skips rows it cannot show and reads missing fields as unknown', () => {
    expect(parseChange({ id: 'x', kind: 'teleported', ra: 1, dec: 2 })).toBeNull();
    expect(parseChange({ id: 'x', kind: 'brightness', dec: 2 })).toBeNull();
    const c = parseChange({ id: 'z-ch-1', kind: 'brightness', ra: 10, dec: -5, det: 9, gaia_variable: 'yes',
      checks: [{ code: 'a', level: 'odd', text: 'measured' }, { code: 'b' }] })!;
    expect(c.det).toBe(2);
    expect(c.gaia_variable).toBeNull();
    expect(c.checks).toEqual([{ code: 'a', level: 'warn', text: 'measured', look: '', values: null }]);
    expect(() => parseChangesFile({ candidates: [] })).toThrow();
  });

  it('follows a star, not one change of it', () => {
    const [a, b] = file.changes;
    expect(starKey(a)).toBe('gaia:181458215025292160');
    const vanished = file.changes.find((c) => c.kind === 'vanished')!;
    expect(starKey(vanished)).toBe('src:63782006953200');
    const catwise = file.changes.find((c) => c.name.startsWith('CatWISE'))!;
    expect(starKey(catwise)).toBe('catwise:J081628.26+142456.2');
    const following = new Set([starKey(b), 'gaia:42']);
    expect(visible(file.changes, 'following', following).map((c) => c.id)).toEqual([b.id]);
    expect(visible(file.changes, 'all', following)).toHaveLength(8);
    expect(followedElsewhere(file.changes, following)).toBe(1);
  });

  it('reads gaia_id as a string, every digit (contract v3.27)', () => {
    const row = { id: 'z-ch-gaia160747814004063488', kind: 'brightness', ra: 1, dec: 2 };
    expect(parseChange({ ...row, gaia_id: '160747814004063488' })!.gaia_id).toBe('160747814004063488');
    // an older file's number: JavaScript has rounded it; ref_id gives the digits back
    const old = parseChange({ ...row, gaia_id: 160747814004063488, ref_id: 'gaia:160747814004063488' })!;
    expect(old.gaia_id).toBe('160747814004063488');
    expect(starKey(old)).toBe('gaia:160747814004063488');
    expect(parseChange({ ...row, gaia_id: 'x1' })!.gaia_id).toBeNull();
    expect(parseChange({ ...row, gaia_id: 1.5 })!.gaia_id).toBeNull();
    expect(file.changes.filter((c) => c.gaia_id != null).every((c) => c.ref_id === `gaia:${c.gaia_id}`)).toBe(true);
  });

  it('moves a follow saved under a rounded Gaia id to the star\'s exact key', () => {
    const c = parseChange({ id: 'z-ch-g', kind: 'brightness', ra: 1, dec: 2, gaia_id: '160747814004063488' })!;
    const rounded = `gaia:${String(Number('160747814004063488'))}`;     // what the page saved before v3.27
    expect(rounded).not.toBe('gaia:160747814004063488');
    expect(roundedFollows([c], new Set([rounded, 'gaia:42']))).toEqual([[rounded, 'gaia:160747814004063488']]);
    expect(roundedFollows([c], new Set(['gaia:160747814004063488']))).toEqual([]);
  });

  it('opens the Viewer on the months clock in the change\'s detector', () => {
    expect(viewerHref({ ra: 78.612175, dec: 32.800894, det: 5 })).toBe('?ra=78.61217&dec=32.80089&clock=months&fov=5&det=5#/explore');
  });

  it('says the numbers and the dates in plain words', () => {
    const c = file.changes[0];
    expect(numbers(c)).toBe('1.14 → 0.83 of its usual brightness in D5 · 20.7σ');
    expect(when(c)).toBe('visits 1–2 (6 Mar 2026) → visit 3 (12 Sep 2026)');
    const v = file.changes.find((x) => x.kind === 'vanished')!;
    expect(numbers(v)).toBe('S/N 61 · D1 D2 D3 D4 D5');
    expect(variableLabel(file.changes[7])).toMatch(/^Gaia already lists it as variable/);
    expect(variableLabel(c)).toBeNull();
  });
});
