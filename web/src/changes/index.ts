// Changes (#/changes, #/changes/<id>): known stars whose brightness or colour changed between SPHEREx visits.
// Reads /data/changes.json (pipeline changes_export.py); no file → a plain empty state. Each row opens the Viewer
// on the star (months clock, its detector). ☆ Follow keeps the star in this browser's watch list (watch.ts);
// "Following (N)" shows only those. The public Atom feed /data/changes.atom says the same for feed readers.
import './changes.css';
import type { Det } from '../api/types';
import type { MountPage, PageContext, PageHandle } from '../app/modules';
import {
  followedElsewhere,
  parseChangesFile,
  roundedFollows,
  starKey,
  visible,
  type Change,
  type ChangesFile,
  type ListFilter,
} from './model';
import { barHtml, emptyFileHtml, emptyListHtml, headHtml, noteHtml, rowHtml } from './render';
import { openWatchList, type WatchList } from './watch';

export const CHANGES_URL = '/data/changes.json';

/** The file, or null when it isn't published (404) or can't be read. */
export async function loadChanges(signal?: AbortSignal): Promise<ChangesFile | null> {
  try {
    const r = await fetch(CHANGES_URL, { signal, cache: 'no-cache' });
    if (!r.ok) return null;
    return parseChangesFile(await r.json());
  } catch (e) {
    if ((e as Error).name === 'AbortError') throw e;
    return null;
  }
}

export interface ChangesDeps {
  load?: (signal: AbortSignal) => Promise<ChangesFile | null>;
  watch?: WatchList;
}

export function mountChangesWith(el: HTMLElement, ctx: PageContext, deps: ChangesDeps = {}): PageHandle {
  const watch = deps.watch ?? openWatchList();
  const load = deps.load ?? loadChanges;
  let data: ChangesFile | null = null;
  let loaded = false;
  let filter: ListFilter = 'all';
  let target: string | null = null;
  const ctl = new AbortController();
  el.innerHTML = `<div class="ch" aria-busy="true">${headHtml(null)}<p class="ch-loading" role="status">Loading the list…</p></div>`;
  const root = el.querySelector<HTMLElement>('.ch')!;

  function draw(): void {
    root.removeAttribute('aria-busy');
    if (!data) {
      root.innerHTML = headHtml(null) + emptyFileHtml();
      return;
    }
    const keys = watch.keys();
    const followed = data.changes.filter((c) => keys.has(starKey(c))).length;
    const rows = visible(data.changes, filter, keys);
    const elsewhere = followedElsewhere(data.changes, keys);
    root.innerHTML = headHtml(data) + barHtml(data.changes.length, followed + elsewhere, filter)
      + noteHtml(watch.saved, elsewhere, filter, rows.length)
      + (rows.length ? `<ol class="ch-list">${rows.map((c) => rowHtml(c, keys.has(starKey(c)))).join('')}</ol>`
        : emptyListHtml(filter, elsewhere));
    if (target) focusRow(target);
  }

  function rowOf(id: string): HTMLElement | undefined {
    return [...root.querySelectorAll<HTMLElement>('.ch-row')].find((r) => r.dataset.id === id);
  }

  function focusRow(id: string): void {
    const row = rowOf(id);
    root.querySelectorAll('.ch-row.is-target').forEach((r) => r.classList.remove('is-target'));
    if (!row) return;
    row.classList.add('is-target');
    row.scrollIntoView?.({ block: 'center' });
    row.querySelector<HTMLElement>('.ch-open')?.focus({ preventScroll: true });
  }

  function byId(id: string): Change | undefined {
    return data?.changes.find((c) => c.id === id);
  }

  root.addEventListener('click', (ev) => {
    const t = ev.target as Element;
    const f = t.closest<HTMLButtonElement>('button[data-filter]');
    if (f) {
      filter = f.dataset.filter === 'following' ? 'following' : 'all';
      target = null;
      draw();
      root.querySelector<HTMLButtonElement>(`button[data-filter="${filter}"]`)?.focus();
      return;
    }
    const b = t.closest<HTMLButtonElement>('button[data-follow]');
    if (b) {
      const key = b.dataset.follow!;
      const id = b.closest<HTMLElement>('.ch-row')?.dataset.id ?? null;
      watch.toggle(key);
      draw();
      const again = id ? rowOf(id)?.querySelector<HTMLButtonElement>('button[data-follow]') : null;
      (again ?? root.querySelector<HTMLButtonElement>('button[data-filter="following"]'))?.focus();
      return;
    }
    const a = t.closest<HTMLAnchorElement>('a[data-open]');
    if (a) {
      const me = ev as MouseEvent;
      if (me.button !== 0 || me.metaKey || me.ctrlKey || me.shiftKey || me.altKey) return;  // a new tab keeps the link
      const c = byId(a.dataset.open!);
      if (!c) return;
      ev.preventDefault();
      ctx.explore({ ra: c.ra, dec: c.dec, clock: 'months', det: c.det as Det, fov: 5 });
    }
  });

  const ready = load(ctl.signal).then((d) => {
    data = d;
    // follows saved under a rounded Gaia id (before v3.27) move to the star's exact key
    if (data) for (const [old, now] of roundedFollows(data.changes, watch.keys())) { watch.toggle(old); watch.toggle(now); }
    loaded = true;
    draw();
  }).catch((e) => {
    if ((e as Error).name === 'AbortError') return;
    loaded = true;
    draw();
  });

  return {
    show(param) {
      target = param;
      if (loaded) {
        if (target && filter === 'following' && byId(target) && !watch.has(starKey(byId(target)!))) filter = 'all';
        draw();
      } else void ready.then(() => { if (target) focusRow(target); });
    },
    destroy() {
      ctl.abort();
      el.innerHTML = '';
    },
  };
}

export const mountChanges: MountPage = (el, ctx) => mountChangesWith(el, ctx);
