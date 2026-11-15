// The opening: "The sky is never still." A scroll-driven overlay from the SPHEREx sky down to
// Earth, to one person looking up, into her eye and out onto the sky again, then it fades into
// the page underneath. Loaded on demand (src/intro/index.ts), so the home page never waits.
// Original detailed planets and chosen goddess by 春。 with Codex, from 1390d01 in
// “讨论项目网站首页与接入”. The overlay ends on the current page, including the neutral sky map.
//
// The background is real SPHEREx data: the IRSA SPHEREx DR1 all-sky HiPS cube
// (https://irsa.ipac.caltech.edu/data/hips/SPHEREx/spherex_allsky_cube), frame 25 (the middle
// channel of D2, 1.346 µm: CUBE_UM in src/sky/layers.ts). Order-3 tiles, reprojected to a 50°
// SIN view at RA 281°, Dec −4° (the Milky Way in Aquila and Scutum, Galactic longitude ≈ 28°;
// the branch's Galactic-centre field has no-data gaps right where the title and pupil sit),
// ×0.78 with gamma 1.1 and a 0.5 px Gaussian blur (keeps the file small), 1600 × 1000 WebP q55.
// Everything else is drawn in code (scene.ts, portrait.ts).
import "./opening.css";
import { CHAPTERS, chapterAt, sceneAt, scrollProgress } from "./model";
import { paintScene } from "./scene";
import { preparePlanetaryOpening, type PlanetPreparation } from './planet-worker-client';

export const SKY_URL = "/opening/spherex-sky.webp";
// English wording: the author's own (1390d01 on codex/frontend-unified) wherever it exists.
const CREDIT_SKY = "Sky: SPHEREx DR1 at 1.35 µm, NASA/JPL-Caltech/IPAC (IRSA).";
const CREDIT_ART = "Illustration: Earth, planets and Galileo drawn in code.";

export type CloseReason = "skip" | "end" | "navigate";

export interface OpeningOptions {
  /** A still title card instead of the scroll journey (prefers-reduced-motion). */
  still?: boolean;
  /** Focus goes back here when the opening closes (the replay link). */
  returnFocus?: HTMLElement | null;
  onClose?(reason: CloseReason): void;
  skyUrl?: string;
}

export interface OpeningHandle {
  readonly element: HTMLElement;
  close(reason?: CloseReason): void;
}

const html = String.raw;

