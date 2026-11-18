// The Learn page (web/DESIGN.md "Pages", "Two audiences"): a short course of seven lessons, each
// one screen of plain words around one real SPHEREx example, plus guided practice, stories, the
// glossary and a teacher corner. Learn is in the header after the four screens.
// Routes (the shell passes everything after #/learn/ as param):
//   ""            the course as a path, then cards for everything else
//   "1" … "7"     one lesson
//   "glossary"    every word, with anchors: "glossary/<slug>"
//   "teachers"    a 45-minute class activity, printable
//   "stories[/<part>[/<arg>]]"  Stories in the sky: guest stars, around a spot, planets. Its code,
//                 CSS and the /guxing/ scripts load only when it opens (./stories, dynamic import).
//   "practice"    never reaches this page: guided practice plays on the Viewer (app/navigation.ts).
import "./learn.css";
import type { MountPage, PageContext, PageHandle } from "../app/modules";
import { readView } from "../app/state";
import { readPractice } from "../product/practice";
import { PRACTICE } from "../review/practice";
import { buildFigure, type FigureHost } from "./figures";
import { GLOSSARY, closeTermPopover, enhanceTerms, esc, findTerm } from "./glossary";
import { LESSONS, LESSON_COUNT, type Lesson } from "./lessons";
import { markDone, markOpened, nextLesson, readProgress, resetProgress, safeStorage, type KV, type Progress } from "./progress";
import { STORY_PARTS, isStoryPart, type StoryPart } from "./stories-route";
import { fillAnswerKey, teachersHtml } from "./teachers";
import { ASTEROID_ROUTE, ASTEROID_TITLE, COMET_ROUTE, COMET_TITLE, FIRST_STAR_ROUTE, FIRST_STAR_TITLE } from "./first-star-steps";

export type LearnRoute =
  | { view: "home" }
  | { view: "hunt" }
  | { view: "first-star" }
  | { view: "comet" }
  | { view: "asteroid" }
  | { view: "detective" }
  | { view: "lesson"; n: number }
  | { view: "glossary"; slug: string | null }
  | { view: "teachers" }
  | { view: "stories"; part: StoryPart; arg: string | null };
type StoriesRoute = Extract<LearnRoute, { view: "stories" }>;
type TrackingRoute = Extract<LearnRoute, { view: "first-star" | "comet" | "asteroid" }>;

const isTrackingRoute = (route: LearnRoute): route is TrackingRoute =>
  route.view === "first-star" || route.view === "comet" || route.view === "asteroid";

