import { describe, expect, it } from "vitest";
import { cellSideDeg } from "./healpix";
import { LEVEL_FOV, cellPx, defaultMol, fovToOpen, heatOrder, levelFor, showsHeat, showsSignals, wholeFov } from "./zoom";

describe("levelFor", () => {
  it("splits the field of view into the four levels of the design", () => {
    expect(levelFor(360)).toBe("whole");
    expect(levelFor(180)).toBe("whole");
    expect(levelFor(60.01)).toBe("whole");
    expect(levelFor(60)).toBe("mid");
    expect(levelFor(24)).toBe("mid");
    expect(levelFor(8)).toBe("close");
    expect(levelFor(1.2)).toBe("close");
    expect(levelFor(0.5)).toBe("detail");
    expect(levelFor(0.05)).toBe("detail");
  });

  it("treats a broken field of view as the whole sky", () => {
    expect(levelFor(NaN)).toBe("whole");
    expect(levelFor(Infinity)).toBe("whole");
  });

  it("shows heat far out and signals close in, never both", () => {
    for (const l of ["whole", "mid", "close", "detail"] as const) {
      expect(showsHeat(l)).not.toBe(showsSignals(l));
    }
    expect(showsHeat("whole") && showsHeat("mid")).toBe(true);
    expect(showsSignals("close") && showsSignals("detail")).toBe(true);
  });
});

describe("heatOrder", () => {
  it("uses order 3 for the whole sky and order 5 in the middle range", () => {
    expect(heatOrder(180, 1400)).toBe(3);
    expect(heatOrder(24, 1400)).toBe(5);
  });

  it("drops to order 4 when order-5 cells would be specks", () => {
    // A phone, 390 px across 60°: an order-5 cell (1.8°) is ~12 px.
    expect(cellPx(5, 60, 390)).toBeLessThan(12);
    expect(heatOrder(60, 390)).toBe(4);
  });

  it("measures a cell on screen", () => {
    expect(cellPx(5, 10, 1000)).toBeCloseTo((cellSideDeg(5) / 10) * 1000);
    expect(cellPx(5, 0, 1000)).toBe(0);
    expect(cellPx(5, 10, 0)).toBe(0);
  });
});

describe("fovToOpen", () => {
  it("lands one level down from where the click happened", () => {
    expect(levelFor(fovToOpen(7, "whole"))).toBe("mid");
    expect(levelFor(fovToOpen(0.2, "whole"))).toBe("mid");
    expect(levelFor(fovToOpen(1.8, "mid"))).toBe("close");
    expect(levelFor(fovToOpen(30, "mid"))).toBe("close");
    expect(levelFor(fovToOpen(1, "close"))).toBe("detail");
  });

  it("leaves some sky round the patch", () => {
    expect(fovToOpen(10, "whole")).toBeGreaterThan(10);
    expect(fovToOpen(2, "mid")).toBeGreaterThan(2);
    expect(fovToOpen(0.01, "close")).toBeGreaterThanOrEqual(0.1);
    expect(fovToOpen(100, "whole")).toBeLessThanOrEqual(LEVEL_FOV.whole);
  });
});

describe("the whole-sky view", () => {
  it("is 180° on the globe and 360° on the ellipse", () => {
    expect(wholeFov(false)).toBe(180);
    expect(wholeFov(true)).toBe(360);
    expect(levelFor(wholeFov(false))).toBe("whole");
  });

  it("opens on the ellipse on a wide map and on the globe on a tall one", () => {
    expect(defaultMol(1440, 700)).toBe(true);
    expect(defaultMol(1024, 700)).toBe(true);
    expect(defaultMol(800, 700)).toBe(false);
    expect(defaultMol(390, 454)).toBe(false);
    expect(defaultMol(0, 500)).toBe(false);
    expect(defaultMol(500, 0)).toBe(false);
  });
});
