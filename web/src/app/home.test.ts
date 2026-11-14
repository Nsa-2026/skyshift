// The home page is the Sky globe (#119), under Explore → Sky map; Watch opens on target links.
import { afterEach, expect, it, vi } from "vitest";

const atlas = vi.hoisted(() => ({ show: vi.fn() }));
vi.mock("../atlas", () => ({ mountAtlas: () => ({ show: atlas.show, hide: vi.fn(), destroy: vi.fn() }) }));
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

it("opens on the Sky globe under Explore, with Watch one click away, and loads no frames", () => {
  history.replaceState(null, "", "/");
  const root = document.createElement("div");
  document.body.append(root);
  startApp(root);
  expect(visible(root)).toEqual(["atlas"]);
  expect(atlas.show).toHaveBeenCalledTimes(1);
  expect(root.querySelector(".nav [aria-current]")!.textContent).toBe("Explore");
  expect(root.querySelector(".feature-nav [aria-current]")!.textContent).toBe("Sky map");
  expect(root.querySelector<HTMLAnchorElement>('.feature-nav [data-feature="watch"]')!.getAttribute("href")).toBe("#/explore");
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
