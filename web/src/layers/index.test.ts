// The plate-layer control around the player: what the player is handed for each view and layer,
// what the row says, and that a newer view always wins. The player is a stand-in that records
// what it was shown (web/src/blink is P4's and not under test here).
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { FramesResponse } from "../api/types";
import type { BlinkHandle, BlinkOptions, MountBlink, View } from "../app/modules";
import { mountPlateLayers, type PlateLayersHandle } from "./index";
import { epoch, frames } from "./testkit";

const BARNARD: View = { ra: 269.4462, dec: 4.76883, clock: "months", fov: 5, det: 2, slug: "barnard", name: "Barnard's Star" };
const EPSINDIB: View = { ra: 331.09668, dec: -56.80067, clock: "months", fov: 5, det: 2, slug: "epsindib", name: "Epsilon Indi B" };
const SPOT: View = { ra: 125.5, dec: 19.4, clock: "months", fov: 5, det: 2, slug: null, name: null };
const OBS_EPOCHS = [epoch("d2-v1", 2, 60913.3286), epoch("d2-v2", 2, 61117.63)];
const GAIA = [{ id: "1", ra: 269.45, dec: 4.77, pmra: 1, pmdec: 2, parallax: null, g: 12 }];
const observed = (): FramesResponse => frames(OBS_EPOCHS, { gaia: GAIA });
const OBS_URL = "http://localhost/data/barnard/frames_months_5.json";

interface Shown {
  view: View;
  frames: FramesResponse;
  url: string;
}
let shows: Shown[];
let statuses: [string, string | undefined][];
let blinkOpts: BlinkOptions;
let el: HTMLElement;
let handle: PlateLayersHandle;
let onViewChange: ReturnType<typeof vi.fn>;

/** Stand-in player: the DOM the row attaches to, and a record of show() and status() calls. */
const fakeMount: MountBlink = (target, opts) => {
  blinkOpts = opts;
  target.innerHTML = `<div class="bk"><div class="bk-col"><div class="bk-mat"></div><div class="bk-row"></div>
    <div class="bk-adjust" hidden></div><section class="bk-meas"></section></div></div>`;
  const h: BlinkHandle = {
    async show(view, f, url) {
      shows.push({ view, frames: f, url });
    },
    status(kind, message) {
      statuses.push([kind, message]);
    },
    destroy() {
      target.innerHTML = "";
    },
  };
  return h;
};

/** Science-data answers: a residual of Barnard and Eps Indi B on the same visits, or a failure. */
function stubFetch(answer: (url: string) => unknown | Promise<unknown>) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      calls.push(String(url));
      const body = await answer(String(url));
      if (body === 404) return { ok: false, status: 404, statusText: "Not Found", json: async () => ({ error: { code: "not_found", message: "no" } }) };
      return { ok: true, json: async () => body };
    }),
  );
  return calls;
}
const residual = (url: string): FramesResponse =>
  frames(
    OBS_EPOCHS.map((e) => ({ ...e, flag_frac: null, sat_frac: null, um_min: null, um_max: null, data_url: `views/resid/frames/${e.id}.bin` }) as never),
    { center: { ra: 269.4462, dec: 4.769 }, gaia: [], ...(url.includes("epsindib") ? { center: { ra: 331.0967, dec: -56.8013 } } : {}) },
  );

const q = <T extends Element>(sel: string) => el.querySelector(sel) as T;
const button = (id: string) => q<HTMLButtonElement>(`[data-plate-layer="${id}"]`);
const says = () => q<HTMLElement>(".pl-say").textContent ?? "";
const flush = () => new Promise((r) => setTimeout(r, 0));

beforeEach(() => {
  // The layer lives in the URL: every test starts from a plain one.
  history.replaceState(null, "", "/");
  shows = [];
  statuses = [];
  el = document.createElement("div");
  document.body.appendChild(el);
  onViewChange = vi.fn();
  handle = mountPlateLayers(el, fakeMount, { onViewChange, onPick() {}, onEpoch: vi.fn() });
});
afterEach(() => {
  handle.destroy();
  el.remove();
  vi.unstubAllGlobals();
});

