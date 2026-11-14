// Page shell: coordinate box, target chips, URL state, and the wiring between areas.
// Areas plug in through the interfaces in ./modules.ts.
import { ApiFailure, loadFrames, loadObject, resolve } from "../api/client";
import type { FramesResponse, ObjectResponse } from "../api/types";
import { mountBlink } from "../blink";
import { mountCard } from "../card";
import { mountScore } from "../score";
import { mountSky } from "../sky";
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
import { loadFrames as loadScienceFrames } from "../api/science-client";
import { formatRaDec } from "./astro";
import { featuredNear } from "./state";
import { parseCoords } from "./coords";
import type { BlinkHandle, CardHandle, MountPage, PageContext, PageHandle, SkyHandle, View, VoteHandle } from "./modules";
import { TARGETS, isEmbed, readView, targetBySlug, viewForTarget, writeView } from "./state";

const html = String.raw;
/** Each page's name in the browser tab ("Candidates: SkyShift"); the home page is plain SkyShift. */
const PAGE_TITLES: Record<string, string> = {
  discover: "Candidates",
  review: "Review",
  explore: "Blink",
  live: "Live search",
  community: "Community",
  results: "Results",
  learn: "Learn",
  tools: "Tools and data",
  about: "About",
};
/** Flip to true when src/atlas replaces its placeholder. */
const ATLAS_READY = true;