/** Read the route argument. Anything unknown goes to the course list. */
export function parseLearnRoute(param: string | null | undefined): LearnRoute {
  const p = (param ?? "").replace(/^\/+|\/+$/g, "");
  const [head, ...rest] = p.split("/");
  if (!head) return { view: "home" };
  if (head === "hunt" || head === "play") return { view: "hunt" };
  if (head === "first-star") return { view: "first-star" };
  if (head === "comet") return { view: rest[0] === "asteroid" ? "asteroid" : "comet" };
  if (head === "detective") return { view: "detective" };
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
  r.view === "asteroid" ? ASTEROID_ROUTE
    : r.view === "comet" ? COMET_ROUTE
    : r.view === "first-star"
    ? FIRST_STAR_ROUTE
    : r.view === "home"
    ? "#/learn"
    : r.view === "hunt"
      ? "#/learn/hunt"
    : r.view === "detective"
      ? "#/learn/detective"
    : r.view === "lesson"
      ? `#/learn/${r.n}`
      : r.view === "glossary"
        ? `#/learn/glossary${r.slug ? `/${r.slug}` : ""}`
        : r.view === "stories"
          ? `#/learn/stories${r.part === "guests" && !r.arg ? "" : `/${r.part}`}${r.arg ? `/${encodeURIComponent(r.arg).replace(/%2C/gi, ",")}` : ""}`
          : "#/learn/teachers";

/** The browser tab's name for a route (the shell names the page "Learn" before show()). */
export function learnTitle(r: LearnRoute): string {
  const name =
    r.view === "asteroid" ? ASTEROID_TITLE
      : r.view === "comet" ? COMET_TITLE
      : r.view === "first-star"
      ? FIRST_STAR_TITLE
      : r.view === "hunt"
      ? "Sky hunt"
    : r.view === "detective"
      ? "Sky detective"
    : r.view === "lesson"
      ? `${LESSONS[r.n - 1].title}`
      : r.view === "glossary"
        ? "Glossary"
        : r.view === "teachers"
          ? "For teachers"
          : r.view === "stories"
            ? "Stories in the sky"
            : "Learn";
  return `${name}: SkyShift`;
}

const check = `<svg class="ln-check" viewBox="0 0 16 16" aria-hidden="true" width="14" height="14"><path d="M3 8.5l3.2 3L13 4.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
const arrowR = `<svg viewBox="0 0 16 16" aria-hidden="true" width="14" height="14"><path d="M3 8h9M8.5 4l4 4-4 4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
const arrowL = `<svg viewBox="0 0 16 16" aria-hidden="true" width="14" height="14"><path d="M13 8H4M7.5 4l-4 4 4 4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

function progressLine(p: Progress): string {
  const n = p.done.length;
  return n === 0 ? "Not started yet." : n === LESSON_COUNT ? "All seven lessons done." : `${n} of ${LESSON_COUNT} lessons done.`;
}

const meter = (p: Progress, cls = "ln-meter") =>
  `<div class="${cls}" role="img" aria-label="${esc(progressLine(p))}"><span style="width:${((p.done.length / LESSON_COUNT) * 100).toFixed(1)}%"></span></div>`;

function sideNav(r: LearnRoute, p: Progress): string {
  const current = r.view === "lesson" ? r.n : null;
  const here = (v: LearnRoute["view"]) => (r.view === v ? `aria-current="page"` : "");
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
      ${meter(p)}
      <p class="ln-side-progress">${progressLine(p)}</p>
      <ul class="ln-side-more">
        <li><a href="${FIRST_STAR_ROUTE}" ${here("first-star")}>${FIRST_STAR_TITLE}</a></li>
        <li><a href="${COMET_ROUTE}" ${here("comet")}>${COMET_TITLE}</a></li>
        <li><a href="#/learn/detective" ${here("detective")}>Sky detective</a></li>
        <li><a href="#/learn/hunt" ${here("hunt")}>Sky hunt</a></li>
        <li><a href="#/learn/practice">Guided practice</a></li>
        <li><a href="#/learn/stories" ${here("stories")}>Stories in the sky</a></li>
        <li><a href="#/learn/glossary" ${here("glossary")}>Glossary</a></li>
        <li><a href="#/learn/teachers" ${here("teachers")}>For teachers</a></li>
      </ul>
    </nav>`;
}

// ---- home: one inviting page ------------------------------------------------------------------
export interface PracticeState {
  done: number;
  of: number;
}

function practiceState(kv: KV): PracticeState {
  let done = 0;
  try {
    done = readPractice(kv).length;
  } catch {
    // Storage blocked: show the card as not started.
  }
  return { done, of: PRACTICE.length };
}

/** The glossary card shows one real entry, dotted the way it looks across the site. */
const SPECIMEN = "proper motion";

