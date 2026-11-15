// Results (#/results): one page in sections, built from the areas' own renderers (web/DESIGN.md
// "Pages", "Two audiences"). In order: What we found, How sure we are, Search progress (the Live
// page as a section), List for astronomers (the Community page's consensus list); with Advanced on,
// also Data and methods and Tools (the Tools page as a section).
//
// Routes: #/results/<section>[/<more>] → show("<section>[/<more>]") scrolls to that section and
// opens it. <more> is a check id under "sure" (V5), "suspects" or "distant" under "found", a zone
// under "progress" (hunt), a Tools part under "tools" (batch). A section that needs Advanced turns
// it on: following a link to it asks for it.
//
// Live polling runs only while Results is shown, the progress section is open and near the screen
// (IntersectionObserver); hide() stops it. Until the shell calls show() and hide(), the page follows
// #/results/<section> itself and the observer alone stops the polling when the page is hidden.
import { loadCandidates, loadDistant, loadSuspects, loadValidation } from "../api/client";
import type { CandidatesResponse, DistantResponse, SuspectsResponse, ValidationResponse } from "../api/types";
import type { PageContext, PageHandle } from "../app/modules";
import { mountCommunityWith, type CommunityOptions } from "../community";
import { createLivePage, type LiveDeps, type LivePage } from "../live";
import { getDetails, onDetails, setDetails } from "../prefs/details";
import { createToolsPage } from "../tools";
import { methodsHtml, validationHtml } from "../tools/methods";
import { downloadText, suspectsCsv } from "./format";
import { renderFound, renderSure, syntheticNote } from "./render";
import "./score.css";

export type SectionId = "found" | "sure" | "progress" | "astronomers" | "methods" | "tools";

export interface SectionInfo {
  id: SectionId;
  title: string;
  /** One line under the heading while the section is closed. */
  gist: string;
  /** Shown only with Advanced on. */
  advanced?: boolean;
  /** Closed until someone opens it (or a route asks for it). */
  closed?: boolean;
}

export const SECTIONS: readonly SectionInfo[] = [
  { id: "found", title: "What we found", gist: "The objects our pipeline put up to check, the unknown movers among them, and the search for a distant planet." },
  { id: "sure", title: "How sure we are", gist: "The checks V1–V11 against what is already known, and how much of what is there the search finds." },
  { id: "progress", title: "Search progress", gist: "The run along the ecliptic, zone by zone, as the cloud machines report it." },
  { id: "astronomers", title: "List for astronomers", gist: "What people agreed on, with the next step for each: leads to check, not discoveries." },
  { id: "methods", title: "Data and methods", gist: "Where the pixels come from and what happens to them before a number reaches this page.", advanced: true, closed: true },
  { id: "tools", title: "Tools", gist: "Downloads, batch coordinates, the API, an MCP server for AI assistants, and how to cite.", advanced: true, closed: true },
];

const isSection = (x: string): x is SectionId => SECTIONS.some((s) => s.id === x);

/** "sure/V5" → { section: "sure", more: "V5" }; anything else → null. */
export function parseResultsParam(param: string | null | undefined): { section: SectionId; more: string | null } | null {
  if (!param) return null;
  const [head, ...rest] = param.split("/");
  if (!isSection(head)) return null;
  let more: string | null = rest.join("/") || null;
  if (more) {
    try {
      more = decodeURIComponent(more);
    } catch {
      /* keep it as written */
    }
  }
  return { section: head, more };
}

/** The section part of a Results hash ("#/results/sure/V5" → "sure/V5"), or null. */
export function resultsParamOf(hash: string): string | null {
  const m = /^#\/(?:results|score)\/(.+)$/.exec(hash);
  return m ? m[1] : null;
}

export interface ResultsDeps {
  /** The four static files (default: /data/*.json through src/api). */
  loadValidation?: (signal: AbortSignal) => Promise<ValidationResponse | null>;
  loadSuspects?: (signal: AbortSignal) => Promise<SuspectsResponse | null>;
  loadDistant?: (signal: AbortSignal) => Promise<DistantResponse | null>;
  loadCandidates?: (signal: AbortSignal) => Promise<CandidatesResponse | null>;
  live?: LiveDeps;
  community?: CommunityOptions;
  /** IntersectionObserver for the progress section; null = none (the section counts as on screen). */
  observer?: typeof IntersectionObserver | null;
}

