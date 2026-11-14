// The home page is the Sky map (#119), before any area is chosen; Watch opens on shared target links.
import { afterEach, expect, it, vi } from "vitest";

const atlas = vi.hoisted(() => ({ show: vi.fn(), ctx: null as null | import("./modules").PageContext }));
vi.mock("../atlas", () => ({
  mountAtlas: (_el: HTMLElement, ctx: import("./modules").PageContext) => {
    atlas.ctx = ctx;
    return { show: atlas.show, hide: vi.fn(), destroy: vi.fn() };
  },
}));
vi.mock("../blink", () => ({
  mountBlink: () => ({ show: vi.fn(async () => {}), status: vi.fn(), highlight: vi.fn(), active: vi.fn(), destroy: vi.fn() }),
}));
vi.mock("../vote", async (original) => ({
  ...(await original<typeof import("../vote")>()),
  mountVote: () => ({ pick: vi.fn(), view: vi.fn() }),
}));
vi.mock("../sky", () => ({ mountSky: () => ({ goto: vi.fn(), epoch: vi.fn(), destroy: vi.fn() }) }));
vi.mock("../api/client", async (original) => {
  const actual = await original<typeof import("../api/client")>();
  return { ...actual, loadFrames: vi.fn(() => new Promise(() => {})), loadObject: vi.fn(() => new Promise(() => {})) };
});
import { loadFrames } from "../api/client";
import { startApp } from "./app";

window.scrollTo = vi.fn() as unknown as typeof window.scrollTo; // jsdom doesn't implement it

const visible = (root: HTMLElement) =>
  [...root.querySelectorAll<HTMLElement>(".page")].filter((p) => !p.hidden).map((p) => p.dataset.page);

afterEach(() => {
  document.body.replaceChildren();
  vi.mocked(loadFrames).mockClear();
});

it("opens on the Sky map with the three areas one click away, and loads no frames", () => {
  history.replaceState(null, "", "/");
  const root = document.createElement("div");
  document.body.append(root);
  startApp(root);
  expect(visible(root)).toEqual(["atlas"]);
  expect(atlas.show).toHaveBeenCalledTimes(1);
  expect(root.querySelector(".nav [aria-current]")).toBeNull();
  expect(root.querySelector<HTMLElement>(".area-features")!.hidden).toBe(true);
  expect([...root.querySelectorAll<HTMLAnchorElement>(".nav a")].map((a) => a.getAttribute("href"))).toEqual([
    "#/explore",
    "#/analyze/motion",
    "#/research/data",
  ]);
  expect(loadFrames).not.toHaveBeenCalled();
});

it("opens Explore → Watch for a target deep link without a hash", () => {
  history.replaceState(null, "", "/?t=wise0855");
  const root = document.createElement("div");
  document.body.append(root);
  startApp(root);
  expect(visible(root)).toEqual(["explore"]);
  expect(root.querySelector(".feature-nav [aria-current]")!.textContent).toBe("Watch");
  expect(loadFrames).toHaveBeenCalledTimes(1);
  expect(vi.mocked(loadFrames).mock.calls[0][0]).toMatchObject({ slug: "wise0855" });
});

it("from the whole-sky view (nothing picked), Explore opens a featured target, not the map centre", async () => {
  history.replaceState(null, "", "/");
  const root = document.createElement("div");
  document.body.append(root);
  startApp(root);
  atlas.ctx!.skySelection?.(null, "#/sky/125.6315,19.2541,220");
  root.querySelector<HTMLAnchorElement>('.nav a[data-level="explore"]')!.click();
  await new Promise((resolve) => setTimeout(resolve, 20));
  expect(visible(root)).toEqual(["explore"]);
  // Earlier tests' shells still listen to hashchange, so check every call, not their count.
  expect(loadFrames).toHaveBeenCalled();
  for (const [v] of vi.mocked(loadFrames).mock.calls) expect(v).toMatchObject({ slug: "barnard" });
  expect(root.querySelector<HTMLAnchorElement>(".wordmark")!.getAttribute("href")).toBe("#/sky/125.6315,19.2541,220");
});
