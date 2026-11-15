// The opening's camera is a pure function of scroll position: no clock, no tween. Scrolling
// back plays it backwards. Ported from codex/frontend-unified (春。 with Codex).

export const clamp = (n: number) => Math.max(0, Math.min(1, Number.isFinite(n) ? n : 0));
export const segment = (p: number, from: number, to: number) => clamp((p - from) / (to - from));
/** Smoothstep of p between from and to. */
export const smooth = (p: number, from: number, to: number) => {
  const t = segment(p, from, to);
  return t * t * (3 - 2 * t);
};

/** The dive's master curve (stars.ts, planets.ts): 0 on the title, 1 when the clouds close. */
export const descentAt = (p: number) => smooth(p, 0, .3);

export function scrollProgress(scrollTop: number, viewport: number, totalHeight: number): number {
  return totalHeight > viewport ? clamp(scrollTop / (totalHeight - viewport)) : 0;
}

/**
 * Where the story is, 0 (title over the planets and the stars) to 1 (the overlay has faded
 * into the site). Two acts share this timeline:
 * - the dive (stars.ts, planets.ts): the camera flies through real Gaia stars toward Earth,
 *   the planets slide past, Earth fills the screen, the clouds part onto the ground at night;
 * - Galileo (portrait.ts): he stands on the ground with his telescope; the camera pushes in
 *   to his face, glides along the telescope to its lens, and the lens opens onto the sky.
 */
export function sceneAt(progress: number) {
  const p = clamp(progress);
  return {
    p,
    title: 1 - smooth(p, 0.015, 0.1),
    /** The camera's flight through the star field and down to Earth. */
    descent: descentAt(p),
    /** The star field stays until the clouds close over it. */
    stars: 1 - smooth(p, 0.24, 0.285),
    /** Earth fills the screen, then goes once the clouds cover it. */
    earth: 1 - smooth(p, 0.265, 0.295),
    /** Night clouds rush past, close, then part onto the ground. */
    clouds: smooth(p, 0.205, 0.255) * (1 - smooth(p, 0.33, 0.42)),
    ground: smooth(p, 0.31, 0.44) * (1 - smooth(p, 0.52, 0.62)),
    figure: smooth(p, 0.32, 0.45) * (1 - smooth(p, 0.885, 0.9)),
    /** Push in to Galileo's head and the eyepiece. */
    face: smooth(p, 0.47, 0.6),
    /** Glide along the telescope to its objective lens. */
    scope: smooth(p, 0.6, 0.72),
    /** The lens opens onto the sky and grows to fill the screen. */
    aperture: smooth(p, 0.72, 0.9),
    /** Brand and credit step back before the fade, so they never print over the site's header. */
    chrome: 1 - smooth(p, 0.86, 0.9),
    /** Scroll contribution to the fade; the overlay completes its reveal with a short dissolve. */
    veil: 1 - smooth(p, 0.92, 0.995),
  };
}
export type SceneState = ReturnType<typeof sceneAt>;

/** The four captions, by progress. */
export const CHAPTERS = [
  "From the stars around us to one small world.",
  "Padua, 1609. Galileo turns a telescope to the sky.",
  "Look closer.",
  "Four centuries later, SPHEREx maps the whole sky in 102 colours.",
] as const;

export function chapterAt(p: number): number {
  if (p <= 0.12 || p >= 0.88) return -1;
  return p < 0.27 ? 0 : p < 0.47 ? 1 : p < 0.72 ? 2 : 3;
}
