// Live search model: the sky run's progress file (schema 1), read defensively, and what the page
// derives from it (counts per state, progress, rates, time left, machines). Pure: no DOM, no fetch.
//
// Schema 1 (GET /api/skyrun or VITE_SKYRUN_URL): { schema, updated, started, ends, deadline?, final_pass_at?,
//   totals: { zones, extracted, combined, failed, photos, dots, sources, movers, suspects },
//   machines: [{ name, cpus, kind, status, zone }],
//   zones: [{ name, ra: [lo, hi], dec: [lo, hi], state, machine, photos, dots, extracted_at, combined_at,
//             sources, movers, suspects, recall, final?, error?, suspects_url?, deferred?, checks? }],
//   notes?: { deferred, provisional } }
// RA/Dec in degrees (ICRS); a zone is ra[0] ≤ RA < ra[1], dec[0] ≤ Dec < dec[1]. Times ISO UTC.

export const ZONE_STATES = ["queued", "extracting", "extracted", "combining", "combined", "failed"] as const;
export type ZoneState = (typeof ZONE_STATES)[number];

/** Zones a machine is working on right now. */
export const ACTIVE_STATES: readonly ZoneState[] = ["extracting", "combining"];
/** Zones whose photos have all been read. */
export const PAST_EXTRACTION: readonly ZoneState[] = ["extracted", "combining", "combined"];

/** The deadline when the file doesn't carry one: 10:00 CET on 15 Nov 2026. */
export const DEFAULT_DEADLINE = Date.UTC(2026, 10, 15, 9, 0, 0);
/** Older than this, the page says the numbers may be out of date. */
export const STALE_AFTER_MS = 10 * 60e3;

export interface SkyZone {
  name: string;
  /** Degrees in [0, 360]. ra[0] > ra[1] means the box wraps through RA 0. */
  ra: [number, number];
  /** Degrees in [-90, 90], dec[0] < dec[1]. */
  dec: [number, number];
  state: ZoneState;
  /** The machine that extracted it (its label: "worker", "od", "od2"), or null. */
  machine: string | null;
  /** The machine combining it right now (state "combining"), or null. */
  combiner: string | null;
  photos: number | null;
  dots: number | null;
  sources: number | null;
  movers: number | null;
  suspects: number | null;
  /** Known asteroids re-found, 0..1. */
  recall: number | null;
  /** Times in ms since 1970 (UTC). */
  extracted_at: number | null;
  combined_at: number | null;
  /** True once zone edges are merged and the CatWISE check has run; until then results are provisional. */
  final: boolean;
  error: string | null;
  suspects_url: string | null;
  /** A Galactic bulge zone: very crowded, so it comes last in every machine's list. */
  deferred: boolean;
  /** Checks run on its suspects: "gaia" while provisional, "gaia+catwise" after the WISE check; null in older files. */
  checks: string | null;
}

export interface SkyMachine {
  /** Full name, e.g. "skyshift-worker". */
  name: string;
  /** The label zones use, e.g. "worker" (name without "skyshift-"). */
  label: string;
  cpus: number | null;
  kind: string | null;
  /** Compute Engine status, upper case: RUNNING, TERMINATED, STOPPING, ... */
  status: string;
  zone: string | null;
}

export interface SkyTotals {
  zones: number | null;
  extracted: number | null;
  combined: number | null;
  failed: number | null;
  photos: number | null;
  dots: number | null;
  sources: number | null;
  movers: number | null;
  suspects: number | null;
  deferred: number | null;
}

/** Sentences the run writes for the page, shown as they are. */
export interface SkyNotes {
  /** Why the deferred (Galactic bulge) zones come last. */
  deferred: string | null;
  /** What "provisional" means for suspects that had the Gaia check only. */
  provisional: string | null;
}

export interface SkyRun {
  schema: number;
  updated: number | null;
  started: number | null;
  ends: number | null;
  deadline: number;
  /** When the final pass (zone edges joined, WISE check) finished; null until then. */
  final_pass_at: number | null;
  /** When the final pass is planned to start. */
  final_pass_starts: number | null;
  /** The run's own pace in photos per hour (null for a few minutes after a writer restart). */
  pace_photos_per_hour: number | null;
  /** The run's own progress figures (percentages 0..100), or null when the file has none. */
  progress: SkyProgress | null;
  totals: SkyTotals;
  notes: SkyNotes;
  machines: SkyMachine[];
  zones: SkyZone[];
  /** What the parser dropped or guessed, for the console. */
  warnings: string[];
}

