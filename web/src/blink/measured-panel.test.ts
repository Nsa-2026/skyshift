// The "What SkyShift measured" panel in the mounted player: each state of contracts/api.md v3.3,
// the toggle beside the Gaia and Known marks, and the list that rings an entry on the plate.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { FramesResponse } from "../api/types";
import type { BlinkHandle, View } from "../app/modules";
import hoursJson from "./fixtures/frames-hours-measured.json";
import monthsJson from "./fixtures/frames-months-measured.json";
import { mountBlink } from "./index";

const N = 8;
const clone = <T>(x: T): T => JSON.parse(JSON.stringify(x)) as T;
const hours = () => clone(hoursJson) as unknown as FramesResponse;
const months = () => clone(monthsJson) as unknown as FramesResponse;
const hoursView: View = { ra: 123.94, dec: 17.41, clock: "hours", fov: 20, det: 2, slug: null, name: null };
const monthsView: View = { ra: 269.4462, dec: 4.76883, clock: "months", fov: 5, det: 2, slug: null, name: null };
const URL = "https://example.test/api/frames?x";

describe("what SkyShift measured", () => {
  let el: HTMLDivElement;
  let h: BlinkHandle;
  let onViewChange: ReturnType<typeof vi.fn>;
  let onEpoch: ReturnType<typeof vi.fn>;
  const panel = () => el.querySelector<HTMLElement>(".bk-meas")!;
  const lead = () => el.querySelector<HTMLElement>(".bk-meas-lead")!;
  const toggles = () => [...el.querySelectorAll<HTMLButtonElement>("button[data-ov='measured']")];

  beforeEach(() => {
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        disconnect() {}
      },
    );
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(new Uint16Array(N * N).fill(1000).buffer)),
    );
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    el = document.createElement("div");
    document.body.append(el);
    vi.spyOn(el, "getClientRects").mockReturnValue({ length: 1 } as unknown as DOMRectList);
    onViewChange = vi.fn();
    onEpoch = vi.fn();
    h = mountBlink(el, { onViewChange, onPick: vi.fn(), onEpoch });
  });
  afterEach(() => {
    h.destroy();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    el.remove();
  });

  it("shows nothing for data older than v3.3 (no measured fields)", async () => {
    const f = hours();
    delete f.measured;
    delete f.measured_reason;
    await h.show(hoursView, f, URL);
    expect(panel().hidden).toBe(true);
  });

  it.each([
    ["not_processed", "Not processed yet: this spot isn't in tonight's run."],
    ["processing", "Being processed: tonight's run covers this spot, but its results aren't combined yet. Check back later."],
    ["no_dots", "Not measured in these photos: the run covers this spot, but none of these photos has been searched for dots yet."],
    ["unavailable", "SkyShift's measurements didn't load in time. Asking again usually works. Try again"],
  ] as const)("says why nothing is measured: %s", async (reason, text) => {
    const f = hours();
    f.measured = null;
    f.measured_reason = reason;
    await h.show(hoursView, f, URL);
    expect(panel().hidden).toBe(false);
    expect(lead().textContent?.trim()).toBe(text);
    // Only the summary's parts hide: no legend, list or toggle without a measurement.
    expect(el.querySelector<HTMLElement>(".bk-meas-key")!.hidden).toBe(true);
    expect(el.querySelector<HTMLElement>(".bk-meas-toggle")!.hidden).toBe(true);
    const link = lead().querySelector("a");
    if (reason === "not_processed" || reason === "processing") expect(link?.getAttribute("href")).toBe("#/live");
    else expect(link).toBeNull();
  });

  it("asks for the frames again when the measurement was unavailable", async () => {
    const f = hours();
    f.measured = null;
    f.measured_reason = "unavailable";
    await h.show(hoursView, f, URL);
    el.querySelector<HTMLButtonElement>("[data-meas-retry]")!.click();
    expect(onViewChange).toHaveBeenCalledWith({});
  });

  it("hours: the summary, legend, arrow note and movers list", async () => {
    await h.show(hoursView, hours(), URL);
    expect(panel().hidden).toBe(false);
    expect(lead().textContent).toBe(
      "14 dots in 4 photos, and 3 movers: 1 known asteroid, 1 suspect (grade B) and 1 other mover.",
    );
    expect(el.querySelector(".bk-meas-stage")!.textContent).toBe("Final");
    const legend = [...el.querySelectorAll(".bk-meas-key li")].map((li) => li.textContent);
    // Its photos have saturated pixels (sat_frac 0.001): the legend explains the dashed ring.
    expect(legend).toEqual(["Suspect", "Known asteroid or comet", "Other mover", "Artefact", "Gaia star", "Other source", "Saturated star"]);
    const how = el.querySelector(".bk-meas-how")!.textContent!;
    expect(how).toMatch(/each as big as its source looks on the plate/);
    // Plain view: one short line; the long explanation, breakdown and notes are Advanced only.
    expect(el.querySelector(".bk-meas-short")!.textContent).toBe(
      "Circles: what we measured. Blue rings: Gaia stars. Violet diamonds: known asteroids. Arrows: motion.",
    );
    for (const c of [".bk-meas-how", ".bk-meas-rest", ".bk-meas-notes"]) expect(el.querySelector(c)!.classList.contains("pro-only")).toBe(true);
    expect(how).toMatch(/a little bigger, so a match reads as a circle inside a ring/);
    expect(how).toMatch(/Arrows join each mover's dots in time order, to scale\. One too short to see is drawn longer, with its factor \(×10\)/);
    expect(el.querySelector(".bk-meas-lh")!.textContent).toBe("Movers and artefacts on all detectors");
    const items = [...el.querySelectorAll<HTMLButtonElement>(".bk-mi")];
    expect(items.map((b) => b.querySelector(".bk-mi-t")!.textContent)).toEqual([
      "Suspect, grade B",
      "Known: (433) Eros",
      "Other mover: near a Gaia star",
      "Artefact: ghost, halo, new_code",
    ]);
    expect(items.every((b) => b.getAttribute("aria-pressed") === "false")).toBe(true);
    expect(el.querySelector(".bk-meas-notes")!.textContent).toBe("Final results from the hunt zone.");
  });

  it("the toggle sits with the Gaia and Known marks, is on by default and mirrors the panel's", async () => {
    await h.show(hoursView, hours(), URL);
    const marks = [...el.querySelectorAll<HTMLButtonElement>(".bk-marks button")].map((b) => b.dataset.ov);
    expect(marks.slice(0, 3)).toEqual(["measured", "gaia", "known"]);
    expect(toggles()).toHaveLength(2);
    expect(toggles().map((b) => b.getAttribute("aria-pressed"))).toEqual(["true", "true"]);
    el.querySelector<HTMLButtonElement>(".bk-meas-toggle")!.click();
    expect(toggles().map((b) => b.getAttribute("aria-pressed"))).toEqual(["false", "false"]);
    el.querySelector<HTMLButtonElement>(".bk-marks [data-ov='measured']")!.click();
    expect(toggles().map((b) => b.getAttribute("aria-pressed"))).toEqual(["true", "true"]);
  });

  it("a list entry names itself under the plate on focus, and a click pins it and shows a photo it is in", async () => {
    await h.show(hoursView, hours(), URL);
    const say = () => el.querySelector(".bk-say")!.textContent;
    const art = [...el.querySelectorAll<HTMLButtonElement>(".bk-mi")][3];
    art.focus();
    expect(say()).toBe("Artefact: ghost, halo, new_code S/N 11, 480 µJy");
    art.blur();
    expect(say()).toMatch(/^Each exposure in turn/);
    onEpoch.mockClear();
    art.click();
    expect(art.getAttribute("aria-pressed")).toBe("true");
    // The artefact is in the second D2 exposure only: the player goes there.
    expect(onEpoch).toHaveBeenLastCalledWith(expect.objectContaining({ id: "d2-2025W42_2A_0213_2" }));
    art.click();
    expect(art.getAttribute("aria-pressed")).toBe("false");
  });

  it("months: shifts, distinct sources, provisional, missing photos and the cap", async () => {
    await h.show(monthsView, months(), URL);
    expect(lead().textContent).toBe("At least 5 sources in 48 photos, and 1 moved between visits: 1 Gaia star.");
    expect(el.querySelector(".bk-meas-rest")!.textContent).toMatch(/^2 weaker shifts are listed but not drawn: under 5σ, or Gaia measures a different motion\./);
    expect(el.querySelector(".bk-meas-stage")!.textContent).toBe("Provisional");
    const how = el.querySelector(".bk-meas-how")!.textContent!;
    expect(how).toMatch(/Arrows run from where we measured a source in the first visit to where we measured it in the last, to scale\./);
    expect(how).toMatch(/Only shifts of 5σ or more, or that Gaia's own motion confirms, get one; the rest are in the list\./);
    // Barnard's Star (G 8.2) saturates: the dashed ring is explained, and no word about shortened arrows.
    expect(how).toMatch(/A dashed ring is a saturated star/);
    expect(how).not.toMatch(/shortened|1σ/);
    expect(el.querySelector(".bk-meas-lh")!.textContent).toBe("What moved between visits");
    expect([...el.querySelectorAll(".bk-mi-t")].map((x) => x.textContent)).toEqual([
      "Artefact: too few dots, poor motion fit",
      "Gaia star moved 10.4″ ± 0.09″ in 1.0 yr",
      "Suspect moved 0.65″ ± 0.20″ in 1.0 yr",
      "Source without Gaia moved 0.39″ ± 0.11″ in 1.0 yr",
    ]);
    expect(el.querySelectorAll(".bk-mi-n")[2].textContent).toMatch(/Not on the plate: under 5σ\.$/);
    const notes = el.querySelector(".bk-meas-notes")!.textContent!;
    expect(notes).toContain("2 of these 50 photos have no dots yet");
    expect(notes).toContain("A busy field: each visit shows at most 250 marks");
  });

  it("shows the extracted stage, and a stage it doesn't know, without breaking", async () => {
    const f = hours();
    f.measured!.stage = "extracted";
    f.measured!.tracks = [];
    await h.show(hoursView, f, URL);
    expect(el.querySelector(".bk-meas-stage")!.textContent).toBe("Extracted");
    expect(lead().textContent).toBe("14 dots in 4 photos, classed against Gaia only: movers aren't linked yet.");
    const g = hours();
    g.measured!.stage = "something_new";
    await h.show({ ...hoursView, ra: 123.95 }, g, URL);
    expect(el.querySelector(".bk-meas-stage")!.textContent).toBe("Something new");
    expect(el.querySelector(".bk-meas-notes")!.textContent).toBe("Results from the hunt zone, at the stage it calls something new.");
  });

  it("a grade C suspect is greyed in the list and the legend, still named", async () => {
    const f = hours();
    for (const d of f.measured!.detections) if (d.class === "suspect") Object.assign(d, { grade: "C", reasons: ["hot_pixel"] });
    await h.show(hoursView, f, URL);
    const sus = [...el.querySelectorAll<HTMLButtonElement>(".bk-mi")].find((b) => b.textContent!.startsWith("Suspect"))!;
    expect(sus.querySelector(".bk-mg")!.getAttribute("data-grade")).toBe("C");
    expect(sus.querySelector(".bk-mi-t")!.textContent).toBe("Suspect, grade C: hot pixel");
    expect([...el.querySelectorAll(".bk-meas-key li")].map((li) => li.textContent)).toContain("Suspect, grade C");
    expect(lead().textContent).toContain("1 suspect (grade C)");
  });

  it("hides the panel again when the next spot has no measured fields, and on an error", async () => {
    await h.show(hoursView, hours(), URL);
    expect(panel().hidden).toBe(false);
    const old = hours();
    delete old.measured;
    delete old.measured_reason;
    await h.show({ ...hoursView, ra: 124 }, old, URL);
    expect(panel().hidden).toBe(true);
    await h.show(hoursView, hours(), URL);
    h.status("error", "The live service failed.");
    expect(panel().hidden).toBe(true);
  });
});

