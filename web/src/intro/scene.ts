import { sceneAt, type SceneState } from './model';
import { apertureSteps, applyCamera, drawPortrait, portraitCamera } from './portrait';
import { objectiveLens } from '../opening/galileo';
import { paintPlanetaryOpening } from './planets';
import { paintStars } from './stars';
import { paintClouds, warmClouds } from './clouds';

/**
 * The ground at night: the Euganean hills far off, a low skyline of Padua in silhouette, and the
 * ground he stands on. Dark and unlit (no windows). The layers sink a little as the camera rises
 * to his face, the near ones more than the far ones.
 */
function horizon(c: CanvasRenderingContext2D, w: number, h: number, s: SceneState) {
  if (!s.ground) return;
  c.save(); c.globalAlpha = s.ground;
  const haze = c.createLinearGradient(0, h * .25, 0, h);
  haze.addColorStop(0, '#172b4300'); haze.addColorStop(.7, '#15233999'); haze.addColorStop(1, '#060b13');
  c.fillStyle = haze; c.fillRect(0, 0, w, h);
  const sink = (k: number) => h * k * s.face;
  c.fillStyle = '#08111d'; c.beginPath(); c.moveTo(0, h);
  for (let i = 0; i <= 80; i++) {
    const x = i / 80;
    c.lineTo(x * w, sink(.08) + h * (.80 + Math.sin(x * 11) * .025 + Math.cos(x * 26) * .011));
  }
  c.lineTo(w, h); c.fill();
  c.fillStyle = '#060c17'; c.fill(paduaSkyline(w, h, h * .845 + sink(.13)));
  c.fillStyle = '#03070e'; c.beginPath(); c.moveTo(0, h);
  for (let i = 0; i <= 80; i++) {
    const x = i / 80;
    c.lineTo(x * w, sink(.22) + h * (.89 + Math.sin(x * 9 + 1) * .025));
  }
  c.lineTo(w, h); c.fill();
  c.restore();
}

/** Roofs, a campanile, the hull roof of the Palazzo della Ragione, the domes and slender towers
 *  of the Basilica of Saint Anthony, a crenellated tower: one path from the left edge to the right. */
