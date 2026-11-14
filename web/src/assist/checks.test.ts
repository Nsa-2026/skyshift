import { describe, expect, it } from "vitest";
import fixture from "../../../fixtures/api/candidates.json";
import type { Candidate } from "../api/types";
import { blinkHref, checklist, fitTrack, summarize, withReplies } from "./checks";
import type { AssistCandidate, Detection, StepId } from "./types";

const all = fixture.candidates as unknown as AssistCandidate[];
const realMover = all.find((c) => c.id === "pilot-642020070317-87802695")!;
const realStar = all.find((c) => c.id === "pilot-pm-0000")!;

const MOVER: Candidate = {
  id: "t-mover",
  kind: "mover",
  ra: 122.5,
  dec: 17,
  mjd: 60974.09,
  clock: "hours",
  det: 3,
  zone: "pilot",
  score: null,
  snr: 40,
  flux_ujy: 1500,
  rate_arcsec_h: null,
  pm_mas_yr: null,
  pm_err_mas_yr: null,
  pa_deg: null,
  nearest_known_arcsec: 400,
  gaia_match: false,
  catwise_match: false,
  galactic_b: 25,
  labels: [],
  reasons: ["two_exposures"],
  why: "test",
  flags: [],
  frames_url: null,
};
const STAR: Candidate = {
  ...MOVER,
  id: "t-star",
  kind: "high_pm",
  clock: "months",
  det: 2,
  snr: null,
  flux_ujy: null,
  pm_mas_yr: 600,
  pm_err_mas_yr: 50,
  pa_deg: 90,
  nearest_known_arcsec: null,
  reasons: [],
};
const mover = (o: Partial<AssistCandidate> = {}): AssistCandidate => ({ ...MOVER, ...o });
const star = (o: Partial<AssistCandidate> = {}): AssistCandidate => ({ ...STAR, ...o });
const step = (c: AssistCandidate, id: StepId) => checklist(c).find((s) => s.id === id)!;

/** Positions along a straight line: rate ″/h towards PA (deg), every 2.1 min. */
function track(n: number, rate: number, pa: number, noise: number[] = []): Detection[] {
  const r = (pa * Math.PI) / 180;
  return Array.from({ length: n }, (_, i) => {
    const t = (i * 2.1) / 60; // hours
    const e = rate * t * Math.sin(r) + (noise[i] ?? 0);
    const nn = rate * t * Math.cos(r) - (noise[i] ?? 0);
    return {
      ra: 122.5 + e / 3600 / Math.cos((17 * Math.PI) / 180),
      dec: 17 + nn / 3600,
      mjd: 60974.09 + t / 24,
      det: 3,
      pos_err_arcsec: 0.3,
      flux_ujy: 1500,
      flux_err_ujy: 40,
    };
  });
}

describe("checklist shape", () => {
  it("asks the six questions of DESIGN.md in order", () => {
    expect(checklist(realMover).map((s) => s.id)).toEqual(["motion", "known", "artefact", "same", "colour", "call"]);
    expect(checklist(realMover).map((s) => s.question)).toEqual([
      "Does it move in a straight line at a steady rate?",
      "Is it already known?",
      "Could it be an artefact?",
      "Is it the same object in every frame?",
      "What does its colour say?",
      "Your call: what is it?",
    ]);
  });

  it("never prints null, undefined or NaN, and ends every finding with a full stop (whole pilot sample)", () => {
    for (const c of all)
      for (const s of checklist(c)) {
        expect(s.finding, `${c.id} ${s.id}`).not.toMatch(/null|undefined|NaN|Infinity/);
        expect(s.finding, `${c.id} ${s.id}`).toMatch(/^[A-Z].*[.]$/);
        expect(s.howTo.length, `${c.id} ${s.id}`).toBeGreaterThanOrEqual(2);
        if (s.status === "ask" && s.id !== "call") expect(s.prompt, `${c.id} ${s.id}`).toMatch(/\?$/);
      }
  });

  it("puts field names and raw values in the details", () => {
    expect(step(realStar, "motion").details).toContain("pm_mas_yr=501.4");
    expect(step(realStar, "known").details).toContain("gaia_match=true");
    expect(step(realMover, "known").details).toContain("nearest_known_arcsec=338.3");
    expect(step(realMover, "same").details).toContain("flux_ujy=2320.2");
  });

  it("links to the Blink page at the candidate, on its own clock and detector", () => {
    expect(blinkHref(realMover)).toBe("?ra=122.55415&dec=17.00357&clock=hours&det=3#/explore");
  });
});

