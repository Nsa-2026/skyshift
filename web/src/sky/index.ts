// Sky view (P3): the sky around the blink in Aladin Lite with NASA IRSA layers, the blink's field
// outline, Gaia stars at the shown epoch, and the target. Click the sky, then "Blink here".
// Aladin loads only when the panel is on screen, and the page never depends on it.
import "./sky.css";
import type { Det, FramesResponse, SkyPos } from "../api/types";
import { formatMjd, formatRaDec, sepArcsec } from "../app/astro";
import type { MountSky, View } from "../app/modules";
import type { AladinCatalog, AladinClick, AladinHiPS, AladinInstance, AladinOverlay, AladinStatic } from "./aladin";
import { BASE_LAYERS, SPHEREX, TWO_MASS, type SkyLayer, cubeFrameForDet, cubeFrameUm } from "./layers";
import { SkyUnavailable, loadAladinOnce } from "./loader";
import { blinkFieldArcmin, brightest, centerAt, fieldCorners, firstEpochMjd, gaiaBudget, gaiaDots, skyFovDeg } from "./overlay";

const html = String.raw;
const DETS: Det[] = [1, 2, 3, 4, 5, 6];
/** Ignore a second "Blink here" within this long: one deliberate action, one re-blink. */
const CLICK_LOCK_MS = 900;

function token(name: string, fallback: string): string {
  try {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  } catch {
    return fallback;
  }
}

/** The target mark: four ticks around a gap, so the star itself stays visible. */
function reticle(color: string, size = 30): HTMLCanvasElement | "plus" {
  const c = document.createElement("canvas");
  let ctx: CanvasRenderingContext2D | null = null;
  try {
    ctx = c.getContext("2d");
  } catch {
    ctx = null;
  }
  if (!ctx) return "plus";
  c.width = c.height = size;
  const m = size / 2;
  const gap = size * 0.2;
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.lineCap = "round";
  ctx.beginPath();
  for (const [dx, dy] of [
    [1, 0],
    [-1, 0],
    [0, 1],
    [0, -1],
  ]) {
    ctx.moveTo(m + dx * gap, m + dy * gap);
    ctx.lineTo(m + dx * (m - 2), m + dy * (m - 2));
  }
  ctx.stroke();
  return c;
}

