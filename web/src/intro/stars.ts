// Real stars for the opening's dive: Gaia DR3 positions, distances (from parallax) and colours
// (BP−RP), made by scripts/precompute/opening_stars.py into /opening/stars-gaia.bin (see the
// README there). At p = 0 every star sits where the SPHEREx backdrop shows it, as seen from the
// Sun. Then the camera flies forward a few tens of parsecs: near stars stream past, far ones barely
// move. The flight is illustrative; the stars are not. Nothing here is random or invented.
//
// The layer is its own canvas inside .op-sky, so it shares every CSS transform of the backdrop
// (the scroll camera from opening.ts and the slow title drift): registration holds at all times.
// Every frame is a pure function of (progress, viewport): no clock, no tween.
import { descentAt, type SceneState } from './model';
import { diveTarget, orbitalLayout } from './planets';

export const STARS_URL = '/opening/stars-gaia.bin';
const MAGIC = 'GDR3STAR';
const HEADER = 64;
const DEG = 180 / Math.PI;

/** The backdrop (spherex-sky.webp): SIN projection, 1600 × 1000 px, 32 px per degree, east left. */
export const BACKDROP = { width: 1600, height: 1000, pxPerDeg: 32, ra0: 281, dec0: -4 } as const;

export interface StarCatalog {
  count: number;
  ra: Float64Array; dec: Float64Array;
  /** mas */ parallax: Float32Array;
  g: Float32Array;
  /** NaN where Gaia has no BP−RP. */ bpRp: Float32Array;
  /** Parsecs in the backdrop's view frame: x west (screen right), y north (up), z along the view. */
  x: Float32Array; y: Float32Array; z: Float32Array;
  /** ln(distance²), for the apparent magnitude from anywhere. */ lnD2: Float32Array;
  /** Colour bin, 0 (blue) … TONES.length − 1 (red). */ tone: Uint8Array;
}

/** Display colours by observed BP−RP (reddened in the Milky Way, as Gaia sees them). */
export const TONE_EDGES = [0, .35, .65, .85, 1.05, 1.35, 1.75, 2.3];
export const TONES: ReadonlyArray<readonly [number, number, number]> = [
  [157, 180, 255], [181, 199, 255], [216, 226, 255], [251, 248, 255], [255, 241, 223],
  [255, 226, 191], [255, 207, 152], [255, 184, 116], [255, 159, 90],
];
const WHITE_TONE = 3;
export function toneOf(bpRp: number): number {
  if (!Number.isFinite(bpRp)) return WHITE_TONE;
  let i = 0;
  while (i < TONE_EDGES.length && bpRp >= TONE_EDGES[i]) i++;
  return i;
}

/** Unit vectors of the backdrop's view frame (ICRS): west, north, forward. */
function viewBasis(ra0: number, dec0: number) {
  const a = ra0 / DEG, d = dec0 / DEG;
  return {
    west: [Math.sin(a), -Math.cos(a), 0],
    north: [-Math.sin(d) * Math.cos(a), -Math.sin(d) * Math.sin(a), Math.cos(d)],
    view: [Math.cos(d) * Math.cos(a), Math.cos(d) * Math.sin(a), Math.sin(d)],
  };
}

/** Direction cosines of (ra, dec) in the view frame. */
export function viewDirection(ra: number, dec: number, ra0: number = BACKDROP.ra0, dec0: number = BACKDROP.dec0): [number, number, number] {
  const { west, north, view } = viewBasis(ra0, dec0);
  const a = ra / DEG, d = dec / DEG;
  const u = [Math.cos(d) * Math.cos(a), Math.cos(d) * Math.sin(a), Math.sin(d)];
  const dot = (v: number[]) => u[0] * v[0] + u[1] * v[1] + u[2] * v[2];
  return [dot(west), dot(north), dot(view)];
}

