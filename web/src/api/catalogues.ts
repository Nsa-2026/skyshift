// Catalogue answers for one field (contracts/api.md "GET /api/gaia" and "GET /api/enrichment"), asked once
// per page and shared: the Signals list checks every signal against Gaia, CatWISE and MPC orbits as soon as
// it loads, and the "Known objects" buttons (card/science.ts) show those same answers instead of asking
// again. The server caches complete answers too. A failed answer is never kept, so asking again retries;
// a synthetic example is a failure here (getReal), never a result.
import {ApiFailure, getReal} from './client';

export type CatalogueKind = 'gaia' | 'catwise' | 'asteroids' | 'relay' | 'comovers';
export type GaiaStar = {id:string;ra:number;dec:number;pmra:number|null;pmdec:number|null;parallax:number|null;g:number|null};
export type CatwiseSource = {name:string;ra:number|null;dec:number|null;w1mpro:number|null;w2mpro:number|null;pmra:number|null;pmdec:number|null};
export type KnownPrediction = {name:string;ra:number;dec:number;v:number|null;visit:number;mjd:number};
export type GaiaAnswer = {gaia:GaiaStar[]};
export type CatwiseAnswer = {source:string;sources:CatwiseSource[];note?:string};
export type AsteroidsAnswer = {source:string;objects:KnownPrediction[];errors:{visit:number;mjd:number;error:string}[];note?:string};

/** What each automatic check is called on the page. */
export const CATALOGUE_NAME: Record<'gaia'|'catwise'|'asteroids', string> = {
  gaia:'Gaia DR3', catwise:'CatWISE2020', asteroids:'MPC orbits',
};

/** The URL a check asks: the same for the Signals list and the buttons, so the answer is shared. */
export function catalogueUrl(kind: CatalogueKind, at: {ra:number;dec:number}): string {
  return kind === 'gaia' ? `/api/gaia?ra=${at.ra}&dec=${at.dec}&r=3` : `/api/enrichment?ra=${at.ra}&dec=${at.dec}&kind=${kind}`;
}

/** Longest wait for one answer; Firebase Hosting gives up on the API after 60 s anyway. */
export const CATALOGUE_TIMEOUT_MS = 75_000;
const asked = new Map<string, Promise<unknown>>();
const answered = new Set<string>();

function abortable<T>(p: Promise<T>, signal: AbortSignal): Promise<T> {
  if (signal.aborted) return Promise.reject(new DOMException('Aborted', 'AbortError'));
  return new Promise<T>((resolve, reject) => {
    const stop = () => reject(new DOMException('Aborted', 'AbortError'));
    signal.addEventListener('abort', stop, {once:true});
    p.then(v => {signal.removeEventListener('abort', stop); resolve(v)}, e => {signal.removeEventListener('abort', stop); reject(e)});
  });
}

/** One catalogue's answer for this field. Shared by every caller; `signal` only stops this caller
 *  waiting (another view may still want the answer). */
export function fetchCatalogue<T>(kind: CatalogueKind, at: {ra:number;dec:number}, signal?: AbortSignal): Promise<T> {
  const url = catalogueUrl(kind, at);
  let p = asked.get(url) as Promise<T> | undefined;
  if (!p) {
    const timeout = new AbortController();
    const timer = setTimeout(() => timeout.abort(), CATALOGUE_TIMEOUT_MS);
    p = getReal<T>(url, timeout.signal).then(
      value => {answered.add(url); return value},
      error => {
        if (asked.get(url) === p) asked.delete(url);
        throw timeout.signal.aborted ? new ApiFailure(`No answer within ${CATALOGUE_TIMEOUT_MS / 1000} s`, 504, 'upstream') : error;
      },
    ).finally(() => clearTimeout(timer));
    asked.set(url, p);
    p.catch(() => {});  // a failure is reported to each caller, not as an unhandled rejection
    if (asked.size > 60) asked.delete(asked.keys().next().value!);
  }
  return signal ? abortable(p, signal) : p;
}

/** Has this field's answer already arrived (so a button shows it at once)? */
export function catalogueAnswered(kind: CatalogueKind, at: {ra:number;dec:number}): boolean {
  const url = catalogueUrl(kind, at);
  return answered.has(url) && asked.has(url);
}

/** Tests only: forget every answer. */
export function forgetCatalogues(): void {asked.clear(); answered.clear()}

/** A failure in words for the page: the server's message (it names the service), else the reason. */
export function failureText(error: unknown): string {
  if (error instanceof ApiFailure) return error.message;
  if (error instanceof Error && error.message) return error.message === 'Failed to fetch' ? 'the network request failed' : error.message;
  return 'the request failed';
}
