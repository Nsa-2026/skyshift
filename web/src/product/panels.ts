// The Viewer's movable panels. Advanced dashboards: drag one by its header to move it, drag its
// corner to resize it (width in grid columns, height free); the arrow keys do the same on the
// header's handle and the corner. The plate's column: drag the divider beside it to make the plate
// larger or smaller. Layouts are remembered in this browser (localStorage, per-viewer convenience
// only); "Reset layout" and a double-click on the divider undo them.

export const LAYOUT_KEY = 'skyshift:dashboard-layout';
export const PLATE_KEY = 'skyshift:plate-column';

export interface PanelSize { cols?: number; h?: number }
export interface DashboardLayout { order: string[]; size: Record<string, PanelSize> }

const empty = (): DashboardLayout => ({order: [], size: {}});

function readJson<T>(key: string): T | null {
  try { const v = localStorage.getItem(key); return v ? JSON.parse(v) as T : null; } catch { return null; }
}
function writeJson(key: string, value: unknown): void {
  try { if (value == null) localStorage.removeItem(key); else localStorage.setItem(key, JSON.stringify(value)); } catch { /* storage blocked: this page only */ }
}

export function readLayout(): DashboardLayout {
  const v = readJson<Partial<DashboardLayout>>(LAYOUT_KEY);
  const order = Array.isArray(v?.order) ? v!.order.filter((k): k is string => typeof k === 'string') : [];
  const size: Record<string, PanelSize> = {};
  if (v?.size && typeof v.size === 'object') {
    for (const [k, s] of Object.entries(v.size)) {
      const cols = Number((s as PanelSize)?.cols), h = Number((s as PanelSize)?.h);
      size[k] = {...(cols >= 1 ? {cols: Math.round(cols)} : {}), ...(h > 0 ? {h: Math.round(h)} : {})};
    }
  }
  return {order, size};
}

/** A dashboard's key: its card part, workbench section or science section. */
export function dashboardKey(el: HTMLElement): string | null {
  return el.dataset.cardDashboard ?? el.dataset.wb ?? el.dataset.science ?? null;
}

/** Move `key` to `index` in the visible order `keys`, keeping the others' relative order. */
export function moveKey(keys: readonly string[], key: string, index: number): string[] {
  const rest = keys.filter((k) => k !== key);
  const at = Math.max(0, Math.min(rest.length, index));
  return [...rest.slice(0, at), key, ...rest.slice(at)];
}

/** Columns a panel `width` px wide covers in a grid of `cols` columns `colW` px wide, `gap` apart. */
export function spanFor(width: number, colW: number, gap: number, cols: number): number {
  if (!(colW > 0)) return 1;
  return Math.max(1, Math.min(cols, Math.round((width + gap) / (colW + gap))));
}

export const MIN_PANEL_H = 120;

/**
 * Dashboards in `grid` become movable and resizable. `repack` re-measures the masonry grid.
 * Returns reset() (back to the default layout) and apply() (after the dashboards change).
 */
