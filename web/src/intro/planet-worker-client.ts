import { createEarthMask } from './planet-mask';
import { deferPlanetaryPreparation, earthSpriteSize, installPreparedPlanets, planetaryCacheReady } from './planets';
import type { PlanetPreparationReply } from './planet-preparation';

export type PreparationResult = 'ready' | 'fallback' | 'cancelled';
export interface PlanetPreparation { pending: boolean; ready: Promise<PreparationResult>; cancel(): void }

/** Prepare CPU-heavy textures and geometry away from the thread handling scroll and buttons. */
export function preparePlanetaryOpening(w: number, h: number): PlanetPreparation {
  const size = earthSpriteSize(w, h);
  const done = (result: PreparationResult): PlanetPreparation => ({ pending: false, ready: Promise.resolve(result), cancel() {} });
  if (planetaryCacheReady(size)) return done('ready');
  if (typeof Worker !== 'function') return done('fallback');

  let worker: Worker;
  try {
    worker = new Worker(new URL('./planet-preparation.worker.ts', import.meta.url), { type: 'module' });
  } catch {
    return done('fallback');
  }
  const release = deferPlanetaryPreparation(size);
  let settled = false;
  let resolve!: (result: PreparationResult) => void;
  const ready = new Promise<PreparationResult>(r => { resolve = r; });
  const finish = (result: PreparationResult) => {
    if (settled) return;
    settled = true;
    clearTimeout(timeout);
    worker.onmessage = worker.onerror = worker.onmessageerror = null;
    worker.terminate();
    release();
    resolve(result);
  };
  // An unavailable/cached stale worker chunk must never leave the illustration waiting forever.
  const timeout = setTimeout(() => finish('fallback'), 8000);
  worker.onmessage = ({ data }: MessageEvent<PlanetPreparationReply>) => {
    if (settled) return;
    try {
      if (!data.ok) return finish('fallback');
      installPreparedPlanets(data.planets, size);
      finish('ready');
    } catch {
      finish('fallback');
    }
  };
  worker.onerror = event => { event.preventDefault(); finish('fallback'); };
  worker.onmessageerror = () => finish('fallback');
  try {
    // Own a transferable buffer independently of the canvas implementation's backing store.
    const mask = new Uint8ClampedArray(createEarthMask());
    worker.postMessage({ mask, earthSize: size }, [mask.buffer]);
  } catch {
    finish('fallback');
  }
  return { pending: !settled, ready, cancel: () => finish('cancelled') };
}
