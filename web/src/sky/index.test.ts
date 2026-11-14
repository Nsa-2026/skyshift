import { describe, expect, it, vi } from "vitest";
import type { View } from "../app/modules";

const view: View = { ra: 269.4462, dec: 4.76883, clock: "months", fov: 5, det: 2, slug: "barnard", name: "Barnard's Star" };

describe("mountSky without WebGL2 (jsdom)", () => {
  it("stays closed until asked, then shows a calm fallback; never throws; controls stay honest", async () => {
    const getContext = vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
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

    // Closed by default on every width: nothing loads until the button.
    const toggle = el.querySelector<HTMLButtonElement>(".sky-toggle")!;
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(toggle.textContent).toContain("Show the sky around it");
    expect(el.querySelector<HTMLElement>(".sky-body")!.hidden).toBe(true);
    await new Promise((r) => setTimeout(r, 0));
    expect(getContext).not.toHaveBeenCalledWith("webgl2");

    toggle.click();
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(el.querySelector<HTMLElement>(".sky-body")!.hidden).toBe(false);
    await vi.waitFor(() => expect(el.textContent).toContain("The sky map can't open here: this browser has no WebGL2."));
    expect(el.textContent).toContain("The plate above works without it.");
    expect(el.classList.contains("sky-failed")).toBe(true);
    expect(onClick).not.toHaveBeenCalled();

    // Layer controls are real buttons with aria-pressed: one segmented pair, two toggles, six detectors.
    const pressed = [...el.querySelectorAll("button[aria-pressed]")].map((b) => b.textContent!.trim());
    expect(pressed).toEqual(expect.arrayContaining(["2MASS", "AllWISE", "SPHEREx", "Gaia stars", "D1", "D6"]));
    expect(el.querySelector(".seg [data-base=2mass]")!.getAttribute("aria-pressed")).toBe("true");
    expect(el.querySelector('[data-det="2"]')!.getAttribute("aria-pressed")).toBe("true");

    // Adjust is a disclosure, closed by default.
    const adjust = el.querySelector<HTMLButtonElement>(".sky-adjust")!;
    expect(adjust.getAttribute("aria-expanded")).toBe("false");
    expect(el.querySelector<HTMLElement>(".sky-more")!.hidden).toBe(true);
    adjust.click();
    expect(el.querySelector<HTMLElement>(".sky-more")!.hidden).toBe(false);

    // The key is one sentence with the pencil marks; turning Gaia off drops its clause.
    const key = () => el.querySelector(".sky-key")!.textContent!.replace(/\s+/g, " ").trim();
    expect(key()).toBe("The plate's 5′ field, Gaia DR3 stars and the target."); // no frames: no epoch date
    sky.epoch(61000);
    expect(key()).toMatch(/^The plate's 5′ field, Gaia DR3 stars on \d+ \w+ 2025 and the target\.$/);
    expect(el.querySelectorAll(".sky-key .key")).toHaveLength(3);
    el.querySelector<HTMLButtonElement>("[data-gaia]")!.click();
    expect(key()).toBe("The plate's 5′ field and the target.");

    toggle.click();
    expect(el.querySelector<HTMLElement>(".sky-body")!.hidden).toBe(true);
    sky.destroy();
    expect(el.innerHTML).toBe("");
    getContext.mockRestore();
  });
});