/** Read stars-gaia.bin. Throws on anything that is not a complete version-1 file. */
export function decodeStars(buffer: ArrayBuffer): StarCatalog {
  if (buffer.byteLength < HEADER) throw new Error('star file too short');
  const view = new DataView(buffer);
  const magic = String.fromCharCode(...new Uint8Array(buffer, 0, 8));
  if (magic !== MAGIC) throw new Error('not a Gaia star file');
  if (view.getUint32(8, true) !== 1) throw new Error('unknown star file version');
  const n = view.getUint32(12, true);
  if (!n || buffer.byteLength !== HEADER + n * 8) throw new Error('star file has the wrong size');
  const f = (i: number) => view.getFloat32(16 + i * 4, true);
  const [raLo, raSpan, decLo, decSpan, plLo, plSpan, gLo, gSpan, cLo, cSpan, ra0, dec0] = Array.from({ length: 12 }, (_, i) => f(i));
  const u16 = (k: number) => new Uint16Array(buffer.slice(HEADER + k * n * 2, HEADER + (k + 1) * n * 2));
  const qRa = u16(0), qDec = u16(1), qPlx = u16(2);
  const qG = new Uint8Array(buffer, HEADER + 6 * n, n), qC = new Uint8Array(buffer, HEADER + 7 * n, n);
  const cat: StarCatalog = {
    count: n, ra: new Float64Array(n), dec: new Float64Array(n), parallax: new Float32Array(n),
    g: new Float32Array(n), bpRp: new Float32Array(n), x: new Float32Array(n), y: new Float32Array(n),
    z: new Float32Array(n), lnD2: new Float32Array(n), tone: new Uint8Array(n),
  };
  const { west, north, view: fwd } = viewBasis(ra0, dec0);
  for (let i = 0; i < n; i++) {
    const ra = raLo + qRa[i] / 65535 * raSpan, dec = decLo + qDec[i] / 65535 * decSpan;
    const plx = Math.exp(plLo + qPlx[i] / 65535 * plSpan), dist = 1000 / plx;
    const bpRp = qC[i] ? cLo + (qC[i] - 1) / 254 * cSpan : NaN;
    cat.ra[i] = ra; cat.dec[i] = dec; cat.parallax[i] = plx;
    cat.g[i] = gLo + qG[i] / 255 * gSpan; cat.bpRp[i] = bpRp; cat.tone[i] = toneOf(bpRp);
    const a = ra / DEG, d = dec / DEG, cx = Math.cos(d) * Math.cos(a), cy = Math.cos(d) * Math.sin(a), cz = Math.sin(d);
    cat.x[i] = dist * (cx * west[0] + cy * west[1] + cz * west[2]);
    cat.y[i] = dist * (cx * north[0] + cy * north[1] + cz * north[2]);
    cat.z[i] = dist * (cx * fwd[0] + cy * fwd[1] + cz * fwd[2]);
    cat.lnD2[i] = 2 * Math.log(dist);
  }
  return cat;
}

