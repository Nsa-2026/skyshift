// Live search page (#/live, #/live/<zone>): tonight's sky run along the ecliptic, zone by zone.
// Reads the run's progress (load.ts) every 60 s while the page is shown and visible; draws the head
// (what is happening, progress, counters, time left, machines), the all-sky map (the one bold thing)
// and a zone panel (side column on desktop, bottom sheet on phone).
import type { MountPage, PageContext, PageHandle } from "../app/modules";
import { FeedError, loadSkyrun } from "./load";
import {
  axisLabels,
  buildGeometry,
  mapSvg,
  markChanged,
  neighbour,
  ring,
  updateZones,
  type MapGeometry,
  type NavKey,
} from "./map";
import { SkyrunInvalid, changedZones, summarize, zoneCentre, type RunSummary, type SkyRun } from "./model";
import { createMinuteTicker, createRefresher } from "./refresh";
import { fmtDuration, fmtTimePair } from "./format";
import {
  countersHtml,
  ledeText,
  listsHtml,
  machinesHtml,
  progressHtml,
  timeHtml,
  updatedText,
  zonePanelHtml,
} from "./render";
import "./live.css";

// For other pages (the Sky page's "Tonight's search" layer).
export { FeedError, SKYRUN_URL, httpSource, loadSkyrun, type SkyrunSource } from "./load";
export { zonesToPolygons, type ZonePolygon, type Ring } from "./project";
export { parseSkyrun, summarize, zoneCentre, zoneContains, type SkyRun, type SkyZone, type ZoneState } from "./model";

export interface LiveDeps {
  load?: (signal: AbortSignal) => Promise<SkyRun>;
  now?: () => number;
  intervalMs?: number;
  doc?: Document;
}

const NAV_KEYS = new Set(["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"]);
const html = String.raw;

/** One plain sentence for a failed load. */
export function errorSentence(e: unknown, run: SkyRun | null, now: number): string {
  const since = run?.updated != null ? ` Showing the numbers from ${fmtTimePair(run.updated, now)}.` : "";
  if (e instanceof FeedError && (e.kind === "not_connected" || e.kind === "unreachable")) {
    return run
      ? `The live feed isn't answering right now.${since}`
      : "The live feed isn't connected yet. Tonight's progress appears here as soon as the machines publish it.";
  }
  if (e instanceof SkyrunInvalid) return `The progress file couldn't be read.${since}`;
  if (e instanceof FeedError && e.status) return `The live feed answered with an error (${e.status}).${since}`;
  return `The live feed couldn't be loaded.${since}`;
}

/** Calm (ink) when the feed simply isn't there yet; amber when older numbers are shown; red otherwise. */
function errorTone(e: unknown, run: SkyRun | null): string {
  const missing = e instanceof FeedError && (e.kind === "not_connected" || e.kind === "unreachable");
  if (missing && !run) return "lv-calm";
  return run ? "lv-stale" : "";
}