function cardsHtml(practice: PracticeState): string {
  const g = findTerm(SPECIMEN);
  const practiceMeta =
    practice.done === 0 ? `${practice.of} real examples` : practice.done >= practice.of ? `Done ${check}` : `${practice.done} of ${practice.of} done`;
  const cards = [
    {
      cls: "hunt",
      href: "#/learn/hunt",
      kicker: "Explore by playing",
      title: "Sky hunt",
      text: "Find the same star in three real observations. Explore five fields, switch on infrared vision and build your own collection. Explore at your own pace.",
      meta: "Five fields to explore",
      pic: `<span class="ln-card-pic ln-card-sky" role="img" aria-label="Real SPHEREx infrared sky"></span>`,
    },

    {
      cls: "practice",
      href: "#/learn/practice",
      kicker: "Practice",
      title: "Guided practice",
      text: "Three real SPHEREx fields with a known answer: an asteroid, a fast star and a star that changes. Blink each one in the Viewer and say what you see.",
      meta: practiceMeta,
      pic: `<span class="ln-card-pic ln-card-sky" role="img" aria-label="The Milky Way in Aquila as SPHEREx saw it at 1.35 µm"></span>`,
    },
    {
      cls: "stories",
      href: "#/learn/stories",
      kicker: "Stories",
      title: "Stories in the sky",
      text: "Guest stars from Chinese histories and what SPHEREx sees where they blazed, the old star map around any spot, and the Moon and planets in 2026–27.",
      meta: `${STORY_PARTS.length} parts`,
      pic: `<span class="ln-card-pic"><img src="/guxing/data/nasa/spherex_remnants/SN1054.png" alt="The Crab Nebula, the remnant of the guest star of 1054, in SPHEREx colours" width="256" height="256" decoding="async"></span>`,
    },
    {
      cls: "glossary",
      href: "#/learn/glossary",
      kicker: "Words",
      title: "Glossary",
      text: `${GLOSSARY.length} words in one plain sentence each. Dotted words across SkyShift open the same sentences.`,
      meta: "A to Z",
      pic: `<span class="ln-card-pic ln-card-word" aria-hidden="true"><span class="ln-card-term">${esc(g?.term ?? "Proper motion")}</span><span class="ln-card-def">${esc(
        g?.def ?? "",
      )}</span></span>`,
    },
    {
      cls: "teachers",
      href: "#/learn/teachers",
      kicker: "Classroom",
      title: "For teachers",
      text: "A 45-minute class activity: measure Barnard's Star's motion between SPHEREx visits and compare it with Gaia. Printable worksheet and answer key.",
      meta: "Ages 13 and up",
      pic: `<span class="ln-card-pic ln-card-sheet" aria-hidden="true"><span class="ln-card-sheet-h">Barnard's Star in 45 minutes</span><span class="ln-card-row"><span>Measured</span><i></i></span><span class="ln-card-row"><span>Gaia: 10.39″ × years</span><i></i></span><span class="ln-card-row"><span>Difference</span><i></i></span></span>`,
    },
  ];
  return `
    <ul class="ln-cards" role="list">
      ${cards
        .map(
          (c) => `<li class="ln-card ln-card-${c.cls}">
            <a href="${c.href}">
              ${c.pic}
              <span class="ln-card-t">
                <span class="ln-card-k">${c.kicker}</span>
                <span class="ln-card-title">${esc(c.title)}</span>
                <span class="ln-card-text">${esc(c.text)}</span>
                <span class="ln-card-meta">${c.meta}</span>
              </span>
            </a>
          </li>`,
        )
        .join("")}
    </ul>`;
}

