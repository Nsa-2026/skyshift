/**
 * Galileo Galilei in Padua, 1609–10: standing in profile at night, looking through his telescope.
 * Drawn entirely in code (Canvas 2D paths), so every part the camera visits stays sharp: the
 * opening pushes in to his face, glides along the tube and ends in the objective lens.
 *
 * What it follows: a man of about 45 with a full, short, reddish-brown beard and a receding
 * hairline (Santi di Tito's portrait, c. 1601); the long dark gown and small plain white collar of
 * a professor at Padua; no hat. The telescope is a slender tube of about a metre (his two surviving
 * instruments of 1609–10, in the Museo Galileo in Florence, are 92 and 98 cm), slightly tapered,
 * covered in red-brown leather with thin gold tooling, with the objective at the wide far end.
 *
 * Figure space: centimetres from his feet; x positive = the way he faces (screen right), y negative
 * = up. He is a flat profile. The telescope is a tapered cylinder, so the camera can swing round to
 * face its lens (`turn`) and the lens ends as a true circle.
 */
type Ctx = CanvasRenderingContext2D;
type Pt = { x: number; y: number };
const TAU = Math.PI * 2;
const DEG = Math.PI / 180;
const P = (x: number, y: number): Pt => ({ x, y });
const clamp01 = (n: number) => Math.max(0, Math.min(1, n));

/** Crown to soles, standing straight. */
export const GALILEO_HEIGHT = 172;
/** The head is drawn upright about the top of the neck, then tilted back to the eyepiece. */
const NECK = P(-4, -153);
const TILT = -24 * DEG;
/** Drawn a touch larger than life, as figures in illustrations are, so the face carries. */
const HEAD_SCALE = 1.1;
const head = (x: number, y: number): Pt => ({
  x: NECK.x + HEAD_SCALE * (x * Math.cos(TILT) - y * Math.sin(TILT)),
  y: NECK.y + HEAD_SCALE * (x * Math.sin(TILT) + y * Math.cos(TILT)),
});
/** Middle of the head and his (near) eye, the one at the eyepiece. */
export const GALILEO_HEAD = head(1, -9);
export const GALILEO_EYE = head(8.2, -9.6);

/** The tube's elevation above the horizon, and how far it turns toward us in the side view. */
export const SCOPE_ELEVATION = 35 * DEG;
export const SCOPE_YAW = 15 * DEG;
/** Unit vector along the tube, eyepiece to objective, in figure space; NORMAL points below it. */
export const SCOPE_AXIS = P(Math.cos(SCOPE_ELEVATION), -Math.sin(SCOPE_ELEVATION));
const NORMAL = P(Math.sin(SCOPE_ELEVATION), Math.cos(SCOPE_ELEVATION));
const along = (p: Pt, d: number): Pt => P(p.x + SCOPE_AXIS.x * d, p.y + SCOPE_AXIS.y * d);
/** Objective face to eyepiece end, true length (cm). Axial distances below count from the objective. */
export const SCOPE_LENGTH = 97;
const CELL = 1.8, BODY_END = 92.5;
const R_FRONT = 2.45, R_BACK = 1.85, R_EYEPIECE = 1.3;
/** The glass of the objective (a lens about 38 mm across), and the wooden cell round it. */
export const OBJECTIVE_RADIUS = 1.9;
export const OBJECTIVE_CELL = 2.75;
const R_CELL = OBJECTIVE_CELL;
/** The eyepiece end, against his eye; the objective centre, at the far end of the tube. */
export const GALILEO_EYEPIECE = along(GALILEO_EYE, .9);
export const GALILEO_OBJECTIVE = along(GALILEO_EYEPIECE, SCOPE_LENGTH * Math.cos(SCOPE_YAW));
/** Where his near hand holds the tube, as an axial distance from the objective. */
const GRIP_A = 58;
export const GALILEO_HAND = along(GALILEO_OBJECTIVE, -GRIP_A * Math.cos(SCOPE_YAW));

const hex = (h: string) => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
/** Night grading: every colour is pulled toward deep blue; `light` 0..1 lifts it back. */
function grade(light: number) {
  const night = [8, 12, 26], k = .2 + .75 * clamp01(light);
  return (h: string, a = 1) => {
    const c = hex(h).map((v, i) => Math.round(night[i] + (v - night[i]) * k));
    return `rgba(${c[0]},${c[1]},${c[2]},${a})`;
  };
}
type Grade = ReturnType<typeof grade>;

