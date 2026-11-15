import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AladinStatic } from "../sky/aladin";
import { createAladinMap } from "./aladin-map";
import type { MapCallbacks, MapModel } from "./map";
import { zoneAreas } from "./zones";

/** Screen = a plain linear map of the sky round (125, 19), 10 px a degree, east to the left. */
const toScreen = (ra: number, dec: number): [number, number] => [720 - (ra - 125) * 10, 200 - (dec - 19) * 10];

function setup(opts: { fov?: number; zoomFactor?: boolean } = {}) {
  const host = document.createElement("div");
  document.body.append(host);
  Object.defineProperties(host, { clientWidth: { value: 1440 }, clientHeight: { value: 400 } });
  const events = new Map<string, (e: unknown) => void>();
  // Aladin's getFov() stops at the projection's widest field (360° on the whole sky); its zoom factor doesn't.
  let fov = opts.fov ?? 360;
  const al = {
    getRaDec: () => [125, 19],
    getFov: () => [Math.min(360, fov), Math.min(180, fov / 2)],
    ...(opts.zoomFactor === false ? {} : { getZoomFactor: () => fov / 360 }),
    gotoRaDec: vi.fn(),
    setFoV: vi.fn((value: number) => {
      fov = value;
    }),
    setFoVRange: vi.fn(),
    animateToRaDec: vi.fn(),
    zoomToFoV: vi.fn<(fov: number, seconds: number) => void>(),
    stopAnimation: vi.fn(),
    pix2world: vi.fn().mockReturnValue([130, 19]),
    world2pix: (ra: number, dec: number) => toScreen(ra, dec),
    setBaseImageLayer: vi.fn(),
    setProjection: vi.fn(),
    setFrame: vi.fn(),
    addOverlay: vi.fn(),
    addCatalog: vi.fn(),
    on: (name: string, fn: (e: unknown) => void) => events.set(name, fn),
  };
  const polygons: { points: [number, number][]; opts: Record<string, unknown> }[] = [];
  const A = {
    aladin: vi.fn((_el: unknown, o: { fov: number }) => {
      fov = o.fov;
      return al;
    }),
    HiPS: () => ({ setOpacity: vi.fn(), setSliceNumber: vi.fn() }),
    graphicOverlay: () => ({ removeAll: vi.fn(() => polygons.splice(0)), add: vi.fn() }),
    polygon: (points: [number, number][], o: Record<string, unknown>) => polygons.push({ points, opts: o }),
    source: (ra: number, dec: number, data: unknown) => ({ ra, dec, data }),
    catalog: () => ({ removeAll: vi.fn(), addSources: vi.fn() }),
  } as unknown as AladinStatic;
  const cb = {
    onView: vi.fn(),
    onSignal: vi.fn(),
    onTarget: vi.fn(),
    onCell: vi.fn(),
    onZone: vi.fn(),
    onEmpty: vi.fn(),
    onHover: vi.fn(),
  } satisfies MapCallbacks;
  const map = createAladinMap(
    host,
    A,
    {
      view: { ra: 125, dec: 19, fov: 360 },
      gal: false,
      mol: true,
      reducedMotion: true,
      layers: { background: "2mass", negative: false, heat: false, zones: true, signals: true, search: false, provisional: true },
    },
    cb,
  );
  const inner = host.querySelector<HTMLElement>(".atl-aladin")!;
  return { host, inner, map, al, A, cb, events, polygons, fovNow: () => fov };
}

const areas = zoneAreas([
  { zone: "hunt", count: 230, ra0: 120.1, ra1: 130.9, dec0: 14.1, dec1: 24.7, center: { ra: 125.5, dec: 19.4 } },
  { zone: "pilot", count: 42, ra0: 120.13, ra1: 124.5, dec0: 14.14, dec1: 18.3, center: { ra: 122.3, dec: 16.2 } },
]);

