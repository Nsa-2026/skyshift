import {loadFrameData,toMJySr,loadFrames} from '../api/client';
import type {Epoch,LoadedFrames} from '../api/types';
import type {Target} from '../api/science-client';
import {median,offset,robustLimits,type Limits} from '../blink/math';
import {paint,readPalette,buildLut,plainIndex} from '../blink/paint';
export type Observation={epoch:Epoch;values:Float32Array;set:LoadedFrames};
export async function observation(set:LoadedFrames,epoch:Epoch,signal:AbortSignal):Promise<Observation>{
  const raw=await loadFrameData(epoch,set.jsonUrl,signal);
  if(raw.length!==set.frames.size[0]*set.frames.size[1])throw new Error('Frame data length does not match the image dimensions');
  const values=toMJySr(raw,epoch);return {epoch,values:offset(values,median(values)),set};
}
export async function lessonObservations(target:Target,det:3|6|2,signal:AbortSignal,options:{fov?:5|15;all?:boolean}={}){
  const set=await loadFrames({...target,slug:target.slug,clock:'months',fov:options.fov??5,det},signal);
  const epochs=set.frames.epochs.filter(e=>e.det===det).sort((a,b)=>a.mjd_mid-b.mjd_mid);
  if(epochs.length<2)throw new Error('This band has fewer than two visits. Choose another field or try again later.');
  if(options.all&&epochs.length<3)throw new Error('This field has fewer than three observations, so tracking is unavailable.');
  const selected=options.all?[epochs[0],epochs[Math.floor((epochs.length-1)/2)],epochs.at(-1)!]:[epochs[0],epochs.at(-1)!];
  return Promise.all(selected.map(epoch=>observation(set,epoch,signal)));
}
export function displayLimits(observations:Observation[]){return robustLimits(observations.map(o=>o.values),.45)}
export function drawObservation(canvas:HTMLCanvasElement,o:Observation,limits:Limits){
  const [w,h]=o.set.frames.size;canvas.width=w;canvas.height=h;
  const ctx=canvas.getContext('2d');if(!ctx)return;
  const pixels=ctx.createImageData(w,h),palette=readPalette(canvas);
  paint(o.values,pixels.data,buildLut(palette,false,false),plainIndex(limits,'asinh'));ctx.putImageData(pixels,0,0);
}
export function bandPairs(short:Epoch[],long:Epoch[]){
  const unused=new Set(long);
  return short.flatMap(a=>{
    const b=[...unused].filter(b=>a.mjd_start!=null&&a.mjd_end!=null&&b.mjd_start!=null&&b.mjd_end!=null&&Math.max(a.mjd_start,b.mjd_start)<=Math.min(a.mjd_end,b.mjd_end)).sort((x,y)=>Math.abs(x.mjd_mid-a.mjd_mid)-Math.abs(y.mjd_mid-a.mjd_mid))[0];
    if(!b)return [];unused.delete(b);return [{short:a,long:b}];
  });
}
