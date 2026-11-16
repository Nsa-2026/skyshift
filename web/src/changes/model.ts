// The Changes list's data: /data/changes.json (pipeline changes_export.py; proposed contract text in its PR).
// Read defensively: a field the page doesn't know is ignored, a missing one reads as "not measured".

export type ChangeKind = 'brightness' | 'colour' | 'appeared' | 'vanished';
export type CheckLevel = 'strong' | 'warn' | 'ok';

export interface ChangeCheck {
  code: string;
  level: CheckLevel;
  text: string;
  look: string;
  values: Record<string, unknown> | null;
}

export interface OtherDetector { det: number; change_frac: number; sigma: number }

export interface Change {
  id: string;
  kind: ChangeKind;
  zone: string;
  zones: string[];
  name: string;
  /** Gaia DR3 source_id as a string (contract v3.27: above 2^53 a JSON number loses digits in JavaScript). */
  gaia_id: string | null;
  ref_id: string | null;
  source_id: number | null;
  ra: number;
  dec: number;
  g_mag: number | null;
  /** The detector the change is strongest in: the Viewer opens on it. */
  det: number;
  measured: 'own' | 'forced';
  visits_before: number[];
  visit_after: number;
  date_before: string | null;
  date_after: string | null;
  mjd_after: number | null;
  rel_before: number | null;
  rel_after: number | null;
  change_frac: number | null;
  sigma: number | null;
  other_detectors: OtherDetector[];
  colour_change: number | null;
  colour_sigma: number | null;
  snr: number | null;
  detectors: number[];
  /** true: Gaia DR3 already lists it as variable; false: it doesn't; null: not known. */
  gaia_variable: boolean | null;
  gaia_class: string | null;
  headline: string;
  text: string;
  look: string;
  checks: ChangeCheck[];
  score: number;
}

export interface ZoneWatched {
  zone: string;
  stars: number | null;
  visits: number[] | null;
  listed: number;
  false_alarms_per_1000: number | null;
}

export interface ChangesFile {
  generated: string | null;
  data_release: string | null;
  zones: string[];
  watched: ZoneWatched[];
  feed: string;
  changes: Change[];
}

export const KINDS: readonly ChangeKind[] = ['brightness', 'colour', 'appeared', 'vanished'];
const LEVELS: readonly CheckLevel[] = ['strong', 'warn', 'ok'];
export const DET_RANGE: Record<number, string> = {
  1: '0.75–1.1 µm', 2: '1.1–1.6 µm', 3: '1.6–2.4 µm', 4: '2.4–3.8 µm', 5: '3.8–4.4 µm', 6: '4.4–5.0 µm',
};

const num = (x: unknown): number | null => (typeof x === 'number' && Number.isFinite(x) ? x : null);
const str = (x: unknown): string => (typeof x === 'string' ? x : '');
const ints = (x: unknown): number[] => (Array.isArray(x) ? x.filter((v): v is number => Number.isInteger(v)) : []);

/** The Gaia id as a string. Files before v3.27 wrote a JSON number, which JavaScript rounds (11 of the 66 sample ids
 * came out wrong): the exact digits are then taken from ref_id `gaia:<N>` when it is the same star. */
export function gaiaId(x: unknown, refId: string | null): string | null {
  if (typeof x === 'string') return /^\d+$/.test(x) ? x : null;
  if (typeof x !== 'number' || !Number.isInteger(x) || x <= 0) return null;
  const m = /^gaia:(\d+)$/.exec(refId ?? '');
  if (m && Number(m[1]) === x) return m[1];
  return BigInt(x).toString();
}

function check(x: unknown): ChangeCheck | null {
  if (!x || typeof x !== 'object') return null;
  const c = x as Record<string, unknown>;
  const text = str(c.text);
  if (!text) return null;
  const level = LEVELS.includes(c.level as CheckLevel) ? (c.level as CheckLevel) : 'warn';
  const values = c.values && typeof c.values === 'object' ? (c.values as Record<string, unknown>) : null;
  return { code: str(c.code) || 'check', level, text, look: str(c.look), values };
}

