import { afterEach, describe, expect, it, vi } from 'vitest';
import { WATCH_KEY, openWatchList } from './watch';

function memoryStorage(): Storage {
  const m = new Map<string, string>();
  return {
    get length() { return m.size; },
    clear: () => m.clear(),
    getItem: (k) => (m.has(k) ? m.get(k)! : null),
    setItem: (k, v) => void m.set(k, String(v)),
    removeItem: (k) => void m.delete(k),
    key: (i) => [...m.keys()][i] ?? null,
  };
}

afterEach(() => vi.unstubAllGlobals());

describe('watch list', () => {
  it('keeps the followed stars in this browser', () => {
    const s = memoryStorage();
    vi.stubGlobal('localStorage', s);
    const w = openWatchList();
    expect(w.saved).toBe(true);
    expect(w.toggle('gaia:1')).toBe(true);
    expect(w.toggle('src:2')).toBe(true);
    expect(JSON.parse(s.getItem(WATCH_KEY)!)).toEqual(['gaia:1', 'src:2']);
    expect(openWatchList().has('src:2')).toBe(true);
    expect(w.toggle('gaia:1')).toBe(false);
    expect(openWatchList().keys()).toEqual(new Set(['src:2']));
  });

  it('works for the page when the browser keeps nothing', () => {
    vi.stubGlobal('localStorage', {
      getItem: () => { throw new Error('denied'); },
      setItem: () => { throw new Error('denied'); },
      removeItem: () => { throw new Error('denied'); },
    });
    const w = openWatchList();
    expect(w.saved).toBe(false);
    expect(w.toggle('gaia:1')).toBe(true);
    expect(w.has('gaia:1')).toBe(true);
  });

  it('ignores a damaged list', () => {
    const s = memoryStorage();
    s.setItem(WATCH_KEY, '{not json');
    vi.stubGlobal('localStorage', s);
    expect(openWatchList().keys().size).toBe(0);
    s.setItem(WATCH_KEY, JSON.stringify(['gaia:1', 7, null]));
    expect([...openWatchList().keys()]).toEqual(['gaia:1']);
  });
});
