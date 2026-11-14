export type Mansion={index:number;zh:string;en:string;du:number};
export type Neighbour={asterism_zh:string;asterism_en:string;star_zh:string;sep_deg:number;relation:{zh:string;en:string}};
export type Location={mansion:Mansion;nearest:Neighbour[];log_zh:string;log_en:string};
export type Segment={zh:string;en:string;segments:[[number,number],[number,number]][]};
export type Guxing={locate(ra:number,dec:number,options?:Record<string,unknown>):Location;mansion(ra:number,dec:number,epoch?:number):Mansion;segments(ra:number,dec:number,radius:number):Segment[];mansions:[string,string,string,number,number,number][]};
export type Guest={id:string;year:number;ra:number;dec:number;remnant:string;record_zh:string;record_en:string;source:string;source_checked:boolean};
export type PlanetEvent={time:string;log_zh:string;log_en:string;ra:number;dec:number};
export type CultureWindow=Window & {Guxing:new()=>Guxing;GUXING_NASA:{guest_stars:{items:Guest[]};planets:{start:string;end:string;source:string;events:PlanetEvent[]};spherex_remnants:{items:{id:string;image:string;remnant:string;seen_in_spherex:string;source:string}[]}}};
let pending:Promise<void>|undefined;
export function loadCulture(){
  if(!pending)pending=(async()=>{for(const name of ['guxing-data','guxing','guxing-nasa','guxing-widget']){
    if(document.querySelector(`script[data-culture="${name}"]`))continue;
    await new Promise<void>((resolve,reject)=>{const script=document.createElement('script');script.src=`/guxing/${name}.js`;script.dataset.culture=name;script.onload=()=>resolve();script.onerror=()=>{script.remove();reject(new Error('Could not load the historical sky module. Please try again.'))};document.head.append(script)});
  }})().catch(e=>{pending=undefined;throw e});
  return pending;
}
export function culture(){const w=window as unknown as CultureWindow;return {gx:new w.Guxing(),nasa:w.GUXING_NASA}}
export function englishCultureText(text:string){
  const {gx}=culture();
  for(const [name,,english] of gx.mansions)text=text.replaceAll(`${name}宿`,`${english} Lodge`);
  return text.replace(/\s*\([^()]*\p{Script=Han}[^()]*\)/gu,'');
}