function model(over: Partial<MapModel> = {}): MapModel {
  return {
    candidates: [],
    cellsAt: () => [],
    areas,
    targets: [],
    selectedId: null,
    layers: { background: "2mass", negative: false, heat: false, zones: true, signals: true, search: false, provisional: true },
    search: null,
    provisional: null,
    ...over,
  };
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

describe("Aladin map: the whole sky fills the map", () => {
  it("keeps manual zoom after interrupting an unfinished flight", () => {
    const { map, al } = setup();
    let flight: ReturnType<typeof setTimeout> | undefined;
    al.zoomToFoV.mockImplementation((value) => {
      flight = setTimeout(() => al.setFoV(value), 1100);
    });
    al.stopAnimation.mockImplementation(() => clearTimeout(flight));
    map.flyTo({ ra: 125, dec: 19, fov: 40 }, true);
    map.zoomBy(1 / 1.6);
    vi.advanceTimersByTime(1200);
    expect(map.view().fov).toBeCloseTo(225);
    map.destroy();
  });

  it("opens the whole sky as large as the map holds it, with no outer ellipse", () => {
    const { A, host, inner, map } = setup();
    // 1440 × 400: the 2:1 ellipse is held by the height, so Aladin zooms out past 360°.
    const fov = (A.aladin as unknown as { mock: { calls: [unknown, { fov: number }][] } }).mock.calls[0][1].fov;
    expect(fov).toBeCloseTo((180 * 1440) / (400 - 16));
    expect(inner.style.width).toBe("");
    expect(inner.style.clipPath).toBe("ellipse(384px 192px at 720px 200px)");
    expect(host.querySelector(".atl-limb, .atl-limb-edge")).toBeNull();
    map.destroy();
  });

  it("never lets the whole sky shrink below the map's size", () => {
    const { map, al, events, fovNow } = setup();
    al.setFoV(900); // zoomed out past the fit with the wheel
    events.get("zoomChanged")?.(undefined);
    vi.advanceTimersByTime(20);
    expect(fovNow()).toBeCloseTo((180 * 1440) / 384);
    map.destroy();
  });

  it("reports the field Aladin knows (360° at most) and clears the clip once zoomed in", () => {
    const { map, inner } = setup();
    expect(map.view().fov).toBe(360);
    map.flyTo({ ra: 125, dec: 19, fov: 30 }, false);
    vi.advanceTimersByTime(20);
    expect(inner.style.clipPath).toBe("");
    expect(map.view().fov).toBe(30);
    map.flyTo({ ra: 125, dec: 19, fov: 360 }, false);
    vi.advanceTimersByTime(20);
    expect(inner.style.clipPath).toBe("ellipse(384px 192px at 720px 200px)");
    map.destroy();
  });

  it("pans relative to the map's centre", () => {
    const { map, al } = setup();
    map.panBy(0.15, 0);
    expect(al.pix2world).toHaveBeenCalledWith(720 + 216, 200);
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

describe("Aladin map: zones", () => {
  it("draws the hunt zone and the pilot inside it as one polygon with one fill", () => {
    const ctx = { setTransform: vi.fn(), clearRect: vi.fn(), beginPath: vi.fn(), moveTo: vi.fn(), lineTo: vi.fn(), closePath: vi.fn(), fill: vi.fn(), stroke: vi.fn() };
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(function (this: HTMLCanvasElement) {
      return this.classList.contains('atl-zonecanvas') ? ctx as unknown as CanvasRenderingContext2D : null;
    });
    const { map, host } = setup();
    map.update(model());
    expect(ctx.fill).toHaveBeenCalledTimes(1);
    expect(ctx.stroke).toHaveBeenCalledTimes(1);
    expect(host.querySelector('.atl-zonecanvas')).not.toBeNull();
    map.destroy();
    expect(host.querySelector('.atl-zonecanvas')).toBeNull();
  });

  it("opens a zone from a click on it on the whole sky, and the Viewer from anywhere else", () => {
    const { map, events, cb } = setup();
    map.update(model());
    vi.advanceTimersByTime(20);
    events.get("click")?.({ ra: 126, dec: 20, x: 710, y: 190, isDragging: false });
    expect(cb.onZone).toHaveBeenCalledWith(areas[0]);
    events.get("click")?.({ ra: 200, dec: -40, x: 10, y: 390, isDragging: false });
    expect(cb.onEmpty).toHaveBeenCalledWith({ ra: 200, dec: -40 }, 10, 390);
  });
});
