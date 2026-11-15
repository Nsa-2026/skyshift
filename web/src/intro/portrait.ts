import {
  drawGalileo, objectiveAspect, GALILEO_HAND, GALILEO_HEAD, GALILEO_OBJECTIVE, OBJECTIVE_CELL,
  OBJECTIVE_RADIUS, SCOPE_ELEVATION,
} from '../opening/galileo';
import { smooth, type SceneState } from './model';

type Pt = { x: number; y: number };

/**
 * The second act's camera, a pure function of scroll (no clock, no tween): the same progress
 * always gives the same view, so scrubbing back plays it backwards exactly.
 * - wide: Galileo stands on the ground line with his telescope;
 * - face (s.face): push in to his head, the eyepiece and the hand on the tube;
 * - scope (s.scope): glide along the tube to the objective, rolling until the tube is nearly level;
 * - aperture (s.aperture): swing round to face the lens; the glass opens onto the sky (the overlay's
 *   real SPHEREx image behind the canvas) and the window grows past the corners of the screen.
 */
export interface PortraitCamera {
  /** Screen point the focus lands on (CSS px). */
  x: number;
  y: number;
  /** Screen px per figure centimetre. */
  scale: number;
  /** Figure-space point at the centre of attention. */
  focusX: number;
  focusY: number;
  /** Camera roll (radians, clockwise on screen). */
  roll: number;
  /** 0 = side view of the telescope, 1 = looking straight into its objective. */
  turn: number;
}

/** Figure-space box of the wide shot: him, his gown's hem and the whole telescope. */
const BOX = { left: -27, right: GALILEO_OBJECTIVE.x + 3, top: GALILEO_OBJECTIVE.y - 3, bottom: 0 };
const WIDE_FOCUS: Pt = { x: (BOX.left + BOX.right) / 2, y: -110 };
/** Face shot: between his head and the hand on the tube, a little toward the tube. */
const FACE_FOCUS: Pt = { x: GALILEO_HEAD.x + (GALILEO_HAND.x - GALILEO_HEAD.x) * .45, y: GALILEO_HEAD.y + (GALILEO_HAND.y - GALILEO_HEAD.y) * .45 };
/** At the end of the glide the tube lies this far from level (it rises 35°; the camera rolls 28°). */
const ROLL = SCOPE_ELEVATION * .8;

/** The aperture phase in steps: swing round, the glass opens, the window grows. */
export function apertureSteps(s: SceneState) {
  const a = s.aperture;
  return {
    turn: smooth(a, 0, .42),
    /** The night closes in round the lens while it turns. */
    dark: smooth(a, 0, .36),
    /** The glass becomes the window: destination-out from 0 to 1. */
    open: smooth(a, .3, .66),
    zoom: smooth(a, .18, 1),
    /** Galileo himself is far off screen by now; only the telescope is drawn in perspective. */
    body: 1 - smooth(a, 0, .1),
  };
}

export function portraitCamera(w: number, h: number, s: SceneState): PortraitCamera {
  const steps = apertureSteps(s);
  const wide = Math.min(h * .44 / (BOX.bottom - BOX.top), w * .6 / (BOX.right - BOX.left));
  const faceScale = Math.min(h * .64, w * .74) / 54;
  // End of the glide: the objective's cell about a third of the short side across.
  const scopeScale = Math.min(w, h) * .3 / (OBJECTIVE_CELL * 2);
  // End of the aperture: the glass reaches well past the corners.
  const full = Math.hypot(w, h) / (OBJECTIVE_RADIUS * 1.8);
  const scale = wide * Math.pow(faceScale / wide, s.face) * Math.pow(scopeScale / faceScale, s.scope) * Math.pow(full / scopeScale, steps.zoom);
  const focusX = WIDE_FOCUS.x + (FACE_FOCUS.x - WIDE_FOCUS.x) * s.face + (GALILEO_OBJECTIVE.x - FACE_FOCUS.x) * s.scope;
  const focusY = WIDE_FOCUS.y + (FACE_FOCUS.y - WIDE_FOCUS.y) * s.face + (GALILEO_OBJECTIVE.y - FACE_FOCUS.y) * s.scope;
  // His feet (y = 0) stand on the ground line before the camera moves.
  const groundedY = h * .86 + WIDE_FOCUS.y * wide;
  return {
    x: w / 2,
    y: groundedY + (h / 2 - groundedY) * s.face,
    scale, focusX, focusY,
    roll: ROLL * s.scope,
    turn: steps.turn,
  };
}

/** Where a figure-space point lands on screen. */
export function toScreen(cam: PortraitCamera, p: Pt): Pt {
  const dx = (p.x - cam.focusX) * cam.scale, dy = (p.y - cam.focusY) * cam.scale;
  const c = Math.cos(cam.roll), s = Math.sin(cam.roll);
  return { x: cam.x + dx * c - dy * s, y: cam.y + dx * s + dy * c };
}

export function applyCamera(c: CanvasRenderingContext2D, cam: PortraitCamera): void {
  c.translate(cam.x, cam.y); c.rotate(cam.roll); c.scale(cam.scale, cam.scale); c.translate(-cam.focusX, -cam.focusY);
}

/** The sky window in screen space: an ellipse while the lens turns, a circle once it faces us. */
export function lensWindow(w: number, h: number, s: SceneState) {
  const cam = portraitCamera(w, h, s);
  const centre = toScreen(cam, GALILEO_OBJECTIVE);
  return {
    ...centre,
    rx: OBJECTIVE_RADIUS * objectiveAspect(cam.turn) * cam.scale,
    ry: OBJECTIVE_RADIUS * cam.scale,
    open: apertureSteps(s).open,
  };
}

/** Draw him in figure space (call inside applyCamera). */
export function drawPortrait(c: CanvasRenderingContext2D, s: SceneState, cam: PortraitCamera): void {
  const steps = apertureSteps(s);
  drawGalileo(c, {
    pxPerCm: cam.scale,
    // A moonlit near-silhouette in the wide shot; colours lift as the camera closes in.
    light: s.face,
    turn: cam.turn,
    body: steps.body,
    open: steps.open,
  });
}
