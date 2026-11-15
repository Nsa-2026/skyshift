import {readFileSync} from 'node:fs';
import {join} from 'node:path';
import {afterEach,expect,it,vi} from 'vitest';
import {mountCard} from '../card';
import {mountWorkbench} from './workbench';
import {targetBySlug,viewForTarget} from '../app/state';
import {workspaceState} from './workspace-state';
import {siteRoute} from '../app/navigation';
import type {ObjectResponse} from '../api/types';

afterEach(()=>{document.body.replaceChildren();history.replaceState(null,'','/');});
const object=(slug:string):ObjectResponse=>JSON.parse(readFileSync(join(__dirname,`../../public/data/${slug}/object.json`),'utf8'));
function setup(){
  const host=document.createElement('div'),cardElement=document.createElement('section');document.body.append(host);
  const card=mountCard(cardElement);
  const wb=mountWorkbench(host,{card,cardElement,layer:vi.fn(),locate:vi.fn(),reference:vi.fn(),rebuilt:vi.fn()});
  wb.active(false);
  const view=viewForTarget(targetBySlug('barnard')!);
  wb.select(view,'raw');wb.object(object('barnard'));card.show(view,object('barnard'),null);
  wb.mode(workspaceState('analyze/motion'));
  const click=(selector:string)=>host.querySelector<HTMLButtonElement>(selector)!.click();
  const open=()=>[...host.querySelectorAll<HTMLButtonElement>('[data-toggle-dashboard][aria-pressed="true"]')].map(el=>el.dataset.toggleDashboard);
  return {host,card,wb,click,open};
}

it('keeps Motion, Spectrum and Light curve open together, with working chart controls and independent close buttons',()=>{
  const {host,click,open}=setup();
  click('[data-toggle-dashboard="spectrum"]');click('[data-toggle-dashboard="lightcurve"]');
  expect(open()).toEqual(['motion','spectrum','lightcurve']);
  expect(host.querySelector('[data-card-dashboard="motion"] .cd-n')!.textContent).toBe('10.83″');
  expect(host.querySelector('[data-card-dashboard="spectrum"] svg')).not.toBeNull();
  expect(host.querySelector<HTMLElement>('[data-science="lightcurve"]')!.hidden).toBe(false);
  expect(host.querySelector('#light-chart svg')).not.toBeNull();
  expect(siteRoute(location.hash).workspace?.panels).toEqual(open());
  click('.cd-spec [data-mode="visit"]');
  expect(host.querySelector('.cd-spec [data-mode="visit"]')!.getAttribute('aria-pressed')).toBe('true');
  expect(host.querySelector('[data-card-dashboard="motion"] .cd-n')).not.toBeNull();
  const ids=[...host.querySelectorAll('[id]')].map(el=>el.id);
  expect(new Set(ids).size).toBe(ids.length);
  host.querySelectorAll<HTMLAnchorElement>('.cd-ref a').forEach(link=>expect(host.querySelector(link.getAttribute('href')!)).not.toBeNull());
  click('[data-close-dashboard="motion"]');
  expect(open()).toEqual(['spectrum','lightcurve']);
  expect(host.querySelector('[data-card-dashboard="motion"]')).toBeNull();
  expect(host.querySelector('[data-card-dashboard="spectrum"] svg')).not.toBeNull();
});

it('opens all six dashboards and closes them all without discarding their content',()=>{
  const {host,click,open}=setup();
  click('[data-open-all]');
  expect(open()).toEqual(['motion','spectrum','lightcurve','compare','crossmatch','signals']);
  expect(host.querySelector<HTMLDetailsElement>('.wb-comparison')!.open).toBe(false);
  expect(host.querySelector<HTMLElement>('[data-science="crossmatch"]')!.hidden).toBe(false);
  expect(host.querySelector<HTMLElement>('[data-wb="signals"]')!.hidden).toBe(false);
  click('[data-close-all]');
  expect(open()).toEqual([]);
  expect(host.querySelector<HTMLElement>('.wb-empty')!.hidden).toBe(false);
  expect(siteRoute(location.hash).workspace?.panels).toEqual([]);
  click('[data-toggle-dashboard="spectrum"]');
  expect(open()).toEqual(['spectrum']);
  expect(host.querySelector('[data-card-dashboard="spectrum"] svg')).not.toBeNull();
});

it('updates both object dashboards for another target and preserves the selection through loading and errors',()=>{
  const {host,card,wb,click,open}=setup();click('[data-toggle-dashboard="spectrum"]');
  card.status('loading');
  expect(host.querySelectorAll('[data-card-dashboard]')).toHaveLength(2);
  card.status('error','Photometry is temporarily unavailable');
  expect(host.querySelectorAll('[data-card-dashboard] .cd-error')).toHaveLength(2);
  const view=viewForTarget(targetBySlug('rwaur')!),data=object('rwaur');
  wb.select(view,'raw');wb.object(data);card.show(view,data,null);
  expect(open()).toEqual(['motion','spectrum']);
  const names=[...host.querySelectorAll('[data-card-dashboard] .cd-name')].map(el=>el.textContent);
  expect(names).toEqual([data.name,data.name]);
  expect(host.querySelector('.cd-spec svg')).not.toBeNull();
  expect(host.querySelector('.cd-error')).toBeNull();
});
