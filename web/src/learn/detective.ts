import './detective.css';
import type { MountPage } from '../app/modules';
import type { LoadedFrames, ObjectResponse, SpectrumPoint } from '../api/types';
import { loadFrames, loadObject } from '../api/client';
import { DETECTORS, formatMjd, gaiaAt, skyToPixel } from '../app/astro';
import { targetBySlug } from '../app/state';
import { displayLimits, observation, type Observation } from '../product/observations';
import { escapeHtml as esc } from '../product/evidence';
import { paintNegative } from './plate';
import { cropAround } from './data';
import { loadGuxing, type GuxingLibs } from './stories-data';
import { bandEvidence, matchesTarget, measuredDirection, nearestSample, skyCase, spectrumSvg, usableSpectrum, type Pixel, type SkyCase } from './detective-model';

const STORE = 'skyshift-detective-v1';
const MISSIONS = ['Read the old sky', 'Catch the change', 'Read the light'];
type Entry = { mission: number; note: string; source: string };
type Barnard = { observations: Observation[]; object: ObjectResponse; field: SkyCase };
type Spectra = { object: ObjectResponse; set: LoadedFrames; valid: SpectrumPoint[]; visits: number[] };
const sourceLink = (url: string, label: string) => {
  try { if (!['http:', 'https:'].includes(new URL(url,location.href).protocol)) return esc(label); } catch { return esc(label); }
  return `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(label)} ↗</a>`;
};
const dataSource = (slug: string) => `/data/${slug}/object.json`;
const flux = (n: number) => n.toLocaleString('en-GB', { maximumFractionDigits: n < 100 ? 2 : 0 });

