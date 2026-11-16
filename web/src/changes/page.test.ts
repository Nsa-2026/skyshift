import { afterEach, describe, expect, it, vi } from 'vitest';
import type { PageContext } from '../app/modules';
import sample from './fixtures/changes.sample.json';
import { loadChanges, mountChangesWith } from './index';
import { parseChangesFile, type ChangesFile } from './model';
import type { WatchList } from './watch';

const tick = () => new Promise((r) => setTimeout(r, 0));

function memoryWatch(saved = true): WatchList {
  const keys = new Set<string>();
  return {
    has: (k) => keys.has(k),
    toggle: (k) => (keys.has(k) ? keys.delete(k) : keys.add(k), keys.has(k)),
    keys: () => keys,
    saved,
  };
}

function ctx(): PageContext & { explore: ReturnType<typeof vi.fn> } {
  return { navigate: vi.fn(), explore: vi.fn() } as never;
}

async function mount(data: ChangesFile | null, watch = memoryWatch()) {
  const el = document.createElement('main');
  document.body.append(el);
  const c = ctx();
  const page = mountChangesWith(el, c, { load: async () => data, watch });
  page.show(null);
  await tick();
  return { el, c, page, watch };
}

afterEach(() => {
  document.body.innerHTML = '';
  vi.unstubAllGlobals();
});

describe('Changes page', () => {
  it('lists every change with what changed, the Gaia mark and a Follow button', async () => {
    const { el } = await mount(parseChangesFile(sample));
    const rows = el.querySelectorAll('.ch-row');
    expect(rows).toHaveLength(8);
    const first = rows[0];
    expect(first.querySelector('.ch-name')!.textContent).toBe('Gaia DR3 181458215025292160');
    expect(first.querySelector('.ch-headline')!.textContent).toBe('27% fainter in D5 (3.8–4.4 µm)');
    expect(first.querySelector('.ch-nums')!.textContent).toContain('20.7σ');
    expect(first.querySelector('.ch-var')).toBeNull();
    const last = rows[7];
    expect(last.querySelector('.ch-var')!.textContent).toMatch(/^Gaia already lists it as variable/);
    expect(el.querySelector<HTMLAnchorElement>('.ch-feed-link')!.getAttribute('href')).toBe('/data/changes.atom');
    expect(el.querySelector('.ch-lede')!.textContent).toContain('182,580 stars in 3 zones');
    expect(el.querySelector('.ch-scope')!.textContent).toBe('3 featured fields watched so far; the whole sky after the next rebuild.');
    const follow = first.querySelector<HTMLButtonElement>('button[data-follow]')!;
    expect(follow.getAttribute('aria-pressed')).toBe('false');
  });

  it('follows a star and shows only the followed ones', async () => {
    const { el, watch } = await mount(parseChangesFile(sample));
    const btn = () => el.querySelector<HTMLButtonElement>('.ch-row[data-id="pilot-ch-src63782006953200"] button[data-follow]')!;
    btn().click();
    expect(watch.has('src:63782006953200')).toBe(true);
    expect(btn().getAttribute('aria-pressed')).toBe('true');
    const following = el.querySelector<HTMLButtonElement>('button[data-filter="following"]')!;
    expect(following.textContent).toBe('Following (1)');
    following.click();
    expect(el.querySelectorAll('.ch-row')).toHaveLength(1);
    expect(el.querySelector<HTMLButtonElement>('button[data-filter="following"]')!.getAttribute('aria-pressed')).toBe('true');
    btn().click();
    expect(el.querySelectorAll('.ch-row')).toHaveLength(0);
    expect(el.querySelector('.ch-empty')!.textContent).toContain('You follow no star yet');
  });

  it('opens the Viewer on the star: months clock, its detector', async () => {
    const { el, c } = await mount(parseChangesFile(sample));
    const a = el.querySelector<HTMLAnchorElement>('.ch-row a.ch-open')!;
    expect(a.getAttribute('href')).toContain('clock=months');
    a.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 }));
    expect(c.explore).toHaveBeenCalledWith({ ra: 78.612175, dec: 32.800894, clock: 'months', det: 5, fov: 5 });
  });

  it('says plainly when no list is published', async () => {
    const { el } = await mount(null);
    expect(el.querySelector('.ch-row')).toBeNull();
    expect(el.querySelector('.ch-empty')!.textContent).toContain('No changes are published yet');
  });

  it('says when the browser will not keep the list', async () => {
    const { el } = await mount(parseChangesFile(sample), memoryWatch(false));
    expect(el.querySelector('.ch-note')!.textContent).toContain("won't keep your list");
  });

  it('marks the change a link points at', async () => {
    const { el, page } = await mount(parseChangesFile(sample));
    page.show('pilot-ch-src63782006953200');
    expect(el.querySelector('.ch-row.is-target')!.getAttribute('data-id')).toBe('pilot-ch-src63782006953200');
  });

  it('reads a missing file as no list', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('not found', { status: 404 })));
    expect(await loadChanges()).toBeNull();
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(sample), { status: 200 })));
    expect((await loadChanges())!.changes).toHaveLength(8);
  });
});
