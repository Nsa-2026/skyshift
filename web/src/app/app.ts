// Page shell: coordinate box, target chips, URL state, and the wiring between areas.
// Areas plug in through the interfaces in ./modules.ts.
import { ApiFailure, loadFrames, loadObject, resolve } from "../api/client";
import type { FramesResponse, ObjectResponse } from "../api/types";
import { mountBlink } from "../blink";
import { mountCard } from "../card";
import { mountScore } from "../score";
import { mountSky } from "../sky";
import { mountFilters } from "../colour";
import { mountVote } from "../vote";
import { mountDiscover } from "../discover";
import { mountAtlas } from "../atlas";
import { mountLearn } from "../learn";
import { mountTools } from "../tools";
import { mountLive } from "../live";
import { mountDetailsSwitch } from "../prefs/details";
import { mountCandidate, mountReview } from "../review";
import { mountAbout, mountCommunity } from "../community";
import { mountWorkbench, targetFor, type Layer } from "../product/workbench";
import {MODES, workspaceHref, type WorkspaceState} from "../product/workspace-state";
import {mountGuidedPractice} from '../product/practice';
import {SCREENS,siteRoute,viewerState} from './navigation';
import {legacyHash} from './legacy-routes';
import {getDetails,onDetails,setDetails} from '../prefs/details';
import {observationForSky,type SkySelection} from './sky-entry';
import { loadFrames as loadScienceFrames } from "../api/science-client";
import { mountOpening } from "../intro";
import { browserEnv, landingUnderOpening } from "../intro/rules";
import { formatRaDec } from "./astro";
import { featuredNear } from "./state";
import { parseCoords } from "./coords";
import type { BlinkHandle, CardHandle, MountPage, PageContext, PageHandle, SkyHandle, View, VoteHandle } from "./modules";
import { TARGETS, isEmbed, readView, targetBySlug, viewForTarget, writeView } from "./state";

const html = String.raw;
/** Each page's name in the browser tab ("Candidates: SkyShift"); the home page is plain SkyShift. */
const PAGE_TITLES: Record<string, string> = {
  discover: "To check",
  review: "To check, one at a time",
  explore: "Viewer",
  live: "Live search",
  community: "Community",
  results: "Results",
  learn: "Learn",
  tools: "Tools and data",
  about: "About",
};
/** How the Workspace names each image source (Research → Processing). */
const LAYER_LABELS: Record<Layer, string> = {
  raw: "SPHEREx observations",
  model: "Static sky model",
  resid: "Model residual",
  refdiff: "DR1 reference difference",
};