export interface ResultsPage extends PageHandle {
  hide(): void;
  /** Loaded and drawn (tests). */
  ready(): Promise<void>;
}

/** Wire the suspects' "Download CSV" button inside el (null = file not published). */
export function wireSuspectsCsv(el: HTMLElement, sr: SuspectsResponse | null): void {
  el.querySelector<HTMLButtonElement>("[data-csv]")?.addEventListener("click", () => {
    if (!sr) return;
    const day = (sr.generated || "").slice(0, 10) || "latest";
    downloadText(suspectsCsv(sr.suspects), `skyshift-suspects-${day}.csv`);
  });
}

/** Without the shell's context (it mounts Results with the element only): plain navigation. */
function fallbackContext(): PageContext {
  return {
    navigate(hash) {
      location.hash = hash;
    },
    explore(at) {
      const p = new URLSearchParams({ ra: at.ra.toFixed(5), dec: at.dec.toFixed(5) });
      if (at.clock) p.set("clock", at.clock);
      if (at.fov) p.set("fov", String(at.fov));
      if (at.det) p.set("det", String(at.det));
      location.assign(`?${p}#/explore`);
    },
  };
}

const CHEVRON = `<svg class="rs-chev" width="14" height="14" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M2.5 4.5 6 8l3.5-3.5"/></svg>`;

function sectionHtml(s: SectionInfo): string {
  return `<section class="rs-sec" id="rs-${s.id}" data-section="${s.id}" aria-labelledby="rs-${s.id}-h"${s.advanced ? " data-advanced hidden" : ""}>
    <h2 class="rs-h2" id="rs-${s.id}-h"><button type="button" class="rs-toggle" aria-expanded="${!s.closed}" aria-controls="rs-${s.id}-body">${s.title}${CHEVRON}</button></h2>
    <p class="rs-gist"${s.closed ? "" : " hidden"}>${s.gist}</p>
    <div class="rs-body" id="rs-${s.id}-body"${s.closed ? " hidden" : ""}></div>
  </section>`;
}

function pageHtml(): string {
  return `<div class="sc rs">
    <header class="rs-head">
      <h1 class="sc-title">Results</h1>
      <p class="sc-lede">What SkyShift found in NASA's SPHEREx sky, how sure we are of it, how far the search has got, and what goes to astronomers.</p>
      <div class="rs-synthetic"></div>
      <nav class="rs-toc" aria-label="On this page">
        <ul>${SECTIONS.map(
          (s) => `<li${s.advanced ? " data-advanced hidden" : ""}><a href="#/results/${s.id}" data-section="${s.id}">${s.title}</a></li>`,
        ).join("")}</ul>
      </nav>
    </header>
    ${SECTIONS.map(sectionHtml).join("")}
    <p class="rs-more" hidden>More for astronomers, the data and methods behind every number and the tools to reuse them:
      <button type="button" class="rs-more-btn" data-advanced-on>turn on Advanced</button>.</p>
  </div>`;
}

