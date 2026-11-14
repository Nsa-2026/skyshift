// P3 placeholder: replaced by the readout and spectrum card.
import type { MountCard } from "../app/modules";

export const mountCard: MountCard = (el) => ({
  show(view, object) {
    el.innerHTML = `<h2>${object?.name ?? view.name ?? "This spot"}</h2>`;
  },
  status(kind, message) {
    if (kind !== "idle") el.innerHTML = `<p class="muted">${message ?? kind}</p>`;
  },
});
