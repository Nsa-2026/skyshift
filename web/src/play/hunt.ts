import {drawObservation,displayLimits,type Observation} from '../product/observations';
import {dateLabel} from '../product/evidence';
import {expectedPosition,positionPixel} from './model';
import type {Target,ObjectData} from '../api/science-client';
import {clampCamera,mapScale,toScreen,toWorld,zoomAt,verifyTrail,clueRegion,type Camera,type Point,type Size,type Footprint} from './hunt-model';

type Options={
  target:Target;object:ObjectData;observations:Observation[];night:boolean;active:boolean;
  changeNight?:(enabled:boolean)=>Promise<Observation[]>;
  action:(label:string,run:()=>void,disabled?:boolean)=>void;
  feedback:(text:string)=>void;
  help:(label:string,run:()=>void,disabled?:boolean)=>void;
  complete:()=>void;
};
export function mountHunt(host:HTMLElement,options:Options){
  host.innerHTML=`<div class="hunt-mission"><span class="hunt-phase">① 搜寻</span><span class="hunt-stage-trail">② 跟踪三次</span><span class="hunt-stage-found">③ 收集</span></div>
    <h2 class="guide-heading" tabindex="-1">这片星空里，谁偷偷动了？</h2><p class="guide-instruction">拖动地图找一找，放大后点一颗可疑的星。</p>
    <div class="hunt-map"><canvas class="hunt-canvas" tabindex="0" role="img" aria-label="可拖动缩放的真实星空地图。方向键移动视野，加减号缩放，空格换照片，回车标记视野中心。"></canvas>
      <div class="hunt-map-label"><span class="hunt-area"></span><span class="hunt-zoom"></span></div>
      <div class="hunt-zoom-controls"><button class="hunt-in" aria-label="放大地图">＋</button><button class="hunt-out" aria-label="缩小地图">−</button><button class="hunt-reset">全图</button></div>
      <div class="hunt-overview"><canvas class="hunt-mini" aria-label="全图导航，点击可移动视野"></canvas><span>全图 · 点这里跳转</span></div>
      <div class="hunt-map-prompt">拖动找星 · 滚轮 / ＋ 放大</div><div class="hunt-win" hidden>✦ 跟住它了！</div>
    </div>
    <div class="hunt-tools"><button class="hunt-play" aria-pressed="false">▶ 来回对比</button><div class="hunt-frames" aria-label="三次真实观测"></div>${options.changeNight?'<button class="hunt-night" role="switch" aria-label="红外夜视" aria-checked="false">☾ 打开夜视</button>':''}</div>
    <div class="hunt-bottom"><p class="hunt-date"></p><button class="hunt-retry" hidden>← 换一颗找找</button></div><p class="hunt-trail-note" aria-live="polite"></p>`;
  const $=<T extends HTMLElement=HTMLElement>(s:string)=>host.querySelector<T>(s)!;
  let observations=options.observations,night=options.night,frame=0,phase:'search'|'follow'|'found'='search',selected:Point|undefined;
  let world:Size={width:observations[0].set.frames.size[0],height:observations[0].set.frames.size[1]},screen:Size={width:800,height:500};
  let camera:Camera={x:world.width*(.35+Math.random()*.3),y:world.height*(.32+Math.random()*.36),zoom:1.6};
  let images:HTMLCanvasElement[]=[],marks:Footprint[]=[],rejected:Point[]=[],hints=0,active=options.active,disposed=false,busy=false,keyboard=false;
  let timer:ReturnType<typeof setInterval>|undefined;
  const canvas=$<HTMLCanvasElement>('.hunt-canvas'),mini=$<HTMLCanvasElement>('.hunt-mini');
  const reduced=()=>window.matchMedia?.('(prefers-reduced-motion: reduce)').matches??false;
  const expected=()=>observations.map(o=>({...positionPixel(expectedPosition(options.target,options.object,o.epoch.mjd_mid),{...o.set.frames,center:o.epoch.center||o.set.frames.center}),epoch:o.epoch.id}));
  const pause=()=>{if(timer)clearInterval(timer);timer=undefined};
  function prepare(){
    const limits=displayLimits(observations);images=observations.map(o=>{const c=document.createElement('canvas');drawObservation(c,o,limits);return c});
    world={width:images[0].width,height:images[0].height};
    $('.hunt-frames').innerHTML=observations.map((_,i)=>`<button data-frame="${i}" aria-pressed="${frame===i}"><span>${i+1}</span><small>照片 ${i+1}</small></button>`).join('');
    $('.hunt-area').textContent=`真实星空 · ${Math.round(world.width*observations[0].set.frames.pixel_arcsec/60)}′ 天区`;
  }
  function local(event:PointerEvent|WheelEvent){const r=canvas.getBoundingClientRect();return {x:event.clientX-r.left,y:event.clientY-r.top}}
  function inMap(p:Point){return p.x>=0&&p.y>=0&&p.x<world.width&&p.y<world.height}
  function hintBox(){
    return clueRegion(expected(),world,hints);
  }
  function resize(){
    const rect=canvas.getBoundingClientRect();if(!rect.width||!rect.height)return;
    screen={width:rect.width,height:rect.height};const dpr=Math.min(window.devicePixelRatio||1,2);
    canvas.width=Math.round(screen.width*dpr);canvas.height=Math.round(screen.height*dpr);camera=clampCamera(camera,world,screen);draw();
  }
  function draw(){
    if(disposed)return;const ctx=canvas.getContext('2d');if(!ctx)return;
    const sx=canvas.width/screen.width,sy=canvas.height/screen.height;ctx.setTransform(sx,0,0,sy,0,0);
    ctx.fillStyle='#e3e8e7';ctx.fillRect(0,0,screen.width,screen.height);
    const scale=mapScale(camera,world,screen),origin=toScreen({x:0,y:0},camera,world,screen);
    ctx.imageSmoothingEnabled=camera.zoom<3;ctx.drawImage(images[frame],origin.x,origin.y,world.width*scale,world.height*scale);
    // Overlays are navigation aids only; source pixels are never moved or invented.
    ctx.save();ctx.beginPath();ctx.rect(origin.x,origin.y,world.width*scale,world.height*scale);ctx.clip();
    ctx.lineWidth=1;ctx.strokeStyle='#a9b1b655';ctx.font='11px system-ui';ctx.fillStyle='#4f5867';
    for(let n=0;n<=4;n++){
      const x=origin.x+world.width*n/4*scale,y=origin.y+world.height*n/4*scale;
      ctx.beginPath();ctx.moveTo(x,origin.y);ctx.lineTo(x,origin.y+world.height*scale);ctx.stroke();ctx.beginPath();ctx.moveTo(origin.x,y);ctx.lineTo(origin.x+world.width*scale,y);ctx.stroke();
    }
    for(let y=0;y<4;y++)for(let x=0;x<4;x++){const p=toScreen({x:world.width*x/4,y:world.height*y/4},camera,world,screen);ctx.fillText(`${'ABCD'[x]}${y+1}`,p.x+8,p.y+18)}
    if(hints&&phase!=='found'){
      const box=hintBox(),p=toScreen(box,camera,world,screen);ctx.fillStyle='#a35f0012';ctx.fillRect(p.x,p.y,box.width*scale,box.height*scale);ctx.strokeStyle='#a35f00';ctx.setLineDash([8,6]);ctx.strokeRect(p.x,p.y,box.width*scale,box.height*scale);ctx.setLineDash([]);
    }
    const ring=(point:Point,colour:string,label:string)=>{const p=toScreen(point,camera,world,screen);ctx.strokeStyle=colour;ctx.lineWidth=2;ctx.beginPath();ctx.arc(p.x,p.y,14,0,Math.PI*2);ctx.stroke();ctx.fillStyle=colour;ctx.fillText(label,p.x+19,p.y-10)};
    rejected.forEach(p=>ring(p,'#78818f88','已试过'));
    marks.forEach((p,i)=>ring(p,phase==='found'?'#1f7a4d':'#2556c8',String(i+1)));
    if(selected)ring(selected,phase==='found'?'#1f7a4d':'#a35f00',phase==='search'?'可疑位置':phase==='found'?'跟踪成功':'待记录');
    if(keyboard){ctx.strokeStyle='#18202e';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(screen.width/2-8,screen.height/2);ctx.lineTo(screen.width/2+8,screen.height/2);ctx.moveTo(screen.width/2,screen.height/2-8);ctx.lineTo(screen.width/2,screen.height/2+8);ctx.stroke()}
    ctx.restore();
    $('.hunt-zoom').textContent=`${camera.zoom.toFixed(1)}×`;
    $('.hunt-date').textContent=`照片 ${frame+1} / ${observations.length} · ${dateLabel(observations[frame].epoch.mjd_mid)}${night&&options.changeNight?' · 夜视已开':''}`;
    host.querySelectorAll<HTMLButtonElement>('[data-frame]').forEach(b=>{const i=Number(b.dataset.frame);b.setAttribute('aria-pressed',String(frame===i));b.disabled=phase!=='search'||busy;b.classList.toggle('recorded',marks.some(m=>m.epoch===observations[i].epoch.id))});
    drawOverview();
  }
  function drawOverview(){
    mini.width=104;mini.height=104;const ctx=mini.getContext('2d');if(!ctx)return;ctx.drawImage(images[frame],0,0,104,104);
    if(hints){const b=hintBox();ctx.fillStyle='#a35f0033';ctx.fillRect(b.x/world.width*104,b.y/world.height*104,b.width/world.width*104,b.height/world.height*104)}
    const top=toWorld({x:0,y:0},camera,world,screen),bottom=toWorld({x:screen.width,y:screen.height},camera,world,screen);
    ctx.strokeStyle='#2556c8';ctx.lineWidth=2;ctx.strokeRect(Math.max(1,top.x/world.width*104),Math.max(1,top.y/world.height*104),Math.min(world.width,bottom.x-Math.max(0,top.x))/world.width*104-2,Math.min(world.height,bottom.y-Math.max(0,top.y))/world.height*104-2);
  }
  function controls(){
    const following=phase==='follow',found=phase==='found';
    $('.hunt-phase').classList.toggle('current',phase==='search');$('.hunt-stage-trail').classList.toggle('current',following);$('.hunt-stage-found').classList.toggle('current',found);
    $('.hunt-stage-trail').textContent=following?`② 跟踪 ${marks.length} / ${observations.length}`:'② 跟踪三次';
    $('.guide-heading').textContent=found?'三张照片，都跟住了。':following?`再找出照片 ${frame+1} 里的同一颗星。`:options.target.slug==='wise0855'?'打开夜视，搜出藏起来的星。':['rwaur','rdra'].includes(options.target.slug)?'这片星空里，谁在改变亮暗？':'这片星空里，谁偷偷动了？';
    $('.guide-instruction').textContent=found?'这次，是你从一整片星空里把它找了出来。':following?'点它现在的位置，留下一个脚印。':'拖动地图找一找，放大后点一颗可疑的星。';
    $('.hunt-win').hidden=!found;$('.hunt-retry').hidden=!following;
    $('.hunt-map-prompt').textContent=following?'点星星，留下位置 · 可拖动 / 放大':found?'✦ 已在三次观测中找回目标':'拖动找星 · 滚轮 / ＋ 放大';
    $('.hunt-trail-note').textContent=following?`${'● '.repeat(marks.length)}${'○ '.repeat(observations.length-marks.length)} 每张照片都要重新找到它。`:phase==='search'?'点中只是线索，跟住三张照片才算找到。':'';
    const play=$<HTMLButtonElement>('.hunt-play');play.disabled=phase!=='search'||busy;play.textContent=reduced()?'↔ 换一张':timer?'Ⅱ 暂停对比':'▶ 来回对比';play.setAttribute('aria-pressed',String(!!timer));
    const nightButton=$<HTMLButtonElement>('.hunt-night');if(nightButton){nightButton.disabled=phase!=='search'||busy;nightButton.setAttribute('aria-checked',String(night));nightButton.textContent=busy?'夜视切换中…':night?'☾ 夜视已开':'☾ 打开夜视'}
    options.help(hints<2?`✧ 给我一点线索${hints?'（再缩小范围）':''}`:'线索已缩到最小范围',hint,hints>=2||found||busy);
    if(found)options.action('收进我的星空 ✦',options.complete);
    else if(following)options.action(selected?(frame===observations.length-1?'核对三个脚印 →':'留下脚印，看下一张 →'):'点出这张照片里的同一颗星',record,!selected);
    else options.action(busy?'正在切换照片…':options.changeNight&&!night?'先打开夜视，再找星星':selected?'跟踪这颗星 →':'先在地图上选一颗可疑的星',follow,!selected||busy||!!options.changeNight&&!night);
  }
  function hint(){
    if(hints>=2||phase==='found'||busy)return;hints++;const b=hintBox();
    options.feedback(hints===1?`看看 ${b.name} 附近的黄色区域。试着放大、来回对比。`:'范围再小一点了。黄色方框里找找看，仍然需要跟住三张照片。');
    if(hints===2)camera=clampCamera({x:b.x+b.width/2,y:b.y+b.height/2,zoom:4},world,screen);
    controls();draw();$('.guide-heading').scrollIntoView({block:'start'});
  }
  function pick(point:Point){
    if(phase==='found'||busy)return;pause();if(!inMap(point)){options.feedback('这里超出了照片边界，往有星星的地方拖一拖。');controls();return}
    selected=point;keyboard=false;controls();draw();
    options.feedback(phase==='search'?'记下这个可疑位置了。跟踪它，看看是不是同一颗。':'位置已标记。点下面的按钮，留下这个脚印。');
  }
  function follow(){
    if(!selected||busy||options.changeNight&&!night)return;pause();camera=clampCamera({...selected,zoom:Math.max(4,camera.zoom)},world,screen);phase='follow';frame=0;marks=[];selected=undefined;options.feedback('照片换回最早的一张。在这片区域里重新找到它。');controls();draw();$('.guide-heading').scrollIntoView({block:'start'});
  }
  function record(){
    if(!selected||phase!=='follow')return;
    marks.push({...selected,epoch:observations[frame].epoch.id});
    if(frame<observations.length-1){frame++;selected=undefined;options.feedback('脚印留下了。时间往后走，再点出同一颗星。');controls();draw();$('.guide-heading').scrollIntoView({block:'start'});return}
    const radius=18.45/observations[0].set.frames.pixel_arcsec;
    if(verifyTrail(marks,expected(),radius)){phase='found';options.feedback('✦ 搜寻完成 · 三个脚印都对上了');controls();draw()}
    else{rejected.push(marks[0]);rejected=rejected.slice(-8);backToSearch();options.feedback('这条线索还没对上。回到大地图，换一颗试试；也可以要一点线索。')}
  }
  function backToSearch(){pause();phase='search';marks=[];selected=undefined;camera=clampCamera({...camera,zoom:1.6},world,screen);controls();draw()}
  function zoom(factor:number,anchor:Point={x:screen.width/2,y:screen.height/2}){camera=zoomAt(camera,camera.zoom*factor,anchor,world,screen);draw()}
  $('.hunt-in').onclick=()=>zoom(1.5);$('.hunt-out').onclick=()=>zoom(1/1.5);
  $('.hunt-reset').onclick=()=>{camera={x:world.width/2,y:world.height/2,zoom:1};draw()};
  $('.hunt-retry').onclick=()=>{backToSearch();options.feedback('回到地图了，换一颗继续找。')};
  $('.hunt-play').onclick=()=>{
    if(phase!=='search'||busy)return;
    if(reduced()){frame=(frame+1)%observations.length;draw();return}
    if(timer)pause();else if(active)timer=setInterval(()=>{frame=(frame+1)%observations.length;draw()},1050);
    controls();
  };
  $('.hunt-frames').onclick=event=>{const button=(event.target as HTMLElement).closest<HTMLButtonElement>('[data-frame]');if(!button||phase!=='search'||busy)return;pause();frame=Number(button.dataset.frame);controls();draw()};
  if(options.changeNight)$('.hunt-night').onclick=async()=>{
    if(busy||phase!=='search')return;pause();busy=true;controls();
    try{const next=await options.changeNight!(!night);if(disposed)return;observations=next;night=!night;selected=undefined;rejected=[];marks=[];frame=0;prepare();camera=clampCamera(camera,world,screen);options.feedback(night?'夜视打开了。继续在地图里找可疑的星。':'夜视已关闭，仍在同一片天空。')}
    catch(error){if(!disposed)options.feedback(`暂时没打开夜视，再试一次。${(error as Error).message}`)}
    finally{if(!disposed){busy=false;controls();draw()}}
  };
  const pointers=new Map<number,Point>();let start:Point|undefined,last:Point|undefined,dragged=false,pinched=false,pinchDistance=0;
  canvas.onpointerdown=e=>{
    if(e.button!==0)return;canvas.focus({preventScroll:true});canvas.setPointerCapture(e.pointerId);const p=local(e);pointers.set(e.pointerId,p);start=p;last=p;dragged=false;keyboard=false;
    if(pointers.size===2){pinched=true;const a=[...pointers.values()];pinchDistance=Math.hypot(a[0].x-a[1].x,a[0].y-a[1].y)}
  };
  canvas.onpointermove=e=>{
    if(!pointers.has(e.pointerId)||!last)return;const p=local(e);pointers.set(e.pointerId,p);
    if(pointers.size===2){const a=[...pointers.values()],distance=Math.hypot(a[0].x-a[1].x,a[0].y-a[1].y);if(pinchDistance>0)zoom(distance/pinchDistance,{x:(a[0].x+a[1].x)/2,y:(a[0].y+a[1].y)/2});pinchDistance=distance;return}
    if(pinched)return;if(start&&Math.hypot(p.x-start.x,p.y-start.y)>6)dragged=true;
    if(dragged){const s=mapScale(camera,world,screen);camera=clampCamera({...camera,x:camera.x-(p.x-last.x)/s,y:camera.y-(p.y-last.y)/s},world,screen);draw()}
    last=p;
  };
  canvas.onpointerup=e=>{const p=local(e),wasPinched=pinched;pointers.delete(e.pointerId);if(canvas.hasPointerCapture(e.pointerId))canvas.releasePointerCapture(e.pointerId);if(!dragged&&!wasPinched)pick(toWorld(p,camera,world,screen));if(!pointers.size){start=undefined;last=undefined;pinched=false;dragged=false}};
  canvas.onpointercancel=e=>{pointers.delete(e.pointerId);start=undefined;last=undefined;pinched=false;dragged=false};
  canvas.addEventListener('wheel',e=>{e.preventDefault();zoom(Math.exp(-e.deltaY*.002),local(e))},{passive:false});
  canvas.onkeydown=e=>{
    if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','+','=','-','Enter',' '].includes(e.key))return;e.preventDefault();
    if(e.key==='Enter'){pick({x:camera.x,y:camera.y});return}
    if(e.key==='+'||e.key==='='){zoom(1.5);return}if(e.key==='-'){zoom(1/1.5);return}
    if(e.key===' '){if(phase==='search'&&!busy){pause();frame=(frame+1)%observations.length;controls();draw()}return}
    keyboard=true;const delta=world.width/camera.zoom*.08;
    camera=clampCamera({...camera,x:camera.x+(e.key==='ArrowLeft'?-delta:e.key==='ArrowRight'?delta:0),y:camera.y+(e.key==='ArrowUp'?-delta:e.key==='ArrowDown'?delta:0)},world,screen);draw();
  };
  mini.onpointerdown=e=>{const r=mini.getBoundingClientRect();camera=clampCamera({...camera,x:(e.clientX-r.left)/r.width*world.width,y:(e.clientY-r.top)/r.height*world.height},world,screen);draw()};
  prepare();controls();const observer=new ResizeObserver(resize);observer.observe(canvas);resize();
  return {setActive(value:boolean){active=value;if(!value){pause();controls()}else resize()},dispose(){disposed=true;pause();observer.disconnect();pointers.clear()}};
}
