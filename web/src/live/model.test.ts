import { describe, expect, it } from "vitest";
import real from "./fixtures/skyrun-real-1410Z.json";
import real2 from "./fixtures/skyrun-real-1440Z.json";
import real3 from "./fixtures/skyrun-real-1505Z.json";
import sample from "./fixtures/skyrun-sample.json";
import { fmtAbout, fmtAgo, fmtCount, fmtDec, fmtDuration, fmtTimePair } from "./format";
import {
  DEFAULT_DEADLINE,
  isProvisional,
  SkyrunInvalid,
  changedZones,
  normaliseState,
  parseSkyrun,
  parseTime,
  summarize,
  workerOf,
  zoneArea,
  zoneCentre,
  zoneContains,
} from "./model";

const T = (iso: string) => Date.parse(iso);

describe("parseSkyrun", () => {
  it("reads the real snapshot (14 Nov, 14:10 UTC): 333 zones, the hunt zone final and combined", () => {
    const run = parseSkyrun(real);
    expect(run.schema).toBe(1);
    expect(run.zones).toHaveLength(333);
    expect(run.warnings).toEqual([]);
    expect(run.updated).toBe(T("2026-11-14T14:10:25Z"));
    expect(run.deadline).toBe(T("2026-11-15T09:00:00Z"));
    expect(run.final_pass_at).toBeNull();
    const hunt = run.zones.find((z) => z.name === "hunt")!;
    expect(hunt).toMatchObject({ state: "combined", final: true, ra: [120.5, 130.5], dec: [14.4, 24.4], suspects: 228, recall: 0.9037 });
    expect(run.zones.find((z) => z.name === "ecl35")!.ra).toEqual([350.5, 360]);
    expect(run.zones.find((z) => z.name === "ecl36")!.ra).toEqual([0, 0.5]);
    expect(run.machines.map((m) => [m.label, m.cpus, m.kind])).toEqual([
      ["worker", 96, "spot"],
      ["od", 80, "on-demand"],
      ["od2", 40, "on-demand"],
    ]);
  });

  it("reads the synthetic sample without warnings", () => {
    const run = parseSkyrun(sample);
    expect(run.zones).toHaveLength(333);
    expect(run.warnings).toEqual([]);
  });

  it("refuses what isn't a progress file", () => {
    expect(() => parseSkyrun(null)).toThrow(SkyrunInvalid);
    expect(() => parseSkyrun("<html>")).toThrow(SkyrunInvalid);
    expect(() => parseSkyrun({ zones: [] })).toThrow(/schema/);
    expect(() => parseSkyrun({ schema: 1 })).toThrow(/neither zones nor totals/);
  });

  it("drops bad zones and machines, and notes why", () => {
    const run = parseSkyrun({
      schema: 1,
      updated: "2026-11-14T18:00:00Z",
      zones: [
        { name: "a", ra: [10, 20], dec: [0, 10], state: "combined", photos: "120", suspects: -3, recall: 87 },
        { name: "b", ra: [10], dec: [0, 10], state: "queued" },
        "nope",
        { name: "a", ra: [10, 20], dec: [0, 10], state: "queued" },
        { name: "c", ra: [-5, 5], dec: [20, 10], state: "extract_failed", error: "boom" },
        { name: "d", ra: [30, 40], dec: [0, 10], state: "sleeping" },
      ],
      machines: [{ name: "skyshift-od", status: "terminated" }, { cpus: 4 }],
    });
    expect(run.zones.map((z) => z.name)).toEqual(["a", "c", "d"]);
    const [a, c, d] = run.zones;
    expect(a.photos).toBe(120);
    expect(a.suspects).toBeNull();
    expect(a.recall).toBeCloseTo(0.87);
    expect(c.ra).toEqual([355, 5]); // wraps through RA 0
    expect(c.dec).toEqual([10, 20]);
    expect(c.state).toBe("failed");
    expect(c.error).toBe("boom");
    expect(d.state).toBe("queued");
    expect(run.machines).toHaveLength(1);
    expect(run.machines[0]).toMatchObject({ label: "od", status: "TERMINATED", cpus: null });
    expect(run.deadline).toBe(DEFAULT_DEADLINE);
    expect(run.warnings.join(" ")).toMatch(/zone 1.*zone 2.*listed twice.*unknown state/);
  });

  it("reads times with or without a zone, and states loosely", () => {
    expect(parseTime("2026-11-14T13:49:34Z")).toBe(T("2026-11-14T13:49:34Z"));
    expect(parseTime("2026-11-14T13:49:34")).toBe(T("2026-11-14T13:49:34Z"));
    expect(parseTime("2026-11-14T14:49:34+01:00")).toBe(T("2026-11-14T13:49:34Z"));
    expect(parseTime("soon")).toBeNull();
    expect(parseTime(null)).toBeNull();
    expect(normaliseState("Combined")).toBe("combined");
    expect(normaliseState("combine_failed")).toBe("failed");
    expect(normaliseState("?")).toBeNull();
  });
});