/** Closed (or open) smooth path through points: Catmull-Rom as Béziers. */
function curve(pts: Pt[], closed = true, path = new Path2D()): Path2D {
  const n = pts.length;
  path.moveTo(pts[0].x, pts[0].y);
  const at = (i: number) => closed ? pts[(i + n) % n] : pts[Math.max(0, Math.min(n - 1, i))];
  for (let i = 0; i < (closed ? n : n - 1); i++) {
    const p0 = at(i - 1), p1 = at(i), p2 = at(i + 1), p3 = at(i + 2);
    path.bezierCurveTo(p1.x + (p2.x - p0.x) / 6, p1.y + (p2.y - p0.y) / 6, p2.x - (p3.x - p1.x) / 6, p2.y - (p3.y - p1.y) / 6, p2.x, p2.y);
  }
  if (closed) path.closePath();
  return path;
}
function poly(pts: Pt[]): Path2D {
  const path = new Path2D();
  pts.forEach((q, i) => (i ? path.lineTo(q.x, q.y) : path.moveTo(q.x, q.y)));
  path.closePath();
  return path;
}

/** Moonlight comes from high in front of him (the sky he is looking at). */
const LIGHT = P(.5, -.866);

interface Pen { c: Ctx; G: Grade; px: number; rim: string; rimW: number }
/** Fill a shape and leave a thin moonlit rim along the edges that face the light. */
function lit(pen: Pen, path: Path2D, fill: string | CanvasGradient, light = LIGHT, strength = 1) {
  const { c } = pen;
  c.fillStyle = fill; c.fill(path);
  if (strength <= 0) return;
  c.save(); c.clip(path);
  c.globalAlpha *= strength;
  c.fillStyle = pen.rim; c.fill(path);
  c.globalAlpha /= strength;
  c.translate(-light.x * pen.rimW, -light.y * pen.rimW);
  c.fillStyle = fill; c.fill(path);
  c.restore();
}
/** Moonlit rim along one stretch of a shape's edge (where the shape is the silhouette). */
function rimEdge(pen: Pen, shape: Path2D, edge: Pt[]) {
  const { c } = pen;
  c.save(); c.clip(shape);
  c.strokeStyle = pen.rim; c.lineWidth = pen.rimW * 1.6;
  c.stroke(curve(edge, false));
  c.restore();
}
function line(pen: Pen, pts: Pt[], color: string, width: number) {
  const { c } = pen;
  c.strokeStyle = color; c.lineWidth = width;
  c.stroke(curve(pts, false));
}

export interface GalileoView {
  /** Screen pixels per centimetre, for hairline widths. */
  pxPerCm: number;
  /** 0 = moonlit near-silhouette, 1 = full colour. */
  light: number;
  /** 0 = side view; 1 = the camera has swung round to face the objective. */
  turn?: number;
  /** Opacity of everything but the telescope (it fades while the camera swings round). */
  body?: number;
  /** 0..1: how far the lens glass has gone (its reflection fades with it). */
  open?: number;
}

/** Draw Galileo and his telescope in figure space (the caller sets up the camera). */
export function drawGalileo(c: Ctx, view: GalileoView): void {
  const G = grade(view.light);
  const px = 1 / view.pxPerCm;
  const pen: Pen = {
    c, G, px,
    rim: `rgba(172,198,246,${.62 - .3 * clamp01(view.light)})`,
    rimW: px * 2.2,
  };
  const body = clamp01(view.body ?? 1);
  c.save();
  c.lineJoin = 'round'; c.lineCap = 'round';
  if (body > 0) {
    c.save(); c.globalAlpha *= body;
    drawBody(pen);
    drawHead(pen);
    c.restore();
  }
  drawTelescope(pen, view.turn ?? 0, view.open ?? 0);
  if (body > 0) {
    c.save(); c.globalAlpha *= body;
    drawArm(pen);
    c.restore();
  }
  c.restore();
}