/** Fetch and decode; resolves null (with one console warning) when the file is missing or broken. */
export async function loadStars(url = STARS_URL, fetcher: typeof fetch = fetch): Promise<StarCatalog | null> {
  try {
    const res = await fetcher(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return decodeStars(await res.arrayBuffer());
  } catch (err) {
    console.warn(`Opening: no Gaia star layer (${url}: ${err instanceof Error ? err.message : err}).`);
    return null;
  }
}

/** How the backdrop image lies in a w × h box with object-fit: cover (before its CSS transform). */
export function coverLayout(w: number, h: number) {
  const k = Math.max(w / BACKDROP.width, h / BACKDROP.height);
  return { k, ox: (w - BACKDROP.width * k) / 2, oy: (h - BACKDROP.height * k) / 2 };
}

/** A view-frame direction to the sky layer's own pixels (SIN, as the backdrop). */
export function projectDirection(dx: number, dy: number, w: number, h: number): [number, number] {
  const { k, ox, oy } = coverLayout(w, h), s = BACKDROP.pxPerDeg * DEG;
  return [ox + k * (BACKDROP.width / 2 + s * dx), oy + k * (BACKDROP.height / 2 - s * dy)];
}

/** Sky position to the sky layer's pixels at p = 0 (the backdrop's own pixel grid, under cover). */
export function skyToLayer(ra: number, dec: number, w: number, h: number): [number, number] {
  const [dx, dy] = viewDirection(ra, dec);
  return projectDirection(dx, dy, w, h);
}

/**
 * The transform opening.ts gives .op-sky (scale and turn about the screen centre), so a point on
 * screen can be taken back to the sky layer. The title drift (CSS, ±2°) is left out: it only moves
 * where the flight is aimed, by a few pixels.
 */
export function skyCamera(p: number) {
  const camera = Math.min(p, .9);
  return { scale: (1.12 + camera * .08) * 1.04, turn: (-2 + camera * 3) / DEG };
}

/** Where the camera flies: toward Earth's dive target, as a unit vector in the view frame. */
export function flightDirection(w: number, h: number): [number, number, number] {
  const [tx, ty] = diveTarget(w, h);
  const { scale, turn } = skyCamera(0);
  const ux = (tx - w / 2) / scale, uy = (ty - h / 2) / scale;
  const lx = w / 2 + ux * Math.cos(-turn) - uy * Math.sin(-turn), ly = h / 2 + ux * Math.sin(-turn) + uy * Math.cos(-turn);
  const { k, ox, oy } = coverLayout(w, h), s = BACKDROP.pxPerDeg * DEG;
  const dx = ((lx - ox) / k - BACKDROP.width / 2) / s, dy = (BACKDROP.height / 2 - (ly - oy) / k) / s;
  const dz = Math.sqrt(Math.max(0, 1 - dx * dx - dy * dy));
  return [dx, dy, dz];
}

/**
 * Earth's disc in the sky layer's frame, shrunk to stay safely inside it whatever the title drift
 * (up to 4°), or 'all' once it covers the whole screen; null while it is see-through or absent.
 */
export function earthCover(w: number, h: number, s: SceneState): [number, number, number] | 'all' | null {
  if (s.earth < 1) return null;
  const { x, y, r } = orbitalLayout(w, h, s.p).earth;
  const corners = [[0, 0], [w, 0], [0, h], [w, h]];
  if (corners.every(([cx, cy]) => Math.hypot(cx - x, cy - y) < r * .99)) return 'all';
  const { scale, turn } = skyCamera(s.p);
  const dx = (x - w / 2) / scale, dy = (y - h / 2) / scale, back = -turn;
  const lx = w / 2 + dx * Math.cos(back) - dy * Math.sin(back), ly = h / 2 + dx * Math.sin(back) + dy * Math.cos(back);
  const safe = r / scale * .99 - Math.hypot(dx, dy) * .07 - 6;
  return safe > 0 ? [lx, ly, safe * safe] : null;
}

/** Illustrative flight length, parsecs. */
export const FLIGHT_PC = 46;
/** Streaks show the path over this much progress (a camera shutter in scroll units). */
export const SHUTTER = .02;
/** Parsecs flown at progress p: eases in, then accelerates; the clouds hide the end. */
export function flightAt(p: number): number {
  const g = descentAt(p), k = 2.6;
  return FLIGHT_PC * (Math.exp(k * g) - 1) / (Math.exp(k) - 1);
}

/** Apparent magnitude of star i from a camera `along` parsecs down `dir`. */
export function apparentMagnitude(cat: StarCatalog, i: number, along: number, dir: readonly number[]): number {
  const qx = cat.x[i] - along * dir[0], qy = cat.y[i] - along * dir[1], qz = cat.z[i] - along * dir[2];
  return cat.g[i] + 1.0857362 * (Math.log(qx * qx + qy * qy + qz * qz) - cat.lnD2[i]);
}

/** Star i on the sky layer from a camera `along` parsecs down `dir`, or null once it is behind. */
export function projectStar(cat: StarCatalog, i: number, along: number, dir: readonly number[], w: number, h: number): [number, number] | null {
  const qx = cat.x[i] - along * dir[0], qy = cat.y[i] - along * dir[1], qz = cat.z[i] - along * dir[2];
  if (qz <= NEAR_PC) return null;
  const inv = 1 / Math.sqrt(qx * qx + qy * qy + qz * qz);
  return projectDirection(qx * inv, qy * inv, w, h);
}
const NEAR_PC = .03;

/** How many stars (brightest first) a viewport draws: fewer on phones. */
export function starBudget(w: number, h: number, total: number): number {
  return Math.min(total, Math.max(5000, Math.round(w * h / 28)));
}

// Display stretch. Magnitudes map to size and opacity; this is a picture, not photometry.
const M_FAINT = 10.9, M_FULL = 6.4;
const LEVELS = 8;
/** Streak widths (CSS px): a streak takes the first width at least as wide as its star. */
const WIDTHS = [1.1, 1.9, 2.8, 4.2];
/** Shorter paths than this (CSS px) draw as plain stars. */
const STREAK_MIN = 3;

let sprites: HTMLCanvasElement[] | null = null;
function glowSprites(): HTMLCanvasElement[] {
  if (sprites) return sprites;
  sprites = TONES.map(([r, g, b]) => {
    const size = 64, canvas = document.createElement('canvas');
    canvas.width = canvas.height = size;
    const c = canvas.getContext('2d')!;
    const glow = c.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
    glow.addColorStop(0, 'rgba(255,255,255,1)');
    glow.addColorStop(.07, `rgba(${r},${g},${b},.9)`);
    glow.addColorStop(.22, `rgba(${r},${g},${b},.28)`);
    glow.addColorStop(.5, `rgba(${r},${g},${b},.07)`);
    glow.addColorStop(1, `rgba(${r},${g},${b},0)`);
    c.fillStyle = glow; c.fillRect(0, 0, size, size);
    return canvas;
  });
  return sprites;
}
const STYLES = TONES.map(([r, g, b]) => Array.from({ length: LEVELS }, (_, l) => `rgba(${r},${g},${b},${((l + 1) / LEVELS).toFixed(3)})`));

// Scratch buffers, reused between frames (grown when a bigger catalogue arrives).
let cap = 0;
let sx = new Float32Array(0), sy = new Float32Array(0), size = new Float32Array(0), mags = new Float32Array(0);
let bucket = new Int16Array(0), order = new Int32Array(0);
// Trail segments: up to two per star (a brighter half at the head, a fainter tail).
let tx0 = new Float32Array(0), ty0 = new Float32Array(0), tx1 = new Float32Array(0), ty1 = new Float32Array(0);
let tBucket = new Int16Array(0), tOrder = new Int32Array(0);
function ensure(n: number) {
  if (n <= cap) return;
  cap = n;
  sx = new Float32Array(n); sy = new Float32Array(n); size = new Float32Array(n); mags = new Float32Array(n);
  bucket = new Int16Array(n); order = new Int32Array(n);
  tx0 = new Float32Array(2 * n); ty0 = new Float32Array(2 * n); tx1 = new Float32Array(2 * n); ty1 = new Float32Array(2 * n);
  tBucket = new Int16Array(2 * n); tOrder = new Int32Array(2 * n);
}
/** Counting sort of `count` items by bucket, so each style is set once and each trail style is one path. */
function sortByBucket(keys: Int16Array, count: number, buckets: number, out: Int32Array): Int32Array {
  const starts = new Int32Array(buckets + 1);
  for (let j = 0; j < count; j++) starts[keys[j] + 1]++;
  for (let b = 0; b < buckets; b++) starts[b + 1] += starts[b];
  const next = starts.slice(0, buckets);
  for (let j = 0; j < count; j++) out[next[keys[j]]++] = j;
  return starts;
}
const levelOf = (alpha: number) => Math.max(0, Math.min(LEVELS - 1, Math.round(alpha * LEVELS) - 1));

/** Draw the star field for scene state s into a w × h (CSS px) context. Returns stars drawn. */
export function drawStarField(c: CanvasRenderingContext2D, cat: StarCatalog, w: number, h: number, s: SceneState): number {
  const fade = s.stars;
  if (fade <= 0) return 0;
  // Earth hides the stars behind it; near the end it covers the whole screen.
  const hide = earthCover(w, h, s);
  if (hide === 'all') return 0;
  const [ex, ey, er2] = hide ?? [0, 0, -1];
  const n = starBudget(w, h, cat.count);
  ensure(n);
  const dir = flightDirection(w, h);
  const along = flightAt(s.p), before = flightAt(Math.max(0, s.p - SHUTTER));
  const { k, ox, oy } = coverLayout(w, h), scale = k * BACKDROP.pxPerDeg * DEG;
  const cx = ox + k * BACKDROP.width / 2, cy = oy + k * BACKDROP.height / 2;
  const margin = 24, perKind = TONES.length * LEVELS;
  const { x, y, z, g, lnD2, tone } = cat;
  const ax = along * dir[0], ay = along * dir[1], az = along * dir[2];
  const bx = before * dir[0], by = before * dir[1], bz = before * dir[2];
  const moving = before !== along;
  let shown = 0, trails = 0;
  for (let i = 0; i < n; i++) {
    const qx = x[i] - ax, qy = y[i] - ay, qz = z[i] - az;
    if (qz <= NEAR_PC) continue;
    const r2 = qx * qx + qy * qy + qz * qz, inv = 1 / Math.sqrt(r2);
    const X = cx + scale * qx * inv, Y = cy - scale * qy * inv;
    if (X < -margin || X > w + margin || Y < -margin || Y > h + margin) continue;
    if ((X - ex) * (X - ex) + (Y - ey) * (Y - ey) < er2) continue;
    const m = g[i] + 1.0857362 * (Math.log(r2) - lnD2[i]);
    if (m > M_FAINT) continue;
    const level = Math.min(1, (M_FAINT - m) / (M_FAINT - M_FULL));
    const alpha = (.26 + .74 * Math.pow(level, 1.25)) * fade;
    const dot = m < M_FULL ? Math.min(4.4, 1.2 + .45 * (M_FULL - m)) : .95 + .25 * level;
    const t = tone[i];
    if (moving) {
      const rx = x[i] - bx, ry = y[i] - by, rz = z[i] - bz;
      if (rz > NEAR_PC) {
        const rinv = 1 / Math.sqrt(rx * rx + ry * ry + rz * rz);
        const PX = cx + scale * rx * rinv, PY = cy - scale * ry * rinv;
        const len = Math.hypot(X - PX, Y - PY);
        if (len > STREAK_MIN) {
          // Light spread along the path, fading to the tail; kept bright so the motion reads.
          const head = Math.min(1, alpha * 1.25) * Math.min(1, 5 / Math.sqrt(len));
          let wc = 0;
          while (wc < WIDTHS.length - 1 && dot > WIDTHS[wc]) wc++;
          const base = (1 + wc) * perKind + t * LEVELS;
          const MX = (X + PX) / 2, MY = (Y + PY) / 2;
          tx0[trails] = MX; ty0[trails] = MY; tx1[trails] = X; ty1[trails] = Y; tBucket[trails++] = base + levelOf(head);
          tx0[trails] = PX; ty0[trails] = PY; tx1[trails] = MX; ty1[trails] = MY; tBucket[trails++] = base + levelOf(head * .55);
        }
      }
    }
    sx[shown] = X; sy[shown] = Y; size[shown] = dot; mags[shown] = m;
    bucket[shown++] = t * LEVELS + levelOf(alpha);
  }
  c.save();
  c.lineCap = 'round';
  if (trails) {
    const buckets = perKind * (1 + WIDTHS.length), starts = sortByBucket(tBucket, trails, buckets, tOrder);
    for (let b = perKind; b < buckets; b++) {
      const from = starts[b], to = starts[b + 1];
      if (from === to) continue;
      const rest = b % perKind;
      c.strokeStyle = STYLES[Math.floor(rest / LEVELS)][rest % LEVELS];
      c.lineWidth = WIDTHS[Math.floor(b / perKind) - 1];
      c.beginPath();
      for (let o = from; o < to; o++) {
        const j = tOrder[o];
        c.moveTo(tx0[j], ty0[j]); c.lineTo(tx1[j], ty1[j]);
      }
      c.stroke();
    }
  }
  const starts = sortByBucket(bucket, shown, perKind, order);
  for (let b = 0; b < perKind; b++) {
    const from = starts[b], to = starts[b + 1];
    if (from === to) continue;
    c.fillStyle = STYLES[Math.floor(b / LEVELS)][b % LEVELS];
    for (let o = from; o < to; o++) {
      const j = order[o], d = size[j];
      c.fillRect(sx[j] - d / 2, sy[j] - d / 2, d, d);
    }
  }
  // The brightest few get a soft glow (a display effect, like a lens's).
  const glows = glowSprites();
  c.globalCompositeOperation = 'lighter';
  for (let j = 0; j < shown; j++) {
    const m = mags[j];
    if (m > 5.2) continue;
    const t = Math.floor(bucket[j] / LEVELS);
    const r = Math.min(64, 7 + 5.5 * (5.2 - m));
    c.globalAlpha = Math.min(1, .35 + .12 * (5.2 - m)) * fade;
    c.drawImage(glows[t], sx[j] - r, sy[j] - r, r * 2, r * 2);
  }
  c.restore();
  return shown;
}

// The live layer: one canvas and one catalogue while the opening is up.
let layer: { canvas: HTMLCanvasElement; catalog: StarCatalog } | null = null;
let failed = false;

/** Attach (or with null, detach) the star canvas and its catalogue. */
export function setStarLayer(canvas: HTMLCanvasElement | null, catalog: StarCatalog | null = null): void {
  layer = canvas && catalog ? { canvas, catalog } : null;
  failed = false;
}

/** Paint the star layer for scene state s. Never throws: a failure hides the layer, once. */
export function paintStars(w: number, h: number, dpr: number, s: SceneState): void {
  if (!layer || failed) return;
  const { canvas, catalog } = layer;
  try {
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
    }
    const c = canvas.getContext('2d');
    if (!c) throw new Error('no 2D context');
    c.setTransform(dpr, 0, 0, dpr, 0, 0); c.clearRect(0, 0, w, h);
    drawStarField(c, catalog, w, h, s);
  } catch (err) {
    failed = true;
    canvas.hidden = true;
    console.warn(`Opening: the Gaia star layer stopped (${err instanceof Error ? err.message : err}).`);
  }
}
