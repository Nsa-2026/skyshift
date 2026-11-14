import {describe,expect,it} from 'vitest';
import {AREA_FEATURES,areaRoute} from './navigation';
import {discoverQuery,isDiscoverUrl} from '../review/model';
import {routeQuery} from '../discover/filters';

describe('area-first navigation',()=>{
  it('places every feature under its area and resolves every navigation entry',()=>{
    for(const [mode,features] of Object.entries(AREA_FEATURES))for(const feature of features){
      expect(areaRoute(feature.href)).toMatchObject({mode,feature:feature.id});
    }
    expect(areaRoute('#/')).toMatchObject({page:'explore',mode:'explore',feature:'watch'});
    expect(areaRoute('#/analyze/spectrum')).toMatchObject({page:'explore',mode:'analyze',workspace:{tab:'spectrum'}});
    expect(areaRoute('#/research/export')).toMatchObject({page:'explore',mode:'research',workspace:{tab:'export'}});
  });
  it('keeps existing shared coordinates, candidate IDs, lessons and scientific links usable',()=>{
    const pairs=[
      ['#/sky/154.4122,23.0921,360,mol','#/explore/sky/154.4122,23.0921,360,mol'],
      ['#/candidates/kind=mover&grade=A','#/analyze/candidates/kind=mover&grade=A'],
      ['#/c/shape%2Fexample','#/analyze/c/shape%2Fexample'],
      ['#/learn/stories','#/explore/learn/stories'],
      ['#/tools/api','#/research/tools/api'],
      ['#/live/zone-2','#/research/live/zone-2'],
    ];
    for(const [oldLink,newLink] of pairs)expect(areaRoute(oldLink)).toEqual(areaRoute(newLink));
    expect(areaRoute('#/explore/analyze/spectrum').workspace).toEqual(areaRoute('#/analyze/spectrum').workspace);
    expect(areaRoute('#/explore/science').workspace).toEqual(areaRoute('#/research/processing').workspace);
    expect(areaRoute('#/results')).toMatchObject({mode:'research',feature:'results'});
    expect(areaRoute('#/review')).toMatchObject({mode:'analyze',feature:'review'});
  });
  it('preserves candidate filters when entering and returning through the Analyze area',()=>{
    const hash='#/analyze/candidates/kind=mover&grade=A&sort=fewest';
    expect(isDiscoverUrl(hash)).toBe(true);
    expect(isDiscoverUrl('#/analyze/candidates-extra')).toBe(false);
    expect(discoverQuery(hash,'?t=barnard')).toBe('kind=mover&grade=A&sort=fewest');
    expect(routeQuery(hash)).toBe('kind=mover&grade=A&sort=fewest');
    expect(areaRoute(hash).param).toBe('kind=mover&grade=A&sort=fewest');
  });
});
