// Catalogue checks of the Signals list (pure, no DOM). Every signal is matched at its own times and
// places against the field's Gaia DR3 stars, CatWISE2020 sources and MPC orbit predictions, by the rules
// contract v3.23 uses for candidates (contracts/api.md, candidates.json `status`, `checks`):
//   certain   a known body predicted within 10″ of every sighting, with its rate within 20% and its
//             direction within 15° when both have one → listed apart as "Known body: <name>", never deleted.
//             A slow mover that a Gaia or CatWISE star matches the same way (place at every visit, motion
//             within 20% and 15°) is that star's own motion → "Known star: <name>", listed apart too.
//   warn      a catalogue star within 6″, or a known body near it that doesn't fully match → "Can't rule out",
//             with the numbers and what to look at.
//   ok        nothing near it in a catalogue that answered → "Sign for".
// A catalogue that didn't answer is named as not checked; nothing is ever implied to have passed.
import type {Candidate} from '../api/science-client';
import type {CatwiseSource, GaiaStar, KnownPrediction} from '../api/catalogues';
import {dateLabel, separation} from './evidence';

export const GAIA_EPOCH_MJD = 57388.5;     // Gaia DR3 positions: J2016.0
export const CATWISE_EPOCH_MJD = 57170.0;  // CatWISE2020 positions: MJD 57170 (2015.4)
export const PIXEL_ARCSEC = 6.15;          // the analysis grid's native pixel
export const STAR_RADIUS = 6;              // ″: a catalogue star this close may be the signal
export const BODY_CERTAIN = 10;            // ″: a known body this close at every sighting (with its motion) is it
export const BODY_NEAR = 30;               // ″: a known body this close at any sighting can't be ruled out
export const RATE_TOLERANCE = 0.2;
export const PA_TOLERANCE = 15;            // degrees
/** Predictions are made per observing session (exposures under 10 minutes apart); a sighting belongs to the
 *  session whose prediction time is this close. */
const SESSION_DAYS = 0.02;
const HOURS_PER_YEAR = 365.25 * 24;

export type SignalKind = 'slow' | 'fast' | 'cold';
export type Sighting = {mjd:number;ra:number;dec:number};
/** Motion on the sky: rate in ″ per hour, direction as position angle (degrees east of north). */
export type Motion = {arcsec_h:number;pa_deg:number};
/** `still`: a source that stays put (a cold source). Its detections scatter by a pixel or more, so stars are
 *  matched at the mean of its sightings; known bodies still at each sighting's own time. */
export type Track = {sightings:Sighting[];motion:Motion|null;still?:boolean};
export type ScienceFrame = {mjd:number;det:number;visit:number};
export type Service = 'gaia' | 'catwise' | 'mpc';
export type Answer<T> = {state:'pending'} | {state:'failed';error:string} | {state:'ok';rows:T[];unchecked?:number[]};
export type Catalogues = {gaia:Answer<GaiaStar>;catwise:Answer<CatwiseSource>;mpc:Answer<KnownPrediction>};
export type Level = 'strong' | 'warn' | 'ok';
export type Check = {code:string;level:Level;text:string;look:string;values:Record<string,number|string>|null};
export type Verdict = {
  status:'open'|'known'; known_name:string|null; known_kind:'body'|'star'|null; checks:Check[];
  /** What was found, for the summary's counts. */
  found:{gaia:boolean;catwise:boolean;body:boolean;clear:boolean};
  pending:Service[]; failed:Service[];
  /** No times for its sightings: nothing could be checked. */
  untimed:boolean;
};

export const SERVICE_NAME: Record<Service, string> = {gaia:'Gaia DR3', catwise:'CatWISE2020', mpc:'MPC orbits'};
export const SERVICES: readonly Service[] = ['gaia', 'catwise', 'mpc'];
const LEVEL_RANK: Record<Level, number> = {strong:0, warn:1, ok:2};

// ---- geometry ---------------------------------------------------------------------------------------

