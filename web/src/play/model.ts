import type {ObjectData, Target} from '../api/science-client';
import type {GaiaStar,Measure,SkyPos} from '../api/types';
import {separation} from '../product/evidence';
export const LESSONS = [
  {slug:'barnard',title:'A star on the move',subtitle:'Find a star that shifts between visits',hint:'The shift is small. Compare the observations and look for a point moving north.',western:'Ophiuchus'},
  {slug:'epsindib',title:'A southern neighbour',subtitle:'Look for a gentler shift',hint:'Two brown dwarfs appear as one point here. Compare their position carefully and distinguish motion from flickering noise.',western:'Indus'},
  {slug:'rwaur',title:'A change without a move',subtitle:'Watch the brightness change',hint:'Look for a target that brightens or fades. It need not move; saturation can affect how bright its core appears.',western:'Auriga'},
  {slug:'rdra',title:'A star that pulses',subtitle:'Find another changing star',hint:'Inspect this variable star. Wavelength sampling varies even within a detector, so consider photometry and saturation together.',western:'Draco'},
  {slug:'wise0855',title:'Switch on infrared vision',subtitle:'Find a star hidden at short wavelengths',hint:'A target that is faint at short wavelengths may appear at longer wavelengths. Switch to D6, which covers 4.6 µm, and compare.',western:'Hydra'},
];
export function expectedPosition(target:Target,object:ObjectData,epoch:number):SkyPos{
  const g=object.gaia as GaiaStar|null;
  if(g&&g.pmra!=null&&g.pmdec!=null){const dt=(epoch-57388.5)/365.25;return {ra:(g.ra+g.pmra*dt/(3.6e6*Math.cos(g.dec*Math.PI/180))+360)%360,dec:g.dec+g.pmdec*dt/3.6e6}}
  return {ra:target.ra,dec:target.dec};
}
export function isTargetPick(pick:SkyPos,target:Target,object:ObjectData,epoch:number,pixelArcsec:number){return separation(pick,expectedPosition(target,object,epoch))<=Math.max(3*pixelArcsec,15)}
export function measureOf(o:ObjectData|undefined){return o?.measure as Measure|null|undefined}
const key='skyshift-lessons-v1';
export function readProgress():string[]{try{const x=JSON.parse(localStorage.getItem(key)||'[]');return Array.isArray(x)?x.filter(s=>typeof s==='string'&&[...LESSONS.map(l=>l.slug),'SN1054','SN1572'].includes(s)):[]}catch{return []}}
export function saveProgress(done:string[]){try{localStorage.setItem(key,JSON.stringify([...new Set(done)]));return true}catch{return false}}

export type LessonStep='hunt'|'done';
export function lessonSteps(_slug:string):LessonStep[]{return ['hunt','done']}
export type ImageGeometry={size:[number,number];pixel_arcsec:number;center:SkyPos};
export type Crop={x:number;y:number;size:number};
export function positionPixel(pos:SkyPos,frame:ImageGeometry){
  const dra=((pos.ra-frame.center.ra+540)%360)-180;
  return {x:frame.size[0]/2-dra*Math.cos(frame.center.dec*Math.PI/180)*3600/frame.pixel_arcsec,y:frame.size[1]/2-(pos.dec-frame.center.dec)*3600/frame.pixel_arcsec};
}
export function targetCrop(pos:SkyPos,frame:ImageGeometry):Crop{
  const p=positionPixel(pos,frame),size=Math.min(50,...frame.size);
  return {x:Math.max(0,Math.min(frame.size[0]-size,p.x-size/2)),y:Math.max(0,Math.min(frame.size[1]-size,p.y-size/2)),size};
}
export function cropPick(x:number,y:number,crop:Crop,frame:ImageGeometry):SkyPos{
  return {ra:(frame.center.ra-(crop.x+x*crop.size-frame.size[0]/2)*frame.pixel_arcsec/(3600*Math.cos(frame.center.dec*Math.PI/180))+360)%360,dec:frame.center.dec-(crop.y+y*crop.size-frame.size[1]/2)*frame.pixel_arcsec/3600};
}
