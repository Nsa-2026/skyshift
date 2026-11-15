// What the composition filter (layer A3) draws and says: one marker per source, shaded squares
// for diffuse PAH glow, and plain words for every state and reason. The numbers come from
// composition.ts; nothing here measures anything, except two guards that need the plate's own
// catalogues (Gaia stars and known asteroids), applied in applyContext.
import { INDEX_IDS, MIN_DEPTH, type IndexId } from "./indices";
import type { Block, FeatureState, Reason, Source } from "./composition";

export type GroupId = "methane" | "ice" | "gas" | "pah";

export interface Group {
  id: GroupId;
  label: string;
  /**
   * Marker colour. Three ring colours and no more: blue, magenta and amber are the largest set that
   * stays apart for colour-blind readers when marks are scattered over a picture (checked pair by
   * pair: worst pair 13 in colour-blind ΔE, 19 in normal vision). Dust is a filled patch, so its
   * shape tells it from the rings. Every colour is at least 3:1 against black sky.
   */
  colour: string;
  bands: IndexId[];
  /** Where it is seen: a phrase that follows the count in the key ("3 stars · …"). */
  what: string;
}

export const GROUPS: readonly Group[] = [
  {
    id: "methane",
    label: "Methane",
    colour: "#d55181",
    bands: ["ch4_33", "ch4_16"],
    what: "a cold brown dwarf's air",
  },
  {
    id: "ice",
    label: "Ices",
    colour: "#3987e5",
    bands: ["h2o_ice_305", "co2_ice_427", "co_ice_467"],
    what: "seen through a cold, dark cloud",
  },
  {
    id: "gas",
    label: "Water vapour or CO gas",
    colour: "#c98500",
    bands: ["h2o_14", "h2o_19", "co_23"],
    what: "the air of a cool star or brown dwarf",
  },
  {
    id: "pah",
    label: "Glowing dust (PAH)",
    colour: "#199e70",
    bands: ["pah_33"],
    what: "dust lit by young stars",
  },
];

export const BANDS: Record<IndexId, { name: string; um: string; group: GroupId }> = {
  ch4_33: { name: "Methane", um: "3.3 µm", group: "methane" },
  ch4_16: { name: "Methane", um: "1.67 µm", group: "methane" },
  h2o_14: { name: "Water vapour", um: "1.4 µm", group: "gas" },
  h2o_19: { name: "Water vapour", um: "1.9 µm", group: "gas" },
  h2o_ice_305: { name: "Water ice", um: "3.05 µm", group: "ice" },
  co2_ice_427: { name: "CO₂ ice", um: "4.27 µm", group: "ice" },
  co_ice_467: { name: "CO ice", um: "4.67 µm", group: "ice" },
  co_23: { name: "Carbon monoxide gas", um: "2.3 µm", group: "gas" },
  pah_33: { name: "PAH dust", um: "3.3 µm", group: "pah" },
};

/** What else can look like each band. */
const LOOKALIKE: Record<IndexId, string> = {
  ch4_33: "PAH dust sits at the same 3.3 µm (as a bump, not a dip), and one bad frame can fake a dip.",
  ch4_16: "Water vapour's bands sit either side of 1.67 µm, so a cool star with strong water can mimic it.",
  h2o_14: "A star that changed brightness between the dates of the two sides of the band.",
  h2o_19: "A star that changed brightness between the dates of the two sides of the band.",
  co_23: "The map has no data at 2.40–2.45 µm, so the carbon monoxide band rests on few frames.",
  h2o_ice_305: "Water vapour in red dwarfs absorbs at 2.7–3.2 µm too.",
  co2_ice_427: "",
  co_ice_467: "Carbon monoxide gas (in cool stars, or around young stars) absorbs at 4.67 µm too.",
  pah_33: "Methane absorbs at the same wavelength, and the band is one frame wide in this map.",
};

/** What else could look like the molecules found on this source, each said once. */
export function lookalikes(s: Source): string[] {
  const out: string[] = [];
  for (const g of GROUPS) for (const id of g.bands) if (s.bands[id].state === "found" && LOOKALIKE[id] && !out.includes(LOOKALIKE[id])) out.push(LOOKALIKE[id]);
  return out;
}

const RANK: Record<FeatureState, number> = { found: 3, possible: 2, none: 1, cant: 0 };

/** A source with what the plate's catalogues add. */
export interface Marked extends Source {
  /** Gaia G of a star within 10″, if any. */
  gaiaG?: number | null;
  /** A known asteroid that passed within 2 pixels on one of the plate's dates. */
  knownName?: string | null;
}

