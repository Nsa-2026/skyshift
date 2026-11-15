import './workbench.css';
import type {Epoch, FramesResponse, ObjectResponse} from '../api/types';
import type {BlinkPresentation, CardHandle, View} from '../app/modules';
import {ApiFailure} from '../api/client';
import {json, loadAnalysis, hasScience, type Target, type Candidate} from '../api/science-client';
import {mountScience} from '../card/science';
import {mountComparison} from './comparison';
import {renderFindings, renderCalibration, renderFrameQuality} from './findings';
import {MODES, workspaceHref, workspaceState, type WorkspaceState} from './workspace-state';
import {targetBySlug} from '../app/state';

export type Layer = 'raw' | 'model' | 'resid' | 'refdiff';
export const targetFor = (view: View): Target => ({
  slug:view.slug??'', name:view.name??'Selected sky', ra:view.ra, dec:view.dec,
  default_det:view.det, clock:view.clock, fov:view.fov, n_frames:0, n_visits:0,
});

/** Where the derived images (sky model, residual, DR1 difference) exist: the five featured stars'
 *  prepared fields, on the Months clock at 5′. Elsewhere /api/frames?view= answers 404, so the
 *  options are greyed out with the reason instead of failing and snapping back. */
export function layerAvailability(view:Pick<View,'slug'|'clock'|'fov'>):{ok:boolean;reason:string} {
  if(!hasScience(view.slug))return {ok:false,reason:"Sky model, residual and DR1 difference exist only for the five featured stars: Barnard's Star, Eps Indi B, WISE 0855, RW Aur and R Dra."};
  if(view.clock!=='months'||view.fov!==5)return {ok:false,reason:'These images are made on the Months clock at 5′. Switch the player to Months and 5′ to use them.'};
  return {ok:true,reason:''};
}

