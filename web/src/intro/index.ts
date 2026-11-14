import { paintScene } from './scene';
import { sceneAt, scrollProgress, smooth } from './model';

export function mountIntro(root: HTMLElement, opts: {
  progress(p: number): void;
  complete(manual: boolean): void;
}) {
  const el = document.createElement('section');
  el.className = 'entry-intro'; el.hidden = true; el.tabIndex = 0;
  el.setAttribute('aria-label', 'SkyShift opening. Scroll down to explore, or skip the intro.');
  el.innerHTML = `<div class="entry-scroll-space" aria-hidden="true"></div>
    <canvas class="entry-scene" aria-hidden="true"></canvas>
    <header class="entry-intro-nav"><a class="entry-intro-brand" href="#/">SkyShift<span>THE UNIVERSE, IN MOTION</span></a><button class="entry-skip" type="button">Skip intro <span aria-hidden="true">↗</span></button></header>
    <div class="entry-hero"><p class="entry-kicker"><i></i> A journey through the infrared sky</p><h1>The sky is<br>never <em>still.</em></h1><p class="entry-hero-copy">Every glance is a new beginning.</p><div class="entry-scroll-hint"><span>Scroll to explore</span><span aria-hidden="true">↓</span></div></div>
    <div class="entry-chapter" aria-hidden="true"><span class="entry-chapter-no"></span><p></p></div>
    <div class="entry-progress" aria-hidden="true"><span></span></div>
    <p class="entry-art-credit">SPHEREx observations · Earth, planets and figure drawn in code</p>`;
  root.append(el);
  const canvas = el.querySelector<HTMLCanvasElement>('canvas')!;
  const hero = el.querySelector<HTMLElement>('.entry-hero')!;
  const chapter = el.querySelector<HTMLElement>('.entry-chapter')!;
  const fill = el.querySelector<HTMLElement>('.entry-progress span')!;
  let active = false, frame = 0, done = false, painted = -1;
  function draw() {
    frame = 0;
    if (!active || done) return;
    const p = scrollProgress(el.scrollTop, el.clientHeight, el.scrollHeight);
    painted = p;
    const state = sceneAt(p);
    paintScene(canvas, p);
    hero.style.opacity = String(state.title);
    hero.style.transform = `translateY(${-p * 150}px)`;
    hero.hidden = p > .15;
    fill.style.transform = `scaleX(${p})`;
    el.querySelector<HTMLElement>('.entry-intro-brand')!.style.opacity = String(1 - smooth(p, .88, .91));
    const text = p < .25 ? ['01 / 04', 'From the cosmos to a single gaze.'] : p < .45 ? ['02 / 04', 'Every discovery begins with a look.'] : p < .70 ? ['03 / 04', 'Look closer. Then closer still.'] : ['04 / 04', 'A universe of discoveries, in your eyes.'];
    chapter.querySelector('span')!.textContent = text[0]; chapter.querySelector('p')!.textContent = text[1];
    chapter.style.opacity = p > .12 && p < .88 ? '1' : '0';
    el.querySelector<HTMLElement>('.entry-art-credit')!.style.opacity = p < .9 ? '.65' : '0';
    opts.progress(p);
    if (p >= .9999) { done = true; opts.complete(false); }
  }
  function schedule() { if (active && !frame) frame = requestAnimationFrame(draw); }
  function resize() {
    // Browser chrome / orientation changes must not advance or finish the story.
    if (active && painted >= 0) el.scrollTop = painted * Math.max(0, el.scrollHeight - el.clientHeight);
    schedule();
  }
  el.addEventListener('scroll', schedule, { passive: true });
  window.addEventListener('resize', resize, { passive: true });
  const media = window.matchMedia?.('(prefers-reduced-motion: reduce)');
  const motionChanged = () => { if (active && media?.matches) { done = true; opts.complete(false); } };
  media?.addEventListener('change', motionChanged);
  el.querySelector<HTMLButtonElement>('.entry-skip')!.onclick = () => {
    if (!active || done) return;
    done = true; opts.complete(true);
  };
  return {
    active(value: boolean) {
      if (active === value) return;
      active = value; el.hidden = !value;
      document.documentElement.classList.toggle('entry-intro-open', value);
      if (value) {
        done = false; el.scrollTop = 0; painted = -1;
        el.focus({ preventScroll: true }); draw();
      } else { cancelAnimationFrame(frame); frame = 0; }
    },
    get progress() { return painted; },
    destroy() {
      active = false; cancelAnimationFrame(frame); window.removeEventListener('resize', resize);
      media?.removeEventListener('change', motionChanged); el.remove();
      document.documentElement.classList.remove('entry-intro-open');
    },
  };
}
