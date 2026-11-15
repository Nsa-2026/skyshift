import './guide.css';
import {loadAnalysis,type Target,type ObjectData,type Analysis} from '../api/science-client';
import {loadObject} from '../api/client';
import {loadCulture,culture,type Location,type Guxing} from './culture';
import {lessonObservations,drawObservation,displayLimits,type Observation} from '../product/observations';
import {dateLabel,falseAlarm,quality,spectralColour,motionDirection,matchedCandidate,escapeHtml as esc} from '../product/evidence';
import {LESSONS,expectedPosition,measureOf,readProgress,saveProgress,lessonSteps,positionPixel,targetCrop,type Crop,type LessonStep} from './model';
import {mountExtras} from './extras';
import {mountHunt} from './hunt';

const STEP_NAMES:Record<LessonStep,string>={hunt:'大地图搜寻',done:'收入收藏'};

export function mountLearning(host:HTMLElement,onExplore:(slug:string)=>void){
  host.innerHTML=`<nav class="guide-nav" aria-label="学习入口"><a href="#/learn">← Learn</a><div><a href="#/learn/stories">文化故事</a><a href="#/learn">科学课程</a><a href="#/learn/practice">引导练习</a></div></nav>
  <div class="guide-home">
    <section class="guide-welcome" aria-labelledby="hunt-title"><div class="guide-intro"><p class="guide-kicker">Sky hunt / 星空捉迷藏</p>
      <h1 id="hunt-title" tabindex="-1">一整片星空，<br>藏着一个秘密。</h1><p class="guide-lead">拖动地图，找出可疑的星。<br>跟住三张照片，才算真正找到。</p>
      <ol class="guide-promise"><li><span>1</span> 搜寻星空</li><li><span>2</span> 跟踪三次</li><li><span>3</span> 收入收藏</li></ol>
      <button type="button" class="guide-start guide-primary" disabled>正在准备星空…</button><p class="guide-time">可以找错，线索会一点点给你。</p></div>
      <figure class="guide-preview"><div class="guide-preview-plates">${[1,2,3].map(n=>`<div><canvas role="img" aria-label="巴纳德星的第${n}次真实观测"></canvas><span>第${n}次观测</span></div>`).join('')}</div><figcaption>巴纳德星的三次真实观测。<br>同一片天空，再看一眼就有新线索。</figcaption><span class="guide-preview-source">NASA SPHEREx</span></figure>
    </section>
    <section class="guide-shelf-section" aria-labelledby="hunt-collection"><div><h2 id="hunt-collection">我的星空</h2><p>找到的每一颗星，都留在这里。</p></div><div class="guide-collection" aria-label="我的星星收藏"></div></section>
    <details class="guide-levels" open><summary>换一片星空 <span class="lesson-progress"></span></summary><div class="lesson-cards"></div></details>
    <div class="guide-extras"></div>
  </div>
  <section class="guide-stage" hidden aria-label="星空捉迷藏">
    <div class="guide-top"><button class="guide-back guide-text">← 返回</button><span class="guide-count"></span><button class="guide-exit guide-text">我的星空</button></div>
    <progress class="guide-progress" max="3" value="1" aria-label="找星进度"></progress>
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
    if(!saveProgress(done))feedback('收集到了！这个浏览器暂时不能保存，下次回来可能需要再找一次。');
    cards();
  }
  function cards(){
    $('.lesson-progress').textContent=`已收集 ${done.filter(s=>LESSONS.some(l=>l.slug===s)).length} / 5`;
    $('.guide-collection').innerHTML=LESSONS.map(l=>`<button type="button" data-lesson="${l.slug}" class="collection-star ${done.includes(l.slug)?'collected':''}" title="${esc(l.title)}" aria-label="${esc(l.title)}：${done.includes(l.slug)?'已收集':'还没找到'}">${done.includes(l.slug)?'✦':'✧'}</button>`).join('');
    $('.lesson-cards').innerHTML=LESSONS.map((l,i)=>`<button type="button" data-lesson="${l.slug}" class="${done.includes(l.slug)?'is-collected':''}"><span class="lesson-number">${done.includes(l.slug)?'✦':String(i+1).padStart(2,'0')}</span><span class="lesson-copy"><strong>${l.title}</strong><small>${l.subtitle}</small><span class="lesson-target">${esc(l.western)}</span></span><span class="lesson-arrow" aria-hidden="true">↗</span></button>`).join('');
    const button=$<HTMLButtonElement>('.guide-start');button.disabled=!targets.length;
    button.textContent=!targets.length?'正在准备星空…':done.some(s=>LESSONS.some(l=>l.slug===s))?'继续搜寻星空 →':'进入星空地图 →';
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
    $('.guide-content').innerHTML=heading('正在打开星空…','这次玩的是 NASA 拍下的真实照片。');feedback('');updateHeader();
    primary('正在准备…',()=>{},true);$('.guide-stage').scrollIntoView({block:'start'});
    try{
      const [data,,images]=await Promise.all([loadObject(target,signal),loadCulture(),lessonObservations(target,det,signal,{fov:slug==='wise0855'?5:15,all:true})]);
      if(signal.aborted||id!==request)return;
      object=data;observations=images;bands[det]=images;gx=culture().gx;location=gx.locate(target.ra,target.dec,{date:dateLabel(images[0].epoch.mjd_mid)});
      recrop();ready=true;render();
      loadAnalysis(target,target.default_det,signal).then(value=>{if(id===request&&!signal.aborted){analysis=value;if(step()==='done')reviewEvidence()}}).catch(()=>{});
    }catch(error){if(!signal.aborted&&id===request){
      $('.guide-content').innerHTML=heading('照片暂时没打开','再试一次，就能继续找星星。');
      feedback((error as Error).message);primary('重新打开照片',()=>void start(slug));
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
    return `<div class="guide-image"><canvas class="guide-canvas" role="img" aria-label="你找到的星，真实观测照片"></canvas><span class="guide-marker" hidden></span><span class="guide-photo-tag"></span></div><p class="guide-photo-date"></p><p class="guide-missing" hidden>格子处没有照片数据，看周围的亮边就好。</p>`;
  }
  function draw(){
    const o=observations[index],canvas=$<HTMLCanvasElement>('.guide-canvas');if(!canvas)return;
    const source=document.createElement('canvas');drawObservation(source,o,displayLimits(observations));
    canvas.width=360;canvas.height=360;const context=canvas.getContext('2d');
    if(context){context.imageSmoothingEnabled=false;context.drawImage(source,crop.x,crop.y,crop.size,crop.size,0,0,360,360)}
    $('.guide-photo-tag').textContent=`${index?'后来':'之前'}${lesson.slug==='wise0855'&&det===6?' · 夜视已开':''}`;
    $('.guide-photo-date').textContent=`${dateLabel(o.epoch.mjd_mid)} · NASA 真实照片`;
    const f={...o.set.frames,center:o.epoch.center||o.set.frames.center};
    const q=quality(o.epoch),target=positionPixel(expectedPosition(current!,object!,o.epoch.mjd_mid),f),px=Math.round(target.x-.5),py=Math.round(target.y-.5);
    $('.guide-missing').hidden=Number.isFinite(o.values[py*f.size[0]+px]);
    const note=$('.guide-quality');if(note)note.textContent=`D${det} · ${o.epoch.n_exposures} 次曝光 · ${q.label}。${q.detail}`;
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
        if(signal.aborted||id!==request)throw new Error('已离开当前地图');
        bands[nextDet]=next;observations=next;det=nextDet;recrop();return next;
      }}:{}),
    });
    $('.guide-notes').innerHTML=`<details class="guide-data"><summary>这片星空从哪里来？</summary><p>这里是 NASA SPHEREx 拍下的完整${lesson.slug==='wise0855'?' 5′':' 15′'}天区，网格和黄色框只是导航提示。三张照片共用亮度显示尺度；可以拖动、缩放，星点没有人为移动或补画。格子处没有有效观测数据。</p><p>${lesson.slug==='wise0855'?'夜视使用同一片 5′ 天区的 D3 / D6 实际观测，各自拉伸，不能直接比较屏幕亮度。':''}跟踪奖励表示在三次照片中找到了已知目标，不代表发现新天体。</p></details>`;
  }
  function discovery(){
    if(lesson.slug==='wise0855')return '换一种光，就能找出更多藏在暗处的线索。';
    if(['rwaur','rdra'].includes(lesson.slug))return '星星不挪位置，也可能改变亮暗。';
    return motionDirection(measureOf(object))==='No significant motion measured'?'照片里的小变化，还能继续放大看看。':'原来看似不动的星，也会慢慢挪位置。';
  }
  function doneStep(){
    const next=LESSONS.find(l=>!done.includes(l.slug)&&l.slug!==lesson.slug);
    $('.guide-content').innerHTML=heading('你的星空，多了一颗。',discovery())+`<div class="guide-reward"><span class="reward-stamp">✦ 已收集</span>${photoHtml()}<strong>${esc(lesson.title)}</strong><small>${esc(current!.name)}</small></div><p class="guide-shelf"></p>`;
    index=observations.length-1;draw();const p=targetPointer();mark(p.x,p.y,'found');complete(current!.slug);
    $('.guide-shelf').textContent=`✦ 已收集 ${done.filter(s=>LESSONS.some(l=>l.slug===s)).length} / 5 颗`;
    primary(next?'再去找一颗 →':'五颗集齐了！回我的星空 →',()=>next?void start(next.slug):home());
    const help=$<HTMLButtonElement>('.guide-help');help.hidden=false;help.disabled=false;help.textContent='再玩这颗';help.onclick=()=>void start(current!.slug);
    const motion=motionDirection(measureOf(object)),direction=({North:'北',Northeast:'东北',East:'东',Southeast:'东南',South:'南',Southwest:'西南',West:'西',Northwest:'西北'} as Record<string,string>)[motion],colour=spectralColour(object!.spectrum);
    $('.guide-notes').innerHTML=`<details class="guide-data"><summary>它还有什么秘密？</summary><p>${esc(current!.name)}：${direction?`测得向${direction}移动`:'这组数据还没有测出明显移动'}。${esc(colour.detail)}</p><p>古人把这一带叫${esc(location.mansion.zh)}；它附近的星星有个名字：${esc(location.nearest[0]?.asterism_zh||'暂无资料')}。西方星座：${esc(lesson.western)}。</p><div class="guide-star-map"></div><p>星图采用清代星官目录，只表示天空中的视线位置。</p><button class="guide-explore guide-text">去探索里放大看看 →</button><details><summary>这算发现新天体了吗？</summary><div class="guide-science-review"></div></details><details><summary>把时间拨到很久以前</summary><label>年代 <input class="year-slider" type="range" min="-200" max="3000" value="2026" step="1"></label><output class="year-output"></output><p>按实测自行与岁差作线性外推，不是古代拍下的照片。</p></details></details>`;
    $('.guide-explore').onclick=()=>onExplore(current!.slug);drawMap();reviewEvidence();years();
    if(!next){$('.guide-notes').insertAdjacentHTML('beforeend','<button class="guide-story guide-text">彩蛋：找找千年前的星星 →</button>');$('.guide-story').onclick=()=>{home();extras.story('SN1054')}}
  }
  function drawMap(){
    const center=current!,radius=14,project=(ra:number,dec:number)=>[200-(((ra-center.ra+540)%360)-180)*Math.cos(center.dec*Math.PI/180)*170/radius,120-(dec-center.dec)*100/radius];
    $('.guide-star-map').innerHTML=`<svg viewBox="0 0 400 240" role="img" aria-label="真实古星位置图，橙点为目标"><svg x="0" y="0" width="400" height="240">${gx.segments(center.ra,center.dec,radius).map(s=>`<g class="map-asterism"><title>${esc(s.zh)} · ${esc(s.en)}</title>${s.segments.map(([a,b])=>{const p=project(...a),q=project(...b);return `<line x1="${p[0]}" y1="${p[1]}" x2="${q[0]}" y2="${q[1]}"/><circle cx="${p[0]}" cy="${p[1]}" r="2"/>`}).join('')}</g>`).join('')}</svg><circle cx="200" cy="120" r="5" class="map-target"/><text x="210" y="115">你找到的星</text></svg>`;
  }
  function reviewEvidence(){
    const el=$('.guide-science-review');if(!el)return;
    const candidate=matchedCandidate(analysis,current!,30),evidence=falseAlarm(candidate?.p_false);
    el.innerHTML=`<p>你找到的是已知目标。确认一个新天体，还需要继续检查照片、噪声和已知天体。</p><span class="evidence ${evidence.tone}">${esc(evidence.label)}</span><p>${esc(evidence.explanation)}</p><p>${candidate?'沿用探索里位置匹配候选的背景检验。':'没有匹配到可用的候选误报评分，不能借用其他天体的概率。'} 收集奖励不代表科学确认。</p>`;
  }
  function years(){
    const slider=$<HTMLInputElement>('.year-slider');
    const update=()=>{
      const y=Number(slider.value),g=object?.gaia as {ra:number;dec:number;pmra:number|null;pmdec:number|null}|null;
      let p:{ra:number;dec:number}|undefined;
      if(g&&g.pmra!=null&&g.pmdec!=null)p={ra:(g.ra+g.pmra*(y-2016)/(3.6e6*Math.cos(g.dec*Math.PI/180))+360)%360,dec:Math.max(-90,Math.min(90,g.dec+g.pmdec*(y-2016)/3.6e6))};
      const m=measureOf(object);
      if(!p&&m&&m.shift_arcsec>=3*m.sigma_arcsec){const a=m.pa_deg*Math.PI/180,dt=y-(2000+(observations[0].epoch.mjd_mid-51544.5)/365.25);p={ra:(current!.ra+Math.sin(a)*m.rate_arcsec_yr*dt/(3600*Math.cos(current!.dec*Math.PI/180))+360)%360,dec:current!.dec+Math.cos(a)*m.rate_arcsec_yr*dt/3600}}
      $('.year-output').textContent=p?`${y<1?`公元前 ${1-y}`:`公元 ${y}`} 年 · ${gx.mansion(p.ra,p.dec,y).zh}（模型推算）`:'数据还不足以推算这颗星在古代的位置。';
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