export function createResultsPage(el: HTMLElement, ctx: PageContext, deps: ResultsDeps = {}): ResultsPage {
  el.innerHTML = pageHtml();
  const ctl = new AbortController();
  const $ = <T extends Element>(sel: string) => el.querySelector(sel) as T;
  const sec = (id: SectionId) => $<HTMLElement>(`#rs-${id}`);
  const body = (id: SectionId) => $<HTMLElement>(`#rs-${id}-body`);
  const toggle = (id: SectionId) => $<HTMLButtonElement>(`#rs-${id}-h .rs-toggle`);
  const isOpen = (id: SectionId) => toggle(id).getAttribute("aria-expanded") === "true";

  let pageShown = true;
  /** The shell calls show(): it routes #/results/<section>, so the fallback below stands down. */
  let wired = false;
  let v: ValidationResponse | null = null;

  // ---- 1 and 2: What we found, How sure we are --------------------------------------------------
  body("found").innerHTML = `<p class="sc-loading" role="status">Loading what we found…</p>`;
  body("sure").innerHTML = `<p class="sc-loading" role="status">Loading the checks…</p>`;
  const quiet = <T>(p: Promise<T | null>): Promise<T | null> => p.catch(() => null);
  const loaded = Promise.all([
    quiet((deps.loadValidation ?? loadValidation)(ctl.signal)),
    quiet((deps.loadSuspects ?? loadSuspects)(ctl.signal)),
    quiet((deps.loadDistant ?? loadDistant)(ctl.signal)),
    quiet((deps.loadCandidates ?? loadCandidates)(ctl.signal)),
  ]).then(([val, sr, dist, cands]) => {
    if (ctl.signal.aborted) return;
    v = val;
    $<HTMLElement>(".rs-synthetic").innerHTML = syntheticNote(val, sr, dist, cands);
    body("found").innerHTML = renderFound(sr, dist, cands);
    wireSuspectsCsv(body("found"), sr);
    body("sure").innerHTML = renderSure(val, dist);
    const valid = $<HTMLElement>(".tl-validation");
    if (valid) valid.innerHTML = validationHtml(val);
    // Content above (or inside) the section a route asked for has just been drawn: go there again.
    reanchor();
  });

  // ---- 3: Search progress (the Live page as a section) --------------------------------------------
  const live: LivePage = createLivePage(body("progress"), ctx, { ...deps.live, embedded: true, hashBase: "#/results/progress" });
  const IO = deps.observer === undefined ? (typeof IntersectionObserver === "function" ? IntersectionObserver : null) : deps.observer;
  let liveInView = !IO;
  let liveRunning = false;
  function updateLive() {
    const want = pageShown && liveInView && isOpen("progress");
    if (want && !liveRunning) {
      liveRunning = true;
      live.resume();
    } else if (!want && liveRunning) {
      liveRunning = false;
      live.pause();
    }
  }
  const io = IO
    ? new IO(
        (entries) => {
          for (const e of entries) liveInView = e.isIntersecting;
          updateLive();
        },
        { rootMargin: "300px 0px" },
      )
    : null;
  io?.observe(sec("progress"));

  // ---- 4: List for astronomers (the Community page's consensus list) ------------------------------
  const community = mountCommunityWith(body("astronomers"), ctx, { ...deps.community, variant: "astronomers" });

  // ---- 5 and 6, with Advanced on: Data and methods, Tools ------------------------------------------
  let tools: PageHandle | null = null;
  let toolsShown = false;
  function mountAdvanced() {
    if (!body("methods").firstChild) {
      body("methods").innerHTML = `<div class="tl-sec rs-methods">${methodsHtml()}</div>`;
      if (v) $<HTMLElement>(".tl-validation").innerHTML = validationHtml(v);
    }
    tools ??= createToolsPage(body("tools"), ctx, { embedded: true, hashBase: "#/results/tools" });
  }
  /** The Tools section reads its static files the first time it is opened (its parts scroll here). */
  function showTools() {
    if (!tools || toolsShown) return;
    toolsShown = true;
    tools.show(null);
  }
  function applyAdvanced(on: boolean) {
    el.querySelectorAll<HTMLElement>("[data-advanced]").forEach((n) => (n.hidden = !on));
    $<HTMLElement>(".rs-more").hidden = on;
    if (on) {
      mountAdvanced();
      if (isOpen("tools") && pageShown) showTools();
    }
  }
  const offDetails = onDetails(applyAdvanced);
  applyAdvanced(getDetails());

  // ---- open, close, go to ------------------------------------------------------------------------
  function setOpen(id: SectionId, open: boolean) {
    toggle(id).setAttribute("aria-expanded", String(open));
    body(id).hidden = !open;
    sec(id).querySelector<HTMLElement>(".rs-gist")!.hidden = open;
    if (id === "progress") updateLive();
    if (id === "tools" && open) showTools();
  }

  /** Bring a section, or one check or part of it, to the top of the screen; focus its heading. */
  function scrollToSection(section: SectionId, more: string | null, focus = true) {
    const id = (more ?? "").replace(/[^\w-]/g, "");
    let target: HTMLElement | null = null;
    if (id && section === "sure") target = el.querySelector<HTMLElement>(`#sc-${id}`);
    if (id && section === "found") target = el.querySelector<HTMLElement>(`#sc-${id}-h`);
    if (id && section === "tools") target = el.querySelector<HTMLElement>(`#tl-${id}-h`);
    (target ?? sec(section)).scrollIntoView?.({ block: "start" });
    // A Tools part's heading takes focus itself (tabindex -1); otherwise the section's toggle does.
    if (focus) (section === "tools" && target ? target : toggle(section)).focus({ preventScroll: true });
  }

  // The sections above a route's target fill in after it (files, the live feed, the votes): for a
  // few seconds keep the target in place as they grow, unless the person scrolls or acts first.
  let settle: { section: SectionId; more: string | null; until: number } | null = null;
  function reanchor() {
    if (settle && Date.now() < settle.until) scrollToSection(settle.section, settle.more, false);
    else settle = null;
  }
  const stopSettling = () => (settle = null);
  const INTENT = ["wheel", "touchstart", "keydown", "pointerdown"] as const;
  for (const t of INTENT) addEventListener(t, stopSettling, { capture: true, passive: true });
  const ro = typeof ResizeObserver === "function" ? new ResizeObserver(() => reanchor()) : null;
  ro?.observe(el.firstElementChild!);

  /** Open a section (Advanced first, if it needs it) and bring it, or one part of it, into view. */
  function go(section: SectionId, more: string | null) {
    const info = SECTIONS.find((s) => s.id === section)!;
    if (info.advanced && !getDetails()) setDetails(true);
    if (!isOpen(section)) setOpen(section, true);
    if (section === "progress" && more) {
      live.show(more);
      liveRunning = true;
      updateLive();
    }
    scrollToSection(section, more);
    settle = { section, more, until: Date.now() + 4000 };
  }

  // ---- events ------------------------------------------------------------------------------------
  el.addEventListener("click", (ev) => {
    const t = ev.target as Element;
    const tg = t.closest<HTMLButtonElement>(".rs-toggle");
    if (tg) {
      const id = tg.closest<HTMLElement>(".rs-sec")!.dataset.section as SectionId;
      setOpen(id, !isOpen(id));
      return;
    }
    if (t.closest("[data-advanced-on]")) {
      setDetails(true);
      return;
    }
    // A contents link to where the address already points: no hashchange, so go there here.
    const a = t.closest<HTMLAnchorElement>('a[href^="#/results/"]');
    const me = ev as MouseEvent;
    if (a && location.hash === a.getAttribute("href") && me.button === 0 && !me.metaKey && !me.ctrlKey && !me.shiftKey && !me.altKey) {
      const p = parseResultsParam(resultsParamOf(a.getAttribute("href")!));
      if (p) {
        ev.preventDefault();
        go(p.section, p.more);
      }
    }
  });

  // Until the shell calls show(param): follow #/results/<section> here, after the shell's own scroll.
  const onHash = () => {
    if (wired) return;
    const p = parseResultsParam(resultsParamOf(location.hash));
    if (p) setTimeout(() => !wired && go(p.section, p.more), 0);
  };
  addEventListener("hashchange", onHash);
  onHash();
  community.show(null);
  updateLive();

  return {
    show(param) {
      wired = true;
      pageShown = true;
      community.show(null);
      if (getDetails() && isOpen("tools")) showTools();
      updateLive();
      const p = parseResultsParam(param);
      // The shell scrolls to the top after show(): go there once it has.
      if (p) setTimeout(() => go(p.section, p.more), 0);
    },
    hide() {
      pageShown = false;
      updateLive();
    },
    destroy() {
      ctl.abort();
      io?.disconnect();
      ro?.disconnect();
      for (const t of INTENT) removeEventListener(t, stopSettling, { capture: true });
      offDetails();
      removeEventListener("hashchange", onHash);
      live.destroy();
      community.destroy();
      tools?.destroy();
      el.innerHTML = "";
    },
    ready: () => loaded.then(() => community.ready()),
  };
}

/** What the shell mounts for #/results: today with the element only; pass ctx once it is wired. */
export const mountResults = (el: HTMLElement, ctx?: PageContext, deps?: ResultsDeps): ResultsPage =>
  createResultsPage(el, ctx ?? fallbackContext(), deps);
