// The red circles' spots (track.ts): solid where measured, dashed where predicted, and the rate and
// position-angle conventions (″/h and mas/yr, east of north, cos dec in RA) against hand-computed cases.
import { describe, expect, it } from "vitest";
import type { Candidate, Epoch, FramesResponse, Measured, MeasuredDetection } from "../api/types";
import { sepArcsec } from "../app/astro";
import { MATCH_ARCSEC, MOVER_DAYS, STILL_DAYS, buildTrackMarks, candidateMarks } from "./track";

const H = 1 / 24;
const epoch = (id: string, mjd: number, kind: Epoch["kind"] = "exposure"): Epoch => ({
  id,
  kind,
  mjd_mid: mjd,
  mjd_start: mjd - 0.0006,
  mjd_end: mjd + 0.0006,
  det: 5,
  um_min: 3.8,
  um_max: 4.4,
  n_exposures: 1,
  flag_frac: 0,
  sat_frac: 0,
  data_url: `frames/${id}.bin`,
  scale: 0.001,
  zero: 0,
});
const measured = (m: Partial<Measured>): Measured => ({
  zone: "pilot",
  stage: "final",
  zone_box: { ra: [0, 360], dec: [-90, 90] },
  clock: "hours",
  photos: 4,
  photos_missing: 0,
  truncated: false,
  counts: { star: 0, known: 0, mover: 0, suspect: 0, artifact: 0, other: 0 },
  detections: [],
  tracks: [],
  shifts: [],
  ...m,
});
const response = (epochs: Epoch[], m: Measured | null = null, clock: FramesResponse["clock"] = "hours"): FramesResponse => ({
  clock,
  center: { ra: 0, dec: 0 },
  pixel_arcsec: 6.15,
  size: [195, 195],
  epochs,
  gaia: [],
  known: [],
  data_release: "test",
  archive_checked: "2026-11-15",
  measured: m,
});
const cand = (c: Partial<Candidate>): Candidate => ({
  id: "pilot-1",
  kind: "mover",
  ra: 180,
  dec: 60,
  mjd: 60000,
  clock: "hours",
  det: 5,
  zone: "pilot",
  score: 0.5,
  snr: 20,
  flux_ujy: 1000,
  rate_arcsec_h: null,
  pm_mas_yr: null,
  pm_err_mas_yr: null,
  pa_deg: null,
  nearest_known_arcsec: null,
  gaia_match: false,
  catwise_match: false,
  galactic_b: 30,
  labels: [],
  reasons: [],
  why: "",
  flags: [],
  frames_url: null,
  ...c,
});
const det = (d: Partial<MeasuredDetection> & Pick<MeasuredDetection, "epoch_id" | "ra" | "dec">): MeasuredDetection => ({
  snr: 50,
  flux_ujy: 1000,
  n_dots: 1,
  class: "star",
  source_id: 1,
  gaia_id: null,
  known_name: null,
  track_id: null,
  grade: null,
  reasons: [],
  ...d,
});

// The example from the request (web/public/data/cand/pilot-645590069534-88041494/frames.json, 14 Nov):
// a D5 suspect with dots in exposures 1 and 4 only, rate 75.6″/h, PA 262.7°.
const PILOT = cand({
  id: "pilot-645590069534-88041494",
  ra: 121.58158,
  dec: 17.599035,
  mjd: 61139.926429,
  rate_arcsec_h: 75.6,
  pa_deg: 262.7,
});
const PILOT_EPOCHS = [
  epoch("d5-2026W15_2A_0110_1", 61139.924191),
  epoch("d5-2026W15_2A_0110_2", 61139.925683),
  epoch("d5-2026W15_2A_0110_3", 61139.927175),
  epoch("d5-2026W15_2A_0110_4", 61139.928668),
];
const PILOT_DOTS = [
  { ra: 121.582754, dec: 17.599178, mjd: 61139.924191, epoch_id: "d5-2026W15_2A_0110_1" },
  { ra: 121.580406, dec: 17.598892, mjd: 61139.928668, epoch_id: "d5-2026W15_2A_0110_4" },
];
const pilotFrames = (dots = PILOT_DOTS) =>
  response(
    PILOT_EPOCHS,
    measured({
      tracks: [{ id: PILOT.id, class: "suspect", suspect_id: PILOT.id, known_name: null, evidence: "two_exposures", dots }],
    }),
  );

