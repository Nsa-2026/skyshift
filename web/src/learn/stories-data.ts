// Stories in the sky (Learn → #/learn/stories): data → text. Pure functions over the Guxing
// library and its NASA bundle in web/public/guxing (by 春。 and Codex, #214; credits in its
// NOTICE.md), so every sentence the page shows can be tested without a browser.
// Nothing here invents history: records, years and positions come from guxing-nasa.js; lodges,
// asterisms and directions are computed by guxing.js from those positions.
import type { Det, SkyPos } from "../api/types";
import { formatRaDec } from "../app/astro";
import { featuredNear } from "../app/state";

// ---- the library's shapes (guxing.js, guxing-nasa.js) --------------------------------------
export interface Mansion {
  index: number;
  zh: string;
  zh_short: string;
  pinyin: string;
  en: string;
  du: number;
  deg: number;
}
export interface Near {
  asterism_zh: string;
  asterism_en: string;
  star_hip: number;
  star_zh: string;
  star_en: string;
  sep_deg: number;
  direction_zh: string;
}
export interface GuxingApi {
  /** [zh, pinyin, en, determinative HIP, pmra, pmdec] for the 28 lodges. */
  mansions: [string, string, string, number, number, number][];
  asterisms: { zh: string; en: string }[];
  mansion(ra: number, dec: number, epoch?: number): Mansion;
  nearest(ra: number, dec: number, k?: number, maxDeg?: number): Near[];
  distanceToAsterism(ra: number, dec: number, nameZh: string): { sep_deg: number; star_hip: number; direction_zh: string } | null;
  starName(hip: number): [string | null, string];
}
export interface GuestStar {
  id: string;
  year: number;
  remnant: string;
  ra: number;
  dec: number;
  record_zh: string;
  record_en: string;
  source: string;
  expect_mansion: string | null;
  expect_asterism: string | null;
  expect_direction: string | null;
  source_checked: boolean;
}
export interface Remnant {
  id: string;
  remnant: string;
  diameter_arcmin: number;
  seen_in_spherex: string;
  ra: number;
  dec: number;
  image: string;
  field_deg: number;
  rgb: string;
  source: string;
}
export interface PlanetEvent {
  type: string;
  body: string;
  body_zh: string;
  body_en: string;
  time: string;
  ra: number;
  dec: number;
  star_zh?: string;
  star_en?: string;
  min_sep_deg?: number;
  mansion?: string;
  from_mansion?: string;
  then?: string;
}
export interface NasaBundle {
  guest_stars: { about: string; items: GuestStar[] };
  spherex_remnants: { source: string; items: Remnant[] };
  planets: {
    source: string;
    frame: string;
    start: string;
    end: string;
    star_limit_mag: number;
    moon_star_limit_mag: number;
    events: PlanetEvent[];
  };
}

/** The asterism distance below which the library calls a spot "near" a star (Guxing.constants.NEAR_DEG). */
export const NEAR_DEG = 3;

// ---- words ---------------------------------------------------------------------------------
export function esc(s: unknown): string {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}
/** Chinese text inside English prose, marked for screen readers and fonts. */
export const zh = (s: string): string => `<span lang="zh">${esc(s)}</span>`;

const DIR: Record<string, string> = { 东: "east", 西: "west", 南: "south", 北: "north" };
/** "东南" → "south-east": north or south first, as English says it. */
export function directionWords(dz: string): string {
  const chars = [...dz];
  const ns = chars.filter((c) => c === "南" || c === "北").map((c) => DIR[c]);
  const ew = chars.filter((c) => c === "东" || c === "西").map((c) => DIR[c]);
  return [...ns, ...ew].join("-");
}

/** "氐" or "氐宿" → "Root Lodge" (the library's English names). */
export function lodgeEn(gx: GuxingApi, name: string): string {
  const short = name.replace(/宿$/, "");
  const m = gx.mansions.find((x) => x[0] === short);
  return m ? `${m[2]} Lodge` : name;
}
/** "the Root Lodge (氐宿)" */
export const lodgeHtml = (gx: GuxingApi, name: string): string =>
  `the ${esc(lodgeEn(gx, name))} (${zh(name.endsWith("宿") ? name : `${name}宿`)})`;

