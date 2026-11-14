// Science workspace data (contracts/api.md, "Science workspace"). Static first: the five featured
// fields' prepared products live in /science-data/<slug>/; anything else asks /api/*, which
// answers or says why not.
import {getJson as json,loadFrames as loadRawFrames} from './client';
import type {Det,Clock} from './types';
export type Target = {slug:string;name:string;ra:number;dec:number;default_det:number;n_frames:number;n_visits:number;clock?:string;fov?:number;published?:boolean};
export type Epoch = {id:string;det:number;kind:string;center?:{ra:number;dec:number};mjd_mid:number;mjd_start:number|null;mjd_end:number|null;n_exposures:number;data_url:string;scale:number;zero:number;flag_frac:number|null;sat_frac:number|null};
export type Frames = {clock:string;center:{ra:number;dec:number};size:[number,number];pixel_arcsec:number;epochs:Epoch[];gaia:unknown[];known:unknown[];data_release:string;archive_checked:string;synthetic?:boolean};
export type Spectrum = {um:number;flux_ujy:number|null;err_ujy:number|null;det:number;mjd:number;visit:number;saturated:boolean};
export type ObjectData = {name:string|null;spectrum:Spectrum[];measure:unknown|null;gaia:unknown|null;synthetic?:boolean};
export type Candidate = {ra:number;dec:number;status:string;n_visits?:number;p_false:number|null;pm_arcsec_yr?:number;pmra_arcsec_yr?:number;pmdec_arcsec_yr?:number;mjd_ref?:number;n_slides?:number;speed_arcsec_day?:number};
export type Analysis = {detector:number;n_slides:number;source:{n_frames:number;n_visits:number;coverage_arcmin:number;flags_complete?:boolean};warnings:string[];slow_movers:Candidate[];fast_movers:Candidate[];cold_candidates:Candidate[]};
export {json};
/** Featured fields with prepared science products in web/public/science-data/<slug>/. */
export const SCIENCE_SLUGS = ['barnard','epsindib','wise0855','rwaur','rdra'] as const;
export const hasScience = (slug: string | null | undefined): boolean => !!slug && (SCIENCE_SLUGS as readonly string[]).includes(slug);
export async function loadFrames(target:Target,det:number,clock:string,signal:AbortSignal,view="raw",fov=clock==='months'?5:20){
 let data:Frames|undefined;let base:string;
 if(view==='raw'){
   const result=await loadRawFrames({ra:target.ra,dec:target.dec,slug:target.slug||null,clock:clock as Clock,det:det as Det,fov},signal);
   data=result.frames as Frames;base=result.jsonUrl;
 }else{
   base=new URL(`/science-data/${target.slug}/${view}.json`,location.href).href;
   if(hasScience(target.slug)&&fov===5){try{const result=await json<Frames>(base,signal);if(result.clock===clock&&result.epochs.some(e=>e.det===det))data=result}catch(e){if(signal.aborted)throw e}}
   if(!data){base=new URL(`/api/frames?ra=${target.ra}&dec=${target.dec}&clock=${clock}&det=${det}&view=${view}&fov=${fov}`,location.href).href;data=await json<Frames>(base,signal)}
 }
 return {data,jsonUrl:base};
}
export async function loadAnalysis(t:Target,det:number,signal:AbortSignal){
 if(hasScience(t.slug)){try{const d=await json<Analysis>(`/science-data/${t.slug}/analysis.json`,signal);if(d.detector===det)return d}catch(e){if(signal.aborted)throw e}}
 return json<Analysis>(`/api/analysis?ra=${t.ra}&dec=${t.dec}&det=${det}`,signal);
}
/** The science product (sensitivity, light curves, reference spectrum, exposure list) of a field. */
export async function loadScience<T>(t:{slug:string|null;ra:number;dec:number},signal:AbortSignal):Promise<T>{
 if(hasScience(t.slug)){try{return await json<T>(`/science-data/${t.slug}/science.json`,signal)}catch(e){if(signal.aborted)throw e}}
 return json<T>(`/api/science?ra=${t.ra}&dec=${t.dec}`,signal);
}
