import '../styles/tokens.css';
import '../styles.css';
import {mountBlink} from '../blink';
import {mountSky} from '../sky';
import {mountCard} from '../card';
import {renderSpectrum} from '../card/spectrum';
import {mountScore} from '../score';
import {mountVote} from '../vote';
import {mountScience} from '../card/science';
import {readView,writeView,featuredNear} from './state';
import type {View,SkyHandle} from './modules';
import type {ObjectResponse,FramesResponse,SpectrumPoint,Det,Clock} from '../api/types';
import {getTargets,resolve,loadFrames,loadObject,loadAnalysis,type Target,type ObjectData,type Analysis,type Candidate} from '../api/science-client';
const $=<T extends HTMLElement=HTMLElement>(id:string)=>document.getElementById(id) as T;
const descriptions:Record<string,string>={barnard:'追踪高速自行恒星',epsindib:'附近的褐矮星双星',wise0855:'在长波段寻找冷天体',rwaur:'观察年轻恒星的变化',rdra:'黄极附近的米拉变星','3i-atlas':'跟随星际彗星，观察恒星流过',elpis:'跟随小行星的已知轨道',euphrosyne:'在曝光序列中检查小行星'};
$('app').innerHTML=`<header><a class="brand" href="/">✦ <b>SkyShift</b><span>星空的两个时钟</span></a><span class="mission">NASA SPACE APPS 2026 <i> / </i> SPHEREx</span></header>
<main><section class="intro"><div><p class="eyebrow">THE SKY DOESN’T STAND STILL</p><h1>把星空的时间<br><em>拨快一点。</em></h1><p class="lead">在真实的红外观测之间闪烁，寻找移动与变化。<br>从五个已知目标开始，练习读懂星空留下的线索。</p></div><div class="orbit" aria-hidden="true"><div class="orbit-ring"></div><div class="orbit-dot"></div><span>01 <small>观测</small> ── 02 <small>比较</small> ── 03 <small>核查</small></span><b>两个时钟<br><strong>同一片星空</strong></b></div></section>
<section aria-labelledby="targets-title"><div class="section-label"><h2 id="targets-title">选择一处星空</h2><span>挑战目标 · 团队真实观测与分析缓存</span></div><div id="targets" class="targets"></div><form id="search"><label for="query">天体名称 / ICRS 坐标</label><input id="query" placeholder="例如 WISE 0855，或 133.7599 −7.2422" required><button type="submit">前往星空 ↗</button></form></section>
<div id="status" role="status" aria-live="polite">正在读取观测目录…</div><button id="start-analysis" hidden>为此坐标建立观测分析（首次可能需数分钟）</button>
<section id="workspace" hidden><div class="section-label result-title"><div><p class="eyebrow">OBSERVATION DESK</p><h2 id="target-name"></h2></div><span id="coords"></span></div>
<div class="desk"><section class="player panel"><div class="view-row"><label>数据图层 <select id="view"><option value="raw">原始观测（团队主线）</option><option value="resid">静态模型残差</option><option value="model">静态天空模型</option><option value="refdiff">DR1 参考差图</option></select></label></div><div id="blink-host"></div><p class="source" id="frame-source"></p></section><aside><div class="panel extra" id="object-card"></div><div class="panel facts"><p class="eyebrow">时间机分析缓存</p><div class="numbers"><div><b id="nframes"></b><span>分析曝光（全波段）</span></div><div><b id="nvisits"></b><span>巡天 visit</span></div></div><hr><h3>先看位置，再看颜色</h3><p>播放时只比较同一探测器。切换 D1–D6 看到的是不同波长，亮暗差异不能当成移动。</p><p class="notice" id="coverage-note">缓存覆盖约 4′，画面外沿留空；放大后的像素不会增加实际分辨率。</p><dl><dt>图像方向</dt><dd>北上 · 东左</dd><dt>采样</dt><dd id="sampling"></dd><dt>源像素</dt><dd>6.15″ / px</dd><dt>分析 FLAGS</dt><dd id="flags-status">完整 FLAGS 未保留</dd></dl></div><div class="panel tip"><span>✧</span><h3>一条线索，还不是一次发现</h3><p>重复出现、位移、颜色与数据质量需要一起核查。已知目标也可能出现在候选列表中。</p></div></aside></div>
<details class="panel extra" id="sky-panel"><summary>天区背景 · Aladin / NASA IRSA</summary><div id="sky-content"></div></details><div class="lower"><section class="panel spectrum"><div class="section-label"><h3>目标的红外光谱</h3><span>µJy · 对数纵轴</span></div><div id="spectrum" role="img" aria-label="目标红外光谱"></div><p class="source">数据：SPHEREx 缓存逐帧测光，mJy 已换算为 µJy。按探测器着色；未连线。白点为已载入的 DR1 参考，饱和核心测量为空心点。</p><p id="object-note" class="source"></p></section><section class="panel finding"><div class="section-label"><h3>筛选与误报检验</h3><span id="analysis-det"></span></div><div id="analysis-status" role="status"></div><div id="findings"></div><p class="source">p(false) 为随机背景中出现同等或更强候选的频率估计，含有限采样修正；不是天体为假的概率。冷天体颜色筛选没有单独标定的误报率。</p><details><summary>查看数据与方法的限制</summary><ul id="warnings"></ul></details></section></div>
<details class="panel extra" id="vote-panel"><summary>标记与投票 · 点击图像中的点</summary><div id="vote-content"></div></details><details class="panel extra" id="score-panel"><summary>验证面板 · 团队检查与待核查列表</summary><div id="score-content"></div></details><footer><span>NASA/JPL-Caltech/IPAC SPHEREx · ESA Gaia DR3 · MPC / IMCCE · Aladin Lite (CDS, GPL-3.0)</span><a id="export" download>下载帧索引 ↗</a></footer></section></main>`;
let targets:Target[]=[],target:Target,det=6,clock:Clock='months',fov=5,layer='raw';
let frames:FramesResponse|undefined,jsonUrl='',lastObject:ObjectData|undefined,reference:{um:number;flux_ujy:number}[]=[],epochMjd=0;
let controller=new AbortController(),generation=0,sky:SkyHandle|undefined;
const view=():View=>({ra:frames?.center.ra??target.ra,dec:frames?.center.dec??target.dec,clock,fov,det:det as Det,slug:target.slug||null,name:target.name});
const card=mountCard($('object-card'),{plots:false});
const votes=mountVote($('vote-content'));
const science=mountScience(points=>{reference=points;if(lastObject)showObject(lastObject)},()=>lastObject?.spectrum||[]);
const blink=mountBlink($('blink-host'),{
 onViewChange(change){if(change.clock)clock=change.clock;if(change.det)det=change.det;if(change.fov)fov=change.fov;load()},
 onPick(pick){votes.pick(pick);($('vote-panel') as HTMLDetailsElement).open=true},
 onEpoch(epoch){epochMjd=epoch.mjd_mid;sky?.epoch(epoch.mjd_mid)}
});
let score:{destroy():void}|undefined;
($('score-panel') as HTMLDetailsElement).ontoggle=()=>{if(($('score-panel') as HTMLDetailsElement).open&&!score)score=mountScore($('score-content'))};
($('sky-panel') as HTMLDetailsElement).ontoggle=()=>{
 if(!($('sky-panel') as HTMLDetailsElement).open)return;
 if(!sky)sky=mountSky($('sky-content'),{onClick(pos){const near=featuredNear(pos);target=targets.find(t=>t.slug===near?.slug)||{slug:'',name:'天区位置',ra:pos.ra,dec:pos.dec,default_det:det,n_frames:0,n_visits:0};layer='raw';$('view').querySelectorAll('option').forEach(o=>(o as HTMLOptionElement).selected=o.value==='raw');load()}});
 if(frames){sky.goto(view(),frames);sky.epoch(epochMjd)}
};
function showObject(object:ObjectData){
 const points=object.spectrum.filter(p=>p.flux_ujy!==null&&p.err_ujy!==null&&Number.isFinite(p.flux_ujy)&&Number.isFinite(p.err_ujy)) as SpectrumPoint[];
 $('spectrum').innerHTML=renderSpectrum(points,{width:Math.min(650,$('spectrum').clientWidth||650),mode:'det',id:'unified-spectrum',reference}).svg;
 $('object-note').textContent=object.measure?'正式位移与 Gaia 预测见天体详情卡。':'正式测量的限制见详情卡；时间机实验性拟合单独标明。';
 card.show(view(),{...object,spectrum:points} as ObjectResponse,frames||null);science.refreshObject();
}
function showAnalysis(a:Analysis){
 $('flags-status').closest('.facts')!.removeAttribute('hidden');$('nframes').textContent=String(a.source.n_frames);$('nvisits').textContent=String(a.source.n_visits);
 $('flags-status').textContent=a.source.flags_complete?'已按统一 FLAGS 掩膜处理':'完整 FLAGS 未保留';
 $('coverage-note').textContent=`分析数据覆盖约 ${a.source.coverage_arcmin}′；原始观测图使用团队主线数据，二者来源和质量状态分别保留。`;
 $('analysis-status').textContent='';$('analysis-det').textContent=`运动：D${a.detector} · ${a.n_slides} 次随机背景`;
 const persistent=a.cold_candidates.filter(c=>(c.n_visits||0)>=2);
 const rows=[...a.slow_movers.map(c=>({c,label:'跨 visit 移动线索',value:`${c.pm_arcsec_yr?.toFixed(2)}″/年`})),...a.fast_movers.map(c=>({c,label:'曝光间移动线索',value:`${c.speed_arcsec_day?.toFixed(2)}″/天`})),...persistent.map(c=>({c,label:'D6 亮 / D3 弱',value:`${c.n_visits} 个 visit 重复`}))];
 $('findings').replaceChildren();const summary=document.createElement('p');summary.className='finding-summary';summary.textContent=`${rows.length} 条待核查线索 · ${a.cold_candidates.length-persistent.length} 组单 visit 颜色信号未通过重复性门槛`;$('findings').append(summary);
 rows.slice(0,20).forEach(({c,label,value})=>{
  const el=document.createElement('div');el.className='finding-row';const title=document.createElement('strong');title.textContent=label;
  const text=document.createElement('p');text.textContent=`${value} · ${c.ra.toFixed(5)}, ${c.dec.toFixed(5)}`;
  const badge=document.createElement('span');badge.textContent=c.p_false===null?'误报率未标定':`p(false) = ${c.p_false.toFixed(3)}`;
  const locate=document.createElement('button');locate.textContent='在图上定位';locate.onclick=()=>highlight(c);
  el.append(title,text,badge,locate);$('findings').append(el);
 });
 $('warnings').replaceChildren();for(const warning of a.warnings){const li=document.createElement('li');li.textContent=warning;$('warnings').append(li)}
}
function highlight(candidate:Candidate){
 const dt=candidate.mjd_ref?(epochMjd-candidate.mjd_ref)/365.25:0;
 blink.highlight?.({ra:candidate.ra+(candidate.pmra_arcsec_yr||0)*dt/(3600*Math.cos(candidate.dec*Math.PI/180)),dec:candidate.dec+(candidate.pmdec_arcsec_yr||0)*dt/3600});
}
async function load(){
 controller.abort();controller=new AbortController();const signal=controller.signal,id=++generation;
 frames=undefined;lastObject=undefined;reference=[];$('workspace').hidden=true;$('start-analysis').hidden=true;
 $('flags-status').closest('.facts')!.setAttribute('hidden','');$('findings').replaceChildren();$('spectrum').replaceChildren();$('object-note').textContent='';$('analysis-det').textContent='';$('analysis-status').textContent='正在计算筛选结果…';
 $('status').textContent=`正在加载 ${target.name} · D${det}…`;$('status').className='loading';
 blink.status('loading');card.show(view(),null,null);science.selection(det,clock);
 document.querySelectorAll<HTMLButtonElement>('[data-slug]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.slug===target.slug)));
 try{
  const result=await loadFrames(target,det,clock,signal,layer,fov);if(id!==generation)return;
  frames=result.data as FramesResponse;jsonUrl=result.jsonUrl;
  if(!frames.epochs.some(e=>e.det===det))throw new Error('当前探测器暂无可用曝光');
  // The upstream player is the only owner of decoding, stretching, playback and overlays.
  await blink.show({...view(),slug:layer==='raw'?target.slug||null:null},frames,jsonUrl);if(id!==generation)return;
  $('target-name').textContent=target.name;$('coords').textContent=`ICRS ${frames.center.ra.toFixed(5)}°, ${frames.center.dec.toFixed(5)}°`;
  $('sampling').textContent=`${frames.pixel_arcsec}″ / px`;
  $('workspace').hidden=false;blink.status('idle');
  const count=frames.epochs.filter(e=>e.det===det).length;
  $('status').textContent=`✓ ${target.name} 已就绪 · ${count} 帧 · ${clock==='months'?'巡天叠加':'逐次曝光'}`;$('status').className='ready';
  $('frame-source').textContent=`NASA SPHEREx ${frames.data_release} · 归档查询 ${frames.archive_checked} · 图层 ${layer}。模型/残差与旧缓存的质量限制在分析区标明。`;
  ($('export') as HTMLAnchorElement).href=jsonUrl;
  writeView(view(),true);votes.view(view());sky?.goto(view(),frames);
  science.load(target,signal);
  loadObject(target,signal).then(o=>{if(id===generation){lastObject=o;showObject(o)}}).catch(e=>{if(!signal.aborted){card.status('error',e.message);$('object-note').textContent=e.message}});
  loadAnalysis(target,det,signal).then(a=>{if(id===generation)showAnalysis(a)}).catch(()=>{if(!signal.aborted)$('analysis-status').textContent='当前目标暂无时间机候选评分；已知目标预测位置可在图像中核查。'});
 }catch(error){
  if(signal.aborted)return;const message=(error as Error).message;blink.status('error',message);
  $('status').textContent=message;$('status').className='error';
  if(layer!=='raw'){layer='raw';($('view') as HTMLSelectElement).value='raw';await load();$('status').textContent=`${message}；已返回原始观测。`}
  else $('start-analysis').hidden=false;
 }
}
function choose(t:Target){target=t;det=t.default_det;clock=(t.clock||'months') as Clock;fov=t.fov||(clock==='months'?5:20);layer='raw';($('view') as HTMLSelectElement).value='raw';load()}
$('targets').onclick=e=>{const b=(e.target as HTMLElement).closest<HTMLButtonElement>('[data-slug]');if(b){const t=targets.find(t=>t.slug===b.dataset.slug);if(t)choose(t)}};
$('view').onchange=()=>{layer=($('view') as HTMLSelectElement).value;load()};
$('search').onsubmit=async e=>{
 e.preventDefault();controller.abort();controller=new AbortController();const signal=controller.signal,id=++generation;
 $('status').textContent='正在解析坐标…';
 try{const result=await resolve(($('query') as HTMLInputElement).value,signal);if(id!==generation)return;
  const near=result.slug?undefined:featuredNear(result);const featured=targets.find(t=>t.slug===(result.slug||near?.slug));
  choose(featured||{slug:'',name:result.name||'坐标位置',ra:result.ra,dec:result.dec,default_det:2,n_frames:0,n_visits:0});
 }catch(error){if(!signal.aborted){$('status').textContent=(error as Error).message;$('status').className='error'}}
};
$('start-analysis').onclick=async()=>{
 controller.abort();controller=new AbortController();const signal=controller.signal;$('start-analysis').hidden=true;$('workspace').hidden=true;
 try{const response=await fetch(`/api/jobs?ra=${target.ra}&dec=${target.dec}&size=${fov}`,{method:'POST',signal});const job=await response.json();if(!response.ok)throw new Error(job.error?.message||'无法开始分析');
  while(!signal.aborted){const r=await fetch(`/api/jobs/${job.id}`,{signal});const state=await r.json();if(!r.ok)throw new Error(state.error?.message||'任务状态读取失败');$('status').textContent=`${state.state} · ${state.progress.at(-1)||'等待观测查询'}`;
   if(state.state==='error')throw new Error(state.error);if(state.state==='done'){target={...target,slug:'',n_frames:0,n_visits:0};await load();return}await new Promise(ok=>setTimeout(ok,1500));
  }
 }catch(error){if(!signal.aborted){$('status').textContent=(error as Error).message;$('status').className='error';$('start-analysis').hidden=false}}
};
window.addEventListener('skyshift:goto',((e:CustomEvent)=>choose({slug:'',name:'候选位置',ra:e.detail.ra,dec:e.detail.dec,default_det:e.detail.det||2,clock:e.detail.clock||'hours',n_frames:0,n_visits:0})) as EventListener);
window.addEventListener('skyshift:gaia',((e:CustomEvent)=>{if(!frames||Math.abs(e.detail.ra-target.ra)>.00001||Math.abs(e.detail.dec-target.dec)>.00001)return;frames.gaia=e.detail.stars;blink.show(view(),frames,jsonUrl)}) as EventListener);
function applyShared(){const shared=readView();if(!shared)return false;target=targets.find(t=>t.slug===shared.slug)||{slug:'',name:shared.name||'坐标位置',ra:shared.ra,dec:shared.dec,default_det:shared.det,n_frames:0,n_visits:0};det=shared.det;clock=shared.clock;fov=shared.fov;layer='raw';load();return true}
window.addEventListener('popstate',()=>applyShared());
getTargets().then(list=>{
 targets=list;$('targets').innerHTML=list.map((t,i)=>`<button class="target" data-slug="${t.slug}" data-published="${t.published!==false}" aria-pressed="false"><span class="target-number">${String(i+1).padStart(2,'0')} <i>↗</i></span><strong>${t.name}</strong><small>${descriptions[t.slug]||''}</small><span class="target-count">${t.published===false?'按需联网加载':`${t.n_frames} 次曝光`}</span></button>`).join('');
 if(!applyShared())choose(list.find(t=>t.slug==='wise0855')||list[0]);
}).catch(e=>{$('status').textContent=`无法读取观测目录：${e.message}`});