export function asterismEn(gx: GuxingApi, nameZh: string): string {
  return gx.asterisms.find((a) => a.zh === nameZh)?.en ?? nameZh;
}

/** English names of the histories the records come from (keys as guxing-nasa.js gives them). */
const SOURCES: Record<string, string> = {
  "《后汉书·天文志》": "Book of the Later Han, Treatise on Astronomy",
  "《宋史·天文志》": "History of Song, Treatise on Astronomy",
  "《宋会要》": "Song Huiyao",
  "《宋史》《文献通考》": "History of Song and Wenxian Tongkao",
  "《明实录》": "Veritable Records of the Ming",
};
/** "《宋会要》" → "Song Huiyao (宋会要)"; a trailing "(via …)" is kept in English. */
export function sourceHtml(source: string): string {
  const m = /^(.*?)\s*\((via [^)]*)\)\s*$/.exec(source);
  const title = (m ? m[1] : source).trim();
  const via = m ? `, ${esc(m[2])}` : "";
  const en = SOURCES[title];
  const plain = title.replace(/[《》]/g, " ").trim().replace(/\s+/g, ", ");
  return en ? `${esc(en)} (${zh(plain)})${via}` : `${zh(title)}${via}`;
}

export const yearText = (y: number): string => (y <= 0 ? `${1 - y} BC` : `AD ${y}`);
export const snName = (id: string): string => id.replace(/^SN(\d+)$/, "SN $1");

// ---- guest stars: does the record fit the position? ----------------------------------------
export interface Check {
  /** true: matches the record; false: differs; null: information, nothing to compare. */
  ok: boolean | null;
  html: string;
}

export function guestChecks(gx: GuxingApi, g: GuestStar): Check[] {
  const out: Check[] = [];
  const then = gx.mansion(g.ra, g.dec, g.year);
  const where = `in ${esc(yearText(g.year))} the remnant's position lay in ${lodgeHtml(gx, then.zh)}`;
  if (g.expect_mansion) {
    const ok = then.zh_short === g.expect_mansion.replace(/宿$/, "");
    out.push({
      ok,
      html: ok ? `The record names ${lodgeHtml(gx, g.expect_mansion)}, and ${where}.` : `The record names ${lodgeHtml(gx, g.expect_mansion)}, but ${where}.`,
    });
  } else {
    out.push({ ok: null, html: `${where[0].toUpperCase()}${where.slice(1)}.` });
  }
  if (g.expect_asterism) {
    const named = `${esc(asterismEn(gx, g.expect_asterism))} (${zh(g.expect_asterism)})`;
    const d = gx.distanceToAsterism(g.ra, g.dec, g.expect_asterism);
    if (!d) {
      out.push({ ok: null, html: `The record names ${named}, which this star map doesn't have.` });
      return out;
    }
    const [starZh, starEn] = gx.starName(d.star_hip);
    const star = `${esc(starEn)}${starZh ? ` (${zh(starZh)})` : ""}`;
    const single = starZh === g.expect_asterism; // an asterism of one star
    if (d.sep_deg <= NEAR_DEG) {
      out.push({
        ok: true,
        html: single
          ? `The record names ${named}, a single star ${d.sep_deg.toFixed(1)}° from the remnant.`
          : `The record names ${named}: its nearest star, ${star}, is ${d.sep_deg.toFixed(1)}° from the remnant.`,
      });
    } else {
      const n0 = gx.nearest(g.ra, g.dec, 1)[0];
      out.push({
        ok: false,
        html:
          `The record names ${named}, but its nearest star, ${star}, is ${d.sep_deg.toFixed(1)}° away.` +
          (n0 ? ` The closest asterism in this map is ${esc(n0.asterism_en)} (${zh(n0.asterism_zh)}), ${n0.sep_deg.toFixed(1)}° away.` : ""),
      });
    }
    if (g.expect_direction) {
      const ok = [...g.expect_direction].every((c) => d.direction_zh.includes(c));
      const said = directionWords(g.expect_direction);
      const got = directionWords(d.direction_zh);
      out.push({
        ok,
        html: ok
          ? `It lies ${esc(got)} of ${star}, as the record says.`
          : `The record says ${esc(said)}; the remnant lies ${esc(got)} of ${star}.`,
      });
    }
  }
  return out;
}

