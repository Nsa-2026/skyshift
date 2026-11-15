import { describe, expect, it } from "vitest";
import type { BandResult, Block, FeatureState, Reason, Source } from "./composition";
import {
  GLOW_HINT_OPACITY,
  GLOW_OPACITY,
  GROUPS,
  MAX_LABELS,
  RING_PX,
  RING_STEP_PX,
  applyContext,
  bandRows,
  compositionSvg,
  describeBlock,
  describeSource,
  glowAt,
  glowRange,
  groupState,
  lookalikes,
  markerOf,
  mjy,
  moleculeNames,
  nearestSource,
  reasonText,
  summarise,
  tally,
} from "./compview";
import { INDEX_IDS, type IndexId } from "./indices";

function band(state: FeatureState, depth = 0, reasons: Reason[] = []): BandResult {
  return { state, depth, err: 0.03, sig: depth / 0.03, contSnr: 30, reasons };
}

function source(x: number, y: number, set: Partial<Record<IndexId, BandResult>> = {}, all: FeatureState = "none"): Source {
  const bands = {} as Record<IndexId, BandResult>;
  for (const id of INDEX_IDS) bands[id] = set[id] ?? band(all, 0, all === "cant" ? ["faint"] : []);
  return { x, y, snr: 50, bright: false, glare: false, blend: false, erratic: false, roughness: 1, dropped: { exposures: 0, glitches: 0 }, redBd: false, redIce: false, bands };
}

const WIN = { x0: 0, y0: 0, x1: 100, y1: 100 };
const ALL = { hints: true, all: true, selected: -1 };

describe("markers", () => {
  it("every band belongs to exactly one group", () => {
    const seen = GROUPS.flatMap((g) => g.bands).sort();
    expect(seen).toEqual([...INDEX_IDS].sort());
  });
  it("found beats possible beats none beats can't tell", () => {
    const s = source(5, 5, { ch4_33: band("found", 0.68), h2o_14: band("possible", 0.3, ["no_second_band"]), co_23: band("cant", NaN, ["faint"]) });
    expect(groupState(s, GROUPS[0])).toBe("found");
    const m = markerOf(s);
    expect(m.state).toBe("found");
    expect(m.groups.map((g) => g.id)).toEqual(["methane"]);
    expect(m.depth).toBeCloseTo(0.68);
    expect(markerOf(source(1, 1, { h2o_14: band("possible", 0.3) })).state).toBe("possible");
    expect(markerOf(source(1, 1)).state).toBe("none");
    expect(markerOf(source(1, 1, {}, "cant")).state).toBe("cant");
  });
  it("a source measured in one band only is 'none', not 'can't tell'", () => {
    expect(markerOf(source(1, 1, { h2o_14: band("none") }, "cant")).state).toBe("none");
  });
});

describe("applyContext", () => {
  const found = () => source(10, 10, { ch4_33: band("found", 0.6), h2o_14: band("found", 0.5) });
  it("leaves sources alone without a match", () => {
    const [s] = applyContext([found()], { gaia: [{ x: 30, y: 30, g: 12 }], known: [] });
    expect(s.bands.ch4_33.state).toBe("found");
    expect(s.gaiaG).toBeNull();
  });
  it("methane on a bright Gaia star becomes possible; water stays", () => {
    const [s] = applyContext([found()], { gaia: [{ x: 10.3, y: 9.8, g: 14.2 }], known: [] });
    expect(s.bands.ch4_33.state).toBe("possible");
    expect(s.bands.ch4_33.reasons).toContain("gaia_star");
    expect(s.bands.h2o_14.state).toBe("found");
    expect(s.gaiaG).toBe(14.2);
  });
  it("a faint Gaia match (a nearby brown dwarf, like Epsilon Indi B at G = 18.0) keeps methane", () => {
    const [s] = applyContext([found()], { gaia: [{ x: 10, y: 10, g: 18.03 }], known: [] });
    expect(s.bands.ch4_33.state).toBe("found");
    expect(s.gaiaG).toBe(18.03);
  });
  it("a known asteroid nearby makes every feature possible; a very faint one doesn't", () => {
    const [s] = applyContext([found()], { gaia: [], known: [{ x: 11, y: 11, v: 15, name: "(31) Euphrosyne" }] });
    expect(s.bands.ch4_33.state).toBe("possible");
    expect(s.bands.h2o_14.reasons).toContain("moving");
    expect(s.knownName).toBe("(31) Euphrosyne");
    const [t] = applyContext([found()], { gaia: [], known: [{ x: 11, y: 11, v: 22.6, name: "2015 RW209" }] });
    expect(t.bands.ch4_33.state).toBe("found");
  });
  it("a variable target's features are possible only", () => {
    const [s, far] = applyContext([found(), source(40, 40, { h2o_14: band("found", 0.5) })], {
      gaia: [],
      known: [],
      target: { x: 10.4, y: 10.4, is: "variable", name: "R Draconis" },
    });
    expect(s.bands.h2o_14.state).toBe("possible");
    expect(s.bands.h2o_14.reasons).toContain("variable");
    expect(far.bands.h2o_14.state).toBe("found");
  });
  it("never upgrades or hides a state", () => {
    const [s] = applyContext([source(10, 10, { ch4_33: band("none"), co_23: band("cant", NaN, ["faint"]) })], {
      gaia: [{ x: 10, y: 10, g: 10 }],
      known: [{ x: 10, y: 10, v: 10, name: "x" }],
    });
    expect(s.bands.ch4_33.state).toBe("none");
    expect(s.bands.co_23.state).toBe("cant");
  });
});

