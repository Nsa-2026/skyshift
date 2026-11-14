import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mountEntryDashboard } from './dashboard';
import { candidateSet, toCandidateSet } from '../review/data';
import type { CandidatesResponse } from '../api/types';
vi.mock('../api/client', () => ({ loadCandidates: vi.fn(), ALLOWED_FOV: { months: [5, 15], hours: [20, 60] }, DEFAULT_FOV: { months: 5, hours: 20 } }));
vi.mock('../review/data', async importOriginal => {
  const original = await importOriginal<typeof import('../review/data')>();
  return { toCandidateSet: original.toCandidateSet, candidateSet: vi.fn() };
});

describe('real entry dashboard', () => {
  let root: HTMLElement, dashboard: ReturnType<typeof mountEntryDashboard>;
  const target = vi.fn(), replay = vi.fn();
  beforeEach(() => {
    vi.clearAllMocks();
    root = document.createElement('div');
    root.innerHTML = '<header class="top"><input id="q"></header><div class="featured"></div><p class="find-msg"></p><main data-page="explore"><section class="stage"><canvas id="original-player"></canvas></section></main>';
    document.body.append(root);
    vi.mocked(candidateSet).mockResolvedValue(null);
    dashboard = mountEntryDashboard(root, { target, replay });
  });
  afterEach(() => { dashboard.destroy(); root.remove(); });
  it('reuses the actual search/player and preserves other-page header placement', () => {
    const search = root.querySelector('#q'), player = root.querySelector('#original-player');
    dashboard.active(true);
    expect(root.querySelector('.entry-header #q')).toBe(search);
    expect(root.querySelector('.entry-player canvas')).toBe(player);
    expect(root.querySelectorAll('[data-target]')).toHaveLength(5);
    root.querySelector<HTMLButtonElement>('[data-target="wise0855"]')!.click();
    expect(target).toHaveBeenCalledWith('wise0855');
    dashboard.select('wise0855'); expect(root.querySelector('[data-target="wise0855"]')?.getAttribute('aria-pressed')).toBe('true');
    dashboard.active(false); expect(root.querySelector(':scope > .top #q')).toBe(search);
  });
  it('keeps preview controls inert, then activates the same elements at handoff', () => {
    dashboard.progress(.94, true);
    const shell = root.querySelector<HTMLElement>('.entry-dashboard')!;
    expect(shell.inert).toBe(true); expect(shell.getAttribute('aria-hidden')).toBe('true');
    dashboard.progress(1, false);
    expect(shell.inert).toBe(false); expect(root.querySelector<HTMLElement>('[data-reveal="3"]')!.style.opacity).toBe('1');
  });
  it('uses returned candidates with encoded links and explicit unconfirmed labels', async () => {
    vi.mocked(candidateSet).mockResolvedValue(toCandidateSet({ candidates: [{ id: 'real/test-1', kind: 'mover', ra: 12, dec: -3 }] } as CandidatesResponse, false));
    dashboard.active(true); await vi.waitFor(() => expect(root.textContent).toContain('1 candidate signals'));
    expect(root.textContent).toContain('not confirmed discoveries');
    expect(root.querySelector('.entry-candidate-content a')?.getAttribute('href')).toBe('#/c/real%2Ftest-1');
  });
  it('shows a recoverable failure instead of fake discoveries', async () => {
    dashboard.active(true); await vi.waitFor(() => expect(root.textContent).toContain('candidate list is unavailable'));
    expect(root.querySelectorAll('.entry-candidate-content li')).toHaveLength(0);
    root.querySelector<HTMLButtonElement>('.entry-retry')!.click();
    await vi.waitFor(() => expect(candidateSet).toHaveBeenCalledTimes(2));
  });
});