// ── The body: shoes, gown, collar, neck ─────────────────────────────────────────────────────
/** He leans back to look up: the lower body is drawn shifted forward, nothing above the chest. */
const LEAN = 5;
const leanBack = (pts: Pt[]) => pts.map(q => P(q.x + LEAN * clamp01(1 + q.y / 132), q.y));
function drawBody(pen: Pen) {
  const { c, G, px } = pen;
  const SHOE = G('#241b16');
  // Back foot (his weight is on it), then the front foot, a short step forward.
  lit(pen, curve(leanBack([P(-20.4, 0), P(-.6, 0), P(1.6, -1.6), P(.2, -4.4), P(-6.4, -6.6), P(-16.6, -7), P(-20.8, -4.2)])), SHOE);
  lit(pen, curve(leanBack([P(-.8, 0), P(23.4, 0), P(26.6, -1.5), P(25.4, -4.2), P(17.6, -6.6), P(5, -7.6), P(-.8, -5)])), SHOE);

  // The gown: long and dark, falling straight from the shoulders; he leans back a little.
  const gown = curve(leanBack([
    P(-8.6, -149.4), P(-14.6, -146.2), P(-18.2, -138.4), P(-19.2, -123), P(-18.4, -105), P(-18.6, -87),
    P(-20.2, -62), P(-22.6, -34), P(-25.8, -7.6), P(-24.4, -4.6), P(-14, -5.8), P(-3, -4.4), P(9, -5.8),
    P(18.6, -4.6), P(23.2, -6.2), P(21.6, -30), P(19.8, -52), P(17.8, -72), P(16.8, -92), P(16.2, -110),
    P(14.4, -126), P(11, -138.6), P(6.8, -146.6),
  ]));
  const cloth = c.createLinearGradient(-24, 0, 22, 0);
  cloth.addColorStop(0, G('#121016')); cloth.addColorStop(.55, G('#221f2a')); cloth.addColorStop(1, G('#2c2836'));
  lit(pen, gown, cloth);
  // Folds: long soft creases from the chest to the hem.
  c.save(); c.clip(gown);
  const fold = G('#0a080d', .7), sheen = G('#3a3546', .55);
  const w = Math.max(.45, px);
  line(pen, leanBack([P(-4, -100), P(-6.5, -62), P(-10.5, -26), P(-13.5, -5)]), fold, w * 1.8);
  line(pen, leanBack([P(5.5, -96), P(5, -60), P(3.6, -28), P(3, -5)]), fold, w * 1.4);
  line(pen, leanBack([P(-13, -104), P(-15.6, -64), P(-19.2, -28), P(-21.4, -5)]), fold, w * 1.3);
  line(pen, leanBack([P(12.6, -90), P(14, -56), P(16.4, -24), P(17.4, -5)]), fold, w);
  line(pen, leanBack([P(9.4, -92), P(10.2, -58), P(10.8, -26), P(11.6, -6)]), sheen, w * .9);
  line(pen, leanBack([P(.8, -98), P(-.6, -62), P(-3.4, -28), P(-4.4, -6)]), sheen, w * .8);
  // The front edge of the gown, down from the collar.
  line(pen, leanBack([P(6.6, -145.6), P(11.4, -130), P(14, -110), P(15.2, -88)]), fold, w * 1.1);
  c.restore();

  // Neck, between the collar and the beard, mostly in the beard's shadow.
  c.fillStyle = G('#7a4a36');
  c.fill(poly([P(-8.2, -148), head(-5.6, 2.4), head(-1.4, 4.8), head(4.6, 4.6), P(6.2, -146.4)]));

  // A small plain white collar, falling over the gown.
  const collar = curve([P(-10.2, -150), P(-4.8, -151.6), P(1.6, -151), P(6.8, -149), P(8.6, -145.6), P(3, -144.4), P(-4, -145), P(-11.4, -146.6)]);
  lit(pen, collar, G('#e8e2d3'));
  c.save(); c.clip(collar);
  line(pen, [P(-11, -146.8), P(-4, -145.4), P(3, -144.8), P(8.6, -145.8)], G('#b3ad9f'), Math.max(.3, px));
  line(pen, [P(-10, -149.8), P(-4.6, -151.2), P(1.6, -150.7), P(6.8, -148.8)], G('#a49e91', .8), Math.max(.25, px * .9));
  c.restore();
}

// ── The head: skin, hair, beard, ear, brow ─────────────────────────────────────────────────
// Drawn upright about the top of the neck (x forward, y down), then tilted back.
const FACE_D = (
  'M -5.6 2.6 C -7.6 0.4 -8.8 -3.4 -9.4 -8 C -10 -13 -8.4 -17.4 -4.6 -19.4 C -1 -21.2 3.6 -20.8 6.4 -18.6 ' +
  'C 8.4 -17 9.4 -14.6 9.8 -12.4 C 10 -11.4 10.4 -10.7 10.3 -10.1 C 10.2 -9.6 9.7 -9.4 9.7 -9 ' +
  'C 10.3 -8.2 11.3 -7 12 -6 C 12.5 -5.3 12.7 -4.6 12.2 -4.3 C 11.7 -4.05 11.1 -4.3 10.7 -4.2 ' +
  'C 10.9 -3.6 11.1 -3.1 10.9 -2.6 C 10.6 -2.3 10.6 -2 10.9 -1.6 C 11 -1 10.4 -.6 10.5 .2 ' +
  'C 10.7 1.2 10.4 2.2 9 2.6 C 7 3.2 4.4 3.2 2.4 4.4 C 0 5.6 -3 4.8 -5.6 2.6 Z');
