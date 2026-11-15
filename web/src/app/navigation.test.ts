import {describe,expect,it} from 'vitest';
import {SCREENS,siteRoute,viewerHref,viewerState} from './navigation';
import {communityHash,legacyHash} from './legacy-routes';

/** What the shell does: rewrite an old link first, then resolve it. */
const resolve=(hash:string,search='',advanced=false)=>{
  const legacy=legacyHash(hash);
  return siteRoute(legacy?.hash??hash,false,search,advanced||!!legacy?.advanced);
};

describe('top-level screens',()=>{
  it('has Sky, Viewer, To check, Community and Results, each resolving to its own screen',()=>{
    expect(SCREENS.map(s=>s.label)).toEqual(['Sky','Viewer','To check','Community','Results']);
    for(const s of SCREENS)expect(siteRoute(s.href).screen).toBe(s.id);
  });
  it('opens the Sky at home, and the Viewer for shared observations and the embed',()=>{
    expect(siteRoute('')).toMatchObject({page:'atlas',screen:'sky'});
    expect(siteRoute('#/')).toMatchObject({page:'atlas',screen:'sky'});
    expect(siteRoute('#/sky/154.4,23.1,360,mol')).toMatchObject({page:'atlas',param:'154.4,23.1,360,mol'});
    expect(siteRoute('',true)).toMatchObject({page:'explore',screen:'viewer'});
    expect(siteRoute('',false,'?t=barnard')).toMatchObject({page:'explore',screen:'viewer'});
    expect(siteRoute('#/',false,'?ra=10&dec=20')).toMatchObject({page:'explore',screen:'viewer'});
    expect(siteRoute('#/sky',false,'?t=barnard')).toMatchObject({page:'atlas'});
  });
  it('puts every page under its screen; Learn and About under none',()=>{
    expect(siteRoute('#/c/shape%2Fexample')).toMatchObject({page:'candidate',param:'shape/example',screen:'viewer'});
    expect(siteRoute('#/candidates/kind=mover')).toMatchObject({page:'discover',param:'kind=mover',screen:'check'});
    expect(siteRoute('#/review')).toMatchObject({page:'review',screen:'check'});
    expect(siteRoute('#/community')).toMatchObject({page:'community',screen:'community'});
    expect(siteRoute('#/results')).toMatchObject({page:'results',screen:'results'});
    expect(siteRoute('#/live')).toMatchObject({page:'live',screen:'results'});
    expect(siteRoute('#/tools/api')).toMatchObject({page:'tools',param:'api',screen:'results'});
    expect(siteRoute('#/learn/glossary')).toMatchObject({page:'learn',screen:null});
    expect(siteRoute('#/about')).toMatchObject({page:'about',screen:null});
  });
  it('opens Viewer sections, with the measurements when Advanced is on',()=>{
    expect(siteRoute('#/explore').workspace).toEqual({mode:'explore',tab:''});
    expect(siteRoute('#/explore',false,'',true).workspace).toEqual({mode:'analyze',tab:'motion'});
    expect(siteRoute('#/explore/spectrum').workspace).toEqual({mode:'analyze',tab:'spectrum'});
    expect(siteRoute('#/explore/processing').workspace).toEqual({mode:'research',tab:'processing'});
    expect(viewerState('nonsense')).toEqual({mode:'explore',tab:''});
    expect(viewerHref('compare')).toBe('#/explore/compare');
  });
  it('plays guided practice in Learn on the Viewer\'s player',()=>{
    expect(siteRoute('#/learn/practice')).toMatchObject({page:'explore',screen:null,workspace:{mode:'explore',tab:'play'}});
    expect(viewerHref('play')).toBe('#/learn/practice');
  });
});

describe('old links',()=>{
  it('leave the site\'s own hashes alone',()=>{
    for(const h of ['','#/','#/sky/1,2,3','#/explore','#/explore/spectrum','#/c/hunt-1','#/candidates/kind=mover','#/candidates/one=1','#/community','#/results','#/results/progress/hunt','#/results/tools/api','#/learn/stories'])
      expect(legacyHash(h)).toBeNull();
  });
  it('land Live search and Tools on their Results sections',()=>{
    expect(legacyHash('#/live')).toEqual({hash:'#/results/progress'});
    expect(legacyHash('#/live/hunt')).toEqual({hash:'#/results/progress/hunt'});
    expect(resolve('#/live/ecl22')).toMatchObject({page:'results',param:'progress/ecl22',screen:'results'});
    expect(legacyHash('#/tools')).toEqual({hash:'#/results/tools',advanced:true});
    expect(resolve('#/tools/batch')).toMatchObject({page:'results',param:'tools/batch',screen:'results'});
  });
  it('map the area-first Explore links',()=>{
    expect(resolve('#/explore/sky/154.4,23.1,360,mol')).toMatchObject({page:'atlas',param:'154.4,23.1,360,mol'});
    expect(resolve('#/explore/learn/stories')).toMatchObject({page:'learn',param:'stories'});
    expect(resolve('#/explore/play')).toMatchObject({page:'explore',workspace:{tab:'play'}});
    expect(legacyHash('#/explore/community')?.hash).toBe(communityHash());
    expect(resolve('#/explore/community')).toMatchObject({page:'community',screen:'community'});
  });
  it('map Analyze to the Viewer\'s sections with Advanced on, and keep filters and ids',()=>{
    expect(legacyHash('#/analyze')).toEqual({hash:'#/explore',advanced:true});
    expect(legacyHash('#/analyze/spectrum')).toEqual({hash:'#/explore/spectrum',advanced:true});
    expect(resolve('#/analyze/crossmatch')).toMatchObject({page:'explore',workspace:{mode:'analyze',tab:'crossmatch'}});
    expect(resolve('#/analyze/candidates/kind=mover&grade=A')).toMatchObject({page:'discover',param:'kind=mover&grade=A'});
    expect(resolve('#/analyze/c/shape%2Fexample')).toMatchObject({page:'candidate',param:'shape/example'});
    expect(legacyHash('#/analyze/review')).toEqual({hash:'#/candidates/one=1'});
    expect(resolve('#/review')).toMatchObject({page:'discover',param:'one=1',screen:'check'});
    expect(legacyHash('#/explore/analyze/lightcurve')).toEqual({hash:'#/explore/lightcurve',advanced:true});
    expect(legacyHash('#/explore/science')).toEqual({hash:'#/explore/processing',advanced:true});
  });
  it('map Research to Results with Advanced on',()=>{
    expect(resolve('#/research/live/zone-2')).toMatchObject({page:'results',param:'progress/zone-2',screen:'results'});
    expect(resolve('#/research/live')).toMatchObject({page:'results',param:'progress',screen:'results'});
    expect(resolve('#/research/results')).toMatchObject({page:'results'});
    expect(legacyHash('#/research/tools/api')).toEqual({hash:'#/results/tools/api',advanced:true});
    expect(legacyHash('#/research/export')).toEqual({hash:'#/explore/export',advanced:true});
    expect(legacyHash('#/research')).toEqual({hash:'#/explore/data',advanced:true});
  });
  it('keep the community page reachable and map the score page',()=>{
    expect(communityHash()).toBe('#/community');
    expect(resolve('#/community')).toMatchObject({page:'community',screen:'community'});
    expect(resolve('#/score')).toMatchObject({page:'results'});
  });
});
