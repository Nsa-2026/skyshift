import {it,expect,vi,afterEach} from 'vitest';
import {loadFrames} from './client';
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
