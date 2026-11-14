// Live search model: the sky run's progress file (schema 1), read defensively, and what the page
// derives from it (counts per state, progress, rates, time left, machines). Pure: no DOM, no fetch.
//
// Schema 1 (GET /api/skyrun or VITE_SKYRUN_URL): { schema, updated, started, ends, deadline?, final_pass_at?,
//   totals: { zones, extracted, combined, failed, photos, dots, sources, movers, suspects },
//   machines: [{ name, cpus, kind, status, zone }],
//   zones: [{ name, ra: [lo, hi], dec: [lo, hi], state, machine, photos, dots, extracted_at, combined_at,
//             sources, movers, suspects, recall, final?, error?, suspects_url? }] }
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
  /** Short machine label as the file gives it ("worker", "od", "od2"), or null. */
  machine: string | null;
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
}

export interface SkyRun {
  schema: number;
  updated: number | null;
  started: number | null;
  ends: number | null;
  deadline: number;
  final_pass_at: number | null;
  totals: SkyTotals;
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
    label: machineLabel(name),
    cpus: count(raw.cpus),
    kind: str(raw.kind),
    status: (str(raw.status) ?? "UNKNOWN").toUpperCase(),
    zone: str(raw.zone),
  };
}

const TOTAL_KEYS = ["zones", "extracted", "combined", "failed", "photos", "dots", "sources", "movers", "suspects"] as const;

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
    totals,
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
  /** The zone it is on (from the machine, else the zone list), or null. */
  current: SkyZone | null;
  /** Zones this machine extracted and combined so far. */
  extracted: number;
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
  photosPerHour: number | null;
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
  const photosPerHour = usable && photos > 0 ? photos / hours : null;

  const toExtract = counts.queued + counts.extracting;
  const toCombine = total - counts.combined - counts.failed;
  const etaExtracted = toExtract > 0 && zonesExtractedPerHour ? end + (toExtract / zonesExtractedPerHour) * 3600e3 : null;
  let etaCombined = toCombine > 0 && zonesCombinedPerHour ? end + (toCombine / zonesCombinedPerHour) * 3600e3 : null;
  if (etaCombined !== null && etaExtracted !== null) etaCombined = Math.max(etaCombined, etaExtracted);

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
