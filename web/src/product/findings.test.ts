import {readFileSync} from 'node:fs';
import {join} from 'node:path';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import type {Analysis} from '../api/science-client';
import {forgetCatalogues} from '../api/catalogues';
import {renderFindings} from './findings';
import {signalTrack,type ScienceFrame} from './vetting';

const read=(file:string)=>JSON.parse(readFileSync(join(__dirname,`../../public/science-data/barnard/${file}.json`),'utf8'));
const analysis=read('analysis') as Analysis, science=read('science') as {frames:ScienceFrame[]};
const target={slug:'barnard',ra:269.4462,dec:4.76883};
// The first fast mover's sightings, so a known body can be put on them (the "grade A mover was 306P" case).
const sightings=signalTrack('fast',analysis.fast_movers[0],science.frames,2)!.sightings;
const comet=sightings.map(s=>({name:'306P/LINEAR',ra:s.ra+2/3600,dec:s.dec,v:17.9,visit:3,mjd:s.mjd}));
const gaia={gaia:[{id:'4472832130942575872',ra:269.452,dec:4.6933,pmra:-801.5,pmdec:10362.4,parallax:546.98,g:8.19}]};
const catwise={source:'NASA/IPAC CatWISE2020; proper motions in arcsec/yr',sources:[{name:'J175746.52+044746.7',ra:269.4438,dec:4.7963,w1mpro:9.3,w2mpro:9.4,pmra:0,pmdec:0}],note:''};

type Reply={status?:number;body:unknown}|Promise<{status?:number;body:unknown}>;
function serve(routes:Record<string,()=>Reply>){
  const calls:string[]=[];
  vi.stubGlobal('fetch',vi.fn(async(url:string)=>{
    calls.push(url);
    const key=Object.keys(routes).find(k=>url.includes(k));
    if(!key)return {ok:false,status:404,statusText:'Not Found',json:async()=>({error:{code:'not_found',message:'no route'}})};
    const r=await routes[key]();const status=r.status??200;
    return {ok:status<400,status,statusText:'',json:async()=>r.body};
  }));
  return calls;
}
const all={
  'science-data/barnard/science.json':()=>({body:science}),
  'api/gaia':()=>({body:gaia}),
  'kind=catwise':()=>({body:catwise}),
  'kind=asteroids':()=>({body:{source:'Minor Planet Center orbits (kete)',objects:comet,errors:[],note:'All 73 observing sessions checked'}}),
};
beforeEach(()=>forgetCatalogues());
afterEach(()=>{vi.unstubAllGlobals();document.body.replaceChildren()});

