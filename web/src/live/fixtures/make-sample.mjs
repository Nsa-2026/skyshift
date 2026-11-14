// Synthetic Live search sample (schema 1), for TESTS and local screenshots only. Never copy it into
// public/data and never show it as real. Run: node web/src/live/fixtures/make-sample.mjs
//
// Zones follow pipeline/src/skyshift_pipeline/config.py ecliptic_zones(): 37 RA columns (0.5 + 10k ..
// 10.5 + 10k, column 36 is the RA 0-0.5 strip) x 9 rows of 10° centred on the ecliptic's Dec at the
// column's middle RA; column 12 row 0 is "hunt". Three machines (96 + 80 + 40 CPUs, as on the real run)
// extract zones outward from the hunt zone; any machine combines extracted zones, oldest first, two at a
// time. The state at UPDATED is what this file holds. Photos, dots, movers and suspects are made up but
// scale sensibly with area, ecliptic latitude and the Milky Way.
import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const OUT = fileURLToPath(new URL("./skyrun-sample.json", import.meta.url));
const EPS = 23.4393;
const rad = (d) => (d * Math.PI) / 180;
const deg = (r) => (r * 180) / Math.PI;
const round1 = (x) => Math.round(x * 10) / 10;
const iso = (ms) => new Date(Math.round(ms / 1000) * 1000).toISOString().replace(".000Z", "Z");

const STARTED = Date.parse("2026-11-14T13:49:34Z");
const UPDATED = Date.parse("2026-11-14T19:12:40Z");

// mulberry32: the same file every run
let seed = 20261114;
function rand() {
  seed |= 0;
  seed = (seed + 0x6d2b79f5) | 0;
  let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
  t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
}
const between = (a, b) => a + (b - a) * rand();

const eclDec = (ra) => deg(Math.atan(Math.sin(rad(ra)) * Math.tan(rad(EPS))));
function eclipticLat(ra, dec) {
  const s = Math.sin(rad(dec)) * Math.cos(rad(EPS)) - Math.cos(rad(dec)) * Math.sin(rad(EPS)) * Math.sin(rad(ra));
  return deg(Math.asin(s));
}
function galacticLat(ra, dec) {
  // North galactic pole (J2000): RA 192.85948, Dec 27.12825
  const s =
    Math.sin(rad(dec)) * Math.sin(rad(27.12825)) +
    Math.cos(rad(dec)) * Math.cos(rad(27.12825)) * Math.cos(rad(ra - 192.85948));
  return deg(Math.asin(s));
}

// ---- the zones ------------------------------------------------------------------------------------
const zones = [];
const cols = [...Array(36).keys()].map((k) => [k, 0.5 + 10 * k, Math.min(10.5 + 10 * k, 360)]).concat([[36, 0, 0.5]]);
for (const [k, r0, r1] of cols) {
  const c = round1(eclDec((r0 + r1) / 2));
  for (const row of [0, 1, 2, 3, 4, -1, -2, -3, -4]) {
    const name = k === 12 && row === 0 ? "hunt" : `ecl${String(k).padStart(2, "0")}${row === 0 ? "" : (row > 0 ? "n" : "s") + Math.abs(row)}`;
    const d0 = Math.max(round1(c - 5 + 10 * row), -90);
    const d1 = Math.min(round1(c + 5 + 10 * row), 90);
    const ra = (r0 + r1) / 2;
    const dec = (d0 + d1) / 2;
    const area = (r1 - r0) * deg(Math.sin(rad(d1)) - Math.sin(rad(d0)));
    const beta = Math.abs(eclipticLat(ra, dec));
    const b = Math.abs(galacticLat(ra, dec));
    const photos = Math.round(4000 * Math.pow(area / 100, 0.8) * Math.pow(1 / Math.cos(rad(beta)), 0.7) * between(0.9, 1.1)) + (r1 - r0 < 1 ? 600 : 0);
    const dotsPerPhoto = 3200 + 26000 * Math.exp(-b / 7);
    zones.push({ k, row, name, ra: [r0, r1], dec: [d0, d1], area, beta, b, photos, dotsPerPhoto });
  }
}

