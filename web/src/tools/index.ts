// Placeholder: the Tools and data page (DESIGN.md "Pages"), replaced by its owner.
import type { MountPage } from "../app/modules";

export const mountTools: MountPage = (el) => {
  el.innerHTML = `<h1>Tools and data</h1><p class="muted">Downloads, the API and batch lookups for professionals.</p>`;
  return { show() {}, destroy() { el.innerHTML = ""; } };
};
