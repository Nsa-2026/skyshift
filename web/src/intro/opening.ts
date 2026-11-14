// The opening: "The sky is never still." A scroll-driven overlay from the SPHEREx sky down to
// Earth, to one person looking up, into her eye and out onto the sky again, then it fades into
// the page underneath. Loaded on demand (src/intro/index.ts), so the home page never waits.
// Design and scenery by 春。 with Codex (codex/frontend-unified), adapted for main.
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

export const SKY_URL = "/opening/spherex-sky.webp";
const CREDIT_SKY = "Sky: SPHEREx DR1 at 1.35 µm, NASA/JPL-Caltech/IPAC (IRSA).";
const CREDIT_ART = "Earth and figure are drawn in code.";

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
  const still = !!opts.still;
  const el = document.createElement("div");
  el.className = still ? "op is-still" : "op";
  el.setAttribute("role", "dialog");
  el.setAttribute("aria-modal", "true");
  el.setAttribute("aria-labelledby", "op-title");
  el.setAttribute("aria-describedby", "op-how");
  el.tabIndex = -1;
  el.innerHTML = html`
    <div class="op-sky" aria-hidden="true"><img alt="" decoding="async" /></div>
    <canvas class="op-scene" aria-hidden="true"></canvas>
    <div class="op-bar">
      <p class="op-brand">SkyShift<span>The universe, in motion</span></p>
      <button class="op-skip" type="button">
        ${still ? "Enter" : html`Skip<span class="visually-hidden"> the opening</span>`}<span class="op-skip-arrow" aria-hidden="true">↗</span>
      </button>
    </div>
    <div class="op-hero">
      <h2 id="op-title">The sky is<br /> never <em>still.</em></h2>
      <p class="op-sub">Every look up starts a search.</p>
      <p class="op-hint" aria-hidden="true">Scroll to enter<span>↓</span></p>
    </div>
    <p class="op-chapter" aria-hidden="true"><span class="op-chapter-no"></span><span class="op-chapter-text"></span></p>
    <p class="visually-hidden" id="op-how">
      ${still
        ? "SkyShift's opening, shown still because your system asks for reduced motion."
        : "A short animation that plays as you scroll. Press Escape or Skip to go straight to the site."}
    </p>
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

  function draw() {
    frame = 0;
    if (closed) return;
    const p = still ? 0 : scrollProgress(el.scrollTop, el.clientHeight, el.scrollHeight);
    painted = p;
    const s = sceneAt(p);
    paintScene(canvas, p);
    const camera = Math.min(p, 0.9);
    sky.style.transform = `scale(${1.12 + camera * 0.08}) rotate(${-2 + camera * 3}deg)`;
    hero.style.opacity = String(s.title);
    hero.style.transform = `translateY(${-p * 150}px)`;
    hero.hidden = p > 0.15;
    const i = chapterAt(p);
    chapter.hidden = i < 0;
    if (i >= 0) {
      chapterNo.textContent = `${i + 1} / ${CHAPTERS.length}`;
      chapterText.textContent = CHAPTERS[i];
    }
    brand.style.opacity = credit.style.opacity = String(s.chrome);
    // Skip steps back too, so it never sits on the site's header (unless it has focus: CSS).
    skip.style.opacity = String(s.chrome);
    fill.style.transform = `scaleX(${p})`;
    el.style.opacity = String(s.veil);
    if (p >= 0.995) close("end");
  }
  function schedule() {
    if (!closed && !frame) frame = requestAnimationFrame(draw);
  }
  function onResize() {
    // A rotated phone or a new toolbar height must not move the story on or end it.
    if (!still && painted >= 0) el.scrollTop = painted * Math.max(0, el.scrollHeight - el.clientHeight);
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
  let motion: MediaQueryList | null = null;
  try {
    motion = window.matchMedia("(prefers-reduced-motion: reduce)");
  } catch {
    /* no matchMedia */
  }
  function onMotion() {
    if (!still && motion?.matches) close("skip");
  }

  function close(reason: CloseReason = "skip") {
    if (closed) return;
    closed = true;
    cancelAnimationFrame(frame);
    el.removeEventListener("scroll", schedule);
    removeEventListener("resize", onResize);
    removeEventListener("hashchange", onHash);
    document.removeEventListener("keydown", onKey, true);
    motion?.removeEventListener?.("change", onMotion);
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

  el.scrollTop = 0;
  draw();
  // Focus the dialog itself: screen readers read its name and description, the arrow keys and
  // Space scroll the story, and Tab reaches Skip.
  el.focus({ preventScroll: true });

  return { element: el, close };
}