export const mountSky: MountSky = (el, opts) => {
  el.classList.add("sky-panel");
  el.innerHTML = html`
    <header class="sky-head">
      <div class="sky-title">
        <h2 id="sky-h">Sky around this spot</h2>
        <p class="sky-explain">NASA infrared surveys around the blink. Click the sky, then Blink here to blink that spot.</p>
      </div>
      <button class="btn sky-toggle" type="button" aria-expanded="true" aria-controls="sky-body">
        <span class="sky-toggle-open">Hide</span><span class="sky-toggle-closed">Show sky</span>
      </button>
    </header>
    <div class="sky-body" id="sky-body">
      <div class="sky-controls" role="group" aria-label="Sky layers">
        <div class="sky-group" role="group" aria-label="Background survey">
          <span class="sky-group-label" aria-hidden="true">Background</span>
          ${BASE_LAYERS.map(
            (l) => html`<button class="btn sky-layer" type="button" data-base="${l.id}" title="${l.about}"
              aria-pressed="${l.id === TWO_MASS.id}">${l.label}</button>`,
          ).join("")}
        </div>
        <div class="sky-group sky-group-spherex" role="group" aria-label="SPHEREx layer">
          <button class="btn sky-layer" type="button" data-spherex title="${SPHEREX.about}" aria-pressed="true">SPHEREx</button>
          <label class="sky-opacity">
            <span class="visually-hidden">SPHEREx opacity</span>
            <input type="range" min="0" max="100" step="5" value="50" data-opacity aria-label="SPHEREx opacity" />
          </label>
          <div class="sky-dets" role="group" aria-label="SPHEREx wavelength, by detector">
            ${DETS.map(
              (d) => html`<button class="sky-det det-${d}" type="button" data-det="${d}" aria-pressed="false"
                title="D${d}: ${cubeFrameUm(cubeFrameForDet(d)).toFixed(2)} µm">D${d}</button>`,
            ).join("")}
          </div>
          <span class="sky-um" data-um aria-live="polite"></span>
        </div>
        <button class="btn sky-layer sky-gaia-btn" type="button" data-gaia aria-pressed="true" title="Gaia DR3 stars, moved to the shown epoch">
          Gaia stars
        </button>
      </div>
      <div class="sky-stage">
        <div class="sky-map"><div class="sky-aladin" data-map></div></div>
        <div class="sky-note" data-note role="status"><p>The sky view loads when you scroll here.</p></div>
        <div class="sky-pin" data-pin hidden>
          <button class="sky-pin-go" type="button" data-pin-go>Blink here</button>
          <span class="sky-pin-coords" data-pin-coords></span>
          <button class="sky-pin-x" type="button" data-pin-x aria-label="Cancel">×</button>
        </div>
      </div>
      <div class="sky-foot">
        <ul class="sky-key" aria-label="Key">
          <li><span class="key-field" aria-hidden="true"></span><span data-field-label>Blink field</span></li>
          <li><span class="key-gaia" aria-hidden="true"></span><span data-epoch-label>Gaia DR3 stars</span></li>
          <li><span class="key-target" aria-hidden="true"></span>Target</li>
        </ul>
        <button class="btn sky-centre" type="button" data-centre disabled>Blink the map centre</button>
      </div>
    </div>
  `;

  const q = <T extends Element>(sel: string) => el.querySelector(sel) as T;
  const mapEl = q<HTMLDivElement>("[data-map]");
  const note = q<HTMLDivElement>("[data-note]");
  const pin = q<HTMLDivElement>("[data-pin]");
  const pinCoords = q<HTMLSpanElement>("[data-pin-coords]");
  const pinGo = q<HTMLButtonElement>("[data-pin-go]");
  const centreBtn = q<HTMLButtonElement>("[data-centre]");
  const toggle = q<HTMLButtonElement>(".sky-toggle");
  const body = q<HTMLDivElement>(".sky-body");
  const umLabel = q<HTMLSpanElement>("[data-um]");
  const epochLabel = q<HTMLSpanElement>("[data-epoch-label]");
  const fieldLabel = q<HTMLSpanElement>("[data-field-label]");
  const opacityIn = q<HTMLInputElement>("[data-opacity]");

  // State that outlives Aladin: kept until it loads, then applied.
  let view: View | null = null;
  let frames: FramesResponse | null = null;
  let mjd: number | null = null;
  let lastCentre: { ra: number; dec: number; fov: number } | null = null;
  let base: SkyLayer = TWO_MASS;
  let spherexOn = true;
  let opacity = 0.5;
  let slice: number | null = null; // null: follow the blink's detector
  let gaiaOn = true;
  let pinPos: SkyPos | null = null;
  let lockedUntil = 0;
  let destroyed = false;
  let state: "idle" | "loading" | "ready" | "failed" = "idle";

  let A: AladinStatic | null = null;
  let al: AladinInstance | null = null;
  let hips: Partial<Record<SkyLayer["id"], AladinHiPS>> = {};
  let gaiaCat: AladinCatalog | null = null;
  let targetCat: AladinCatalog | null = null;
  let fieldOv: AladinOverlay | null = null;

  function showNote(text: string | null, kind: "info" | "fail" = "info") {
    note.hidden = text == null;
    note.dataset.kind = kind;
    if (text != null) note.innerHTML = `<p>${text}</p>`;
  }

  function fail(reason: string) {
    if (destroyed) return;
    state = "failed";
    el.classList.add("sky-failed");
    hidePin();
    centreBtn.disabled = true;
    showNote(`Sky view unavailable: ${reason}; the blink above still works.`, "fail");
  }

  // ---- layers ---------------------------------------------------------------------------
  const currentSlice = () => slice ?? cubeFrameForDet(view?.det ?? 2);

  function layer(l: SkyLayer, onReady?: (h: AladinHiPS) => void): AladinHiPS | null {
    if (!A) return null;
    if (!hips[l.id]) {
      try {
        hips[l.id] = A.HiPS(l.url, {
          name: l.label,
          imgFormat: l.format,
          successCallback: (h) => onReady?.(h),
          errorCallback: () => {
            if (l.id === base.id) fail(`IRSA's ${l.label} sky tiles didn't load`);
            else if (l.id === SPHEREX.id) umLabel.textContent = "SPHEREx tiles didn't load";
          },
        });
      } catch {
        return null;
      }
    }
    return hips[l.id] ?? null;
  }

  function applyBase() {
    if (!al) return;
    const h = layer(base);
    if (h) al.setBaseImageLayer(h);
  }

  function applySlice() {
    const f = currentSlice();
    umLabel.textContent = `${cubeFrameUm(f).toFixed(2)} µm`;
    el.querySelectorAll<HTMLButtonElement>("[data-det]").forEach((b) => {
      b.setAttribute("aria-pressed", String(cubeFrameForDet(Number(b.dataset.det) as Det) === f));
    });
    const h = hips.spherex;
    if (h?.added) {
      try {
        h.setSliceNumber(f);
      } catch {
        /* cube API missing: the default frame stays */
      }
    }
  }

  function applySpherex() {
    el.querySelector("[data-spherex]")!.setAttribute("aria-pressed", String(spherexOn));
    opacityIn.disabled = !spherexOn;
    el.querySelectorAll<HTMLButtonElement>("[data-det]").forEach((b) => (b.disabled = !spherexOn));
    if (!al) return;
    try {
      if (spherexOn) {
        const h = layer(SPHEREX, () => applySlice());
        if (!h) return;
        h.setOpacity(opacity);
        al.setOverlayImageLayer(h, "spherex");
        if (h.added) applySlice();
      } else {
        al.removeImageLayer("spherex");
        if (hips.spherex) hips.spherex.added = false;
      }
    } catch {
      /* layer change failed: leave the map as it is */
    }
  }

  // ---- overlays -------------------------------------------------------------------------
  function drawField() {
    if (!view) return;
    const field = blinkFieldArcmin(view, frames);
    const fmt = field >= 10 ? field.toFixed(0) : field.toFixed(1).replace(/\.0$/, "");
    fieldLabel.textContent = `Blink field, ${fmt}′`;
    if (!A || !fieldOv || !targetCat) return;
    const c = centerAt(frames, mjd, view);
    fieldOv.removeAll();
    targetCat.removeAll();
    fieldOv.add(A.polygon(fieldCorners(c, field, frames)));
    targetCat.addSources([A.source(c.ra, c.dec, { name: view.name ?? "Target" })]);
  }

  /** How many rings fit legibly at the current zoom. */
  function currentBudget(): number {
    if (!al || !view) return Infinity;
    try {
      const fovW = al.getFov()[0];
      return gaiaBudget(((blinkFieldArcmin(view, frames) / 60) * mapEl.clientWidth) / fovW);
    } catch {
      return Infinity;
    }
  }
  let shownBudget = Infinity;

  function drawGaia() {
    const date = mjd != null ? ` on ${formatMjd(mjd)}` : "";
    epochLabel.textContent = `Gaia DR3 stars${date}`;
    if (!A || !gaiaCat) return;
    gaiaCat.removeAll();
    if (!frames || mjd == null || !gaiaOn) return;
    const Al = A;
    const all = gaiaDots(frames.gaia, mjd);
    shownBudget = currentBudget();
    const dots = brightest(all, shownBudget);
    if (dots.length < all.length) epochLabel.textContent = `Brightest ${dots.length} of ${all.length} Gaia DR3 stars${date}`;
    gaiaCat.addSources(dots.map((s) => Al.source(s.ra, s.dec, { id: s.id, G: s.g })));
  }

  /** Zooming changes how many rings fit: redraw when the budget moves by more than a fifth. */
  function maybeRedrawGaia() {
    const b = currentBudget();
    if (!Number.isFinite(b) || !Number.isFinite(shownBudget)) return;
    if (Math.abs(b - shownBudget) > shownBudget * 0.2) drawGaia();
  }

  const aspect = () => (mapEl.clientHeight > 0 ? mapEl.clientWidth / mapEl.clientHeight : 1);

  function centreOnView(force = false) {
    if (!al || !view) return;
    const fov = skyFovDeg(blinkFieldArcmin(view, frames), aspect());
    const same =
      lastCentre && lastCentre.ra === view.ra && lastCentre.dec === view.dec && Math.abs(lastCentre.fov - fov) < 1e-6;
    if (same && !force) return;
    lastCentre = { ra: view.ra, dec: view.dec, fov };
    const c = frames?.center ?? view;
    al.gotoRaDec(c.ra, c.dec);
    al.setFoV(fov);
  }

  // ---- click, then a deliberate "Blink here" ----------------------------------------------
  function placePin() {
    if (!pinPos || !al) return;
    let xy: [number, number] | null | undefined = null;
    try {
      xy = al.world2pix(pinPos.ra, pinPos.dec);
    } catch {
      xy = null;
    }
    const w = mapEl.clientWidth;
    const h = mapEl.clientHeight;
    if (!xy || xy[0] < 0 || xy[1] < 0 || xy[0] > w || xy[1] > h) {
      pin.hidden = true;
      return;
    }
    pin.hidden = false;
    pin.style.left = `${xy[0]}px`;
    pin.style.top = `${xy[1]}px`;
    // Keep the button inside the map near the edges.
    pin.dataset.below = String(xy[1] < 64);
    const half = pin.offsetWidth / 2;
    const shift = Math.max(half + 6 - xy[0], Math.min(0, w - 6 - half - xy[0]));
    pin.style.setProperty("--pin-shift", `${shift}px`);
  }

  function showPin(pos: SkyPos) {
    pinPos = pos;
    pinCoords.textContent = formatRaDec(pos);
    pin.hidden = false;
    placePin();
  }

  function hidePin() {
    pinPos = null;
    pin.hidden = true;
  }

  function blinkAt(pos: SkyPos) {
    const now = Date.now();
    if (now < lockedUntil) return;
    lockedUntil = now + CLICK_LOCK_MS;
    hidePin();
    opts.onClick?.({ ra: ((pos.ra % 360) + 360) % 360, dec: pos.dec });
  }

  function updateCentreBtn() {
    if (!al || !view) return;
    try {
      const [ra, dec] = al.getRaDec();
      // Enabled once the map centre has left the blink field.
      centreBtn.disabled = sepArcsec({ ra, dec }, view) < (blinkFieldArcmin(view, frames) * 60) / 2;
    } catch {
      centreBtn.disabled = true;
    }
  }

  let raf = 0;
  function onMove() {
    if (raf) return;
    raf = requestAnimationFrame(() => {
      raf = 0;
      placePin();
      updateCentreBtn();
      maybeRedrawGaia();
    });
  }

  // ---- start Aladin ------------------------------------------------------------------------
  async function start() {
    if (state !== "idle" || destroyed) return;
    state = "loading";
    showNote("Loading the sky viewer…");
    try {
      A = await loadAladinOnce();
    } catch (e) {
      fail(e instanceof SkyUnavailable ? e.reason : "the sky viewer couldn't start");
      return;
    }
    if (destroyed) return;
    try {
      const c = frames?.center ?? view ?? { ra: 0, dec: 0 };
      al = A.aladin(mapEl, {
        survey: TWO_MASS.url, // never a CDS id: the default P/DSS2/color is broken
        target: `${c.ra} ${c.dec}`,
        fov: skyFovDeg(view ? blinkFieldArcmin(view, frames) : 5, aspect()),
        cooFrame: "ICRS",
        projection: "TAN",
        backgroundColor: token("--plate", "#0f1a2c"),
        showSimbadPointerControl: false,
        showLayersControl: false,
        showFullscreenControl: false,
        showProjectionControl: false,
        showCooGridControl: false,
        showSettingsControl: false,
        showShareControl: false,
        showContextMenu: false,
        showFrame: false,
        showStatusBar: false,
        showReticle: false,
        showZoomControl: true,
        showCooLocation: true,
        showFov: true,
        samp: false,
        log: false, // Aladin's usage logger lives on CDS
      });
      hips = {};
      applyBase();
      gaiaCat = A.catalog({ name: "Gaia DR3", color: token("--gaia", "#62c9e6"), sourceSize: 11, shape: "circle" });
      targetCat = A.catalog({ name: "Target", color: token("--mover", "#ff9b3d"), sourceSize: 30, shape: reticle(token("--mover", "#ff9b3d")) });
      fieldOv = A.graphicOverlay({ name: "Blink field", color: token("--mover", "#ff9b3d"), lineWidth: 2 });
      al.addOverlay(fieldOv);
      al.addCatalog(gaiaCat);
      al.addCatalog(targetCat);
      al.on("click", (e: AladinClick) => {
        if (!e || e.isDragging || !Number.isFinite(e.ra) || !Number.isFinite(e.dec)) return;
        showPin({ ra: e.ra, dec: e.dec });
      });
      al.on("positionChanged", onMove);
      al.on("zoomChanged", onMove);
    } catch {
      fail("the sky viewer couldn't start");
      return;
    }
    state = "ready";
    showNote(null);
    applySpherex();
    lastCentre = null;
    centreOnView(true);
    drawField();
    drawGaia();
    updateCentreBtn();
  }

  // Lazy: load only when the map is on screen (and not collapsed), so the blink's first frame isn't slowed.
  let io: IntersectionObserver | null = null;
  if (typeof IntersectionObserver === "function") {
    io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          io?.disconnect();
          io = null;
          void start();
        }
      },
      { rootMargin: "200px 0px" },
    );
    io.observe(mapEl);
  } else {
    void start();
  }

  // ---- controls ------------------------------------------------------------------------------
  const narrow = () => typeof matchMedia === "function" && matchMedia("(max-width: 900px)").matches;
  function setOpen(open: boolean) {
    toggle.setAttribute("aria-expanded", String(open));
    body.hidden = !open;
    el.classList.toggle("sky-closed", !open);
  }
  // Collapsed by default on phones: the blink comes first.
  if (narrow()) setOpen(false);
  toggle.addEventListener("click", () => setOpen(toggle.getAttribute("aria-expanded") !== "true"));

  el.querySelectorAll<HTMLButtonElement>("[data-base]").forEach((b) =>
    b.addEventListener("click", () => {
      const next = BASE_LAYERS.find((l) => l.id === b.dataset.base);
      if (!next || next.id === base.id) return;
      base = next;
      el.querySelectorAll<HTMLButtonElement>("[data-base]").forEach((o) =>
        o.setAttribute("aria-pressed", String(o.dataset.base === base.id)),
      );
      applyBase();
    }),
  );
  q<HTMLButtonElement>("[data-spherex]").addEventListener("click", () => {
    spherexOn = !spherexOn;
    applySpherex();
  });
  opacityIn.addEventListener("input", () => {
    opacity = Number(opacityIn.value) / 100;
    try {
      hips.spherex?.setOpacity(opacity);
    } catch {
      /* ignore */
    }
  });
  el.querySelectorAll<HTMLButtonElement>("[data-det]").forEach((b) =>
    b.addEventListener("click", () => {
      slice = cubeFrameForDet(Number(b.dataset.det) as Det);
      applySlice();
    }),
  );
  q<HTMLButtonElement>("[data-gaia]").addEventListener("click", (ev) => {
    gaiaOn = !gaiaOn;
    (ev.currentTarget as HTMLButtonElement).setAttribute("aria-pressed", String(gaiaOn));
    drawGaia();
  });
  pinGo.addEventListener("click", () => pinPos && blinkAt(pinPos));
  q<HTMLButtonElement>("[data-pin-x]").addEventListener("click", hidePin);
  const onKey = (ev: KeyboardEvent) => {
    if (ev.key === "Escape" && !pin.hidden) hidePin();
  };
  el.addEventListener("keydown", onKey);
  centreBtn.addEventListener("click", () => {
    if (!al) return;
    try {
      const [ra, dec] = al.getRaDec();
      blinkAt({ ra, dec });
    } catch {
      /* ignore */
    }
  });
  const onResize = () => onMove();
  addEventListener("resize", onResize);

  applySpherex();
  applySlice();

  return {
    goto(v, f) {
      const moved = !view || v.ra !== view.ra || v.dec !== view.dec;
      view = v;
      frames = f;
      if (moved) {
        hidePin();
        mjd = firstEpochMjd(f, v.det);
      } else if (mjd == null) {
        mjd = firstEpochMjd(f, v.det);
      }
      applySlice();
      if (state !== "ready") {
        drawField();
        drawGaia();
        return;
      }
      centreOnView();
      drawField();
      drawGaia();
      updateCentreBtn();
    },
    epoch(m) {
      if (!Number.isFinite(m) || m === mjd) return;
      mjd = m;
      drawGaia();
      // Moving targets: the field follows the object from epoch to epoch.
      if (frames?.epochs.some((e) => e.center)) drawField();
    },
    destroy() {
      destroyed = true;
      io?.disconnect();
      if (raf) cancelAnimationFrame(raf);
      removeEventListener("resize", onResize);
      el.removeEventListener("keydown", onKey);
      el.classList.remove("sky-panel", "sky-failed", "sky-closed");
      el.innerHTML = "";
      al = null;
    },
  };
};
