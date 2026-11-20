import { clampCamera, mapScale, toScreen, toWorld, zoomAt, type Camera, type Point, type Size } from "../play/hunt-model";

type MarkKind = "landmark" | "candidate" | "correct" | "incorrect" | "archive";
type Mark = { at: Point; label: string; kind: MarkKind; radius?: number };

const MARK_STYLE: Record<MarkKind, { color: string; radius: number; symbol?: string }> = {
  landmark: { color: "#71caff", radius: 10 },
  candidate: { color: "#ffd485", radius: 15 },
  correct: { color: "#8ff0bd", radius: 15, symbol: "✓" },
  incorrect: { color: "#ffe27a", radius: 15, symbol: "×" },
  archive: { color: "#8ff0bd", radius: 21 },
};
type Patch = Point & { w: number; h: number };
type Guide = { patch: Patch; label: string; kind: "landmarks" | "compare" | "hint" };
type FieldView = { image: HTMLCanvasElement; marks: Mark[]; guides: Guide[]; trail?: Point[] };

export function drawFirstStarPatch(
  canvas: HTMLCanvasElement,
  image: HTMLCanvasElement,
  patch: Patch,
  mark?: Point,
  markKind: "candidate" | "correct" = "candidate",
) {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(image, patch.x, patch.y, patch.w, patch.h, 0, 0, canvas.width, canvas.height);
  if (mark) {
    const x = (mark.x - patch.x) * canvas.width / patch.w;
    const y = (mark.y - patch.y) * canvas.height / patch.h;
    ctx.beginPath();
    ctx.arc(x, y, 6, 0, Math.PI * 2);
    ctx.strokeStyle = "#19222c";
    ctx.lineWidth = 3;
    ctx.stroke();
    ctx.strokeStyle = MARK_STYLE[markKind].color;
    ctx.lineWidth = 1.5;
    ctx.stroke();
  }
}