export function mountWorkbench(host:HTMLElement, opts:{
  card:CardHandle; cardElement:HTMLElement;
  layer(layer:Layer):void; locate(candidate:Candidate):void;
  reference(points:{um:number;flux_ujy:number}[]):void; rebuilt(view:View):void;
}) {
  host.innerHTML=`<p class="wb-context"></p>
    <div class="wb-quality" aria-label="Current observation quality"></div>
    <nav class="wb-tabs" aria-label="Evidence sections" hidden></nav>
    <div class="wb-dashboard-controls" hidden><p>Open several dashboards to view them together.</p><button class="btn btn-ghost" type="button" data-open-all>Open all</button><button class="btn btn-ghost" type="button" data-close-all>Close all</button></div>
    <p class="wb-status" role="status" aria-live="polite"></p>
    <p class="wb-empty" hidden>No dashboards open. Choose one or more above.</p>
    <div class="wb-dashboards">
    <div class="wb-card-slot"></div>
    <div class="wb-guide" data-wb="explore">
      <a class="wb-learn" href="#/learn">New to blinking? Start with a lesson.</a>
    </div>
    <section data-wb="signals" hidden><h2>Signals to check</h2><div class="wb-findings-body"></div>
      <p class="source">Background frequencies rank follow-up checks; they are not probabilities that an object is real. Cold-source false alarms remain uncalibrated.</p>
    </section>
    <section data-wb="compare" hidden><details class="wb-comparison"></details></section>
    <section data-wb="data" hidden><h2>Current observation</h2><p class="source">Player metadata below describe the selected image. The analysis observations have their own footprint and quality information.</p><div class="button-row wb-products"><a class="wb-science-json" target="_blank" rel="noopener">Scientific product JSON ↗</a><a class="wb-analysis-json" target="_blank" rel="noopener">Detector diagnostics JSON ↗</a></div><details class="wb-source-details"><summary>Image metadata and provenance</summary><pre class="wb-metadata"></pre></details></section>
    <section data-wb="processing" hidden><h2>Image processing</h2>
      <label class="wb-source-label">Image source <select class="input wb-layer" aria-label="Image source">
        <option value="raw">SPHEREx observations</option><option value="model">Static sky model</option>
        <option value="resid">Model residual</option><option value="refdiff">DR1 reference difference</option>
      </select></label>
      <p class="wb-layer-note" role="status"></p>
      <p class="source">Published models, residuals and DR1 differences use the analysis cube. They currently support the five featured science targets at 5′ on the Months clock; their measured footprint can be smaller. Original object measurements remain separate.</p>
      <p class="wb-capability" role="status"></p><button class="btn wb-build" type="button" hidden>Build an analysis for this sky</button>
    </section>
    <section data-wb="validation" hidden><h2>Validation</h2><div class="wb-calibration-body"></div></section>
    <div class="wb-science"></div></div>`;
  const $=<T extends HTMLElement=HTMLElement>(s:string)=>host.querySelector<T>(s)!;
  $('.wb-card-slot').append(opts.cardElement);
  const layer=$<HTMLSelectElement>('.wb-layer'), build=$<HTMLButtonElement>('.wb-build');
  const science=mountScience($('.wb-science'),opts.reference,()=>object?.spectrum??[]);
  const comparison=mountComparison($('.wb-comparison'));
  for(const [key,label] of MODES.analyze.tabs){
    const panel=host.querySelector<HTMLElement>(`section[data-wb="${key}"], section[data-science="${key}"]`);
    if(!panel)continue;
    panel.classList.add('wb-dashboard');
    const body=document.createElement('div');body.className='wb-dashboard-body';
    body.append(...panel.childNodes);panel.append(body);
    const header=document.createElement('header');header.className='wb-dashboard-head';
    header.innerHTML=`<h2>${label}</h2><button class="wb-dashboard-close" type="button" data-close-dashboard="${key}" aria-label="Close ${label} dashboard">×</button>`;
    panel.prepend(header);
  }
  let state:WorkspaceState={mode:'explore',tab:''}, view:View|undefined;
  let object:ObjectResponse|null=null, epoch:Epoch|undefined;
  let presentation:BlinkPresentation|undefined;
  let frameSource:{frames:FramesResponse;url:string}|null=null;
  let controller=new AbortController(), jobController=new AbortController();
  let scienceLoaded=false, findingsLoaded=false, calibrated=false, visible=true, capabilitiesLoaded=false;
  const panels=()=>state.mode==='explore'?['explore']:state.mode==='analyze'?(state.panels??[state.tab]):[state.tab];
  function choosePanels(keys:string[]){
    show(workspaceState(`analyze/${keys.join('+')||'dashboards'}`));
    location.hash=workspaceHref('analyze',keys.join('+')||'dashboards');
  }
  host.addEventListener('click',event=>{
    const button=(event.target as Element).closest<HTMLButtonElement>('[data-toggle-dashboard],[data-close-dashboard],[data-open-all],[data-close-all]');
    if(!button||state.mode!=='analyze')return;
    const key=button.dataset.toggleDashboard??button.dataset.closeDashboard;
    const keys=panels(), all=MODES.analyze.tabs.map(([key])=>key);
    if(button.hasAttribute('data-open-all'))choosePanels(all);
    else if(button.hasAttribute('data-close-all'))choosePanels([]);
    else choosePanels(all.filter(k=>k===key?!keys.includes(k):keys.includes(k)));
    const selector=key?`[data-toggle-dashboard="${key}"]`:button.hasAttribute('data-open-all')?'[data-open-all]':'[data-close-all]';
    host.querySelector<HTMLButtonElement>(selector)?.focus({preventScroll:true});
  });

  async function capabilities(){
    if(capabilitiesLoaded)return; capabilitiesLoaded=true;
    try {
      const value=await json<{analysis_jobs:boolean}>('/api/science/capabilities');
      build.hidden=!value.analysis_jobs; science.capabilities(value);
      $('.wb-capability').textContent=value.analysis_jobs?'Local analysis engine is available. A new field may take several minutes.':'Published products are available. New-field computation is not enabled on this server.';
    } catch {
      $('.wb-capability').textContent='Analysis engine status is unavailable. Published products can still be inspected.';
    }
  }
  async function findings(){
    if(!view||findingsLoaded)return; findingsLoaded=true;
    const signal=controller.signal, selected=view;
    $('.wb-findings-body').textContent='Loading the analysis…';
    try {
      const result=await loadAnalysis(targetFor(selected),selected.det,signal);
      if(signal.aborted)return;
      renderFindings($('.wb-findings-body'),result,opts.locate);
      host.querySelectorAll('.candidate button').forEach(b=>b.classList.add('btn'));
    } catch(error) {
      if(signal.aborted)return;
      findingsLoaded=false;
      const home=hasScience(selected.slug)?targetBySlug(selected.slug)?.det:undefined;
      $('.wb-findings-body').textContent=error instanceof ApiFailure && error.status===404
        ?home&&home!==selected.det
          ?`Signals for this target are published for D${home} only. Switch the player to D${home} to see them.`
          :'No signal analysis has been published for this position and detector.'
        :'Signal analysis could not be loaded. Try again, or continue with the original observations.';
    }
  }
  function load(){
    if(!view||!visible)return;
    const open=panels();
    if(open.some(panel=>['spectrum','lightcurve','crossmatch','data','processing','sensitivity','export'].includes(panel))&&!scienceLoaded){
      scienceLoaded=true;
      const selected=view, signal=controller.signal;
      void science.load(targetFor(selected),signal).then(loaded=>{
        // A transient failure can be retried when the person returns to a section.
        if(!loaded&&!signal.aborted&&controller.signal===signal)scienceLoaded=false;
      });
    }
    if(open.includes('signals'))void findings();
    if(open.includes('validation')&&!calibrated){calibrated=true;void renderCalibration($('.wb-calibration-body'));}
    if(state.mode==='research')void capabilities();
  }
  function metadata(){
    $('.wb-metadata').textContent=JSON.stringify({
      center:view?{ra:view.ra,dec:view.dec}:null, clock:view?.clock,
      selected_detector:view?.det,
      detector:presentation?.mode==='all-wavelengths'?null:epoch?.det??view?.det,
      requested_fov_arcmin:view?.fov, image_source:layer.value,
      display:presentation??null,
      metadata_url:frameSource?.url??null,
      image_grid:frameSource?{center:frameSource.frames.center,size:frameSource.frames.size,pixel_arcsec:frameSource.frames.pixel_arcsec}:null,
      data_release:frameSource?.frames.data_release??null, archive_checked:frameSource?.frames.archive_checked??null,
      epoch:presentation?.mode==='all-wavelengths'?null:epoch??null,
    },null,2);
    if(view)$('.wb-context').textContent=`${view.name??'Selected sky'} · ${presentation?.mode==='all-wavelengths'?`All wavelengths (${presentation.detectors.map(d=>`D${d}`).join(', ')})`:`D${epoch?.det??view.det}`} · ${view.clock==='months'?'Months':'Hours'} · ${view.fov}′ field`;
  }
  function show(next:WorkspaceState){
    state=next;
    host.dataset.mode=state.mode; host.dataset.panel=state.tab;
    const open=panels();
    host.dataset.openDashboards=String(open.length);
    const cardSections=open.filter((key):key is 'motion'|'spectrum'=>key==='motion'||key==='spectrum');
    opts.cardElement.hidden=state.mode!=='explore'&&!cardSections.length;
    // The Viewer shows the whole card (measurement, facts, spectrum, brightness, what to look for);
    // the Advanced dashboards show one part each.
    opts.card.section?.(state.mode==='explore'?'all':cardSections);
    const tabs=$('.wb-tabs'); tabs.hidden=state.mode==='explore';
    tabs.classList.toggle('wb-dashboard-toggles',state.mode==='analyze');
    tabs.setAttribute('aria-label',state.mode==='analyze'?'Dashboards':'Evidence sections');
    if(state.mode==='analyze'){
      if(!tabs.querySelector('button'))tabs.innerHTML=MODES.analyze.tabs.map(([key,label])=>`<button type="button" data-toggle-dashboard="${key}" aria-pressed="false"><span aria-hidden="true">+</span> ${label}</button>`).join('');
      tabs.querySelectorAll<HTMLButtonElement>('button').forEach(button=>{
        const pressed=open.includes(button.dataset.toggleDashboard!);
        button.setAttribute('aria-pressed',String(pressed));button.querySelector('span')!.textContent=pressed?'✓':'+';
      });
    }else tabs.innerHTML=MODES[state.mode].tabs.map(([key,label])=>`<a href="${workspaceHref(state.mode,key)}" ${key===state.tab?'aria-current="page"':''}>${label}</a>`).join('');
    $('.wb-dashboard-controls').hidden=state.mode!=='analyze';
    $('.wb-empty').hidden=state.mode!=='analyze'||open.length>0;
    $('.wb-dashboards').classList.toggle('wb-dashboard-grid',state.mode==='analyze');
    host.querySelectorAll<HTMLElement>('[data-wb]').forEach(el=>{el.hidden=!open.includes(el.dataset.wb!)});
    science.panel(open);
    $<HTMLDetailsElement>('.wb-comparison').open=open.includes('compare')&&visible;
    science.pause(); metadata(); load();
  }
  layer.onchange=()=>opts.layer(layer.value as Layer);
  build.onclick=async()=>{
    if(!view)return;
    const selected={...view}; jobController.abort();jobController=new AbortController();
    const signal=jobController.signal;build.disabled=true;
    $('.wb-status').textContent='Starting analysis. The first run may take several minutes.';
    try {
      const response=await fetch(`/api/jobs?ra=${selected.ra}&dec=${selected.dec}&size=${selected.fov}`,{method:'POST',signal});
      const started=await response.json();
      if(!response.ok)throw new Error(started.error?.message??'Could not start analysis');
      while(!signal.aborted){
        const result=await json<{state:string;progress:string[];error:string|null}>(`/api/jobs/${started.id}`,signal);
        if(signal.aborted)return;
        $('.wb-status').textContent=`${result.state}: ${result.progress.at(-1)??'Waiting for observations'}`;
        if(result.state==='error')throw new Error(result.error??'Analysis failed');
        if(result.state==='done'){opts.rebuilt({...selected,slug:null});return;}
        await new Promise<void>(resolve=>{
          const done=()=>{clearTimeout(timer);signal.removeEventListener('abort',done);resolve()};
          const timer=setTimeout(done,1500);signal.addEventListener('abort',done,{once:true});
        });
      }
    } catch(error){if(!signal.aborted)$('.wb-status').textContent=(error as Error).message;}
    finally{if(!signal.aborted)build.disabled=false;}
  };
  show(state);
  return {
    mode:show,
    select(next:View,selectedLayer:Layer){
      const changed=!view||(['ra','dec','det','clock','fov','slug'] as const).some(k=>view![k]!==next[k]);
      if(changed){
        if(!view||view.ra!==next.ra||view.dec!==next.dec)object=null;
        controller.abort();controller=new AbortController();jobController.abort();science.pause();
        scienceLoaded=false;findingsLoaded=false;epoch=undefined;presentation=undefined;frameSource=null;
        renderFrameQuality($('.wb-quality'),undefined);
        $('.wb-findings-body').replaceChildren();$('.wb-status').textContent='';
        opts.reference([]);science.selection(next.det,next.clock);
        if(!view||view.ra!==next.ra||view.dec!==next.dec)comparison.setTarget(targetFor(next));
      }
      view=next;layer.value=selectedLayer;build.disabled=false;
      const available=layerAvailability(next);
      layer.querySelectorAll<HTMLOptionElement>('option:not([value="raw"])').forEach(o=>{o.disabled=!available.ok;});
      $('.wb-layer-note').textContent=available.reason;
      // Static first: a featured field's products are files; elsewhere the API answers (or says why not).
      const published=hasScience(next.slug);
      $<HTMLAnchorElement>('.wb-science-json').href=published?`/science-data/${next.slug}/science.json`:`/api/science?ra=${next.ra}&dec=${next.dec}`;
      const publishedDetector=published&&targetBySlug(next.slug)?.det===next.det;
      $<HTMLAnchorElement>('.wb-analysis-json').href=publishedDetector?`/science-data/${next.slug}/analysis.json`:`/api/analysis?ra=${next.ra}&dec=${next.dec}&det=${next.det}`;
      $('.wb-context').textContent=`${next.name??'Selected sky'} · D${next.det} · ${next.clock==='months'?'Months':'Hours'} · ${next.fov}′ field`;
      metadata();load();
    },
    object(value:ObjectResponse|null){object=value;science.refreshObject()},
    frames(value:FramesResponse|null,url=''){
      frameSource=value?{frames:value,url}:null;
      if(!value){epoch=undefined;presentation=undefined;renderFrameQuality($('.wb-quality'),undefined);}
      metadata();
    },
    epoch(value:Epoch|undefined,display?:BlinkPresentation){
      epoch=value;presentation=display;
      if(display?.mode==='all-wavelengths')$('.wb-quality').textContent='Combined display in sky-noise units. Quality flags belong to the individual source observations.';
      else renderFrameQuality($('.wb-quality'),value);
      metadata();
    },
    layer(value:Layer){layer.value=value;metadata()},
    message(value:string){$('.wb-status').textContent=value},
    active(value:boolean){
      visible=value;
      if(!value){science.pause();jobController.abort();$<HTMLDetailsElement>('.wb-comparison').open=false;}
      else{if(controller.signal.aborted){controller=new AbortController();scienceLoaded=false;findingsLoaded=false;}$<HTMLDetailsElement>('.wb-comparison').open=panels().includes('compare');load();}
    },
  };
}
