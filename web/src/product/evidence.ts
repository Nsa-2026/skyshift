import type {Analysis, Candidate, Spectrum} from '../api/science-client';
import type {Epoch, SkyPos} from '../api/types';

export type EvidenceTone = 'good' | 'caution' | 'risk' | 'unknown';
export function falseAlarm(p: number | null | undefined) {
  if (p == null || !Number.isFinite(p) || p < 0 || p > 1) return {tone:'unknown' as EvidenceTone, label:'False alarms uncalibrated', explanation:'There is not enough evidence to estimate false alarms. Check the original images and known-object catalogues first.'};
  const tone: EvidenceTone = p < .02 ? 'good' : p <= .1 ? 'caution' : 'risk';
  return {tone, label:`Background false alarms ${(100*p).toFixed(2)}%`, explanation: tone === 'good' ? 'Random backgrounds rarely produce a signal this strong. Further checks are worthwhile.' : tone === 'caution' ? 'A chance background alignment is still possible. More observations are needed.' : 'Similar signals often occur in the background. Check artefacts and incorrect matches first.'};
}
export function quality(e: Pick<Epoch,'flag_frac'|'sat_frac'>) {
  if (e.flag_frac == null || e.sat_frac == null || !Number.isFinite(e.flag_frac) || !Number.isFinite(e.sat_frac)) return {tone:'unknown' as EvidenceTone,label:'Incomplete quality information',detail:'Complete quality fractions were not retained for this frame, so its quality cannot be assumed to be good.'};
  const flagged=e.flag_frac*100,saturated=e.sat_frac*100;
  return {tone:(saturated>0 || flagged>10 ? 'caution':'good') as EvidenceTone,label:saturated>0?'Contains saturated pixels':flagged>10?'High masked fraction':'Quality information available',detail:`Masked ${flagged.toFixed(1)}% · saturated ${saturated.toFixed(2)}% (whole-frame fractions, not quality at the signal core)`};
}
export function candidateRows(a:Analysis) {
  return [...a.slow_movers.map(c=>({c,kind:'slow' as const,label:'Motion between visits',value:`${c.pm_arcsec_yr?.toFixed(2) ?? '—'}″ / year`})),...a.fast_movers.map(c=>({c,kind:'fast' as const,label:'Motion between exposures',value:`${c.speed_arcsec_day?.toFixed(2) ?? '—'}″ / day`})),...a.cold_candidates.filter(c=>(c.n_visits||0)>=2).map(c=>({c,kind:'cold' as const,label:'Bright at long wavelengths, faint at short wavelengths',value:`Repeated in ${c.n_visits} visits`}))];
}
export function nextChecks(c:Candidate, kind:string, flagsComplete:boolean) {
  return [!flagsComplete?'Check FLAGS: the legacy cache cannot establish whether bad pixels affect the signal core.':'Inspect the signal core, saturation, ghosts and residual shape in every frame.',kind==='cold'?'Compare D3 and D6 from the same exposure. Missing short-wavelength coverage is not a nondetection.':kind==='fast'?'Blink Hours across its exposures: a real mover steps along one line at a steady rate. The catalogue check cannot see asteroids fainter than V 21 or comets missing from the MPC list.':'Blink Months across the visits: a moving star steps the same way each visit while its neighbours stay put.',(c.p_false==null?'Calibrate false alarms independently before assessing confidence.':'Check whether the signal repeats at other times. A background false-alarm estimate does not confirm a discovery.')];
}
export function separation(a:SkyPos,b:SkyPos) {
  const rad=Math.PI/180,dra=((a.ra-b.ra+540)%360)-180;
  const h=Math.sin((a.dec-b.dec)*rad/2)**2+Math.cos(a.dec*rad)*Math.cos(b.dec*rad)*Math.sin(dra*rad/2)**2;
  return 2*Math.asin(Math.sqrt(Math.min(1,Math.max(0,h))))/rad*3600;
}
export function matchedCandidate(a:Analysis|undefined, p:SkyPos, radius:number) {
  return a ? candidateRows(a).map(r=>r.c).filter(c=>separation(c,p)<=radius).sort((a,b)=>separation(a,p)-separation(b,p))[0] : undefined;
}
export function spectralColour(points:Spectrum[]) {
  const median=(v:number[])=>{v.sort((a,b)=>a-b);return v.length?v[Math.floor(v.length/2)]:null};
  const valid=(d:number)=>points.filter(p=>p.det===d&&!p.saturated&&p.flux_ujy!=null&&p.err_ujy!=null&&p.err_ujy>0&&p.flux_ujy/p.err_ujy>=3).map(p=>p.flux_ujy!);
  const short=median(valid(3)),long=median(valid(6));
  if(short==null||long==null)return {value:'unknown',label:'Insufficient infrared colour evidence',detail:'Both bands need valid, unsaturated photometry with S/N ≥ 3. This does not establish visible colour.'};
  const ratio=long/short;
  return {value:ratio>1?'long':'short',label:ratio>1?'Brighter at long wavelengths':'Brighter at short wavelengths',detail:`D6 / D3 median flux ratio: ${ratio.toFixed(2)}. This is an infrared colour indicator, not a measurement of visible colour or temperature.`};
}
export function motionDirection(measure: {shift_arcsec:number;sigma_arcsec:number;pa_deg:number}|null|undefined) {
  if(!measure||measure.shift_arcsec<3*measure.sigma_arcsec)return 'No significant motion measured';
  return ['North','Northeast','East','Southeast','South','Southwest','West','Northwest'][Math.round(((measure.pa_deg%360)+360)%360/45)%8];
}
export function dateLabel(mjd:number){return new Date((mjd-40587)*86400000).toISOString().slice(0,10)}
export function escapeHtml(value:unknown){return String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]!))}
