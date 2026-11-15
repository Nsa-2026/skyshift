import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { runInNewContext } from 'node:vm';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { loadFrames, loadObject, toMJySr } from '../api/client';
import type { Epoch, LoadedFrames, ObjectResponse } from '../api/types';
import { observation, type Observation } from '../product/observations';
import { loadGuxing } from './stories-data';
import { bandEvidence, matchesTarget, measuredDirection, nearestSample, skyCase, spectrumSvg, usableSpectrum } from './detective-model';
import { mountDetective } from './detective';
import { learnHref, mountLearn, parseLearnRoute } from './index';
import type { PageHandle } from '../app/modules';

const publicRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../../public');
const json = (file: string) => JSON.parse(readFileSync(resolve(publicRoot,file),'utf8'));
function frames(slug: string, fov = 15): LoadedFrames {
  const file = fov === 5 ? 'frames_months_5.json' : 'frames_months_15.json';
  return { frames: json(`data/${slug}/${file}`), jsonUrl: `http://localhost/data/${slug}/${file}`, origin: 'static' };
}
function realObservation(set: LoadedFrames, epoch: Epoch): Observation {
  const path = new URL(epoch.data_url,set.jsonUrl).pathname.slice(1), bytes = readFileSync(resolve(publicRoot,path));
  const raw = new Uint16Array(bytes.byteLength/2);
  for(let i=0;i<raw.length;i++) raw[i]=bytes.readUInt16LE(i*2);
  return { set, epoch, values:toMJySr(raw,epoch) };
}
const barnardObject = () => json('data/barnard/object.json') as ObjectResponse;
const epsObject = () => json('data/epsindib/object.json') as ObjectResponse;
const firstBarnard = () => { const set=frames('barnard');return realObservation(set,set.frames.epochs.find(e=>e.det===2)!); };

vi.mock('../api/client', async original => ({ ...await original<object>(), loadFrames:vi.fn(), loadObject:vi.fn() }));
vi.mock('../product/observations', async original => ({ ...await original<object>(), observation:vi.fn() }));
vi.mock('./stories-data', async original => ({ ...await original<object>(), loadGuxing:vi.fn() }));

let historical: any;
beforeAll(()=>{
  const sandbox:Record<string,any>={};
  for(const name of ['guxing-data','guxing','guxing-nasa'])runInNewContext(readFileSync(resolve(publicRoot,`guxing/${name}.js`),'utf8'),sandbox);
  historical={gx:new sandbox.Guxing(),nasa:sandbox.GUXING_NASA};
});

