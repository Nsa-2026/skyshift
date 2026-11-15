// The measured layer's words and numbers (contracts/api.md v3.3), from fixtures in the contract's shape.
import { describe, expect, it } from "vitest";
import type { FramesResponse, Measured } from "../api/types";
import hoursJson from "./fixtures/frames-hours-measured.json";
import monthsJson from "./fixtures/frames-months-measured.json";
import {
  describeDetection,
  describeShift,
  arrowFactor,
  motionArrow,
  shiftAt,
  shiftVerdict,
  gaiaById,
  verdictWords,
  blendCount,
  offsetArcsec,
  listItems,
  measuredState,
  moreLine,
  reasonLine,
  sourcesByClass,
  summarize,
  trackDotsFor,
  trackSpanArcsec,
  arrowNote,
  greyed,
  stageLabel,
  classesPresent,
  CLASS_INFO,
  CLASS_ORDER,
  classInfo,
  knownClass,
  normalizeMeasured,
  reasonWords,
  compass,
  plainVerdict,
  viewerShows,
  withoutArtefacts,
} from "./measured";

const hours = hoursJson as unknown as FramesResponse;
const months = monthsJson as unknown as FramesResponse;
const H = hours.measured as Measured;
const M = months.measured as Measured;
const detOf = (f: FramesResponse) => new Map(f.epochs.map((e) => [e.id, e.det as number]));
const strip = (html: string) => html.replace(/<[^>]+>/g, "");

describe("which state the panel shows", () => {
  it("shows the summary when measured is set", () => {
    expect(measuredState(hours)).toEqual({ kind: "measured", m: H });
  });
  it.each(["not_processed", "processing", "no_dots", "unavailable"] as const)("says why for %s", (reason) => {
    expect(measuredState({ measured: null, measured_reason: reason })).toEqual({ kind: "missing", reason });
  });
  it("shows nothing for files older than v3.3 (no fields), or null without a reason", () => {
    const { measured: _m, measured_reason: _r, ...old } = hours;
    expect(measuredState(old as FramesResponse)).toEqual({ kind: "absent" });
    expect(measuredState({ measured: null, measured_reason: null })).toEqual({ kind: "absent" });
    expect(measuredState(null)).toEqual({ kind: "absent" });
  });
});

