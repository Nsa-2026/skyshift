import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { runInNewContext } from 'node:vm';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { loadObject } from '../api/client';
import { loadAnalysis } from '../api/science-client';
import { lessonObservations, type Observation } from '../product/observations';
import { TARGETS } from '../app/state';
import { legacyHash } from '../app/legacy-routes';
import { siteRoute } from '../app/navigation';
import { learnHref, mountLearn, parseLearnRoute } from '../learn';
import { LESSONS, readProgress } from './model';
import { mountLearning } from './index';

const historical = vi.hoisted(() => ({ nasa: null as any, gx: null as any }));
const nasaFile = resolve(dirname(fileURLToPath(import.meta.url)), '../../public/guxing/guxing-nasa.js');
vi.mock('../api/client', async original => ({ ...await original<object>(), loadObject: vi.fn() }));
vi.mock('../api/science-client', async original => ({ ...await original<object>(), loadAnalysis: vi.fn() }));
vi.mock('../product/observations', () => ({
  lessonObservations: vi.fn(), displayLimits: () => ({ lo: 0, hi: 1 }),
  drawObservation: (canvas: HTMLCanvasElement) => { canvas.width = 100; canvas.height = 100; },
}));
vi.mock('./culture', () => ({ loadCulture: vi.fn().mockResolvedValue(undefined), culture: () => ({
  gx: historical.gx,
  nasa: historical.nasa,
}) }));

let host: HTMLElement, game: ReturnType<typeof mountLearning> | undefined;
const originalScroll = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollIntoView');
let storage: Map<string, string>;
const button = (selector: string) => host.querySelector<HTMLButtonElement>(selector)!;
const enter = () => host.querySelector<HTMLCanvasElement>('.hunt-canvas')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));

beforeEach(() => {
  const sandbox: Record<string, any> = {};
  for (const name of ['guxing-data', 'guxing', 'guxing-nasa']) {
    runInNewContext(readFileSync(resolve(dirname(nasaFile), `${name}.js`), 'utf8'), sandbox);
  }
  historical.nasa = sandbox.GUXING_NASA;
  historical.gx = new sandbox.Guxing();
  storage = new Map();
  vi.stubGlobal('localStorage', { getItem: (key: string) => storage.get(key) ?? null, setItem: (key: string, value: string) => storage.set(key, String(value)) });
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('offline')));
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
  vi.spyOn(HTMLCanvasElement.prototype, 'getBoundingClientRect').mockReturnValue({ width: 900, height: 500, left: 0, top: 0, right: 900, bottom: 500, x: 0, y: 0, toJSON() {} });
  const context = new Proxy({}, { get: () => () => {} });
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(context as CanvasRenderingContext2D);
  vi.mocked(loadObject).mockResolvedValue({ name: 'Known target', gaia: null, spectrum: [], measure: null } as any);
  vi.mocked(loadAnalysis).mockRejectedValue(new Error('No optional analysis'));
  vi.mocked(lessonObservations).mockImplementation(async (target, det) => [0, 1, 2].map(i => ({
    epoch: { id: `visit-${i}-d${det}`, det, mjd_mid: 60000 + i * 180, n_exposures: 3, flag_frac: 0, sat_frac: 0 },
    values: new Float32Array(10000),
    set: { frames: { size: [100, 100], center: { ra: target.ra, dec: target.dec }, pixel_arcsec: 3 }, jsonUrl: 'http://localhost/data/frames.json' },
  })) as Observation[]);
  host = document.createElement('main'); document.body.append(host);
});

afterEach(() => {
  game?.destroy(); game = undefined; host?.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.clearAllMocks();
  if (originalScroll) Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', originalScroll);
  else delete (HTMLElement.prototype as Partial<HTMLElement>).scrollIntoView;
});

function mount() {
  game = mountLearning(host, vi.fn());
  game.setTargets(TARGETS.map(t => ({ ...t, default_det: t.det, n_frames: 0, n_visits: 0 })));
  game.activate(true);
}
async function start(slug: string) {
  button(`.lesson-cards [data-lesson="${slug}"]`).click();
  await vi.waitFor(() => expect(host.querySelector('.guide-stage .hunt-canvas')).not.toBeNull());
}
function track() {
  button('.hunt-reset').click(); enter(); button('.guide-next').click();
  for (let i = 0; i < 3; i++) { enter(); button('.guide-next').click(); }
}

// Traditional names and explicitly labelled source quotations may keep their original language.
function expectEnglishUi() {
  const copy = host.cloneNode(true) as HTMLElement;
  copy.querySelectorAll('[lang="zh"]').forEach(el => el.remove());
  expect(copy.textContent).not.toMatch(/\p{Script=Han}/u);
  for (const el of copy.querySelectorAll('[aria-label], [title], [alt]')) {
    for (const attr of ['aria-label', 'title', 'alt']) expect(el.getAttribute(attr) ?? '').not.toMatch(/\p{Script=Han}/u);
  }
}

