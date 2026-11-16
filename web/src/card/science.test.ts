import {afterEach,expect,it,vi} from 'vitest';
import {mountScience} from './science';
import type {Target,Spectrum} from '../api/science-client';
import {fetchCatalogue,forgetCatalogues} from '../api/catalogues';

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

it('shows a catalogue answer the Signals checks already fetched instead of asking again',async()=>{
 forgetCatalogues();
 const urls:string[]=[];
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>{urls.push(url);
  if(url.startsWith('/api/gaia'))return {ok:true,status:200,json:async()=>({gaia:[{id:'1',ra:10,dec:20,pmra:1,pmdec:2,parallax:null,g:12}]})};
  return {ok:false,status:404,json:async()=>({error:{code:'not_found',message:'No published field'}})};
 }));
 await fetchCatalogue('gaia',target);  // what the Signals list does when it loads
 const host=document.createElement('div');
 const science=mountScience(host,vi.fn(),()=>spectrum);
 science.panel('crossmatch');
 await science.load(target,new AbortController().signal);
 host.querySelector<HTMLButtonElement>('[data-service="gaia"]')!.click();
 await vi.waitFor(()=>expect(host.querySelector('#enrichment-status')!.textContent).toContain('1 records'));
 expect(host.querySelector('#enrichment-status')!.textContent).toContain('fetched earlier, not asked again');
 expect(host.querySelector('#enrichment-result td')!.textContent).toBe('1');
 expect(urls.filter(u=>u.startsWith('/api/gaia'))).toEqual(['/api/gaia?ra=10&dec=20&r=3']);
 forgetCatalogues();
});

it('asks a failed catalogue again on the next click',async()=>{
 forgetCatalogues();
 let calls=0;
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>{
  if(url.includes('kind=catwise')){calls++;return calls===1
   ?{ok:false,status:502,json:async()=>({error:{code:'upstream',message:'NASA/IPAC IRSA (CatWISE2020) did not answer'}})}
   :{ok:true,status:200,json:async()=>({source:'NASA/IPAC CatWISE2020',sources:[],note:'n'})}}
  return {ok:false,status:404,json:async()=>({error:{code:'not_found',message:'No published field'}})};
 }));
 const host=document.createElement('div');
 const science=mountScience(host,vi.fn(),()=>spectrum);
 await science.load(target,new AbortController().signal);
 const button=host.querySelector<HTMLButtonElement>('[data-service="catwise"]')!;
 button.click();
 await vi.waitFor(()=>expect(host.querySelector('#enrichment-status')!.textContent).toContain('Query unavailable: NASA/IPAC IRSA (CatWISE2020) did not answer'));
 await vi.waitFor(()=>expect(button.disabled).toBe(false));
 button.click();
 await vi.waitFor(()=>expect(host.querySelector('#enrichment-status')!.textContent).toContain('0 records'));
 expect(calls).toBe(2);
 forgetCatalogues();
});
