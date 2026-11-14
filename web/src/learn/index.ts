// The Learn page (web/DESIGN.md "Pages", "Two audiences"): a short course of seven lessons, each
// one screen of plain words around one real SPHEREx example, plus the glossary and a teacher corner.
// Routes (the shell passes everything after #/learn/ as param):
//   ""            the course: lesson list and "continue"
//   "1" … "7"     one lesson
//   "glossary"    every word, with anchors: "glossary/<slug>"
//   "teachers"    a 45-minute class activity, printable
//   "stories[/<part>[/<arg>]]"  Stories in the sky: guest stars, around a spot, planets. Its code,
//                 CSS and the /guxing/ scripts load only when it opens (./stories, dynamic import).
import "./learn.css";
import type { MountPage, PageHandle } from "../app/modules";
import { buildFigure, type FigureHost } from "./figures";
import { GLOSSARY, closeTermPopover, enhanceTerms, esc } from "./glossary";
import { LESSONS, LESSON_COUNT, type Lesson } from "./lessons";
import { markDone, markOpened, nextLesson, readProgress, resetProgress, safeStorage, type KV, type Progress } from "./progress";
import { STORY_PARTS, isStoryPart, type StoryPart } from "./stories-route";
import { fillAnswerKey, teachersHtml } from "./teachers";

export type LearnRoute =
  | { view: "home" }
  | { view: "lesson"; n: number }
  | { view: "glossary"; slug: string | null }
  | { view: "teachers" }
  | { view: "stories"; part: StoryPart; arg: string | null };
type StoriesRoute = Extract<LearnRoute, { view: "stories" }>;

/** Read the route argument. Anything unknown goes to the course list. */
export function parseLearnRoute(param: string | null | undefined): LearnRoute {
  const p = (param ?? "").replace(/^\/+|\/+$/g, "");
  const [head, ...rest] = p.split("/");
  if (!head) return { view: "home" };
  if (/^\d+$/.test(head)) {
    const n = Number(head);
    return n >= 1 && n <= LESSON_COUNT ? { view: "lesson", n } : { view: "home" };
  }
  if (head === "glossary") return { view: "glossary", slug: rest[0] ? decodeURIComponent(rest[0]) : null };
  if (head === "teachers" || head === "teacher") return { view: "teachers" };
  if (head === "stories") {
    if (!isStoryPart(rest[0])) return { view: "stories", part: "guests", arg: null };
    const arg = rest.slice(1).join("/");
    return { view: "stories", part: rest[0], arg: arg ? decodeURIComponent(arg) : null };
  }
  return { view: "home" };
}

export const learnHref = (r: LearnRoute): string =>
  r.view === "home"
    ? "#/learn"
    : r.view === "lesson"
      ? `#/learn/${r.n}`
      : r.view === "glossary"
        ? `#/learn/glossary${r.slug ? `/${r.slug}` : ""}`
        : r.view === "stories"
          ? `#/learn/stories${r.part === "guests" && !r.arg ? "" : `/${r.part}`}${r.arg ? `/${encodeURIComponent(r.arg).replace(/%2C/gi, ",")}` : ""}`
          : "#/learn/teachers";

