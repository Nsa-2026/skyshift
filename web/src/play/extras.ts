import {loadCulture,culture} from './culture';
import {escapeHtml as esc} from '../product/evidence';
export function mountExtras(host:HTMLElement,onComplete:(id:string)=>void){
  host.innerHTML=`<details class="learn-extras" open><summary>自由逛逛 · 历史故事、生日星、古星图</summary>
    <p class="extras-intro">把找到的变化，放进更长的时间里。读一个故事，或选一天看看天象。</p>
    <div class="extras-grid"><div class="story-cards">
      <button type="button" data-story="SN1054"><img src="/guxing/data/nasa/spherex_remnants/SN1054.png" width="128" height="128" loading="lazy" alt="SPHEREx 拍摄的蟹状星云天区"><span><small>1054 年</small><strong>客星去了哪里？</strong><span>从一条古老记录，找到今天的蟹状星云。</span></span><b aria-hidden="true">↗</b></button>
      <button type="button" data-story="SN1572"><img src="/guxing/data/nasa/spherex_remnants/SN1572.png" width="128" height="128" loading="lazy" alt="SPHEREx 拍摄的第谷超新星遗迹天区"><span><small>1572 年</small><strong>中西方看见同一颗星</strong><span>两种记录，指向同一次天空中的变化。</span></span><b aria-hidden="true">↗</b></button>
    </div><section class="curiosity" aria-labelledby="hunt-date-title"><h3 id="hunt-date-title">看看某一天的星空</h3><p>选一个生日、纪念日，或就看看今晚。</p><div class="button-row"><label>生日 / 日期 <input class="sky-date input" type="date" min="2026-01-01" max="2027-12-31"></label><button type="button" class="date-go btn">看这一天</button><button type="button" class="date-today btn btn-ghost">今晚天象</button></div><p class="date-status" role="status"></p><div class="date-events"></div><details><summary>日期范围与数据来源</summary><p>NASA JPL DE421 预计算，仅覆盖 2026–2027。事件时间为 UTC，不代表你所在城市的夜间可见性。</p></details></section></div>
    <section class="story-panel extra" aria-live="polite" hidden></section><div id="learn-culture"><a href="#/learn/stories/asterisms">在古星图里找一个位置 →</a><a href="#/learn/stories/planets">按月份查看天象 →</a></div><p class="culture-credit">星官采用清代版本，是文化解读，不参与科学评分。Stellarium / HYG，CC BY-SA。<a href="/guxing/NOTICE.md" target="_blank" rel="noopener">许可说明</a></p></details>`;
  const $=<T extends HTMLElement=HTMLElement>(s:string)=>host.querySelector<T>(s)!;
  let loadId=0,disposed=false;
  async function story(id:string){
    $('.story-panel').hidden=false;$('.story-panel').textContent='读取历史记录与真实遗迹图…';const request=++loadId;
    try{await loadCulture();if(disposed||request!==loadId)return;const {nasa}=culture(),guest=nasa.guest_stars.items.find(g=>g.id===id)!,remnant=nasa.spherex_remnants.items.find(g=>g.id===id)!;const tycho=id==='SN1572';
      $('.story-panel').innerHTML=`<p class="eyebrow">${tycho?'欧洲篇 · 1572':'彩蛋 · 1054'}</p><h2>${tycho?'同一颗星，两种记录':'客星后来去了哪里？'}</h2><div class="story-grid"><figure><img src="/guxing/data/nasa/${esc(remnant.image)}" alt="${esc(remnant.remnant)} 天区的现代 SPHEREx 红外合成图"><figcaption>现代 NASA SPHEREx DR1 · 红外合成，北上东左。${tycho?'遗迹在 0.75–5 µm 并不明显，不能把场中亮星认作超新星。':'蟹状星云是 1054 客星留下的遗迹。'}</figcaption></figure><div><h3>中国记录</h3><blockquote>${esc(guest.record_zh)}</blockquote><p>${esc(guest.source)} · 沿用古星资料的二手转引，尚未逐字校对史书原本。</p><p>现代释义：${tycho?'东北方出现一颗客星，位置在阁道星官附近。':'天关东南方向出现了一颗先前没有见到的星。'}</p>${tycho?'<h3>欧洲记录 · 第谷</h3><p>第谷在仙后座记录了 1572 年的亮星。它相对于远方恒星没有可测的日周视差，为“恒星世界也会变化”提供了证据。</p><p>这里概述观测，不伪造第谷原文。中国的阁道记录与欧洲的仙后座记录指向同一次超新星事件。</p>':'<p>1054 年的突然增亮被古人记为“客星”；现代望远镜看到的是爆炸后继续膨胀的星云。</p>'}<a target="_blank" rel="noopener" href="${tycho?'https://science.nasa.gov/missions/hubble/stellar-survivor-from-1572-ad-explosion-supports-supernova-theory/':'https://science.nasa.gov/mission/hubble/science/explore-the-night-sky/hubble-messier-catalog/messier-1/'}">NASA 背景资料 ↗</a>${tycho?'<p><a href="https://www.frontiersin.org/journals/astronomy-and-space-sciences/articles/10.3389/fspas.2023.1255481/full" target="_blank" rel="noopener">第谷与中国观测的研究资料 ↗</a></p>':''}</div></div><button class="story-collect guide-primary">把这个故事收进星空 ✦</button><p class="story-feedback" role="status"></p>`;
      $('.story-collect').onclick=()=>{onComplete(id);$('.story-feedback').textContent='✦ 故事收好了。古人看见的变化，今天还有迹可寻。';$<HTMLButtonElement>('.story-collect').disabled=true;$('.story-collect').textContent='✦ 已收藏这个故事'};
    }catch(e){if(!disposed&&request===loadId)$('.story-panel').textContent=(e as Error).message}
  }
  async function dateEvents(){
    const day=$<HTMLInputElement>('.sky-date').value;$('.date-events').replaceChildren();if(!day||day<'2026-01-01'||day>'2027-12-31'){$('.date-status').textContent='请选择 2026–2027 年的有效日期；其他年份尚无预计算星历。';return}$('.date-status').textContent='读取 JPL 预计算事件…';try{await loadCulture();const {nasa}=culture();if(disposed||$<HTMLInputElement>('.sky-date').value!==day)return;const events=nasa.planets.events.filter(e=>e.time.startsWith(day));$('.date-status').textContent=events.length?`${day}（UTC）· ${events.length} 项地心星象事件`:`${day} 没有记录达到本模块阈值的事件。并不代表当晚没有可看的天体。`;$('.date-events').innerHTML=events.map(e=>`<article><time>${esc(e.time)}</time><p>${esc(e.log_zh)}</p><small>${esc(e.log_en)}</small></article>`).join('')}catch(e){if(!disposed)$('.date-status').textContent=(e as Error).message}}

  $('.story-cards').onclick=e=>{const b=(e.target as HTMLElement).closest<HTMLElement>('[data-story]');if(b)void story(b.dataset.story!)};
  $('.date-go').onclick=()=>void dateEvents();
  $('.date-today').onclick=()=>{$<HTMLInputElement>('.sky-date').value=new Intl.DateTimeFormat('sv-SE',{timeZone:'Europe/Rome'}).format(new Date());void dateEvents()};
  $<HTMLInputElement>('.sky-date').value=new Intl.DateTimeFormat('sv-SE',{timeZone:'Europe/Rome'}).format(new Date());
  return {story(id:string){($('details') as HTMLDetailsElement).open=true;void story(id)},destroy(){disposed=true;loadId++;}};
}