describe("the not-measured line", () => {
  it("links not_processed to tonight's run", () => {
    const r = reasonLine("not_processed");
    expect(strip(r.html)).toBe("Not processed yet: this spot isn't in tonight's run.");
    expect(r.html).toContain('href="#/live"');
    expect(r.retry).toBe(false);
  });
  it("words processing, no_dots and unavailable plainly; only unavailable offers a retry", () => {
    expect(strip(reasonLine("processing").html)).toMatch(/^Being processed: tonight's run covers this spot/);
    expect(reasonLine("no_dots").html).toMatch(/^Not measured in these photos/);
    expect(reasonLine("unavailable")).toEqual({ html: expect.stringMatching(/didn't load in time/), retry: true });
    expect(reasonLine("some_new_code").html).toBe("SkyShift hasn't measured this spot.");
  });
});

describe("hours summary", () => {
  const s = summarize(H);
  it("counts dots and movers by class, with the suspect's grade", () => {
    expect(strip(s.lead)).toBe(
      "14 dots in 4 photos, and 3 movers: 1 known asteroid, 1 suspect (grade B) and 1 other mover.",
    );
    expect(s.rest).toBe("4 dots match Gaia stars, 2 have no Gaia match and 1 looks like an artefact.");
  });
  it("says the zone and stage, and nothing about missing photos or the cap when unset", () => {
    expect(s.notes).toEqual(["Final results from the hunt zone."]);
  });
  it("says when nothing moved, and names comets as comets", () => {
    expect(strip(summarize({ ...H, tracks: [] }).lead)).toMatch(/, and no movers\.$/);
    const comet = { ...H, tracks: [{ ...H.tracks[1], known_name: "3I/ATLAS" }] };
    expect(strip(summarize(comet).lead)).toMatch(/1 movers?: 1 known comet\.$/);
  });
  it("counts a known asteroid paired in several sessions once, and lists it once", () => {
    const k = H.tracks[1];
    const later = {
      ...k,
      id: "hunt-m644470070960-87790400",
      dots: [{ ...k.dots[1], epoch_id: "d2-2025W42_2A_0213_3", mjd: 60965.46874 }],
    };
    const m: Measured = { ...H, tracks: [...H.tracks, later] };
    expect(strip(summarize(m).lead)).toBe(
      "14 dots in 4 photos, and 3 movers: 1 known asteroid (in 2 sessions), 1 suspect (grade B) and 1 other mover.",
    );
    const known = listItems(m, detOf(hours), 2).filter((i) => i.cls === "known");
    expect(known).toHaveLength(1);
    expect(known[0].label).toBe("Known: (433) Eros");
    expect(known[0].numbers).toMatch(/^3 dots in 2 sessions/);
    expect(known[0].epochs).toEqual(["d2-2025W42_2A_0213_1", "d2-2025W42_2A_0213_2", "d2-2025W42_2A_0213_3"]);
  });
  it("mentions a lone dot where a known asteroid should be (no mover paired it)", () => {
    const m: Measured = { ...H, tracks: [], detections: H.detections.map((d) => ({ ...d, track_id: null })) };
    expect(summarize(m).rest).toBe(
      "4 dots match Gaia stars, 2 sit where (433) Eros should be, 2 have no Gaia match and 1 looks like an artefact.",
    );
  });
  it("lists several grades and ungraded suspects", () => {
    const t = H.tracks[0];
    const m: Measured = {
      ...H,
      tracks: [t, { ...t, id: "s2" }, { ...t, id: "s3" }],
      detections: [
        ...H.detections,
        { ...H.detections[1], track_id: "s2", grade: "C" },
        { ...H.detections[1], track_id: "s3", grade: null },
      ],
    };
    expect(strip(summarize(m).lead)).toContain("3 suspects (1 grade B, 1 grade C, 1 not graded yet)");
  });
  it("prints no grades while the zone is ungraded (grade null, reasons [])", () => {
    const m: Measured = { ...H, detections: H.detections.map((d) => ({ ...d, grade: null, reasons: d.class === "suspect" ? [] : d.reasons })) };
    const lead = strip(summarize(m).lead);
    expect(lead).toContain("1 known asteroid, 1 suspect and 1 other mover.");
    expect(lead).not.toMatch(/grade/);
    const it = listItems(m, detOf(hours), 2)[0];
    expect(it.label).toBe("Suspect");
  });
  it("shows only the classes present in the legend, most important first", () => {
    expect(classesPresent(H)).toEqual(["suspect", "known", "mover", "artifact", "star", "other"]);
    expect(classesPresent({ ...H, counts: { star: 3, known: 0, mover: 0, suspect: 0, artifact: 0, other: 0 }, tracks: [] })).toEqual([
      "star",
    ]);
  });
});

describe("months summary", () => {
  const s = summarize(M);
  it("counts distinct sources (at least, when capped) and only the shifts the plate draws as moved", () => {
    expect(sourcesByClass(M)).toMatchObject({ star: 2, other: 1, artifact: 1, suspect: 1 });
    // Barnard's Star at 115σ moved; the suspect (3.2σ) and the source without Gaia (3.6σ) are under 5σ.
    expect(strip(s.lead)).toBe("At least 5 sources in 48 photos, and 1 moved between visits: 1 Gaia star.");
    expect(s.rest).toBe(
      "2 weaker shifts are neither counted nor drawn: under 5σ, or Gaia measures a different motion. " +
        "Of the sources, at least 2 match Gaia stars, at least 1 has no Gaia match, 1 looks like an artefact and 1 is a suspect.",
    );
    expect(strip(summarize({ ...M, shifts: M.shifts.map((x) => ({ ...x, pm_snr: 6 })) }, months.gaia).lead)).toBe(
      "At least 5 sources in 48 photos, and 3 moved between visits: 1 Gaia star, 1 suspect and 1 source without Gaia.",
    );
  });
  it("says provisional, the photos without dots and the cap", () => {
    expect(s.notes[0]).toBe(
      "Provisional results from the t_barnard zone, checked against Gaia only: expect more suspects than the final pass keeps.",
    );
    expect(s.notes[1]).toBe("2 of these 50 photos have no dots yet, so they show no marks.");
    expect(s.notes[2]).toMatch(/^A busy field: each visit shows at most 250 marks/);
  });
});

describe("arrows", () => {
  it("are to scale from 3 px; shorter ones get the smallest plain factor reaching about 16 px", () => {
    expect(arrowFactor(0)).toBe(0);
    expect(arrowFactor(3)).toBe(1);
    expect(arrowFactor(24.5)).toBe(1);
    expect(arrowFactor(2.9)).toBe(10); // ×5 is 14.5 px, ×10 is 29 px
    expect(arrowFactor(1.6)).toBe(10);
    expect(arrowFactor(0.115)).toBe(200);
    expect(arrowFactor(0.01)).toBe(0); // even ×1000 wouldn't show: nothing drawn
  });

  it("Barnard's 10.8″ on the 15′ months plate is to scale: no factor, the tip on the later position", () => {
    // 3.075″ per image pixel, about 2.3 screen pixels each: 10.8″ is about 8 px.
    const s = 2.3 / 3.075;
    const from = { X: 400, Y: 400 };
    const to = { X: 400 - 1.1 * s, Y: 400 - 10.75 * s };
    const a = motionArrow([from, to], { X: to.X - from.X, Y: to.Y - from.Y })!;
    expect(a.factor).toBe(1);
    expect(a.pts).toEqual([from, to]);
    expect(a.end).toEqual(to);
  });

  it("stretches a short arrow about its start, along the fit, and keeps the true end", () => {
    const from = { X: 100, Y: 100 };
    // Two measured positions 1 px apart, but the fit says 1.5 px due east-ish: the stretch follows the fit.
    const a = motionArrow([from, { X: 101, Y: 100 }], { X: 1.5, Y: 0 })!;
    expect(a.factor).toBe(20);
    expect(a.pts[1]).toEqual({ X: 130, Y: 100 });
    expect(a.end).toEqual({ X: 101.5, Y: 100 });
    // Without a fit (an hours track), every dot moves out by the same factor.
    const t = motionArrow([from, { X: 100, Y: 100.5 }, { X: 100, Y: 101 }])!;
    expect(t.factor).toBe(20);
    expect(t.pts.map((p) => p.Y)).toEqual([100, 110, 120]);
    expect(t.end).toEqual({ X: 100, Y: 101 });
  });

  it("moves a fitted shift to any epoch with its proper motion", () => {
    const s = M.shifts[0];
    const p = shiftAt(s, s.mjd_to);
    const o = offsetArcsec(s, p);
    expect(o.east).toBeCloseTo((s.pmra * s.dt_yr) / 1000, 2);
    expect(o.north).toBeCloseTo((s.pmdec * s.dt_yr) / 1000, 2);
  });

  it("says how arrows are drawn", () => {
    expect(arrowNote("hours")).toMatch(/^Arrows join each mover's dots in time order, to scale\. One too short to see is drawn longer, with its factor \(×10\)/);
    expect(arrowNote("months")).toMatch(/to scale\. Only shifts of 5σ or more, or that Gaia's own motion confirms, get one; the rest are in the list\./);
  });
  it("keeps a partner detector's dot (same moment) off the arrow", () => {
    const d = detOf(hours);
    const twoDet = H.tracks[2];
    expect(trackDotsFor(twoDet, d, 2).map((x) => x.epoch_id)).toEqual(["d2-2025W42_2A_0213_1"]);
    expect(trackSpanArcsec(trackDotsFor(twoDet, d, 2))).toBe(0);
    const sus = trackDotsFor(H.tracks[0], d, 2);
    expect(sus).toHaveLength(3);
    // 0.00018° east and 0.00008° north over 4.3 minutes: about 0.7″.
    expect(trackSpanArcsec(sus)).toBeCloseTo(Math.hypot(0.00018 * 3600 * Math.cos((17.41195 * Math.PI) / 180), 0.00008 * 3600), 2);
  });
});

describe("which shifts get an arrow", () => {
  const G = gaiaById(months.gaia);
  const base = M.shifts[0];
  it("draws a Gaia star when Gaia's own motion agrees (Barnard's Star)", () => {
    expect(shiftVerdict(base, G)).toMatchObject({ draw: true, why: "gaia_agrees" });
  });
  it("hides a Gaia star whose measured shift Gaia doesn't see, however many σ (a saturated star's fake shift)", () => {
    // Barnard's 15′ field: a G 13.6 neighbour measured at 148 ± 19 mas/yr (7.8σ), Gaia 6 mas/yr.
    const fake = { ...base, gaia_id: "n1", pmra: 44.6, pmdec: -141.5, pm_err: 19, pm_snr: 7.8 };
    const g = new Map([["n1", { id: "n1", ra: 0, dec: 0, pmra: -1, pmdec: -6, parallax: 1, g: 13.6 }]]);
    const v = shiftVerdict(fake, g);
    expect(v).toMatchObject({ draw: false, why: "gaia_disagrees" });
    expect(verdictWords(v)).toBe("Not on the plate: Gaia measures 6 mas/yr for this star.");
  });
  it("draws a 4σ Gaia star when Gaia confirms its motion (WISE 0855's field)", () => {
    const s = { ...base, gaia_id: "w", pmra: -124, pmdec: -125, pm_err: 45, pm_snr: 4.0 };
    const g = new Map([["w", { id: "w", ra: 0, dec: 0, pmra: -153, pmdec: -113, parallax: 1, g: 17.2 }]]);
    expect(shiftVerdict(s, g).draw).toBe(true);
  });
  it("without a Gaia motion: 5σ or more, and never an artefact", () => {
    const other = { ...base, gaia_id: null, class: "other" as const };
    expect(shiftVerdict({ ...other, pm_snr: 5 }, G).draw).toBe(true);
    expect(shiftVerdict({ ...other, pm_snr: 4.9 }, G)).toMatchObject({ draw: false, why: "weak" });
    expect(shiftVerdict({ ...other, class: "artifact", pm_snr: 40 }, G)).toMatchObject({ draw: false, why: "artifact" });
    expect(verdictWords({ draw: false, why: "weak" })).toBe("Not on the plate: under 5σ.");
  });
  it("counts a blend from the server's reason, else Gaia stars within 9″ with no detection of their own", () => {
    expect(blendCount(base, M, months.gaia)).toBe(1);
    const g0 = months.gaia[0];
    const twin = { ...g0, id: "twin", ra: g0.ra + 5 / 3600, pmra: g0.pmra, pmdec: g0.pmdec, g: 9.5 };
    expect(blendCount(base, M, [g0, twin])).toBe(2);
    const told: Measured = {
      ...M,
      detections: M.detections.map((d) => (d.source_id === 101 ? { ...d, reasons: ["blend"], blend_gaia_ids: ["a", "b", "c"] } : d)),
    };
    expect(blendCount(base, told, months.gaia)).toBe(3);
  });
});

describe("the list under the summary", () => {
  it("hours: movers on this detector, then lone notable dots, suspects first", () => {
    const items = listItems(H, detOf(hours), 2);
    expect(items.map((i) => [i.cls, i.label])).toEqual([
      ["suspect", "Suspect, grade B"],
      ["known", "Known: (433) Eros"],
      ["mover", "Other mover: near a Gaia star"],
      ["artifact", "Artefact: ghost, halo, new_code"],
    ]);
    expect(items[0].numbers).toBe("3 dots, 4.3 min apart. S/N 23, 997 µJy");
    expect(items[2].numbers).toMatch(/^1 dot\. Seen in two detectors at once/);
    expect(items[0].epochs).toEqual(["d2-2025W42_2A_0213_1", "d2-2025W42_2A_0213_2", "d2-2025W42_2A_0213_3"]);
  });
  it("hours on the partner detector lists only what has a dot there", () => {
    expect(listItems(H, detOf(hours), 5).map((i) => i.cls)).toEqual(["mover"]);
  });
  it("months: every shift with an arrow (what the lead counts), then the shifts that get none, saying why, then sources without a shift", () => {
    const items = listItems(M, detOf(months), 2, months.gaia);
    expect(items.map((i) => i.label)).toEqual([
      "Gaia star moved 10.4″ ± 0.09″ in 1.0 yr",
      "Suspect moved 0.65″ ± 0.20″ in 1.0 yr",
      "Source without Gaia moved 0.39″ ± 0.11″ in 1.0 yr",
      "Artefact: too few dots, poor motion fit",
    ]);
    expect(items.map((i) => !!i.undrawn)).toEqual([false, true, true, false]);
    expect(items.map((i) => i.counted)).toEqual([true, false, false, false]);
    expect(items[3].numbers).toBe("In 1 visit. S/N 9.4, 380 µJy, from 3 dots");
    // The fixture's start sits 4.9″ from where Gaia puts Barnard's Star that day: the list says so.
    expect(items[0].numbers).toBe("10,391 ± 90 mas/yr, 115.4σ, towards 356° east of north. Measured 4.9″ from Gaia's position.");
    expect(items[1].numbers).toBe("648 ± 200 mas/yr, 3.2σ, towards 74° east of north. Not on the plate: under 5σ.");
    // No offset note within 1.5″.
    const near = { ...M, shifts: [{ ...M.shifts[0], dec: 4.767397 }] };
    expect(listItems(near, detOf(months), 2, months.gaia)[0].numbers).not.toMatch(/Gaia's position/);
  });
});

// The lead's count and the visible list agree: the list shows exactly what the lead counts (shifts
// with an arrow; movers on this plate), and the rest fold under one quiet line (moreLine).
describe("the lead and the list agree", () => {
  /** The number the lead counts: "and 3 moved", "and 2 movers", or 0 ("none moved", "no movers", a candidate). */
  const leadCount = (lead: string) => Number(/and (\d+) (?:moved|movers?)\b/.exec(strip(lead))?.[1] ?? 0);
  const counted = (items: { counted: boolean }[]) => items.filter((i) => i.counted).length;
  // Barnard-like: one shift Gaia confirms, two that Gaia says are noise (a slow star), as on the live site.
  const slow = { id: "slow", ra: 269.45, dec: 4.76, pmra: 5, pmdec: 2, parallax: 1, g: 12 };
  const noisy = (id: number, pmra: number): Measured["shifts"][number] => ({
    ...M.shifts[0],
    source_id: id,
    gaia_id: "slow",
    pmra,
    pmdec: 0,
    pm_err: 30,
    pm_snr: pmra / 30,
    shift_arcsec: pmra / 1000,
    err_arcsec: 0.03,
  });
  const barnardLike: Measured = {
    ...M,
    shifts: [M.shifts[0], noisy(201, 160), noisy(202, 460)],
    detections: M.detections.filter((d) => d.class === "star" || d.class === "other"),
  };
  const gaia = [...months.gaia, slow];

  it.each([
    ["the fixture", M, months.gaia],
    ["every shift at 6σ", { ...M, shifts: M.shifts.map((x) => ({ ...x, pm_snr: 6 })) }, months.gaia],
    ["no shift with an arrow", { ...M, shifts: M.shifts.slice(1) }, months.gaia],
    ["Barnard-like (Gaia disagrees with two)", barnardLike, gaia],
    ["no shifts", { ...M, shifts: [] }, months.gaia],
  ] as const)("months, %s: as many entries shown as the lead counts, plain and Advanced", (_name, m, g) => {
    const lead = summarize(m as Measured, [...g]).lead;
    for (const plain of [false, true]) expect(counted(listItems(m as Measured, detOf(months), 2, [...g], plain))).toBe(leadCount(lead));
  });
  it("months, Barnard-like: 1 moved, 1 shown, the 2 Gaia disputes folded under one line", () => {
    const items = listItems(barnardLike, detOf(months), 2, gaia, true);
    expect(strip(summarize(barnardLike, gaia).lead)).toMatch(/and 1 moved between visits: 1 Gaia star\.$/);
    expect(items.filter((i) => i.counted).map((i) => i.label)).toEqual(["Gaia star moved 10.4″ ± 0.09″ in 1.0 yr"]);
    expect(items.filter((i) => !i.counted).map((i) => i.label)).toEqual([
      "Gaia star moved 0.16″ ± 0.03″ in 1.0 yr",
      "Gaia star moved 0.46″ ± 0.03″ in 1.0 yr",
    ]);
    expect(moreLine(items, "months")).toBe("2 more shifts we can't be sure of");
  });
  it("months, every uncounted shift under 5σ: \"smaller shifts, too small to be sure\"; none moved", () => {
    const m: Measured = { ...M, shifts: M.shifts.slice(1), detections: M.detections.filter((d) => d.class !== "artifact") };
    const items = listItems(m, detOf(months), 2, months.gaia, true);
    expect(strip(summarize(m, months.gaia).lead)).toMatch(/none moved measurably between visits\.$/);
    expect(counted(items)).toBe(0);
    expect(moreLine(items, "months")).toBe("2 smaller shifts, too small to be sure");
    expect(moreLine(items.slice(0, 1), "months")).toBe("1 smaller shift, too small to be sure");
    // A source without a shift among them: the line stops naming shifts.
    expect(moreLine(listItems(M, detOf(months), 2, months.gaia), "months")).toBe("3 more we can't be sure of");
    expect(moreLine(items.filter((i) => i.counted), "months")).toBe("");
  });
  it("hours: the lead counts the movers on this plate's detector, as the list does; lone dots fold", () => {
    for (const det of [2, 5]) {
      const lead = summarize(H, undefined, { epochDet: detOf(hours), det }).lead;
      const items = listItems(H, detOf(hours), det);
      expect(counted(items)).toBe(leadCount(lead));
    }
    // D5 has only the mover seen in two detectors at once.
    expect(strip(summarize(H, undefined, { epochDet: detOf(hours), det: 5 }).lead)).toBe("14 dots in 4 photos, and 1 mover: 1 other mover.");
    const items = listItems(H, detOf(hours), 2);
    expect(items.filter((i) => !i.counted).map((i) => i.label)).toEqual(["Artefact: ghost, halo, new_code"]);
    expect(moreLine(items, "hours")).toBe("1 lone dot, not paired into a mover");
  });
});

// A candidate on the plate (BlinkHandle.track from src/review) that SkyShift didn't measure in these
// photos: the lead says so, and why when known, never a bare "no movers".
describe("a candidate SkyShift didn't measure here", () => {
  const none: Measured = { ...H, tracks: [], detections: H.detections.map((d) => ({ ...d, track_id: null })) };
  const cand = { label: "the fast mover", measured: false, marked: false, gapDays: 355.8 };
  it("its own exposures aren't in these frames: says so, how far the nearest is, and what marks the find", () => {
    expect(strip(summarize(none, undefined, { candidate: cand }).lead)).toBe(
      "14 dots in 4 photos. The fast mover isn't among them: SkyShift found it in exposures these frames leave out " +
        "(the nearest is 355.8 days away). The crosshair marks where it was found.",
    );
    expect(strip(summarize(none, undefined, { candidate: { ...cand, gapDays: 3.2 / 1440, marked: true } }).lead)).toBe(
      "14 dots in 4 photos. The fast mover isn't among them: SkyShift found it in exposures these frames leave out " +
        "(the nearest is 3.2 min away). Dashed red circles show where it should be.",
    );
  });
  it("without a known cause, and beside other movers", () => {
    expect(strip(summarize(none, undefined, { candidate: { ...cand, gapDays: null } }).lead)).toBe(
      "14 dots in 4 photos. The fast mover isn't among them: SkyShift didn't measure it in these photos. The crosshair marks where it was found.",
    );
    expect(strip(summarize(H, undefined, { candidate: cand }).lead)).toMatch(
      /^14 dots in 4 photos, and 3 movers: .*\. The fast mover isn't one of them: SkyShift found it/,
    );
  });
  it("measured here, or not an hours plate: the lead is as usual", () => {
    expect(strip(summarize(H, undefined, { candidate: { ...cand, measured: true } }).lead)).toBe(
      "14 dots in 4 photos, and 3 movers: 1 known asteroid, 1 suspect (grade B) and 1 other mover.",
    );
    expect(strip(summarize(M, months.gaia, { candidate: cand }).lead)).toBe("At least 5 sources in 48 photos, and 1 moved between visits: 1 Gaia star.");
    expect(strip(summarize({ ...none, stage: "extracted" }, undefined, { candidate: cand }).lead)).toMatch(/movers aren't linked yet\.$/);
  });
});

describe("stages", () => {
  it("extracted: classed against Gaia only, movers not linked yet (never 'no movers')", () => {
    const m: Measured = { ...H, stage: "extracted", tracks: [] };
    const s = summarize(m);
    expect(strip(s.lead)).toBe("14 dots in 4 photos, classed against Gaia only: movers aren't linked yet.");
    expect(s.notes[0]).toBe(
      "Early results from the hunt zone: each photo's detections classed against Gaia only, before the zone is combined.",
    );
    const mo: Measured = { ...M, stage: "extracted", shifts: [], truncated: false };
    expect(strip(summarize(mo).lead)).toBe(
      "12 detections in 48 photos, classed against Gaia only: sources aren't linked and motions aren't fitted yet.",
    );
    expect(stageLabel("extracted")).toBe("Extracted");
  });
  it("an unknown stage, or missing pieces, never break the summary", () => {
    const m = { ...H, stage: "re_run", counts: undefined, tracks: undefined } as unknown as Measured;
    const s = summarize(m);
    expect(strip(s.lead)).toBe("0 dots in 4 photos, and no movers.");
    expect(s.notes[0]).toBe("Results from the hunt zone, at the stage it calls re run.");
    expect(stageLabel("re_run")).toBe("Re run");
    expect(stageLabel(undefined)).toBe("");
  });
});

describe("grades", () => {
  const graded = (g: "A" | "B" | "C", reasons: string[] = []): Measured => ({
    ...H,
    detections: H.detections.map((d) => (d.class === "suspect" ? { ...d, grade: g, reasons } : d)),
  });
  it("greys grade C suspects, like artefacts, and keeps them named", () => {
    expect(greyed("suspect", "C")).toBe(true);
    expect(greyed("suspect", "B")).toBe(false);
    expect(greyed("suspect", null)).toBe(false);
    expect(greyed("artifact")).toBe(true);
    const it = listItems(graded("C", ["very_sharp", "allwise"]), detOf(hours), 2).find((i) => i.cls === "suspect")!;
    expect(it.grade).toBe("C");
    expect(it.label).toBe("Suspect, grade C: very sharp, sharper than the optics allow, an AllWISE source sits here");
  });
  it("puts grade C suspects after the other movers, before artefacts", () => {
    const order = listItems(graded("C"), detOf(hours), 2).map((i) => `${i.cls}${i.grade ?? ""}`);
    expect(order).toEqual(["known", "mover", "suspectC", "artifact"]);
    expect(listItems(graded("A"), detOf(hours), 2)[0].grade).toBe("A");
  });
  it("names a graded suspect's shift with its grade", () => {
    const m: Measured = { ...M, detections: M.detections.map((d) => (d.source_id === 105 ? { ...d, grade: "C" } : d)) };
    const it = listItems(m, detOf(months), 2).find((i) => i.cls === "suspect")!;
    expect(it.label).toBe("Suspect, grade C, moved 0.65″ ± 0.20″ in 1.0 yr");
    const strong: Measured = { ...m, shifts: m.shifts.map((x) => (x.source_id === 105 ? { ...x, pm_snr: 5.2 } : x)) };
    expect(strip(summarize(strong).lead)).toContain("1 suspect (grade C)");
  });
});

describe("plain words for one detection", () => {
  it("names the class and passes unknown reason codes through as given", () => {
    const d = H.detections[10];
    expect(describeDetection(d)).toBe("Artefact: ghost, halo, new_code");
    expect(describeDetection({ ...d, reasons: [] })).toBe("Artefact, reason not given");
    expect(describeDetection({ ...d, class: "suspect", grade: "C", reasons: ["hot_pixel", "edge"] })).toBe(
      "Suspect, grade C: hot pixel, at the detector's edge",
    );
    expect(describeShift(M.shifts[0])).toBe("Gaia star moved 10.4″ ± 0.09″ in 1.0 yr");
  });
});

describe("classes added on the server (v3.12, v3.13)", () => {
  it("names the stack class, with its own colour token, last in the legend order", () => {
    expect(CLASS_INFO.stack.name).toBe("Seen in the stack only");
    expect(CLASS_INFO.stack.token).toBe("--m-stack");
    expect(CLASS_ORDER[CLASS_ORDER.length - 1]).toBe("stack");
    expect(describeDetection({ ...M.detections[0], class: "stack", reasons: [] })).toBe("Seen in the stack only");
  });
  it("words split_by_fit and stack_near_bright", () => {
    expect(reasonWords(["split_by_fit"])).toEqual(["a second object a two-object fit split off this source"]);
    expect(describeDetection({ ...M.detections[0], class: "other", reasons: ["split_by_fit"] })).toBe(
      "Other source: a second object a two-object fit split off this source",
    );
    expect(describeDetection({ ...M.detections[0], class: "artifact", reasons: ["stack_near_bright"] })).toContain("much brighter source");
  });
  it("reads a class it doesn't know as other, everywhere the plate looks it up", () => {
    expect(knownClass("galaxy_cluster")).toBe("other");
    expect(knownClass("stack")).toBe("stack");
    expect(classInfo("galaxy_cluster")).toBe(CLASS_INFO.other);
    const odd = { ...M, detections: M.detections.map((d, i) => (i === 0 ? { ...d, class: "brand_new" as never } : d)) };
    const st = measuredState({ measured: odd, measured_reason: null } as never);
    expect(st.kind).toBe("measured");
    if (st.kind !== "measured") return;
    expect(st.m.detections[0].class).toBe("other");
    expect(st.m.detections.slice(1)).toEqual(M.detections.slice(1));
    expect(normalizeMeasured(M)).toBe(M);
  });
  it("counts stack sources apart in the months summary and the legend", () => {
    const withStack: Measured = {
      ...M,
      counts: { ...M.counts, stack: 1 },
      detections: [...M.detections, { ...M.detections[0], class: "stack", source_id: null, gaia_id: null, reasons: [] }],
    };
    expect(sourcesByClass(withStack).stack).toBe(1);
    expect(classesPresent(withStack)).toContain("stack");
    expect(strip(summarize(withStack).rest ?? "")).toContain("1 shows only in the visit stacks");
  });
});

describe("the Viewer's plain version", () => {
  it("lists no artefacts and says it without S/N, µJy, σ or mas/yr", () => {
    const items = listItems(H, detOf(hours), 2, hours.gaia, true);
    expect(items.map((i) => i.cls)).toEqual(["suspect", "known", "mover"]);
    expect(items[0].numbers).toBe("Seen in 3 photos, 4.3 min apart.");
    const all = [...items, ...listItems(M, detOf(months), 2, months.gaia, true)].map((i) => i.numbers).join(" ");
    expect(all).not.toMatch(/S\/N|µJy|σ|mas\/yr|Artefact/);
    for (const it of listItems(M, detOf(months), 2, months.gaia, true).filter((i) => i.shift != null)) {
      expect(it.numbers).toMatch(/^Towards the (north|south|east|west)/);
    }
  });

  it("names directions and why a shift has no arrow", () => {
    expect(compass(357)).toBe("north");
    expect(compass(163)).toBe("south-southeast");
    expect(compass(-90)).toBe("west");
    expect(plainVerdict({ draw: false, why: "gaia_disagrees", gaiaPm: 6 })).toBe("No arrow on the plate: Gaia measures only 0.006″ a year for this star.");
    expect(plainVerdict({ draw: false, why: "weak" } as never)).toBe("No arrow on the plate: too small to be sure.");
    expect(plainVerdict({ draw: true, why: "ok" } as never)).toBe("");
  });

  it("circles only what a visitor can act on", () => {
    expect(["suspect", "known", "mover", "star"].every((c) => viewerShows(c as never))).toBe(true);
    expect(["artifact", "other", "stack"].some((c) => viewerShows(c as never))).toBe(false);
    expect(viewerShows("suspect", "C")).toBe(false);
  });
});

describe("without artefacts (the Viewer and the To check plates)", () => {
  it("drops artefact dots, movers and shifts, and their words, and leaves the response alone", () => {
    const h = withoutArtefacts(H);
    expect(h.detections.some((d) => d.class === "artifact")).toBe(false);
    expect(h.counts.artifact).toBe(0);
    expect(H.detections.some((d) => d.class === "artifact")).toBe(true);
    expect(classesPresent(h)).not.toContain("artifact");
    const s = summarize(h, hours.gaia, { artefacts: false });
    expect(strip(s.lead)).toBe("13 dots in 4 photos, and 3 movers: 1 known asteroid, 1 suspect (grade B) and 1 other mover.");
    expect(s.rest).toBe("4 dots match Gaia stars and 2 have no Gaia match.");
    expect(listItems(h, detOf(hours), 2, hours.gaia).map((i) => i.cls)).toEqual(["suspect", "known", "mover"]);
    const m = withoutArtefacts(M);
    expect(m.shifts.some((x) => x.class === "artifact")).toBe(false);
    const all = [summarize(m, months.gaia, { artefacts: false }), summarize({ ...m, truncated: true }, months.gaia, { artefacts: false })];
    expect(all.map((x) => [x.lead, x.rest, ...x.notes].join(" ")).join(" ")).not.toMatch(/artefact/i);
    expect(listItems(m, detOf(months), 2, months.gaia).some((i) => i.cls === "artifact")).toBe(false);
  });
});