describe('NASA-derived investigation evidence',()=>{
  it('builds a solvable relative-position clue inside the real Barnard frame',()=>{
    const first=firstBarnard(), field=skyCase(first,barnardObject());
    expect(field.direction).toBe('northwest');
    expect(field.arcmin).toBeGreaterThan(0);
    for(const p of [field.target,field.reference]){
      expect(p.x).toBeGreaterThanOrEqual(field.crop.x);
      expect(p.x).toBeLessThan(field.crop.x+field.crop.w);
      expect(p.y).toBeGreaterThanOrEqual(field.crop.y);
      expect(p.y).toBeLessThan(field.crop.y+field.crop.h);
    }
    expect(matchesTarget(field.target,field)).toBe(true);
    expect(matchesTarget(field.reference,field)).toBe(false);
    expect(field.referenceStar.id).not.toBe(barnardObject().gaia!.id);
  });
  it('refuses to use saturated Barnard photometry and retains actual Epsilon Indi samples',()=>{
    expect(usableSpectrum(barnardObject().spectrum)).toHaveLength(0);
    const raw=epsObject().spectrum, valid=usableSpectrum(raw);
    expect(valid.length).toBeGreaterThan(300);
    expect(new Set(valid.map(p=>p.det)).size).toBe(6);
    expect(valid.every(p=>raw.includes(p))).toBe(true);
    expect(usableSpectrum([{...raw[10],flux_ujy:NaN},{...raw[10],err_ujy:0},{...raw[10],saturated:true}])).toHaveLength(0);
  });
  it('keeps visits apart instead of teaching a predetermined longer-is-brighter answer',()=>{
    const raw=epsObject().spectrum;
    expect(bandEvidence(raw,1).winner).toBe('D6');
    expect(bandEvidence(raw,3).winner).toBe('D3');
    expect(bandEvidence(raw,99).winner).toBe('Not enough evidence');
    const v3=usableSpectrum(raw,3);
    expect(v3[nearestSample(v3,3.3)].visit).toBe(3);
    const svg=spectrumSvg(v3,usableSpectrum(raw),0,true,true);
    expect((svg.match(/class="sd-sample /g)||[])).toHaveLength(v3.length);
    expect(svg).toContain('logarithmic scale');
    expect(svg).not.toContain('<path'); // no invented smooth spectrum between measured dots
    expect(spectrumSvg(v3,usableSpectrum(raw),0,true,true,340)).toContain('viewBox="0 0 340 323"');
  });
  it('uses the measured direction only when the reduction supports this detector',()=>{
    const object=barnardObject();expect(measuredDirection(object,2)).toBe('North');
    expect(measuredDirection(object,6)).toBe('Not enough evidence');
    expect(measuredDirection({...object,measure:null},2)).toBe('Not enough evidence');
  });
});

let host:HTMLElement, page:PageHandle|undefined, storage:Map<string,string>;
const scrollDescriptor=Object.getOwnPropertyDescriptor(HTMLElement.prototype,'scrollIntoView');
const ctx={navigate:vi.fn(),explore:vi.fn()};
const click=(selector:string)=>(host.querySelector(selector) as HTMLButtonElement).click();
async function ready(stage=0){
  click(`[data-stage="${stage}"]`);
  await vi.waitFor(()=>expect(host.querySelector(stage===2?'.sd-spectrum':'.sd-sky-canvas')).not.toBeNull());
}
function englishUi(){
  const copy=host.cloneNode(true) as HTMLElement;copy.querySelectorAll('[lang="zh"]').forEach(e=>e.remove());
  expect(copy.textContent).not.toMatch(/\p{Script=Han}/u);
  for(const e of copy.querySelectorAll('[aria-label],[alt],[title]'))for(const a of ['aria-label','alt','title'])expect(e.getAttribute(a)??'').not.toMatch(/\p{Script=Han}/u);
}
beforeEach(()=>{
  storage=new Map();vi.stubGlobal('localStorage',{getItem:(k:string)=>storage.get(k)??null,setItem:(k:string,v:string)=>storage.set(k,v)});
  vi.stubGlobal('fetch',vi.fn().mockRejectedValue(new TypeError('offline')));
  Object.defineProperty(HTMLElement.prototype,'scrollIntoView',{configurable:true,value:vi.fn()});
  vi.spyOn(HTMLCanvasElement.prototype,'getContext').mockImplementation(()=>({createImageData:(w:number,h:number)=>({data:new Uint8ClampedArray(w*h*4)}),putImageData:vi.fn()}) as any);
  vi.spyOn(HTMLCanvasElement.prototype,'getBoundingClientRect').mockReturnValue({width:100,height:100,left:0,top:0,right:100,bottom:100,x:0,y:0,toJSON(){}});
  vi.mocked(loadFrames).mockImplementation(async t=>frames(t.slug!,t.fov));
  vi.mocked(loadObject).mockImplementation(async t=>t.slug==='barnard'?barnardObject():epsObject());
  vi.mocked(observation).mockImplementation(async(set,e)=>realObservation(set,e));
  vi.mocked(loadGuxing).mockResolvedValue(historical);
  host=document.createElement('div');document.body.append(host);
});
afterEach(()=>{
  page?.destroy();page=undefined;host.remove();vi.restoreAllMocks();vi.unstubAllGlobals();vi.clearAllMocks();
  if(scrollDescriptor)Object.defineProperty(HTMLElement.prototype,'scrollIntoView',scrollDescriptor);
  else delete(HTMLElement.prototype as Partial<HTMLElement>).scrollIntoView;
});
function mount(){page=mountDetective(host,ctx);page.show(null);}

describe('the three NASA investigations',()=>{
  it('records evidence only after solving the location, motion and spectral tasks',async()=>{
    mount();await ready();englishUi();
    const field=skyCase(firstBarnard(),barnardObject());
    host.querySelector('canvas')!.dispatchEvent(new MouseEvent('pointerdown',{clientX:(field.target.x-field.crop.x+.5)/field.crop.w*100,clientY:(field.target.y-field.crop.y+.5)/field.crop.h*100}));
    expect(host.querySelector('.sd-feedback')!.textContent).toContain('You found Barnard');
    click('.sd-continue');click('[data-answer="East"]');
    expect(host.querySelector('.sd-feedback')!.textContent).toContain('Try another direction');
    expect(host.querySelector('.sd-record-count')!.textContent).toBe('1 / 3 recorded');
    click('[data-answer="North"]');expect(host.querySelector('.sd-feedback')!.textContent).toContain('10.83″');englishUi();
    click('.sd-continue');await vi.waitFor(()=>expect(host.querySelector('.sd-spectrum')).not.toBeNull());
    click('[data-band-answer="D3"]');expect(host.querySelector('.sd-spectrum-feedback')!.textContent).toContain('Try again');
    click('[data-band-answer="D6"]');expect(host.querySelector('.sd-record-count')!.textContent).toBe('3 / 3 recorded');
    expect(storage.get('skyshift-detective-v1')).toContain('21,890');
    englishUi();
    click('.sd-new-notebook');expect(host.querySelector('.sd-record-count')!.textContent).toBe('0 / 3 recorded');
    expect(storage.get('skyshift-detective-v1')).toBe('[]');
  });
  it('moves through actual wavelength samples, exposes errors, and selects the correct detector stack',async()=>{
    mount();await ready(2);const slider=host.querySelector<HTMLInputElement>('.sd-wave')!, points=usableSpectrum(epsObject().spectrum,1);
    slider.value=String(points.length-1);slider.dispatchEvent(new Event('input'));
    expect(host.querySelector('.sd-wave-output')!.textContent).toContain(points.at(-1)!.um.toFixed(3));
    expect(host.querySelector('.sd-point-reading')!.textContent).toContain('D6');
    await vi.waitFor(()=>expect(host.querySelector('.sd-band-image-caption')!.textContent).toContain('D6 ·'));
    click('.sd-bands');expect(host.querySelectorAll('.sd-molecule')).toHaveLength(4);
    click('.sd-errors');expect(host.querySelectorAll('.sd-errorbar')).toHaveLength(0);
    click('[data-detector="1"]');expect(host.querySelector('.sd-wave-output')!.textContent).toContain('D1');
    const select=host.querySelector<HTMLSelectElement>('.sd-spectrum-visit')!;select.value='3';select.dispatchEvent(new Event('change'));
    click('[data-band-answer="D3"]');expect(host.querySelector('.sd-spectrum-feedback')!.textContent).toContain('Visit 3:');
    expect(host.querySelector('.sd-spectrum-feedback')!.textContent).toContain('D3 is higher');
  });
  it('shows an unavailable state instead of fabricating a teaching spectrum, and can retry',async()=>{
    vi.mocked(loadObject).mockImplementation(async t=>t.slug==='barnard'?barnardObject():{...epsObject(),spectrum:barnardObject().spectrum});
    mount();await ready();click('[data-stage="2"]');
    await vi.waitFor(()=>expect(host.querySelector('.sd-retry')).not.toBeNull());
    expect(host.querySelector('.sd-spectrum')).toBeNull();expect(host.textContent).toContain('No unsaturated spectral measurements');
    vi.mocked(loadObject).mockImplementation(async t=>t.slug==='barnard'?barnardObject():epsObject());click('.sd-retry');
    await vi.waitFor(()=>expect(host.querySelector('.sd-spectrum')).not.toBeNull());
  });
  it('rejects synthetic data instead of presenting it as a NASA teaching observation',async()=>{
    vi.mocked(loadFrames).mockImplementation(async t=>{
      const set=frames(t.slug!,t.fov);return {...set,frames:{...set.frames,synthetic:true}};
    });
    mount();await vi.waitFor(()=>expect(host.querySelector('.sd-retry')).not.toBeNull());
    expect(host.textContent).toContain('requires real NASA observations');
    expect(host.querySelector('canvas')).toBeNull();
  });
  it('does not replace a spectrum with an earlier task that finishes loading late',async()=>{
    let finish!:(set:LoadedFrames)=>void;
    vi.mocked(loadFrames).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
    mount();await ready(2);finish(frames('barnard'));
    await vi.waitFor(()=>expect(host.querySelector('.sd-spectrum')).not.toBeNull());
    await new Promise(resolve=>setTimeout(resolve,0));
    expect(host.querySelector('.sd-sky-canvas')).toBeNull();
    expect(host.querySelector('[data-stage="2"]')!.getAttribute('aria-selected')).toBe('true');
  });
  it('stops blinking on departure and aborts its data requests on destruction',async()=>{
    const clear=vi.spyOn(globalThis,'clearInterval');mount();await ready(1);click('.sd-blink');
    expect(host.querySelector('.sd-blink')!.getAttribute('aria-pressed')).toBe('true');
    const signal=vi.mocked(loadFrames).mock.calls[0][1]!;page!.hide?.();expect(clear).toHaveBeenCalled();
    page!.destroy();page=undefined;expect(signal.aborted).toBe(true);expect(host.childElementCount).toBe(0);
  });
  it('integrates with Learn and returns without leaving the activity mounted',async()=>{
    expect(parseLearnRoute('detective')).toEqual({view:'detective'});expect(learnHref({view:'detective'})).toBe('#/learn/detective');
    page=mountLearn(host,ctx);page.show(null);
    expect(host.querySelector('.ln-detective-entry')!.getAttribute('href')).toBe('#/learn/detective');
    page.show('detective');await vi.waitFor(()=>expect(host.querySelector('.sd-tabs')).not.toBeNull());
    expect(document.title).toBe('Sky detective: SkyShift');page.show('1');expect(host.querySelector('.sd-tabs')).toBeNull();
  });
});
