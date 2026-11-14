import { loadAladinOnce } from '../sky/loader';
import { SPHEREX, cubeFrameForDet } from '../sky/layers';
import { epochsFor, loadFrameData, loadFrames, toMJySr } from '../api/client';
import { TARGETS, viewForTarget } from '../app/state';
import type { AladinInstance } from '../sky/aladin';

/** A real local observation is also the offline / no-WebGL background and loader texture. */
let localTexture: Promise<HTMLCanvasElement> | undefined;
export function observationTexture(): Promise<HTMLCanvasElement> {
  return localTexture ??= (async () => {
    const view = viewForTarget(TARGETS[0]);
    const loaded = await loadFrames(view);
    const epoch = epochsFor(loaded.frames, view.det)[0];
    if (!epoch) throw new Error('No local SPHEREx observation');
    const values = toMJySr(await loadFrameData(epoch, loaded.jsonUrl), epoch);
    const sorted = values.filter(Number.isFinite).sort();
    if (!sorted.length) throw new Error('No measured pixels');
    const lo = sorted[Math.floor(sorted.length * .30)];
    const hi = Math.max(lo + 1e-6, sorted[Math.floor(sorted.length * .997)]);
    const c = document.createElement('canvas');
    [c.width, c.height] = loaded.frames.size;
    const ctx = c.getContext('2d');
    if (!ctx) throw new Error('Canvas unavailable');
    const pixels = ctx.createImageData(c.width, c.height);
    for (let i = 0; i < values.length; i++) {
      const t = Number.isFinite(values[i]) ? Math.asinh(12 * Math.max(0, Math.min(1, (values[i] - lo) / (hi - lo)))) / Math.asinh(12) : 0;
      const light = Math.round(t * 232);
      pixels.data.set([5 + light, 9 + light, 15 + light, 255], i * 4);
    }
    ctx.putImageData(pixels, 0, 0);
    return c;
  })().catch(error => { localTexture = undefined; throw error; });
}

export function mountEntrySky(host: HTMLElement) {
  host.inert = true;
  host.innerHTML = '<div class="entry-sky-field"><canvas class="entry-local-sky"></canvas><div class="entry-hips"></div></div><p class="entry-sky-credit">SPHEREx · NASA / JPL-Caltech / IPAC · 红外观测</p>';
  const field = host.querySelector<HTMLElement>('.entry-sky-field')!;
  const canvas = host.querySelector<HTMLCanvasElement>('canvas')!;
  const hips = host.querySelector<HTMLElement>('.entry-hips')!;
  const credit = host.querySelector<HTMLElement>('.entry-sky-credit')!;
  let started = false;
  let destroyed = false;
  let active = false;
  let al: AladinInstance | undefined;
  let texture: HTMLCanvasElement | undefined;
  let generation = 0;
  function resize() {
    if (!texture) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    canvas.width = Math.round(window.innerWidth * dpr);
    canvas.height = Math.round(window.innerHeight * dpr);
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const scale = Math.max(canvas.width / texture.width, canvas.height / texture.height);
    ctx.drawImage(texture, (canvas.width - texture.width * scale) / 2, (canvas.height - texture.height * scale) / 2, texture.width * scale, texture.height * scale);
  }
  async function start() {
    if (started) return;
    started = true;
    observationTexture().then(t => {
      if (destroyed) return;
      texture = t; resize();
      if (!host.classList.contains('has-hips')) credit.textContent = 'SPHEREx · Barnard’s Star 天区 · NASA / IPAC · 本地真实观测';
    }).catch(() => { if (!destroyed && !host.classList.contains('has-hips')) credit.textContent = '星图暂不可用 · 仍可直接进入探索'; });
    const ticket = ++generation;
    try {
      const A = await loadAladinOnce({ timeoutMs: 5000 });
      if (destroyed || ticket !== generation) return;
      if (!active) { started = false; return; }
      al = A.aladin(hips, {
        target: '266 -29', fov: 50, cooFrame: 'ICRS', projection: 'SIN', log: false,
        showReticle: false, showZoomControl: false, showFullscreenControl: false,
        showLayersControl: false, showGotoControl: false, showShareControl: false,
        showCooGridControl: false, showFrame: false, showStatusBar: false, showProjectionControl: false,
        showSimbadPointerControl: false, showContextMenu: false,
        survey: SPHEREX.url,
      });
      const layer = A.HiPS(SPHEREX.url, {
        name: 'SPHEREx infrared sky', imgFormat: SPHEREX.format,
        successCallback(h) {
          if (destroyed) return;
          h.setSliceNumber(cubeFrameForDet(2));
          host.classList.add('has-hips');
          credit.textContent = 'SPHEREx DR1 · 1.38 μm · NASA / JPL-Caltech / IPAC (IRSA)';
        },
        errorCallback() { host.classList.remove('has-hips'); credit.textContent = 'SPHEREx · Barnard’s Star 天区 · 本地真实观测'; },
      });
      al.setBaseImageLayer(layer);
      al.gotoRaDec(266, -29);
      al.setFoV(50);
    } catch { /* The already loaded local observation remains available. */ }
  }
  window.addEventListener('resize', resize, { passive: true });
  return {
    active(value: boolean) {
      active = value;
      host.hidden = !value;
      field.classList.remove('is-idle');
      if (value) void start();
    },
    progress(p: number, idle: boolean) {
      field.classList.toggle('is-idle', idle);
      const camera = Math.min(p, .9);
      field.style.transform = `scale(${1.12 + camera * .08}) rotate(${-2 + camera * 3}deg)`;
    },
    destroy() { destroyed = true; generation++; window.removeEventListener('resize', resize); host.replaceChildren(); al = undefined; },
  };
}
