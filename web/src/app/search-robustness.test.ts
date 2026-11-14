import { expect, it, vi } from 'vitest';
vi.mock('../atlas', () => ({ mountAtlas: () => ({show:vi.fn(),hide:vi.fn(),destroy:vi.fn()}) }));
vi.mock('../intro', () => ({ mountOpening:vi.fn() }));
vi.mock('../blink', () => ({ mountBlink: () => ({show:vi.fn(async()=>{}),status:vi.fn(),active:vi.fn(),highlight:vi.fn(),destroy:vi.fn()}) }));
vi.mock('../vote', async original => ({...await original<typeof import('../vote')>(),mountVote:()=>({pick:vi.fn(),view:vi.fn()})}));
vi.mock('../sky', () => ({mountSky:()=>({goto:vi.fn(),epoch:vi.fn(),destroy:vi.fn()})}));
vi.mock('../api/client', async original => ({...await original<typeof import('../api/client')>(),loadFrames:vi.fn(()=>new Promise(()=>{})),loadObject:vi.fn(()=>new Promise(()=>{})),resolve:vi.fn()}));
import { loadFrames, resolve } from '../api/client';
import { startApp } from './app';
import type { ResolveResponse } from '../api/types';

it('ignores late name lookups after a newer search, target choice, edit or navigation', async () => {
  vi.stubGlobal('scrollTo',vi.fn());
  history.replaceState(null,'','/?t=barnard#/explore');
  const root=document.createElement('div');document.body.append(root);startApp(root);
  const pending: {finish:(r:ResolveResponse)=>void; signal:AbortSignal}[]=[];
  vi.mocked(resolve).mockImplementation((_q,signal)=>new Promise(finish=>pending.push({finish,signal:signal!})));
  const input=root.querySelector<HTMLInputElement>('#q')!;
  const form=root.querySelector('form')!;
  const search=(q:string)=>{input.value=q;form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));};
  const result=(name:string):ResolveResponse=>({q:name,name,ra:100,dec:20,slug:null,source:'builtin'});
  const tick=async()=>{await Promise.resolve();await Promise.resolve();};
  search('First remote name');search('Second remote name');
  expect(pending[0].signal.aborted).toBe(true);
  pending[1].finish(result('Second remote name'));await tick();
  expect(input.value).toBe('Second remote name');
  const count=vi.mocked(loadFrames).mock.calls.length;
  pending[0].finish(result('First remote name'));await tick();
  expect(loadFrames).toHaveBeenCalledTimes(count);
  expect(input.value).toBe('Second remote name');

  search('Before target choice');root.querySelector<HTMLButtonElement>('[data-slug="wise0855"]')!.click();
  expect(pending[2].signal.aborted).toBe(true);
  pending[2].finish(result('Before target choice'));await tick();
  expect(vi.mocked(loadFrames).mock.lastCall![0].slug).toBe('wise0855');

  search('Before editing');input.value='Still typing';input.dispatchEvent(new Event('input'));
  expect(pending[3].signal.aborted).toBe(true);
  pending[3].finish(result('Before editing'));await tick();
  expect(input.value).toBe('Still typing');
  expect(root.querySelector('.find-msg')!.textContent).toBe('');

  search('Before navigation');history.pushState(null,'','#/sky');dispatchEvent(new HashChangeEvent('hashchange'));
  expect(pending[4].signal.aborted).toBe(true);
  pending[4].finish(result('Before navigation'));await tick();
  expect(root.querySelector<HTMLElement>('.page-atlas')!.hidden).toBe(false);
  expect(root.querySelector<HTMLElement>('.page-blink')!.hidden).toBe(true);
  vi.unstubAllGlobals();root.remove();
});
