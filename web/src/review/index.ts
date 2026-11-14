// Placeholders: the Review queue and the Candidate page (DESIGN.md "Pages"), replaced by their owner.
import type { MountPage } from "../app/modules";

export const mountReview: MountPage = (el) => {
  el.innerHTML = `<h1>Review</h1>`;
  return { show() {}, destroy() { el.innerHTML = ""; } };
};

export const mountCandidate: MountPage = (el) => {
  return {
    show(id) {
      el.innerHTML = `<h1>Candidate</h1><p class="muted">${id ?? ""}</p>`;
    },
    destroy() { el.innerHTML = ""; },
  };
};