/** Best state among a group's bands. */
export function groupState(s: Source, g: Group): FeatureState {
  let best: FeatureState = "cant";
  for (const id of g.bands) if (RANK[s.bands[id].state] > RANK[best]) best = s.bands[id].state;
  return best;
}

export interface Marker {
  state: FeatureState;
  /** The groups found (or, when none is found, the possible ones). */
  groups: Group[];
  /** Largest depth among those groups' bands in that state, for the ring's weight. */
  depth: number;
}

export function markerOf(s: Source): Marker {
  const by = (st: FeatureState) => GROUPS.filter((g) => groupState(s, g) === st);
  for (const st of ["found", "possible"] as const) {
    const groups = by(st);
    if (!groups.length) continue;
    let depth = 0;
    for (const g of groups) for (const id of g.bands) if (s.bands[id].state === st) depth = Math.max(depth, s.bands[id].depth);
    return { state: st, groups, depth };
  }
  return { state: by("none").length ? "none" : "cant", groups: [], depth: 0 };
}

// ---- guards that need the plate's catalogues ---------------------------------------------------
/** A methane dwarf brighter than this in Gaia's visible band would have to be closer than any known star. */
export const GAIA_STAR_MAX_G = 17;
/** Gaia match radius, pixels (10″ at the cube's 12.9″ pixels: the peak pixel plus position error). */
export const GAIA_MATCH_PX = 0.8;
/** A known asteroid this close (pixels) on one date may have put light into some frames. */
export const KNOWN_MATCH_PX = 2;
/** Asteroids fainter than this (V) are far below what one cube frame can see. */
export const KNOWN_MAX_V = 20;

export interface Context {
  /** Gaia stars at the map's date, in field pixels. */
  gaia: { x: number; y: number; g: number | null }[];
  /** Known solar-system objects at the plate's dates, in field pixels. */
  known: { x: number; y: number; v: number | null; name: string }[];
  /** The plate's own target, when it is a known variable star or a solar-system body. */
  target?: { x: number; y: number; is: "variable" | "moving"; name: string } | null;
}

/** The plate's target is the source within this many pixels of its position. */
export const TARGET_MATCH_PX = 1.5;

/**
 * Two of the pipeline's guards the cube alone can't run. A found feature is never removed: it
 * becomes "possible" with the reason.
 * - Methane on a source Gaia sees brighter than G = 17: an ordinary star, not a methane dwarf
 *   (the pipeline's LABEL_CH4_MIN_G is 19.5; Epsilon Indi B, 3.6 pc away, has G = 18.0).
 * - Any feature on a source a known asteroid passed over: its light is in some frames only.
 * - Any feature on the plate's own target when it is a known variable star (each wavelength was
 *   seen on a different date) or a solar-system body.
 */
export function applyContext(sources: Source[], ctx: Context): Marked[] {
  return sources.map((s) => {
    let gaiaG: number | null = null;
    let best = GAIA_MATCH_PX;
    for (const g of ctx.gaia) {
      const d = Math.hypot(g.x - s.x, g.y - s.y);
      if (d <= best && g.g != null) {
        best = d;
        gaiaG = g.g;
      }
    }
    const t = ctx.target && Math.hypot(ctx.target.x - s.x, ctx.target.y - s.y) <= TARGET_MATCH_PX ? ctx.target : null;
    const known =
      ctx.known.find((k) => (k.v == null || k.v <= KNOWN_MAX_V) && Math.hypot(k.x - s.x, k.y - s.y) <= KNOWN_MATCH_PX) ??
      (t?.is === "moving" ? { name: t.name } : undefined);
    const bands = { ...s.bands };
    for (const id of INDEX_IDS) {
      const b = bands[id];
      if (b.state !== "found" && b.state !== "possible") continue;
      const extra: Reason[] = [];
      if (known) extra.push("moving");
      if (t?.is === "variable") extra.push("variable");
      if (BANDS[id].group === "methane" && gaiaG != null && gaiaG < GAIA_STAR_MAX_G) extra.push("gaia_star");
      if (extra.length) bands[id] = { ...b, state: "possible", reasons: [...b.reasons, ...extra] };
    }
    return { ...s, bands, gaiaG, knownName: known?.name ?? null };
  });
}

