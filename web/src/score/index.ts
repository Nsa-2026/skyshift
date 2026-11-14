// P3 placeholder: replaced by the scoreboard (reads /data/validation.json).
import type { MountScore } from "../app/modules";

export const mountScore: MountScore = (el) => {
  el.innerHTML = `<h1>Scoreboard</h1>`;
  return { destroy() { el.innerHTML = ""; } };
};