// The Viewer tab's plate (BlinkOptions.viewer): the circles start off and the panel is one folded
// line with its count; opening it draws the circles. Field buttons read Close-up/Wide, and a
// double-click zooms. Other plates keep the panel above.
describe("what SkyShift measured, on the Viewer", () => {
  let el: HTMLDivElement;
  let h: BlinkHandle;
  const toggles = () => [...el.querySelectorAll<HTMLButtonElement>("button[data-ov='measured']")];

  beforeEach(() => {
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        disconnect() {}
      },
    );
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(new Uint16Array(N * N).fill(1000).buffer)),
    );
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    el = document.createElement("div");
    document.body.append(el);
    vi.spyOn(el, "getClientRects").mockReturnValue({ length: 1 } as unknown as DOMRectList);
    h = mountBlink(el, { onViewChange: vi.fn(), onPick: vi.fn(), viewer: true });
  });
  afterEach(() => {
    h.destroy();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    el.remove();
  });

  it("is folded to one line with its count; opening it draws the circles, folding it clears them", async () => {
    await h.show(hoursView, hours(), URL);
    const panel = el.querySelector<HTMLDetailsElement>(".bk-meas")!;
    expect(panel.tagName).toBe("DETAILS");
    expect(panel.open).toBe(false);
    expect(el.querySelector(".bk-meas-sum")!.textContent!.replace(/\s+/g, " ").trim()).toBe("What SkyShift measured (3)");
    expect(toggles().map((b) => b.getAttribute("aria-pressed"))).toEqual(["false", "false"]);
    panel.open = true;
    panel.dispatchEvent(new Event("toggle"));
    expect(toggles().map((b) => b.getAttribute("aria-pressed"))).toEqual(["true", "true"]);
    el.querySelector<HTMLButtonElement>(".bk-meas-toggle")!.click(); // the switch still works while open
    expect(toggles().map((b) => b.getAttribute("aria-pressed"))).toEqual(["false", "false"]);
    panel.open = false;
    panel.dispatchEvent(new Event("toggle"));
    expect(toggles().map((b) => b.getAttribute("aria-pressed"))).toEqual(["false", "false"]);
  });

  it("says it in plain words: no artefacts, no stage badge, no S/N or µJy, and what the blue circles are", async () => {
    await h.show(hoursView, hours(), URL);
    const text = (sel: string) => el.querySelector(sel)!.textContent!.replace(/\s+/g, " ").trim();
    const items = [...el.querySelectorAll(".bk-mi-t")].map((x) => x.textContent);
    expect(items).toEqual(["Suspect, grade B", "Known: (433) Eros", "Other mover: near a Gaia star"]);
    expect(text(".bk-meas-key")).not.toMatch(/Artefact|Other source|stack only/);
    expect(el.querySelector<HTMLElement>(".bk-meas-stage")!.hidden).toBe(true);
    for (const sel of [".bk-meas-rest", ".bk-meas-how", ".bk-meas-notes", ".bk-meas-pro"]) expect(text(sel)).toBe("");
    expect(text(".bk-meas-lh")).toBe("What moved between photos");
    expect(text(".bk-meas-short")).toMatch(/^Blue circles: stars Gaia already knows/);
    const numbers = [...el.querySelectorAll(".bk-mi-n")].map((x) => x.textContent!).join(" ");
    expect(numbers).toMatch(/Seen in \d+ photos/);
    expect(numbers).not.toMatch(/S\/N|µJy|σ|mas\/yr/);
    expect(text(".bk-key")).toMatch(/^Blue circles: stars Gaia already knows ?Click a dot to flag it/); // the "·" between them is CSS
  });

  it("names the fields Close-up and Wide, with their size against the Moon", async () => {
    await h.show(monthsView, months(), URL);
    const fov = [...el.querySelectorAll<HTMLButtonElement>(".bk-fov button")];
    expect(fov.map((b) => b.textContent)).toEqual(["Close-up", "Wide"]);
    expect(fov[0].title).toMatch(/^Close-up: 5 arcminutes, about a sixth of the full Moon's width$/);
  });
});