const HAIR = [
  P(1.6, -20.7), P(-3.4, -21.1), P(-8, -18.3), P(-10.3, -12.4), P(-10.1, -6), P(-8.6, -1.2), P(-6.6, 2.2),
  P(-4.4, 1.4), P(-3.4, -2.6), P(-3.2, -7.4), P(-1.4, -11.8), P(1.2, -13.4), P(2.6, -15.6), P(1.6, -18),
];
const BEARD = [
  P(.6, -12.4), P(2.4, -11.8), P(3.6, -9.2), P(5.6, -7.2), P(8, -6.1), P(9.8, -5.2), P(10.9, -4.4),
  P(11.9, -3.6), P(11.8, -2.6), P(11.1, -2.1), P(11.5, -1.1), P(12.4, .6), P(12.5, 2.6), P(11.4, 4.2),
  P(9, 5.2), P(6.2, 5.6), P(3.4, 5.2), P(.8, 3.4), P(-1.1, -.4), P(-1.4, -4.6), P(-.8, -9),
];
function drawHead(outer: Pen) {
  const pen: Pen = { ...outer, px: outer.px / HEAD_SCALE, rimW: outer.rimW / HEAD_SCALE };
  const { c, G, px } = pen;
  c.save();
  c.translate(NECK.x, NECK.y); c.rotate(TILT); c.scale(HEAD_SCALE, HEAD_SCALE);
  // Moonlight in the head's own frame.
  const L = P(LIGHT.x * Math.cos(-TILT) - LIGHT.y * Math.sin(-TILT), LIGHT.x * Math.sin(-TILT) + LIGHT.y * Math.cos(-TILT));

  const SKIN = G('#d39d79'), SHADE = G('#ae7354'), DEEP = G('#764632');
  const HAIR_C = G('#7c4229'), HAIR_DARK = G('#4f2717'), HAIR_LIGHT = G('#9e5a36');
  const BEARD_C = G('#86482a'), BEARD_DARK = G('#562b19'), BEARD_LIGHT = G('#a8633c');
  const strand = Math.max(.1, px * .9);

  const FACE = new Path2D(FACE_D);
  lit(pen, FACE, SKIN, L);
  // Modelling: the temple and the cheek turn away from the moon.
  c.save(); c.clip(FACE);
  const cheek = c.createRadialGradient(2.5, -8, .5, 2.5, -8, 9.5);
  cheek.addColorStop(0, G('#a86c4e', .5)); cheek.addColorStop(1, G('#a86c4e', 0));
  c.fillStyle = cheek; c.fillRect(-10, -21, 24, 27);
  // Shadow under the brow, round the eyepiece.
  const socket = c.createRadialGradient(8.4, -9.2, .2, 8.4, -9.2, 2.6);
  socket.addColorStop(0, G('#8e5a40', .55)); socket.addColorStop(1, G('#8e5a40', 0));
  c.fillStyle = socket; c.fillRect(5, -13, 7, 7);
  // Under the jaw, the throat is in the beard's shadow.
  const throat = c.createLinearGradient(0, -1, 0, 5);
  throat.addColorStop(0, G('#6e4030', 0)); throat.addColorStop(1, G('#6e4030', .85));
  c.fillStyle = throat; c.fillRect(-10, -1, 22, 7);
  c.restore();

  // Hair: short, reddish brown, gone from the top of the forehead, thick at the back.
  const hair = curve(HAIR);
  c.fillStyle = HAIR_C; c.fill(hair);
  c.save(); c.clip(hair);
  const hairShade = c.createLinearGradient(-1, -21, -6, 2);
  hairShade.addColorStop(0, G('#9e5a36', .3)); hairShade.addColorStop(1, G('#3a1b0f', .7));
  c.fillStyle = hairShade; c.fillRect(-11, -22, 15, 26);
  for (const [pts, col, k] of [
    [[P(.6, -19.9), P(-3.6, -19.8), P(-7.4, -16.8)], HAIR_LIGHT, 1.4],
    [[P(1.4, -17.2), P(-2.6, -17.6), P(-6.8, -14.4), P(-8.4, -10)], HAIR_LIGHT, 1.1],
    [[P(-.4, -14.6), P(-4.4, -13.6), P(-6.8, -9), P(-7, -4)], HAIR_DARK, 1.2],
    [[P(-3.2, -10.8), P(-5.2, -6.6), P(-5.4, -1.6)], HAIR_DARK, 1],
    [[P(-8.8, -13.6), P(-9.6, -8.6), P(-8.4, -3.4), P(-6.8, .6)], HAIR_DARK, 1],
    [[P(-1.8, -12.6), P(-2.6, -9.4)], HAIR_DARK, .9],
  ] as [Pt[], string, number][]) line(pen, pts, col, strand * 1.5 * k);
  c.restore();
  // Rim of moonlight over the crown.
  rimEdge(pen, hair, [P(1.6, -20.7), P(-3.4, -21.1), P(-8, -18.3), P(-10.3, -12.4)]);

  // Beard: full and short, from the sideburn along the cheek to the moustache, round the chin.
  const beard = curve(BEARD);
  c.fillStyle = BEARD_C; c.fill(beard);
  c.save(); c.clip(beard);
  const beardShade = c.createLinearGradient(9, -6, 4, 6);
  beardShade.addColorStop(0, G('#a8633c', .45)); beardShade.addColorStop(1, G('#331709', .65));
  c.fillStyle = beardShade; c.fillRect(-2, -13, 15, 19);
  // Short curling strokes, in tufts that follow the jaw.
  const curl = G('#4a2414', .55), gleam = G('#b8744a', .5);
  for (let i = 0; i < 34; i++) {
    const t = i / 33;
    const x = -.4 + t * 11.6 + Math.sin(i * 2.3) * .6;
    const y = -9.8 + t * 6.6 + Math.cos(i * 1.7) * 1.6 + (i % 2) * 2.6;
    const len = 2 + Math.sin(i * 3.1) * .6;
    line(pen, [P(x, y), P(x + .7, y + len * .5), P(x + .3, y + len)], i % 3 ? curl : gleam, strand * (i % 3 ? 1.2 : 1));
  }
  // The moustache over the lip, combed down.
  line(pen, [P(10.5, -4.3), P(11.5, -3.5), P(11.3, -2.5)], BEARD_LIGHT, strand * 1.3);
  line(pen, [P(9.9, -4.6), P(10.8, -3.6), P(10.7, -2.6)], BEARD_DARK, strand);
  c.restore();
  rimEdge(pen, beard, [P(10.9, -4.4), P(11.9, -3.6), P(11.8, -2.6), P(11.1, -2.1), P(11.5, -1.1), P(12.4, .6), P(12.5, 2.6), P(11.4, 4.2)]);
  // Lower lip, just showing between moustache and beard.
  c.fillStyle = G('#a45848');
  c.fill(curve([P(10.95, -2.25), P(11.5, -2), P(11.45, -1.45), P(10.85, -1.35)]));

  // Nostril wing.
  c.strokeStyle = SHADE; c.lineWidth = Math.max(.13, px);
  c.beginPath(); c.moveTo(10.5, -5.6); c.bezierCurveTo(10.1, -5.2, 10.3, -4.55, 10.95, -4.45); c.stroke();
  c.strokeStyle = DEEP; c.lineWidth = Math.max(.11, px * .9);
  c.beginPath(); c.arc(11.35, -4.6, .3, Math.PI * .15, Math.PI * .9); c.stroke();
  // Two lines on the forehead: a man of forty-five, frowning at the sky.
  c.strokeStyle = G('#a96d4f', .6); c.lineWidth = Math.max(.09, px * .8);
  for (const y of [-14.8, -13.2]) { c.beginPath(); c.moveTo(6.6, y - .3); c.quadraticCurveTo(8, y - .8, 9.3, y + .1); c.stroke(); }
  // Brow: thick and reddish, drawn down over the eye at the eyepiece.
  const brow = curve([P(5.8, -11.3), P(7.8, -12.5), P(9.9, -11.9), P(10.25, -11.1), P(8.4, -11.4), P(6.2, -10.8)]);
  c.fillStyle = HAIR_DARK; c.fill(brow);
  c.save(); c.clip(brow);
  for (let i = 0; i < 6; i++) line(pen, [P(6.2 + i * .7, -11), P(6.7 + i * .7, -11.9)], HAIR_LIGHT, strand * .8);
  c.restore();

  // Ear.
  const ear = curve([P(.2, -10.8), P(-1.6, -10.6), P(-2.7, -9), P(-2.6, -6.6), P(-1.6, -4.4), P(-.2, -3.9), P(.5, -5.4), P(.6, -8.4)]);
  c.fillStyle = SKIN; c.fill(ear);
  c.save(); c.clip(ear);
  const earShade = c.createLinearGradient(.4, -8, -2.6, -7);
  earShade.addColorStop(0, G('#a86c4e', .6)); earShade.addColorStop(1, G('#a86c4e', 0));
  c.fillStyle = earShade; c.fill(ear);
  c.restore();
  c.strokeStyle = SHADE; c.lineWidth = Math.max(.13, px);
  c.stroke(curve([P(-.3, -9.7), P(-1.7, -9.2), P(-2, -7), P(-1.1, -5.3)], false));
  c.fillStyle = DEEP; c.beginPath(); c.ellipse(-.5, -7.2, .5, .85, .2, 0, TAU); c.fill();
  c.restore();
}

