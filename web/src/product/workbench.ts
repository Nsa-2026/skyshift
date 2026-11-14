import './workbench.css';
import type {Epoch, FramesResponse, ObjectResponse} from '../api/types';
import type {CardHandle, View} from '../app/modules';
import {json, loadAnalysis, type Target, type Candidate} from '../api/science-client';
import {mountScience} from '../card/science';
import {mountComparison} from './comparison';
import {renderFindings, renderCalibration, renderFrameQuality} from './findings';
import {MODES, workspaceHref, type WorkspaceState} from './workspace-state';

export type Layer = 'raw' | 'model' | 'resid' | 'refdiff';
export const targetFor = (view: View): Target => ({
  slug:view.slug??'', name:view.name??'Selected sky', ra:view.ra, dec:view.dec,
  default_det:view.det, clock:view.clock, fov:view.fov, n_frames:0, n_visits:0,
});

export function mountWorkbench(host:HTMLElement, opts:{
  card:CardHandle; cardElement:HTMLElement;
  layer(layer:Layer):void; locate(candidate:Candidate):void;
  reference(points:{um:number;flux_ujy:number}[]):void; rebuilt(view:View):void;
}) {
  host.innerHTML=`<p class="wb-context"></p>
    <div class="wb-quality" aria-label="Current observation quality"></div>
    <nav class="wb-tabs" aria-label="Evidence sections" hidden></nav>
    <p class="wb-status" role="status" aria-live="polite"></p>
    <div class="wb-card-slot"></div>
    <div class="wb-guide" data-wb="explore">
      <p>Follow the marked target as the dates change. A jump may be motion; a change in brightness needs a closer look.</p>
      <a class="btn btn-primary" href="#/explore/analyze">Analyze this target <span aria-hidden="true">→</span></a>
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
      <p class="source">Published models, residuals and DR1 differences use the analysis cube. They currently support the five featured science targets at 5′ on the Months clock; their measured footprint can be smaller. Original object measurements remain separate.</p>
      <p class="wb-capability" role="status"></p><button class="btn wb-build" type="button" hidden>Build an analysis for this sky</button>
    </section>
    <section data-wb="validation" hidden><h2>Validation</h2><div class="wb-calibration-body"></div></section>
    <div class="wb-science"></div>`;
  const $=<T extends HTMLElement=HTMLElement>(s:string)=>host.querySelector<T>(s)!;
  $('.wb-card-slot').append(opts.cardElement);
  const layer=$<HTMLSelectElement>('.wb-layer'), build=$<HTMLButtonElement>('.wb-build');
  const science=mountScience($('.wb-science'),opts.reference,()=>object?.spectrum??[]);
  const comparison=mountComparison($('.wb-comparison'));
  let state:WorkspaceState={mode:'explore',tab:''}, view:View|undefined;
  let object:ObjectResponse|null=null, epoch:Epoch|undefined;
  let frameSource:{frames:FramesResponse;url:string}|null=null;
  let controller=new AbortController(), jobController=new AbortController();
  let scienceLoaded=false, findingsLoaded=false, calibrated=false, visible=true, capabilitiesLoaded=false;

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
      $('.wb-findings-body').textContent=`No analysis has been published for this detector. Try the target’s default detector. ${(error as Error).message}`;
    }
  }
  function load(){
    if(!view||!visible)return;
    const panel=state.mode==='explore'?'explore':state.tab;
    if(['spectrum','lightcurve','crossmatch','data','processing','sensitivity','export'].includes(panel)&&!scienceLoaded){
      scienceLoaded=true; void science.load(targetFor(view),controller.signal);
    }
    if(panel==='signals')void findings();
    if(panel==='validation'&&!calibrated){calibrated=true;void renderCalibration($('.wb-calibration-body'));}
    if(state.mode==='research')void capabilities();
  }
  function metadata(){
    $('.wb-metadata').textContent=JSON.stringify({
      center:view?{ra:view.ra,dec:view.dec}:null, clock:view?.clock, detector:view?.det,
      requested_fov_arcmin:view?.fov, image_source:layer.value,
      metadata_url:frameSource?.url??null,
      image_grid:frameSource?{center:frameSource.frames.center,size:frameSource.frames.size,pixel_arcsec:frameSource.frames.pixel_arcsec}:null,
      data_release:frameSource?.frames.data_release??null, archive_checked:frameSource?.frames.archive_checked??null,
      epoch:epoch??null,
    },null,2);
  }
  function show(next:WorkspaceState){
    state=next;
    host.dataset.mode=state.mode; host.dataset.panel=state.tab;
    const panel=state.mode==='explore'?'explore':state.tab;
    const cardSection=panel==='explore'?'summary':panel==='motion'?'motion':panel==='spectrum'?'spectrum':null;
    opts.cardElement.hidden=cardSection===null;
    if(cardSection)opts.card.section?.(cardSection);
    const tabs=$('.wb-tabs'); tabs.hidden=state.mode==='explore';
    tabs.innerHTML=MODES[state.mode].tabs.map(([key,label])=>`<a href="${workspaceHref(state.mode,key)}" ${key===state.tab?'aria-current="page"':''}>${label}</a>`).join('');
    host.querySelectorAll<HTMLElement>('[data-wb]').forEach(el=>{el.hidden=el.dataset.wb!==panel});
    science.panel(panel);
    $<HTMLDetailsElement>('.wb-comparison').open=panel==='compare'&&visible;
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
        scienceLoaded=false;findingsLoaded=false;epoch=undefined;frameSource=null;
        renderFrameQuality($('.wb-quality'),undefined);
        $('.wb-findings-body').replaceChildren();$('.wb-status').textContent='';
        opts.reference([]);science.selection(next.det,next.clock);
        if(!view||view.ra!==next.ra||view.dec!==next.dec)comparison.setTarget(targetFor(next));
      }
      view=next;layer.value=selectedLayer;build.disabled=false;
      $<HTMLAnchorElement>('.wb-science-json').href=`/api/science?ra=${next.ra}&dec=${next.dec}`;
      $<HTMLAnchorElement>('.wb-analysis-json').href=`/api/analysis?ra=${next.ra}&dec=${next.dec}&det=${next.det}`;
      $('.wb-context').textContent=`${next.name??'Selected sky'} · D${next.det} · ${next.clock==='months'?'Months':'Hours'} · ${next.fov}′ field`;
      metadata();load();
    },
    object(value:ObjectResponse|null){object=value;science.refreshObject()},
    frames(value:FramesResponse|null,url=''){
      frameSource=value?{frames:value,url}:null;
      if(!value){epoch=undefined;renderFrameQuality($('.wb-quality'),undefined);}
      metadata();
    },
    epoch(value:Epoch|undefined){epoch=value;renderFrameQuality($('.wb-quality'),value);metadata()},
    layer(value:Layer){layer.value=value;metadata()},
    message(value:string){$('.wb-status').textContent=value},
    active(value:boolean){
      visible=value;
      if(!value){science.pause();jobController.abort();$<HTMLDetailsElement>('.wb-comparison').open=false;}
      else{if(controller.signal.aborted){controller=new AbortController();scienceLoaded=false;findingsLoaded=false;}load();}
    },
  };
}
