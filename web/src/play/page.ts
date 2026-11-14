import type { MountPage } from '../app/modules';
import { TARGETS } from '../app/state';
import { mountLearning } from './index';
import '../product/workbench.css';

export const mountPlay: MountPage = (host, ctx) => {
  const game = mountLearning(host, slug => {
    const target = TARGETS.find(t => t.slug === slug);
    if (target) ctx.explore({ ra: target.ra, dec: target.dec, det: target.det, clock: target.clock, fov: target.fov });
  });
  game.setTargets(TARGETS.map(t => ({ ...t, default_det: t.det, n_frames: 0, n_visits: 0 })));
  return {
    show() { game.activate(true); },
    hide() { game.activate(false); },
    destroy() { game.destroy(); host.replaceChildren(); },
  };
};
