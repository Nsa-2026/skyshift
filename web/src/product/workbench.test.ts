import {readFileSync} from 'node:fs';
import {join} from 'node:path';
import {afterEach,expect,it,vi} from 'vitest';
import {mountWorkbench} from './workbench';
import {targetBySlug,viewForTarget} from '../app/state';
import type {BlinkPresentation} from '../app/modules';
import type {FramesResponse} from '../api/types';

afterEach(()=>document.body.replaceChildren());
function setup(){
  const host=document.createElement('div'),card=document.createElement('div');
  document.body.append(host);
  const wb=mountWorkbench(host,{card:{show:vi.fn(),status:vi.fn()},cardElement:card,
    layer:vi.fn(),locate:vi.fn(),reference:vi.fn(),rebuilt:vi.fn()});
  wb.active(false);
  return {host,wb};
}

it('links detector diagnostics to the selected detector rather than another published detector',()=>{
  const {host,wb}=setup(),barnard=viewForTarget(targetBySlug('barnard')!);
  const link=()=>host.querySelector<HTMLAnchorElement>('.wb-analysis-json')!;
  wb.select(barnard,'raw');
  expect(link().getAttribute('href')).toBe('/science-data/barnard/analysis.json');
  wb.select({...barnard,det:6},'raw');
  expect(new URL(link().href).searchParams.get('det')).toBe('6');
  expect(new URL(link().href).pathname).toBe('/api/analysis');
  const wise=viewForTarget(targetBySlug('wise0855')!);
  wb.select(wise,'raw');
  expect(link().getAttribute('href')).toBe('/science-data/wise0855/analysis.json');
});

it('describes a combined plate with its loaded members and sky-noise units, then restores native metadata',()=>{
  const {host,wb}=setup(),view=viewForTarget(targetBySlug('barnard')!);
  const frames=JSON.parse(readFileSync(join(__dirname,'../../public/data/barnard/frames_months_5.json'),'utf8')) as FramesResponse;
  const source='http://localhost/data/barnard/frames_months_5.json';
  const epoch=frames.epochs.find(e=>e.det===view.det)!;
  const other=frames.epochs.find(e=>e.det!==view.det)!;
  const display:BlinkPresentation={mode:'all-wavelengths',detectors:[epoch.det,other.det],units:'sky-noise',
    members:[epoch,other].map(e=>({id:e.id,det:e.det,mjd_mid:e.mjd_mid,data_url:new URL(e.data_url,source).href}))};
  wb.select(view,'raw');wb.frames(frames,source);wb.epoch(epoch,display);
  const metadata=()=>JSON.parse(host.querySelector('.wb-metadata')!.textContent!);
  expect(metadata()).toMatchObject({selected_detector:view.det,detector:null,epoch:null,display});
  expect(host.querySelector('.wb-context')!.textContent).toContain('All wavelengths');
  expect(host.querySelector('.wb-quality')!.textContent).toContain('individual source observations');
  wb.epoch(epoch,{mode:'single-detector',detectors:[epoch.det],units:'MJy/sr',members:[display.members[0]]});
  expect(metadata()).toMatchObject({detector:epoch.det,epoch:{id:epoch.id},display:{units:'MJy/sr'}});
  expect(host.querySelector('.wb-context')!.textContent).not.toContain('All wavelengths');
  wb.frames(null);
  expect(metadata()).toMatchObject({display:null,epoch:null});
});

it('offers derived images only where they exist, and says why elsewhere',()=>{
  const {host,wb}=setup(),barnard=viewForTarget(targetBySlug('barnard')!);
  const derived=()=>[...host.querySelectorAll<HTMLOptionElement>('.wb-layer option:not([value="raw"])')];
  const note=()=>host.querySelector('.wb-layer-note')!.textContent;
  wb.select(barnard,'raw');
  expect(derived().every(o=>!o.disabled)).toBe(true);
  expect(note()).toBe('');
  wb.select({...barnard,clock:'hours',fov:20},'raw');
  expect(derived().every(o=>o.disabled)).toBe(true);
  expect(note()).toMatch(/Months and 5′/);
  const elpis=viewForTarget(targetBySlug('elpis')!);
  wb.select(elpis,'raw');
  expect(derived().every(o=>o.disabled)).toBe(true);
  expect(note()).toMatch(/five featured stars/);
  wb.select({ra:130.5,dec:10.2,clock:'months',fov:5,det:2,slug:null,name:null},'raw');
  expect(derived().every(o=>o.disabled)).toBe(true);
});