// ---- words ---------------------------------------------------------------------------------------
export function reasonText(r: Reason, group: GroupId, s?: Marked): string {
  switch (r) {
    case "bright":
      return "too bright, so the camera's pixels overflowed and NASA's map left those readings out";
    case "blend":
      return "another source sits within 2 pixels (26″), so their light is mixed";
    case "glare":
      return "it sits in the glare of a brighter star or a bright cloud (the light around it is more than half its own), so the sign may belong to that light and not to this source";
    case "erratic":
      return "its brightness jumps between neighbouring wavelengths more than noise allows (a variable star, a moving object or damaged data)";
    case "faint":
      return "too faint around this band to tell";
    case "nodata":
      return "the map has no usable data in this band here";
    case "nocal":
      return "too few ordinary stars in this field to set this band's zero point";
    case "one_frame":
      return "it rests on a single frame of the map, so one bad reading could fake it";
    case "not_red":
      return group === "methane"
        ? "it isn't red enough for a cold brown dwarf (4.6 µm against 1.2 µm)"
        : "it isn't reddened the way a star behind an icy cloud is (4.6 µm against 1.2 µm)";
    case "no_second_band":
      return group === "methane"
        ? "no second band agrees (methane's other band, or water)"
        : "water's other band (1.4 and 1.9 µm come together) doesn't show it";
    case "vapour":
      return "it also shows water vapour, so this is a cool star's own atmosphere, not ice";
    case "co_gas":
      return "it also shows CO gas at 2.3 µm, so this is a cool star's own CO, not ice";
    case "gaia_star":
      return `Gaia sees an ordinary star here (G = ${s?.gaiaG?.toFixed(1) ?? "?"}), too bright in visible light for a methane dwarf`;
    case "moving":
      return `a solar-system body${s?.knownName ? ` (${s.knownName})` : ""} was within 26″ on one of the dates, so only some frames hold its light`;
    case "ice":
      return "there is a dip here, but it is the edge of water ice's wide band, which is deeper: ice, not methane";
    case "ice_maybe":
      return "water ice's wide band reaches 3.3 µm too, and here ice shows (or couldn't be ruled out), so this dip isn't counted as methane";
    case "variable":
      return "it is a known variable star, and each wavelength was observed on a different date, so a brighter or fainter moment can look like a band";
  }
}

const pct = (v: number) => `${Math.round(100 * v)}%`;
/** A depth as a share of the light missing. Noise can push a fully dark band past 100%: say "all". */
const missing = (depth: number) => (depth >= 0.995 ? "all of the light is missing" : `${pct(depth)} of the light is missing`);

export function stateWord(st: FeatureState): string {
  return st === "found" ? "found" : st === "possible" ? "possible, not confirmed" : st === "none" ? "measured, not there" : "can't tell";
}

/** A band's name inside a sentence: "methane", "water ice", "CO₂ ice". */
const inText = (name: string) => (/^(CO|PAH)/.test(name) ? name : name[0].toLowerCase() + name.slice(1));

function list(words: string[]): string {
  return words.length <= 1 ? (words[0] ?? "") : `${words.slice(0, -1).join(", ")} and ${words[words.length - 1]}`;
}

/** The molecules of a source in one state, each named once, in the key's order. */
export function moleculeNames(s: Source, state: "found" | "possible"): string[] {
  const names: string[] = [];
  for (const g of GROUPS) for (const id of g.bands) if (s.bands[id].state === state && !names.includes(BANDS[id].name)) names.push(BANDS[id].name);
  return names;
}

/** The short verdict on one source, without numbers: "Methane and water vapour found." */
export function summarise(s: Marked): string {
  const m = markerOf(s);
  const cap = (t: string) => t[0].toUpperCase() + t.slice(1);
  if (m.state === "found") return `${cap(list(moleculeNames(s, "found").map(inText)))} found.`;
  if (m.state === "possible") return `A hint of ${list(moleculeNames(s, "possible").map(inText))}, not confirmed.`;
  if (m.state === "none") return "Measured: none of these molecules shows.";
  return `Can't tell: ${whyCant(s)}.`;
}

