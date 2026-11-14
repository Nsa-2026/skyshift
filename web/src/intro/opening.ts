// The opening: "The sky is never still." A scroll-driven overlay from the SPHEREx sky down to
// Earth, to one person looking up, into her eye and out onto the sky again, then it fades into
// the page underneath (on the home page, the Sky map, which loads meanwhile).
// Design and scenery by 春。 with Codex (codex/frontend-unified), adapted for main.
//
// The background is real SPHEREx data: the IRSA SPHEREx DR1 all-sky HiPS cube
// (https://irsa.ipac.caltech.edu/data/hips/SPHEREx/spherex_allsky_cube), frame 25 (the middle
// channel of D2, 1.346 µm: CUBE_UM in src/sky/layers.ts). Order-3 tiles, reprojected to a 50°
// SIN view at RA 281°, Dec −4° (the Milky Way in Aquila and Scutum, Galactic longitude ≈ 28°;
// the branch's Galactic-centre field has no-data gaps right where the title and pupil sit),
// ×0.78 with gamma 1.1 and a 0.5 px Gaussian blur (keeps the file small), 1600 × 1000 WebP q55.
// Everything else is drawn in code (scene.ts, portrait.ts).
//
// Images come from public/opening/manifest.json (manifest.ts): the sky, and optional Earth and
// hills images that replace the drawn ones. The credit line follows what is on screen.
import "./opening.css";
import { DEFAULT_SLOTS, creditLine, loadManifest, sizeProblem, slotUrl, type SlotId, type Slots } from "./manifest";
import { CHAPTERS, chapterAt, sceneAt, scrollProgress } from "./model";
import { paintScene, type SceneArt } from "./scene";

export type CloseReason = "skip" | "end" | "navigate";

