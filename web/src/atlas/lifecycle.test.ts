import { afterEach, describe, expect, it, vi } from "vitest";
import type { PageContext, PageHandle } from "../app/modules";
import { loadAtlasData, type AtlasData } from "./data";
import { mountAtlas } from "./index";

vi.mock("./data", () => ({ loadAtlasData: vi.fn() }));
vi.mock("../sky/loader", () => ({ browserHasWebGL2: () => false, loadAladinOnce: vi.fn(), SkyUnavailable: class extends Error {} }));
vi.mock("./search", async (original) => ({ ...await original<object>(), loadSearch: vi.fn().mockResolvedValue(null) }));

const empty: AtlasData = { generated: null, dataRelease: null, zones: [], candidates: [], origin: "none" };
let page: PageHandle | undefined;

afterEach(() => {
  page?.destroy?.();
  page = undefined;
  vi.restoreAllMocks();
  vi.useRealTimers();
  document.body.innerHTML = "";
});

describe("atlas startup navigation", () => {
  it("uses the latest sky when navigation changes while candidates are loading", async () => {
    let ready!: (data: AtlasData) => void;
    vi.mocked(loadAtlasData).mockReturnValueOnce(new Promise((resolve) => { ready = resolve; }));
    const el = document.createElement("section");
    document.body.append(el);
    const skySelection = vi.fn();
    page = mountAtlas(el, { skySelection, explore: vi.fn() } as unknown as PageContext);
    page.show("10,20,5");
    page.show("42,-12,2");
    ready(empty);
    await vi.waitFor(() => expect(skySelection).toHaveBeenLastCalledWith({ ra: 42, dec: -12 }, "#/sky/42,-12,2"));
    expect(location.hash).toBe("#/sky/42,-12,2");
    expect(el.querySelectorAll(".atl-svgmap")).toHaveLength(1);
  });

  it("does not create a map after the page is destroyed during its load", async () => {
    let ready!: (data: AtlasData) => void;
    vi.mocked(loadAtlasData).mockReturnValueOnce(new Promise((resolve) => { ready = resolve; }));
    const el = document.createElement("section");
    document.body.append(el);
    const skySelection = vi.fn();
    page = mountAtlas(el, { skySelection, explore: vi.fn() } as unknown as PageContext);
    page.show("10,20,5");
    page.destroy?.();
    page = undefined;
    ready(empty);
    await Promise.resolve();
    await Promise.resolve();
    expect(el.innerHTML).toBe("");
    expect(skySelection).not.toHaveBeenCalled();
  });

  it("cancels settling and refresh timers when the map is hidden", async () => {
    vi.useFakeTimers();
    vi.mocked(loadAtlasData).mockResolvedValueOnce(empty);
    const el = document.createElement("section");
    document.body.append(el);
    page = mountAtlas(el, { skySelection: vi.fn(), explore: vi.fn() } as unknown as PageContext);
    page.show("10,20,5");
    await Promise.resolve();
    await Promise.resolve();
    page.show("42,-12,2");
    vi.advanceTimersByTime(20);
    expect(vi.getTimerCount()).toBeGreaterThan(0);
    page.hide?.();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("defers map allocation when hidden during startup and resumes at the new view", async () => {
    let ready!: (data: AtlasData) => void;
    vi.mocked(loadAtlasData).mockReturnValueOnce(new Promise((resolve) => { ready = resolve; }));
    const el = document.createElement("section");
    document.body.append(el);
    const skySelection = vi.fn();
    page = mountAtlas(el, { skySelection, explore: vi.fn() } as unknown as PageContext);
    page.show("10,20,5");
    page.hide?.();
    ready(empty);
    await Promise.resolve();
    await Promise.resolve();
    expect(el.querySelector(".atl-svgmap")).toBeNull();
    expect(skySelection).not.toHaveBeenCalled();
    page.show("42,-12,2");
    expect(el.querySelectorAll(".atl-svgmap")).toHaveLength(1);
    expect(skySelection).toHaveBeenLastCalledWith({ ra: 42, dec: -12 }, "#/sky/42,-12,2");
  });
});
