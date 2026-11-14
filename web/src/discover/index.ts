// Placeholder: the Discover page (DESIGN.md "Pages"), replaced by its owner.
import type { MountPage } from "../app/modules";

export const mountDiscover: MountPage = (el) => {
  el.innerHTML = `<h1>Discover</h1><p class="muted">Candidates from the pipeline, for people to check.</p>`;
  return { show() {}, destroy() { el.innerHTML = ""; } };
};
