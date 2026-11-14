// The opening's scenery, drawn with canvas paths and gradients: Earth, clouds, hills and the
// silhouette. None of it is astronomical imagery; the only sky pixels are SPHEREx's, in the
// <img> behind this canvas, seen at the start and through the pupil at the end.
// Ported from codex/frontend-unified (春。 with Codex).
// Earth and the hills can come from images instead (public/opening/manifest.json); the figure
// stays in code, because the camera zooms about 250× into its pupil and a picture can't hold that.
import { sceneAt, smooth, type SceneState } from "./model";
import { drawPortrait, PORTRAIT_EYE } from "./portrait";

/** Images that replace drawn parts of the scene (see manifest.ts); missing ones are drawn. */
export interface SceneArt {
  earth?: HTMLImageElement;
  horizon?: HTMLImageElement;
}

const land = [
  "M-70 -40 L-52 -68 -24 -62 -12 -41 -27 -28 -21 -10 -39 5 -48 -9 -65 -17Z",
  "M-30 4 L-12 10 -5 29 -17 63 -30 80 -34 48 -43 25Z",
  "M3 -46 L25 -64 53 -58 76 -34 56 -19 35 -28 24 -9 14 -22 -1 -19Z",
  "M6 -13 L28 -14 39 6 28 39 14 49 3 22 -4 2Z",
  "M52 39 L75 32 89 45 80 63 59 58Z",
];

function earth(c: CanvasRenderingContext2D, w: number, h: number, s: SceneState, pic?: HTMLImageElement) {
  if (!s.earth) return;
  const d = s.descent;
  const r = Math.min(w, h) * 0.17 * Math.exp(d * 2.9);
  const x = w * (0.74 - 0.24 * d);
  const y = h * (0.86 + 0.34 * d);
  c.save();
  c.globalAlpha = s.earth;
  const glow = c.createRadialGradient(x, y, r * 0.94, x, y, r * 1.09);
  glow.addColorStop(0, "#7abce666");
  glow.addColorStop(0.52, "#61b3ee22");
  glow.addColorStop(1, "#346cab00");
  c.fillStyle = glow;
  c.beginPath();
  c.arc(x, y, r * 1.09, 0, Math.PI * 2);
  c.fill();
  c.save();
  c.translate(x, y);
  c.scale(r / 100, r / 100);
  c.beginPath();
  c.arc(0, 0, 100, 0, Math.PI * 2);
  c.clip();
  if (pic) {
    // The picture's disc fills its square; it turns as slowly as the drawn continents.
    c.rotate(-0.25 + d * 0.18);
    c.drawImage(pic, -100, -100, 200, 200);
    c.restore();
    c.restore();
    return;
  }
  const ocean = c.createRadialGradient(-60, -65, 8, 10, 15, 150);
  ocean.addColorStop(0, "#558cac");
  ocean.addColorStop(0.35, "#244c65");
  ocean.addColorStop(0.7, "#102130");
  ocean.addColorStop(1, "#03080d");
  c.fillStyle = ocean;
  c.fillRect(-100, -100, 200, 200);
  c.save();
  c.rotate(-0.25 + d * 0.18);
  c.fillStyle = "#436168";
  for (const path of land) c.fill(new Path2D(path));
  c.strokeStyle = "#d1e0e847";
  c.lineWidth = 1.5;
  for (let i = 0; i < 6; i++) {
    c.beginPath();
    c.moveTo(-88 + i * 8, -68 + i * 23);
    c.bezierCurveTo(-52, -77 + i * 23, -32, -46 + i * 19, 10 + i * 10, -60 + i * 21);
    c.stroke();
  }
  c.restore();
  const night = c.createLinearGradient(-70, -60, 60, 25);
  night.addColorStop(0, "#02081200");
  night.addColorStop(0.45, "#02081233");
  night.addColorStop(1, "#02060bf5");
  c.fillStyle = night;
  c.fillRect(-100, -100, 200, 200);
  c.restore();
  c.restore();
}

/** Where the figure's feet stand, as a fraction of the screen's height (figure() below). */
const GROUND_Y = 0.86;

