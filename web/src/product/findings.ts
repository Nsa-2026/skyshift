import './signal-checks.css';
import type {Analysis,Candidate} from '../api/science-client';
import {loadScience} from '../api/science-client';
import {loadValidation} from '../api/client';
import {fetchCatalogue,failureText,type AsteroidsAnswer,type CatwiseAnswer,type GaiaAnswer} from '../api/catalogues';
import type {Epoch} from '../api/types';
import {falseAlarm,quality,candidateRows,nextChecks,dateLabel,escapeHtml as esc} from './evidence';
import {checkSummary,signalTrack,vetSignal,BODY_CERTAIN,BODY_NEAR,PA_TOLERANCE,RATE_TOLERANCE,SERVICES,SERVICE_NAME,STAR_RADIUS,
  type Catalogues,type Check,type Level,type ScienceFrame,type Service,type Track,type Verdict} from './vetting';

/** The words in front of each check, as on the To check list (PR #364): strong, warn, ok. */
export const LEVEL_LABEL: Record<Level,{label:string;hint:string}> = {
  strong:{label:'Sign against',hint:'Measured: a sign it is not new or not real. Nothing is hidden for it.'},
  warn:{label:'Can\'t rule out',hint:'Not ruled out by these checks: look for yourself.'},
  ok:{label:'Sign for',hint:'Measured: a sign it is new.'},
};
const MARK: Record<Level,string> = {
  strong:'<svg class="sgc-mark" viewBox="0 0 12 12" aria-hidden="true" focusable="false"><circle cx="6" cy="6" r="6"/><path d="M3 6h6"/></svg>',
  warn:'<svg class="sgc-mark" viewBox="0 0 12 12" aria-hidden="true" focusable="false"><circle cx="6" cy="6" r="5.25"/><path d="M6 3v3.6M6 8.6v.1"/></svg>',
  ok:'<svg class="sgc-mark" viewBox="0 0 12 12" aria-hidden="true" focusable="false"><circle cx="6" cy="6" r="6"/><path d="m3.4 6.2 1.8 1.8 3.4-3.6"/></svg>',
};
const sentence=(s:string)=>/[.!?…]$/.test(s)?s:`${s}.`;
export function checksHtml(checks:Check[]){
  if(!checks.length)return '';
  return `<ul class="sgc-list" aria-label="Catalogue checks">${checks.map(c=>`<li class="sgc is-${c.level}" data-code="${esc(c.code)}"><span class="sgc-level" title="${esc(LEVEL_LABEL[c.level].hint)}">${MARK[c.level]}${esc(LEVEL_LABEL[c.level].label)}<span class="visually-hidden">:</span></span> <span class="sgc-text">${esc(sentence(c.text))}</span>${c.look?`<span class="sgc-look"><span class="sgc-look-k">Look:</span> ${esc(sentence(c.look))}</span>`:''}</li>`).join('')}</ul>`;
}
export function statusHtml(v:Verdict){
  if(v.status!=='known'||!v.known_name)return '';
  const head=v.known_kind==='star'?'Known star':'Known body';
  const plain=v.known_kind==='star'?'Its motion is this catalogued star\'s own motion. Listed apart, not deleted.':'Predicted at its place every time it was seen, moving at its rate and in its direction. Listed apart, not deleted.';
  return `<p class="sgc-status"><b class="sgc-status-head">${head}: <span class="sgc-name">${esc(v.known_name)}</span></b> ${plain}</p>`;
}
const names=(list:Service[])=>{const v=list.map(s=>SERVICE_NAME[s]);return v.length<=1?v.join(''):`${v.slice(0,-1).join(', ')} and ${v.at(-1)}`};
const RULES=`<details class="wb-limits sgc-rules"><summary>What the catalogue checks compare</summary><ul>
<li>Each signal is matched at its own times and places: every exposure of a mover, every visit of a slow mover, the mean place of a cold source.</li>
<li>Gaia DR3 stars, moved from their 2016.0 positions with their proper motion to the signal's time; a star within ${STAR_RADIUS}″ can't be ruled out.</li>
<li>CatWISE2020 sources (positions of 2015.4), moved with their catalogued motion and also taken where they were then; within ${STAR_RADIUS}″ can't be ruled out.</li>
<li>Known asteroids and comets: Minor Planet Center orbits computed with kete for every observing session, seen from the Earth's centre, asteroids to V 21. Predicted within ${BODY_CERTAIN}″ at every sighting, with its rate within ${RATE_TOLERANCE*100}% and its direction within ${PA_TOLERANCE}° of ours, it is that body; within ${BODY_NEAR}″ it can't be ruled out.</li>
<li>A slow mover that sits on a Gaia or CatWISE star at every visit and moves the same way (rate within ${RATE_TOLERANCE*100}%, direction within ${PA_TOLERANCE}°) is that star.</li>
<li>Not covered: planets and their moons, asteroids fainter than V 21, and comets missing from the MPC comet list this service reads (306P/LINEAR is one; a fix is under way).</li>
</ul></details>`;

