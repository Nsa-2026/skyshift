/**
 * The goddess, standing in profile, looking up at the sky. Drawn entirely in code after the
 * conventions of ancient Egyptian painting: the proportions follow the reference figure Daniel picked
 * (measured in its pixel grid, 451 px from crown to sole), with two changes he asked for: she lifts
 * her head, and her skin (or, in `silhouette` mode, her whole figure) is black.
 *
 * Drawing space is the reference grid (ground at y = 500); figure space is centimetres from her feet,
 * x negative = the way she faces (screen left), y negative = up.
 */
type Ctx = CanvasRenderingContext2D;
const TAU = Math.PI * 2;

/** Centimetres per reference pixel (crown of the wig to the soles = 170 cm). */
const K = 170 / 451;
const ORIGIN = { x: 140, y: 500 };
/** The head turns up about the top of the neck. */
const PIVOT = { x: 150, y: 95 };
export const LOOK_UP = .5;
const rot = (x: number, y: number, a = LOOK_UP, p = PIVOT) => {
  const c = Math.cos(a), s = Math.sin(a), dx = x - p.x, dy = y - p.y;
  return { x: p.x + dx * c - dy * s, y: p.y + dx * s + dy * c };
};
const toFigure = (p: { x: number; y: number }) => ({ x: (p.x - ORIGIN.x) * K, y: (p.y - ORIGIN.y) * K });
/** Eye centre and width in figure space: what the camera pushes in on. */
const EYE = { x: 127.5, y: 77.4, w: 13 };
export const GODDESS_EYE = toFigure(rot(EYE.x, EYE.y));
export const GODDESS_EYE_WIDTH = EYE.w * K;
/** Actual iris centre/radius, distinct from the almond centre: used for the sky aperture. */
export const GODDESS_PUPIL = toFigure(rot(125.8, 76.9));
export const GODDESS_PUPIL_RADIUS = 2.45 * K;
export const GODDESS_HEAD = toFigure(rot(140, 78));
export const GODDESS_HEIGHT = (ORIGIN.y - rot(151.5, 7).y) * K;

const hex = (h: string) => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
/** Night grading: every colour is pulled toward deep blue; `light` 0..1 lifts it back. */
function grade(light: number) {
  const night = [8, 12, 26], k = .34 + .6 * light;
  return (h: string, a = 1) => {
    const c = hex(h).map((v, i) => Math.round(night[i] + (v - night[i]) * k));
    return a === 1 ? `rgb(${c[0]},${c[1]},${c[2]})` : `rgba(${c[0]},${c[1]},${c[2]},${a})`;
  };
}

type Pt = { x: number; y: number };
/** Polygon / smooth path through points (Catmull-Rom when `smooth`). */
function shape(pts: Pt[], smooth = false) {
  const p = new Path2D();
  p.moveTo(pts[0].x, pts[0].y);
  if (!smooth) { for (const q of pts.slice(1)) p.lineTo(q.x, q.y); p.closePath(); return p; }
  const n = pts.length;
  for (let i = 0; i < n; i++) {
    const p0 = pts[(i - 1 + n) % n], p1 = pts[i], p2 = pts[(i + 1) % n], p3 = pts[(i + 2) % n];
    p.bezierCurveTo(p1.x + (p2.x - p0.x) / 6, p1.y + (p2.y - p0.y) / 6, p2.x - (p3.x - p1.x) / 6, p2.y - (p3.y - p1.y) / 6, p2.x, p2.y);
  }
  p.closePath(); return p;
}
const P = (x: number, y: number): Pt => ({ x, y });
const H = (x: number, y: number): Pt => rot(x, y);

/** Striped band laid across a limb: centre, direction along the limb, half-width across, length along. */
function band(c: Ctx, x: number, y: number, dx: number, dy: number, half: number, len: number, cols: string[]) {
  c.save(); c.translate(x, y); c.rotate(Math.atan2(dy, dx));
  const step = len / cols.length;
  cols.forEach((col, i) => { c.fillStyle = col; c.fillRect(-len / 2 + i * step, -half, step + .05, half * 2); });
  c.restore();
}

export type GoddessMode = 'skin' | 'silhouette';