export class SkyrunInvalid extends Error {
  constructor(message: string) {
    super(message);
    this.name = "SkyrunInvalid";
  }
}

// ---- parsing ----------------------------------------------------------------------------------
type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);

/** A finite number ≥ 0, or null. */
function count(v: unknown): number | null {
  const n = typeof v === "string" && v.trim() !== "" ? Number(v) : v;
  return typeof n === "number" && Number.isFinite(n) && n >= 0 ? n : null;
}

/** ISO time → ms, or null. Bare times without a zone are read as UTC. */
export function parseTime(v: unknown): number | null {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v !== "string" || !v.trim()) return null;
  const s = v.trim();
  const hasZone = /([zZ]|[+-]\d\d:?\d\d)$/.test(s);
  const t = Date.parse(hasZone || !/T\d/.test(s) ? s : `${s}Z`);
  return Number.isFinite(t) ? t : null;
}

function str(v: unknown): string | null {
  return typeof v === "string" && v.trim() ? v.trim() : null;
}

function pair(v: unknown): [number, number] | null {
  if (!Array.isArray(v) || v.length < 2) return null;
  const a = Number(v[0]);
  const b = Number(v[1]);
  return Number.isFinite(a) && Number.isFinite(b) ? [a, b] : null;
}

/** "extract_failed", "Combined", "combine-failed" → a known state, or null. */
export function normaliseState(v: unknown): ZoneState | null {
  const s = typeof v === "string" ? v.trim().toLowerCase() : "";
  if ((ZONE_STATES as readonly string[]).includes(s)) return s as ZoneState;
  if (/fail|error/.test(s)) return "failed";
  return null;
}

const wrap360 = (x: number) => ((x % 360) + 360) % 360;

function parseZone(raw: unknown, i: number, warnings: string[]): SkyZone | null {
  if (!isObj(raw)) {
    warnings.push(`zone ${i}: not an object`);
    return null;
  }
  const name = str(raw.name);
  const ra = pair(raw.ra);
  const dec = pair(raw.dec);
  if (!name || !ra || !dec) {
    warnings.push(`zone ${i}: missing name, ra or dec`);
    return null;
  }
  // RA: keep 360 as the top edge (a box ending at 360 doesn't wrap); a box wider than the sky is the whole ring.
  let r0 = ra[0] === 360 ? 360 : wrap360(ra[0]);
  let r1 = ra[1] === 360 ? 360 : wrap360(ra[1]);
  if (ra[1] - ra[0] >= 360) [r0, r1] = [0, 360];
  if (r0 === 360) r0 = 0;
  let [d0, d1] = dec.map((d) => Math.max(-90, Math.min(90, d))) as [number, number];
  if (d0 > d1) [d0, d1] = [d1, d0];
  if (d0 === d1 || r0 === r1) {
    warnings.push(`zone ${name}: empty box`);
    return null;
  }
  let state = normaliseState(raw.state);
  if (!state) {
    warnings.push(`zone ${name}: unknown state ${JSON.stringify(raw.state)}, shown as queued`);
    state = "queued";
  }
  let recall = count(raw.recall);
  if (recall !== null && recall > 1) recall = recall <= 100 ? recall / 100 : null;
  return {
    name,
    ra: [r0, r1],
    dec: [d0, d1],
    state,
    machine: str(raw.machine),
    combiner: str(raw.combiner),
    photos: count(raw.photos),
    dots: count(raw.dots),
    sources: count(raw.sources),
    movers: count(raw.movers),
    suspects: count(raw.suspects),
    recall,
    extracted_at: parseTime(raw.extracted_at),
    combined_at: parseTime(raw.combined_at),
    final: raw.final === true,
    error: str(raw.error),
    suspects_url: str(raw.suspects_url),
    deferred: raw.deferred === true,
    checks: str(raw.checks),
  };
}

/** "skyshift-worker" → "worker". */
export function machineLabel(name: string): string {
  return name.replace(/^skyshift-/, "");
}

