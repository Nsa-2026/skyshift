import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { preparePlanetaryOpening } from './planet-worker-client';
import { createEarthMask } from './planet-mask';
import { deferPlanetaryPreparation, installPreparedPlanets, planetaryCacheReady } from './planets';

vi.mock('./planet-mask', () => ({ createEarthMask: vi.fn(() => new Uint8ClampedArray(16)) }));
vi.mock('./planets', () => ({
  earthSpriteSize: (w: number, h: number) => Math.min(w, h) < 600 ? 384 : 768,
  planetaryCacheReady: vi.fn(() => false),
  deferPlanetaryPreparation: vi.fn(() => vi.fn()),
  installPreparedPlanets: vi.fn(),
}));

class FakeWorker {
  static instances: FakeWorker[] = [];
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onmessageerror: (() => void) | null = null;
  postMessage = vi.fn();
  terminate = vi.fn();
  constructor() { FakeWorker.instances.push(this); }
  send(data: unknown) { this.onmessage?.({ data } as MessageEvent); }
}

describe('off-thread planetary preparation lifecycle', () => {
  beforeEach(() => {
    vi.useFakeTimers(); vi.clearAllMocks();
    FakeWorker.instances = [];
    vi.stubGlobal('Worker', FakeWorker);
  });
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

  it('transfers the original coastline mask and installs completed pixels, then releases the worker', async () => {
    const prep = preparePlanetaryOpening(1440, 1000), worker = FakeWorker.instances[0];
    expect(prep.pending).toBe(true);
    const [request, transfer] = worker.postMessage.mock.calls[0];
    expect(request.earthSize).toBe(768);
    expect(transfer).toEqual([request.mask.buffer]);
    const planets: never[] = [];
    worker.send({ ok: true, planets });
    expect(await prep.ready).toBe('ready');
    expect(installPreparedPlanets).toHaveBeenCalledWith(planets, 768);
    expect(worker.terminate).toHaveBeenCalledOnce();
    expect(vi.mocked(deferPlanetaryPreparation).mock.results[0].value).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('cancels immediately on close and ignores a late worker reply', async () => {
    const prep = preparePlanetaryOpening(390, 844), worker = FakeWorker.instances[0];
    const late = worker.onmessage!;
    expect(worker.postMessage.mock.calls[0][0].earthSize).toBe(384);
    prep.cancel(); prep.cancel();
    late({ data: { ok: true, planets: [] } } as unknown as MessageEvent);
    expect(await prep.ready).toBe('cancelled');
    expect(installPreparedPlanets).not.toHaveBeenCalled();
    expect(worker.terminate).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
  });

  it.each(['error', 'messageerror', 'reported-failure', 'timeout', 'invalid-pixels'])('falls back cleanly after %s', async kind => {
    const prep = preparePlanetaryOpening(1440, 1000), worker = FakeWorker.instances[0];
    if (kind === 'error') worker.onerror!(new Event('error', { cancelable: true }));
    if (kind === 'messageerror') worker.onmessageerror!();
    if (kind === 'reported-failure') worker.send({ ok: false });
    if (kind === 'timeout') vi.advanceTimersByTime(8000);
    if (kind === 'invalid-pixels') {
      vi.mocked(installPreparedPlanets).mockImplementationOnce(() => { throw new Error('bad pixels'); });
      worker.send({ ok: true, planets: [] });
    }
    expect(await prep.ready).toBe('fallback');
    expect(worker.terminate).toHaveBeenCalledOnce();
    expect(vi.mocked(deferPlanetaryPreparation).mock.results[0].value).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('keeps the synchronous fallback when Workers are unavailable or blocked', async () => {
    vi.stubGlobal('Worker', undefined);
    expect(await preparePlanetaryOpening(390, 844).ready).toBe('fallback');
    vi.stubGlobal('Worker', class { constructor() { throw new Error('blocked'); } });
    expect(await preparePlanetaryOpening(1440, 1000).ready).toBe('fallback');
    expect(deferPlanetaryPreparation).not.toHaveBeenCalled();
  });

  it('cleans up when coastline rasterization fails', async () => {
    vi.mocked(createEarthMask).mockImplementationOnce(() => { throw new Error('no canvas'); });
    const prep = preparePlanetaryOpening(1440, 1000);
    expect(await prep.ready).toBe('fallback');
    expect(FakeWorker.instances[0].terminate).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('reuses prepared caches on replay without constructing a new worker', async () => {
    vi.mocked(planetaryCacheReady).mockReturnValueOnce(true);
    const prep = preparePlanetaryOpening(1440, 1000);
    expect(prep.pending).toBe(false);
    expect(await prep.ready).toBe('ready');
    expect(FakeWorker.instances).toHaveLength(0);
    expect(createEarthMask).not.toHaveBeenCalled();
  });
});