const check = `<svg class="ln-check" viewBox="0 0 16 16" aria-hidden="true" width="14" height="14"><path d="M3 8.5l3.2 3L13 4.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
const arrowR = `<svg viewBox="0 0 16 16" aria-hidden="true" width="14" height="14"><path d="M3 8h9M8.5 4l4 4-4 4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
const arrowL = `<svg viewBox="0 0 16 16" aria-hidden="true" width="14" height="14"><path d="M13 8H4M7.5 4l-4 4 4 4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

function progressLine(p: Progress): string {
  const n = p.done.length;
  return n === 0 ? "Not started yet." : n === LESSON_COUNT ? "All seven lessons done." : `${n} of ${LESSON_COUNT} lessons done.`;
}

function sideNav(r: LearnRoute, p: Progress): string {
  const current = r.view === "lesson" ? r.n : null;
  return `
    <nav class="ln-side" aria-label="Course">
      <a class="ln-side-home" href="#/learn">Learn</a>
      <ol class="ln-toc">
        ${LESSONS.map((l) => {
          const done = p.done.includes(l.n);
          return `<li><a href="#/learn/${l.n}" ${l.n === current ? `aria-current="page"` : ""} class="${done ? "is-done" : ""}">
            <span class="ln-num" aria-hidden="true">${l.n}</span><span class="ln-toc-t">${esc(l.title)}</span>${
              done ? `${check}<span class="visually-hidden">, done</span>` : ""
            }</a></li>`;
        }).join("")}
      </ol>
      <div class="ln-meter" role="img" aria-label="${esc(progressLine(p))}"><span style="width:${((p.done.length / LESSON_COUNT) * 100).toFixed(1)}%"></span></div>
      <p class="ln-side-progress">${progressLine(p)}</p>
      <ul class="ln-side-more">
        <li><a href="#/learn/glossary" ${r.view === "glossary" ? `aria-current="page"` : ""}>Glossary</a></li>
        <li><a href="#/learn/teachers" ${r.view === "teachers" ? `aria-current="page"` : ""}>For teachers</a></li>
        <li><a href="#/learn/stories" ${r.view === "stories" ? `aria-current="page"` : ""}>Stories in the sky</a></li>
      </ul>
    </nav>`;
}

function homeHtml(p: Progress): string {
  const next = nextLesson(p, LESSON_COUNT);
  const started = p.done.length > 0 || p.last != null;
  const all = p.done.length === LESSON_COUNT;
  const cta = all ? "Start again from lesson 1" : started ? `Continue with lesson ${next}` : "Start with lesson 1";
  return `
    <section class="ln-home" aria-labelledby="ln-h">
      <header class="ln-home-head">
        <h1 id="ln-h" tabindex="-1">Learn to read the SPHEREx sky</h1>
        <p class="ln-lede-p">Seven short lessons, each built around real SPHEREx pictures. About twenty minutes in all, and then you'll know what to look for when you blink.</p>
        <p class="ln-home-cta">
          <a class="btn btn-primary" href="#/learn/${all ? 1 : next}">${cta} ${arrowR}</a>
          <span class="ln-home-progress">${progressLine(p)}</span>
          ${started ? `<button type="button" class="btn btn-ghost ln-reset">Clear my progress</button>` : ""}
        </p>
      </header>
      <div class="ln-home-fig ln-fig" role="group" aria-label="Real SPHEREx example: Barnard's Star"></div>
      <ol class="ln-course">
        ${LESSONS.map((l) => {
          const done = p.done.includes(l.n);
          return `<li class="${done ? "is-done" : ""}">
            <a href="#/learn/${l.n}">
              <span class="ln-num" aria-hidden="true">${l.n}</span>
              <span class="ln-course-t"><span class="ln-course-title">${esc(l.title)}${done ? ` ${check}<span class="visually-hidden">, done</span>` : ""}</span>
              <span class="ln-course-sum">${esc(l.summary)}</span></span>
            </a>
          </li>`;
        }).join("")}
      </ol>
      <div class="ln-home-more">
        <div>
          <h2><a href="#/learn/glossary">Glossary</a></h2>
          <p>${GLOSSARY.length} words in one plain sentence each. Dotted words across SkyShift open the same sentences.</p>
        </div>
        <div>
          <h2><a href="#/learn/teachers">For teachers</a></h2>
          <p>A 45-minute class activity: measure Barnard's Star's motion and compare it with Gaia. Printable.</p>
        </div>
        <div>
          <h2><a href="#/learn/stories">Stories in the sky</a></h2>
          <p>Six guest stars from Chinese histories, what SPHEREx sees where they blazed, and the old star map around any spot.</p>
        </div>
      </div>
    </section>`;
}

function lessonHtml(l: Lesson): string {
  const prev = LESSONS[l.n - 2];
  const next = LESSONS[l.n];
  return `
    <article class="ln-lesson" aria-labelledby="ln-h">
      <header class="ln-head">
        <h1 id="ln-h" tabindex="-1"><span class="ln-h-num">${l.n}</span> ${esc(l.title)}</h1>
      </header>
      <div class="ln-lede">${l.lede()}</div>
      <div class="ln-fig" role="group" aria-label="Real SPHEREx example"></div>
      <div class="ln-body">${l.body()}</div>
      <section class="ln-try" aria-labelledby="ln-try-h">
        <h2 id="ln-try-h">Try it</h2>
        <p>${esc(l.tryText)}</p>
        <p class="ln-try-actions">${l.tryIt
          .map((a, i) => `<a class="btn ${i === 0 ? "btn-primary" : "btn-ghost"}" href="${esc(a.href)}" data-try>${esc(a.label)}</a>`)
          .join("")}</p>
      </section>
      <nav class="ln-pager" aria-label="Lessons">
        ${prev ? `<a class="ln-prev" href="#/learn/${prev.n}" rel="prev">${arrowL}<span><span class="ln-pager-k">Previous:</span> ${esc(prev.title)}</span></a>` : `<a class="ln-prev" href="#/learn">${arrowL}<span>Back to the course</span></a>`}
        <span class="ln-pager-pos">Lesson ${l.n} of ${LESSON_COUNT}</span>
        ${
          next
            ? `<a class="ln-next" href="#/learn/${next.n}" rel="next" data-done><span><span class="ln-pager-k">Next:</span> ${esc(next.title)}</span>${arrowR}</a>`
            : `<a class="ln-next" href="#/learn" data-done><span>Finish the course</span>${arrowR}</a>`
        }
      </nav>
    </article>`;
}

function storiesHtml(r: StoriesRoute): string {
  return `
    <section class="ln-stories" aria-labelledby="ln-h">
      <header class="ln-head">
        <h1 id="ln-h" tabindex="-1">Stories in the sky</h1>
        <p class="ln-lede-p">Long before SPHEREx, court astronomers in China watched the same sky and wrote down what changed. Their records name places on an older star map, and some of those places hold the remains of exploded stars that SPHEREx photographs today.</p>
      </header>
      <nav class="seg ln-st-parts" aria-label="Stories">${STORY_PARTS.map(
        (p) => `<a href="${learnHref({ view: "stories", part: p.id, arg: null })}" ${p.id === r.part ? `aria-current="page"` : ""}>${esc(p.label)}</a>`,
      ).join("")}</nav>
      <div class="ln-st-body"><p class="st-status" role="status">Loading the old star maps…</p></div>
    </section>`;
}

function glossaryHtml(): string {
  const sorted = [...GLOSSARY].sort((a, b) => a.term.localeCompare(b.term, "en", { sensitivity: "base" }));
  return `
    <section class="ln-gloss" aria-labelledby="ln-h">
      <header class="ln-head">
        <h1 id="ln-h" tabindex="-1">Glossary</h1>
        <p class="ln-lede-p">The words SkyShift uses, each in one plain sentence. Words with a dotted underline anywhere on the site open these sentences when you hover, focus or tap them.</p>
      </header>
      <dl class="ln-gloss-list">
        ${sorted
          .map(
            (g) => `<div class="ln-gloss-item" id="g-${g.slug}">
              <dt><a href="#/learn/glossary/${g.slug}" tabindex="-1">${esc(g.term)}</a></dt>
              <dd>${esc(g.def)}</dd>
            </div>`,
          )
          .join("")}
      </dl>
    </section>`;
}

export const mountLearn: MountPage = (el, ctx): PageHandle => {
  const kv: KV = safeStorage();
  let ctl: AbortController | null = null;
  let host: FigureHost | null = null;
  let lastRoute: LearnRoute | null = null;
  el.classList.add("ln-page");

  function teardown() {
    ctl?.abort();
    host?.plates.forEach((p) => p.destroy());
    host?.cleanups.forEach((f) => f());
    host = null;
    closeTermPopover();
  }

  function render(r: LearnRoute) {
    teardown();
    ctl = new AbortController();
    host = { signal: ctl.signal, plates: [], cleanups: [] };
    let p = readProgress(kv, LESSON_COUNT);
    if (r.view === "lesson") p = markOpened(kv, LESSON_COUNT, r.n);

    if (r.view === "home") {
      el.innerHTML = `<div class="ln ln-is-home">${homeHtml(p)}</div>`;
    } else {
      const main =
        r.view === "lesson"
          ? lessonHtml(LESSONS[r.n - 1])
          : r.view === "glossary"
            ? glossaryHtml()
            : r.view === "stories"
              ? storiesHtml(r)
              : `<section class="ln-teach" aria-labelledby="ln-h">${teachersHtml()}</section>`;
      el.innerHTML = `<div class="ln ln-is-${r.view}">${sideNav(r, p)}<div class="ln-main">${main}</div></div>`;
    }
    enhanceTerms(el);

    if (r.view === "home") void buildFigure("barnard", el.querySelector<HTMLElement>(".ln-home-fig")!, host);
    if (r.view === "lesson") {
      const fig = el.querySelector<HTMLElement>(".ln-fig")!;
      void buildFigure(LESSONS[r.n - 1].figure, fig, host);
      el.querySelectorAll<HTMLAnchorElement>("[data-done], [data-try]").forEach((a) =>
        a.addEventListener("click", () => markDone(kv, LESSON_COUNT, r.n)),
      );
    }
    if (r.view === "teachers") {
      void fillAnswerKey(el, host.signal);
      el.querySelector(".ln-print")?.addEventListener("click", () => window.print());
    }
    if (r.view === "stories") void openStories(r, host.signal);
    el.querySelector(".ln-reset")?.addEventListener("click", () => {
      resetProgress(kv);
      render({ view: "home" });
      el.querySelector<HTMLElement>("#ln-h")?.focus({ preventScroll: true });
    });
  }

  /** The Stories page's code, CSS and scripts load here, the first time it opens. */
  async function openStories(r: StoriesRoute, signal: AbortSignal) {
    const body = el.querySelector<HTMLElement>(".ln-st-body");
    if (!body) return;
    try {
      const m = await import("./stories");
      if (signal.aborted) return;
      await m.renderStories(body, r.part, r.arg, { signal, explore: ctx.explore, scrollTop: () => window.scrollTo({ top: 0 }) });
    } catch (e) {
      if (signal.aborted) return;
      console.warn("Learn: the Stories page didn't load", e);
      body.innerHTML = `<p class="ln-fail" role="alert">Couldn't load the Stories page. Reload the page to try again.</p>`;
    }
  }

  function focusTarget(r: LearnRoute, moved: boolean) {
    // The shell scrolls to the top after show(); anchors and focus wait for that.
    const run = () => {
      if (r.view === "glossary" && r.slug) {
        const found = document.getElementById(`g-${r.slug}`);
        const item = found && el.contains(found) ? found : null;
        if (item) {
          el.querySelectorAll(".ln-gloss-item.is-target").forEach((x) => x.classList.remove("is-target"));
          item.classList.add("is-target");
          item.scrollIntoView({ block: "center" });
          item.querySelector<HTMLElement>("dt a")?.focus({ preventScroll: true });
          return;
        }
      }
      if (moved) el.querySelector<HTMLElement>("#ln-h")?.focus({ preventScroll: true });
    };
    if (typeof requestAnimationFrame === "function") requestAnimationFrame(run);
    else run();
  }

  return {
    show(param) {
      const r = parseLearnRoute(param);
      const moved = lastRoute != null;
      lastRoute = r;
      render(r);
      if (r.view === "stories") document.title = "Stories in the sky: SkyShift";
      focusTarget(r, moved);
    },
    hide() {
      host?.plates.forEach((p) => p.suspend());
      closeTermPopover();
      lastRoute = null;
    },
    destroy() {
      teardown();
      el.innerHTML = "";
    },
  };
};
