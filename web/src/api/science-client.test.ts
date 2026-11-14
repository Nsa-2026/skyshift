import {it,expect,vi,afterEach} from 'vitest';
import {loadFrames,type Target} from './science-client';
const target:Target={slug:'field',name:'Field',ra:10,dec:20,default_det:2,n_frames:0,n_visits:0};
afterEach(()=>vi.unstubAllGlobals());
it('loads a model from published science data with its own binary base URL',async()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,json:async()=>({clock:'months',epochs:[{det:2}]})})));
 const result=await loadFrames(target,2,'months',new AbortController().signal,'model',5);
 expect(result.jsonUrl).toContain('/science-data/field/model.json');
 expect(fetch).toHaveBeenCalledTimes(1);
});
it('requests the selected detector when the published model lacks it',async()=>{
 const urls:string[]=[];
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>{urls.push(String(url));return {ok:true,json:async()=>({clock:'months',epochs:[{det:String(url).includes('/api/')?6:2}]})}}));
 const result=await loadFrames(target,6,'months',new AbortController().signal,'resid',5);
 expect(result.jsonUrl).toContain('/api/frames?');expect(urls.at(-1)).toContain('view=resid');expect(urls.at(-1)).toContain('det=6');
});