/** Show the opening over `app` (the page is inert until it closes). */
export function playOpening(app: HTMLElement, opts: OpeningOptions = {}): OpeningHandle {
  let motion: MediaQueryList | null = null;
  try {
    motion = window.matchMedia("(prefers-reduced-motion: reduce)");
  } catch {
    /* no matchMedia */
  }
  // The preference may have changed while this lazy-loaded module was in flight.
  const still = !!opts.still || !!motion?.matches;
  const el = document.createElement("div");
  el.className = still ? "op is-still" : "op";
  el.setAttribute("role", "dialog");
  el.setAttribute("aria-modal", "true");
  el.setAttribute(
    "aria-label",
    still ? "SkyShift opening, shown still for reduced motion." : "SkyShift opening. Scroll to follow the story, or choose Explore the sky.",
  );
  el.setAttribute("aria-describedby", "op-how");
  el.tabIndex = -1;
  el.innerHTML = html`
    <div class="op-sky" aria-hidden="true"><img alt="" decoding="async" /></div>
    <canvas class="op-scene" aria-hidden="true"></canvas>
    <div class="op-bar">
      <p class="op-brand">SkyShift<span>The universe, in motion</span></p>
    </div>
    <button class="op-skip" type="button">Explore the sky<span class="op-skip-arrow" aria-hidden="true">↗</span></button>
    <div class="op-hero">
      <p class="op-kicker">A journey through the infrared sky</p>
      <h2 id="op-title">The sky is<br /> never <em>still.</em></h2>
      <p class="op-sub">Every glance is a new beginning.</p>
      <p class="op-hint" aria-hidden="true">Scroll to follow the story<span>↓</span></p>
    </div>
    <p class="op-chapter" aria-hidden="true"><span class="op-chapter-no"></span><span class="op-chapter-text"></span></p>
    <p class="visually-hidden" id="op-how">Explore the sky opens the site. Escape also closes the opening.</p>
    <p class="op-credit"><span class="op-credit-sky">${CREDIT_SKY}</span> ${CREDIT_ART}</p>
    <div class="op-progress" aria-hidden="true"><span></span></div>
    <div class="op-track" aria-hidden="true"></div>
  `;
  const $ = <T extends Element>(sel: string) => el.querySelector(sel) as T;
  const img = $<HTMLImageElement>(".op-sky img");
  const sky = $<HTMLElement>(".op-sky");
  const canvas = $<HTMLCanvasElement>(".op-scene");
  const hero = $<HTMLElement>(".op-hero");
  const chapter = $<HTMLElement>(".op-chapter");
  const chapterNo = $<HTMLElement>(".op-chapter-no");
  const chapterText = $<HTMLElement>(".op-chapter-text");
  const brand = $<HTMLElement>(".op-brand");
  const credit = $<HTMLElement>(".op-credit");
  const fill = $<HTMLElement>(".op-progress span");
  const skip = $<HTMLButtonElement>(".op-skip");

  img.addEventListener("error", () => {
    // No sky, no sky credit; the scenery still works on the dark ground.
    sky.remove();
    $(".op-credit-sky")?.remove();
  });
  img.src = opts.skyUrl ?? SKY_URL;

  // The attribute (not the property) so it also holds where inert isn't implemented.
  const wasInert = app.hasAttribute("inert");
  app.setAttribute("inert", "");
  document.documentElement.classList.add("op-open");
  document.body.append(el);

  let frame = 0;
  let painted = -1;
  let closed = false;
  let revealing = false;
  let revealFrame = 0;
  let revealTimer: number | undefined;
  let sceneAvailable = true;
  let sceneProgress = -1;
  let sceneSize = "";
  let preparation: PlanetPreparation | undefined;
  let preparationSize = 0;

  function preparePlanets() {
    const size = Math.min(innerWidth, innerHeight) < 600 ? 384 : 768;
    if (size === preparationSize) return;
    preparation?.cancel();
    preparationSize = size;
    const current = preparation = preparePlanetaryOpening(innerWidth, innerHeight);
    el.dataset.planetPreparation = current.pending ? 'pending' : 'ready';
    void current.ready.then(result => {
      if (closed || current !== preparation) return;
      el.dataset.planetPreparation = result;
      sceneProgress = -1;
      schedule();
    });
  }

  function draw() {
    frame = 0;
    if (closed || revealing) return;
    const p = still ? 0 : scrollProgress(el.scrollTop, el.clientHeight, el.scrollHeight);
    painted = p;
    const s = sceneAt(p);
    // Ambient movement stays on the compositor; the detailed canvas only redraws on input.
    el.classList.toggle('is-drifting', !still && p < .12);
    const size = `${innerWidth}:${innerHeight}:${devicePixelRatio || 1}`;
    if (sceneAvailable && (p !== sceneProgress || size !== sceneSize)) {
      try {
        paintScene(canvas, p);
        sceneProgress = p;
        sceneSize = size;
      } catch {
        // Canvas resources can fail on low-memory devices. Keep the sky, text and exit
        // action usable rather than leaving the page inert behind a broken renderer.
        sceneAvailable = false;
        canvas.hidden = true;
      }
    }
    const camera = Math.min(p, 0.9);
    sky.style.transform = `scale(${1.12 + camera * 0.08}) rotate(${-2 + camera * 3}deg)`;
    hero.style.opacity = String(s.title);
    hero.style.transform = `translateY(${-p * 150}px)`;
    hero.hidden = p > 0.15;
    const i = chapterAt(p);
    chapter.hidden = i < 0;
    if (i >= 0) {
      chapterNo.textContent = `0${i + 1} / 0${CHAPTERS.length}`;
      chapterText.textContent = CHAPTERS[i];
    }
    brand.style.opacity = credit.style.opacity = String(s.chrome);
    // Skip steps back too, so it never sits on the site's header (unless it has focus: CSS).
    skip.style.opacity = String(s.chrome);
    fill.style.transform = `scaleX(${p})`;
    // Keep enough sky for a continuous final dissolve, even when a wheel fling skips ahead.
    el.style.opacity = String(Math.max(.65, s.veil));
    if (p >= 0.995) reveal();
  }
  function schedule() {
    if (!closed && !revealing && !frame) frame = requestAnimationFrame(draw);
  }
  function reveal() {
    if (revealing) return;
    revealing = true;
    el.classList.remove('is-drifting');
    el.classList.add('is-revealing');
    revealFrame = requestAnimationFrame(() => {
      revealFrame = 0;
      if (!closed) el.style.opacity = '0';
    });
    // transitionend may not arrive in a background tab or after an interrupted paint.
    revealTimer = window.setTimeout(() => close('end'), 730);
  }
  function onTransitionEnd(event: TransitionEvent) {
    if (revealing && event.target === el && event.propertyName === 'opacity') close('end');
  }
  function onVisibility() {
    el.classList.toggle('is-background-paused', document.hidden);
  }
  function onResize() {
    // A rotated phone or a new toolbar height must not move the story on or end it.
    if (!still && painted >= 0) el.scrollTop = painted * Math.max(0, el.scrollHeight - el.clientHeight);
    preparePlanets();
    schedule();
  }
  function onKey(ev: KeyboardEvent) {
    if (ev.key === "Escape") {
      ev.preventDefault();
      ev.stopPropagation();
      close("skip");
    }
  }
  function onHash() {
    close("navigate");
  }
  // Reduced motion switched on mid-way: get out of the way.
  function onMotion() {
    if (!still && motion?.matches) close("skip");
  }

  function close(reason: CloseReason = "skip") {
    if (closed) return;
    closed = true;
    cancelAnimationFrame(frame);
    cancelAnimationFrame(revealFrame);
    clearTimeout(revealTimer);
    preparation?.cancel();
    el.removeEventListener("scroll", schedule);
    removeEventListener("resize", onResize);
    removeEventListener("hashchange", onHash);
    document.removeEventListener("keydown", onKey, true);
    motion?.removeEventListener?.("change", onMotion);
    document.removeEventListener('visibilitychange', onVisibility);
    el.removeEventListener('transitionend', onTransitionEnd);
    el.remove();
    if (!wasInert) app.removeAttribute("inert");
    document.documentElement.classList.remove("op-open");
    const back = opts.returnFocus;
    if (back && back.isConnected) back.focus({ preventScroll: true });
    opts.onClose?.(reason);
  }

  skip.addEventListener("click", () => close("skip"));
  el.addEventListener("scroll", schedule, { passive: true });
  addEventListener("resize", onResize, { passive: true });
  addEventListener("hashchange", onHash);
  document.addEventListener("keydown", onKey, true);
  motion?.addEventListener?.("change", onMotion);
  document.addEventListener('visibilitychange', onVisibility);
  el.addEventListener('transitionend', onTransitionEnd);

  onVisibility();
  el.scrollTop = 0;
  preparePlanets();
  draw();
  // Focus the dialog itself: screen readers read its name and description, the arrow keys and
  // Space scroll the story, and Tab reaches Skip.
  el.focus({ preventScroll: true });

  return { element: el, close };
}
