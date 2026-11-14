import { describe, expect, it } from "vitest";
import { cleanCandidates, toAtlasData } from "./data";
import { KINDS, KIND_TEXT, countSentence, detectorText, flagsSentence, isCaution, isKind, isLikelyMismatch, keyNumber, labelText, oneLine, plural, reasonText, zoneName } from "./kinds";
import { cand } from "./testdata";

describe("kinds in words", () => {
  it("has a one-letter code for each kind, all different", () => {
    const codes = KINDS.map((k) => KIND_TEXT[k].code);
    expect(new Set(codes).size).toBe(KINDS.length);
    for (const c of codes) expect(c).toMatch(/^[a-z]$/);
    expect(isKind("high_pm")).toBe(true);
    expect(isKind("planet")).toBe(false);
  });

  it("writes in sentence case, never all caps", () => {
    for (const k of KINDS) {
      const t = KIND_TEXT[k];
      for (const s of [t.name, t.plural, t.meaning]) {
        expect(s.charAt(0)).toBe(s.charAt(0).toUpperCase());
        expect(s).not.toBe(s.toUpperCase());
      }
    }
  });

  it("knows the contract's check-first codes and the likely mismatches", () => {
    for (const c of ["implausible_pm", "gaia_pm_disagrees", "detector_excess", "repeat_position", "few_dots"]) expect(isCaution(c)).toBe(true);
    for (const c of ["two_exposures", "pm_fit", "vetted", "no_gaia", "catwise_pm_agrees"]) expect(isCaution(c)).toBe(false);
    expect(isLikelyMismatch(cand({ kind: "high_pm", reasons: ["pm_pair", "implausible_pm"] }))).toBe(true);
    expect(isLikelyMismatch(cand({ kind: "high_pm", reasons: ["pm_fit", "vetted"] }))).toBe(false);
  });

  it("turns codes into phrases, known or not", () => {
    expect(reasonText("gaia_pm_disagrees")).toBe("Gaia's motion differs");
    expect(reasonText("new_thing_here")).toBe("New thing here");
    expect(labelText("ch4")).toBe("Methane");
    expect(labelText("odd_band")).toBe("Odd band");
  });

  it("warns about artefacts in one sentence", () => {
    expect(flagsSentence([])).toBeNull();
    expect(flagsSentence(["GHOST"])).toBe("Careful: it sits on a ghost reflection.");
    expect(flagsSentence(["ghost", "GHOST_FPA", "HALO", "SOMETHING_ELSE"])).toBe(
      "Careful: it sits on a ghost reflection, a bright star's halo and something else.",
    );
  });

  it("names detectors with their wavelengths, zones and counts", () => {
    expect(detectorText(3)).toBe("D3, 1.6–2.4 µm");
    expect(detectorText(9)).toBe("D9");
    expect(zoneName("pilot")).toBe("Pilot zone");
    expect(zoneName("hunt_2")).toBe("Hunt 2 zone");
    expect(countSentence(194, 1)).toBe("194 objects to check in 1 zone");
    expect(countSentence(1, 2)).toBe("1 object to check in 2 zones");
    expect(countSentence(1200, 0)).toBe("1,200 objects to check");
    expect(plural(1, "signal", "signals")).toBe("1 signal");
  });
});

describe("keyNumber", () => {
  it("gives a moving star's motion in ″/yr with its error and direction", () => {
    const k = keyNumber(cand({ kind: "high_pm", pm_mas_yr: 1302, pm_err_mas_yr: 160, pa_deg: 61.2 }))!;
    expect(k).toEqual({ value: "1.30", unit: "″/yr", detail: "± 0.16″/yr, toward 61° east of north" });
    expect(keyNumber(cand({ kind: "high_pm", pm_mas_yr: 10_400 }))!.value).toBe("10.4");
  });

  it("gives a fast mover's rate in ″/h, or its brightness when there is no rate", () => {
    expect(keyNumber(cand({ kind: "mover", rate_arcsec_h: 42.25, pa_deg: 200 }))).toEqual({
      value: "42.3",
      unit: "″/h",
      detail: "toward 200° east of north",
    });
    expect(keyNumber(cand({ kind: "mover", flux_ujy: 2320.2, snr: 56.9 }))).toEqual({
      value: "2,320",
      unit: "µJy",
      detail: "signal-to-noise 57",
    });
  });

  it("falls back to the score, then to nothing", () => {
    expect(keyNumber(cand({ flux_ujy: null, snr: null, score: 0.123 }))).toEqual({ value: "0.12", unit: "", detail: "pipeline score" });
    expect(keyNumber(cand({ flux_ujy: null, snr: null, score: null }))).toBeNull();
  });

  it("writes one line per signal", () => {
    expect(oneLine(cand({ kind: "high_pm", pm_mas_yr: 500, pa_deg: 151 }))).toBe("Moving star, 0.50″/yr, toward 151°");
    expect(oneLine(cand({ kind: "mover", flux_ujy: 88.12 }))).toBe("Fast mover, 88.1 µJy");
    expect(oneLine(cand({ flux_ujy: null, snr: null, score: null }))).toBe("Fast mover");
  });
});

describe("cleanCandidates", () => {
  it("keeps what can be placed and drawn, once, with safe defaults", () => {
    const raw = [
      cand({ id: "a" }),
      cand({ id: "a" }),
      { ...cand({ id: "b" }), kind: "planet" },
      cand({ id: "c", dec: 95 }),
      cand({ id: "d", ra: NaN }),
      { ...cand({ id: "e" }), zone: "", labels: undefined, flags: null, frames_url: "" },
      null,
      42,
    ];
    const out = cleanCandidates(raw);
    expect(out.map((c) => c.id)).toEqual(["a", "e"]);
    const e = out[1];
    expect(e.zone).toBe("other");
    expect(e.labels).toEqual([]);
    expect(e.flags).toEqual([]);
    expect(e.frames_url).toBeNull();
    expect(cleanCandidates({ not: "a list" })).toEqual([]);
  });

  it("lists only zones that have candidates", () => {
    const d = toAtlasData(
      { generated: "2026-11-14T13:12:18Z", data_release: "DR1+QR3", zones: ["pilot", "hunt"], candidates: [cand({ id: "a" })] } as never,
      "export",
    );
    expect(d.zones).toEqual(["pilot"]);
    expect(d.origin).toBe("export");
    expect(toAtlasData(null, "export").origin).toBe("none");
  });
});