// ---- the run: extraction order per machine, then combining --------------------------------------
const SEC_PER_PHOTO_CPU = 5.6;
const machines = [
  { name: "skyshift-worker", label: "worker", cpus: 96, kind: "spot", start: STARTED, cols: [13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24] },
  { name: "skyshift-od", label: "od", cpus: 80, kind: "on-demand", start: STARTED + 4 * 60e3, cols: [11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0, 36, 35] },
  { name: "skyshift-od2", label: "od2", cpus: 40, kind: "on-demand", start: STARTED + 9 * 60e3, cols: [28, 27, 29, 26, 30, 25, 31, 32, 33, 34] },
];
const byName = new Map(zones.map((z) => [z.name, z]));
const rowOrder = [0, 1, -1, 2, -2, 3, -3, 4, -4];
for (const m of machines) {
  m.queue = m.cols.flatMap((k) => rowOrder.map((r) => zones.find((z) => z.k === k && z.row === r)).filter((z) => z && z.name !== "hunt"));
  m.t = m.start;
}
// the worker is a spot machine: preempted once for 14 minutes
const PREEMPT_AT = STARTED + 3.1 * 3600e3;
const hunt = byName.get("hunt");
Object.assign(hunt, { state: "combined", machine: "worker", extracted_at: Date.parse("2026-11-14T11:30:00Z"), combined_at: Date.parse("2026-11-14T13:40:00Z") });

const extracted = [];
let busy = true;
while (busy) {
  busy = false;
  for (const m of machines) {
    if (m.t >= UPDATED) continue;
    const z = m.queue.shift();
    if (!z) continue;
    busy = true;
    if (m.label === "worker" && m.t < PREEMPT_AT && m.t + 600e3 > PREEMPT_AT) m.t += 14 * 60e3;
    const dur = (z.photos * SEC_PER_PHOTO_CPU * 1000) / m.cpus + between(30e3, 90e3);
    z.machine = m.label;
    z.extract_start = m.t;
    m.t += dur;
    if (m.t > UPDATED) {
      z.state = "extracting";
      m.current = z.name;
    } else {
      z.extracted_at = m.t;
      extracted.push(z);
    }
  }
}
// one zone fails extraction (a corrupt photo list), one fails combining
const extractFail = byName.get("ecl08s3");
const combineFail = byName.get("ecl14n2");

// combining: five slots in all, oldest extracted first, 7-13 min each at nice 19
extracted.sort((a, b) => a.extracted_at - b.extracted_at);
const slots = Array(5).fill(STARTED);
for (const z of extracted) {
  if (z === extractFail) {
    z.state = "failed";
    z.error = "photolist: IRSA search timed out twice";
    continue;
  }
  const i = slots.indexOf(Math.min(...slots));
  const start = Math.max(slots[i], z.extracted_at);
  const end = start + between(7, 13) * 60e3 * (z.dotsPerPhoto > 12000 ? 1.8 : 1);
  slots[i] = end;
  if (start > UPDATED) z.state = "extracted";
  else if (end > UPDATED) {
    z.state = "combining";
    z.combiner = ["worker", "od", "od2", "worker", "od"][i];
  } else if (z === combineFail) {
    z.state = "failed";
    z.error = "combine: link step ran out of memory (exit=137)";
  } else {
    z.state = "combined";
    z.combined_at = end;
  }
}

// ---- numbers per zone -------------------------------------------------------------------------
function numbers(z) {
  const out = { photos: null, dots: null, sources: null, movers: null, suspects: null, recall: null };
  if (z.state === "queued") return out;
  out.photos = z.photos;
  if (z.state === "extracting") return out;
  out.dots = Math.round(z.photos * z.dotsPerPhoto * between(0.92, 1.08));
  if (z.state !== "combined") return out;
  out.sources = Math.round(out.dots / between(80, 140));
  const movers = 5600 * Math.exp(-z.beta / 7) + (z.b < 10 ? between(200, 900) : between(10, 80));
  out.movers = Math.max(0, Math.round(movers * between(0.8, 1.2)));
  const lam = 0.035 * out.movers + (z.b < 8 ? 25 : 1);
  out.suspects = Math.max(0, Math.round(lam + (rand() - 0.5) * 2 * Math.sqrt(lam + 0.3)));
  out.recall = z.beta < 25 ? Math.round(between(0.78, 0.93) * 1e4) / 1e4 : null;
  return out;
}
Object.assign(hunt, { photos: 3692, dots: 33359558, sources: 303056, movers: 6143, suspects: 228, recall: 0.9037 });

