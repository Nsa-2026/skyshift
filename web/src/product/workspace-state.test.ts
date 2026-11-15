import {describe,it,expect} from 'vitest';
import {workspaceState,workspaceHref,analysisPanels} from './workspace-state';
import {siteRoute} from '../app/navigation';
describe('workspace deep links',()=>{
  it('starts with Explore and accepts guided practice and the former science link',()=>{
    expect(workspaceState(null)).toEqual({mode:'explore',tab:''});
    expect(workspaceState('science')).toEqual({mode:'research',tab:'processing'});
    expect(workspaceState('play')).toEqual({mode:'explore',tab:'play'});
    expect(workspaceHref('explore','play')).toBe('#/learn/practice');
  });
  it('uses only sections that belong to the selected level',()=>{
    expect(workspaceState('analyze/spectrum')).toEqual({mode:'analyze',tab:'spectrum'});
    expect(workspaceState('analyze/sensitivity')).toEqual({mode:'analyze',tab:'motion'});
    expect(workspaceState('research/export')).toEqual({mode:'research',tab:'export'});
    expect(workspaceState('research/motion')).toEqual({mode:'research',tab:'data'});
  });
  it('provides direct links for every level',()=>{
    expect(workspaceHref('explore')).toBe('#/explore');
    expect(workspaceHref('analyze','lightcurve')).toBe('#/explore/lightcurve');
    expect(workspaceHref('analyze')).toBe('#/explore/motion');
    expect(workspaceHref('research','validation')).toBe('#/explore/validation');
  });
  it('restores multiple dashboards from a shareable Viewer link and filters duplicate or unrelated sections',()=>{
    const keys=analysisPanels('lightcurve+motion+spectrum+motion+processing+unknown');
    expect(keys).toEqual(['motion','spectrum','lightcurve']);
    const href=workspaceHref('analyze',keys.join('+'));
    expect(href).toBe('#/explore/motion+spectrum+lightcurve');
    expect(siteRoute(href).workspace).toEqual({mode:'analyze',tab:'motion',panels:keys});
    expect(workspaceState('analyze/spectrum+processing+unknown')).toEqual({mode:'analyze',tab:'spectrum',panels:['spectrum']});
  });
  it('keeps all dashboards closed on reload instead of reopening Motion',()=>{
    expect(siteRoute(workspaceHref('analyze','dashboards')).workspace).toEqual({mode:'analyze',tab:'',panels:[]});
  });
});