export function drawGoddess(c: Ctx, t: number, pxPerCm: number, light = 1, mode: GoddessMode = 'skin') {
  const G = grade(light);
  const sil = mode === 'silhouette';
  const BLACK = G('#1b1e29'), DEEP = G('#11131b');
  const col = (h: string) => sil ? BLACK : G(h);
  const SKIN = BLACK, NAVY = col('#0d2a5c'), RED = col('#c0391b'), BLUE = col('#1d4fb0'), GOLD = col('#f2c447'),
    ORANGE = col('#e8902a'), GREEN = col('#2c9c63'), WHITE = col('#f2efe6'), RIBBON = col('#d8141c');
  const px = 1 / (pxPerCm * K);                                 // one screen pixel in reference units
  const RIM = `rgba(165,192,245,${.3 + .34 * light})`, rw = Math.max(.32, px * 1.15);
  const sway = Math.sin(t * .9) * .6;                           // the ribbons and the wig ends move a little
  c.save();
  c.scale(K, K); c.translate(-ORIGIN.x, -ORIGIN.y);
  c.lineJoin = 'round'; c.lineCap = 'round';
  const rim = (p: Path2D | (() => void)) => { c.strokeStyle = RIM; c.lineWidth = rw; if (typeof p === 'function') { c.beginPath(); p(); c.stroke(); } else c.stroke(p); };

  // ── 1. hair falling behind the shoulders ──
  const backHair = shape([
    H(123, 58), H(150, 47.5), H(178, 54), H(183, 72), P(196, 120), P(207.5 + sway * .3, 167), P(208 + sway * .3, 174),
    P(201 + sway * .3, 174.5), P(188, 140), P(170, 118), H(160, 96), H(150, 90), H(124, 70)], true);
  c.fillStyle = NAVY; c.fill(backHair);
  c.fillStyle = GOLD; c.fillRect(201 + sway * .3, 167.5, 7, 6.5);
  c.fillStyle = ORANGE; for (let i = 0; i < 3; i++) c.fillRect(201 + sway * .3, 168.6 + i * 2, 7, .7);

  // ── 2. body: torso, dress, legs and feet ──
  const torso = shape([P(100, 130), P(118, 122), P(140, 119), P(162, 120), P(186, 125), P(199, 133), P(201, 150), P(182, 152), P(172, 180), P(110, 180), P(109, 152), P(90, 145), P(93, 134)], true);
  c.fillStyle = SKIN; c.fill(torso);
  // Legs below the hem.
  c.fillStyle = SKIN;
  const foot1 = shape([P(118, 455), P(136, 455), P(137, 482), P(137, 499.6), P(71.5, 499.6), P(69.6, 497.6), P(71.6, 495.4), P(92, 490), P(109, 483.5), P(118, 474)]);
  c.fill(foot1);
  const foot2 = shape([P(138, 455), P(156, 455), P(157, 482), P(159.6, 490), P(159.5, 497), P(157.5, 499.6), P(94.5, 499.6), P(92.4, 498), P(94, 496.2), P(113, 490.5), P(128, 484), P(138, 474)]);
  c.fill(foot2);
  rim(() => { c.moveTo(94, 496.2); c.lineTo(113, 490.5); c.lineTo(128, 484); c.lineTo(138, 474); });
  rim(() => { c.moveTo(71.6, 495.4); c.lineTo(92, 490); c.lineTo(109, 483.5); c.lineTo(118, 474); c.lineTo(118, 458); });
  for (const x0 of [118, 138]) band(c, x0 + 9, 463.5, 1, 0, 2.8, 18, [GOLD, BLUE, GOLD, BLUE, GOLD]);
  // Sheath dress, with a white hem and bodice edge.
  const dress = shape([P(110, 180), P(172, 180), P(166, 196), P(164, 210), P(171, 226), P(174.5, 244), P(171, 256), P(167.5, 263), P(162, 300), P(160, 325), P(157.5, 400), P(157.5, 453), P(120, 453), P(121, 410), P(122, 365), P(116.5, 340), P(112.5, 305), P(112.3, 262), P(114, 225), P(118, 200), P(121, 190)]);
  c.fillStyle = RED; c.fill(dress);
  rim(() => { c.moveTo(121, 190); c.lineTo(118, 200); c.lineTo(114, 225); c.lineTo(112.3, 262); c.lineTo(112.5, 305); c.lineTo(116.5, 340); c.lineTo(122, 365); c.lineTo(121, 410); c.lineTo(120, 453); });
  c.fillStyle = WHITE; c.fillRect(110, 179, 62, 4); c.fillRect(120, 451.5, 37.5, 3.6);
  if (!sil) { c.fillStyle = G('#9aa0ad'); for (let x = 113; x < 170; x += 6) { c.fillRect(x, 180, .7, 2.2); c.fillRect(x + 1.6, 180, .7, 2.2); } for (let x = 123; x < 156; x += 6) c.fillRect(x, 452.3, .7, 2); }
  // Shoulder straps.
  for (const [a, b] of [[P(107, 125), P(116.5, 180)], [P(184, 125), P(170, 180)]] as [Pt, Pt][]) {
    c.strokeStyle = WHITE; c.lineWidth = 5.6; c.beginPath(); c.moveTo(a.x, a.y); c.lineTo(b.x, b.y); c.stroke();
    c.strokeStyle = RED; c.lineWidth = 3.8; c.beginPath(); c.moveTo(a.x, a.y); c.lineTo(b.x, b.y); c.stroke();
  }

  // ── 3. the back arm, hanging, with the ankh ──
  c.strokeStyle = NAVY; c.lineWidth = 2.8;
  c.beginPath(); c.ellipse(190, 304.5, 4.2, 6.5, 0, 0, TAU); c.stroke();
  c.lineWidth = 3; c.beginPath(); c.moveTo(176.5, 326.5); c.lineTo(203.5, 326.5); c.stroke();
  c.fillStyle = NAVY; c.fill(shape([P(188.6, 311), P(191.4, 311), P(191.6, 348), P(193.2, 351.5), P(186.8, 351.5), P(188.4, 348)]));
  c.fillStyle = SKIN;
  const backArm = shape([P(182, 150), P(186, 143), P(194, 140.5), P(201, 143), P(201.8, 170), P(201.4, 230), P(198.4, 279.5), P(184, 279.5), P(181, 230), P(181, 175)], true);
  c.fill(backArm);
  const backHand = shape([P(184, 282), P(198.4, 282), P(199.2, 292), P(197.5, 303), P(195.5, 309), P(191.6, 310.6), P(189.4, 306.5), P(186.5, 309.8), P(183.6, 307), P(179.8, 300.5), P(180.6, 292)], true);
  c.fill(backHand);
  rim(() => { c.moveTo(181.6, 160); c.lineTo(181, 230); c.lineTo(184, 279.5); });
  band(c, 191.4, 165.5, 1, 0, 4, 20.4, [GOLD, BLUE, GOLD, BLUE, GOLD]);
  band(c, 191.2, 280.5, 1, 0, 2.6, 14.6, [GOLD, BLUE, GOLD, BLUE, GOLD]);

  // ── 4. neck, collar, lappet ──
  c.fillStyle = SKIN;
  const neck = shape([H(122, 104), H(134, 110.6), P(133, 118), P(132.5, 126), P(153, 126), H(156, 95), H(148, 86), H(134, 92)]);
  c.fill(neck);
  rim(() => { const a = H(134, 110.6); c.moveTo(a.x, a.y); c.lineTo(133, 118); });
  // Broad collar: concentric bands under the throat, the outer one beaded.
  c.save(); c.translate(146.5, 116.5);
  const ring = (rx: number, ry: number, fill: string) => { c.fillStyle = fill; c.beginPath(); c.ellipse(0, 0, rx, ry, 0, 0, Math.PI); c.closePath(); c.fill(); };
  ring(34.5, 31.5, GOLD); ring(29.5, 26.2, ORANGE); ring(28.3, 25, GOLD);
  ring(25.2, 21.6, BLUE); ring(23.4, 19.6, RED); ring(21.8, 17.8, BLUE); ring(20.2, 16, GREEN); ring(18.6, 14.2, BLUE); ring(17, 12.4, RED);
  ring(15.6, 10.8, SKIN);
  if (!sil) {
    c.fillStyle = ORANGE;
    for (let i = 1; i < 22; i++) { const a = i / 22 * Math.PI; c.beginPath(); c.ellipse(Math.cos(a) * 31.6, Math.sin(a) * 28.6, .9, 1.6, a - Math.PI / 2, 0, TAU); c.fill(); }
  }
  c.restore();
  // Lappet of the wig, falling in front of the collar.
  const lappet = shape([H(145, 74), H(161.5, 75), P(161.4 + sway * .2, 118), P(161 + sway * .25, 161), P(145 + sway * .25, 161), P(145.4 + sway * .2, 118)], true);
  c.fillStyle = NAVY; c.fill(lappet);
  c.fillStyle = GOLD; c.fillRect(145 + sway * .25, 159.5, 16, 6.5);
  c.fillStyle = ORANGE; for (let i = 0; i < 3; i++) c.fillRect(145 + sway * .25, 160.6 + i * 2, 16, .7);

  // ── 5. the front arm, reaching a little forward ──
  c.fillStyle = SKIN;
  const arm = shape([P(93, 134), P(100, 131), P(106.5, 139), P(107.8, 157), P(98, 196), P(87.5, 232), P(70, 256), P(51.2, 280.5), P(40, 277.2), P(57, 248), P(75, 220), P(84, 180), P(88, 150)], true);
  c.fill(arm);
  const hand = shape([P(40.4, 277), P(51.4, 280.6), P(48.5, 287.5), P(43, 293), P(37.6, 296.6), P(32.6, 302.6), P(30.4, 302.4), P(31.6, 298.6), P(27.4, 301.4), P(22.4, 304.6), P(17.4, 308.4), P(15.2, 306.6), P(18.4, 301.2), P(24.8, 292.6), P(32, 284.2)], true);
  c.fill(hand);
  rim(() => { c.moveTo(88, 150); c.lineTo(84, 180); c.lineTo(75, 220); c.lineTo(57, 248); c.lineTo(40, 277.2); c.lineTo(32, 284.2); c.lineTo(24.8, 292.6); c.lineTo(18.4, 301.2); });
  band(c, 97.6, 162.5, -2.6, 10, 9.9, 9.2, [BLUE, GOLD, BLUE, GOLD]);
  band(c, 45.4, 279.6, -12, 18, 6, 5.6, [BLUE, GOLD, BLUE]);

  // ── 6. the head, turned up toward the sky ──
  c.translate(PIVOT.x, PIVOT.y); c.rotate(LOOK_UP); c.translate(-PIVOT.x, -PIVOT.y);
  const face = new Path2D('M 121 69.5 C 119.2 74.5 117.3 79 116.2 82.4 C 114.4 85 112.4 88 112.5 90 C 113 91.6 115 92.5 116.6 93 ' +
    'C 115.7 94.4 115.6 95.5 116 96.4 C 116.4 97.6 116 99.2 117 101 C 118.2 104 120.2 106 123 107.6 C 127 109.6 131 110.6 134 111 L 147 108 L 151 73.5 C 145 73.5 132 71 121 69.5 Z');
  c.fillStyle = SKIN; c.fill(face);
  rim(() => { c.moveTo(121, 69.5); c.bezierCurveTo(119.2, 74.5, 117.3, 79, 116.2, 82.4); c.bezierCurveTo(114.4, 85, 112.4, 88, 112.5, 90);
    c.bezierCurveTo(113, 91.6, 115, 92.5, 116.6, 93); c.bezierCurveTo(115.7, 94.4, 115.6, 95.5, 116, 96.4); c.bezierCurveTo(116.4, 97.6, 116, 99.2, 117, 101);
    c.bezierCurveTo(118.2, 104, 120.2, 106, 123, 107.6); c.bezierCurveTo(127, 109.6, 131, 110.6, 134, 111); });
  c.strokeStyle = DEEP; c.lineWidth = Math.max(.35, px);
  c.beginPath(); c.moveTo(116.3, 96.2); c.lineTo(119.6, 96.5); c.stroke();
  // Ear.
  const ear = new Path2D('M 150 72 C 153 68.6 157.4 70.6 157.4 77 C 157.4 83 155.4 88 151.6 90.2 C 150 86 149.4 78 150 72 Z');
  c.fillStyle = SKIN; c.fill(ear); rim(ear);
  // Wig over the crown, framing the face.
  const wig = new Path2D('M 121 70.5 C 120.4 64 121 58 124 55 C 132 49.2 145 47.4 155 47.8 C 166 48.4 175 51 178.4 55 L 182 72 ' +
    'C 175 75.5 166 75 160 74 L 158.6 73.4 C 157.4 70.2 154.2 68.8 150 72 C 140 72 130 71.4 121 70.5 Z');
  c.fillStyle = NAVY; c.fill(wig);
  // Pot-shaped crown (her hieroglyph).
  c.fillStyle = NAVY; c.fill(new Path2D('M 140 7 L 163 7 L 161.4 11.2 C 168 13.2 171.2 20 171.2 28 C 171.2 37 166.4 44 160.4 48 L 142.6 48 C 136.6 44 131.8 37 131.8 28 C 131.8 20 135 13.2 141.6 11.2 Z'));
  // Headband, knotted at the back.
  c.strokeStyle = RIBBON; c.lineWidth = 3.4; c.lineCap = 'butt';
  c.beginPath(); c.moveTo(121.6, 60); c.bezierCurveTo(140, 58.6, 162, 58.6, 180, 57.4); c.stroke();
  c.lineCap = 'round';
  drawEye(c, G, sil, px);
  c.restore();

  // Ribbon ends hang from the knot (back in body space so they fall with gravity).
  c.save(); c.scale(K, K); c.translate(-ORIGIN.x, -ORIGIN.y);
  const knot = H(183.6, 53.4);
  c.strokeStyle = RIBBON; c.lineWidth = 4; c.lineCap = 'butt';
  for (const [dx, dy] of [[-14, 50], [5, 46]]) {
    const ex = knot.x + dx + sway, ey = knot.y + dy;
    c.beginPath(); c.moveTo(knot.x, knot.y); c.quadraticCurveTo((knot.x + ex) / 2 + 2, (knot.y + ey) / 2, ex, ey); c.stroke();
  }
  c.fillStyle = RIBBON; c.beginPath(); c.arc(knot.x, knot.y, 3, 0, TAU); c.fill();
  c.restore();
}

