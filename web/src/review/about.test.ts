// The suspect panel's description (src/review/about) and the object's own detector on the plate.
import { describe, expect, it, vi } from "vitest";
import type { Det, Epoch, FramesResponse, Measured, MeasuredDetection } from "../api/types";
import { aboutCandidate, arcsecText, cautionText, partnerSaw, spanText } from "./about";
import { aboutHtml, suspectWave } from "./candidate";
import { candidateView } from "./data";
import { cand } from "./testkit";

const ID = "ecl04-670520025038-87705962";
// ecl04-670520025038-87705962 as candidates.json lists it (16 Nov).
const ecl04 = cand(ID, {
  kind: "mover",
  ra: 44.92999,
  dec: 21.75381,
  mjd: 60906.917883,
  clock: "hours",
  det: 6,
  zone: "ecl04",
  grade: "A",
  snr: 9.9,
  flux_ujy: 1757.8,
  rate_arcsec_h: 104.9,
  pa_deg: 76.4,
  nearest_known_arcsec: 418.7,
  reasons: ["two_exposures", "outlier_flag", "detector_excess"],
  flags: ["outlier"],
  why: "Seen at different places in 4 exposures over 6.4 minutes, moving about 105″ an hour.",
});

const ep = (id: string, det: Det, mjd: number): Epoch => ({
  id,
  kind: "exposure",
  mjd_mid: mjd,
  mjd_start: mjd - 0.0007,
  mjd_end: mjd + 0.0007,
  det,
  um_min: 4.4,
  um_max: 5,
  n_exposures: 1,
  flag_frac: 0,
  sat_frac: 0,
  data_url: `frames/${id}.bin`,
  scale: 1,
  zero: 0,
});
// Its live /api/frames track (D6 only), dots as the server gave them.
const DOTS = [
  { ra: 44.928343, dec: 21.753626, mjd: 60906.915644, epoch_id: "d6-1" },
  { ra: 44.929425, dec: 21.753726, mjd: 60906.917137, epoch_id: "d6-2" },
  { ra: 44.930768, dec: 21.753928, mjd: 60906.918629, epoch_id: "d6-3" },
  { ra: 44.931573, dec: 21.754147, mjd: 60906.920121, epoch_id: "d6-4" },
];
const det = (epoch_id: string, ra: number, dec: number): MeasuredDetection => ({
  epoch_id,
  ra,
  dec,
  snr: 9,
  flux_ujy: 100,
  n_dots: 1,
  class: "other",
  source_id: null,
  gaia_id: null,
  known_name: null,
  track_id: null,
  grade: null,
  reasons: [],
});
function framesWith(opts: { partner?: boolean; partnerDetection?: boolean } = {}): FramesResponse {
  const epochs = DOTS.map((d) => ep(d.epoch_id, 6, d.mjd));
  if (opts.partner) epochs.push(...DOTS.map((d, i) => ep(`d3-${i + 1}`, 3, d.mjd)));
  const measured = {
    zone: "ecl04",
    stage: "final",
    zone_box: { ra: [40, 50], dec: [15, 25] },
    clock: "hours",
    photos: epochs.length,
    photos_missing: 0,
    truncated: false,
    counts: { star: 0, known: 0, mover: 0, suspect: 4, artifact: 0, other: 0 },
    detections: opts.partnerDetection ? [det("d3-2", DOTS[1].ra, DOTS[1].dec)] : [],
    tracks: [{ id: ID, class: "suspect", suspect_id: ID, known_name: null, evidence: "two_exposures", dots: DOTS }],
    shifts: [],
  } as unknown as Measured;
  return {
    clock: "hours",
    center: { ra: ecl04.ra, dec: ecl04.dec },
    pixel_arcsec: 6.15,
    size: [8, 8],
    epochs,
    gaia: [],
    known: [],
    data_release: "DR1+QR3",
    archive_checked: "2026-11-16",
    measured,
  };
}

