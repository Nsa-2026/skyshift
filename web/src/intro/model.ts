// The opening's camera is a pure function of scroll position: no clock, no tween. Scrolling
// back plays it backwards. Ported from codex/frontend-unified (春。 with Codex).

export const clamp = (n: number) => Math.max(0, Math.min(1, Number.isFinite(n) ? n : 0));
export const segment = (p: number, from: number, to: number) => clamp((p - from) / (to - from));
/** Smoothstep of p between from and to. */
export const smooth = (p: number, from: number, to: number) => {
  const t = segment(p, from, to);
  return t * t * (3 - 2 * t);
};

export function scrollProgress(scrollTop: number, viewport: number, totalHeight: number): number {
  return totalHeight > viewport ? clamp(scrollTop / (totalHeight - viewport)) : 0;
}

/** Where the story is, 0 (title over the sky) to 1 (the overlay has faded into the site). */
export function sceneAt(progress: number) {
  const p = clamp(progress);
  return {
    p,
    title: 1 - smooth(p, 0.015, 0.12),
    earth: 1 - smooth(p, 0.25, 0.35),
    descent: smooth(p, 0, 0.28),
    ground: smooth(p, 0.29, 0.42) * (1 - smooth(p, 0.5, 0.6)),
    figure: smooth(p, 0.3, 0.43) * (1 - smooth(p, 0.885, 0.9)),
    face: smooth(p, 0.45, 0.61),
    eye: smooth(p, 0.61, 0.7),
    pupil: smooth(p, 0.7, 0.9),
    clouds: smooth(p, 0.21, 0.29) * (1 - smooth(p, 0.32, 0.43)),
    /** Brand and credit step back before the fade, so they never print over the site's header. */
    chrome: 1 - smooth(p, 0.86, 0.9),
    /** Scroll contribution to the fade; the overlay completes its reveal with a short dissolve. */
    veil: 1 - smooth(p, 0.92, 0.995),
  };
}
export type SceneState = ReturnType<typeof sceneAt>;

/** The four captions, by progress, in the author's English (1390d01). */
export const CHAPTERS = [
  "From the cosmos to a single gaze.",
  "Every discovery begins with a look.",
  "Look closer.",
  "A universe of discoveries, in your eyes.",
] as const;

export function chapterAt(p: number): number {
  if (p <= 0.12 || p >= 0.88) return -1;
  return p < 0.25 ? 0 : p < 0.45 ? 1 : p < 0.7 ? 2 : 3;
}
