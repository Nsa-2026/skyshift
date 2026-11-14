// Placeholder: the The sky page (DESIGN.md "Pages"), replaced by its owner.
import type { MountPage } from "../app/modules";

export const mountAtlas: MountPage = (el) => {
  el.innerHTML = `<h1>The sky</h1><p class="muted">Every candidate on the whole SPHEREx sky. Zoom in to see them.</p>`;
  return { show() {}, destroy() { el.innerHTML = ""; } };
};