const rad = Math.PI / 180;
const paOf = (east: number, north: number) => ((Math.atan2(east, north) / rad) % 360 + 360) % 360;
const angleBetween = (a: number, b: number) => {const d = Math.abs(((a - b) % 360 + 360) % 360); return Math.min(d, 360 - d)};
/** Move a position by east/north offsets in arcsec. */
function offset(p: {ra:number;dec:number}, east: number, north: number) {
  return {ra:p.ra + east / 3600 / Math.cos(p.dec * rad), dec:p.dec + north / 3600};
}
function motionBetween(a: Sighting, b: Sighting): Motion | null {
  const hours = (b.mjd - a.mjd) * 24;
  if (!(Math.abs(hours) > 0.01)) return null;
  const east = (((b.ra - a.ra + 540) % 360) - 180) * 3600 * Math.cos(((a.dec + b.dec) / 2) * rad), north = (b.dec - a.dec) * 3600;
  return {arcsec_h:Math.hypot(east, north) / Math.abs(hours), pa_deg:paOf(east * Math.sign(hours), north * Math.sign(hours))};
}
/** Same motion by the contract's rule: rate within 20% of the catalogue's, direction within 15°. */
export function sameMotion(ours: Motion, theirs: Motion) {
  return Math.abs(ours.arcsec_h - theirs.arcsec_h) <= RATE_TOLERANCE * theirs.arcsec_h && angleBetween(ours.pa_deg, theirs.pa_deg) <= PA_TOLERANCE;
}
const fromYear = (pmra: number, pmdec: number): Motion => ({arcsec_h:Math.hypot(pmra, pmdec) / HOURS_PER_YEAR, pa_deg:paOf(pmra, pmdec)});

/** Where and when a signal was seen, on the sky. Frames (the science product's `frames`) give the times;
 *  without them a mover falls back to its fitted position at its reference time, and a cold source has none. */
export function signalTrack(kind: SignalKind, c: Candidate, frames: ScienceFrame[] | null, detector: number): Track | null {
  const at = (x0: number, y0: number) => (x: number, y: number) => offset(c, -(x - x0) * PIXEL_ARCSEC, (y - y0) * PIXEL_ARCSEC);
  if (kind === 'fast') {
    const motion = num(c.vx) && num(c.vy) ? {arcsec_h:Math.hypot(c.vx, c.vy) * PIXEL_ARCSEC / 24, pa_deg:paOf(-c.vx, c.vy)} : null;
    if (frames && c.detections?.length && num(c.x0) && num(c.y0)) {
      const sky = at(c.x0, c.y0);
      const seen = c.detections.map(d => frames[d.t] ? {mjd:frames[d.t].mjd, ...sky(d.x, d.y)} : null);
      if (seen.every(Boolean)) return {sightings:seen as Sighting[], motion};
    }
    return num(c.t0) ? {sightings:[{mjd:c.t0, ra:c.ra, dec:c.dec}], motion} : null;
  }
  if (kind === 'slow') {
    const motion = num(c.pmra_arcsec_yr) && num(c.pmdec_arcsec_yr) ? fromYear(c.pmra_arcsec_yr, c.pmdec_arcsec_yr) : null;
    if (frames && c.points?.length && num(c.x) && num(c.y)) {
      const sky = at(c.x, c.y);
      // A visit's time: the middle of its first and last exposure in this detector (the analysis's own rule).
      const mid = (visit: number) => {const t = frames.filter(f => f.det === detector && f.visit === visit).map(f => f.mjd); return t.length ? (Math.min(...t) + Math.max(...t)) / 2 : null};
      const seen = c.points.map(p => {const t = mid(p.visit); return t === null ? null : {mjd:t, ...sky(p.x, p.y)}});
      if (seen.every(Boolean)) return {sightings:seen as Sighting[], motion};
    }
    return num(c.mjd_ref) ? {sightings:[{mjd:c.mjd_ref, ra:c.ra, dec:c.dec}], motion} : null;
  }
  if (frames && c.detections?.length && num(c.x) && num(c.y)) {
    const sky = at(c.x, c.y);
    const seen = c.detections.map(d => frames[d.t] ? {mjd:frames[d.t].mjd, ...sky(d.x, d.y)} : null);
    if (seen.every(Boolean)) return {sightings:seen as Sighting[], motion:null, still:true};
  }
  return null;
}
function num(v: unknown): v is number {return typeof v === 'number' && Number.isFinite(v)}

