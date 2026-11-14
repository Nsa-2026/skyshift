// The measured layer's words and numbers (contracts/api.md v3.3), from fixtures in the contract's shape.
import { describe, expect, it } from "vitest";
import type { FramesResponse, Measured } from "../api/types";
import hoursJson from "./fixtures/frames-hours-measured.json";
import monthsJson from "./fixtures/frames-months-measured.json";
import {
  describeDetection,
  describeShift,
  exaggeration,
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
      "Here SkyShift found 14 dots in 4 photos, and 3 movers: 1 known asteroid, 1 suspect (grade B) and 1 other mover.",
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
      "Here SkyShift found 14 dots in 4 photos, and 3 movers: 1 known asteroid (in 2 sessions), 1 suspect (grade B) and 1 other mover.",
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
  it("counts distinct sources (at least, when capped) and what moved", () => {
    expect(sourcesByClass(M)).toMatchObject({ star: 2, other: 1, artifact: 1, suspect: 1 });
    expect(strip(s.lead)).toBe(
      "Here SkyShift found at least 5 sources in 48 photos, and 3 moved between visits: 1 Gaia star, 1 suspect and 1 source without Gaia.",
    );
    expect(s.rest).toBe("Of the sources, at least 2 match Gaia stars, at least 1 has no Gaia match and 1 looks like an artefact.");
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
  it("picks a plain exaggeration that keeps the typical arrow near the target", () => {
    expect(exaggeration([], 72)).toBe(1);
    expect(exaggeration([0, 0], 72)).toBe(1);
    expect(exaggeration([3.1], 72)).toBe(20);
    expect(exaggeration([0.39, 0.65, 10.36], 18)).toBe(20);
    expect(exaggeration([25], 18)).toBe(1);
  });
  it("says how arrows are drawn", () => {
    expect(arrowNote("hours", 20)).toBe("Arrows join each mover's dots in time order, drawn 20 times longer than the motion.");
    expect(arrowNote("months", 1)).toMatch(/^Arrows show each measured shift from the first visit to the last, drawn to scale\. The circle at the tip/);
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
  it("months: every shift, then artefact or suspect sources without one", () => {
    const items = listItems(M, detOf(months), 2);
    expect(items.map((i) => i.label)).toEqual([
      "Suspect moved 0.65″ ± 0.20″ in 1.0 yr",
      "Artefact: too few dots, poor motion fit",
      "Gaia star moved 10.4″ ± 0.09″ in 1.0 yr",
      "Source without Gaia moved 0.39″ ± 0.11″ in 1.0 yr",
    ]);
    expect(items[2].numbers).toBe("10,391 ± 90 mas/yr, 115.4σ, towards 356° east of north");
    expect(items[1].numbers).toBe("In 1 visit. S/N 9.4, 380 µJy, from 3 dots");
  });
});

describe("stages", () => {
  it("extracted: classed against Gaia only, movers not linked yet (never 'no movers')", () => {
    const m: Measured = { ...H, stage: "extracted", tracks: [] };
    const s = summarize(m);
    expect(strip(s.lead)).toBe("Here SkyShift found 14 dots in 4 photos, classed against Gaia only: movers aren't linked yet.");
    expect(s.notes[0]).toBe(
      "Early results from the hunt zone: each photo's detections classed against Gaia only, before the zone is combined.",
    );
    const mo: Measured = { ...M, stage: "extracted", shifts: [], truncated: false };
    expect(strip(summarize(mo).lead)).toBe(
      "Here SkyShift found 12 detections in 48 photos, classed against Gaia only: sources aren't linked and motions aren't fitted yet.",
    );
    expect(stageLabel("extracted")).toBe("Extracted");
  });
  it("an unknown stage, or missing pieces, never break the summary", () => {
    const m = { ...H, stage: "re_run", counts: undefined, tracks: undefined } as unknown as Measured;
    const s = summarize(m);
    expect(strip(s.lead)).toBe("Here SkyShift found 0 dots in 4 photos, and no movers.");
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
    expect(strip(summarize(m).lead)).toContain("1 suspect (grade C)");
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
