export type Target = {slug:string;name:string;ra:number;dec:number;default_det:number;n_frames:number;n_visits:number};
export type Epoch = {id:string;det:number;kind:string;mjd_mid:number;mjd_start:number|null;mjd_end:number|null;n_exposures:number;data_url:string;scale:number;zero:number;flag_frac:number|null;sat_frac:number|null};
export type Frames = {clock:string;center:{ra:number;dec:number};size:[number,number];pixel_arcsec:number;epochs:Epoch[];gaia:unknown[];known:unknown[];data_release:string;archive_checked:string;synthetic?:boolean};
export type Spectrum = {um:number;flux_ujy:number|null;err_ujy:number|null;det:number;mjd:number;visit:number;saturated:boolean};
export type ObjectData = {name:string|null;spectrum:Spectrum[];measure:unknown|null;gaia:unknown|null;synthetic?:boolean};
export type Candidate = {ra:number;dec:number;status:string;n_visits?:number;p_false:number|null;pm_arcsec_yr?:number;pmra_arcsec_yr?:number;pmdec_arcsec_yr?:number;mjd_ref?:number;n_slides?:number;speed_arcsec_day?:number};
export type Analysis = {detector:number;n_slides:number;source:{n_frames:number;n_visits:number;coverage_arcmin:number;flags_complete?:boolean};warnings:string[];slow_movers:Candidate[];fast_movers:Candidate[];cold_candidates:Candidate[]};
export async function json<T>(url:string, signal?:AbortSignal):Promise<T> {
 const r=await fetch(url,{signal}); if(!r.ok){let detail;try{detail=(await r.json()).error?.message}catch{}throw new Error(detail||`数据请求失败 (${r.status})`)}
 const data=await r.json(); if(data.synthetic) throw new Error('此响应为模拟样例，未作为真实观测加载。');return data;
}
export const getTargets=()=>json<Target[]>('/data/targets.json');
export const resolve=(q:string,signal?:AbortSignal)=>json<{slug:string|null;ra:number;dec:number;name:string|null}>(`/api/resolve?q=${encodeURIComponent(q)}`,signal);
export async function loadFrames(target:Target,det:number,clock:string,signal:AbortSignal,view="raw"){
 let data:Frames|undefined;let base:string;
 const url=new URL(`/data/${target.slug}/${view==='raw'?'frames':`frames_${view}`}.json`,location.href).href;
 if(clock==='months' && target.slug && view!=='refdiff'){try{const s=await json<Frames>(url,signal); if(s.clock===clock && s.epochs.some(e=>e.det===det)){data={...s,epochs:s.epochs.filter(e=>e.det===det)};base=url}}catch(e){if(signal.aborted)throw e}}
 if(!data){base=new URL(`/api/frames?ra=${target.ra}&dec=${target.dec}&clock=${clock}&det=${det}&view=${view}`,location.href).href;data=await json<Frames>(base,signal)}
 const arrays=await Promise.all(data.epochs.map(async e=>{const r=await fetch(new URL(e.data_url,base!),{signal});if(!r.ok)throw new Error('帧数据暂不可用');const b=await r.arrayBuffer();if(b.byteLength!==data!.size[0]*data!.size[1]*2)throw new Error('帧尺寸与接口不一致');const v=new DataView(b);return Float32Array.from({length:b.byteLength/2},(_,i)=>{const raw=v.getUint16(i*2,true);return raw===0?NaN:raw*e.scale+e.zero})}));
 return {data,arrays};
}
export async function loadObject(t:Target,signal:AbortSignal){
 if(t.slug){try{return await json<ObjectData>(`/data/${t.slug}/object.json`,signal)}catch(e){if(signal.aborted)throw e}}
 return json<ObjectData>(`/api/object?ra=${t.ra}&dec=${t.dec}`,signal);
}
export async function loadAnalysis(t:Target,det:number,signal:AbortSignal){
 if(t.slug){try{const d=await json<Analysis>(`/data/${t.slug}/analysis.json`,signal);if(d.detector===det)return d}catch(e){if(signal.aborted)throw e}}
 return json<Analysis>(`/api/analysis?ra=${t.ra}&dec=${t.dec}&det=${det}`,signal);
}
