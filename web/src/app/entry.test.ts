import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { startApp } from './app';
import { SEEN_KEY } from '../intro/model';
import { loadFrames } from '../api/client';

const mounts = vi.hoisted(() => ({
  blink: { active: vi.fn(), status: vi.fn(), show: vi.fn(async () => {}), highlight: vi.fn() },
  learn: vi.fn(() => ({ show: vi.fn(), hide: vi.fn(), destroy: vi.fn() })),
  play: vi.fn(() => ({ show: vi.fn(), hide: vi.fn(), destroy: vi.fn() })),
}));
vi.mock('../blink', () => ({ mountBlink: (el: HTMLElement) => { el.innerHTML = '<div class="bk"></div>'; return mounts.blink; } }));
vi.mock('../card', () => ({ mountCard: () => ({ status: vi.fn(), show: vi.fn() }) }));
vi.mock('../sky', () => ({ mountSky: () => ({ epoch: vi.fn(), goto: vi.fn() }) }));
vi.mock('../vote', () => ({ mountVote: () => ({ view: vi.fn(), pick: vi.fn() }) }));
vi.mock('../prefs/details', () => ({ mountDetailsSwitch: vi.fn() }));
vi.mock('./observatory', () => ({ mountObservatory: () => ({ active: vi.fn(), view: vi.fn(), panel: vi.fn() }) }));
vi.mock('../product/workbench', () => ({ mountWorkbench: () => ({ active: vi.fn(), select: vi.fn(), object: vi.fn() }), targetFor: vi.fn() }));
vi.mock('../intro/scene', () => ({ paintScene: vi.fn() }));
vi.mock('../intro/sky', () => ({ mountEntrySky: () => ({ active: vi.fn(), progress: vi.fn() }), observationTexture: () => new Promise(() => {}) }));
vi.mock('../review/data', () => ({ candidateSet: async () => null }));
vi.mock('../learn', () => ({ mountLearn: mounts.learn }));
vi.mock('../play/page', () => ({ mountPlay: mounts.play }));
vi.mock('../discover', () => ({ mountDiscover: vi.fn() }));
vi.mock('../review', () => ({ mountReview: vi.fn(), mountCandidate: vi.fn() }));
vi.mock('../community', () => ({ mountAbout: vi.fn(), mountCommunity: vi.fn() }));
vi.mock('../tools', () => ({ mountTools: vi.fn() }));
vi.mock('../live', () => ({ mountLive: vi.fn() }));
vi.mock('../score', () => ({ mountScore: vi.fn() }));
vi.mock('../api/client', async importOriginal => ({
  ...await importOriginal<typeof import('../api/client')>(),
  loadFrames: vi.fn(async () => ({ frames: { epochs: [], gaia: [] }, jsonUrl: '/data/barnard/frames.json' })),
  loadObject: vi.fn(async () => ({})),
}));

describe('entry to existing application integration', () => {
  let root: HTMLElement;
  let listeners: Array<[string, EventListenerOrEventListenerObject]>;
  beforeEach(() => {
    vi.clearAllMocks(); vi.useFakeTimers();
    history.replaceState(null, '', '/'); localStorage.removeItem(SEEN_KEY);
    vi.stubGlobal('scrollTo', vi.fn());
    vi.stubGlobal('requestAnimationFrame', (fn: () => void) => setTimeout(fn, 16));
    vi.stubGlobal('cancelAnimationFrame', (id: number) => clearTimeout(id));
    vi.stubGlobal('matchMedia', () => Object.assign(new EventTarget(), { matches: false }));
    listeners = [];
    const add = window.addEventListener.bind(window);
    vi.spyOn(window, 'addEventListener').mockImplementation((type, listener, options) => {
      listeners.push([type, listener]); add(type, listener, options);
    });
    root = document.createElement('div'); root.id = 'app'; document.body.append(root);
  });
  afterEach(() => {
    listeners.forEach(([type, listener]) => window.removeEventListener(type, listener));
    document.body.replaceChildren(); document.documentElement.classList.remove('entry-intro-open');
    vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers();
    localStorage.removeItem(SEEN_KEY);
  });
  it('prepares one real player without navigating, hands it off once and retains Learn/Play', async () => {
    startApp(root);
    expect(root.dataset.page).toBe('intro'); expect(loadFrames).not.toHaveBeenCalled();
    const el = root.querySelector<HTMLElement>('.entry-intro')!;
    Object.defineProperties(el, { clientHeight: { value: 800 }, scrollHeight: { value: 5200 } });
    el.scrollTop = 3520; el.dispatchEvent(new Event('scroll')); await vi.advanceTimersByTimeAsync(20);
    expect(location.hash).toBe(''); expect(root.dataset.page).toBe('intro');
    expect(loadFrames).toHaveBeenCalledTimes(1); expect(mounts.blink.show).toHaveBeenCalledTimes(1);
    expect(mounts.blink.active).toHaveBeenLastCalledWith(false);
    el.scrollTop = 4400; el.dispatchEvent(new Event('scroll')); await vi.advanceTimersByTimeAsync(20);
    expect(root.dataset.page).toBe('explore'); expect(location.hash).toBe('#/explore');
    expect(loadFrames).toHaveBeenCalledTimes(1); expect(localStorage.getItem(SEEN_KEY)).toBe('1');
    expect(root.querySelectorAll('#q')).toHaveLength(1); expect(root.querySelectorAll('.bk')).toHaveLength(1);
    for (const route of ['learn', 'play']) {
      history.replaceState(null, '', `/#/${route}`); window.dispatchEvent(new HashChangeEvent('hashchange'));
      expect(root.dataset.page).toBe(route);
      expect(root.querySelector(':scope > .top')).not.toBeNull();
      expect(root.classList.contains('entry-workspace')).toBe(false);
    }
    expect(mounts.learn).toHaveBeenCalledTimes(1); expect(mounts.play).toHaveBeenCalledTimes(1);
  });
  it('jumps straight to the supplied target and keeps the existing embed layout', async () => {
    history.replaceState(null, '', '/?target=wise0855&embed=1');
    startApp(root); await vi.advanceTimersByTimeAsync(20);
    expect(root.dataset.page).toBe('explore');
    expect(loadFrames).toHaveBeenCalledWith(expect.objectContaining({ slug: 'wise0855', det: 6 }), expect.any(AbortSignal));
    expect(root.querySelector('.entry-dashboard')).toBeNull();
    expect(root.querySelector<HTMLElement>('.entry-intro')!.hidden).toBe(true);
  });
  it('skips a completed first visit without replaying on ordinary navigation', () => {
    localStorage.setItem(SEEN_KEY, '1'); startApp(root);
    expect(root.dataset.page).toBe('explore'); expect(location.hash).toBe('#/explore');
    expect(root.querySelector<HTMLElement>('.entry-intro')!.hidden).toBe(true);
  });
  it('respects reduced motion from the first render', () => {
    vi.stubGlobal('matchMedia', () => Object.assign(new EventTarget(), { matches: true }));
    startApp(root);
    expect(root.dataset.page).toBe('explore');
    expect(root.querySelector<HTMLElement>('.entry-intro')!.hidden).toBe(true);
    expect(root.querySelector<HTMLElement>('.entry-dashboard')!.inert).toBe(false);
    expect(localStorage.getItem(SEEN_KEY)).toBeNull();
  });
});