// The To check plates (src/review/plate.ts, BlinkOptions.hideArtefacts): the full panel, minus
// artefacts on the plate, in the legend, the summary and the list.
describe("what SkyShift measured, on the To check plates", () => {
  let el: HTMLDivElement;
  let h: BlinkHandle;

  beforeEach(() => {
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        disconnect() {}
      },
    );
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(new Uint16Array(N * N).fill(1000).buffer)),
    );
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    el = document.createElement("div");
    document.body.append(el);
    vi.spyOn(el, "getClientRects").mockReturnValue({ length: 1 } as unknown as DOMRectList);
    h = mountBlink(el, { onViewChange: vi.fn(), onPick: vi.fn(), hideArtefacts: true });
  });
  afterEach(() => {
    h.destroy();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    el.remove();
  });

  it("hours: no artefact in the legend, the heading, the list or the Advanced lines", async () => {
    await h.show(hoursView, hours(), URL);
    const text = (sel: string) => el.querySelector(sel)!.textContent!.replace(/\s+/g, " ").trim();
    expect(el.querySelector(".bk-meas-stage")!.textContent).toBe("Final");
    expect([...el.querySelectorAll(".bk-meas-key li")].map((li) => li.textContent)).toEqual([
      "Suspect",
      "Known asteroid or comet",
      "Other mover",
      "Gaia star",
      "Other source",
      "Saturated star",
    ]);
    expect(text(".bk-meas-lh")).toBe("Movers on all detectors");
    expect([...el.querySelectorAll(".bk-mi-t")].map((x) => x.textContent)).toEqual([
      "Suspect, grade B",
      "Known: (433) Eros",
      "Other mover: near a Gaia star",
    ]);
    expect(text(".bk-meas")).not.toMatch(/artefact|artifact/i);
  });

  it("months: the artefact source is left out of the list", async () => {
    await h.show(monthsView, months(), URL);
    expect([...el.querySelectorAll(".bk-mi-t")].map((x) => x.textContent)).toEqual([
      "Gaia star moved 10.4″ ± 0.09″ in 1.0 yr",
      "Suspect moved 0.65″ ± 0.20″ in 1.0 yr",
      "Source without Gaia moved 0.39″ ± 0.11″ in 1.0 yr",
    ]);
    expect(el.querySelector(".bk-meas")!.textContent).not.toMatch(/artefact|artifact/i);
  });
});
