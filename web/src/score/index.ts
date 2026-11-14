// Scoreboard page (#/score): checks V1–V11 from /data/validation.json, the unknown-mover
// suspects from /data/suspects.json and the distant-planet search from /data/distant.json.
// Missing files show a pending state, never fixtures.
import { loadDistant, loadSuspects, loadValidation } from "../api/client";
import type { DistantResponse, SuspectsResponse, ValidationResponse } from "../api/types";
import type { MountScore } from "../app/modules";
import { downloadText, suspectsCsv } from "./format";
import { renderPage } from "./render";
import "./score.css";

/** Draw the page for already-loaded data (null = file not published) and wire its controls. */
export function renderScore(
  el: HTMLElement,
  v: ValidationResponse | null,
  sr: SuspectsResponse | null,
  dist: DistantResponse | null = null,
): void {
  el.innerHTML = renderPage(v, sr, dist);
  el.querySelector<HTMLButtonElement>("[data-csv]")?.addEventListener("click", () => {
    if (!sr) return;
    const day = (sr.generated || "").slice(0, 10) || "latest";
    downloadText(suspectsCsv(sr.suspects), `skyshift-suspects-${day}.csv`);
  });
}

export const mountScore: MountScore = (el) => {
  const ctl = new AbortController();
  el.innerHTML = `<div class="sc"><p class="sc-loading" role="status">Loading the scoreboard…</p></div>`;
  Promise.all([loadValidation(ctl.signal), loadSuspects(ctl.signal), loadDistant(ctl.signal)]).then(([v, sr, dist]) => {
    if (!ctl.signal.aborted) renderScore(el, v, sr, dist);
  });
  return {
    destroy() {
      ctl.abort();
      el.innerHTML = "";
    },
  };
};