function parseMachine(raw: unknown, i: number, warnings: string[]): SkyMachine | null {
  if (!isObj(raw)) {
    warnings.push(`machine ${i}: not an object`);
    return null;
  }
  const name = str(raw.name);
  if (!name) {
    warnings.push(`machine ${i}: no name`);
    return null;
  }
  return {
    name,
    label: str(raw.label) ?? machineLabel(name),
    cpus: count(raw.cpus),
    kind: str(raw.kind),
    status: (str(raw.status) ?? "UNKNOWN").toUpperCase(),
    zone: str(raw.zone),
  };
}

export interface SkyProgress {
  extracted_pct: number | null;
  combined_pct: number | null;
  combined_of_extracted_pct: number | null;
  /** Share of the time from `started` to the deadline that has gone. */
  time_pct: number | null;
  /** null for the first minutes after a writer restart. */
  zones_per_hour: number | null;
  projected_extracted_by_ends: number | null;
  projected_extracted_pct: number | null;
}

const PROGRESS_KEYS = [
  "extracted_pct",
  "combined_pct",
  "combined_of_extracted_pct",
  "time_pct",
  "zones_per_hour",
  "projected_extracted_by_ends",
  "projected_extracted_pct",
] as const;

function parseProgress(v: unknown): SkyProgress | null {
  if (!isObj(v)) return null;
  const out = Object.fromEntries(PROGRESS_KEYS.map((k) => [k, count(v[k])])) as unknown as SkyProgress;
  for (const k of PROGRESS_KEYS) if (k.endsWith("_pct") && out[k] !== null) out[k] = Math.min(100, out[k]!);
  return out;
}

const TOTAL_KEYS =["zones", "extracted", "combined", "failed", "photos", "dots", "sources", "movers", "suspects", "deferred"] as const;

function parseNotes(raw: unknown): SkyNotes {
  const n = isObj(raw) ? raw : {};
  return { deferred: str(n.deferred), provisional: str(n.provisional) };
}

/** Read a progress file. Throws SkyrunInvalid when it isn't one; drops (and notes) bad zones and machines. */
export function parseSkyrun(raw: unknown): SkyRun {
  if (!isObj(raw)) throw new SkyrunInvalid("The progress file isn't a JSON object.");
  const schema = Number(raw.schema);
  if (!Number.isFinite(schema)) throw new SkyrunInvalid("The progress file has no schema number.");
  const warnings: string[] = [];
  if (schema !== 1) warnings.push(`schema ${schema}: read as schema 1`);
  if (!Array.isArray(raw.zones) && !isObj(raw.totals)) throw new SkyrunInvalid("The progress file has neither zones nor totals.");

  const seen = new Set<string>();
  const zones: SkyZone[] = [];
  (Array.isArray(raw.zones) ? raw.zones : []).forEach((z, i) => {
    const zone = parseZone(z, i, warnings);
    if (!zone) return;
    if (seen.has(zone.name)) {
      warnings.push(`zone ${zone.name}: listed twice, kept the first`);
      return;
    }
    seen.add(zone.name);
    zones.push(zone);
  });
  const machines = (Array.isArray(raw.machines) ? raw.machines : [])
    .map((m, i) => parseMachine(m, i, warnings))
    .filter((m): m is SkyMachine => m !== null);
  const t = isObj(raw.totals) ? raw.totals : {};
  const totals = Object.fromEntries(TOTAL_KEYS.map((k) => [k, count(t[k])])) as unknown as SkyTotals;

  return {
    schema,
    updated: parseTime(raw.updated),
    started: parseTime(raw.started),
    ends: parseTime(raw.ends),
    deadline: parseTime(raw.deadline) ?? DEFAULT_DEADLINE,
    final_pass_at: parseTime(raw.final_pass_at),
    final_pass_starts: parseTime(raw.final_pass_starts),
    pace_photos_per_hour: count(raw.pace_photos_per_hour),
    progress: parseProgress(raw.progress),
    totals,
    notes: parseNotes(raw.notes),
    machines,
    zones,
    warnings,
  };
}

// ---- what the page shows ----------------------------------------------------------------------
export interface MachineSummary extends SkyMachine {
  running: boolean;
  /** Plain words for the status: "running", "stopped", "starting", ... */
  statusText: string;
  /** The zone it is extracting (from the machine, else the zone list), or null. */
  current: SkyZone | null;
  /** Zones it is combining right now. */
  combining: SkyZone[];
  /** Zones this machine has extracted so far. */
  extracted: number;
}

