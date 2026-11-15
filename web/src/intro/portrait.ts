import {
  drawGoddess, GODDESS_HEAD, GODDESS_HEIGHT, GODDESS_EYE_WIDTH,
  GODDESS_PUPIL, GODDESS_PUPIL_RADIUS,
} from '../opening/goddess';
import type { SceneState } from './model';

/** Use the supplied goddess model and its centimetre coordinates throughout the shot. */
// Open the inner pupil so the sky stays inside the painted eyelids during the close-up.
export const PORTRAIT_EYE = { ...GODDESS_PUPIL, radius: GODDESS_PUPIL_RADIUS * .6 };

export function portraitCamera(w: number, h: number, s: SceneState) {
  const wide = Math.min(h * .38 / GODDESS_HEIGHT, w * .46 / 75);
  const faceTarget = Math.min(h * .62, w * .70) / 46;
  const eyeTarget = Math.min(w, h) * .6 / GODDESS_EYE_WIDTH;
  const faceScale = wide * Math.pow(faceTarget / wide, s.face);
  const eyeScale = faceScale * Math.pow(eyeTarget / faceScale, s.scope);
  const full = Math.hypot(w, h) / (PORTRAIT_EYE.radius * 1.8);
  const scale = eyeScale * Math.pow(full / eyeScale, s.aperture);
  const focusX = GODDESS_HEAD.x + (PORTRAIT_EYE.x - GODDESS_HEAD.x) * s.scope;
  const focusY = GODDESS_HEAD.y + (PORTRAIT_EYE.y - GODDESS_HEAD.y) * s.scope;
  // Figure feet are at y=0; anchor them to the ground before the camera starts moving.
  const standingHeadY = h * .86 + GODDESS_HEAD.y * wide;
  return {
    scale, focusX, focusY, x: w / 2,
    y: standingHeadY * (1 - s.face) + h * .5 * s.face,
  };
}

export function drawPortrait(c: CanvasRenderingContext2D, s: SceneState, pxPerCm: number): void {
  // Ribbons and wig follow scroll too, so rewinding produces the same pose.
  drawGoddess(c, s.p * 12, pxPerCm, .6 + .4 * s.face, 'skin');
}