describe("the real pilot mover", () => {
  const steps = checklist(realMover);
  it("hands motion and sameness to the person, passes the catalogue and artefact checks", () => {
    expect(steps.map((s) => s.status)).toEqual(["ask", "pass", "pass", "ask", "unknown", "ask"]);
    expect(summarize(steps)).toBe("2 good signs, 3 for you to check and 1 unknown.");
  });
  it("says what we know with units", () => {
    expect(steps[1].finding).toBe("No Gaia or CatWISE source within 6″; the nearest known asteroid or comet was predicted 338″ (5.6′) away.");
    expect(steps[2].finding).toContain("S/N up to 57");
    expect(steps[3].finding).toContain("2,320 µJy in D3 (1.6–2.4 µm)");
    expect(steps[0].prompt).toContain("Hours clock");
  });
});

describe("1. motion", () => {
  it("fits a straight line through three or more positions", () => {
    const fit = fitTrack(track(4, 30, 270))!;
    expect(fit.rateArcsecH).toBeCloseTo(30, 6);
    expect(fit.paDeg).toBeCloseTo(270, 4);
    expect(fit.rmsArcsec).toBeLessThan(1e-6);
    expect(fit.errArcsec).toBe(0.3);
    expect(fit.spanH).toBeCloseTo((3 * 2.1) / 60, 9);
    expect(fitTrack(track(2, 30, 270))).toBeNull();
  });

  it("passes a steady track and says its rate and direction", () => {
    const s = step(mover({ detections: track(4, 30, 270, [0.1, -0.1, 0.05, 0]) }), "motion");
    expect(s.status).toBe("pass");
    expect(s.finding).toMatch(/^Its 4 positions fall on a straight line within 0\.\d\d″ RMS, moving 30(\.\d)?″ per hour towards the west \(position angle 2\d\d°\)\.$/);
  });

  it("cautions when the positions stray well beyond their errors", () => {
    const s = step(mover({ detections: track(4, 30, 270, [2, -2, 2, -2]) }), "motion");
    expect(s.status).toBe("caution");
    expect(s.finding).toContain("stray from a straight line");
  });

  it("uses the 0.43″ floor when dots carry no error", () => {
    const ds = track(3, 30, 0).map((d) => ({ ...d, pos_err_arcsec: null }));
    expect(fitTrack(ds)!.errArcsec).toBe(0.43);
  });

  it("asks the person when there are only two positions, with their rate", () => {
    const s = step(mover({ detections: track(2, 36, 90) }), "motion");
    expect(s.status).toBe("ask");
    expect(s.finding).toMatch(/Two positions 2\.1 min apart put it at 36″ per hour towards the east/);
  });

  it("asks with the pipeline's rate when that is all there is", () => {
    expect(step(mover({ rate_arcsec_h: 25.04 }), "motion").finding).toContain("25″ per hour");
  });

  it("explains a two-detector catch, which shows no motion", () => {
    const s = step(mover({ reasons: ["two_detectors"] }), "motion");
    expect(s.status).toBe("ask");
    expect(s.finding).toContain("one moment in two detectors");
  });

  it("grades a proper motion by its significance", () => {
    const pass = step(star(), "motion");
    expect(pass.status).toBe("pass");
    expect(pass.finding).toContain("600 ± 50 mas/yr (12.0σ) towards the east (position angle 90°)");
    expect(pass.finding).toContain("0.30″ in six months, 4.9% of a 6.15″ pixel");
    expect(step(star({ pm_err_mas_yr: 150 }), "motion").status).toBe("caution");
    const weak = step(star({ pm_err_mas_yr: 300 }), "motion");
    expect(weak.status).toBe("fail");
    expect(weak.finding).toContain("under 3σ");
    expect(step(star({ pm_err_mas_yr: null }), "motion").finding).toContain("no error on record");
  });

  it("matches the real pilot star", () => {
    const s = step(realStar, "motion");
    expect(s.finding).toBe(
      "Moves 501 ± 57 mas/yr (8.9σ) towards the south-east (position angle 151°), about 0.25″ in six months, 4.1% of a 6.15″ pixel.",
    );
  });

  it("turns pipeline vetting reasons into cautions and the speed cap into a bad sign", () => {
    expect(step(star({ reasons: ["bad_fit"] }), "motion").status).toBe("caution");
    expect(step(star({ reasons: ["few_dots"] }), "motion").finding).toContain("fewer than 20 detections");
    const cap = step(star({ pm_mas_yr: 11000, pm_err_mas_yr: 1000, reasons: ["over_pm_cap"] }), "motion");
    expect(cap.status).toBe("fail");
    expect(cap.finding).toContain("faster than 10.5″ a year");
  });

  it("cautions on motions faster than any known star even without the cap reason", () => {
    const s = step(star({ pm_mas_yr: 171694, pm_err_mas_yr: 1373 }), "motion");
    expect(s.status).toBe("caution");
    expect(s.finding).toContain("14 SPHEREx pixels");
    expect(s.finding).toContain("Barnard's Star moves 10.4″ a year");
  });

  it("says a spectral candidate has no motion on record", () => {
    const s = step(star({ kind: "spectral", pm_mas_yr: null, pm_err_mas_yr: null }), "motion");
    expect(s.status).toBe("unknown");
    expect(s.prompt).toBeUndefined();
  });
});

