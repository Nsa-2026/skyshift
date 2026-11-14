// Placeholder: the Live search page (DESIGN.md "Pages"), replaced by its owner.
import type { MountPage } from "../app/modules";

export const mountLive: MountPage = (el) => {
  el.innerHTML = `<h1>Live search</h1><p class="muted">Tonight's run across the ecliptic, zone by zone.</p>`;
  return { show() {}, destroy() { el.innerHTML = ""; } };
};
