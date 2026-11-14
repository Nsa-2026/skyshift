import targetCatalog from '../data/targets.json';
import {getJson as json,loadTargetIndex,loadFrames as loadRawFrames,loadObject as loadRawObject,resolve as resolvePosition} from './client';
import type {Det,Clock} from './types';
export type Target = {slug:string;name:string;ra:number;dec:number;default_det:number;n_frames:number;n_visits:number;clock?:string;fov?:number;published?:boolean};
export type Epoch = {id:string;det:number;kind:string;center?:{ra:number;dec:number};mjd_mid:number;mjd_start:number|null;mjd_end:number|null;n_exposures:number;data_url:string;scale:number;zero:number;flag_frac:number|null;sat_frac:number|null};
export type Frames = {clock:string;center:{ra:number;dec:number};size:[number,number];pixel_arcsec:number;epochs:Epoch[];gaia:unknown[];known:unknown[];data_release:string;archive_checked:string;synthetic?:boolean};
export type Spectrum = {um:number;flux_ujy:number|null;err_ujy:number|null;det:number;mjd:number;visit:number;saturated:boolean};
export type ObjectData = {name:string|null;spectrum:Spectrum[];measure:unknown|null;gaia:unknown|null;synthetic?:boolean};
export type Candidate = {ra:number;dec:number;status:string;n_visits?:number;p_false:number|null;pm_arcsec_yr?:number;pmra_arcsec_yr?:number;pmdec_arcsec_yr?:number;mjd_ref?:number;n_slides?:number;speed_arcsec_day?:number};
export type Analysis = {detector:number;n_slides:number;source:{n_frames:number;n_visits:number;coverage_arcmin:number;flags_complete?:boolean};warnings:string[];slow_movers:Candidate[];fast_movers:Candidate[];cold_candidates:Candidate[]};
export {json};
export const getTargets=async()=>{
 const base:Target[]=(await json<Target[]>('/data/targets.json')).map(t=>({...t,published:true}));
 for(const t of targetCatalog.targets){
  if(base.some(b=>b.slug===t.slug))continue;
  const idx=await loadTargetIndex(t.slug);
  if(!idx){base.push({slug:t.slug,name:t.name,ra:t.ra,dec:t.dec,default_det:t.det,clock:t.clock,fov:t.fov,n_frames:0,n_visits:0,published:false});continue;}
  const file=await json<Frames>(`/data/${t.slug}/${idx.own}`);
  base.push({slug:t.slug,name:t.name,ra:file.center.ra,dec:file.center.dec,default_det:t.det,clock:t.clock,fov:t.fov,published:true,n_frames:file.epochs.reduce((n,e)=>n+e.n_exposures,0),n_visits:1});
 }
 return base;
};
export const resolve=resolvePosition;
export async function loadFrames(target:Target,det:number,clock:string,signal:AbortSignal,view="raw",fov=clock==='months'?5:20){
 let data:Frames|undefined;let base:string;
 if(view==='raw'){
   const result=await loadRawFrames({ra:target.ra,dec:target.dec,slug:target.slug||null,clock:clock as Clock,det:det as Det,fov},signal);
   data=result.frames as Frames;base=result.jsonUrl;
 }else{
   base=new URL(`/science-data/${target.slug}/${view}.json`,location.href).href;
   if(target.slug&&view!=='refdiff'&&fov===5){try{const result=await json<Frames>(base,signal);if(result.clock===clock&&result.epochs.some(e=>e.det===det))data=result}catch(e){if(signal.aborted)throw e}}
   if(!data){base=new URL(`/api/frames?ra=${target.ra}&dec=${target.dec}&clock=${clock}&det=${det}&view=${view}&fov=${fov}`,location.href).href;data=await json<Frames>(base,signal)}
 }
 return {data,jsonUrl:base};
}
export async function loadObject(t:Target,signal:AbortSignal){
 return await loadRawObject({ra:t.ra,dec:t.dec,slug:t.slug||null},signal) as ObjectData;
}
export async function loadAnalysis(t:Target,det:number,signal:AbortSignal){
 if(t.slug){try{const d=await json<Analysis>(`/science-data/${t.slug}/analysis.json`,signal);if(d.detector===det)return d}catch(e){if(signal.aborted)throw e}}
 return json<Analysis>(`/api/analysis?ra=${t.ra}&dec=${t.dec}&det=${det}`,signal);
}
