import { sceneAt, smooth, type SceneState } from './model';
import { drawPortrait, portraitCamera, PORTRAIT_EYE } from './portrait';

// Authored vector scenery, not astronomical imagery. The only sky pixels come from SPHEREx.
const land = [
  'M-70 -40 L-52 -68 -24 -62 -12 -41 -27 -28 -21 -10 -39 5 -48 -9 -65 -17Z',
  'M-30 4 L-12 10 -5 29 -17 63 -30 80 -34 48 -43 25Z',
  'M3 -46 L25 -64 53 -58 76 -34 56 -19 35 -28 24 -9 14 -22 -1 -19Z',
  'M6 -13 L28 -14 39 6 28 39 14 49 3 22 -4 2Z',
  'M52 39 L75 32 89 45 80 63 59 58Z',
];
function fillPath(c: CanvasRenderingContext2D, path: string, color: string) {
  c.fillStyle = color; c.fill(new Path2D(path));
}

function earth(c: CanvasRenderingContext2D, w: number, h: number, s: SceneState) {
  if (!s.earth) return;
  const d = s.descent;
  const r = Math.min(w, h) * .17 * Math.exp(d * 2.9);
  const x = w * (.74 - .24 * d), y = h * (.86 + .34 * d);
  c.save(); c.globalAlpha = s.earth;
  const glow = c.createRadialGradient(x, y, r * .94, x, y, r * 1.09);
  glow.addColorStop(0, '#7abce666'); glow.addColorStop(.52, '#61b3ee22'); glow.addColorStop(1, '#346cab00');
  c.fillStyle = glow; c.beginPath(); c.arc(x, y, r * 1.09, 0, Math.PI * 2); c.fill();
  c.save(); c.translate(x, y); c.scale(r / 100, r / 100);
  c.beginPath(); c.arc(0, 0, 100, 0, Math.PI * 2); c.clip();
  const ocean = c.createRadialGradient(-60, -65, 8, 10, 15, 150);
  ocean.addColorStop(0, '#558cac'); ocean.addColorStop(.35, '#244c65'); ocean.addColorStop(.7, '#102130'); ocean.addColorStop(1, '#03080d');
  c.fillStyle = ocean; c.fillRect(-100, -100, 200, 200);
  c.save(); c.rotate(-.25 + d * .18);
  land.forEach(path => fillPath(c, path, '#436168'));
  c.strokeStyle = '#d1e0e847'; c.lineWidth = 1.5;
  for (let i = 0; i < 6; i++) {
    c.beginPath(); c.moveTo(-88 + i * 8, -68 + i * 23);
    c.bezierCurveTo(-52, -77 + i * 23, -32, -46 + i * 19, 10 + i * 10, -60 + i * 21); c.stroke();
  }
  c.restore();
  const night = c.createLinearGradient(-70, -60, 60, 25);
  night.addColorStop(0, '#02081200'); night.addColorStop(.45, '#02081233'); night.addColorStop(1, '#02060bf5');
  c.fillStyle = night; c.fillRect(-100, -100, 200, 200);
  c.restore(); c.restore();
}

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
  if (!s.girl) return;
  const cam = portraitCamera(w, h, s);
  c.save(); c.globalAlpha = s.girl;
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
  earth(c, w, h, s); horizon(c, w, h, s); girl(c, w, h, s); clouds(c, w, h, s);
}
