// Results (#/results, #/results/<section>): one page in sections (page.ts). "What we found" and
// "How sure we are" read /data/candidates.json, /data/suspects.json, /data/distant.json and
// /data/validation.json; missing files show a pending state, never fixtures.
import type { DistantResponse, SuspectsResponse, ValidationResponse } from "../api/types";
import { wireSuspectsCsv } from "./page";
import { renderPage } from "./render";
import "./score.css";

export {
  SECTIONS,
  createResultsPage,
  mountResults,
  parseResultsParam,
  wireSuspectsCsv,
  type ResultsDeps,
  type ResultsPage,
  type SectionId,
} from "./page";
// The shell mounts Results as before (mountScore(el)); the handle also has show(param) and hide(),
// and mountScore(el, ctx) takes the shell's context once it passes one (see page.ts).
export { mountResults as mountScore } from "./page";

/** Draw "How sure we are" and "What we found" in one block for already-loaded data, and wire the CSV. */
export function renderScore(
  el: HTMLElement,
  v: ValidationResponse | null,
  sr: SuspectsResponse | null,
  dist: DistantResponse | null = null,
): void {
  el.innerHTML = renderPage(v, sr, dist);
  wireSuspectsCsv(el, sr);
}
