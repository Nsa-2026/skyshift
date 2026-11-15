// The home page is the Sky (#119); the Viewer opens on shared target links.
import { afterEach, expect, it, vi } from "vitest";

const atlas = vi.hoisted(() => ({ show: vi.fn(), ctx: null as null | import("./modules").PageContext }));
const opening = vi.hoisted(() => ({ mount:vi.fn() }));
vi.mock('../intro',()=>({mountOpening:opening.mount}));
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
  opening.mount.mockClear();
  vi.restoreAllMocks();
});

it("opens on the Sky with every top-level screen one click away, and loads no frames", () => {
  history.replaceState(null, "", "/");
  const root = document.createElement("div");
  document.body.append(root);
  startApp(root);
  expect(visible(root)).toEqual(["atlas"]);
  expect(atlas.show).toHaveBeenCalledTimes(1);
  expect(root.querySelector(".nav [aria-current]")!.textContent).toBe("Sky");
  expect([...root.querySelectorAll<HTMLAnchorElement>(".nav a")].map((a) => a.getAttribute("href"))).toEqual([
    "#/sky",
    "#/explore",
    "#/candidates",
    "#/community",
    "#/results",
    "#/learn",
  ]);
  expect(root.querySelector(".top .details-switch")!.textContent).toBe("Advanced");
  expect([...root.querySelectorAll<HTMLAnchorElement>(".foot-links a")].map((a) => a.textContent)).toEqual(["Community", "Learn", "About"]);
  expect(loadFrames).not.toHaveBeenCalled();
});

it("lands on the Sky, not the Viewer, under the opening, even from a target link", () => {
  history.replaceState(null, "", "/?t=wise0855#/explore");
  const root = document.createElement("div");
  document.body.append(root);
  startApp(root);
  expect(visible(root)).toEqual(["atlas"]);
  expect(location.hash).toBe("#/sky");
  expect(location.search).toBe("");
  expect(loadFrames).not.toHaveBeenCalled();
});

it("opens the Viewer for a target deep link when the opening is off", () => {
  history.replaceState(null, "", "/?t=wise0855&intro=0");
  const root = document.createElement("div");
  document.body.append(root);
  startApp(root);
  expect(visible(root)).toEqual(["explore"]);
  expect(root.querySelector(".nav [aria-current]")!.textContent).toBe("Viewer");
  expect(loadFrames).toHaveBeenCalledTimes(1);
  expect(vi.mocked(loadFrames).mock.calls[0][0]).toMatchObject({ slug: "wise0855" });
});

it("keeps the Viewer target and open dashboards when refreshing a visited tab", () => {
  history.replaceState(null, "", "/?t=wise0855&clock=months&fov=5&det=3#/explore/motion+spectrum");
  vi.spyOn(window, "sessionStorage", "get").mockReturnValue({
    length: 1, getItem: () => "1", key: () => null,
    clear: vi.fn(), removeItem: vi.fn(), setItem: vi.fn(),
  });
  const root = document.createElement("div");
  document.body.append(root);
  startApp(root);
  expect(visible(root)).toEqual(["explore"]);
  expect(location.hash).toBe("#/explore/motion+spectrum");
  expect(vi.mocked(loadFrames).mock.calls[0][0]).toMatchObject({ slug: "wise0855", det: 3, fov: 5 });
  expect(opening.mount).toHaveBeenCalledWith(root, expect.objectContaining({ visitedInTab: true }));
});

it('keeps the original home entry when the atlas rewrites the URL during startup',()=>{
  history.replaceState(null,'','/?utm_source=entry-test');
  atlas.show.mockImplementationOnce(()=>history.replaceState(null,'','#/sky/42,10,360,mol'));
  const root=document.createElement('div');document.body.append(root);startApp(root);
  expect(location.hash).toBe('#/sky/42,10,360,mol');
  expect(opening.mount).toHaveBeenCalledWith(root,expect.objectContaining({hash:'',search:'?utm_source=entry-test'}));
});

it("from the whole-sky view (nothing picked), Viewer opens a featured target, not the map centre", async () => {
  history.replaceState(null, "", "/");
  const root = document.createElement("div");
  document.body.append(root);
  startApp(root);
  atlas.ctx!.skySelection?.(null, "#/sky/125.6315,19.2541,220");
  root.querySelector<HTMLAnchorElement>('.nav a[data-screen="viewer"]')!.click();
  await vi.waitFor(() => expect(visible(root)).toEqual(["explore"]));
  // Earlier tests' shells still listen to hashchange, so check every call, not their count.
  expect(loadFrames).toHaveBeenCalled();
  for (const [v] of vi.mocked(loadFrames).mock.calls) expect(v).toMatchObject({ slug: "barnard" });
  expect(root.querySelector<HTMLAnchorElement>(".wordmark")!.getAttribute("href")).toBe("#/sky/125.6315,19.2541,220");
});
