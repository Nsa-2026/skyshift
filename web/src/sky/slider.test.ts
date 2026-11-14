import { describe, expect, it } from "vitest";
import { frameText } from "./index";

describe("wavelength slider labels (layer A1)", () => {
  it("names wavelength, detector, channel and any feature", () => {
    expect(frameText(0)).toBe("0.75 µm · D1 channel 1");
    expect(frameText(62)).toMatch(/^3\.29 µm · D4 channel 12 · methane/);
    expect(frameText(80)).toMatch(/CO₂/);
  });
});
