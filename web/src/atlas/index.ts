// Sky, the home page (web/DESIGN.md "Sky: the home page"). The whole SPHEREx sky as a globe you
// can zoom into: candidates as a heat map that resolves into single pencil marks, a preview drawer
// with a live mini blink, and the view in the URL (#/sky/<ra>,<dec>,<fov>[,gal][,mol][,kinds]).
// Aladin Lite draws the globe; without WebGL2 a static Aitoff SVG of the same marks stands in.
import "./atlas.css";
import type { Candidate, CandidateKind, SkyPos } from "../api/types";
import { formatRaDec } from "../app/astro";
import { parseCoords } from "../app/coords";
import type { MountPage, PageContext } from "../app/modules";
import { TARGETS, featuredNear, type Target } from "../app/state";
import { browserHasWebGL2, loadAladinOnce, SkyUnavailable } from "../sky/loader";
import { ALLWISE, SPHEREX, TWO_MASS } from "../sky/layers";
import { createAladinMap, readInk } from "./aladin-map";
import { loadAtlasData, type AtlasData } from "./data";
import { kindIcon, renderCandidate, renderTarget, type DrawerHandle } from "./drawer";
import { createFallbackMap } from "./fallback";
import { parseState, sameState, stateHash, type AtlasState } from "./hash";
import { binCandidates, rampLabels, rampSteps, zoneBoxes, type Cell, type ZoneBox } from "./heat";
import { KINDS, KIND_TEXT, countSentence, oneLine, plural, zoneName } from "./kinds";
import { inView, type BackgroundId, type Layers, type MapHandle, type MapModel, type MapView } from "./map";
import { meanPosition, sepDeg, wrap360 } from "./sphere";
import { LEVEL_FOV, SIGNAL_FOV, SPOT_FOV, WHOLE_FOV, defaultMol, fovToOpen, heatOrder, levelFor, showsSignals, wholeFov, type Level } from "./zoom";
import { cellSideDeg } from "./healpix";

const html = String.raw;
const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);
const HINT_KEY = "skyshift.atlas.hint.v1";
const LIST_MAX = 60;

function storageGet(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}
function storageSet(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* private mode: the hint shows again next time */
  }
}