describe("words", () => {
  it("says what was found, how deep and how sure", () => {
    const t = describeSource(source(1, 1, { ch4_33: band("found", 0.68) }));
    expect(t).toMatch(/^Found: Methane at 3\.3 µm: 68% of the light is missing/);
    expect(t).toMatch(/22\.7 times the noise/);
  });
  it("says possible and not found for a failed check", () => {
    expect(describeSource(source(1, 1, { pah_33: band("possible", 0.5, ["one_frame"]) }))).toMatch(/^Possible, not confirmed: PAH dust at 3\.3 µm: 50% extra light/);
  });
  it("can't tell gives the reason, and never says 'nothing there'", () => {
    const s = source(1, 1, {}, "cant");
    for (const id of INDEX_IDS) s.bands[id] = band("cant", NaN, ["bright"]);
    expect(describeSource(s)).toMatch(/^Can't tell: too bright/);
  });
  it("every reason has words for every group", () => {
    const reasons: Reason[] = ["bright", "glare", "blend", "erratic", "faint", "nodata", "nocal", "one_frame", "not_red", "no_second_band", "vapour", "co_gas", "gaia_star", "moving", "variable", "ice", "ice_maybe"];
    for (const r of reasons) for (const g of GROUPS) expect(reasonText(r, g.id).length).toBeGreaterThan(15);
  });
  it("a band darker than its noise allows reads as 'all', never as more than 100%", () => {
    const t = describeSource(source(1, 1, { h2o_ice_305: band("found", 1.07) }));
    expect(t).toMatch(/Water ice at 3\.05 µm: all of the light is missing/);
    expect(t).not.toMatch(/107%/);
  });
  it("says when water vapour counts through its three bands together", () => {
    const joint = { depth: 0.23, err: 0.03, sig: 7.1 };
    const rows = bandRows(source(1, 1, { h2o_14: { ...band("found", 0.1), joint }, h2o_19: { ...band("found", 0.12), joint } }));
    expect(rows.find((r) => r.id === "h2o_14")!.note).toBe(
      "counted with water's other bands: the three together (1.4, 1.9 and 2.7 µm, wide windows) are 23% ± 3% deep (7.1σ)",
    );
    expect(rows.find((r) => r.id === "co_23")!.note).toBe("");
  });
  it("says why a one-frame ice band counts", () => {
    const rows = bandRows(source(1, 1, { h2o_ice_305: band("found", 0.8), co2_ice_427: { ...band("found", 0.6), confirmedBy: "h2o_ice_305" } }));
    expect(rows.find((r) => r.id === "co2_ice_427")!.note).toMatch(/one frame wide.*water ice is found in the same source/);
    expect(rows.find((r) => r.id === "h2o_ice_305")!.note).toBe("");
  });
  it("lists all nine bands with the pipeline's requirement", () => {
    const rows = bandRows(source(1, 1, { ch4_33: band("found", 0.68) }));
    expect(rows.length).toBe(9);
    expect(rows.find((r) => r.id === "ch4_33")!.needs).toBe("≥ 25% and ≥ 5σ");
    expect(rows.find((r) => r.id === "ch4_33")!.value).toBe("68% ± 3% (22.7σ)");
  });
});

describe("drawing and counting", () => {
  const sources = [
    source(10, 10, { ch4_33: band("found", 0.68), h2o_14: band("found", 0.5) }),
    source(20, 20, { co_23: band("possible", 0.4, ["one_frame"]) }),
    source(30, 30),
    source(40, 40, {}, "cant"),
    source(300, 300, { ch4_33: band("found", 0.6) }), // outside the window
  ];
  const blocks: Block[] = [
    // Dust-glow cells, 7 pixels a side; depth is the 3.3 µm excess in MJy/sr.
    { x: 0, y: 0, size: 7, state: "found", depth: 0.02, err: 0.002, reasons: [] },
    { x: 7, y: 0, size: 7, state: "found", depth: 0.06, err: 0.002, reasons: [] },
    { x: 49, y: 49, size: 7, state: "possible", depth: 0.006, err: 0.002, reasons: [] },
    { x: 63, y: 63, size: 7, state: "cant", depth: NaN, err: NaN, reasons: ["bright"] },
    { x: 63, y: 0, size: 7, state: "none", depth: 0, err: 0.002, reasons: [] },
  ];
  it("by default draws only what was found: one ring per molecule, the dust as one patch, and the names", () => {
    const svg = compositionSvg(sources, blocks, WIN, { hints: false, all: false, selected: -1 });
    expect(svg.match(/class="cp-ring"/g)?.length).toBe(2); // methane, and water vapour around it
    expect(svg).toContain(`r="${RING_PX}"`);
    expect(svg).toContain(`r="${RING_PX + RING_STEP_PX}"`);
    expect(svg).toContain(GROUPS.find((g) => g.id === "methane")!.colour);
    expect(svg).toContain(GROUPS.find((g) => g.id === "gas")!.colour);
    // The dust glow: one shaded cell per found cell, the brighter one more opaque, no outlines.
    expect(svg.match(/class="cp-glow"/g)?.length).toBe(2);
    expect(svg).toContain(`fill-opacity:${GLOW_OPACITY[0]}`);
    expect(svg).toContain(`fill-opacity:${GLOW_OPACITY[1]}`);
    expect(svg).not.toContain("cp-glow-maybe");
    expect(svg).not.toContain("<path");
    expect(svg).toContain(">Methane + water vapour</text>");
    for (const cls of ["cp-maybe", "cp-none", "cp-cant", "cp-picked"]) expect(svg).not.toContain(cls);
  });
  it("hints are dashed and drawn only when asked for", () => {
    const svg = compositionSvg(sources, blocks, WIN, { hints: true, all: false, selected: -1 });
    expect(svg.match(/class="cp-ring cp-maybe"/g)?.length).toBe(1);
    expect(svg.match(/cp-glow-maybe/g)?.length).toBe(1);
    expect(svg).toContain(`fill-opacity:${GLOW_HINT_OPACITY}`); // fainter than any found cell
    expect(GLOW_HINT_OPACITY).toBeLessThan(GLOW_OPACITY[0]);
    expect(svg).not.toContain("cp-none");
    expect(svg).not.toContain("cp-cant");
  });
  it("everything else that was checked is drawn only when asked for, each state with its own mark", () => {
    const svg = compositionSvg(sources, blocks, WIN, ALL);
    expect(svg.match(/class="cp-none"/g)?.length).toBe(1);
    expect(svg.match(/class="cp-cant"/g)?.length).toBe(1);
  });
  it("the picked source is ringed whatever its state, even when its mark is switched off", () => {
    for (const k of [0, 1, 2, 3]) {
      expect(compositionSvg(sources, blocks, WIN, { hints: false, all: false, selected: k }).match(/cp-picked/g)?.length).toBe(1);
    }
    expect(compositionSvg(sources, blocks, WIN, { hints: false, all: false, selected: 4 })).not.toContain("cp-picked"); // outside
  });
  it("names the rings only while there are few of them", () => {
    const many = Array.from({ length: MAX_LABELS + 1 }, (_, k) => source(10 + 10 * k, 50, { h2o_ice_305: band("found", 0.6) }));
    expect(compositionSvg(many, [], WIN, ALL)).not.toContain("cp-label");
    const few = compositionSvg(many.slice(0, MAX_LABELS), [], WIN, ALL);
    expect(few.match(/cp-label/g)?.length).toBe(MAX_LABELS);
    expect(few).toContain(">Water ice</text>");
    // Near the right edge the name goes to the left of the ring.
    expect(compositionSvg([source(90, 50, { h2o_ice_305: band("found", 0.6) })], [], WIN, ALL)).toContain('text-anchor="end"');
    // The name sits above the ring, and below it at the top edge, always inside the picture.
    const y = (svg: string) => Number(/<text[^>]* y="([\d.-]+)"/.exec(svg)![1]);
    expect(y(compositionSvg([source(50, 50, { h2o_ice_305: band("found", 0.6) })], [], WIN, ALL))).toBeLessThan(50);
    expect(y(compositionSvg([source(50, 2, { h2o_ice_305: band("found", 0.6) })], [], WIN, ALL))).toBeGreaterThan(3);
  });
  it("names the look-alikes of what was found only", () => {
    const l = lookalikes(sources[0]);
    expect(l.join(" ")).toMatch(/PAH dust sits at the same 3\.3 µm/);
    expect(l.join(" ")).toMatch(/changed brightness/);
    expect(l.join(" ")).not.toMatch(/carbon monoxide/);
    expect(lookalikes(sources[1])).toEqual([]); // a hint only
    expect(lookalikes(source(1, 1, { h2o_14: band("found", 0.5), h2o_19: band("found", 0.5) })).length).toBe(1);
  });
  it("says each source in a few words", () => {
    expect(summarise(sources[0])).toBe("Methane and water vapour found.");
    expect(summarise(sources[1])).toBe("A hint of carbon monoxide gas, not confirmed.");
    expect(summarise(sources[2])).toBe("Measured: none of these molecules shows.");
    expect(summarise(sources[3])).toMatch(/^Can't tell: too faint/);
    expect(summarise(source(1, 1, { h2o_ice_305: band("found", 0.8), co2_ice_427: band("found", 0.6), co_ice_467: band("found", 0.5) }))).toBe(
      "Water ice, CO₂ ice and CO ice found.",
    );
    expect(moleculeNames(sources[0], "found")).toEqual(["Methane", "Water vapour"]);
  });
  it("counts what is in the window", () => {
    const t = tally(sources, blocks, WIN);
    expect(t).toMatchObject({ sources: 4, found: 1, possible: 1, none: 1, cant: 1 });
    expect(t.groups.methane).toEqual({ found: 1, possible: 0 });
    expect(t.groups.gas).toEqual({ found: 1, possible: 1 }); // water vapour on one source, a hint of CO on another
    expect(t.glow).toMatchObject({ found: 2, possible: 1, none: 1 });
    // Glow could be measured in four cells of 7 × 7 of the 100 × 100 pixels; the "can't tell" cell doesn't count.
    expect(t.glowChecked).toBeCloseTo((4 * 49) / 10000);
  });
  it("finds the source and the glow under the pointer", () => {
    expect(nearestSource(sources, 10.6, 10.4)).toBe(0);
    expect(nearestSource(sources, 15, 15)).toBe(-1);
    expect(glowAt(blocks, 5, 5)?.depth).toBe(0.02);
    expect(glowAt(blocks, 9, 3)?.depth).toBe(0.06);
    expect(glowAt(blocks, 50, 50)?.state).toBe("possible");
    expect(glowAt(blocks, 70, 70)).toBeNull(); // off the cells
    expect(glowAt(blocks, 65, 2)).toBeNull(); // measured, nothing there
    expect(describeBlock(blocks[0])).toBe("Glowing dust (PAH) here: the sky is 0.020 MJy/sr brighter at 3.3 µm than at the colours either side (10 times the noise).");
    expect(describeBlock(blocks[2])).toMatch(/^A hint of glowing dust \(PAH\) here: the sky is 0\.006 MJy\/sr .*Too faint to be sure\.$/);
    expect(describeBlock({ ...blocks[2], reasons: ["one_frame"] })).toMatch(/nothing beside it confirms it/);
    expect(glowRange(blocks, WIN)).toEqual({ lo: 0.02, hi: 0.06 });
    expect(glowRange(blocks.slice(2), WIN)).toBeNull();
    expect([mjy(0.0123), mjy(0.456), mjy(1.65)]).toEqual(["0.012", "0.46", "1.6"]);
  });
});
