import { afterEach, describe, expect, it, vi } from "vitest";
import type { AladinStatic } from "./aladin";
import type { SkyHandle } from "../app/modules";
import { mountSky } from "./index";
import { loadAladinOnce } from "./loader";

vi.mock("./loader", () => ({ loadAladinOnce: vi.fn(), SkyUnavailable: class extends Error {} }));
let sky: SkyHandle | undefined;
afterEach(() => {
  sky?.destroy();
  sky = undefined;
  vi.restoreAllMocks();
  document.body.innerHTML = "";
});

describe("lazy sky viewer loading", () => {
  it("does not create a hidden WebGL map when the CDN resolves after closing", async () => {
    let ready!: (A: AladinStatic) => void;
    vi.mocked(loadAladinOnce).mockReturnValueOnce(new Promise((resolve) => { ready = resolve; }));
    const el = document.createElement("section");
    document.body.append(el);
    sky = mountSky(el, {});
    const toggle = el.querySelector<HTMLButtonElement>(".sky-toggle")!;
    toggle.click();
    toggle.click();
    const aladin = vi.fn();
    ready({ aladin } as unknown as AladinStatic);
    await Promise.resolve();
    await Promise.resolve();
    expect(aladin).not.toHaveBeenCalled();
    expect(el.querySelector<HTMLElement>(".sky-body")!.hidden).toBe(true);

    // Reopening can use the already downloaded module; no stuck "loading" state.
    vi.mocked(loadAladinOnce).mockResolvedValueOnce({ aladin } as unknown as AladinStatic);
    toggle.click();
    await vi.waitFor(() => expect(aladin).toHaveBeenCalledOnce());
  });

  it("does not allocate the viewer after destruction during download", async () => {
    let ready!: (A: AladinStatic) => void;
    vi.mocked(loadAladinOnce).mockReturnValueOnce(new Promise((resolve) => { ready = resolve; }));
    const el = document.createElement("section");
    document.body.append(el);
    sky = mountSky(el, {});
    el.querySelector<HTMLButtonElement>(".sky-toggle")!.click();
    sky.destroy();
    sky = undefined;
    const aladin = vi.fn();
    ready({ aladin } as unknown as AladinStatic);
    await Promise.resolve();
    await Promise.resolve();
    expect(aladin).not.toHaveBeenCalled();
    expect(el.innerHTML).toBe("");
  });
});
