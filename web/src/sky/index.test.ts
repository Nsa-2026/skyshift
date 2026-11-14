import { describe, expect, it, vi } from "vitest";
import type { View } from "../app/modules";

const view: View = { ra: 269.4462, dec: 4.76883, clock: "months", fov: 5, det: 2, slug: "barnard", name: "Barnard's Star" };

describe("mountSky without WebGL2 (jsdom)", () => {
  it("shows a calm fallback, never throws, and keeps its controls honest", async () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    const { mountSky } = await import("./index");
    const el = document.createElement("section");
    document.body.append(el);
    const onClick = vi.fn();
    const sky = mountSky(el, { onClick });
    expect(() => {
      sky.goto(view, null);
      sky.epoch(61000);
      sky.goto({ ...view, ra: 10, dec: -5 }, null);
    }).not.toThrow();
    await vi.waitFor(() => expect(el.textContent).toContain("Sky view unavailable: this browser has no WebGL2"));
    expect(el.textContent).toContain("the blink above still works");
    expect(onClick).not.toHaveBeenCalled();
    // Layer buttons are real buttons with aria-pressed.
    const pressed = [...el.querySelectorAll("button[aria-pressed]")].map((b) => b.textContent!.trim());
    expect(pressed).toEqual(expect.arrayContaining(["2MASS", "AllWISE", "SPHEREx", "Gaia stars", "D1", "D6"]));
    expect(el.querySelector('[data-det="2"]')!.getAttribute("aria-pressed")).toBe("true");
    sky.destroy();
    expect(el.innerHTML).toBe("");
  });
});
