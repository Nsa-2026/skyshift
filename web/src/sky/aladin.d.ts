// The small part of the Aladin Lite 3.8.2 API the sky view uses. Aladin is loaded at runtime
// from a CDN (src/sky/loader.ts), so these types are ours, written against the 3.8.2 bundle.

export interface AladinClick {
  ra: number;
  dec: number;
  x: number;
  y: number;
  isDragging: boolean;
}

export interface AladinHiPS {
  setOpacity(opacity: number): void;
  setSliceNumber(slice: number): void;
  added?: boolean;
}

export interface AladinCatalog {
  addSources(sources: unknown[]): void;
  removeAll(): void;
  show(): void;
  hide(): void;
}

export interface AladinOverlay {
  add(shape: unknown): void;
  removeAll(): void;
}

export interface AladinInstance {
  gotoRaDec(ra: number, dec: number): void;
  setFoV(deg: number): void;
  getRaDec(): [number, number];
  /** [width, height] of the view in degrees. */
  getFov(): [number, number];
  world2pix(ra: number, dec: number): [number, number] | null | undefined;
  setBaseImageLayer(layer: AladinHiPS): void;
  setOverlayImageLayer(layer: AladinHiPS, name: string): void;
  removeImageLayer(name: string): void;
  addCatalog(cat: AladinCatalog): void;
  addOverlay(ov: AladinOverlay): void;
  on(event: string, cb: (e: never) => void): void;
}

export interface HiPSOptions {
  name?: string;
  imgFormat?: "jpeg" | "png" | "fits" | "webp";
  successCallback?(layer: AladinHiPS): void;
  errorCallback?(err: unknown): void;
}

export interface AladinStatic {
  init: Promise<void>;
  aladin(el: HTMLElement, opts: Record<string, unknown>): AladinInstance;
  HiPS(url: string, opts?: HiPSOptions): AladinHiPS;
  catalog(opts: Record<string, unknown>): AladinCatalog;
  source(ra: number, dec: number, data?: Record<string, unknown>): unknown;
  graphicOverlay(opts: Record<string, unknown>): AladinOverlay;
  polygon(points: [number, number][], opts?: Record<string, unknown>): unknown;
}
