// Old links keep working (15 Nov, four screens). Links written by the area-first shell
// (#/explore/…, #/analyze/…, #/research/…) and by earlier pages map to the new place.
// `advanced` asks the shell to turn the Advanced switch on (the section needs it).
// Returns null when the hash is already one of ours.
import {PRESETS, candidatesHash, presetState} from '../discover/filters';

export interface LegacyRoute {hash: string; advanced?: boolean}

/** "To check" with the "Community says real" filter: the consensus list. */
export function communityHash(): string {
  const preset = PRESETS.find(p => p.id === 'community-real');
  return preset ? candidatesHash(presetState(preset)) : '#/candidates';
}

/** To check with "One at a time" on (discover's view key, not a filter). */
export const ONE_AT_A_TIME = '#/candidates/one=1';
const MEASUREMENTS = ['motion', 'spectrum', 'lightcurve', 'compare', 'crossmatch', 'signals'];
const METHODS = ['data', 'processing', 'validation', 'sensitivity', 'export'];

export function legacyHash(hash: string): LegacyRoute | null {
  const [head, feature = '', ...tail] = hash.replace(/^#\/?/, '').split('/');
  const rest = tail.join('/');
  const at = (route: string, more = rest) => `#/${route}${more ? `/${more}` : ''}`;
  if (head === 'explore') {
    if (feature === 'sky') return {hash:at('sky')};
    if (feature === 'learn') return {hash:at('learn')};
    if (feature === 'community') return {hash:communityHash()};
    if (feature === 'play') return {hash:'#/learn/practice'};
    if (feature === 'watch') return {hash:'#/explore'};
    // Older still: #/explore/analyze/<tab>, #/explore/science.
    if (feature === 'analyze') return {hash:MEASUREMENTS.includes(tail[0]) ? `#/explore/${tail[0]}` : '#/explore', advanced:true};
    if (feature === 'science') return {hash:'#/explore/processing', advanced:true};
    return null;
  }
  if (head === 'analyze') {
    if (feature === 'candidates') return {hash:at('candidates')};
    if (feature === 'review') return {hash:ONE_AT_A_TIME};
    if (feature === 'c' && rest) return {hash:`#/c/${rest}`};
    if (MEASUREMENTS.includes(feature)) return {hash:`#/explore/${feature}`, advanced:true};
    return {hash:'#/explore', advanced:true};
  }
  if (head === 'research') {
    if (feature === 'live') return {hash:at('live')};
    if (feature === 'results') return {hash:'#/results'};
    if (feature === 'tools') return {hash:at('tools'), advanced:true};
    if (METHODS.includes(feature)) return {hash:`#/explore/${feature}`, advanced:true};
    return {hash:'#/explore/data', advanced:true};
  }
  // The review queue is To check's "One at a time" view now.
  if (head === 'review') return {hash:ONE_AT_A_TIME};
  if (head === 'community') return {hash:communityHash()};
  if (head === 'score') return {hash:'#/results'};
  return null;
}
