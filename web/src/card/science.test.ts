import {afterEach,expect,it,vi} from 'vitest';
import {mountScience} from './science';
import type {Target,Spectrum} from '../api/science-client';

const target:Target={slug:'',name:'Selected sky',ra:10,dec:20,default_det:2,n_frames:0,n_visits:0};
const spectrum:Spectrum[]=[
 {um:1.2,flux_ujy:10,err_ujy:1,det:2,mjd:60000,visit:1,saturated:false},
 {um:1.2,flux_ujy:12,err_ujy:1,det:2,mjd:60200,visit:2,saturated:false},
];
afterEach(()=>vi.unstubAllGlobals());

it.each([
 {status:404,code:'not_found',message:'No published field',expected:'has not been published'},
 {status:502,code:'upstream',message:'IRSA request timed out',expected:'could not be loaded'},
])('distinguishes a $status response while keeping formal photometry available',async({status,code,message,expected})=>{
 vi.stubGlobal('fetch',vi.fn(async()=>({ok:false,status,json:async()=>({error:{code,message}})})));
 const host=document.createElement('div');
 const science=mountScience(host,vi.fn(),()=>spectrum);
 science.panel('lightcurve');
 await science.load(target,new AbortController().signal);
 const feedback=host.querySelector('#science-status')!.textContent!;
 expect(feedback).toContain(expected);
 if(status===502){expect(feedback).toContain(message);expect(feedback).not.toContain('has not been published');}
 expect(host.querySelector('#light-chart svg')).not.toBeNull();
});

it('reports a network failure without claiming the product is unpublished',async()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>{throw new TypeError('Failed to fetch')}));
 const host=document.createElement('div');
 const science=mountScience(host,vi.fn(),()=>spectrum);
 await science.load(target,new AbortController().signal);
 expect(host.querySelector('#science-status')!.textContent).toContain('could not be loaded');
 expect(host.querySelector('#science-status')!.textContent).not.toContain('has not been published');
});

it('does not show a request failure after the selected observation is cancelled',async()=>{
 const controller=new AbortController();
 vi.stubGlobal('fetch',vi.fn(async()=>{controller.abort();throw new DOMException('Aborted','AbortError')}));
 const host=document.createElement('div');
 const science=mountScience(host,vi.fn(),()=>spectrum);
 await science.load(target,controller.signal);
 expect(host.querySelector('#science-status')!.textContent).toBe('');
});
