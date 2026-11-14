import {it,expect,vi,afterEach} from 'vitest';
import {loadFrames,loadAnalysis,loadScience,hasScience,type Target} from './science-client';
const target:Target={slug:'barnard',name:'Field',ra:10,dec:20,default_det:2,n_frames:0,n_visits:0};
afterEach(()=>vi.unstubAllGlobals());
it('loads a model from published science data with its own binary base URL',async()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,json:async()=>({clock:'months',epochs:[{det:2}]})})));
 const result=await loadFrames(target,2,'months',new AbortController().signal,'model',5);
 expect(result.jsonUrl).toContain('/science-data/barnard/model.json');
 expect(fetch).toHaveBeenCalledTimes(1);
});
it('requests the selected detector when the published model lacks it',async()=>{
 const urls:string[]=[];
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>{urls.push(String(url));return {ok:true,json:async()=>({clock:'months',epochs:[{det:String(url).includes('/api/')?6:2}]})}}));
 const result=await loadFrames(target,6,'months',new AbortController().signal,'resid',5);
 expect(result.jsonUrl).toContain('/api/frames?');expect(urls.at(-1)).toContain('view=resid');expect(urls.at(-1)).toContain('det=6');
});
it('asks the API directly for a field without prepared products (no static 404 round trip)',async()=>{
 const urls:string[]=[];
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>{urls.push(String(url));return {ok:true,json:async()=>({detector:2})}}));
 await loadAnalysis({...target,slug:'elpis'},2,new AbortController().signal);
 await loadScience({slug:'',ra:10,dec:20},new AbortController().signal);
 expect(urls).toEqual(['/api/analysis?ra=10&dec=20&det=2','/api/science?ra=10&dec=20']);
 expect(hasScience('wise0855')).toBe(true);expect(hasScience('elpis')).toBe(false);expect(hasScience(null)).toBe(false);
});
it('never treats a synthetic fixture as a real derived image',async()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,json:async()=>({synthetic:true,clock:'months',epochs:[{det:2}]})})));
 await expect(loadFrames(target,2,'months',new AbortController().signal,'model',5)).rejects.toMatchObject({code:'upstream'});
});
