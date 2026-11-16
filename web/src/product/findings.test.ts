import {readFileSync} from 'node:fs';
import {join} from 'node:path';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import type {Analysis} from '../api/science-client';
import {forgetCatalogues} from '../api/catalogues';
import {renderFindings} from './findings';
import {signalTrack,type ScienceFrame} from './vetting';

const read=(file:string,slug='barnard')=>JSON.parse(readFileSync(join(__dirname,`../../public/science-data/${slug}/${file}.json`),'utf8'));
/** An analysis as the API answers it at live coordinates or in an older file: no checks written into it. */
const bare=(a:Analysis):Analysis=>{
  const strip=(list:Analysis['fast_movers'])=>list.map(c=>{const {check_status:_s,known_name:_k,checks:_c,...rest}=c as typeof c&{check_status?:unknown;known_name?:unknown;checks?:unknown};return rest});
  const {catalogue_checks:_m,...rest}=a as Analysis&{catalogue_checks?:unknown};
  return {...rest,slow_movers:strip(a.slow_movers),fast_movers:strip(a.fast_movers),cold_candidates:strip(a.cold_candidates)};
};
const analysis=bare(read('analysis') as Analysis), science=read('science') as {frames:ScienceFrame[]};
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

// Checks written into the file before the list was published (scripts/precompute/signal_checks.py).
const written={
  made:'2026-11-16T18:00:00Z',
  sources:{gaia:'Gaia DR3 (NASA/IPAC IRSA), within 3′',catwise:'CatWISE2020 (NASA/IPAC IRSA), within 2.84′',mpc:'MPC orbits of 1,580,438 asteroids at any brightness and 2,215 comets'},
};
const knownComet={check_status:'known',known_name:'306P/LINEAR',checks:[{code:'known_body',level:'strong',
  text:'MPC orbits put 306P/LINEAR (V 17.9) within 2.0″ of it at all 3 sightings, moving 30.1″/h toward PA 251° (ours 29.8″/h toward PA 250°)',
  look:'Blink Hours on D2 around 2025-09-20: see whether the dot follows 306P/LINEAR\'s predicted path.',values:{sep_arcsec:2,sightings:3}}]};
const onGaia={check_status:'open',known_name:null,checks:[
  {code:'catalogues_checked',level:'ok',text:'CatWISE2020 and MPC orbits checked: nothing in CatWISE within 6″, and no known asteroid (at any brightness), comet, planet or moon predicted within 30″ at its times',look:'',values:null},
  {code:'catalogue_star',level:'warn',text:'Gaia DR3 4472832130942575872 (G 19.1) sits 3.2″ from it on 2025-09-20, within 6″ at 1 of its 3 sightings (the star moved from 2016.0 with its catalogued motion)',
    look:'Blink Hours on D2 around 2025-09-20: see whether the dot sits still on the star there or moves past it.',values:{sep_arcsec:3.2,sightings:1,sources:1,catalogue:'gaia'}}]};
const checked=(a:Analysis,fast:object[])=>({...a,catalogue_checks:written,fast_movers:a.fast_movers.map((c,i)=>({...c,...fast[i]}))}) as Analysis;

