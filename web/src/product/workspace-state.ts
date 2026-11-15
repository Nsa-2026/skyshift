export type WorkspaceMode = 'explore' | 'analyze' | 'research';
export const MODES = {
  explore: {title:'Explore the sky', description:'Watch the sky change. Start with what you can see.', tabs:[]},
  analyze: {title:'Analyze the evidence', description:'Open several dashboards to compare the evidence together.', tabs:[
    ['motion','Motion'],['spectrum','Spectrum'],['lightcurve','Light curve'],['compare','Compare bands'],['crossmatch','Cross-match'],['signals','Signals'],
  ]},
  research: {title:'Research the result', description:'Trace the data, inspect the processing and test the assumptions.', tabs:[
    ['data','Data'],['processing','Processing'],['validation','Validation'],['sensitivity','Sensitivity'],['export','Export'],
  ]},
} as const;
export interface WorkspaceState {mode:WorkspaceMode; tab:string; panels?:string[]}
/** Keep dashboard links ordered, unique and limited to evidence sections. */
export function analysisPanels(value:string):string[] {
  return MODES.analyze.tabs.map(([key])=>key).filter(key=>value.split('+').includes(key));
}
export function workspaceState(param:string|null):WorkspaceState {
  const [head,tab] = (param??'').split('/');
  if(head === 'play') return {mode:'explore',tab:'play'};
  if(head === 'science') return {mode:'research',tab:'processing'};
  const mode:WorkspaceMode = head === 'analyze' || head === 'research' ? head : 'explore';
  if(mode==='analyze'&&(tab==='dashboards'||tab?.includes('+'))){
    const panels=analysisPanels(tab);
    return {mode,tab:panels[0]??'',panels};
  }
  const tabs = MODES[mode].tabs;
  return {mode,tab:tabs.find(t=>t[0] === tab)?.[0] ?? tabs[0]?.[0] ?? ''};
}
/** The Viewer's hash for a section (four screens, 15 Nov): sections are #/explore/<tab>. */
export function workspaceHref(mode:WorkspaceMode,tab='') {
  if(mode === 'explore') return tab === 'play' ? '#/learn/practice' : '#/explore';
  if(mode === 'analyze'&&(tab==='dashboards'||tab.includes('+'))){
    return `#/explore/${analysisPanels(tab).join('+')||'dashboards'}`;
  }
  const tabs = MODES[mode].tabs;
  return `#/explore/${tabs.find(t=>t[0] === tab)?.[0] ?? tabs[0][0]}`;
}