// ── The telescope: a tapered cylinder seen from the side, or swung round to face us ─────────
interface ScopeView { phi: number; k: number }
/** turn 0: the side view (orthographic); 1: looking straight into the objective (perspective). */
function scopeView(turn: number): ScopeView {
  const t = clamp01(turn);
  return { phi: SCOPE_YAW + (Math.PI / 2 - SCOPE_YAW) * t, k: t * t / 40 };
}
/** A point on the tube at axial distance a from the objective, radius r, angle psi round it. */
function proj(v: ScopeView, a: number, r: number, psi: number): Pt {
  const sp = Math.sin(v.phi), cp = Math.cos(v.phi), ss = Math.sin(psi), cs = Math.cos(psi);
  const X = -a * cp + r * ss * sp, Y = r * cs, Z = -a * sp - r * ss * cp;
  const f = 1 / (1 - Z * v.k);
  return P(X * f, Y * f);
}
function ring(v: ScopeView, a: number, r: number, from = 0, to = TAU, n = 48): Pt[] {
  const pts: Pt[] = [];
  for (let i = 0; i <= n; i++) pts.push(proj(v, a, r, from + (to - from) * i / n));
  return pts;
}
/** Convex hull (monotone chain): a frustum's outline is the hull of its two end circles. */
function hull(pts: Pt[]): Pt[] {
  const s = [...pts].sort((a, b) => a.x - b.x || a.y - b.y);
  const cross = (o: Pt, a: Pt, b: Pt) => (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
  const lower: Pt[] = [], upper: Pt[] = [];
  for (const p of s) { while (lower.length > 1 && cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) lower.pop(); lower.push(p); }
  for (const p of s.reverse()) { while (upper.length > 1 && cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) upper.pop(); upper.push(p); }
  return lower.slice(0, -1).concat(upper.slice(0, -1));
}
const frustum = (v: ScopeView, a0: number, r0: number, a1: number, r1: number) => poly(hull([...ring(v, a0, r0), ...ring(v, a1, r1)]));
const radiusAt = (a: number) => R_FRONT + (R_BACK - R_FRONT) * (a - CELL) / (BODY_END - CELL);

/** How round the objective looks for this `turn`: its width over its height. */
export const objectiveAspect = (turn: number) => Math.sin(scopeView(turn).phi);

/** The objective glass in figure space, as the camera sees it for this `turn`: the sky window. */
export function objectiveLens(turn: number, radius = OBJECTIVE_RADIUS): Path2D {
  const path = new Path2D();
  const o = GALILEO_OBJECTIVE;
  path.ellipse(o.x, o.y, radius * Math.sin(scopeView(turn).phi), radius, -SCOPE_ELEVATION, 0, TAU);
  return path;
}

function drawTelescope(pen: Pen, turn: number, open: number) {
  const { c, G, px } = pen;
  const v = scopeView(turn);
  const sp = Math.sin(v.phi);
  c.save();
  c.translate(GALILEO_OBJECTIVE.x, GALILEO_OBJECTIVE.y); c.rotate(-SCOPE_ELEVATION);
  // In this frame +x runs toward the objective (at 0), +y is below the tube, light from above.
  const L = P(0, -1);

  // Eyepiece: a short dark wooden draw tube, and the collar where it enters the main tube.
  lit(pen, frustum(v, BODY_END - .4, R_EYEPIECE, SCOPE_LENGTH, R_EYEPIECE * .95), G('#2a1c14'), L, .8);
  lit(pen, frustum(v, BODY_END - 1.2, R_BACK + .2, BODY_END, R_BACK + .2), G('#4a2a18'), L, .8);

  // The tube: red-brown leather, moonlit on top.
  const tube = frustum(v, CELL, R_FRONT, BODY_END - 1.2, radiusAt(BODY_END - 1.2));
  const leather = c.createLinearGradient(0, -R_FRONT, 0, R_FRONT);
  leather.addColorStop(0, G('#b4573a')); leather.addColorStop(.28, G('#8e3a24'));
  leather.addColorStop(.7, G('#64231a')); leather.addColorStop(1, G('#3c140e'));
  lit(pen, tube, leather, L);

  // Gold tooling: two fillets along the tube, and tooled bands of lozenges between double rules.
  const gold = G('#e0b04c'), goldDim = G('#b98a34', .85);
  const fine = Math.max(.045, px * .75);
  c.save(); c.clip(tube);
  for (const psi of [1.5 * Math.PI - .62, 1.5 * Math.PI + .62]) {
    const a = proj(v, CELL + 2.4, radiusAt(CELL + 2.4), psi), b = proj(v, BODY_END - 3, radiusAt(BODY_END - 3), psi);
    c.strokeStyle = goldDim; c.lineWidth = fine; c.beginPath(); c.moveTo(a.x, a.y); c.lineTo(b.x, b.y); c.stroke();
  }
  const visible = (a: number, r: number) => ring(v, a, r, Math.PI, TAU, 32);
  const stroke = (pts: Pt[], col: string, w: number) => {
    c.strokeStyle = col; c.lineWidth = w; c.beginPath();
    pts.forEach((q, i) => (i ? c.lineTo(q.x, q.y) : c.moveTo(q.x, q.y))); c.stroke();
  };
  for (const a0 of [3.4, 15, 28, 41, 54, 67, 80, 89.6]) {
    const r = radiusAt(a0);
    stroke(visible(a0, r), gold, fine * 1.3);
    stroke(visible(a0 + 1.3, r), gold, fine * 1.3);
    // A row of small lozenges round the band.
    if (r / px > 6) {
      c.fillStyle = goldDim;
      for (let i = 1; i < 12; i++) {
        const psi = Math.PI + Math.PI * i / 12;
        const u = proj(v, a0 + .2, r, psi), d = proj(v, a0 + 1.1, r, psi);
        const side = proj(v, a0 + .65, r, psi + .09), back = proj(v, a0 + .65, r, psi - .09);
        c.beginPath(); c.moveTo(u.x, u.y); c.lineTo(side.x, side.y); c.lineTo(d.x, d.y); c.lineTo(back.x, back.y); c.closePath(); c.fill();
      }
    }
  }
  // A seam of light along the top, where the leather catches the moon.
  stroke([proj(v, CELL + 1, R_FRONT * .98, 1.5 * Math.PI - 1.15), proj(v, BODY_END - 2, radiusAt(BODY_END - 2) * .98, 1.5 * Math.PI - 1.15)], G('#d88a62', .45), Math.max(.06, px));
  c.restore();

  // Objective cell: a wider ring of dark wood, edged in gold.
  const cell = frustum(v, 0, R_CELL, CELL, R_CELL);
  lit(pen, cell, G('#3f2617'), L, .9);
  c.save(); c.clip(cell);
  stroke(visible(CELL - .25, R_CELL), gold, fine * 1.6);
  c.restore();

  // Its front face: the ring of the cell, then the glass.
  c.save(); c.scale(sp, 1);
  const face = c.createRadialGradient(-.8, -1.2, .4, 0, 0, R_CELL);
  face.addColorStop(0, G('#5a3822')); face.addColorStop(1, G('#2c190f'));
  c.fillStyle = face; c.beginPath(); c.arc(0, 0, R_CELL, 0, TAU); c.fill();
  c.strokeStyle = gold; c.lineWidth = Math.max(.06, px * 1.1) / Math.max(sp, .2);
  c.beginPath(); c.arc(0, 0, R_CELL - .1, 0, TAU); c.stroke();
  c.fillStyle = G('#160d08'); c.beginPath(); c.arc(0, 0, OBJECTIVE_RADIUS + .28, 0, TAU); c.fill();
  // Glass: deep blue-black, a bright edge, and a faint reflection of the night.
  const glass = c.createRadialGradient(-.5, -.6, .1, 0, 0, OBJECTIVE_RADIUS);
  glass.addColorStop(0, '#1a2a44'); glass.addColorStop(.75, '#0b1424'); glass.addColorStop(1, '#16243a');
  c.fillStyle = glass; c.beginPath(); c.arc(0, 0, OBJECTIVE_RADIUS, 0, TAU); c.fill();
  const shine = 1 - clamp01(open * 1.6);
  if (shine > 0) {
    c.strokeStyle = `rgba(214,228,255,${.2 * shine})`; c.lineWidth = OBJECTIVE_RADIUS * .13;
    c.beginPath(); c.arc(0, 0, OBJECTIVE_RADIUS * .74, Math.PI * 1.08, Math.PI * 1.42); c.stroke();
    c.fillStyle = `rgba(236,244,255,${.45 * shine})`;
    c.beginPath(); c.arc(-OBJECTIVE_RADIUS * .42, -OBJECTIVE_RADIUS * .5, OBJECTIVE_RADIUS * .07, 0, TAU); c.fill();
  }
  c.strokeStyle = 'rgba(190,212,250,.55)'; c.lineWidth = Math.max(.05, px * 1.2) / Math.max(sp, .2);
  c.beginPath(); c.arc(0, 0, OBJECTIVE_RADIUS, 0, TAU); c.stroke();
  c.restore();
  c.restore();
}

// ── The near arm: the wide gown sleeve, the forearm raised, the hand under the tube ──────────
function drawArm(pen: Pen) {
  const { c, G, px } = pen;
  const r = radiusAt(GRIP_A);
  /** A point near the grip: u along the tube, w across it (positive below the axis). */
  const at = (u: number, w: number): Pt => P(GALILEO_HAND.x + SCOPE_AXIS.x * u + NORMAL.x * w, GALILEO_HAND.y + SCOPE_AXIS.y * u + NORMAL.y * w);
  // Wrist (back and front edge) and elbow: the forearm rises almost straight from the elbow.
  const wristBack = at(-4.6, r + 3.4), wristFront = at(-1.6, r + 6);
  const elbow = P(24.4, -153.4);
  const wrist = P((wristBack.x + wristFront.x) / 2, (wristBack.y + wristFront.y) / 2);
  const len = Math.hypot(wrist.x - elbow.x, wrist.y - elbow.y);
  const dir = P((wrist.x - elbow.x) / len, (wrist.y - elbow.y) / len), across = P(-dir.y, dir.x);
  const fore = (t: number, s: number): Pt => P(elbow.x + dir.x * t + across.x * s, elbow.y + dir.y * t + across.y * s);

  // The sleeve: wide, hanging from the raised upper arm in a deep fold below the elbow.
  const sleeve = curve([
    P(-7.6, -145.4), P(-1, -148.6), P(6, -151), P(13, -154), P(19, -157.8), fore(6.6, -3.9), fore(7.4, 0),
    fore(6.4, 4), P(31.4, -150), P(31.2, -140), P(29.6, -130), P(26.8, -121), P(24, -115.6), P(21.6, -118.4),
    P(19.6, -126), P(17.6, -134), P(14.6, -139.6), P(8, -139), P(1.5, -136), P(-4, -138.2),
  ]);
  const cloth = c.createLinearGradient(4, -158, 12, -124);
  cloth.addColorStop(0, G('#312c3a')); cloth.addColorStop(1, G('#14111a'));
  lit(pen, sleeve, cloth);
  c.save(); c.clip(sleeve);
  const fold = G('#09070c', .8), w = Math.max(.4, px);
  line(pen, [fore(2, -1.6), P(23.4, -142), P(23.6, -131), P(23.4, -120)], fold, w * 1.8);
  line(pen, [fore(2, 2.4), P(28.4, -144), P(27.8, -132)], fold, w * 1.3);
  line(pen, [P(17.6, -154), P(19.4, -144), P(20.4, -134)], fold, w * 1.2);
  line(pen, [P(9, -150.6), P(11, -145), P(12, -140)], fold, w);
  line(pen, [P(-2, -146.4), P(8, -150.4), P(18, -155.6)], G('#47414f', .55), w * .8);
  c.restore();
  // The mouth of the sleeve, dark inside, where the forearm comes out.
  c.fillStyle = G('#08060a');
  c.fill(curve([fore(5.4, -3.6), fore(7.2, 0), fore(5.6, 3.8), fore(4.2, 2.6), fore(4, -2.4)]));

  // Forearm, in the close dark sleeve of the doublet, with a small white cuff at the wrist.
  const forearm = curve([fore(5.6, -3), fore(14, -3.4), wristBack, wristFront, fore(14, 2.6), fore(5.6, 3)]);
  lit(pen, forearm, G('#29252f'));
  const cuff = poly([fore(len - .2, -2.5), fore(len - .2, 2.5), fore(len - 1.7, 2.7), fore(len - 1.7, -2.7)]);
  lit(pen, cuff, G('#e4ddcd'), LIGHT, .8);
  c.strokeStyle = G('#aaa292'); c.lineWidth = Math.max(.08, px * .8);
  c.beginPath(); const k0 = fore(len - .95, -2.6), k1 = fore(len - .95, 2.6); c.moveTo(k0.x, k0.y); c.lineTo(k1.x, k1.y); c.stroke();

  // The hand under the tube: four fingertips come over the top from the far side; the palm is
  // below; the thumb lies along the near side.
  const SKIN = G('#d29b77'), SHADE = G('#a26a4c'), NAIL = G('#e6c0a8');
  for (const [u, half, h] of [[-3.6, .5, .5], [-2.35, .58, .62], [-1.05, .6, .66], [.25, .56, .6]]) {
    const tip = curve([at(u - half, -r + .5), at(u - half * .95, -r - h * .55), at(u, -r - h), at(u + half * .95, -r - h * .55), at(u + half, -r + .5)]);
    lit(pen, tip, SKIN, LIGHT, .9);
    c.fillStyle = NAIL;
    c.fill(curve([at(u - half * .55, -r - h * .45), at(u, -r - h * .88), at(u + half * .55, -r - h * .45), at(u, -r - h * .3)]));
  }
  const hand = curve([
    wristBack, at(-4.9, r + 1.9), at(-4.1, r + .55), at(-2.6, r + .2), at(-.7, r + .05), at(1.2, r - .8),
    at(3, r - 1.8), at(4.3, r - 2.5), at(5, r - 2.3), at(5.1, r - 1.6), at(4, r - .75), at(2.5, r + .35),
    at(1.7, r + 1.5), at(.6, r + 3.2), at(-.6, r + 4.8), wristFront,
  ]);
  lit(pen, hand, SKIN);
  c.save(); c.clip(hand);
  const g0 = at(0, r - 1), g1 = at(-3, r + 5.2);
  const shade = c.createLinearGradient(g0.x, g0.y, g1.x, g1.y);
  shade.addColorStop(0, G('#d29b77', 0)); shade.addColorStop(1, G('#7e5038', .55));
  c.fillStyle = shade; c.fill(hand);
  c.restore();
  // Thumbnail, the thumb's crease and the line of the palm.
  c.fillStyle = NAIL;
  c.fill(curve([at(3.9, r - 2.2), at(4.8, r - 2.5), at(5, r - 2.05), at(4.1, r - 1.75)]));
  c.strokeStyle = SHADE; c.lineWidth = Math.max(.08, px * .9);
  c.stroke(curve([at(2.2, r - .55), at(2.6, r - 1.6)], false));
  c.stroke(curve([at(-3.8, r + 2), at(-2, r + 1.2), at(.1, r + 1.4)], false));
}