function homeHtml(p: Progress, practice: PracticeState): string {
  const next = nextLesson(p, LESSON_COUNT);
  const started = p.done.length > 0 || p.last != null;
  const all = p.done.length === LESSON_COUNT;
  const cta = all ? "Start again from lesson 1" : started ? `Continue with lesson ${next}` : "Start with lesson 1";
  const upNext = all ? null : next;
  return `
    <section class="ln-home" aria-labelledby="ln-h">
      <header class="ln-home-head">
        <p class="ln-eyebrow">Learn</p>
        <h1 id="ln-h" tabindex="-1">Learn to read the SPHEREx sky</h1>
        <p class="ln-lede-p">Seven short lessons, each built around real SPHEREx pictures. About twenty minutes in all, and then you'll know what to look for when you blink.</p>
        <p class="ln-home-cta">
          <a class="btn btn-primary" href="#/learn/${all ? 1 : next}">${cta} ${arrowR}</a>
          <a class="btn btn-ghost" href="#/learn/practice">Guided practice</a>
          <a class="btn btn-ghost ln-hunt-entry" href="#/learn/hunt">Sky hunt ↗</a>
          <a class="btn btn-ghost ln-detective-start" href="#/learn/detective">Sky detective ↗</a>
        </p>
        <div class="ln-home-progress">
          ${meter(p, "ln-meter ln-home-meter")}
          <span>${progressLine(p)}</span>
          ${started ? `<button type="button" class="btn btn-ghost ln-reset">Clear my progress</button>` : ""}
        </div>
      </header>
      <div class="ln-home-figure-area"><div class="ln-home-fig ln-fig" role="group" aria-label="Real SPHEREx example: Barnard's Star"></div></div>
      <section class="ln-path" aria-labelledby="ln-path-h">
        <h2 id="ln-path-h" class="ln-sec-h">The course</h2>
        <ol class="ln-course">
          ${LESSONS.map((l) => {
            const done = p.done.includes(l.n);
            const isNext = l.n === upNext;
            return `<li class="${done ? "is-done" : ""}${isNext ? " is-next" : ""}">
              <a href="#/learn/${l.n}" ${isNext ? `aria-describedby="ln-next-${l.n}"` : ""}>
                <span class="ln-num" aria-hidden="true">${done ? check : l.n}</span>
                <span class="ln-course-t"><span class="ln-course-title">${esc(l.title)}${done ? `<span class="visually-hidden">, done</span>` : ""}${
                  isNext ? ` <span class="ln-tag" id="ln-next-${l.n}">${started ? "Up next" : "Start here"}</span>` : ""
                }</span>
                <span class="ln-course-sum">${esc(l.summary)}</span></span>
              </a>
            </li>`;
          }).join("")}
        </ol>
      </section>
      <section class="ln-home-more" aria-labelledby="ln-more-h">
        <h2 id="ln-more-h" class="ln-sec-h">Practise, explore, teach</h2>
        <a class="ln-activity-entry ln-first-star-entry" href="${FIRST_STAR_ROUTE}"><span><strong>${FIRST_STAR_TITLE}</strong><span>Follow one clue at a time through real NASA observations and find a star for yourself.</span></span><span class="ln-activity-action">Start the session ↗</span></a>
        <a class="ln-activity-entry ln-comet-entry" href="${COMET_ROUTE}"><span><strong>${COMET_TITLE}</strong><span>Track a comet in NASA TESS images, then find a moving point in NASA SPHEREx images.</span></span><span class="ln-activity-action">Start the session ↗</span></a>
        <a class="ln-activity-entry ln-detective-entry" href="#/learn/detective"><span><span class="ln-card-k">NASA data, in your hands</span><strong>Sky detective: old clues, new light.</strong><span>Find a star by its neighbours, compare real visits and explore an infrared spectrum. Three investigations. One observation notebook.</span></span><span class="ln-activity-action">Start investigating ↗</span></a>
        ${cardsHtml(practice)}
      </section>
    </section>`;
}

