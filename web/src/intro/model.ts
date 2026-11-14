/** Camera state is a pure function of scroll: no clock-driven tween or smoothing. */
export const clamp = (n: number) => Math.max(0, Math.min(1, Number.isFinite(n) ? n : 0));
export const segment = (p: number, from: number, to: number) => clamp((p - from) / (to - from));
export const smooth = (p: number, from: number, to: number) => {
  const t = segment(p, from, to);
  return t * t * (3 - 2 * t);
};
export function scrollProgress(scrollTop: number, viewport: number, totalHeight: number): number {
  return totalHeight > viewport ? clamp(scrollTop / (totalHeight - viewport)) : 0;
}
export function sceneAt(progress: number) {
  const p = clamp(progress);
  return {
    p,
    title: 1 - smooth(p, 0.015, 0.12),
    earth: 1 - smooth(p, 0.25, 0.35),
    descent: smooth(p, 0, 0.28),
    ground: smooth(p, 0.29, 0.42) * (1 - smooth(p, 0.50, 0.60)),
    girl: smooth(p, 0.30, 0.43) * (1 - smooth(p, 0.885, 0.90)),
    face: smooth(p, 0.45, 0.61),
    eye: smooth(p, 0.61, 0.70),
    pupil: smooth(p, 0.70, 0.90),
    clouds: smooth(p, 0.21, 0.29) * (1 - smooth(p, 0.32, 0.43)),
    panels: [smooth(p, 0.90, 0.925), smooth(p, 0.925, 0.95), smooth(p, 0.95, 0.975), smooth(p, 0.975, 1)],
  };
}
export type SceneState = ReturnType<typeof sceneAt>;

export const SEEN_KEY = 'skyshift:intro:v1:seen';
export interface IntroStorage { getItem(key: string): string | null; setItem(key: string, value: string): void }
export function storage(): IntroStorage | undefined {
  try { return window.localStorage; } catch { return undefined; }
}
export function hasSeen(store = storage()): boolean {
  try { return store?.getItem(SEEN_KEY) === '1'; } catch { return false; }
}
export function rememberIntro(store = storage()): void {
  try { store?.setItem(SEEN_KEY, '1'); } catch { /* Private browsing may deny storage. */ }
}
export function reducedMotion(): boolean {
  return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
}
