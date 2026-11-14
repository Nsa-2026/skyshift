import { describe, expect, it } from "vitest";
import { whyUnreliable, type TargetCheck } from "./why";

const base: TargetCheck = {
  name: "Barnard's Star",
  kind: "star",
  saturated: false,
  kept: [],
  moving: false,
  dates: { beg: "2025-08-12T00:00:00", end: "2026-05-08T00:00:00" },
};

describe("why a colour isn't reliable", () => {
  it("says nothing for a well-measured target", () => {
    expect(whyUnreliable(base)).toEqual([]);
  });
  // Numbers as measured on 14 Nov (docs/colour-layers.md).
  const barnard = [{ um: 1.24, share: 0.1 }, { um: 2.15, share: 0.05 }, { um: 4.6, share: 0.62 }];
  const rdra = [{ um: 1.24, share: 0.03 }, { um: 2.15, share: 0.0 }, { um: 4.6, share: 0.01 }];
  it("Barnard's Star: short wavelengths lost more, so it looks too red", () => {
    const r = whyUnreliable({ ...base, saturated: true, kept: barnard });
    expect(r.map((x) => x.key)).toEqual(["saturated"]);
    expect(r[0].text).toMatch(/^Barnard's Star is so bright/);
    expect(r[0].text).toMatch(/kept 10% of the usual exposures at 1\.24 µm, 5% at 2\.15 µm and 62% at 4\.60 µm/);
    expect(r[0].text).toMatch(/looks redder than it really is/);
  });
  it("R Draconis: every wavelength lost its core, so no direction is claimed", () => {
    const t = whyUnreliable({ ...base, name: "R Draconis", saturated: true, kept: rdra })[0].text;
    expect(t).toMatch(/missing at every wavelength/);
    expect(t).not.toMatch(/redder|bluer/);
  });
  it("says 'bluer' when the long wavelengths lost more, and never claims a fix is impossible", () => {
    const t = whyUnreliable({ ...base, saturated: true, kept: [{ um: 1.2, share: 0.9 }, { um: 4.6, share: 0.2 }] })[0].text;
    expect(t).toMatch(/looks bluer/);
    expect(t).toMatch(/might allow it/);
    expect(t).not.toMatch(/impossible|can't be fixed/);
  });
  it("explains variability with the map's dates", () => {
    const r = whyUnreliable({ ...base, name: "R Draconis", kind: "variable star", saturated: true, kept: rdra });
    expect(r.map((x) => x.key)).toEqual(["saturated", "variable"]);
    expect(r[1].text).toMatch(/between 2025-08-12 and 2026-05-08/);
  });
  it("explains motion", () => {
    const r = whyUnreliable({ ...base, name: "3I/ATLAS", kind: "interstellar comet", moving: true });
    expect(r.map((x) => x.key)).toEqual(["moving"]);
  });
  it("names an unnamed position plainly", () => {
    expect(whyUnreliable({ ...base, name: null, saturated: true, kept: barnard })[0].text).toMatch(/^The object at the centre/);
  });
  it("explains a hole at the core in a single-wavelength picture", () => {
    const r = whyUnreliable({ ...base, holes: 3 });
    expect(r.map((x) => x.key)).toEqual(["hole"]);
    expect(r[0].text).toMatch(/no data at the core of Barnard's Star/);
  });
  it("explains a blend when the short and long wavelengths peak in different pixels", () => {
    const r = whyUnreliable({ ...base, name: "WISE 0855−0714", blend: { shortUm: 1.24, longUm: 4.6 } });
    expect(r.map((x) => x.key)).toEqual(["blend"]);
    expect(r[0].text).toMatch(/1\.24 µm and the light at 4\.60 µm peak in different pixels/);
  });
});
