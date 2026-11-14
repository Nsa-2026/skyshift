// P4 placeholder: replaced by flag-a-dot and votes.
import type { MountVote } from "../app/modules";

export const mountVote: MountVote = (el) => {
  el.innerHTML = `<p class="muted">Click a dot in the blink to flag it.</p>`;
  return { pick() {}, view() {} };
};