/** The Signals list, then catalogue checks of every signal (Gaia, CatWISE, MPC orbits), which run by
 *  themselves and fill in as each catalogue answers. Resolves when every check has answered or failed. */
export function renderFindings(host:HTMLElement,a:Analysis,locate:(c:Candidate)=>void,opts:{target?:{slug:string|null;ra:number;dec:number};signal?:AbortSignal}={}):Promise<void>{
  const rows=candidateRows(a),excluded=a.cold_candidates.length-rows.filter(r=>r.kind==='cold').length;
  const checking=Boolean(opts.target&&rows.length);
  // The analysis's own warning that no catalogue veto ran is answered by the checks below.
  const warnings=(a.warnings??[]).filter(w=>!(checking&&/vetoes have not been run/i.test(w)));
  const countText=(known:number)=>rows.length?`${rows.length-known} signal${rows.length-known===1?'':'s'} to check${known?` · ${known} explained by a catalogue`:''}`:'No moving or cold-source signals passed these checks';
  host.innerHTML=`<div class="search-summary"><strong class="finding-count">${countText(0)}</strong><p>The cached analysis checked ${a.source.n_frames} exposures across all bands, covering ${a.source.n_visits} visits and about ${a.source.coverage_arcmin}′ of sky. Motion checks use D${a.detector}.</p><p>${excluded} colour signal${excluded===1?'':'s'} did not meet the requirement to repeat in at least two visits. ${rows.length?'Each signal below says what the catalogues found at its place.':'This does not mean the field is empty: faint, fast-moving or saturated objects and limited coverage can cause missed detections. Check same-band light curves for variability.'}</p>${rows.length?`<p class="sgc-summary" role="status" aria-live="polite">${checking?`Checking catalogues… still waiting for ${names([...SERVICES])}.`:'Catalogue checks were not run for this list.'}</p>${checking?RULES:''}`:''}${warnings.length?`<details class="wb-limits"><summary>Limits of this analysis (${warnings.length})</summary><ul>${warnings.map(w=>`<li>${esc(w)}</li>`).join('')}</ul></details>`:''}</div><label class="finding-filter">Show <select aria-label="Signal filter"><option value="all">All signals (${rows.length})</option><option value="slow">Motion between visits</option><option value="fast">Motion between exposures</option><option value="cold">Cold-source colours</option><option value="good">Background false alarms &lt; 2%</option></select></label><div class="finding-list"><div class="finding-open"></div><p class="finding-empty" hidden>No signals match this filter. Adjust the filter or inspect the sensitivity and light curves below.</p><section class="finding-known" hidden><h4>Explained by a catalogue (<span class="finding-known-count">0</span>)</h4><p>Certain matches are listed apart and kept, never deleted: a known asteroid or comet predicted at every sighting with its rate and direction, or a catalogued star moving the same way.</p><div class="finding-known-list"></div></section></div>`;
  const $=<T extends HTMLElement=HTMLElement>(s:string)=>host.querySelector<T>(s)!;
  const open=$('.finding-open'),knownList=$('.finding-known-list'),select=$<HTMLSelectElement>('select');
  const cards=rows.map(({c,kind,label,value},i)=>{const state=falseAlarm(c.p_false),row=document.createElement('article');row.className='candidate';row.dataset.index=String(i);row.dataset.kind=kind;
    row.innerHTML=`<div class="candidate-heading"><h4>${label}</h4><span class="evidence ${state.tone}">${esc(state.label)}</span></div><div class="sgc-status-slot"></div><p>${esc(value)} <span class="expert-only">· ICRS ${c.ra.toFixed(5)}°, ${c.dec.toFixed(5)}°</span></p><p>${esc(state.explanation)}</p><div class="sgc-slot">${checking?'<p class="sgc-pending">Checking Gaia DR3, CatWISE2020 and MPC orbits…</p>':''}</div><details><summary>Next checks</summary><ol>${nextChecks(c,kind,Boolean(a.source.flags_complete)).map(s=>`<li>${esc(s)}</li>`).join('')}</ol></details><p class="expert-only source">Status: ${esc(c.status)} · Background trials ${c.n_slides??(kind==='cold'?'uncalibrated':a.n_slides)} · p = ${c.p_false??'null'}</p><button type="button">Locate on image</button>`;
    row.querySelector('button')!.onclick=()=>locate(c);open.append(row);return row});
  // The filter hides rows in both groups; the explained group shows only when one of its rows is shown.
  const filter=()=>{let shown=0;rows.forEach((r,i)=>{const on=select.value==='all'||select.value===r.kind||(select.value==='good'&&falseAlarm(r.c.p_false).tone==='good');cards[i].hidden=!on;if(on)shown++});$('.finding-empty').hidden=shown>0;$('.finding-known').hidden=!knownList.querySelector('.candidate:not([hidden])')};
  select.onchange=filter;filter();
  if(!checking)return Promise.resolve();

  // ---- the catalogue checks, all at once, filled in as each answers --------------------------------
  const target=opts.target!,signal=opts.signal;
  const cats:Catalogues={gaia:{state:'pending'},catwise:{state:'pending'},mpc:{state:'pending'}};
  const failures:Partial<Record<Service,string>>={};
  let tracks:(Track|null)[]|undefined;
  const update=()=>{
    if(signal?.aborted||!tracks)return;
    const verdicts=rows.map((r,i)=>vetSignal(r.kind,tracks![i],cats,a.detector));
    verdicts.forEach((v,i)=>{
      const card=cards[i],waiting=v.pending.length&&!v.untimed?`<p class="sgc-pending">Still checking ${names(v.pending)}…</p>`:'';
      card.querySelector('.sgc-slot')!.innerHTML=checksHtml(v.checks)+waiting;
      card.querySelector('.sgc-status-slot')!.innerHTML=statusHtml(v);
      card.dataset.catalogue=v.status;
      const home=v.status==='known'?knownList:open;
      if(card.parentElement!==home){const after=[...home.children].find(el=>Number((el as HTMLElement).dataset.index)>i);home.insertBefore(card,after??null)}
    });
    const known=verdicts.filter(v=>v.status==='known').length;
    $('.finding-known-count').textContent=String(known);filter();
    $('.finding-count').textContent=countText(known);
    $('.sgc-summary').textContent=checkSummary(verdicts,cats,failures).text;
    host.querySelectorAll('.candidate button').forEach(b=>b.classList.add('btn'));
  };
  const failed=(service:Service)=>(e:unknown)=>{if(signal?.aborted)return;const why=failureText(e);cats[service]={state:'failed',error:why};failures[service]=why};
  const list=<T,>(v:unknown,what:string):T[]=>{if(!Array.isArray(v))throw new Error(`the answer had no ${what} list`);return v as T[]};
  const times=loadScience<{frames?:ScienceFrame[]}>(target,signal??new AbortController().signal)
    .then(s=>Array.isArray(s.frames)?s.frames:null,()=>null)
    .then(frames=>{if(signal?.aborted)return;tracks=rows.map(r=>signalTrack(r.kind,r.c,frames,a.detector));update()});
  const checks=[
    fetchCatalogue<GaiaAnswer>('gaia',target,signal).then(d=>list<GaiaAnswer['gaia'][number]>(d.gaia,'star'))
      .then(rows=>{cats.gaia={state:'ok',rows}},failed('gaia')).then(update),
    fetchCatalogue<CatwiseAnswer>('catwise',target,signal).then(d=>list<CatwiseAnswer['sources'][number]>(d.sources,'source'))
      .then(rows=>{cats.catwise={state:'ok',rows}},failed('catwise')).then(update),
    fetchCatalogue<AsteroidsAnswer>('asteroids',target,signal)
      .then(d=>({rows:list<AsteroidsAnswer['objects'][number]>(d.objects,'prediction'),unchecked:Array.isArray(d.errors)?d.errors.map(e=>e.mjd):[]}))
      .then(({rows,unchecked})=>{cats.mpc={state:'ok',rows,unchecked}},failed('mpc')).then(update),
  ];
  // A malformed answer is a failure, never an empty catalogue.
  return Promise.all([times,...checks]).then(()=>{});
}
export function renderFrameQuality(host:HTMLElement,epoch:Epoch|undefined){
  if(!epoch){host.textContent='Waiting for frame quality information';return}const q=quality(epoch);
  host.innerHTML=`<span class="evidence ${q.tone}">${q.label}</span><span>${dateLabel(epoch.mjd_mid)} · ${q.detail}</span>`;
}
export async function renderCalibration(host:HTMLElement){
  const data=await loadValidation();if(!data){host.textContent='Calibration results are unavailable. The thresholds cannot be claimed as validated.';return}
  host.innerHTML=`<p>Based on the published validation measurements. Green &lt; 2%, amber 2–10% and red &gt; 10% indicate review priority for background false alarms in Signals; these thresholds are not posterior probabilities fitted to the five targets. Pending checks have not been run yet.</p><div class="calibration-grid">${data.checks.map(c=>`<article><span class="evidence ${c.status==='pass'?'good':c.status==='fail'?'risk':'caution'}">${esc(c.id)} · ${({pass:'Passed',partial:'Partial',fail:'Failed',pending:'Pending'} as Record<string,string>)[c.status]??esc(c.status)}</span><h4>${esc(c.claim)}</h4><p>${esc(c.headline)}</p><details><summary>Methods and original results</summary><p>${esc(c.note||c.expected)}</p><pre>${esc(JSON.stringify(c.rows.slice(0,5),null,2))}</pre><p>Showing the first 5 rows. The data file contains the complete results.</p></details></article>`).join('')}</div><p class="source">Updated ${esc(data.generated)} · ${esc(data.data_release)} · <a href="#/research/results">How we know it works</a> · <a href="/data/validation.json" target="_blank" rel="noopener">Full validation JSON ↗</a></p>`;
}
