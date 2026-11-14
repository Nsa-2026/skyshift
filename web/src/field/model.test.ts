import { describe, expect, it } from "vitest";
import type { Analysis } from "../api/science-client";
import type { GaiaStar } from "../api/types";
import epsAnalysis from "../../public/science-data/epsindib/analysis.json";
import barnardAnalysis from "../../public/science-data/barnard/analysis.json";
import rdraAnalysis from "../../public/science-data/rdra/analysis.json";
import wiseScience from "../../public/science-data/wise0855/science.json";
import {
  countSentence,
  earthRadii,
  falseAlarm,
  gaiaNear,
  percent,
  planetSummary,
  rank,
  signalAt,
  signalRows,
  sizeWord,
  type Sensitivity,
  type SignalRow,
} from "./model";

const EPS = epsAnalysis as unknown as Analysis;
/** Epsilon Indi B in Gaia DR3 (epoch 2016.0), as in /data/epsindib/frames_months_5.json. */
const EPS_B: GaiaStar = {
  id: "6412596012146801152",
  ra: 331.07645259656397,
  dec: -56.79381206688221,
  pmra: 3981.977,
  pmdec: -2466.832,
  parallax: 270.658,
  g: 18.031,
};

describe("false-alarm chip", () => {
  it("formats with the precision the trials support", () => {
    expect(percent(1 / 101)).toBe("0.99 %");
    expect(percent(2 / 101)).toBe("1.98 %");
    expect(percent(0.05)).toBe("5 %");
    expect(percent(12 / 101)).toBe("12 %");
    expect(percent(1)).toBe("100 %");
  });

  it("is green under 2 %, amber from 2 to 10 %, red over 10 %", () => {
    expect(falseAlarm(1 / 101, 100).tone).toBe("good");
    expect(falseAlarm(0.0198, 100).tone).toBe("good");
    expect(falseAlarm(0.02, 100).tone).toBe("caution");
    expect(falseAlarm(0.1, 100).tone).toBe("caution");
    expect(falseAlarm(0.1188, 100).tone).toBe("risk");
    expect(falseAlarm(1, 100).tone).toBe("risk");
  });

  it("says the chip's number and never calls it a probability of being real", () => {
    const f = falseAlarm(0.1188, 100);
    expect(f.text).toBe("12 % false alarms");
    expect(f.note).toMatch(/artefacts/);
    expect(`${f.text} ${f.note}`).not.toMatch(/probab|real\b|confirm/i);
  });

  it("names the floor of 100 trials instead of claiming less", () => {
    expect(falseAlarm(1 / 101, 100).note).toBe("No scrambled copy matched it, the lowest rate 100 trials can show.");
    expect(falseAlarm(2 / 101, 100).note).not.toMatch(/lowest/);
  });

  it("is 'not measured' for null or nonsense values (cold sources)", () => {
    for (const p of [null, undefined, NaN, -0.1, 1.5]) {
      const f = falseAlarm(p, 100);
      expect(f.tone).toBe("unknown");
      expect(f.text).toBe("False alarms not measured");
    }
  });
});