describe("2. already known?", () => {
  it("fails a mover that sits on a known object's predicted position", () => {
    const s = step(mover({ nearest_known_arcsec: 12.4 }), "known");
    expect(s.status).toBe("fail");
    expect(s.finding).toContain("12.4″ away at that time, so it's probably that object");
  });

  it("cautions within 60″ and passes beyond", () => {
    expect(step(mover({ nearest_known_arcsec: 45 }), "known").status).toBe("caution");
    expect(step(mover({ nearest_known_arcsec: 61 }), "known").status).toBe("pass");
  });

  it("says nothing was predicted within 600″ when the distance is null", () => {
    const s = step(mover({ nearest_known_arcsec: null }), "known");
    expect(s.status).toBe("pass");
    expect(s.finding).toContain("within 600″ (10′)");
  });

  it("cautions a mover on a catalogued source", () => {
    const s = step(mover({ gaia_match: true }), "known");
    expect(s.status).toBe("caution");
    expect(s.finding).toContain("Gaia DR3 (within 6″)");
  });

  it("passes a moving star no catalogue lists", () => {
    const s = step(star(), "known");
    expect(s.status).toBe("pass");
    expect(s.finding).toBe("No Gaia DR3 star within 2.5″ and no CatWISE2020 source within 3″: neither catalogue lists it.");
  });

  it("cautions a catalogued star, asking to compare motions", () => {
    const s = step(star({ gaia_match: true }), "known");
    expect(s.status).toBe("caution");
    expect(s.finding).toContain("No CatWISE2020 source within 3″; Gaia DR3 (within 2.5″) lists a source here");
    expect(s.finding).toContain("compare the catalogue's motion with ours");
  });

  it("explains a motion clash, with or without a match at this position", () => {
    const here = step(star({ gaia_match: true, reasons: ["gaia_pm_disagrees"] }), "known");
    expect(here.status).toBe("caution");
    expect(here.finding).toContain("Gaia DR3 lists a source here with a different motion");
    const there = step(star({ reasons: ["catwise_pm_disagrees"] }), "known");
    expect(there.finding).toContain("CatWISE2020 lists a source near its positions with a different motion");
  });
});

describe("3. artefact?", () => {
  it("fails a candidate on pixels the pipeline masks", () => {
    const s = step(mover({ flags: ["PERSIST"] }), "artefact");
    expect(s.status).toBe("fail");
    expect(s.finding).toContain("persistence");
    expect(s.details).toContain("mask: [PERSIST]");
  });

  it("cautions near recorded flags, in plain words", () => {
    const s = step(mover({ flags: ["ghost", "HALO"] }), "artefact");
    expect(s.status).toBe("caution");
    expect(s.finding).toContain("optical ghost");
    expect(s.finding).toContain("halo of a cosmic-ray hit");
  });

  it("ignores SOURCE and FULLSAMPLE, which are not artefacts", () => {
    expect(step(mover({ flags: ["SOURCE", "FULLSAMPLE"] }), "artefact").status).toBe("pass");
  });

  it("names the two detectors exposed together", () => {
    expect(step(mover({ reasons: ["two_detectors"], det: 5 }), "artefact").finding).toContain("D2 and D5");
  });

  it("cautions in the crowded Milky Way plane", () => {
    const s = step(star({ galactic_b: -4.2 }), "artefact");
    expect(s.status).toBe("caution");
    expect(s.finding).toContain("4.2° from the Milky Way's plane");
  });
});

