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
      "2 weaker shifts are listed but not drawn: under 5σ, or Gaia measures a different motion. " +
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
  it("months: every shift with an arrow and the artefact sources, then the shifts that get none, saying why", () => {
    const items = listItems(M, detOf(months), 2, months.gaia);
    expect(items.map((i) => i.label)).toEqual([
      "Artefact: too few dots, poor motion fit",
      "Gaia star moved 10.4″ ± 0.09″ in 1.0 yr",
      "Suspect moved 0.65″ ± 0.20″ in 1.0 yr",
      "Source without Gaia moved 0.39″ ± 0.11″ in 1.0 yr",
    ]);
    expect(items.map((i) => !!i.undrawn)).toEqual([false, false, true, true]);
    expect(items[0].numbers).toBe("In 1 visit. S/N 9.4, 380 µJy, from 3 dots");
    // The fixture's start sits 4.9″ from where Gaia puts Barnard's Star that day: the list says so.
    expect(items[1].numbers).toBe("10,391 ± 90 mas/yr, 115.4σ, towards 356° east of north. Measured 4.9″ from Gaia's position.");
    expect(items[2].numbers).toBe("648 ± 200 mas/yr, 3.2σ, towards 74° east of north. Not on the plate: under 5σ.");
    // No offset note within 1.5″.
    const near = { ...M, shifts: [{ ...M.shifts[0], dec: 4.767397 }] };
    expect(listItems(near, detOf(months), 2, months.gaia)[0].numbers).not.toMatch(/Gaia's position/);
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