export function startApp(root: HTMLElement): void {
  const embed = isEmbed();
  root.innerHTML = html`
    <header class="top" ${embed ? "hidden" : ""}>
      <div class="brand">
        <a class="wordmark" href="./" aria-label="SkyShift home">SkyShift</a>
        <p class="lede">Find what moves in NASA's SPHEREx sky, together.</p>
      </div>
      <form class="find" role="search">
        <label class="visually-hidden" for="q">Coordinates or a featured name</label>
        <input id="q" class="input find-input" name="q" autocomplete="off" spellcheck="false"
          placeholder="Paste coordinates, e.g. 08h55m10.831s -7d14m42.53s" />
        <button class="btn btn-primary find-go" type="submit">Blink</button>
      </form>
      <nav class="nav" aria-label="Pages">
        <a href="#/" data-route="atlas" ${ATLAS_READY ? "" : "hidden"}>Sky</a>
        <a href="#/candidates" data-route="discover">To check</a>
        <a href="#/review" data-route="review">Review</a>
        <a href="#/explore" data-route="explore">Blink</a>
        <a href="#/explore/science" data-route="science">Science</a>
        <a href="#/live" data-route="live">Live search</a>
        <a href="#/community" data-route="community">Community</a>
        <a href="#/results" data-route="results">Results</a>
        <a href="#/learn" data-route="learn">Learn</a>
      </nav>
      <div class="details-slot"></div>
    </header>
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
      <h1 class="visually-hidden" id="blink-h1">Blink</h1>
      <section class="stage" aria-label="Blink"><p class="science-layer-caption" role="status" hidden></p><div id="blink"></div></section>
      <section id="card" class="side-card" aria-label="Object"></section>
      <div class="below">
        <section id="vote" class="side-vote" aria-label="Flag and vote"></section>
        <section id="sky" class="sky" aria-label="Sky around this spot"></section>
      </div>
      <section id="science-workbench" aria-label="Extended science and cultural sky"></section>
    </main>
    <main class="page page-score" data-page="results" hidden><div id="score"></div></main>
    <main class="page page-community" data-page="community" hidden></main>
    <main class="page page-about" data-page="about" hidden></main>
    <main class="page page-learn" data-page="learn" hidden></main>
    <main class="page page-tools" data-page="tools" hidden></main>
    <main class="page page-live" data-page="live" hidden></main>
    <footer class="foot" ${embed ? "hidden" : ""}>
      <p class="foot-links"><a href="#/about">About</a> <a href="#/learn">Learn</a> <a href="#/tools">Tools and data</a> <a href="#/results">How we know it works</a></p>
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
  let layer: Layer = 'raw';
  let epochMjd = 0;

  const blink: BlinkHandle = mountBlink($("#blink"), {
    onViewChange(change) {
      if (!view) return;
      go({ ...view, ...change }, { objectToo: false });
    },
    onPick(pick) {
      vote.pick(pick);
    },
    onEpoch(e) {
      epochMjd = e.mjd_mid;
      sky.epoch(e.mjd_mid);
      workbench.epoch(frames?.epochs.find(epoch => epoch.id === e.id));
    },
  });
  const card: CardHandle = mountCard($("#card"));
  const vote: VoteHandle = mountVote($("#vote"));
  const sky: SkyHandle = mountSky($("#sky"), {
    onClick(pos) {
      if (view) go({ ...view, ...pos, slug: null, name: null });
    },
  });
  const workbench = mountWorkbench($("#science-workbench"), {
    layer(next) { if (view) { layer = next; void go(view, { objectToo: false }); $('.stage').scrollIntoView({block:'start'}); } },
    reference(points) { card.reference?.(points); },
    locate(candidate) {
      const dt = candidate.mjd_ref ? (epochMjd - candidate.mjd_ref) / 365.25 : 0;
      blink.highlight?.({ ra: candidate.ra + (candidate.pmra_arcsec_yr ?? 0) * dt / (3600 * Math.cos(candidate.dec * Math.PI / 180)), dec: candidate.dec + (candidate.pmdec_arcsec_yr ?? 0) * dt / 3600 });
      $("#blink").scrollIntoView({ block: 'start', behavior: 'auto' });
    },
    rebuilt(next) { layer = 'raw'; void go(next); },
  });
  root.querySelector<HTMLAnchorElement>('.nav [data-route="science"]')!.addEventListener('click', () => {
    if (current === 'explore') workbench.open();
  });
  let score: { destroy(): void } | null = null;

  function say(text: string, kind: "info" | "error" = "info") {
    msg.textContent = text;
    msg.dataset.kind = kind;
  }

  function markChips(slug: string | null) {
    root.querySelectorAll<HTMLButtonElement>(".chip").forEach((b) => {
      b.setAttribute("aria-pressed", String(b.dataset.slug === slug));
    });
  }

  async function go(next: View, opts: { push?: boolean; objectToo?: boolean } = {}) {
    const { push = true, objectToo = true } = opts;
    if (current !== "explore") {
      exploreStarted = true;
      history.replaceState(null, "", `${location.pathname}${location.search}#/explore`);
      route();
    }
    const moved = !view || view.ra !== next.ra || view.dec !== next.dec;
    if (moved) layer = 'raw';
    view = next;
    workbench.select(next, layer);
    const layerCaption = $<HTMLElement>('.science-layer-caption');
    layerCaption.hidden = layer === 'raw';
    layerCaption.textContent = ({raw:'',model:'Static sky model · 科学模型',resid:'Model residual · 模型残差',refdiff:'DR1 reference difference · 参考差图'})[layer] + ' · Derived analysis layer; original measurements remain in the object card.';
    if (push) writeView(next);
    blinkName = next.name ?? formatRaDec(next);
    $<HTMLHeadingElement>("#blink-h1").textContent = `Blink: ${blinkName}`;
    if (current === "explore") document.title = `${blinkName}: SkyShift`;
    markChips(next.slug);
    input.value = next.name ?? formatRaDec(next);
    say("");
    vote.view(next);

    frameCtl?.abort();
    frameCtl = new AbortController();
    const fsig = frameCtl.signal;
    blink.status("loading", next.slug ? "Loading frames…" : "Building frames from SPHEREx (up to 15 s)…");

    frames = null;
    blink.highlight?.(null);
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
      sky.goto(next, frames);
      card.show(next, object, frames);
      await blink.show({ ...next, slug: layer === 'raw' ? next.slug : null }, loaded.frames, loaded.jsonUrl);
      if (fsig.aborted) return;
      blink.status('idle');
    } catch (e) {
      if (fsig.aborted) return;
      frames = null;
      sky.goto(next, null);
      if (layer !== 'raw') {
        const failed = layer;
        layer = 'raw';
        await go(next, { push: false, objectToo: false });
        if (view === next) workbench.message(`${failed} is unavailable here. Showing the original observations. Build an analysis to prepare the model and residual.`);
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
    const q = input.value.trim();
    if (!q) return;
    const pos = parseCoords(q);
    const clock = view?.clock ?? "months";
    const base = { clock, fov: view?.fov ?? (clock === "months" ? 5 : 20), det: view?.det ?? 2 } as const;
    if (pos) {
      // Static first: coordinates inside a featured target's field open its prepared frames.
      const near = featuredNear(pos);
      return go(near ? viewForTarget(near) : { ...base, ...pos, slug: null, name: null });
    }
    const local = TARGETS.find((t) =>
      [t.name, t.short, t.slug].some((n) => n.toLowerCase().replace(/\W/g, "") === q.toLowerCase().replace(/\W/g, "")),
    );
    if (local) return go(viewForTarget(local));
    say("Looking up the name…");
    try {
      const r = await resolve(q);
      const t = targetBySlug(r.slug);
      go(t ? viewForTarget(t) : { ...base, ra: r.ra, dec: r.dec, slug: null, name: r.name });
    } catch (e) {
      say(
        e instanceof ApiFailure && e.status === 404
          ? `“${q}” isn't a featured name. Paste coordinates instead, like 08h55m10.831s -7d14m42.53s.`
          : `Couldn't read “${q}”. Paste coordinates like 08h55m10.831s -7d14m42.53s or 133.795 -7.245.`,
        "error",
      );
    }
  });

  root.querySelectorAll<HTMLButtonElement>(".chip").forEach((b) =>
    b.addEventListener("click", () => {
      const t = targetBySlug(b.dataset.slug);
      if (t) go(viewForTarget(t));
    }),
  );

  // Routes (DESIGN.md "Pages"). Pages mount on first visit; Explore loads frames only when opened.
  const ctx: PageContext = {
    navigate(hash) {
      location.hash = hash;
    },
    explore(at) {
      // Static first: a spot inside a featured target's field opens its prepared frames.
      const near = featuredNear(at);
      if (near && (!at.clock || at.clock === near.clock)) return void go(viewForTarget(near));
      const clock = at.clock ?? view?.clock ?? "months";
      const fov = at.fov ?? (view && view.clock === clock ? view.fov : clock === "months" ? 5 : 20);
      go({ ra: at.ra, dec: at.dec, clock, fov, det: at.det ?? view?.det ?? 2, slug: null, name: null });
    },
  };
  const pageMounts: Record<string, MountPage> = {
    atlas: mountAtlas,
    discover: mountDiscover,
    learn: mountLearn,
    tools: mountTools,
    live: mountLive,
    review: mountReview,
    candidate: mountCandidate,
    community: mountCommunity,
    about: mountAbout,
  };
  const pages = new Map<string, PageHandle>();
  let current = "";
  let exploreStarted = false;

  function parseRoute(): { page: string; param: string | null } {
    const h = location.hash.replace(/^#\/?/, "");
    const [head, ...rest] = h.split("/");
    if (head === "c" && rest.length) return { page: "candidate", param: decodeURIComponent(rest.join("/")) };
    if (head === "score" || head === "results") return { page: "results", param: null };
    if (head === "candidates" || head === "discover") return { page: "discover", param: rest.join("/") || null };
    if (head === "sky") return { page: "atlas", param: rest.join("/") || null };
    if (["review", "explore", "community", "about", "learn", "tools", "live"].includes(head)) return { page: head, param: rest.join("/") || null };
    // No hash: deep links to a target or coordinates open Explore; otherwise the sky (atlas).
    if (!head && (new URLSearchParams(location.search).has("t") || new URLSearchParams(location.search).has("ra")))
      return { page: "explore", param: null };
    // Until the Sky globe lands, the home page shows tonight's live search.
    return { page: ATLAS_READY ? "atlas" : "live", param: null };
  }

  function route() {
    const { page, param } = parseRoute();
    if (current && current !== page) pages.get(current)?.hide?.();
    blink.active?.(page === 'explore');
    workbench.active(page === 'explore');
    current = page;
    // Pages with their own title (Review, a candidate) set it in show(), after this.
    const name = page === "explore" && blinkName ? blinkName : PAGE_TITLES[page];
    document.title = name ? `${name}: SkyShift` : "SkyShift";
    root.querySelectorAll<HTMLElement>(".page").forEach((p) => (p.hidden = p.dataset.page !== page));
    root.querySelectorAll<HTMLElement>("[data-only]").forEach((p) => (p.hidden = embed || p.dataset.only !== page));
    const navKey = page === "candidate" ? "review" : page === "explore" && param === "science" ? "science" : page;
    root.querySelectorAll<HTMLAnchorElement>(".nav a").forEach((a) =>
      a.dataset.route === navKey ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current"),
    );
    if (page === "results" && !score) score = mountScore($("#score"));
    if (page === "explore" && !exploreStarted) {
      exploreStarted = true;
      const first = readView() ?? viewForTarget(TARGETS[0]);
      void go(first, { push: false }).then(() => {
        if (current === 'explore' && parseRoute().param === 'science') workbench.open();
      });
      writeView(first, true);
    }
    if (page === "explore" && param === "science") workbench.open();
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
    if (v && parseRoute().page === "explore") go(v, { push: false });
  });

  route();
}