describe('the restored five-field game', () => {
  it('keeps all five fields, three distinct observations, night vision and collection rewards', async () => {
    mount();
    expectEnglishUi();
    expect(host.querySelectorAll('.lesson-cards [data-lesson]')).toHaveLength(5);
    for (const lesson of LESSONS) {
      await start(lesson.slug);
      expectEnglishUi();
      expect(host.querySelectorAll('[data-frame]')).toHaveLength(3);
      if (lesson.slug === 'wise0855') {
        expect(button('.hunt-night').getAttribute('aria-checked')).toBe('false');
        button('.hunt-night').click();
        await vi.waitFor(() => expect(button('.hunt-night').getAttribute('aria-checked')).toBe('true'));
        expect(lessonObservations).toHaveBeenLastCalledWith(expect.objectContaining({ slug: 'wise0855' }), 6, expect.anything(), { fov: 5, all: true });
      }
      track();
      expect(readProgress()).not.toContain(lesson.slug);
      expect(button('.guide-next').textContent).toBe('Add to my collection ✦');
      button('.guide-next').click();
      expect(readProgress()).toContain(lesson.slug);
      const slider = host.querySelector<HTMLInputElement>('.year-slider')!;
      expect([slider.min, slider.max]).toEqual(['-200', '3000']);
      slider.value = '1054'; slider.dispatchEvent(new Event('input'));
      expect(host.querySelector('.year-output')!.textContent).toBeTruthy();
      expectEnglishUi();
      button('.guide-exit').click();
    }
    expect(host.querySelectorAll('.collection-star.collected')).toHaveLength(5);
    expect(readProgress()).toEqual(LESSONS.map(l => l.slug));
  });

  it('does not award a collection for an incorrect trail and permits another attempt', async () => {
    mount(); await start('barnard');
    button('.hunt-reset').click(); enter(); button('.guide-next').click();
    for (let i = 0; i < 3; i++) {
      const canvas = host.querySelector<HTMLCanvasElement>('.hunt-canvas')!;
      for (let offset = 0; offset < 5; offset++) canvas.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight' }));
      enter(); button('.guide-next').click();
    }
    expect(host.querySelector('.guide-feedback')!.textContent).toContain('Those positions do not match yet');
    expect(readProgress()).toEqual([]);
    track(); expect(button('.guide-next').textContent).toBe('Add to my collection ✦');
  });

  it('preserves old collection records and both historical stories, with day selection and tonight', async () => {
    storage.set('skyshift-lessons-v1', JSON.stringify(['barnard', 'SN1054'])); mount();
    expect(host.querySelectorAll('.collection-star.collected')).toHaveLength(1);
    for (const id of ['SN1054', 'SN1572']) {
      button(`[data-story="${id}"]`).click();
      await vi.waitFor(() => expect(host.querySelector('.story-collect')).not.toBeNull());
      expectEnglishUi();
      expect(host.querySelector('.story-panel blockquote')!.textContent).toBeTruthy();
      expect((host.querySelector('.story-panel details') as HTMLDetailsElement).open).toBe(false);
      button('.story-collect').click(); expect(readProgress()).toContain(id);
    }
    const day = historical.nasa.planets.events[0].time.slice(0, 10);
    host.querySelector<HTMLInputElement>('.sky-date')!.value = day; button('.date-go').click();
    await vi.waitFor(() => expect(host.querySelector('.date-events article')).not.toBeNull());
    expect(host.querySelector('.date-status')!.textContent).toContain(day);
    expectEnglishUi();
    button('.date-today').click();
    expect(host.querySelector<HTMLInputElement>('.sky-date')!.value).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    host.querySelector<HTMLInputElement>('.sky-date')!.value = '2028-01-01'; button('.date-go').click();
    expect(host.querySelector('.date-status')!.textContent).toContain('Choose a valid date in 2026–2027');
    expect(readProgress()).toContain('barnard');
  });

  it('pauses comparison when hidden and cancels an unfinished target load on departure', async () => {
    mount(); await start('barnard'); button('.hunt-play').click();
    expect(button('.hunt-play').getAttribute('aria-pressed')).toBe('true');
    game!.activate(false); expect(button('.hunt-play').getAttribute('aria-pressed')).toBe('false');
    button('.guide-exit').click();
    vi.mocked(loadObject).mockImplementationOnce(() => new Promise(() => {}));
    button('.lesson-cards [data-lesson="epsindib"]').click();
    const signal = vi.mocked(loadObject).mock.calls.at(-1)![1]!;
    game!.activate(false); expect(signal.aborted).toBe(true);
    game!.activate(true);
    await vi.waitFor(() => expect(host.querySelector('.guide-stage .hunt-canvas')).not.toBeNull());
  });
});

describe('integration with Learn and existing practice', () => {
  it('restores old Play links under Learn and keeps guided practice on the shared Viewer', () => {
    expect(legacyHash('#/play')).toEqual({ hash: '#/learn/hunt' });
    expect(siteRoute('#/learn/hunt')).toMatchObject({ page: 'learn', screen: 'learn', param: 'hunt' });
    expect(parseLearnRoute('hunt')).toEqual({ view: 'hunt' });
    expect(learnHref({ view: 'hunt' })).toBe('#/learn/hunt');
    expect(siteRoute('#/learn/practice')).toMatchObject({ page: 'explore', screen: 'learn' });
  });

  it('makes the game reachable from Learn and returns to existing learning paths', async () => {
    const page = mountLearn(host, { navigate: vi.fn(), explore: vi.fn() });
    page.show(null); const originalHeading = host.querySelector('h1')!.textContent;
    expect(host.querySelector('.ln-hunt-entry')?.getAttribute('href')).toBe('#/learn/hunt');
    page.show('hunt');
    await vi.waitFor(() => expect(host.querySelectorAll('.lesson-cards [data-lesson]')).toHaveLength(5));
    expect(document.title).toBe('Sky hunt: SkyShift');
    page.hide?.(); page.show(null);
    expect(host.querySelector('.sky-hunt')).toBeNull();
    expect(host.querySelector('h1')!.textContent).toBe(originalHeading);
    page.destroy();
  });
});