it('shows the list at once, says it is checking, then names the known body and lists it apart',async()=>{
  let release!:()=>void;const held=new Promise<void>(r=>{release=r});
  const calls=serve({...all,'kind=asteroids':()=>held.then(()=>all['kind=asteroids']())});
  const host=document.createElement('div');document.body.append(host);
  const done=renderFindings(host,analysis,vi.fn(),{target,signal:new AbortController().signal});
  // Not blocked: both signals are there before any catalogue answered.
  expect(host.querySelectorAll('.candidate')).toHaveLength(2);
  expect(host.querySelector('.sgc-summary')!.textContent).toBe('Checking catalogues… still waiting for Gaia DR3, CatWISE2020 and MPC orbits.');
  expect(host.querySelectorAll('.sgc-pending')).toHaveLength(2);
  expect(host.textContent).not.toContain('not yet been vetted');
  await vi.waitFor(()=>expect(host.querySelector('.sgc-summary')!.textContent).toContain('still waiting for MPC orbits'));
  expect(host.querySelector('.candidate .sgc-pending')!.textContent).toBe('Still checking MPC orbits…');
  release();await done;
  // Each catalogue asked once, in parallel with the times.
  expect(calls.filter(u=>u.includes('/api/')).sort()).toEqual([
    '/api/enrichment?ra=269.4462&dec=4.76883&kind=asteroids','/api/enrichment?ra=269.4462&dec=4.76883&kind=catwise','/api/gaia?ra=269.4462&dec=4.76883&r=3']);
  expect(host.querySelector('.sgc-summary')!.textContent).toBe('Checked against Gaia DR3, CatWISE2020 and MPC orbits (asteroids and comets): 1 signal is a known body, 1 has a known asteroid or comet nearby.');
  expect(host.querySelector('.finding-count')!.textContent).toBe('1 signal to check · 1 explained by a catalogue');
  const known=host.querySelector<HTMLElement>('.finding-known')!;
  expect(known.hidden).toBe(false);
  expect(known.querySelector('h4')!.textContent).toBe('Explained by a catalogue (1)');
  const row=known.querySelector('.candidate')!;
  expect(row.querySelector('.sgc-status')!.textContent).toMatch(/^Known body: 306P\/LINEAR Predicted at its place every time/);
  expect(row.querySelector('.sgc.is-strong .sgc-level')!.textContent).toBe('Sign against:');
  expect(row.querySelector('.sgc.is-strong .sgc-text')!.textContent).toMatch(/^MPC orbits put 306P\/LINEAR \(V 17\.9\) within 2\.0″ of it at all 3 sightings/);
  // The other signal shares one exposure's dot with it: it stays to check, with the comet and the WISE source
  // as things to look at.
  const open=host.querySelector('.finding-open .candidate')!;
  const warns=[...open.querySelectorAll('.sgc.is-warn')];
  expect(warns.map(w=>w.querySelector('.sgc-level')!.textContent)).toEqual(['Can\'t rule out:','Can\'t rule out:']);
  expect(warns[0].querySelector('.sgc-text')!.textContent).toMatch(/^MPC orbits put 306P\/LINEAR \(V 17\.9\) 2\.0″ from it on \d{4}-\d\d-\d\d, within 30″ at 1 of its 3 sightings/);
  expect(warns[1].querySelector('.sgc-text')!.textContent).toMatch(/^CatWISE2020 J175746\.52\+044746\.7 \(W1 9\.3, W2 9\.4\) sits/);
  expect(warns[1].querySelector('.sgc-look')!.textContent).toMatch(/^Look: Blink Hours on D2 around/);
  expect(open.querySelector('.sgc.is-ok .sgc-text')!.textContent).toBe('Gaia DR3 checked: nothing in Gaia within 6″.');
  // The analysis's "vetoes have not been run" warning is answered by the checks.
  expect(host.querySelector('.wb-limits:not(.sgc-rules)')!.textContent).not.toMatch(/vetoes have not been run/);
  // The filter still hides rows in both groups.
  const select=host.querySelector('select')!;select.value='cold';select.dispatchEvent(new Event('change'));
  expect(known.hidden).toBe(true);expect(host.querySelector<HTMLElement>('.finding-empty')!.hidden).toBe(false);
});

it('names a catalogue that did not answer and never implies its check passed',async()=>{
  serve({...all,'kind=catwise':()=>({status:502,body:{error:{code:'upstream',message:'NASA/IPAC IRSA (CatWISE2020) did not answer (ReadTimeout). No catalogue results are shown; try again later.'}}})});
  const host=document.createElement('div');
  await renderFindings(host,analysis,vi.fn(),{target});
  const summary=host.querySelector('.sgc-summary')!.textContent!;
  expect(summary).toContain('Checked against Gaia DR3 and MPC orbits (asteroids and comets)');
  expect(summary).toContain('Not checked against CatWISE2020: NASA/IPAC IRSA (CatWISE2020) did not answer (ReadTimeout).');
  expect(summary).not.toContain('try again later');
  for(const row of host.querySelectorAll('.finding-open .candidate')){
    expect(row.textContent).toContain('Not checked against CatWISE2020: the service did not answer.');
    expect([...row.querySelectorAll('.sgc.is-ok')].map(e=>e.textContent).join(' ')).not.toContain('CatWISE');
  }
});

it('a malformed answer is a failure, not an empty catalogue',async()=>{
  serve({...all,'api/gaia':()=>({body:{stars:'?'}})});
  const host=document.createElement('div');
  await renderFindings(host,analysis,vi.fn(),{target});
  expect(host.querySelector('.sgc-summary')!.textContent).toContain('Not checked against Gaia DR3: the answer had no star list.');
});

it('runs no checks for an empty list, and keeps the plain list where no field is given',async()=>{
  const calls=serve(all);
  const host=document.createElement('div');
  await renderFindings(host,{...analysis,fast_movers:[]},vi.fn(),{target});
  await renderFindings(host,analysis,vi.fn());
  expect(calls).toEqual([]);
  expect(host.querySelector('.sgc-summary')!.textContent).toBe('Catalogue checks were not run for this list.');
});