describe("4. same object?", () => {
  it("passes steady brightness within one detector", () => {
    const ds = track(3, 30, 0).map((d, i) => ({ ...d, flux_ujy: [1500, 1520, 1490][i] }));
    const s = step(mover({ detections: ds }), "same");
    expect(s.status).toBe("pass");
    expect(s.finding).toMatch(/^Across 3 exposures in D3 \(1\.6–2\.4 µm\) its brightness stays within 0\.\dσ of 1,50\d µJy\.$/);
  });

  it("cautions a jump beyond 3σ", () => {
    const ds = track(3, 30, 0).map((d, i) => ({ ...d, flux_ujy: [1500, 1500, 2100][i] }));
    expect(step(mover({ detections: ds }), "same").status).toBe("caution");
  });

  it("never compares brightness across detectors", () => {
    const ds = track(2, 30, 0).map((d, i) => ({ ...d, det: (i ? 6 : 3) as 3 | 6 }));
    expect(step(mover({ detections: ds }), "same").status).toBe("ask");
    expect(step(mover({ reasons: ["two_detectors"] }), "same").status).toBe("unknown");
  });

  it("reads a moving star's flux ratio against the ×/÷1.25 variability line", () => {
    expect(step(star({ flux_ratio: 0.9 }), "same").status).toBe("pass");
    const s = step(star({ flux_ratio: 1.6 }), "same");
    expect(s.status).toBe("caution");
    expect(s.finding).toContain("×1.60");
  });

  it("cautions pairs at the control level and lets the person look", () => {
    const s = step(star({ reasons: ["at_control_level"] }), "same");
    expect(s.status).toBe("caution");
    expect(s.prompt).toBeDefined();
  });

  it("asks when no per-frame brightness is on record", () => {
    const s = step(star(), "same");
    expect(s.status).toBe("ask");
    expect(s.prompt).toBe("Blink it on the Months clock in D2. Is the moving dot equally dark at each visit?");
  });
});

describe("5. colour", () => {
  it("reads labels in plain words and ties cold ones to brown dwarfs for moving stars", () => {
    const s = step(star({ labels: ["red", "ch4"] }), "colour");
    expect(s.status).toBe("pass");
    expect(s.finding).toContain("red and methane");
    expect(s.finding).toContain("3.3 µm");
    expect(s.finding).toContain("fits a nearby brown dwarf");
  });

  it("reads a D6/D2 ratio", () => {
    expect(step(star({ red_ratio: 2 }), "colour").status).toBe("pass");
    const blue = step(star({ red_ratio: 0.2 }), "colour");
    expect(blue.status).toBe("unknown");
    expect(blue.finding).toContain("0.20×");
  });

  it("says why a mover has no colour", () => {
    expect(step(realMover, "colour").finding).toContain("spectra are built for sources that stay put");
  });
});

describe("6. your call, and replies", () => {
  it("states the consensus rule with its numbers", () => {
    const s = step(realMover, "call");
    expect(s.status).toBe("ask");
    expect(s.finding).toContain("five votes with 70%");
    expect(s.howTo).toHaveLength(4);
  });

  it("turns replies into statuses on ask steps only", () => {
    const steps = withReplies(checklist(realMover), { motion: "yes", same: "no", known: "no", call: "yes" });
    const by = (id: StepId) => steps.find((s) => s.id === id)!;
    expect(by("motion").status).toBe("pass");
    expect(by("motion").dataStatus).toBe("ask");
    expect(by("same").status).toBe("fail");
    expect(by("known").status).toBe("pass"); // no prompt: the reply is ignored
    expect(by("known").reply).toBeNull();
    expect(by("call").status).toBe("ask"); // the vote has its own control
    expect(withReplies(checklist(realMover), { motion: "unsure" })[0].status).toBe("unknown");
  });

  it("lets a 'no' make a caution worse, never a 'yes' make it better", () => {
    const c = star({ reasons: ["at_control_level"] });
    expect(withReplies(checklist(c), { same: "yes" })[3].status).toBe("caution");
    expect(withReplies(checklist(c), { same: "no" })[3].status).toBe("fail");
  });

  it("summarises in one sentence", () => {
    expect(summarize([{ status: "pass" }, { status: "fail" }])).toBe("1 good sign and 1 bad sign.");
    expect(summarize([])).toBe("");
  });
});