describe("the suspect panel's description", () => {
  it("says what the ecl04 mover did, that its dots are all in D6, and what to watch for", () => {
    const a = aboutCandidate(ecl04, framesWith());
    expect(a.said).toEqual([
      "Moved 11″ in D6 over 6.4 minutes (4 photos), about 105″ an hour.",
      "All its dots are in D6; D3 sees the same sky at the same moments. Seen in D6 alone is typical of an asteroid or comet glowing by its own heat, but it can also be a flaw of D6.",
    ]);
    expect(a.watch).toBe(
      "Watch for: NASA marked these pixels as unusual (OUTLIER); D6 had more unexplained movers than usual in that visit.",
    );
  });

  it("says less before its frames are loaded: the rate, no distance", () => {
    expect(aboutCandidate(ecl04).said[0]).toBe("Moves about 105″ an hour in D6.");
  });

  it("lets the partner's measured layer decide when its exposures are in the frames", () => {
    expect(partnerSaw(ecl04, framesWith({ partner: true }))).toBe("no");
    expect(aboutCandidate(ecl04, framesWith({ partner: true })).said[1]).toBe(
      "D3 photographed the same spot at the same moments and saw nothing there: seen in D6 alone is typical of an asteroid or comet glowing by its own heat, but it can also be a flaw of D6.",
    );
    expect(partnerSaw(ecl04, framesWith({ partner: true, partnerDetection: true }))).toBe("yes");
    expect(aboutCandidate(ecl04, framesWith({ partner: true, partnerDetection: true })).said[1]).toBe(
      "D3 saw it too at the same moment: real light, not a flaw of one detector.",
    );
    // Without its track or the partner's frames, the evidence alone claims nothing.
    expect(partnerSaw(ecl04, null)).toBe("unknown");
    expect(aboutCandidate(ecl04).said).toHaveLength(1);
  });

  it("a mover seen in D4 alone: rare for a real asteroid, so a flaw of D4 is more likely", () => {
    const d4 = { ...ecl04, det: 4 as const };
    const f = framesWith();
    f.measured!.tracks[0].dots = f.measured!.tracks[0].dots.map((d) => ({ ...d, epoch_id: d.epoch_id.replace(/^d6-/, "d4-") }));
    expect(partnerSaw(d4, f)).toBe("own");
    expect(aboutCandidate(d4, f).said[1]).toBe(
      "All its dots are in D4; D1 sees the same sky at the same moments. Seen in D4 alone is rare for a real asteroid (their heat glow starts in D5), so a flaw of D4 is more likely.",
    );
  });

  it("a short-wavelength mover without its frames: no claim about the partner", () => {
    const c = cand("hunt-665380068525-88045793", {
      kind: "mover",
      clock: "hours",
      det: 2,
      reasons: ["two_exposures", "motion_not_significant"],
    });
    const a = aboutCandidate(c);
    expect(a.said).toEqual(["Seen at different places in D2 photos minutes apart."]);
    expect(a.watch).toBe("Watch for: the shift is within its position errors, so it may not move at all.");
  });

  it("two detectors at one moment: no motion, real light", () => {
    const c = cand("m2", { kind: "mover", clock: "hours", det: 4, reasons: ["two_detectors", "edge", "hot_pixel"] });
    const a = aboutCandidate(c);
    expect(a.said).toEqual([
      "Seen once, by D4 and D1 at the same moment, so its motion was not measured.",
      "Two detectors seeing it at once means real light, not a flaw of one detector.",
    ]);
    expect(a.watch).toBe(
      "Watch for: a dot lies within 6 pixels of the detector's edge; a dot sits on a pixel where dots keep turning up (a hot pixel).",
    );
  });

  it("names the known object's distance and a repeated position", () => {
    const c = cand("m3", { kind: "mover", clock: "hours", det: 1, rate_arcsec_h: 32.4, nearest_known_arcsec: 75, reasons: ["two_exposures", "known_object_near", "repeat_position"] });
    expect(aboutCandidate(c).watch).toBe(
      "Watch for: a known asteroid, comet, planet or moon was predicted 1.3′ away: it may be that object; another unexplained mover was seen within 30″ of here in another session, so it may be a fixed source.",
    );
  });

  it("a moving star: the fitted shift between visits, Gaia and CatWISE, its cautions", () => {
    const c = cand("hunt-pm-1", {
      kind: "high_pm",
      ra: 10,
      dec: -5,
      det: 5,
      pm_mas_yr: 870.2,
      pa_deg: 95,
      gaia_match: true,
      reasons: ["pm_fit", "vetted", "catwise_pm_agrees", "blend"],
      flags: ["bloom"],
    });
    const f = framesWith();
    f.measured!.shifts = [
      { source_id: 1, gaia_id: "1", class: "star", ra: 10.0001, dec: -5, mjd_from: 60700, mjd_to: 60870, dt_yr: 0.47, dra_arcsec: 0.4, ddec_arcsec: 0, shift_arcsec: 0.41, err_arcsec: 0.03, pa_deg: 95, pmra: 870, pmdec: 0, pm_err: 40, pm_snr: 20 },
    ];
    const a = aboutCandidate(c, f);
    expect(a.said).toEqual([
      "Moved 0.41″ toward the east between visits 6 months apart (0.87″ a year); clearest in D5.",
      "Gaia lists a star here and CatWISE gives the same motion: most likely a known nearby star.",
    ]);
    expect(a.watch).toBe(
      "Watch for: two or more Gaia stars sit within 8″, so the light of a pair may be merged; NASA flagged charge spilling from a saturated star here (BLOOM).",
    );
    // Without frames: the yearly motion alone; no Gaia star: what it could be.
    const lone = { ...c, gaia_match: false, labels: ["red"], reasons: ["pm_fit", "vetted"], flags: [] };
    expect(aboutCandidate(lone).said).toEqual([
      "Moves 0.87″ a year toward the east between visits; clearest in D5.",
      "Gaia lists no star here, and it is red: a faint nearby star or brown dwarf, if the motion is real.",
    ]);
    expect(aboutCandidate(lone).watch).toBe("");
  });

  it("an odd spectrum: which bands, which detector, what it could be", () => {
    const c = cand("hunt-sp-1", {
      kind: "spectral",
      det: 4,
      snr: 41.6,
      labels: ["red", "brown_dwarf_like", "h2o_vapour", "co_23"],
      reasons: ["no_gaia", "class_unsure"],
    });
    expect(aboutCandidate(c).said).toEqual([
      "Its spectrum shows water vapour (1.4 and 1.9 µm) and carbon monoxide (2.3 µm); its strongest signal is in D4 (S/N 42).",
      "Gaia lists no star here, it is point-like and with its red colour that is what a brown dwarf would look like.",
    ]);
    const pah = cand("hunt-sp-2", { kind: "spectral", det: 3, labels: ["pah_33"], reasons: ["no_gaia", "class_galaxy"] });
    expect(aboutCandidate(pah).said[0]).toBe("Its spectrum shows the 3.3 µm glow of a star-forming galaxy (PAH); its strongest signal is in D3.");
    expect(aboutCandidate(pah).watch).toBe("Watch for: it looks like a galaxy, and galaxies are extended by nature.");
  });

  it("one or two: the stretch, its axis and whether both arms agree", () => {
    const c = cand("hunt-sh-1", { kind: "shape", det: 3, pa_deg: 37, reasons: ["elongated", "steady_pa", "one_catalogue_source"] });
    const a = aboutCandidate(c);
    expect(a.said).toEqual([
      "Its image is stretched along PA 37° (north-east–south-west) like two objects side by side, but Gaia and CatWISE list only one; clearest in D3.",
      "D1–D3 and D4–D6 show the stretch along the same axis, which a flaw of one detector would not do.",
    ]);
    expect(a.watch).toBe("");
    expect(aboutCandidate({ ...c, reasons: ["elongated", "one_visit"] }).watch).toBe("Watch for: only one visit shows the stretch.");
  });

  it("keeps to three sentences, shows unknown codes as given, and escapes in HTML", () => {
    for (const c of [ecl04, cand("x", { kind: "spectral", labels: ["ch4"], reasons: ["no_gaia", "class_unsure", "variable"] })]) {
      expect(aboutCandidate(c, framesWith()).said.length).toBeLessThanOrEqual(3);
    }
    expect(cautionText("new_gate", ecl04)).toBe("pipeline check “new_gate”");
    const html = aboutHtml({ ...ecl04, reasons: ["two_exposures", "<b>"] });
    expect(html).toContain("&lt;b&gt;");
    expect(html).toContain('class="sp-about-watch"');
    expect(aboutHtml(cand("q", { kind: "shape" }))).not.toContain("sp-about-watch");
  });

  it("formats distances and spans", () => {
    expect(arcsecText(0.413)).toBe("0.41″");
    expect(arcsecText(3.14)).toBe("3.1″");
    expect(arcsecText(10.96)).toBe("11″");
    expect(spanText(6.447 / 1440)).toBe("6.4 minutes");
    expect(spanText(150 / 1440)).toBe("2.5 hours");
  });
});