/** One change from the file, or null when it can't be shown (no id, place or kind). */
export function parseChange(x: unknown): Change | null {
  if (!x || typeof x !== 'object') return null;
  const c = x as Record<string, unknown>;
  const id = str(c.id);
  const ra = num(c.ra);
  const dec = num(c.dec);
  const kind = c.kind as ChangeKind;
  if (!id || ra == null || dec == null || !KINDS.includes(kind)) return null;
  const det = num(c.det);
  const gv = c.gaia_variable;
  const others = Array.isArray(c.other_detectors) ? c.other_detectors : [];
  return {
    id, kind, ra, dec,
    zone: str(c.zone),
    zones: Array.isArray(c.zones) ? c.zones.filter((z): z is string => typeof z === 'string') : [str(c.zone)],
    name: str(c.name) || id,
    gaia_id: gaiaId(c.gaia_id, str(c.ref_id) || null),
    ref_id: str(c.ref_id) || null,
    source_id: num(c.source_id),
    g_mag: num(c.g_mag),
    det: det != null && det >= 1 && det <= 6 ? Math.round(det) : 2,
    measured: c.measured === 'forced' ? 'forced' : 'own',
    visits_before: ints(c.visits_before),
    visit_after: num(c.visit_after) ?? 0,
    date_before: str(c.date_before) || null,
    date_after: str(c.date_after) || null,
    mjd_after: num(c.mjd_after),
    rel_before: num(c.rel_before),
    rel_after: num(c.rel_after),
    change_frac: num(c.change_frac),
    sigma: num(c.sigma),
    other_detectors: others
      .map((o) => o as Record<string, unknown>)
      .filter((o) => num(o?.det) != null && num(o?.change_frac) != null && num(o?.sigma) != null)
      .map((o) => ({ det: o.det as number, change_frac: o.change_frac as number, sigma: o.sigma as number })),
    colour_change: num(c.colour_change),
    colour_sigma: num(c.colour_sigma),
    snr: num(c.snr),
    detectors: ints(c.detectors),
    gaia_variable: gv === true ? true : gv === false ? false : null,
    gaia_class: str(c.gaia_class) || null,
    headline: str(c.headline),
    text: str(c.text),
    look: str(c.look),
    checks: (Array.isArray(c.checks) ? c.checks : []).map(check).filter((v): v is ChangeCheck => v != null),
    score: num(c.score) ?? 0,
  };
}

/** The whole file; throws when it isn't a changes file at all. */
export function parseChangesFile(x: unknown): ChangesFile {
  if (!x || typeof x !== 'object' || !Array.isArray((x as { changes?: unknown }).changes)) {
    throw new Error('not a changes file');
  }
  const d = x as Record<string, unknown>;
  const watched = (Array.isArray(d.watched) ? d.watched : []).map((w) => w as Record<string, unknown>).map((w) => ({
    zone: str(w.zone),
    stars: num(w.stars),
    visits: Array.isArray(w.visits) ? ints(w.visits) : null,
    listed: num(w.listed) ?? 0,
    false_alarms_per_1000: num(w.false_alarms_per_1000),
  }));
  const changes = (d.changes as unknown[]).map(parseChange).filter((c): c is Change => c != null);
  return {
    generated: str(d.generated) || null,
    data_release: str(d.data_release) || null,
    zones: Array.isArray(d.zones) ? d.zones.filter((z): z is string => typeof z === 'string') : [],
    watched,
    feed: str(d.feed) || 'changes.atom',
    changes: sortChanges(changes),
  };
}

/** Unexpected changes first, then the stars Gaia already lists as variable; each by score. */
export function sortChanges(list: Change[]): Change[] {
  return [...list].sort((a, b) =>
    Number(a.gaia_variable === true) - Number(b.gaia_variable === true) || b.score - a.score || a.id.localeCompare(b.id));
}

/** What a follow is about: the star, not one change of it (the same key in the next release's file). */
export function starKey(c: Pick<Change, 'gaia_id' | 'ref_id' | 'source_id' | 'id'>): string {
  if (c.gaia_id != null) return `gaia:${c.gaia_id}`;
  if (c.ref_id) return c.ref_id;
  if (c.source_id != null) return `src:${c.source_id}`;
  return `id:${c.id}`;
}

/** Follows saved while gaia_id was a rounded number (`gaia:160747814004063500`): the key of the star in this list
 * whose id rounds to the same number. Returns [old, new] pairs; keys of stars not in this list stay as they are. */
