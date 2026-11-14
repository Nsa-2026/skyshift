// P4 placeholder: replaced by the blink player. Keeps the seam in src/app/modules.ts.
import type { BlinkHandle, MountBlink } from "../app/modules";

export const mountBlink: MountBlink = (el): BlinkHandle => {
  el.innerHTML = `<p class="muted">Blink player loading…</p>`;
  return {
    async show(view, frames) {
      el.innerHTML = `<p class="muted">${frames.epochs.filter((e) => e.det === view.det).length} frames on the ${view.clock} clock.</p>`;
    },
    status(kind, message) {
      if (kind !== "idle") el.innerHTML = `<p class="muted">${message ?? kind}</p>`;
    },
    destroy() {
      el.innerHTML = "";
    },
  };
};