describe("summarize", () => {
  const run = parseSkyrun(sample);
  const now = T("2026-11-14T19:14:40Z");
  const s = summarize(run, now);

  it("counts zones per state and the share combined", () => {
    const total = Object.values(s.counts).reduce((a, b) => a + b, 0);
    expect(total).toBe(333);
    expect(s.total).toBe(333);
    expect(s.counts.combined).toBe(run.zones.filter((z) => z.state === "combined").length);
    expect(s.combinedPct).toBeCloseTo((100 * s.counts.combined) / 333);
    expect(s.pastExtraction).toBe(s.counts.extracted + s.counts.combining + s.counts.combined);
    expect(s.finished).toBe(false);
  });

  it("takes the totals from the file", () => {
    expect(s.photos).toBe(sample.totals.photos);
    expect(s.suspects).toBe(sample.totals.suspects);
  });

  it("derives rates and an ETA from the zones done since the start", () => {
    const hours = (T(sample.updated) - T(sample.started)) / 3600e3;
    expect(s.hoursRunning).toBeCloseTo(hours);
    // the run's own pace wins over our average
    expect(s.photosPerHour).toBe(sample.pace_photos_per_hour);
    expect(s.paceFrom).toBe("run");
    const noPace = summarize(parseSkyrun({ ...sample, pace_photos_per_hour: null }), now);
    expect(noPace.photosPerHour).toBeCloseTo(sample.totals.photos / hours);
    expect(noPace.paceFrom).toBe("average");
    // photos by the run's end = photos now + pace × hours left (from the update)
    expect(s.photosByEnds).toBeCloseTo(
      sample.totals.photos + (sample.pace_photos_per_hour * (T(sample.ends) - T(sample.updated))) / 3600e3,
    );
    // the hunt zone (combined before the start) doesn't count towards the pace
    expect(s.zonesCombinedPerHour).toBeCloseTo((s.counts.combined - 1) / hours);
    expect(s.etaExtracted).toBeGreaterThan(T(sample.updated));
    expect(s.etaCombined).toBeGreaterThanOrEqual(s.etaExtracted!);
    expect(s.endsIn).toBe(T("2026-11-15T05:00:00Z") - now);
    expect(s.deadlineIn).toBe(T("2026-11-15T09:00:00Z") - now);
  });

  it("summarises machines with their current zone and work", () => {
    expect(s.cpus).toBe(224);
    expect(s.machines.map((m) => m.label)).toEqual(["worker", "od", "od2", "test"]);
    const worker = s.machines.find((m) => m.label === "worker")!;
    expect(worker.running).toBe(true);
    expect(worker.statusText).toBe("running");
    expect(worker.current?.state).toBe("extracting");
    expect(worker.extracted).toBeGreaterThan(0);
    // combining is credited to the combiner, not the extractor
    for (const m of s.machines) for (const z of m.combining) expect(z.combiner).toBe(m.label);
    const combining = run.zones.filter((z) => z.state === "combining");
    expect(s.machines.reduce((n, m) => n + m.combining.length, 0)).toBe(combining.length);
    expect(s.active.every((z) => z.state === "extracting" || z.state === "combining")).toBe(true);
    expect(s.recent.length).toBeLessThanOrEqual(5);
    expect(s.recent[0].combined_at).toBeGreaterThanOrEqual(s.recent[1].combined_at!);
  });

  it("takes the bar from the run's progress block, else works it out", () => {
    expect(s.bar).toMatchObject({
      from: "run",
      combinedPct: sample.progress.combined_pct,
      extractedPct: sample.progress.extracted_pct,
      timePct: sample.progress.time_pct,
      projectedPct: sample.progress.projected_extracted_pct,
      projectedZones: sample.progress.projected_extracted_by_ends,
    });
    const own = summarize(parseSkyrun({ ...sample, progress: undefined }), now);
    expect(own.bar.from).toBe("page");
    expect(own.bar.combinedPct).toBeCloseTo((100 * s.counts.combined) / 333);
    expect(own.bar.extractedPct).toBeCloseTo((100 * s.pastExtraction) / 333);
    expect(own.bar.timePct).toBeCloseTo(sample.progress.time_pct, 0);
    expect(own.bar.projectedPct).toBeGreaterThanOrEqual(own.bar.extractedPct);
    // a restarted writer: no pace yet, so no projection
    const restarted = summarize(parseSkyrun({ ...sample, progress: { ...sample.progress, zones_per_hour: null, projected_extracted_pct: null, projected_extracted_by_ends: null } }), now);
    expect(restarted.bar.projectedPct).toBeNull();
    expect(restarted.bar.from).toBe("run");
  });

  it("reads the newer fields of the real file: labels, combiner, final pass", () => {
    const r = parseSkyrun(real2);
    expect(r.machines.map((m) => m.label)).toEqual(["worker", "od", "od2"]);
    expect(r.final_pass_starts).toBe(T("2026-11-15T05:00:00Z"));
    expect(r.pace_photos_per_hour).toBeNull();
    const ecl13 = r.zones.find((z) => z.name === "ecl13")!;
    expect(ecl13).toMatchObject({ state: "combining", machine: "worker", combiner: "od" });
    expect(workerOf(ecl13)).toBe("od");
    expect(r.zones.find((z) => z.name === "hunt")!.suspects_url).toBe("https://storage.googleapis.com/nasa-glazing-live/skyrun/suspects/hunt.json");
    const od = summarize(r, T("2026-11-14T14:41:00Z")).machines.find((m) => m.label === "od")!;
    expect(od.combining.map((z) => z.name)).toEqual(["ecl13"]);
  });

  it("reads deferred zones, checks and notes (real file, 15:05 UTC); provisional = no WISE check", () => {
    const r = parseSkyrun(real3);
    expect(r.totals.deferred).toBe(25);
    expect(r.zones.filter((z) => z.deferred)).toHaveLength(25);
    expect(r.notes.deferred).toMatch(/^Galactic bulge zones: very crowded/);
    expect(r.notes.provisional).toMatch(/^Provisional results \(overnight\): checked against Gaia only\./);
    const hunt = r.zones.find((z) => z.name === "hunt")!;
    expect(hunt.checks).toBe("gaia+catwise");
    expect(isProvisional(hunt)).toBe(false);
    expect(isProvisional(r.zones.find((z) => z.name === "ecl13")!)).toBe(true);
    const sum = summarize(r, T("2026-11-14T15:07:00Z"));
    expect(sum.deferred).toEqual({ total: 25, queued: 25 });
    expect(sum.provisionalCombined).toBe(0);
    // older files: no checks, so provisional until final; no notes; deferred from totals when no zones are listed
    expect(isProvisional({ checks: null, final: false })).toBe(true);
    expect(isProvisional({ checks: null, final: true })).toBe(false);
    expect(isProvisional({ checks: "Gaia + CatWISE", final: false })).toBe(false);
    expect(parseSkyrun(real2).notes).toEqual({ deferred: null, provisional: null });
    const bare = summarize(parseSkyrun({ schema: 1, totals: { zones: 333, deferred: 25 } }), T("2026-11-14T15:07:00Z"));
    expect(bare.deferred).toEqual({ total: 25, queued: 25 });
  });

  it("is never stale once paused on purpose", () => {
    const paused = parseSkyrun({ ...sample, paused_at: sample.updated });
    expect(paused.paused_at).toBe(T(sample.updated));
    expect(summarize(paused, T(sample.updated) + 5 * 3600e3).stale).toBe(false);
    expect(run.paused_at).toBeNull();
  });

  it("is stale after 10 minutes without an update", () => {
    expect(summarize(run, T(sample.updated) + 9 * 60e3).stale).toBe(false);
    expect(summarize(run, T(sample.updated) + 11 * 60e3).stale).toBe(true);
  });

  it("never shows fewer zones than are listed, and survives a totals-only file", () => {
    const fewer = parseSkyrun({ ...sample, totals: { ...sample.totals, zones: 332 } });
    expect(summarize(fewer, now).total).toBe(333);
    const bare = summarize(parseSkyrun({ schema: 1, totals: { zones: 10, extracted: 4, combined: 3, failed: 1 } }), now);
    expect(bare.counts).toMatchObject({ combined: 3, failed: 1, extracted: 1, queued: 5 });
    expect(bare.etaCombined).toBeNull();
    expect(bare.age).toBeNull();
  });

  it("finds zones whose state changed", () => {
    const next = structuredClone(sample);
    next.zones.find((z: { state: string }) => z.state === "queued")!.state = "extracting";
    const changed = changedZones(run, parseSkyrun(next));
    expect(changed.size).toBe(1);
    expect(changedZones(null, run).size).toBe(0);
  });
});