/** Percentages (0..100) for the stacked bar and the time bar. */
export interface ProgressBar {
  combinedPct: number;
  /** Extracted, combined or not (≥ combinedPct). */
  extractedPct: number;
  /** Time gone from started to the deadline, or null. */
  timePct: number | null;
  /** Zones expected to be extracted by `ends` at the current pace, or null when not known yet. */
  projectedPct: number | null;
  projectedZones: number | null;
  from: "run" | "page";
}

/** Suspects that had the Gaia check only (no WISE check yet) are provisional; older files: until final. */
export function isProvisional(z: Pick<SkyZone, "checks" | "final">): boolean {
  if (z.checks === null) return !z.final;
  return !z.checks.toLowerCase().split("+").map((c) => c.trim()).includes("catwise");
}

/** Who is working on a zone now: its combiner while combining (older files: its machine), else its extractor. */
export function workerOf(z: SkyZone): string | null {
  return z.state === "combining" ? z.combiner ?? z.machine : z.machine;
}

export interface RunSummary {
  /** Zones in the run: totals.zones, never fewer than the boxes listed. */
  total: number;
  counts: Record<ZoneState, number>;
  /** Combined zones as a percentage of total, 0..100. */
  combinedPct: number;
  /** Zones whose photos have all been read (extracted, combining, combined). */
  pastExtraction: number;
  /** Totals: from the file when given, else summed from the zones. */
  photos: number;
  dots: number;
  sources: number;
  movers: number;
  suspects: number;
  /** Hours from started to updated, or null. */
  hoursRunning: number | null;
  /** The run's pace when it gives one, else photos / hours running. */
  photosPerHour: number | null;
  /** "run": pace_photos_per_hour from the file; "average": our own average since the start. */
  paceFrom: "run" | "average" | null;
  /** Photos read by `ends` at this pace (photos now + pace × hours left), or null. */
  photosByEnds: number | null;
  /** The progress bar: from the file's progress block when there is one, else worked out here. */
  bar: ProgressBar;
  /** Galactic bulge zones (last in every machine's list): all of them, and those still queued. */
  deferred: { total: number; queued: number };
  /** Combined zones whose suspects are still provisional (Gaia check only). */
  provisionalCombined: number;
  zonesExtractedPerHour: number | null;
  zonesCombinedPerHour: number | null;
  /** At the pace so far: when every zone is extracted / combined (ms), or null when unknown or done. */
  etaExtracted: number | null;
  etaCombined: number | null;
  ends: number | null;
  deadline: number;
  /** ms from now; negative once passed. */
  endsIn: number | null;
  deadlineIn: number;
  /** ms since `updated`, or null. */
  age: number | null;
  stale: boolean;
  /** Nothing queued or at work. */
  finished: boolean;
  cpus: number;
  machines: MachineSummary[];
  /** Zones at work right now (extracting, combining), then the five most recently combined. */
  active: SkyZone[];
  recent: SkyZone[];
}

const STATUS_WORDS: Record<string, string> = {
  RUNNING: "running",
  TERMINATED: "stopped",
  STOPPED: "stopped",
  STOPPING: "stopping",
  SUSPENDED: "suspended",
  SUSPENDING: "suspending",
  PROVISIONING: "starting",
  STAGING: "starting",
  REPAIRING: "being repaired",
  PREEMPTED: "preempted",
};

export function statusText(status: string): string {
  return STATUS_WORDS[status.toUpperCase()] ?? status.toLowerCase();
}

const sumOf = (zones: SkyZone[], k: "photos" | "dots" | "sources" | "movers" | "suspects") =>
  zones.reduce((s, z) => s + (z[k] ?? 0), 0);