// ---- one lesson ---------------------------------------------------------------------------------
function lessonHtml(l: Lesson): string {
  const prev = LESSONS[l.n - 2];
  const next = LESSONS[l.n];
  return `
    <article class="ln-lesson" aria-labelledby="ln-h">
      <header class="ln-head">
        <p class="ln-eyebrow">Lesson ${l.n} of ${LESSON_COUNT}</p>
        <h1 id="ln-h" tabindex="-1">${esc(l.title)}</h1>
      </header>
      <div class="ln-lede">${l.lede()}</div>
      <div class="ln-fig" role="group" aria-label="Real SPHEREx example"></div>
      <div class="ln-body">${l.body()}</div>
      <section class="ln-try" aria-labelledby="ln-try-h">
        <h2 id="ln-try-h">${esc(l.tryTitle)}</h2>
        <p>${esc(l.tryText)}</p>
        <p class="ln-try-actions">${l.tryIt
          .map((a, i) => `<a class="btn ${i === 0 ? "btn-primary" : "btn-ghost"}" href="${esc(a.href)}" data-try>${esc(a.label)}${i === 0 ? ` ${arrowR}` : ""}</a>`)
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
        <p class="ln-eyebrow">Another way to read the sky</p>
        <h1 id="ln-h" tabindex="-1">Stories in the sky</h1>
        <p class="ln-lede-p">Long before SPHEREx, court astronomers in China watched the same sky and wrote down what changed. Their records name places on an older star map, and some of those places hold the remains of exploded stars that SPHEREx photographs today.</p>
        <p class="ln-small">A cultural reading of the sky, kept apart from SkyShift's scientific checks.</p>
      </header>
      <nav class="seg ln-st-parts" aria-label="Stories">${STORY_PARTS.map(
        (p) => `<a href="${learnHref({ view: "stories", part: p.id, arg: null })}" ${p.id === r.part ? `aria-current="page"` : ""}>${esc(p.label)}</a>`,
      ).join("")}</nav>
      <div class="ln-st-body"><p class="st-status" role="status">Loading the old star maps…</p></div>
    </section>`;
}

function glossaryHtml(): string {
  const sorted = [...GLOSSARY].sort((a, b) => a.term.localeCompare(b.term, "en", { sensitivity: "base" }));
  // Groups by first letter; "2MASS" goes under #, "µm" under µ.
  const groupOf = (term: string) => {
    const c = term[0].toUpperCase();
    return /\d/.test(c) ? "#" : c === "Μ" ? "µ" : c;
  };
  const letters = [...new Set(sorted.map((g) => groupOf(g.term)))];
  let last = "";
  return `
    <section class="ln-gloss" aria-labelledby="ln-h">
      <header class="ln-head">
        <h1 id="ln-h" tabindex="-1">Glossary</h1>
        <p class="ln-lede-p">The words SkyShift uses, each in one plain sentence. Words with a dotted underline anywhere on the site open these sentences when you hover, focus or tap them.</p>
      </header>
      <p class="ln-az" role="group" aria-label="Jump to a letter">${letters.map((c) => `<button type="button" data-letter="${c}">${c}</button>`).join("")}</p>
      <dl class="ln-gloss-list">
        ${sorted
          .map((g) => {
            const c = groupOf(g.term);
            const first = c !== last;
            last = c;
            return `<div class="ln-gloss-item" id="g-${g.slug}" ${first ? `data-first="${c}"` : ""}>
              <dt><a href="#/learn/glossary/${g.slug}" tabindex="-1">${esc(g.term)}</a></dt>
              <dd>${esc(g.def)}</dd>
            </div>`;
          })
          .join("")}
      </dl>
    </section>`;
}

