import {loadFrames} from '../api/client';
import type {Target} from '../api/science-client';
import {bandPairs,observation,drawObservation,displayLimits} from './observations';
import {dateLabel,quality,escapeHtml as esc} from './evidence';
export function mountComparison(host:HTMLElement){
  host.innerHTML=`<summary>Short- and long-wavelength comparison <span class="tag">D3 ↔ D6 · Cold-source checks</span></summary><p>The same field during overlapping observation intervals. D6 spans 4.4–5.0 µm, including 4.6 µm; this is not a monochromatic 4.6 µm image.</p><label>Observation interval <select class="compare-epoch" aria-label="Comparison interval"></select></label><p class="compare-status" role="status"></p><div class="band-grid"><figure><h4>D3 · 1.6–2.4 µm</h4><canvas aria-label="D3 short-wavelength observation"></canvas><figcaption></figcaption></figure><figure><h4>D6 · 4.4–5.0 µm</h4><canvas aria-label="D6 long-wavelength observation"></canvas><figcaption></figcaption></figure></div><p class="source">Each image is stretched separately to reveal faint sources. Screen brightness is not a flux ratio. Quantitative colours require photometry paired from the same exposure; differences between bands do not indicate motion.</p>`;
  const status=host.querySelector<HTMLElement>('.compare-status')!,select=host.querySelector<HTMLSelectElement>('select')!;
  let current:Target|undefined,controller=new AbortController(),loaded=false,request=0;
  async function load(){
    if(!current||loaded)return;controller.abort();controller=new AbortController();const signal=controller.signal;
    status.textContent='Loading real D3 and D6 observations…';select.disabled=true;
    try{
      const [short,long]=await Promise.all([3,6].map(det=>loadFrames({...current!,slug:current!.slug||null,det:det as 3|6,clock:'months',fov:5},signal)));
      if(signal.aborted)return;
      const pairs=bandPairs(short.frames.epochs.filter(e=>e.det===3),long.frames.epochs.filter(e=>e.det===6));
      if(!pairs.length)throw new Error('D3 and D6 have no overlapping observation intervals, so they cannot be compared for colour.');
      select.innerHTML=pairs.map((p,i)=>`<option value="${i}">${dateLabel(p.short.mjd_mid)} / ${dateLabel(p.long.mjd_mid)}</option>`).join('');
      const show=async()=>{const id=++request;status.textContent='Loading frame pixels…';try{const p=pairs[Number(select.value)],obs=await Promise.all([observation(short,p.short,signal),observation(long,p.long,signal)]);if(signal.aborted||id!==request)return;host.querySelectorAll('figure').forEach((f,i)=>{drawObservation(f.querySelector('canvas')!,obs[i],displayLimits([obs[i]]));const q=quality(obs[i].epoch);f.querySelector('figcaption')!.innerHTML=`${dateLabel(obs[i].epoch.mjd_mid)} · ${obs[i].epoch.n_exposures} exposures <span class="evidence ${q.tone}">${esc(q.label)}</span><br>${esc(q.detail)}`});status.textContent='Matched overlapping observation intervals · north up, east left';}catch(e){if(!signal.aborted&&id===request)status.textContent=(e as Error).message}};
      select.onchange=()=>void show();select.disabled=false;loaded=true;await show();
    }catch(e){if(!signal.aborted){status.textContent=`Comparison unavailable: ${(e as Error).message}`;loaded=false;}}
  }
  host.addEventListener('toggle',()=>{if((host as HTMLDetailsElement).open)void load()});
  return {setTarget(t:Target){current=t;controller.abort();request++;loaded=false;select.replaceChildren();status.textContent='';host.querySelectorAll('canvas').forEach(c=>{c.width=0;c.height=0});host.querySelectorAll('figcaption').forEach(c=>c.replaceChildren());if((host as HTMLDetailsElement).open)void load()}};
}
