import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AladinStatic } from "../sky/aladin";
import { createAladinMap } from "./aladin-map";
import type { MapCallbacks } from "./map";

function setup() {
  const host = document.createElement("div");
  document.body.append(host);
  Object.defineProperties(host, { clientWidth: { value: 1440 }, clientHeight: { value: 400 } });
  const events = new Map<string, (e: unknown) => void>();
  let fov = 360;
  const al = {
    getRaDec: () => [125, 19], getFov: () => [fov, fov / 2],
    gotoRaDec: vi.fn(), setFoV: (value: number) => { fov = value; },
    pix2world: vi.fn().mockReturnValue([130, 19]), world2pix: () => [380, 200],
    setBaseImageLayer: vi.fn(), setProjection: vi.fn(), setFrame: vi.fn(),
    addOverlay: vi.fn(), addCatalog: vi.fn(), on: (name: string, fn: (e: unknown) => void) => events.set(name, fn),
  };
  const A = {
    aladin: () => al,
    HiPS: () => ({ setOpacity: vi.fn(), setSliceNumber: vi.fn() }),
    graphicOverlay: () => ({ removeAll: vi.fn(), add: vi.fn() }),
    catalog: () => ({ removeAll: vi.fn(), addSources: vi.fn() }),
  } as unknown as AladinStatic;
  const cb = { onView: vi.fn(), onSignal: vi.fn(), onTarget: vi.fn(), onCell: vi.fn(), onEmpty: vi.fn(), onHover: vi.fn() } satisfies MapCallbacks;
  const map = createAladinMap(host, A, {
    view: { ra: 125, dec: 19, fov: 360 }, gal: false, mol: true, reducedMotion: true,
    layers: { background: "2mass", negative: false, heat: true, zones: true, signals: true, search: false, provisional: true },
  }, cb);
  const inner = host.querySelector<HTMLElement>(".atl-aladin")!;
  Object.defineProperties(inner, {
    clientWidth: { get: () => Number.parseFloat(inner.style.width) || host.clientWidth },
    clientHeight: { get: () => host.clientHeight },
  });
  return { host, inner, map, al, cb, events };
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
  document.body.innerHTML = "";
});

describe("Aladin map boundaries and lifecycle", () => {
  it("clips in the narrowed map's coordinates and clears the clip after zooming in", () => {
    const { map, host, inner } = setup();
    expect(inner.style.width).toBe("760px");
    expect(inner.style.clipPath).toBe("ellipse(380px 190px at 380px 200px)");
    expect(host.querySelector(".atl-limb-edge")?.getAttribute("cx")).toBe("720");
    map.flyTo({ ra: 125, dec: 19, fov: 30 }, false);
    vi.advanceTimersByTime(20);
    expect(inner.style.clipPath).toBe("");
    expect(inner.style.width).toBe("");
    map.destroy();
  });

  it("pans relative to Aladin's actual viewport on a wide screen", () => {
    const { map, al } = setup();
    map.panBy(.15, 0);
    expect(al.pix2world).toHaveBeenCalledWith(494, 200);
    expect(al.gotoRaDec).toHaveBeenCalledWith(130, 19);
    map.destroy();
  });

  it("ignores late map clicks and cancels queued view work after destruction", () => {
    const { map, events, cb } = setup();
    events.get("positionChanged")?.(undefined);
    map.destroy();
    events.get("click")?.({ ra: 125, dec: 19, x: 380, y: 200, isDragging: false });
    events.get("positionChanged")?.(undefined);
    vi.advanceTimersByTime(100);
    expect(cb.onView).not.toHaveBeenCalled();
    expect(cb.onEmpty).not.toHaveBeenCalled();
  });
});