describe("an object to check opens on its own detector", () => {
  it("the plate opens on c.det", () => {
    expect(candidateView(ecl04).det).toBe(6);
  });

  it("turns All off for the object and puts the person's choice back when they leave", () => {
    let pref = true;
    const plate = {
      preferAllWave: vi.fn((on: boolean) => {
        const before = pref;
        pref = on;
        return before;
      }),
    };
    const w = suspectWave(plate);
    w.open();
    expect(pref).toBe(false);
    // The next object (Next, Previous) opens on its own detector too; the saved choice stays the first.
    pref = true; // the person pressed All on the first object
    w.open();
    expect(pref).toBe(false);
    w.leave();
    expect(pref).toBe(true);
    // Leaving twice changes nothing; a person who chose one detector before keeps it.
    w.leave();
    expect(plate.preferAllWave).toHaveBeenCalledTimes(3);
    pref = false;
    w.open();
    w.leave();
    expect(pref).toBe(false);
  });

  it("does nothing on a plate without the preference", () => {
    const w = suspectWave({});
    expect(() => {
      w.open();
      w.leave();
    }).not.toThrow();
  });
});

describe("the file's checks in the suspect panel (contracts/api.md v3.23)", () => {
  const oneDetector = {
    code: "one_detector",
    level: "strong" as const,
    text: "D3 measured 76 ± 41 µJy at its place, against 1,795 in D6 (ratio 0.04; known asteroids here: 0.03–3)",
    look: "Hours, D3 and D6: in D3, at the same moments, the spot stays empty",
    values: { flux_ujy: 76, ratio: 0.04 },
  };
  const outlier = { code: "flag_outlier", level: "warn" as const, text: "NASA marked 3 of 9 pixels OUTLIER in photo 2", look: "", values: null };
  const seen = { code: "partner_seen", level: "ok" as const, text: "D5 saw it too", look: "Hours, D5", values: null };
  const withChecks = { ...ecl04, status: "open" as const, known_name: null, checks: [seen, outlier, oneDetector] };

  it("replaces the guessed “Watch for” line with the checks, strong first", () => {
    const a = aboutCandidate(withChecks);
    expect(a.watch).toBe("");
    expect(a.checks.map((k) => k.code)).toEqual(["one_detector", "flag_outlier", "partner_seen"]);
  });

  it("lets a measured partner check replace the guess about the partner detector", () => {
    const f = framesWith({ partner: true });
    expect(aboutCandidate(ecl04, f).said).toHaveLength(2);
    expect(aboutCandidate(withChecks, f).said).toEqual([aboutCandidate(ecl04, f).said[0]]);
  });

  it("keeps today's text without checks (older files, or none measured)", () => {
    expect(aboutCandidate({ ...ecl04, checks: [] }).watch).toBe(aboutCandidate(ecl04).watch);
    expect(aboutCandidate(ecl04).watch).toMatch(/^Watch for: /);
    expect(aboutCandidate(ecl04).checks).toEqual([]);
  });

  it("shows each check marked in words, with what was measured and what to look at", () => {
    const html = aboutHtml(withChecks);
    expect(html).not.toContain("sp-about-watch");
    const el = document.createElement("div");
    el.innerHTML = html;
    expect(el.querySelector(".sp-checks-h")?.textContent).toBe("What to check");
    const items = [...el.querySelectorAll(".ck")];
    expect(items.map((li) => li.querySelector(".ck-level")!.textContent)).toEqual(["Sign against:", "Can't rule out:", "Sign for:"]);
    expect(items[0].querySelector(".ck-text")?.textContent).toBe(`${oneDetector.text}.`);
    expect(items[0].querySelector(".ck-look")?.textContent).toBe(`Look: ${oneDetector.look}.`);
    expect(items[1].querySelector(".ck-look")).toBeNull();
  });

  it("says first what a known body or an instrument flaw certainly is", () => {
    const el = document.createElement("div");
    el.innerHTML = aboutHtml({ ...withChecks, status: "known", known_name: "306P/LINEAR" });
    expect(el.querySelector(".ck-status .ck-status-head")?.textContent).toBe("Known body: 306P/LINEAR");
    expect(el.querySelector(".ck-status")?.textContent).toContain("Listed apart, not deleted.");
    el.innerHTML = aboutHtml({ ...withChecks, status: "flaw" });
    expect(el.querySelector(".ck-status-head")?.textContent).toBe("Instrument flaw");
    el.innerHTML = aboutHtml(ecl04);
    expect(el.querySelector(".ck-status, .sp-checks")).toBeNull();
  });
});
