export interface Route { page: string; param: string | null }
/** Explicit routes always win over remembered entry preferences. */
export function entryRoute(hash: string, search: string, seen: boolean, reduced: boolean): Route {
  const [head, ...rest] = hash.replace(/^#\/?/, '').split('/');
  if (head === 'c' && rest.length) {
    try { return { page: 'candidate', param: decodeURIComponent(rest.join('/')) }; }
    catch { return { page: 'discover', param: null }; }
  }
  if (head === 'score' || head === 'results') return { page: 'results', param: null };
  if (head === 'candidates' || head === 'discover') return { page: 'discover', param: rest.join('/') || null };
  if (head === 'sky') return { page: 'atlas', param: rest.join('/') || null };
  if (['review', 'explore', 'community', 'about', 'learn', 'play', 'tools', 'live'].includes(head))
    return { page: head, param: rest.join('/') || null };
  const query = new URLSearchParams(search);
  const deep = ['target', 't', 'ra', 'dec'].some(key => query.has(key)) || query.get('embed') === '1';
  return { page: deep || seen || reduced ? 'explore' : 'intro', param: null };
}
