import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mountIntro } from './index';
import { paintScene } from './scene';
vi.mock('./scene', () => ({ paintScene: vi.fn() }));

describe('native scroll controller', () => {
  let controller: ReturnType<typeof mountIntro>;
  let el: HTMLElement;
  const complete = vi.fn(), progress = vi.fn();
  let motion: EventTarget & { matches: boolean };
  beforeEach(() => {
    vi.useFakeTimers(); vi.clearAllMocks();
    vi.stubGlobal('requestAnimationFrame', (fn: () => void) => setTimeout(fn, 16));
    vi.stubGlobal('cancelAnimationFrame', (id: number) => clearTimeout(id));
    motion = Object.assign(new EventTarget(), { matches: false });
    vi.stubGlobal('matchMedia', () => motion);
    const root = document.createElement('div'); document.body.append(root);
    controller = mountIntro(root, { complete, progress });
    el = root.querySelector('.entry-intro')!;
    Object.defineProperties(el, { clientHeight: { value: 800, configurable: true }, scrollHeight: { value: 5200, configurable: true } });
    controller.active(true);
  });
  afterEach(() => { controller.destroy(); document.body.replaceChildren(); vi.useRealTimers(); vi.unstubAllGlobals(); });
  function scroll(top: number) { el.scrollTop = top; el.dispatchEvent(new Event('scroll')); vi.advanceTimersByTime(20); }
  it('does not advance with time and draws the same state when scrubbing back', () => {
    vi.advanceTimersByTime(3000); expect(progress).toHaveBeenLastCalledWith(0);
    scroll(2200); expect(progress).toHaveBeenLastCalledWith(.5);
    scroll(3300); scroll(2200); expect(paintScene).toHaveBeenLastCalledWith(expect.any(HTMLCanvasElement), .5);
    expect(complete).not.toHaveBeenCalled();
  });
  it('completes once even when a touch fling skips intermediate cuts', () => {
    scroll(4400); scroll(4400);
    expect(complete).toHaveBeenCalledTimes(1); expect(complete).toHaveBeenCalledWith(false);
  });
  it('can be skipped immediately and releases the document scroll lock when hidden', () => {
    el.querySelector<HTMLButtonElement>('.entry-skip')!.click();
    expect(complete).toHaveBeenCalledTimes(1); expect(complete).toHaveBeenCalledWith(true);
    controller.active(false);
    expect(el.hidden).toBe(true); expect(document.documentElement.classList.contains('entry-intro-open')).toBe(false);
    const count = progress.mock.calls.length; scroll(2000); expect(progress.mock.calls.length).toBe(count);
  });
  it('responds to a reduced-motion preference changed while watching', () => {
    motion.matches = true; motion.dispatchEvent(new Event('change'));
    expect(complete).toHaveBeenCalledTimes(1); expect(complete).toHaveBeenCalledWith(false);
  });
  it('preserves the camera progress across a phone orientation / viewport resize', () => {
    scroll(2200);
    Object.defineProperties(el, { clientHeight: { value: 600 }, scrollHeight: { value: 3900 } });
    window.dispatchEvent(new Event('resize')); vi.advanceTimersByTime(20);
    expect(el.scrollTop).toBe(1650); expect(progress).toHaveBeenLastCalledWith(.5);
    expect(complete).not.toHaveBeenCalled();
  });
});