describe("the row", () => {
  it("sits under the player's controls, hidden until a view is shown, with Observed pressed", async () => {
    const row = q<HTMLElement>(".pl");
    expect(row.previousElementSibling?.classList.contains("bk-adjust")).toBe(true);
    expect(row.hidden).toBe(true);
    await handle.show(BARNARD, observed(), OBS_URL);
    expect(row.hidden).toBe(false);
    expect([...el.querySelectorAll("[data-plate-layer]")].map((b) => b.textContent)).toEqual(["Observed", "Sky model", "Residual", "DR1 difference"]);
    expect(button("raw").getAttribute("aria-pressed")).toBe("true");
    expect(button("resid").getAttribute("aria-disabled")).toBe("false");
    // Observed: the player gets the page's own frames, untouched.
    expect(shows.at(-1)).toEqual({ view: BARNARD, frames: expect.objectContaining({ epochs: OBS_EPOCHS }), url: OBS_URL });
    expect(says()).toBe("");
  });

  it("says the shown visit's quality in one sentence", async () => {
    await handle.show(BARNARD, observed(), OBS_URL);
    blinkOpts.onEpoch?.({ id: "d2-v2", mjd_mid: 61117.63, det: 2 });
    expect(q(".pl-quality").textContent).toBe(
      "In D2, this visit has 2.5% of its pixels masked and 0.23% saturated, counted over the whole frame.",
    );
  });
});