export function roundedFollows(list: Change[], following: ReadonlySet<string>): [string, string][] {
  const here = new Map<number, string>();
  for (const c of list) if (c.gaia_id != null) here.set(Number(c.gaia_id), starKey(c));
  const out: [string, string][] = [];
  for (const k of following) {
    const m = /^gaia:(\d+)$/.exec(k);
    if (!m) continue;
    const now = here.get(Number(m[1]));
    if (now && now !== k && !following.has(now)) out.push([k, now]);
  }
  return out;
}

export type ListFilter = 'all' | 'following';

export function visible(list: Change[], filter: ListFilter, following: ReadonlySet<string>): Change[] {
  return filter === 'following' ? list.filter((c) => following.has(starKey(c))) : list;
}

/** Stars followed that have no change in this file (followed from an earlier release). */
export function followedElsewhere(list: Change[], following: ReadonlySet<string>): number {
  const here = new Set(list.map(starKey));
  return [...following].filter((k) => !here.has(k)).length;
}

/** The Viewer on the star: months clock, its detector, the close field. */
export function viewerHref(c: Pick<Change, 'ra' | 'dec' | 'det'>, fov = 5): string {
  const p = new URLSearchParams({ ra: c.ra.toFixed(5), dec: c.dec.toFixed(5), clock: 'months', fov: String(fov), det: String(c.det) });
  return `?${p}#/explore`;
}

export function watchedStars(d: ChangesFile): number {
  return d.watched.reduce((s, w) => s + (w.stars ?? 0), 0);
}

/** "0.12–0.33 per 1,000 stars" from the zones' measured false-alarm rates, or "" when none is known. */
export function falseAlarmRange(d: ChangesFile): string {
  const r = d.watched.map((w) => w.false_alarms_per_1000).filter((x): x is number => x != null);
  if (!r.length) return '';
  const lo = Math.min(...r);
  const hi = Math.max(...r);
  const f = (x: number) => x.toFixed(x < 1 ? 2 : 1);
  return `${lo === hi ? f(lo) : `${f(lo)}–${f(hi)}`} per 1,000 stars`;
}

/** The change in numbers for the row's second line: "1.14 → 0.83 of its usual brightness · 20.7σ". */
export function numbers(c: Change): string {
  if ((c.kind === 'brightness' || c.kind === 'colour') && c.rel_before != null && c.rel_after != null) {
    const parts = [`${c.rel_before.toFixed(2)} → ${c.rel_after.toFixed(2)} of its usual brightness in D${c.det}`];
    if (c.kind === 'brightness' && c.sigma != null) parts.push(`${Math.abs(c.sigma).toFixed(1)}σ`);
    if (c.kind === 'colour' && c.colour_sigma != null) parts.push(`colour ${Math.abs(c.colour_sigma).toFixed(1)}σ`);
    return parts.join(' · ');
  }
  if (c.snr != null) return `S/N ${c.snr.toFixed(0)} · ${c.detectors.map((d) => `D${d}`).join(' ')}`;
  return '';
}

/** "visits 1–2 (to 6 Mar 2026) → visit 3 (12 Sep 2026)". */
export function when(c: Change): string {
  const vb = c.visits_before;
  const before = vb.length > 1 ? `visits ${vb[0]}–${vb[vb.length - 1]}` : vb.length ? `visit ${vb[0]}` : 'before';
  const d = (s: string | null) => (s ? ` (${fmtDate(s)})` : '');
  return `${before}${d(c.date_before)} → visit ${c.visit_after}${d(c.date_after)}`;
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
export function fmtDate(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return iso;
  return `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]} ${m[1]}`;
}

/** Gaia's variability class in plain words (Gaia DR3 vari_classifier_result). */
const CLASS_WORDS: Record<string, string> = {
  ECL: 'eclipsing binary', RR: 'RR Lyrae', LPV: 'long-period variable', YSO: 'young star', RS: 'RS CVn binary',
  CEP: 'Cepheid', 'DSCT|GDOR|SXPHE': 'delta Scuti type', SOLAR_LIKE: 'spotted Sun-like star', S: 'short-timescale',
  AGN: 'active galaxy', ELL: 'ellipsoidal binary', CV: 'cataclysmic variable',
};
export function variableLabel(c: Pick<Change, 'gaia_variable' | 'gaia_class'>): string | null {
  if (c.gaia_variable !== true) return null;
  const w = c.gaia_class ? CLASS_WORDS[c.gaia_class] ?? c.gaia_class : '';
  return `Gaia already lists it as variable${w ? ` (${w})` : ''}`;
}