export function mountDashboardLayout(grid: HTMLElement, repack: () => void): {reset(): void; apply(): void} {
  let layout = readLayout();
  const save = () => writeJson(LAYOUT_KEY, layout.order.length || Object.keys(layout.size).length ? layout : null);

  const dashboards = (): HTMLElement[] => [...grid.querySelectorAll<HTMLElement>('.wb-dashboard')].filter((el) => !!dashboardKey(el));
  const visible = (): HTMLElement[] => dashboards().filter((el) => !el.hidden && el.offsetParent !== null);
  const enabled = () => grid.classList.contains('wb-dashboard-grid');
  /** Visible dashboards in the order they are shown (CSS order, then document order). */
  function shown(): HTMLElement[] {
    return visible().map((el, i) => ({el, i, o: Number(getComputedStyle(el).order) || 0})).sort((a, b) => a.o - b.o || a.i - b.i).map((x) => x.el);
  }
  function columns(): {n: number; colW: number; gap: number} {
    const cs = getComputedStyle(grid);
    const tracks = cs.gridTemplateColumns.split(' ').filter((t) => t.endsWith('px'));
    const gap = parseFloat(cs.columnGap) || 0;
    return {n: Math.max(1, tracks.length), colW: parseFloat(tracks[0] ?? '0') || grid.clientWidth, gap};
  }

  function decorate(el: HTMLElement): void {
    const key = dashboardKey(el)!;
    const head = el.querySelector<HTMLElement>(':scope > .wb-dashboard-head');
    if (head && !head.querySelector('.wb-move')) {
      const label = head.querySelector('h2')?.textContent ?? key;
      const move = document.createElement('button');
      move.type = 'button';
      move.className = 'wb-move';
      move.setAttribute('aria-label', `Move the ${label} dashboard (arrow keys)`);
      move.title = 'Drag to move';
      move.innerHTML = '<span aria-hidden="true">⋮⋮</span>';
      head.prepend(move);
      head.classList.add('wb-draggable');
      head.title = 'Drag to move';
    }
    if (!el.querySelector(':scope > .wb-grip')) {
      const grip = document.createElement('button');
      grip.type = 'button';
      grip.className = 'wb-grip';
      grip.setAttribute('aria-label', `Resize the ${head?.querySelector('h2')?.textContent ?? key} dashboard (arrow keys; double-click to reset)`);
      grip.title = 'Drag to resize · double-click to reset';
      el.append(grip);
    }
  }

  function apply(): void {
    const on = enabled();
    const all = dashboards();
    for (const el of all) {
      decorate(el);
      const key = dashboardKey(el)!;
      const idx = layout.order.indexOf(key);
      el.style.order = on && layout.order.length ? String(idx >= 0 ? idx : 100) : '';
      const s = layout.size[key];
      el.style.gridColumn = on && s?.cols ? `span ${Math.min(s.cols, columns().n)}` : '';
      el.style.height = on && s?.h ? `${s.h}px` : '';
      el.classList.toggle('wb-sized', on && !!s?.h);
    }
    repack();
  }

  function setOrder(keys: string[]): void {
    // Keep keys of dashboards not open now, after the open ones, so their place is remembered.
    const hidden = layout.order.filter((k) => !keys.includes(k));
    layout = {...layout, order: [...keys, ...hidden]};
    apply();
  }

  // ---- moving: drag the header, or arrow keys on its handle ----
  let drag: {el: HTMLElement; key: string; id: number} | null = null;
  grid.addEventListener('pointerdown', (e) => {
    const head = (e.target as Element).closest<HTMLElement>('.wb-draggable');
    if (!head || !enabled() || e.button !== 0) return;
    if ((e.target as Element).closest('button:not(.wb-move), a, select, input, label')) return;
    const el = head.closest<HTMLElement>('.wb-dashboard');
    const key = el && dashboardKey(el);
    if (!el || !key) return;
    e.preventDefault();
    drag = {el, key, id: e.pointerId};
    head.setPointerCapture?.(e.pointerId);
    el.classList.add('wb-dragging');
    document.documentElement.classList.add('wb-moving');
  });
  grid.addEventListener('pointermove', (e) => {
    if (!drag || e.pointerId !== drag.id) return;
    const order = shown();
    const others = order.filter((x) => x !== drag!.el);
    // The dashboard under the pointer, or the nearest one; before it when the pointer is on its first half.
    let best: HTMLElement | null = null, bestD = Infinity, before = true;
    for (const el of others) {
      const r = el.getBoundingClientRect();
      const dx = Math.max(r.left - e.clientX, 0, e.clientX - r.right), dy = Math.max(r.top - e.clientY, 0, e.clientY - r.bottom);
      const d = dx * dx + dy * dy;
      if (d < bestD) { bestD = d; best = el; before = r.width > r.height * 1.5 ? e.clientY < r.top + r.height / 2 : e.clientX < r.left + r.width / 2 && e.clientY < r.bottom; }
    }
    if (!best) return;
    const keys = order.map((x) => dashboardKey(x)!);
    const target = others.indexOf(best) + (before ? 0 : 1);
    const next = moveKey(keys, drag.key, target);
    if (next.join() !== keys.join()) setOrder(next);
  });
  const endDrag = (e: PointerEvent) => {
    if (!drag || e.pointerId !== drag.id) return;
    drag.el.classList.remove('wb-dragging');
    document.documentElement.classList.remove('wb-moving');
    drag = null;
    save();
  };
  grid.addEventListener('pointerup', endDrag);
  grid.addEventListener('pointercancel', endDrag);

  grid.addEventListener('keydown', (e) => {
    const move = (e.target as Element).closest<HTMLButtonElement>('.wb-move');
    const grip = (e.target as Element).closest<HTMLButtonElement>('.wb-grip');
    if (!move && !grip) return;
    const el = (move ?? grip)!.closest<HTMLElement>('.wb-dashboard');
    const key = el && dashboardKey(el);
    if (!el || !key || !enabled()) return;
    const dir = {ArrowUp: -1, ArrowLeft: -1, ArrowDown: 1, ArrowRight: 1}[e.key];
    if (!dir) return;
    e.preventDefault();
    if (move) {
      const keys = shown().map((x) => dashboardKey(x)!);
      setOrder(moveKey(keys, key, keys.indexOf(key) + dir));
      move.focus({preventScroll: true});
    } else {
      const {n} = columns();
      const r = el.getBoundingClientRect();
      const s = {...layout.size[key]};
      if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') s.cols = Math.max(1, Math.min(n, (s.cols ?? Math.min(n, spanOf(el))) + dir));
      else s.h = Math.max(MIN_PANEL_H, Math.round((s.h ?? r.height) + dir * 40));
      layout = {...layout, size: {...layout.size, [key]: s}};
      apply();
      save();
    }
  });
  const spanOf = (el: HTMLElement): number => {
    const {colW, gap, n} = columns();
    return spanFor(el.getBoundingClientRect().width, colW, gap, n);
  };

  // ---- resizing: drag the corner ----
  let size: {el: HTMLElement; key: string; id: number; x: number; y: number; w: number; h: number} | null = null;
  grid.addEventListener('pointerdown', (e) => {
    const grip = (e.target as Element).closest<HTMLElement>('.wb-grip');
    if (!grip || !enabled() || e.button !== 0) return;
    const el = grip.closest<HTMLElement>('.wb-dashboard');
    const key = el && dashboardKey(el);
    if (!el || !key) return;
    e.preventDefault();
    const r = el.getBoundingClientRect();
    size = {el, key, id: e.pointerId, x: e.clientX, y: e.clientY, w: r.width, h: r.height};
    grip.setPointerCapture?.(e.pointerId);
    el.classList.add('wb-resizing');
    document.documentElement.classList.add('wb-sizing');
  });
  grid.addEventListener('pointermove', (e) => {
    if (!size || e.pointerId !== size.id) return;
    const {colW, gap, n} = columns();
    const cols = spanFor(size.w + e.clientX - size.x, colW, gap, n);
    const h = Math.max(MIN_PANEL_H, Math.round(size.h + e.clientY - size.y));
    layout = {...layout, size: {...layout.size, [size.key]: {cols, h}}};
    apply();
  });
  const endSize = (e: PointerEvent) => {
    if (!size || e.pointerId !== size.id) return;
    size.el.classList.remove('wb-resizing');
    document.documentElement.classList.remove('wb-sizing');
    size = null;
    save();
  };
  grid.addEventListener('pointerup', endSize);
  grid.addEventListener('pointercancel', endSize);
  grid.addEventListener('dblclick', (e) => {
    const grip = (e.target as Element).closest<HTMLElement>('.wb-grip');
    const el = grip?.closest<HTMLElement>('.wb-dashboard');
    const key = el && dashboardKey(el);
    if (!key) return;
    const {[key]: _gone, ...rest} = layout.size;
    layout = {...layout, size: rest};
    apply();
    save();
  });

  // Dashboards are re-rendered (the card's parts): decorate and size the new ones.
  let queued = 0;
  if (typeof MutationObserver !== 'undefined') {
    new MutationObserver(() => {
      cancelAnimationFrame(queued);
      queued = requestAnimationFrame(() => { if (!drag && !size) apply(); });
    }).observe(grid, {childList: true, subtree: true});
  }
  apply();
  return {
    reset() { layout = empty(); save(); apply(); },
    apply,
  };
}