/** "R = 3.8-5.0 µm, G = 1.5-2.4 µm, B = 0.75-1.1 µm (SPHEREx), …" → "Red 3.8–5.0 µm, green 1.5–2.4 µm, blue 0.75–1.1 µm." */
export function colourText(rgb: string): string {
  const band = (k: string) => new RegExp(`${k} = ([\\d.]+)-([\\d.]+) µm`).exec(rgb);
  const r = band("R");
  const g = band("G");
  const b = band("B");
  if (!r || !g || !b) return rgb;
  return `Red ${r[1]}–${r[2]} µm, green ${g[1]}–${g[2]} µm, blue ${b[1]}–${b[2]} µm.`;
}

/** The picture's caption, from the remnant's own fields. */
export function remnantCaption(r: Remnant): string {
  const field = r.field_deg >= 1 ? `${r.field_deg}°` : `${Math.round(r.field_deg * 60)}′`;
  const flip = /north up, east left/.test(r.rgb) ? " North up, east left." : "";
  return `SPHEREx, ${field} across.${flip} ${colourText(r.rgb)} The circle shows the remnant's size, ${r.diameter_arcmin}′ across.`;
}

// ---- links into SkyShift ----------------------------------------------------------------------
export interface BlinkAt extends SkyPos {
  clock: "months";
  fov: 5 | 15;
  det: Det;
}
/** The Months clock at 15′: the widest field, so the remnant's centre and its stars are in view. */
export const blinkAt = (pos: SkyPos): BlinkAt => ({ ra: pos.ra, dec: pos.dec, clock: "months", fov: 15, det: 2 });

/** The Blink page at a spot: a featured target's prepared frames when the spot is in its field. */
export function blinkHref(at: BlinkAt): string {
  const near = featuredNear(at);
  if (near) return `?t=${encodeURIComponent(near.slug)}#/explore`;
  const p = new URLSearchParams({ ra: at.ra.toFixed(5), dec: at.dec.toFixed(5), clock: at.clock, fov: String(at.fov), det: String(at.det) });
  return `?${p}#/explore`;
}
/** The Sky map (atlas) at a spot, fovDeg across. */
export const skyHref = (pos: SkyPos, fovDeg: number): string => `#/sky/${pos.ra.toFixed(5)},${pos.dec.toFixed(5)},${fovDeg}`;

// ---- around a spot ------------------------------------------------------------------------------
export interface Spot extends SkyPos {
  /** Route argument: a featured slug, a remnant id, or "<ra>,<dec>". */
  key: string;
  label: string;
  group: "here" | "featured" | "guest" | "typed";
  slug: string | null;
  /** The record's year for a guest star (the card logs it then), else null (this year). */
  year: number | null;
}

export const coordKey = (p: SkyPos): string => `${p.ra.toFixed(4)},${p.dec.toFixed(4)}`;

export function parseCoordKey(key: string): SkyPos | null {
  const m = /^(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)$/.exec(key);
  if (!m) return null;
  const ra = Number(m[1]);
  const dec = Number(m[2]);
  return ra >= 0 && ra < 360 && Math.abs(dec) <= 90 ? { ra, dec } : null;
}

export function spotList(
  targets: readonly { slug: string; name: string; ra: number; dec: number; clock: string }[],
  remnants: readonly Remnant[],
  guests: readonly GuestStar[],
): Spot[] {
  const featured: Spot[] = targets
    .filter((t) => t.clock === "months")
    .map((t) => ({ key: t.slug, label: t.name, group: "featured", slug: t.slug, ra: t.ra, dec: t.dec, year: null }));
  const old: Spot[] = remnants.map((r) => {
    const g = guests.find((x) => x.id === r.id);
    return { key: r.id, label: `${snName(r.id)}: ${r.remnant}`, group: "guest", slug: null, ra: r.ra, dec: r.dec, year: g?.year ?? null };
  });
  return [...featured, ...old];
}