export function summarize(run: SkyRun, now: number): RunSummary {
  const counts = Object.fromEntries(ZONE_STATES.map((s) => [s, 0])) as Record<ZoneState, number>;
  for (const z of run.zones) counts[z.state]++;
  const hasZones = run.zones.length > 0;
  if (!hasZones) {
    // Totals only: rebuild what we can.
    counts.combined = run.totals.combined ?? 0;
    counts.failed = run.totals.failed ?? 0;
    counts.extracted = Math.max(0, (run.totals.extracted ?? 0) - counts.combined);
    counts.queued = Math.max(0, (run.totals.zones ?? 0) - counts.combined - counts.failed - counts.extracted);
  }
  const total = Math.max(run.totals.zones ?? 0, run.zones.length, 1);
  const pastExtraction = counts.extracted + counts.combining + counts.combined;
  const combined = run.zones.filter((z) => z.state === "combined");

  const t = run.totals;
  const photos = t.photos ?? sumOf(run.zones.filter((z) => PAST_EXTRACTION.includes(z.state)), "photos");
  const dots = t.dots ?? sumOf(combined, "dots");
  const sources = t.sources ?? sumOf(combined, "sources");
  const movers = t.movers ?? sumOf(combined, "movers");
  const suspects = t.suspects ?? sumOf(combined, "suspects");

  const end = run.updated ?? now;
  const hours = run.started !== null && end > run.started ? (end - run.started) / 3600e3 : null;
  const usable = hours !== null && hours >= 0.05;
  // Rates count only zones finished during this run (the hunt zone was done in the morning).
  const sinceStart = (at: number | null) => at === null || run.started === null || at >= run.started;
  const extractedThisRun = run.zones.filter((z) => PAST_EXTRACTION.includes(z.state) && sinceStart(z.extracted_at)).length;
  const combinedThisRun = combined.filter((z) => sinceStart(z.combined_at)).length;
  const zonesExtractedPerHour = usable && extractedThisRun > 0 ? extractedThisRun / hours : null;
  const zonesCombinedPerHour = usable && combinedThisRun > 0 ? combinedThisRun / hours : null;
  const average = usable && photos > 0 ? photos / hours : null;
  const photosPerHour = run.pace_photos_per_hour ?? average;
  const paceFrom = run.pace_photos_per_hour !== null ? "run" : average !== null ? "average" : null;
  const hoursLeft = run.ends !== null ? (run.ends - end) / 3600e3 : null;
  const photosByEnds = photosPerHour !== null && hoursLeft !== null && hoursLeft > 0 ? photos + photosPerHour * hoursLeft : null;
  const bar = progressBar(run, { total, pastExtraction, combined: counts.combined, perHour: zonesExtractedPerHour, hoursLeft, now });

  const toExtract = counts.queued + counts.extracting;
  const toCombine = total - counts.combined - counts.failed;
  const etaExtracted = toExtract > 0 && zonesExtractedPerHour ? end + (toExtract / zonesExtractedPerHour) * 3600e3 : null;
  let etaCombined = toCombine > 0 && zonesCombinedPerHour ? end + (toCombine / zonesCombinedPerHour) * 3600e3 : null;
  if (etaCombined !== null && etaExtracted !== null) etaCombined = Math.max(etaCombined, etaExtracted);

  const deferredZones = run.zones.filter((z) => z.deferred);
  const deferred = deferredZones.length
    ? { total: deferredZones.length, queued: deferredZones.filter((z) => z.state === "queued").length }
    : { total: run.totals.deferred ?? 0, queued: run.totals.deferred ?? 0 };
  const provisionalCombined = combined.filter(isProvisional).length;

  const age = run.updated !== null ? Math.max(0, now - run.updated) : null;
  const byName = new Map(run.zones.map((z) => [z.name, z]));
  const machines: MachineSummary[] = run.machines.map((m) => {
    const running = m.status === "RUNNING";
    const fromList = run.zones.find((z) => z.machine === m.label && z.state === "extracting") ?? null;
    const current = (m.zone ? byName.get(m.zone) : null) ?? (running ? fromList : null);
    return {
      ...m,
      running,
      statusText: statusText(m.status),
      current: current ?? null,
      combining: running ? run.zones.filter((z) => z.state === "combining" && workerOf(z) === m.label && z !== current) : [],
      extracted: run.zones.filter((z) => z.machine === m.label && PAST_EXTRACTION.includes(z.state)).length,
    };
  });

  const active = run.zones
    .filter((z) => ACTIVE_STATES.includes(z.state))
    .sort((a, b) => ACTIVE_STATES.indexOf(a.state) - ACTIVE_STATES.indexOf(b.state) || a.name.localeCompare(b.name));
  const recent = combined
    .filter((z) => z.combined_at !== null)
    .sort((a, b) => (b.combined_at ?? 0) - (a.combined_at ?? 0))
    .slice(0, 5);

  return {
    total,
    counts,
    combinedPct: Math.min(100, (100 * counts.combined) / total),
    pastExtraction,
    photos,
    dots,
    sources,
    movers,
    suspects,
    hoursRunning: hours,
    photosPerHour,
    paceFrom,
    photosByEnds,
    bar,
    deferred,
    provisionalCombined,
    zonesExtractedPerHour,
    zonesCombinedPerHour,
    etaExtracted,
    etaCombined,
    ends: run.ends,
    deadline: run.deadline,
    endsIn: run.ends !== null ? run.ends - now : null,
    deadlineIn: run.deadline - now,
    age,
    stale: age !== null && age > STALE_AFTER_MS,
    finished: hasZones ? counts.queued + counts.extracting + counts.extracted + counts.combining === 0 : false,
    cpus: run.machines.reduce((s, m) => s + (m.cpus ?? 0), 0),
    machines,
    active,
    recent,
  };
}

