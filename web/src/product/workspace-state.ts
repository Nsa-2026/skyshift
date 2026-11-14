export type WorkspaceMode = 'explore' | 'analyze' | 'research';
export const MODES = {
  explore: {title:'Explore the sky', description:'Watch the sky change. Start with what you can see.', tabs:[]},
  analyze: {title:'Analyze the evidence', description:'Check one line of evidence at a time.', tabs:[
    ['motion','Motion'],['spectrum','Spectrum'],['lightcurve','Light curve'],['compare','Compare bands'],['crossmatch','Cross-match'],['signals','Signals'],
  ]},
  research: {title:'Research the result', description:'Trace the data, inspect the processing and test the assumptions.', tabs:[
    ['data','Data'],['processing','Processing'],['validation','Validation'],['sensitivity','Sensitivity'],['export','Export'],
  ]},
} as const;
export interface WorkspaceState {mode:WorkspaceMode; tab:string}
export function workspaceState(param:string|null):WorkspaceState {
  const [head,tab] = (param??'').split('/');
  if(head === 'science') return {mode:'research',tab:'processing'};
  const mode:WorkspaceMode = head === 'analyze' || head === 'research' ? head : 'explore';
  const tabs = MODES[mode].tabs;
  return {mode,tab:tabs.find(t=>t[0] === tab)?.[0] ?? tabs[0]?.[0] ?? ''};
}
export function workspaceHref(mode:WorkspaceMode,tab='') {
  return `#/explore${mode === 'explore' ? '' : `/${mode}${tab ? `/${tab}` : ''}`}`;
}
