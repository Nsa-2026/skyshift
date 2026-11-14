import { describe, expect, it } from "vitest";
import { MAX_FOV, MIN_FOV, formatState, parseState, sameState, stateHash, type AtlasState } from "./hash";

const ALL = ["mover", "high_pm", "spectral"];

describe("parseState", () => {
  it("reads ra, dec and field of view", () => {
    const s = parseState("122.554,17.0036,4.2")!;
    expect(s).toEqual({ ra: 122.554, dec: 17.0036, fov: 4.2, gal: false, mol: false, kinds: ALL });
  });

  it("reads the galactic grid, the whole-sky map and the kinds in any order", () => {
    const s = parseState("10,-5,30,mp,gal,mol")!;
    expect(s.gal).toBe(true);
    expect(s.mol).toBe(true);
    expect(s.kinds).toEqual(["mover", "high_pm"]);
    expect(parseState("10,-5,30,s")!.kinds).toEqual(["spectral"]);
    expect(parseState("10,-5,30,none")!.kinds).toEqual([]);
  });

  it("wraps RA and clamps the field of view", () => {
    expect(parseState("-10,0,1")!.ra).toBeCloseTo(350);
    expect(parseState("370,0,1")!.ra).toBeCloseTo(10);
    expect(parseState("0,0,0.0001")!.fov).toBe(MIN_FOV);
    expect(parseState("0,0,999")!.fov).toBe(MAX_FOV);
  });

  it("accepts an encoded parameter", () => {
    expect(parseState("122.5%2C17%2C3")!.fov).toBe(3);
  });

  it("refuses what isn't a view", () => {
    for (const bad of [null, undefined, "", "1,2", "a,b,c", "0,91,1", "0,-91,1", "0,0,0", "0,0,-2"]) {
      expect(parseState(bad)).toBeNull();
    }
  });

  it("ignores unknown tokens", () => {
    const s = parseState("1,2,3,zzz,gal")!;
    expect(s.gal).toBe(true);
    expect(s.kinds).toEqual(ALL);
  });
});

describe("formatState", () => {
  const base: AtlasState = { ra: 122.55415, dec: 17.00357, fov: 4.2, gal: false, mol: false, kinds: ["mover", "high_pm", "spectral"] };

  it("writes short numbers and leaves out the defaults", () => {
    expect(formatState(base)).toBe("122.5542,17.0036,4.2");
    expect(stateHash(base)).toBe("#/sky/122.5542,17.0036,4.2");
  });

  it("keeps about three figures of the field of view", () => {
    expect(formatState({ ...base, fov: 180 })).toBe("122.5542,17.0036,180");
    expect(formatState({ ...base, fov: 24.06 })).toBe("122.5542,17.0036,24.1");
    expect(formatState({ ...base, fov: 0.3512 })).toBe("122.5542,17.0036,0.351");
    expect(formatState({ ...base, fov: 0.04213 })).toBe("122.5542,17.0036,0.0421");
  });

  it("writes the flags and the kinds shown, in a fixed order", () => {
    expect(formatState({ ...base, gal: true, mol: true, kinds: ["high_pm", "mover"] })).toBe("122.5542,17.0036,4.2,gal,mol,mp");
    expect(formatState({ ...base, kinds: [] })).toBe("122.5542,17.0036,4.2,none");
  });

  it("round-trips", () => {
    const states: AtlasState[] = [
      base,
      { ...base, ra: 359.99, dec: -89.5, fov: 0.05, gal: true, kinds: ["spectral"] },
      { ...base, ra: 0, dec: 0, fov: 360, mol: true, kinds: [] },
    ];
    for (const s of states) {
      const back = parseState(formatState(s))!;
      expect(formatState(back)).toBe(formatState(s));
      expect(sameState(back, s)).toBe(true);
    }
  });

  it("treats views that print the same as the same", () => {
    expect(sameState(base, { ...base, ra: base.ra + 1e-6 })).toBe(true);
    expect(sameState(base, { ...base, fov: 5 })).toBe(false);
  });
});
