import type {ObjectData, Target} from '../api/science-client';
import type {GaiaStar,Measure,SkyPos} from '../api/types';
import {separation} from '../product/evidence';
export const LESSONS = [
  {slug:'barnard',title:'会走路的星',subtitle:'找一颗偷偷走动的星',hint:'恒星的位置变化很小。来回比较两次观测，试着找出向北挪动的点。',western:'蛇夫座 Ophiuchus'},
  {slug:'epsindib',title:'南天的邻居',subtitle:'这次，动得更轻一点',hint:'两颗褐矮星在这里看起来像一个点。仔细比较位置，不要把噪声闪烁当成移动。',western:'印第安座 Indus'},
  {slug:'rwaur',title:'不走，也会变',subtitle:'看看亮暗有没有变化',hint:'这次检查会变亮或变暗的目标。它不需要明显移动；饱和核心会影响亮度判断。',western:'御夫座 Auriga'},
  {slug:'rdra',title:'星星的呼吸',subtitle:'再找一颗会变的星',hint:'检查这颗变星。同波段中也有波长采样差异，测光与饱和状态要一起看。',western:'天龙座 Draco'},
  {slug:'wise0855',title:'打开红外夜视',subtitle:'用夜视镜找藏起来的星',hint:'短波里不明显的目标，可能在长波里留下线索。切换到覆盖 4.6 µm 的 D6，再判断。',western:'长蛇座 Hydra'},
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
