import { afterEach, describe, expect, it, vi } from 'vitest';

describe('real-sky fallback', () => {
  afterEach(() => { vi.restoreAllMocks(); vi.resetModules(); document.body.replaceChildren(); });
  it('uses measured local pixels when WebGL/HiPS cannot start, and stays usable after hiding', async () => {
    vi.doMock('../sky/loader', () => ({ loadAladinOnce: vi.fn(async () => { throw new Error('No WebGL2'); }) }));
    vi.doMock('../api/client', () => ({
      ALLOWED_FOV: { months: [5, 15], hours: [20, 60] }, DEFAULT_FOV: { months: 5, hours: 20 },
      loadFrames: async () => ({ frames: { size: [2, 2], epochs: [{ det: 2 }] }, jsonUrl: '/data/barnard/frames.json' }),
      epochsFor: (data: { epochs: unknown[] }) => data.epochs,
      loadFrameData: async () => new Uint16Array([1, 10, 20, 30]),
      toMJySr: (data: Uint16Array) => Float32Array.from(data),
    }));
    const putImageData = vi.fn(), drawImage = vi.fn();
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
      createImageData: (w: number, h: number) => ({ data: new Uint8ClampedArray(w * h * 4) }),
      putImageData, drawImage,
    } as unknown as CanvasRenderingContext2D);
    const { mountEntrySky } = await import('./sky');
    const host = document.createElement('div'); document.body.append(host);
    const sky = mountEntrySky(host); sky.active(true);
    await vi.waitFor(() => expect(host.textContent).toContain('Local observation'));
    expect(putImageData).toHaveBeenCalledTimes(1); expect(drawImage).toHaveBeenCalled();
    expect(host.classList.contains('has-hips')).toBe(false);
    sky.active(false); expect(host.hidden).toBe(true);
    sky.active(true); expect(host.hidden).toBe(false);
    sky.destroy(); expect(host.childElementCount).toBe(0);
  });
});
