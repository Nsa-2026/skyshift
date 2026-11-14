// Scoreboard page (#/score): checks V1–V11 from /data/validation.json and the unknown-mover
// suspects from /data/suspects.json. Missing files show a pending state, never fixtures.
import { loadSuspects, loadValidation } from "../api/client";
import type { SuspectsResponse, ValidationResponse } from "../api/types";
import type { MountScore } from "../app/modules";
import { downloadText, suspectsCsv } from "./format";
import { renderPage } from "./render";
import "./score.css";

/** Draw the page for already-loaded data (null = file not published) and wire its controls. */
export function renderScore(el: HTMLElement, v: ValidationResponse | null, sr: SuspectsResponse | null): void {
  el.innerHTML = renderPage(v, sr);
  el.querySelector<HTMLButtonElement>("[data-csv]")?.addEventListener("click", () => {
    if (!sr) return;
    const day = (sr.generated || "").slice(0, 10) || "latest";
    downloadText(suspectsCsv(sr.suspects), `skyshift-suspects-${day}.csv`);
  });
}

export const mountScore: MountScore = (el) => {
  const ctl = new AbortController();
  el.innerHTML = `<div class="sc"><p class="sc-loading" role="status">Loading the scoreboard…</p></div>`;
  Promise.all([loadValidation(ctl.signal), loadSuspects(ctl.signal)]).then(([v, sr]) => {
    if (!ctl.signal.aborted) renderScore(el, v, sr);
  });
  return {
    destroy() {
      ctl.abort();
      el.innerHTML = "";
    },
  };
};