export const mountDetective: MountPage = (host) => {
  host.classList.add('sd'); host.lang = 'en';
  const ctl = new AbortController();
  let visible = false, disposed = false, stage = 0, request = 0, imageRequest = 0;
  let barnard: Barnard | undefined, spectra: Spectra | undefined, culture: GuxingLibs | undefined;
  let barnardPending: Promise<void> | undefined, spectrumPending: Promise<void> | undefined;
  let frameIndex = 0, hint = false, pick: Pixel | null = null;
  let visit = 0, sample = 0, errors = true, bands = false;
  let timer: ReturnType<typeof setInterval> | undefined;
  const imageCache = new Map<string, Promise<Observation>>();
  let entries: Entry[] = [];
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(STORE) ?? '[]');
    if (Array.isArray(saved)) entries = saved.filter((e): e is Entry => e && Number.isInteger(e.mission) && e.mission >= 0 && e.mission < 3 && typeof e.note === 'string' && typeof e.source === 'string').slice(0, 3);
    entries = entries.filter((e,i,a) => a.findIndex(x => x.mission === e.mission) === i);
  } catch { /* The activity also works without storage. */ }
  const $ = <T extends HTMLElement = HTMLElement>(selector: string) => host.querySelector<T>(selector)!;
  const recorded = (mission: number) => entries.some(e => e.mission === mission);
  const stop = () => { if (timer) clearInterval(timer); timer = undefined; };

  host.innerHTML = `<nav class="sd-back" aria-label="Learning activities"><a href="#/learn">← Learn</a><a href="#/learn/stories">Explore sky stories ↗</a></nav>
    <header class="sd-head"><div><p class="sd-kicker">Sky detective</p><h1 id="ln-h" tabindex="-1">Old clues.<br>New light.</h1><p class="sd-lead">Find a star by its neighbours. Follow a change. Read the light.<br>Every image and spectral point comes from real NASA SPHEREx observations.</p></div><div class="sd-mission-note"><span class="sd-data-badge"><i></i> Powered by NASA data</span><p>Three small investigations.<br>Your own observation notebook.</p><button class="sd-notebook-jump">Open my notebook ↓</button></div></header>
    <nav class="sd-tabs" role="tablist" aria-label="Investigations">${MISSIONS.map((name,i) => `<button type="button" role="tab" id="sd-tab-${i}" aria-controls="sd-panel" data-stage="${i}"><span class="sd-tab-number">0${i+1}</span><span>${name}<small>${['Navigate with a relative position','Compare real visits','Explore a measured spectrum'][i]}</small></span><b class="sd-tab-check" aria-hidden="true"></b></button>`).join('')}</nav>
    <section class="sd-work" id="sd-panel" role="tabpanel" aria-labelledby="sd-tab-0"></section>
    <details class="sd-notebook"><summary><span>Observation notebook</span><span class="sd-record-count"></span></summary><div class="sd-entries"></div><p class="sd-storage-note">Saved in this browser. Your notes describe known objects, not new discoveries.</p><button class="sd-new-notebook">Start a new notebook</button></details>
    <footer class="sd-credit">Images and photometry: NASA/JPL-Caltech/IPAC · SPHEREx, via IRSA. Image reductions: SkyShift. Position references: ESA Gaia DR3. Traditional names: the existing Qing-era star-map dataset. ${sourceLink('https://irsa.ipac.caltech.edu/Missions/spherex.html','NASA data archive')}</footer>`;

  function notebook() {
    $('.sd-record-count').textContent = `${entries.length} / 3 recorded`;
    $('.sd-entries').innerHTML = MISSIONS.map((name,i) => {
      const e = entries.find(e => e.mission === i);
      return `<article class="${e ? 'is-recorded' : ''}"><span>${e ? '✓' : `0${i+1}`}</span><div><h3>${name}</h3><p>${e ? esc(e.note) : 'Complete this investigation to record your evidence.'}</p>${e ? sourceLink(e.source,'Inspect the source data') : ''}</div></article>`;
    }).join('');
    host.querySelectorAll<HTMLElement>('[data-stage]').forEach(b => { b.querySelector('.sd-tab-check')!.textContent = recorded(Number(b.dataset.stage)) ? '✓' : ''; });
  }
  function record(mission: number, note: string, source: string) {
    entries = [...entries.filter(e => e.mission !== mission), { mission, note, source }].sort((a,b) => a.mission - b.mission);
    try { localStorage.setItem(STORE, JSON.stringify(entries)); } catch { $('.sd-storage-note').textContent = 'Your notes are available for this session. This browser could not save them.'; }
    notebook();
  }
  function go(next: number) {
    stop(); stage = next; frameIndex = 0; pick = null; render();
  }
  function tabs() {
    host.querySelectorAll<HTMLButtonElement>('[data-stage]').forEach(b => {
      const current = Number(b.dataset.stage) === stage;
      b.setAttribute('aria-selected', String(current)); b.tabIndex = current ? 0 : -1;
    });
    $('.sd-work').setAttribute('aria-labelledby', `sd-tab-${stage}`);
  }
  async function loadBarnard() {
    const t = targetBySlug('barnard')!;
    const [set, object] = await Promise.all([
      loadFrames({ ...t, fov: 15, det: 2 }, ctl.signal), loadObject(t, ctl.signal),
    ]);
    if ((set.frames as { synthetic?: boolean }).synthetic || (object as { synthetic?: boolean }).synthetic || /synthetic|sample/i.test(set.frames.data_release)) throw new Error('This activity requires real NASA observations.');
    const epochs = set.frames.epochs.filter(e => e.det === 2).sort((a,b) => a.mjd_mid - b.mjd_mid);
    if (epochs.length < 2) throw new Error('At least two D2 visits are needed to compare this field.');
    const chosen = [...new Set([epochs[0], epochs[Math.floor((epochs.length - 1) / 2)], epochs.at(-1)!])];
    const observations = await Promise.all(chosen.map(e => observation(set, e, ctl.signal)));
    barnard = { observations, object, field: skyCase(observations[0], object) };
  }
  async function loadSpectra() {
    const t = targetBySlug('epsindib')!;
    const [object, set] = await Promise.all([loadObject(t, ctl.signal), loadFrames({ ...t, fov: 5, det: 3 }, ctl.signal)]);
    if ((set.frames as { synthetic?: boolean }).synthetic || (object as { synthetic?: boolean }).synthetic || /synthetic|sample/i.test(set.frames.data_release)) throw new Error('This activity requires real NASA observations.');
    const valid = usableSpectrum(object.spectrum);
    if (!valid.length) throw new Error('No unsaturated spectral measurements with S/N ≥ 3 are available for this target.');
    const visits = [...new Set(valid.map(p => p.visit))].sort((a,b) => a-b);
    spectra = { object, set, valid, visits }; visit = visits[0]; sample = Math.floor(usableSpectrum(object.spectrum, visit).length / 2);
  }
  async function ensureData() {
    const id = ++request;
    $('.sd-work').innerHTML = `<div class="sd-loading" role="status"><span class="sd-loading-mark">✦</span><h2>Opening the NASA observations…</h2><p>Loading ${stage === 2 ? 'measured photometry and detector metadata' : 'SPHEREx visit stacks and catalogue positions'}.</p></div>`;
    try {
      if (stage === 2) { spectrumPending ??= loadSpectra().catch(e => { spectrumPending = undefined; throw e; }); await spectrumPending; }
      else { barnardPending ??= loadBarnard().catch(e => { barnardPending = undefined; throw e; }); await barnardPending; }
      if (disposed || !visible || id !== request) return;
      renderReady();
    } catch (e) {
      if (disposed || !visible || id !== request || ctl.signal.aborted) return;
      $('.sd-work').innerHTML = `<div class="sd-loading" role="alert"><h2>The observations could not load.</h2><p>${esc((e as Error).message)}</p><button class="sd-retry sd-primary">Try loading NASA data again</button></div>`;
      $('.sd-retry').onclick = () => void ensureData();
    }
  }
  function render() {
    imageRequest++; tabs(); notebook();
    if (stage === 2 ? !!spectra : !!barnard) { request++; renderReady(); } else void ensureData();
  }
  function renderReady() { if (stage === 2) renderLight(); else renderSky(); }

  function renderSky() {
    const b = barnard!, finding = stage === 0, f = b.observations[0].set.frames;
    const clue = b.field, first = b.observations[0];
    $('.sd-work').innerHTML = `<div class="sd-section-head"><div><p class="sd-kicker">Investigation 0${stage+1} · Barnard’s Star</p><h2>${finding ? 'A neighbour is a wayfinder.' : 'The sky leaves a trail.'}</h2></div><span class="sd-real-label">NASA SPHEREx · ${esc(f.data_release)}</span></div>
      <div class="sd-sky-layout"><figure class="sd-image-panel"><div class="sd-frame"><canvas class="sd-sky-canvas" role="img" ${finding ? 'tabindex="0"' : ''} aria-label="${finding ? 'Real NASA SPHEREx star field. Use arrow keys to move a crosshair and Enter to choose a source.' : 'Magnified NASA SPHEREx visits of Barnard’s Star, north up and east left.'}"></canvas><svg class="sd-map-overlay" aria-hidden="true"></svg><span class="sd-compass" aria-hidden="true">N ↑<br>E ←</span><span class="sd-image-tag">D2 · 1.1–1.6 µm</span></div><figcaption class="sd-image-caption"></figcaption>
      ${finding ? '<p class="sd-key-help">Select a source. Keyboard: arrow keys move the crosshair; Enter selects.</p>' : `<div class="sd-visit-controls"><button class="sd-blink" aria-pressed="false">▶ Blink visits</button><div role="group" aria-label="NASA visits">${b.observations.map((_,i) => `<button data-visit="${i}" aria-pressed="${i === 0}">Visit ${i+1}</button>`).join('')}</div></div>`}</figure>
      <aside class="sd-clue-panel">${finding ? `<span class="sd-paper-tag">An old way of looking</span><h3>Start from Reference A.</h3><p>Look <strong>${clue.direction}</strong>, about <strong>${clue.arcmin.toFixed(1)} arcminutes</strong> away. Which source fits the clue?</p><p class="sd-small">North is up. East is left. Use the marked neighbour to judge a relative position.</p><button class="sd-hint" aria-pressed="${hint}">${hint ? 'Hide the distance guide' : 'Show a distance guide'}</button><div class="sd-culture"><p class="sd-small">Loading this region’s traditional names…</p></div>` : `<span class="sd-paper-tag">Look, compare, infer</span><h3>Which way does it move?</h3><p>Blink the same detector across real visits. The red ring stays at the star’s first catalogue position.</p><div class="sd-compass-quiz" role="group" aria-label="Direction of motion">${['Northwest','North','Northeast','West','Not enough evidence','East','Southwest','South','Southeast'].map((d,i) => `<button class="sd-direction" data-answer="${d}"><span aria-hidden="true">${['↖','↑','↗','←','?','→','↙','↓','↘'][i]}</span><small>${d === 'Not enough evidence' ? 'Not enough evidence' : d}</small></button>`).join('')}</div><p class="sd-small">The centre is saturated in these images. Follow its surrounding light; a blank core does not mean the star vanished.</p>`}<div class="sd-feedback" role="status" aria-live="polite"></div><button class="sd-continue sd-primary" ${recorded(stage) ? '' : 'hidden'}>${finding ? 'Compare its motion →' : 'Read infrared light →'}</button></aside></div>
      <details class="sd-provenance"><summary>Follow the evidence: data and processing</summary><div><p>NASA SPHEREx ${esc(f.data_release)}, D2 visit stacks. Each image uses the same brightness scale; the sky median is subtracted for display. No sources are added or repositioned. Blank pixels have no usable data. Reference positions are propagated from ESA Gaia DR3 using proper motion.</p><p>This is a modern infrared field used to practise relative-position clues, not a reconstruction of a naked-eye view in antiquity. The cultural names use Qing-era identifications, not the sky map of a specific earlier century.</p><p>${sourceLink(first.set.jsonUrl,'Frame metadata')} · ${sourceLink(dataSource('barnard'),'Object and measurement data')} · ${sourceLink('https://irsa.ipac.caltech.edu/Missions/spherex.html','NASA archive')}</p><ul>${b.observations.map(o => `<li>${esc(o.epoch.id)} · ${formatMjd(o.epoch.mjd_mid)} UTC · ${o.epoch.n_exposures} exposures · ${sourceLink(new URL(o.epoch.data_url,o.set.jsonUrl).href,'Image array')}</li>`).join('')}</ul></div></details>`;
    drawSky();
    $('.sd-continue').onclick = () => go(stage + 1);
    if (finding) {
      $('.sd-hint').onclick = () => { hint = !hint; $('.sd-hint').setAttribute('aria-pressed', String(hint)); $('.sd-hint').textContent = hint ? 'Hide the distance guide' : 'Show a distance guide'; drawSky(); };
      const canvas = $<HTMLCanvasElement>('.sd-sky-canvas');
      canvas.addEventListener('pointerdown', e => {
        const r = canvas.getBoundingClientRect(), c = b.field.crop;
        choose({ x: c.x + (e.clientX-r.left)/r.width*c.w - .5, y: c.y + (e.clientY-r.top)/r.height*c.h - .5 });
      });
      canvas.onkeydown = e => {
        const c = b.field.crop, p = pick ?? { x: c.x+c.w/2, y: c.y+c.h/2 };
        const delta: Record<string, [number,number]> = { ArrowLeft: [-1,0], ArrowRight: [1,0], ArrowUp: [0,-1], ArrowDown: [0,1] };
        if (delta[e.key]) { e.preventDefault(); const [dx,dy]=delta[e.key]; pick={x:Math.max(c.x,Math.min(c.x+c.w-1,p.x+dx)),y:Math.max(c.y,Math.min(c.y+c.h-1,p.y+dy))}; drawSky(); }
        else if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); choose(p); }
      };
      void fillCulture();
    } else {
      host.querySelectorAll<HTMLButtonElement>('[data-visit]').forEach(button => { button.onclick = () => { stop(); frameIndex = Number(button.dataset.visit); drawSky(); }; });
      $('.sd-blink').onclick = () => {
        if (timer) stop(); else if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) frameIndex = (frameIndex + 1) % b.observations.length;
        else timer = setInterval(() => { frameIndex = (frameIndex + 1) % b.observations.length; drawSky(); }, 750);
        drawSky();
      };
      host.querySelectorAll<HTMLButtonElement>('.sd-direction').forEach(button => { button.onclick = () => {
        const answer = measuredDirection(b.object, 2);
        if (button.dataset.answer !== answer) { $('.sd-feedback').textContent = 'Try another direction. Use north up and east left, and compare the star with the fixed red ring.'; return; }
        const m = b.object.measure;
        const note = answer === 'Not enough evidence' ? 'The available reduction does not establish a reliable direction in this detector.' : `${answer}: SkyShift measured ${m!.shift_arcsec.toFixed(2)}″ ± ${m!.sigma_arcsec.toFixed(2)}″ over ${m!.dt_yr.toFixed(3)} years from NASA SPHEREx observations.`;
        $('.sd-feedback').textContent = `✓ ${note}`; $('.sd-continue').hidden = false; record(1,note,dataSource('barnard'));
      }; });
    }
  }
  function choose(at: Pixel) {
    pick = at;
    if (matchesTarget(at,barnard!.field)) {
      $('.sd-feedback').textContent = '✓ You found Barnard’s Star. A direction and a nearby reference can locate a source, even before you know its name.';
      $('.sd-continue').hidden = false;
      record(0,`Located Barnard’s Star ${barnard!.field.direction} of Reference A, ${barnard!.field.arcmin.toFixed(1)}′ away, in the NASA SPHEREx observation of ${formatMjd(barnard!.observations[0].epoch.mjd_mid)}.`,barnard!.observations[0].set.jsonUrl);
    } else $('.sd-feedback').textContent = 'Keep looking. Start at A, then use the direction and distance in the clue. The distance guide can help.';
    drawSky();
  }
  function drawSky() {
    const b = barnard!, obs = b.observations[frameIndex], f = obs.set.frames;
    const initial = b.field.target;
    const crop = stage === 0 ? b.field.crop : cropAround(b.observations.map(o => skyToPixel(gaiaAt(b.object.gaia!,o.epoch.mjd_mid),f,o.epoch.center??f.center)),f.size,12,40);
    const canvas = $<HTMLCanvasElement>('.sd-sky-canvas'), context = canvas?.getContext('2d'); if (!canvas) return;
    canvas.width=crop.w; canvas.height=crop.h;
    if (context) { const image=context.createImageData(crop.w,crop.h); image.data.set(paintNegative(obs.values,f.size[0],crop,displayLimits(b.observations))); context.putImageData(image,0,0); }
    const overlay = $<SVGSVGElement & HTMLElement>('.sd-map-overlay'); overlay.setAttribute('viewBox',`${crop.x-.5} ${crop.y-.5} ${crop.w} ${crop.h}`);
    const a=b.field.reference, radius=Math.hypot(a.x-initial.x,a.y-initial.y);
    overlay.innerHTML = stage === 0 ? `<circle cx="${a.x}" cy="${a.y}" r="3.5" class="sd-reference"/><text x="${a.x+5}" y="${a.y-4}" class="sd-reference-text">A</text>${hint ? `<circle cx="${a.x}" cy="${a.y}" r="${radius}" class="sd-distance-ring"/>` : ''}${pick ? `<path d="M${pick.x-3},${pick.y}h6 M${pick.x},${pick.y-3}v6" class="sd-pick"/>` : ''}${recorded(0) ? `<circle cx="${initial.x}" cy="${initial.y}" r="${b.field.tolerance}" class="sd-found"/>` : ''}` : `<circle cx="${initial.x}" cy="${initial.y}" r="4" class="sd-first-ring"/>`;
    $('.sd-image-caption').textContent = `${formatMjd(obs.epoch.mjd_mid)} UTC · ${obs.epoch.n_exposures} exposures · ${(crop.w*f.pixel_arcsec/60).toFixed(1)}′ across · ${stage === 0 ? 'Reference A: ESA Gaia DR3 position' : 'Magnified view, common brightness scale'}`;
    if (stage === 1) {
      host.querySelectorAll<HTMLElement>('[data-visit]').forEach(b => b.setAttribute('aria-pressed',String(Number(b.dataset.visit) === frameIndex)));
      $('.sd-blink').setAttribute('aria-pressed',String(!!timer)); $('.sd-blink').textContent = timer ? 'Ⅱ Pause visits' : window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'Next visit →' : '▶ Blink visits';
    }
  }
  async function fillCulture() {
    try {
      culture ??= await loadGuxing(); if (disposed || !visible || stage !== 0 || !host.querySelector('.sd-culture')) return;
      const t=targetBySlug('barnard')!, where=culture.gx.mansion(t.ra,t.dec), near=culture.gx.nearest(t.ra,t.dec,1)[0];
      $('.sd-culture').innerHTML=`<h4>Two maps, one sky</h4><p>This region lies in the ${esc(where.en)} (<span lang="zh">${esc(where.zh)}</span>).${near ? ` A nearby asterism is ${esc(near.asterism_en)} (<span lang="zh">${esc(near.asterism_zh)}</span>).` : ''}</p><a href="#/learn/stories/asterisms/${t.ra},${t.dec}">Explore the traditional map ↗</a>`;
    } catch { if (!disposed && stage === 0 && host.querySelector('.sd-culture')) $('.sd-culture').textContent='The traditional names are unavailable. The NASA image and position clue still work.'; }
  }

  function selectedPoints() { return usableSpectrum(spectra!.object.spectrum,visit); }
  function renderLight() {
    const s=spectra!, points=selectedPoints(), evidence=bandEvidence(s.object.spectrum,visit); sample=Math.min(sample,points.length-1);
    $('.sd-work').innerHTML=`<div class="sd-section-head"><div><p class="sd-kicker">Investigation 03 · Epsilon Indi B</p><h2>Every point is a piece of light.</h2><p>A pair of brown dwarfs, unresolved here. Explore the infrared light SPHEREx actually measured.</p></div><span class="sd-real-label">NASA SPHEREx · ${esc(s.set.frames.data_release)}</span></div>
      <div class="sd-spectrum-toolbar"><label>Observation visit <select class="sd-spectrum-visit">${s.visits.map(v=>`<option value="${v}" ${v===visit?'selected':''}>Visit ${v}</option>`).join('')}</select></label><div><button class="sd-errors" aria-pressed="${errors}">Uncertainty bars</button><button class="sd-bands" aria-pressed="${bands}">Molecular regions</button></div></div>
      <div class="sd-lab-layout"><div class="sd-graph-panel"><div class="sd-chart"></div><div class="sd-detector-strip" role="group" aria-label="Jump to a detector’s measured samples">${([1,2,3,4,5,6] as const).map(d=>`<button data-detector="${d}" style="--sd-detector:var(--d${d})" ${points.some(p=>p.det===d)?'': 'disabled'} aria-label="D${d}, ${DETECTORS[d].min} to ${DETECTORS[d].max} micrometres${points.some(p=>p.det===d)?'':', no usable samples in this visit'}"><b>D${d}</b><small>${DETECTORS[d].min}–${DETECTORS[d].max}</small></button>`).join('')}</div><label class="sd-wave-label" for="sd-wave"><span>Travel through the measured wavelengths</span><output class="sd-wave-output" for="sd-wave"></output></label><input class="sd-wave" id="sd-wave" type="range" min="0" max="${points.length-1}" step="1" value="${sample}"><div class="sd-wave-ends"><span>Shorter wavelengths</span><span>Longer wavelengths →</span></div><p class="sd-graph-caption"></p><p class="sd-band-note" ${bands?'':'hidden'}>Shaded regions mark reference absorption bands of water, methane and carbon monoxide. A point in a shaded region is not, by itself, a molecular detection. These are reference annotations over measured data.</p><div class="sd-spectrum-question"><span class="sd-paper-tag">Read the evidence</span><h3>Which band has the higher median measured flux?</h3><p>Compare D3 (1.6–2.4 µm) with D6 (4.4–5.0 µm), using this visit’s usable samples. The median is the middle value when the measured fluxes are sorted.</p><div class="sd-band-summary"><div><span>D3 median</span><strong>${evidence.short?`${flux(evidence.short.flux)} <small>µJy</small>`:'Unavailable'}</strong><small>${evidence.short?`${evidence.short.count} measured samples`:'Fewer than 3 usable samples'}</small></div><div><span>D6 median</span><strong>${evidence.long?`${flux(evidence.long.flux)} <small>µJy</small>`:'Unavailable'}</strong><small>${evidence.long?`${evidence.long.count} measured samples`:'Fewer than 3 usable samples'}</small></div></div><div class="sd-band-answers"><button data-band-answer="D3">D3 · shorter wavelengths</button><button data-band-answer="D6">D6 · longer wavelengths</button><button data-band-answer="Not enough evidence">Not enough evidence</button></div><div class="sd-spectrum-feedback" role="status"></div></div></div>
      <aside class="sd-reading-panel"><span class="sd-paper-tag">A real measurement</span><div class="sd-point-reading" aria-live="polite"></div><figure class="sd-band-image"><div class="sd-mini-frame"><canvas role="img" aria-label="Real NASA SPHEREx detector image of Epsilon Indi B"></canvas></div><figcaption class="sd-band-image-caption"></figcaption><p class="sd-image-status" role="status"></p></figure><p class="sd-small">This is a detector-band visit stack, not an image at one exact spectral wavelength. Display contrast is set separately for each detector; use the graph to compare measured flux.</p></aside></div>
      <details class="sd-provenance"><summary>Follow the evidence: photometry, dates and image arrays</summary><div><p>All plotted points come from NASA SPHEREx observations reduced by SkyShift. Only finite, unsaturated detections with signal-to-noise ≥ 3 are plotted. Each visit is kept separate. Bars show ±1 measurement error; the vertical axis is logarithmic. No synthetic spectrum or interpolated model is used.</p><p>The D3/D6 exercise compares medians of samples at different wavelengths within each band. It is not a temperature measurement, a calibrated colour index or a test of visible colour.</p><p>${sourceLink(dataSource('epsindib'),'Full measured spectrum')} · ${sourceLink(s.set.jsonUrl,'Image-stack metadata')} · ${sourceLink('https://www.jpl.nasa.gov/news/nasas-spherex-telescope-sees-menagerie-of-brown-dwarfs/','NASA: reading brown-dwarf spectra')}</p><div class="sd-current-image-source"></div></div></details>
      <div class="sd-finish"><p>Keep following the evidence.</p><a class="sd-primary" href="?t=epsindib#/explore/spectrum">Open this spectrum in the Viewer ↗</a><button class="sd-review-notes">Review my notebook ↓</button></div>`;
    $('.sd-spectrum-visit').onchange=()=>{const um=selectedPoints()[sample]?.um??3;visit=Number($<HTMLSelectElement>('.sd-spectrum-visit').value);sample=nearestSample(selectedPoints(),um);renderLight();};
    $('.sd-errors').onclick=()=>{errors=!errors;$('.sd-errors').setAttribute('aria-pressed',String(errors));drawSpectrum();};
    $('.sd-bands').onclick=()=>{bands=!bands;$('.sd-bands').setAttribute('aria-pressed',String(bands));$('.sd-band-note').hidden=!bands;drawSpectrum();};
    $<HTMLInputElement>('.sd-wave').oninput=()=>{sample=Number($<HTMLInputElement>('.sd-wave').value);updateSample();};
    host.querySelectorAll<HTMLButtonElement>('[data-detector]').forEach(button=>{button.onclick=()=>{
      const points=selectedPoints(), indices=points.map((p,i)=>({p,i})).filter(x=>x.p.det===Number(button.dataset.detector));
      if(!indices.length)return;sample=indices[Math.floor(indices.length/2)].i;$<HTMLInputElement>('.sd-wave').value=String(sample);updateSample();
    };});
    $('.sd-chart').onclick=e=>{const svg=host.querySelector('.sd-spectrum')!,r=svg.getBoundingClientRect(),width=Number(svg.getAttribute('data-chart-width')),x=(e.clientX-r.left)/r.width*width;sample=nearestSample(selectedPoints(),.7+(x-60)/(width-80)*4.4);$<HTMLInputElement>('.sd-wave').value=String(sample);updateSample();};
    host.querySelectorAll<HTMLButtonElement>('[data-band-answer]').forEach(button=>{button.onclick=()=>{
      const evidence=bandEvidence(s.object.spectrum,visit);
      if(button.dataset.bandAnswer!==evidence.winner){$('.sd-spectrum-feedback').textContent='Try again. Use the vertical axis: higher points mean more measured flux. Switch between samples in D3 and D6.';return;}
      const note=evidence.short&&evidence.long?`Visit ${visit}: D3 median ${flux(evidence.short.flux)} µJy (${evidence.short.count} samples); D6 median ${flux(evidence.long.flux)} µJy (${evidence.long.count} samples). ${evidence.winner} is higher.`:'This visit does not have enough usable samples in both bands for the comparison.';
      $('.sd-spectrum-feedback').textContent=`✓ ${note} This compares measured medians; it does not establish a statistically significant difference, temperature or visible colour.`;record(2,note,dataSource('epsindib'));
    };});
    $('.sd-review-notes').onclick=openNotebook;updateSample();
  }
  function drawSpectrum() { $('.sd-chart').innerHTML=spectrumSvg(selectedPoints(),spectra!.valid,sample,errors,bands,$('.sd-chart').clientWidth||760); }
  function updateSample() {
    const points=selectedPoints(),p=points[sample]; if(!p)return;
    drawSpectrum();
    $('.sd-wave-output').textContent=`${p.um.toFixed(3)} µm · D${p.det}`;
    host.querySelectorAll<HTMLElement>('[data-detector]').forEach(b=>b.setAttribute('aria-pressed',String(Number(b.dataset.detector)===p.det)));
    $<HTMLInputElement>('.sd-wave').setAttribute('aria-valuetext',`${p.um.toFixed(3)} micrometres, detector D${p.det}, flux ${flux(p.flux_ujy)} microjansky`);
    $('.sd-point-reading').innerHTML=`<p class="sd-point-wavelength">${p.um.toFixed(3)} <small>µm</small></p><dl><div><dt>Measured flux</dt><dd>${flux(p.flux_ujy)} <small>µJy</small></dd></div><div><dt>Uncertainty (±1σ)</dt><dd>± ${flux(p.err_ujy)} <small>µJy</small></dd></div><div><dt>Signal / noise</dt><dd>${(p.flux_ujy/p.err_ujy).toFixed(1)}</dd></div><div><dt>Observed (UTC)</dt><dd>${formatMjd(p.mjd)}</dd></div><div><dt>Visit / detector</dt><dd>${p.visit} / D${p.det}</dd></div></dl>`;
    const total=spectra!.object.spectrum.filter(x=>x.visit===visit).length;
    $('.sd-graph-caption').textContent=`${points.length} usable measurements of ${total} in visit ${visit}. Each dot is an observed sample. Samples marked saturated, missing or S/N < 3 are excluded. The axis scale stays fixed between visits.`;
    void updateBandImage(p);
  }
  async function updateBandImage(p:SpectrumPoint) {
    const id=++imageRequest,s=spectra!,epochs=s.set.frames.epochs.filter(e=>e.det===p.det).sort((a,b)=>Math.abs(a.mjd_mid-p.mjd)-Math.abs(b.mjd_mid-p.mjd));
    const imageBox=$('.sd-mini-frame');
    if(!epochs.length){imageBox.hidden=true;$('.sd-band-image-caption').textContent='';$('.sd-current-image-source').textContent='';$('.sd-image-status').textContent='No detector image is available for this sample. Its measured photometry is still shown.';return;}
    const e=epochs[0];
    if(imageBox.dataset.epoch!==e.id){imageBox.hidden=true;$('.sd-band-image-caption').textContent='';$('.sd-current-image-source').textContent='';$('.sd-image-status').textContent='Loading the real detector image…';}
    try {
      let pending=imageCache.get(e.id);if(!pending){pending=observation(s.set,e,ctl.signal);imageCache.set(e.id,pending);pending.catch(()=>imageCache.delete(e.id));}
      const o=await pending;if(disposed||!visible||stage!==2||id!==imageRequest)return;
      const f=o.set.frames,t=targetBySlug('epsindib')!,at=s.object.gaia?gaiaAt(s.object.gaia,e.mjd_mid):t,pixel=skyToPixel(at,f,e.center??f.center),crop=cropAround([pixel],f.size,20,50);
      const canvas=$<HTMLCanvasElement>('.sd-band-image canvas'),context=canvas.getContext('2d');canvas.width=crop.w;canvas.height=crop.h;
      if(context){const img=context.createImageData(crop.w,crop.h);img.data.set(paintNegative(o.values,f.size[0],crop,displayLimits([o])));context.putImageData(img,0,0);}
      imageBox.hidden=false;imageBox.dataset.epoch=e.id;
      const d=DETECTORS[p.det];$('.sd-band-image-caption').textContent=`D${p.det} · ${d.min}–${d.max} µm · ${formatMjd(e.mjd_mid)} UTC · ${e.n_exposures} exposures. Nearest available detector stack in time. North up, east left.`;
      $('.sd-image-status').textContent='';$('.sd-current-image-source').innerHTML=`<p>Shown image: ${esc(e.id)} · ${sourceLink(new URL(e.data_url,s.set.jsonUrl).href,'NASA-derived image array')}</p>`;
    }catch{if(!disposed&&visible&&stage===2&&id===imageRequest)$('.sd-image-status').textContent='This detector image could not load. The measured spectrum is still available.';}
  }
  function openNotebook() { $<HTMLDetailsElement>('.sd-notebook').open=true;$('.sd-notebook').scrollIntoView({block:'start',behavior:window.matchMedia?.('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'}); }
  $('.sd-new-notebook').onclick=()=>{
    entries=[];try{localStorage.setItem(STORE,'[]');}catch{/* session-only reset */}
    notebook();if(stage===0)pick=null;render();
  };
  const resize=typeof ResizeObserver==='function'?new ResizeObserver(()=>{
    if(visible&&!disposed&&stage===2&&spectra&&host.querySelector('.sd-chart'))drawSpectrum();
  }):undefined;
  resize?.observe(host);
  $('.sd-notebook-jump').onclick=openNotebook;
  host.querySelectorAll<HTMLButtonElement>('[data-stage]').forEach(button=>{
    button.onclick=()=>go(Number(button.dataset.stage));
    button.onkeydown=e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();const next=e.key==='Home'?0:e.key==='End'?2:(stage+(e.key==='ArrowRight'?1:2))%3;go(next);$<HTMLButtonElement>(`[data-stage="${next}"]`).focus();};
  });
  return {
    show(){visible=true;render();},
    hide(){visible=false;stop();imageRequest++;request++;},
    destroy(){disposed=true;visible=false;stop();resize?.disconnect();ctl.abort();imageRequest++;request++;host.replaceChildren();host.classList.remove('sd');},
  };
};