function paduaSkyline(w: number, h: number, base: number): Path2D {
  const m = Math.min(w, h), path = new Path2D();
  const X = (f: number) => f * w;
  path.moveTo(0, h); path.lineTo(0, base - m * .012);
  const flat = (x0: number, x1: number, top: number) => { path.lineTo(x0, base - top); path.lineTo(x1, base - top); };
  const gable = (x0: number, x1: number, wall: number, ridge: number) => {
    path.lineTo(x0, base - wall); path.lineTo((x0 + x1) / 2, base - wall - ridge); path.lineTo(x1, base - wall);
  };
  const tower = (x: number, width: number, height: number, spire: number) => {
    path.lineTo(x - width / 2, base - m * .02); path.lineTo(x - width / 2, base - height);
    path.lineTo(x - width * .62, base - height); path.lineTo(x - width * .62, base - height - m * .006);
    path.lineTo(x, base - height - m * .006 - spire);
    path.lineTo(x + width * .62, base - height - m * .006); path.lineTo(x + width * .62, base - height);
    path.lineTo(x + width / 2, base - height); path.lineTo(x + width / 2, base - m * .02);
  };
  const dome = (x: number, r: number, drum: number, roof: number) => {
    path.lineTo(x - r * 1.05, base - roof); path.lineTo(x - r * 1.05, base - roof - drum);
    path.arc(x, base - roof - drum, r, Math.PI, 0); path.lineTo(x + r * 1.05, base - roof);
  };
  // West: houses, the campanile, the Palazzo della Ragione.
  gable(X(.01), X(.05), m * .016, m * .01); flat(X(.05), X(.09), m * .02); gable(X(.09), X(.14), m * .014, m * .012);
  tower(X(.165), m * .016, m * .085, m * .03);
  gable(X(.18), X(.22), m * .018, m * .009);
  path.lineTo(X(.23), base - m * .024);
  path.bezierCurveTo(X(.25), base - m * .06, X(.33), base - m * .06, X(.35), base - m * .024);
  flat(X(.35), X(.39), m * .017); gable(X(.39), X(.44), m * .014, m * .009);
  flat(X(.44), X(.58), m * .012);
  // East: the Basilica of Saint Anthony, then houses and a crenellated tower.
  gable(X(.58), X(.62), m * .015, m * .01);
  const santo = X(.72), body = m * .03;
  path.lineTo(santo - m * .1, base - body);
  tower(santo - m * .085, m * .009, m * .068, m * .018);
  dome(santo - m * .052, m * .011, m * .008, body); dome(santo - m * .024, m * .012, m * .009, body);
  path.lineTo(santo - m * .011, base - body - m * .012); path.lineTo(santo, base - body - m * .045);
  path.lineTo(santo + m * .011, base - body - m * .012);
  dome(santo + m * .024, m * .012, m * .009, body); dome(santo + m * .052, m * .011, m * .008, body);
  tower(santo + m * .085, m * .009, m * .068, m * .018);
  path.lineTo(santo + m * .1, base - body);
  gable(X(.8), X(.84), m * .016, m * .01); flat(X(.84), X(.88), m * .02);
  path.lineTo(X(.885), base - m * .02); path.lineTo(X(.885), base - m * .07);
  for (let i = 0; i < 4; i++) {
    const x0 = X(.885) + i * m * .005;
    path.lineTo(x0, base - m * .076); path.lineTo(x0 + m * .0025, base - m * .076); path.lineTo(x0 + m * .0025, base - m * .07);
  }
  path.lineTo(X(.885) + m * .02, base - m * .07); path.lineTo(X(.885) + m * .02, base - m * .02);
  gable(X(.91), X(.96), m * .015, m * .011); flat(X(.96), w, m * .018);
  path.lineTo(w, h); path.closePath();
  return path;
}

/** Galileo and his telescope; at the end the objective lens is cut out of the canvas, so the
 *  window shows the real SPHEREx sky behind it. Nothing is painted inside the window. */
function galileo(c: CanvasRenderingContext2D, w: number, h: number, s: SceneState) {
  if (!s.figure) return;
  const cam = portraitCamera(w, h, s);
  const { dark, open } = apertureSteps(s);
  c.save(); c.globalAlpha = s.figure;
  // Night closes in round the lens as it turns to face us.
  if (dark > 0) { c.fillStyle = `rgba(5,9,17,${dark})`; c.fillRect(0, 0, w, h); }
  applyCamera(c, cam);
  drawPortrait(c, s, cam);
  if (open > 0) {
    c.globalCompositeOperation = 'destination-out'; c.globalAlpha = open;
    c.fill(objectiveLens(cam.turn));
  }
  c.restore();
}

function clouds(c: CanvasRenderingContext2D, w: number, h: number, s: SceneState) {
  if (s.clouds) paintClouds(c, w, h, s);
  else if (s.p < .2) warmClouds();
}

export function paintScene(canvas: HTMLCanvasElement, progress: number): void {
  const w = window.innerWidth, h = window.innerHeight;
  const dpr = Math.min(window.devicePixelRatio || 1, 1.75);
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
  }
  const c = canvas.getContext('2d');
  if (!c) return;
  c.setTransform(dpr, 0, 0, dpr, 0, 0); c.clearRect(0, 0, w, h);
  const s = sceneAt(progress);
  // Real Gaia stars first (their own canvas in the sky, under everything here), then the planets.
  paintStars(w, h, dpr, s);
  paintPlanetaryOpening(c, w, h, s); horizon(c, w, h, s); galileo(c, w, h, s); clouds(c, w, h, s);
}
