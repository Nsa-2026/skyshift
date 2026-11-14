import type {MountPage, PageHandle, View} from '../app/modules';
import {TARGETS, viewForTarget} from '../app/state';
import {loadScience} from '../api/science-client';
import {loadCulture, culture, englishCultureText} from './culture';
import {escapeHtml as esc, dateLabel} from './evidence';

/** Keep stories in Learn while retaining its existing course and route lifecycle. */
export function loadCultureStories(learn:MountPage):MountPage {
  return (el,ctx)=>{
    el.innerHTML='<div class="course-host"></div><div class="stories-host" hidden></div>';
    const courseEl=el.querySelector<HTMLElement>('.course-host')!,storyEl=el.querySelector<HTMLElement>('.stories-host')!;
    let course:PageHandle|undefined,stories:PageHandle|undefined,active:PageHandle|undefined;
    return {
      show(param){
        const story=param==='stories'||param?.startsWith('stories/');
        active?.hide?.();courseEl.hidden=!!story;storyEl.hidden=!story;
        active=story?(stories??=mountStories(storyEl,ctx)):(course??=learn(courseEl,ctx));
        active.show(story?param?.split('/')[1]??null:param);
      },
      hide(){active?.hide?.()},
      destroy(){course?.destroy();stories?.destroy();el.replaceChildren()},
    };
  };
}
const mountStories:MountPage=(el,ctx)=>{
  let view:View=ctx.currentView?.()??viewForTarget(TARGETS[0]);
  let controller=new AbortController(),tab='asterisms';
  el.className='stories-page';
  function shell(){
    el.innerHTML=`<a class="stories-back" href="#/learn">← Learn</a>
      <header><p class="workspace-eyebrow">Stories</p><h1>Another way to read the sky</h1><p>Explore historical asterisms, guest stars and planetary events. These cultural interpretations are separate from scientific signal checks.</p></header>
      <nav class="wb-tabs" aria-label="Sky stories">${[['asterisms','Asterisms'],['guests','Guest stars'],['planets','Planet events']].map(([id,label])=>`<a href="#/learn/stories/${id}" ${tab===id?'aria-current="page"':''}>${label}</a>`).join('')}</nav>
      <div class="story-content"><p role="status">Loading sky stories…</p></div>`;
  }
  async function render(){
    controller.abort();controller=new AbortController();const signal=controller.signal;
    shell();const content=el.querySelector<HTMLElement>('.story-content')!;
    try {
      await loadCulture();if(signal.aborted)return;
      const {nasa}=culture();
      if(tab==='guests'){
        content.innerHTML=`<h2>Historical guest stars</h2><p class="source">SPHEREx DR1 views of their remnants, paired with historical accounts.</p><div class="story-gallery"></div>`;
        for(const item of nasa.spherex_remnants.items){
          const figure=document.createElement('figure'),img=document.createElement('img'),caption=document.createElement('figcaption');
          img.src=`/guxing/data/nasa/${item.image}`;img.alt=`${item.remnant} · SPHEREx DR1`;img.loading='lazy';
          const record=nasa.guest_stars.items.find(g=>g.id===item.id);
          caption.textContent=`${item.id} · ${item.remnant}. ${englishCultureText(record?.record_en??'')} ${item.seen_in_spherex}`;
          figure.append(img,caption);content.querySelector('.story-gallery')!.append(figure);
        }
      } else if(tab==='planets'){
        content.innerHTML=`<h2>Planet and Moon events</h2><label>Month <input class="input" type="month" value="2026-11"></label><div class="story-events"></div><p class="source">NASA JPL DE421 · geocentric ephemerides, not visibility from a particular site. Proximity, guarding and occultation thresholds follow this module’s conventions.</p>`;
        const input=content.querySelector('input')!;
        const show=()=>{content.querySelector('.story-events')!.innerHTML=`<div class="table-scroll"><table><thead><tr><th>Time (UTC)</th><th>Historical sky description</th></tr></thead><tbody>${nasa.planets.events.filter(e=>e.time.startsWith(input.value)).map(e=>`<tr><td>${esc(e.time)}</td><td>${esc(englishCultureText(e.log_en))}</td></tr>`).join('')}</tbody></table></div>`;};
        input.onchange=show;show();
      } else {
        content.innerHTML=`<label>Sky position <select class="input" aria-label="Story target">${!TARGETS.slice(0,5).some(t=>t.slug===view.slug)?`<option value="custom" selected>${esc(view.name??'Current workspace position')}</option>`:''}${TARGETS.slice(0,5).map(t=>`<option value="${t.slug}" ${view.slug===t.slug?'selected':''}>${esc(t.name)}</option>`).join('')}</select></label><p class="story-status source" role="status">Loading the observation context…</p><div class="story-asterism"></div><a class="btn" href="#/explore">Return to the workspace</a>`;
        content.querySelector('select')!.onchange=e=>{const target=TARGETS.find(t=>t.slug===(e.target as HTMLSelectElement).value);if(target){view=viewForTarget(target);void render();}};
        const card=document.createElement('guxing-card');card.setAttribute('lang','en');card.setAttribute('ra',String(view.ra));card.setAttribute('dec',String(view.dec));
        card.setAttribute('date',new Date().toISOString().slice(0,10));card.setAttribute('source','Sky coordinates · cultural interpretation');
        content.querySelector('.story-asterism')!.append(card);
        try {
          const data=await loadScience<{frames:{mjd:number}[];target_motion:null|{pmra:number;pmdec:number}}>(view,signal);
          if(signal.aborted)return;
          if(data.frames.length)card.setAttribute('date',dateLabel(Math.max(...data.frames.map(f=>f.mjd))));
          if(data.target_motion){card.setAttribute('pmra',String(data.target_motion.pmra*1000));card.setAttribute('pmdec',String(data.target_motion.pmdec*1000));card.setAttribute('years','1000');}
          card.setAttribute('source','NASA SPHEREx · measured coordinates and experimental proper motion');
          content.querySelector('.story-status')!.textContent='The path is an experimental proper-motion extrapolation, not an orbit prediction.';
        } catch {if(!signal.aborted)content.querySelector('.story-status')!.textContent='Showing the sky position. No measured proper-motion context is available.';}
      }
    } catch(error){if(!signal.aborted)content.textContent=(error as Error).message;}
  }
  return {show(param){document.title='Sky stories: SkyShift';tab=param==='guests'||param==='planets'?param:'asterisms';view=ctx.currentView?.()??view;void render()},hide(){controller.abort()},destroy(){controller.abort();el.replaceChildren()}};
};
