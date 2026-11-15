import type { MountPage } from '../app/modules';
import { TARGETS } from '../app/state';
import { lessonObservations, drawObservation, displayLimits } from '../product/observations';
import { dateLabel } from '../product/evidence';
import { mountLearning } from './index';
import { expectedPosition, targetCrop } from './model';
import { loadObject } from '../api/client';

export const mountPlay: MountPage = (host, ctx) => {
  host.classList.add('sky-hunt');
  host.lang = 'en';
  const controller = new AbortController();
  const game = mountLearning(host, slug => {
    const t = TARGETS.find(t => t.slug === slug);
    if (t) ctx.explore({ ra: t.ra, dec: t.dec, det: t.det, clock: t.clock, fov: t.fov });
  });
  game.setTargets(TARGETS.map(t => ({ ...t, default_det: t.det, n_frames: 0, n_visits: 0 })));

  // A static preview of three real visits; no animation keeps running when the page is hidden.
  async function preview() {
    try {
      const target = TARGETS.find(t => t.slug === 'barnard')!;
      const field = { ...target, default_det: 6, n_frames: 0, n_visits: 0 };
      const [object, observations] = await Promise.all([
        loadObject(target, controller.signal),
        lessonObservations(field, 6, controller.signal, { fov: 15, all: true }),
      ]);
      if (controller.signal.aborted) return;
      const crop = targetCrop(expectedPosition(field, object, observations[0].epoch.mjd_mid), observations[0].set.frames);
      const limits = displayLimits(observations);
      host.querySelectorAll<HTMLCanvasElement>('.guide-preview canvas').forEach((canvas, i) => {
        const source = document.createElement('canvas');
        drawObservation(source, observations[i], limits);
        canvas.width = 200; canvas.height = 200;
        const context = canvas.getContext('2d');
        if (context) { context.imageSmoothingEnabled = false; context.drawImage(source, crop.x, crop.y, crop.size, crop.size, 0, 0, 200, 200); }
        canvas.nextElementSibling!.textContent = dateLabel(observations[i].epoch.mjd_mid);
      });
      host.querySelector('.guide-preview')?.classList.add('is-ready');
    } catch {
      if (!controller.signal.aborted) host.querySelector('.guide-preview')?.setAttribute('hidden', '');
    }
  }
  void preview();
  return {
    show() { game.activate(true); },
    hide() { game.activate(false); },
    destroy() { controller.abort(); game.destroy(); host.replaceChildren(); host.classList.remove('sky-hunt'); },
  };
};
