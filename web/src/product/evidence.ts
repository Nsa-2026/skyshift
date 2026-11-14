import type {Analysis, Candidate, Spectrum} from '../api/science-client';
import type {Epoch, SkyPos} from '../api/types';

export type EvidenceTone = 'good' | 'caution' | 'risk' | 'unknown';
export function falseAlarm(p: number | null | undefined) {
  if (p == null || !Number.isFinite(p) || p < 0 || p > 1) return {tone:'unknown' as EvidenceTone, label:'误报未标定', explanation:'当前证据不足以给出误报估计，请先核查原图与已知目录。'};
  const tone: EvidenceTone = p < .02 ? 'good' : p <= .1 ? 'caution' : 'risk';
  return {tone, label:`背景误报 ${(100*p).toFixed(2)}%`, explanation: tone === 'good' ? '随机背景很少产生这么强的线索，值得继续核查。' : tone === 'caution' ? '背景偶然重合仍有可能，需要更多观测支持。' : '背景也经常出现类似信号，优先排查伪影与错误匹配。'};
}
export function quality(e: Pick<Epoch,'flag_frac'|'sat_frac'>) {
  if (e.flag_frac == null || e.sat_frac == null || !Number.isFinite(e.flag_frac) || !Number.isFinite(e.sat_frac)) return {tone:'unknown' as EvidenceTone,label:'质量信息不完整',detail:'该帧未保留完整质量比例，不能视作质量良好。'};
  const flagged=e.flag_frac*100,saturated=e.sat_frac*100;
  return {tone:(saturated>0 || flagged>10 ? 'caution':'good') as EvidenceTone,label:saturated>0?'含饱和像素':flagged>10?'掩膜较多':'质量信息可用',detail:`掩膜 ${flagged.toFixed(1)}% · 饱和 ${saturated.toFixed(2)}%（整帧比例，非候选核心质量）`};
}
export function candidateRows(a:Analysis) {
  return [...a.slow_movers.map(c=>({c,kind:'slow' as const,label:'跨巡天移动',value:`${c.pm_arcsec_yr?.toFixed(2) ?? '—'}″ / 年`})),...a.fast_movers.map(c=>({c,kind:'fast' as const,label:'曝光间移动',value:`${c.speed_arcsec_day?.toFixed(2) ?? '—'}″ / 天`})),...a.cold_candidates.filter(c=>(c.n_visits||0)>=2).map(c=>({c,kind:'cold' as const,label:'长波亮 / 短波弱',value:`${c.n_visits} 次巡天重复`}))];
}
export function nextChecks(c:Candidate, kind:string, flagsComplete:boolean) {
  return [!flagsComplete?'补查 FLAGS：旧缓存不能判断候选核心是否受坏像素影响。':'逐帧检查候选核心、饱和、鬼影与残差形状。',kind==='cold'?'核对同次曝光的 D3 / D6；短波缺失覆盖不等于短波未检出。':kind==='fast'?'按曝光时刻与 JPL 小天体位置交叉核对。':'与传播到观测时刻的 Gaia / CatWISE 位置比较。',(c.p_false==null?'补充独立误报标定，再讨论可信度。':'检查更多时刻是否重复出现；背景误报估计不等于发现确认。')];
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
  if(short==null||long==null)return {value:'unknown',label:'红外颜色证据不足',detail:'两波段均需有效、未饱和且 S/N ≥ 3 的测光；不能据此推断肉眼颜色。'};
  const ratio=long/short;
  return {value:ratio>1?'long':'short',label:ratio>1?'长波更亮':'短波更亮',detail:`D6 / D3 中位通量比 ${ratio.toFixed(2)}。红外颜色指标，不是可见光颜色或温度测量。`};
}
export function motionDirection(measure: {shift_arcsec:number;sigma_arcsec:number;pa_deg:number}|null|undefined) {
  if(!measure||measure.shift_arcsec<3*measure.sigma_arcsec)return '未测得显著移动';
  return ['北行','东北行','东行','东南行','南行','西南行','西行','西北行'][Math.round(((measure.pa_deg%360)+360)%360/45)%8];
}
export function dateLabel(mjd:number){return new Date((mjd-40587)*86400000).toISOString().slice(0,10)}
export function escapeHtml(value:unknown){return String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]!))}