const outZones = zones.map((z) => {
  const state = z.state ?? "queued";
  const n = z.name === "hunt" ? { photos: hunt.photos, dots: hunt.dots, sources: hunt.sources, movers: hunt.movers, suspects: hunt.suspects, recall: hunt.recall } : numbers({ ...z, state });
  return {
    name: z.name,
    ra: z.ra,
    dec: z.dec,
    state,
    final: z.name === "hunt",
    machine: state === "queued" ? null : z.machine ?? null,
    combiner: state === "combining" ? z.combiner ?? null : null,
    photos: n.photos,
    dots: n.dots,
    extracted_at: z.extracted_at ? iso(z.extracted_at) : null,
    combined_at: z.combined_at ? iso(z.combined_at) : null,
    sources: n.sources,
    movers: n.movers,
    suspects: n.suspects,
    recall: n.recall,
    error: z.error ?? null,
    suspects_url: state === "combined" ? `https://storage.googleapis.com/nasa-glazing-live/skyrun/suspects/${z.name}.json` : null,
  };
});

const done = outZones.filter((z) => ["extracted", "combining", "combined"].includes(z.state));
const combined = outZones.filter((z) => z.state === "combined");
const sum = (zs, k) => zs.reduce((s, z) => s + (z[k] ?? 0), 0);
const ENDS = Date.parse("2026-11-15T05:00:00Z");
const DEADLINE = Date.parse("2026-11-15T09:00:00Z");
const hoursIn = (UPDATED - STARTED) / 3600e3;
const zonesPerHour = (done.length - 1) / hoursIn; // the hunt zone was done before the start
const projected = Math.min(outZones.length, Math.round(done.length + (zonesPerHour * (ENDS - UPDATED)) / 3600e3));
const r1 = (x) => Math.round(x * 10) / 10;
const sample = {
  _synthetic: "Synthetic test sample made by make-sample.mjs. Not real data: never publish it.",
  schema: 1,
  updated: iso(UPDATED),
  started: iso(STARTED),
  ends: iso(ENDS),
  deadline: iso(DEADLINE),
  final_pass_starts: "2026-11-15T05:00:00Z",
  progress: {
    extracted_pct: r1((100 * done.length) / outZones.length),
    combined_pct: r1((100 * combined.length) / outZones.length),
    combined_of_extracted_pct: r1((100 * combined.length) / done.length),
    time_pct: r1((100 * (UPDATED - STARTED)) / (DEADLINE - STARTED)),
    zones_per_hour: r1(zonesPerHour),
    projected_extracted_by_ends: projected,
    projected_extracted_pct: r1((100 * projected) / outZones.length),
  },
  final_pass_at: null,
  pace_photos_per_hour: Math.round((sum(done, "photos") * 0.85 * 3600e3 * 1.04) / (UPDATED - STARTED)),
  totals: {
    zones: outZones.length,
    extracted: done.length,
    combined: combined.length,
    failed: outZones.filter((z) => z.state === "failed").length,
    // unique photos: neighbouring zones share about 15% of their photos
    photos: Math.round(sum(done, "photos") * 0.85),
    dots: sum(combined, "dots"),
    sources: sum(combined, "sources"),
    movers: sum(combined, "movers"),
    suspects: sum(combined, "suspects"),
  },
  machines: machines
    .map((m) => ({ name: m.name, label: m.label, cpus: m.cpus, kind: m.kind, status: "RUNNING", zone: m.current ?? null }))
    // the 8-CPU test machine joined the extraction later (14 Nov evening); in this sample it is between zones
    .concat([{ name: "skyshift-test", label: "test", cpus: 8, kind: "on-demand", status: "RUNNING", zone: null }]),
  zones: outZones,
};
writeFileSync(OUT, JSON.stringify(sample, null, 1) + "\n");
const count = (s) => outZones.filter((z) => z.state === s).length;
console.log(
  `wrote ${OUT}: ${outZones.length} zones; ` +
    ["queued", "extracting", "extracted", "combining", "combined", "failed"].map((s) => `${s} ${count(s)}`).join(", ") +
    `; photos ${sample.totals.photos}, suspects ${sample.totals.suspects}`,
);