describe("a derived layer", () => {
  it("loads the residual from /science-data/ and keeps the observations' Gaia stars on it", async () => {
    const calls = stubFetch(residual);
    await handle.show(BARNARD, observed(), OBS_URL);
    button("resid").click();
    await vi.waitFor(() => expect(shows).toHaveLength(2));
    expect(calls).toEqual(["http://localhost:3000/science-data/barnard/resid.json"]);
    const s = shows[1];
    expect(s.view).toEqual(BARNARD);
    expect(s.url).toBe("http://localhost:3000/science-data/barnard/resid.json");
    expect(s.frames.epochs[0].data_url).toBe("views/resid/frames/d2-v1.bin");
    expect(s.frames.gaia).toEqual(GAIA);
    // A quick load never puts the player in its loading state (which would pause the blink).
    expect(statuses).toEqual([]);
    expect(button("resid").getAttribute("aria-pressed")).toBe("true");
    expect(says()).toMatch(/^Each visit minus the sky model/);
    expect(q(".pl-quality").textContent).toMatch(/without SPHEREx's flags/);
    // Back to Observed: the page's frames again, no fetch.
    button("raw").click();
    await flush();
    expect(shows.at(-1)!.url).toBe(OBS_URL);
    expect(calls).toHaveLength(1);
    expect(says()).toBe("");
  });

  it("stays on while the target changes, if the new target has it", async () => {
    const calls = stubFetch(residual);
    await handle.show(BARNARD, observed(), OBS_URL);
    button("resid").click();
    await vi.waitFor(() => expect(shows).toHaveLength(2));
    handle.status("loading");
    await handle.show(EPSINDIB, observed(), "http://localhost/data/epsindib/frames_months_5.json");
    expect(calls.at(-1)).toBe("http://localhost:3000/science-data/epsindib/resid.json");
    expect(shows.at(-1)!.view).toEqual(EPSINDIB);
    expect(shows.at(-1)!.url).toContain("/science-data/epsindib/resid.json");
  });

  it("goes back to Observed where the layer doesn't exist, says why and offers the switch", async () => {
    stubFetch(residual);
    await handle.show(BARNARD, observed(), OBS_URL);
    button("resid").click();
    await vi.waitFor(() => expect(shows).toHaveLength(2));
    const hours: View = { ...BARNARD, clock: "hours", fov: 20 };
    await handle.show(hours, observed(), "http://localhost/data/barnard/frames_hours_20.json");
    expect(shows.at(-1)!.url).toBe("http://localhost/data/barnard/frames_hours_20.json");
    expect(handle.layer()).toBe("raw");
    expect(button("raw").getAttribute("aria-pressed")).toBe("true");
    expect(button("resid").getAttribute("aria-disabled")).toBe("true");
    expect(says()).toMatch(/prepared at 5′ on the Months clock\. The plate shows the observations\./);
    // The switch asks the page for Months 5′ and brings the residual back with it.
    q<HTMLButtonElement>(".pl-fix").click();
    expect(onViewChange).toHaveBeenCalledWith({ clock: "months", fov: 5 });
    expect(handle.layer()).toBe("resid");
    await handle.show(BARNARD, observed(), OBS_URL);
    expect(shows.at(-1)!.url).toContain("/science-data/barnard/resid.json");
  });

  it("is offered nowhere else: a dimmed option says why and loads nothing", async () => {
    const calls = stubFetch(residual);
    await handle.show(SPOT, observed(), "http://localhost/api/frames?ra=125.5");
    expect(button("model").getAttribute("aria-disabled")).toBe("true");
    expect(button("model").title).toContain("five featured stars");
    button("model").click();
    await flush();
    expect(calls).toEqual([]);
    expect(shows).toHaveLength(1);
    expect(handle.layer()).toBe("raw");
    expect(says()).toContain("Pick one of them under Try");
    expect(el.querySelector(".pl-fix")).toBeNull();
  });

  it("falls back to the observations with a plain line when the layer fails to load", async () => {
    stubFetch(() => 404);
    await handle.show(BARNARD, observed(), OBS_URL);
    button("refdiff").click();
    // Static file missing, then the API: both 404.
    await vi.waitFor(() => expect(shows).toHaveLength(2));
    expect(shows[1].url).toBe(OBS_URL);
    expect(handle.layer()).toBe("raw");
    expect(says()).toBe("The DR1 difference didn't load, so the plate shows the observations. Try it again in a minute.");
  });

  it("shows the player's loading state when a layer is slow to arrive", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    stubFetch(async (url) => {
      await gate;
      return residual(url);
    });
    await handle.show(BARNARD, observed(), OBS_URL);
    button("model").click();
    await vi.waitFor(() => expect(statuses).toContainEqual(["loading", "Loading the sky model…"]), { timeout: 1000 });
    release();
    await vi.waitFor(() => expect(shows.at(-1)!.url).toContain("/science-data/barnard/model.json"));
  });

  it("never shows a layer that finished loading after the page moved on", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    stubFetch(async (url) => {
      await gate;
      return residual(url);
    });
    await handle.show(BARNARD, observed(), OBS_URL);
    button("resid").click();
    await flush();
    // The page starts loading another view before the residual arrives.
    handle.status("loading", "Loading frames…");
    release();
    await flush();
    await flush();
    expect(shows).toHaveLength(1);
    expect(statuses.at(-1)).toEqual(["loading", "Loading frames…"]);
  });

  it("lives in the URL beside the view, so a residual can be shared", async () => {
    stubFetch(residual);
    history.replaceState(null, "", "/?t=barnard&clock=months&fov=5&det=2#/explore");
    await handle.show(BARNARD, observed(), OBS_URL);
    button("resid").click();
    expect(location.search).toBe("?t=barnard&clock=months&fov=5&det=2&layer=resid");
    expect(location.hash).toBe("#/explore");
    await vi.waitFor(() => expect(shows).toHaveLength(2));
    button("raw").click();
    expect(location.search).toBe("?t=barnard&clock=months&fov=5&det=2");
    // Opening such a link starts on that layer.
    history.replaceState(null, "", "/?t=barnard&layer=model#/explore");
    handle.destroy();
    shows = [];
    handle = mountPlateLayers(el, fakeMount, { onViewChange, onPick() {} });
    expect(handle.layer()).toBe("model");
    await handle.show(BARNARD, observed(), OBS_URL);
    expect(shows.at(-1)!.url).toContain("/science-data/barnard/model.json");
    // Other pages' URLs are left alone.
    history.replaceState(null, "", "/?t=barnard#/review");
    button("raw").click();
    expect(location.search).toBe("?t=barnard");
    history.replaceState(null, "", "/");
  });

  it("takes a colour filter off the plate so the chosen layer is seen", async () => {
    stubFetch(residual);
    await handle.show(BARNARD, observed(), OBS_URL);
    // A filter is on (web/src/colour adds .flt-on to the player and a Remove filter button).
    const off = document.createElement("button");
    off.className = "flt-off";
    const removed = vi.fn(() => q(".bk").classList.remove("flt-on"));
    off.addEventListener("click", removed);
    q(".bk").classList.add("flt-on");
    q(".bk-col").prepend(off);
    button("model").click();
    expect(removed).toHaveBeenCalledTimes(1);
    expect(document.activeElement).toBe(button("model"));
    await vi.waitFor(() => expect(shows.at(-1)!.url).toContain("/science-data/barnard/model.json"));
  });
});