/** One line for the readout under the picture. `hint`: end with what a click does. */
export function describeSource(s: Marked, hint = true): string {
  const m = markerOf(s);
  if (m.state === "found" || m.state === "possible") {
    const parts: string[] = [];
    for (const g of m.groups) {
      for (const id of g.bands) {
        const b = s.bands[id];
        if (b.state !== m.state) continue;
        const how = id === "pah_33" ? `${pct(b.depth)} extra light` : missing(b.depth);
        parts.push(`${BANDS[id].name} at ${BANDS[id].um}: ${how} (give or take ${pct(b.err)}; ${b.sig.toFixed(1)} times the noise)`);
      }
    }
    return `${m.state === "found" ? "Found" : "Possible, not confirmed"}: ${parts.join(" · ")}.${hint ? " Click for the checks." : ""}`;
  }
  if (m.state === "none") {
    const n = INDEX_IDS.filter((id) => s.bands[id].state === "none").length;
    return `Measured in ${n} of ${INDEX_IDS.length} bands: none of these molecules shows.${hint ? " Click for the numbers." : ""}`;
  }
  return `Can't tell: ${whyCant(s)}.`;
}

/** The first reason shared by the bands that couldn't be measured. */
function whyCant(s: Marked): string {
  const counts = new Map<Reason, number>();
  for (const id of INDEX_IDS) for (const r of s.bands[id].reasons) counts.set(r, (counts.get(r) ?? 0) + 1);
  const order: Reason[] = ["bright", "glare", "blend", "erratic", "faint", "nodata", "nocal"];
  const r = order.find((x) => (counts.get(x) ?? 0) > 0) ?? "faint";
  return reasonText(r, "methane", s);
}

export interface BandRow {
  id: IndexId;
  name: string;
  um: string;
  state: FeatureState;
  /** "68% ± 3% (19.7σ)", or "" when unmeasured. */
  value: string;
  needs: string;
  reasons: string[];
  /** Why a found band counts although it is one frame wide, or "". */
  note: string;
}

/** Every band of one source, for the details panel. */
export function bandRows(s: Marked): BandRow[] {
  return INDEX_IDS.map((id) => {
    const b = s.bands[id];
    const measured = Number.isFinite(b.depth) && Number.isFinite(b.err);
    return {
      id,
      name: BANDS[id].name,
      um: BANDS[id].um,
      state: b.state,
      value: measured ? `${pct(b.depth)} ± ${pct(b.err)} (${b.sig.toFixed(1)}σ)` : "",
      needs: `≥ ${pct(MIN_DEPTH[id])} and ≥ 5σ`,
      reasons: b.reasons.map((r) => reasonText(r, BANDS[id].group, s)),
      note: b.confirmedBy
        ? `one frame wide in this map; counted because ${BANDS[b.confirmedBy].name.toLowerCase()} is found in the same source`
        : b.joint && b.state === "found"
          ? `counted with water's other bands: the three together (1.4, 1.9 and 2.7 µm, wide windows) are ${pct(b.joint.depth)} ± ${pct(b.joint.err)} deep (${b.joint.sig.toFixed(1)}σ)`
          : "",
    };
  });
}

