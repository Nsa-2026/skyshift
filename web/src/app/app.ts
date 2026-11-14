// Page shell: coordinate box, target chips, URL state, and the wiring between areas.
// Areas plug in through the interfaces in ./modules.ts.
import { ApiFailure, loadFrames, loadObject, resolve } from "../api/client";
import type { FramesResponse, ObjectResponse } from "../api/types";
import { mountBlink } from "../blink";
import { mountCard } from "../card";
import { mountScore } from "../score";
import { mountSky } from "../sky";
import { mountVote } from "../vote";
import { formatRaDec } from "./astro";
import { parseCoords } from "./coords";
import type { BlinkHandle, CardHandle, SkyHandle, View, VoteHandle } from "./modules";
import { TARGETS, isEmbed, readView, targetBySlug, viewForTarget, writeView } from "./state";

const html = String.raw;

export function startApp(root: HTMLElement): void {
  const embed = isEmbed();
  root.innerHTML = html`
    <header class="top" ${embed ? "hidden" : ""}>
      <a class="wordmark" href="./" aria-label="SkyShift home">SkyShift</a>
      <form class="find" role="search">
        <label class="visually-hidden" for="q">Coordinates or a featured name</label>
        <input id="q" class="input find-input" name="q" autocomplete="off" spellcheck="false"
          placeholder="Paste coordinates, e.g. 08h55m10.831s -7d14m42.53s" />
        <button class="btn find-go" type="submit">Blink</button>
      </form>
      <nav class="nav" aria-label="Pages">
        <a href="#/" data-route="blink">Blink</a>
        <a href="#/score" data-route="score">Scoreboard</a>
      </nav>
    </header>
    <div class="chips" role="list" aria-label="Featured targets" ${embed ? "hidden" : ""}>
      ${TARGETS.map(
        (t) => html`<button class="chip" role="listitem" type="button" data-slug="${t.slug}" aria-pressed="false">
          <span class="chip-name">${t.short}</span><span class="chip-kind">${t.kind}</span>
        </button>`,
      ).join("")}
    </div>
    <p class="find-msg" role="status" aria-live="polite"></p>
    <main class="page page-blink" data-page="blink">
      <section class="stage" aria-label="Blink"><div id="blink"></div></section>
      <aside class="side">
        <section id="card" class="panel side-card" aria-label="Object"></section>
        <section id="vote" class="panel side-vote" aria-label="Flag and vote"></section>
      </aside>
      <section id="sky" class="panel sky" aria-label="Sky view"></section>
    </main>
    <main class="page page-score" data-page="score" hidden><div id="score"></div></main>
    <footer class="foot" ${embed ? "hidden" : ""}>
      <p>SPHEREx data: NASA/JPL-Caltech/IPAC (IRSA). Stars: ESA Gaia DR3. Known asteroids: MPC, IMCCE SkyBoT.</p>
    </footer>
  `;

  const $ = <T extends Element>(sel: string) => root.querySelector(sel) as T;
  const msg = $<HTMLParagraphElement>(".find-msg");
  const input = $<HTMLInputElement>("#q");

  let view: View | null = null;
  let frames: FramesResponse | null = null;
  let object: ObjectResponse | null = null;
  let frameCtl: AbortController | null = null;
  let objectCtl: AbortController | null = null;

  const blink: BlinkHandle = mountBlink($("#blink"), {
    onViewChange(change) {
      if (!view) return;
      go({ ...view, ...change }, { objectToo: false });
    },
    onPick(pick) {
      vote.pick(pick);
    },
    onEpoch(e) {
      sky.epoch(e.mjd_mid);
    },
  });
  const card: CardHandle = mountCard($("#card"));
  const vote: VoteHandle = mountVote($("#vote"));
  const sky: SkyHandle = mountSky($("#sky"), {
    onClick(pos) {
      if (view) go({ ...view, ...pos, slug: null, name: null });
    },
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
    const moved = !view || view.ra !== next.ra || view.dec !== next.dec;
    view = next;
    if (push) writeView(next);
    markChips(next.slug);
    input.value = next.name ?? formatRaDec(next);
    say("");
    vote.view(next);

    frameCtl?.abort();
    frameCtl = new AbortController();
    const fsig = frameCtl.signal;
    blink.status("loading", next.slug ? "Loading frames…" : "Building frames from SPHEREx (up to 15 s)…");

    if (moved || objectToo) {
      objectCtl?.abort();
      objectCtl = new AbortController();
      const osig = objectCtl.signal;
      card.status("loading");
      loadObject(next, osig)
        .then((o) => {
          object = o;
          card.show(next, o, frames);
        })
        .catch((e) => {
          if (osig.aborted) return;
          object = null;
          card.show(next, null, frames);
          if (!(e instanceof ApiFailure && e.status === 404)) card.status("error", errorText(e));
        });
    }

    try {
      const loaded = await loadFrames(next, fsig);
      if (fsig.aborted) return;
      frames = loaded.frames;
      sky.goto(next, frames);
      card.show(next, object, frames);
      await blink.show(next, loaded.frames, loaded.jsonUrl);
    } catch (e) {
      if (fsig.aborted) return;
      frames = null;
      sky.goto(next, null);
      blink.status("error", errorText(e));
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
    if (pos) return go({ ...base, ...pos, slug: null, name: null });
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

  // Routes: #/ blink, #/score scoreboard.
  function route() {
    const page = location.hash.startsWith("#/score") ? "score" : "blink";
    root.querySelectorAll<HTMLElement>(".page").forEach((p) => (p.hidden = p.dataset.page !== page));
    root.querySelectorAll<HTMLAnchorElement>(".nav a").forEach((a) =>
      a.dataset.route === page ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current"),
    );
    if (page === "score" && !score) score = mountScore($("#score"));
  }
  addEventListener("hashchange", route);
  route();

  // Votes' "Show" re-centres the blink on a candidate.
  addEventListener("skyshift:goto", (ev) => {
    const d = (ev as CustomEvent<{ ra: number; dec: number; clock?: View["clock"] }>).detail;
    if (!d || !Number.isFinite(d.ra) || !Number.isFinite(d.dec)) return;
    const clock = d.clock ?? view?.clock ?? "months";
    const fov = view && view.clock === clock ? view.fov : clock === "months" ? 5 : 20;
    if (location.hash.startsWith("#/score")) location.hash = "#/";
    go({ ra: d.ra, dec: d.dec, clock, fov, det: view?.det ?? 2, slug: null, name: null });
  });

  addEventListener("popstate", () => {
    const v = readView();
    if (v) go(v, { push: false });
  });

  const first = readView() ?? viewForTarget(TARGETS[0]);
  go(first, { push: false });
  writeView(first, true);
}