/**
 * The spot a route argument names. No argument: the spot last blinked (the view in the URL), else the
 * first featured target. A featured target's slug or a remnant id picks it from the list; "<ra>,<dec>"
 * is any position.
 */
export function resolveSpot(
  arg: string | null,
  list: readonly Spot[],
  here: (SkyPos & { slug: string | null; name: string | null }) | null,
): Spot | null {
  if (arg) {
    const found = list.find((s) => s.key === arg);
    if (found) return found;
    const pos = parseCoordKey(arg);
    return pos ? { ...pos, key: coordKey(pos), label: formatRaDec(pos), group: "typed", slug: null, year: null } : null;
  }
  if (here) {
    const same = here.slug ? list.find((s) => s.slug === here.slug) : undefined;
    if (same) return same;
    return { ra: here.ra, dec: here.dec, key: coordKey(here), label: here.name ?? formatRaDec(here), group: "here", slug: null, year: null };
  }
  return list[0] ?? null;
}

/**
 * Proper motion for the card's path, mas/yr (east component includes cos dec), from SkyShift's own
 * measurement between visits. null unless the shift is at least three times its error.
 */
export function measuredPath(m: { shift_arcsec: number; sigma_arcsec: number; rate_arcsec_yr: number; pa_deg: number } | null | undefined): {
  pmra: number;
  pmdec: number;
  rate: number;
} | null {
  if (!m || !(m.sigma_arcsec > 0) || m.shift_arcsec < 3 * m.sigma_arcsec) return null;
  const pa = (m.pa_deg * Math.PI) / 180;
  return { pmra: m.rate_arcsec_yr * Math.sin(pa) * 1000, pmdec: m.rate_arcsec_yr * Math.cos(pa) * 1000, rate: m.rate_arcsec_yr };
}

// ---- planets ------------------------------------------------------------------------------------
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2026-01-01", "2027-12-31" → ["2026-01", …, "2027-12"]. */
export function monthsBetween(start: string, end: string): string[] {
  const [y0, m0] = start.split("-").map(Number);
  const [y1, m1] = end.split("-").map(Number);
  const out: string[] = [];
  for (let y = y0, m = m0; y < y1 || (y === y1 && m <= m1); m === 12 ? ((m = 1), y++) : m++) {
    out.push(`${y}-${String(m).padStart(2, "0")}`);
  }
  return out;
}
export const monthLabel = (ym: string): string => `${MONTHS[Number(ym.slice(5, 7)) - 1]} ${ym.slice(0, 4)}`;

/** The month to open on: the one asked for, else this month, clamped into the data's range. */
export function pickMonth(months: readonly string[], asked: string | null, now: Date): string {
  if (asked && months.includes(asked)) return asked;
  const cur = `${now.getUTCFullYear()}-${String(now.getUTCMonth() + 1).padStart(2, "0")}`;
  if (months.includes(cur)) return cur;
  return cur < months[0] ? months[0] : months[months.length - 1];
}

/** The Chinese term and its English gloss, by event type. */
export const EVENT_TERMS: Record<string, { zh: string; en: string }> = {
  犯: { zh: "犯", en: "trespassing" },
  掩: { zh: "掩", en: "occultation" },
  入宿: { zh: "入宿", en: "entering a lodge" },
  留: { zh: "留", en: "standing still" },
};

export interface EventLine {
  date: string;
  term: { zh: string; en: string };
  html: string;
}