it('shows checks written before publishing at once: the certain one starts apart, nothing is asked or waited for',async()=>{
  const calls=serve(all);
  const host=document.createElement('div');document.body.append(host);
  const done=renderFindings(host,checked(analysis,[knownComet,onGaia]),vi.fn(),{target,signal:new AbortController().signal});
  // The first paint: every check, the known body in its group, no "checking".
  expect(host.querySelectorAll('.sgc-pending')).toHaveLength(0);
  expect(host.querySelector('.sgc-summary')!.textContent).toBe('Checked before this list was published (2026-11-16) against Gaia DR3, CatWISE2020 and MPC orbits (every asteroid at any brightness and every comet, seen from SPHEREx): 1 signal is a known body, 1 sits on a Gaia star.');
  expect(host.querySelector('.finding-count')!.textContent).toBe('1 signal to check · 1 explained by a catalogue');
  const known=host.querySelector<HTMLElement>('.finding-known')!;
  expect(known.hidden).toBe(false);
  expect(known.querySelector('h4')!.textContent).toBe('Explained by a catalogue (1)');
  expect(known.querySelector('.sgc-status')!.textContent).toMatch(/^Known body: 306P\/LINEAR Predicted at its place every time/);
  expect(known.querySelector('.sgc.is-strong .sgc-text')!.textContent).toMatch(/^MPC orbits put 306P\/LINEAR \(V 17\.9\) within 2\.0″/);
  const open=host.querySelector('.finding-open .candidate')!;
  // Sorted as the contract says: warn before ok, whatever order the file gave.
  expect([...open.querySelectorAll('.sgc')].map(e=>e.getAttribute('data-code'))).toEqual(['catalogue_star','catalogues_checked']);
  expect(open.querySelector('.sgc.is-warn .sgc-look')!.textContent).toMatch(/^Look: Blink Hours on D2 around 2025-09-20/);
  expect(open.querySelector('details ol')!.textContent).toContain('covered every MPC asteroid and comet');
  const rules=host.querySelector('.sgc-rules')!.textContent!;
  expect(rules).toContain('Every signal was checked before this list was published (2026-11-16)');
  expect(rules).toContain('MPC orbits: MPC orbits of 1,580,438 asteroids at any brightness and 2,215 comets.');
  expect(rules).not.toContain('asteroids fainter than V 21');
  await done;
  expect(calls).toEqual([]);
});

it('checks live only the signals a file holds no checks for',async()=>{
  const calls=serve(all);
  const host=document.createElement('div');
  const done=renderFindings(host,checked(analysis,[knownComet,{}]),vi.fn(),{target});
  expect(host.querySelector('.finding-known .candidate')!.getAttribute('data-index')).toBe('0');
  expect(host.querySelectorAll('.sgc-pending')).toHaveLength(1);
  expect(host.querySelector('.finding-open .candidate .sgc-pending')).not.toBeNull();
  expect(host.querySelector('.sgc-summary')!.textContent).toMatch(/^Checking catalogues…/);
  await done;
  expect(calls.filter(u=>u.includes('/api/'))).toHaveLength(3);
  // The written verdict stays; the other signal got the browser's own.
  expect(host.querySelector('.finding-known .candidate')!.getAttribute('data-index')).toBe('0');
  expect(host.querySelector('.finding-count')!.textContent).toBe('1 signal to check · 1 explained by a catalogue');
  expect(host.querySelector('.sgc-summary')!.textContent).toMatch(/^Checked against Gaia DR3, CatWISE2020 and MPC orbits \(asteroids and comets\): 1 signal is a known body, /);
});

it('the published fields carry their checks: Eps Indi B\'s known star is listed apart in the first paint',async()=>{
  for(const slug of ['barnard','epsindib','wise0855','rwaur','rdra']){
    const calls=serve({});
    const host=document.createElement('div');
    const published=read('analysis',slug) as Analysis;
    await renderFindings(host,published,vi.fn(),{target:{slug,ra:0,dec:0}});
    expect(calls).toEqual([]);
    expect(host.querySelectorAll('.sgc-pending')).toHaveLength(0);
    const rows=host.querySelectorAll('.candidate');
    if(rows.length)expect(host.querySelector('.sgc-summary')!.textContent).toMatch(/^Checked before this list was published \(\d{4}-\d\d-\d\d\) against /);
    for(const row of rows)expect(row.querySelectorAll('.sgc').length).toBeGreaterThan(0);
    if(slug==='epsindib'){
      const known=[...host.querySelectorAll('.finding-known .candidate')];
      expect(known.map(r=>r.querySelector('.sgc-name')!.textContent)).toContain('Gaia DR3 6412596012146801152');
      expect(known[0].querySelector('.sgc-status-head')!.textContent).toMatch(/^Known star:/);
    }
    vi.unstubAllGlobals();
  }
});

it('runs no checks for an empty list, and keeps the plain list where no field is given',async()=>{
  const calls=serve(all);
  const host=document.createElement('div');
  await renderFindings(host,{...analysis,fast_movers:[]},vi.fn(),{target});
  await renderFindings(host,analysis,vi.fn());
  expect(calls).toEqual([]);
  expect(host.querySelector('.sgc-summary')!.textContent).toBe('Catalogue checks were not run for this list.');
});
