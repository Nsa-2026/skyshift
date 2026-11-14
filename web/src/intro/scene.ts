import { sceneAt, smooth, type SceneState } from './model';
import { drawPortrait, portraitCamera, PORTRAIT_EYE } from './portrait';
import { paintPlanetaryOpening } from './planets';

function horizon(c: CanvasRenderingContext2D, w: number, h: number, s: SceneState) {
  if (!s.ground) return;
  c.save(); c.globalAlpha = s.ground;
  const haze = c.createLinearGradient(0, h * .25, 0, h);
  haze.addColorStop(0, '#172b4300'); haze.addColorStop(.7, '#15233999'); haze.addColorStop(1, '#060b13');
  c.fillStyle = haze; c.fillRect(0, 0, w, h);
  c.fillStyle = '#08111d'; c.beginPath(); c.moveTo(0, h);
  for (let i = 0; i <= 80; i++) {
    const x = i / 80;
    c.lineTo(x * w, h * (.80 + Math.sin(x * 11) * .025 + Math.cos(x * 26) * .011));
  }
  c.lineTo(w, h); c.fill();
  c.fillStyle = '#03070e'; c.beginPath(); c.moveTo(0, h);
  for (let i = 0; i <= 80; i++) {
    const x = i / 80;
    c.lineTo(x * w, h * (.89 + Math.sin(x * 9 + 1) * .025));
  }
  c.lineTo(w, h); c.fill();
  c.restore();
}

function girl(c: CanvasRenderingContext2D, w: number, h: number, s: SceneState) {
  if (!s.figure) return;
  const cam = portraitCamera(w, h, s);
  c.save(); c.globalAlpha = s.figure;
  c.fillStyle = `rgba(7,13,23,${smooth(s.p, .65, .70)})`; c.fillRect(0, 0, w, h);
  c.translate(cam.x, cam.y); c.scale(cam.scale, cam.scale); c.translate(-cam.focusX, -cam.focusY);
  drawPortrait(c, s, cam.scale);
  c.globalCompositeOperation = 'destination-out'; c.globalAlpha = smooth(s.p, .64, .72);
  c.beginPath(); c.arc(PORTRAIT_EYE.x, PORTRAIT_EYE.y, PORTRAIT_EYE.radius, 0, Math.PI * 2); c.fill();
  c.globalCompositeOperation = 'source-over';
  c.fillStyle = `rgba(3,8,15,${.88 * (1 - smooth(s.p, .70, .89))})`; c.fill();
  c.restore();
}

function clouds(c: CanvasRenderingContext2D, w: number, h: number, s: SceneState) {
  if (!s.clouds) return;
  c.save(); c.globalAlpha = s.clouds;
  const through = smooth(s.p, .25, .43);
  for (let i = 0; i < 9; i++) {
    const x = w * (i / 8 + Math.sin(i * 4) * .09);
    const y = h * (.3 + Math.sin(i * 7) * .3 + through * .8);
    const radius = Math.max(w, h) * (.28 + through * .2);
    const mist = c.createRadialGradient(x, y, 0, x, y, radius);
    mist.addColorStop(0, '#aabdcbb3'); mist.addColorStop(.3, '#8193a28c'); mist.addColorStop(1, '#6a829b00');
    c.fillStyle = mist; c.fillRect(0, 0, w, h);
  }
  c.restore();
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
  paintPlanetaryOpening(c, w, h, s); horizon(c, w, h, s); girl(c, w, h, s); clouds(c, w, h, s);
}
