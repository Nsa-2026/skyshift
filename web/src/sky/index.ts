// P3 placeholder: replaced by the Aladin sky view.
import type { MountSky } from "../app/modules";

export const mountSky: MountSky = (el) => {
  el.innerHTML = `<p class="muted">Sky view</p>`;
  return { goto() {}, epoch() {}, destroy() { el.innerHTML = ""; } };
};