describe("signal rows", () => {
  const row = (p: number | null, strength: number, key: string) => ({ p, strength, key }) as SignalRow;

  it("ranks the lowest false-alarm rate first, unmeasured last, then the strongest", () => {
    const out = rank([row(null, 50, "n"), row(0.5, 9, "b"), row(0.01, 2, "a2"), row(0.01, 7, "a1"), row(null, 80, "m")]);
    expect(out.map((r) => r.key)).toEqual(["a1", "a2", "b", "m", "n"]);
  });

  it("reads Epsilon Indi B's analysis: one slow mover, four fast movers, cold sources from two visits", () => {
    const g = signalRows(EPS, { centre: { ra: 331.09668, dec: -56.80067 }, name: "Epsilon Indi B" });
    expect(g.slow).toHaveLength(1);
    expect(g.fast).toHaveLength(4);
    expect(g.cold.every((r) => /^Seen in \d visits$/.test(r.value))).toBe(true);
    expect(g.cold).toHaveLength(EPS.cold_candidates.filter((c) => (c.n_visits ?? 0) >= 2).length);
    expect(g.coldSingle).toBe(EPS.cold_candidates.length - g.cold.length);
    expect(g.total).toBe(1 + 4 + g.cold.length);

    const s = g.slow[0];
    expect(s.value).toBe("4.53″/yr");
    expect(s.direction).toBe("south-east");
    expect(s.alarm.tone).toBe("risk");
    expect(s.facts.join(" ")).toMatch(/probably Epsilon Indi B itself/);
    // Without a Gaia list nothing is claimed about Gaia.
    expect(s.gaia).toBeUndefined();
    expect(s.facts.join(" ")).not.toMatch(/Gaia/);
    expect(s.next.join(" ")).toMatch(/bad-pixel flags weren't kept/);
  });

  it("orders fast movers by false alarms, then strength; every speed in ″/day", () => {
    const g = signalRows(barnardAnalysis as unknown as Analysis);
    expect(g.fast.map((r) => r.value)).toEqual(["7.55″/day", "15.58″/day"]);
    expect(g.fast.map((r) => r.alarm.tone)).toEqual(["good", "good"]);
    expect(g.slow).toHaveLength(0);
    expect(g.cold).toHaveLength(0);
    expect(g.coldSingle).toBe(8);
  });

  it("matches Gaia stars moved to the signal's time", () => {
    const s = EPS.slow_movers[0];
    const near = gaiaNear(s, s.mjd_ref, [EPS_B]);
    expect(near?.sep).toBeGreaterThan(0.3);
    expect(near?.sep).toBeLessThan(1);
    expect(near?.pm).toBeCloseTo(4.68, 2);
    // At its 2016.0 position the star is 45″ away: no match without moving it.
    expect(gaiaNear(s, null, [EPS_B])).toBeNull();
    expect(gaiaNear(s, s.mjd_ref, null)).toBeUndefined();

    const g = signalRows(EPS, { gaia: [EPS_B], mjdMid: 61062 });
    expect(g.slow[0].facts.join(" ")).toMatch(/Gaia DR3 has a star 0\.\d″ away \(G 18\.0, moving 4\.68″\/yr\)/);
    expect(g.slow[0].next.join(" ")).toMatch(/Gaia star's own motion/);
    expect(g.fast.some((r) => r.facts.includes("No Gaia DR3 star within 4″."))).toBe(true);
  });

  it("describes cold sources with the D6 and D3 bands and their wavelengths", () => {
    const g = signalRows(EPS);
    expect(g.cold[0].facts[0]).toMatch(/^Up to S\/N \d+ in D6 \(4\.4–5\.0 µm\), below S\/N 2 in D3 \(1\.6–2\.4 µm\) in the same exposure\.$/);
    expect(g.cold[0].alarm.tone).toBe("unknown");
    // Strongest first among the unmeasured.
    const strengths = g.cold.map((r) => r.strength);
    expect([...strengths].sort((a, b) => b - a)).toEqual(strengths);
  });

  it("finds nothing in R Draconis and says so in the count", () => {
    const g = signalRows(rdraAnalysis as unknown as Analysis);
    expect(g.total).toBe(0);
    expect(countSentence(g)).toBe("no slow movers, no fast movers and no cold sources");
  });

  it("counts in words with singulars", () => {
    const one = [{}] as SignalRow[];
    expect(countSentence({ slow: one, fast: [...one, ...one], cold: [] })).toBe("1 slow mover, 2 fast movers and no cold sources");
  });

  it("moves a slow mover along its motion; others stay put", () => {
    const r = { ra: 10, dec: 60, pmra: 3.6, pmdec: -7.2, mjdRef: 60000 };
    const a = signalAt(r, 60000 + 365.25);
    expect((a.dec - 60) * 3600).toBeCloseTo(-7.2, 6);
    expect((a.ra - 10) * 3600 * Math.cos((60 * Math.PI) / 180)).toBeCloseTo(3.6, 6);
    expect(signalAt(r, null)).toEqual({ ra: 10, dec: 60 });
    expect(signalAt({ ...r, pmra: null }, 61000)).toEqual({ ra: 10, dec: 60 });
  });

  it("copes with a minimal analysis (missing optional fields)", () => {
    const a = {
      detector: 2,
      n_slides: 100,
      source: { n_frames: 1, n_visits: 1, coverage_arcmin: 4 },
      warnings: [],
      slow_movers: [{ ra: 1, dec: 2, status: "unvetted", p_false: 0.05 }],
      fast_movers: [{ ra: 1, dec: 2, status: "artifact_warning", p_false: null }],
      cold_candidates: [],
    } as Analysis;
    const g = signalRows(a);
    expect(g.slow[0].value).toBe("Motion not given");
    expect(g.slow[0].alarm.tone).toBe("caution");
    expect(g.fast[0].value).toBe("Speed not given");
    expect(g.fast[0].facts).toContain("Status: an artefact warning is set.");
  });
});

describe("Planet X sensitivity", () => {
  const sens = (wiseScience as unknown as { sensitivity: Sensitivity }).sensitivity;

  it("answers honestly for WISE 0855: the Planet Nine models stay below S/N 5", () => {
    const s = planetSummary(sens)!;
    expect(s.verdict).toBe("hidden");
    expect(s.bestSnr).toBeCloseTo(1.22, 2);
    expect(s.vRange).toEqual([21, 23]);
    expect(s.answer).toBe(
      "Yes. A planet like the Planet Nine models (V 21 to 23) would reach at most S/N 1.2 here, and SPHEREx needs S/N 5 to see it.",
    );
  });

  it("gives the radius needed at 100, 300, 500 and 1,000 AU for albedo 0.3, in Earth radii", () => {
    const s = planetSummary(sens)!;
    expect(s.sizes.map((z) => z.au)).toEqual([100, 300, 500, 1000]);
    expect(s.sizes.map((z) => z.text)).toEqual(["0.25 R⊕", "2.2 R⊕", "6.2 R⊕", "25 R⊕"]);
    expect(s.sizes.map((z) => z.compare)).toEqual([
      "smaller than Earth",
      "between Earth and Neptune",
      "between Neptune and Jupiter",
      "bigger than Jupiter",
    ]);
  });

  it("lists the 5σ depth by wavelength in µJy and AB, and finds the deepest", () => {
    const s = planetSummary(sens)!;
    expect(s.depths).toHaveLength(6);
    expect(s.depths.map((d) => d.um)).toEqual([...s.depths.map((d) => d.um)].sort((a, b) => a - b));
    expect(s.depths[0].text).toBe("83 µJy (AB 19.1)");
    expect(s.deepest?.ujy).toBeCloseTo(54.7, 1);
    expect(s.deepest?.um).toBeCloseTo(3.0, 1);
    expect(s.twin).toBe("A twin of WISE 0855, the coldest known brown dwarf, would show at 4.4–5.0 µm out to 4.5 pc (15 light-years).");
  });

  it("says which models would show when some reach S/N 5", () => {
    const s = planetSummary({ ...sens, p9: [{ V: 21, best_snr: 8 }, { V: 21.5, best_snr: 5.1 }, { V: 22, best_snr: 3 }] })!;
    expect(s.verdict).toBe("reachable");
    expect(s.answer).toMatch(/^Only if it is fainter than V 21\.5\. Brighter Planet Nine models would reach S\/N 8\.0 here/);
  });

  it("claims nothing without models or without a sensitivity block", () => {
    const s = planetSummary({ ...sens, p9: [{ V: 21, best_snr: null }] })!;
    expect(s.verdict).toBe("unknown");
    expect(s.answer).toMatch(/^Not known here/);
    expect(planetSummary(null)).toBeNull();
    expect(planetSummary({ ...sens, wise0855_twin_reach_pc: null })!.twin).toBeNull();
  });

  it("formats sizes and words for scale", () => {
    expect(earthRadii(0.154)).toBe("0.15");
    expect(earthRadii(5.55)).toBe("5.5");
    expect(earthRadii(61.6)).toBe("62");
    expect(sizeWord(3.87)).toBe("between Earth and Neptune");
    expect(sizeWord(11.3)).toBe("bigger than Jupiter");
  });
});