/** The eye: white almond, black iris toward the front, the long cosmetic line and the brow. */
function drawEye(c: Ctx, G: ReturnType<typeof grade>, sil: boolean, px: number) {
  const LINER = sil ? G('#05060a') : G('#07080c');
  c.save();
  const almond = new Path2D('M 121 77.4 C 123.6 74.6 129.6 74.2 134 77.2 C 130.6 79.6 124.6 79.8 121 77.4 Z');
  c.fillStyle = G('#f3f0e8'); c.fill(almond);
  c.save(); c.clip(almond);
  c.fillStyle = LINER; c.beginPath(); c.arc(125.8, 76.9, 2.45, 0, TAU); c.fill();
  c.fillStyle = 'rgba(235,242,255,.85)'; c.beginPath(); c.arc(125.1, 76.1, .4, 0, TAU); c.fill();
  c.restore();
  // Liner: heavier on the upper lid, extended toward the ear.
  c.strokeStyle = LINER; c.lineWidth = Math.max(.9, px * 1.2);
  c.beginPath(); c.moveTo(120.6, 77.6); c.bezierCurveTo(123.6, 74.2, 129.8, 73.8, 134.2, 77.1); c.lineTo(139.6, 76.2); c.stroke();
  c.lineWidth = Math.max(.5, px); c.beginPath(); c.moveTo(121, 77.4); c.bezierCurveTo(124.6, 79.8, 130.6, 79.6, 134, 77.2); c.stroke();
  // Brow.
  c.lineWidth = 1.25; c.beginPath(); c.moveTo(120.2, 74.2); c.bezierCurveTo(124.6, 71.4, 131.6, 71.2, 137.6, 72.8); c.stroke();
  // On black skin the liner disappears, so a thread of moonlight traces the upper lid.
  c.strokeStyle = 'rgba(190,210,250,.45)'; c.lineWidth = Math.max(.25, px * .8);
  c.beginPath(); c.moveTo(121.6, 76); c.bezierCurveTo(124.4, 73.4, 129.6, 73.1, 133.4, 75.4); c.stroke();
  c.restore();
}
