import type {Analysis,Candidate} from '../api/science-client';
import {loadValidation} from '../api/client';
import type {Epoch} from '../api/types';
import {falseAlarm,quality,candidateRows,nextChecks,dateLabel,escapeHtml as esc} from './evidence';
export function renderFindings(host:HTMLElement,a:Analysis,locate:(c:Candidate)=>void){
  const rows=candidateRows(a),excluded=a.cold_candidates.length-rows.filter(r=>r.kind==='cold').length;
  host.innerHTML=`<div class="search-summary"><strong>${rows.length?`${rows.length} 条待核查线索`:'本次没有通过筛选的移动 / 冷源线索'}</strong><p>分析缓存检查 ${a.source.n_frames} 次曝光（全波段），覆盖 ${a.source.n_visits} 次巡天、约 ${a.source.coverage_arcmin}′ 天区。运动筛选使用 D${a.detector}。</p><p>${excluded} 组颜色信号未通过至少两次巡天的重复性门槛。${rows.length?'已知天体仍可能留在列表中。':'这不代表天空没有目标：过暗、移动太快、饱和或覆盖不足都可能漏检；变星请看同波段光变。'}</p><p>目录排除：尚未完成逐候选的 Gaia / JPL / CatWISE 认证。</p></div><label class="finding-filter">显示 <select aria-label="候选筛选"><option value="all">全部线索 (${rows.length})</option><option value="slow">跨巡天移动</option><option value="fast">曝光间移动</option><option value="cold">冷天体颜色</option><option value="good">背景误报 &lt; 2%</option></select></label><div class="finding-list"></div>`;
  const list=host.querySelector<HTMLElement>('.finding-list')!,select=host.querySelector<HTMLSelectElement>('select')!;
  const render=()=>{const filtered=rows.filter(r=>select.value==='all'||select.value===r.kind||(select.value==='good'&&falseAlarm(r.c.p_false).tone==='good'));list.replaceChildren();if(!filtered.length){const empty=document.createElement('p');empty.textContent='当前条件下没有线索。可调整筛选，或检查下方灵敏度和光变。';list.append(empty);return}
    for(const {c,kind,label,value} of filtered){const state=falseAlarm(c.p_false),row=document.createElement('article');row.className='candidate';row.innerHTML=`<div class="candidate-heading"><h4>${label}</h4><span class="evidence ${state.tone}">${esc(state.label)}</span></div><p>${esc(value)} <span class="expert-only">· ICRS ${c.ra.toFixed(5)}°, ${c.dec.toFixed(5)}°</span></p><p>${esc(state.explanation)}</p><details><summary>下一步检查</summary><ol>${nextChecks(c,kind,Boolean(a.source.flags_complete)).map(s=>`<li>${esc(s)}</li>`).join('')}</ol></details><p class="expert-only source">状态：${esc(c.status)} · 背景试验 ${c.n_slides??(kind==='cold'?'未标定':a.n_slides)} 次 · p = ${c.p_false??'null'}</p><button type="button">在图上定位</button>`;row.querySelector('button')!.onclick=()=>locate(c);list.append(row)}
  };select.onchange=render;render();
}
export function renderFrameQuality(host:HTMLElement,epoch:Epoch|undefined){
  if(!epoch){host.textContent='等待当前帧质量信息';return}const q=quality(epoch);
  host.innerHTML=`<span class="evidence ${q.tone}">${q.label}</span><span>${dateLabel(epoch.mjd_mid)} · ${q.detail}</span>`;
}
export async function renderCalibration(host:HTMLElement){
  const data=await loadValidation();if(!data){host.textContent='校准结果暂不可用，不能声称阈值已验证。';return}
  host.innerHTML=`<p>依据团队已发布的实测验证。绿 &lt; 2%、黄 2–10%、红 &gt; 10% 是复核优先级；这些分界并不是用五目标拟合出的后验概率。</p><div class="calibration-grid">${data.checks.filter(c=>['V1','V2','V3','V4','V5'].includes(c.id)).map(c=>`<article><span class="evidence ${c.status==='pass'?'good':c.status==='fail'?'risk':'caution'}">${esc(c.id)} · ${{pass:'通过',partial:'部分完成',fail:'未通过',pending:'待验证'}[c.status]}</span><h4>${esc(c.claim)}</h4><p>${esc(c.headline)}</p><details><summary>检验方法与原始结果</summary><p>${esc(c.note||c.expected)}</p><pre>${esc(JSON.stringify(c.rows.slice(0,5),null,2))}</pre><p>显示前 5 行；完整结果见数据文件。</p></details></article>`).join('')}</div><p class="source">更新 ${esc(data.generated)} · ${esc(data.data_release)} · <a href="/data/validation.json" target="_blank" rel="noopener">完整验证 JSON ↗</a></p>`;
}