/**
 * The divider between the plate's column and the rest (two-column screens only). Dragging it sets
 * the plate column's width on `page` (--vw-plate-col and data-plate-col); double-click resets.
 */
export function mountPlateDivider(page: HTMLElement, side: HTMLElement): void {
  /** The plate column's width now: the page grid's first track. */
  const current = () => parseFloat(getComputedStyle(page).gridTemplateColumns.split(' ')[0] ?? '') || 0;
  const bar = document.createElement('div');
  bar.className = 'vw-split';
  bar.setAttribute('role', 'separator');
  bar.setAttribute('aria-orientation', 'vertical');
  bar.setAttribute('aria-label', 'Plate size: drag, or use the arrow keys; double-click to reset');
  bar.tabIndex = 0;
  bar.title = 'Drag to resize the plate · double-click to reset';
  bar.innerHTML = '<span class="vw-split-knob" aria-hidden="true"></span>';
  side.prepend(bar);

  /** The widest column now: the largest plate that fits the window (app.css clamps the same way,
   *  so a stored width from a larger window still fits), and room for the evidence beside it. */
  const widest = () => {
    const total = page.getBoundingClientRect().width;
    return total > 0 ? Math.max(300, Math.min(total - 380, window.innerHeight - 72)) : Infinity;
  };
  function set(px: number | null, persist = true): void {
    if (px == null) {
      page.style.removeProperty('--vw-plate-col');
      delete page.dataset.plateCol;
    } else {
      const w = Math.round(Math.max(300, Math.min(widest(), px)));
      page.style.setProperty('--vw-plate-col', `${w}px`);
      page.dataset.plateCol = String(w);
      bar.setAttribute('aria-valuenow', String(w));
    }
    if (persist) writeJson(PLATE_KEY, px == null ? null : Number(page.dataset.plateCol));
    window.dispatchEvent(new Event('resize'));
  }
  const saved = readJson<number>(PLATE_KEY);
  if (typeof saved === 'number' && saved > 0) set(saved, false);

  let drag: {id: number; x: number; w: number} | null = null;
  bar.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    drag = {id: e.pointerId, x: e.clientX, w: current()};
    bar.setPointerCapture?.(e.pointerId);
    document.documentElement.classList.add('vw-splitting');
  });
  bar.addEventListener('pointermove', (e) => {
    if (!drag || e.pointerId !== drag.id) return;
    set(drag.w + e.clientX - drag.x, false);
  });
  const end = (e: PointerEvent) => {
    if (!drag || e.pointerId !== drag.id) return;
    drag = null;
    document.documentElement.classList.remove('vw-splitting');
    set(Number(page.dataset.plateCol) || null);
  };
  bar.addEventListener('pointerup', end);
  bar.addEventListener('pointercancel', end);
  bar.addEventListener('dblclick', () => set(null));
  bar.addEventListener('keydown', (e) => {
    const dir = {ArrowLeft: -1, ArrowRight: 1}[e.key];
    if (!dir) return;
    e.preventDefault();
    set(current() + dir * 40);
  });
}