/** A still source's one place: the mean of its sightings, at their mean time. */
function meanPlace(t: Track): Track {
  if (!t.still || t.sightings.length < 2) return t;
  const m = (f: (s: Sighting) => number) => t.sightings.reduce((sum, s) => sum + f(s), 0) / t.sightings.length;
  const ra0 = t.sightings[0].ra;
  const ra = (ra0 + m(s => ((s.ra - ra0 + 540) % 360) - 180) + 360) % 360;
  return {sightings:[{mjd:m(s => s.mjd), ra, dec:m(s => s.dec)}], motion:null, still:true};
}

export function gaiaAt(s: GaiaStar, mjd: number) {
  const years = (mjd - GAIA_EPOCH_MJD) / 365.25;
  return offset(s, (s.pmra ?? 0) * years / 1000, (s.pmdec ?? 0) * years / 1000);
}
export function catwiseAt(s: CatwiseSource, mjd: number) {
  const years = (mjd - CATWISE_EPOCH_MJD) / 365.25;
  return offset({ra:s.ra!, dec:s.dec!}, (s.pmra ?? 0) * years, (s.pmdec ?? 0) * years);
}

/** A known body's place at a time: its prediction for that session, moved within the session by its own
 *  motion (from its neighbouring prediction in the same visit), or null when it wasn't predicted near the
 *  field then. */
function bodyAt(preds: KnownPrediction[], mjd: number): {pos:Sighting;motion:Motion|null;v:number|null} | null {
  let best: KnownPrediction | undefined;
  for (const p of preds) if (Math.abs(p.mjd - mjd) <= SESSION_DAYS && (!best || Math.abs(p.mjd - mjd) < Math.abs(best.mjd - mjd))) best = p;
  if (!best) return null;
  const b = best;
  const next = preds.filter(q => q !== b && q.visit === b.visit && Math.abs(q.mjd - b.mjd) > 0.001 && Math.abs(q.mjd - b.mjd) <= 3)
    .sort((x, y) => Math.abs(x.mjd - b.mjd) - Math.abs(y.mjd - b.mjd))[0];
  const motion = next ? motionBetween(b, next) : null;
  const hours = (mjd - b.mjd) * 24;
  const pos = motion ? {mjd, ...offset(b, motion.arcsec_h * hours * Math.sin(motion.pa_deg * rad), motion.arcsec_h * hours * Math.cos(motion.pa_deg * rad))} : {mjd, ra:b.ra, dec:b.dec};
  return {pos, motion, v:b.v};
}

// ---- words ------------------------------------------------------------------------------------------

const arcsec = (v: number) => `${v < 10 ? v.toFixed(1) : Math.round(v)}″`;
/** Two rates in one unit, chosen by the larger: ″/h, ″/day or ″/yr. */
function rates(a: number, b: number): [string, string] {
  const big = Math.max(a, b);
  const [k, unit, digits] = big >= 1 ? [1, '″/h', 1] : big * 24 >= 1 ? [24, '″/day', 1] : [HOURS_PER_YEAR, '″/yr', 2];
  return [`${(a * k).toFixed(digits)}${unit}`, `${(b * k).toFixed(digits)}${unit}`];
}
const pa = (m: Motion) => `PA ${Math.round(m.pa_deg)}°`;
const mag = (v: number | null | undefined, band: string) => num(v) ? `${band} ${v.toFixed(1)}` : '';
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
function motionVs(theirs: Motion, ours: Motion | null) {
  if (!ours) return `moving ${rates(theirs.arcsec_h, theirs.arcsec_h)[0]} toward ${pa(theirs)}`;
  const [t, o] = rates(theirs.arcsec_h, ours.arcsec_h);
  return `moving ${t} toward ${pa(theirs)} (ours ${o} toward ${pa(ours)})`;
}
function seenWhen(t: Track, k: number) {
  const n = t.sightings.length;
  return n === 1 ? '' : k === n ? ` at all ${n} sightings` : ` at ${k} of its ${n} sightings`;
}
/** ", within 6″ at 2 of its 6 sightings" (nothing for a single sighting). */
function within(t: Track, k: number, radius: number) {
  return t.sightings.length === 1 ? '' : `, within ${radius}″${seenWhen(t, k)}`;
}
function lookFor(kind: SignalKind, det: number, what: 'star'|'body'|'catwise', mjd: number, name = '') {
  const day = dateLabel(mjd);
  if (what === 'body') return `Blink Hours on D${det} around ${day}: see whether the dot follows ${name || 'the known body'}'s predicted path.`;
  if (kind === 'cold') return what === 'star'
    ? 'On Months, compare D3 with D6 here: if the star shows in D3 too, the long-wavelength light is probably the star\'s.'
    : 'On Months at D6, check that the signal is centred on this WISE source: if it is, it is already catalogued.';
  if (kind === 'fast') return `Blink Hours on D${det} around ${day}: see whether the dot sits still on the ${what === 'star' ? 'star' : 'WISE source'} there or moves past it.`;
  return `Blink Months on D${det}: see whether the shift belongs to this ${what === 'star' ? 'star' : 'source'} or comes from a neighbour blended with it.`;
}

