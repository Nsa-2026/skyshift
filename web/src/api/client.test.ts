import {it,expect,vi,afterEach} from 'vitest';
import {loadFrames,loadObject,loadValidation} from './client';
afterEach(()=>vi.unstubAllGlobals());
it('falls back to the API when a published field lacks the requested detector',async()=>{
 const calls:string[]=[];
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>{
  calls.push(String(url));
  const data=String(url).endsWith('index.json')?{frames:[{clock:'months',fov:15,dets:[2],file:'frames_months_15.json'}]}:
   {clock:'months',epochs:[{det:String(url).includes('/api/')?6:2}]};
  return {ok:true,json:async()=>data};
 }));
 const result=await loadFrames({ra:10,dec:20,slug:'field',clock:'months',fov:15,det:6});
 expect(result.origin).toBe('api');
 expect(calls.at(-1)).toContain('det=6');
});
it('never shows a synthetic example as a real observation, but still hands labelled examples to Results',async()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,json:async()=>({synthetic:true,clock:'months',epochs:[],checks:[]})})));
 await expect(loadFrames({ra:10,dec:20,slug:null,clock:'months',fov:5,det:2})).rejects.toMatchObject({code:'upstream'});
 await expect(loadObject({ra:10,dec:20})).rejects.toMatchObject({code:'upstream'});
 expect(await loadValidation()).toMatchObject({synthetic:true});
});