export function mountFirstStarField(
  canvas: HTMLCanvasElement,
  world: Size,
  read: () => FieldView,
  pick: (point: Point) => void,
) {
  const ctx = canvas.getContext("2d")!;
  const initial = { x: world.width / 2, y: world.height / 2, zoom: 1 };
  let camera: Camera = initial;
  let screen: Size = { width: 1, height: 1 };
  let drag: { id: number; start: Point; last: Point; moved: boolean } | undefined;
  let pendingPick: Point | undefined;
  let keyboard = false;
  const listeners = new AbortController();
  const { signal } = listeners;

  function draw() {
    const { image, marks, guides, trail } = read();
    ctx.setTransform(canvas.width / screen.width, 0, 0, canvas.height / screen.height, 0, 0);
    ctx.fillStyle = "#141d28";
    ctx.fillRect(0, 0, screen.width, screen.height);
    const scale = mapScale(camera, world, screen);
    const origin = toScreen({ x: 0, y: 0 }, camera, world, screen);
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(image, origin.x, origin.y, world.width * scale, world.height * scale);
    ctx.save();
    ctx.beginPath();
    ctx.rect(origin.x, origin.y, world.width * scale, world.height * scale);
    ctx.clip();
    for (const guide of guides) drawGuide(guide, origin, scale);
    if (trail?.length) {
      const [first, ...rest] = trail.map(at => toScreen(at, camera, world, screen));
      ctx.beginPath();
      ctx.moveTo(first.x, first.y);
      for (const point of rest) ctx.lineTo(point.x, point.y);
      ctx.strokeStyle = "#19222c";
      ctx.lineWidth = 4;
      ctx.stroke();
      ctx.strokeStyle = "#8ff0bd";
      ctx.lineWidth = 2;
      ctx.stroke();
    }
    for (const mark of marks) {
      const point = toScreen(mark.at, camera, world, screen);
      const { color, radius: defaultRadius, symbol } = MARK_STYLE[mark.kind];
      const radius = mark.radius ?? defaultRadius;
      ctx.beginPath();
      ctx.arc(point.x, point.y, radius, 0, Math.PI * 2);
      if (mark.kind === "archive") ctx.setLineDash([5, 3]);
      ctx.strokeStyle = "#19222c";
      ctx.lineWidth = 4;
      ctx.stroke();
      ctx.strokeStyle = color;
      ctx.lineWidth = symbol ? 3 : 2;
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.strokeStyle = "#19222c";
      ctx.lineWidth = 3;
      const label = symbol ? (mark.label ? `${symbol} ${mark.label}` : symbol) : mark.label;
      ctx.font = symbol && !mark.label ? "700 18px system-ui" : "600 12px system-ui";
      let labelX = point.x + radius + 4;
      let labelY = point.y + (mark.kind === "candidate" ? radius + 8 : -5);
      if (symbol) {
        const width = ctx.measureText(label).width;
        const right = Math.min(origin.x + world.width * scale, screen.width);
        if (labelX + width > right) labelX = point.x - radius - width - 4;
        labelY = Math.max(labelY, Math.max(origin.y, 0) + 16);
      }
      ctx.strokeText(label, labelX, labelY);
      ctx.fillStyle = color;
      ctx.fillText(label, labelX, labelY);
    }
    if (keyboard) {
      const x = screen.width / 2;
      const y = screen.height / 2;
      ctx.beginPath();
      ctx.moveTo(x - 9, y); ctx.lineTo(x + 9, y);
      ctx.moveTo(x, y - 9); ctx.lineTo(x, y + 9);
      ctx.lineWidth = 4;
      ctx.strokeStyle = "#19222c";
      ctx.stroke();
      ctx.lineWidth = 1.5;
      ctx.strokeStyle = "#ffffff";
      ctx.stroke();
    }
    ctx.restore();
  }

  function drawGuide({ patch, label, kind }: Guide, origin: Point, scale: number) {
    const left = origin.x + patch.x * scale;
    const top = origin.y + patch.y * scale;
    const right = left + patch.w * scale;
    const bottom = top + patch.h * scale;
    const corners = Math.min(18, patch.w * scale / 3, patch.h * scale / 3);
    const color = kind === "landmarks" ? "#9bdbff" : "#ffdc91";

    ctx.beginPath();
    if (kind === "hint") {
      ctx.rect(left, top, right - left, bottom - top);
      ctx.setLineDash([6, 5]);
    } else {
      for (const [x, y, dx, dy] of [[left, top, 1, 1], [right, top, -1, 1], [left, bottom, 1, -1], [right, bottom, -1, -1]]) {
        ctx.moveTo(x + dx * corners, y);
        ctx.lineTo(x, y);
        ctx.lineTo(x, y + dy * corners);
      }
    }
    ctx.strokeStyle = "#19222c";
    ctx.lineWidth = 4;
    ctx.stroke();
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.setLineDash([]);

    ctx.font = "600 12px system-ui";
    const labelWidth = ctx.measureText(label).width + 12;
    const canPlace = left >= Math.max(origin.x, 0) && left + labelWidth <= Math.min(origin.x + world.width * scale, screen.width);
    const labelY = kind === "hint" ? bottom + 5
      : top >= Math.max(origin.y, 0) + 24 ? top - 24 : top + 5;
    const insideImage = labelY >= Math.max(origin.y, 0) && labelY + 19 <= Math.min(origin.y + world.height * scale, screen.height);
    if (canPlace && insideImage) {
      ctx.fillStyle = "#19222c";
      ctx.fillRect(left, labelY, labelWidth, 19);
      ctx.fillStyle = color;
      ctx.fillText(label, left + 6, labelY + 13);
    }
  }

  function resize() {
    const { width, height } = canvas.getBoundingClientRect();
    if (!(width && height)) return;
    screen = { width, height };
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    camera = clampCamera(camera, world, screen);
    draw();
  }

  function zoom(factor: number, anchor = { x: screen.width / 2, y: screen.height / 2 }) {
    camera = zoomAt(camera, camera.zoom * factor, anchor, world, screen);
    draw();
  }

  function move(x: number, y: number) {
    const scale = mapScale(camera, world, screen);
    camera = clampCamera({ ...camera, x: camera.x + x / scale, y: camera.y + y / scale }, world, screen);
    draw();
  }

  function local(event: PointerEvent | WheelEvent) {
    const bounds = canvas.getBoundingClientRect();
    return { x: event.clientX - bounds.left, y: event.clientY - bounds.top };
  }

  function select(at: Point) {
    if (at.x >= 0 && at.x < world.width && at.y >= 0 && at.y < world.height) pick(at);
  }

  canvas.addEventListener("pointerdown", event => {
    if (event.button !== 0 || !event.isPrimary) return;
    pendingPick = undefined;
    canvas.focus({ preventScroll: true });
    canvas.setPointerCapture(event.pointerId);
    const at = local(event);
    keyboard = false;
    drag = { id: event.pointerId, start: at, last: at, moved: false };
  }, { signal });
  canvas.addEventListener("pointermove", event => {
    if (drag?.id !== event.pointerId) return;
    const at = local(event);
    drag.moved ||= Math.hypot(at.x - drag.start.x, at.y - drag.start.y) > 5;
    if (!drag.moved) return;
    move(drag.last.x - at.x, drag.last.y - at.y);
    drag.last = at;
  }, { signal });
  canvas.addEventListener("pointerup", event => {
    if (drag?.id !== event.pointerId) return;
    if (!drag.moved) pendingPick = toWorld(local(event), camera, world, screen);
    drag = undefined;
  }, { signal });
  // A mark can change the lesson layout. Consume the tap before moving controls under it.
  canvas.addEventListener("click", () => {
    if (!pendingPick) return;
    const at = pendingPick;
    pendingPick = undefined;
    select(at);
  }, { signal });
  canvas.addEventListener("lostpointercapture", () => { drag = undefined; }, { signal });
  canvas.addEventListener("wheel", event => {
    if (!(event.ctrlKey || event.metaKey)) return;
    event.preventDefault();
    zoom(Math.exp(-event.deltaY * .01), local(event));
  }, { signal, passive: false });
  canvas.addEventListener("keydown", event => {
    const panning: Record<string, [number, number]> = {
      ArrowLeft: [-24, 0], ArrowUp: [0, -24], ArrowRight: [24, 0], ArrowDown: [0, 24],
    };
    const delta = panning[event.key];
    if (!(delta || ["Enter", "+", "=", "-"].includes(event.key))) return;
    event.preventDefault();
    keyboard = true;
    if (delta) move(...delta);
    else if (event.key === "Enter") select(camera);
    else zoom(event.key === "-" ? 1 / 1.5 : 1.5);
    draw();
  }, { signal });
  const observer = new ResizeObserver(resize);
  observer.observe(canvas);
  resize();

  return {
    draw, zoom,
    reset() { camera = initial; draw(); },
    focus(at: Point, magnification: number) {
      camera = clampCamera({ ...at, zoom: Math.max(camera.zoom, magnification) }, world, screen);
      draw();
    },
    destroy() { listeners.abort(); observer.disconnect(); },
  };
}
