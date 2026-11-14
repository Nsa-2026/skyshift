import {workspaceState, type WorkspaceMode, type WorkspaceState} from '../product/workspace-state';

export interface AreaFeature {id:string;label:string;href:string}
export const AREA_FEATURES:Record<WorkspaceMode,readonly AreaFeature[]> = {
  explore:[
    {id:'watch',label:'Watch',href:'#/explore'},
    {id:'sky',label:'Sky map',href:'#/explore/sky'},
    {id:'practice',label:'Practice',href:'#/explore/play'},
    {id:'learn',label:'Learn',href:'#/explore/learn'},
    {id:'community',label:'Community',href:'#/explore/community'},
  ],
  analyze:[
    {id:'measurements',label:'Measurements',href:'#/analyze'},
    {id:'candidates',label:'Candidates',href:'#/analyze/candidates'},
    {id:'review',label:'Review',href:'#/analyze/review'},
  ],
  research:[
    {id:'methods',label:'Data & methods',href:'#/research'},
    {id:'live',label:'Live search',href:'#/research/live'},
    {id:'results',label:'Results',href:'#/research/results'},
    {id:'tools',label:'Tools & API',href:'#/research/tools'},
  ],
};
export const AREA_LABELS:Record<WorkspaceMode,string>={explore:'Explore',analyze:'Analyze',research:'Research'};

export interface AreaRoute {
  page:string;
  param:string|null;
  mode:WorkspaceMode|null;
  feature:string;
  workspace?:WorkspaceState;
}

const PAGE_AREA:Record<string,[WorkspaceMode,string]>={
  atlas:['explore','sky'],learn:['explore','learn'],community:['explore','community'],about:['explore',''],
  discover:['analyze','candidates'],review:['analyze','review'],candidate:['analyze','review'],
  live:['research','live'],results:['research','results'],tools:['research','tools'],
};
function pageRoute(page:string,param:string|null):AreaRoute {
  const [mode,feature]=PAGE_AREA[page];return {page,param,mode:page==='atlas'?null:mode,feature};
}
function observationRoute(param:string|null):AreaRoute {
  const workspace=workspaceState(param);
  return {page:'explore',param,mode:workspace.mode,
    feature:workspace.mode==='explore'?(workspace.tab==='play'?'practice':'watch'):workspace.mode==='analyze'?'measurements':'methods',workspace};
}
function candidateId(value:string):string {try{return decodeURIComponent(value);}catch{return value;}}

/** Area-first links and existing shared URLs resolve to the same page controllers. */
export function areaRoute(hash:string,embed=false):AreaRoute {
  if(embed&&(!hash||hash==='#/'))return observationRoute(null);
  const [head,...parts]=hash.replace(/^#\/?/,'').split('/');
  const param=parts.join('/')||null;
  const [feature,...tail]=parts;
  const rest=tail.join('/')||null;
  if(head==='explore') {
    if(feature==='sky')return pageRoute('atlas',rest);
    if(feature==='learn')return pageRoute('learn',rest);
    if(feature==='community')return pageRoute('community',rest);
    return observationRoute(param);
  }
  if(head==='analyze') {
    if(feature==='candidates')return pageRoute('discover',rest);
    if(feature==='review')return pageRoute('review',rest);
    if(feature==='c'&&rest)return pageRoute('candidate',candidateId(rest));
    return observationRoute(`analyze${param?`/${param}`:''}`);
  }
  if(head==='research') {
    if(feature==='live')return pageRoute('live',rest);
    if(feature==='results')return pageRoute('results',rest);
    if(feature==='tools')return pageRoute('tools',rest);
    return observationRoute(`research${param?`/${param}`:''}`);
  }
  if(head==='c'&&param)return pageRoute('candidate',candidateId(param));
  if(head==='score'||head==='results')return pageRoute('results',param);
  if(head==='candidates'||head==='discover')return pageRoute('discover',param);
  if(head==='sky')return pageRoute('atlas',param);
  if(['review','community','about','learn','tools','live'].includes(head))return pageRoute(head,param);
  return pageRoute('atlas',null);
}