// ---- one signal ---------------------------------------------------------------------------------------

type StarHit = {name:string;seps:number[];k:number;nearest:number;nearestAt:number;detail:string;motion:Motion|null};

function starHits(t: Track, place: (mjd: number) => (Sighting & {name:string;detail:string;motion:Motion|null})[]): StarHit[] {
  const byName = new Map<string, StarHit>();
  t.sightings.forEach((s, i) => {
    for (const star of place(s.mjd)) {
      const sep = separation(star, s);
      let hit = byName.get(star.name);
      if (!hit) {hit = {name:star.name, seps:t.sightings.map(() => Infinity), k:0, nearest:Infinity, nearestAt:s.mjd, detail:star.detail, motion:star.motion}; byName.set(star.name, hit)}
      hit.seps[i] = Math.min(hit.seps[i], sep);
    }
  });
  const hits = [...byName.values()];
  for (const h of hits) {
    h.k = h.seps.filter(v => v <= STAR_RADIUS).length;
    const i = h.seps.indexOf(Math.min(...h.seps));
    h.nearest = h.seps[i]; h.nearestAt = t.sightings[i].mjd;
  }
  return hits.filter(h => h.k > 0).sort((a, b) => b.k - a.k || a.nearest - b.nearest);
}

/** Check one signal against whatever catalogues have answered so far. */
export function vetSignal(kind: SignalKind, t: Track | null, cats: Catalogues, det: number): Verdict {
  const checks: Check[] = [];
  const found = {gaia:false, catwise:false, body:false, clear:false};
  const pending = (Object.keys(cats) as Service[]).filter(k => cats[k].state === 'pending');
  const failed = (Object.keys(cats) as Service[]).filter(k => cats[k].state === 'failed');
  let status: Verdict['status'] = 'open', known_name: string | null = null, known_kind: Verdict['known_kind'] = null;
  if (!t || !t.sightings.length) {
    checks.push({code:'catalogue_unchecked', level:'warn', text:'Not checked against catalogues: the times of its observations did not load, so catalogue positions could not be moved to them', look:'Reload the page to try again; until then a catalogued star or known body can\'t be ruled out.', values:null});
    return {status, known_name, known_kind, checks, found, pending:[], failed:[], untimed:true};
  }
  const n = t.sightings.length, first = t.sightings[0].mjd;
  const clean: Service[] = [];   // services that answered and found nothing near it

  // Known bodies (MPC orbits): every sighting at its own session's prediction.
  const mpc = cats.mpc;
  if (mpc.state === 'ok') {
    const missed = t.sightings.filter(s => (mpc.unchecked ?? []).some(m => Math.abs(m - s.mjd) <= SESSION_DAYS)).length;
    const names = new Map<string, KnownPrediction[]>();
    for (const p of mpc.rows) names.set(p.name, [...(names.get(p.name) ?? []), p]);
    type BodyHit = {name:string;seps:(number|null)[];k:number;nearest:number;nearestAt:number;motion:Motion|null;v:number|null;certain:boolean;placeAll:boolean};
    const hits: BodyHit[] = [];
    for (const [name, preds] of names) {
      const at = t.sightings.map(s => bodyAt(preds, s.mjd));
      const motion = at.find(b => b?.motion)?.motion ?? null, v = at.find(b => b)?.v ?? null;
      const seps = at.map((b, i) => b ? separation(b.pos, t.sightings[i]) : null);
      const valid = seps.filter((x): x is number => x !== null);
      if (!valid.length) continue;
      const nearest = Math.min(...valid);
      if (nearest > BODY_NEAR) continue;
      const placeAll = seps.every(x => x !== null && x <= BODY_CERTAIN);
      const certain = placeAll && (!motion || !t.motion || sameMotion(t.motion, motion));
      hits.push({name, seps, k:valid.filter(x => x <= BODY_NEAR).length, nearest, nearestAt:t.sightings[seps.indexOf(nearest)].mjd, motion, v, certain, placeAll});
    }
    hits.sort((a, b) => Number(b.certain) - Number(a.certain) || a.nearest - b.nearest);
    const sure = hits.find(h => h.certain);
    if (sure) {
      status = 'known'; known_name = sure.name; known_kind = 'body';
      const worst = Math.max(...sure.seps.map(x => x ?? 0));
      checks.push({code:'known_body', level:'strong',
        text:`MPC orbits put ${sure.name}${sure.v !== null ? ` (V ${sure.v.toFixed(1)})` : ''} within ${arcsec(worst)} of it${seenWhen(t, n)}${sure.motion ? `, ${motionVs(sure.motion, t.motion)}` : ''}`,
        look:lookFor(kind, det, 'body', first, sure.name),
        values:{sep_arcsec:+worst.toFixed(2), sightings:n, ...(sure.motion ? {rate_arcsec_h:+sure.motion.arcsec_h.toFixed(3), pa_deg:Math.round(sure.motion.pa_deg)} : {})}});
    }
    // Once certain, other bodies passing by add nothing to check.
    for (const h of sure ? [] : hits.slice(0, 2)) {
      found.body = true;
      const why = h.placeAll && h.motion && t.motion ? `, but ${motionVs(h.motion, t.motion)}` : h.motion ? `, ${motionVs(h.motion, t.motion)}` : '';
      checks.push({code:'known_nearby', level:'warn',
        text:`MPC orbits put ${h.name}${h.v !== null ? ` (V ${h.v.toFixed(1)})` : ''} ${arcsec(h.nearest)} from it on ${dateLabel(h.nearestAt)}${within(t, h.k, BODY_NEAR)}${why}`,
        look:lookFor(kind, det, 'body', h.nearestAt, h.name),
        values:{sep_arcsec:+h.nearest.toFixed(2), sightings:h.k, ...(h.motion ? {rate_arcsec_h:+h.motion.arcsec_h.toFixed(3), pa_deg:Math.round(h.motion.pa_deg)} : {})}});
    }
    if (missed) checks.push({code:'catalogue_unchecked', level:'warn', text:`MPC orbits were not checked at ${n === 1 ? 'its sighting' : `${missed} of its ${n} sightings`}: the known-object index doesn't reach ${n === 1 ? 'that time' : 'those times'}`, look:'', values:{sightings:missed}});
    if (!hits.length && !missed) clean.push('mpc');
  }

  // Catalogue stars: Gaia DR3 (moved from 2016.0) and CatWISE2020 (moved from 2015.4) to each sighting's time.
  const place = meanPlace(t), m = place.sightings.length;
  const stars = (service: 'gaia' | 'catwise') => {
    const answer = cats[service];
    if (answer.state !== 'ok') return;
    if (!answer.rows.length) {
      checks.push({code:'catalogue_empty', level:'warn', text:`${SERVICE_NAME[service]} lists no source anywhere in this field (it can leave gaps around very bright stars), so it says nothing about this signal`, look:'', values:null});
      return;
    }
    const hits = service === 'gaia'
      ? starHits(place, mjd => (answer.rows as GaiaStar[]).map(s => ({mjd, ...gaiaAt(s, mjd), name:`Gaia DR3 ${s.id}`, detail:mag(s.g, 'G'),
          motion:num(s.pmra) && num(s.pmdec) ? fromYear(s.pmra / 1000, s.pmdec / 1000) : null})))
      : starHits(place, mjd => (answer.rows as CatwiseSource[]).filter(s => num(s.ra) && num(s.dec)).flatMap(s => {
          const detail = [mag(s.w1mpro, 'W1'), mag(s.w2mpro, 'W2')].filter(Boolean).join(', ');
          const motion = num(s.pmra) && num(s.pmdec) ? fromYear(s.pmra, s.pmdec) : null;
          // A faint source's catalogued motion can be noise: compare at its catalogue epoch too, keep the nearer.
          return [{mjd, ...catwiseAt(s, mjd), name:`CatWISE2020 ${s.name}`, detail, motion}, {mjd, ra:s.ra!, dec:s.dec!, name:`CatWISE2020 ${s.name}`, detail, motion}];
        }));
    if (!hits.length) {clean.push(service); return}
    const best = hits[0];
    const sameStar = t.motion && best.motion && best.k === m && sameMotion(t.motion, best.motion);
    // The other catalogue's entry for a star already named: say so on that line instead of a doubt.
    if (known_kind === 'star' && sameStar) {
      const line = checks.find(c => c.code === 'known_star');
      if (line) line.text += ` (${SERVICE_NAME[service]} lists it too, as ${best.name.replace(/^\S+ /, '')})`;
    }
    // Already certain: a catalogue neighbour adds nothing to check.
    if (status === 'known') return;
    // A slow mover on the same star at every visit, moving the same way: that star's own motion.
    if (kind === 'slow' && status === 'open' && sameStar && t.motion && best.motion) {
      status = 'known'; known_name = best.name; known_kind = 'star';
      checks.push({code:'known_star', level:'strong',
        text:`${best.name}${best.detail ? ` (${best.detail})` : ''} is within ${arcsec(Math.max(...best.seps))} of it${seenWhen(place, m)}, ${motionVs(best.motion, t.motion)}: this is that star's own catalogued motion`,
        look:`Blink Months on D${det}: the star should step the same way each visit.`,
        values:{sep_arcsec:+Math.max(...best.seps).toFixed(2), rate_arcsec_yr:+(best.motion.arcsec_h * HOURS_PER_YEAR).toFixed(3), pa_deg:Math.round(best.motion.pa_deg)}});
      return;
    }
    found[service] = true;
    const epoch = service === 'gaia' ? 'the star moved from 2016.0 with its catalogued motion' : 'catalogue epoch 2015.4, moved with its catalogued motion';
    const more = hits.length > 1 ? `; ${plural(hits.length - 1, 'more source')} within ${STAR_RADIUS}″` : '';
    const motion = kind === 'slow' && best.motion ? `, ${motionVs(best.motion, t.motion)}` : '';
    checks.push({code:'catalogue_star', level:'warn',
      text:`${best.name}${best.detail ? ` (${best.detail})` : ''} sits ${arcsec(best.nearest)} from ${place.still && place !== t ? 'its mean place' : 'it'}${m > 1 ? ` on ${dateLabel(best.nearestAt)}` : ''}${within(place, best.k, STAR_RADIUS)}${motion} (${epoch}${more})`,
      look:lookFor(kind, det, service === 'gaia' ? 'star' : 'catwise', best.nearestAt),
      values:{sep_arcsec:+best.nearest.toFixed(2), sightings:best.k, sources:hits.length}});
  };
  stars('gaia');
  stars('catwise');

  for (const service of failed) checks.push({code:'catalogue_unchecked', level:'warn',
    text:`Not checked against ${SERVICE_NAME[service]}: the service did not answer`,
    look:'Try again later; until then what that catalogue lists here can\'t be ruled out.', values:null});

  if (clean.length && status === 'open') {
    clean.sort((a, b) => SERVICES.indexOf(a as Service) - SERVICES.indexOf(b as Service));
    const names = clean.map(s => SERVICE_NAME[s as Service]);
    const list = names.length === 1 ? names[0] : `${names.slice(0, -1).join(', ')} and ${names.at(-1)}`;
    const parts: string[] = [];
    const starNames = clean.filter(s => s !== 'mpc').map(s => s === 'gaia' ? 'Gaia' : 'CatWISE');
    if (starNames.length) parts.push(`nothing in ${starNames.join(' or ')} within ${STAR_RADIUS}″`);
    if (clean.includes('mpc')) parts.push(`no known asteroid (V ≤ 21) or comet predicted within ${BODY_NEAR}″${n > 1 ? ' at its times' : ''}`);
    checks.push({code:'catalogues_checked', level:'ok', text:`${list} checked: ${parts.join(', and ')}`, look:'', values:null});
  }
  found.clear = status === 'open' && !found.gaia && !found.catwise && !found.body && !pending.length && !failed.length
    && clean.length === 3;
  const order = checks.map((c, i) => ({c, i})).sort((a, b) => LEVEL_RANK[a.c.level] - LEVEL_RANK[b.c.level] || a.i - b.i).map(x => x.c);
  return {status, known_name, known_kind, checks:order, found, pending, failed, untimed:false};
}