describe("zone geometry helpers", () => {
  it("centres, areas and membership, including through RA 0", () => {
    expect(zoneCentre({ ra: [120.5, 130.5], dec: [14.4, 24.4] })).toEqual({ ra: 125.5, dec: 19.4 });
    expect(zoneCentre({ ra: [355, 5], dec: [0, 10] }).ra).toBeCloseTo(0);
    // 10° × (sin 24.4° − sin 14.4°) × 180/π
    expect(zoneArea({ ra: [120.5, 130.5], dec: [14.4, 24.4] })).toBeCloseTo(94.2, 1);
    expect(zoneArea({ ra: [355, 5], dec: [-5, 5] })).toBeCloseTo(99.7, 0);
    const wrap = { ra: [355, 5] as [number, number], dec: [0, 10] as [number, number] };
    expect(zoneContains(wrap, 359, 5)).toBe(true);
    expect(zoneContains(wrap, 2, 5)).toBe(true);
    expect(zoneContains(wrap, 5, 5)).toBe(false); // ra[0] ≤ RA < ra[1]
    expect(zoneContains(wrap, 180, 5)).toBe(false);
    expect(zoneContains({ ra: [350.5, 360], dec: [0, 10] }, 360, 5)).toBe(false); // RA 360 is RA 0
    expect(zoneContains({ ra: [0, 0.5], dec: [0, 10] }, 360, 5)).toBe(true);
    expect(zoneContains({ ra: [10, 20], dec: [0, 10] }, 15, 10)).toBe(false);
  });
});