function horizon(c: CanvasRenderingContext2D, w: number, h: number, s: SceneState, pic?: HTMLImageElement) {
  if (!s.ground) return;
  c.save();
  c.globalAlpha = s.ground;
  const haze = c.createLinearGradient(0, h * 0.25, 0, h);
  haze.addColorStop(0, "#172b4300");
  haze.addColorStop(0.7, "#15233999");
  haze.addColorStop(1, "#060b13");
  c.fillStyle = haze;
  c.fillRect(0, 0, w, h);
  if (pic && pic.naturalWidth && pic.naturalHeight) {
    // Covers the screen's width and is at least 28% of its height, so with its middle row (the
    // ground line) where the figure stands, it always reaches the bottom. Sides crop on phones.
    const scale = Math.max(w / pic.naturalWidth, (h * 2 * (1 - GROUND_Y)) / pic.naturalHeight);
    const dw = pic.naturalWidth * scale;
    const dh = pic.naturalHeight * scale;
    c.drawImage(pic, (w - dw) / 2, h * GROUND_Y - dh / 2, dw, dh);
    c.restore();
    return;
  }
  c.fillStyle = "#08111d";
  c.beginPath();
  c.moveTo(0, h);
  for (let i = 0; i <= 80; i++) {
    const x = i / 80;
    c.lineTo(x * w, h * (0.8 + Math.sin(x * 11) * 0.025 + Math.cos(x * 26) * 0.011));
  }
  c.lineTo(w, h);
  c.fill();
  c.fillStyle = "#03070e";
  c.beginPath();
  c.moveTo(0, h);
  for (let i = 0; i <= 80; i++) {
    const x = i / 80;
    c.lineTo(x * w, h * (0.89 + Math.sin(x * 9 + 1) * 0.025));
  }
  c.lineTo(w, h);
  c.fill();
  c.restore();
}

function figure(c: CanvasRenderingContext2D, w: number, h: number, s: SceneState) {
  if (!s.figure) return;
  const base = Math.min(w / 640, h / 980) * 0.74;
  // Phones: frame the face by the screen's width, so the eye stays on screen.
  const faceTarget = Math.min(5, w / 210);
  const faceZoom = base * Math.pow(faceTarget / base, s.face);
  const eyeZoom = faceZoom * Math.pow(18 / faceZoom, s.eye);
  const full = Math.hypot(w, h) / (PORTRAIT_EYE.radius * 1.8);
  const scale = eyeZoom * Math.pow(full / eyeZoom, s.pupil);
  // Square to the silhouette first, then towards its one visible eye.
  const lookAtEye = smooth(s.p, 0.55, 0.66);
  const centerX = 800 + (PORTRAIT_EYE.x - 800) * lookAtEye;
  const centerY = 420 + (PORTRAIT_EYE.y - 420) * lookAtEye;
  // Feet on the ground even when the screen's width limits the figure's size.
  const standingEyeY = h * GROUND_Y - (946 - 420) * base;
  const x = w * 0.5;
  const y = standingEyeY * (1 - s.face) + h * 0.5 * s.face;
  c.save();
  c.globalAlpha = s.figure;
  c.fillStyle = `rgba(7,13,23,${smooth(s.p, 0.65, 0.7)})`;
  c.fillRect(0, 0, w, h);
  c.translate(x, y);
  c.scale(scale, scale);
  c.translate(-centerX, -centerY);
  drawPortrait(c);
  // Cut the pupil out of everything drawn so far: the SPHEREx sky shows through it.
  c.globalCompositeOperation = "destination-out";
  c.globalAlpha = 1;
  c.beginPath();
  c.arc(PORTRAIT_EYE.x, PORTRAIT_EYE.y, PORTRAIT_EYE.radius, 0, Math.PI * 2);
  c.fill();
  c.globalCompositeOperation = "source-over";
  c.fillStyle = `rgba(3,8,15,${0.88 * (1 - smooth(s.p, 0.7, 0.89))})`;
  c.fill();
  c.restore();
}

function clouds(c: CanvasRenderingContext2D, w: number, h: number, s: SceneState) {
  if (!s.clouds) return;
  c.save();
  c.globalAlpha = s.clouds;
  const through = smooth(s.p, 0.25, 0.43);
  for (let i = 0; i < 9; i++) {
    const x = w * (i / 8 + Math.sin(i * 4) * 0.09);
    const y = h * (0.3 + Math.sin(i * 7) * 0.3 + through * 0.8);
    const radius = Math.max(w, h) * (0.28 + through * 0.2);
    const mist = c.createRadialGradient(x, y, 0, x, y, radius);
    mist.addColorStop(0, "#aabdcbb3");
    mist.addColorStop(0.3, "#8193a28c");
    mist.addColorStop(1, "#6a829b00");
    c.fillStyle = mist;
    c.fillRect(0, 0, w, h);
  }
  c.restore();
}

/** Paint the scene at this progress into a full-window canvas. */
export function paintScene(canvas: HTMLCanvasElement, progress: number, art: SceneArt = {}): void {
  const w = window.innerWidth;
  const h = window.innerHeight;
  const dpr = Math.min(window.devicePixelRatio || 1, 1.75);
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }
  const c = canvas.getContext("2d");
  if (!c) return;
  c.setTransform(dpr, 0, 0, dpr, 0, 0);
  c.clearRect(0, 0, w, h);
  const s = sceneAt(progress);
  earth(c, w, h, s, art.earth);
  horizon(c, w, h, s, art.horizon);
  figure(c, w, h, s);
  clouds(c, w, h, s);
}
