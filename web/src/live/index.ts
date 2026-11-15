// Live search page (#/live, #/live/<zone>): tonight's sky run along the ecliptic, zone by zone.
// Reads the run's progress (load.ts) every 60 s while the page is shown and visible; draws the head
// (what is happening, progress, counters, time left, machines), the all-sky map (the one bold thing)
// and a zone panel (side column on desktop, bottom sheet on phone).
import type { MountPage, PageContext, PageHandle } from "../app/modules";
import type { Suspect } from "../api/types";
import { FeedError, loadSkyrun, loadZoneSuspectsFile, toZoneSuspects, type ZoneSuspects } from "./load";
import {
  axisLabels,
  buildGeometry,
  mapSvg,
  markChanged,
  markSuspects,
  neighbour,
  ring,
  updateZones,
  type MapGeometry,
  type NavKey,
} from "./map";
import { SkyrunInvalid, changedZones, isProvisional, summarize, zoneCentre, type RunSummary, type SkyRun, type SkyZone } from "./model";
import { createMinuteTicker, createRefresher } from "./refresh";
import { fmtDuration, fmtTimePair } from "./format";
import {
  countersHtml,
  ledeText,
  listsHtml,
  machinesHtml,
  progressHtml,
  suspectsHtml,
  timeHtml,
  updatedText,
  zonePanelHtml,
  type SuspectsState,
} from "./render";
import "./live.css";

// For other pages (the Sky page's "Tonight's search" layer).
export { FeedError, SKYRUN_URL, httpSource, loadSkyrun, loadZoneSuspects, loadZoneSuspectsFile, type SkyrunSource, type ZoneSuspects } from "./load";
export { zonesToPolygons, type ZonePolygon, type Ring } from "./project";
export { isProvisional, parseSkyrun, summarize, zoneCentre, zoneContains, type SkyRun, type SkyZone, type ZoneState } from "./model";

export interface LiveDeps {
  load?: (signal: AbortSignal) => Promise<SkyRun>;
  now?: () => number;
  intervalMs?: number;
  doc?: Document;
  /** A zone's suspects from its suspects_url (default: loadZoneSuspectsFile). A plain list of rows works too. */
  loadSuspects?: (url: string, signal: AbortSignal) => Promise<Suspect[] | ZoneSuspects>;
  /** A section of another page (Results → Search progress): that page gives the heading, so no h1 here. */
  embedded?: boolean;
  /** The route an open zone is written under, as <hashBase>/<zone>. Default "#/live". */
  hashBase?: string;
  /**
   * Plain words for a host page (Results): with Advanced off only the progress in plain words, the bar,
   * one line and the map show; the legends, pace and deadline sums, machines and zone ids are marked
   * "pro-only" (src/prefs/details.ts) and come back with Advanced on. Off by default.
   */
  plain?: boolean;
}

/** The Live page's handle, plus pause and resume for a host that shows it as a section (Results). */
export interface LivePage extends PageHandle {
  /** Start refreshing again and keep the open zone as it is (show(param) sets the zone from a route). */
  resume(): void;
  /** Stop refreshing and the minute ticker, like hide(). */
  pause(): void;
}

