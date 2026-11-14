import { afterEach, describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { loadFrames, epochsFor } from './api/client';
import { mountBlink } from './blink';
import { mountVoteWith } from './vote';
import type { View } from './app/modules';
import { TARGETS } from './app/state';

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.clearAllTimers(); vi.useRealTimers(); });
const view: View = {ra: 133.75969, dec: -7.24216, slug: null, name: null, clock: 'months', fov: 5, det: 2};
describe('frontend/backend compatibility', () => {
  it('keeps every featured target on its prepared static data when the requested set exists', async () => {
    const fetcher = vi.fn(async (url: string) => {
      const path = new URL(String(url), 'http://localhost').pathname;
      return new Response(readFileSync(new URL('../public' + path, import.meta.url), 'utf8'));
    });
    vi.stubGlobal('fetch', fetcher);
    for (const target of TARGETS) {
      const loaded = await loadFrames(target);
      expect(loaded.origin, target.slug).toBe('static');
      expect(epochsFor(loaded.frames, target.det).length, target.slug).toBeGreaterThan(0);
    }
    expect(fetcher.mock.calls.some(([url]) => String(url).includes('/api/frames'))).toBe(false);
  });
  it('requests the live API when WISE switches to Hours D6 without a static set', async () => {
    const fetcher = vi.fn(async (url: string) => {
      const path = new URL(String(url), 'http://localhost').pathname;
      if (path === '/api/frames') return Response.json({clock:'hours', epochs:[{det:6}]}, {headers:{'X-SkyShift-Notice':'known_lookup_failed'}});
      return new Response(readFileSync(new URL('../public' + path, import.meta.url), 'utf8'));
    });
    vi.stubGlobal('fetch', fetcher);
    const loaded = await loadFrames({...view, slug:'wise0855', clock:'hours', fov:20, det:6});
    expect(loaded.origin).toBe('api');
    expect(epochsFor(loaded.frames, 6)).toHaveLength(1);
    expect(loaded.notices).toEqual(['known_lookup_failed']);
    expect(fetcher.mock.calls.some(([url]) => String(url).includes('/api/frames'))).toBe(true);
  });
  it('keeps other detectors requestable after a single-detector live response', async () => {
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
    vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
    vi.stubGlobal('requestAnimationFrame', () => 0);
    vi.stubGlobal('matchMedia', () => ({matches:true}));
    vi.stubGlobal('fetch', async () => new Response(Uint16Array.from([1]).buffer));
    const el=document.createElement('div');
    const h=mountBlink(el,{onViewChange:vi.fn(),onPick:vi.fn()});
    const e={id:'d2-v1',kind:'visit' as const,mjd_mid:61000,mjd_start:61000,mjd_end:61000,det:2 as const,um_min:1.1,um_max:1.6,n_exposures:1,flag_frac:0,sat_frac:0,data_url:'data:application/octet-stream;base64,AQA=',scale:1,zero:0};
    await h.show(view,{clock:'months',center:view,pixel_arcsec:6.15,size:[1,1],epochs:[e],gaia:[],known:[],data_release:'DR1',archive_checked:'2026-11-14'},'http://localhost/api/frames');
    const disabled=[...el.querySelectorAll<HTMLButtonElement>('[data-det]')].filter(b=>b.disabled).map(b=>Number(b.dataset.det));
    expect(disabled).toEqual([]);
    h.destroy();
  });
  it('clears a pending flag when the target or observation changes', () => {
    vi.useFakeTimers();
    const el=document.createElement('div');
    const store={mode:'local' as const,near:async()=>[],counts:async()=>new Map(),flag:vi.fn(),vote:vi.fn()};
    const h=mountVoteWith(el,{store});
    h.view(view);
    h.pick({...view,mjd:61000,epoch_id:'d2-v1'});
    h.view({...view,ra:269.4462,dec:4.76883});
    expect((el.querySelector('.vt-pick') as HTMLElement).hidden).toBe(true);
    expect(el.querySelector('[data-act="flag"]')).toBeNull();
    h.pick({...view,mjd:61000,epoch_id:'d2-v1'});
    h.view({...view,ra:269.4462,dec:4.76883,det:6});
    expect((el.querySelector('.vt-pick') as HTMLElement).hidden).toBe(true);
  });
});