function prefersReducedMotion(): boolean {
  try {
    return matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

const BACKGROUNDS: { id: BackgroundId; label: string; about: string }[] = [
  { id: "spherex", label: "SPHEREx", about: SPHEREX.about },
  { id: "2mass", label: "2MASS", about: TWO_MASS.about },
  { id: "allwise", label: "AllWISE", about: ALLWISE.about },
];

export const mountAtlas: MountPage = (el: HTMLElement, ctx: PageContext) => {
  el.classList.add("ab");
  el.innerHTML = html`
    <div class="ab-intro">
      <h1 class="ab-lede" id="ab-title">
        Every candidate our pipeline found in NASA's SPHEREx sky.
        <span class="ab-lede-2">Zoom into a red patch, click a signal, and help decide what it is.</span>
      </h1>
      <div class="ab-intro-side">
        <p class="ab-count" data-count aria-live="polite">Loading candidates…</p>
        <a class="btn btn-primary ab-start" href="#/review">Start reviewing</a>
      </div>
    </div>

    <div class="ab-controls">
      <div class="ab-controls-main">
        <div class="seg" role="group" aria-label="Map shape">
          <button type="button" data-proj="globe" aria-pressed="true" title="A globe: the sky as a sphere, half at a time">Globe</button>
          <button type="button" data-proj="mol" aria-pressed="false" title="The whole sky at once (Mollweide projection)">Whole sky</button>
        </div>
        <div class="seg" role="group" aria-label="Coordinate grid">
          <button type="button" data-frame="eq" aria-pressed="true" title="Right ascension and declination">Equatorial</button>
          <button type="button" data-frame="gal" aria-pressed="false" title="Galactic longitude and latitude: the Milky Way runs along the equator">Galactic</button>
        </div>
        <div class="ab-kinds" role="group" aria-label="Kinds of candidate shown">
          ${KINDS.map(
            (k) => html`<button class="ab-kind" type="button" data-kind="${k}" aria-pressed="true" title="${KIND_TEXT[k].meaning}">
              ${kindIcon(k, 18)}<span>${KIND_TEXT[k].plural}</span><span class="ab-kind-n" data-kind-n="${k}"></span>
            </button>`,
          ).join("")}
        </div>
      </div>
      <div class="ab-controls-side">
        <form class="ab-find" role="search">
          <label class="visually-hidden" for="ab-q">Fly to coordinates or a featured name</label>
          <input id="ab-q" class="input ab-find-input" name="q" autocomplete="off" spellcheck="false" placeholder="Fly to coordinates or a name" />
          <button class="btn ab-find-go" type="submit">Fly</button>
        </form>
        <button class="ab-adjust" type="button" aria-expanded="false" aria-controls="ab-more">Adjust</button>
      </div>
    </div>
    <div class="ab-more" id="ab-more" hidden>
      <div class="ab-more-row">
        <span class="ab-more-label" id="ab-bg-label">Background</span>
        <div class="seg" role="group" aria-labelledby="ab-bg-label">
          ${BACKGROUNDS.map((b) => html`<button type="button" data-bg="${b.id}" aria-pressed="${b.id === "spherex"}" title="${b.about}">${b.label}</button>`).join("")}
        </div>
      </div>
      <div class="ab-more-row">
        <span class="ab-more-label" id="ab-plate-label">Plate</span>
        <div class="seg" role="group" aria-labelledby="ab-plate-label">
          <button type="button" data-neg="1" aria-pressed="true" title="Dark stars on light glass, as astronomers blink plates">Negative</button>
          <button type="button" data-neg="0" aria-pressed="false" title="The survey's own colours, bright stars on dark sky">Positive</button>
        </div>
      </div>
      <div class="ab-more-row">
        <button class="ab-switch" type="button" data-layer="heat" aria-pressed="true">Heat map</button>
        <button class="ab-switch" type="button" data-layer="zones" aria-pressed="true">Zones</button>
        <button class="ab-switch" type="button" data-layer="signals" aria-pressed="true">Signals</button>
      </div>
    </div>
    <p class="ab-msg" data-msg role="status" aria-live="polite"></p>

    <div class="ab-stage" data-stage>
      <div class="ab-map" data-map tabindex="0" role="region" aria-roledescription="sky map"
        aria-label="Sky map" aria-describedby="ab-keys"></div>
      <p class="visually-hidden" id="ab-keys">Arrow keys move the map, plus and minus zoom, Enter opens the signal nearest the centre. The list below the map holds every signal in view.</p>
      <div class="ab-note" data-note role="status"><p>Loading the sky…</p></div>
      <div class="ab-hint" data-hint hidden>
        <ol>
          <li>Zoom into a red patch.</li>
          <li>Click a signal.</li>
          <li>Review it.</li>
        </ol>
        <button class="btn btn-ghost ab-hint-x" type="button" data-hint-x>Got it</button>
      </div>
      <div class="ab-tip" data-tip role="tooltip" hidden></div>
      <div class="ab-pin" data-pin hidden>
        <p class="ab-pin-coords" data-pin-coords></p>
        <div class="ab-pin-actions">
          <button class="btn btn-primary" type="button" data-pin-blink>Blink here</button>
          <button class="btn btn-ghost" type="button" data-pin-copy>Copy</button>
          <button class="ab-pin-x" type="button" data-pin-x aria-label="Close">×</button>
        </div>
      </div>
      <p class="ab-status" data-status aria-live="polite"></p>
      <div class="ab-zoom" role="group" aria-label="Zoom">
        <button type="button" data-zoom="in" aria-label="Zoom in">+</button>
        <button type="button" data-zoom="out" aria-label="Zoom out">−</button>
        <button type="button" data-zoom="all" title="Back to the whole sky">Whole sky</button>
      </div>
      <aside class="ab-drawer" data-drawer aria-labelledby="ab-dr-title" hidden></aside>
    </div>

    <div class="ab-below">
      <p class="ab-key" data-key></p>
      <div class="ab-listbox">
        <button class="ab-list-toggle" type="button" aria-expanded="false" aria-controls="ab-list" data-list-toggle></button>
        <ol class="ab-list" id="ab-list" data-list hidden></ol>
      </div>
      <p class="ab-source" data-source></p>
    </div>
  `;

  const q = <T extends Element>(s: string) => el.querySelector(s) as T;
  const stage = q<HTMLDivElement>("[data-stage]");
  const mapEl = q<HTMLDivElement>("[data-map]");
  const note = q<HTMLDivElement>("[data-note]");
  const countEl = q<HTMLParagraphElement>("[data-count]");
  const statusEl = q<HTMLParagraphElement>("[data-status]");
  const msgEl = q<HTMLParagraphElement>("[data-msg]");
  const tip = q<HTMLDivElement>("[data-tip]");
  const pin = q<HTMLDivElement>("[data-pin]");
  const drawerEl = q<HTMLElement>("[data-drawer]");
  const keyEl = q<HTMLParagraphElement>("[data-key]");
  const listEl = q<HTMLOListElement>("[data-list]");
  const listToggle = q<HTMLButtonElement>("[data-list-toggle]");
  const sourceEl = q<HTMLParagraphElement>("[data-source]");
  const hint = q<HTMLDivElement>("[data-hint]");
  const input = q<HTMLInputElement>("#ab-q");
  const adjust = q<HTMLButtonElement>(".ab-adjust");
  const more = q<HTMLDivElement>("#ab-more");

  /** The stage bleeds to the window's edges and ends at the bottom of the first screen. */
  function measure() {
    try {
      el.style.setProperty("--ab-vw", `${document.documentElement.clientWidth}px`);
      const top = stage.getBoundingClientRect().top + scrollY;
      if (top > 0) el.style.setProperty("--ab-top", `${Math.round(top)}px`);
    } catch {
      /* CSS defaults hold */
    }
  }

  const reduced = prefersReducedMotion();
  const ink = readInk();

  // ---- state ---------------------------------------------------------------------------------
  let state: AtlasState = { ra: 0, dec: 0, fov: WHOLE_FOV, gal: false, mol: false, kinds: [...KINDS] };
  let layers: Layers = { heat: true, zones: true, signals: true, background: "spherex", negative: true };
  let data: AtlasData | null = null;
  let shown: Candidate[] = [];
  let zones: ZoneBox[] = [];
  const cellCache = new Map<number, Cell[]>();
  let selected: Candidate | null = null;
  let drawer: DrawerHandle | null = null;
  let map: MapHandle | null = null;
  let visible = false;
  let started = false;
  let destroyed = false;
  let pinPos: SkyPos | null = null;
  let pinAt: { x: number; y: number } | null = null;
  let level: Level = "whole";
  /** "Next nearby" walks outward without revisiting: ids seen in this chain. */
  let chain = new Set<string>();
  let lastFocus: HTMLElement | null = null;

  const cellsAt = (order: number): Cell[] => {
    let c = cellCache.get(order);
    if (!c) {
      c = binCandidates(shown, order);
      cellCache.set(order, c);
    }
    return c;
  };

  function model(): MapModel {
    return { candidates: shown, cellsAt, zones, targets: TARGETS, selectedId: selected?.id ?? null, layers };
  }

  function refilter() {
    const all = data?.candidates ?? [];
    shown = all.filter((c) => state.kinds.includes(c.kind));
    cellCache.clear();
    if (selected && !state.kinds.includes(selected.kind)) closeDrawer();
  }

  // ---- URL -----------------------------------------------------------------------------------
  let hashTimer: ReturnType<typeof setTimeout> | null = null;
  let lastWritten = "";
  function writeHash(now = false) {
    if (hashTimer) clearTimeout(hashTimer);
    const write = () => {
      hashTimer = null;
      if (!visible || destroyed) return;
      const h = stateHash(state);
      if (h === lastWritten && location.hash === h) return;
      lastWritten = h;
      // replaceState never fires hashchange: panning doesn't re-route the app.
      history.replaceState(history.state, "", `${location.pathname}${location.search}${h}`);
    };
    if (now) write();
    else hashTimer = setTimeout(write, 350);
  }

  // ---- counts, key, status, list --------------------------------------------------------------
  function renderCounts() {
    const all = data?.candidates ?? [];
    for (const k of KINDS) {
      const n = all.filter((c) => c.kind === k).length;
      const span = el.querySelector<HTMLSpanElement>(`[data-kind-n="${k}"]`);
      if (span) span.textContent = new Intl.NumberFormat("en-GB").format(n);
    }
    if (!data) return;
    if (data.origin === "none") {
      countEl.textContent = "No candidates yet: the pipeline's list hasn't been published.";
      return;
    }
    countEl.textContent = `${countSentence(all.length, data.zones.length)}.`;
    const when = data.generated ? new Date(data.generated) : null;
    const date =
      when && !Number.isNaN(when.getTime())
        ? when.toLocaleString("en-GB", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC" }) + " UTC"
        : null;
    sourceEl.textContent =
      data.origin === "sample"
        ? "Development preview: showing the pilot sample from fixtures, because /data/candidates.json isn't there yet. Background images: NASA/IPAC IRSA."
        : `SPHEREx ${data.dataRelease ?? ""} candidates${date ? `, exported ${date}` : ""}. Background images: NASA/IPAC IRSA.`;
  }

  function renderKey() {
    const sig = showsSignals(level);
    if (!sig) {
      const order = heatOrder(state.fov, stage.clientWidth || 800);
      const cells = cellsAt(order);
      const steps = rampSteps(cells.reduce((m, c) => Math.max(m, c.count), 0));
      const labels = rampLabels(steps);
      const side = cellSideDeg(order);
      const sw = steps
        .map((_, i) => {
          const a = steps.length <= 1 ? 0.55 : 0.16 + (0.62 * i) / (steps.length - 1);
          return `<span class="ab-swatch" style="--a:${a.toFixed(3)}" aria-hidden="true"></span>${esc(labels[i])}`;
        })
        .join(" ");
      keyEl.innerHTML = `Red cells: candidates per ${side >= 3 ? Math.round(side) : side.toFixed(1)}° cell, ${sw}. ${
        layers.zones ? `<span class="ab-key-zone" aria-hidden="true"></span>Outlined: the zones searched so far. ` : ""
      }${kindIcon("target", 16)} Featured targets.`;
    } else {
      keyEl.innerHTML = `${KINDS.map((k) => `${kindIcon(k, 16)} ${KIND_TEXT[k].name}`).join(", ")}, ${kindIcon("target", 16)} featured target. A moving star's arrow shows where it will be in 100 years.`;
    }
  }

  function renderStatus(v: MapView) {
    if (!data) return;
    const all = data.candidates.length;
    if (!all) {
      statusEl.textContent = "No candidates to show yet.";
      return;
    }
    if (level === "whole") {
      statusEl.textContent = `${countSentence(shown.length, zones.length)}. Zoom into a red patch.`;
    } else if (level === "mid") {
      const n = map ? inView(shown, map, { w: stage.clientWidth, h: stage.clientHeight }).length : 0;
      statusEl.textContent = n ? `${plural(n, "candidate", "candidates")} in view. Zoom in to see each one.` : "No candidates here. Zoom out to find a red patch.";
    } else {
      const n = map ? inView(shown, map, { w: stage.clientWidth, h: stage.clientHeight }).length : 0;
      statusEl.textContent = n ? `${plural(n, "signal", "signals")} in view. Click one to preview its blink.` : "No signals here. Zoom out to find a red patch.";
    }
    void v;
  }

  /** The keyboard path: signals in view at close range, the busiest places otherwise. */
  function renderList() {
    if (!map || !data) {
      listToggle.textContent = "Signals in view";
      listToggle.disabled = true;
      return;
    }
    listToggle.disabled = false;
    if (showsSignals(level)) {
      const items = inView(shown, map, { w: stage.clientWidth, h: stage.clientHeight });
      listToggle.textContent = `Signals in view (${items.length})`;
      listEl.innerHTML = items.length
        ? items
            .slice(0, LIST_MAX)
            .map(
              (c) => html`<li><button type="button" class="ab-list-item" data-id="${esc(c.id)}">${kindIcon(c.kind, 16)}<span>${esc(oneLine(c))}</span><span class="ab-list-pos">${esc(formatRaDec(c))}</span></button></li>`,
            )
            .join("") + (items.length > LIST_MAX ? `<li class="ab-list-more">${items.length - LIST_MAX} more: zoom in to narrow the list.</li>` : "")
        : `<li class="ab-list-more">No signals in view.</li>`;
    } else {
      const order = heatOrder(state.fov, stage.clientWidth || 800);
      const cells = cellsAt(order).slice(0, 12);
      listToggle.textContent = `Busiest places (${cells.length})`;
      listEl.innerHTML = cells.length
        ? cells
            .map(
              (c) => html`<li><button type="button" class="ab-list-item" data-cell="${c.pix}" data-order="${order}"><span class="ab-swatch" style="--a:0.6" aria-hidden="true"></span><span>${esc(plural(c.count, "candidate", "candidates"))}</span><span class="ab-list-pos">near ${esc(formatRaDec(c.center))}</span></button></li>`,
            )
            .join("")
        : `<li class="ab-list-more">No candidates of these kinds.</li>`;
    }
  }

  let viewRaf: ReturnType<typeof setTimeout> | null = null;
  function onView(v: MapView) {
    const nextLevel = levelFor(v.fov);
    const changed = nextLevel !== level;
    level = nextLevel;
    stage.classList.toggle("is-whole", level === "whole");
    state = { ...state, ra: wrap360(v.ra), dec: v.dec, fov: v.fov };
    writeHash();
    hideTip();
    placePin();
    if (changed) renderKey();
    // The list and the status are cheap but not free: once the view settles.
    if (viewRaf) clearTimeout(viewRaf);
    viewRaf = setTimeout(() => {
      viewRaf = null;
      renderStatus(v);
      renderList();
    }, 120);
  }

  // ---- drawer ------------------------------------------------------------------------------
  function openDrawer(fill: (el: HTMLElement) => DrawerHandle) {
    drawer?.destroy();
    if (!drawerEl.contains(document.activeElement)) lastFocus = document.activeElement as HTMLElement | null;
    drawerEl.hidden = false;
    stage.classList.add("has-drawer");
    drawer = fill(drawerEl);
    drawerEl.scrollTop = 0;
    q<HTMLElement>("#ab-dr-title")?.focus({ preventScroll: true });
    // On a phone the drawer is a sheet over the lower half: bring the map to the top so the
    // chosen mark, at the map's centre, stays in sight above it.
    if (phone()) {
      const top = stage.getBoundingClientRect().top;
      if (Math.abs(top) > 4) window.scrollBy({ top, behavior: reduced ? "auto" : "smooth" });
    }
  }

  function phone(): boolean {
    try {
      return matchMedia("(max-width: 720px)").matches;
    } catch {
      return false;
    }
  }

  function closeDrawer() {
    drawer?.destroy();
    drawer = null;
    drawerEl.hidden = true;
    stage.classList.remove("has-drawer");
    const had = selected;
    selected = null;
    chain = new Set();
    if (had) map?.update(model());
    if (lastFocus && document.contains(lastFocus)) lastFocus.focus({ preventScroll: true });
    lastFocus = null;
  }

  function nextNearby(from: Candidate): Candidate | null {
    let best: Candidate | null = null;
    let bestD = Infinity;
    for (const c of shown) {
      if (c.id === from.id || chain.has(c.id)) continue;
      const d = sepDeg(c, from);
      if (d < bestD) {
        bestD = d;
        best = c;
      }
    }
    return best;
  }

  function selectCandidate(c: Candidate, opts: { fly?: boolean; keepChain?: boolean } = {}) {
    hidePin();
    hideTip();
    if (!opts.keepChain) chain = new Set();
    chain.add(c.id);
    selected = c;
    map?.update(model());
    if (opts.fly && map) {
      const v = map.view();
      const fov = showsSignals(levelFor(v.fov)) ? v.fov : SIGNAL_FOV;
      map.flyTo({ ra: c.ra, dec: c.dec, fov }, !reduced);
    }
    openDrawer((d) =>
      renderCandidate(d, c, {
        reducedMotion: reduced,
        red: ink.red,
        hasNext: shown.length > chain.size,
        handlers: {
          review: (k) => ctx.navigate(`#/c/${encodeURIComponent(k.id)}`),
          blink: (k) => ctx.explore({ ra: k.ra, dec: k.dec, clock: k.clock, det: k.det }),
          next: (k) => {
            const n = nextNearby(k);
            if (n) selectCandidate(n, { fly: true, keepChain: true });
          },
          close: closeDrawer,
        },
      }),
    );
  }

  /** Featured targets open their prepared frames (static first): the ?t= deep link of the Blink page. */
  function openTarget(t: Target) {
    // The shell reads ?t= on popstate and opens Explore with the target's static frames.
    history.pushState(null, "", `${location.pathname}?t=${encodeURIComponent(t.slug)}${location.hash}`);
    dispatchEvent(new PopStateEvent("popstate"));
    // If the shell didn't switch pages from the popstate, the hash change will.
    if (!location.hash.startsWith("#/explore")) ctx.navigate("#/explore");
  }

  function selectTarget(t: Target) {
    hidePin();
    selected = null;
    map?.update(model());
    openDrawer((d) => renderTarget(d, t, { open: openTarget, close: closeDrawer }));
  }

  // ---- pin, tooltip --------------------------------------------------------------------------
  function placePin() {
    if (!pinPos || !map) return;
    const p = map.screenOf(pinPos);
    if (!p || p.x < 0 || p.y < 0 || p.x > stage.clientWidth || p.y > stage.clientHeight) {
      pin.style.visibility = "hidden";
      return;
    }
    pin.style.visibility = "";
    pinAt = p;
    const w = pin.offsetWidth || 240;
    const x = Math.max(8, Math.min(stage.clientWidth - w - 8, p.x - w / 2));
    const y = p.y + 14 + (pin.offsetHeight || 80) > stage.clientHeight - 8 ? p.y - (pin.offsetHeight || 80) - 14 : p.y + 14;
    pin.style.transform = `translate(${Math.round(x)}px, ${Math.round(Math.max(8, y))}px)`;
  }

  function showPin(pos: SkyPos) {
    pinPos = { ra: wrap360(pos.ra), dec: pos.dec };
    const near = featuredNear(pinPos);
    q<HTMLParagraphElement>("[data-pin-coords]").textContent = near ? `${formatRaDec(pinPos)}, by ${near.name}` : formatRaDec(pinPos);
    pin.hidden = false;
    placePin();
  }

  function hidePin() {
    pinPos = null;
    pinAt = null;
    pin.hidden = true;
  }
  void pinAt;

  function hideTip() {
    tip.hidden = true;
  }

  function showTip(c: Candidate | null, x: number, y: number) {
    if (!c) return hideTip();
    tip.textContent = oneLine(c);
    tip.hidden = false;
    const w = tip.offsetWidth;
    const left = Math.max(6, Math.min(stage.clientWidth - w - 6, x + 14));
    const top = Math.max(6, y - 34);
    tip.style.transform = `translate(${Math.round(left)}px, ${Math.round(top)}px)`;
  }

  // ---- the map -------------------------------------------------------------------------------
  const callbacks = {
    onView,
    onSignal: (c: Candidate) => selectCandidate(c),
    onTarget: (t: Target) => selectTarget(t),
    onCell: (cell: Cell) => {
      hidePin();
      if (!map) return;
      const lvl = levelFor(map.view().fov);
      map.flyTo({ ...cell.center, fov: fovToOpen(cellSideDeg(cell.order), lvl) }, !reduced);
    },
    onEmpty: (pos: SkyPos) => {
      hideTip();
      if (pinPos && sepDeg(pinPos, pos) < 1e-6) return;
      showPin(pos);
    },
    onHover: (c: Candidate | null, x: number, y: number) => showTip(c, x, y),
  };

  function showNote(text: string | null) {
    note.hidden = text == null;
    if (text != null) note.innerHTML = `<p>${esc(text)}</p>`;
  }

  function startFallback(reason: string | null) {
    if (destroyed) return;
    map?.destroy();
    stage.classList.add("is-fallback");
    stage.classList.toggle("is-whole", levelFor(state.fov) === "whole");
    map = createFallbackMap(mapEl, { view: { ra: state.ra, dec: state.dec, fov: state.fov }, gal: state.gal }, callbacks);
    map.update(model());
    showNote(null);
    msg(
      reason
        ? `The globe can't open here (${reason}), so this is a flat map of the same candidates. Click a red cell to look closer.`
        : null,
    );
    el.querySelectorAll<HTMLButtonElement>("[data-proj], [data-bg], [data-neg]").forEach((b) => (b.disabled = true));
    afterMapReady();
  }

  async function startMap() {
    if (!browserHasWebGL2()) return startFallback("this browser has no WebGL2");
    try {
      const A = await loadAladinOnce();
      if (destroyed) return;
      map = createAladinMap(
        mapEl,
        A,
        { view: { ra: state.ra, dec: state.dec, fov: state.fov }, gal: state.gal, mol: state.mol, layers, reducedMotion: reduced },
        callbacks,
      );
      map.update(model());
      showNote(null);
      afterMapReady();
    } catch (e) {
      if (import.meta.env.DEV) console.error("Sky: the globe failed, using the flat map.", e);
      // Clear whatever Aladin left behind before the flat map takes the stage.
      mapEl.querySelectorAll(".ab-aladin").forEach((n) => n.remove());
      map = null;
      startFallback(e instanceof SkyUnavailable ? e.reason : "the sky viewer couldn't start");
    }
  }

  function afterMapReady() {
    if (!map) return;
    const v = map.view();
    level = levelFor(v.fov);
    renderKey();
    renderStatus(v);
    renderList();
    writeHash(true);
    if (!storageGet(HINT_KEY) && (data?.candidates.length ?? 0) > 0) hint.hidden = false;
  }

  function msg(text: string | null, kind: "info" | "error" = "info") {
    msgEl.textContent = text ?? "";
    msgEl.dataset.kind = kind;
    measure();
  }

  /** Default view: the globe turned to face the most candidates. */
  /**
   * Default view: the whole sky turned to face the most candidates. A wide map opens on the
   * whole-sky ellipse (all of it fits); a tall one (a phone) on the globe, which fills it.
   */
  function defaultState(): AtlasState {
    const c = zones[0]?.center ?? meanPosition(data?.candidates ?? []) ?? { ra: 0, dec: 0 };
    const mol = defaultMol(stage.clientWidth, stage.clientHeight);
    return { ra: c.ra, dec: c.dec, fov: wholeFov(mol), gal: false, mol, kinds: [...KINDS] };
  }

  function applyState(next: AtlasState, animate: boolean) {
    const prev = state;
    state = next;
    if (prev.kinds.join() !== next.kinds.join()) {
      refilter();
      syncKindButtons();
      map?.update(model());
      renderKey();
    }
    if (prev.gal !== next.gal) {
      map?.setFrame(next.gal);
      syncSeg("[data-frame]", next.gal ? "gal" : "eq", "frame");
    }
    if (prev.mol !== next.mol) {
      map?.setProjection(next.mol);
      syncSeg("[data-proj]", next.mol ? "mol" : "globe", "proj");
    }
    map?.flyTo({ ra: next.ra, dec: next.dec, fov: next.fov }, animate && !reduced);
  }

  async function start(param: string | null) {
    started = true;
    try {
      data = await loadAtlasData();
    } catch {
      data = { generated: null, dataRelease: null, zones: [], candidates: [], origin: "none" };
    }
    if (destroyed) return;
    zones = zoneBoxes(data.candidates);
    const fromUrl = parseState(param);
    state = fromUrl ?? defaultState();
    refilter();
    renderCounts();
    measure();
    syncKindButtons();
    syncSeg("[data-frame]", state.gal ? "gal" : "eq", "frame");
    syncSeg("[data-proj]", state.mol ? "mol" : "globe", "proj");
    if (state.mol && state.fov === WHOLE_FOV) state.fov = 360;
    await startMap();
  }

  // ---- controls ------------------------------------------------------------------------------
  function syncSeg(sel: string, value: string, attr: string) {
    el.querySelectorAll<HTMLButtonElement>(sel).forEach((b) => b.setAttribute("aria-pressed", String(b.dataset[attr] === value)));
  }

  function syncKindButtons() {
    el.querySelectorAll<HTMLButtonElement>("[data-kind]").forEach((b) =>
      b.setAttribute("aria-pressed", String(state.kinds.includes(b.dataset.kind as CandidateKind))),
    );
  }

  el.querySelectorAll<HTMLButtonElement>("[data-proj]").forEach((b) =>
    b.addEventListener("click", () => {
      const mol = b.dataset.proj === "mol";
      if (mol === state.mol) return;
      state = { ...state, mol };
      syncSeg("[data-proj]", mol ? "mol" : "globe", "proj");
      map?.setProjection(mol);
      if (map && levelFor(map.view().fov) === "whole") map.flyTo({ ...map.view(), fov: wholeFov(mol) }, false);
      writeHash();
    }),
  );
  el.querySelectorAll<HTMLButtonElement>("[data-frame]").forEach((b) =>
    b.addEventListener("click", () => {
      const gal = b.dataset.frame === "gal";
      if (gal === state.gal) return;
      state = { ...state, gal };
      syncSeg("[data-frame]", gal ? "gal" : "eq", "frame");
      map?.setFrame(gal);
      writeHash();
    }),
  );
  el.querySelectorAll<HTMLButtonElement>("[data-kind]").forEach((b) =>
    b.addEventListener("click", () => {
      const k = b.dataset.kind as CandidateKind;
      const kinds = state.kinds.includes(k) ? state.kinds.filter((x) => x !== k) : KINDS.filter((x) => x === k || state.kinds.includes(x));
      state = { ...state, kinds };
      refilter();
      syncKindButtons();
      map?.update(model());
      renderKey();
      if (map) {
        renderStatus(map.view());
        renderList();
      }
      writeHash();
    }),
  );
  adjust.addEventListener("click", () => {
    const open = adjust.getAttribute("aria-expanded") !== "true";
    adjust.setAttribute("aria-expanded", String(open));
    more.hidden = !open;
    measure();
  });
  el.querySelectorAll<HTMLButtonElement>("[data-bg]").forEach((b) =>
    b.addEventListener("click", () => {
      layers = { ...layers, background: b.dataset.bg as BackgroundId };
      syncSeg("[data-bg]", layers.background, "bg");
      map?.update(model());
    }),
  );
  el.querySelectorAll<HTMLButtonElement>("[data-neg]").forEach((b) =>
    b.addEventListener("click", () => {
      layers = { ...layers, negative: b.dataset.neg === "1" };
      syncSeg("[data-neg]", layers.negative ? "1" : "0", "neg");
      stage.classList.toggle("is-positive", !layers.negative);
      map?.update(model());
    }),
  );
  el.querySelectorAll<HTMLButtonElement>("[data-layer]").forEach((b) =>
    b.addEventListener("click", () => {
      const key = b.dataset.layer as "heat" | "zones" | "signals";
      layers = { ...layers, [key]: !layers[key] };
      b.setAttribute("aria-pressed", String(layers[key]));
      map?.update(model());
      renderKey();
    }),
  );

  q<HTMLFormElement>(".ab-find").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    msg(null);
    const pos = parseCoords(text);
    const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, "");
    if (pos) {
      map?.flyTo({ ...pos, fov: SPOT_FOV }, !reduced);
      showPin(pos);
      return;
    }
    const t = TARGETS.find((x) => [x.name, x.short, x.slug].some((n) => norm(n) === norm(text) || (norm(text).length >= 4 && norm(n).includes(norm(text)))));
    if (t) {
      map?.flyTo({ ra: t.ra, dec: t.dec, fov: SPOT_FOV }, !reduced);
      selectTarget(t);
      return;
    }
    const c = data?.candidates.find((x) => x.id === text);
    if (c) {
      if (!state.kinds.includes(c.kind)) {
        state = { ...state, kinds: KINDS.filter((k) => k === c.kind || state.kinds.includes(k)) };
        refilter();
        syncKindButtons();
      }
      selectCandidate(c, { fly: true });
      return;
    }
    const z = zones.find((x) => norm(zoneName(x.zone)).startsWith(norm(text)) || norm(x.zone) === norm(text));
    if (z) {
      map?.flyTo({ ...z.center, fov: Math.max(2, Math.min(LEVEL_FOV.whole * 0.75, (z.dec1 - z.dec0) * 2.2)) }, !reduced);
      return;
    }
    msg(`“${text}” isn't coordinates or a featured name. Try 122.55 17.00, 08h10m12s +17d00m13s or Barnard's Star.`, "error");
  });

  q<HTMLButtonElement>("[data-pin-blink]").addEventListener("click", () => {
    if (!pinPos) return;
    const p = pinPos;
    hidePin();
    const near = featuredNear(p);
    if (near) openTarget(near);
    else ctx.explore({ ra: p.ra, dec: p.dec });
  });
  q<HTMLButtonElement>("[data-pin-copy]").addEventListener("click", (ev) => {
    if (!pinPos) return;
    const btn = ev.currentTarget as HTMLButtonElement;
    const text = `${pinPos.ra.toFixed(6)} ${pinPos.dec.toFixed(6)}`;
    try {
      navigator.clipboard.writeText(text).then(
        () => {
          btn.textContent = "Copied";
          setTimeout(() => (btn.textContent = "Copy"), 1400);
        },
        () => (btn.textContent = "Copy failed"),
      );
    } catch {
      btn.textContent = "Copy failed";
    }
  });
  q<HTMLButtonElement>("[data-pin-x]").addEventListener("click", hidePin);

  el.querySelectorAll<HTMLButtonElement>("[data-zoom]").forEach((b) =>
    b.addEventListener("click", () => {
      if (!map) return;
      if (b.dataset.zoom === "in") map.zoomBy(1 / 1.6);
      else if (b.dataset.zoom === "out") map.zoomBy(1.6);
      else map.flyTo({ ...map.view(), fov: wholeFov(state.mol) }, !reduced);
    }),
  );

  q<HTMLButtonElement>("[data-hint-x]").addEventListener("click", () => {
    hint.hidden = true;
    storageSet(HINT_KEY, "1");
    mapEl.focus({ preventScroll: true });
  });

  listToggle.addEventListener("click", () => {
    const open = listToggle.getAttribute("aria-expanded") !== "true";
    listToggle.setAttribute("aria-expanded", String(open));
    listEl.hidden = !open;
    if (open) renderList();
  });
  listEl.addEventListener("click", (ev) => {
    const b = (ev.target as Element).closest<HTMLButtonElement>("button.ab-list-item");
    if (!b || !map) return;
    if (b.dataset.id) {
      const c = shown.find((x) => x.id === b.dataset.id);
      if (c) selectCandidate(c, { fly: true });
    } else if (b.dataset.cell) {
      const order = Number(b.dataset.order);
      const cell = cellsAt(order).find((c) => c.pix === Number(b.dataset.cell));
      if (cell) map.flyTo({ ...cell.center, fov: fovToOpen(cellSideDeg(order), levelFor(map.view().fov)) }, !reduced);
    }
  });

  // Keyboard on the map: arrows pan, + and − zoom, Enter opens the signal nearest the centre.
  mapEl.addEventListener("keydown", (ev) => {
    if (!map) return;
    const k = ev.key;
    const pan: Record<string, [number, number]> = { ArrowLeft: [-0.15, 0], ArrowRight: [0.15, 0], ArrowUp: [0, -0.15], ArrowDown: [0, 0.15] };
    if (pan[k]) {
      ev.preventDefault();
      map.panBy(pan[k][0], pan[k][1]);
    } else if (k === "+" || k === "=") {
      ev.preventDefault();
      map.zoomBy(1 / 1.6);
    } else if (k === "-" || k === "_") {
      ev.preventDefault();
      map.zoomBy(1.6);
    } else if (k === "Enter") {
      ev.preventDefault();
      if (showsSignals(level)) {
        const first = inView(shown, map, { w: stage.clientWidth, h: stage.clientHeight })[0];
        if (first) selectCandidate(first);
      } else {
        const order = heatOrder(state.fov, stage.clientWidth || 800);
        const v = map.view();
        const cell = [...cellsAt(order)].sort((a, b) => sepDeg(a.center, v) - sepDeg(b.center, v))[0];
        if (cell) callbacks.onCell(cell);
      }
    }
  });

  const onKey = (ev: KeyboardEvent) => {
    if (ev.key !== "Escape" || !visible) return;
    if (!pin.hidden) hidePin();
    else if (!drawerEl.hidden) closeDrawer();
  };
  document.addEventListener("keydown", onKey);

  // The fallback map redraws on resize itself; Aladin resizes its canvas, the status follows.
  const onResize = () => {
    measure();
    if (map) {
      placePin();
      renderStatus(map.view());
    }
  };
  addEventListener("resize", onResize);
  // Whatever sits above the map (the header, the lede, a message, Adjust) moves its top edge.
  let ro: ResizeObserver | null = null;
  if (typeof ResizeObserver === "function") {
    ro = new ResizeObserver(() => measure());
    for (const n of [document.querySelector("header.top"), q(".ab-intro"), q(".ab-controls"), more, msgEl]) if (n) ro.observe(n);
  }

  return {
    show(param) {
      const wasVisible = visible;
      visible = true;
      measure();
      if (!started) {
        void start(param);
        return;
      }
      const next = parseState(param);
      if (next) {
        if (!sameState(next, state)) applyState(next, wasVisible);
      } else if (wasVisible && data) {
        // The Sky link while already here: back to the whole sky.
        closeDrawer();
        hidePin();
        applyState({ ...defaultState(), gal: state.gal, mol: state.mol, kinds: state.kinds, fov: wholeFov(state.mol) }, true);
      }
      writeHash(true);
    },
    hide() {
      visible = false;
      drawer?.pause();
      hideTip();
      if (hashTimer) clearTimeout(hashTimer);
    },
    destroy() {
      destroyed = true;
      drawer?.destroy();
      map?.destroy();
      document.removeEventListener("keydown", onKey);
      removeEventListener("resize", onResize);
      ro?.disconnect();
      if (hashTimer) clearTimeout(hashTimer);
      el.innerHTML = "";
      el.classList.remove("ab");
    },
  };
};
