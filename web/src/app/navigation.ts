// Four screens and one switch (decided 15 Nov): Sky · Viewer · To check · Results, plus Advanced.
// Learn follows them in the header (and stays in the footer); About is in the footer only.
// Old links are rewritten first by ./legacy-routes.ts; this file resolves the site's own hashes
// to a page and its screen.
import {MODES, type WorkspaceState} from '../product/workspace-state';

export type Screen = 'sky' | 'viewer' | 'check' | 'results' | 'learn';
export interface ScreenLink {id:Screen; label:string; href:string}
export const SCREENS: readonly ScreenLink[] = [
  {id:'sky', label:'Sky', href:'#/sky'},
  {id:'viewer', label:'Viewer', href:'#/explore'},
  {id:'check', label:'To check', href:'#/candidates'},
  {id:'results', label:'Results', href:'#/results'},
  {id:'learn', label:'Learn', href:'#/learn'},
];

/** Which screen each page belongs to (its nav entry); About belongs to none. */
const PAGE_SCREEN: Record<string, Screen | null> = {
  atlas:'sky', explore:'viewer', candidate:'viewer',
  discover:'check', review:'check', community:'check',
  results:'results', live:'results', tools:'results',
  learn:'learn', about:null,
};

export interface SiteRoute {
  page: string;
  param: string | null;
  screen: Screen | null;
  /** The Viewer's evidence section (only for page "explore"). */
  workspace?: WorkspaceState;
}

const tabsOf = (mode: 'analyze' | 'research') => MODES[mode].tabs.map(([key]) => key as string);

/** The Viewer's section from its param ("spectrum", "processing", "play"). With no section,
 *  Advanced opens the measurements (motion first); plain shows the summary. */
export function viewerState(param: string | null, advanced = false): WorkspaceState {
  const section = (param ?? '').split('/')[0];
  if (section === 'play') return {mode:'explore', tab:'play'};
  if (tabsOf('analyze').includes(section)) return {mode:'analyze', tab:section};
  if (tabsOf('research').includes(section)) return {mode:'research', tab:section};
  return advanced ? {mode:'analyze', tab:'motion'} : {mode:'explore', tab:''};
}

/** The hash that opens a Viewer section: "#/explore" or "#/explore/spectrum". */
export function viewerHref(section = ''): string {
  if (section === 'play') return '#/learn/practice';
  return section ? `#/explore/${section}` : '#/explore';
}

function page(name: string, param: string | null): SiteRoute {
  return {page:name, param, screen:PAGE_SCREEN[name] ?? null};
}
function viewer(param: string | null, advanced: boolean): SiteRoute {
  const workspace = viewerState(param, advanced);
  // Guided practice plays on the Viewer's player but belongs to Learn.
  return {page:'explore', param, screen:workspace.tab === 'play' ? 'learn' : 'viewer', workspace};
}
function candidateId(value: string): string { try { return decodeURIComponent(value); } catch { return value; } }

/** Resolve one of the site's own hashes. No hash is the Sky (home), except that a shared
 *  observation (?t=slug, ?ra=&dec=) and the embed open the Viewer on it. */
export function siteRoute(hash: string, embed = false, search = '', advanced = false): SiteRoute {
  const h = hash.replace(/^#\/?/, '');
  if (!h) {
    const query = new URLSearchParams(search);
    return embed || query.has('t') || query.has('ra') ? viewer(null, advanced) : page('atlas', null);
  }
  const [head, ...parts] = h.split('/');
  const param = parts.join('/') || null;
  if (head === 'explore') return viewer(param, advanced);
  // Guided practice lives in Learn but plays on the Viewer's own player.
  if (head === 'learn' && parts[0] === 'practice') return viewer('play', advanced);
  if (head === 'c' && param) return page('candidate', candidateId(param));
  if (head === 'score' || head === 'results') return page('results', param);
  if (head === 'candidates' || head === 'discover') return page('discover', param);
  if (head === 'sky') return page('atlas', param);
  if (['review', 'community', 'about', 'learn', 'tools', 'live'].includes(head)) return page(head, param);
  return page('atlas', null);
}