export function eventLine(gx: GuxingApi, e: PlanetEvent): EventLine {
  const d = new Date(e.time.replace(/Z$/, ":00Z"));
  const date = Number.isFinite(d.getTime()) ? `${d.getUTCDate()} ${MON[d.getUTCMonth()]}` : e.time.slice(0, 10);
  const body = `${esc(e.body === "moon" ? "The Moon" : e.body_en)} (${zh(e.body_zh)})`;
  const star = `${esc(e.star_en ?? "")}${e.star_zh ? ` (${zh(e.star_zh)})` : ""}`;
  const term = EVENT_TERMS[e.type] ?? { zh: e.type, en: e.type };
  let html: string;
  if (e.type === "犯") html = `${body} passes ${(e.min_sep_deg ?? 0).toFixed(2)}° from ${star}.`;
  else if (e.type === "掩") html = `${body} covers ${star}, seen from the centre of the Earth.`;
  else if (e.type === "入宿") html = `${body} enters ${lodgeHtml(gx, e.mansion ?? "")}${e.from_mansion ? ` from ${lodgeHtml(gx, e.from_mansion)}` : ""}.`;
  else if (e.type === "留")
    html = `${body} stands still in ${lodgeHtml(gx, e.mansion ?? "")}, then turns ${e.then === "逆行" ? "retrograde (westward)" : "prograde (eastward)"}.`;
  else html = `${body}: ${esc(e.type)}.`;
  return { date, term, html };
}

export function eventsIn(events: readonly PlanetEvent[], ym: string): PlanetEvent[] {
  return events.filter((e) => e.time.startsWith(ym)).sort((a, b) => a.time.localeCompare(b.time));
}

// ---- loading the library, only when the page opens ---------------------------------------------
/** In the order they must run: star map, the library, NASA's precomputed data, the card element. */
export const GUXING_FILES = ["guxing-data", "guxing", "guxing-nasa", "guxing-widget"] as const;

export interface GuxingWindow {
  GUXING_DATA?: unknown;
  Guxing?: new () => GuxingApi;
  GUXING_NASA?: NasaBundle;
}
export interface GuxingLibs {
  gx: GuxingApi;
  nasa: NasaBundle;
}

let pending: Promise<GuxingLibs> | null = null;

function libsFrom(w: GuxingWindow, hasCard: boolean): GuxingLibs | null {
  if (!w.GUXING_DATA || typeof w.Guxing !== "function" || !w.GUXING_NASA || !hasCard) return null;
  return { gx: new w.Guxing(), nasa: w.GUXING_NASA };
}

/**
 * Add the four scripts from /guxing/ once, downloading in parallel and running in order. Resolves
 * with the library; rejects with a message that names the file. After a failure the next call
 * tries again.
 */
export function loadGuxing(
  doc: Document = document,
  base = "/guxing/",
  w: GuxingWindow & { customElements?: { get(name: string): unknown } } = window as unknown as GuxingWindow,
): Promise<GuxingLibs> {
  const hasCard = () => !!w.customElements?.get("guxing-card");
  const ready = libsFrom(w, hasCard());
  if (ready) return Promise.resolve(ready);
  if (pending) return pending;
  const added: HTMLScriptElement[] = [];
  pending = Promise.all(
    GUXING_FILES.map((name) => {
      const src = `${base}${name}.js`;
      const old = doc.querySelector<HTMLScriptElement>(`script[data-guxing="${name}"]`);
      if (old?.dataset.loaded === "1") return Promise.resolve();
      old?.remove();
      return new Promise<void>((resolve, reject) => {
        const s = doc.createElement("script");
        s.src = src;
        s.async = false;
        s.dataset.guxing = name;
        s.onload = () => {
          s.dataset.loaded = "1";
          resolve();
        };
        s.onerror = () => reject(new Error(`Couldn't download the old star maps (${src}). Check your connection, then try again.`));
        added.push(s);
        doc.head.append(s);
      });
    }),
  )
    .then(() => {
      const libs = libsFrom(w, hasCard());
      if (!libs) throw new Error(`The old star maps downloaded but didn't start (${base}). Reload the page to try again.`);
      return libs;
    })
    .catch((e: unknown) => {
      for (const s of added) if (s.dataset.loaded !== "1") s.remove();
      pending = null;
      throw e;
    });
  return pending;
}

/** Tests only: forget a load in progress. */
export function _resetGuxingLoad(): void {
  pending = null;
}
