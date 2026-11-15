// Night clouds at the end of the dive (illustration, drawn in code): wisps rush past the camera,
// the mist closes over the screen while Earth hands over to the ground, then it parts from the
// middle onto the ground. Textures are made once from deterministic noise, at low resolution
// (mist is soft), and every frame is a pure function of progress.
import { smooth, type SceneState } from './model';
import { clamp01, fbm } from './spheres';
import { diveTarget } from './planets';

type Shape = (u: number, v: number) => number;

/** Moonlit mist: soft noise, lighter where it faces up, transparent where it thins out. */
function mistTexture(size: number, seed: number, shape: Shape, aspect = 1): HTMLCanvasElement {
  const width = size, height = Math.round(size / aspect);
  const canvas = document.createElement('canvas'); canvas.width = width; canvas.height = height;
  const c = canvas.getContext('2d')!, img = c.createImageData(width, height), d = img.data;
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const u = x / width, v = y / height, i = (y * width + x) * 4;
    const warp = fbm(u * 2.2 + seed, v * 2.2 / aspect, seed + 3);
    const body = fbm(u * 4.5 + warp * 1.6, v * 4.5 / aspect + warp, seed);
    const fine = fbm(u * 13 + warp * 2, v * 13 / aspect, seed + 8);
    const density = body * .75 + fine * .35 + shape(u, v);
    const alpha = clamp01((density - .42) * 2.6);
    const lift = fbm(u * 4.5 + warp * 1.6, (v - .03) * 4.5 / aspect + warp, seed) - body; // facing up: lit
    const light = clamp01(.3 + fine * .35 + lift * 6 + (1 - v) * .12);
    d[i] = 18 + 92 * light; d[i + 1] = 28 + 104 * light; d[i + 2] = 46 + 122 * light;
    d[i + 3] = Math.round(alpha * 235);
  }
  c.putImageData(img, 0, 0);
  return canvas;
}

let textures: { mist: HTMLCanvasElement; wisp: HTMLCanvasElement } | null = null;
function cloudTextures() {
  return textures ??= {
    mist: mistTexture(192, 4, () => .06),
    wisp: mistTexture(160, 11, (u, v) => .25 - .75 * Math.hypot((u - .5) / .5, (v - .5) / .5) ** 2, 1.7),
  };
}
/** Make the textures in idle time ahead of the clouds, so no scrolled frame waits for them. */
let warming = false;
export function warmClouds(): void {
  if (warming || textures) return;
  warming = true;
  const idle = (globalThis as { requestIdleCallback?: (fn: () => void, o?: { timeout: number }) => number }).requestIdleCallback;
  if (idle) idle(() => cloudTextures(), { timeout: 3000 }); else setTimeout(() => cloudTextures(), 500);
}

/** Wisps around the camera's path: direction, distance from it (screen units) and depth. */
const WISPS = Array.from({ length: 14 }, (_, i) => ({
  angle: i * 2.39996 + .4, reach: .22 + (i * .37 % 1) * .5, depth: .3 + i * .055, size: .7 + (i * .61 % 1) * .5,
}));

// The mist is drawn at half resolution in its own layer, so it can part without erasing the scene.
let layer: HTMLCanvasElement | null = null;

export function paintClouds(c: CanvasRenderingContext2D, w: number, h: number, s: SceneState): void {
  if (!s.clouds) return;
  const { mist, wisp } = cloudTextures();
  const lw = Math.ceil(w / 2), lh = Math.ceil(h / 2);
  layer ??= document.createElement('canvas');
  if (layer.width !== lw || layer.height !== lh) { layer.width = lw; layer.height = lh; }
  const l = layer.getContext('2d')!;
  l.setTransform(1, 0, 0, 1, 0, 0); l.globalCompositeOperation = 'source-over'; l.globalAlpha = 1;
  l.clearRect(0, 0, lw, lh);
  l.setTransform(.5, 0, 0, .5, 0, 0);
  l.imageSmoothingEnabled = true;

  const m = Math.hypot(w, h);
  // The flight heads where the dive did, then drifts to the middle, where the ground will be.
  const [tx, ty] = diveTarget(w, h), mid = smooth(s.p, .2, .29);
  const cx = tx + (w * .5 - tx) * mid, cy = ty + (h * .55 - ty) * mid;

  // Wisps stream out from ahead, growing as they pass.
  const travel = smooth(s.p, .195, .3) * 1.12;
  for (const p of WISPS) {
    if (travel >= p.depth * .985) continue;
    const f = p.depth / (p.depth - travel), a = smooth(f, 1.03, 1.5) * (1 - smooth(f, 4, 10));
    if (a <= 0) continue;
    const width = m * .5 * p.size * f, height = width / 1.7;
    const x = cx + Math.cos(p.angle) * p.reach * m * .5 * f, y = cy + Math.sin(p.angle) * p.reach * m * .4 * f;
    if (x + width / 2 < 0 || x - width / 2 > w || y + height / 2 < 0 || y - height / 2 > h) continue;
    l.globalAlpha = a;
    l.drawImage(wisp, x - width / 2, y - height / 2, width, height);
  }

  // Inside: a solid night fog, and mist in three depths that keep growing past the camera.
  const inside = smooth(s.p, .228, .262);
  if (inside > 0) {
    l.globalAlpha = inside * (1 - smooth(s.p, .3, .345));
    const fog = l.createLinearGradient(0, 0, 0, h);
    fog.addColorStop(0, '#2a3a52'); fog.addColorStop(.55, '#1a2740'); fog.addColorStop(1, '#0f1829');
    l.fillStyle = fog; l.fillRect(0, 0, w, h);
    const flow = smooth(s.p, .22, .42) * 2.2;
    for (let k = 0; k < 3; k++) {
      const phase = (flow + k / 3) % 1, grow = Math.pow(2.6, phase);
      const size = m * 1.15 * grow;
      l.globalAlpha = inside * Math.sin(Math.PI * phase) * .95;
      l.save(); l.translate(cx, cy); l.rotate(k * 2.1 + phase * .2); if (k === 1) l.scale(-1, 1);
      l.drawImage(mist, -size / 2, -size / 2, size, size);
      l.restore();
    }
  }

  // The clouds part from the middle: a widening opening onto the ground, its edge torn by wisps.
  const open = smooth(s.p, .285, .4);
  if (open > 0) {
    const r = m * (.06 + open * .9);
    l.globalCompositeOperation = 'destination-out'; l.globalAlpha = 1;
    const hole = l.createRadialGradient(cx, cy, 0, cx, cy, r);
    hole.addColorStop(0, 'rgba(0,0,0,1)'); hole.addColorStop(.35, 'rgba(0,0,0,.9)'); hole.addColorStop(1, 'rgba(0,0,0,0)');
    l.save(); l.translate(cx, cy); l.scale(1.4, 1); l.translate(-cx, -cy);
    l.fillStyle = hole; l.fillRect(0, 0, w, h);
    l.restore();
    l.globalAlpha = .8;
    for (let i = 0; i < 9; i++) {
      const a = i * 2.39996 + open * 1.3, size = r * (.7 + (i % 3) * .2);
      const x = cx + Math.cos(a) * r * .95, y = cy + Math.sin(a) * r * .68;
      l.drawImage(wisp, x - size / 2, y - size / 3.4, size, size / 1.7);
    }
    l.globalCompositeOperation = 'source-over';
  }

  c.save();
  c.globalAlpha = s.clouds;
  c.imageSmoothingEnabled = true; c.imageSmoothingQuality = 'medium';
  c.drawImage(layer, 0, 0, w, h);
  c.restore();
}