export function createLivePage(el: HTMLElement, ctx: PageContext, deps: LiveDeps = {}): PageHandle {
  const now = deps.now ?? Date.now;
  const doc = deps.doc ?? document;
  el.innerHTML = html`
    <div class="lv">
      <div class="lv-intro">
        <div class="lv-titlerow">
          <h1 class="lv-title">Live search</h1>
          <p class="lv-updated" data-slot="updated"></p>
        </div>
        <p class="lv-lede" data-slot="lede">Cloud machines are reading every SPHEREx photo along the ecliptic tonight, looking for things that move.</p>
        <div class="lv-status" data-slot="status" role="status" aria-live="polite"><p class="lv-muted">Loading tonight's run…</p></div>
        <div class="lv-progress" data-slot="progress"></div>
        <div class="lv-time" data-slot="time"></div>
      </div>
      <div class="lv-facts" data-slot="facts" hidden>
        <div class="lv-counters" data-slot="counters"></div>
        <div class="lv-mach" data-slot="machines"></div>
      </div>
      <div class="lv-stage" data-slot="stage" hidden>
        <figure class="lv-figure">
          <div class="lv-plot" data-slot="plot"></div>
        </figure>
        <aside class="lv-side" aria-label="Zones">
          <section class="lv-panel" data-slot="panel" aria-label="Zone details" hidden></section>
          <div class="lv-lists" data-slot="lists"></div>
        </aside>
      </div>
    </div>`;

  const slot = (name: string) => el.querySelector<HTMLElement>(`[data-slot="${name}"]`)!;
  let run: SkyRun | null = null;
  let sum: RunSummary | null = null;
  let error: unknown = null;
  let geom: MapGeometry | null = null;
  let selected: string | null = null;
  let tabbable: string | null = null;
  let pending: string | null = null;
  let shown = false;

  /** Replace a slot's markup, keeping focus on the control with the same data-key. */
  function fill(name: string, markup: string) {
    const s = slot(name);
    const active = doc.activeElement as HTMLElement | null;
    const key = active && s.contains(active) ? active.dataset?.key : undefined;
    if (s.innerHTML === markup) return;
    s.innerHTML = markup;
    if (key) s.querySelector<HTMLElement>(`[data-key="${key.replace(/"/g, "")}"]`)?.focus();
  }

  const svg = () => slot("plot").querySelector<SVGSVGElement>("svg.lv-svg");

  function renderStatus() {
    const t = now();
    const parts: string[] = [];
    if (error) {
      parts.push(
        `<p class="lv-alert ${errorTone(error, run)}"><span>${errorSentence(error, run, t)}</span> <button type="button" class="btn btn-ghost lv-retry" data-retry data-key="retry">Try again</button></p>`,
      );
    } else if (sum?.stale && sum.age !== null) {
      parts.push(
        `<p class="lv-alert lv-stale">No news from the machines for ${fmtDuration(sum.age)}, so these numbers may be out of date.</p>`,
      );
    }
    fill("status", parts.join(""));
  }

  function renderTimes() {
    if (!run) return;
    sum = summarize(run, now());
    fill("updated", updatedText(sum));
    fill("time", timeHtml(sum, run, now()));
    renderStatus();
  }

  function renderMap() {
    if (!run) return;
    const plot = slot("plot");
    const names = new Set(run.zones.map((z) => z.name));
    if (!tabbable || !names.has(tabbable)) tabbable = names.has("hunt") ? "hunt" : run.zones[0]?.name ?? null;
    if (selected && !names.has(selected)) closePanel(false);
    const hunt = names.has("hunt") ? "hunt" : undefined;
    const next = buildGeometryCached(run);
    const s = svg();
    if (!s || geom?.key !== next.key || !updateZones(s, run.zones, next)) {
      geom = next;
      plot.innerHTML = mapSvg(run.zones, geom, { hunt, selected, tabbable }) + axisLabels(geom, hunt);
    }
    geom = next;
    ring(svg()!, "lv-sel", selected, geom);
    svg()!
      .querySelectorAll<SVGGElement>("g.z")
      .forEach((g) => {
        g.setAttribute("aria-pressed", String(g.dataset.zone === selected));
        g.setAttribute("tabindex", g.dataset.zone === tabbable ? "0" : "-1");
      });
  }

  let geomCache: MapGeometry | null = null;
  function buildGeometryCached(r: SkyRun): MapGeometry {
    const key = r.zones.map((z) => `${z.name}:${z.ra.join(",")}:${z.dec.join(",")}`).join("|");
    if (geomCache?.key !== key) geomCache = buildGeometry(r.zones);
    return geomCache;
  }

  function renderPanel() {
    const panel = slot("panel");
    const z = selected && run ? run.zones.find((q) => q.name === selected) : null;
    if (!z) {
      panel.hidden = true;
      panel.innerHTML = "";
      el.querySelector(".lv")?.classList.remove("has-panel");
      return;
    }
    fill("panel", zonePanelHtml(z, now()));
    panel.hidden = false;
    el.querySelector(".lv")?.classList.add("has-panel");
  }

  function renderAll(prev: SkyRun | null) {
    if (!run) return;
    sum = summarize(run, now());
    fill("lede", ledeText(run, sum));
    fill("progress", progressHtml(sum));
    fill("counters", countersHtml(sum));
    fill("machines", machinesHtml(sum));
    fill("lists", listsHtml(sum, now()));
    slot("facts").hidden = false;
    slot("stage").hidden = false;
    renderTimes();
    renderMap();
    renderPanel();
    if (geom) markChanged(svg()!, changedZones(prev, run), geom);
  }

  function openZone(name: string, opts: { focusMap?: boolean } = {}) {
    if (!run?.zones.some((z) => z.name === name)) return;
    selected = name;
    tabbable = name;
    renderMap();
    renderPanel();
    const want = `#/live/${encodeURIComponent(name)}`;
    if (location.hash !== want) history.replaceState(history.state, "", `${location.pathname}${location.search}${want}`);
    if (opts.focusMap) focusZone(name);
  }

  function closePanel(restoreFocus = true) {
    const was = selected;
    const hadFocus = slot("panel").contains(doc.activeElement);
    selected = null;
    renderPanel();
    if (svg() && geom) ring(svg()!, "lv-sel", null, geom);
    svg()
      ?.querySelectorAll<SVGGElement>("g.z[aria-pressed='true']")
      .forEach((g) => g.setAttribute("aria-pressed", "false"));
    if (location.hash.startsWith("#/live/")) history.replaceState(history.state, "", `${location.pathname}${location.search}#/live`);
    if (restoreFocus && hadFocus && was) focusZone(was);
  }

  function zoneEl(name: string): SVGGElement | null {
    return svg()?.querySelector<SVGGElement>(`g.z[data-zone="${name.replace(/"/g, "")}"]`) ?? null;
  }

  function focusZone(name: string) {
    const g = zoneEl(name);
    if (!g) return;
    svg()!
      .querySelectorAll<SVGGElement>("g.z[tabindex='0']")
      .forEach((o) => o !== g && o.setAttribute("tabindex", "-1"));
    g.setAttribute("tabindex", "0");
    tabbable = name;
    g.focus();
  }

  // ---- events ---------------------------------------------------------------------------------------
  el.addEventListener("click", (ev) => {
    const t = ev.target as Element;
    const z = t.closest<SVGGElement>("g.z");
    if (z?.dataset.zone) {
      openZone(z.dataset.zone);
      return;
    }
    const link = t.closest<HTMLElement>(".lv-zlink");
    if (link?.dataset.zone) {
      openZone(link.dataset.zone);
      return;
    }
    if (t.closest("[data-close]")) return closePanel();
    if (t.closest("[data-retry]")) return refresher.refreshNow();
    if (t.closest("[data-blink]") && selected && run) {
      const z2 = run.zones.find((q) => q.name === selected);
      if (z2) ctx.explore(zoneCentre(z2));
    }
  });

  el.addEventListener("keydown", (ev) => {
    const t = ev.target as Element;
    const z = t.closest?.<SVGGElement>("g.z");
    if (z?.dataset.zone && geom) {
      if (NAV_KEYS.has(ev.key)) {
        ev.preventDefault();
        const next = neighbour(geom, z.dataset.zone, ev.key as NavKey);
        focusZone(next);
        if (selected) openZone(next);
        return;
      }
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        if (selected === z.dataset.zone) closePanel(false);
        else openZone(z.dataset.zone);
        return;
      }
    }
    if (ev.key === "Escape" && selected) {
      ev.preventDefault();
      const fromPanel = slot("panel").contains(t);
      const was = selected;
      closePanel(false);
      if (fromPanel || z) focusZone(was);
    }
  });

  el.addEventListener("focusin", (ev) => {
    const z = (ev.target as Element).closest?.<SVGGElement>("g.z");
    if (z?.dataset.zone && geom && svg()) ring(svg()!, "lv-focus", z.dataset.zone, geom);
  });
  el.addEventListener("focusout", (ev) => {
    const z = (ev.target as Element).closest?.<SVGGElement>("g.z");
    if (z && geom && svg()) ring(svg()!, "lv-focus", null, geom);
  });

  // ---- refresh ------------------------------------------------------------------------------------
  const load = deps.load ?? ((signal: AbortSignal) => loadSkyrun(signal));
  const refresher = createRefresher<SkyRun>({
    load,
    intervalMs: deps.intervalMs ?? 60e3,
    // the refresh clock is the wall clock; deps.now only sets the time the page shows
    doc,
    onData(next) {
      const prev = run;
      run = next;
      error = null;
      renderAll(prev);
      if (pending) {
        const p = pending;
        pending = null;
        openZone(p);
      }
    },
    onError(e) {
      error = e;
      if (!(e instanceof FeedError) && !(e instanceof SkyrunInvalid)) console.warn("skyrun:", e);
      if (!run) {
        slot("progress").innerHTML = "";
        slot("lede").textContent = "Tonight, cloud machines read every SPHEREx photo along the ecliptic, looking for things that move.";
      }
      renderStatus();
    },
  });
  const ticker = createMinuteTicker(() => {
    if (shown) renderTimes();
  }, now);

  return {
    show(param) {
      shown = true;
      if (param) {
        const name = decodeURIComponent(param);
        if (run?.zones.some((z) => z.name === name)) openZone(name);
        else pending = name;
      } else if (selected) closePanel(false);
      if (run) renderTimes();
      refresher.start();
      ticker.start();
    },
    hide() {
      shown = false;
      refresher.stop();
      ticker.stop();
    },
    destroy() {
      shown = false;
      refresher.destroy();
      ticker.stop();
      el.innerHTML = "";
    },
  };
}

export const mountLive: MountPage = (el, ctx) => createLivePage(el, ctx);
