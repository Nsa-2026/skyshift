import {decodeFrame, filterEpochs, combineFrames, limits, epochDate, staticFile} from './model.mjs';
const $ = id => document.getElementById(id);
const targets = [
  {name:"Barnard's Star", slug:'barnard', ra:269.4462195, dec:4.7688342},
  {name:'WISE 0855−0714', slug:'wise0855', ra:133.7599, dec:-7.2422},
  {name:'Epsilon Indi B', slug:'epsindib', ra:331.0967, dec:-56.8013},
  {name:'RW Aurigae', slug:'rwaur', ra:76.9564, dec:30.4011},
  {name:'R Draconis', slug:'rdra', ra:248.1674, dec:66.7550},
];
const state = {clock:'months', mode:'blink', invert:false, frames:[], epochs:[], meta:null, object:null,
  index:0, timer:null, requests:[], currentLog:null, generation:0, controller:null, bounds:[0,1], pixels:null, spectrum:[]};
for (const t of targets) {
  const b = document.createElement('button'); b.className = 'target'; b.textContent = t.name;
  b.type = 'button'; b.addEventListener('click', () => {$('query-input').value=t.name; loadObservation();});
  $('targets').append(b);
}
function notice(text, kind='') { $('notice').textContent=text; $('notice').className='notice '+kind; }
function fmt(x, digits=2) { return Number.isFinite(x) ? x.toLocaleString('en-US',{maximumFractionDigits:digits}) : '—'; }
function params(obj) { return new URLSearchParams(Object.entries(obj).filter(([,v]) => v !== undefined && v !== null)).toString(); }
function showLog(log) {
  state.currentLog=log; $('response-title').textContent = `${log.method} ${log.path} · ${log.status} · ${Math.round(log.ms)} ms`;
  $('json').textContent=JSON.stringify(log.data,null,2);
  for (const b of $('requests').children) b.classList.toggle('active', b.dataset.id===String(log.id));
}
function addLog(log) {
  log.id=state.requests.length ? state.requests[0].id+1 : 1;
  state.requests.unshift(log); state.requests=state.requests.slice(0,100);
  $('requests').replaceChildren();
  for (const row of state.requests) {
    const b=document.createElement('button'); b.type='button'; b.className='request-row'+(row.ok?'':' bad'); b.dataset.id=row.id;
    for (const [cls,txt] of [['status',row.status],['path',row.path],['ms',`${Math.round(row.ms)} ms`]]) {
      const s=document.createElement('span'); s.className=cls; s.textContent=txt; b.append(s);
    }
    b.title=row.url; b.addEventListener('click',()=>showLog(row)); $('requests').append(b);
  }
  showLog(log);
}
async function request(path, {binary=false,signal,expected}={}) {
  const url=new URL(path,location.href); if (!['http:','https:','data:'].includes(url.protocol)) throw Error('Unsupported data URL protocol');
  const started=performance.now(); let response, data;
  try {
    response=await fetch(url,{signal});
    if (binary && response.ok) data=await response.arrayBuffer();
    else {const raw=await response.text();try{data=JSON.parse(raw);}catch{data={message:raw.slice(0,2000)};}}
    const ok=expected ? response.status===expected : response.ok;
    addLog({method:'GET',url:url.href,path:url.protocol==='data:'?'inline frame':url.pathname+url.search,status:response.status,ms:performance.now()-started,ok,
      data:data instanceof ArrayBuffer?{binary:true,bytes:data.byteLength,encoding:'little-endian uint16'}:data});
    if (!ok) throw Object.assign(Error(data?.error?.message||data?.message||`HTTP ${response.status}`),{logged:true});
    return {data,url:response.url||url.href,status:response.status};
  } catch(e) {
    if (!e.logged) addLog({method:'GET',url:url.href,path:url.pathname+url.search,status:e.name==='AbortError'?'ABORT':'ERR',ms:performance.now()-started,ok:false,data:{error:e.message}});
    throw e;
  }
}
async function health() {
  try {
    const {data}=await request('/api/health'); if(data.status!=='ok') throw Error('Unexpected health response');
    $('health-dot').className='dot ok'; $('health-label').textContent=`Backend online · v${data.version||'?'}`;
  } catch { $('health-dot').className='dot bad'; $('health-label').textContent='Backend offline'; }
}
function setClock(clock) {
  state.clock=clock; for(const c of ['months','hours']) $(c).setAttribute('aria-pressed',String(c===clock));
  $('fov').replaceChildren(...(clock==='months'?[5,15]:[20,60]).map(v=>new Option(`${v} arcmin`,v)));
  if(state.frames.length) notice('Clock changed. Load observation to request this configuration.');
}
function stop() { clearInterval(state.timer);state.timer=null;$('play').textContent='▶';$('play').setAttribute('aria-pressed','false'); }
function play() {
  if(state.timer) return stop(); if(!state.frames.length) return;
  state.timer=setInterval(()=>step(1),Number($('speed').value));$('play').textContent='Ⅱ';$('play').setAttribute('aria-pressed','true');
}
function step(by) { if(!state.frames.length)return;state.index=(state.index+by+state.frames.length)%state.frames.length;draw(); }
async function position(source, signal) {
  const q=$('query-input').value.trim();
  const t=targets.find(t=>t.name.toLowerCase()===q.toLowerCase()||t.slug===q.toLowerCase());
  if(source!=='api'&&t) return t;
  return (await request('/api/resolve?'+params({q}),{signal})).data;
}
function resetObject() {
  state.object=null;state.spectrum=[];$('motion').replaceChildren(document.createTextNode('—'),Object.assign(document.createElement('small'),{textContent:' arcsec / year'}));
  $('motion-note').textContent='Object measurements have not been loaded.';$('predicted').textContent='—';$('references').textContent='—';
  $('spectrum-count').textContent='No measurements';$('spectrum-empty').hidden=false;plotSpectrum();
}
async function loadObservation() {
  const generation=++state.generation;state.controller?.abort(); const controller=new AbortController();state.controller=controller;
  const signal=controller.signal;stop();$('load').disabled=true;
  // Never leave an old field or old object visible while requesting a new one.
  state.frames=[];state.epochs=[];state.meta=null;state.pixels=null;resetObject();
  $('sky').getContext('2d').clearRect(0,0,$('sky').width,$('sky').height);$('empty').hidden=false;
  $('provenance').textContent='LOADING';$('provenance').className='tag';$('frame-counter').textContent='00 / 00';
  for(const id of ['epoch-stat','det-stat','pixel-stat','release-stat','coords']) $(id).textContent='—';
  $('field-name').textContent='Loading observation';$('frame-date').textContent='Waiting for frames';
  notice('Resolving target and loading frames…');
  const source=$('source').value, clock=state.clock, fov=Number($('fov').value), det=Number($('det').value);
  try {
    let pos, result, objectPath, provenance;
    if(source==='fixture') {
      const months=clock==='months';
      result=await request(`/fixtures/frames_${months?'barnard':'comet'}.json`,{signal});
      pos={...result.data.center,name:months?'Synthetic Barnard-like field':'Synthetic comet field'};
      objectPath=months?'/fixtures/object_barnard.json':null;provenance='FIXTURE';
    } else {
      pos=await position(source,signal);
      if(!Number.isFinite(pos.ra)||!Number.isFinite(pos.dec)) throw Error('Resolve response is missing numeric ra / dec');
      if(source==='static' && pos.slug) {
        const root=`/data/${encodeURIComponent(pos.slug)}/`;
        try {
          const index=await request(root+'index.json',{signal});
          result=await request(new URL(staticFile(index.data,clock,fov),new URL(root,location.href)).href,{signal});
          objectPath=root+'object.json';provenance='STATIC';
        } catch(e) {
          if(signal.aborted) throw e;
          notice('Static index unavailable; checking the standard frames.json before API fallback…');
          try {
            result=await request(root+'frames.json',{signal});
            if(result.data.clock!==clock) throw Error('Static clock differs from requested clock');
            objectPath=root+'object.json';provenance='STATIC';
          } catch(e) {if(signal.aborted)throw e;result=null;}
        }
      }
      if(!result) {
        result=await request('/api/frames?'+params({ra:pos.ra,dec:pos.dec,fov,clock,det}),{signal});
        objectPath='/api/object?'+params({ra:pos.ra,dec:pos.dec});provenance='API';
      }
    }
    const meta=result.data;
    let chosen=det, epochs=filterEpochs(meta,chosen);
    if(!epochs.length && (meta.synthetic||source==='fixture')) {
      chosen=meta.epochs[0]?.det;epochs=filterEpochs(meta,chosen);$('det').value=String(chosen);
    }
    if(!epochs.length) throw Error(`No epochs for D${chosen}. Choose another detector or use Live API.`);
    const buffers=[];
    // Bound concurrency; do not request every Hours exposure simultaneously.
    let cursor=0;
    await Promise.all(Array.from({length:Math.min(4,epochs.length)},async()=>{
      for(;;){const i=cursor++;if(i>=epochs.length)break;
        const e=epochs[i];if(!e.data_url)throw Error('Epoch is missing data_url');
        const blob=await request(new URL(e.data_url,result.url).href,{binary:true,signal});
        buffers[i]=decodeFrame(blob.data,meta.size,e.scale,e.zero);
      }
    }));
    if(generation!==state.generation)return;
    state.meta=meta;state.epochs=epochs;state.frames=buffers;state.index=0;state.bounds=limits(buffers);state.mode='blink';
    for(const b of document.querySelectorAll('[data-mode]'))b.setAttribute('aria-pressed',String(b.dataset.mode==='blink'));
    $('timeline').max=epochs.length-1;$('timeline').value=0;$('empty').hidden=true;
    const synthetic=meta.synthetic===true||source==='fixture';
    $('provenance').textContent=synthetic?'SYNTHETIC · NOT REAL DATA':provenance+' · SPHEREx';$('provenance').className='tag'+(synthetic?' synthetic':'');
    $('field-name').textContent=synthetic?(meta.clock==='months'?'Synthetic Barnard-like field':'Synthetic comet field'):(pos.name||$('query-input').value);
    $('coords').textContent=`${fmt(meta.center.ra,6)} / ${fmt(meta.center.dec,6)}`;
    $('epoch-stat').textContent=epochs.length;$('det-stat').textContent=`D${chosen}`;$('pixel-stat').textContent=fmt(meta.pixel_arcsec)+'″';
    $('release-stat').textContent=meta.data_release||'not reported';$('sampling').textContent=`${fmt(meta.pixel_arcsec)} arcsec / px · ${meta.size.join(' × ')}`;
    const mismatch=Number.isFinite(pos.ra)&&Math.abs(meta.center.ra-pos.ra)>.01 || Number.isFinite(pos.dec)&&Math.abs(meta.center.dec-pos.dec)>.01;
    notice(synthetic?`${provenance}: simulated pixels and measurements, not a real observation of your requested target. The backend shell may return fixtures until its engine is merged.`:
      `${provenance}: ${epochs.length} epochs preloaded · D${chosen} · ${meta.clock} clock · archive checked ${meta.archive_checked||'not reported'}${mismatch?' · Returned field differs from requested coordinates.':''}`,synthetic||mismatch?'warn':'');
    draw();
    if($('with-object').checked && objectPath) {
      try {
        const obj=await request(objectPath,{signal});if(generation!==state.generation)return;
        state.object=obj.data;renderObject(obj.data);
      } catch(e) {if(signal.aborted)return;$('motion-note').textContent='Object request failed: '+e.message;notice('Frames loaded. Object / spectrum request failed: '+e.message,'warn');}
    }
    if(generation===state.generation){play();const frameLog=state.requests.find(r=>r.url===result.url);if(frameLog)showLog(frameLog);}
    document.querySelectorAll('.target').forEach(b=>b.classList.toggle('selected',b.textContent===$('query-input').value));
  } catch(e) {
    if(generation!==state.generation)return;controller.abort();
    $('provenance').textContent='LOAD FAILED';$('field-name').textContent='No observation loaded';
    notice(e.message,'error');
  } finally {if(generation===state.generation)$('load').disabled=false;}
}
function draw() {
  if(!state.frames.length)return;
  const values=combineFrames(state.frames,state.index,state.mode);state.pixels=values;
  const [nx,ny]=state.meta.size,c=$('sky');c.width=nx;c.height=ny;
  const ctx=c.getContext('2d'),image=ctx.createImageData(nx,ny);
  const [lo,hi]=state.mode==='blink'?state.bounds:limits([values],state.mode);
  const gain=Number($('stretch').value),den=Math.asinh(gain);
  for(let i=0;i<values.length;i++){
    let r=5,g=12,b=14;
    if(Number.isFinite(values[i])) {
      if(state.mode==='diff'){
        const v=Math.max(-1,Math.min(1,values[i]/Math.max(hi,1e-10))),a=Math.asinh(Math.abs(v)*gain)/den;
        r=Math.round(18+a*(v<0?92:230));g=Math.round(27+a*(v<0?175:160));b=Math.round(29+a*(v<0?226:95));
      }else{
        const v=Math.max(0,Math.min(1,(values[i]-lo)/(hi-lo))),s=Math.asinh(v*gain)/den;
        r=Math.round(6+s*233);g=Math.round(12+s*235);b=Math.round(15+s*224);
      }
      if(state.invert){r=255-r;g=255-g;b=255-b;}
    }
    const j=i*4;image.data[j]=r;image.data[j+1]=g;image.data[j+2]=b;image.data[j+3]=255;
  }
  ctx.putImageData(image,0,0);const e=state.epochs[state.index];
  $('frame-date').textContent=state.mode==='sum'?'Sum of all loaded epochs':epochDate(e.mjd_mid);
  $('frame-counter').textContent=`${String(state.index+1).padStart(2,'0')} / ${String(state.frames.length).padStart(2,'0')}`;
  $('timeline').value=state.index;
  $('frame-quality').textContent=`${e.id} · ${e.n_exposures} exposure(s) · masked ${fmt((e.flag_frac??0)*100,1)}% · saturated ${fmt((e.sat_frac??0)*100,1)}%`;
}
function renderObject(obj) {
  const m=obj.measure;
  $('motion').replaceChildren(document.createTextNode(fmt(m?.rate_arcsec_yr)),Object.assign(document.createElement('small'),{textContent:' arcsec / year'}));
  $('motion-note').textContent=(obj.synthetic?'SIMULATED measurement. ':'')+(m?`Shift ${fmt(m.shift_arcsec)}″ over ${fmt(m.dt_yr,3)} years · σ ${fmt(m.sigma_arcsec)}″`:'No motion measurement (may have fewer than two usable visits).');
  $('predicted').textContent=m?.predicted_arcsec!=null?fmt(m.predicted_arcsec)+'″':'—';$('references').textContent=m?.n_ref_stars??'—';
  state.spectrum=Array.isArray(obj.spectrum)?obj.spectrum:[];
  $('spectrum-count').textContent=`${state.spectrum.length} samples${obj.synthetic?' · SYNTHETIC':''}`;
  $('spectrum-empty').hidden=state.spectrum.some(p=>Number.isFinite(p.um)&&Number.isFinite(p.flux_ujy));plotSpectrum();
}
function plotSpectrum() {
  const c=$('spectrum-chart'),dpr=devicePixelRatio||1,w=c.clientWidth||1000,h=240;
  c.width=w*dpr;c.height=h*dpr;const ctx=c.getContext('2d');ctx.scale(dpr,dpr);ctx.clearRect(0,0,w,h);
  const points=state.spectrum.filter(p=>Number.isFinite(p.um)&&Number.isFinite(p.flux_ujy));
  if(!points.length)return;
  const left=68,right=24,top=20,bottom=38,min=Math.min(0,...points.map(p=>p.flux_ujy)),max=Math.max(...points.map(p=>p.flux_ujy))||1;
  const x=um=>left+(um-.7)/4.5*(w-left-right),y=f=>h-bottom-(f-min)/(max-min||1)*(h-top-bottom);
  ctx.font='9px monospace';ctx.textBaseline='middle';
  for(let i=0;i<=4;i++){
    const v=min+(max-min)*i/4,yy=y(v);ctx.strokeStyle='#26372c';ctx.lineWidth=.6;ctx.beginPath();ctx.moveTo(left,yy);ctx.lineTo(w-right,yy);ctx.stroke();
    ctx.fillStyle='#789581';ctx.textAlign='right';ctx.fillText(Math.abs(v)>999?Number(v).toExponential(1):fmt(v),left-10,yy);
  }
  ctx.textAlign='center';for(let um=1;um<=5;um++)ctx.fillText(um+' µm',x(um),h-15);
  for(const [um,label] of [[1.4,'H₂O'],[2.3,'CH₄'],[3.3,'CH₄'],[4.3,'CO₂'],[4.7,'CO']]){
    ctx.fillStyle='#718a78';ctx.fillText(label,x(um),top-7);ctx.setLineDash([2,5]);ctx.strokeStyle='#324638';ctx.beginPath();ctx.moveTo(x(um),top+5);ctx.lineTo(x(um),h-bottom);ctx.stroke();ctx.setLineDash([]);
  }
  for(const p of points){const color=['#a6e8be','#e5c58e','#9eabed'][(Math.max(1,p.visit||1)-1)%3];
    ctx.strokeStyle=color;ctx.fillStyle=color;ctx.globalAlpha=.8;
    if(Number.isFinite(p.err_ujy)&&p.err_ujy>0){ctx.beginPath();ctx.moveTo(x(p.um),y(p.flux_ujy-p.err_ujy));ctx.lineTo(x(p.um),y(p.flux_ujy+p.err_ujy));ctx.stroke();}
    ctx.beginPath();ctx.arc(x(p.um),y(p.flux_ujy),2.7,0,Math.PI*2);p.saturated?ctx.stroke():ctx.fill();
  }ctx.globalAlpha=1;
}
async function endpointRequest() {
  $('send').disabled=true;
  try {
    const endpoint=$('endpoint').value;
    let p={};if(endpoint==='resolve')p={q:$('query-input').value};
    if(!['health','resolve'].includes(endpoint)) {
      const pos=await position($('source').value);p={ra:pos.ra,dec:pos.dec};
      if(endpoint==='frames')Object.assign(p,{clock:state.clock,fov:$('fov').value,det:$('det').value});
      if(['gaia','known'].includes(endpoint))p.r=$('radius').value;
      if(endpoint==='known')p.mjd=$('mjd').value;
    }
    await request('/api/'+endpoint+(Object.keys(p).length?'?'+params(p):''));notice(`GET /api/${endpoint} completed. Inspect the response below.`);
  }catch(e){notice(e.message,'error');}finally{$('send').disabled=false;}
}
$('query').addEventListener('submit',e=>{e.preventDefault();loadObservation();});
for(const c of ['months','hours'])$(c).addEventListener('click',()=>setClock(c));
$('health').onclick=health;$('play').onclick=play;$('previous').onclick=()=>{stop();step(-1);};$('next').onclick=()=>{stop();step(1);};
$('timeline').oninput=()=>{stop();state.index=Number($('timeline').value);draw();};
$('stretch').oninput=draw;$('speed').oninput=()=>{if(state.timer){stop();play();}};
$('invert').onclick=()=>{state.invert=!state.invert;$('invert').setAttribute('aria-pressed',String(state.invert));draw();};
for(const b of document.querySelectorAll('[data-mode]'))b.onclick=()=>{state.mode=b.dataset.mode;for(const t of document.querySelectorAll('[data-mode]'))t.setAttribute('aria-pressed',String(t===b));draw();};
$('sky').addEventListener('mousemove',e=>{
  if(!state.meta||!state.pixels)return;
  const rect=e.currentTarget.getBoundingClientRect(),[nx,ny]=state.meta.size,s=Math.min(rect.width/nx,rect.height/ny),ox=(rect.width-nx*s)/2,oy=(rect.height-ny*s)/2;
  const x=Math.floor((e.clientX-rect.left-ox)/s),y=Math.floor((e.clientY-rect.top-oy)/s);
  const v=state.pixels[y*nx+x];$('pixel-readout').textContent=x>=0&&x<nx&&y>=0&&y<ny?`x ${x} · y ${y} · ${Number.isFinite(v)?fmt(v,5)+' MJy/sr':'masked / no data'}`:'Outside frame';
});
$('send').onclick=endpointRequest;
$('smoke').onclick=async()=>{
  $('smoke').disabled=true;let pass=0;
  for(const [url,expected] of [['/api/health',200],['/api/resolve?q=Barnard',200],['/api/frames?ra=400&dec=0',400]]){
    try{await request(url,{expected});pass++;}catch{};
  }
  notice(`${pass}/3 API checks passed: health, built-in resolve, invalid-coordinate rejection. Frame science is not validated by these checks.`,pass===3?'':'warn');$('smoke').disabled=false;
};
$('download').onclick=()=>{
  const blob=new Blob([JSON.stringify({exported:new Date().toISOString(),frames:state.meta,object:state.object,requests:state.requests},null,2)],{type:'application/json'});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='skyshift-playground-session.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);
};
$('copy').onclick=async()=>{try{await navigator.clipboard.writeText($('json').textContent);$('copy').textContent='Copied';setTimeout(()=>$('copy').textContent='Copy JSON',1400);}catch{notice('Clipboard unavailable. Select the response text or export the session.','warn');}};
window.addEventListener('resize',plotSpectrum);document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();});
try{const config=(await request('/_config')).data;$('origin').textContent=config.api_origin;if(config.static_ref!=='working tree')$('source').value='static';}catch{}
await health();
if($('health-dot').classList.contains('bad')){$('source').value='fixture';notice('Backend offline. Loading clearly labelled fixtures so you can explore the player.','warn');}
await loadObservation();