export interface OpeningOptions {
  /** A still title card instead of the scroll journey (prefers-reduced-motion). */
  still?: boolean;
  /** Where focus goes when the opening closes by Skip, Escape or its end (not on navigation). */
  returnFocus?: () => HTMLElement | null;
  onClose?(reason: CloseReason): void;
  /** The image slots (tests); by default read from the manifest. */
  slots?: Slots | Promise<Slots>;
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
  el.setAttribute(
    "aria-label",
    still ? "SkyShift opening, shown still for reduced motion. Skip the intro to see the sky map." : "SkyShift opening. Scroll down to explore, or skip the intro.",
  );
  el.setAttribute("aria-describedby", "op-how");
  el.tabIndex = -1;
  el.innerHTML = html`
    <div class="op-sky"><img alt="" decoding="async" hidden /></div>
    <canvas class="op-scene" aria-hidden="true"></canvas>
    <div class="op-bar">
      <p class="op-brand">SkyShift<span>NASA's SPHEREx sky, in motion</span></p>
      <button class="op-skip" type="button">Skip intro<span class="op-skip-arrow" aria-hidden="true">↗</span></button>
    </div>
    <div class="op-hero">
      <p class="op-kicker">A journey through the infrared sky</p>
      <h2 id="op-title">The sky is<br /> never <em>still.</em></h2>
      <p class="op-sub">SPHEREx maps the whole sky every six months. Blink its photos and see what moved.</p>
      <p class="op-hint" aria-hidden="true">${still ? "Scroll to continue" : "Scroll to explore"}<span>↓</span></p>
    </div>
    <p class="op-chapter" aria-hidden="true"><span class="op-chapter-no"></span><span class="op-chapter-text"></span></p>
    <p class="visually-hidden" id="op-how">Escape also skips the intro.</p>
    <p class="op-credit"></p>
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

  let frame = 0;
  let painted = -1;
  let closed = false;

  // ---- images (manifest.ts) -------------------------------------------------------------------
  let slots: Slots = DEFAULT_SLOTS;
  /** Slots whose image is on screen; the credit line names exactly these. */
  const shown = new Set<SlotId>();
  const art: SceneArt = {};
  const renderCredit = () => void (credit.textContent = creditLine(slots, shown));
  renderCredit();
  function checkSize(id: SlotId, w: number, h: number) {
    const problem = sizeProblem(slots[id], w, h);
    if (problem) console.warn(`Opening image: ${problem}`);
  }

  function showSky(url: string | null) {
    if (!url) {
      // No sky, no sky credit; the scenery still works on the dark ground.
      sky.remove();
      shown.delete("sky");
      renderCredit();
      return;
    }
    img.alt = slots.sky.alt;
    img.onload = () => {
      if (closed) return;
      img.hidden = false;
      shown.add("sky");
      checkSize("sky", img.naturalWidth, img.naturalHeight);
      renderCredit();
    };
    img.onerror = () => {
      // The manifest's file didn't load: today's file, then none.
      const fallback = slotUrl(DEFAULT_SLOTS.sky);
      if (url !== fallback) {
        console.warn(`Opening image: sky: ${url} didn't load; using ${fallback}.`);
        slots = { ...slots, sky: DEFAULT_SLOTS.sky };
        showSky(fallback);
      } else showSky(null);
    };
    img.src = url;
  }

  function showArt(id: "earth" | "horizon") {
    const url = slotUrl(slots[id]);
    if (!url) return; // drawn in code
    const pic = new Image();
    pic.decoding = "async";
    pic.onload = () => {
      if (closed) return;
      art[id] = pic;
      shown.add(id);
      checkSize(id, pic.naturalWidth, pic.naturalHeight);
      renderCredit();
      schedule();
    };
    pic.onerror = () => console.warn(`Opening image: ${id}: ${url} didn't load; drawn in code instead.`);
    pic.src = url;
  }

  function useSlots(next: Slots) {
    if (closed) return;
    slots = next;
    showSky(slotUrl(slots.sky));
    showArt("earth");
    showArt("horizon");
  }

  // The attribute (not the property) so it also holds where inert isn't implemented.
  const wasInert = app.hasAttribute("inert");
  app.setAttribute("inert", "");
  document.documentElement.classList.add("op-open");
  document.body.append(el);

  function draw() {
    frame = 0;
    if (closed) return;
    const p = still ? 0 : scrollProgress(el.scrollTop, el.clientHeight, el.scrollHeight);
    painted = p;
    const s = sceneAt(p);
    paintScene(canvas, p, art);
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
    // The brand steps back before the fade, so it never prints over the site's header. The credit
    // stays as long as the sky does: it fades only with the whole overlay (veil).
    brand.style.opacity = String(s.chrome);
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
  // The still card has no journey to scroll: scrolling down (wheel, swipe, keys) moves on to the site.
  let touchY: number | null = null;
  function onWheel(ev: WheelEvent) {
    if (ev.deltaY > 0) close("end");
  }
  function onTouchStart(ev: TouchEvent) {
    touchY = ev.touches[0]?.clientY ?? null;
  }
  function onTouchMove(ev: TouchEvent) {
    const y = ev.touches[0]?.clientY;
    if (touchY != null && y != null && touchY - y > 40) close("end");
  }
  // Keys scroll the story even with focus on Skip (it sits in a fixed bar, outside the scroller,
  // so the browser alone would scroll nothing). Space and Enter still press Skip.
  const KEY_STEP: Record<string, number> = { ArrowDown: 0.1, ArrowUp: -0.1, PageDown: 0.9, PageUp: -0.9 };
  function onStoryKey(ev: KeyboardEvent) {
    if (ev.altKey || ev.ctrlKey || ev.metaKey) return;
    if (still) {
      if (ev.key === "ArrowDown" || ev.key === "PageDown" || ev.key === "End") {
        ev.preventDefault();
        close("end");
      }
      return;
    }
    const max = el.scrollHeight - el.clientHeight;
    const to =
      ev.key === "Home" ? 0 : ev.key === "End" ? max : KEY_STEP[ev.key] ? el.scrollTop + KEY_STEP[ev.key] * el.clientHeight : null;
    if (to == null) return;
    ev.preventDefault();
    const top = Math.max(0, Math.min(max, to));
    if (typeof el.scrollTo === "function") el.scrollTo({ top, behavior: "smooth" });
    else el.scrollTop = top;
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
    // Skip, Escape or the end: focus lands on the page (the Sky map, or the replay button).
    // A navigation leaves focus to the page it opens.
    const back = reason === "navigate" ? null : opts.returnFocus?.();
    if (back && back.isConnected) back.focus({ preventScroll: true });
    opts.onClose?.(reason);
  }

  skip.addEventListener("click", () => close("skip"));
  el.addEventListener("scroll", schedule, { passive: true });
  addEventListener("resize", onResize, { passive: true });
  addEventListener("hashchange", onHash);
  document.addEventListener("keydown", onKey, true);
  motion?.addEventListener?.("change", onMotion);
  el.addEventListener("keydown", onStoryKey);
  if (still) {
    el.addEventListener("wheel", onWheel, { passive: true });
    el.addEventListener("touchstart", onTouchStart, { passive: true });
    el.addEventListener("touchmove", onTouchMove, { passive: true });
  }

  const given = opts.slots ?? loadManifest();
  if (given instanceof Promise) void given.then(useSlots);
  else useSlots(given);

  el.scrollTop = 0;
  draw();
  // Focus starts on Skip, so one key (Enter or Space) leaves at once; Escape works from anywhere.
  // Screen readers read the dialog's name and description with it. The arrow keys, Page Down and
  // the wheel still scroll the story (onStoryKey).
  skip.focus({ preventScroll: true });

  return { element: el, close };
}