export const mountLearn: MountPage = (el, ctx: PageContext): PageHandle => {
  const kv: KV = safeStorage();
  let ctl: AbortController | null = null;
  let host: FigureHost | null = null;
  let lastRoute: LearnRoute | null = null;
  let hunt: PageHandle | null = null;
  let detective: PageHandle | null = null;
  let visible = false;
  el.classList.add("ln-page");

  function teardown() {
    ctl?.abort();
    hunt?.destroy?.();
    hunt = null;
    detective?.destroy?.();
    detective = null;
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
      el.innerHTML = `<div class="ln ln-is-home">${homeHtml(p, practiceState(kv))}</div>`;
    } else if (isTrackingRoute(r)) {
      el.innerHTML = `<div class="ln ln-is-home"><div class="ln-first-star-host"><p role="status">Loading the session…</p></div></div>`;
    } else if (r.view === "hunt") {
      el.innerHTML = `<div class="ln ln-is-home ln-is-hunt"><div class="ln-hunt-host"><p role="status">Loading Sky hunt…</p></div></div>`;
    } else if (r.view === "detective") {
      el.innerHTML = `<div class="ln ln-is-home ln-is-detective"><div class="ln-detective-host"><p role="status">Loading Sky detective…</p></div></div>`;
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
    if (r.view === "glossary") {
      // Letters jump within the page; the address stays the glossary's.
      const signal = host.signal;
      el.querySelectorAll<HTMLButtonElement>(".ln-az button").forEach((b) =>
        b.addEventListener(
          "click",
          () => {
            const item = el.querySelector<HTMLElement>(`[data-first="${b.dataset.letter}"]`);
            item?.scrollIntoView({ block: "start" });
            item?.querySelector<HTMLElement>("dt a")?.focus({ preventScroll: true });
          },
          { signal },
        ),
      );
    }
    if (r.view === "teachers") {
      void fillAnswerKey(el, host.signal);
      el.querySelector(".ln-print")?.addEventListener("click", () => window.print());
    }
    if (r.view === "stories") void openStories(r, host.signal);
    if (r.view === "hunt") void openHunt(host.signal);
    if (isTrackingRoute(r)) void openTrackingGuide(r, host.signal);
    if (r.view === "detective") void openDetective(host.signal);
    el.querySelector(".ln-reset")?.addEventListener("click", () => {
      resetProgress(kv);
      render({ view: "home" });
      el.querySelector<HTMLElement>("#ln-h")?.focus({ preventScroll: true });
    });
  }

  async function openTrackingGuide(r: TrackingRoute, signal: AbortSignal) {
    const body = el.querySelector<HTMLElement>(".ln-first-star-host");
    if (!body) return;
    try {
      const { mountTrackingGuide } = await import("./first-star");
      if (signal.aborted) return;
      mountTrackingGuide(body, signal, r.view === "first-star" ? "star" : r.view);
      focusTarget(r, true);
    } catch (e) {
      if (signal.aborted) return;
      console.warn("Learn: tracking session didn't load", e);
      body.innerHTML = '<p class="ln-fail" role="alert">Couldn’t load the session. Reload the page to try again.</p>';
    }
  }

  async function openHunt(signal: AbortSignal) {
    const body = el.querySelector<HTMLElement>(".ln-hunt-host");
    if (!body) return;
    try {
      const m = await import("../play/page");
      if (signal.aborted || !visible) return;
      hunt = m.mountPlay(body, ctx);
      hunt.show(null);
    } catch (e) {
      if (signal.aborted || !visible) return;
      console.warn("Learn: Sky hunt didn't load", e);
      body.innerHTML = `<p class="ln-fail" role="alert">Couldn't load Sky hunt. Reload the page to try again.</p>`;
    }
  }

  async function openDetective(signal: AbortSignal) {
    const body = el.querySelector<HTMLElement>(".ln-detective-host");
    if (!body) return;
    try {
      const m = await import("./detective");
      if (signal.aborted || !visible) return;
      detective = m.mountDetective(body, ctx);
      detective.show(null);
    } catch (e) {
      if (signal.aborted || !visible) return;
      console.warn("Learn: Sky detective didn't load", e);
      body.innerHTML = `<p class="ln-fail" role="alert">Couldn't load Sky detective. Reload the page to try again.</p>`;
    }
  }

  /** The Stories page's code, CSS and scripts load here, the first time it opens. */
  async function openStories(r: StoriesRoute, signal: AbortSignal) {
    const body = el.querySelector<HTMLElement>(".ln-st-body");
    if (!body) return;
    try {
      const m = await import("./stories");
      if (signal.aborted) return;
      await m.renderStories(body, r.part, r.arg, {
        signal,
        explore: ctx.explore,
        // Around a spot opens where the Viewer is now (or the spot in the address).
        here: () => ctx.currentView?.() ?? readView(),
        scrollTop: () => window.scrollTo({ top: 0 }),
      });
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
      visible = true;
      const r = parseLearnRoute(param);
      const moved = lastRoute != null;
      lastRoute = r;
      render(r);
      document.title = learnTitle(r);
      focusTarget(r, moved);
    },
    hide() {
      visible = false;
      if (lastRoute && isTrackingRoute(lastRoute)) ctl?.abort();
      hunt?.hide?.();
      detective?.hide?.();
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
