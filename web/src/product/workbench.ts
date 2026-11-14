import './workbench.css';
import type { Epoch, ObjectResponse } from '../api/types';
import type { View } from '../app/modules';
import { json, loadAnalysis, type Target, type Candidate } from '../api/science-client';
import { mountScience } from '../card/science';
import { mountComparison } from './comparison';
import { renderFindings, renderCalibration, renderFrameQuality } from './findings';

export type Layer = 'raw' | 'model' | 'resid' | 'refdiff';
export const targetFor = (view: View): Target => ({
  slug: view.slug ?? '', name: view.name ?? 'Selected sky', ra: view.ra, dec: view.dec,
  default_det: view.det, clock: view.clock, fov: view.fov, n_frames: 0, n_visits: 0,
});

export function mountWorkbench(host: HTMLElement, opts: {
  layer(layer: Layer): void;
  locate(candidate: Candidate): void;
  reference(points: { um: number; flux_ujy: number }[]): void;
  rebuilt(view: View): void;
}) {
  host.innerHTML = `<details class="wb-main">
    <summary>Scientific analysis <span>模型、误报检验、光变与灵敏度</span></summary>
    <p class="wb-intro">Compare models, inspect a signal, or follow the same sky through other surveys.</p>
    <div class="wb-controls">
      <label>Image source <select class="input wb-layer" aria-label="Image source">
        <option value="raw">SPHEREx observations</option><option value="model">Static sky model</option>
        <option value="resid">Model residual</option><option value="refdiff">DR1 reference difference</option>
      </select></label>
      <button class="btn wb-build" type="button" hidden>Build an analysis for this sky</button>
    </div>
    <p class="wb-source">Models and residuals use the analysis cube. Its coverage and quality can differ from the original observations.</p>
    <p class="wb-status" role="status" aria-live="polite"></p>
    <div class="wb-quality"></div>
    <details class="wb-findings"><summary>Motion and cold-source checks <span>线索与误报检验</span></summary>
      <div class="wb-findings-body"></div><p class="source">Background frequencies rank follow-up checks; they are not probabilities that an object is real. Cold-source false alarms remain uncalibrated.</p>
    </details>
    <details class="wb-comparison"></details>
    <details class="wb-calibration"><summary>Calibration evidence <span>校准依据</span></summary><div class="wb-calibration-body"></div></details>
    <div class="wb-science"></div>
  </details>`;
  const $ = <T extends HTMLElement = HTMLElement>(selector: string) => host.querySelector<T>(selector)!;
  const main = $<HTMLDetailsElement>('.wb-main');
  const layer = $<HTMLSelectElement>('.wb-layer');
  const build = $<HTMLButtonElement>('.wb-build');
  const status = $('.wb-status');
  let view: View | undefined, object: ObjectResponse | null = null;
  let controller = new AbortController(), jobController = new AbortController();
  let loaded = false, findingsLoaded = false, calibrated = false, visible = true;
  const comparison = mountComparison($('.wb-comparison'));
  const science = mountScience($('.wb-science'), opts.reference, () => object?.spectrum ?? []);
  void json<{analysis_jobs:boolean}>('/api/science/capabilities').then(capabilities => {
    build.hidden = !capabilities.analysis_jobs;
    science.capabilities(capabilities);
  }).catch(() => { build.hidden = true; });

  function load() {
    if (!view || loaded || !main.open || !visible) return;
    loaded = true;
    void science.load(targetFor(view), controller.signal);
  }
  async function findings() {
    if (!view || findingsLoaded || !$<HTMLDetailsElement>('.wb-findings').open) return;
    findingsLoaded = true;
    const signal = controller.signal;
    $('.wb-findings-body').textContent = 'Loading the analysis…';
    try {
      const analysis = await loadAnalysis(targetFor(view), view.det, signal);
      if (signal.aborted) return;
      renderFindings($('.wb-findings-body'), analysis, opts.locate);
      host.querySelectorAll('.candidate button').forEach(b => b.classList.add('btn'));
    } catch (error) {
      if (signal.aborted) return;
      findingsLoaded = false;
      $('.wb-findings-body').textContent = `No analysis has been published for this detector. Try the target’s default detector. ${(error as Error).message}`;
    }
  }
  main.addEventListener('toggle', () => { if (main.open) { load(); void findings(); } else science.pause(); });
  $('.wb-findings').addEventListener('toggle', () => void findings());
  $('.wb-calibration').addEventListener('toggle', () => {
    if (calibrated || !$<HTMLDetailsElement>('.wb-calibration').open) return;
    calibrated = true;
    void renderCalibration($('.wb-calibration-body'));
  });
  layer.onchange = () => opts.layer(layer.value as Layer);
  build.onclick = async () => {
    if (!view) return;
    const selected = { ...view };
    jobController.abort(); jobController = new AbortController();
    const signal = jobController.signal;
    build.disabled = true;
    status.textContent = 'Starting analysis. The first run may take several minutes.';
    try {
      const response = await fetch(`/api/jobs?ra=${selected.ra}&dec=${selected.dec}&size=${selected.fov}`, { method: 'POST', signal });
      const started = await response.json();
      if (!response.ok) throw new Error(started.error?.message ?? 'Could not start analysis');
      while (!signal.aborted) {
        const state = await json<{state:string; progress:string[]; error:string|null}>(`/api/jobs/${started.id}`, signal);
        if (signal.aborted) return;
        status.textContent = `${state.state}: ${state.progress.at(-1) ?? 'Waiting for observations'}`;
        if (state.state === 'error') throw new Error(state.error ?? 'Analysis failed');
        if (state.state === 'done') {
          opts.rebuilt({ ...selected, slug: null });
          return;
        }
        await new Promise<void>(resolve => {
          const done = () => { clearTimeout(timer); signal.removeEventListener('abort', done); resolve(); };
          const timer = setTimeout(done, 1500);
          signal.addEventListener('abort', done, { once: true });
        });
      }
    } catch (error) {
      if (!signal.aborted) status.textContent = (error as Error).message;
    } finally {
      if (!signal.aborted) build.disabled = false;
    }
  };

  return {
    open() {
      main.open = true;
      load();
      requestAnimationFrame(() => host.scrollIntoView({block: 'start'}));
    },
    select(next: View, selectedLayer: Layer) {
      controller.abort(); controller = new AbortController();
      jobController.abort(); science.pause();
      view = next; object = null; loaded = false; findingsLoaded = false;
      build.disabled = false; status.textContent = ''; layer.value = selectedLayer;
      $('.wb-findings-body').replaceChildren();
      renderFrameQuality($('.wb-quality'), undefined);
      opts.reference([]);
      science.selection(next.det, next.clock);
      comparison.setTarget(targetFor(next));
      load(); void findings();
    },
    object(value: ObjectResponse | null) { object = value; science.refreshObject(); },
    epoch(value: Epoch | undefined) { renderFrameQuality($('.wb-quality'), value); },
    layer(value: Layer) { layer.value = value; },
    message(value: string) { status.textContent = value; },
    active(value: boolean) {
      visible = value;
      if (value) load();
      else { science.pause(); jobController.abort(); build.disabled = false; }
    },
  };
}
