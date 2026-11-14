// Placeholder: the Learn page (DESIGN.md "Pages"), replaced by its owner.
import type { MountPage } from "../app/modules";

export const mountLearn: MountPage = (el) => {
  el.innerHTML = `<h1>Learn</h1><p class="muted">How to find things that move in SPHEREx.</p>`;
  return { show() {}, destroy() { el.innerHTML = ""; } };
};