/** What a loader gave: a read file as is; a bare list as a file without the vetting's zone fields. */
function asZoneSuspects(x: Suspect[] | ZoneSuspects): ZoneSuspects {
  return Array.isArray(x) ? toZoneSuspects({ suspects: x }) : x;
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

export function createLivePage(el: HTMLElement, ctx: PageContext, deps: LiveDeps = {}): LivePage {
  const now = deps.now ?? Date.now;
  const hashBase = deps.hashBase ?? "#/live";
  const loadSuspects = deps.loadSuspects ?? ((url: string, signal: AbortSignal) => loadZoneSuspectsFile(url, signal));
  const doc = deps.doc ?? document;
  const plain = deps.plain === true;
  const pro = plain ? " pro-only" : "";
  el.innerHTML = html`
    <div class="lv${deps.embedded ? " lv-embedded" : ""}${plain ? " lv-plain" : ""}">
      <div class="lv-intro">
        <div class="lv-titlerow">
          ${deps.embedded ? "" : html`<h1 class="lv-title">Live search</h1>`}
          <p class="lv-updated" data-slot="updated"></p>
        </div>
        <p class="lv-lede${pro}" data-slot="lede">Cloud machines are reading every SPHEREx photo along the ecliptic tonight, looking for things that move.</p>
        <div class="lv-status" data-slot="status" role="status" aria-live="polite"><p class="lv-muted">Loading tonight's run…</p></div>
        <div class="lv-progress" data-slot="progress"></div>
        <div class="lv-time${pro}" data-slot="time"></div>
      </div>
      <div class="lv-facts${pro}" data-slot="facts" hidden>
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
    } else if (run?.paused_at != null) {
      parts.push(`<p class="lv-alert lv-calm">The search is paused. These are the numbers from when it stopped, ${fmtTimePair(run.paused_at, t)}.</p>`);
    } else if (sum?.stale && sum.age !== null) {
      parts.push(
        // plain (Results): "Updated … ago" above says it already
        `<p class="lv-alert lv-stale${pro}">No news from the machines for ${fmtDuration(sum.age)}, so these numbers may be out of date.</p>`,
      );
    }
    fill("status", parts.join(""));
  }

  function renderTimes() {
    if (!run) return;
    sum = summarize(run, now());
    fill("updated", updatedText(sum));
    // Results (plain): no run-end and deadline sums, no machines (user, 15 Nov): just the rate.
    fill("time", plain ? "" : timeHtml(sum, run, now()));
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
      // The hunt zone's name is an internal one: plain view leaves it out (the box labels: live.css).
      if (plain) plot.querySelector(".lv-ax-hunt")?.classList.add("pro-only");
    }
    geom = next;
    ring(svg()!, "lv-sel", selected, geom);
    markSuspects(svg()!, selected ? susRows : [], geom, susProv);
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
      showSuspects(null);
      return;
    }
    const active = doc.activeElement as HTMLElement | null;
    const key = active && panel.contains(active) ? active.dataset?.key : undefined;
    fill("panel", zonePanelHtml(z, now(), run!.notes));
    panel.hidden = false;
    el.querySelector(".lv")?.classList.add("has-panel");
    showSuspects(z);
    if (key && !panel.contains(doc.activeElement)) panel.querySelector<HTMLElement>(`[data-key="${key.replace(/"/g, "")}"]`)?.focus();
  }

  // ---- the selected zone's suspects: loaded on demand, cached for the page's life ------------------
  const susCache = new Map<string, ZoneSuspects>();
  let susCtl: AbortController | null = null;
  let susRows: Suspect[] = [];
  let susProv = false;

  function paintSuspects(z: SkyZone, state: SuspectsState) {
    const section = slot("panel").querySelector<HTMLElement>("[data-sus]");
    if (section) section.innerHTML = suspectsHtml(z, state, run?.notes);
    susRows = state.status === "ok" ? state.rows : [];
    susProv = isProvisional(z);
    if (svg() && geom) markSuspects(svg()!, susRows, geom, susProv);
  }

  function showSuspects(z: SkyZone | null) {
    const url = z?.suspects_url ?? null;
    if (!z || !url) {
      susCtl?.abort();
      susCtl = null;
      susRows = [];
      if (svg() && geom) markSuspects(svg()!, [], geom);
      return;
    }
    const cached = susCache.get(url);
    if (cached) return paintSuspects(z, { status: "ok", rows: cached.rows, file: cached });
    susCtl?.abort();
    const ctl = new AbortController();
    susCtl = ctl;
    paintSuspects(z, { status: "loading" });
    loadSuspects(url, ctl.signal).then(
      (got) => {
        const file = asZoneSuspects(got);
        susCache.set(url, file);
        if (ctl.signal.aborted || selected !== z.name) return;
        paintSuspects(z, { status: "ok", rows: file.rows, file });
      },
      () => {
        if (ctl.signal.aborted || selected !== z.name) return;
        paintSuspects(z, { status: "error" });
      },
    );
  }

  function renderAll(prev: SkyRun | null) {
    if (!run) return;
    sum = summarize(run, now());
    fill("lede", ledeText(run, sum));
    fill("progress", progressHtml(sum, now(), run.notes, plain));
    fill("counters", countersHtml(sum));
    fill("machines", plain ? "" : machinesHtml(sum));
    fill("lists", listsHtml(sum, now(), plain));
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
    const want = `${hashBase}/${encodeURIComponent(name)}`;
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
    if (location.hash.startsWith(`${hashBase}/`)) history.replaceState(history.state, "", `${location.pathname}${location.search}${hashBase}`);
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
    const sus = t.closest<HTMLElement>(".lv-sus-blink");
    if (sus) {
      const r = susRows[Number(sus.dataset.sus)];
      if (r) ctx.explore({ ra: r.ra, dec: r.dec, clock: "hours", det: r.det });
      return;
    }
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

  function resume() {
    shown = true;
    if (run) renderTimes();
    refresher.start();
    ticker.start();
  }
  function pause() {
    shown = false;
    refresher.stop();
    ticker.stop();
  }

  return {
    show(param) {
      if (param) {
        const name = decodeURIComponent(param);
        if (run?.zones.some((z) => z.name === name)) openZone(name);
        else pending = name;
      } else if (selected) closePanel(false);
      resume();
    },
    hide: pause,
    resume,
    pause,
    destroy() {
      shown = false;
      refresher.destroy();
      ticker.stop();
      susCtl?.abort();
      el.innerHTML = "";
    },
  };
}

export const mountLive: MountPage = (el, ctx) => createLivePage(el, ctx);
