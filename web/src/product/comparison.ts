import {loadFrames} from '../api/client';
import type {Target} from '../api/science-client';
import {bandPairs,observation,drawObservation,displayLimits} from './observations';
import {dateLabel,quality,escapeHtml as esc} from './evidence';
export function mountComparison(host:HTMLElement){
  host.innerHTML=`<summary>短波 / 长波并排核查 <span class="tag">D3 ↔ D6 · 冷天体筛选</span></summary><p>同一片天空、重叠的观测时段。D6 覆盖 4.4–5.0 µm，包含 4.6 µm；这不是精确的单色 4.6 µm 图像。</p><label>观测时段 <select class="compare-epoch" aria-label="比较观测时段"></select></label><p class="compare-status" role="status"></p><div class="band-grid"><figure><h4>D3 · 1.6–2.4 µm</h4><canvas aria-label="D3 短波观测"></canvas><figcaption></figcaption></figure><figure><h4>D6 · 4.4–5.0 µm</h4><canvas aria-label="D6 长波观测"></canvas><figcaption></figcaption></figure></div><p class="source">两边分别拉伸以查看微弱源，屏幕亮度不能直接当成通量比。定量颜色使用测光与同次曝光配对；跨波段差别不代表运动。</p>`;
  const status=host.querySelector<HTMLElement>('.compare-status')!,select=host.querySelector<HTMLSelectElement>('select')!;
  let current:Target|undefined,controller=new AbortController(),loaded=false,request=0;
  async function load(){
    if(!current||loaded)return;controller.abort();controller=new AbortController();const signal=controller.signal;
    status.textContent='正在读取 D3 与 D6 的真实观测…';select.disabled=true;
    try{
      const [short,long]=await Promise.all([3,6].map(det=>loadFrames({...current!,slug:current!.slug||null,det:det as 3|6,clock:'months',fov:5},signal)));
      if(signal.aborted)return;
      const pairs=bandPairs(short.frames.epochs.filter(e=>e.det===3),long.frames.epochs.filter(e=>e.det===6));
      if(!pairs.length)throw new Error('D3 / D6 没有重叠观测时段，不能并排作颜色判断。');
      select.innerHTML=pairs.map((p,i)=>`<option value="${i}">${dateLabel(p.short.mjd_mid)} / ${dateLabel(p.long.mjd_mid)}</option>`).join('');
      const show=async()=>{const id=++request;status.textContent='读取帧像素…';try{const p=pairs[Number(select.value)],obs=await Promise.all([observation(short,p.short,signal),observation(long,p.long,signal)]);if(signal.aborted||id!==request)return;host.querySelectorAll('figure').forEach((f,i)=>{drawObservation(f.querySelector('canvas')!,obs[i],displayLimits([obs[i]]));const q=quality(obs[i].epoch);f.querySelector('figcaption')!.innerHTML=`${dateLabel(obs[i].epoch.mjd_mid)} · ${obs[i].epoch.n_exposures} 次曝光 <span class="evidence ${q.tone}">${esc(q.label)}</span><br>${esc(q.detail)}`});status.textContent='已配对重叠观测时段 · 北上，东左';}catch(e){if(!signal.aborted&&id===request)status.textContent=(e as Error).message}};
      select.onchange=()=>void show();select.disabled=false;loaded=true;await show();
    }catch(e){if(!signal.aborted){status.textContent=`暂不能比较：${(e as Error).message}`;loaded=false;}}
  }
  host.addEventListener('toggle',()=>{if((host as HTMLDetailsElement).open)void load()});
  return {setTarget(t:Target){current=t;controller.abort();request++;loaded=false;select.replaceChildren();status.textContent='';host.querySelectorAll('canvas').forEach(c=>{c.width=0;c.height=0});host.querySelectorAll('figcaption').forEach(c=>c.replaceChildren());if((host as HTMLDetailsElement).open)void load()}};
}
