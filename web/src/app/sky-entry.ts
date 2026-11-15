import type {SkyPos} from '../api/types';
import type {View} from './modules';
import {featuredNear,targetBySlug,viewForTarget} from './state';

export type SkySelection=SkyPos & Partial<Pick<View,'clock'|'det'|'slug'>>;

/** Sky-map angles are degrees; the observation uses a supported field in arcminutes. */
export function observationForSky(at:SkySelection):View {
  const named=targetBySlug(at.slug);
  if(named)return viewForTarget(named);
  const near=featuredNear(at);
  if(near && (!at.clock || near.clock===at.clock))return viewForTarget(near);
  const clock=at.clock??'months';
  return {ra:at.ra,dec:at.dec,clock,det:at.det??2,fov:clock==='hours'?20:5,slug:null,name:null};
}