describe("red circles for a mover", () => {
  it("is solid on the exposures with a dot and dashed between them (pilot-645590069534-88041494)", () => {
    const t = buildTrackMarks(PILOT, pilotFrames())!;
    expect(t.label).toBe("the fast mover");
    expect(t.spots.map((s) => [s.epoch_id.slice(-1), s.measured])).toEqual([
      ["1", true],
      ["2", false],
      ["3", false],
      ["4", true],
    ]);
    const [a, b, c2, d] = t.spots;
    expect(a).toMatchObject({ ra: PILOT_DOTS[0].ra, dec: PILOT_DOTS[0].dec, mjd: 61139.924191 });
    expect(d).toMatchObject({ ra: PILOT_DOTS[1].ra, dec: PILOT_DOTS[1].dec });
    // Exposures 2 and 3 sit a third and two thirds of the way along (equal steps of 2.1 minutes).
    const span = sepArcsec(a, d);
    expect(span).toBeCloseTo(8.12, 1);
    expect(sepArcsec(a, b) / span).toBeCloseTo(1 / 3, 2);
    expect(sepArcsec(a, c2) / span).toBeCloseTo(2 / 3, 2);
    expect(sepArcsec(b, d) / span).toBeCloseTo(2 / 3, 2);
  });

  it("agrees with the candidate's own rate and direction: east of north, ″/h, cos dec in RA", () => {
    // Without any dot, the prediction from (ra, dec) at mjd with 75.6″/h at PA 262.7° lands on the
    // measured dots of exposures 1 and 4 (the candidate is their mean): the conventions hold.
    const t = buildTrackMarks(PILOT, response(PILOT_EPOCHS))!;
    expect(t.spots.every((s) => !s.measured)).toBe(true);
    expect(sepArcsec(t.spots[0], PILOT_DOTS[0])).toBeLessThan(0.1);
    expect(sepArcsec(t.spots[3], PILOT_DOTS[1])).toBeLessThan(0.1);
  });

  it.each([
    // [pa, expected Δra (deg), expected Δdec (deg)] after one hour at 36″/h from (180, +60):
    // east is +RA and 36″ of sky is 72″ of RA at dec 60 (cos 60 = 0.5); north is +Dec.
    [90, 0.02, 0],
    [270, -0.02, 0],
    [0, 0, 0.01],
    [180, 0, -0.01],
  ])("moves PA %i° the right way (hand-computed)", (pa, dra, ddec) => {
    const c = cand({ rate_arcsec_h: 36, pa_deg: pa });
    const t = buildTrackMarks(c, response([epoch("e0", 60000), epoch("e1", 60000 + H)]))!;
    expect(t.spots[0]).toMatchObject({ epoch_id: "e0", measured: false });
    expect(t.spots[0].ra).toBeCloseTo(180, 6);
    expect(t.spots[0].dec).toBeCloseTo(60, 6);
    expect(t.spots[1].ra - 180).toBeCloseTo(dra, 5);
    expect(t.spots[1].dec - 60).toBeCloseTo(ddec, 5);
  });

  it("goes past the dots with the candidate's rate, from the nearest dot, up to a day", () => {
    const later = epoch("d5-later", PILOT_DOTS[1].mjd + H);
    const far = epoch("d5-far", PILOT_DOTS[1].mjd + MOVER_DAYS + 0.01);
    const before = epoch("d5-before", PILOT_DOTS[0].mjd - 0.5 * H);
    const t = buildTrackMarks(PILOT, response([before, ...PILOT_EPOCHS, later, far], pilotFrames().measured))!;
    const by = new Map(t.spots.map((s) => [s.epoch_id, s]));
    expect(by.has("d5-far")).toBe(false);
    expect(by.get("d5-later")!.measured).toBe(false);
    expect(sepArcsec(by.get("d5-later")!, PILOT_DOTS[1])).toBeCloseTo(75.6, 1);
    expect(sepArcsec(by.get("d5-before")!, PILOT_DOTS[0])).toBeCloseTo(37.8, 1);
    // West-ish (PA 262.7°): later is at lower RA, earlier at higher RA.
    expect(by.get("d5-later")!.ra).toBeLessThan(PILOT_DOTS[1].ra);
    expect(by.get("d5-before")!.ra).toBeGreaterThan(PILOT_DOTS[0].ra);
  });

  it("extrapolates from the end dots when the candidate has no rate", () => {
    const c = { ...PILOT, rate_arcsec_h: null, pa_deg: null };
    const later = epoch("d5-later", PILOT_DOTS[1].mjd + H);
    const t = buildTrackMarks(c, response([...PILOT_EPOCHS, later], pilotFrames().measured))!;
    const s = t.spots.find((x) => x.epoch_id === "d5-later")!;
    // The dots' own rate: 8.12″ in 6.45 minutes ≈ 75.6″/h.
    expect(sepArcsec(s, PILOT_DOTS[1])).toBeCloseTo(75.6, 0);
  });

  it("finds its dots through suspect_id and track-tagged detections", () => {
    const m = measured({
      tracks: [{ id: "pilot-m7-1", class: "suspect", suspect_id: PILOT.id, known_name: null, evidence: "two_exposures", dots: [PILOT_DOTS[0]] }],
      detections: [det({ epoch_id: PILOT_DOTS[1].epoch_id, ra: PILOT_DOTS[1].ra, dec: PILOT_DOTS[1].dec, class: "suspect", track_id: "pilot-m7-1", source_id: null })],
    });
    const t = buildTrackMarks(PILOT, response(PILOT_EPOCHS, m))!;
    expect(t.spots.map((s) => s.measured)).toEqual([true, false, false, true]);
  });

  it("treats two detectors at one moment as one dot, and holds a rate-less mover only a short while", () => {
    // two_detectors: D1 and D4 dots at the same instant, no rate.
    const c = cand({ id: "x", rate_arcsec_h: null, pa_deg: null });
    const e1 = { ...epoch("d1-a", 60000), det: 1 as const };
    const e4 = { ...epoch("d4-a", 60000), det: 4 as const };
    const near = epoch("d4-b", 60000 + 0.5 * STILL_DAYS);
    const far = epoch("d4-c", 60000 + 2 * STILL_DAYS);
    const m = measured({
      tracks: [
        {
          id: "x",
          class: "suspect",
          suspect_id: "x",
          known_name: null,
          evidence: "two_detectors",
          dots: [
            { ra: 180, dec: 60, mjd: 60000, epoch_id: "d1-a" },
            { ra: 180.0002, dec: 60, mjd: 60000, epoch_id: "d4-a" },
          ],
        },
      ],
    });
    const t = buildTrackMarks(c, response([e1, e4, near, far], m))!;
    expect(t.spots.map((s) => [s.epoch_id, s.measured])).toEqual([
      ["d1-a", true],
      ["d4-a", true],
      ["d4-b", false],
    ]);
    expect(t.spots[2].ra).toBeCloseTo(180.0001, 6);
  });

  it("draws nothing on the months clock (a visit is weeks: the mover is long gone)", () => {
    const t = buildTrackMarks(PILOT, response([epoch("d5-v1", 61000, "visit"), epoch("d5-v2", 61180, "visit")], measured({ clock: "months" }), "months"));
    expect(t).toBeNull();
  });

  it("still names the candidate for the panel with no spot in these frames, and carries how far its own exposures are", () => {
    // Live frames of another visit, a year from the find: no red circle, but the panel must know.
    const far = response([epoch("d5-a", PILOT.mjd - 356), epoch("d5-b", PILOT.mjd - 355.9)]);
    expect(buildTrackMarks(PILOT, far)).toBeNull();
    expect(candidateMarks(PILOT, far, 355.9)).toEqual({ label: "the fast mover", spots: [], find_gap_days: 355.9 });
    const t = candidateMarks(PILOT, pilotFrames(), null);
    expect(t.spots.length).toBeGreaterThan(0);
    expect(t.find_gap_days).toBeNull();
  });
});

