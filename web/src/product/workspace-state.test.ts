import {describe,it,expect} from 'vitest';
import {workspaceState,workspaceHref} from './workspace-state';
describe('workspace deep links',()=>{
  it('starts with Explore and accepts guided practice and the former science link',()=>{
    expect(workspaceState(null)).toEqual({mode:'explore',tab:''});
    expect(workspaceState('science')).toEqual({mode:'research',tab:'processing'});
    expect(workspaceState('play')).toEqual({mode:'explore',tab:'play'});
    expect(workspaceHref('explore','play')).toBe('#/explore/play');
  });
  it('uses only sections that belong to the selected level',()=>{
    expect(workspaceState('analyze/spectrum')).toEqual({mode:'analyze',tab:'spectrum'});
    expect(workspaceState('analyze/sensitivity')).toEqual({mode:'analyze',tab:'motion'});
    expect(workspaceState('research/export')).toEqual({mode:'research',tab:'export'});
    expect(workspaceState('research/motion')).toEqual({mode:'research',tab:'data'});
  });
  it('provides direct links for every level',()=>{
    expect(workspaceHref('explore')).toBe('#/explore');
    expect(workspaceHref('analyze','lightcurve')).toBe('#/explore/analyze/lightcurve');
    expect(workspaceHref('research','validation')).toBe('#/explore/research/validation');
  });
});