describe("format", () => {
  it("counts, durations and signs", () => {
    expect(fmtCount(61204)).toBe("61,204");
    expect(fmtCount(48_213_904)).toBe("48.2 million");
    expect(fmtCount(120_400_000)).toBe("120 million");
    expect(fmtCount(3_790_000_000)).toBe("3.79 billion");
    expect(fmtCount(null)).toBe("not yet");
    expect(fmtAbout(92_870)).toBe("93,000");
    expect(fmtDuration(30e3)).toBe("under a minute");
    expect(fmtDuration(45 * 60e3)).toBe("45 min");
    expect(fmtDuration(6 * 3600e3)).toBe("6 h");
    expect(fmtDuration((6 * 60 + 12) * 60e3)).toBe("6 h 12 min");
    expect(fmtAgo(20e3)).toBe("just now");
    expect(fmtAgo(125e3)).toBe("2 min ago");
    expect(fmtDec(-4.1)).toBe("\u22124.1°");
    expect(fmtDec(11.9)).toBe("+11.9°");
    expect(fmtDec(0)).toBe("0°");
  });

  it("shows local time and UTC", () => {
    const s = fmtTimePair(T("2026-11-15T06:00:00Z"), T("2026-11-14T15:00:00Z"));
    expect(s).toMatch(/\(06:00 UTC\)$/);
    expect(s).toMatch(/^\d\d:\d\d( tomorrow)? \(|^\w{3} \d\d:\d\d \(/);
  });
});