describe("red circles for a source (moving star, one-or-two)", () => {
  it("is solid on the visits with its detection, dashed where it should be otherwise, moved by its proper motion", () => {
    // 1000 mas/yr due north: one year later the star is 1″ north.
    const c = cand({ id: "hunt-pm-7", kind: "high_pm", ra: 180, dec: 60, mjd: 60000, pm_mas_yr: 1000, pa_deg: 0, clock: "months" });
    const v1 = epoch("d4-v1", 60000, "visit");
    const v2 = epoch("d4-v2", 60365.25, "visit");
    const m = measured({
      clock: "months",
      detections: [
        det({ epoch_id: "d4-v1", ra: 180.0002, dec: 60.0001, source_id: 7 }),
        // Another source in v2, too far to be it.
        det({ epoch_id: "d4-v2", ra: 180.004, dec: 60, source_id: 8 }),
      ],
    });
    const t = buildTrackMarks(c, response([v1, v2], m, "months"))!;
    expect(t.label).toBe("the moving star");
    expect(t.spots[0]).toMatchObject({ epoch_id: "d4-v1", measured: true, ra: 180.0002, dec: 60.0001 });
    expect(t.spots[1]).toMatchObject({ epoch_id: "d4-v2", measured: false });
    expect(t.spots[1].ra).toBeCloseTo(180, 7);
    expect((t.spots[1].dec - 60) * 3600).toBeCloseTo(1, 3);
  });

  it("takes the nearest detection within the match radius", () => {
    const c = cand({ id: "hunt-sh-9", kind: "shape", ra: 129.694367, dec: 16.626013, mjd: 61072.4, pa_deg: 1.8, clock: "months" });
    const v1 = epoch("d3-v1", 60979.96, "visit");
    const v2 = epoch("d3-v2", 61148.92, "visit");
    const m = measured({
      clock: "months",
      detections: [
        det({ epoch_id: "d3-v1", ra: 129.694419, dec: 16.626012, source_id: 9 }),
        det({ epoch_id: "d3-v1", ra: 129.694367, dec: 16.626013 + (MATCH_ARCSEC + 1) / 3600, source_id: 10 }),
        det({ epoch_id: "d3-v2", ra: 129.694346, dec: 16.626058, source_id: 9 }),
      ],
    });
    const t = buildTrackMarks(c, response([v1, v2], m, "months"))!;
    expect(t.label).toBe("the stretched source");
    // A shape's pa_deg is the stretch axis, not a motion: the expected spot never moves.
    expect(t.spots.map((s) => [s.measured, s.ra, s.dec])).toEqual([
      [true, 129.694419, 16.626012],
      [true, 129.694346, 16.626058],
    ]);
  });

  it("stays dashed at the candidate's position without a measured layer", () => {
    const c = cand({ id: "hunt-sh-9", kind: "shape", ra: 10, dec: -5, mjd: 61000, pa_deg: 45, clock: "months" });
    const t = buildTrackMarks(c, response([epoch("d3-v1", 60900, "visit"), epoch("d3-v2", 61100, "visit")], null, "months"))!;
    expect(t.spots).toEqual([
      { epoch_id: "d3-v1", mjd: 60900, ra: 10, dec: -5, measured: false },
      { epoch_id: "d3-v2", mjd: 61100, ra: 10, dec: -5, measured: false },
    ]);
  });
});