// ---- counts --------------------------------------------------------------------------------------
export interface Window {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

const inside = (w: Window, x: number, y: number) => x >= w.x0 && x <= w.x1 && y >= w.y0 && y <= w.y1;

export interface Tally {
  sources: number;
  found: number;
  possible: number;
  none: number;
  cant: number;
  /** Per group: sources with it found / possible. */
  groups: Record<GroupId, { found: number; possible: number }>;
  /** Diffuse glow at the finest block size: squares found, possible, measured and unmeasurable. */
  glow: { found: number; possible: number; none: number; cant: number };
  /** Share of the window (0–1) where the glow could be measured at some square size. */
  glowChecked: number;
}

export function tally(sources: Marked[], blocks: Block[], w: Window): Tally {
  const t: Tally = {
    sources: 0,
    found: 0,
    possible: 0,
    none: 0,
    cant: 0,
    groups: { methane: { found: 0, possible: 0 }, ice: { found: 0, possible: 0 }, gas: { found: 0, possible: 0 }, pah: { found: 0, possible: 0 } },
    glow: { found: 0, possible: 0, none: 0, cant: 0 },
    glowChecked: 0,
  };
  for (const s of sources) {
    if (!inside(w, s.x, s.y)) continue;
    t.sources++;
    t[markerOf(s).state]++;
    for (const g of GROUPS) {
      const st = groupState(s, g);
      if (st === "found" || st === "possible") t.groups[g.id][st]++;
    }
  }
  const finest = Math.min(...blocks.map((b) => b.size));
  for (const b of blocks) {
    if (!inside(w, b.x + b.size / 2, b.y + b.size / 2)) continue;
    if (b.state === "found" || b.state === "possible") t.glow[b.state]++;
    else if (b.size === finest) t.glow[b.state]++;
  }
  const W = Math.ceil(w.x1 - w.x0);
  const H = Math.ceil(w.y1 - w.y0);
  const seen = new Uint8Array(Math.max(0, W * H));
  for (const b of blocks) {
    if (b.state === "cant") continue;
    const x0 = Math.max(0, b.x - w.x0);
    const x1 = Math.min(W, b.x + b.size - w.x0);
    for (let y = Math.max(0, b.y - w.y0); y < Math.min(H, b.y + b.size - w.y0); y++) if (x1 > x0) seen.fill(1, y * W + x0, y * W + x1);
  }
  let k = 0;
  for (let i = 0; i < seen.length; i++) k += seen[i];
  t.glowChecked = seen.length ? k / seen.length : 0;
  return t;
}

/** The source whose peak is nearest (px, py), within `max` pixels, or −1. */
export function nearestSource(sources: Marked[], px: number, py: number, max = 2.5): number {
  let best = -1;
  let d2 = max * max;
  sources.forEach((s, k) => {
    const d = (s.x + 0.5 - px) ** 2 + (s.y + 0.5 - py) ** 2;
    if (d <= d2) {
      d2 = d;
      best = k;
    }
  });
  return best;
}

/** The finest glow block (found or possible) containing (px, py), or null. */
export function glowAt(blocks: Block[], px: number, py: number): Block | null {
  let best: Block | null = null;
  for (const b of blocks) {
    if (b.state !== "found" && b.state !== "possible") continue;
    if (px >= b.x && px < b.x + b.size && py >= b.y && py < b.y + b.size && (!best || b.size < best.size)) best = b;
  }
  return best;
}

/** A sky brightness in MJy/sr with two significant figures or so. */
export function mjy(v: number): string {
  const a = Math.abs(v);
  return a < 0.1 ? v.toFixed(3) : a < 1 ? v.toFixed(2) : v.toFixed(1);
}

export function describeBlock(b: Block, _pixelArcsec?: number): string {
  const v = `the sky is ${mjy(b.depth)} MJy/sr brighter at 3.3 µm than at the colours either side (${(b.depth / b.err).toFixed(0)} times the noise)`;
  if (b.state === "found") return `Glowing dust (PAH) here: ${v}.`;
  return b.reasons.includes("one_frame")
    ? `A hint of glowing dust (PAH) here: ${v}, but nothing beside it confirms it.`
    : `A hint of glowing dust (PAH) here: ${v}. Too faint to be sure.`;
}

/**
 * The brightness range of the glow cells found inside the window (faintest, and the 95th
 * percentile, so one bright knot doesn't flatten the rest), for the map's shading and its key.
 */
export function glowRange(blocks: Block[], w: Window): { lo: number; hi: number } | null {
  const v = blocks.filter((b) => b.state === "found" && inside(w, b.x + b.size / 2, b.y + b.size / 2)).map((b) => b.depth).sort((p, q) => p - q);
  if (!v.length) return null;
  return { lo: v[0], hi: v[Math.min(v.length - 1, Math.floor(0.95 * v.length))] };
}

/**
 * Fill of the glow map: a lighter step of the PAH green, so the shade adds light to the dark sky
 * instead of dimming the stars under it (the key's swatch is drawn with the same fill).
 */
export const GLOW_FILL = "#3fe0a2";
/** Opacity of a found glow cell: from GLOW_OPACITY[0] at the faintest to GLOW_OPACITY[1] at the brightest. */
export const GLOW_OPACITY: [number, number] = [0.14, 0.4];
/** Opacity of a hint cell: clearly under the faintest found cell. */
export const GLOW_HINT_OPACITY = 0.06;

// ---- drawing -------------------------------------------------------------------------------------
export interface DrawOptions {
  /** Draw the possible, not confirmed signs (dashed). */
  hints: boolean;
  /** Draw every other source that was checked: a thin ring for nothing found, a cross for can't tell. */
  all: boolean;
  selected: number;
  /** Screen pixels per field pixel, for the size of the name labels. */
  scale?: number;
}

/** Ring radius in pixels: the 3 × 3 pixels the light was measured in. */
export const RING_PX = 1.9;
/** A second molecule on the same source is a second ring, this much wider. */
export const RING_STEP_PX = 0.8;
/** Found rings are named on the picture when there are at most this many. */
export const MAX_LABELS = 4;

const num = (v: number) => String(Math.round(v * 100) / 100);

/** One ring per group, the first innermost. */
function rings(cx: number, cy: number, colours: string[], cls: string): string {
  return colours.map((c, k) => `<circle class="${cls}" cx="${cx}" cy="${cy}" r="${num(RING_PX + k * RING_STEP_PX)}" style="stroke:${c}" />`).join("");
}

/**
 * SVG (viewBox in field pixels) of what was found inside the window: shaded cells where dust glows,
 * a ring on each source with a molecule, and their names when there are few. Hints and everything
 * else that was checked are drawn only when asked for, so the picture stays readable.
 */
export function compositionSvg(sources: Marked[], blocks: Block[], w: Window, opt: DrawOptions): string {
  const parts: string[] = [];
  const pah = GLOW_FILL;
  const shown = blocks.filter((b) => inside(w, b.x + b.size / 2, b.y + b.size / 2));
  // The dust glow: one shaded cell per measured cell, stronger green where the glow is brighter.
  const range = glowRange(blocks, w);
  for (const b of shown) {
    let opacity = 0;
    if (b.state === "found" && range) {
      const t = range.hi > range.lo ? Math.min(1, Math.max(0, (b.depth - range.lo) / (range.hi - range.lo))) : 0.5;
      opacity = GLOW_OPACITY[0] + t * (GLOW_OPACITY[1] - GLOW_OPACITY[0]);
    } else if (b.state === "possible" && opt.hints) opacity = GLOW_HINT_OPACITY;
    if (opacity) {
      parts.push(`<rect class="cp-glow${b.state === "possible" ? " cp-glow-maybe" : ""}" x="${b.x}" y="${b.y}" width="${b.size}" height="${b.size}" style="fill:${pah};fill-opacity:${num(opacity)}" />`);
    }
  }
  const marks = sources.map((s, k) => ({ s, k, m: markerOf(s) })).filter((e) => inside(w, e.s.x, e.s.y));
  const found = marks.filter((e) => e.m.state === "found");
  const order: FeatureState[] = ["cant", "none", "possible", "found"];
  for (const st of order) {
    if ((st === "possible" && !opt.hints) || ((st === "none" || st === "cant") && !opt.all)) continue;
    for (const { s, m } of marks) {
      if (m.state !== st) continue;
      const cx = s.x + 0.5;
      const cy = s.y + 0.5;
      if (st === "found") parts.push(rings(cx, cy, m.groups.map((g) => g.colour), "cp-ring"));
      else if (st === "possible") parts.push(rings(cx, cy, m.groups.map((g) => g.colour), "cp-ring cp-maybe"));
      else if (st === "none") parts.push(`<circle class="cp-none" cx="${cx}" cy="${cy}" r="${RING_PX}" />`);
      else {
        const a = RING_PX * 0.45;
        parts.push(`<path class="cp-cant" d="M${num(cx - a)},${num(cy - a)}l${num(2 * a)},${num(2 * a)}M${num(cx + a)},${num(cy - a)}l${num(-2 * a)},${num(2 * a)}" />`);
      }
    }
  }
  // The picked source's ring is drawn whatever its state, so a click always shows where it landed.
  const sel = marks.find((e) => e.k === opt.selected);
  if (sel) {
    const outer = RING_PX + Math.max(0, sel.m.groups.length - 1) * RING_STEP_PX;
    parts.push(`<circle class="cp-picked" cx="${sel.s.x + 0.5}" cy="${sel.s.y + 0.5}" r="${num(outer + 1)}" />`);
  }
  if (found.length && found.length <= MAX_LABELS) {
    const scale = opt.scale && opt.scale > 0 ? opt.scale : 5;
    const size = 13 / scale;
    for (const { s, m } of found) {
      const cx = s.x + 0.5;
      const cy = s.y + 0.5;
      // Up and to the right of the ring, clear of the cross at the centre; mirrored near an edge.
      const reach = 0.75 * (RING_PX + (m.groups.length - 1) * RING_STEP_PX) + 3 / scale;
      const left = cx - w.x0 > 0.62 * (w.x1 - w.x0);
      const below = cy - w.y0 < 0.1 * (w.y1 - w.y0);
      const names = moleculeNames(s, "found").map((t, k) => (k ? inText(t) : t));
      parts.push(
        `<text class="cp-label" x="${num(left ? cx - reach : cx + reach)}" y="${num(below ? cy + reach + size * 0.8 : cy - reach)}" font-size="${num(size)}"${left ? ' text-anchor="end"' : ""}>${names.join(" + ")}</text>`,
      );
    }
  }
  return parts.join("");
}
