import {it,expect,vi,afterEach} from 'vitest';
import {loadFrames,loadObject,loadTargetIndex,loadValidation} from './client';
afterEach(()=>vi.unstubAllGlobals());
it.each([{}, {frames:null}, {frames:[null]}, {frames:[{clock:'months',fov:5,file:'frames.json',dets:null}]}])('falls back from a malformed published index without breaking frame loading: %j',async index=>{
 const calls:string[]=[];
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>{
  calls.push(String(url));
  if(String(url).endsWith('index.json'))return {ok:true,json:async()=>index};
  if(!String(url).includes('/api/'))return {ok:false,status:404,json:async()=>({})};
  return {ok:true,json:async()=>({clock:'months',epochs:[{det:2}]})};
 }));
 const result=await loadFrames({ra:10,dec:20,slug:'field',clock:'months',fov:5,det:2});
 expect(result.origin).toBe('api');
 expect(calls.at(-1)).toContain('/api/frames?');
});

it('stops at a cancelled target index instead of starting legacy and API fallback requests',async()=>{
 const ctl=new AbortController();
 const cancelled=new DOMException('The operation was aborted.','AbortError');
 vi.stubGlobal('fetch',vi.fn(async()=>{ctl.abort();throw cancelled}));
 await expect(loadFrames({ra:10,dec:20,slug:'field',clock:'months',fov:5,det:2},ctl.signal)).rejects.toBe(cancelled);
 expect(fetch).toHaveBeenCalledTimes(1);
});

it('distinguishes a cancelled index from an unpublished target',async()=>{
 const cancelled=new DOMException('The operation was aborted.','AbortError');
 vi.stubGlobal('fetch',vi.fn(async()=>{throw cancelled}));
 await expect(loadTargetIndex('field')).rejects.toBe(cancelled);
});

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

it.each([
 {clock:'months' as const,fov:15,size:[146,146],pixel_arcsec:2.05},
 {clock:'hours' as const,fov:60,size:[195,195],pixel_arcsec:6.15},
])('does not substitute a smaller legacy $clock field when its index is unavailable',async({clock,fov,size,pixel_arcsec})=>{
 const calls:string[]=[];
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>{
  calls.push(String(url));
  if(String(url).endsWith('index.json'))return {ok:false,status:404,json:async()=>({})};
  return {ok:true,json:async()=>({clock,size,pixel_arcsec,epochs:[{det:2}]})};
 }));
 const result=await loadFrames({ra:10,dec:20,slug:'field',clock,fov,det:2});
 expect(result.origin).toBe('api');
 expect(calls.at(-1)).toContain(`/api/frames?ra=10&dec=20&clock=${clock}&fov=${fov}&det=2`);
});

it.each([
 {clock:'months' as const,fov:5,size:[146,146],pixel_arcsec:2.05},
 {clock:'hours' as const,fov:20,size:[195,195],pixel_arcsec:6.15},
])('retains a legacy $clock static field whose grid matches the requested size',async({clock,fov,size,pixel_arcsec})=>{
 const calls:string[]=[];
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>{
  calls.push(String(url));
  if(String(url).endsWith('index.json'))return {ok:false,status:404,json:async()=>({})};
  return {ok:true,json:async()=>({clock,size,pixel_arcsec,epochs:[{det:2}]})};
 }));
 const result=await loadFrames({ra:10,dec:20,slug:'field',clock,fov,det:2});
 expect(result.origin).toBe('static');
 expect(calls.at(-1)).toContain('/data/field/frames.json');
 expect(calls.some(url=>url.includes('/api/frames?'))).toBe(false);
});
