import './guide.css';
import {loadAnalysis,type Target,type ObjectData,type Analysis} from '../api/science-client';
import {loadObject} from '../api/client';
import {loadCulture,culture,type Location,type Guxing} from './culture';
import {lessonObservations,drawObservation,displayLimits,type Observation} from '../product/observations';
import {dateLabel,falseAlarm,quality,spectralColour,motionDirection,matchedCandidate,escapeHtml as esc} from '../product/evidence';
import {LESSONS,expectedPosition,measureOf,readProgress,saveProgress,lessonSteps,positionPixel,targetCrop,type Crop,type LessonStep} from './model';
import {mountExtras} from './extras';
import {mountHunt} from './hunt';

const STEP_NAMES:Record<LessonStep,string>={hunt:'Search the sky',done:'Add to collection'};

export function mountLearning(host:HTMLElement,onExplore:(slug:string)=>void){
  host.innerHTML=`<nav class="guide-nav" aria-label="Learning activities"><a href="#/learn">← Learn</a><div><a href="#/learn/stories">Sky stories</a><a href="#/learn">Science lessons</a><a href="#/learn/practice">Guided practice</a></div></nav>
  <div class="guide-home">
    <section class="guide-welcome" aria-labelledby="hunt-title"><div class="guide-intro"><p class="guide-kicker">Sky hunt</p>
      <h1 id="hunt-title" tabindex="-1">A whole sky.<br>One small change.</h1><p class="guide-lead">Pan across the sky and choose a star.<br>Find it in three observations to complete the hunt.</p>
      <ol class="guide-promise"><li><span>1</span> Search</li><li><span>2</span> Follow three times</li><li><span>3</span> Add to collection</li></ol>
      <button type="button" class="guide-start guide-primary" disabled>Preparing the sky…</button><p class="guide-time">Take your time. Clues are here when you need them.</p></div>
      <figure class="guide-preview"><div class="guide-preview-plates">${[1,2,3].map(n=>`<div><canvas role="img" aria-label="Real observation ${n} of Barnard’s Star"></canvas><span>Observation ${n}</span></div>`).join('')}</div><figcaption>Three real observations of Barnard’s Star.<br>The same sky, with something new to notice.</figcaption><span class="guide-preview-source">NASA SPHEREx</span></figure>
    </section>
    <section class="guide-shelf-section" aria-labelledby="hunt-collection"><div><h2 id="hunt-collection">My collection</h2><p>A place for every star you find.</p></div><div class="guide-collection" aria-label="My star collection"></div></section>
    <details class="guide-levels" open><summary>Choose a field <span class="lesson-progress"></span></summary><div class="lesson-cards"></div></details>
    <div class="guide-extras"></div>
  </div>
  <section class="guide-stage" hidden aria-label="Sky hunt">
    <div class="guide-top"><button class="guide-back guide-text">← Back</button><span class="guide-count"></span><button class="guide-exit guide-text">My collection</button></div>
    <progress class="guide-progress" max="3" value="1" aria-label="Hunt progress"></progress>
    <div class="guide-content"></div>
    <p class="guide-feedback" role="status" aria-live="polite"></p>
    <div class="guide-actions"><button class="guide-next guide-primary"></button><button class="guide-help guide-text" hidden></button></div>
    <div class="guide-notes"></div>
  </section>`;
  const $=<T extends HTMLElement=HTMLElement>(selector:string)=>host.querySelector<T>(selector)!;
  let targets:Target[]=[],done=readProgress(),current:Target|undefined,lesson=LESSONS[0],position=0;
  let observations:Observation[]=[],object:ObjectData|undefined,analysis:Analysis|undefined,gx:Guxing,location:Location;
  let crop:Crop={x:0,y:0,size:50},index=0,det:2|3|6=2;
  let controller=new AbortController(),request=0,active=false,ready=false;
  let interrupted=false;
  let hunt:ReturnType<typeof mountHunt>|undefined;
  let bands:Partial<Record<2|3|6,Observation[]>>={};
  const steps=()=>lessonSteps(lesson.slug);
  const step=()=>steps()[position];
  const feedback=(message:string)=>{$('.guide-feedback').textContent=message};
  const stop=()=>{hunt?.dispose();hunt=undefined};
  const extras=mountExtras($('.guide-extras'),complete);
  const nextUncollected=()=>LESSONS.find(l=>!done.includes(l.slug))||LESSONS[0];

  function complete(slug:string){
    if(!done.includes(slug))done.push(slug);
    if(!saveProgress(done))feedback('You found it. This browser could not save your collection, so you may need to find it again next time.');
    cards();
  }
  function cards(){
    $('.lesson-progress').textContent=`Collected ${done.filter(s=>LESSONS.some(l=>l.slug===s)).length} of 5`;
    $('.guide-collection').innerHTML=LESSONS.map(l=>`<button type="button" data-lesson="${l.slug}" class="collection-star ${done.includes(l.slug)?'collected':''}" title="${esc(l.title)}" aria-label="${esc(l.title)}: ${done.includes(l.slug)?'Collected':'Not found yet'}">${done.includes(l.slug)?'✦':'✧'}</button>`).join('');
    $('.lesson-cards').innerHTML=LESSONS.map((l,i)=>`<button type="button" data-lesson="${l.slug}" class="${done.includes(l.slug)?'is-collected':''}"><span class="lesson-number">${done.includes(l.slug)?'✦':String(i+1).padStart(2,'0')}</span><span class="lesson-copy"><strong>${l.title}</strong><small>${l.subtitle}</small><span class="lesson-target">${esc(l.western)}</span></span><span class="lesson-arrow" aria-hidden="true">↗</span></button>`).join('');
    const button=$<HTMLButtonElement>('.guide-start');button.disabled=!targets.length;
    button.textContent=!targets.length?'Preparing the sky…':done.some(s=>LESSONS.some(l=>l.slug===s))?'Continue the hunt →':'Explore the sky →';
  }
  function home(){
    stop();controller.abort();request++;ready=false;
    $('.guide-home').hidden=false;$('.guide-stage').hidden=true;
    $('.guide-start').focus({preventScroll:true});host.scrollIntoView({block:'start'});
  }
  function primary(text:string,action:()=>void,disabled=false){
    const button=$<HTMLButtonElement>('.guide-next');button.textContent=text;button.disabled=disabled;button.onclick=action;
  }
  function heading(title:string,subtitle:string){
    return `<h2 class="guide-heading" tabindex="-1">${title}</h2><p class="guide-instruction">${subtitle}</p>`;
  }
  function advance(){if(position<steps().length-1){position++;render()}}
  function updateHeader(){
    const total=steps().length,number=position+1;
    $('.guide-count').textContent=STEP_NAMES[step()];
    const progress=$<HTMLProgressElement>('.guide-progress');progress.max=total;progress.value=number;
    $<HTMLButtonElement>('.guide-back').disabled=position===0;
  }
  async function start(slug:string){
    const target=targets.find(t=>t.slug===slug);if(!target)return;
    stop();controller.abort();controller=new AbortController();const signal=controller.signal,id=++request;
    // Barnard's D6 images have a smaller saturated core than D2, so its real motion is easier to follow.
    current=target;lesson=LESSONS.find(l=>l.slug===slug)!;position=0;det=slug==='wise0855'?3:slug==='barnard'?6:2;
    ready=false;object=undefined;analysis=undefined;observations=[];bands={};
    $('.guide-home').hidden=true;$('.guide-stage').hidden=false;$('.guide-help').hidden=true;$('.guide-notes').replaceChildren();
    $('.guide-content').innerHTML=heading('Opening the sky…','You will explore real NASA observations.');feedback('');updateHeader();
    primary('Preparing…',()=>{},true);$('.guide-stage').scrollIntoView({block:'start'});
    try{
      const [data,,images]=await Promise.all([loadObject(target,signal),loadCulture(),lessonObservations(target,det,signal,{fov:slug==='wise0855'?5:15,all:true})]);
      if(signal.aborted||id!==request)return;
      object=data;observations=images;bands[det]=images;gx=culture().gx;location=gx.locate(target.ra,target.dec,{date:dateLabel(images[0].epoch.mjd_mid)});
      recrop();ready=true;render();
      loadAnalysis(target,target.default_det,signal).then(value=>{if(id===request&&!signal.aborted){analysis=value;if(step()==='done')reviewEvidence()}}).catch(()=>{});
    }catch(error){if(!signal.aborted&&id===request){
      $('.guide-content').innerHTML=heading('These observations could not load','Try again to continue the hunt.');
      feedback((error as Error).message);primary('Reload observations',()=>void start(slug));
    }}
  }
  function recrop(){crop=targetCrop(expectedPosition(current!,object!,observations[0].epoch.mjd_mid),observations[0].set.frames)}
  function render(){
    stop();feedback('');$('.guide-help').hidden=true;$('.guide-notes').replaceChildren();updateHeader();
    $('.guide-stage').classList.toggle('is-hunting',step()==='hunt');
    const renderers:Record<LessonStep,()=>void>={hunt:huntStep,done:doneStep};
    renderers[step()]();
    $('.guide-heading')?.focus({preventScroll:true});$('.guide-stage').scrollIntoView({block:'start'});
  }
  function photoHtml(){
    return `<div class="guide-image"><canvas class="guide-canvas" role="img" aria-label="The star you found in a real observation"></canvas><span class="guide-marker" hidden></span><span class="guide-photo-tag"></span></div><p class="guide-photo-date"></p><p class="guide-missing" hidden>Blank regions have no usable data. Look at the surrounding light instead.</p>`;
  }
  function draw(){
    const o=observations[index],canvas=$<HTMLCanvasElement>('.guide-canvas');if(!canvas)return;
    const source=document.createElement('canvas');drawObservation(source,o,displayLimits(observations));
    canvas.width=360;canvas.height=360;const context=canvas.getContext('2d');
    if(context){context.imageSmoothingEnabled=false;context.drawImage(source,crop.x,crop.y,crop.size,crop.size,0,0,360,360)}
    $('.guide-photo-tag').textContent=`${index?'Later':'Earlier'}${lesson.slug==='wise0855'&&det===6?' · Infrared vision on':''}`;
    $('.guide-photo-date').textContent=`${dateLabel(o.epoch.mjd_mid)} · Real NASA observation`;
    const f={...o.set.frames,center:o.epoch.center||o.set.frames.center};
    const q=quality(o.epoch),target=positionPixel(expectedPosition(current!,object!,o.epoch.mjd_mid),f),px=Math.round(target.x-.5),py=Math.round(target.y-.5);
    $('.guide-missing').hidden=Number.isFinite(o.values[py*f.size[0]+px]);
    const note=$('.guide-quality');if(note)note.textContent=`D${det} · ${o.epoch.n_exposures} exposures · ${q.label}. ${q.detail}`;
  }
  function mark(x:number,y:number,kind=''){
    const marker=$('.guide-marker');marker.hidden=false;marker.className=`guide-marker ${kind}`;marker.style.left=`${x*100}%`;marker.style.top=`${y*100}%`;
  }
  function targetPointer(epoch=index){
    const o=observations[epoch],p=positionPixel(expectedPosition(current!,object!,o.epoch.mjd_mid),{...o.set.frames,center:o.epoch.center||o.set.frames.center});
    return {x:(p.x-crop.x)/crop.size,y:(p.y-crop.y)/crop.size};
  }
  function huntStep(){
    hunt=mountHunt($('.guide-content'),{
      target:current!,object:object!,observations,night:lesson.slug==='wise0855'&&det===6,active,
      action:primary,feedback,
      help(label,action,disabled=false){const button=$<HTMLButtonElement>('.guide-help');button.hidden=false;button.disabled=disabled;button.textContent=label;button.onclick=action},
      complete:advance,
      ...(lesson.slug==='wise0855'?{changeNight:async(enabled:boolean)=>{
        const id=request,signal=controller.signal,nextDet=enabled?6:3,next=bands[nextDet]||await lessonObservations(current!,nextDet,signal,{fov:5,all:true});
        if(signal.aborted||id!==request)throw new Error('You have left this field');
        bands[nextDet]=next;observations=next;det=nextDet;recrop();return next;
      }}:{}),
    });
    $('.guide-notes').innerHTML=`<details class="guide-data"><summary>Where do these images come from?</summary><p>This is a real NASA SPHEREx ${lesson.slug==='wise0855'?' 5′':' 15′'}field. The grid and yellow clue boxes help you navigate. All three observations share a brightness scale. Pan and zoom to inspect them; no stars have been moved or added. Blank regions have no usable data.</p><p>${lesson.slug==='wise0855'?'Infrared vision switches between real D3 and D6 observations of the same 5′ field. Each band has its own display stretch, so screen brightness cannot be compared directly.':''}A tracking reward means you found a known target in three observations. It does not confirm a new discovery.</p></details>`;
  }
  function discovery(){
    if(lesson.slug==='wise0855')return 'A different wavelength reveals clues that were hidden before.';
    if(['rwaur','rdra'].includes(lesson.slug))return 'A star can change brightness without moving.';
    return motionDirection(measureOf(object))==='No significant motion measured'?'Small changes invite a closer look.':'Even a star that seems still can move between observations.';
  }
  function doneStep(){
    const next=LESSONS.find(l=>!done.includes(l.slug)&&l.slug!==lesson.slug);
    $('.guide-content').innerHTML=heading('One more star in your collection.',discovery())+`<div class="guide-reward"><span class="reward-stamp">✦ Collected</span>${photoHtml()}<strong>${esc(lesson.title)}</strong><small>${esc(current!.name)}</small></div><p class="guide-shelf"></p>`;
    index=observations.length-1;draw();const p=targetPointer();mark(p.x,p.y,'found');complete(current!.slug);
    $('.guide-shelf').textContent=`✦ Collected ${done.filter(s=>LESSONS.some(l=>l.slug===s)).length} of 5`;
    primary(next?'Find another star →':'All five collected! Back to my collection →',()=>next?void start(next.slug):home());
    const help=$<HTMLButtonElement>('.guide-help');help.hidden=false;help.disabled=false;help.textContent='Try this star again';help.onclick=()=>void start(current!.slug);
    const direction=motionDirection(measureOf(object)),colour=spectralColour(object!.spectrum),nearby=location.nearest[0];
    const motionText=direction==='No significant motion measured'?'No significant motion was measured in these observations.':`Measured motion: ${direction.toLowerCase()}.`;
    $('.guide-notes').innerHTML=`<details class="guide-data"><summary>What else can this star tell us?</summary><p>${esc(current!.name)}: ${motionText} ${esc(colour.detail)}</p><p>On the traditional Chinese map, this region lies in ${esc(location.mansion.en)} (<span lang="zh">${esc(location.mansion.zh)}</span>). A nearby asterism is ${nearby?`${esc(nearby.asterism_en)} (<span lang="zh">${esc(nearby.asterism_zh)}</span>)`:'not available'}. Western constellation: ${esc(lesson.western)}.</p><div class="guide-star-map"></div><p>This chart uses Qing-era asterism identifications and shows positions along our line of sight.</p><button class="guide-explore guide-text">Look closer in the Viewer →</button><details><summary>Have I discovered a new object?</summary><div class="guide-science-review"></div></details><details><summary>Move through the centuries</summary><label>Year <input class="year-slider" type="range" min="-200" max="3000" value="2026" step="1"></label><output class="year-output"></output><p>A linear model using proper motion and precession, not a photograph of the ancient sky.</p></details></details>`;
    $('.guide-explore').onclick=()=>onExplore(current!.slug);drawMap();reviewEvidence();years();
    if(!next){$('.guide-notes').insertAdjacentHTML('beforeend','<button class="guide-story guide-text">Explore a star from a thousand years ago →</button>');$('.guide-story').onclick=()=>{home();extras.story('SN1054')}}
  }
  function drawMap(){
    const center=current!,radius=14,project=(ra:number,dec:number)=>[200-(((ra-center.ra+540)%360)-180)*Math.cos(center.dec*Math.PI/180)*170/radius,120-(dec-center.dec)*100/radius];
    $('.guide-star-map').innerHTML=`<svg viewBox="0 0 400 240" role="img" aria-label="Traditional asterism map with the target marked in red"><svg x="0" y="0" width="400" height="240">${gx.segments(center.ra,center.dec,radius).map(s=>`<g class="map-asterism"><title>${esc(s.en)}</title>${s.segments.map(([a,b])=>{const p=project(...a),q=project(...b);return `<line x1="${p[0]}" y1="${p[1]}" x2="${q[0]}" y2="${q[1]}"/><circle cx="${p[0]}" cy="${p[1]}" r="2"/>`}).join('')}</g>`).join('')}</svg><circle cx="200" cy="120" r="5" class="map-target"/><text x="210" y="115">Your star</text></svg>`;
  }
  function reviewEvidence(){
    const el=$('.guide-science-review');if(!el)return;
    const candidate=matchedCandidate(analysis,current!,30),evidence=falseAlarm(candidate?.p_false);
    el.innerHTML=`<p>You found a known target. Confirming a new object requires more checks of the images, noise and catalogued sources.</p><span class="evidence ${evidence.tone}">${esc(evidence.label)}</span><p>${esc(evidence.explanation)}</p><p>${candidate?'This uses the background check for the position-matched signal in the Viewer.':'No calibrated false-alarm score is available for a matching signal. A score from another object cannot be used here.'} A collection reward is not scientific confirmation.</p>`;
  }
  function years(){
    const slider=$<HTMLInputElement>('.year-slider');
    const update=()=>{
      const y=Number(slider.value),g=object?.gaia as {ra:number;dec:number;pmra:number|null;pmdec:number|null}|null;
      let p:{ra:number;dec:number}|undefined;
      if(g&&g.pmra!=null&&g.pmdec!=null)p={ra:(g.ra+g.pmra*(y-2016)/(3.6e6*Math.cos(g.dec*Math.PI/180))+360)%360,dec:Math.max(-90,Math.min(90,g.dec+g.pmdec*(y-2016)/3.6e6))};
      const m=measureOf(object);
      if(!p&&m&&m.shift_arcsec>=3*m.sigma_arcsec){const a=m.pa_deg*Math.PI/180,dt=y-(2000+(observations[0].epoch.mjd_mid-51544.5)/365.25);p={ra:(current!.ra+Math.sin(a)*m.rate_arcsec_yr*dt/(3600*Math.cos(current!.dec*Math.PI/180))+360)%360,dec:current!.dec+Math.cos(a)*m.rate_arcsec_yr*dt/3600}}
      $('.year-output').textContent=p?`${y<1?`${1-y} BC`:`AD ${y}`} · ${gx.mansion(p.ra,p.dec,y).en} (model estimate)`:'There is not enough data to estimate the earlier position of this star.';
    };slider.oninput=update;update();
  }
  $('.guide-start').onclick=()=>void start(nextUncollected().slug);
  $('.lesson-cards').onclick=event=>{const button=(event.target as HTMLElement).closest<HTMLElement>('[data-lesson]');if(button)void start(button.dataset.lesson!)};
  $('.guide-collection').onclick=event=>{const button=(event.target as HTMLElement).closest<HTMLElement>('[data-lesson]');if(button)void start(button.dataset.lesson!)};
  $('.guide-exit').onclick=home;
  $('.guide-back').onclick=()=>{if(ready&&position>0){position--;render()}};
  cards();
  return {
    setTargets(value:Target[]){targets=value;cards()},
    activate(value:boolean){
      active=value;
      if(!value&&!ready&&current&&!controller.signal.aborted){controller.abort();request++;interrupted=true;}
      if(value&&interrupted&&current){interrupted=false;void start(current.slug);}
      hunt?.setActive(value);
    },
    destroy(){controller.abort();request++;extras.destroy();stop();}
  };
}