const clampPct = (x: number) => Math.max(0, Math.min(100, x));

/** The stacked bar's figures: the run's own progress block when present, else from the zone counts. */
function progressBar(
  run: SkyRun,
  c: { total: number; pastExtraction: number; combined: number; perHour: number | null; hoursLeft: number | null; now: number },
): ProgressBar {
  const at = run.updated ?? c.now;
  const timePct =
    run.started !== null && run.deadline > run.started ? clampPct((100 * (at - run.started)) / (run.deadline - run.started)) : null;
  const p = run.progress;
  if (p && p.extracted_pct !== null && p.combined_pct !== null) {
    const extractedPct = clampPct(Math.max(p.extracted_pct, p.combined_pct));
    return {
      combinedPct: clampPct(p.combined_pct),
      extractedPct,
      timePct: p.time_pct ?? timePct,
      projectedPct: p.projected_extracted_pct !== null ? clampPct(Math.max(p.projected_extracted_pct, extractedPct)) : null,
      projectedZones: p.projected_extracted_by_ends,
      from: "run",
    };
  }
  // No progress block: project extraction to `ends` from the zones extracted per hour so far.
  const extractedPct = clampPct((100 * c.pastExtraction) / c.total);
  const zones =
    c.perHour !== null && c.hoursLeft !== null && c.hoursLeft > 0 ? Math.min(c.total, c.pastExtraction + c.perHour * c.hoursLeft) : null;
  return {
    combinedPct: clampPct((100 * c.combined) / c.total),
    extractedPct,
    timePct,
    projectedPct: zones !== null ? Math.max(extractedPct, clampPct((100 * zones) / c.total)) : null,
    projectedZones: zones !== null ? Math.round(zones) : null,
    from: "page",
  };
}

/** Names whose state differs between two runs (new zones count as changed; none when there is no previous run). */
export function changedZones(prev: SkyRun | null, next: SkyRun): Set<string> {
  const out = new Set<string>();
  if (!prev) return out;
  const before = new Map(prev.zones.map((z) => [z.name, z.state]));
  for (const z of next.zones) if (before.get(z.name) !== z.state) out.add(z.name);
  return out;
}

/** Centre of a zone (RA wraps through 0 when the box does). */
export function zoneCentre(z: Pick<SkyZone, "ra" | "dec">): { ra: number; dec: number } {
  const [r0, r1] = z.ra;
  const span = r1 >= r0 ? r1 - r0 : r1 + 360 - r0;
  return { ra: wrap360(r0 + span / 2), dec: (z.dec[0] + z.dec[1]) / 2 };
}

/** Area of a zone's RA/Dec box in square degrees. */
export function zoneArea(z: Pick<SkyZone, "ra" | "dec">): number {
  const [r0, r1] = z.ra;
  const span = r1 >= r0 ? r1 - r0 : r1 + 360 - r0;
  const rad = Math.PI / 180;
  return (span * (Math.sin(z.dec[1] * rad) - Math.sin(z.dec[0] * rad))) / rad;
}

/** True when (ra, dec) lies in the zone: ra[0] ≤ RA < ra[1], dec[0] ≤ Dec < dec[1] (wrap-aware). */
export function zoneContains(z: Pick<SkyZone, "ra" | "dec">, ra: number, dec: number): boolean {
  if (dec < z.dec[0] || dec >= z.dec[1]) return false;
  const [r0, r1] = z.ra;
  const a = wrap360(ra);
  return r1 >= r0 ? a >= r0 && a < r1 : a >= r0 || a < r1;
}