export function startApp(root: HTMLElement): void {
  const embed = isEmbed();
  // Remember the entry URL before the atlas writes its current sky into the hash.
  const openingEnv = browserEnv();
  // The opening ends on the Sky, not the Viewer: a Viewer address becomes #/sky before anything loads.
  const landing = embed ? null : landingUnderOpening(openingEnv);
  if (landing) history.replaceState(history.state, "", `${location.pathname}${landing}`);
  root.classList.toggle('cosmic-shell', !embed);
  root.innerHTML = html`
    ${embed ? '' : '<div class="site-sky" aria-hidden="true"></div>'}
    <header class="top" ${embed ? "hidden" : ""}>
      <div class="brand">
        <a class="wordmark" href="#/sky" aria-label="SkyShift sky map">SkyShift</a>
        <p class="lede">Find what moves in NASA's SPHEREx sky, together.</p>
      </div>
      <nav class="nav" aria-label="Screens">
        ${SCREENS.map(s => `<a href="${s.href}" data-screen="${s.id}">${s.label}</a>`).join("")}
      </nav>
      <div class="details-slot"></div>
    </header>
    <form class="find" role="search" ${embed?'hidden':''}>
      <label class="visually-hidden" for="q">Coordinates or a featured name</label>
      <input id="q" class="input find-input" name="q" autocomplete="off" spellcheck="false"
        placeholder="Paste coordinates, e.g. 08h55m10.831s -7d14m42.53s" />
      <button class="btn btn-primary find-go" type="submit">Blink</button>
    </form>
    <section class="featured" data-only="explore" aria-label="Featured targets" ${embed ? "hidden" : ""}>
      <span class="featured-label" id="featured-label">Try</span>
      <div class="featured-list" role="group" aria-labelledby="featured-label" style="display:contents">
        ${TARGETS.map(
          (t) => html`<button class="chip" type="button" data-slug="${t.slug}" aria-pressed="false"
            title="${t.name}, ${t.kind}">${t.short}<span class="chip-kind">, ${t.kind}</span></button>`,
        ).join("")}
      </div>
    </section>
    <p class="find-msg" role="status" aria-live="polite"></p>
    <main class="page page-atlas" data-page="atlas" hidden></main>
    <main class="page page-discover" data-page="discover" hidden></main>
    <main class="page page-review" data-page="review" hidden></main>
    <main class="page page-candidate" data-page="candidate" hidden></main>
    <main class="page page-blink" data-page="explore" hidden>
      <header class="workspace-head">
        <div><h1 id="blink-h1">Viewer</h1><p class="workspace-description"></p></div>
      </header>
      <section class="practice-task" aria-label="Practice task" hidden></section>
      <section class="practice-panel" aria-label="Guided practice" hidden></section>
      <!-- Two columns: the plate with its controls and the sky around it; the object and flagging. -->
      <div class="vw-main">
        <section class="stage" aria-label="Blink"><div class="science-layer-caption" role="status" hidden><span data-layer-description></span><button class="btn" type="button" data-raw-source>Show observations</button></div><div id="blink"></div></section>
        <section id="sky" class="sky vw-panel" aria-label="Sky around this spot"></section>
      </div>
      <div class="vw-side">
        <!-- Here, not above the plate: a bar appearing above it moved the plate under the pointer. -->
        <div class="workspace-selection" hidden><span>Position selected on the image.</span><button class="btn btn-ghost" type="button" data-clear-selection>Clear</button><button class="btn" type="button" data-flag-selection>Flag this position</button></div>
        <section id="card" class="side-card" aria-label="Object"></section>
        <section id="science-workbench" aria-label="Workspace evidence"></section>
        <section id="vote" class="side-vote vw-panel" aria-label="Flag and vote"></section>
      </div>
    </main>
    <main class="page page-score" data-page="results" hidden><div id="score"></div></main>
    <main class="page page-community" data-page="community" hidden></main>
    <main class="page page-about" data-page="about" hidden></main>
    <main class="page page-learn" data-page="learn" hidden></main>
    <main class="page page-tools" data-page="tools" hidden></main>
    <main class="page page-live" data-page="live" hidden></main>
    <footer class="foot" ${embed ? "hidden" : ""}>
      <p class="foot-links"><a href="#/community">Community</a> <a href="#/learn">Learn</a> <a href="#/about">About</a></p>
      <p>SPHEREx data: NASA/JPL-Caltech/IPAC (IRSA). Stars: ESA Gaia DR3. Known asteroids and comets: Minor Planet Center orbits, computed with kete (Dar Dahlen, first written at Caltech/IPAC, BSD-3-Clause). Sky view: Aladin Lite (CDS, GPL-3.0) with IRSA HiPS. Votes: Google Firebase.</p>
    </footer>
  `;

  const $ = <T extends Element>(sel: string) => root.querySelector(sel) as T;
  const msg = $<HTMLParagraphElement>(".find-msg");
  mountDetailsSwitch($<HTMLElement>(".details-slot"));
  const input = $<HTMLInputElement>("#q");

  let view: View | null = null;
  /** What the Blink page shows, for its heading and the tab title. */
  let blinkName: string | null = null;
  let frames: FramesResponse | null = null;
  let object: ObjectResponse | null = null;
  let frameCtl: AbortController | null = null;
  let objectCtl: AbortController | null = null;
  let resolveCtl: AbortController | null = null;
  function cancelResolve() {
    resolveCtl?.abort();
    resolveCtl = null;
  }
  let layer: Layer = 'raw';
  let epochMjd = 0;
  let selectedSignal: {ra:number;dec:number;pmra_arcsec_yr?:number;pmdec_arcsec_yr?:number;mjd_ref?:number} | null = null;
  let selectedSky:SkySelection|null=null;
  let returnSkyHash='#/sky';
  function highlightSelected() {
    if(!selectedSignal){blink.highlight?.(null);return;}
    const dt=selectedSignal.mjd_ref?(epochMjd-selectedSignal.mjd_ref)/365.25:0;
    blink.highlight?.({ra:selectedSignal.ra+(selectedSignal.pmra_arcsec_yr??0)*dt/(3600*Math.cos(selectedSignal.dec*Math.PI/180)),dec:selectedSignal.dec+(selectedSignal.pmdec_arcsec_yr??0)*dt/3600});
  }
  function selectSignal(value:typeof selectedSignal) {
    selectedSignal=value;
    $<HTMLElement>('.workspace-selection').hidden=value===null;
    highlightSelected();
  }

  const blink: BlinkHandle = mountBlink($("#blink"), {
    onViewChange(change) {
      if (!view) return;
      go({ ...view, ...change }, { objectToo: false });
    },
    onPick(pick) {
      if(viewerNow().tab==='play')return;
      vote.pick(pick);
      selectSignal(pick);
    },
    onEpoch(e) {
      epochMjd = e.mjd_mid;
      highlightSelected();
      sky.epoch(e.mjd_mid);
      workbench.epoch(frames?.epochs.find(epoch => epoch.id === e.id), e.presentation);
      filters.epoch(e);
    },
  });
  // Filters (colour layers A1, A2) sit on the blink's plate: button above, key below.
  const filters = mountFilters($("#blink"));
  // ctx (below) exists by the time anyone clicks: a featured target's field opens its static data.
  const card: CardHandle = mountCard($("#card"), { onExplore: (at) => ctx.explore(at) });
  const vote: VoteHandle = mountVote($("#vote"));
  const sky: SkyHandle = mountSky($("#sky"), {
    onClick(pos) {
      if (view) go({ ...view, ...pos, slug: null, name: null });
    },
  });
  const workbench = mountWorkbench($("#science-workbench"), {
    card, cardElement: $("#card"),
    layer(next) { if (view) { layer = next; void go(view, { objectToo: false }); $('.stage').scrollIntoView({block:'start'}); } },
    reference(points) { card.reference?.(points); },
    locate(candidate) {
      selectSignal(candidate);
      $("#blink").scrollIntoView({ block: 'start', behavior: 'auto' });
    },
    rebuilt(next) { layer = 'raw'; void go(next); },
  });
  const practice=mountGuidedPractice($('.practice-task'),$('.practice-panel'),{
    target(slug){const t=targetBySlug(slug);if(t){layer='raw';blink.display?.('blink');void go(viewForTarget(t),{push:false});writeView(viewForTarget(t),true);}},
    display(mode){blink.display?.(mode);},
    step(){blink.step?.();},
  });
  $('[data-clear-selection]').addEventListener('click',()=>selectSignal(null));
  $('[data-raw-source]').addEventListener('click',()=>{if(view){layer='raw';void go(view,{objectToo:false});}});
  $('[data-flag-selection]').addEventListener('click',()=>{
    if(!selectedSignal||!view)return;
    vote.pick({...selectedSignal,mjd:epochMjd,det:view.det,clock:view.clock,epoch_id:frames?.epochs.find(e=>e.mjd_mid===epochMjd)?.id??''});
    location.hash=workspaceHref('explore');
    requestAnimationFrame(()=>$<HTMLElement>('#vote').scrollIntoView({block:'start'}));
  });

  function say(text: string, kind: "info" | "error" = "info") {
    msg.textContent = text;
    msg.dataset.kind = kind;
  }

  function markChips(slug: string | null) {
    root.querySelectorAll<HTMLButtonElement>(".chip").forEach((b) => {
      b.setAttribute("aria-pressed", String(b.dataset.slug === slug));
    });
  }

  /**
   * `nav` means "the person asked for Blink": only then may go() take the page over. Without it a
   * late callback (the player reporting its own view once frames land, a vote panel on the hidden
   * Explore page) would pull someone off the page they are reading.
   */
  async function go(next: View, opts: { push?: boolean; objectToo?: boolean; nav?: boolean } = {}) {
    cancelResolve();
    const { push = true, objectToo = true, nav = false } = opts;
    if (nav && current !== "explore") {
      exploreStarted = true;
      history.replaceState(null, "", `${location.pathname}${location.search}#/explore`);
      route();
    }
    const moved = !view || view.ra !== next.ra || view.dec !== next.dec;
    if (moved) {layer = 'raw';selectSignal(null);}
    view = next;
    practice.selection(next);
    workbench.select(next, layer);
    const layerCaption = $<HTMLElement>('.science-layer-caption');
    layerCaption.hidden = layer === 'raw';
    $<HTMLElement>('[data-layer-description]').textContent = LAYER_LABELS[layer] + ' · Derived analysis layer; original measurements remain in the object card.';
    if (push) writeView(next);
    blinkName = next.name ?? formatRaDec(next);
    if (viewerNow().tab !== 'play') $<HTMLHeadingElement>("#blink-h1").textContent = blinkName;
    if (current === "explore") document.title = `${blinkName}: SkyShift`;
    markChips(next.slug);
    input.value = next.name ?? formatRaDec(next);
    say("");
    vote.view(next);

    frameCtl?.abort();
    frameCtl = new AbortController();
    const fsig = frameCtl.signal;
    blink.status("loading", next.slug ? "Loading frames…" : "Building frames from SPHEREx (up to 15 s)…");
    filters.active(layer === 'raw' && viewerNow().tab !== 'play');
    filters.loading(next);

    frames = null;
    workbench.frames(null);
    highlightSelected();
    if (moved) {
      // Don't hand the card or sky the previous spot's data while the new one loads.
      object = null;
      frames = null;
    }
    if (moved || objectToo) {
      objectCtl?.abort();
      objectCtl = new AbortController();
      const osig = objectCtl.signal;
      card.status("loading");
      loadObject(next, osig)
        .then((o) => {
          if (osig.aborted) return;
          object = o;
          workbench.object(o);
          card.show(next, o, frames);
        })
        .catch((e) => {
          if (osig.aborted) return;
          object = null;
          card.show(next, null, frames);
          if (!(e instanceof ApiFailure && e.status === 404)) card.status("error", errorText(e));
        });
    }

    if (!moved && !objectToo) workbench.object(object);
    try {
      const loaded = layer === 'raw' ? await loadFrames(next, fsig) : await (async () => {
        const extended = await loadScienceFrames(targetFor(next), next.det, next.clock, fsig, layer, next.fov);
        return { frames: extended.data as FramesResponse, jsonUrl: extended.jsonUrl };
      })();
      if (fsig.aborted) return;
      frames = loaded.frames;
      workbench.frames(frames,loaded.jsonUrl);
      sky.goto(next, frames);
      filters.goto(next, frames);
      card.show(next, object, frames);
      await blink.show({ ...next, slug: layer === 'raw' ? next.slug : null }, loaded.frames, loaded.jsonUrl,
        {singleDetector:layer !== 'raw' || viewerNow().tab === 'play'});
      if (fsig.aborted) return;
      blink.status('idle');
      practice.ready(blink.canCompare?.() ?? false);
      highlightSelected();
    } catch (e) {
      if (fsig.aborted) return;
      frames = null;
      workbench.frames(null);
      sky.goto(next, null);
      filters.goto(next, null);
      if (layer !== 'raw') {
        const failed = layer;
        layer = 'raw';
        await go(next, { push: false, objectToo: false });
        if (view === next) workbench.message(`The ${LAYER_LABELS[failed].toLowerCase()} is unavailable for this field, detector, clock or field of view. Showing the original observations.`);
      } else blink.status("error", errorText(e));
    }
  }

  function errorText(e: unknown): string {
    if (e instanceof ApiFailure) {
      if (e.code === "not_found") return "SPHEREx has no frames here yet. Try a featured target.";
      if (e.code === "upstream") return "NASA's archive didn't answer. Try again in a minute, or pick a featured target.";
      if (e.code === "bad_request") return e.message;
      return `The live service failed (${e.status}). The featured targets still work.`;
    }
    return "The live service can't be reached. The featured targets still work.";
  }

  // Coordinate box: coordinates parse here; names go to the featured list, then /api/resolve.
  $<HTMLFormElement>(".find").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    cancelResolve();
    say('');
    const q = input.value.trim();
    if (!q) return;
    const pos = parseCoords(q);
    const clock = view?.clock ?? "months";
    const base = { clock, fov: view?.fov ?? (clock === "months" ? 5 : 20), det: view?.det ?? 2 } as const;
    if (pos) {
      // Static first: coordinates inside a featured target's field open its prepared frames.
      const near = featuredNear(pos);
      return go(near ? viewForTarget(near) : { ...base, ...pos, slug: null, name: null }, { nav: true });
    }
    const local = TARGETS.find((t) =>
      [t.name, t.short, t.slug].some((n) => n.toLowerCase().replace(/\W/g, "") === q.toLowerCase().replace(/\W/g, "")),
    );
    if (local) return go(viewForTarget(local), { nav: true });
    const ctl = new AbortController();
    resolveCtl = ctl;
    say("Looking up the name…");
    try {
      const r = await resolve(q, ctl.signal);
      if (ctl.signal.aborted) return;
      const t = targetBySlug(r.slug);
      go(t ? viewForTarget(t) : { ...base, ra: r.ra, dec: r.dec, slug: null, name: r.name }, { nav: true });
    } catch (e) {
      if (ctl.signal.aborted) return;
      say(
        e instanceof ApiFailure && e.status === 404
          ? `“${q}” isn't a featured name. Paste coordinates instead, like 08h55m10.831s -7d14m42.53s.`
          : `Couldn't read “${q}”. Paste coordinates like 08h55m10.831s -7d14m42.53s or 133.795 -7.245.`,
        "error",
      );
    } finally {
      if (resolveCtl === ctl) resolveCtl = null;
    }
  });

  input.addEventListener('input', () => {
    if (!resolveCtl) return;
    cancelResolve();
    say('');
  });

  root.querySelectorAll<HTMLButtonElement>(".chip").forEach((b) =>
    b.addEventListener("click", () => {
      const t = targetBySlug(b.dataset.slug);
      if (t) go(viewForTarget(t), { nav: true });
    }),
  );

  // Routes (DESIGN.md "Pages"). Pages mount on first visit; Explore loads frames only when opened.
  const ctx: PageContext = {
    currentView:()=>view,
    skySelection(at,returnHash){
      selectedSky=at;returnSkyHash=returnHash;
      $<HTMLAnchorElement>('.wordmark').href=returnHash;
    },
    navigate(hash) {
      if(current==='atlas'&&selectedSky&&siteRoute(hash).page==='explore'){enterSkyArea(hash);return;}
      location.hash = hash;
    },
    explore(at) {
      // Static first: a spot inside a featured target's field opens its prepared frames.
      const near = featuredNear(at);
      if (near && (!at.clock || at.clock === near.clock)) return void go(viewForTarget(near), { nav: true });
      const clock = at.clock ?? view?.clock ?? "months";
      const fov = at.fov ?? (view && view.clock === clock ? view.fov : clock === "months" ? 5 : 20);
      go({ ra: at.ra, dec: at.dec, clock, fov, det: at.det ?? view?.det ?? 2, slug: null, name: null }, { nav: true });
    },
  };
  const pageMounts: Record<string, MountPage> = {
    atlas: mountAtlas,
    discover: mountDiscover,
    learn: mountLearn,
    tools: mountTools,
    live: mountLive,
    // Results in sections: show("<section>[/<part>]") opens and scrolls; hide() stops the live polling.
    results: mountScore,
    review: mountReview,
    candidate: mountCandidate,
    community: mountCommunity,
    about: mountAbout,
  };
  const pages = new Map<string, PageHandle>();
  let current = "";
  let exploreStarted = false;

  /** Rewrite an old link in place (turning Advanced on when its section needs it), then resolve. */
  function parseRoute() {
    const legacy = legacyHash(location.hash);
    if (legacy) {
      if (legacy.advanced) setDetails(true);
      history.replaceState(history.state, "", `${location.pathname}${location.search}${legacy.hash}`);
    }
    return siteRoute(location.hash, embed, location.search, getDetails());
  }
  /** The Viewer's section for the current address (the summary when the page isn't the Viewer). */
  function viewerNow(): WorkspaceState { return parseRoute().workspace ?? viewerState(null, getDetails()); }

  function enterSkyArea(hash:string) {
    if(!selectedSky){location.hash=hash;return;}
    const picked={ra:selectedSky.ra,dec:selectedSky.dec};
    const next=observationForSky(selectedSky);
    exploreStarted=true;
    history.pushState(null,'',`${location.pathname}${location.search}${hash}`);
    route();
    void go(next,{push:false}).then(()=>{if(view===next)selectSignal(picked);});writeView(next,true);
  }

  root.addEventListener('click',event=>{
    const link=(event.target as Element).closest<HTMLAnchorElement>('a[data-enter-area],a[data-screen="viewer"]');
    if(!link||current!=='atlas'||!selectedSky||event.defaultPrevented||event.button!==0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey)return;
    event.preventDefault();
    enterSkyArea(link.hash);
  });

  function route() {
    if (resolveCtl) { cancelResolve(); say(''); }
    const { page, param, screen, workspace } = parseRoute();
    const practicing=page==='explore' && workspace?.tab==='play';
    if(page==='explore')blink.singleDetector?.(practicing || layer !== 'raw');
    filters.active(!practicing && layer === 'raw');
    if (current && current !== page) pages.get(current)?.hide?.();
    blink.active?.(page === 'explore');
    workbench.active(page === 'explore' && !practicing);
    if(!practicing)practice.hide();
    current = page;
    // Pages with their own title (Review, a candidate) set it in show(), after this.
    const name = page === "explore" && blinkName ? blinkName : PAGE_TITLES[page];
    document.title = name ? `${name}: SkyShift` : "SkyShift";
    root.querySelectorAll<HTMLElement>(".page").forEach((p) => (p.hidden = p.dataset.page !== page));
    root.querySelectorAll<HTMLElement>("[data-only]").forEach((p) => (p.hidden = embed || p.dataset.only !== page || practicing));
    if (page === "explore" && !exploreStarted) {
      exploreStarted = true;
      if(!practicing){
        const first = readView() ?? viewForTarget(TARGETS[0]);
        void go(first, { push: false });
        writeView(first, true);
      }
    }
    if(page === 'explore' && workspace){
      const state=workspace;
      $<HTMLElement>('.page-blink').dataset.workspaceMode=state.mode;
      $<HTMLElement>('.page-blink').dataset.practice=String(practicing);
      $<HTMLElement>('#science-workbench').hidden=practicing;
      $<HTMLElement>('.practice-task').hidden=!practicing;
      $<HTMLElement>('.practice-panel').hidden=!practicing;
      $<HTMLElement>('.workspace-description').textContent=practicing?MODES.explore.description:'';
      $<HTMLHeadingElement>('#blink-h1').textContent=practicing?'Guided practice':(blinkName??'Viewer');
      workbench.mode(state);
      if(practicing)practice.show();
    }
    root.querySelectorAll<HTMLAnchorElement>('[data-screen]').forEach(a=>{
      if(a.dataset.screen===screen)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');
    });
    root.dataset.screen=screen??page;
    $<HTMLAnchorElement>('.wordmark').href=returnSkyHash;
    $<HTMLFormElement>('.find').hidden=embed||page!=='explore'||practicing;
    const mount = pageMounts[page];
    if (mount) {
      let handle = pages.get(page);
      if (!handle) {
        handle = mount(root.querySelector<HTMLElement>(`.page[data-page="${page}"]`)!, ctx);
        pages.set(page, handle);
      }
      handle.show(param);
    }
    if (page !== "explore") window.scrollTo({ top: 0 });
  }
  addEventListener("hashchange", route);
  // Advanced on the Viewer opens the measurements; off returns to the summary.
  onDetails(() => { if (current === "explore") route(); });

  addEventListener('skyshift:gaia', (event) => {
    const data = (event as CustomEvent<{ra: number; dec: number; stars: FramesResponse['gaia']}>).detail;
    if (!view || !frames || !data || data.ra !== view.ra || data.dec !== view.dec || !Array.isArray(data.stars)) return;
    frames.gaia = data.stars;
    blink.gaia?.(data.stars);
    sky.goto(view, frames);
    sky.epoch(epochMjd);
  });

  // Votes' "Show" re-centres the blink on a candidate.
  addEventListener("skyshift:goto", (ev) => {
    const d = (ev as CustomEvent<{ ra: number; dec: number; clock?: View["clock"] }>).detail;
    if (!d || !Number.isFinite(d.ra) || !Number.isFinite(d.dec)) return;
    const clock = d.clock ?? view?.clock ?? "months";
    const fov = view && view.clock === clock ? view.fov : clock === "months" ? 5 : 20;
    go({ ra: d.ra, dec: d.dec, clock, fov, det: view?.det ?? 2, slug: null, name: null });
  });

  addEventListener("popstate", () => {
    const v = readView();
    // Coordinates in the shared URL use five decimals; a hash-only navigation must
    // retain the higher-precision map selection and the current image frame.
    if (v && parseRoute().page === "explore" && (!view || v.ra.toFixed(5)!==view.ra.toFixed(5) || v.dec.toFixed(5)!==view.dec.toFixed(5) || (["clock","det","fov","slug"] as const).some(k=>v[k]!==view![k]))) void go(v, { push: false });
  });

  route();
  // The opening (src/intro): every page load (not in-app navigation), loaded on demand.
  if (!embed) mountOpening(root, openingEnv);
}
