import { observationTexture } from './sky';

/** A request-owned indicator: stale completion must never hide a newer request. */
export function mountEyeLoading(parent: HTMLElement) {
  const el = document.createElement('div');
  el.className = 'eye-loading'; el.hidden = true;
  el.innerHTML = '<div class="eye-loading-art" aria-hidden="true"><div class="eye-loading-iris"><canvas width="160" height="160"></canvas></div></div><p role="status" aria-live="polite"></p>';
  parent.append(el);
  let serial = 0, pageActive = false, pending = false, textureStarted = false, destroyed = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const stop = () => { clearTimeout(timer); el.hidden = true; };
  function reveal() {
    stop();
    if (!pageActive || !pending) return;
    timer = setTimeout(() => {
      if (!pageActive || !pending || destroyed) return;
      el.hidden = false;
      if (textureStarted) return;
      textureStarted = true;
      observationTexture().then(t => {
        if (destroyed) return;
        const canvas = el.querySelector('canvas')!;
        canvas.getContext('2d')?.drawImage(t, 0, 0, canvas.width, canvas.height);
      }).catch(() => { textureStarted = false; });
    }, 160); // Cached requests should not flash a loading screen.
  }
  return {
    begin(message: string) {
      const ticket = ++serial;
      pending = true; el.querySelector('p')!.textContent = message;
      reveal();
      return () => { if (ticket === serial) { pending = false; stop(); } };
    },
    active(value: boolean) { pageActive = value; if (value) reveal(); else stop(); },
    destroy() { destroyed = true; serial++; pending = false; stop(); el.remove(); },
  };
}
