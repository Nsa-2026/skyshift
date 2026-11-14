import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mountEyeLoading } from './loading';
vi.mock('./sky', () => ({ observationTexture: vi.fn(() => new Promise(() => {})) }));

describe('request-owned eye loader', () => {
  let loader: ReturnType<typeof mountEyeLoading>, el: HTMLElement;
  beforeEach(() => {
    vi.useFakeTimers();
    loader = mountEyeLoading(document.body); el = document.querySelector('.eye-loading')!;
    loader.active(true);
  });
  afterEach(() => { loader.destroy(); vi.useRealTimers(); });
  it('never delays a fast result to finish the 2.4-second visual cycle', () => {
    const end = loader.begin('Loading'); end(); vi.advanceTimersByTime(500);
    expect(el.hidden).toBe(true);
    const endSlow = loader.begin('Loading'); vi.advanceTimersByTime(180);
    expect(el.hidden).toBe(false); endSlow(); expect(el.hidden).toBe(true);
  });
  it('an aborted old request cannot hide the current request', () => {
    const old = loader.begin('First sky');
    const current = loader.begin('Second sky'); vi.advanceTimersByTime(180); old();
    expect(el.hidden).toBe(false); expect(el.textContent).toContain('Second sky');
    current(); expect(el.hidden).toBe(true);
  });
  it('hides on route changes, resumes a pending query and cleans up delayed work', () => {
    loader.begin('Loading'); loader.active(false); vi.advanceTimersByTime(180);
    expect(el.hidden).toBe(true);
    loader.active(true); vi.advanceTimersByTime(180); expect(el.hidden).toBe(false);
    loader.destroy(); vi.advanceTimersByTime(3000); expect(el.isConnected).toBe(false);
  });
});