// ---- the list --------------------------------------------------------------------------------------

export type Category = 'body' | 'star' | 'near' | 'gaia' | 'catwise' | 'clear' | 'partial' | 'untimed';
/** Each signal once, by what explains it best (for the counts). */
export function category(v: Verdict): Category {
  if (v.untimed) return 'untimed';
  if (v.known_kind) return v.known_kind;
  return v.found.body ? 'near' : v.found.gaia ? 'gaia' : v.found.catwise ? 'catwise' : v.found.clear ? 'clear' : 'partial';
}

/** One sentence on what was checked and what turned up, for the top of the list. */
export function checkSummary(verdicts: Verdict[], cats: Catalogues, failures: Partial<Record<Service, string>> = {}): {text:string;done:boolean} {
  const services = Object.keys(cats) as Service[];
  const pending = services.filter(s => cats[s].state === 'pending');
  const answered = services.filter(s => cats[s].state === 'ok');
  const failed = services.filter(s => cats[s].state === 'failed');
  const names = (list: Service[]) => {const v = list.map(s => SERVICE_NAME[s]); return v.length <= 1 ? v.join('') : `${v.slice(0, -1).join(', ')} and ${v.at(-1)}`};
  // The server's message names the service and the reason; its generic advice is left out.
  const reason = (s: Service) => (failures[s] ?? '').replace(/\s*No catalogue results are shown; try again later\.?\s*$/i, '').replace(/[.\s]+$/, '');
  const failText = failed.map(s => `Not checked against ${SERVICE_NAME[s]}: ${reason(s) || 'the service did not answer'}.`).join(' ');
  if (pending.length) {
    const sofar = answered.length ? ` ${names(answered)} answered;` : '';
    return {text:`Checking catalogues…${sofar} still waiting for ${names(pending)}.${failText ? ` ${failText}` : ''}`, done:false};
  }
  if (!answered.length) return {text:`Catalogue checks could not run. ${failText}`, done:true};
  const n = (c: Category) => verdicts.filter(v => category(v) === c).length;
  const parts: string[] = [];
  const add = (count: number, one: string, many: string) => {
    if (!count) return;
    const noun = parts.length ? '' : count === 1 ? ' signal' : ' signals';
    parts.push(`${count}${noun} ${count === 1 ? one : many}`);
  };
  add(n('body'), 'is a known body', 'are known bodies');
  add(n('star'), 'is a known star', 'are known stars');
  add(n('near'), 'has a known asteroid or comet nearby', 'have a known asteroid or comet nearby');
  add(n('gaia'), 'sits on a Gaia star', 'sit on a Gaia star');
  add(n('catwise'), 'sits on a CatWISE source', 'sit on a CatWISE source');
  add(n('clear'), 'has nothing in any of them', 'have nothing in any of them');
  add(n('partial'), 'has nothing in the catalogues that answered', 'have nothing in the catalogues that answered');
  add(n('untimed'), 'could not be checked (its observation times did not load)', 'could not be checked (their observation times did not load)');
  const what = parts.length ? parts.join(', ') : 'there were no signals to check';
  return {text:`Checked against ${names(answered)}${answered.includes('mpc') ? ' (asteroids and comets)' : ''}: ${what}.${failText ? ` ${failText}` : ''}`, done:true};
}
